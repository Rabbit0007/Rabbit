"""Verify tasks 1.4 (login + rate limiting) and 1.5 (logout/me/password).

Mounts ONLY the auth router on a standalone FastAPI app, since the auth router
is not wired into the main app until task 2.2. Uses an https base_url so the
Secure session cookie round-trips through the TestClient.
"""
import tempfile
from pathlib import Path

from cairn.server import db, auth_db

tmp = Path(tempfile.mkdtemp()) / "test.db"
db.configure(tmp)
auth_db.configure_auth_db()

from fastapi import FastAPI
from fastapi.testclient import TestClient
from cairn.server.routers import auth

app = FastAPI()
app.include_router(auth.router)

failures = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    if not cond:
        failures.append(name)


with TestClient(app, base_url="https://testserver") as client:
    # --- Register to get a session ---
    r = client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
    check("register 201", r.status_code == 201)
    user_id = r.json()["id"]

    # --- task 1.5: /me with valid session ---
    r = client.get("/api/auth/me")
    check("me 200 with session", r.status_code == 200 and r.json().get("username") == "alice")

    # --- /me without session ---
    anon = TestClient(app, base_url="https://testserver")
    check("me 401 without session", anon.get("/api/auth/me").status_code == 401)

    # --- task 1.4: login wrong password -> 401 ---
    bad = TestClient(app, base_url="https://testserver")
    r = bad.post("/api/auth/login", json={"username": "alice", "password": "wrongpass1"})
    check("login wrong pw 401", r.status_code == 401)

    # --- login correct -> 200 + cookie ---
    good = TestClient(app, base_url="https://testserver")
    r = good.post("/api/auth/login", json={"username": "alice", "password": "password123"})
    check("login correct 200", r.status_code == 200)
    check("login sets session cookie", "session_token" in good.cookies)

    # --- login unknown user -> 401 (generic) ---
    r = anon.post("/api/auth/login", json={"username": "nobody", "password": "whatever12"})
    check("login unknown user 401", r.status_code == 401)

    # --- rate limit: 5 failures then 429 ---
    rl = TestClient(app, base_url="https://testserver")
    client.post("/api/auth/register", json={"username": "ratelimit", "password": "password123"})
    codes = []
    for _ in range(6):
        codes.append(rl.post("/api/auth/login", json={"username": "ratelimit", "password": "wrongpass1"}).status_code)
    check("rate limit: first 5 are 401", codes[:5] == [401] * 5)
    check("rate limit: 6th is 429", codes[5] == 429)

    # --- task 1.5: password change wrong current -> 401 ---
    r = client.put("/api/auth/password", json={"current_password": "wrongpass1", "new_password": "NewPass1!aa"})
    check("pw change wrong current 401", r.status_code == 401)

    # --- weak new password -> 422 ---
    r = client.put("/api/auth/password", json={"current_password": "password123", "new_password": "short"})
    check("pw change weak new 422", r.status_code == 422)

    # --- successful change -> 204, other sessions invalidated ---
    from datetime import datetime, timedelta, timezone
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    now = datetime.now(timezone.utc)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            ("other_tok", user_id, now.strftime(fmt), (now + timedelta(hours=24)).strftime(fmt)),
        )
    r = client.put("/api/auth/password", json={"current_password": "password123", "new_password": "NewPass1!aa"})
    check("pw change success 204", r.status_code == 204)
    import bcrypt
    with db.get_conn() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
        toks = {s["token"] for s in conn.execute("SELECT token FROM sessions WHERE user_id=?", (user_id,)).fetchall()}
    check("new pw works", bcrypt.checkpw(b"NewPass1!aa", row["password_hash"].encode()))
    check("old pw rejected", not bcrypt.checkpw(b"password123", row["password_hash"].encode()))
    check("other session invalidated", "other_tok" not in toks)

    # --- logout -> 204 and session removed ---
    r = client.post("/api/auth/logout")
    check("logout 204", r.status_code == 204)
    with db.get_conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) c FROM sessions WHERE user_id=?", (user_id,)).fetchone()["c"]
    check("session removed after logout", cnt == 0)

print()
if failures:
    print("FAILURES:", failures)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
