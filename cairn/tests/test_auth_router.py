"""Unit tests for the authentication router (spec task 1.6).

Target: ``cairn.server.routers.auth`` mounted on a minimal FastAPI app.

Covers requirements 1.1-1.7 (registration), 2.1-2.5 (login + rate limiting),
3.3 (logout / me), and 4.1-4.4 (password change). Tests are example-based and
talk to the router through the FastAPI ``TestClient`` over an https origin so
the Secure session cookie round-trips.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

import pytest
from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.routers.auth import SESSION_COOKIE_NAME, SESSION_DURATION

from .conftest import TIMESTAMP_FORMAT, format_timestamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_USERNAME = "alice"
VALID_PASSWORD = "password123"


def captcha_payload(client):
    response = client.get("/api/auth/captcha")
    assert response.status_code == 200
    body = response.json()
    nums = [int(value) for value in re.findall(r"\d+", body["question"])]
    assert len(nums) == 2
    return {
        "captcha_id": body["captcha_id"],
        "captcha_answer": str(sum(nums)),
    }


def register(client, username=VALID_USERNAME, password=VALID_PASSWORD):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": password, **captcha_payload(client)},
    )


def login(client, username=VALID_USERNAME, password=VALID_PASSWORD):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password, **captcha_payload(client)},
    )


def _set_cookie_headers(response):
    """Return the list of raw Set-Cookie header values on a response."""
    # httpx exposes multiple Set-Cookie headers via get_list / multi_items.
    return response.headers.get_list("set-cookie")


def _session_set_cookie(response):
    """Return the raw Set-Cookie header for the session cookie, or None."""
    for header in _set_cookie_headers(response):
        if header.startswith(f"{SESSION_COOKIE_NAME}="):
            return header
    return None


# ---------------------------------------------------------------------------
# Registration (requirements 1.1-1.7)
# ---------------------------------------------------------------------------


def test_register_valid_returns_201_user_and_sets_secure_cookie(client):
    response = register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == VALID_USERNAME
    assert body["id"].startswith("user_")
    assert "created_at" in body

    # A session cookie should be set with the security attributes from req 3.4.
    raw_cookie = _session_set_cookie(response)
    assert raw_cookie is not None
    lowered = raw_cookie.lower()
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=strict" in lowered

    # The cookie is retained by the client jar (https origin) for future calls.
    assert SESSION_COOKIE_NAME in client.cookies


def test_local_http_register_keeps_session_cookie(auth_app):
    local_client = TestClient(auth_app, base_url="http://127.0.0.1")

    response = register(local_client)

    assert response.status_code == 201
    raw_cookie = _session_set_cookie(response)
    assert raw_cookie is not None
    lowered = raw_cookie.lower()
    assert "httponly" in lowered
    assert "secure" not in lowered
    assert "samesite=strict" in lowered

    assert SESSION_COOKIE_NAME in local_client.cookies
    assert local_client.get("/api/auth/me").status_code == 200


def test_register_persists_bcrypt_hash_cost_at_least_12(client):
    register(client)

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username_lower = ?",
            (VALID_USERNAME,),
        ).fetchone()

    assert row is not None
    stored_hash = row["password_hash"]
    # bcrypt hashes look like: $2b$<cost>$<22-char-salt><31-char-digest>
    assert stored_hash.startswith("$2")
    parts = stored_hash.split("$")
    cost = int(parts[2])
    assert cost >= 12

    # The stored hash verifies the original password.
    import bcrypt

    assert bcrypt.checkpw(VALID_PASSWORD.encode(), stored_hash.encode())


def test_register_duplicate_username_case_insensitive_returns_409(client):
    assert register(client, username="Alice").status_code == 201
    # Different casing must still collide (requirements 1.2, 1.5).
    dup = register(client, username="alice", password="anotherpass1")
    assert dup.status_code == 409


def test_register_password_too_short_returns_422(client):
    response = register(client, password="short")  # 5 chars < 8
    assert response.status_code == 422


def test_register_invalid_username_format_returns_422(client):
    response = register(client, username="bad name!")  # space + '!'
    assert response.status_code == 422


def test_register_username_too_short_returns_422(client):
    response = register(client, username="ab")  # 2 chars < 3
    assert response.status_code == 422


def test_register_missing_fields_returns_422(client):
    assert client.post("/api/auth/register", json={}).status_code == 422
    assert (
        client.post("/api/auth/register", json={"username": "bob"}).status_code == 422
    )
    assert (
        client.post(
            "/api/auth/register", json={"password": "password123"}
        ).status_code
        == 422
    )


def test_register_empty_username_or_password_returns_422(client):
    assert register(client, username="", password=VALID_PASSWORD).status_code == 422
    assert register(client, username=VALID_USERNAME, password="").status_code == 422


def test_register_requires_valid_captcha(client):
    response = client.post(
        "/api/auth/register",
        json={"username": "bob", "password": VALID_PASSWORD},
    )
    assert response.status_code == 400
    assert "验证码" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Login (requirements 2.1-2.5)
# ---------------------------------------------------------------------------


def test_login_valid_credentials_creates_session_and_cookie(client, new_auth_client):
    register(client)

    # Use a fresh client (empty cookie jar) to confirm login itself sets a cookie.
    fresh = new_auth_client()
    response = login(fresh)

    assert response.status_code == 200
    assert response.json()["username"] == VALID_USERNAME

    raw_cookie = _session_set_cookie(response)
    assert raw_cookie is not None
    lowered = raw_cookie.lower()
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=strict" in lowered

    # The session row exists with a ~24h expiry (requirements 2.1, 3.1).
    token = fresh.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    with db.get_conn() as conn:
        srow = conn.execute(
            "SELECT created_at, expires_at FROM sessions WHERE token = ?",
            (token,),
        ).fetchone()
    assert srow is not None
    created = datetime.strptime(srow["created_at"], TIMESTAMP_FORMAT)
    expires = datetime.strptime(srow["expires_at"], TIMESTAMP_FORMAT)
    assert expires - created == SESSION_DURATION


def test_login_session_token_has_at_least_128_bits_entropy(client):
    register(client)
    token = client.cookies.get(SESSION_COOKIE_NAME)
    # token_hex(32) -> 64 hex chars == 256 bits, well over the 128-bit minimum.
    assert token is not None
    assert len(token) >= 32  # >= 128 bits even if implementation changed to hex(16)


def test_login_wrong_password_and_unknown_user_return_same_generic_error(
    client, new_auth_client
):
    register(client)

    wrong_pw = login(new_auth_client(), password="wrongpassword")
    unknown = login(new_auth_client(), username="ghost", password="whatever12")

    assert wrong_pw.status_code == 401
    assert unknown.status_code == 401
    # No distinction between the two failure modes (requirement 2.2).
    assert wrong_pw.json()["detail"] == unknown.json()["detail"]
    # And no session cookie is established on failure.
    assert _session_set_cookie(wrong_pw) is None
    assert _session_set_cookie(unknown) is None


def test_login_rate_limit_blocks_sixth_failed_attempt(client, new_auth_client):
    register(client)

    # 5 failed attempts are permitted (each returns 401).
    for _ in range(5):
        resp = login(new_auth_client(), password="wrongpassword")
        assert resp.status_code == 401

    # The 6th attempt within the window is blocked (requirements 2.3, 2.4).
    blocked = login(new_auth_client(), password="wrongpassword")
    assert blocked.status_code == 429

    # Even a correct password is blocked while the window is saturated.
    blocked_valid = login(new_auth_client())
    assert blocked_valid.status_code == 429


def test_login_rate_limit_window_is_per_username(client, new_auth_client):
    register(client, username="alice")
    register(client, username="bob", password="bobpassword1")

    for _ in range(5):
        assert login(new_auth_client(), username="alice", password="wrong").status_code == 401

    # alice is locked, but bob is unaffected (requirement 2.3 is per-username).
    assert login(new_auth_client(), username="alice").status_code == 429
    assert (
        login(new_auth_client(), username="bob", password="bobpassword1").status_code
        == 200
    )


def test_login_disabled_account_returns_generic_error(client, new_auth_client):
    register(client)
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE users SET disabled = 1 WHERE username_lower = ?", (VALID_USERNAME,)
        )

    response = login(new_auth_client())
    # Disabled accounts get the same generic 401 (requirement 2.5).
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


# ---------------------------------------------------------------------------
# Logout (requirement 3.3)
# ---------------------------------------------------------------------------


def test_logout_invalidates_session_and_subsequent_authed_call_fails(client):
    register(client)
    # Authenticated call works before logout.
    assert client.get("/api/auth/me").status_code == 200

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 204

    # The session row is gone.
    with db.get_conn() as conn:
        remaining = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
    assert remaining == 0

    # A subsequent authenticated call fails (cookie cleared / session invalid).
    me_after = client.get("/api/auth/me")
    assert me_after.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/auth/me (requirement 3.3)
# ---------------------------------------------------------------------------


def test_me_returns_current_user_with_valid_session(client):
    reg = register(client)
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == VALID_USERNAME
    assert me.json()["id"] == reg.json()["id"]


def test_me_without_session_returns_401(new_auth_client):
    response = new_auth_client().get("/api/auth/me")
    assert response.status_code == 401


def test_me_with_expired_session_returns_401(client):
    register(client)
    token = client.cookies.get(SESSION_COOKIE_NAME)
    # Force the session to be expired in the DB.
    past = format_timestamp(datetime.now(timezone.utc) - timedelta(hours=1))
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?", (past, token)
        )

    assert client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/auth/password (requirements 4.1-4.4)
# ---------------------------------------------------------------------------

VALID_NEW_PASSWORD = "NewPass1!"  # upper, lower, digit, special, >= 8 chars


def change_password(client, current, new):
    return client.put(
        "/api/auth/password",
        json={"current_password": current, "new_password": new},
    )


def test_password_change_updates_hash_with_correct_current_password(client):
    register(client)
    with db.get_conn() as conn:
        old_hash = conn.execute(
            "SELECT password_hash FROM users WHERE username_lower = ?",
            (VALID_USERNAME,),
        ).fetchone()["password_hash"]

    response = change_password(client, VALID_PASSWORD, VALID_NEW_PASSWORD)
    assert response.status_code == 204

    with db.get_conn() as conn:
        new_hash = conn.execute(
            "SELECT password_hash FROM users WHERE username_lower = ?",
            (VALID_USERNAME,),
        ).fetchone()["password_hash"]
    assert new_hash != old_hash

    import bcrypt

    assert bcrypt.checkpw(VALID_NEW_PASSWORD.encode(), new_hash.encode())


def test_password_change_with_wrong_current_password_is_rejected_and_no_change(client):
    register(client)
    with db.get_conn() as conn:
        old_hash = conn.execute(
            "SELECT password_hash FROM users WHERE username_lower = ?",
            (VALID_USERNAME,),
        ).fetchone()["password_hash"]

    response = change_password(client, "totallywrong", VALID_NEW_PASSWORD)
    assert response.status_code == 401

    with db.get_conn() as conn:
        unchanged = conn.execute(
            "SELECT password_hash FROM users WHERE username_lower = ?",
            (VALID_USERNAME,),
        ).fetchone()["password_hash"]
    # Requirement 4.2: stored hash must not be modified on failure.
    assert unchanged == old_hash


def test_password_change_rejects_non_policy_compliant_new_password(client):
    register(client)
    # Missing uppercase/digit/special -> policy violation -> 422 (requirement 4.4).
    response = change_password(client, VALID_PASSWORD, "alllowercase")
    assert response.status_code == 422


def test_password_change_invalidates_other_sessions(client, new_auth_client):
    register(client)

    # Establish a second independent session for the same user.
    other = new_auth_client()
    assert login(other).status_code == 200
    assert other.get("/api/auth/me").status_code == 200

    # Change password on the first session.
    assert change_password(client, VALID_PASSWORD, VALID_NEW_PASSWORD).status_code == 204

    # Requirement 4.3: other sessions are invalidated...
    assert other.get("/api/auth/me").status_code == 401
    # ...while the session that performed the change remains valid.
    assert client.get("/api/auth/me").status_code == 200
