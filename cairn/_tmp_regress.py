import os
import tempfile
from pathlib import Path

# Default (no internal token): existing deployment behavior must be preserved.
from fastapi.testclient import TestClient
from cairn.server import db, auth_db, product_db

tmp = Path(tempfile.mkdtemp()) / "t.db"
db.configure(tmp)
auth_db.configure_auth_db()
product_db.configure_product_db()
from cairn.server.app import app

lines = []
c = TestClient(app, base_url="http://testserver")

# CORE (no token configured -> dispatcher-safe open behavior, unchanged)
lines.append(f"[core] GET /projects -> {c.get('/projects').status_code}")
r = c.post("/projects", json={"title": "T", "origin": "o", "goal": "g"})
lines.append(f"[core] POST /projects -> {r.status_code}")
pid = r.json()["project"]["id"]
lines.append(f"[core] GET /projects/{{id}} -> {c.get(f'/projects/{pid}').status_code}")
lines.append(f"[core] GET /settings -> {c.get('/settings').status_code}")
lines.append(f"[core] GET /projects/{{id}}/export -> {c.get(f'/projects/{pid}/export?format=yaml').status_code}")
lines.append(f"[core] POST intent -> {c.post(f'/projects/{pid}/intents', json={'from':['origin'],'description':'d','creator':'w','worker':None}).status_code}")

# NEW product endpoints reachable (open in default mode)
lines.append(f"[new] GET /api/templates -> {c.get('/api/templates').status_code}")
lines.append(f"[new] GET /api/vulnerabilities/summary -> {c.get('/api/vulnerabilities/summary').status_code}")
lines.append(f"[new] GET /api/projects/{{id}}/timeline -> {c.get(f'/api/projects/{pid}/timeline').status_code}")
lines.append(f"[ui] GET / -> {c.get('/').status_code}")

Path("/tmp/regress.txt").write_text("\n".join(lines) + "\n")
