from fastapi import APIRouter

from cairn.server.db import get_conn
from cairn.server.models import (
    CompleteGoalRequest,
    CreateGoalRequest,
    Goal,
    UpdateGoalRequest,
)
from cairn.server.services import (
    check_project_active,
    check_all_top_goals_completed,
    goal_to_model,
    get_goal_or_404,
    next_goal_id,
    next_step_id,
    utcnow,
)

router = APIRouter(tags=["goals"])


@router.post(
    "/projects/{project_id}/goals",
    response_model=Goal,
    status_code=201,
)
def create_goal(project_id: str, body: CreateGoalRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        if body.parent_goal_id is not None:
            get_goal_or_404(conn, project_id, body.parent_goal_id)

        now = utcnow()
        gid = next_goal_id(conn, project_id)
        conn.execute(
            "INSERT INTO goals (id, project_id, description, parent_goal_id, status, priority, created_at, completed_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, NULL)",
            (gid, project_id, body.description, body.parent_goal_id, body.priority, now),
        )

        return Goal(
            id=gid,
            description=body.description,
            parent_goal_id=body.parent_goal_id,
            status="active",
            priority=body.priority,
            created_at=now,
            completed_at=None,
        )


@router.patch(
    "/projects/{project_id}/goals/{goal_id}",
    response_model=Goal,
)
def update_goal(project_id: str, goal_id: str, body: UpdateGoalRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        get_goal_or_404(conn, project_id, goal_id)

        if body.status is not None:
            completed_at = utcnow() if body.status == "completed" else None
            conn.execute(
                "UPDATE goals SET status = ?, completed_at = ? WHERE id = ? AND project_id = ?",
                (body.status, completed_at, goal_id, project_id),
            )
        if body.priority is not None:
            conn.execute(
                "UPDATE goals SET priority = ? WHERE id = ? AND project_id = ?",
                (body.priority, goal_id, project_id),
            )
        if body.description is not None:
            conn.execute(
                "UPDATE goals SET description = ? WHERE id = ? AND project_id = ?",
                (body.description, goal_id, project_id),
            )

        updated = conn.execute(
            "SELECT * FROM goals WHERE id = ? AND project_id = ?",
            (goal_id, project_id),
        ).fetchone()
        return goal_to_model(updated)


@router.post(
    "/projects/{project_id}/goals/{goal_id}/complete",
    response_model=Goal,
)
def complete_goal(project_id: str, goal_id: str, body: CompleteGoalRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        get_goal_or_404(conn, project_id, goal_id)

        now = utcnow()
        conn.execute(
            "UPDATE goals SET status = 'completed', completed_at = ? WHERE id = ? AND project_id = ?",
            (now, goal_id, project_id),
        )

        # Create a completion step for traceability
        sid = next_step_id(conn, project_id)
        conn.execute(
            "INSERT INTO steps (id, project_id, to_fact_id, description, goal_id, priority, "
            "creator, worker, last_heartbeat_at, created_at, concluded_at, abandoned) "
            "VALUES (?, ?, NULL, ?, ?, 0, ?, ?, ?, ?, ?, 0)",
            (sid, project_id, body.description, goal_id, body.worker, body.worker, now, now, now),
        )
        for fid in body.from_:
            conn.execute(
                "INSERT INTO step_sources (step_id, project_id, fact_id) VALUES (?, ?, ?)",
                (sid, project_id, fid),
            )

        # Auto-complete project if all top-level goals are done
        if check_all_top_goals_completed(conn, project_id):
            conn.execute(
                "UPDATE projects SET status = 'completed' WHERE id = ?",
                (project_id,),
            )

        updated = conn.execute(
            "SELECT * FROM goals WHERE id = ? AND project_id = ?",
            (goal_id, project_id),
        ).fetchone()
        return goal_to_model(updated)
