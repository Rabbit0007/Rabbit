from fastapi import APIRouter

from cairn.server.db import get_conn
from cairn.server.models import CreateFindingRequest, Finding
from cairn.server.services import (
    build_findings,
    check_project_active,
    next_finding_id,
    utcnow,
)

router = APIRouter(tags=["findings"])


@router.post(
    "/projects/{project_id}/findings",
    response_model=Finding,
    status_code=201,
)
def create_finding(project_id: str, body: CreateFindingRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)

        now = utcnow()
        vid = next_finding_id(conn, project_id)
        conn.execute(
            "INSERT INTO findings (id, project_id, title, description, severity, fact_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (vid, project_id, body.title, body.description,
             body.severity, body.fact_id, now),
        )

        return Finding(
            id=vid,
            title=body.title,
            description=body.description,
            severity=body.severity,
            fact_id=body.fact_id,
            created_at=now,
        )


@router.get(
    "/projects/{project_id}/findings",
    response_model=list[Finding],
)
def list_findings(project_id: str):
    with get_conn() as conn:
        return build_findings(conn, project_id)
