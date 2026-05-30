"""Task 5.4 verification: workers router wired into the app.

Writes results to _tmp_task54_result.txt to avoid terminal noise.
"""

import os
import tempfile
from pathlib import Path

lines = []


def log(msg):
    lines.append(str(msg))


# Use an isolated temp DB so we never touch the real one. Configure BEFORE the
# app lifespan runs (db.configure is idempotent and returns early once set).
tmpdir = tempfile.mkdtemp(prefix="cairn_task54_")
tmp_db = Path(tmpdir) / "cairn.db"

from cairn.server import db, auth_db, product_db  # noqa: E402

db.configure(tmp_db)
auth_db.configure_auth_db()
product_db.configure_product_db()

# 1. App imports cleanly.
from cairn.server.app import app  # noqa: E402

log("PASS: app imported cleanly")

# 2. Workers routes are registered.
routes = {getattr(r, "path", None) for r in app.routes}
workers_status = "/api/workers"
workers_history = "/api/workers/{name}/history"
log(f"/api/workers registered: {workers_status in routes}")
log(f"/api/workers/{{name}}/history registered: {workers_history in routes}")
assert workers_status in routes, "workers status route missing"
assert workers_history in routes, "workers history route missing"
log("PASS: both workers routes registered")

# Existing routes still present (sanity / no regression).
existing_present = "/projects" in routes and "/settings" in routes
log(f"existing routes still present (/projects, /settings): {existing_present}")
assert existing_present, "existing routes missing after change"

# 3. Auth enforcement: when CAIRN_INTERNAL_TOKEN is set and no cookie/header is
#    provided, protected workers routes must return 401.
from fastapi.testclient import TestClient  # noqa: E402

os.environ["CAIRN_INTERNAL_TOKEN"] = "test-secret-token"
with TestClient(app) as client:
    # Workers history hits the DB only after auth; auth should reject first.
    r_hist = client.get("/api/workers/some-worker/history")
    log(f"GET /api/workers/some-worker/history (no auth) -> {r_hist.status_code}")
    assert r_hist.status_code == 401, f"expected 401, got {r_hist.status_code}"

    # An existing protected route is also enforced.
    r_proj = client.get("/projects")
    log(f"GET /projects (no auth) -> {r_proj.status_code}")
    assert r_proj.status_code == 401, f"expected 401, got {r_proj.status_code}"

    log("PASS: auth enforced (401) on protected routes when token is set")

# 4. Dispatcher pass-through: when CAIRN_INTERNAL_TOKEN is UNSET, require_auth
#    allows requests through (does not 401). Verify on workers history route.
del os.environ["CAIRN_INTERNAL_TOKEN"]
with TestClient(app) as client:
    r = client.get("/api/workers/some-worker/history")
    log(f"GET /api/workers/some-worker/history (token unset) -> {r.status_code}")
    # Should NOT be 401 (dispatcher pass-through preserved). Empty history -> 200 [].
    assert r.status_code != 401, "pass-through broken: got 401 with token unset"
    log("PASS: dispatcher pass-through preserved (no 401 when token unset)")

log("")
log("ALL CHECKS PASSED")

Path(__file__).parent.joinpath("_tmp_task54_result.txt").write_text("\n".join(lines))
print("done")
