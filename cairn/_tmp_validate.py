import tempfile
from pathlib import Path

from cairn.server import db, auth_db

# Point the global db at a fresh temp file and configure base + auth schema.
tmp = Path(tempfile.mkdtemp()) / "cairn_test.db"
db.configure(tmp)
auth_db.configure_auth_db()

# Idempotency: running again must not raise.
auth_db.configure_auth_db()

with db.get_conn() as conn:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    indexes = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }

required_tables = {"users", "sessions", "login_attempts"}
required_indexes = {
    "idx_sessions_user_id",
    "idx_sessions_expires_at",
    "idx_login_attempts_username_time",
}

missing_t = required_tables - tables
missing_i = required_indexes - indexes
assert not missing_t, f"missing tables: {missing_t}"
assert not missing_i, f"missing indexes: {missing_i}"

# Verify column definitions for users table.
with db.get_conn() as conn:
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    session_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    attempt_cols = {row[1] for row in conn.execute("PRAGMA table_info(login_attempts)").fetchall()}

assert {"id", "username", "username_lower", "password_hash", "created_at", "disabled"} <= user_cols, user_cols
assert {"token", "user_id", "created_at", "expires_at"} <= session_cols, session_cols
assert {"id", "username_lower", "attempted_at", "success"} <= attempt_cols, attempt_cols

print("OK: auth schema applied cleanly; tables and indexes present.")
