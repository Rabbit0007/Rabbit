from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException

from cairn.server.models import (
    Finding,
    Goal,
    Intent,
    ProjectDecide,
    ProjectMeta,
    ProjectReason,
    Step,
)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_project_id(conn: sqlite3.Connection) -> str:
    conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'project'")
    row = conn.execute("SELECT value FROM counters WHERE name = 'project'").fetchone()
    return f"proj_{row['value']:03d}"


def _next_scoped_id(
    conn: sqlite3.Connection, kind: str, prefix: str, project_id: str
) -> str:
    conn.execute(
        "INSERT OR IGNORE INTO scoped_counters (project_id, kind, value) VALUES (?, ?, 0)",
        (project_id, kind),
    )
    conn.execute(
        "UPDATE scoped_counters SET value = value + 1 WHERE project_id = ? AND kind = ?",
        (project_id, kind),
    )
    row = conn.execute(
        "SELECT value FROM scoped_counters WHERE project_id = ? AND kind = ?",
        (project_id, kind),
    ).fetchone()
    assert row is not None
    return f"{prefix}{row['value']:03d}"


def next_fact_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "fact", "f", project_id)


def next_goal_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "goal", "g", project_id)


def next_step_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "step", "s", project_id)


def next_finding_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "finding", "v", project_id)


def next_intent_id(conn: sqlite3.Connection, project_id: str) -> str:
    """Backward-compat alias for next_step_id."""
    return next_step_id(conn, project_id)


def next_hint_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "hint", "h", project_id)


# ── Project helpers ────────────────────────────────────────────────────

def get_project_or_404(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Project not found")
    return row


def check_project_active(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] != "active":
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def check_project_hint_writable(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] not in ("active", "stopped", "completed"):
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def check_project_completed(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] != "completed":
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def validate_facts_exist(
    conn: sqlite3.Connection, project_id: str, fact_ids: list[str]
) -> None:
    for fid in fact_ids:
        row = conn.execute(
            "SELECT 1 FROM facts WHERE id = ? AND project_id = ?", (fid, project_id)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Fact {fid} not found")


# ── Goal helpers ───────────────────────────────────────────────────────

def validate_goal_exists(conn: sqlite3.Connection, project_id: str, goal_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM goals WHERE id = ? AND project_id = ?", (goal_id, project_id)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"Goal {goal_id} not found")


def validate_goal_active(conn: sqlite3.Connection, project_id: str, goal_id: str) -> None:
    row = conn.execute(
        "SELECT status FROM goals WHERE id = ? AND project_id = ?", (goal_id, project_id)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"Goal {goal_id} not found")
    if row["status"] != "active":
        raise HTTPException(409, f"Goal {goal_id} is completed")


def get_goal_or_404(
    conn: sqlite3.Connection, project_id: str, goal_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM goals WHERE id = ? AND project_id = ?",
        (goal_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Goal not found")
    return row


def goal_to_model(conn: sqlite3.Connection, row: sqlite3.Row, project_id: str) -> Goal:
    sources = conn.execute(
        "SELECT fact_id FROM goal_sources WHERE goal_id = ? AND project_id = ? ORDER BY rowid",
        (row["id"], project_id),
    ).fetchall()
    return Goal(
        id=row["id"],
        description=row["description"],
        parent_goal_id=row["parent_goal_id"],
        status=row["status"],
        priority=row["priority"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        completion_description=row["completion_description"],
        completed_by=row["completed_by"],
        **{"from": [source["fact_id"] for source in sources]},
    )


def build_goals(conn: sqlite3.Connection, project_id: str) -> list[Goal]:
    rows = conn.execute(
        "SELECT * FROM goals WHERE project_id = ? ORDER BY priority, created_at",
        (project_id,),
    ).fetchall()
    return [goal_to_model(conn, r, project_id) for r in rows]


def check_all_goals_completed(conn: sqlite3.Connection, project_id: str) -> bool:
    """Return True when every current completion condition is satisfied."""
    rows = conn.execute(
        "SELECT 1 FROM goals WHERE project_id = ? AND status != 'completed'",
        (project_id,),
    ).fetchall()
    return len(rows) == 0


check_all_top_goals_completed = check_all_goals_completed


# ── Step helpers ───────────────────────────────────────────────────────

def step_to_model(conn: sqlite3.Connection, row: sqlite3.Row, project_id: str) -> Step:
    sources = conn.execute(
        "SELECT fact_id FROM step_sources WHERE step_id = ? AND project_id = ? ORDER BY rowid",
        (row["id"], project_id),
    ).fetchall()
    return Step(
        id=row["id"],
        **{"from": [s["fact_id"] for s in sources]},
        to=row["to_fact_id"],
        description=row["description"],
        goal_id=row["goal_id"],
        priority=row["priority"],
        creator=row["creator"],
        worker=row["worker"],
        last_heartbeat_at=row["last_heartbeat_at"],
        created_at=row["created_at"],
        concluded_at=row["concluded_at"],
        abandoned=bool(row["abandoned"]),
    )


def build_steps(conn: sqlite3.Connection, project_id: str) -> list[Step]:
    rows = conn.execute(
        "SELECT * FROM steps WHERE project_id = ? ORDER BY priority DESC, created_at",
        (project_id,),
    ).fetchall()
    return [step_to_model(conn, r, project_id) for r in rows]


def get_step_or_404(
    conn: sqlite3.Connection, project_id: str, step_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM steps WHERE id = ? AND project_id = ?",
        (step_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Step not found")
    return row


def get_claimable_open_step_or_404(
    conn: sqlite3.Connection, project_id: str, step_id: str, worker: str
) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_step_or_404(conn, project_id, step_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Step already concluded")
    if row["abandoned"]:
        raise HTTPException(409, "Step is abandoned")
    if row["worker"] is not None and row["worker"] != worker:
        raise HTTPException(409, f"Step is currently claimed by {row['worker']}")
    return row


def get_releasable_open_step_or_404(
    conn: sqlite3.Connection, project_id: str, step_id: str, worker: str
) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_step_or_404(conn, project_id, step_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Step already concluded")
    if row["worker"] is None:
        return row
    if row["worker"] != worker:
        raise HTTPException(409, f"Step is currently claimed by {row['worker']}")
    return row


# ── Backward-compat: intent helpers ────────────────────────────────────

def intent_to_model(conn: sqlite3.Connection, row: sqlite3.Row, project_id: str) -> Intent:
    return step_to_model(conn, row, project_id)


def build_intents(conn: sqlite3.Connection, project_id: str) -> list[Intent]:
    return build_steps(conn, project_id)


def get_intent_or_404(
    conn: sqlite3.Connection, project_id: str, intent_id: str
) -> sqlite3.Row:
    return get_step_or_404(conn, project_id, intent_id)


def get_claimable_open_intent_or_404(
    conn: sqlite3.Connection, project_id: str, intent_id: str, worker: str
) -> sqlite3.Row:
    return get_claimable_open_step_or_404(conn, project_id, intent_id, worker)


def get_releasable_open_intent_or_404(
    conn: sqlite3.Connection, project_id: str, intent_id: str, worker: str
) -> sqlite3.Row:
    return get_releasable_open_step_or_404(conn, project_id, intent_id, worker)


# ── Finding helpers ────────────────────────────────────────────────────

def finding_to_model(row: sqlite3.Row) -> Finding:
    return Finding(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        severity=row["severity"],
        kind=row["kind"],
        data=parse_json_object(row["data_json"]),
        fact_id=row["fact_id"],
        created_at=row["created_at"],
    )


def parse_json_object(value: str | None) -> dict:
    """Decode persisted Finding data without letting legacy/corrupt rows break a project read."""
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def build_findings(conn: sqlite3.Connection, project_id: str) -> list[Finding]:
    rows = conn.execute(
        "SELECT * FROM findings WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    return [finding_to_model(r) for r in rows]


# ── Timeout helpers ────────────────────────────────────────────────────

def get_step_timeout(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT step_timeout FROM settings WHERE rowid = 1").fetchone()
    return row["step_timeout"]


def get_decide_timeout(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT decide_timeout FROM settings WHERE rowid = 1").fetchone()
    return row["decide_timeout"]


# API compatibility only. Core code uses the Cairn-Y names above.
get_intent_timeout = get_step_timeout
get_reason_timeout = get_decide_timeout


# ── Project meta helpers ───────────────────────────────────────────────

def project_decide_from_row(row: sqlite3.Row) -> ProjectDecide | None:
    """Build ProjectDecide from a project row (decide_* columns)."""
    if row["decide_worker"] is None:
        return None
    return ProjectDecide(
        worker=row["decide_worker"],
        trigger=row["decide_trigger"],
        started_at=row["decide_started_at"],
        last_heartbeat_at=row["decide_last_heartbeat_at"],
    )


def project_reason_from_row(row: sqlite3.Row) -> ProjectReason | None:
    """Backward-compat: same as project_decide_from_row."""
    return project_decide_from_row(row)


def project_meta_from_row(row: sqlite3.Row) -> ProjectMeta:
    return ProjectMeta(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        bootstrap_enabled=bool(row["bootstrap_enabled"]),
        created_at=row["created_at"],
        decide=project_decide_from_row(row),
    )


def clear_project_decide(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        """
        UPDATE projects
        SET decide_worker = NULL,
            decide_trigger = NULL,
            decide_started_at = NULL,
            decide_last_heartbeat_at = NULL
        WHERE id = ?
        """,
        (project_id,),
    )


clear_project_reason = clear_project_decide


# ── Worker lease expiry ────────────────────────────────────────────────

def expire_workers(conn: sqlite3.Connection, project_id: str | None = None) -> None:
    timeout = get_step_timeout(conn)
    now = utcnow()
    query = """
        UPDATE steps
        SET worker = NULL
        WHERE to_fact_id IS NULL
          AND abandoned = 0
          AND worker IS NOT NULL
          AND last_heartbeat_at IS NOT NULL
          AND (julianday(?) - julianday(last_heartbeat_at)) * 86400 > ?
    """
    params: tuple = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE project_id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)


def expire_decide_leases(conn: sqlite3.Connection, project_id: str | None = None) -> None:
    timeout = get_decide_timeout(conn)
    now = utcnow()
    query = """
        UPDATE projects
        SET decide_worker = NULL,
            decide_trigger = NULL,
            decide_started_at = NULL,
            decide_last_heartbeat_at = NULL
        WHERE decide_worker IS NOT NULL
          AND decide_last_heartbeat_at IS NOT NULL
          AND (julianday(?) - julianday(decide_last_heartbeat_at)) * 86400 > ?
    """
    params: tuple = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)


expire_reason_leases = expire_decide_leases


# ── Legacy complete helpers (backward compat) ──────────────────────────

def get_completion_step_or_409(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    """Find the step that completed the final goal (backward compat)."""
    rows = conn.execute(
        "SELECT * FROM steps WHERE project_id = ? AND to_fact_id = 'goal'",
        (project_id,),
    ).fetchall()
    if not rows:
        raise HTTPException(409, "Completed project is missing its completion step")
    if len(rows) != 1:
        raise HTTPException(409, "Completed project has multiple completion steps")
    return rows[0]


get_completion_intent_or_409 = get_completion_step_or_409


def validate_goal_not_in_sources(fact_ids: list[str]) -> None:
    """Legacy guard — no longer enforced since 'goal' is not a fact."""
    pass
