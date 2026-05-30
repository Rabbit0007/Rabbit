"""Temporary verification for task 1.4 (login endpoint). Deleted after use."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cairn.server import auth_db, db
from cairn.server.routers import auth


def build_client() -> TestClient:
    tmp = Path(tempfile.mkdtemp()) / "cairn_test.db"
    db._db_path = None  # reset module-level guard so configure runs on temp db
    db.configure(tmp)
    auth_db.configure_auth_db()
    app = FastAPI()
    app.include_router(auth.router)
    # base_url with https so Secure cookies are retained by the test client
    return TestClient(app, base_url="https://testserver")


def main() -> None:
    client = build_client()

    # Register a user (reuses task 1.3 endpoint).
    r = client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
    assert r.status_code == 201, (r.status_code, r.text)
    print("register: OK", r.status_code)

    # 1. Valid login returns 200 and sets an HTTP-only Secure SameSite=Strict cookie.
    r = client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
    assert r.status_code == 200, (r.status_code, r.text)
    set_cookie = r.headers.get("set-cookie", "")
    assert "session_token=" in set_cookie, set_cookie
    assert "HttpOnly" in set_cookie, set_cookie
    assert "Secure" in set_cookie, set_cookie
    assert "SameSite=strict" in set_cookie.lower().replace("samesite=strict", "SameSite=strict") or "samesite=strict" in set_cookie.lower(), set_cookie
    assert "Max-Age=86400" in set_cookie, set_cookie  # 24h expiry
    print("valid login: OK -> 200, cookie:", set_cookie)

    # 2. Wrong password -> generic 401.
    r = client.post("/api/auth/login", json={"username": "alice", "password": "wrongpass"})
    assert r.status_code == 401, (r.status_code, r.text)
    assert r.json()["detail"] == "Invalid credentials", r.json()
    print("wrong password: OK -> 401 generic")

    # 2b. Unknown username -> same generic 401 (no distinction).
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever1"})
    assert r.status_code == 401, (r.status_code, r.text)
    assert r.json()["detail"] == "Invalid credentials", r.json()
    print("unknown user: OK -> 401 generic (same message)")

    # 3. Rate limiting: 5 failed attempts per username in window, 6th -> 429.
    client2 = client  # same db
    # 'alice' already has failures from above (1 wrong pw). Use fresh user 'bob'.
    r = client.post("/api/auth/register", json={"username": "bob", "password": "password123"})
    assert r.status_code == 201, r.text
    for i in range(5):
        r = client.post("/api/auth/login", json={"username": "bob", "password": "bad-pass"})
        assert r.status_code == 401, (i, r.status_code, r.text)
    # 6th attempt within window -> locked out (429), even with correct password.
    r = client.post("/api/auth/login", json={"username": "bob", "password": "password123"})
    assert r.status_code == 429, (r.status_code, r.text)
    print("rate limit: OK -> 429 after 5 failed attempts (even correct pw blocked)")

    print("\nALL TASK 1.4 CHECKS PASSED")


if __name__ == "__main__":
    main()
