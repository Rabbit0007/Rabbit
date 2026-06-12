from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

DEFAULT_DB = Path.home() / ".local" / "share" / "cairn" / "cairn.db"

_db_path: Path | None = None

SETTINGS_DEFAULTS: dict[str, int] = {
    "intent_timeout": 15,
    "reason_timeout": 15,
    "worker_unhealthy_retry_after_seconds": 5,
    "worker_rejected_retry_after_seconds": 5,
    "max_failed_login_attempts": 5,
    "rate_limit_window_minutes": 15,
    "session_duration_hours": 24,
    "log_retention_days": 30,
    "export_retention_days": 30,
    "notification_retention_days": 14,
    "project_idle_alert_hours": 12,
}

SETTINGS_ADDITIONAL_COLUMNS: dict[str, str] = {
    "worker_unhealthy_retry_after_seconds": "INTEGER NOT NULL DEFAULT 5",
    "worker_rejected_retry_after_seconds": "INTEGER NOT NULL DEFAULT 5",
    "max_failed_login_attempts": "INTEGER NOT NULL DEFAULT 5",
    "rate_limit_window_minutes": "INTEGER NOT NULL DEFAULT 15",
    "session_duration_hours": "INTEGER NOT NULL DEFAULT 24",
    "log_retention_days": "INTEGER NOT NULL DEFAULT 30",
    "export_retention_days": "INTEGER NOT NULL DEFAULT 30",
    "notification_retention_days": "INTEGER NOT NULL DEFAULT 14",
    "project_idle_alert_hours": "INTEGER NOT NULL DEFAULT 12",
}

SCHEMA = """\
CREATE TABLE IF NOT EXISTS settings (
    intent_timeout INTEGER NOT NULL DEFAULT 15,
    reason_timeout INTEGER NOT NULL DEFAULT 15
);

INSERT OR IGNORE INTO settings (rowid, intent_timeout, reason_timeout) VALUES (1, 15, 15);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    reason_worker TEXT,
    reason_trigger TEXT,
    reason_started_at TEXT,
    reason_last_heartbeat_at TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS intents (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    to_fact_id TEXT,
    description TEXT NOT NULL,
    creator TEXT NOT NULL,
    worker TEXT,
    last_heartbeat_at TEXT,
    created_at TEXT NOT NULL,
    concluded_at TEXT,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS intent_sources (
    intent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    PRIMARY KEY (intent_id, project_id, fact_id),
    FOREIGN KEY (intent_id, project_id) REFERENCES intents(id, project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS hints (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    creator TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO counters (name, value) VALUES ('project', 0);

CREATE TABLE IF NOT EXISTS scoped_counters (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, kind)
);
"""


def configure(path: Path) -> None:
    global _db_path
    if _db_path is not None:
        return
    _db_path = path
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _ensure_settings_columns(conn)


def _ensure_settings_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(settings)").fetchall()
    }
    for name, ddl in SETTINGS_ADDITIONAL_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE settings ADD COLUMN {name} {ddl}")


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    assert _db_path is not None
    conn = sqlite3.connect(str(_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
