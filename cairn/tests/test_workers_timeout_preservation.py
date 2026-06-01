"""Preservation property tests for the worker connectivity test timeout bugfix.

Spec: ``.kiro/specs/worker-connectivity-test-timeout`` — Task 2
(observation-first / Property 2: Preservation).

These tests capture the **baseline behavior that must NOT change** when the fix
splits the proxy timeout into a short status-polling timeout and a dedicated
longer test/config timeout. They encode **Property 2 (Preservation)** from the
design:

    For any input where the bug condition does NOT hold (``isBugCondition``
    returns false) — status polling, genuinely unreachable/erroring dispatchers,
    fast operations, and the ``CAIRN_DISPATCHER_INTERNAL_TIMEOUT`` fallback path —
    the fixed proxy SHALL produce the same result as the original proxy.

``isBugCondition(X)`` (design): ``X.kind ∈ {TEST, CONFIG_GET, CONFIG_PUT}`` AND
``X.dispatcherWouldSucceed`` AND ``X.dispatcherLatencySeconds > X.statusTimeout``.

**These tests MUST PASS on the current (unfixed) code** — they record the
observed baseline. They are re-run unchanged in Task 3.6 and must still PASS,
proving the fix introduced no regressions for non-buggy inputs.

The baseline outputs asserted here were observed by running the UNFIXED router
against a latency-aware fake (see the spec's observation step):

* STATUS success → 200 with the reshaped ``WorkerStatus`` cards; ``requests.get``
  invoked with ``timeout`` = the resolved status timeout (2.0s default).
* STATUS unreachable / non-200 / slow-timeout → 503
  ``{"message": "Worker status unavailable", "last_updated": null}``.
* Status-timeout fallback: ``""`` / ``"abc"`` / ``"-1"`` / ``"0"`` → 2.0; a valid
  positive value (e.g. ``"5.0"``) is honored.
* TEST success → 200 ``{"ok": true, ...}``; within-timeout ``{"ok": false}`` → 200
  propagated unchanged; non-2xx → that status + body propagated; unreachable →
  503 ``"Worker connectivity test failed"``.
* CONFIG_GET unreachable → 503 ``"Worker config unavailable"``; CONFIG_PUT
  unreachable → 503 ``"Worker config update failed"``.

Conventions mirror ``tests/test_workers_router.py`` /
``tests/test_workers_timeout_bugfix.py``: mount the ``workers`` router on a
minimal FastAPI app and monkeypatch ``workers.requests`` with latency-aware
fakes — no real network or dispatcher is needed.
"""

from __future__ import annotations

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from cairn.server.routers import workers

from .conftest import BASE_URL

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workers_app(temp_db) -> FastAPI:
    """A minimal FastAPI app mounting only the workers router."""
    app = FastAPI()
    app.include_router(workers.router)
    return app


@pytest.fixture
def client(workers_app) -> TestClient:
    return TestClient(workers_app, base_url=BASE_URL)


@pytest.fixture(autouse=True)
def _clear_timeout_env(monkeypatch):
    """Start each test with the timeout env vars unset so defaults apply."""
    monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", raising=False)
    monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", raising=False)


# ---------------------------------------------------------------------------
# Constants observed from the UNFIXED baseline
# ---------------------------------------------------------------------------

DEFAULT_STATUS_TIMEOUT = 2.0

_UNAVAILABLE_MESSAGE = {
    "STATUS": "Worker status unavailable",
    "TEST": "Worker connectivity test failed",
    "CONFIG_GET": "Worker config unavailable",
    "CONFIG_PUT": "Worker config update failed",
}

# A 300-char description that must be truncated to 120 chars for a busy worker.
LONG_TASK = "explore-target " * 20


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Stand-in for a ``requests.Response``."""

    def __init__(self, payload, status_code: int = 200, *, json_raises: bool = False):
        self._payload = payload
        self.status_code = status_code
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("no JSON body")
        return self._payload


def _worker_item(name: str = "mock-1") -> dict:
    """A minimal, valid ``WorkerConfigItem`` payload."""
    return {
        "name": name,
        "type": "mock",
        "enabled": True,
        "task_types": ["bootstrap"],
        "max_running": 1,
        "priority": 0,
        "env": {},
        "secret_env_keys": [],
    }


def _test_result_body(*, ok: bool = True) -> dict:
    """A ``WorkerConnectionTestResult`` body (ok true/false)."""
    return {
        "worker_name": "mock-1",
        "ok": ok,
        "returncode": 0 if ok else 1,
        "duration_ms": 12,
        "http_status": None,
        "response_preview": "pong",
        "stderr_preview": "",
        "preview": "pong",
        "command": "python3 -c ...",
    }


def _config_body() -> dict:
    """A (masked) ``WorkerConfigResponse`` body."""
    return {"workers": [_worker_item("mock-1")]}


def _snapshot() -> dict:
    """A representative dispatcher status snapshot covering the mapping paths."""
    return {
        "workers": [
            {"name": "alpha", "type": "claude", "status": "busy", "running": 1, "unhealthy": False},
            {"name": "beta", "type": "gpt", "status": "idle", "running": 0, "unhealthy": False},
            {"name": "gamma", "type": "mock", "status": "idle", "running": 0, "unhealthy": True},
            {"name": "delta", "type": "pi", "enabled": False, "status": "disabled", "running": 0, "unhealthy": False},
        ],
        "running_tasks": [{"worker_name": "alpha", "current_task": LONG_TASK}],
        "task_history": [
            {"worker_name": "alpha", "duration_seconds": 10.0},
            {"worker_name": "alpha", "duration_seconds": 25.0},
            {"worker_name": "beta", "duration_seconds": None},
        ],
        "heartbeats": {"alpha": {"last_heartbeat_seconds_ago": 3.5}},
    }


def _expected_status_cards() -> list[dict]:
    """The reshaped ``WorkerStatus`` cards observed for ``_snapshot()`` on UNFIXED code."""
    return [
        {
            "name": "alpha",
            "type": "claude",
            "enabled": True,
            "status": "busy",
            "current_task": LONG_TASK[:120],
            "tasks_completed": 2,
            "avg_duration_seconds": 17.5,
            "last_heartbeat_seconds_ago": 3.5,
        },
        {
            "name": "beta",
            "type": "gpt",
            "enabled": True,
            "status": "idle",
            "current_task": None,
            "tasks_completed": 1,
            "avg_duration_seconds": None,
            "last_heartbeat_seconds_ago": None,
        },
        {
            "name": "gamma",
            "type": "mock",
            "enabled": True,
            "status": "offline",
            "current_task": None,
            "tasks_completed": 0,
            "avg_duration_seconds": None,
            "last_heartbeat_seconds_ago": None,
        },
        {
            "name": "delta",
            "type": "pi",
            "enabled": False,
            "status": "disabled",
            "current_task": None,
            "tasks_completed": 0,
            "avg_duration_seconds": None,
            "last_heartbeat_seconds_ago": None,
        },
    ]


def _resolve_status_timeout(env_value) -> float:
    """Mirror ``workers._status_timeout()`` parse/fallback semantics.

    Used as the oracle for which timeout the proxy resolves from
    ``CAIRN_DISPATCHER_INTERNAL_TIMEOUT`` (req 3.4): unset/blank/non-numeric/
    non-positive → 2.0; a valid positive value is honored.
    """
    if env_value is None:
        return DEFAULT_STATUS_TIMEOUT
    raw = env_value.strip()
    if not raw:
        return DEFAULT_STATUS_TIMEOUT
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_STATUS_TIMEOUT
    return timeout if timeout > 0 else DEFAULT_STATUS_TIMEOUT


def _success_response(url: str) -> _FakeResponse:
    """The dispatcher's success body keyed by endpoint."""
    if url.endswith(workers.STATUS_PATH):
        return _FakeResponse(_snapshot())
    if url.endswith(workers.TEST_PATH):
        return _FakeResponse(_test_result_body(ok=True))
    return _FakeResponse(_config_body())


def _install_fakes(monkeypatch, *, scenario: str, latency: float, captured: list) -> None:
    """Patch both ``requests.get`` and ``requests.request`` with a shared, latency-aware fake.

    ``scenario`` controls the simulated dispatcher behavior:

    * ``"success"``  — return the endpoint's success body, unless ``latency`` exceeds
      the applied ``timeout`` (then raise ``requests.Timeout``).
    * ``"unreachable"`` — always raise ``requests.ConnectionError``.
    * ``"error"`` — return a non-2xx (500) ``{"detail": "boom"}`` body.
    * ``"ok_false"`` — return a within-timeout 200 ``{"ok": false}`` body (TEST only).
    """

    def behave(url, timeout):
        captured.append(timeout)
        if scenario == "unreachable":
            raise requests.ConnectionError("connection refused")
        if scenario == "error":
            return _FakeResponse({"detail": "boom"}, status_code=500)
        if scenario == "ok_false":
            return _FakeResponse(_test_result_body(ok=False), status_code=200)
        # success
        if timeout is None or latency > timeout:
            raise requests.Timeout(f"simulated latency {latency}s > timeout {timeout}s")
        return _success_response(url)

    def fake_get(url, timeout=None):  # noqa: ARG001
        return behave(url, timeout)

    def fake_request(method, url, json=None, timeout=None):  # noqa: ARG001
        return behave(url, timeout)

    monkeypatch.setattr(workers.requests, "get", fake_get)
    monkeypatch.setattr(workers.requests, "request", fake_request)


def _do_op(client, kind: str):
    if kind == "STATUS":
        return client.get("/api/workers")
    if kind == "TEST":
        return client.post("/api/workers/config/test", json={"worker": _worker_item()})
    if kind == "CONFIG_GET":
        return client.get("/api/workers/config")
    if kind == "CONFIG_PUT":
        return client.put("/api/workers/config", json={"workers": [_worker_item()]})
    raise AssertionError(f"unknown kind {kind!r}")


# ===========================================================================
# Example-based preservation tests (the key scenarios from the design)
# ===========================================================================

# ---------------------------------------------------------------------------
# 1. Status polling uses the short timeout and reshapes the snapshot (3.1, 3.5)
# ---------------------------------------------------------------------------


def test_status_polling_uses_short_status_timeout(client, monkeypatch):
    """GET /api/workers proxies with the short status timeout (default 2.0s).

    **Validates: Requirements 3.1**
    """
    captured: list = []
    _install_fakes(monkeypatch, scenario="success", latency=0.01, captured=captured)

    resp = client.get("/api/workers")

    assert resp.status_code == 200
    assert captured == [DEFAULT_STATUS_TIMEOUT]


def test_status_polling_honors_custom_status_timeout_env(client, monkeypatch):
    """A valid positive CAIRN_DISPATCHER_INTERNAL_TIMEOUT is honored for status polling.

    **Validates: Requirements 3.1, 3.4**
    """
    monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", "5.0")
    captured: list = []
    _install_fakes(monkeypatch, scenario="success", latency=0.01, captured=captured)

    resp = client.get("/api/workers")

    assert resp.status_code == 200
    assert captured == [5.0]


def test_status_polling_reshapes_snapshot_into_cards(client, monkeypatch):
    """GET /api/workers reshapes the snapshot into the same WorkerStatus cards.

    Covers status mapping (idle/busy/offline/disabled), current-task truncation
    to 120 chars, completed counts, average-duration rounding, and heartbeat age.

    **Validates: Requirements 3.5**
    """
    captured: list = []
    _install_fakes(monkeypatch, scenario="success", latency=0.01, captured=captured)

    resp = client.get("/api/workers")

    assert resp.status_code == 200
    assert resp.json() == _expected_status_cards()


def test_busy_worker_current_task_truncated_to_120_chars(client, monkeypatch):
    """Req 3.5: a busy worker's current task is truncated to 120 chars."""
    captured: list = []
    _install_fakes(monkeypatch, scenario="success", latency=0.01, captured=captured)

    cards = {c["name"]: c for c in client.get("/api/workers").json()}
    assert len(cards["alpha"]["current_task"]) == 120
    assert cards["alpha"]["current_task"] == LONG_TASK[:120]


# ---------------------------------------------------------------------------
# 2. Unreachable dispatcher still yields the per-endpoint 503 (3.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["STATUS", "TEST", "CONFIG_GET", "CONFIG_PUT"])
def test_unreachable_dispatcher_returns_503_per_endpoint(client, monkeypatch, kind):
    """A requests.ConnectionError on any endpoint yields the same 503 warning.

    **Validates: Requirements 3.2**
    """
    captured: list = []
    _install_fakes(monkeypatch, scenario="unreachable", latency=0.01, captured=captured)

    resp = _do_op(client, kind)

    assert resp.status_code == 503
    assert resp.json()["detail"] == {
        "message": _UNAVAILABLE_MESSAGE[kind],
        "last_updated": None,
    }


def test_status_non_200_returns_503(client, monkeypatch):
    """A non-200 from /internal/status degrades to a 503 connectivity warning.

    **Validates: Requirements 3.2**
    """
    captured: list = []
    _install_fakes(monkeypatch, scenario="error", latency=0.01, captured=captured)

    resp = client.get("/api/workers")

    assert resp.status_code == 503
    assert resp.json()["detail"]["message"] == "Worker status unavailable"


# ---------------------------------------------------------------------------
# 3. Genuine error / {"ok": false} propagated unchanged (3.3)
# ---------------------------------------------------------------------------


def test_test_endpoint_ok_false_propagated_unchanged(client, monkeypatch):
    """A within-timeout {"ok": false} from /internal/workers/test is propagated.

    **Validates: Requirements 3.3**
    """
    captured: list = []
    _install_fakes(monkeypatch, scenario="ok_false", latency=0.01, captured=captured)

    resp = client.post("/api/workers/config/test", json={"worker": _worker_item()})

    assert resp.status_code == 200
    assert resp.json() == _test_result_body(ok=False)


@pytest.mark.parametrize("kind", ["TEST", "CONFIG_GET", "CONFIG_PUT"])
def test_test_config_non_2xx_propagated_unchanged(client, monkeypatch, kind):
    """A non-2xx response from a test/config endpoint propagates status + body.

    **Validates: Requirements 3.3**
    """
    captured: list = []
    _install_fakes(monkeypatch, scenario="error", latency=0.01, captured=captured)

    resp = _do_op(client, kind)

    assert resp.status_code == 500
    assert resp.json()["detail"] == "boom"


# ---------------------------------------------------------------------------
# 4. Status-timeout parsing/fallback semantics unchanged (3.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_value,expected_timeout",
    [
        (None, 2.0),   # unset
        ("", 2.0),     # blank
        ("   ", 2.0),  # whitespace-only
        ("abc", 2.0),  # non-numeric
        ("-1", 2.0),   # negative
        ("0", 2.0),    # zero
        ("1.5", 1.5),  # valid positive
        ("5.0", 5.0),  # valid positive
        ("30", 30.0),  # valid positive
    ],
)
def test_status_timeout_fallback_semantics(client, monkeypatch, env_value, expected_timeout):
    """Unset/blank/invalid/non-positive → 2.0; valid positive honored.

    **Validates: Requirements 3.4**
    """
    if env_value is None:
        monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", env_value)

    captured: list = []
    _install_fakes(monkeypatch, scenario="success", latency=0.001, captured=captured)

    resp = client.get("/api/workers")

    assert resp.status_code == 200
    assert captured == [expected_timeout]


# ===========================================================================
# Property 2 (Preservation) — property-based test over the input domain
# ===========================================================================


@st.composite
def _proxied_operations(draw):
    """Generate a non-buggy ``ProxiedOperation``.

    Domain: ``kind ∈ {STATUS, TEST, CONFIG_GET, CONFIG_PUT}``, a scenario
    (success / unreachable / error / ok_false), a dispatcher latency, and a
    ``CAIRN_DISPATCHER_INTERNAL_TIMEOUT`` env string (valid/blank/invalid/
    non-positive). The composite never emits the bug condition (a healthy
    test/config op whose latency exceeds the resolved status timeout).
    """
    kind = draw(st.sampled_from(["STATUS", "TEST", "CONFIG_GET", "CONFIG_PUT"]))
    scenarios = ["success", "unreachable", "error"]
    if kind == "TEST":
        scenarios = scenarios + ["ok_false"]
    scenario = draw(st.sampled_from(scenarios))
    latency = draw(st.floats(min_value=0.001, max_value=60.0, allow_nan=False, allow_infinity=False))
    env_value = draw(
        st.sampled_from([None, "", "   ", "abc", "not-a-number", "-1", "-2.5", "0", "0.0", "1.5", "2.0", "5.0", "30"])
    )
    resolved_timeout = _resolve_status_timeout(env_value)

    if kind != "STATUS" and scenario == "success":
        # The bug condition is exactly a healthy test/config op whose latency
        # exceeds the (shared) status timeout. Exclude it from the preservation
        # domain by keeping the success latency within the resolved timeout.
        assume(latency <= resolved_timeout)

    return {
        "kind": kind,
        "scenario": scenario,
        "latency": latency,
        "env_value": env_value,
        "resolved_timeout": resolved_timeout,
    }


def _expected_outcome(op: dict):
    """Oracle: the baseline (status_code, body) for a non-buggy ProxiedOperation."""
    kind = op["kind"]
    scenario = op["scenario"]

    if kind == "STATUS":
        # Status success within the resolved timeout reshapes the snapshot;
        # everything else (unreachable, non-200, or a slow status poll that
        # times out — all non-buggy for STATUS) degrades to the 503 warning.
        if scenario == "success" and op["latency"] <= op["resolved_timeout"]:
            return 200, _expected_status_cards()
        return 503, {"message": "Worker status unavailable", "last_updated": None}

    # test/config endpoints
    if scenario == "unreachable":
        return 503, {"message": _UNAVAILABLE_MESSAGE[kind], "last_updated": None}
    if scenario == "error":
        return 500, "boom"
    if scenario == "ok_false":
        return 200, _test_result_body(ok=False)
    # success (latency guaranteed <= resolved_timeout by the generator)
    if kind == "TEST":
        return 200, _test_result_body(ok=True)
    return 200, _config_body()


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(op=_proxied_operations())
def test_preservation_non_buggy_inputs_match_baseline(client, monkeypatch, op):
    """For every non-buggy input, the router returns the observed baseline result.

    This is Property 2 (Preservation): for all ``X`` where ``isBugCondition(X)``
    is false, the proxy's status code and body match the recorded baseline. On
    UNFIXED code this PASSES (it captures today's behavior); re-run unchanged
    after the fix it must still PASS, proving no regression.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
    """
    if op["env_value"] is None:
        monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", op["env_value"])

    captured: list = []
    _install_fakes(
        monkeypatch, scenario=op["scenario"], latency=op["latency"], captured=captured
    )

    resp = _do_op(client, op["kind"])
    expected_status, expected_body = _expected_outcome(op)

    assert resp.status_code == expected_status, (
        f"{op} returned {resp.status_code} ({resp.json()!r}); expected {expected_status}"
    )

    if expected_status >= 400:
        # Error bodies are surfaced under FastAPI's "detail" envelope.
        detail = resp.json().get("detail")
        assert detail == expected_body, f"{op}: detail {detail!r} != {expected_body!r}"
    else:
        assert resp.json() == expected_body, f"{op}: body != baseline"

    # Status polling must always resolve the short status timeout (never a
    # longer one) — the longer test timeout is never applied to status polling.
    if op["kind"] == "STATUS":
        assert captured and captured[0] == op["resolved_timeout"]
