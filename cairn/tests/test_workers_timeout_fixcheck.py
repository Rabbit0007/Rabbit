"""Fix-Checking tests for the worker connectivity test timeout bugfix.

Spec: ``.kiro/specs/worker-connectivity-test-timeout`` — Task 3.4
(Property 1: Expected Behavior — Test/Config Operations Use the Dedicated Longer
Timeout).

These tests extend the validation beyond the Task 1 re-run. They encode the
**Fix Checking** cases from the design's Testing Strategy: for a healthy test or
config operation whose dispatcher-side latency exceeds the short status-polling
timeout but is within the dedicated longer test timeout, the fixed proxy applies
``_test_timeout()`` (``CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT``, default 30.0s)
and returns the dispatcher's real result instead of a timeout-based 503.

The fix is already implemented in ``cairn.server.routers.workers``:

* ``TEST_TIMEOUT_ENV`` / ``DEFAULT_INTERNAL_TEST_TIMEOUT = 30.0`` and the shared
  ``_resolve_timeout`` resolver backing both ``_status_timeout()`` and
  ``_test_timeout()``.
* ``_request_internal_json`` takes a keyword-only ``timeout`` parameter passed
  through to ``requests.request``.
* ``get_worker_config`` / ``update_worker_config`` / ``test_worker_config`` pass
  ``timeout=_test_timeout()``; status polling stays on ``_status_timeout()``.

**On the fixed code these tests MUST PASS.**

Conventions mirror ``tests/test_workers_timeout_bugfix.py`` /
``tests/test_workers_router.py``: mount the ``workers`` router on a minimal
FastAPI app and monkeypatch ``workers.requests`` with the same latency-aware fake
— it records the ``timeout`` it was called with and raises ``requests.Timeout``
when the simulated dispatcher latency exceeds that ``timeout``, otherwise returns
a ``_FakeResponse`` carrying the dispatcher's success body. No real network or
dispatcher is needed.
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

# The dedicated longer timeout the fix uses for test/config operations.
DEFAULT_TEST_TIMEOUT = 30.0
# The short status-polling timeout (default), which test/config ops must NOT use.
SHORT_STATUS_TIMEOUT = 2.0


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

    With ``CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT`` unset, ``_test_timeout()``
    resolves the 30.0s default; individual tests set it explicitly where the
    custom-override behavior is under test.
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
    returns a ``_FakeResponse`` carrying the dispatcher's success body keyed by
    endpoint (``{"ok": true, ...}`` for the test endpoint; the masked config body
    for config GET/PUT).
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


def _do_test_op(client):
    return client.post("/api/workers/config/test", json={"worker": _worker_item()})


def _do_config_get(client):
    return client.get("/api/workers/config")


def _do_config_put(client):
    return client.put("/api/workers/config", json={"workers": [_worker_item()]})


_OPERATIONS = {
    "TEST": (_do_test_op, lambda body: body.get("ok") is True),
    "CONFIG_GET": (_do_config_get, lambda body: body.get("workers", [{}])[0].get("name") == "mock-1"),
    "CONFIG_PUT": (_do_config_put, lambda body: body.get("workers", [{}])[0].get("name") == "mock-1"),
}


# ===========================================================================
# Fix Checking case 1 — connectivity test under the longer timeout
# ===========================================================================


def test_connectivity_test_succeeds_under_longer_timeout(client, monkeypatch):
    """POST /api/workers/config/test with a 2.05s latency returns 200 ``{"ok": true}``.

    The dispatcher-side test takes 2.05s — above the short 2.0s status timeout but
    well within the dedicated 30.0s test timeout — so the fixed proxy returns the
    real ``WorkerConnectionTestResult`` and invokes ``requests.request`` with
    ``timeout ≈ 30.0``.

    **Validates: Requirements 2.1, 2.2**
    """
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=2.05, captured_timeouts=captured)

    resp = _do_test_op(client)

    assert resp.status_code == 200, resp.json()
    assert resp.json()["ok"] is True
    assert captured, "requests.request was never invoked"
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)


# ===========================================================================
# Fix Checking case 2 — config read/write under the longer timeout
# ===========================================================================


def test_config_read_succeeds_under_longer_timeout(client, monkeypatch):
    """GET /api/workers/config with a 2.5s latency returns 200 with the masked config.

    **Validates: Requirements 2.3**
    """
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=2.5, captured_timeouts=captured)

    resp = _do_config_get(client)

    assert resp.status_code == 200, resp.json()
    assert resp.json() == _config_body()
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)


def test_config_write_succeeds_under_longer_timeout(client, monkeypatch):
    """PUT /api/workers/config with a 2.5s latency returns 200 with the applied config.

    **Validates: Requirements 2.3**
    """
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=2.5, captured_timeouts=captured)

    resp = _do_config_put(client)

    assert resp.status_code == 200, resp.json()
    assert resp.json() == _config_body()
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)


# ===========================================================================
# Fix Checking case 3 — custom env override is honored
# ===========================================================================


def test_custom_env_override_succeeds_within_configured_bound(client, monkeypatch):
    """With CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT=5, a 4s latency returns 200.

    The operator-tuned dedicated timeout (5s) governs test/config operations, so a
    4s op completes within bound and ``requests.request`` is called with ``timeout
    ≈ 5.0``.

    **Validates: Requirements 2.4**
    """
    monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", "5")
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=4.0, captured_timeouts=captured)

    resp = _do_test_op(client)

    assert resp.status_code == 200, resp.json()
    assert resp.json()["ok"] is True
    assert captured[-1] == pytest.approx(5.0)


def test_custom_env_override_times_out_beyond_configured_bound(client, monkeypatch):
    """With CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT=5, a 6s latency yields a genuine 503.

    A latency beyond the configured 5s bound is a real timeout, so the proxy maps
    it to the per-endpoint 503 connectivity warning — demonstrating the dedicated
    var actually controls the test/config timeout.

    **Validates: Requirements 2.4**
    """
    monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", "5")
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=6.0, captured_timeouts=captured)

    resp = _do_test_op(client)

    assert resp.status_code == 503
    assert resp.json()["detail"] == {
        "message": "Worker connectivity test failed",
        "last_updated": None,
    }
    assert captured[-1] == pytest.approx(5.0)


# ===========================================================================
# Fix Checking case 4 — invalid/unset env falls back to the 30s default
# ===========================================================================


@pytest.mark.parametrize(
    "env_value",
    [
        None,    # unset
        "abc",   # non-numeric
        "-1",    # negative
        "0",     # zero
        "",      # blank
        "   ",   # whitespace-only
    ],
)
def test_invalid_or_unset_env_falls_back_to_30s_default(client, monkeypatch, env_value):
    """An unset/invalid CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT falls back to 30.0s.

    ``_test_timeout()`` returns 30.0 and a 2.05s connectivity test succeeds (200),
    with ``requests.request`` invoked with ``timeout ≈ 30.0``.

    **Validates: Requirements 2.4**
    """
    if env_value is None:
        monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", env_value)

    # _test_timeout() itself resolves the default.
    assert workers._test_timeout() == pytest.approx(DEFAULT_TEST_TIMEOUT)

    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=2.05, captured_timeouts=captured)

    resp = _do_test_op(client)

    assert resp.status_code == 200, resp.json()
    assert resp.json()["ok"] is True
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)


# ===========================================================================
# Fix Checking case 5 — property-based: healthy latencies in (status, test]
# ===========================================================================


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    kind=st.sampled_from(["TEST", "CONFIG_GET", "CONFIG_PUT"]),
    # Healthy dispatcher latencies strictly above the short status timeout and
    # within the dedicated longer test timeout: (2.0s, 30.0s].
    latency=st.floats(
        min_value=2.05,
        max_value=DEFAULT_TEST_TIMEOUT,
        allow_nan=False,
        allow_infinity=False,
        exclude_min=False,
    ),
)
def test_property_healthy_test_config_ops_return_success_under_longer_timeout(
    client, monkeypatch, kind, latency
):
    """Property 1 (Fix Checking): every healthy test/config op whose latency is in
    ``(status_timeout, test_timeout]`` returns the dispatcher's success result.

    The fixed proxy applies ``_test_timeout()`` (30.0s default), so for all such
    latencies the op returns HTTP 200 with the real dispatcher body and never a
    timeout-based 503. ``requests.request`` is always invoked with ``timeout ≈
    30.0``.

    **Validates: Requirements 2.1, 2.2, 2.3, 2.4**
    """
    captured: list = []
    _install_latency_aware_fake(monkeypatch, simulated_latency=latency, captured_timeouts=captured)
    do_op, body_ok = _OPERATIONS[kind]

    resp = do_op(client)

    assert resp.status_code == 200, (
        f"{kind} with healthy latency {latency}s returned {resp.status_code} "
        f"({resp.json()!r}); expected 200. timeout used={captured}"
    )
    assert body_ok(resp.json())
    assert captured[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)
