# Bugfix Requirements Document

## Introduction

The Worker dashboard's "测试" (test) button reports **"Worker connectivity test failed"** even when the workers are healthy. The button triggers `POST /api/workers/config/test`, which the product server proxies to the dispatcher's internal `/internal/workers/test` endpoint using `requests`.

All server-side proxy calls — frequent status polling (`GET /api/workers`), config read/write (`GET`/`PUT /api/workers/config`), and the connectivity test (`POST /api/workers/config/test`) — share a single short timeout resolved by `_status_timeout()` (the `CAIRN_DISPATCHER_INTERNAL_TIMEOUT` env var, defaulting to `DEFAULT_INTERNAL_TIMEOUT = 2.0` seconds).

The real connectivity test does substantial work: the dispatcher creates a startup container, execs the worker CLI against the model endpoint, and tears the container down. This routinely takes longer than 2.0 seconds (≈2.05s for the local `pi` worker, ≈1.78–1.97s+ for `claudecode` workers reaching the remote endpoint). When the test exceeds the shared 2.0s proxy timeout, `requests` raises a `RequestException` and the server returns HTTP 503 with detail "Worker connectivity test failed" — even though the underlying worker is healthy (the dispatcher startup healthcheck reports all workers healthy, and a direct call to `/internal/workers/test` returns `{"ok": true}`).

The fix separates the timeout for the longer-running test and config operations from the short timeout used for latency-sensitive status polling, with the longer timeout configurable via its own environment variable and a default that comfortably exceeds the dispatcher's own `healthcheck_timeout` (20s in `dispatch.yaml`).

## Bug Analysis

### Current Behavior (Defect)

When the underlying worker is healthy but the connectivity test takes longer than the shared 2.0s proxy timeout, the dashboard reports a failure.

1.1 WHEN the connectivity test (`POST /api/workers/config/test`) is invoked and the dispatcher's `/internal/workers/test` work takes longer than the shared proxy timeout (`DEFAULT_INTERNAL_TIMEOUT = 2.0s`, or `CAIRN_DISPATCHER_INTERNAL_TIMEOUT`) THEN the proxy `requests` call times out, raises `RequestException`, and the server returns HTTP 503 with detail "Worker connectivity test failed"

1.2 WHEN a worker is actually healthy (the dispatcher startup healthcheck passes and a direct `/internal/workers/test` call returns `{"ok": true}`) but its test latency exceeds 2.0s THEN the dashboard surfaces a spurious connectivity failure that misrepresents the worker as broken

1.3 WHEN config read/write operations (`GET`/`PUT /api/workers/config`) take longer than the shared 2.0s proxy timeout THEN the proxy times out and the server returns a spurious 503 ("Worker config unavailable" / "Worker config update failed")

1.4 WHEN the timeout for the test/config operations is changed THEN the only available control (`CAIRN_DISPATCHER_INTERNAL_TIMEOUT`) also lengthens the status-polling timeout, so the test cannot be made more tolerant without also slowing the frequent status poll

### Expected Behavior (Correct)

The connectivity test and config operations use a separate, longer timeout so healthy-but-slow operations succeed, while status polling keeps its short timeout.

2.1 WHEN the connectivity test (`POST /api/workers/config/test`) is invoked and the dispatcher completes the test within the dedicated longer timeout THEN the server SHALL return the test result from `/internal/workers/test` (e.g. `{"ok": true}`) without raising a timeout-based 503

2.2 WHEN a worker is healthy and its test latency is below the dedicated longer timeout (default comfortably above the dispatcher's 20s `healthcheck_timeout`, e.g. 30s) THEN the system SHALL report the test as successful rather than "Worker connectivity test failed"

2.3 WHEN config read/write operations (`GET`/`PUT /api/workers/config`) take longer than the short status-polling timeout but complete within the dedicated longer timeout THEN the system SHALL return the config result without a timeout-based 503

2.4 WHEN an operator needs to tune the test/config timeout THEN the system SHALL read it from a dedicated environment variable that is independent of the status-polling timeout, falling back to a sensible default when unset or invalid

### Unchanged Behavior (Regression Prevention)

The short status-polling timeout and all genuine failure handling remain unchanged.

3.1 WHEN status polling (`GET /api/workers` → `/internal/status`) is invoked THEN the system SHALL CONTINUE TO use the short status timeout (`CAIRN_DISPATCHER_INTERNAL_TIMEOUT`, default 2.0s) so a hung or absent dispatcher never blocks the dashboard

3.2 WHEN the dispatcher is genuinely unreachable (connection refused/no route) for any proxied endpoint THEN the system SHALL CONTINUE TO return the appropriate 503 connectivity warning

3.3 WHEN the dispatcher's `/internal/workers/test` endpoint returns a non-2xx error or a genuine `{"ok": false}` result within the timeout THEN the system SHALL CONTINUE TO propagate that error/result unchanged to the dashboard

3.4 WHEN `CAIRN_DISPATCHER_INTERNAL_TIMEOUT` is set to a valid positive value THEN status polling SHALL CONTINUE TO honor that value, and an unset/invalid value SHALL CONTINUE TO fall back to the 2.0s default

3.5 WHEN the dispatcher status snapshot is fetched successfully THEN the system SHALL CONTINUE TO reshape it into the existing `WorkerStatus` cards exactly as before

## Bug Condition Derivation

Using the bug condition methodology, where **F** is the proxy before the fix and **F'** is the proxy after the fix.

**Bug Condition Function** — identifies inputs (proxied operations) that trigger the spurious failure:

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type ProxiedOperation { kind, dispatcherLatencySeconds, statusTimeout }
  OUTPUT: boolean

  // The test/config operation is healthy at the dispatcher but its real
  // latency exceeds the short status-polling timeout it currently shares.
  RETURN X.kind IN { TEST, CONFIG_GET, CONFIG_PUT }
         AND X.dispatcherWouldSucceed = TRUE
         AND X.dispatcherLatencySeconds > X.statusTimeout
END FUNCTION
```

Concrete counterexample: `POST /api/workers/config/test` for a healthy worker whose dispatcher-side test takes ≈2.05s while `statusTimeout = 2.0s` → server returns 503 "Worker connectivity test failed".

**Property: Fix Checking** — for buggy inputs, the fixed proxy must succeed using the dedicated longer timeout:

```pascal
// Property: Fix Checking - test/config operations use the longer timeout
FOR ALL X WHERE isBugCondition(X) DO
  result ← proxy'(X)   // F' uses testTimeout (default ~30s) for TEST/CONFIG ops
  ASSERT X.dispatcherLatencySeconds <= testTimeout
         IMPLIES result = dispatcherResult(X)   // no timeout-based 503
END FOR
```

**Property: Preservation Checking** — for non-buggy inputs, the fixed proxy behaves identically to the original:

```pascal
// Property: Preservation Checking
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

This preserves status polling behavior, the short timeout, genuine unreachable/error handling, and the snapshot reshaping for all inputs that do not trigger the bug.
