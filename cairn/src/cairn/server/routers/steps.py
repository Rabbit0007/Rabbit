from fastapi import APIRouter

from cairn.server.db import get_conn
from cairn.server.models import (
    ConcludeRequest,
    ConcludeResponse,
    CreateFindingRequest,
    CreateStepRequest,
    Fact,
    Finding,
    HeartbeatRequest,
    Step,
    UpdateStepRequest,
)
from cairn.server.services import (
    build_findings,
    check_project_active,
    get_claimable_open_step_or_404,
    get_releasable_open_step_or_404,
    next_fact_id,
    next_finding_id,
    next_step_id,
    step_to_model,
    utcnow,
    validate_facts_exist,
    validate_goal_exists,
)

router = APIRouter(tags=["steps"])


@router.post(
    "/projects/{project_id}/steps",
    response_model=Step,
    status_code=201,
)
def create_step(project_id: str, body: CreateStepRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        validate_facts_exist(conn, project_id, body.from_)
        if body.goal_id is not None:
            validate_goal_exists(conn, project_id, body.goal_id)

        now = utcnow()
        sid = next_step_id(conn, project_id)
        claimed = body.worker is not None
        conn.execute(
            "INSERT INTO steps (id, project_id, to_fact_id, description, goal_id, priority, creator, worker, last_heartbeat_at, created_at, concluded_at, abandoned) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, NULL, 0)",
            (
                sid,
                project_id,
                body.description,
                body.goal_id,
                body.priority,
                body.creator,
                body.worker,
                now if claimed else None,
                now,
            ),
        )
        for fid in body.from_:
            conn.execute(
                "INSERT INTO step_sources (step_id, project_id, fact_id) VALUES (?, ?, ?)",
                (sid, project_id, fid),
            )

        return Step(
            id=sid,
            **{"from": body.from_},
            to=None,
            description=body.description,
            goal_id=body.goal_id,
            priority=body.priority,
            creator=body.creator,
            worker=body.worker,
            last_heartbeat_at=now if claimed else None,
            created_at=now,
            concluded_at=None,
            abandoned=False,
        )


@router.patch(
    "/projects/{project_id}/steps/{step_id}",
    response_model=Step,
)
def update_step(project_id: str, step_id: str, body: UpdateStepRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        get_claimable_open_step_or_404(conn, project_id, step_id, worker="")

        if body.priority is not None:
            conn.execute(
                "UPDATE steps SET priority = ? WHERE id = ? AND project_id = ?",
                (body.priority, step_id, project_id),
            )
        if body.goal_id is not None:
            validate_goal_exists(conn, project_id, body.goal_id)
            conn.execute(
                "UPDATE steps SET goal_id = ? WHERE id = ? AND project_id = ?",
                (body.goal_id, step_id, project_id),
            )
        if body.abandoned is not None:
            conn.execute(
                "UPDATE steps SET abandoned = ? WHERE id = ? AND project_id = ?",
                (int(body.abandoned), step_id, project_id),
            )

        updated = conn.execute(
            "SELECT * FROM steps WHERE id = ? AND project_id = ?",
            (step_id, project_id),
        ).fetchone()
        return step_to_model(conn, updated, project_id)


@router.post(
    "/projects/{project_id}/steps/{step_id}/heartbeat",
    response_model=Step,
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
    "/projects/{project_id}/steps/{step_id}/release",
    response_model=Step,
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
    "/projects/{project_id}/steps/{step_id}/conclude",
    response_model=ConcludeResponse,
)
def conclude(project_id: str, step_id: str, body: ConcludeRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        get_claimable_open_step_or_404(conn, project_id, step_id, body.worker)

        now = utcnow()
        fid = next_fact_id(conn, project_id)

        conn.execute(
            "INSERT INTO facts (id, project_id, description) VALUES (?, ?, ?)",
            (fid, project_id, body.description),
        )
        conn.execute(
            "UPDATE steps SET to_fact_id = ?, worker = ?, last_heartbeat_at = ?, concluded_at = ? "
            "WHERE id = ? AND project_id = ?",
            (fid, body.worker, now, now, step_id, project_id),
        )

        updated = conn.execute(
            "SELECT * FROM steps WHERE id = ? AND project_id = ?",
            (step_id, project_id),
        ).fetchone()

        finding = None
        if body.finding is not None:
            vid = next_finding_id(conn, project_id)
            conn.execute(
                "INSERT INTO findings (id, project_id, title, description, severity, fact_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (vid, project_id, body.finding.title, body.finding.description,
                 body.finding.severity, fid, now),
            )
            finding = Finding(
                id=vid,
                title=body.finding.title,
                description=body.finding.description,
                severity=body.finding.severity,
                fact_id=fid,
                created_at=now,
            )

        return ConcludeResponse(
            fact=Fact(id=fid, description=body.description),
            step=step_to_model(conn, updated, project_id),
            finding=finding,
        )
