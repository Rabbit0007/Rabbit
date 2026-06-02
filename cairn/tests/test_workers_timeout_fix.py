"""Fix-Checking tests for the worker connectivity test timeout bugfix.

Spec: ``.kiro/specs/worker-connectivity-test-timeout`` — Task 3.4
(**Property 1: Expected Behavior — Test/Config Operations Use the Dedicated
Longer Timeout**).

These tests extend the validation beyond the Task 1 bug-condition re-run
(``tests/test_workers_timeout_bugfix.py``). They encode the additional
**Fix-Checking** cases from the design's "Fix Checking" section, asserting the
*fixed* router applies the dedicated longer ``_test_timeout()``
(``CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT``, default 30.0s) to the test/config
operations, so a healthy-but-slow operation returns the dispatcher's real result
instead of a timeout-based 503.

Formal property (design Fix Checking pseudocode)::

    FOR ALL X WHERE isBugCondition(X) DO
      result := proxy_fixed(X)        # test/config ops use _test_timeout() (default 30s)
      ASSERT X.dispatcherLatencySeconds <= testTimeout
             IMPLIES result = dispatcherResult(X)   # no timeout-based 503
    END FOR

Cases covered (design "Fix Checking" / tasks.md 3.4):

1. Connectivity test under the longer timeout: latency 2.05s, default 30s →
   HTTP 200 with the real ``WorkerConnectionTestResult`` (``ok: true``), and
   ``requests.request`` invoked with ``timeout ≈ 30.0``.
2. Config read/write under the longer timeout: latency 2.5s → 200 with the
   masked ``WorkerConfigResponse`` (GET) and the applied config (PUT).
3. Custom env override honored: ``CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT=5`` →
   latency 4s → 200; latency 6s → 503 (genuine timeout beyond the bound).
4. Invalid/unset env falls back to 30s: var unset / ``"abc"`` / ``"-1"`` →
   ``_test_timeout()`` returns 30.0 and a 2.05s test succeeds (200).
5. Property-based: healthy test/config latencies in ``(status_timeout,
   test_timeout]`` always return the dispatcher's success result under the fix.

Conventions mirror ``tests/test_workers_timeout_bugfix.py``: mount the
``workers`` router on a minimal FastAPI app and monkeypatch ``workers.requests``
with the same latency-aware fake / ``_FakeResponse`` — no real network or
dispatcher is needed.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**
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
# Constants
# ---------------------------------------------------------------------------

# The dedicated longer test/config timeout introduced by the fix
# (workers.DEFAULT_INTERNAL_TEST_TIMEOUT). The short status-polling timeout
# (workers.DEFAULT_INTERNAL_TIMEOUT) is 2.0s.
DEFAULT_TEST_TIMEOUT = 30.0
SHORT_STATUS_TIMEOUT = 2.0

_UNAVAILABLE_MESSAGE = {
    "TEST": "Worker connectivity test failed",
    "CONFIG_GET": "Worker config unavailable",
    "CONFIG_PUT": "Worker config update failed",
}


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
    """Start each test with the timeout env vars unset so the defaults apply.

    Unset ``CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT`` pins ``_test_timeout()`` at
    the 30.0s default; unset ``CAIRN_DISPATCHER_INTERNAL_TIMEOUT`` keeps status
    polling at 2.0s (it is not exercised here, but the isolation matches the
    rest of the suite).
    """
    monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TIMEOUT", raising=False)
    monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", raising=False)


# ---------------------------------------------------------------------------
# Test doubles for the dispatcher proxy call (same convention as the task-1 file)
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
        "duration_ms": 2050,
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
    ``requests.Timeout`` when ``simulated_latency > timeout`` (modelling a
    healthy dispatcher whose work outlasts the applied proxy timeout). Otherwise
    it returns a ``_FakeResponse`` carrying the dispatcher's success body, keyed
    by endpoint (``{"ok": true}`` for the test endpoint, a masked config body for
    config GET/PUT).
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


# ===========================================================================
# Case 1 — Connectivity test under the longer timeout (latency 2.05s, default 30s)
# ===========================================================================


def test_connectivity_test_under_longer_timeout_returns_ok(client, monkeypatch):
    """A 2.05s connectivity test succeeds under the default 30s test timeout.

    The dispatcher-side test outlasts the short 2.0s status timeout but is well
    within the dedicated 30s ``_test_timeout()``. The fixed proxy must return
    HTTP 200 with the real ``WorkerConnectionTestResult`` (``ok: true``) and have
    invoked ``requests.request`` with ``timeout ≈ 30.0``.

    **Validates: Requirements 2.1, 2.2, 2.4**
    """
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=2.05, captured_timeouts=captured)

    resp = _do_test_op(client)

    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["ok"] is True
    assert body["worker_name"] == "mock-1"
    # Root cause is fixed: the test op is proxied with the longer timeout.
    assert captured, "requests.request was never invoked"
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)


# ===========================================================================
# Case 2 — Config read/write under the longer timeout (latency 2.5s)
# ===========================================================================


def test_config_get_under_longer_timeout_returns_masked_config(client, monkeypatch):
    """GET /api/workers/config at 2.5s succeeds with the masked config under 30s.

    **Validates: Requirements 2.3, 2.4**
    """
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=2.5, captured_timeouts=captured)

    resp = _do_config_get(client)

    assert resp.status_code == 200, resp.json()
    assert resp.json() == _config_body()
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)


def test_config_put_under_longer_timeout_returns_applied_config(client, monkeypatch):
    """PUT /api/workers/config at 2.5s succeeds with the applied config under 30s.

    **Validates: Requirements 2.3, 2.4**
    """
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=2.5, captured_timeouts=captured)

    resp = _do_config_put(client)

    assert resp.status_code == 200, resp.json()
    assert resp.json() == _config_body()
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)


@pytest.mark.parametrize("kind", ["TEST", "CONFIG_GET", "CONFIG_PUT"])
@pytest.mark.parametrize("latency", [2.05, 2.5])
def test_all_test_config_ops_succeed_under_longer_timeout(client, monkeypatch, kind, latency):
    """Every test/config op whose latency exceeds 2.0s but is within 30s returns 200.

    Parameterized sweep over the three buggy kinds and the two representative
    latencies from the design — the direct positive counterpart to the task-1
    bug-condition cases.

    **Validates: Requirements 2.1, 2.2, 2.3, 2.4**
    """
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=latency, captured_timeouts=captured)
    do_op, body_ok = _OPERATIONS[kind]

    resp = do_op(client)

    assert resp.status_code == 200, (
        f"{kind} at latency {latency}s returned {resp.status_code} ({resp.json()!r}); "
        f"expected 200 under the {DEFAULT_TEST_TIMEOUT}s test timeout. "
        f"captured timeouts={captured}"
    )
    assert body_ok(resp.json())
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)


# ===========================================================================
# Case 3 — Custom env override honored (CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT=5)
# ===========================================================================


def test_custom_env_override_allows_op_within_bound(client, monkeypatch):
    """With the test timeout set to 5s, a 4s op succeeds (within the configured bound).

    **Validates: Requirements 2.4**
    """
    monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", "5")
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=4.0, captured_timeouts=captured)

    resp = _do_test_op(client)

    assert resp.status_code == 200, resp.json()
    assert resp.json()["ok"] is True
    assert captured[-1] == pytest.approx(5.0)


def test_custom_env_override_times_out_beyond_bound(client, monkeypatch):
    """With the test timeout set to 5s, a 6s op genuinely times out → 503.

    This demonstrates the dedicated env var actually controls the test/config
    timeout: a latency beyond the configured bound still maps to the per-endpoint
    503 connectivity warning (genuine timeout, not a spurious one).

    **Validates: Requirements 2.4**
    """
    monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", "5")
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=6.0, captured_timeouts=captured)

    resp = _do_test_op(client)

    assert resp.status_code == 503
    assert resp.json()["detail"] == {
        "message": _UNAVAILABLE_MESSAGE["TEST"],
        "last_updated": None,
    }
    assert captured[-1] == pytest.approx(5.0)


# ===========================================================================
# Case 4 — Invalid/unset env falls back to the 30s default
# ===========================================================================


@pytest.mark.parametrize("env_value", [None, "", "   ", "abc", "-1", "0"])
def test_invalid_or_unset_env_falls_back_to_30s_default(client, monkeypatch, env_value):
    """Unset/blank/non-numeric/non-positive test-timeout env → 30.0s default.

    ``_test_timeout()`` falls back to 30.0, so a 2.05s op (which exceeds the 2.0s
    status timeout) still succeeds with the real result, and ``requests.request``
    is invoked with ``timeout ≈ 30.0``.

    **Validates: Requirements 2.4**
    """
    if env_value is None:
        monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", env_value)

    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=2.05, captured_timeouts=captured)

    resp = _do_test_op(client)

    assert resp.status_code == 200, resp.json()
    assert resp.json()["ok"] is True
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)


def test_test_timeout_resolver_falls_back_on_invalid(monkeypatch):
    """Unit check: ``_test_timeout()`` returns 30.0 for invalid env and is honored when valid.

    **Validates: Requirements 2.4**
    """
    monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", raising=False)
    assert workers._test_timeout() == pytest.approx(DEFAULT_TEST_TIMEOUT)

    for bad in ["", "   ", "abc", "-1", "0"]:
        monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", bad)
        assert workers._test_timeout() == pytest.approx(DEFAULT_TEST_TIMEOUT), bad

    monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", "5")
    assert workers._test_timeout() == pytest.approx(5.0)


# ===========================================================================
# Case 5 — Property-based Fix Checking
# ===========================================================================


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    kind=st.sampled_from(["TEST", "CONFIG_GET", "CONFIG_PUT"]),
    # Healthy latencies strictly above the short status timeout (2.0s) and within
    # the dedicated test timeout (30.0s): the exact bug-condition input domain.
    latency=st.floats(
        min_value=2.0001, max_value=DEFAULT_TEST_TIMEOUT, allow_nan=False, allow_infinity=False
    ),
)
def test_property_healthy_slow_op_returns_dispatcher_result(client, monkeypatch, kind, latency):
    """Property 1 (Fix Checking): every healthy test/config op with latency in
    ``(status_timeout, test_timeout]`` returns the dispatcher's success result.

    Under the fix the proxy applies the 30s ``_test_timeout()``, so the op never
    hits a spurious timeout: HTTP 200 with the real body, and the captured
    ``timeout`` is the dedicated longer value.

    **Validates: Requirements 2.1, 2.2, 2.3, 2.4**
    """
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=latency, captured_timeouts=captured)
    do_op, body_ok = _OPERATIONS[kind]

    resp = do_op(client)

    assert resp.status_code == 200, (
        f"{kind} at latency {latency}s returned {resp.status_code} ({resp.json()!r}); "
        f"expected 200 under the {DEFAULT_TEST_TIMEOUT}s test timeout. captured={captured}"
    )
    assert body_ok(resp.json())
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)
