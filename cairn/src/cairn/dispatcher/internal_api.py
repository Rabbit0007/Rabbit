"""Optional internal API for the dispatcher.

This module exposes a live ``DispatcherLoop`` status view for the product
server's worker dashboard. It can also manage the worker list when the operator
uses the product UI's worker configuration panel. It is intentionally defensive
and completely optional:

* It is **opt-in**: it only starts when ``CAIRN_DISPATCHER_INTERNAL_API`` is set
  to a truthy value. Existing deployments are unaffected by default.
* It is **non-fatal**: startup is wrapped in ``try/except`` and runs on a daemon
  thread. If the port is in use or anything goes wrong, the dispatcher keeps
  running normally.
* It is **localhost-only** by default (``127.0.0.1``) on a configurable port
  (default ``8989``).
* The status endpoint only reads existing fields, taking defensive copies to
  tolerate concurrent mutation from the scheduler thread.
* Worker configuration writes are validated before being persisted/applied. A
  failed validation or file write leaves the running dispatcher config intact.

The scheduler loop itself is not modified by importing this module. The only
hooks into the loop are:

* an optional, default-off ``DispatcherLoop.task_history`` ring buffer, and
* ``DispatcherLoop.enable_internal_state_tracking`` to turn it on.

Both are inert unless this internal API is explicitly enabled.
"""

from __future__ import annotations

import errno
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import yaml
from pydantic import ValidationError

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.runtime.startup_healthcheck import run_single_startup_healthcheck

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cairn.dispatcher.scheduler.loop import DispatcherLoop

LOG = logging.getLogger(__name__)

ENABLE_ENV = "CAIRN_DISPATCHER_INTERNAL_API"
HOST_ENV = "CAIRN_DISPATCHER_INTERNAL_HOST"
PORT_ENV = "CAIRN_DISPATCHER_INTERNAL_PORT"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8989
TASK_DESCRIPTION_MAX = 120
WORKER_CONFIG_PATH = "/internal/workers/config"
WORKER_TEST_PATH = "/internal/workers/test"
SECRET_MASK = "********"
SECRET_KEY_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

_TRUTHY = {"1", "true", "yes", "on"}


def _env_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def is_internal_api_enabled() -> bool:
    """Return whether the internal API is opted-in via environment."""
    return _env_truthy(os.environ.get(ENABLE_ENV))


def _resolve_host() -> str:
    host = os.environ.get(HOST_ENV, "").strip()
    return host or DEFAULT_HOST


def _resolve_port() -> int:
    raw = os.environ.get(PORT_ENV, "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        LOG.warning("invalid %s=%r; falling back to default port %s", PORT_ENV, raw, DEFAULT_PORT)
        return DEFAULT_PORT
    if not (1 <= port <= 65535):
        LOG.warning("out-of-range %s=%r; falling back to default port %s", PORT_ENV, raw, DEFAULT_PORT)
        return DEFAULT_PORT
    return port


def _safe_copy(producer: Callable[[], list[Any]], *, retries: int = 4) -> list[Any]:
    """Take a defensive copy of a mutable collection from the scheduler thread.

    Iterating a dict/deque that another thread mutates can raise ``RuntimeError``
    ("changed size during iteration"). We retry a few times and degrade to an
    empty list rather than ever raising into the request handler.
    """
    for _ in range(retries):
        try:
            return producer()
        except RuntimeError:
            continue
        except Exception:  # pragma: no cover - defensive only
            LOG.debug("internal status snapshot copy failed", exc_info=True)
            return []
    return []


def _truncate(text: str, limit: int = TASK_DESCRIPTION_MAX) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _is_secret_env_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in SECRET_KEY_MARKERS)


def _worker_payload(worker: WorkerConfig) -> dict[str, Any]:
    payload = worker.model_dump(mode="json")
    env: dict[str, str] = {}
    secret_keys: list[str] = []
    for key, value in worker.env.items():
        if _is_secret_env_key(key):
            secret_keys.append(key)
            env[key] = SECRET_MASK if value else ""
        else:
            env[key] = value
    payload["env"] = env
    payload["secret_env_keys"] = sorted(secret_keys)
    return payload


def _workers_config_payload(loop: "DispatcherLoop") -> dict[str, Any]:
    lock = getattr(loop, "_config_lock", None)
    if lock is None:
        workers = list(loop.config.workers)
    else:
        with lock:
            workers = list(loop.config.workers)
    return {
        "workers": [_worker_payload(worker) for worker in workers],
    }


def _existing_workers_by_name(loop: "DispatcherLoop") -> dict[str, WorkerConfig]:
    lock = getattr(loop, "_config_lock", None)
    if lock is None:
        workers = list(loop.config.workers)
    else:
        with lock:
            workers = list(loop.config.workers)
    return {worker.name: worker for worker in workers}


def _preserve_masked_secrets(
    raw_workers: list[Any],
    existing_workers: dict[str, WorkerConfig],
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for raw_worker in raw_workers:
        if not isinstance(raw_worker, dict):
            raise ValueError("worker entries must be objects")
        worker_data = dict(raw_worker)
        name = worker_data.get("name")
        env_raw = worker_data.get("env") or {}
        if not isinstance(env_raw, dict):
            raise ValueError(f"worker {name or '<unknown>'} env must be an object")
        env = {str(key): str(value) for key, value in env_raw.items()}
        existing = existing_workers.get(name) if isinstance(name, str) else None
        if existing is not None:
            for key, value in list(env.items()):
                if _is_secret_env_key(key) and (not value.strip() or value == SECRET_MASK):
                    old_value = existing.env.get(key)
                    if old_value:
                        env[key] = old_value
        worker_data["env"] = env
        worker_data.pop("secret_env_keys", None)
        resolved.append(worker_data)
    return resolved


def _validate_worker_payloads(loop: "DispatcherLoop", payload: Any) -> list[WorkerConfig]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    raw_workers = payload.get("workers")
    if not isinstance(raw_workers, list):
        raise ValueError("workers must be an array")
    worker_data = _preserve_masked_secrets(raw_workers, _existing_workers_by_name(loop))
    return [WorkerConfig.model_validate(worker) for worker in worker_data]


def _config_with_workers(loop: "DispatcherLoop", workers: list[WorkerConfig]) -> DispatchConfig:
    lock = getattr(loop, "_config_lock", None)
    if lock is None:
        current = loop.config
    else:
        with lock:
            current = loop.config
    data = current.model_dump(mode="json")
    data["workers"] = [worker.model_dump(mode="json") for worker in workers]
    return DispatchConfig.model_validate(data)


def _write_dispatch_config(loop: "DispatcherLoop", config: DispatchConfig) -> None:
    path = loop.config_path
    data = config.model_dump(mode="json")
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    try:
        tmp_path.replace(path)
    except OSError as exc:
        if exc.errno != errno.EBUSY:
            raise
        # Docker single-file bind mounts cannot be replaced by rename(2). Fall
        # back to rewriting the mounted file in place while preserving the same
        # validation-before-apply flow used by the normal atomic path.
        with path.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            LOGGER.debug("failed to remove temporary dispatcher config %s", tmp_path)


def _validation_detail(exc: Exception) -> dict[str, str]:
    if isinstance(exc, ValidationError):
        parts: list[str] = []
        for error in exc.errors(include_input=False):
            loc = ".".join(str(item) for item in error.get("loc", ()))
            msg = str(error.get("msg", "validation error"))
            parts.append(f"{loc}: {msg}" if loc else msg)
        return {"message": "; ".join(parts) or "worker config validation failed"}
    return {"message": str(exc)}


def _healthcheck_payload(result: Any) -> dict[str, Any]:
    preview = result.response_preview or result.stderr_preview or ""
    return {
        "worker_name": result.worker_name,
        "ok": result.ok,
        "returncode": result.returncode,
        "duration_ms": result.duration_ms,
        "http_status": result.http_status,
        "response_preview": result.response_preview,
        "stderr_preview": result.stderr_preview,
        "preview": preview,
        "command": result.command,
    }


def build_status_snapshot(loop: "DispatcherLoop") -> dict[str, Any]:
    """Build a read-only snapshot dict of the dispatcher's live state.

    This function never mutates ``loop``. It reads existing attributes only and
    is resilient to concurrent mutation by the scheduler thread.
    """
    now = time.time()

    lock = getattr(loop, "_config_lock", None)
    if lock is None:
        workers_config = _safe_copy(lambda: list(loop.config.workers))
    else:
        with lock:
            workers_config = list(loop.config.workers)

    # Live, mutable state -- take defensive copies.
    running_tasks = _safe_copy(lambda: list(loop.futures.values()))
    unhealthy_until = dict(_safe_copy(lambda: list(loop.worker_unhealthy_until.items())))
    rejected_until = dict(_safe_copy(lambda: list(loop.worker_rejected_until.items())))
    runtime_project_ids = set(_safe_copy(lambda: list(loop.runtime_project_ids)))

    history_buffer = getattr(loop, "task_history", None)
    if history_buffer is None:
        history_records: list[dict[str, Any]] = []
    else:
        history_records = _safe_copy(lambda: list(history_buffer))

    # Per-worker running counts.
    running_counts: dict[str, int] = {}
    for task in running_tasks:
        running_counts[task.worker_name] = running_counts.get(task.worker_name, 0) + 1

    workers_payload: list[dict[str, Any]] = []
    for worker in workers_config:
        running = running_counts.get(worker.name, 0)
        unhealthy_at = unhealthy_until.get(worker.name, 0.0)
        is_unhealthy = unhealthy_at > now
        if running > 0:
            status = "busy"
        elif not worker.enabled:
            status = "disabled"
        elif is_unhealthy:
            status = "unhealthy"
        else:
            status = "idle"
        workers_payload.append(
            {
                "name": worker.name,
                "type": worker.type,
                "enabled": worker.enabled,
                "task_types": list(worker.task_types),
                "max_running": worker.max_running,
                "priority": worker.priority,
                "running": running,
                "status": status,
                "unhealthy": is_unhealthy,
                "unhealthy_seconds_remaining": round(max(0.0, unhealthy_at - now), 3) if is_unhealthy else None,
            }
        )

    running_payload: list[dict[str, Any]] = []
    for task in running_tasks:
        started_at = getattr(task, "started_at", None)
        running_seconds = round(max(0.0, now - started_at), 3) if isinstance(started_at, (int, float)) else None
        if task.intent_id is not None:
            description = f"{task.task_type} project={task.project_id} intent={task.intent_id}"
        else:
            description = f"{task.task_type} project={task.project_id}"
        running_payload.append(
            {
                "project_id": task.project_id,
                "task_type": task.task_type,
                "worker_name": task.worker_name,
                "intent_id": task.intent_id,
                "current_task": _truncate(description),
                "started_at": started_at,
                "running_seconds": running_seconds,
            }
        )

    history_payload: list[dict[str, Any]] = []
    for record in history_records:
        if not isinstance(record, dict):
            continue
        history_payload.append(dict(record))
    # Most recent first.
    history_payload.reverse()

    heartbeats_payload: dict[str, dict[str, Any]] = {}
    for worker_name, until in unhealthy_until.items():
        heartbeats_payload[worker_name] = {
            "unhealthy_until": until,
            "seconds_remaining": round(max(0.0, until - now), 3),
            "unhealthy": until > now,
        }

    rejected_payload: list[dict[str, Any]] = []
    for key, until in rejected_until.items():
        try:
            project_id, task_type, worker_name = key
        except (ValueError, TypeError):
            continue
        rejected_payload.append(
            {
                "project_id": project_id,
                "task_type": task_type,
                "worker_name": worker_name,
                "rejected_until": until,
                "seconds_remaining": round(max(0.0, until - now), 3),
                "rejected": until > now,
            }
        )

    runtime = loop.config.runtime
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "now": now,
        "runtime": {
            "max_workers": runtime.max_workers,
            "max_running_projects": runtime.max_running_projects,
            "max_project_workers": runtime.max_project_workers,
            "interval": runtime.interval,
            "running_task_count": len(running_tasks),
            "running_project_count": len(runtime_project_ids),
        },
        "workers": workers_payload,
        "running_tasks": running_payload,
        "task_history": history_payload,
        "heartbeats": heartbeats_payload,
        "rejections": rejected_payload,
    }


def create_internal_app(loop: "DispatcherLoop"):
    """Create the minimal FastAPI app exposing dispatcher internal endpoints."""
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Cairn Dispatcher Internal API", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/internal/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "now": time.time()}

    @app.get("/internal/status")
    def status() -> dict[str, Any]:
        # build_status_snapshot is read-only and never raises; if it somehow
        # does, FastAPI returns a 500 and the dispatcher loop is unaffected.
        return build_status_snapshot(loop)

    @app.get(WORKER_CONFIG_PATH)
    def worker_config() -> dict[str, Any]:
        return _workers_config_payload(loop)

    @app.put(WORKER_CONFIG_PATH)
    def update_worker_config(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            workers = _validate_worker_payloads(loop, payload)
            config = _config_with_workers(loop, workers)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc

        try:
            _write_dispatch_config(loop, config)
        except Exception as exc:
            LOG.warning("failed to persist dispatcher worker config; keeping current config", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"message": f"failed to write dispatcher config: {exc}"},
            ) from exc

        apply_config = getattr(loop, "apply_config", None)
        if callable(apply_config):
            apply_config(config)
        else:  # pragma: no cover - compatibility fallback for older loops
            loop.config = config
        return _workers_config_payload(loop)

    @app.post(WORKER_TEST_PATH)
    def test_worker(payload: dict[str, Any]) -> dict[str, Any]:
        worker_payload = payload.get("worker") if isinstance(payload, dict) else None
        if not isinstance(worker_payload, dict):
            raise HTTPException(status_code=422, detail={"message": "worker must be an object"})
        try:
            worker_data = _preserve_masked_secrets([worker_payload], _existing_workers_by_name(loop))[0]
            worker = WorkerConfig.model_validate(worker_data)
            config = _config_with_workers(loop, [worker])
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc

        try:
            result = run_single_startup_healthcheck(config, loop.container_manager, worker)
        except Exception as exc:
            LOG.warning("worker connectivity test failed before command execution worker=%s", worker.name, exc_info=True)
            return {
                "worker_name": worker.name,
                "ok": False,
                "returncode": 1,
                "duration_ms": 0,
                "http_status": None,
                "response_preview": "",
                "stderr_preview": str(exc),
                "preview": str(exc),
                "command": "-",
            }
        return _healthcheck_payload(result)

    return app


def start_internal_api(
    loop: "DispatcherLoop",
    *,
    host: str | None = None,
    port: int | None = None,
    history_size: int = 200,
) -> bool:
    """Start the internal API server on a daemon thread, if opted-in.

    Returns ``True`` if the server thread was started, ``False`` otherwise. This
    function is non-fatal: any failure is logged and swallowed so the dispatcher
    keeps running.
    """
    if not is_internal_api_enabled():
        LOG.debug("dispatcher internal API disabled (set %s=1 to enable)", ENABLE_ENV)
        return False

    resolved_host = host if host is not None else _resolve_host()
    resolved_port = port if port is not None else _resolve_port()

    try:
        import uvicorn

        # Turn on the optional, default-off task-history buffer so the status
        # endpoint can report recently completed tasks.
        enable_tracking = getattr(loop, "enable_internal_state_tracking", None)
        if callable(enable_tracking):
            enable_tracking(history_size)

        app = create_internal_app(loop)
        config = uvicorn.Config(
            app,
            host=resolved_host,
            port=resolved_port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)

        def _serve() -> None:
            try:
                server.run()
            except Exception:  # pragma: no cover - defensive only
                LOG.warning("dispatcher internal API server stopped unexpectedly", exc_info=True)

        thread = threading.Thread(
            target=_serve,
            name="cairn-dispatcher-internal-api",
            daemon=True,
        )
        thread.start()
        LOG.info("dispatcher internal API listening on http://%s:%s/internal/status", resolved_host, resolved_port)
        return True
    except Exception:
        LOG.warning(
            "failed to start dispatcher internal API on %s:%s; dispatcher continues normally",
            resolved_host,
            resolved_port,
            exc_info=True,
        )
        return False
