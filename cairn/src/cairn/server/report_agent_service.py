"""Background report enrichment for native Cairn-Y Findings.

The agent may improve report wording and organize already recorded evidence. It
cannot create, remove, confirm, reject, or reclassify a vulnerability. Native
``findings`` remain the only discovery source of truth.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from cairn.server.db import get_conn
from cairn.server.finding_projection import sync_project_findings
from cairn.server.report_composer_service import (
    _extract_json_payload,
    _request_model_json,
    _resolve_report_composer_profile,
)

_POLL_SECONDS = 5.0
_stop = threading.Event()
_wake = threading.Event()
_thread: threading.Thread | None = None
_successful_fingerprints: dict[str, str] = {}


def start_report_agent() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="cairn-report-agent", daemon=True)
    _thread.start()


def stop_report_agent() -> None:
    _stop.set()
    _wake.set()


def request_report_sync() -> None:
    _wake.set()


def _run() -> None:
    while not _stop.is_set():
        try:
            _sync_changed_projects()
        except Exception:
            # Reporting is a projection; it must never block exploration.
            pass
        _wake.wait(_POLL_SECONDS)
        _wake.clear()


def _sync_changed_projects() -> None:
    with get_conn() as conn:
        projects = conn.execute(
            "SELECT id, title FROM projects WHERE status = 'active' ORDER BY created_at"
        ).fetchall()
        for project in projects:
            sync_project_findings(conn, str(project["id"]))

    profile = _resolve_report_composer_profile()
    if profile is None:
        return

    for project in projects:
        if _stop.is_set():
            return
        project_id = str(project["id"])
        material = _load_project_material(project_id, str(project["title"]))
        # Facts alone are never report candidates.
        if not material["findings"]:
            continue
        fingerprint = hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if _successful_fingerprints.get(project_id) == fingerprint:
            continue
        enrichments = _analyze_with_model(material, profile)
        if enrichments is None:
            continue
        _apply_enrichments(project_id, enrichments)
        _successful_fingerprints[project_id] = fingerprint


def _load_project_material(project_id: str, project_name: str) -> dict[str, Any]:
    with get_conn() as conn:
        facts = conn.execute(
            "SELECT id, description FROM facts WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        ).fetchall()
        steps = conn.execute(
            "SELECT id, to_fact_id, description, created_at, concluded_at "
            "FROM steps WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        ).fetchall()
        findings = conn.execute(
            "SELECT id, title, description, severity, kind, data_json, fact_id, created_at "
            "FROM findings WHERE project_id = ? "
            "AND kind IN ('security_vulnerability', 'vulnerability') "
            "AND severity IN ('critical', 'high', 'medium', 'low') ORDER BY created_at",
            (project_id,),
        ).fetchall()
        artifacts = conn.execute(
            "SELECT id, fact_id, kind, filename FROM report_evidence_artifacts "
            "WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return {
        "project": {"id": project_id, "name": project_name},
        "facts": [dict(row) for row in facts],
        "steps": [dict(row) for row in steps if row["to_fact_id"]],
        "findings": [
            {
                **{key: row[key] for key in row.keys() if key != "data_json"},
                # Existing report wording is output state, not new discovery
                # material. Excluding it keeps the fingerprint stable.
                "data": {
                    key: value
                    for key, value in _json_object(row["data_json"]).items()
                    if key != "report_enrichment"
                },
            }
            for row in findings
        ],
        "evidence_artifacts": [dict(row) for row in artifacts],
    }


def _json_object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _analyze_with_model(material: dict[str, Any], profile) -> list[dict[str, Any]] | None:
    system = (
        "你是独立的渗透测试报告整理 Agent。输入中的 findings 已由执行阶段确认，是唯一允许整理的漏洞集合。"
        "不得从 Facts 推测或新增漏洞，不得删除漏洞，不得改变 severity。只整理已有证据，禁止伪造请求、响应或截图。"
        "仅返回 JSON。"
    )
    user = (
        "对每个输入 Finding 输出一条同 finding_id 的报告增强记录，格式为 "
        '{"enrichments":[{"finding_id":"v001","title":"...","description":"...",'
        '"location":"...","proof":"...","impact":"...","remediation":["..."],'
        '"proof_packets":[{"title":"...","request":"...","response":"...","note":"..."}],'
        '"evidence_status":"complete|needs_proof"}]}。'
        "缺少的原始数据保持为空，不得补造。\n\n"
        + json.dumps(material, ensure_ascii=False, separators=(",", ":"))
    )
    content = _request_model_json(system=system, user=user, profile=profile)
    payload = _extract_json_payload(content or "")
    if not isinstance(payload, dict) or not isinstance(payload.get("enrichments"), list):
        return None
    return [item for item in payload["enrichments"] if isinstance(item, dict)]


def _apply_enrichments(project_id: str, enrichments: list[dict[str, Any]]) -> None:
    with get_conn() as conn:
        allowed = {
            str(row["id"]): row
            for row in conn.execute(
                "SELECT id, data_json FROM findings WHERE project_id = ? "
                "AND kind IN ('security_vulnerability', 'vulnerability')",
                (project_id,),
            ).fetchall()
        }
        for item in enrichments:
            finding_id = str(item.get("finding_id") or "").strip()
            if finding_id not in allowed:
                continue
            enrichment: dict[str, Any] = {}
            for key in ("title", "description", "location", "proof", "impact"):
                value = str(item.get(key) or "").strip()
                if value:
                    enrichment[key] = value
            remediation = item.get("remediation")
            if isinstance(remediation, list):
                enrichment["remediation"] = [
                    str(value).strip() for value in remediation if str(value).strip()
                ]
            proof_packets = item.get("proof_packets")
            if isinstance(proof_packets, list):
                enrichment["proof_packets"] = [
                    packet for packet in proof_packets if isinstance(packet, dict)
                ]
            evidence_status = str(item.get("evidence_status") or "").strip()
            if evidence_status in {"complete", "needs_proof"}:
                enrichment["evidence_status"] = evidence_status

            data = _json_object(allowed[finding_id]["data_json"])
            data["report_enrichment"] = enrichment
            conn.execute(
                "UPDATE findings SET data_json = ? WHERE project_id = ? AND id = ?",
                (
                    json.dumps(data, ensure_ascii=False),
                    project_id,
                    finding_id,
                ),
            )
        # Rebuild the product view from the same native Finding state in the
        # same transaction so enrichment survives every later projection pass.
        sync_project_findings(conn, project_id)
