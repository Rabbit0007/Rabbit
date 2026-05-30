"""Temporary verification for task 1.4 login endpoint."""
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cairn.server import db, auth_db
from cairn.server.routers import auth


def main():
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    db.configure(db_path)
    auth_db.configure_auth_db()

    app = FastAPI()
    app.include_router(auth.router)
    client = TestClient(app)

    # Register a user so we have valid credentials.
    reg = client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
    assert reg.status_code == 201, (reg.status_code, reg.text)
    print("register:", reg.status_code, reg.json())

    # 1. Valid login returns a session cookie.
    ok = client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
    assert ok.status_code == 200, (ok.status_code, ok.text)
    set_cookie = ok.headers.get("set-cookie", "")
    assert "session_token=" in set_cookie, set_cookie
    assert "HttpOnly" in set_cookie, set_cookie
    assert "Secure" in set_cookie, set_cookie
    assert "SameSite=strict" in set_cookie.lower() or "samesite=strict" in set_cookie.lower(), set_cookie
    print("valid login:", ok.status_code, ok.json())
    print("  set-cookie:", set_cookie)

    # Case-insensitive username login works too.
    ok2 = client.post("/api/auth/login", json={"username": "ALICE", "password": "password123"})
    assert ok2.status_code == 200, (ok2.status_code, ok2.text)
    print("case-insensitive login:", ok2.status_code)

    # 2. Invalid password -> generic 401.
    bad_pw = client.post("/api/auth/login", json={"username": "alice", "password": "wrongpass1"})
    assert bad_pw.status_code == 401, (bad_pw.status_code, bad_pw.text)
    # 2. Unknown username -> SAME generic 401 + same detail.
    bad_user = client.post("/api/auth/login", json={"username": "nobody", "password": "wrongpass1"})
    assert bad_user.status_code == 401, (bad_user.status_code, bad_user.text)
    assert bad_pw.json() == bad_user.json(), (bad_pw.json(), bad_user.json())
    print("generic error (wrong pw == unknown user):", bad_pw.json())

    # 3. Rate limiting: bob gets 5 failed attempts then a 429 lockout.
    client.post("/api/auth/register", json={"username": "bob", "password": "password123"})
    statuses = []
    for _ in range(5):
        r = client.post("/api/auth/login", json={"username": "bob", "password": "wrongpass1"})
        statuses.append(r.status_code)
    # 6th attempt should be locked out even though we now use the CORRECT password.
    locked = client.post("/api/auth/login", json={"username": "bob", "password": "password123"})
    statuses.append(locked.status_code)
    print("rate limit statuses (5x wrong, then 1 correct):", statuses)
    assert statuses[:5] == [401] * 5, statuses
    assert statuses[5] == 429, statuses
    assert "Too many attempts" in locked.json()["detail"], locked.json()

    # Empty fields -> 422 validation error.
    empty = client.post("/api/auth/login", json={"username": "", "password": ""})
    assert empty.status_code == 422, (empty.status_code, empty.text)
    print("empty fields -> 422 OK")

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
