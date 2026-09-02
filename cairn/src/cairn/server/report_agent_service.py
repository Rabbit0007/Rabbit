"""Isolated model-driven report agent.

The agent observes the fact graph and maintains the report finding projection.
It never participates in dispatching, worker scheduling, project pause/resume,
or intent conclusion.  Failures are contained here and cannot block the core
exploration workflow.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

from cairn.server.db import get_conn
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
    """Wake the independent agent without coupling callers to model latency."""
    _wake.set()


def _run() -> None:
    while not _stop.is_set():
        try:
            _sync_changed_projects()
        except Exception:
            # The report projection is best-effort. Core project execution must
            # remain healthy even if the model provider or report DB is down.
            pass
        _wake.wait(_POLL_SECONDS)
        _wake.clear()


def _sync_changed_projects() -> None:
    profile = _resolve_report_composer_profile()
    if profile is None:
        return
    with get_conn() as conn:
        # A paused/stopped project is immutable from the report agent's point
        # of view: its already discovered findings remain available and must
        # not be regenerated merely because the server restarted.
        project_rows = conn.execute(
            "SELECT id, title FROM projects WHERE status = 'active' ORDER BY created_at"
        ).fetchall()
    for project in project_rows:
        if _stop.is_set():
            return
        project_id = str(project["id"])
        material = _load_project_material(project_id, str(project["title"]))
        fingerprint = hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if _successful_fingerprints.get(project_id) == fingerprint:
            continue
        findings = _analyze_with_model(material, profile)
        if findings is None:
            continue
        if _replace_project_findings(project_id, findings):
            _successful_fingerprints[project_id] = fingerprint


def _load_project_material(project_id: str, project_name: str) -> dict[str, Any]:
    with get_conn() as conn:
        facts = conn.execute(
            "SELECT id, description FROM facts WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        ).fetchall()
        intents = conn.execute(
            """
            SELECT id, to_fact_id, description, created_at, concluded_at
            FROM intents WHERE project_id = ? ORDER BY rowid
            """,
            (project_id,),
        ).fetchall()
        artifacts = conn.execute(
            "SELECT id, fact_id, kind, filename FROM report_evidence_artifacts WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return {
        "project": {"id": project_id, "name": project_name},
        "facts": [{"id": row["id"], "description": row["description"]} for row in facts],
        "events": [
            {
                "id": row["id"],
                "result_fact_id": row["to_fact_id"],
                "task": row["description"],
                "started_at": row["created_at"],
                "finished_at": row["concluded_at"],
            }
            for row in intents
            if row["to_fact_id"]
        ],
        "evidence_artifacts": [dict(row) for row in artifacts],
    }


def _analyze_with_model(material: dict[str, Any], profile) -> list[dict[str, Any]] | None:
    system = (
        "你是独立的渗透测试报告 Agent。只整理输入事实中已经被实际验证成立的漏洞，"
        "不得重新测试目标，不得依据漏洞关键词猜测，不得把失败、设想、待验证结论列为漏洞。"
        "合并同一漏洞的多条证据。输出面向客户，禁止出现内部任务、时间线、Fact、Worker、Agent 等信息。"
        "仅返回 JSON，不要 Markdown。"
    )
    user = (
        "根据下面项目事实输出 {\"vulnerabilities\": [...]}。每个漏洞必须包含："
        "source_fact_ids(真实事实ID数组，仅用于系统内部关联)、title、severity(critical/high/medium/low)、"
        "description、location、proof、impact、remediation(字符串数组)、"
        "proof_packets(数组，每项含title/request/response/note)、proof_screenshots(已有截图文件名数组)、"
        "evidence_status(complete 或 needs_proof)。"
        "proof 必须明确说明漏洞如何成立；事实中有请求或响应时必须原样整理进 proof_packets，"
        "不允许伪造缺失的请求头、参数、响应或截图。漏洞已经被事实明确验证成立时，即使尚未保存原始包或截图也必须输出，"
        "并把 evidence_status 设为 needs_proof；证据不完整只影响报告提示，不能让已确认漏洞从列表消失。\n\n"
        + json.dumps(material, ensure_ascii=False, separators=(",", ":"))
    )
    content = _request_model_json(system=system, user=user, profile=profile)
    payload = _extract_json_payload(content or "")
    if not isinstance(payload, dict) or not isinstance(payload.get("vulnerabilities"), list):
        return None
    return [item for item in payload["vulnerabilities"] if isinstance(item, dict)]


def _replace_project_findings(
    project_id: str,
    findings: list[dict[str, Any]],
    *,
    allow_paused: bool = False,
) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        project = conn.execute("SELECT status FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not project or (project["status"] != "active" and not allow_paused):
            return False
        existing = {
            row["fact_id"]: (row["status"], row["discovered_at"])
            for row in conn.execute(
                "SELECT fact_id, status, discovered_at FROM vulnerabilities WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        }
        keep: list[str] = []
        for item in findings:
            source_ids = [str(value).strip() for value in item.get("source_fact_ids") or [] if str(value).strip()]
            if not source_ids:
                continue
            primary = source_ids[0]
            severity = str(item.get("severity") or "medium").lower()
            if severity not in {"critical", "high", "medium", "low"}:
                severity = "medium"
            title = str(item.get("title") or "已确认安全漏洞").strip()
            description = str(item.get("description") or item.get("proof") or "").strip()
            if not description:
                continue
            proof_packets = item.get("proof_packets") if isinstance(item.get("proof_packets"), list) else []
            evidence = [
                value
                for value in (
                    str(item.get("location") or "").strip(),
                    str(item.get("proof") or "").strip(),
                    str(item.get("impact") or "").strip(),
                    *[f"证明截图：{value}" for value in item.get("proof_screenshots") or []],
                    "证据待补全：需要补充原始请求/响应数据包或证明截图"
                    if item.get("evidence_status") == "needs_proof"
                    else "",
                    *[str(value).strip() for value in item.get("remediation") or []],
                )
                if value
            ]
            status, discovered_at = existing.get(primary, ("confirmed", now))
            vuln_id = f"vuln_{project_id}_{hashlib.sha1(primary.encode()).hexdigest()[:12]}"
            conn.execute(
                """
                INSERT INTO vulnerabilities
                    (id, project_id, fact_id, title, description, severity, discovered_at,
                     source_fact_ids_json, evidence_json, proof_packets_json, process_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, fact_id) DO UPDATE SET
                    title=excluded.title, description=excluded.description,
                    severity=excluded.severity, source_fact_ids_json=excluded.source_fact_ids_json,
                    evidence_json=excluded.evidence_json,
                    proof_packets_json=excluded.proof_packets_json,
                    process_json=excluded.process_json
                """,
                (
                    vuln_id, project_id, primary, title, description, severity, discovered_at,
                    json.dumps(source_ids, ensure_ascii=False),
                    json.dumps(evidence, ensure_ascii=False),
                    json.dumps(proof_packets, ensure_ascii=False),
                    json.dumps([{"type": "proof_packet", **packet} for packet in proof_packets], ensure_ascii=False),
                    status,
                ),
            )
            keep.append(primary)
        if keep:
            placeholders = ",".join("?" for _ in keep)
            conn.execute(
                f"DELETE FROM vulnerabilities WHERE project_id = ? AND fact_id NOT IN ({placeholders})",
                (project_id, *keep),
            )
        else:
            conn.execute("DELETE FROM vulnerabilities WHERE project_id = ?", (project_id,))
    return True
