# Implementation Plan: Worker Connectivity Test Timeout

## Overview

Bug summary: every dispatcher proxy call in `cairn/src/cairn/server/routers/workers.py`
(`_request_internal_json` and `_fetch_status_snapshot`) shares the short status-polling timeout
(`_status_timeout()`, default `DEFAULT_INTERNAL_TIMEOUT = 2.0s`). The connectivity test
(`POST /api/workers/config/test`) and config read/write operations are genuinely slow (≈2.05s+), so
they exceed the 2.0s bound, `requests` raises `RequestException`, and the handler returns a spurious
HTTP 503 ("Worker connectivity test failed" / "Worker config unavailable" / "Worker config update
failed") even though the worker is healthy.

Fix (from design): split the timeout into two concerns — keep `_status_timeout()` for status polling
and introduce a dedicated longer `_test_timeout()` (`CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT`, default
`DEFAULT_INTERNAL_TEST_TIMEOUT = 30.0s`) used by the test/config handlers via a per-call `timeout`
parameter on `_request_internal_json`.

This plan follows the bug-condition methodology: write the failing bug-condition exploration test
first (Property 1), capture baseline behavior with preservation tests (Property 2), then implement
the minimal fix and re-run both to validate Fix Checking and Preservation Checking.

Build/test command (run from the `cairn/` directory):
`uv run --with pytest --with httpx python -m pytest`
Property-based tasks add `--with hypothesis` (Hypothesis is not yet a project dependency):
`uv run --with pytest --with httpx --with hypothesis python -m pytest`

Tests extend the existing `cairn/tests/test_workers_router.py` / `cairn/tests/test_worker_config_api.py`
conventions: mount the `workers` router on a minimal FastAPI app and monkeypatch `workers.requests`
with a latency-aware fake that inspects the `timeout` argument and raises `requests.Timeout` when the
simulated dispatcher latency exceeds that `timeout`, otherwise returns a `_FakeResponse`.

## Tasks

- [x] 1. Write bug condition exploration test (BEFORE implementing the fix)
  - **Property 1: Bug Condition** - Slow Test/Config Operations Spuriously Time Out at the Shared Status Timeout
  - **CRITICAL**: This test MUST FAIL on the current (unfixed) code — failure confirms the bug exists.
  - **DO NOT attempt to fix the test or the production code when it fails** at this stage.
  - **NOTE**: This test encodes the expected behavior (a healthy slow test/config op returns the real
    dispatcher result, not a 503). It will validate the fix once it passes after implementation.
  - **GOAL**: Surface counterexamples that demonstrate the shared-timeout root cause.
  - **Scoped PBT Approach**: This is a deterministic bug. Scope the property to the concrete failing
    cases — the test/config kinds (`POST /api/workers/config/test`, `GET`/`PUT /api/workers/config`)
    with a simulated dispatcher latency just above the short status timeout. Parameterize/generate
    over the test+config endpoints and a small set of latencies in `(2.0s, 30.0s]` (e.g. 2.05s, 2.5s).
  - Add a latency-aware fake for `workers.requests.request`: it records the `timeout` it was called
    with and raises `requests.Timeout` when `simulated_latency > timeout`, else returns a
    `_FakeResponse` carrying the dispatcher's success body (e.g. `{"ok": true, ...}` for the test
    endpoint; a masked `WorkerConfigResponse` body for config GET/PUT).
  - Bug Condition (from design `isBugCondition`): `kind ∈ {TEST, CONFIG_GET, CONFIG_PUT}` AND
    `dispatcherWouldSucceed = TRUE` AND `dispatcherLatencySeconds > statusTimeout`.
  - Expected Behavior asserted by the test (from design Property 1): HTTP 200 with the dispatcher's
    real result (e.g. `{"ok": true}` for `POST /api/workers/config/test`).
  - Also assert the timeout-argument counterexample: on unfixed code `requests.request` is invoked
    with `timeout ≈ 2.0s` (the shared status timeout) for the test/config endpoints — pinning the
    root cause.
  - Run on UNFIXED code with: `uv run --with pytest --with httpx --with hypothesis python -m pytest`
    (from `cairn/`).
  - **EXPECTED OUTCOME**: Test FAILS (the slow-but-healthy op returns 503 instead of 200, and the
    captured `timeout` is ≈2.0s) — this is correct and proves the bug exists.
  - Document the counterexamples found, e.g. "POST /api/workers/config/test with latency 2.05s and
    the default timeout returns 503 'Worker connectivity test failed'; requests.request was called
    with timeout≈2.0".
  - Mark this task complete when the test is written, run on unfixed code, and the failure is
    documented.
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Write preservation property tests (BEFORE implementing the fix)
  - **Property 2: Preservation** - Status Polling, Error Handling, and Snapshot Reshaping Unchanged
  - **IMPORTANT**: Follow the observation-first methodology — run the UNFIXED code for non-buggy
    inputs (`isBugCondition` returns false), record the actual outputs, then write tests asserting
    those observed outputs.
  - **Testing approach**: Property-based testing (Hypothesis) is recommended here because preservation
    is a universal property ("for all non-buggy inputs F(X) = F'(X)"). Generate a `ProxiedOperation`
    over `kind ∈ {STATUS, TEST, CONFIG_GET, CONFIG_PUT}`, a dispatcher latency, a reachable/error
    flag, and `CAIRN_DISPATCHER_INTERNAL_TIMEOUT` env strings (valid/blank/invalid/non-positive), and
    assert that for every input where `isBugCondition` is false the router returns the same status
    code and body. Pair the PBT with concrete example tests for the key scenarios below.
  - Observe + assert the following non-buggy behaviors on UNFIXED code (from design Preservation
    Requirements):
    - Status polling: `GET /api/workers` calls `requests.get` with `timeout ≈ 2.0s` (or the
      `CAIRN_DISPATCHER_INTERNAL_TIMEOUT` value) and reshapes the snapshot into `WorkerStatus` cards.
    - Unreachable dispatcher: a `requests.ConnectionError` on any endpoint yields a 503 connectivity
      warning ("Worker status unavailable" for status; the per-endpoint unavailable message for
      test/config).
    - Genuine error / `{"ok": false}`: a non-2xx response or a within-timeout `{"ok": false}` body
      from `/internal/workers/test` is propagated unchanged.
    - Status-timeout fallback: valid positive `CAIRN_DISPATCHER_INTERNAL_TIMEOUT` honored;
      unset/blank/invalid/non-positive falls back to 2.0s.
    - Snapshot reshaping: status mapping (idle/busy/offline/disabled), current-task truncation to 120
      chars, completed counts, average-duration rounding, heartbeat age — identical to today.
  - Run on UNFIXED code with: `uv run --with pytest --with httpx --with hypothesis python -m pytest`
    (from `cairn/`).
  - **EXPECTED OUTCOME**: Tests PASS (this confirms the baseline behavior to preserve).
  - Mark this task complete when the tests are written, run, and passing on unfixed code.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix for slow test/config proxy operations spuriously timing out at the shared status timeout

  - [x] 3.1 Add the dedicated timeout configuration constants and resolver in `workers.py`
    - In `cairn/src/cairn/server/routers/workers.py` add `TEST_TIMEOUT_ENV =
      "CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT"` and `DEFAULT_INTERNAL_TEST_TIMEOUT = 30.0` alongside
      the existing `INTERNAL_TIMEOUT_ENV` / `DEFAULT_INTERNAL_TIMEOUT` constants (30.0s comfortably
      exceeds the dispatcher's 20s `healthcheck_timeout`).
    - Add a new resolver `_test_timeout()` mirroring `_status_timeout()` parse/fallback exactly:
      read `TEST_TIMEOUT_ENV`; unset/blank/non-numeric/non-positive → `DEFAULT_INTERNAL_TEST_TIMEOUT`;
      valid positive value honored; reuse the same warning-log shape on invalid input.
    - Optionally factor the shared parse logic into `_resolve_timeout(env_name, default)` and have
      both `_status_timeout()` and `_test_timeout()` call it, to avoid duplication.
    - _Bug_Condition: isBugCondition(X) where X.kind ∈ {TEST, CONFIG_GET, CONFIG_PUT} AND X.dispatcherWouldSucceed AND X.dispatcherLatencySeconds > X.statusTimeout (from design)_
    - _Expected_Behavior: test/config ops resolve their timeout from CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT (default 30.0s), independent of status polling (expectedBehavior / Property 1 from design)_
    - _Preservation: _status_timeout() parsing/fallback semantics unchanged (Preservation Requirements from design)_
    - _Requirements: 2.4, 3.4_

  - [x] 3.2 Add a per-call `timeout` parameter to `_request_internal_json` and select the longer timeout in the test/config handlers
    - Give `_request_internal_json(...)` a keyword-only `timeout: float` parameter and pass it through
      to `requests.request(method, url, json=payload, timeout=timeout)` instead of calling
      `_status_timeout()` internally — making the timeout selection explicit at each call site.
    - Update `get_worker_config` (`GET /internal/workers/config`), `update_worker_config`
      (`PUT /internal/workers/config`), and `test_worker_config` (`POST /internal/workers/test`) to
      pass `timeout=_test_timeout()`.
    - Leave `_fetch_status_snapshot()` / `list_workers` unchanged — they continue to call
      `requests.get(url, timeout=_status_timeout())`.
    - Confirm the endpoint→timeout mapping matches the design table (status → `_status_timeout()` 2.0s;
      config GET/PUT and test → `_test_timeout()` 30.0s).
    - _Bug_Condition: isBugCondition(X) — the slow-but-healthy TEST/CONFIG proxy call (from design)_
    - _Expected_Behavior: expectedBehavior(result) — a healthy op within the longer timeout returns dispatcherResult(X) instead of a timeout-based 503 (Property 1 from design)_
    - _Preservation: status polling stays on _status_timeout(); genuine unreachable/error handling and 503 mapping unchanged (Preservation Requirements from design)_
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.3 Document the new environment variable in `docker-compose.yaml`
    - Under `cairn-server.environment` (next to `CAIRN_DISPATCHER_INTERNAL_URL`), document
      `CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT` (default 30s) so operators can tune the test/config
      timeout independently of `CAIRN_DISPATCHER_INTERNAL_TIMEOUT`. No value change is required for
      correctness since the default covers the observed latencies.
    - _Expected_Behavior: operator-tunable dedicated timeout var, independent of status polling (from design)_
    - _Requirements: 2.4_

  - [x] 3.4 Add Fix Checking tests for the dedicated longer timeout
    - **Property 1: Expected Behavior** - Test/Config Operations Use the Dedicated Longer Timeout
    - **IMPORTANT**: This extends the validation beyond the task 1 re-run — write the additional
      Fix-Checking cases from the design here (using the same latency-aware fake from task 1).
    - Connectivity test under the longer timeout: simulated latency 2.05s, default `_test_timeout()`
      30s → expect HTTP 200 with the real `WorkerConnectionTestResult` (`ok: true`), and assert
      `requests.request` was called with `timeout ≈ 30.0`.
    - Config read/write under the longer timeout: simulated latency 2.5s → expect 200 with the masked
      `WorkerConfigResponse` (GET) and the applied config (PUT).
    - Custom env override honored: `CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT=5`, latency 4s → 200;
      latency 6s → 503 (genuine timeout beyond the configured bound).
    - Invalid/unset env falls back to 30s: var unset or set to `"abc"` / `"-1"` → `_test_timeout()`
      returns 30.0 and a 2.05s test succeeds (200).
    - Property-based (optional, recommended): generate healthy test/config latencies in
      `(status_timeout, test_timeout]` and assert the fixed router returns the dispatcher's success
      result.
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.5 Verify the bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Slow Test/Config Operations Succeed Under the Longer Timeout
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test. The task 1 test
      encodes the expected behavior; when it passes it confirms the bug is fixed.
    - Run with: `uv run --with pytest --with httpx --with hypothesis python -m pytest` (from `cairn/`).
    - **EXPECTED OUTCOME**: Test PASSES (the slow-but-healthy test/config op now returns 200 with the
      real dispatcher result, and `requests.request` is called with `timeout ≈ 30.0`).
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.6 Verify the preservation property tests still pass
    - **Property 2: Preservation** - Status Polling, Error Handling, and Snapshot Reshaping Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests.
    - Run with: `uv run --with pytest --with httpx --with hypothesis python -m pytest` (from `cairn/`).
    - **EXPECTED OUTCOME**: Tests PASS (no regressions — status polling keeps the 2.0s timeout, the
      longer test timeout is never applied to status polling, unreachable/error handling and snapshot
      reshaping are unchanged).
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run the full suite from the `cairn/` directory:
    `uv run --with pytest --with httpx --with hypothesis python -m pytest`
  - Confirm the task 1 bug-condition test now passes, the Fix Checking tests (task 3.4) pass, and the
    preservation tests (task 2) still pass with no regressions across the existing
    `test_workers_router.py` / `test_worker_config_api.py` suites.
  - Ensure all tests pass, ask the user if questions arise.

## Notes

### Specification References

- **Bug Condition (C)**: `kind ∈ {TEST, CONFIG_GET, CONFIG_PUT}` AND `dispatcherWouldSucceed = TRUE`
  AND `dispatcherLatencySeconds > statusTimeout` (design "Bug Condition" / `isBugCondition`).
- **Expected Behavior / Property 1 (Fix Checking)**: for all `X` where `isBugCondition(X)` and
  `dispatcherLatencySeconds <= testTimeout`, the fixed proxy applies `_test_timeout()` (default 30s)
  and returns `dispatcherResult(X)` instead of a timeout-based 503. Validates Requirements 2.1, 2.2,
  2.3, 2.4.
- **Property 2 (Preservation)**: for all `X` where `NOT isBugCondition(X)`, `F(X) = F'(X)` — status
  polling timeout, 503 connectivity/error handling, status-timeout fallback semantics, and snapshot
  reshaping are unchanged. Validates Requirements 3.1, 3.2, 3.3, 3.4, 3.5.

### Methodology Notes

- Tasks 1 and 2 are standalone and MUST precede any production change (bug-first / observation-first).
- Task 1 must FAIL on unfixed code (proves the bug); Task 2 must PASS on unfixed code (baseline).
- The same task-1 test is re-run in task 3.5 (must now PASS); the same task-2 tests are re-run in
  task 3.6 (must still PASS).
- Property-based tasks require `--with hypothesis` since Hypothesis is not yet a project dependency.
- All production changes are confined to `cairn/src/cairn/server/routers/workers.py` and a
  documentation-only edit to `docker-compose.yaml`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2"] },
    { "id": 2, "tasks": ["3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3"] },
    { "id": 4, "tasks": ["3.4", "3.5", "3.6"] },
    { "id": 5, "tasks": ["4"] }
  ]
}
```
