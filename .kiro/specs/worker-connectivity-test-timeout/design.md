# Worker Connectivity Test Timeout Bugfix Design

## Overview

The worker dashboard "测试" (test) button reports **"Worker connectivity test failed"** even when the
underlying worker is healthy. The product server (`cairn-server`) proxies the dashboard's request
(`POST /api/workers/config/test`) to the dispatcher's internal API (`POST /internal/workers/test`)
using the `requests` library. That proxy call — along with status polling and config read/write —
shares a single short timeout (`DEFAULT_INTERNAL_TIMEOUT = 2.0s`, overridable via
`CAIRN_DISPATCHER_INTERNAL_TIMEOUT`) resolved by `_status_timeout()` in
`cairn/src/cairn/server/routers/workers.py`.

The connectivity test is genuinely slow: the dispatcher spins up a startup container, execs the
worker CLI against the model endpoint, and tears the container back down. This routinely takes
longer than 2.0s (≈2.05s for a local `pi` worker, and similar or longer for `claudecode` workers
reaching a remote endpoint). When the dispatcher work exceeds the shared 2.0s proxy timeout,
`requests` raises a `RequestException`, the proxy maps it to HTTP 503, and the dashboard shows a
spurious connectivity failure — even though the worker is healthy and a direct call to
`/internal/workers/test` returns `{"ok": true}`.

The fix is minimal and localized. It splits the proxy timeout into two concerns:

- **Status polling** (`GET /api/workers` → `/internal/status`) keeps the short, latency-sensitive
  timeout so a hung or absent dispatcher never blocks the dashboard.
- **Test and config operations** (`POST /api/workers/config/test`, `GET`/`PUT /api/workers/config`)
  use a new, longer timeout resolved from a dedicated environment variable
  (`CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT`) with a default (30s) that comfortably exceeds the
  dispatcher's own `healthcheck_timeout` (20s in `dispatch.yaml`).

The change touches only `workers.py` (timeout resolver + a per-call timeout parameter on the proxy
helper) plus tests, with the new environment variable documented in `docker-compose.yaml`.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug — a test/config proxy operation
  (`TEST`, `CONFIG_GET`, `CONFIG_PUT`) whose dispatcher-side work would succeed but takes longer
  than the short status-polling timeout it currently shares, causing a `requests` timeout and a
  spurious 503.
- **Property (P)**: The desired behavior — test/config operations are proxied with a dedicated
  longer timeout, so a healthy-but-slow operation that completes within that longer timeout returns
  the dispatcher's real result instead of a timeout-based 503.
- **Preservation**: Existing behavior that must remain unchanged — short-timeout status polling,
  genuine unreachable/error handling, the `CAIRN_DISPATCHER_INTERNAL_TIMEOUT` fallback semantics,
  and the status-snapshot reshaping into `WorkerStatus` cards.
- **`_status_timeout()`**: The existing resolver in `cairn/src/cairn/server/routers/workers.py` that
  reads `CAIRN_DISPATCHER_INTERNAL_TIMEOUT` (default `DEFAULT_INTERNAL_TIMEOUT = 2.0s`) and is
  currently used for *every* proxied call.
- **`_test_timeout()`**: The new resolver introduced by this fix that reads
  `CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT` (default `DEFAULT_INTERNAL_TEST_TIMEOUT = 30.0s`) for the
  slower test/config operations.
- **`_request_internal_json(...)`**: The proxy helper in `workers.py` that performs
  `requests.request(...)` against an internal endpoint. It currently always uses `_status_timeout()`;
  the fix gives it a per-call `timeout` parameter so callers select the appropriate timeout.
- **`_fetch_status_snapshot()`**: The status-polling proxy used by `list_workers` (`GET /api/workers`);
  it must keep using the short status timeout.
- **ProxiedOperation**: The conceptual input domain for the bug condition — a proxied dispatcher call
  characterized by its `kind` (TEST / CONFIG_GET / CONFIG_PUT / STATUS), the dispatcher-side latency,
  whether the dispatcher would succeed, and the timeout applied.

## Bug Details

### Bug Condition

The bug manifests when a test or config proxy operation (`POST /api/workers/config/test`,
`GET`/`PUT /api/workers/config`) is invoked, the dispatcher would complete the work successfully,
but that work takes longer than the short status-polling timeout (`2.0s` by default) that the proxy
currently applies to every call. The `_request_internal_json` helper always calls
`requests.request(..., timeout=_status_timeout())`, so the slow-but-healthy operation hits the
short timeout, `requests` raises a `RequestException`, and the handler returns HTTP 503 with the
relevant "unavailable" / "failed" message.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X of type ProxiedOperation { kind, dispatcherWouldSucceed, dispatcherLatencySeconds, statusTimeout }
  OUTPUT: boolean

  // A healthy test/config operation whose real latency exceeds the short,
  // status-polling timeout it currently shares.
  RETURN X.kind IN { TEST, CONFIG_GET, CONFIG_PUT }
         AND X.dispatcherWouldSucceed = TRUE
         AND X.dispatcherLatencySeconds > X.statusTimeout
END FUNCTION
```

### Examples

- **Connectivity test (primary report)**: `POST /api/workers/config/test` for a healthy worker whose
  dispatcher-side test takes ≈2.05s while `statusTimeout = 2.0s`. Expected: `{"ok": true}` (HTTP 200).
  Actual: HTTP 503 `{"message": "Worker connectivity test failed"}`.
- **Remote `claudecode` test**: `POST /api/workers/config/test` for a `claudecode` worker reaching a
  remote endpoint; container startup + exec + teardown exceeds 2.0s. Expected: the real
  `WorkerConnectionTestResult`. Actual: spurious 503.
- **Config read under load**: `GET /api/workers/config` when the dispatcher serializes its YAML and
  responds in >2.0s. Expected: the masked `WorkerConfigResponse`. Actual: HTTP 503
  `{"message": "Worker config unavailable"}`.
- **Edge case — fast operation (NOT the bug)**: `POST /api/workers/config/test` for a `mock` worker
  that completes in 12ms. This is below any timeout and already returns 200; the fix must leave it
  unchanged.
- **Edge case — genuinely unreachable dispatcher (NOT the bug)**: connection refused. The dispatcher
  would not succeed, so this is not the bug condition and must still yield a 503.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Status polling (`GET /api/workers` → `/internal/status`) must continue to use the short status
  timeout (`CAIRN_DISPATCHER_INTERNAL_TIMEOUT`, default 2.0s) so a hung or absent dispatcher never
  blocks the dashboard.
- A genuinely unreachable dispatcher (connection refused / timeout against an absent endpoint) must
  continue to return the appropriate 503 connectivity warning for every proxied endpoint.
- A non-2xx response or a genuine `{"ok": false}` result from `/internal/workers/test` returned
  within the timeout must continue to be propagated unchanged to the dashboard.
- `CAIRN_DISPATCHER_INTERNAL_TIMEOUT` parsing/fallback semantics (valid positive value honored;
  unset/invalid/non-positive falls back to 2.0s) must be preserved for status polling.
- The status snapshot must continue to be reshaped into the existing `WorkerStatus` cards exactly as
  before.

**Scope:**
All inputs that do NOT involve a slow-but-healthy test/config operation should be completely
unaffected by this fix. This includes:
- Status-polling requests (`GET /api/workers`).
- Any proxied request against a genuinely unreachable or erroring dispatcher.
- Test/config operations that already complete within the short timeout (they simply gain more
  headroom and behave identically for fast cases).

**Note:** The expected correct behavior for buggy inputs (test/config operations succeeding under
the longer timeout) is defined in the Correctness Properties section (Property 1). This section
focuses on what must NOT change.

## Hypothesized Root Cause

Based on the bug description and a read of `cairn/src/cairn/server/routers/workers.py`, the cause is
a single shared timeout applied indiscriminately to operations with very different latency profiles:

1. **Single shared timeout for all proxied calls (primary)**: `_request_internal_json` always calls
   `requests.request(method, url, json=payload, timeout=_status_timeout())`, and `_status_timeout()`
   resolves the short status-polling value (`DEFAULT_INTERNAL_TIMEOUT = 2.0s` or
   `CAIRN_DISPATCHER_INTERNAL_TIMEOUT`). The slow `/internal/workers/test` path therefore inherits a
   timeout meant for a fast status poll.

2. **Default timeout below real test latency**: 2.0s is smaller than the observed test latency
   (≈2.05s local, more for remote). The dispatcher's own `healthcheck_timeout` is 20s
   (`dispatch.yaml`), so the proxy should tolerate well beyond that — the proxy is the tighter,
   incorrect bound.

3. **No independent control**: the only tunable, `CAIRN_DISPATCHER_INTERNAL_TIMEOUT`, governs both
   status polling and test/config, so raising it to fix the test also slows the frequent status poll.

4. **Timeout mapped to a connectivity 503**: in `_request_internal_json`, `requests.Timeout` is a
   `requests.RequestException`, which is caught and converted to a 503 with the unavailable message —
   making a slow-but-healthy operation indistinguishable from a down dispatcher.

The fix addresses (1)–(3) directly by introducing a dedicated longer timeout for test/config
operations while leaving status polling on the short timeout; (4) is preserved intentionally for
genuinely unreachable dispatchers.

## Correctness Properties

Property 1: Bug Condition - Test/Config Operations Use the Dedicated Longer Timeout

_For any_ input where the bug condition holds (`isBugCondition` returns true) — a healthy test or
config operation (`TEST`, `CONFIG_GET`, `CONFIG_PUT`) whose dispatcher-side latency exceeds the short
status timeout but is within the dedicated longer test timeout — the fixed proxy SHALL apply the
longer timeout (`CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT`, default 30.0s) and return the dispatcher's
real result (e.g. `{"ok": true}` for the connectivity test, the masked config for reads/writes)
instead of a timeout-based 503.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

Property 2: Preservation - Status Polling, Error Handling, and Snapshot Reshaping Unchanged

_For any_ input where the bug condition does NOT hold (`isBugCondition` returns false) — status
polling, genuinely unreachable/erroring dispatchers, fast operations, and the
`CAIRN_DISPATCHER_INTERNAL_TIMEOUT` fallback path — the fixed proxy SHALL produce the same result as
the original proxy, preserving the short status-polling timeout, the 503 connectivity/error handling,
the status-timeout parsing/fallback semantics, and the reshaping of the status snapshot into
`WorkerStatus` cards.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `cairn/src/cairn/server/routers/workers.py`

**Functions**: new `_test_timeout()`; updated `_request_internal_json(...)`, `get_worker_config`,
`update_worker_config`, `test_worker_config` (status polling via `_fetch_status_snapshot` is left
unchanged).

**Specific Changes**:
1. **New configuration constants**: add the dedicated env var and default alongside the existing ones:
   - `TEST_TIMEOUT_ENV = "CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT"`
   - `DEFAULT_INTERNAL_TEST_TIMEOUT = 30.0`  (comfortably above the dispatcher's 20s
     `healthcheck_timeout`)

2. **New resolver `_test_timeout()`**: mirror `_status_timeout()` exactly for parsing/fallback
   behavior, but read `TEST_TIMEOUT_ENV` and fall back to `DEFAULT_INTERNAL_TEST_TIMEOUT`. Unset,
   blank, non-numeric, or non-positive values fall back to the default; a valid positive value is
   honored. Reuse the same warning-log shape on invalid input. (Consider factoring the shared parse
   logic into a small helper `_resolve_timeout(env_name, default)` that both resolvers call, to avoid
   duplication.)

3. **Per-call timeout on the proxy helper**: give `_request_internal_json` a keyword-only `timeout`
   parameter (e.g. `timeout: float`) and pass it through to `requests.request(..., timeout=timeout)`
   instead of calling `_status_timeout()` internally. This keeps the helper agnostic about which
   timeout to use and makes the selection explicit at each call site.

4. **Route handlers select the longer timeout**: update the three test/config handlers to pass
   `timeout=_test_timeout()`:
   - `get_worker_config` → `GET /internal/workers/config`
   - `update_worker_config` → `PUT /internal/workers/config`
   - `test_worker_config` → `POST /internal/workers/test`

5. **Status polling unchanged**: `_fetch_status_snapshot()` (used by `list_workers`,
   `GET /api/workers`) continues to call `requests.get(url, timeout=_status_timeout())`. No behavior
   change there.

6. **Endpoint → timeout mapping (summary)**:

   | Endpoint (product server)        | Internal path                | Timeout resolver   | Default |
   |----------------------------------|------------------------------|--------------------|---------|
   | `GET /api/workers`               | `GET /internal/status`       | `_status_timeout()`| 2.0s    |
   | `GET /api/workers/config`        | `GET /internal/workers/config` | `_test_timeout()`| 30.0s   |
   | `PUT /api/workers/config`        | `PUT /internal/workers/config` | `_test_timeout()`| 30.0s   |
   | `POST /api/workers/config/test`  | `POST /internal/workers/test`  | `_test_timeout()`| 30.0s   |

**File**: `docker-compose.yaml`

7. **Document the new env var**: add a commented/explicit entry under `cairn-server.environment`
   documenting `CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT` (default 30s) so operators can tune the
   test/config timeout independently of `CAIRN_DISPATCHER_INTERNAL_TIMEOUT`. No value change is
   required for correctness since the default covers the observed latencies.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate
the bug on unfixed code, then verify the fix works correctly and preserves existing behavior. Tests
live in `cairn/tests/` and extend the existing `test_workers_router.py` conventions: mount the
`workers` router on a minimal FastAPI app and monkeypatch `workers.requests` so no real network or
dispatcher is needed. Latency is simulated by making the patched `requests.request` inspect the
`timeout` argument and either raise `requests.Timeout` (when the simulated dispatcher latency exceeds
the supplied timeout) or return a `_FakeResponse` (when it completes within the timeout).

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or
refute the root cause (a single short timeout shared with status polling). If refuted, re-hypothesize.

**Test Plan**: Write tests that monkeypatch `workers.requests.request` with a latency-aware fake: it
records the `timeout` it was called with and raises `requests.Timeout` when a simulated dispatcher
latency (e.g. 2.05s) exceeds that `timeout`. Run against the UNFIXED code, where every call uses
`_status_timeout()` (2.0s), to observe the spurious 503s and confirm the shared-timeout root cause.

**Test Cases**:
1. **Slow connectivity test**: `POST /api/workers/config/test` with simulated latency 2.05s and the
   default timeout → observe the call is made with `timeout≈2.0` and returns 503
   "Worker connectivity test failed" (will fail on unfixed code).
2. **Slow config read**: `GET /api/workers/config` with simulated latency 2.5s → observe 503
   "Worker config unavailable" (will fail on unfixed code).
3. **Slow config write**: `PUT /api/workers/config` with simulated latency 2.5s → observe 503
   "Worker config update failed" (will fail on unfixed code).
4. **Timeout argument inspection**: assert the `timeout` passed to `requests.request` for the test
   endpoint equals the short status timeout (≈2.0s) on unfixed code — pinning the root cause (will
   change after the fix to the longer timeout).

**Expected Counterexamples**:
- Test/config endpoints invoke `requests.request` with `timeout≈2.0s` and return a 503 when the
  simulated latency is just above 2.0s, despite the dispatcher "succeeding".
- Possible causes: shared `_status_timeout()` in `_request_internal_json`, default below real test
  latency, no independent env var.

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed proxy uses the longer
timeout and produces the expected (success) result.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := proxy_fixed(input)   // test/config ops use _test_timeout() (default 30s)
  ASSERT input.dispatcherLatencySeconds <= testTimeout
         IMPLIES result = dispatcherResult(input)   // no timeout-based 503
END FOR
```

**Test Cases**:
1. **Connectivity test under longer timeout**: simulated latency 2.05s, `_test_timeout()` default 30s
   → expect HTTP 200 with the real `WorkerConnectionTestResult` (`ok: true`), and assert
   `requests.request` was called with `timeout≈30.0`.
2. **Config read/write under longer timeout**: simulated latency 2.5s → expect 200 with the masked
   `WorkerConfigResponse` for GET and the applied config for PUT.
3. **Custom env override honored**: set `CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT=5`, simulated latency
   4s → expect 200 (within 5s); simulated latency 6s → expect 503 (genuine timeout beyond the
   configured bound), demonstrating the dedicated var controls the test/config timeout.
4. **Invalid/unset env falls back to 30s default**: with the var unset or set to `"abc"` / `"-1"`,
   `_test_timeout()` returns 30.0 and a 2.05s test succeeds.

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed proxy produces
the same result as the original proxy.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT proxy_original(input) = proxy_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain (operation kind, latency,
  reachability, and timeout env values).
- It catches edge cases that manual unit tests might miss (e.g. latency exactly at a boundary, blank
  vs invalid env strings).
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs.

A practical PBT formulation (Hypothesis) generates a `ProxiedOperation` — `kind` ∈ {STATUS,
TEST, CONFIG_GET, CONFIG_PUT}, a dispatcher latency, a reachable/error flag, and timeout env values —
and asserts that for every input where `isBugCondition` is false, the fixed router returns the same
status code and body as the original. Status-polling inputs and genuinely unreachable/erroring inputs
are the core of this domain.

**Test Plan**: Observe behavior on UNFIXED code first for status polling, unreachable dispatchers,
non-2xx/`ok:false` results, and the timeout fallback path, then write property-based and example
tests capturing that behavior and assert it is unchanged after the fix.

**Test Cases**:
1. **Status polling uses short timeout**: observe that `GET /api/workers` calls `requests.get` with
   `timeout≈2.0s` (or the `CAIRN_DISPATCHER_INTERNAL_TIMEOUT` value) before and after the fix; assert
   the status timeout is unchanged and the longer test timeout is never applied to status polling.
2. **Unreachable dispatcher still 503**: a `requests.ConnectionError` on any endpoint yields the same
   503 connectivity warning before and after the fix.
3. **Genuine error/`ok:false` propagated**: a non-2xx response or `{"ok": false}` from
   `/internal/workers/test` within the timeout is propagated unchanged.
4. **Status-timeout fallback unchanged**: valid positive `CAIRN_DISPATCHER_INTERNAL_TIMEOUT` honored;
   unset/invalid/non-positive falls back to 2.0s — identical before and after the fix.
5. **Snapshot reshaping unchanged**: `GET /api/workers` reshapes a canned snapshot into the same
   `WorkerStatus` cards (status mapping, current-task truncation, completed counts, average duration,
   heartbeat age) as before.

### Unit Tests

- `_test_timeout()` parsing: unset/blank → 30.0; valid positive → that value; non-numeric → 30.0 with
  a warning log; zero/negative → 30.0.
- `_test_timeout()` is independent of `_status_timeout()`: setting one env var does not affect the
  other resolver's output.
- `_request_internal_json` passes its `timeout` argument through to `requests.request` unchanged.
- Each test/config handler invokes the proxy with `timeout=_test_timeout()`; `list_workers` invokes
  the status proxy with `timeout=_status_timeout()`.

### Property-Based Tests

- Generate random `(kind, latency, reachable, status_timeout_env, test_timeout_env)` tuples and assert
  the preservation property: for all non-buggy inputs the fixed router matches the original router's
  status code and body.
- Generate random valid/invalid env strings for `CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT` and assert
  `_test_timeout()` always returns a positive float (the configured value or the 30.0 default).
- Generate random healthy test/config latencies in `(status_timeout, test_timeout]` and assert the
  fixed router returns the dispatcher's success result (Fix Checking, Property 1).

### Integration Tests

- Full proxy flow for `POST /api/workers/config/test` against a fake internal app whose handler sleeps
  ~2.05s: 503 on unfixed code, 200 on fixed code under the default 30s timeout.
- Switching env configuration: with `CAIRN_DISPATCHER_INTERNAL_TEST_TIMEOUT` set low, a slow test
  times out (503); unset, it succeeds — while `GET /api/workers` polling latency/timeout is unaffected.
- End-to-end mapping check: confirm status polling and test/config operations resolve their timeouts
  from the correct, independent environment variables in a single run.
