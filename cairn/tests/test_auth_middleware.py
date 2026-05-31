"""Unit tests for the auth middleware dependency (spec task 2.3).

Target: ``cairn.server.middleware.auth.require_auth`` as wired into the real
application (:mod:`cairn.server.app`).

Covers requirements 5.1-5.4 plus the dual-auth / dispatcher-safe behaviour:

- ``CAIRN_INTERNAL_TOKEN`` unset  -> protected routers stay open (non-401).
- ``CAIRN_INTERNAL_TOKEN`` set     -> 401 without auth; 200 with a valid session
  cookie or the matching ``X-Cairn-Internal-Token`` header; 401 with a wrong
  token.
- Exempt paths (``/``, ``/api/auth/login``, ``/api/auth/register``, ``/static/*``)
  bypass auth.
- Browser navigation (``Accept: text/html``) to a protected path while
  unauthenticated -> 302 redirect to the login page.
- Expired session -> 401 and the dead session row is removed.

The real app's lifespan calls ``db.configure(db.DEFAULT_DB)``; because
``db.configure`` is idempotent, configuring a temp DB *first* (via the
``temp_db`` fixture) makes the lifespan call a no-op so tests never touch a real
database.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from cairn.server import db
from cairn.server.middleware.auth import (
    INTERNAL_TOKEN_ENV,
    INTERNAL_TOKEN_HEADER,
    SESSION_COOKIE_NAME,
)

from .conftest import BASE_URL, format_timestamp

PROTECTED_PATH = "/projects"  # GET /projects is behind require_auth
VALID_USERNAME = "alice"
VALID_PASSWORD = "password123"


@pytest.fixture
def app_client(temp_db, monkeypatch):
    """TestClient against the real app, backed by the per-test temp DB.

    Importing :mod:`cairn.server.app` builds the FastAPI app with every router
    wired in. The ``temp_db`` fixture has already configured the DB, so the
    app's lifespan ``db.configure(DEFAULT_DB)`` is a no-op and the temp DB is
    used throughout. https origin keeps the Secure cookie working.
    """
    # Default: ensure the internal token is unset unless a test sets it.
    monkeypatch.delenv(INTERNAL_TOKEN_ENV, raising=False)

    from cairn.server.app import app

    with TestClient(app, base_url=BASE_URL) as client:
        yield client


def _register(client):
    """Register a user and return the resulting (authenticated) client."""
    resp = client.post(
        "/api/auth/register",
        json={
            "username": VALID_USERNAME,
            "password": VALID_PASSWORD,
            **_captcha_payload(client),
        },
    )
    assert resp.status_code == 201
    return resp


def _captcha_payload(client):
    resp = client.get("/api/auth/captcha")
    assert resp.status_code == 200
    body = resp.json()
    nums = [int(value) for value in re.findall(r"\d+", body["question"])]
    assert len(nums) == 2
    return {"captcha_id": body["captcha_id"], "captcha_answer": str(sum(nums))}


# ---------------------------------------------------------------------------
# Dispatcher-safe default: CAIRN_INTERNAL_TOKEN unset -> routers stay open
# ---------------------------------------------------------------------------


def test_internal_token_unset_protected_route_is_open(app_client, monkeypatch):
    monkeypatch.delenv(INTERNAL_TOKEN_ENV, raising=False)
    # No auth at all, yet the protected route must not 401 (open default).
    response = app_client.get(PROTECTED_PATH)
    assert response.status_code != 401
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Enforcement: CAIRN_INTERNAL_TOKEN set
# ---------------------------------------------------------------------------


def test_no_auth_returns_401_when_token_configured(app_client, monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "secret-internal-token")
    # Fresh client = no session cookie, no header.
    fresh = TestClient(app_client.app, base_url=BASE_URL)
    response = fresh.get(PROTECTED_PATH)
    assert response.status_code == 401
    assert "authentication" in response.json()["detail"].lower()


def test_valid_session_cookie_returns_200_when_token_configured(
    app_client, monkeypatch
):
    # Register/login while the token is configured so the cookie is established.
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "secret-internal-token")
    _register(app_client)  # registration sets the session cookie on app_client
    response = app_client.get(PROTECTED_PATH)
    assert response.status_code == 200


def test_valid_internal_token_header_returns_200(app_client, monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "secret-internal-token")
    fresh = TestClient(app_client.app, base_url=BASE_URL)
    response = fresh.get(
        PROTECTED_PATH,
        headers={INTERNAL_TOKEN_HEADER: "secret-internal-token"},
    )
    assert response.status_code == 200


def test_wrong_internal_token_header_returns_401(app_client, monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "secret-internal-token")
    fresh = TestClient(app_client.app, base_url=BASE_URL)
    response = fresh.get(
        PROTECTED_PATH,
        headers={INTERNAL_TOKEN_HEADER: "wrong-token"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Exempt paths bypass auth (requirement 5.2)
# ---------------------------------------------------------------------------


def test_exempt_paths_bypass_auth(app_client, monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "secret-internal-token")
    fresh = TestClient(app_client.app, base_url=BASE_URL)

    # Root serves the SPA/login shell -> exempt.
    assert fresh.get("/").status_code == 200
    # Static assets mount -> exempt.
    assert fresh.get("/static/index.html").status_code == 200
    # Login endpoint -> exempt (reachable without auth; 401 here is the auth
    # router's own generic credential error, not the middleware's gate).
    login_resp = fresh.post(
        "/api/auth/login",
        json={
            "username": "nobody",
            "password": "whatever12",
            **_captcha_payload(fresh),
        },
    )
    assert login_resp.status_code != 302  # not redirected by the middleware
    # Register endpoint -> exempt and fully usable without auth.
    reg_resp = fresh.post(
        "/api/auth/register",
        json={
            "username": "exempt_user",
            "password": "password123",
            **_captcha_payload(fresh),
        },
    )
    assert reg_resp.status_code == 201


# ---------------------------------------------------------------------------
# Browser navigation gets a 302 redirect (requirement 5.3)
# ---------------------------------------------------------------------------


def test_browser_navigation_unauthenticated_redirects_to_login(
    app_client, monkeypatch
):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "secret-internal-token")
    fresh = TestClient(app_client.app, base_url=BASE_URL)
    response = fresh.get(
        PROTECTED_PATH,
        headers={"accept": "text/html"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/"


# ---------------------------------------------------------------------------
# Expired session -> 401 and the session row is removed (requirements 5.4, 3.2)
# ---------------------------------------------------------------------------


def test_expired_session_returns_401_and_removes_session_row(app_client, monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "secret-internal-token")
    _register(app_client)
    token = app_client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None

    # Force expiry in the DB.
    past = format_timestamp(datetime.now(timezone.utc) - timedelta(hours=1))
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?", (past, token)
        )

    response = app_client.get(PROTECTED_PATH)
    assert response.status_code == 401

    # The dead session row must be deleted by the middleware (requirement 3.2).
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM sessions WHERE token = ?", (token,)
        ).fetchone()
    assert row["c"] == 0


def test_valid_session_extends_expiration_sliding_window(app_client, monkeypatch):
    monkeypatch.setenv(INTERNAL_TOKEN_ENV, "secret-internal-token")
    _register(app_client)
    token = app_client.cookies.get(SESSION_COOKIE_NAME)

    # Pull the expiry back to near-now so the sliding extension is observable.
    soon = format_timestamp(datetime.now(timezone.utc) + timedelta(minutes=1))
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?", (soon, token)
        )

    assert app_client.get(PROTECTED_PATH).status_code == 200

    # After an authenticated request, expiry is pushed out well beyond 1 minute.
    with db.get_conn() as conn:
        new_expires = conn.execute(
            "SELECT expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()["expires_at"]
    extended = datetime.strptime(new_expires, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    assert extended > datetime.now(timezone.utc) + timedelta(hours=1)
