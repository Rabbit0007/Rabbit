"""Verification harness for task 1.4 (login endpoint).

Points cairn.server.db at a temp SQLite file BEFORE the app lifespan runs so the
real user DB is untouched (db.configure is guarded against re-configuration).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from cairn.server import db

# Configure db at a throwaway path first. The app lifespan's db.configure(DEFAULT_DB)
# becomes a no-op because _db_path is already set.
_tmp = Path(tempfile.mkdtemp()) / "verify.db"
db.configure(_tmp)

from fastapi.testclient import TestClient  # noqa: E402

from cairn.server.app import app  # noqa: E402

USERNAME = "Alice_01"
PASSWORD = "correct horse battery"

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}{(' -> ' + detail) if detail else ''}")
    if not cond:
        failures.append(label)


with TestClient(app) as client:
    # Seed a user via the registration endpoint (task 1.3).
    reg = client.post("/api/auth/register", json={"username": USERNAME, "password": PASSWORD})
    check("register seed user returns 201", reg.status_code == 201, f"status={reg.status_code}")

    # 1) Valid login -> 200 + session cookie (req 2.1)
    ok = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    set_cookie = ok.headers.get("set-cookie", "")
    check("valid login returns 200", ok.status_code == 200, f"status={ok.status_code}")
    check("valid login body echoes username", ok.json().get("username") == USERNAME, ok.text)
    check("valid login sets session_token cookie", "session_token=" in set_cookie)
    check("cookie is HttpOnly", "httponly" in set_cookie.lower(), set_cookie)
    check("cookie is Secure", "secure" in set_cookie.lower(), set_cookie)
    check("cookie is SameSite=Strict", "samesite=strict" in set_cookie.lower(), set_cookie)
    # token entropy: 64 hex chars (256 bits) >= 128 bits required
    import re
    m = re.search(r"session_token=([0-9a-f]+)", set_cookie)
    check("session token has >=32 hex chars (>=128 bits)", bool(m) and len(m.group(1)) >= 32,
          f"len={len(m.group(1)) if m else 0}")
    # 24h expiry: Max-Age ~= 86400
    check("cookie Max-Age is 24h (86400)", "max-age=86400" in set_cookie.lower(), set_cookie)

    # 2) Wrong password -> 401 generic (req 2.2)
    bad_pw = client.post("/api/auth/login", json={"username": USERNAME, "password": "wrong-password"})
    check("wrong password returns 401", bad_pw.status_code == 401, f"status={bad_pw.status_code}")
    wrong_pw_detail = bad_pw.json().get("detail")

    # 3) Unknown username -> 401 generic, identical to wrong-password (req 2.2)
    unknown = client.post("/api/auth/login", json={"username": "nobody_here", "password": "whatever123"})
    check("unknown username returns 401", unknown.status_code == 401, f"status={unknown.status_code}")
    check("unknown username detail == wrong password detail (generic)",
          unknown.json().get("detail") == wrong_pw_detail,
          f"unknown={unknown.json().get('detail')!r} wrongpw={wrong_pw_detail!r}")

    # 4) Rate limiting (req 2.3, 2.4): use a fresh username so prior failures don't count.
    rl_user = "RateLimited_99"
    client.post("/api/auth/register", json={"username": rl_user, "password": PASSWORD})
    statuses = []
    for i in range(6):
        r = client.post("/api/auth/login", json={"username": rl_user, "password": "bad-attempt"})
        statuses.append(r.status_code)
    # First 5 should be 401 (invalid creds), 6th should be 429 (locked)
    check("first 5 failed attempts return 401", statuses[:5] == [401] * 5, f"statuses={statuses}")
    check("6th attempt returns 429 (rate limited)", statuses[5] == 429, f"statuses={statuses}")

    # 4b) Even a correct password is blocked once rate limited (req 2.4)
    blocked = client.post("/api/auth/login", json={"username": rl_user, "password": PASSWORD})
    check("correct password blocked while rate limited", blocked.status_code == 429,
          f"status={blocked.status_code}")

print()
if failures:
    print(f"RESULT: FAILED ({len(failures)} check(s) failed): {failures}")
    raise SystemExit(1)
print("RESULT: ALL CHECKS PASSED")
