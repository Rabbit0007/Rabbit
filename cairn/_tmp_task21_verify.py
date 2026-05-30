"""Temporary verification for task 2.1 auth middleware dependency."""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

from cairn.server import auth_db, db

# Configure a throwaway DB.
tmp = Path(tempfile.mkdtemp()) / "cairn_test.db"
db.configure(tmp)
auth_db.configure_auth_db()

from cairn.server.middleware.auth import (
    SESSION_COOKIE_NAME,
    require_auth,
    is_exempt,
)
from cairn.server.routers.auth import _format_timestamp, generate_session_token

FMT = "%Y-%m-%dT%H:%M:%SZ"


class FakeURL:
    def __init__(self, path):
        self.path = path


class FakeRequest:
    def __init__(self, path="/api/projects", cookies=None, accept=""):
        self.url = FakeURL(path)
        self.cookies = cookies or {}
        self.headers = {"accept": accept}

        class _State:
            pass

        self.state = _State()


class FakeResponse:
    """Minimal stand-in capturing set_cookie calls."""

    def __init__(self):
        self.cookies = {}

    def set_cookie(self, key, value, **kwargs):
        self.cookies[key] = (value, kwargs)


def seed_user(user_id="user_1", username="alice"):
    now = datetime.now(timezone.utc)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, username, username_lower, password_hash, created_at, disabled)"
            " VALUES (?, ?, ?, ?, ?, 0)",
            (user_id, username, username.lower(), "x", _format_timestamp(now)),
        )
    return user_id


def seed_session(user_id, token, expires_delta):
    now = datetime.now(timezone.utc)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, _format_timestamp(now), _format_timestamp(now + expires_delta)),
        )


results = []


def check(name, cond):
    results.append((name, cond))
    print(("PASS" if cond else "FAIL"), name)


# --- Exempt paths ---
check("exempt /api/auth/login", is_exempt("/api/auth/login"))
check("exempt /api/auth/register", is_exempt("/api/auth/register"))
check("exempt /static/foo.js", is_exempt("/static/foo.js"))
check("exempt root", is_exempt("/"))
check("not exempt /api/projects", not is_exempt("/api/projects"))

# --- Valid session: returns user, extends expiry, sets cookie ---
uid = seed_user()
token = generate_session_token()
seed_session(uid, token, timedelta(hours=1))
# capture original expiry
with db.get_conn() as conn:
    orig = conn.execute("SELECT expires_at FROM sessions WHERE token=?", (token,)).fetchone()["expires_at"]

req = FakeRequest(cookies={SESSION_COOKIE_NAME: token})
resp = FakeResponse()
row = require_auth(req, resp)
check("valid session returns user row", row is not None and row["id"] == uid)
check("valid session injects request.state.user", getattr(req.state, "user", {}).get("id") == uid)
check("valid session refreshes cookie", SESSION_COOKIE_NAME in resp.cookies)
ck = resp.cookies[SESSION_COOKIE_NAME][1]
check("cookie httponly", ck.get("httponly") is True)
check("cookie secure", ck.get("secure") is True)
check("cookie samesite strict", ck.get("samesite") == "strict")

with db.get_conn() as conn:
    new_exp = conn.execute("SELECT expires_at FROM sessions WHERE token=?", (token,)).fetchone()["expires_at"]
check("sliding window extends expiry", datetime.strptime(new_exp, FMT) > datetime.strptime(orig, FMT))

# --- Missing token -> 401 ---
try:
    require_auth(FakeRequest(cookies={}), FakeResponse())
    check("missing token raises", False)
except HTTPException as e:
    check("missing token -> 401", e.status_code == 401)
    check("missing token clears cookie", "set-cookie" in {k.lower(): v for k, v in e.headers.items()})

# --- Unknown token -> 401 ---
try:
    require_auth(FakeRequest(cookies={SESSION_COOKIE_NAME: "deadbeef"}), FakeResponse())
    check("unknown token raises", False)
except HTTPException as e:
    check("unknown token -> 401", e.status_code == 401)

# --- Expired token -> 401 and session removed ---
exp_token = generate_session_token()
seed_session(uid, exp_token, timedelta(hours=-1))
try:
    require_auth(FakeRequest(cookies={SESSION_COOKIE_NAME: exp_token}), FakeResponse())
    check("expired token raises", False)
except HTTPException as e:
    check("expired token -> 401", e.status_code == 401)
with db.get_conn() as conn:
    gone = conn.execute("SELECT 1 FROM sessions WHERE token=?", (exp_token,)).fetchone()
check("expired session row removed", gone is None)

# --- Browser navigation -> 302 redirect ---
try:
    require_auth(FakeRequest(path="/projects", cookies={}, accept="text/html"), FakeResponse())
    check("browser nav raises", False)
except HTTPException as e:
    hdrs = {k.lower(): v for k, v in e.headers.items()}
    check("browser nav -> 302", e.status_code == 302)
    check("browser nav location is login", hdrs.get("location") == "/")

# --- Exempt path returns without auth ---
out = require_auth(FakeRequest(path="/api/auth/login", cookies={}), FakeResponse())
check("exempt path bypasses auth", out is None)

print()
failed = [n for n, c in results if not c]
if failed:
    print("FAILURES:", failed)
    raise SystemExit(1)
print(f"ALL {len(results)} CHECKS PASSED")
