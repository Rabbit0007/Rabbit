"""
DEPRECATED ROUTER: This router uses legacy Intent terminology.
Cairn_Y uses Step terminology instead.

Use routers/steps.py for new code.
This router is kept for backward compatibility only and will be removed in v3.0.
"""

from fastapi import APIRouter

from cairn.server.db import get_conn
from cairn.server.models import (
    ConcludeRequest,
    ConcludeResponse,
    CreateIntentRequest,
    Fact,
    HeartbeatRequest,
    Intent,
)
from cairn.server.services import (
    check_project_active,
    get_claimable_open_step_or_404,
    get_releasable_open_step_or_404,
    step_to_model,
    next_fact_id,
    next_step_id,
    utcnow,
    validate_facts_exist,
    validate_intent_creator_worker,
    validate_goal_not_in_sources,
)

router = APIRouter(tags=["intents (deprecated)"], deprecated=True)


@router.post(
    "/projects/{project_id}/intents",
    response_model=Intent,
    status_code=201,
)
def create_intent(project_id: str, body: CreateIntentRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        validate_facts_exist(conn, project_id, body.from_)
        validate_goal_not_in_sources(body.from_)
        validate_intent_creator_worker(body.creator, body.worker)

        now = utcnow()
        iid = next_step_id(conn, project_id)
        claimed = body.worker is not None
        conn.execute(
            "INSERT INTO steps (id, project_id, to_fact_id, description, creator, worker, last_heartbeat_at, created_at, concluded_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, NULL)",
            (
                iid,
                project_id,
                body.description,
                body.creator,
                body.worker,
                now if claimed else None,
                now,
            ),
        )
        for fid in body.from_:
            conn.execute(
                "INSERT INTO step_sources (step_id, project_id, fact_id) VALUES (?, ?, ?)",
                (iid, project_id, fid),
            )

        return Intent(
            id=iid,
            **{"from": body.from_},
            to=None,
            description=body.description,
            creator=body.creator,
            worker=body.worker,
            last_heartbeat_at=now if claimed else None,
            created_at=now,
            concluded_at=None,
        )


@router.post(
    "/projects/{project_id}/intents/{step_id}/heartbeat",
    response_model=Intent,
)
def heartbeat(project_id: str, step_id: str, body: HeartbeatRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        get_claimable_open_step_or_404(conn, project_id, step_id, body.worker)

        now = utcnow()
        conn.execute(
            "UPDATE steps SET worker = ?, last_heartbeat_at = ? WHERE id = ? AND project_id = ?",
            (body.worker, now, step_id, project_id),
        )

        updated = conn.execute(
            "SELECT * FROM steps WHERE id = ? AND project_id = ?",
            (step_id, project_id),
        ).fetchone()
        return step_to_model(conn, updated, project_id)


@router.post(
    "/projects/{project_id}/intents/{step_id}/release",
    response_model=Intent,
)
def release(project_id: str, step_id: str, body: HeartbeatRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        row = get_releasable_open_step_or_404(conn, project_id, step_id, body.worker)

        if row["worker"] == body.worker:
            conn.execute(
                "UPDATE steps SET worker = NULL WHERE id = ? AND project_id = ?",
                (step_id, project_id),
            )
            row = conn.execute(
                "SELECT * FROM steps WHERE id = ? AND project_id = ?",
                (step_id, project_id),
            ).fetchone()

        return step_to_model(conn, row, project_id)


@router.post(
    "/projects/{project_id}/intents/{step_id}/conclude",
    response_model=ConcludeResponse,
)
def conclude(project_id: str, step_id: str, body: ConcludeRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        get_claimable_open_step_or_404(conn, project_id, step_id, body.worker)
        fact_description = body.description

        now = utcnow()
        fid = next_fact_id(conn, project_id)

        conn.execute(
            "INSERT INTO facts (id, project_id, description) VALUES (?, ?, ?)",
            (fid, project_id, fact_description),
        )
        conn.execute(
            "UPDATE steps SET to_fact_id = ?, worker = ?, last_heartbeat_at = ?, concluded_at = ? WHERE id = ? AND project_id = ?",
            (fid, body.worker, now, now, step_id, project_id),
        )

        updated = conn.execute(
            "SELECT * FROM steps WHERE id = ? AND project_id = ?",
            (step_id, project_id),
        ).fetchone()

        return ConcludeResponse(
            fact=Fact(id=fid, description=fact_description),
            step=step_to_model(conn, updated, project_id),
        )
