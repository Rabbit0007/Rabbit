from __future__ import annotations

from cairn.server import db

AUTH_SCHEMA = """\
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    username_lower TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username_lower TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_username_time
    ON login_attempts(username_lower, attempted_at);
"""


def configure_auth_db() -> None:
    """Run the authentication schema DDL on the existing SQLite connection.

    This is additive: it creates the ``users``, ``sessions``, and
    ``login_attempts`` tables (and supporting indexes) if they do not
    already exist. It must be called after :func:`cairn.server.db.configure`
    so that the database connection is initialized.
    """
    with db.get_conn() as conn:
        conn.executescript(AUTH_SCHEMA)
