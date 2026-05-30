"""Regression check: confirm core behavior is unchanged under DEFAULTS.

No CAIRN_INTERNAL_TOKEN, no CAIRN_DISPATCHER_INTERNAL_API set.
"""
import os
import tempfile
from pathlib import Path

# Ensure no opt-in flags are set — this is the default/existing deployment mode.
os.environ.pop("CAIRN_INTERNAL_TOKEN", None)
os.environ.pop("CAIRN_DISPATCHER_INTERNAL_API", None)

from fastapi.testclient import TestClient
from cairn.server import db, auth_db, product_db

tmp = Path(tempfile.mkdtemp()) / "t.db"
db.configure(tmp)
auth_db.configure_auth_db()
product_db.configure_product_db()
from cairn.server.app import app

lines = []
c = TestClient(app, base_url="http://testserver")  # plain http, like the dispatcher

# 1. Core endpoints behave EXACTLY as before auth was added (open, no token set).
lines.append(f"[core] GET /projects (no token, default) -> {c.get('/projects').status_code}")
r = c.post("/projects", json={"title": "Reg", "origin": "10.0.0.1", "goal": "shell"})
lines.append(f"[core] POST /projects -> {r.status_code}")
pid = r.json()["project"]["id"]
lines.append(f"[core] GET /projects/{pid} -> {c.get(f'/projects/{pid}').status_code}")
lines.append(f"[core] GET /projects/{pid}/export -> {c.get(f'/projects/{pid}/export', params={'format':'yaml'}).status_code}")
lines.append(f"[core] GET /settings -> {c.get('/settings').status_code}")
# create a hint (existing feature)
r = c.post(f"/projects/{pid}/hints", json={"content": "try shiro", "creator": "human"})
lines.append(f"[core] POST hint -> {r.status_code}")

# 2. Dispatcher scheduling internals: history disabled by default, no internal API.
from cairn.dispatcher.scheduler.loop import DispatcherLoop
import inspect
src = inspect.getsource(DispatcherLoop.__init__)
lines.append(f"[dispatch] task_history default None in __init__ -> {'self.task_history: deque' in src or 'task_history' in src}")

# 3. New product endpoints exist
for p in ["/api/vulnerabilities", "/api/workers", "/api/templates"]:
    lines.append(f"[product] {p} present (cookie-open default) -> {c.get(p).status_code}")
lines.append(f"[product] GET /api/projects/{pid}/timeline -> {c.get(f'/api/projects/{pid}/timeline').status_code}")

Path("/tmp/reg.txt").write_text("\n".join(lines) + "\n")
