"""Fix Checking tests for the worker connectivity test timeout.

Spec: ``.kiro/specs/worker-connectivity-test-timeout`` — Task 3.4 (Fix Checking).

These tests validate **Property 1 (Expected Behavior)** from the design: once the
dedicated longer ``_test_timeout()`` (``CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT``,
default ``DEFAULT_INTERNAL_TEST_TIMEOUT`` = 30.0s) is wired into the test/config
proxy handlers, a healthy-but-slow test/config operation whose dispatcher-side
latency exceeds the short 2.0s status-polling timeout — but is within the longer
test timeout — returns the dispatcher's real result (HTTP 200) instead of a
timeout-based 503.

The task-1 bug-condition tests (``tests/test_workers_timeout_bugfix.py``) already
re-run as the primary Fix Checking gate (they flip from FAIL → PASS once the fix
lands). This module adds the **additional** Fix-Checking cases called out in the
design's "Fix Checking" section that the task-1 test does not cover:

* the dedicated env var **override** is honored (a low bound makes a slow op time
  out again, proving the new var — not the status var — controls the timeout);
* **invalid/unset** env values fall back to the 30.0s default; and
* a **property-based** sweep over healthy latencies in ``(status_timeout,
  test_timeout]`` always yields the dispatcher's success result.

The first two listed cases (connectivity test + config read/write under the
longer timeout) are included here too so this Fix Checking module is
self-contained and distinct, per Task 3.4.

Conventions mirror ``tests/test_workers_timeout_bugfix.py`` and
``tests/test_workers_router.py``: mount the ``workers`` router on a minimal
FastAPI app and monkeypatch ``workers.requests`` with the same latency-aware
fake / ``_FakeResponse`` — no real network or dispatcher is needed.
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
    """Unset both timeout env vars so the defaults apply unless a test sets them.

    Clearing ``CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT`` pins ``_test_timeout()``
    at its 30.0s default; clearing ``CAIRN_DISPATCHER_INTERNAL_TIMEOUT`` keeps
    the status timeout at 2.0s so the (status_timeout, test_timeout] window the
    fix targets is well defined.
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

# The dedicated longer test/config timeout default introduced by the fix
# (design: DEFAULT_INTERNAL_TEST_TIMEOUT = 30.0s).
DEFAULT_TEST_TIMEOUT = 30.0
SHORT_STATUS_TIMEOUT = 2.0


# ---------------------------------------------------------------------------
# Fix Checking case 1 — connectivity test under the longer timeout
# ---------------------------------------------------------------------------


def test_connectivity_test_succeeds_under_longer_timeout(client, monkeypatch):
    """A 2.05s connectivity test succeeds under the default 30s test timeout.

    Design Fix Checking case 1: ``POST /api/workers/config/test`` with simulated
    latency 2.05s and the default ``_test_timeout()`` (30s) → HTTP 200 with the
    real ``WorkerConnectionTestResult`` (``ok: true``), and ``requests.request``
    invoked with ``timeout ≈ 30.0``.

    **Validates: Requirements 2.1, 2.2**
    """
    captured_timeouts: list = []
    _install_latency_aware_fake(
        monkeypatch, simulated_latency=2.05, captured_timeouts=captured_timeouts
    )

    resp = _do_test_op(client)

    assert resp.status_code == 200, (
        f"a healthy 2.05s connectivity test returned HTTP {resp.status_code} "
        f"({resp.json()!r}); requests.request used timeout={captured_timeouts}"
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["worker_name"] == "mock-1"
    assert captured_timeouts and captured_timeouts[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT), (
        f"connectivity test must use the dedicated longer timeout (~{DEFAULT_TEST_TIMEOUT}s); "
        f"got timeout={captured_timeouts}"
    )


# ---------------------------------------------------------------------------
# Fix Checking case 2 — config read/write under the longer timeout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["CONFIG_GET", "CONFIG_PUT"])
def test_config_read_write_succeeds_under_longer_timeout(client, monkeypatch, kind):
    """Slow (2.5s) config read/write succeeds under the default 30s test timeout.

    Design Fix Checking case 2: ``GET``/``PUT /api/workers/config`` with simulated
    latency 2.5s → HTTP 200 with the masked ``WorkerConfigResponse`` (GET) / the
    applied config (PUT), and ``requests.request`` invoked with ``timeout ≈ 30.0``.

    **Validates: Requirements 2.3, 2.4**
    """
    captured_timeouts: list = []
    _install_latency_aware_fake(
        monkeypatch, simulated_latency=2.5, captured_timeouts=captured_timeouts
    )
    do_op, body_ok = _OPERATIONS[kind]

    resp = do_op(client)

    assert resp.status_code == 200, (
        f"a healthy 2.5s {kind} op returned HTTP {resp.status_code} "
        f"({resp.json()!r}); requests.request used timeout={captured_timeouts}"
    )
    body = resp.json()
    assert body_ok(body), f"unexpected config body for {kind}: {body!r}"
    assert body["workers"][0]["name"] == "mock-1"
    assert captured_timeouts and captured_timeouts[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT), (
        f"{kind} must use the dedicated longer timeout (~{DEFAULT_TEST_TIMEOUT}s); "
        f"got timeout={captured_timeouts}"
    )


# ---------------------------------------------------------------------------
# Fix Checking case 3 — custom env override honored
# ---------------------------------------------------------------------------


def test_custom_env_override_allows_op_within_bound(client, monkeypatch):
    """``CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT=5`` lets a 4s op succeed (200).

    Design Fix Checking case 3 (within bound): with the dedicated var set to 5s,
    a 4s test completes within the configured bound → HTTP 200, and the proxy is
    invoked with ``timeout ≈ 5.0`` (the override, not the 30s default).

    **Validates: Requirements 2.1, 2.4**
    """
    monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", "5")
    captured_timeouts: list = []
    _install_latency_aware_fake(
        monkeypatch, simulated_latency=4.0, captured_timeouts=captured_timeouts
    )

    resp = _do_test_op(client)

    assert resp.status_code == 200, (
        f"a 4s test under a 5s configured timeout returned HTTP {resp.status_code} "
        f"({resp.json()!r}); requests.request used timeout={captured_timeouts}"
    )
    assert resp.json()["ok"] is True
    assert captured_timeouts and captured_timeouts[-1] == pytest.approx(5.0), (
        f"the dedicated env override (5s) must control the test timeout; got {captured_timeouts}"
    )


def test_custom_env_override_times_out_beyond_bound(client, monkeypatch):
    """``CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT=5`` makes a 6s op time out (503).

    Design Fix Checking case 3 (beyond bound): with the dedicated var set to 5s,
    a 6s test genuinely exceeds the configured bound → HTTP 503 "Worker
    connectivity test failed". This proves the *dedicated* var (not the status
    var) controls the test/config timeout.

    **Validates: Requirement 2.4**
    """
    monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", "5")
    captured_timeouts: list = []
    _install_latency_aware_fake(
        monkeypatch, simulated_latency=6.0, captured_timeouts=captured_timeouts
    )

    resp = _do_test_op(client)

    assert resp.status_code == 503, (
        f"a 6s test under a 5s configured timeout should time out (503); got "
        f"HTTP {resp.status_code} ({resp.json()!r})"
    )
    assert resp.json()["detail"]["message"] == "Worker connectivity test failed"
    assert captured_timeouts and captured_timeouts[-1] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Fix Checking case 4 — invalid/unset env falls back to the 30s default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_value",
    [
        None,  # unset (delenv)
        "",  # blank
        "abc",  # non-numeric
        "-1",  # non-positive
        "0",  # non-positive
    ],
)
def test_invalid_or_unset_env_falls_back_to_default(client, monkeypatch, env_value):
    """Unset/invalid/non-positive env → ``_test_timeout()`` is 30.0 and a 2.05s test passes.

    Design Fix Checking case 4: with the var unset or set to ``""`` / ``"abc"`` /
    ``"-1"`` / ``"0"``, ``_test_timeout()`` returns the 30.0s default, so a 2.05s
    connectivity test succeeds (HTTP 200) and the proxy uses ``timeout ≈ 30.0``.

    **Validates: Requirement 2.4**
    """
    if env_value is None:
        monkeypatch.delenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT", env_value)

    # The resolver itself must report the default.
    assert workers._test_timeout() == pytest.approx(DEFAULT_TEST_TIMEOUT)

    captured_timeouts: list = []
    _install_latency_aware_fake(
        monkeypatch, simulated_latency=2.05, captured_timeouts=captured_timeouts
    )

    resp = _do_test_op(client)

    assert resp.status_code == 200, (
        f"with env={env_value!r} the test timeout should fall back to "
        f"{DEFAULT_TEST_TIMEOUT}s and a 2.05s test should pass; got HTTP "
        f"{resp.status_code} ({resp.json()!r})"
    )
    assert resp.json()["ok"] is True
    assert captured_timeouts and captured_timeouts[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)


# ---------------------------------------------------------------------------
# Fix Checking case 5 — property-based sweep over healthy latencies
# ---------------------------------------------------------------------------


@settings(
    max_examples=75,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    kind=st.sampled_from(["TEST", "CONFIG_GET", "CONFIG_PUT"]),
    # Healthy dispatcher latencies strictly above the short status timeout (2.0s)
    # and within the dedicated longer test timeout (default 30.0s): the exact
    # window the fix is meant to cover.
    latency=st.floats(
        min_value=SHORT_STATUS_TIMEOUT + 0.05,
        max_value=DEFAULT_TEST_TIMEOUT - 0.05,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_property_healthy_slow_op_returns_dispatcher_result(client, monkeypatch, kind, latency):
    """For all healthy latencies in ``(status_timeout, test_timeout]`` the fixed
    proxy returns the dispatcher's success result (HTTP 200), never a timeout 503.

    Design Fix Checking (property-based, Property 1): the dedicated 30s test
    timeout must comfortably cover every latency in the targeted window, so the
    real dispatcher result is always surfaced and ``requests.request`` always
    uses ``timeout ≈ 30.0``.

    **Validates: Requirements 2.1, 2.2, 2.3, 2.4**
    """
    captured_timeouts: list = []
    _install_latency_aware_fake(
        monkeypatch, simulated_latency=latency, captured_timeouts=captured_timeouts
    )
    do_op, body_ok = _OPERATIONS[kind]

    resp = do_op(client)

    assert resp.status_code == 200, (
        f"{kind} with healthy latency {latency}s returned HTTP {resp.status_code} "
        f"({resp.json()!r}) instead of 200; requests.request used timeout={captured_timeouts}"
    )
    assert body_ok(resp.json())
    assert captured_timeouts and captured_timeouts[-1] == pytest.approx(DEFAULT_TEST_TIMEOUT)
