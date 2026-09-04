from __future__ import annotations

import sqlite3

from cairn.server import db


def test_configure_adds_bootstrap_enabled_to_legacy_projects_table(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO projects (id, title, created_at) VALUES ('proj_001', 'legacy', '2026-01-01T00:00:00Z')"
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        row = conn.execute("SELECT bootstrap_enabled FROM projects WHERE id = 'proj_001'").fetchone()
    assert row["bootstrap_enabled"] == 1


def test_configure_maps_disabled_bootstrap_mode_to_false(tmp_path, monkeypatch) -> None:
    path = tmp_path / "intermediate.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                bootstrap_mode TEXT NOT NULL DEFAULT 'auto',
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO projects (id, title, bootstrap_mode, created_at) VALUES ('proj_001', 'disabled', 'disabled', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO projects (id, title, bootstrap_mode, created_at) VALUES ('proj_002', 'enabled', 'enabled', '2026-01-01T00:00:00Z')"
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, bootstrap_enabled FROM projects ORDER BY id").fetchall()
    assert [(row["id"], row["bootstrap_enabled"]) for row in rows] == [
        ("proj_001", 0),
        ("proj_002", 1),
    ]


def test_configure_migrates_legacy_intents_after_new_schema_creation(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-intents.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                reason_worker TEXT,
                reason_trigger TEXT,
                reason_started_at TEXT,
                reason_last_heartbeat_at TEXT
            );
            CREATE TABLE facts (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                description TEXT NOT NULL,
                PRIMARY KEY (id, project_id)
            );
            CREATE TABLE intents (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                to_fact_id TEXT,
                description TEXT NOT NULL,
                creator TEXT NOT NULL,
                worker TEXT,
                last_heartbeat_at TEXT,
                created_at TEXT NOT NULL,
                concluded_at TEXT,
                PRIMARY KEY (id, project_id)
            );
            CREATE TABLE intent_sources (
                intent_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                fact_id TEXT NOT NULL,
                PRIMARY KEY (intent_id, project_id, fact_id)
            );
            CREATE TABLE hints (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                content TEXT NOT NULL,
                creator TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (id, project_id)
            );
            CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE settings (intent_timeout INTEGER NOT NULL, reason_timeout INTEGER NOT NULL);
            INSERT INTO projects (id, title, created_at) VALUES ('proj_001', 'legacy', '2026-01-01T00:00:00Z');
            INSERT INTO intents (id, project_id, description, creator, created_at)
                VALUES ('i001', 'proj_001', 'legacy step', 'reasoner', '2026-01-01T00:00:01Z');
            INSERT INTO intent_sources (intent_id, project_id, fact_id)
                VALUES ('i001', 'proj_001', 'origin');
            INSERT INTO intents (id, project_id, description, creator, created_at)
                VALUES ('i_orphan', 'missing_project', 'stale step', 'reasoner', '2026-01-01T00:00:02Z');
            INSERT INTO intent_sources (intent_id, project_id, fact_id)
                VALUES ('i_orphan', 'missing_project', 'origin');
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        step = conn.execute(
            "SELECT id, project_id, description FROM steps WHERE id = 'i001'"
        ).fetchone()
        source = conn.execute(
            "SELECT step_id, project_id, fact_id FROM step_sources WHERE step_id = 'i001'"
        ).fetchone()
        orphan_step = conn.execute("SELECT 1 FROM steps WHERE id = 'i_orphan'").fetchone()
        orphan_source = conn.execute("SELECT 1 FROM step_sources WHERE step_id = 'i_orphan'").fetchone()
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tuple(step) == ("i001", "proj_001", "legacy step")
    assert tuple(source) == ("i001", "proj_001", "origin")
    assert orphan_step is None
    assert orphan_source is None
    assert "intents" not in tables
    assert "intent_sources" not in tables


def test_configure_converts_legacy_goal_fact_and_completion_intent(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-completed.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
                bootstrap_enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                reason_worker TEXT, reason_trigger TEXT, reason_started_at TEXT, reason_last_heartbeat_at TEXT
            );
            CREATE TABLE facts (id TEXT NOT NULL, project_id TEXT NOT NULL, description TEXT NOT NULL,
                                PRIMARY KEY (id, project_id));
            CREATE TABLE intents (
                id TEXT NOT NULL, project_id TEXT NOT NULL, to_fact_id TEXT, description TEXT NOT NULL,
                creator TEXT NOT NULL, worker TEXT, last_heartbeat_at TEXT, created_at TEXT NOT NULL,
                concluded_at TEXT, PRIMARY KEY (id, project_id)
            );
            CREATE TABLE intent_sources (
                intent_id TEXT NOT NULL, project_id TEXT NOT NULL, fact_id TEXT NOT NULL,
                PRIMARY KEY (intent_id, project_id, fact_id)
            );
            CREATE TABLE settings (intent_timeout INTEGER NOT NULL, reason_timeout INTEGER NOT NULL);
            CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE scoped_counters (project_id TEXT NOT NULL, kind TEXT NOT NULL, value INTEGER NOT NULL,
                                          PRIMARY KEY (project_id, kind));
            INSERT INTO projects VALUES ('proj_001', 'legacy', 'completed', 1, '2026-01-01T00:00:00Z',
                                         NULL, NULL, NULL, NULL);
            INSERT INTO facts VALUES ('origin', 'proj_001', 'start');
            INSERT INTO facts VALUES ('goal', 'proj_001', 'recover flag');
            INSERT INTO facts VALUES ('f001', 'proj_001', 'flag{ok}');
            INSERT INTO intents VALUES ('i001', 'proj_001', 'goal', 'flag proves completion', 'reasoner',
                                        'reasoner', '2026-01-01T00:01:00Z', '2026-01-01T00:01:00Z',
                                        '2026-01-01T00:01:00Z');
            INSERT INTO intent_sources VALUES ('i001', 'proj_001', 'f001');
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        goal = conn.execute("SELECT * FROM goals WHERE project_id = 'proj_001'").fetchone()
        source = conn.execute("SELECT * FROM goal_sources WHERE project_id = 'proj_001'").fetchone()
        facts = conn.execute("SELECT id FROM facts WHERE project_id = 'proj_001' ORDER BY id").fetchall()
        steps = conn.execute("SELECT id FROM steps WHERE project_id = 'proj_001'").fetchall()
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert goal["id"] == "g001"
    assert goal["description"] == "recover flag"
    assert goal["status"] == "completed"
    assert goal["completion_description"] == "flag proves completion"
    assert goal["completed_by"] == "reasoner"
    assert source["fact_id"] == "f001"
    assert [row["id"] for row in facts] == ["f001", "origin"]
    assert steps == []
    assert "intents" not in tables
    assert "intent_sources" not in tables
