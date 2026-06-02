from __future__ import annotations

from cairn.server import db

# Product feature tables (vulnerability reports, and future product tables).
#
# These are additive: each statement uses ``CREATE ... IF NOT EXISTS`` so the
# DDL is idempotent and never touches the existing core ``SCHEMA`` in
# ``db.py``. This mirrors the pattern established by ``auth_db.py`` for the
# authentication tables.
#
# The ``vulnerabilities`` table materializes security findings extracted from
# project facts (see the vulnerability extraction service). It references
# ``projects(id)`` with ``ON DELETE CASCADE`` so deleting a project removes its
# vulnerabilities. The ``UNIQUE(project_id, fact_id)`` constraint lets the
# extraction service upsert findings keyed by their source fact without
# creating duplicates.
#
# The ``worker_task_history`` table records each task a worker has executed so
# the worker dashboard can show recent activity and derive health metrics
# (tasks completed, average duration). Rows are appended when a task finishes;
# ``completed_at`` and ``duration_seconds`` are nullable so an in-flight or
# abandoned task can still be recorded. The supporting indexes speed up the two
# dashboard access patterns: lookups by ``worker_name`` and ordering by
# ``completed_at``.
#
# The ``templates`` table stores user-created custom project templates. It
# references ``users(id)`` with ``ON DELETE CASCADE`` so removing a user removes
# their saved templates. The template's hints are serialized as a JSON array of
# ``{content, creator}`` objects in ``hints_json`` (defaulting to an empty
# array). Built-in templates are *not* stored here -- they live as a Python
# constant in ``templates_service.py``. The ``idx_templates_user`` index speeds
# up listing a single user's templates.
PRODUCT_SCHEMA = """\
CREATE TABLE IF NOT EXISTS vulnerabilities (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    fact_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low')),
    discovered_at TEXT NOT NULL,
    source_intent_id TEXT,
    source_intent_description TEXT,
    source_worker TEXT,
    source_fact_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    process_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN ('confirmed', 'ignored')),
    UNIQUE(project_id, fact_id)
);

CREATE INDEX IF NOT EXISTS idx_vulnerabilities_project
    ON vulnerabilities(project_id);
CREATE INDEX IF NOT EXISTS idx_vulnerabilities_severity
    ON vulnerabilities(severity);

CREATE TABLE IF NOT EXISTS worker_task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_name TEXT NOT NULL,
    project_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    intent_id TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_seconds REAL,
    outcome TEXT NOT NULL CHECK(outcome IN ('success', 'failed', 'rejected', 'released'))
);

CREATE INDEX IF NOT EXISTS idx_worker_history_worker
    ON worker_task_history(worker_name);
CREATE INDEX IF NOT EXISTS idx_worker_history_time
    ON worker_task_history(completed_at);

CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    origin TEXT NOT NULL,
    goal TEXT NOT NULL,
    hints_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_templates_user
    ON templates(user_id);

CREATE TABLE IF NOT EXISTS export_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    format TEXT NOT NULL,
    filename TEXT NOT NULL,
    scope TEXT NOT NULL,
    vulnerability_count INTEGER NOT NULL DEFAULT 0,
    project_id TEXT,
    project_name TEXT,
    severity TEXT,
    status TEXT
);

CREATE INDEX IF NOT EXISTS idx_export_records_time
    ON export_records(created_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    summary TEXT NOT NULL,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_time
    ON audit_log(created_at);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    body TEXT,
    link TEXT,
    read INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_notifications_time
    ON notifications(created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_read
    ON notifications(read);
"""

VULNERABILITY_COLUMNS: dict[str, str] = {
    "source_intent_id": "TEXT",
    "source_intent_description": "TEXT",
    "source_worker": "TEXT",
    "source_fact_ids_json": "TEXT NOT NULL DEFAULT '[]'",
    "evidence_json": "TEXT NOT NULL DEFAULT '[]'",
    "process_json": "TEXT NOT NULL DEFAULT '[]'",
    "status": "TEXT NOT NULL DEFAULT 'confirmed'",
}


def _ensure_vulnerability_columns(conn) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(vulnerabilities)").fetchall()
    }
    for name, ddl in VULNERABILITY_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE vulnerabilities ADD COLUMN {name} {ddl}")


def configure_product_db() -> None:
    """Run the product-feature schema DDL on the existing SQLite connection.

    This is additive: it creates the ``vulnerabilities``, ``worker_task_history``,
    and ``templates`` tables (and supporting indexes) if they do not already
    exist. It must be called after :func:`cairn.server.db.configure` so that the
    database connection is initialized, and after the core schema (which defines
    ``projects``) and the auth schema (which defines ``users``) so the foreign
    key targets exist.
    """
    with db.get_conn() as conn:
        conn.executescript(PRODUCT_SCHEMA)
        _ensure_vulnerability_columns(conn)
