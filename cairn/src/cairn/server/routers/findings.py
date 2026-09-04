import json

from fastapi import APIRouter

from cairn.server.db import get_conn
from cairn.server.finding_projection import sync_finding
from cairn.server.models import CreateFindingRequest, Finding
from cairn.server.services import (
    build_findings,
    check_project_active,
    next_finding_id,
    utcnow,
    validate_facts_exist,
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
        if body.fact_id is not None:
            validate_facts_exist(conn, project_id, [body.fact_id])

        now = utcnow()
        vid = next_finding_id(conn, project_id)
        conn.execute(
            "INSERT INTO findings (id, project_id, title, description, severity, kind, data_json, fact_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (vid, project_id, body.title, body.description,
             body.severity, body.kind, json.dumps(body.data, ensure_ascii=False), body.fact_id, now),
        )
        sync_finding(conn, project_id, vid)

        return Finding(
            id=vid,
            title=body.title,
            description=body.description,
            severity=body.severity,
            kind=body.kind,
            data=body.data,
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
