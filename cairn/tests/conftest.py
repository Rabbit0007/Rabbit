"""Shared pytest fixtures for the Cairn authentication tests.

These tests are additive: they exercise the existing auth router
(:mod:`cairn.server.routers.auth`) and auth middleware
(:mod:`cairn.server.middleware.auth`) without modifying any source code.

Key environment notes baked into the fixtures:

- The SQLite DB path is a module-global in :mod:`cairn.server.db`, and
  ``db.configure()`` is idempotent (it returns early once ``_db_path`` is set).
  The :func:`temp_db` fixture resets that global so every test gets a fresh,
  isolated database in a temp directory.
- The auth router sets a ``Secure`` cookie. For the FastAPI ``TestClient`` to
  store and resend that cookie, the client must talk to an ``https`` origin, so
  every client fixture uses ``base_url="https://testserver"``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cairn.server import auth_db, db, product_db

# The UTC timestamp format the auth tables store ``expires_at`` / ``created_at``
# in. Mirrors the format used by the source modules; redeclared here so the
# tests do not depend on private names.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

BASE_URL = "https://testserver"


def format_timestamp(value: datetime) -> str:
    """Format a datetime using the codebase-wide UTC timestamp convention."""
    return value.strftime(TIMESTAMP_FORMAT)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Provide a fresh, isolated SQLite database for a single test.

    ``db.configure()`` short-circuits when ``db._db_path`` is already set, so we
    reset that module-global to ``None`` before configuring with a per-test temp
    path. ``monkeypatch`` restores the original value after the test, keeping
    tests independent of one another and of any real configured database.
    """
    monkeypatch.setattr(db, "_db_path", None)
    db_path = tmp_path / "cairn_test.db"
    db.configure(db_path)
    auth_db.configure_auth_db()
    product_db.configure_product_db()
    return db_path


@pytest.fixture
def auth_app(temp_db) -> FastAPI:
    """A minimal FastAPI app that mounts only the auth router.

    This is sufficient for the task 1.6 auth-router tests: the
    register/login/logout/me/password endpoints all resolve the session from the
    cookie directly and do not depend on the auth middleware.
    """
    from cairn.server.routers import auth

    app = FastAPI()
    app.include_router(auth.router)
    return app


@pytest.fixture
def client(auth_app) -> TestClient:
    """TestClient for the auth router over an https origin (Secure cookies)."""
    return TestClient(auth_app, base_url=BASE_URL)


@pytest.fixture
def new_auth_client(auth_app):
    """Factory returning fresh TestClients (independent cookie jars) for the app.

    Useful when a test needs to model two distinct browser sessions for the same
    backing database (e.g. verifying a password change invalidates other
    sessions).
    """

    def _make() -> TestClient:
        return TestClient(auth_app, base_url=BASE_URL)

    return _make
