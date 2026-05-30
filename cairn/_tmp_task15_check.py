"""Temporary verification for task 1.5: logout, /me, password change.

Run with the venv python from the repo root:
  ./cairn/.venv/bin/python cairn/_tmp_task15_check.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

# Configure a temp DB BEFORE importing the app so lifespan's db.configure
# (which short-circuits when _db_path is already set) becomes a no-op.
from cairn.server import db, auth_db

_tmp = tempfile.mkdtemp()
db.configure(Path(_tmp) / "test.db")
auth_db.configure_auth_db()

from fastapi.testclient import TestClient
from cairn.server.app import app

# https base_url so Secure cookies are stored/sent by the test client.
client = TestClient(app, base_url="https://testserver")

failures = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    if not cond:
        failures.append(name)


# --- Register user A (gives us a session cookie on the client) -------------
r = client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
check("register alice -> 201", r.status_code == 201)
session_a1 = client.cookies.get("session_token")
check("register set session cookie", bool(session_a1))

# --- GET /me returns the current user for a valid session ------------------
r = client.get("/api/auth/me")
check("/me 200 for valid session", r.status_code == 200)
check("/me returns username alice", r.json().get("username") == "alice")

# --- GET /me with an invalid token returns 401 ----------------------------
bad = TestClient(app, base_url="https://testserver")
bad.cookies.set("session_token", "deadbeef" * 8)
r = bad.get("/api/auth/me")
check("/me 401 for invalid token", r.status_code == 401)

# --- GET /me with no cookie returns 401 -----------------------------------
nocookie = TestClient(app, base_url="https://testserver")
r = nocookie.get("/api/auth/me")
check("/me 401 for missing token", r.status_code == 401)

# --- logout invalidates the session ---------------------------------------
r = client.post("/api/auth/logout")
check("logout -> 204", r.status_code == 204)
# After logout the session row must be gone: re-using the old token -> 401
check2 = TestClient(app, base_url="https://testserver")
check2.cookies.set("session_token", session_a1)
r = check2.get("/api/auth/me")
check("session invalidated after logout (/me 401)", r.status_code == 401)

# --- Password change: wrong current password rejected, hash unchanged -----
# Fresh login for alice
r = client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
check("re-login alice -> 200", r.status_code == 200)

# Create a SECOND session for alice (separate client) to verify it gets
# invalidated on password change.
other = TestClient(app, base_url="https://testserver")
r = other.post("/api/auth/login", json={"username": "alice", "password": "password123"})
check("second session login -> 200", r.status_code == 200)
other_token = other.cookies.get("session_token")
r = other.get("/api/auth/me")
check("second session valid before change", r.status_code == 200)

# Wrong current password -> 401, and password must remain unchanged.
r = client.put("/api/auth/password", json={"current_password": "wrongpass1", "new_password": "NewPass1!xyz"})
check("password change wrong current -> 401", r.status_code == 401)
# old password still works
verify = TestClient(app, base_url="https://testserver")
r = verify.post("/api/auth/login", json={"username": "alice", "password": "password123"})
check("old password still valid after failed change", r.status_code == 200)

# Correct current password + valid new password -> 204
r = client.put("/api/auth/password", json={"current_password": "password123", "new_password": "NewPass1!xyz"})
check("password change success -> 204", r.status_code == 204)

# Current (request) session remains valid
r = client.get("/api/auth/me")
check("current session valid after change", r.status_code == 200)

# Other session must be invalidated
chk = TestClient(app, base_url="https://testserver")
chk.cookies.set("session_token", other_token)
r = chk.get("/api/auth/me")
check("other session invalidated after change", r.status_code == 401)

# New password works, old password fails
v2 = TestClient(app, base_url="https://testserver")
r = v2.post("/api/auth/login", json={"username": "alice", "password": "NewPass1!xyz"})
check("login with new password -> 200", r.status_code == 200)
v3 = TestClient(app, base_url="https://testserver")
r = v3.post("/api/auth/login", json={"username": "alice", "password": "password123"})
check("login with old password -> 401", r.status_code == 401)

# Invalid new-password policy -> 422 (validation) and hash unchanged
r = client.put("/api/auth/password", json={"current_password": "NewPass1!xyz", "new_password": "short"})
check("password change weak new password -> 422", r.status_code == 422)

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED:", failures)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
