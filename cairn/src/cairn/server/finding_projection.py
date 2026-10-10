"""Project native Cairn-Y Findings into Rabbit's vulnerability product view.

``findings`` is the source of truth.  The ``vulnerabilities`` table is only a
denormalized UI/report projection and is never allowed to invent a finding.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


VULNERABILITY_KINDS = {"security_vulnerability", "vulnerability"}
REPORTABLE_SEVERITIES = {"critical", "high", "medium", "low"}


def _json_object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_list(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _producer(conn: sqlite3.Connection, project_id: str, fact_id: str | None):
    if not fact_id:
        return None
    return conn.execute(
        "SELECT id, description, creator, worker, created_at, concluded_at "
        "FROM steps WHERE project_id = ? AND to_fact_id = ? "
        "ORDER BY concluded_at DESC, created_at DESC LIMIT 1",
        (project_id, fact_id),
    ).fetchone()


def _source_fact_ids(
    conn: sqlite3.Connection, project_id: str, step_id: str | None
) -> list[str]:
    if not step_id:
        return []
    return [
        str(row["fact_id"])
        for row in conn.execute(
            "SELECT fact_id FROM step_sources WHERE project_id = ? AND step_id = ? ORDER BY rowid",
            (project_id, step_id),
        ).fetchall()
    ]


def _process(
    conn: sqlite3.Connection, project_id: str, fact_id: str | None
) -> list[dict[str, str]]:
    if not fact_id:
        return []
    fact = conn.execute(
        "SELECT description FROM facts WHERE project_id = ? AND id = ?",
        (project_id, fact_id),
    ).fetchone()
    producer = _producer(conn, project_id, fact_id)
    result: list[dict[str, str]] = []
    if producer is not None:
        result.append(
            {
                "type": "step",
                "id": str(producer["id"]),
                "label": "验证步骤",
                "description": str(producer["description"]),
                "worker": str(producer["worker"] or producer["creator"]),
                "time": str(producer["concluded_at"] or producer["created_at"]),
            }
        )
    if fact is not None:
        item = {
            "type": "fact",
            "id": fact_id,
            "label": "确认事实",
            "description": str(fact["description"]),
        }
        if producer is not None:
            item["worker"] = str(producer["worker"] or producer["creator"])
            item["time"] = str(producer["concluded_at"] or producer["created_at"])
        result.append(item)
    return result


def _evidence(description: str, data: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for value in data.get("evidence") or []:
        text = str(value or "").strip()
        if text and text not in items:
            items.append(text)
    for value in (
        data.get("location"),
        data.get("proof"),
        data.get("impact"),
    ):
        text = str(value or "").strip()
        if text and text not in items:
            items.append(text)
    for value in data.get("remediation") or []:
        text = str(value or "").strip()
        if text and text not in items:
            items.append(text)
    if not items:
        items.append(description)
    return items


def sync_finding(
    conn: sqlite3.Connection, project_id: str, finding_id: str
) -> bool:
    row = conn.execute(
        "SELECT * FROM findings WHERE project_id = ? AND id = ?",
        (project_id, finding_id),
    ).fetchone()
    if row is None:
        return False
    if str(row["kind"]).lower() not in VULNERABILITY_KINDS:
        conn.execute(
            "DELETE FROM vulnerabilities WHERE project_id = ? AND finding_id = ?",
            (project_id, finding_id),
        )
        return False
    severity = str(row["severity"]).lower()
    if severity not in REPORTABLE_SEVERITIES:
        conn.execute(
            "DELETE FROM vulnerabilities WHERE project_id = ? AND finding_id = ?",
            (project_id, finding_id),
        )
        return False
    fact_id = str(row["fact_id"] or "").strip()
    if not fact_id:
        conn.execute(
            "DELETE FROM vulnerabilities WHERE project_id = ? AND finding_id = ?",
            (project_id, finding_id),
        )
        return False

    data = _json_object(row["data_json"])
    enrichment = data.get("report_enrichment")
    if not isinstance(enrichment, dict):
        enrichment = {}
    title = str(enrichment.get("title") or row["title"]).strip()
    description = str(enrichment.get("description") or row["description"]).strip()
    producer = _producer(conn, project_id, fact_id)
    step_id = str(producer["id"]) if producer is not None else None
    source_ids = _source_fact_ids(conn, project_id, step_id)
    evidence_data = dict(data)
    for key in ("location", "proof", "impact", "remediation"):
        if enrichment.get(key):
            evidence_data[key] = enrichment[key]
    if enrichment.get("evidence_status") == "needs_proof":
        existing_evidence = evidence_data.get("evidence")
        if not isinstance(existing_evidence, list):
            existing_evidence = []
        evidence_data["evidence"] = [
            *existing_evidence,
            "证据待补全：需要补充原始数据包或证明截图",
        ]
    proof_packets = enrichment.get("proof_packets", data.get("proof_packets"))
    if not isinstance(proof_packets, list):
        proof_packets = []
    vulnerability_id = f"vuln_{project_id}_{hashlib.sha1(finding_id.encode()).hexdigest()[:12]}"

    conn.execute(
        """
        INSERT INTO vulnerabilities
            (id, project_id, fact_id, finding_id, title, description, severity,
             discovered_at, source_intent_id, source_intent_description,
             source_worker, source_fact_ids_json, evidence_json,
             proof_packets_json, process_json, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed')
        ON CONFLICT(project_id, finding_id) DO UPDATE SET
            fact_id=excluded.fact_id,
            title=excluded.title,
            description=excluded.description,
            severity=excluded.severity,
            discovered_at=excluded.discovered_at,
            source_intent_id=excluded.source_intent_id,
            source_intent_description=excluded.source_intent_description,
            source_worker=excluded.source_worker,
            source_fact_ids_json=excluded.source_fact_ids_json,
            evidence_json=excluded.evidence_json,
            proof_packets_json=excluded.proof_packets_json,
            process_json=excluded.process_json
        """,
        (
            vulnerability_id,
            project_id,
            fact_id,
            finding_id,
            title,
            description,
            severity,
            str(row["created_at"]),
            step_id,
            str(producer["description"]) if producer is not None else None,
            str(producer["worker"] or producer["creator"]) if producer is not None else None,
            json.dumps(source_ids, ensure_ascii=False),
            json.dumps(_evidence(description, evidence_data), ensure_ascii=False),
            json.dumps(proof_packets, ensure_ascii=False),
            json.dumps(_process(conn, project_id, fact_id), ensure_ascii=False),
        ),
    )
    return True


def sync_project_findings(conn: sqlite3.Connection, project_id: str) -> int:
    rows = conn.execute(
        "SELECT id FROM findings WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    keep: list[str] = []
    for row in rows:
        finding_id = str(row["id"])
        if sync_finding(conn, project_id, finding_id):
            keep.append(finding_id)
    if keep:
        placeholders = ",".join("?" for _ in keep)
        conn.execute(
            f"DELETE FROM vulnerabilities WHERE project_id = ? AND finding_id IS NOT NULL "
            f"AND finding_id NOT IN ({placeholders})",
            (project_id, *keep),
        )
    else:
        conn.execute(
            "DELETE FROM vulnerabilities WHERE project_id = ? AND finding_id IS NOT NULL",
            (project_id,),
        )
    return len(keep)


def migrate_legacy_vulnerabilities(conn: sqlite3.Connection) -> int:
    """Materialize historical report rows as native Findings once."""
    rows = conn.execute(
        "SELECT * FROM vulnerabilities WHERE finding_id IS NULL ORDER BY project_id, discovered_at"
    ).fetchall()
    migrated = 0
    for row in rows:
        fact_id = str(row["fact_id"] or "").strip()
        if not fact_id:
            continue
        existing = conn.execute(
            "SELECT id FROM findings WHERE project_id = ? AND fact_id = ? "
            "AND kind IN ('security_vulnerability', 'vulnerability') LIMIT 1",
            (row["project_id"], fact_id),
        ).fetchone()
        if existing is not None:
            finding_id = str(existing["id"])
        else:
            finding_id = "legacy_" + hashlib.sha1(str(row["id"]).encode()).hexdigest()[:12]
            data = {
                "legacy_vulnerability_id": row["id"],
                "evidence": _json_list(row["evidence_json"]),
                "proof_packets": _json_list(row["proof_packets_json"]),
                "migrated_from_fact_projection": True,
            }
            conn.execute(
                "INSERT OR IGNORE INTO findings "
                "(id, project_id, title, description, severity, kind, data_json, fact_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'security_vulnerability', ?, ?, ?)",
                (
                    finding_id,
                    row["project_id"],
                    row["title"],
                    row["description"],
                    row["severity"],
                    json.dumps(data, ensure_ascii=False),
                    fact_id,
                    row["discovered_at"],
                ),
            )
            migrated += 1
        conn.execute(
            "UPDATE vulnerabilities SET finding_id = ? WHERE id = ?",
            (finding_id, row["id"]),
        )
    return migrated


def sync_all_findings(conn: sqlite3.Connection) -> int:
    total = 0
    for row in conn.execute("SELECT id FROM projects ORDER BY created_at").fetchall():
        total += sync_project_findings(conn, str(row["id"]))
    return total
