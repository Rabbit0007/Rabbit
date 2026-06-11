"""Project-level campaign synthesis helpers.

This service aggregates the existing fact graph, hints, open intents, and
materialized vulnerabilities into a single deterministic summary. The goal is
to help operators understand the current main line of a project without adding
new autonomous execution or new persistence tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cairn.server.campaign_models import (
    CampaignCounts,
    CampaignFinding,
    CampaignGoalStatus,
    CampaignSynthesis,
)
from cairn.server.db import get_conn
from cairn.server.text_normalization import normalize_hint_content
from cairn.server.services import expire_reason_leases, expire_workers, get_project_or_404


@dataclass(frozen=True)
class _LoadedVulnerability:
    id: str
    fact_id: str
    title: str
    description: str
    severity: str
    status: str
    discovered_at: str


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_POSITIVE_FACT_PATTERNS: tuple[tuple[str, int], ...] = (
    (r"\buid=0\b", 120),
    (r"\buid=\d+\([^)]+\)\b", 110),
    (r"\bwww-data\b", 90),
    (r"\bwhoami\b", 80),
    (r"远程(?:代码|命令)?执行|命令执行|rce", 75),
    (r"未授权\s*(?:读取|访问)|越权读取|跨作业读取", 70),
    (r"路径穿越|directory traversal|path traversal", 65),
    (r"返回\s*200|Content-Type|正文为|页面标题为|解包确认", 35),
    (r"已确认|真实存在|证明攻击者可|证明可|直接返回|暴露|泄露", 30),
)

_NEGATIVE_FACT_PATTERNS: tuple[tuple[str, int], ...] = (
    (r"未观察到(?:任何)?真实(?:执行|利用|命令执行)证据", 90),
    (r"未发现.{0,80}(?:uid=|www-data|whoami|id\s*输出|副作用)", 80),
    (r"尚不能(?:据此)?确认|不能(?:据此)?确认", 70),
    (r"无命令执行|不可利用|未触发|未命中|无可利用", 60),
    (r"失败|超时|未获得|未收到", 30),
)


def build_campaign_synthesis(project_id: str) -> CampaignSynthesis:
    with get_conn() as conn:
        expire_workers(conn, project_id)
        expire_reason_leases(conn, project_id)
        project = get_project_or_404(conn, project_id)
        fact_rows = conn.execute(
            "SELECT id, description FROM facts WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        ).fetchall()
        hint_rows = conn.execute(
            "SELECT id, content FROM hints WHERE project_id = ? ORDER BY created_at, rowid",
            (project_id,),
        ).fetchall()
        intent_rows = conn.execute(
            """
            SELECT id, description, concluded_at
            FROM intents
            WHERE project_id = ?
            ORDER BY created_at, rowid
            """,
            (project_id,),
        ).fetchall()
        vulnerability_rows = conn.execute(
            """
            SELECT id, fact_id, title, description, severity, status, discovered_at
            FROM vulnerabilities
            WHERE project_id = ?
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                    ELSE 4
                END,
                discovered_at DESC,
                id DESC
            """,
            (project_id,),
        ).fetchall()

    facts = [(row["id"], str(row["description"] or "").strip()) for row in fact_rows]
    fact_by_id = {fact_id: description for fact_id, description in facts}
    hints = [(row["id"], normalize_hint_content(row["content"])) for row in hint_rows]
    open_intents = [
        str(row["description"] or "").strip()
        for row in intent_rows
        if row["concluded_at"] is None and str(row["description"] or "").strip()
    ]
    vulnerabilities = [
        _LoadedVulnerability(
            id=row["id"],
            fact_id=row["fact_id"],
            title=str(row["title"] or "").strip(),
            description=str(row["description"] or "").strip(),
            severity=str(row["severity"] or "").strip(),
            status=str(row["status"] or "confirmed").strip(),
            discovered_at=str(row["discovered_at"] or "").strip(),
        )
        for row in vulnerability_rows
    ]

    counts = CampaignCounts(
        facts=len(facts),
        hints=len(hints),
        intents=len(intent_rows),
        open_intents=len(open_intents),
        vulnerabilities=len(vulnerabilities),
        high_value_vulnerabilities=sum(
            1
            for vuln in vulnerabilities
            if vuln.status == "confirmed" and vuln.severity in {"critical", "high"}
        ),
    )
    goal_status = _goal_status(project["status"], facts, vulnerabilities)
    top_findings = _top_findings(facts, hints, vulnerabilities)
    blockers = _blockers(facts)
    lead = _lead(project["title"], goal_status, vulnerabilities, top_findings, open_intents)
    summary = _summary(project["title"], project["status"], goal_status, counts, lead, blockers)
    next_steps = _next_steps(goal_status, counts, open_intents, hints, vulnerabilities)

    return CampaignSynthesis(
        project_id=project["id"],
        project_name=project["title"],
        project_status=project["status"],
        goal_status=goal_status,
        origin=fact_by_id.get("origin", ""),
        goal=fact_by_id.get("goal", ""),
        lead=lead,
        summary=summary,
        counts=counts,
        top_findings=top_findings,
        open_intents=open_intents[:5],
        blockers=blockers,
        next_steps=next_steps,
    )


def _goal_status(
    project_status: str,
    facts: list[tuple[str, str]],
    vulnerabilities: list[_LoadedVulnerability],
) -> CampaignGoalStatus:
    text = "\n".join(description for _fact_id, description in facts)
    has_positive_success = bool(
        re.search(
        r"\buid=0\b|"
        r"\buid=\d+\((?:root|www-data)\)\b|"
        r"connected\s+as\s+(?:root|www-data)|"
        r"interactive\s+shell\s+ready|"
        r"whoami(?:\s*(?:output|输出))?(?:[:：]|\s*为)?\s*(?:root|www-data)",
        text,
        re.IGNORECASE,
        )
    )
    if has_positive_success:
        return "achieved"
    if any(vuln.status == "confirmed" and vuln.severity in {"critical", "high"} for vuln in vulnerabilities):
        return "in_progress"

    score = _fact_score(text)
    if score >= 70:
        return "in_progress"
    return "blocked"


def _fact_score(text: str) -> int:
    score = 0
    for pattern, weight in _POSITIVE_FACT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score += weight
    for pattern, weight in _NEGATIVE_FACT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score -= weight
    return score


def _first_line(text: str, limit: int = 180) -> str:
    snippet = next((line.strip() for line in text.splitlines() if line.strip()), "").strip()
    if len(snippet) <= limit:
        return snippet
    return snippet[: limit - 3].rstrip() + "..."


def _fact_confidence(score: int) -> str:
    if score >= 90:
        return "confirmed"
    if score >= 40:
        return "supported"
    return "tentative"


def _top_findings(
    facts: list[tuple[str, str]],
    hints: list[tuple[str, str]],
    vulnerabilities: list[_LoadedVulnerability],
) -> list[CampaignFinding]:
    findings: list[CampaignFinding] = []
    covered_fact_ids = {vuln.fact_id for vuln in vulnerabilities}

    for vuln in vulnerabilities[:3]:
        findings.append(
            CampaignFinding(
                source_type="vulnerability",
                source_id=vuln.id,
                title=vuln.title,
                summary=_first_line(vuln.description),
                severity=vuln.severity if vuln.severity in _SEVERITY_RANK else None,
                confidence="confirmed" if vuln.status == "confirmed" else "supported",
            )
        )

    scored_facts = sorted(
        (
            (fact_id, description, _fact_score(description))
            for fact_id, description in facts
            if fact_id not in {"origin", "goal"} and fact_id not in covered_fact_ids
        ),
        key=lambda item: (item[2], _fact_numeric_rank(item[0])),
        reverse=True,
    )
    for fact_id, description, score in scored_facts:
        if score < 25 or len(findings) >= 5:
            continue
        findings.append(
            CampaignFinding(
                source_type="fact",
                source_id=fact_id,
                title=_first_line(description, limit=84) or fact_id,
                summary=_first_line(description),
                confidence=_fact_confidence(score),
            )
        )

    if not findings and hints:
        hint_id, hint_text = hints[-1]
        findings.append(
            CampaignFinding(
                source_type="hint",
                source_id=hint_id,
                title="最近 Hint",
                summary=_first_line(hint_text),
                confidence="tentative",
            )
        )

    return findings


def _fact_numeric_rank(fact_id: str) -> int:
    match = re.search(r"(\d+)", fact_id)
    return int(match.group(1)) if match else -1


def _blockers(facts: list[tuple[str, str]]) -> list[str]:
    blockers: list[tuple[int, str]] = []
    for fact_id, description in facts:
        if fact_id in {"origin", "goal"}:
            continue
        if not any(re.search(pattern, description, re.IGNORECASE) for pattern, _ in _NEGATIVE_FACT_PATTERNS):
            continue
        blockers.append((_fact_numeric_rank(fact_id), _first_line(description)))

    blockers.sort(reverse=True)
    unique: list[str] = []
    seen: set[str] = set()
    for _rank, blocker in blockers:
        if not blocker or blocker in seen:
            continue
        seen.add(blocker)
        unique.append(blocker)
        if len(unique) >= 3:
            break
    return unique


def _lead(
    project_name: str,
    goal_status: CampaignGoalStatus,
    vulnerabilities: list[_LoadedVulnerability],
    top_findings: list[CampaignFinding],
    open_intents: list[str],
) -> str:
    if goal_status == "achieved":
        return f"{project_name} 已具备足以证明目标达成的结果。"
    if vulnerabilities:
        head = vulnerabilities[0]
        severity = {"critical": "严重", "high": "高危", "medium": "中危", "low": "低危"}.get(
            head.severity,
            head.severity,
        )
        return f"当前主线已收敛到 {severity} 发现：{head.title}"
    if top_findings:
        return f"当前最强信号来自 {top_findings[0].source_id}：{top_findings[0].title}"
    if open_intents:
        return f"当前仍以 {open_intents[0]} 作为主要推进方向。"
    return f"{project_name} 当前缺少足够强的项目级信号。"


def _summary(
    project_name: str,
    project_status: str,
    goal_status: CampaignGoalStatus,
    counts: CampaignCounts,
    lead: str,
    blockers: list[str],
) -> str:
    parts = [
        f"项目 {project_name} 当前状态为 {project_status}，目标判定为 {goal_status}。",
        lead,
        f"已累计 {counts.facts} 条事实、{counts.open_intents} 条开放意图、{counts.vulnerabilities} 条漏洞记录，其中高价值漏洞 {counts.high_value_vulnerabilities} 条。",
    ]
    if project_status == "completed" and goal_status != "achieved":
        parts.append("项目状态虽然已标记为 completed，但当前事实中仍缺少足以直接证明目标达成的正向成功信号。")
    if blockers:
        parts.append(f"最近主要阻塞点：{blockers[0]}")
    return " ".join(parts)


def _next_steps(
    goal_status: CampaignGoalStatus,
    counts: CampaignCounts,
    open_intents: list[str],
    hints: list[tuple[str, str]],
    vulnerabilities: list[_LoadedVulnerability],
) -> list[str]:
    if goal_status == "achieved":
        return [
            "固定当前项目的关键证据和最小复现实验，避免后续事实改写覆盖成功结论。",
            "将项目级结论与单条事实分开维护，保证最终结论和过程证据都可追溯。",
        ]

    steps: list[str] = []
    if vulnerabilities:
        steps.append("把已确认高价值发现与未确认提权路径分开维护，避免同一事实同时承载正反两类结论。")
    else:
        steps.append("优先把已有强信号拆成独立项目结论，避免零散事实长期停留在图里而无法形成主线判断。")

    if open_intents:
        steps.append("优先收敛现有开放意图，减少在同一候选方向上重复记录相似阴性结果。")
    else:
        steps.append("当前没有开放意图，建议围绕最强已确认事实重新提出少量互不重叠的后续方向。")

    if hints:
        steps.append("对关键 hint 保留原文与原始请求细节，避免语义改写导致后续复现实验漂移。")

    if counts.high_value_vulnerabilities == 0:
        steps.append("在项目级视图中优先沉淀可复现的边界、证据和失败条件，而不是过早把目标缩成单一成功标准。")

    return steps[:4]
