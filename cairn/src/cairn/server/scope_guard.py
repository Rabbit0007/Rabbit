from __future__ import annotations

import sqlite3

from cairn.project_scope import (
    HintRecord,
    ScopeEvaluation,
    evaluate_text_scope,
    is_scope_blocked_fact,
    scope_blocked_fact_description,
)


def project_scope_inputs(
    conn: sqlite3.Connection,
    project_id: str,
) -> tuple[str, str, list[HintRecord]]:
    origin_row = conn.execute(
        "SELECT description FROM facts WHERE project_id = ? AND id = 'origin'",
        (project_id,),
    ).fetchone()
    goal_rows = conn.execute(
        "SELECT description FROM goals WHERE project_id = ? AND parent_goal_id IS NULL "
        "ORDER BY priority DESC, created_at",
        (project_id,),
    ).fetchall()
    hint_rows = conn.execute(
        "SELECT id, content, creator, created_at FROM hints WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    hints = [
        HintRecord(
            id=row["id"],
            content=row["content"],
            creator=row["creator"],
            created_at=row["created_at"],
        )
        for row in hint_rows
    ]
    origin = str(origin_row["description"]) if origin_row else ""
    goal = "\n".join(str(row["description"]) for row in goal_rows)
    return origin, goal, hints


def evaluate_scope_for_description(
    conn: sqlite3.Connection,
    project_id: str,
    description: str,
) -> tuple[ScopeEvaluation, str, str, list[HintRecord]]:
    origin, goal, hints = project_scope_inputs(conn, project_id)
    result = evaluate_text_scope(project_id, origin, goal, hints, description)
    return result, origin, goal, hints


def blocked_scope_fact_description(conn: sqlite3.Connection, project_id: str) -> str:
    origin, goal, hints = project_scope_inputs(conn, project_id)
    return scope_blocked_fact_description(project_id, origin, goal, hints)


def has_scope_blocked_source_fact(
    conn: sqlite3.Connection,
    project_id: str,
    fact_ids: list[str],
) -> bool:
    if not fact_ids:
        return False
    rows = conn.execute(
        f"SELECT description FROM facts WHERE project_id = ? AND id IN ({','.join('?' for _ in fact_ids)})",
        (project_id, *fact_ids),
    ).fetchall()
    return any(is_scope_blocked_fact(row["description"]) for row in rows)
