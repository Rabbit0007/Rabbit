from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from cairn.server.db import get_conn
from cairn.server.report_template_service import compose_template_report, inspect_template
from cairn.server.routers.vulnerabilities import _query_filtered_vulnerabilities

router = APIRouter(prefix="/api/report-templates", tags=["report-templates"])


class TemplateUpload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str


@router.get("")
def list_templates() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, filename, created_at, is_active FROM report_templates ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("")
def upload_template(payload: TemplateUpload) -> dict:
    if not payload.filename.lower().endswith(".docx"):
        raise HTTPException(422, "只支持 DOCX 模板")
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(422, "模板内容不是有效的 Base64") from exc
    if not content.startswith(b"PK") or len(content) > 15 * 1024 * 1024:
        raise HTTPException(422, "DOCX 模板无效或超过 15MB")
    try:
        analysis = inspect_template(content)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    template_id = f"rpt_{uuid.uuid4().hex[:16]}"
    created_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("UPDATE report_templates SET is_active = 0")
        conn.execute(
            "INSERT INTO report_templates (id,name,filename,content,analysis_json,created_at,is_active) VALUES (?,?,?,?,?,?,1)",
            (template_id, payload.name.strip(), payload.filename, content, json.dumps(analysis, ensure_ascii=False), created_at),
        )
    return {"id": template_id, "name": payload.name.strip(), "filename": payload.filename, "created_at": created_at, "is_active": 1}


@router.post("/{template_id}/activate")
def activate_template(template_id: str) -> dict:
    with get_conn() as conn:
        found = conn.execute("SELECT id FROM report_templates WHERE id = ?", (template_id,)).fetchone()
        if not found:
            raise HTTPException(404, "报告模板不存在")
        conn.execute("UPDATE report_templates SET is_active = CASE WHEN id = ? THEN 1 ELSE 0 END", (template_id,))
    return {"status": "active", "id": template_id}


@router.delete("/{template_id}")
def delete_template(template_id: str) -> dict:
    with get_conn() as conn:
        conn.execute("DELETE FROM report_templates WHERE id = ?", (template_id,))
    return {"status": "deleted"}


@router.get("/{template_id}/export")
def export_from_template(template_id: str, project_id: str = Query(...)) -> Response:
    with get_conn() as conn:
        template_row = conn.execute(
            "SELECT name, filename, content, analysis_json FROM report_templates WHERE id = ?", (template_id,)
        ).fetchone()
        project = conn.execute("SELECT id, title FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not template_row:
        raise HTTPException(404, "报告模板不存在")
    if not project:
        raise HTTPException(404, "项目不存在")
    findings = _query_filtered_vulnerabilities(None, project_id, status="confirmed")
    finding_payloads = []
    with get_conn() as conn:
        for item in findings:
            fact_ids = item.related_fact_ids or [item.fact_id]
            placeholders = ",".join("?" for _ in fact_ids)
            screenshots = conn.execute(
                f"SELECT filename, mime_type, content FROM report_evidence_artifacts "
                f"WHERE project_id = ? AND fact_id IN ({placeholders}) ORDER BY created_at",
                (item.project_id, *fact_ids),
            ).fetchall()
            data = item.model_dump(mode="json")
            data["screenshots"] = [
                {"filename": row["filename"], "mime_type": row["mime_type"], "content": bytes(row["content"])}
                for row in screenshots
            ]
            finding_payloads.append(data)
    try:
        body = compose_template_report(
            bytes(template_row["content"]), json.loads(template_row["analysis_json"]),
            {"id": project["id"], "name": project["title"]},
            finding_payloads,
        )
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    filename = f"{project['title']}-渗透测试报告.docx"
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="report.docx"; filename*=UTF-8\'\'{quote(filename)}'},
    )
