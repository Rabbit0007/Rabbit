import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from cairn.server import db, auth_db

tmp = Path(tempfile.mkdtemp()) / "t.db"
db.configure(tmp)
auth_db.configure_auth_db()
from cairn.server.app import app

# browser client with session cookie
c = TestClient(app, base_url="https://testserver")
r = c.post("/api/auth/register", json={"username": "Zoe", "password": "hunter2pw"})
print("register", r.status_code)
print("cookie-authed /projects", c.get("/projects").status_code)

# fresh client, no auth -> blocked because token IS configured
c2 = TestClient(app, base_url="https://testserver")
print("no-auth /projects (enforced)", c2.get("/projects").status_code)
print("internal-token /projects", c2.get("/projects", headers={"X-Cairn-Internal-Token": "secret123"}).status_code)
print("wrong token /projects", c2.get("/projects", headers={"X-Cairn-Internal-Token": "nope"}).status_code)
print("exempt /", c2.get("/").status_code)
