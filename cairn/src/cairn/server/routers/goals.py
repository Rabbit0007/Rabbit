from fastapi import APIRouter, HTTPException, Response

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
    clear_project_decide,
    goal_to_model,
    get_goal_or_404,
    next_goal_id,
    utcnow,
    validate_facts_exist,
    validate_goal_active,
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
            validate_goal_active(conn, project_id, body.parent_goal_id)

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
        goal = get_goal_or_404(conn, project_id, goal_id)
        if goal["status"] == "completed":
            raise HTTPException(409, "Completed goals cannot be updated")

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
        return goal_to_model(conn, updated, project_id)


@router.delete("/projects/{project_id}/goals/{goal_id}", status_code=204)
def delete_sub_goal(project_id: str, goal_id: str):
    """Delete an unused active sub-goal; the root completion condition is immutable."""
    with get_conn() as conn:
        check_project_active(conn, project_id)
        goal = get_goal_or_404(conn, project_id, goal_id)
        if goal["parent_goal_id"] is None:
            raise HTTPException(409, "Top-level goals cannot be deleted")
        if goal["status"] != "active":
            raise HTTPException(409, "Completed goals cannot be deleted")
        child = conn.execute(
            "SELECT 1 FROM goals WHERE project_id = ? AND parent_goal_id = ? LIMIT 1",
            (project_id, goal_id),
        ).fetchone()
        if child is not None:
            raise HTTPException(409, "Goal still has sub-goals")
        linked_step = conn.execute(
            "SELECT 1 FROM steps WHERE project_id = ? AND goal_id = ? LIMIT 1",
            (project_id, goal_id),
        ).fetchone()
        if linked_step is not None:
            raise HTTPException(409, "Goal is still referenced by steps")
        conn.execute(
            "DELETE FROM goals WHERE id = ? AND project_id = ?",
            (goal_id, project_id),
        )
        return Response(status_code=204)


@router.post(
    "/projects/{project_id}/goals/{goal_id}/complete",
    response_model=Goal,
)
def complete_goal(project_id: str, goal_id: str, body: CompleteGoalRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        goal = get_goal_or_404(conn, project_id, goal_id)
        if goal["status"] == "completed":
            raise HTTPException(409, "Goal already completed")
        validate_facts_exist(conn, project_id, body.from_)

        now = utcnow()
        conn.execute(
            "UPDATE goals SET status = 'completed', completed_at = ?, completion_description = ?, completed_by = ? "
            "WHERE id = ? AND project_id = ?",
            (now, body.description, body.worker, goal_id, project_id),
        )
        for fid in body.from_:
            conn.execute(
                "INSERT INTO goal_sources (goal_id, project_id, fact_id) VALUES (?, ?, ?)",
                (goal_id, project_id, fid),
            )

        # The project terminates only when every current completion condition is met.
        if check_all_top_goals_completed(conn, project_id):
            conn.execute(
                "UPDATE projects SET status = 'completed' WHERE id = ?",
                (project_id,),
            )
            clear_project_decide(conn, project_id)
            conn.execute(
                "UPDATE steps SET worker = NULL WHERE project_id = ? AND concluded_at IS NULL",
                (project_id,),
            )

        updated = conn.execute(
            "SELECT * FROM goals WHERE id = ? AND project_id = ?",
            (goal_id, project_id),
        ).fetchone()
        return goal_to_model(conn, updated, project_id)
