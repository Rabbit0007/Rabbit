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
import zipfile
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from datetime import datetime, timezone

from cairn.server.activity_service import record_audit
from cairn.server.db import get_conn
from cairn.server.vulnerabilities_models import (
    ExportRecord,
    Severity,
    Vulnerability,
    VulnerabilitySummary,
    VulnerabilityStatus,
    VulnerabilityStatusUpdate,
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
                COALESCE(v.status, 'confirmed') AS status,
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
                    "status": "confirmed" if any(item.status == "confirmed" for item in items) else "ignored",
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
    status: VulnerabilityStatus | None = Query(
        default=None,
        description="Optional review status filter (confirmed or ignored).",
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

    if status is not None:
        clauses.append("COALESCE(v.status, 'confirmed') = ?")
        params.append(status)

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
    severity: str | None,
    project_id: str | None,
    vulnerability_id: str | None = None,
    vulnerability_ids: list[str] | None = None,
    status: str | None = None,
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

    if status is not None:
        clauses.append("COALESCE(v.status, 'confirmed') = ?")
        params.append(status)

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

    vulnerabilities = _merge_vulnerabilities([_row_to_vulnerability(row) for row in rows])
    if vulnerability_id is not None:
        vulnerabilities = [v for v in vulnerabilities if v.id == vulnerability_id]
        if not vulnerabilities:
            raise HTTPException(status_code=404, detail="Vulnerability not found")
    if vulnerability_ids is not None:
        wanted = set(vulnerability_ids)
        vulnerabilities = [v for v in vulnerabilities if v.id in wanted]
        if not vulnerabilities:
            raise HTTPException(status_code=404, detail="Vulnerabilities not found")
    return vulnerabilities


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


def _md_escape(text: str) -> str:
    return str(text or "").replace("|", "\\|")


def _render_markdown_export(vulnerabilities: list[Vulnerability]) -> str:
    """Render a penetration-test style Markdown report.

    Markdown is the most faithful lightweight format for this product report:
    tables stay readable, request/response proof packets fit naturally in
    fenced code blocks, and users can convert the file to PDF/Word later with a
    dedicated renderer.
    """
    counts = _summarize(vulnerabilities)
    lines: list[str] = [
        f"# {_report_title(vulnerabilities)}",
        "",
        "> Rabbit 自动化安全探索生成的漏洞报告。报告按项目和漏洞组织，包含确认事实、关键证据、证明数据包与漏洞浮现过程。",
        "",
        "## 目录",
        "",
        "- [报告概览](#报告概览)",
        "- [漏洞清单](#漏洞清单)",
    ]
    for project_id, project_name, _items in _project_groups(vulnerabilities):
        anchor = f"项目{project_name}{project_id}".lower()
        lines.append(f"- [项目：{project_name}（{project_id}）](#{anchor})")
    lines.extend(
        [
            "",
            "---",
            "",
        "## 报告概览",
        "",
        "| 指标 | 数量 |",
        "| --- | ---: |",
        f"| 漏洞总数 | {len(vulnerabilities)} |",
        f"| 严重 | {counts['critical']} |",
        f"| 高危 | {counts['high']} |",
        f"| 中危 | {counts['medium']} |",
        f"| 低危 | {counts['low']} |",
        "",
        ]
    )
    if not vulnerabilities:
        lines.extend(["当前范围内没有漏洞。", ""])
        return "\n".join(lines)

    lines.extend(
        [
            "---",
            "",
            "## 漏洞清单",
            "",
            "| ID | 漏洞名称 | 项目 | 严重程度 | 确认事实 | 发现时间 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for index, vuln in enumerate(vulnerabilities, start=1):
        lines.append(
            f"| H-{index:02d} | {_md_escape(vuln.title)} | "
            f"{_md_escape(vuln.project_name)} (`{_md_escape(vuln.project_id)}`) | "
            f"{_SEVERITY_LABELS.get(vuln.severity, vuln.severity)} | "
            f"`{_md_escape(vuln.fact_id)}` | {_md_escape(vuln.discovered_at)} |"
        )
    lines.append("")

    for project_id, project_name, items in _project_groups(vulnerabilities):
        lines.extend(["---", "", f"## 项目：{project_name}（`{project_id}`）", ""])
        for index, vuln in enumerate(items, start=1):
            lines.extend(
                [
                    f"### {index}. {vuln.title}",
                    "",
                    f"> 风险级别：**{_SEVERITY_LABELS.get(vuln.severity, vuln.severity)}**；确认事实：`{_md_escape(vuln.fact_id)}`。",
                    "",
                    "| 字段 | 内容 |",
                    "| --- | --- |",
                    f"| 严重程度 | {_SEVERITY_LABELS.get(vuln.severity, vuln.severity)} |",
                    f"| 确认事实 | `{_md_escape(vuln.fact_id)}` |",
                    f"| 关联事实 | {_md_escape(', '.join(vuln.related_fact_ids or [vuln.fact_id]))} |",
                    f"| 来源意图 | {_md_escape(vuln.source_intent_id or '未记录')} |",
                    f"| 工作节点 | {_md_escape(vuln.source_worker or '未记录')} |",
                    f"| 发现时间 | {_md_escape(vuln.discovered_at)} |",
                    "",
                    "#### 漏洞描述",
                    "",
                    vuln.description or "未记录",
                    "",
                    "#### 关键证据",
                    "",
                ]
            )
            for evidence in vuln.evidence or ["未记录"]:
                lines.append(f"- {evidence}")
            lines.append("")

            lines.extend(["#### 漏洞证明数据包", ""])
            for packet_index, packet in enumerate(vuln.proof_packets or [], start=1):
                lines.extend(
                    [
                        f"**证明 {packet_index}：{packet.get('title') or '漏洞证明'}**",
                        "",
                        "请求数据包：",
                        "",
                        "```http",
                        str(packet.get("request") or "未记录"),
                        "```",
                        "",
                        "响应/回显：",
                        "",
                        "```text",
                        str(packet.get("response") or "未记录"),
                        "```",
                    ]
                )
                if packet.get("note"):
                    lines.extend(["", f"说明：{packet['note']}"])
                lines.append("")

            lines.extend(["#### 漏洞浮现过程", ""])
            for step_index, step in enumerate(vuln.process or [], start=1):
                label = step.get("label") or step.get("type") or "过程"
                step_id = step.get("id") or ""
                worker = f"；节点：{step.get('worker')}" if step.get("worker") else ""
                time = f"；时间：{step.get('time')}" if step.get("time") else ""
                lines.append(
                    f"{step_index}. **{label} `{step_id}`**{worker}{time}："
                    f"{step.get('description') or '无描述'}"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


_SEVERITY_LABELS = {
    "critical": "严重",
    "high": "高危",
    "medium": "中危",
    "low": "低危",
}


def _report_title(vulnerabilities: list[Vulnerability]) -> str:
    if len(vulnerabilities) == 1:
        return f"{vulnerabilities[0].project_name} - 单漏洞验证报告"
    projects = _unique([v.project_name for v in vulnerabilities])
    if len(projects) == 1:
        return f"{projects[0]} - 渗透测试漏洞报告"
    return "Rabbit 渗透测试漏洞报告"


def _project_groups(vulnerabilities: list[Vulnerability]) -> list[tuple[str, str, list[Vulnerability]]]:
    groups: dict[str, tuple[str, list[Vulnerability]]] = {}
    for vuln in vulnerabilities:
        title, items = groups.setdefault(vuln.project_id, (vuln.project_name, []))
        items.append(vuln)
    return [(project_id, title, items) for project_id, (title, items) in groups.items()]


def _export_filename(vulnerabilities: list[Vulnerability], extension: str) -> str:
    if len(vulnerabilities) == 1:
        base = f"{vulnerabilities[0].project_id}-{vulnerabilities[0].fact_id}"
    else:
        projects = _unique([v.project_id for v in vulnerabilities])
        base = projects[0] if len(projects) == 1 else "vulnerabilities"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-") or "vulnerabilities"
    return f"{safe}.{extension}"


@router.patch("/{vulnerability_id}/status", response_model=Vulnerability)
def update_vulnerability_status(
    vulnerability_id: str, payload: VulnerabilityStatusUpdate
) -> Vulnerability:
    """Mark a merged vulnerability as confirmed or ignored.

    The UI shows merged report findings. Updating a merged finding therefore
    applies the requested review state to every raw fact row that contributed to
    that merged report item.
    """
    all_vulnerabilities = _query_filtered_vulnerabilities(None, None)
    target = next((vuln for vuln in all_vulnerabilities if vuln.id == vulnerability_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Vulnerability not found")

    fact_ids = target.related_fact_ids or [target.fact_id]
    placeholders = ",".join("?" for _ in fact_ids)
    with get_conn() as conn:
        conn.execute(
            f"""
            UPDATE vulnerabilities
            SET status = ?
            WHERE project_id = ? AND fact_id IN ({placeholders})
            """,
            [payload.status, target.project_id, *fact_ids],
        )

    status_label = "已忽略" if payload.status == "ignored" else "已确认"
    record_audit(
        "vulnerability.status",
        f"漏洞「{target.title}」标记为{status_label}",
        target_type="vulnerability",
        target_id=vulnerability_id,
    )
    return target.model_copy(update={"status": payload.status})


def _report_lines(vulnerabilities: list[Vulnerability]) -> list[str]:
    counts = _summarize(vulnerabilities)
    lines = [
        "Rabbit 漏洞报告",
        "",
        "报告概览",
        f"严重：{counts['critical']}  高危：{counts['high']}  中危：{counts['medium']}  低危：{counts['low']}",
        f"漏洞总数：{len(vulnerabilities)}",
        "",
    ]
    if not vulnerabilities:
        lines.append("当前筛选条件下没有漏洞。")
        return lines

    for index, vuln in enumerate(vulnerabilities, start=1):
        lines.extend(
            [
                f"{index}. {vuln.title}",
                f"严重程度：{_SEVERITY_LABELS.get(vuln.severity, vuln.severity)}",
                f"项目：{vuln.project_name}（{vuln.project_id}）",
                f"确认事实：{vuln.fact_id}",
                f"关联事实：{', '.join(vuln.related_fact_ids or [vuln.fact_id])}",
                f"发现时间：{vuln.discovered_at}",
                "漏洞描述：",
                vuln.description,
                "关键证据：",
            ]
        )
        for evidence in vuln.evidence or ["未记录"]:
            lines.append(f"- {evidence}")
        if vuln.proof_packets:
            lines.append("漏洞证明数据包：")
            for packet_index, packet in enumerate(vuln.proof_packets, start=1):
                lines.append(f"证明 {packet_index}：{packet.get('title') or '漏洞证明'}")
                lines.append("请求：")
                lines.extend(str(packet.get("request") or "未记录").splitlines())
                lines.append("响应/回显：")
                lines.extend(str(packet.get("response") or "未记录").splitlines())
                note = packet.get("note")
                if note:
                    lines.append(f"说明：{note}")
        if vuln.process:
            lines.append("漏洞浮现过程：")
            for step_index, step in enumerate(vuln.process, start=1):
                step_type = step.get("type", "过程")
                step_id = step.get("id", "")
                desc = step.get("description", "")
                lines.append(f"{step_index}. {step_type} {step_id}：{desc}")
        lines.append("")
    return lines


def _report_plain_lines(vulnerabilities: list[Vulnerability]) -> list[str]:
    counts = _summarize(vulnerabilities)
    lines = [
        _report_title(vulnerabilities),
        "报告概览",
        f"漏洞总数：{len(vulnerabilities)}",
        f"严重：{counts['critical']}    高危：{counts['high']}    中危：{counts['medium']}    低危：{counts['low']}",
        "",
    ]
    if not vulnerabilities:
        return lines + ["当前范围内没有漏洞。"]

    lines.extend(["漏洞清单", "ID | 漏洞名称 | 项目 | 严重程度 | 确认事实", "-" * 72])
    for index, vuln in enumerate(vulnerabilities, start=1):
        lines.append(
            f"{index:02d} | {vuln.title} | {vuln.project_name}({vuln.project_id}) | "
            f"{_SEVERITY_LABELS.get(vuln.severity, vuln.severity)} | {vuln.fact_id}"
        )
    lines.append("")

    for project_id, project_name, items in _project_groups(vulnerabilities):
        lines.extend([f"项目：{project_name}（{project_id}）", "-" * 72])
        for index, vuln in enumerate(items, start=1):
            lines.extend(
                [
                    f"{index}. {vuln.title}",
                    f"严重程度：{_SEVERITY_LABELS.get(vuln.severity, vuln.severity)}",
                    f"确认事实：{vuln.fact_id}",
                    f"关联事实：{', '.join(vuln.related_fact_ids or [vuln.fact_id])}",
                    f"发现时间：{vuln.discovered_at}",
                    "漏洞描述：",
                    vuln.description,
                    "关键证据：",
                ]
            )
            for evidence in vuln.evidence or ["未记录"]:
                lines.append(f"- {evidence}")
            lines.append("漏洞证明数据包：")
            for packet_index, packet in enumerate(vuln.proof_packets or [], start=1):
                lines.extend(
                    [
                        f"证明 {packet_index}：{packet.get('title') or '漏洞证明'}",
                        "请求：",
                        str(packet.get("request") or "未记录"),
                        "响应/回显：",
                        str(packet.get("response") or "未记录"),
                    ]
                )
                if packet.get("note"):
                    lines.append(f"说明：{packet['note']}")
            lines.append("漏洞浮现过程：")
            for step_index, step in enumerate(vuln.process or [], start=1):
                lines.append(
                    f"{step_index}. {step.get('label') or step.get('type') or '过程'} "
                    f"{step.get('id') or ''}：{step.get('description') or '无描述'}"
                )
            lines.append("")
    return lines


def _wrap_report_line(line: str, width: int = 42) -> list[str]:
    if not line:
        return [""]
    chunks: list[str] = []
    current = ""
    units = re.split(r"(\s+)", line)
    for unit in units:
        if not unit:
            continue
        if unit.isspace():
            if current and not current.endswith(" "):
                current += " "
            continue
        while len(unit) > width:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.append(unit[:width])
            unit = unit[width:]
        if len(current) + len(unit) > width:
            if current:
                chunks.append(current.rstrip())
            current = unit
        else:
            current += unit
    if current:
        chunks.append(current.rstrip())
    return chunks or [""]


def _pdf_hex_text(text: str) -> str:
    return text.encode("utf-16-be", errors="replace").hex().upper()


def _render_pdf_export(vulnerabilities: list[Vulnerability]) -> bytes:
    wrapped: list[str] = []
    for line in _report_plain_lines(vulnerabilities):
        wrapped.extend(_wrap_report_line(line, 54))

    lines_per_page = 42
    pages = [wrapped[i : i + lines_per_page] for i in range(0, len(wrapped), lines_per_page)]
    pages = pages or [[_report_title(vulnerabilities), "", "当前范围内没有漏洞。"]]

    objects: list[bytes] = [b"" for _ in range(5)]
    page_object_numbers: list[int] = []
    for page_lines in pages:
        stream_lines = [
            "0.96 0.98 1 rg 0 0 595 842 re f",
            "0.02 0.32 0.62 rg 0 806 595 36 re f",
            "0.86 0.92 1 rg 42 705 511 58 re f",
        ]
        y = 818
        for idx, line in enumerate(page_lines):
            font_size = 16 if idx == 0 else 10
            if line in ("报告概览", "漏洞清单") or line.startswith("项目："):
                font_size = 13
                y -= 6
            stream_lines.append(f"BT /F1 {font_size} Tf {48} {y} Td <{_pdf_hex_text(line)}> Tj ET")
            y -= 16 if font_size >= 13 else 13
        stream = "\n".join(stream_lines).encode("ascii")
        content_obj = len(objects) + 1
        objects.append(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )
        page_obj = len(objects) + 1
        page_object_numbers.append(page_obj)
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_obj} 0 R >>"
            ).encode("ascii")
        )

    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{num} 0 R" for num in page_object_numbers)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_object_numbers)} >>".encode("ascii")
    objects[2] = b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H /DescendantFonts [4 0 R] >>"
    objects[3] = b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> /FontDescriptor 5 0 R >>"
    objects[4] = b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 /FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 880 /StemV 80 >>"

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{index} 0 obj\n".encode("ascii"))
        buffer.write(obj)
        buffer.write(b"\nendobj\n")
    xref = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return buffer.getvalue()


def _docx_paragraph(text: str, style: str | None = None, color: str | None = None) -> str:
    style_xml = f'<w:pStyle w:val="{style}"/>' if style else ""
    color_xml = f'<w:color w:val="{color}"/>' if color else ""
    return (
        "<w:p>"
        f"<w:pPr>{style_xml}</w:pPr>"
        f"<w:r><w:rPr>{color_xml}</w:rPr><w:t xml:space=\"preserve\">{xml_escape(text)}</w:t></w:r>"
        "</w:p>"
    )


def _docx_table(rows: list[list[str]], header: bool = False) -> str:
    xml = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>']
    for row_index, row in enumerate(rows):
        xml.append("<w:tr>")
        for cell in row:
            shade = '<w:shd w:fill="F3F7FB"/>' if header and row_index == 0 else ""
            xml.append(
                "<w:tc><w:tcPr>"
                + shade
                + "</w:tcPr><w:p><w:r><w:t xml:space=\"preserve\">"
                + xml_escape(str(cell or ""))
                + "</w:t></w:r></w:p></w:tc>"
            )
        xml.append("</w:tr>")
    xml.append("</w:tbl>")
    return "".join(xml)


def _docx_pre_block(text: str) -> str:
    rows = [[line] for line in str(text or "未记录").splitlines() or ["未记录"]]
    return _docx_table(rows)


def _render_docx_export(vulnerabilities: list[Vulnerability]) -> bytes:
    counts = _summarize(vulnerabilities)
    body: list[str] = [
        _docx_paragraph(_report_title(vulnerabilities), style="Title", color="0F172A"),
        _docx_paragraph("报告概览", style="Heading1"),
        _docx_table(
            [
                ["总漏洞数", "严重", "高危", "中危", "低危"],
                [str(len(vulnerabilities)), str(counts["critical"]), str(counts["high"]), str(counts["medium"]), str(counts["low"])],
            ],
            header=True,
        ),
    ]
    if vulnerabilities:
        body.extend(
            [
                _docx_paragraph("漏洞清单", style="Heading1"),
                _docx_table(
                    [["ID", "漏洞名称", "项目", "严重程度", "确认事实"]]
                    + [
                        [
                            f"H-{idx:02d}",
                            v.title,
                            f"{v.project_name} ({v.project_id})",
                            _SEVERITY_LABELS.get(v.severity, v.severity),
                            v.fact_id,
                        ]
                        for idx, v in enumerate(vulnerabilities, start=1)
                    ],
                    header=True,
                ),
            ]
        )
    for project_id, project_name, items in _project_groups(vulnerabilities):
        body.append(_docx_paragraph(f"项目：{project_name}（{project_id}）", style="Heading1"))
        for idx, vuln in enumerate(items, start=1):
            body.extend(
                [
                    _docx_paragraph(f"{idx}. {vuln.title}", style="Heading2"),
                    _docx_table(
                        [
                            ["字段", "内容"],
                            ["严重程度", _SEVERITY_LABELS.get(vuln.severity, vuln.severity)],
                            ["确认事实", vuln.fact_id],
                            ["关联事实", ", ".join(vuln.related_fact_ids or [vuln.fact_id])],
                            ["发现时间", vuln.discovered_at],
                            ["工作节点", vuln.source_worker or "未记录"],
                        ],
                        header=True,
                    ),
                    _docx_paragraph("漏洞描述", style="Heading3"),
                    _docx_paragraph(vuln.description),
                    _docx_paragraph("关键证据", style="Heading3"),
                ]
            )
            body.append(_docx_table([["证据"]] + [[item] for item in (vuln.evidence or ["未记录"])], header=True))
            body.append(_docx_paragraph("漏洞证明数据包", style="Heading3"))
            for packet_index, packet in enumerate(vuln.proof_packets or [], start=1):
                body.append(_docx_paragraph(f"证明 {packet_index}：{packet.get('title') or '漏洞证明'}", style="Heading4"))
                body.append(_docx_table([["请求数据包", "响应/回显"], [packet.get("request") or "未记录", packet.get("response") or "未记录"]], header=True))
                if packet.get("note"):
                    body.append(_docx_paragraph(f"说明：{packet['note']}"))
            body.append(_docx_paragraph("漏洞浮现过程", style="Heading3"))
            body.append(
                _docx_table(
                    [["步骤", "类型/ID", "说明"]]
                    + [
                        [
                            str(step_index),
                            f"{step.get('label') or step.get('type') or '过程'} {step.get('id') or ''}",
                            step.get("description") or "无描述",
                        ]
                        for step_index, step in enumerate(vuln.process or [], start=1)
                    ],
                    header=True,
                )
            )
    if not vulnerabilities:
        body.append(_docx_paragraph("当前范围内没有漏洞。"))
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        "</w:body></w:document>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
        '<w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/><w:pPr><w:spacing w:before="360" w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/><w:color w:val="0F4C81"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="Heading 2"/><w:pPr><w:spacing w:before="280" w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="Heading 3"/><w:pPr><w:spacing w:before="220" w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="21"/><w:color w:val="334155"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading4"><w:name w:val="Heading 4"/><w:pPr><w:spacing w:before="160" w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="20"/></w:rPr></w:style>'
        '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:color="D7DEE8"/><w:left w:val="single" w:sz="4" w:color="D7DEE8"/><w:bottom w:val="single" w:sz="4" w:color="D7DEE8"/><w:right w:val="single" w:sz="4" w:color="D7DEE8"/><w:insideH w:val="single" w:sz="4" w:color="D7DEE8"/><w:insideV w:val="single" w:sz="4" w:color="D7DEE8"/></w:tblBorders><w:tblCellMar><w:top w:w="120" w:type="dxa"/><w:left w:w="120" w:type="dxa"/><w:bottom w:w="120" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
        "</w:styles>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            "</Types>",
        )
        docx.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/styles.xml", styles_xml)
    return buffer.getvalue()


def _describe_export_scope(vulnerabilities: list[Vulnerability], project_id: str | None) -> tuple[str, str | None, str | None]:
    """Return a human-readable scope label plus the resolved project id/name."""
    if len(vulnerabilities) == 1:
        only = vulnerabilities[0]
        return f"{only.project_name} · {only.fact_id}", only.project_id, only.project_name
    project_ids = {item.project_id for item in vulnerabilities}
    if project_id and len(project_ids) == 1:
        name = vulnerabilities[0].project_name if vulnerabilities else project_id
        return f"{name}（{len(vulnerabilities)} 条）", project_id, name
    if len(project_ids) == 1 and vulnerabilities:
        only_name = vulnerabilities[0].project_name
        return f"{only_name}（{len(vulnerabilities)} 条）", vulnerabilities[0].project_id, only_name
    return f"全部漏洞（{len(vulnerabilities)} 条）", None, None


def _record_export(
    vulnerabilities: list[Vulnerability],
    *,
    fmt: str,
    filename: str,
    project_id: str | None,
    severity: str | None,
    status: str | None,
) -> None:
    """Persist a single export action to the ``export_records`` table.

    Best-effort: a logging failure must never break the actual download, so any
    database error is swallowed.
    """
    scope, resolved_project_id, project_name = _describe_export_scope(vulnerabilities, project_id)
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO export_records
                    (created_at, format, filename, scope, vulnerability_count,
                     project_id, project_name, severity, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    fmt,
                    filename,
                    scope,
                    len(vulnerabilities),
                    resolved_project_id,
                    project_name,
                    severity,
                    status,
                ),
            )
    except Exception:  # pragma: no cover - logging must not break the download
        pass
    record_audit(
        "vulnerability.export",
        f"导出漏洞报告（{fmt.upper()}）· {scope}",
        target_type="export",
        target_id=filename,
    )


@router.get("/export-records", response_model=list[ExportRecord])
def list_export_records(limit: int = Query(default=50, ge=1, le=200)) -> list[ExportRecord]:
    """Return the most recent export actions, newest first (导出记录 page)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, format, filename, scope, vulnerability_count,
                   project_id, project_name, severity, status
            FROM export_records
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [ExportRecord(**dict(row)) for row in rows]


@router.delete("/export-records/{record_id}")
def delete_export_record(record_id: int) -> dict[str, str]:
    """Delete a single export record from history."""
    with get_conn() as conn:
        conn.execute("DELETE FROM export_records WHERE id = ?", (record_id,))
    return {"status": "deleted"}


@router.delete("/export-records")
def clear_export_records() -> dict[str, str]:
    """Clear all export records."""
    with get_conn() as conn:
        conn.execute("DELETE FROM export_records")
    return {"status": "cleared"}


@router.get("/export")
def export_vulnerabilities(
    format: str = Query(
        default="json",
        description="Export format; one of 'json', 'csv', 'md', 'pdf', 'docx', or 'word'.",
    ),
    severity: str | None = Query(
        default=None,
        description="Optional severity filter (critical, high, medium, low).",
    ),
    project_id: str | None = Query(
        default=None,
        description="Optional project filter; restricts the export to one project.",
    ),
    vulnerability_id: str | None = Query(
        default=None,
        description="Optional vulnerability id; restricts the export to one finding.",
    ),
    vulnerability_ids: str | None = Query(
        default=None,
        description="Comma-separated merged vulnerability ids to export.",
    ),
    status: str | None = Query(
        default=None,
        description="Optional review status filter (confirmed or ignored).",
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
    if normalized not in ("json", "csv", "md", "markdown", "pdf", "docx", "word"):
        raise HTTPException(status_code=422, detail="Supported formats: json, csv, md, pdf, docx")

    selected_ids = [item.strip() for item in (vulnerability_ids or "").split(",") if item.strip()] or None
    vulnerabilities = _query_filtered_vulnerabilities(
        severity,
        project_id,
        vulnerability_id,
        vulnerability_ids=selected_ids,
        status=status,
    )

    if normalized == "json":
        body = _render_json_export(vulnerabilities)
        filename = _export_filename(vulnerabilities, "json")
        _record_export(vulnerabilities, fmt="json", filename=filename, project_id=project_id, severity=severity, status=status)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )

    if normalized in ("md", "markdown"):
        body = _render_markdown_export(vulnerabilities)
        filename = _export_filename(vulnerabilities, "md")
        _record_export(vulnerabilities, fmt="md", filename=filename, project_id=project_id, severity=severity, status=status)
        return Response(
            content=body,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if normalized == "pdf":
        body = _render_pdf_export(vulnerabilities)
        filename = _export_filename(vulnerabilities, "pdf")
        _record_export(vulnerabilities, fmt="pdf", filename=filename, project_id=project_id, severity=severity, status=status)
        return Response(
            content=body,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if normalized in ("docx", "word"):
        body = _render_docx_export(vulnerabilities)
        filename = _export_filename(vulnerabilities, "docx")
        _record_export(vulnerabilities, fmt="docx", filename=filename, project_id=project_id, severity=severity, status=status)
        return Response(
            content=body,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    body = _render_csv_export(vulnerabilities)
    filename = _export_filename(vulnerabilities, "csv")
    _record_export(vulnerabilities, fmt="csv", filename=filename, project_id=project_id, severity=severity, status=status)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
