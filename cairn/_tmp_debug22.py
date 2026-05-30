import os
import tempfile
from pathlib import Path

from cairn.server import auth_db, db

tmp = Path(tempfile.mkdtemp()) / "cairn_debug22.db"
db.configure(tmp)
auth_db.configure_auth_db()

from fastapi.testclient import TestClient
from cairn.server.app import app

with db.get_conn() as conn:
    conn.execute("INSERT INTO projects (id, title, status, created_at) VALUES (?, ?, 'active', ?)", ("p1", "Demo", "2024-01-01T00:00:00Z"))
    conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('origin', 'p1', 'o')")
    conn.execute("INSERT INTO facts (id, project_id, description) VALUES ('goal', 'p1', 'g')")

client = TestClient(app, base_url="http://testserver")
r = client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
print("register", r.status_code, "cookie jar:", dict(client.cookies))

os.environ["CAIRN_INTERNAL_TOKEN"] = "super-secret-internal-token"
re_cookie = client.get("/projects")
print("enforced cookie GET /projects ->", re_cookie.status_code)
print("body:", re_cookie.text[:500])
print("request cookies sent? jar:", dict(client.cookies))

# Inspect DB session state
with db.get_conn() as conn:
    rows = conn.execute("SELECT token, user_id, expires_at FROM sessions").fetchall()
    for row in rows:
        print("session row:", dict(row))
os.environ.pop("CAIRN_INTERNAL_TOKEN", None)
