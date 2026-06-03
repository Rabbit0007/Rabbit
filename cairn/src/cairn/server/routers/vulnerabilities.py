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
from urllib.parse import urlsplit
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
    """Join only reportable text from one project/vulnerability group."""
    parts: list[str] = []
    for vuln in vulns:
        parts.extend([vuln.title, vuln.description, *vuln.evidence])
    return "\n".join(_unique(parts))


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


_FILESYSTEM_PATH_PREFIXES = (
    "/bin/",
    "/dev/",
    "/etc/",
    "/home/",
    "/opt/",
    "/proc/",
    "/root/",
    "/tmp/",
    "/usr/",
    "/var/",
)


def _project_origin(project_id: str) -> str:
    """Return the origin fact for exactly one project, when available."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT description FROM facts WHERE project_id = ? AND id = 'origin'",
            (project_id,),
        ).fetchone()
    return str(row["description"] or "").strip() if row else ""


def _project_fact_text(vulns: list[Vulnerability]) -> str:
    """Load raw descriptions only for the selected project's finding facts."""
    if not vulns:
        return ""
    project_id = vulns[0].project_id
    fact_ids = _unique(
        [vuln.fact_id for vuln in vulns if vuln.project_id == project_id]
    )
    if not fact_ids:
        return ""
    placeholders = ",".join("?" for _ in fact_ids)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT description FROM facts "
            f"WHERE project_id = ? AND id IN ({placeholders}) ORDER BY id",
            (project_id, *fact_ids),
        ).fetchall()
    return "\n".join(str(row["description"] or "") for row in rows)


def _clean_endpoint(value: str) -> str:
    endpoint = value.strip("`'\"*()[]{}<>，。；;：:")
    if not endpoint.startswith("/") or endpoint.startswith("//"):
        return ""
    if endpoint.lower().startswith(_FILESYSTEM_PATH_PREFIXES):
        return ""
    return endpoint


def _local_context(text: str, start: int, end: int) -> str:
    """Return the bullet/sentence that contains a candidate endpoint."""
    line_left = text.rfind("\n", 0, start)
    line_right = text.find("\n", end)
    if line_left >= 0 or line_right >= 0:
        line_right = line_right if line_right >= 0 else len(text)
        line = text[line_left + 1 : line_right].strip()
        if line:
            if line.startswith("-") and line_left > 0:
                preceding = text[:line_left].splitlines()
                for previous in reversed(preceding[-6:]):
                    previous = previous.strip()
                    if not previous:
                        continue
                    if previous.startswith("-"):
                        continue
                    if re.search(
                        r"未授权|漏洞|泄露|发现|确认|攻击面",
                        previous,
                        re.IGNORECASE,
                    ):
                        return previous + "\n" + line
                    break
            return line

    left = max(text.rfind("。", 0, start), text.rfind("；", 0, start))
    right_candidates = [
        position
        for position in (
            text.find("\n", end),
            text.find("。", end),
            text.find("；", end),
        )
        if position >= 0
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1 : right].strip()


def _endpoint_candidates(text: str) -> list[tuple[str, int, str]]:
    """Extract and score HTTP endpoint candidates from report facts."""
    candidates: dict[str, tuple[int, str]] = {}
    patterns = (
        r"https?://[^\s`'\"<>，。；;）)]+",
        r"(?<![\w./:])/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@%/?#\[\]-]+",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            raw = match.group(0)
            if raw.lower().startswith(("http://", "https://")):
                parsed = urlsplit(raw)
                raw = parsed.path or "/"
                if parsed.query:
                    raw += "?" + parsed.query
            endpoint = _clean_endpoint(raw)
            if not endpoint:
                continue
            context = _local_context(text, match.start(), match.end())
            if re.search(
                rf"rtsp://[^\s`'\"<>，。；;）)]*{re.escape(endpoint)}",
                context,
                re.IGNORECASE,
            ):
                continue
            score = 5
            for positive, weight in (
                (
                    r"无需认证|未授权|直接返回|回显|成功|已确认|漏洞|泄露|"
                    r"枚举|错误响应不同|密码提示",
                    30,
                ),
                (r"\b(?:GET|POST|PUT|PATCH|DELETE)\b|请求|响应|状态码|JSON", 16),
                (r"返回\s*(?:HTTP\s*)?2\d\d|\b2\d\d\s+OK\b", 12),
            ):
                if re.search(positive, context, re.IGNORECASE):
                    score += weight
            for negative, weight in (
                (
                    r"404|不存在|失败|不可利用|未发现|无法|未能|错误结论|"
                    r"修正|未被使用|不参与",
                    35,
                ),
                (
                    r"需认证|需要认证|受保护|重定向至登录|未授权访问.*未|"
                    r"为空|需特定",
                    24,
                ),
            ):
                if re.search(negative, context, re.IGNORECASE):
                    score -= weight
            if endpoint.endswith((".action", ".jsp", ".php", ".json")):
                score += 8
            previous = candidates.get(endpoint)
            if previous is None or score > previous[0]:
                candidates[endpoint] = (score, context)
    return sorted(
        ((endpoint, score, context) for endpoint, (score, context) in candidates.items()),
        key=lambda item: (-item[1], -len(item[0]), item[0]),
    )


def _target_host(project_id: str, text: str, endpoint: str, context: str) -> str:
    """Resolve a host only from the current group's text or project origin."""
    urls = re.findall(r"https?://[^\s`'\"<>，。；;）)]+", text, re.IGNORECASE)
    matching = [url for url in urls if endpoint and endpoint.split("?", 1)[0] in url]
    for raw in [*matching, *urls, _project_origin(project_id)]:
        if not raw:
            continue
        parsed = urlsplit(raw)
        if parsed.netloc:
            host = parsed.netloc
            if ":" not in host:
                port_match = re.search(
                    r"(?:^|[^\d])(\d{2,5})\s+(?:DStatus|SS|HTTP|HTTPS|Web|API)",
                    context,
                    re.IGNORECASE,
                )
                if port_match:
                    host += ":" + port_match.group(1)
            return host
    return "<项目事实未记录目标主机>"


def _request_method(context: str, endpoint: str) -> str:
    escaped = re.escape(endpoint.split("?", 1)[0])
    for pattern in (
        rf"\b(GET|POST|PUT|PATCH|DELETE)\b[^。\n]{{0,100}}{escaped}",
        rf"{escaped}[^。\n]{{0,100}}\b(GET|POST|PUT|PATCH|DELETE)\b",
        r"\b(GET|POST|PUT|PATCH|DELETE)\s+请求\b",
    ):
        match = re.search(pattern, context, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return "GET"


def _request_parameters(context: str) -> list[tuple[str, str]]:
    """Extract concrete name=value examples close to the selected endpoint."""
    params: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"(?<![\w.-])([A-Za-z_][\w.\[\]-]*)=([^&\s`，。,；;）)]+)",
        context,
    ):
        name, value = match.group(1), match.group(2).strip("'\"")
        if name.lower() in {"http", "https", "uid", "gid", "euid"} or name in seen:
            continue
        seen.add(name)
        params.append((name, value))
        if len(params) >= 6:
            break
    return params


def _response_body(context: str) -> str:
    """Extract an exact response example when the fact recorded one."""
    json_match = re.search(r"\{[^{}\n]{2,800}\}", context)
    if json_match:
        return json_match.group(0)
    for pattern in (
        r"返回\s*[`'\"“]([^`'\"”\n]{1,500})[`'\"”]",
        r"响应(?:为|内容为|回显)?\s*[`'\"“]([^`'\"”\n]{1,500})[`'\"”]",
        r"→\s*(\[[^\]\n]{1,500}\])",
    ):
        match = re.search(pattern, context, re.IGNORECASE)
        if match:
            return match.group(1)
    behavior = re.search(
        r"[^。\n]{0,120}(?:无需认证|未授权|直接返回|回显|泄露|枚举)[^。\n]{0,220}",
        context,
        re.IGNORECASE,
    )
    if behavior:
        return f"<事实仅记录响应行为：{behavior.group(0).strip()}>"
    return "<事实未记录原始响应体，需复测补充>"


def _reconstructed_http_packets(vulns: list[Vulnerability]) -> list[dict[str, str]]:
    """Build reproducible HTTP proofs from same-project confirmed facts."""
    if not vulns:
        return []
    project_id = vulns[0].project_id
    scoped = [vuln for vuln in vulns if vuln.project_id == project_id]
    raw_fact_text = _project_fact_text(scoped)
    report_text = _all_report_text(scoped)
    text = "\n".join(part for part in (report_text, raw_fact_text) if part)
    candidate_text = raw_fact_text or report_text
    candidates = [
        candidate for candidate in _endpoint_candidates(candidate_text) if candidate[1] > 0
    ]
    if not candidates:
        return []
    top_score = candidates[0][1]
    candidates = [
        candidate
        for candidate in candidates
        if candidate[1] >= max(5, top_score - 12)
    ]
    candidates = [
        candidate
        for candidate in candidates
        if not (
            candidate[0].endswith("/")
            and any(
                other[0] != candidate[0]
                and other[0].startswith(candidate[0])
                and other[1] >= candidate[1]
                for other in candidates
            )
        )
    ]
    fact_ids = ", ".join(_unique([vuln.fact_id for vuln in scoped]))
    packets: list[dict[str, str]] = []
    for endpoint, _score, context in candidates[:3]:
        params = _request_parameters(context)
        if (
            not params
            and "login" in endpoint.lower()
            and re.search(
                r"用户名|密码|登录接口|认证错误|loginName|loginSecretKey",
                context,
                re.IGNORECASE,
            )
        ):
            params = _request_parameters(raw_fact_text)
        method = _request_method(context, endpoint)
        if method == "GET" and params and "login" in endpoint.lower():
            method = "POST"

        request_target = endpoint
        body = ""
        if params:
            encoded = "&".join(f"{name}={value}" for name, value in params)
            if method == "GET" and "?" not in request_target:
                request_target += "?" + encoded
            elif method != "GET":
                body = encoded
        elif method != "GET":
            body = "<根据事实补充请求参数或载荷>"

        host = _target_host(project_id, text, endpoint, context)
        request_lines = [
            f"{method} {request_target} HTTP/1.1",
            f"Host: {host}",
            "Accept: application/json, text/plain, */*",
            "Connection: close",
        ]
        if body:
            request_lines.extend(
                [
                    "Content-Type: application/x-www-form-urlencoded",
                    f"Content-Length: {len(body.encode('utf-8'))}",
                    "",
                    body,
                ]
            )

        status_match = re.search(
            r"(?:返回|HTTP(?:/\d(?:\.\d)?)?\s*)\s*(\d{3})(?:\s+OK)?",
            context,
            re.IGNORECASE,
        )
        status = status_match.group(1) if status_match else "<事实未记录状态码>"
        response_body = _response_body(context)
        content_type = (
            "application/json"
            if "json" in context.lower() or response_body.startswith(("{", "["))
            else "text/plain"
        )
        packets.append(
            {
                "title": f"{endpoint.split('?', 1)[0]} 漏洞证明（依据事实重构）",
                "request": "\n".join(request_lines),
                "response": (
                    f"HTTP/1.1 {status}\n"
                    f"Content-Type: {content_type}\n\n"
                    f"{response_body}"
                ),
                "note": (
                    f"该数据包仅依据当前项目 {project_id} 的确认事实 {fact_ids} 重构，"
                    "不是原始抓包。事实未记录的字段使用占位符，复测时应以真实请求和响应替换。"
                ),
            }
        )
    return packets


def _proof_packets(vulns: list[Vulnerability]) -> list[dict[str, str]]:
    """Reconstruct proof packets from same-project facts without fixed payloads."""
    return _reconstructed_http_packets(vulns)


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
            packets = vuln.proof_packets or []
            if not packets:
                lines.extend(["未记录真实请求/响应数据包。", ""])
            for packet_index, packet in enumerate(packets, start=1):
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
        else:
            lines.extend(["漏洞证明数据包：", "未记录真实请求/响应数据包。"])
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
            packets = vuln.proof_packets or []
            if not packets:
                lines.append("未记录真实请求/响应数据包。")
            for packet_index, packet in enumerate(packets, start=1):
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
            packets = vuln.proof_packets or []
            if not packets:
                body.append(_docx_paragraph("未记录真实请求/响应数据包。"))
            for packet_index, packet in enumerate(packets, start=1):
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
