from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

DEFAULT_DB = Path.home() / ".local" / "share" / "cairn" / "cairn.db"

_db_path: Path | None = None

SETTINGS_DEFAULTS: dict[str, int] = {
    "step_timeout": 15,
    "decide_timeout": 15,
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
    "step_timeout": "INTEGER NOT NULL DEFAULT 15",
    "decide_timeout": "INTEGER NOT NULL DEFAULT 15",
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
    bootstrap_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    decide_worker TEXT,
    decide_trigger TEXT,
    decide_started_at TEXT,
    decide_last_heartbeat_at TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS goals (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    parent_goal_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    completion_description TEXT,
    completed_by TEXT,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS goal_sources (
    goal_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    PRIMARY KEY (goal_id, project_id, fact_id),
    FOREIGN KEY (goal_id, project_id) REFERENCES goals(id, project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS steps (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    to_fact_id TEXT,
    description TEXT NOT NULL,
    goal_id TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    creator TEXT NOT NULL,
    worker TEXT,
    last_heartbeat_at TEXT,
    created_at TEXT NOT NULL,
    concluded_at TEXT,
    abandoned INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id, project_id)
);

CREATE TABLE IF NOT EXISTS step_sources (
    step_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    PRIMARY KEY (step_id, project_id, fact_id),
    FOREIGN KEY (step_id, project_id) REFERENCES steps(id, project_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    kind TEXT NOT NULL DEFAULT 'finding',
    data_json TEXT NOT NULL DEFAULT '{}',
    fact_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, project_id)
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
        _ensure_project_columns(conn)
        _ensure_goal_columns(conn)
        _ensure_finding_columns(conn)
        _migrate_intents_to_steps(conn)
        _migrate_legacy_goal_facts(conn)
        _migrate_completion_steps(conn)
        _drop_legacy_intent_tables(conn)


def _ensure_settings_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
    for name, ddl in SETTINGS_ADDITIONAL_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE settings ADD COLUMN {name} {ddl}")

    # Preserve customized legacy values on the first Cairn-Y migration.  The
    # old columns remain only so an older binary can still open a backup.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
    if "intent_timeout" in columns:
        conn.execute(
            "UPDATE settings SET step_timeout = intent_timeout "
            "WHERE step_timeout = 15 AND intent_timeout != 15"
        )
    if "reason_timeout" in columns:
        conn.execute(
            "UPDATE settings SET decide_timeout = reason_timeout "
            "WHERE decide_timeout = 15 AND reason_timeout != 15"
        )


def _ensure_project_columns(conn: sqlite3.Connection) -> None:
    """Add missing columns from older schema versions."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}

    # Add bootstrap_enabled if missing
    if "bootstrap_enabled" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN bootstrap_enabled INTEGER NOT NULL DEFAULT 1")
        if "bootstrap_mode" in columns:
            conn.execute(
                "UPDATE projects SET bootstrap_enabled = CASE WHEN bootstrap_mode = 'disabled' THEN 0 ELSE 1 END"
            )

    # Migrate reason_* columns to decide_* if they exist
    decide_columns = {
        "decide_worker": "TEXT",
        "decide_trigger": "TEXT",
        "decide_started_at": "TEXT",
        "decide_last_heartbeat_at": "TEXT",
    }
    for name, sql_type in decide_columns.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {sql_type}")
    if "reason_worker" in columns:
        conn.execute(
            "UPDATE projects SET "
            "decide_worker = COALESCE(decide_worker, reason_worker), "
            "decide_trigger = COALESCE(decide_trigger, reason_trigger), "
            "decide_started_at = COALESCE(decide_started_at, reason_started_at), "
            "decide_last_heartbeat_at = COALESCE(decide_last_heartbeat_at, reason_last_heartbeat_at)"
        )


def _ensure_goal_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(goals)")}
    if "completion_description" not in columns:
        conn.execute("ALTER TABLE goals ADD COLUMN completion_description TEXT")
    if "completed_by" not in columns:
        conn.execute("ALTER TABLE goals ADD COLUMN completed_by TEXT")


def _ensure_finding_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(findings)")}
    if "kind" not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN kind TEXT NOT NULL DEFAULT 'finding'")
    if "data_json" not in columns:
        conn.execute("ALTER TABLE findings ADD COLUMN data_json TEXT NOT NULL DEFAULT '{}'")


def _migrate_intents_to_steps(conn: sqlite3.Connection) -> None:
    """Migrate the legacy Intent tables after the new schema is created.

    ``SCHEMA`` creates ``steps`` before this function runs, so checking only
    whether the table exists would make every legacy migration a no-op.  An
    empty steps table is the state produced by ``SCHEMA`` on a legacy database;
    a non-empty one means the migration has already been applied.
    """
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "intents" not in tables:
        return

    conn.execute("""\
        CREATE TABLE IF NOT EXISTS steps (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            to_fact_id TEXT,
            description TEXT NOT NULL,
            goal_id TEXT,
            priority INTEGER NOT NULL DEFAULT 0,
            creator TEXT NOT NULL,
            worker TEXT,
            last_heartbeat_at TEXT,
            created_at TEXT NOT NULL,
            concluded_at TEXT,
            abandoned INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (id, project_id)
        )
    """)
    conn.execute("""\
        CREATE TABLE IF NOT EXISTS step_sources (
            step_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            fact_id TEXT NOT NULL,
            PRIMARY KEY (step_id, project_id, fact_id),
            FOREIGN KEY (step_id, project_id) REFERENCES steps(id, project_id) ON DELETE CASCADE
        )
    """)

    # Copy data.  ``INSERT OR IGNORE`` makes the migration idempotent and also
    # repairs a partially migrated database instead of treating any existing
    # row as proof that all legacy rows were copied.
    conn.execute("""\
        INSERT OR IGNORE INTO steps (id, project_id, to_fact_id, description, goal_id, priority,
                                     creator, worker, last_heartbeat_at, created_at, concluded_at, abandoned)
        SELECT i.id, i.project_id, i.to_fact_id, i.description, NULL, 0,
               i.creator, i.worker, i.last_heartbeat_at, i.created_at, i.concluded_at, 0
        FROM intents i
        JOIN projects p ON p.id = i.project_id
        WHERE i.to_fact_id IS NULL OR i.to_fact_id != 'goal'
    """)
    if "intent_sources" in tables:
        conn.execute("""\
            INSERT OR IGNORE INTO step_sources (step_id, project_id, fact_id)
            SELECT src.intent_id, src.project_id, src.fact_id
            FROM intent_sources src
            JOIN steps s ON s.id = src.intent_id AND s.project_id = src.project_id
            JOIN intents i ON i.id = src.intent_id AND i.project_id = src.project_id
            WHERE i.to_fact_id IS NULL OR i.to_fact_id != 'goal'
        """)


def _migrate_legacy_goal_facts(conn: sqlite3.Connection) -> None:
    """Turn Cairn's special ``goal`` Fact and completion Intent into FGS Goal state."""
    legacy_goals = conn.execute(
        "SELECT f.project_id, f.description, p.status, p.created_at "
        "FROM facts f JOIN projects p ON p.id = f.project_id WHERE f.id = 'goal'"
    ).fetchall()
    if not legacy_goals:
        return

    for legacy in legacy_goals:
        project_id = legacy["project_id"]
        existing = conn.execute(
            "SELECT id FROM goals WHERE project_id = ? AND parent_goal_id IS NULL ORDER BY created_at LIMIT 1",
            (project_id,),
        ).fetchone()
        goal_id = existing["id"] if existing is not None else "g001"
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        completion = None
        if "intents" in tables:
            completion = conn.execute(
                "SELECT * FROM intents WHERE project_id = ? AND to_fact_id = 'goal' ORDER BY concluded_at LIMIT 1",
                (project_id,),
            ).fetchone()
        completed_at = completion["concluded_at"] if completion is not None else None
        completed_by = completion["worker"] if completion is not None else None
        completion_description = completion["description"] if completion is not None else None
        status = "completed" if legacy["status"] == "completed" or completion is not None else "active"
        if existing is None:
            conn.execute(
                "INSERT INTO goals (id, project_id, description, parent_goal_id, status, priority, created_at, "
                "completed_at, completion_description, completed_by) "
                "VALUES (?, ?, ?, NULL, ?, 0, ?, ?, ?, ?)",
                (goal_id, project_id, legacy["description"], status, legacy["created_at"],
                 completed_at, completion_description, completed_by),
            )
        if completion is not None and "intent_sources" in tables:
            rows = conn.execute(
                "SELECT fact_id FROM intent_sources WHERE intent_id = ? AND project_id = ?",
                (completion["id"], project_id),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO goal_sources (goal_id, project_id, fact_id) VALUES (?, ?, ?)",
                    (goal_id, project_id, row["fact_id"]),
                )
        conn.execute("DELETE FROM facts WHERE id = 'goal' AND project_id = ?", (project_id,))
        conn.execute(
            "INSERT INTO scoped_counters (project_id, kind, value) VALUES (?, 'goal', 1) "
            "ON CONFLICT(project_id, kind) DO UPDATE SET value = MAX(value, 1)",
            (project_id,),
        )


def _migrate_completion_steps(conn: sqlite3.Connection) -> None:
    """Remove early Cairn-Y synthetic completion Steps and retain their Goal evidence."""
    rows = conn.execute(
        "SELECT * FROM steps WHERE goal_id IS NOT NULL AND to_fact_id IS NULL "
        "AND concluded_at IS NOT NULL AND abandoned = 0"
    ).fetchall()
    for step in rows:
        goal = conn.execute(
            "SELECT 1 FROM goals WHERE id = ? AND project_id = ? AND status = 'completed'",
            (step["goal_id"], step["project_id"]),
        ).fetchone()
        if goal is None:
            continue
        sources = conn.execute(
            "SELECT fact_id FROM step_sources WHERE step_id = ? AND project_id = ?",
            (step["id"], step["project_id"]),
        ).fetchall()
        for source in sources:
            conn.execute(
                "INSERT OR IGNORE INTO goal_sources (goal_id, project_id, fact_id) VALUES (?, ?, ?)",
                (step["goal_id"], step["project_id"], source["fact_id"]),
            )
        conn.execute(
            "UPDATE goals SET completion_description = COALESCE(completion_description, ?), "
            "completed_by = COALESCE(completed_by, ?) WHERE id = ? AND project_id = ?",
            (step["description"], step["worker"] or step["creator"], step["goal_id"], step["project_id"]),
        )
        conn.execute("DELETE FROM steps WHERE id = ? AND project_id = ?", (step["id"], step["project_id"]))


def _drop_legacy_intent_tables(conn: sqlite3.Connection) -> None:
    """Remove the migrated Cairn state tables so Steps stay authoritative."""
    conn.execute("DROP TABLE IF EXISTS intent_sources")
    conn.execute("DROP TABLE IF EXISTS intents")


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
