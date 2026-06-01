"""Unit tests for the worker dashboard router (spec task 5.5).

Target: ``cairn.server.routers.workers`` mounted on a minimal FastAPI app.

Covers requirements:

* 9.1 / 9.4 / 10.1-10.5 -- ``GET /api/workers`` reshapes the dispatcher's
  read-only internal status snapshot into ``WorkerStatus`` cards: status mapping
  (idle / busy / offline), current-task truncation to 120 characters for busy
  workers, tasks-completed counts, average-duration rounding, and heartbeat age.
* 9.5 -- when the dispatcher internal status endpoint is UNREACHABLE the router
  degrades gracefully to a ``503`` connectivity warning instead of crashing with
  a ``500``.
* 11.1-11.3 -- ``GET /api/workers/{name}/history`` returns up to the 20 most
  recent task-history rows (most-recent-first), each carrying project name, task
  type, synthesized description, start time, duration, and outcome.

The router proxies to the dispatcher over HTTP via ``requests.get``. To test
without a live dispatcher, that call is monkeypatched on the router module to
return a canned snapshot (reachable cases) or to raise a connection error
(unreachable case). The per-worker history is read straight from the
``worker_task_history`` table, so those tests seed the temp DB directly.
"""

from __future__ import annotations

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.routers import workers

from .conftest import BASE_URL

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workers_app(temp_db) -> FastAPI:
    """A minimal FastAPI app mounting only the workers router.

    Mounting the router standalone (no ``require_auth`` dependency) exercises the
    router's own behavior without auth friction, mirroring the auth-router test
    setup. ``temp_db`` (from conftest) gives an isolated DB with the
    ``worker_task_history`` and ``projects`` tables created.
    """
    app = FastAPI()
    app.include_router(workers.router)
    return app


@pytest.fixture
def client(workers_app) -> TestClient:
    return TestClient(workers_app, base_url=BASE_URL)


# ---------------------------------------------------------------------------
# Test doubles for the dispatcher proxy call
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Stand-in for a ``requests.Response`` returned by ``requests.get``."""

    def __init__(self, payload, status_code: int = 200, *, json_raises: bool = False):
        self._payload = payload
        self.status_code = status_code
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("no JSON body")
        return self._payload


def _patch_proxy(monkeypatch, *, response=None, exc: Exception | None = None) -> None:
    """Monkeypatch the router's ``requests.get`` to avoid any real network call."""

    def fake_get(url, timeout=None):  # noqa: ARG001 - signature mirrors requests.get
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(workers.requests, "get", fake_get)


def _patch_internal_request(monkeypatch, *, response=None, exc: Exception | None = None) -> None:
    """Monkeypatch ``requests.request`` for worker config proxy endpoints."""

    def fake_request(method, url, json=None, timeout=None):  # noqa: ARG001
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(workers.requests, "request", fake_request)


# A long description used to verify current-task truncation (req 9.4).
LONG_TASK = "explore-target " * 20  # 300 chars, well over the 120 limit


def _snapshot() -> dict:
    """A representative dispatcher status snapshot covering the mapping paths."""
    return {
        "workers": [
            # busy: running > 0 and a current task present
            {"name": "alpha", "type": "claude", "status": "busy", "running": 1, "unhealthy": False},
            # idle: no running tasks
            {"name": "beta", "type": "gpt", "status": "idle", "running": 0, "unhealthy": False},
            # offline: heartbeat health window lapsed (req 10.5)
            {"name": "gamma", "type": "mock", "status": "idle", "running": 0, "unhealthy": True},
            # disabled: configured but not participating in scheduling
            {"name": "delta", "type": "pi", "enabled": False, "status": "disabled", "running": 0, "unhealthy": False},
        ],
        "running_tasks": [
            {"worker_name": "alpha", "current_task": LONG_TASK},
        ],
        "task_history": [
            {"worker_name": "alpha", "duration_seconds": 10.0},
            {"worker_name": "alpha", "duration_seconds": 25.0},
            # beta completed a task but recorded no duration -> avg None (req 10.3)
            {"worker_name": "beta", "duration_seconds": None},
        ],
        "heartbeats": {
            "alpha": {"last_heartbeat_seconds_ago": 3.5},
        },
    }


def _by_name(payload: list[dict]) -> dict[str, dict]:
    return {w["name"]: w for w in payload}


# ---------------------------------------------------------------------------
# GET /api/workers -- status snapshot reshaping (reqs 9.1, 9.4, 10.1-10.5)
# ---------------------------------------------------------------------------


def test_list_workers_returns_one_card_per_worker(client, monkeypatch):
    _patch_proxy(monkeypatch, response=_FakeResponse(_snapshot()))

    resp = client.get("/api/workers")
    assert resp.status_code == 200
    body = resp.json()
    assert {w["name"] for w in body} == {"alpha", "beta", "gamma", "delta"}


def test_list_workers_maps_status_idle_busy_offline(client, monkeypatch):
    _patch_proxy(monkeypatch, response=_FakeResponse(_snapshot()))

    workers_by_name = _by_name(client.get("/api/workers").json())
    # running > 0 -> busy (req 9.1)
    assert workers_by_name["alpha"]["status"] == "busy"
    # no running tasks -> idle
    assert workers_by_name["beta"]["status"] == "idle"
    # unhealthy heartbeat window -> offline (req 10.5)
    assert workers_by_name["gamma"]["status"] == "offline"
    assert workers_by_name["delta"]["status"] == "disabled"


def test_list_workers_reports_name_and_type(client, monkeypatch):
    _patch_proxy(monkeypatch, response=_FakeResponse(_snapshot()))

    workers_by_name = _by_name(client.get("/api/workers").json())
    assert workers_by_name["alpha"]["type"] == "claude"
    assert workers_by_name["beta"]["type"] == "gpt"
    assert workers_by_name["gamma"]["type"] == "mock"
    assert workers_by_name["delta"]["enabled"] is False


def test_busy_worker_current_task_truncated_to_120_chars(client, monkeypatch):
    """Req 9.4: a busy worker's current task description is truncated to 120 chars."""
    _patch_proxy(monkeypatch, response=_FakeResponse(_snapshot()))

    alpha = _by_name(client.get("/api/workers").json())["alpha"]
    assert alpha["current_task"] is not None
    assert len(alpha["current_task"]) == 120
    assert alpha["current_task"] == LONG_TASK[:120]


def test_short_current_task_is_not_truncated(client, monkeypatch):
    snapshot = _snapshot()
    snapshot["running_tasks"] = [{"worker_name": "alpha", "current_task": "quick scan"}]
    _patch_proxy(monkeypatch, response=_FakeResponse(snapshot))

    alpha = _by_name(client.get("/api/workers").json())["alpha"]
    assert alpha["current_task"] == "quick scan"


def test_non_busy_workers_have_no_current_task(client, monkeypatch):
    """Req 9.4: only busy workers surface a current task description."""
    _patch_proxy(monkeypatch, response=_FakeResponse(_snapshot()))

    workers_by_name = _by_name(client.get("/api/workers").json())
    assert workers_by_name["beta"]["current_task"] is None
    assert workers_by_name["gamma"]["current_task"] is None
    assert workers_by_name["delta"]["current_task"] is None


def test_tasks_completed_counts_and_avg_duration(client, monkeypatch):
    """Reqs 10.1, 10.2, 10.3: completed counts and average duration handling."""
    _patch_proxy(monkeypatch, response=_FakeResponse(_snapshot()))

    workers_by_name = _by_name(client.get("/api/workers").json())
    # alpha: two completed tasks, avg of 10 and 25 = 17.5 (rounded to 1 dp)
    assert workers_by_name["alpha"]["tasks_completed"] == 2
    assert workers_by_name["alpha"]["avg_duration_seconds"] == 17.5
    # beta: one completed task but no recorded duration -> None (dash, req 10.3)
    assert workers_by_name["beta"]["tasks_completed"] == 1
    assert workers_by_name["beta"]["avg_duration_seconds"] is None
    # gamma: no history -> zero completed, no average
    assert workers_by_name["gamma"]["tasks_completed"] == 0
    assert workers_by_name["gamma"]["avg_duration_seconds"] is None


def test_avg_duration_is_rounded_to_one_decimal_place(client, monkeypatch):
    """Req 10.2: average task duration rounded to one decimal place."""
    snapshot = _snapshot()
    # 1 + 2 + 2 = 5 over 3 tasks -> 1.6667 -> 1.7
    snapshot["task_history"] = [
        {"worker_name": "alpha", "duration_seconds": 1.0},
        {"worker_name": "alpha", "duration_seconds": 2.0},
        {"worker_name": "alpha", "duration_seconds": 2.0},
    ]
    _patch_proxy(monkeypatch, response=_FakeResponse(snapshot))

    alpha = _by_name(client.get("/api/workers").json())["alpha"]
    assert alpha["avg_duration_seconds"] == 1.7


def test_last_heartbeat_seconds_ago_surfaced(client, monkeypatch):
    """Req 10.4: time since last heartbeat is reported when present."""
    _patch_proxy(monkeypatch, response=_FakeResponse(_snapshot()))

    workers_by_name = _by_name(client.get("/api/workers").json())
    assert workers_by_name["alpha"]["last_heartbeat_seconds_ago"] == 3.5
    # No heartbeat entry -> None (no data).
    assert workers_by_name["beta"]["last_heartbeat_seconds_ago"] is None


def test_empty_workers_snapshot_returns_empty_list(client, monkeypatch):
    _patch_proxy(monkeypatch, response=_FakeResponse({"workers": []}))
    assert client.get("/api/workers").json() == []


# ---------------------------------------------------------------------------
# GET /api/workers -- graceful degradation when dispatcher unreachable (req 9.5)
# ---------------------------------------------------------------------------


def test_unreachable_dispatcher_returns_503_not_500(client, monkeypatch):
    """Req 9.5: a connection error degrades to a 503 connectivity warning."""
    _patch_proxy(monkeypatch, exc=requests.ConnectionError("connection refused"))

    resp = client.get("/api/workers")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["message"] == "Worker status unavailable"
    assert detail["last_updated"] is None


def test_dispatcher_timeout_returns_503(client, monkeypatch):
    """A request timeout (a ``RequestException``) is handled like unreachable."""
    _patch_proxy(monkeypatch, exc=requests.Timeout("timed out"))

    resp = client.get("/api/workers")
    assert resp.status_code == 503


def test_dispatcher_non_200_returns_503(client, monkeypatch):
    """A non-200 response from the dispatcher degrades to a 503."""
    _patch_proxy(monkeypatch, response=_FakeResponse({"workers": []}, status_code=500))

    resp = client.get("/api/workers")
    assert resp.status_code == 503


def test_dispatcher_non_json_body_returns_503(client, monkeypatch):
    """A 200 with a non-JSON body degrades to a 503 rather than crashing."""
    _patch_proxy(monkeypatch, response=_FakeResponse(None, json_raises=True))

    resp = client.get("/api/workers")
    assert resp.status_code == 503


def test_worker_config_proxy_returns_masked_config(client, monkeypatch):
    _patch_internal_request(
        monkeypatch,
        response=_FakeResponse(
            {
                "workers": [
                    {
                        "name": "mock-1",
                        "type": "mock",
                        "enabled": True,
                        "task_types": ["bootstrap"],
                        "max_running": 1,
                        "priority": 0,
                        "env": {},
                        "secret_env_keys": [],
                    }
                ]
            }
        ),
    )

    resp = client.get("/api/workers/config")
    assert resp.status_code == 200
    assert resp.json()["workers"][0]["name"] == "mock-1"


def test_worker_config_update_proxies_payload(client, monkeypatch):
    seen = {}

    def fake_request(method, url, json=None, timeout=None):  # noqa: ARG001
        seen["method"] = method
        seen["json"] = json
        return _FakeResponse(json)

    monkeypatch.setattr(workers.requests, "request", fake_request)

    payload = {
        "workers": [
            {
                "name": "mock-1",
                "type": "mock",
                "enabled": True,
                "task_types": ["bootstrap"],
                "max_running": 1,
                "priority": 0,
                "env": {},
                "secret_env_keys": [],
            }
        ]
    }
    resp = client.put("/api/workers/config", json=payload)

    assert resp.status_code == 200
    assert seen["method"] == "PUT"
    assert seen["json"]["workers"][0]["name"] == "mock-1"


def test_worker_config_test_proxy_returns_result(client, monkeypatch):
    _patch_internal_request(
        monkeypatch,
        response=_FakeResponse(
            {
                "worker_name": "mock-1",
                "ok": True,
                "returncode": 0,
                "duration_ms": 12,
                "http_status": None,
                "response_preview": "pong",
                "stderr_preview": "",
                "preview": "pong",
                "command": "python3 -c ...",
            }
        ),
    )

    resp = client.post(
        "/api/workers/config/test",
        json={
            "worker": {
                "name": "mock-1",
                "type": "mock",
                "enabled": True,
                "task_types": ["bootstrap"],
                "max_running": 1,
                "priority": 0,
                "env": {},
                "secret_env_keys": [],
            }
        },
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# GET /api/workers/{name}/history (reqs 11.1, 11.2, 11.3)
# ---------------------------------------------------------------------------


def _seed_project(project_id: str, title: str) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, title, created_at) VALUES (?, ?, ?)",
            (project_id, title, "2024-01-01T00:00:00Z"),
        )


def _insert_history(
    *,
    worker_name: str,
    project_id: str,
    task_type: str = "explore",
    intent_id: str | None = None,
    started_at: str,
    completed_at: str | None = None,
    duration_seconds: float | None = None,
    outcome: str = "success",
) -> None:
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO worker_task_history
                (worker_name, project_id, task_type, intent_id, started_at,
                 completed_at, duration_seconds, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                worker_name,
                project_id,
                task_type,
                intent_id,
                started_at,
                completed_at,
                duration_seconds,
                outcome,
            ),
        )


def test_history_returns_at_most_20_most_recent(client, temp_db):
    """Req 11.1: at most the 20 most recent tasks are returned, newest first."""
    _seed_project("proj-1", "Target Project")
    # Insert 25 rows with increasing completion times so ordering is deterministic.
    for i in range(25):
        _insert_history(
            worker_name="alpha",
            project_id="proj-1",
            started_at=f"2024-01-01T00:{i:02d}:00Z",
            completed_at=f"2024-01-01T01:{i:02d}:00Z",
            duration_seconds=float(i),
        )

    body = client.get("/api/workers/alpha/history").json()
    assert len(body) == 20
    # Most recent completion (i == 24) is first; oldest of the 20 (i == 5) is last.
    assert body[0]["started_at"] == "2024-01-01T00:24:00Z"
    assert body[-1]["started_at"] == "2024-01-01T00:05:00Z"


def test_history_orders_most_recent_first(client, temp_db):
    _seed_project("proj-1", "Target Project")
    _insert_history(
        worker_name="alpha",
        project_id="proj-1",
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T00:10:00Z",
    )
    _insert_history(
        worker_name="alpha",
        project_id="proj-1",
        started_at="2024-01-02T00:00:00Z",
        completed_at="2024-01-02T00:10:00Z",
    )

    body = client.get("/api/workers/alpha/history").json()
    assert [e["started_at"] for e in body] == [
        "2024-01-02T00:00:00Z",
        "2024-01-01T00:00:00Z",
    ]


def test_history_entry_carries_all_required_fields(client, temp_db):
    """Req 11.2: each entry has project name, task type, description, start, duration, outcome."""
    _seed_project("proj-1", "Target Project")
    _insert_history(
        worker_name="alpha",
        project_id="proj-1",
        task_type="explore",
        intent_id="i24",
        started_at="2024-01-01T00:24:00Z",
        completed_at="2024-01-01T00:30:00Z",
        duration_seconds=24.0,
        outcome="success",
    )

    entry = client.get("/api/workers/alpha/history").json()[0]
    assert entry["project_name"] == "Target Project"
    assert entry["task_type"] == "explore"
    assert entry["description"] == "explore on Target Project (intent i24)"
    assert entry["started_at"] == "2024-01-01T00:24:00Z"
    assert entry["duration_seconds"] == 24.0
    assert entry["outcome"] == "success"


def test_history_description_without_intent(client, temp_db):
    _seed_project("proj-1", "Target Project")
    _insert_history(
        worker_name="alpha",
        project_id="proj-1",
        task_type="reason",
        intent_id=None,
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T00:05:00Z",
        outcome="success",
    )

    entry = client.get("/api/workers/alpha/history").json()[0]
    assert entry["description"] == "reason on Target Project"


def test_history_released_task_has_null_duration_and_project_fallback(client, temp_db):
    """Req 11.3: a released task (no duration) is reported, with project id fallback."""
    # No project row seeded -> project_name falls back to the project_id.
    _insert_history(
        worker_name="alpha",
        project_id="proj-missing",
        task_type="reason",
        intent_id=None,
        started_at="2024-01-02T00:00:00Z",
        completed_at=None,
        duration_seconds=None,
        outcome="released",
    )

    entry = client.get("/api/workers/alpha/history").json()[0]
    assert entry["project_name"] == "proj-missing"
    assert entry["duration_seconds"] is None
    assert entry["outcome"] == "released"


@pytest.mark.parametrize("outcome", ["success", "failed", "rejected", "released"])
def test_history_supports_all_outcome_states(client, temp_db, outcome):
    """Req 11.3: success / failed / rejected / released outcomes are all surfaced."""
    _seed_project("proj-1", "Target Project")
    _insert_history(
        worker_name="alpha",
        project_id="proj-1",
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T00:05:00Z",
        duration_seconds=5.0,
        outcome=outcome,
    )

    entry = client.get("/api/workers/alpha/history").json()[0]
    assert entry["outcome"] == outcome


def test_history_unknown_worker_returns_empty_list(client, temp_db):
    _seed_project("proj-1", "Target Project")
    _insert_history(
        worker_name="alpha",
        project_id="proj-1",
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T00:05:00Z",
        duration_seconds=5.0,
    )

    assert client.get("/api/workers/ghost/history").json() == []


def test_history_only_returns_rows_for_requested_worker(client, temp_db):
    _seed_project("proj-1", "Target Project")
    _insert_history(
        worker_name="alpha",
        project_id="proj-1",
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T00:05:00Z",
        duration_seconds=5.0,
    )
    _insert_history(
        worker_name="beta",
        project_id="proj-1",
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T00:05:00Z",
        duration_seconds=5.0,
    )

    body = client.get("/api/workers/alpha/history").json()
    assert len(body) == 1
