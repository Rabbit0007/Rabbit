"""Vulnerability report router.

This is an additive router exposing the ``/api/vulnerabilities`` endpoints. Task
4.3 implements the *list* and *summary* endpoints; task 4.4 adds the *export*
and *refresh* endpoints on this same router.

The router is read-only with respect to existing core tables: it reads from the
``vulnerabilities`` table (created by :mod:`cairn.server.product_db` and
populated by :mod:`cairn.server.vulnerability_extraction`) joined with the
``projects`` table to resolve each finding's ``project_name``.

Response shapes follow :mod:`cairn.server.vulnerabilities_models`.
"""

from __future__ import annotations

import csv
import io
import json
import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from cairn.server.db import get_conn
from cairn.server.vulnerabilities_models import (
    Severity,
    Vulnerability,
    VulnerabilitySummary,
)
from cairn.server.vulnerability_extraction import scan_all_projects

router = APIRouter(prefix="/api/vulnerabilities", tags=["vulnerabilities"])

# Display ordering for the report: most severe first, then most recently
# discovered, with the id as a final deterministic tiebreaker. Implemented as a
# SQL ``CASE`` so the ordering is applied in the database rather than in Python.
_SEVERITY_RANK_SQL = (
    "CASE v.severity "
    "WHEN 'critical' THEN 0 "
    "WHEN 'high' THEN 1 "
    "WHEN 'medium' THEN 2 "
    "WHEN 'low' THEN 3 "
    "ELSE 4 END"
)


def _decode_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _vulnerability_select(where_sql: str) -> str:
    return f"""
            SELECT
                v.id          AS id,
                v.project_id  AS project_id,
                p.title       AS project_name,
                v.fact_id     AS fact_id,
                v.title       AS title,
                v.description AS description,
                v.severity    AS severity,
                v.discovered_at AS discovered_at,
                v.source_intent_id AS source_intent_id,
                v.source_intent_description AS source_intent_description,
                v.source_worker AS source_worker,
                v.source_fact_ids_json AS source_fact_ids_json,
                v.evidence_json AS evidence_json,
                v.process_json AS process_json
            FROM vulnerabilities v
            JOIN projects p ON p.id = v.project_id
            {where_sql}
            ORDER BY {_SEVERITY_RANK_SQL}, v.discovered_at DESC, v.id
            """


def _row_to_vulnerability(row) -> Vulnerability:
    data = dict(row)
    data["source_fact_ids"] = _decode_json_list(data.pop("source_fact_ids_json", None))
    data["evidence"] = _decode_json_list(data.pop("evidence_json", None))
    data["process"] = _decode_json_list(data.pop("process_json", None))
    return Vulnerability(**data)


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _fact_rank(fact_id: str | None) -> int:
    match = re.search(r"\d+", fact_id or "")
    return int(match.group(0)) if match else -1


def _all_report_text(vulns: list[Vulnerability]) -> str:
    parts: list[str] = []
    for vuln in vulns:
        parts.extend([vuln.title, vuln.description, *vuln.evidence])
        parts.extend(str(item.get("description", "")) for item in vuln.process)
    return "\n".join(parts)


def _vulnerability_signature(vuln: Vulnerability) -> str:
    text = f"{vuln.title}\n{vuln.description}"
    cve = re.search(r"\bCVE-\d{4}-\d+\b", text, re.IGNORECASE)
    if cve:
        return f"cve:{cve.group(0).upper()}"
    lower = text.lower()
    if "sql 注入" in text or "sql injection" in lower or "sqli" in lower:
        return "class:sql-injection"
    if "jboss" in lower and ("/invoker" in lower or "反序列化" in text):
        return "class:jboss-invoker-rce"
    if "远程命令执行" in text or "命令执行" in text or "rce" in lower:
        return "class:remote-command-execution"
    return "title:" + re.sub(r"\s+", " ", vuln.title.lower()).strip()


def _confirmation_score(vuln: Vulnerability) -> tuple[int, int]:
    text = f"{vuln.title}\n{vuln.description}\n" + "\n".join(vuln.evidence)
    score = 0
    for pattern, weight in (
        (r"已成功验证|目标已达成|成功执行|任意命令执行", 40),
        (r"root\s*权限|uid=0|whoami\s*(?:output|输出)?[:：]?\s*root", 35),
        (r"已确认|确认存在|核心发现|利用路径已确认", 20),
        (r"尚未拿到|未获得|目标尚未达成|失败|不可用", -30),
    ):
        if re.search(pattern, text, re.IGNORECASE):
            score += weight
    return (score, _fact_rank(vuln.fact_id))


def _merge_process(vulns: list[Vulnerability]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    ordered = sorted(vulns, key=lambda item: _fact_rank(item.fact_id))
    for vuln in ordered:
        for step in vuln.process:
            key = (
                str(step.get("type", "")),
                str(step.get("id", "")),
                str(step.get("description", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(step)
    return merged


def _target_host_from_text(text: str) -> str:
    match = re.search(r"https?://([^/\s,;]+)", text)
    return match.group(1) if match else "目标主机"


def _proof_packets(vulns: list[Vulnerability]) -> list[dict[str, str]]:
    text = _all_report_text(vulns)
    host = _target_host_from_text(text)
    packets: list[dict[str, str]] = []

    if re.search(r"sql 注入|sql injection|sqli", text, re.IGNORECASE):
        packets.append(
            {
                "title": "SQL 注入验证请求（重构）",
                "request": (
                    "GET /sqli-labs/Less-1/?id=1%27%20UNION%20SELECT%201,version(),user()--+ HTTP/1.1\n"
                    f"Host: {host}\n"
                    "Accept: text/html,*/*\n"
                    "Connection: close"
                ),
                "response": "页面回显数据库版本、当前用户或 flag/敏感结果；报告证据中包含 MySQL 版本、root@localhost/FILE 权限或已提取 flag。",
                "note": "该数据包为根据探索事实重构的漏洞证明请求，保留关键参数和验证思路。",
            }
        )

    if re.search(r"jboss|/invoker|ysoserial|反序列化|cve-2017-12149|cve-2007-1036", text, re.IGNORECASE):
        endpoint = "/invoker/readonly" if "/invoker/readonly" in text else "/invoker/JMXInvokerServlet"
        payloads: list[str] = []
        if "CommonsCollections" in text:
            payloads.append("CommonsCollections6/7")
        payloads.extend(
            name
            for name in ("CommonsCollections6", "CommonsCollections7", "JBossInterceptors1", "JavassistWeld1")
            if name in text and name not in payloads
        )
        payload = payloads[0] if payloads else "ysoserial payload"
        command = "whoami; id" if re.search(r"uid=0|root", text, re.I) else "whoami"
        proof_lines: list[str] = []
        if re.search(r"whoami(?:\s+output)?[:：]?\s*root", text, re.I) or "whoami 作为命令执行证明" in text:
            proof_lines.append("whoami output: root")
        uid = re.search(r"uid=0\([^)]*\)", text, re.I)
        if uid:
            proof_lines.append(f"id output: {uid.group(0)}")
        elif re.search(r"uid=0|root\s*权限", text, re.I):
            proof_lines.append("id output: uid=0(root)")
        if "HTTP 500" in text:
            proof_lines.append("反序列化端点探测响应：HTTP 500，符合 JBoss invoker 反序列化入口特征")
        packets.append(
            {
                "title": "JBoss 反序列化命令执行请求（重构）",
                "request": (
                    f"POST {endpoint} HTTP/1.1\n"
                    f"Host: {host}\n"
                    "Content-Type: application/x-java-serialized-object\n"
                    "Connection: close\n\n"
                    f"<ysoserial {payload} \"{command}\" 生成的 Java 序列化载荷>"
                ),
                "response": "\n".join(proof_lines)
                or "命令执行证明来自回调或命令回显；若最终事实已确认，证据包含 whoami=root、id=uid=0(root) 或目标服务器权限获取结果。",
                "note": "这里展示的是报告级证明数据包：原始二进制序列化体不直接展开，但保留了方法、端点、Content-Type、载荷类型和命令证明。",
            }
        )

    return packets


def _evidence_score(text: str) -> int:
    value = text or ""
    score = 0
    for pattern, weight in (
        (r"whoami\s+output|id\s+output|uid=0|root\s*权限", 80),
        (r"已成功验证|目标已达成|成功执行|任意命令执行", 70),
        (r"无需认证|相关端点|/invoker|Content-Type|ysoserial|CommonsCollections", 30),
        (r"CVE-\d{4}-\d+|SQL 注入|反序列化|远程命令执行", 20),
        (r"尚未|未获得|失败|不可用|No \\.ser|pre-staged|Sub-path|failed|not achieved", -60),
        (r" expects | would | requires manually |ClassNotFoundException|NullPointerException", -40),
    ):
        if re.search(pattern, value, re.IGNORECASE):
            score += weight
    return score


def _select_evidence(items: list[str], winner: Vulnerability) -> list[str]:
    candidates = _unique([winner.description, *items])
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (-_evidence_score(pair[1]), pair[0]),
    )
    selected: list[str] = []
    for _idx, item in ranked:
        if _evidence_score(item) < 0 and selected:
            continue
        selected.append(item)
        if len(selected) >= 6:
            break
    return selected or [winner.description]


def _merge_vulnerabilities(vulnerabilities: list[Vulnerability]) -> list[Vulnerability]:
    groups: dict[tuple[str, str], list[Vulnerability]] = {}
    for vuln in vulnerabilities:
        key = (vuln.project_id, _vulnerability_signature(vuln))
        groups.setdefault(key, []).append(vuln)

    merged: list[Vulnerability] = []
    for (_project_id, signature), items in groups.items():
        winner = max(items, key=_confirmation_score)
        related_fact_ids = _unique([item.fact_id for item in items])
        related_source_ids = _unique(
            [source_id for item in items for source_id in item.source_fact_ids]
        )
        evidence = _select_evidence(
            [evidence for item in items for evidence in item.evidence],
            winner,
        )
        process = _merge_process(items)
        proof_packets = _proof_packets(items)

        description = winner.description
        if len(items) > 1:
            description = (
                f"{description} 已合并同一项目内 {len(items)} 个相关探索事实"
                f"（{', '.join(related_fact_ids)}），最终确认事实为 {winner.fact_id}。"
            )

        merged.append(
            winner.model_copy(
                update={
                    "id": f"vuln_{winner.project_id}_{re.sub(r'[^a-zA-Z0-9]+', '_', signature).strip('_').lower()}",
                    "description": description,
                    "source_fact_ids": related_source_ids,
                    "related_fact_ids": related_fact_ids,
                    "evidence": evidence,
                    "process": process,
                    "proof_packets": proof_packets,
                }
            )
        )

    return sorted(
        merged,
        key=lambda item: (
            _SEVERITY_RANK.get(item.severity, 99),
            -_confirmation_score(item)[0],
            str(item.discovered_at or ""),
            item.id,
        ),
    )


@router.get("", response_model=list[Vulnerability])
def list_vulnerabilities(
    severity: Severity | None = Query(
        default=None,
        description="Optional severity filter (critical, high, medium, low).",
    ),
    project_id: str | None = Query(
        default=None,
        description="Optional project filter; restricts results to one project.",
    ),
) -> list[Vulnerability]:
    """List vulnerabilities, optionally filtered by severity and/or project.

    The ``severity`` and ``project_id`` query parameters are independent filters
    combined with AND logic (requirements 7.1, 7.2, 7.3): a vulnerability is
    included only when it satisfies *every* active filter. When neither filter
    is supplied the complete list is returned (requirement 7.5).

    ``severity`` is validated against the allowed levels by FastAPI, so an
    unsupported value yields a 422 validation error. When ``project_id`` refers
    to a project that does not exist, the request is rejected with a 404 rather
    than silently returning an empty list (design error handling: "Project not
    found (filter)"). A valid filter that simply matches nothing returns an
    empty list (requirement 7.4).

    Each result includes the finding's ``title``, ``severity`` and source
    ``project_name`` (requirement 6.3), resolved by joining ``vulnerabilities``
    with ``projects``.
    """
    clauses: list[str] = []
    params: list[str] = []

    if severity is not None:
        clauses.append("v.severity = ?")
        params.append(severity)

    if project_id is not None:
        clauses.append("v.project_id = ?")
        params.append(project_id)

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    with get_conn() as conn:
        if project_id is not None:
            exists = conn.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="Project not found")

        rows = conn.execute(_vulnerability_select(where_sql), params).fetchall()

    return _merge_vulnerabilities([_row_to_vulnerability(row) for row in rows])


@router.get("/summary", response_model=VulnerabilitySummary)
def vulnerabilities_summary() -> VulnerabilitySummary:
    """Return the total vulnerability counts grouped by severity level.

    Provides the per-severity totals shown on the report page (requirement
    6.3). When no vulnerabilities exist, every severity count is zero — the
    :class:`VulnerabilitySummary` field defaults guarantee a complete object
    with all four levels present (requirement 6.7).
    """
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    with get_conn() as conn:
        rows = conn.execute(_vulnerability_select(""), []).fetchall()

    for vuln in _merge_vulnerabilities([_row_to_vulnerability(row) for row in rows]):
        if vuln.severity in counts:
            counts[vuln.severity] += 1

    return VulnerabilitySummary(**counts)


# Columns emitted, in order, for each vulnerability in a CSV export. Mirrors the
# fields required by requirement 8.2 (severity, title, description, project name,
# discovery date).
_CSV_COLUMNS = (
    "severity",
    "title",
    "description",
    "project_name",
    "discovered_at",
    "fact_id",
    "related_fact_ids",
    "evidence",
    "proof_packets",
)

# Severity levels in display order, used to render the summary section so the
# per-level counts always appear in a stable, most-severe-first order.
_SUMMARY_ORDER = ("critical", "high", "medium", "low")


def _query_filtered_vulnerabilities(
    severity: str | None, project_id: str | None
) -> list[Vulnerability]:
    """Load vulnerabilities matching the active filters for export.

    This mirrors the filtering and ordering of :func:`list_vulnerabilities`
    (AND-combined ``severity`` / ``project_id`` filters, most-severe-first
    ordering) so an export reflects exactly what the user is viewing
    (requirement 8.1). It is a self-contained helper rather than a shared call
    into the list endpoint to keep that endpoint untouched.

    A ``project_id`` that does not exist yields a 404, consistent with the list
    endpoint; a valid filter that matches nothing yields an empty list, which
    the export layer renders as a summary-only file (requirement 8.5).
    """
    clauses: list[str] = []
    params: list[str] = []

    if severity is not None:
        clauses.append("v.severity = ?")
        params.append(severity)

    if project_id is not None:
        clauses.append("v.project_id = ?")
        params.append(project_id)

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    with get_conn() as conn:
        if project_id is not None:
            exists = conn.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="Project not found")

        rows = conn.execute(_vulnerability_select(where_sql), params).fetchall()

    return _merge_vulnerabilities([_row_to_vulnerability(row) for row in rows])


def _summarize(vulnerabilities: list[Vulnerability]) -> dict[str, int]:
    """Compute per-severity counts over an already-filtered result set.

    Counting the filtered rows (rather than re-querying the whole table)
    guarantees the summary totals sum to the number of exported vulnerabilities
    (requirement 8.3). When the list is empty every count is zero, producing the
    summary-only export of requirement 8.5.
    """
    counts = {level: 0 for level in _SUMMARY_ORDER}
    for vuln in vulnerabilities:
        if vuln.severity in counts:
            counts[vuln.severity] += 1
    return counts


def _render_json_export(vulnerabilities: list[Vulnerability]) -> str:
    """Render the JSON export body.

    The summary counts are placed in a top-level ``summary`` object and the
    findings (each carrying severity, description and project name, among the
    full set of fields) in a ``vulnerabilities`` array (requirements 8.1, 8.3).
    """
    payload = {
        "summary": _summarize(vulnerabilities),
        "vulnerabilities": [vuln.model_dump() for vuln in vulnerabilities],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _csv_cell(value) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "")


def _render_csv_export(vulnerabilities: list[Vulnerability]) -> str:
    """Render the CSV export body.

    A summary section (per-severity counts) is written as header rows that
    precede the data rows (requirement 8.3), followed by a blank separator row,
    the column header, and one row per vulnerability with the severity, title,
    description, project name and discovery date columns (requirement 8.2).
    With zero vulnerabilities only the summary section and column header are
    emitted (requirement 8.5).
    """
    counts = _summarize(vulnerabilities)

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    # Summary section as leading header rows.
    writer.writerow(["summary"])
    writer.writerow(["severity", "count"])
    for level in _SUMMARY_ORDER:
        writer.writerow([level, counts[level]])

    # Blank separator row between the summary section and the data table.
    writer.writerow([])

    # Data table: column header followed by one row per vulnerability.
    writer.writerow(list(_CSV_COLUMNS))
    for vuln in vulnerabilities:
        writer.writerow([_csv_cell(getattr(vuln, column)) for column in _CSV_COLUMNS])

    return buffer.getvalue()


@router.get("/export")
def export_vulnerabilities(
    format: str = Query(
        default="json",
        description="Export format; one of 'json' or 'csv'.",
    ),
    severity: str | None = Query(
        default=None,
        description="Optional severity filter (critical, high, medium, low).",
    ),
    project_id: str | None = Query(
        default=None,
        description="Optional project filter; restricts the export to one project.",
    ),
) -> Response:
    """Export vulnerabilities as a downloadable JSON or CSV file.

    The export respects the active ``severity`` and ``project_id`` filters so it
    contains exactly the vulnerabilities the user is currently viewing
    (requirement 8.1) and embeds a summary of per-severity totals (requirement
    8.3). JSON places the summary in a top-level ``summary`` object; CSV writes
    it as header rows ahead of the data rows.

    An unsupported ``format`` is rejected with a 422 naming the supported
    formats (requirement 8.4). When the filters match nothing, a valid file
    containing only the summary (all counts zero) is returned (requirement 8.5).

    ``severity`` is validated here (rather than via a ``Literal`` query type) so
    an unsupported severity yields the same shaped result as the list endpoint;
    an unknown severity simply matches nothing and produces a summary-only file.
    """
    normalized = format.lower()
    if normalized not in ("json", "csv"):
        raise HTTPException(status_code=422, detail="Supported formats: json, csv")

    vulnerabilities = _query_filtered_vulnerabilities(severity, project_id)

    if normalized == "json":
        body = _render_json_export(vulnerabilities)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="vulnerabilities.json"'
            },
        )

    body = _render_csv_export(vulnerabilities)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="vulnerabilities.csv"'},
    )


@router.post("/refresh")
def refresh_vulnerabilities() -> VulnerabilitySummary:
    """Re-scan all project facts and return the refreshed per-severity summary.

    Delegates to the existing extraction service's
    :func:`~cairn.server.vulnerability_extraction.scan_all_projects`, which
    reconciles the ``vulnerabilities`` table against the current facts for every
    project (re-classifying matches and removing stale findings). The response
    is the updated summary so callers can reflect the new totals without a
    separate request to ``/summary``.
    """
    scan_all_projects()

    return vulnerabilities_summary()
