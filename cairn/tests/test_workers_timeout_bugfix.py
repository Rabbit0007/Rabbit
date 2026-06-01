"""Bug-condition exploration tests for the worker connectivity test timeout.

Spec: ``.kiro/specs/worker-connectivity-test-timeout`` — Task 1 (bug-first methodology).

Bug summary: every dispatcher proxy call in ``cairn.server.routers.workers``
(``_request_internal_json`` / ``_fetch_status_snapshot``) shares the short
status-polling timeout (``_status_timeout()``, default ``DEFAULT_INTERNAL_TIMEOUT``
= 2.0s). The connectivity test (``POST /api/workers/config/test``) and config
read/write operations (``GET``/``PUT /api/workers/config``) are genuinely slow
(≈2.05s+), so they exceed the 2.0s bound, ``requests`` raises a
``RequestException``, and the handler returns a spurious HTTP 503 even though the
worker is healthy.

These tests encode **Property 1 (Bug Condition / Expected Behavior)** from the
design: for a healthy test/config operation whose dispatcher-side latency exceeds
the short status timeout, the proxy SHALL return the dispatcher's real result
(HTTP 200), not a timeout-based 503.

**On the current (unfixed) code these tests MUST FAIL** — the slow-but-healthy op
returns 503 instead of 200, and ``requests.request`` is invoked with the shared
~2.0s status timeout. That failure confirms the bug exists and pins the root
cause. The same tests are re-run in Task 3.5 and must PASS once the dedicated
longer ``_test_timeout()`` is introduced.

Conventions mirror ``tests/test_workers_router.py`` / ``tests/test_worker_config_api.py``:
mount the ``workers`` router on a minimal FastAPI app and monkeypatch
``workers.requests`` with a latency-aware fake — no real network or dispatcher is
needed.
"""

from __future__ import annotations

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cairn.server.routers import workers

from .conftest import BASE_URL

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workers_app(temp_db) -> FastAPI:
    """A minimal FastAPI app mounting only the workers router.

    ``temp_db`` (from conftest) configures an isolated DB; the test/config proxy
    endpoints exercised here do not touch it, but the fixture keeps the router's
    DB-backed dependencies importable and mirrors the existing test setup.
    """
    app = FastAPI()
    app.include_router(workers.router)
    return app


@pytest.fixture
def client(workers_app) -> TestClient:
    return TestClient(workers_app, base_url=BASE_URL)


@pytest.fixture(autouse=True)
def _clear_timeout_env(monkeypatch):
    """Ensure the timeout env vars are unset so the defaults apply.

    Unset ``CAIRN_DISPATCHER_INTERNAL_TIMEOUT`` pins ``_status_timeout()`` at the
    2.0s default that the bug shares with the test/config operations.
    """
    monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", raising=False)
    monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", raising=False)


# ---------------------------------------------------------------------------
# Test doubles for the dispatcher proxy call
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Stand-in for a ``requests.Response`` returned by ``requests.request``."""

    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
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


def _test_result_body(name: str = "mock-1") -> dict:
    """A successful ``WorkerConnectionTestResult`` body ({"ok": true})."""
    return {
        "worker_name": name,
        "ok": True,
        "returncode": 0,
        "duration_ms": 12,
        "http_status": None,
        "response_preview": "pong",
        "stderr_preview": "",
        "preview": "pong",
        "command": "python3 -c ...",
    }


def _config_body() -> dict:
    """A successful (masked) ``WorkerConfigResponse`` body."""
    return {"workers": [_worker_item("mock-1")]}


def _install_latency_aware_fake(monkeypatch, *, simulated_latency: float, captured_timeouts: list):
    """Patch ``workers.requests.request`` with a latency-aware fake.

    The fake records the ``timeout`` it was called with and raises
    ``requests.Timeout`` when ``simulated_latency > timeout`` (modelling a healthy
    dispatcher whose work outlasts the applied proxy timeout). Otherwise it
    returns a ``_FakeResponse`` carrying the dispatcher's success body.
    """

    def fake_request(method, url, json=None, timeout=None):  # noqa: ARG001
        captured_timeouts.append(timeout)
        if timeout is None or simulated_latency > timeout:
            raise requests.Timeout(
                f"simulated dispatcher latency {simulated_latency}s exceeded timeout {timeout}s"
            )
        if url.endswith(workers.TEST_PATH):
            body = _test_result_body()
        else:
            body = _config_body()
        return _FakeResponse(body, 200)

    monkeypatch.setattr(workers.requests, "request", fake_request)


# Scoped input domain: the three test/config kinds with the request/assert shapes.
# Each entry: (kind, method, path, json_body_or_None, response_assertion).
def _do_test_op(client) -> "requests.Response":
    return client.post("/api/workers/config/test", json={"worker": _worker_item()})


def _do_config_get(client) -> "requests.Response":
    return client.get("/api/workers/config")


def _do_config_put(client) -> "requests.Response":
    return client.put("/api/workers/config", json={"workers": [_worker_item()]})


_OPERATIONS = {
    "TEST": (_do_test_op, lambda body: body.get("ok") is True),
    "CONFIG_GET": (_do_config_get, lambda body: body.get("workers", [{}])[0].get("name") == "mock-1"),
    "CONFIG_PUT": (_do_config_put, lambda body: body.get("workers", [{}])[0].get("name") == "mock-1"),
}


# ---------------------------------------------------------------------------
# Property 1 (Bug Condition) — example-based, scoped to the concrete failing cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["TEST", "CONFIG_GET", "CONFIG_PUT"])
@pytest.mark.parametrize("latency", [2.05, 2.5])
def test_bug_condition_slow_test_config_op_returns_real_result(client, monkeypatch, kind, latency):
    """A healthy-but-slow test/config op must return the dispatcher's real result.

    Bug Condition (design ``isBugCondition``): ``kind ∈ {TEST, CONFIG_GET,
    CONFIG_PUT}`` AND ``dispatcherWouldSucceed`` AND ``dispatcherLatencySeconds >
    statusTimeout``. Expected behavior (design Property 1): HTTP 200 with the
    dispatcher's real result.

    On UNFIXED code this FAILS: the op is proxied with the shared ~2.0s status
    timeout, so the 2.05s/2.5s dispatcher work times out and the handler returns
    503. The failure message surfaces the counterexample (503 body + the captured
    ~2.0s timeout) that pins the shared-timeout root cause.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4**
    """
    captured_timeouts: list = []
    _install_latency_aware_fake(
        monkeypatch, simulated_latency=latency, captured_timeouts=captured_timeouts
    )
    do_op, body_ok = _OPERATIONS[kind]

    resp = do_op(client)

    assert resp.status_code == 200, (
        f"BUG: {kind} with a healthy dispatcher latency of {latency}s returned "
        f"HTTP {resp.status_code} ({resp.json()!r}) instead of 200. "
        f"requests.request was invoked with timeout={captured_timeouts} "
        f"(the shared ~2.0s status timeout is the root cause)."
    )
    body = resp.json()
    assert body_ok(body), f"unexpected success body for {kind}: {body!r}"


# ---------------------------------------------------------------------------
# Property 1 (Bug Condition) — scoped property-based test
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    kind=st.sampled_from(["TEST", "CONFIG_GET", "CONFIG_PUT"]),
    # Dispatcher latencies just above the short status timeout, within the
    # dedicated longer test timeout: (2.0s, 30.0s].
    latency=st.floats(min_value=2.05, max_value=29.0, allow_nan=False, allow_infinity=False),
)
def test_bug_condition_property_slow_test_config_op_returns_real_result(
    client, monkeypatch, kind, latency
):
    """Property 1 (scoped): for all healthy test/config ops whose latency exceeds
    the short status timeout but is within the longer test timeout, the proxy
    SHALL return the dispatcher's real result (HTTP 200), not a timeout 503.

    On UNFIXED code Hypothesis finds a counterexample for every input (the shared
    2.0s timeout always trips), shrinking to the minimal latency (~2.05s).

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4**
    """
    captured_timeouts: list = []
    _install_latency_aware_fake(
        monkeypatch, simulated_latency=latency, captured_timeouts=captured_timeouts
    )
    do_op, body_ok = _OPERATIONS[kind]

    resp = do_op(client)

    assert resp.status_code == 200, (
        f"BUG: {kind} with a healthy dispatcher latency of {latency}s returned "
        f"HTTP {resp.status_code} ({resp.json()!r}) instead of 200. "
        f"requests.request was invoked with timeout={captured_timeouts} "
        f"(the shared ~2.0s status timeout is the root cause)."
    )
    assert body_ok(resp.json())


# ---------------------------------------------------------------------------
# Root-cause counterexample — the timeout argument passed to requests.request
# ---------------------------------------------------------------------------

# The dedicated longer test/config timeout the fix introduces (design:
# DEFAULT_INTERNAL_TEST_TIMEOUT = 30.0s). Referenced as a literal here so this
# test fails cleanly (assertion, not AttributeError) on the unfixed code where
# `_test_timeout` does not yet exist.
EXPECTED_TEST_TIMEOUT = 30.0
SHORT_STATUS_TIMEOUT = 2.0


@pytest.mark.parametrize("kind", ["TEST", "CONFIG_GET", "CONFIG_PUT"])
def test_test_config_ops_use_dedicated_longer_timeout(client, monkeypatch, kind):
    """Pin the root cause: test/config ops must be proxied with the longer timeout.

    The fix routes test/config operations through a dedicated ``_test_timeout()``
    (default 30.0s), independent of the 2.0s status-polling timeout. This asserts
    the ``timeout`` argument handed to ``requests.request`` is the longer value.

    On UNFIXED code this FAILS, surfacing the counterexample: the captured timeout
    is the shared ~2.0s status timeout — the exact root cause of the spurious 503.
    A fast (within-timeout) latency is used so the request itself succeeds and the
    assertion isolates the timeout-selection defect.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4**
    """
    captured_timeouts: list = []
    # Latency well below any timeout so the request returns 200; we are only
    # inspecting which timeout the proxy selected for the call.
    _install_latency_aware_fake(
        monkeypatch, simulated_latency=0.01, captured_timeouts=captured_timeouts
    )
    do_op, _ = _OPERATIONS[kind]

    do_op(client)

    assert captured_timeouts, "requests.request was never invoked"
    used = captured_timeouts[-1]
    assert used == pytest.approx(EXPECTED_TEST_TIMEOUT), (
        f"BUG: {kind} was proxied with timeout={used}s (the shared status timeout, "
        f"~{SHORT_STATUS_TIMEOUT}s) instead of the dedicated longer test timeout "
        f"(~{EXPECTED_TEST_TIMEOUT}s). The shared short timeout is the root cause "
        f"of the spurious 503s for slow-but-healthy test/config operations."
    )
