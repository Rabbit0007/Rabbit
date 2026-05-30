"""Optional, read-only internal status API for the dispatcher.

This module exposes a *read-only* view of the live ``DispatcherLoop`` state so
that the product server's worker dashboard can poll it. It is intentionally
defensive and completely optional:

* It is **opt-in**: it only starts when ``CAIRN_DISPATCHER_INTERNAL_API`` is set
  to a truthy value. Existing deployments are unaffected by default.
* It is **non-fatal**: startup is wrapped in ``try/except`` and runs on a daemon
  thread. If the port is in use or anything goes wrong, the dispatcher keeps
  running normally.
* It is **localhost-only** by default (``127.0.0.1``) on a configurable port
  (default ``8989``).
* It **never mutates** scheduler state. The status endpoint only reads existing
  fields, taking defensive copies to tolerate concurrent mutation from the
  scheduler thread.

The scheduler loop itself is not modified by importing this module. The only
hooks into the loop are:

* an optional, default-off ``DispatcherLoop.task_history`` ring buffer, and
* ``DispatcherLoop.enable_internal_state_tracking`` to turn it on.

Both are inert unless this internal API is explicitly enabled.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cairn.dispatcher.scheduler.loop import DispatcherLoop

LOG = logging.getLogger(__name__)

ENABLE_ENV = "CAIRN_DISPATCHER_INTERNAL_API"
HOST_ENV = "CAIRN_DISPATCHER_INTERNAL_HOST"
PORT_ENV = "CAIRN_DISPATCHER_INTERNAL_PORT"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8989
TASK_DESCRIPTION_MAX = 120

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


def build_status_snapshot(loop: "DispatcherLoop") -> dict[str, Any]:
    """Build a read-only snapshot dict of the dispatcher's live state.

    This function never mutates ``loop``. It reads existing attributes only and
    is resilient to concurrent mutation by the scheduler thread.
    """
    now = time.time()

    # Static config: workers are loaded once and not mutated at runtime.
    workers_config = _safe_copy(lambda: list(loop.config.workers))

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
        if is_unhealthy:
            status = "unhealthy"
        elif running > 0:
            status = "busy"
        else:
            status = "idle"
        workers_payload.append(
            {
                "name": worker.name,
                "type": worker.type,
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
    """Create the minimal FastAPI app exposing the read-only status endpoint."""
    from fastapi import FastAPI

    app = FastAPI(title="Cairn Dispatcher Internal API", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/internal/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "now": time.time()}

    @app.get("/internal/status")
    def status() -> dict[str, Any]:
        # build_status_snapshot is read-only and never raises; if it somehow
        # does, FastAPI returns a 500 and the dispatcher loop is unaffected.
        return build_status_snapshot(loop)

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
