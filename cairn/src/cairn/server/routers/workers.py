"""Worker dashboard router.

Additive router exposing the ``/api/workers`` endpoints that back the worker
dashboard. Live worker state and runtime worker configuration are proxied over
HTTP to the dispatcher's optional internal API
(``cairn.dispatcher.internal_api``), while per-worker task history is read from
the ``worker_task_history`` table created by :mod:`cairn.server.product_db`.

Two endpoints are provided:

* ``GET /api/workers`` — proxies to the dispatcher internal ``/internal/status``
  endpoint and reshapes the live snapshot into a list of :class:`WorkerStatus`
  cards (name, type, status, current task, tasks completed, average duration,
  last heartbeat). Requirements 9.1, 9.4, 10.1, 10.2, 10.4, 10.5.
* ``GET /api/workers/{name}/history`` — returns the 20 most recent tasks for a
  worker from the ``worker_task_history`` table, joined to ``projects`` for the
  source project name. Requirements 11.1, 11.2, 11.3.

The dispatcher internal endpoint is **optional** and may be unreachable (it is
opt-in and localhost-only by default). When the proxy call fails, the status
endpoint degrades gracefully to a ``503`` connectivity warning rather than
raising (requirement 9.5, design "Worker Dashboard Errors").

The internal endpoint URL is configurable via the ``CAIRN_DISPATCHER_INTERNAL_URL``
environment variable (default ``http://127.0.0.1:8989`` — matching the internal
API's default host/port), and the proxy uses a short timeout so a hung or absent
dispatcher never blocks the dashboard.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Path

from cairn.server.db import get_conn
from cairn.server.workers_models import (
    WorkerConfigResponse,
    WorkerConfigUpdate,
    WorkerConnectionTestRequest,
    WorkerConnectionTestResult,
    WorkerStatus,
    WorkerTaskHistoryEntry,
)

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workers", tags=["workers"])

# Configuration for the proxy call to the dispatcher's internal API.
# The base URL is configurable so the product server and dispatcher can run on
# different hosts/ports; the default matches the internal API's own defaults
# (127.0.0.1:8989). A short timeout keeps the dashboard responsive even when the
# dispatcher is absent or unresponsive.
INTERNAL_URL_ENV = "CAIRN_DISPATCHER_INTERNAL_URL"
INTERNAL_TIMEOUT_ENV = "CAIRN_DISPATCHER_INTERNAL_TIMEOUT"
# Test/config operations (connectivity test, config read/write) are genuinely
# slow — the dispatcher spins up a startup container, execs the worker CLI, and
# tears it down — so they need a dedicated, longer timeout than the
# latency-sensitive status poll. The 30.0s default comfortably exceeds the
# dispatcher's own 20s healthcheck_timeout (dispatch.yaml).
TEST_TIMEOUT_ENV = "CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT"
DEFAULT_INTERNAL_URL = "http://127.0.0.1:8989"
DEFAULT_INTERNAL_TIMEOUT = 2.0
DEFAULT_INTERNAL_TEST_TIMEOUT = 30.0
STATUS_PATH = "/internal/status"
CONFIG_PATH = "/internal/workers/config"
TEST_PATH = "/internal/workers/test"

# The number of most-recent history rows returned per worker (requirement 11.1).
HISTORY_LIMIT = 20


def _status_url() -> str:
    """Resolve the dispatcher internal ``/internal/status`` URL from the env.

    The configured value is treated as the dispatcher internal API *base* URL;
    the ``/internal/status`` path is appended. Falls back to the localhost
    default when unset/blank.
    """
    base = os.environ.get(INTERNAL_URL_ENV, "").strip() or DEFAULT_INTERNAL_URL
    return f"{base.rstrip('/')}{STATUS_PATH}"


def _internal_url(path: str) -> str:
    base = os.environ.get(INTERNAL_URL_ENV, "").strip() or DEFAULT_INTERNAL_URL
    return f"{base.rstrip('/')}{path}"


def _resolve_timeout(env_name: str, default: float) -> float:
    """Resolve a proxy request timeout (seconds) from an env var.

    Shared parse/fallback logic for the status-polling and test/config timeout
    resolvers: an unset, blank, non-numeric, or non-positive value falls back to
    ``default``; a valid positive value is honored. Invalid input is logged with
    a warning so misconfiguration is visible.
    """
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    try:
        timeout = float(raw)
    except ValueError:
        LOG.warning(
            "invalid %s=%r; falling back to default %.1fs",
            env_name,
            raw,
            default,
        )
        return default
    return timeout if timeout > 0 else default


def _status_timeout() -> float:
    """Resolve the status-polling proxy request timeout (seconds) from the env."""
    return _resolve_timeout(INTERNAL_TIMEOUT_ENV, DEFAULT_INTERNAL_TIMEOUT)


def _test_timeout() -> float:
    """Resolve the test/config proxy request timeout (seconds) from the env.

    Mirrors :func:`_status_timeout` parsing/fallback semantics but reads the
    dedicated ``CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT`` var and falls back to
    the longer ``DEFAULT_INTERNAL_TEST_TIMEOUT`` (30.0s), so slow-but-healthy
    test/config operations are not bound by the short status-polling timeout.
    """
    return _resolve_timeout(TEST_TIMEOUT_ENV, DEFAULT_INTERNAL_TEST_TIMEOUT)


def _fetch_status_snapshot() -> dict[str, Any]:
    """Proxy to the dispatcher internal status endpoint and return its JSON.

    Raises :class:`fastapi.HTTPException` (503) when the dispatcher is
    unreachable, returns a non-200 response, or returns a malformed body. The
    503 carries the design-mandated connectivity warning payload so the
    dashboard can surface the last successful update time (here ``None`` because
    this stateless proxy keeps no cache).
    """
    url = _status_url()
    try:
        response = requests.get(url, timeout=_status_timeout())
    except requests.RequestException as exc:
        LOG.warning("dispatcher internal status unreachable url=%s error=%s", url, exc)
        raise _unavailable_exception()

    if response.status_code != 200:
        LOG.warning(
            "dispatcher internal status returned status=%s url=%s",
            response.status_code,
            url,
        )
        raise _unavailable_exception()

    try:
        payload = response.json()
    except ValueError:
        LOG.warning("dispatcher internal status returned non-JSON body url=%s", url)
        raise _unavailable_exception()

    if not isinstance(payload, dict):
        LOG.warning("dispatcher internal status returned unexpected JSON shape url=%s", url)
        raise _unavailable_exception()
    return payload


def _request_internal_json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    unavailable_message: str,
    timeout: float,
) -> dict[str, Any]:
    url = _internal_url(path)
    try:
        response = requests.request(method, url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        LOG.warning("dispatcher internal request unreachable method=%s url=%s error=%s", method, url, exc)
        raise HTTPException(status_code=503, detail={"message": unavailable_message, "last_updated": None})

    try:
        body = response.json()
    except ValueError:
        body = None

    if response.status_code >= 400:
        detail: Any = {"message": unavailable_message}
        if isinstance(body, dict):
            detail = body.get("detail", body)
        raise HTTPException(status_code=response.status_code, detail=detail)

    if not isinstance(body, dict):
        LOG.warning("dispatcher internal request returned unexpected JSON method=%s url=%s", method, url)
        raise HTTPException(status_code=503, detail={"message": unavailable_message, "last_updated": None})
    return body


def _unavailable_exception() -> HTTPException:
    """Build the 503 connectivity-warning error (design: dispatcher unreachable)."""
    return HTTPException(
        status_code=503,
        detail={"message": "Worker status unavailable", "last_updated": None},
    )


def _map_status(internal_status: Any, *, enabled: bool, is_unhealthy: bool, running: int) -> str:
    """Map the internal worker status onto the dashboard's idle/busy/offline.

    The internal API reports ``idle``/``busy``/``unhealthy``. A worker whose
    heartbeat health window marks it unhealthy is surfaced as ``offline``
    (requirement 10.5). Otherwise a worker with running tasks is ``busy`` and any
    other worker is ``idle`` (requirements 9.1, 10.4/10.5 via heartbeat health).
    """
    if not enabled or internal_status == "disabled":
        return "disabled"
    if is_unhealthy or internal_status == "unhealthy" or internal_status == "offline":
        return "offline"
    if internal_status == "busy" or running > 0:
        return "busy"
    return "idle"


def _current_task_by_worker(snapshot: dict[str, Any]) -> dict[str, str]:
    """Index the first running-task description per worker from the snapshot."""
    by_worker: dict[str, str] = {}
    running_tasks = snapshot.get("running_tasks")
    if not isinstance(running_tasks, list):
        return by_worker
    for task in running_tasks:
        if not isinstance(task, dict):
            continue
        name = task.get("worker_name")
        description = task.get("current_task")
        if not isinstance(name, str) or not isinstance(description, str):
            continue
        # Keep the first running task seen for a worker as its current task.
        by_worker.setdefault(name, description)
    return by_worker


def _completed_metrics_by_worker(
    snapshot: dict[str, Any],
) -> dict[str, tuple[int, float | None]]:
    """Aggregate completed-task count and average duration per worker.

    Derived from the snapshot's ``task_history`` ring buffer. The count is the
    number of recorded completed tasks for the worker (requirement 10.1); the
    average duration is the mean of the recorded ``duration_seconds`` rounded to
    one decimal place (requirement 10.2), or ``None`` when no durations are
    recorded so the dashboard can show a dash (requirement 10.3).
    """
    counts: dict[str, int] = {}
    duration_sums: dict[str, float] = {}
    duration_counts: dict[str, int] = {}

    history = snapshot.get("task_history")
    if isinstance(history, list):
        for record in history:
            if not isinstance(record, dict):
                continue
            name = record.get("worker_name")
            if not isinstance(name, str):
                continue
            counts[name] = counts.get(name, 0) + 1
            duration = record.get("duration_seconds")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                duration_sums[name] = duration_sums.get(name, 0.0) + float(duration)
                duration_counts[name] = duration_counts.get(name, 0) + 1

    metrics: dict[str, tuple[int, float | None]] = {}
    for name, count in counts.items():
        n_durations = duration_counts.get(name, 0)
        avg = round(duration_sums[name] / n_durations, 1) if n_durations > 0 else None
        metrics[name] = (count, avg)
    return metrics


def _heartbeat_seconds_ago(snapshot: dict[str, Any], worker_name: str) -> float | None:
    """Best-effort time-since-last-heartbeat for a worker, else ``None``.

    The internal snapshot does not expose an explicit per-worker last-heartbeat
    timestamp in its base shape, so this reads an optional ``last_heartbeat_seconds_ago``
    field from the per-worker ``heartbeats`` entry when present and returns
    ``None`` otherwise (the dashboard renders ``None`` as "no data").
    """
    heartbeats = snapshot.get("heartbeats")
    if not isinstance(heartbeats, dict):
        return None
    entry = heartbeats.get(worker_name)
    if not isinstance(entry, dict):
        return None
    value = entry.get("last_heartbeat_seconds_ago")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


@router.get("", response_model=list[WorkerStatus])
def list_workers() -> list[WorkerStatus]:
    """Return the live status of every registered worker.

    Proxies to the dispatcher's internal status endpoint and reshapes
    the snapshot into one :class:`WorkerStatus` per registered worker
    (requirement 9.1). For a busy worker, the current task description is
    included (truncated to 120 characters by the model — requirement 9.4). Health
    metrics — tasks completed (requirement 10.1), average duration (requirements
    10.2, 10.3), and last heartbeat (requirement 10.4) — are derived from the
    snapshot. A worker whose heartbeat health window has lapsed is reported as
    ``offline`` (requirement 10.5).

    When the dispatcher internal endpoint is unreachable, returns a ``503`` with
    a connectivity warning rather than raising (requirement 9.5).
    """
    snapshot = _fetch_status_snapshot()

    workers = snapshot.get("workers")
    if not isinstance(workers, list):
        return []

    current_tasks = _current_task_by_worker(snapshot)
    completed_metrics = _completed_metrics_by_worker(snapshot)

    result: list[WorkerStatus] = []
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        name = worker.get("name")
        if not isinstance(name, str):
            continue
        worker_type = worker.get("type")
        running = worker.get("running")
        running_count = running if isinstance(running, int) and not isinstance(running, bool) else 0
        is_unhealthy = bool(worker.get("unhealthy"))
        enabled = bool(worker.get("enabled", True))

        status = _map_status(
            worker.get("status"),
            enabled=enabled,
            is_unhealthy=is_unhealthy,
            running=running_count,
        )

        # Only busy workers surface a current task (requirement 9.4).
        current_task = current_tasks.get(name) if status == "busy" else None

        tasks_completed, avg_duration = completed_metrics.get(name, (0, None))

        result.append(
            WorkerStatus(
                name=name,
                type=worker_type if isinstance(worker_type, str) else "",
                enabled=enabled,
                status=status,
                current_task=current_task,
                tasks_completed=tasks_completed,
                avg_duration_seconds=avg_duration,
                last_heartbeat_seconds_ago=_heartbeat_seconds_ago(snapshot, name),
            )
        )
    return result


@router.get("/config", response_model=WorkerConfigResponse)
def get_worker_config() -> WorkerConfigResponse:
    """Return editable worker configuration with secret values masked."""
    payload = _request_internal_json(
        "GET",
        CONFIG_PATH,
        unavailable_message="Worker config unavailable",
        timeout=_test_timeout(),
    )
    return WorkerConfigResponse.model_validate(payload)


@router.put("/config", response_model=WorkerConfigResponse)
def update_worker_config(config: WorkerConfigUpdate) -> WorkerConfigResponse:
    """Persist and apply the dispatcher worker list.

    The dispatcher validates and writes the underlying YAML file before applying
    the new config, so validation/write failures leave active scheduling intact.
    """
    payload = _request_internal_json(
        "PUT",
        CONFIG_PATH,
        payload=config.model_dump(mode="json"),
        unavailable_message="Worker config update failed",
        timeout=_test_timeout(),
    )
    try:
        from cairn.server.activity_service import record_audit

        worker_count = len(config.workers) if getattr(config, "workers", None) is not None else 0
        record_audit("worker.config", f"更新工作节点配置（{worker_count} 个节点）", target_type="worker")
    except Exception:
        pass
    return WorkerConfigResponse.model_validate(payload)


@router.post("/config/test", response_model=WorkerConnectionTestResult)
def test_worker_config(request: WorkerConnectionTestRequest) -> WorkerConnectionTestResult:
    """Run the dispatcher's own startup healthcheck for a draft worker config."""
    payload = _request_internal_json(
        "POST",
        TEST_PATH,
        payload=request.model_dump(mode="json"),
        unavailable_message="Worker connectivity test failed",
        timeout=_test_timeout(),
    )
    return WorkerConnectionTestResult.model_validate(payload)


def _build_history_description(
    task_type: str, project_name: str, intent_id: str | None
) -> str:
    """Compose a human-readable description for a history row.

    The ``worker_task_history`` table does not persist a free-form description,
    so one is synthesized from the task type, source project, and (when present)
    the intent the task acted on (requirement 11.2).
    """
    if intent_id:
        return f"{task_type} on {project_name} (intent {intent_id})"
    return f"{task_type} on {project_name}"


@router.get("/{name}/history", response_model=list[WorkerTaskHistoryEntry])
def worker_history(
    name: str = Path(..., min_length=1),
) -> list[WorkerTaskHistoryEntry]:
    """Return the 20 most recent tasks executed by a worker.

    Reads from the ``worker_task_history`` table (joined to ``projects`` for the
    source project name) and returns at most :data:`HISTORY_LIMIT` rows ordered
    most-recent-first (requirement 11.1). Each entry carries the project name,
    task type, a synthesized description, start time, duration, and outcome
    (requirements 11.2, 11.3). A worker with no recorded history returns an empty
    list.

    Ordering uses ``COALESCE(completed_at, started_at)`` so in-flight or released
    tasks (which have no ``completed_at``) still sort by their start time, with
    the row id as a stable tiebreaker.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                h.task_type,
                h.intent_id,
                h.started_at,
                h.duration_seconds,
                h.outcome,
                h.project_id,
                COALESCE(p.title, h.project_id) AS project_name
            FROM worker_task_history h
            LEFT JOIN projects p ON p.id = h.project_id
            WHERE h.worker_name = ?
            ORDER BY COALESCE(h.completed_at, h.started_at) DESC, h.id DESC
            LIMIT ?
            """,
            (name, HISTORY_LIMIT),
        ).fetchall()

    entries: list[WorkerTaskHistoryEntry] = []
    for row in rows:
        project_name = row["project_name"]
        entries.append(
            WorkerTaskHistoryEntry(
                project_name=project_name,
                task_type=row["task_type"],
                description=_build_history_description(
                    row["task_type"], project_name, row["intent_id"]
                ),
                started_at=row["started_at"],
                duration_seconds=row["duration_seconds"],
                outcome=row["outcome"],
            )
        )
    return entries
