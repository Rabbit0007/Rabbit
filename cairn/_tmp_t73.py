import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from cairn.server import db, auth_db, product_db

tmp = Path(tempfile.mkdtemp()) / "t.db"
db.configure(tmp)
auth_db.configure_auth_db()
product_db.configure_product_db()
from cairn.server.app import app

c = TestClient(app, base_url="https://testserver")
c.post("/api/auth/register", json={"username": "Tess", "password": "hunter2pw"})

lines = []
r = c.get("/api/templates")
lines.append(f"GET templates -> {r.status_code}")
data = r.json()
builtin = [t for t in data if t.get("is_builtin")]
custom = [t for t in data if not t.get("is_builtin")]
lines.append(f"builtin count -> {len(builtin)}")
lines.append(f"custom count -> {len(custom)}")

# create custom
r = c.post("/api/templates", json={"title": "My Tpl", "origin": "o", "goal": "g", "hints": []})
lines.append(f"create custom -> {r.status_code}")
tid = r.json().get("id") if r.status_code < 300 else None
r = c.get("/api/templates")
lines.append(f"custom after create -> {len([t for t in r.json() if not t.get('is_builtin')])}")
# delete
if tid:
    lines.append(f"delete -> {c.delete(f'/api/templates/{tid}').status_code}")

Path("/tmp/t73.txt").write_text("\n".join(lines) + "\n")
