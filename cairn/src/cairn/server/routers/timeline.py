"""Read-only timeline derived from Cairn-Y Goal/Step/Fact state."""

from __future__ import annotations

from fastapi import APIRouter

from cairn.server.db import get_conn
from cairn.server.services import get_project_or_404
from cairn.server.timeline_models import TimelineEvent

router = APIRouter(prefix="/api/projects/{project_id}/timeline", tags=["timeline"])


@router.get("", response_model=list[TimelineEvent])
def get_timeline(project_id: str) -> list[TimelineEvent]:
    """Return Step declarations/conclusions, Fact discoveries and Goal completions."""
    with get_conn() as conn:
        get_project_or_404(conn, project_id)
        fact_rows = conn.execute(
            "SELECT id, description FROM facts WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        ).fetchall()
        step_rows = conn.execute(
            "SELECT id, to_fact_id, description, creator, worker, created_at, "
            "concluded_at FROM steps WHERE project_id = ? ORDER BY created_at, rowid",
            (project_id,),
        ).fetchall()
        goal_rows = conn.execute(
            "SELECT id, description, completed_at, completed_by FROM goals "
            "WHERE project_id = ? AND completed_at IS NOT NULL "
            "ORDER BY completed_at, created_at, rowid",
            (project_id,),
        ).fetchall()

    facts = {str(row["id"]): str(row["description"]) for row in fact_rows}
    ordered: list[tuple[str, int, TimelineEvent]] = []
    sequence = 0

    for step in step_rows:
        step_id = str(step["id"])
        created_at = str(step["created_at"])
        ordered.append((created_at, sequence, TimelineEvent(
            id=f"step_declaration:{step_id}", event_type="step_declaration",
            description=str(step["description"]), timestamp=created_at,
            actor=str(step["creator"]), node_id=step_id,
        )))
        sequence += 1

        concluded_at = step["concluded_at"]
        if concluded_at is None:
            continue
        concluded_at = str(concluded_at)
        ordered.append((concluded_at, sequence, TimelineEvent(
            id=f"step_conclusion:{step_id}", event_type="step_conclusion",
            description=str(step["description"]), timestamp=concluded_at,
            actor=str(step["worker"] or step["creator"]), node_id=step_id,
        )))
        sequence += 1

        fact_id = str(step["to_fact_id"] or "")
        if fact_id and fact_id in facts:
            ordered.append((concluded_at, sequence, TimelineEvent(
                id=f"fact_discovery:{fact_id}", event_type="fact_discovery",
                description=facts[fact_id], timestamp=concluded_at,
                actor=None, node_id=fact_id,
            )))
            sequence += 1

    for goal in goal_rows:
        goal_id = str(goal["id"])
        completed_at = str(goal["completed_at"])
        ordered.append((completed_at, sequence, TimelineEvent(
            id=f"goal_completion:{goal_id}", event_type="goal_completion",
            description=str(goal["description"]), timestamp=completed_at,
            actor=str(goal["completed_by"]) if goal["completed_by"] else None,
            node_id=goal_id,
        )))
        sequence += 1

    ordered.sort(key=lambda item: (item[0], item[1]))
    return [event for _timestamp, _sequence, event in ordered]
