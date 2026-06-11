"""Read-only vulnerability report composition service.

This service turns an already-confirmed merged vulnerability into a clearer,
delivery-oriented report draft. It uses a deterministic template by default and
can optionally ask a dedicated language model to polish the narrative while
remaining grounded in the same source evidence.

Important constraints:

* No new persistence is introduced.
* The core dispatcher / project execution flow is untouched.
* The model is never asked to invent facts or generate executable exploit
  scripts, payloads, or weaponized step-by-step instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import requests
import yaml

from cairn.server.db import get_conn
from cairn.server.report_composer_models import (
    VulnerabilityNarrativeReport,
    VulnerabilityProofPoint,
)
from cairn.server.vulnerabilities_models import Vulnerability

_REPORT_TIMEOUT_SECONDS = 90
_TRAILING_PUNCTUATION = "`'\"*()[]{}<>，。；;：:,."
_CONFIG_PATH_ENV = "CAIRN_REPORT_COMPOSER_CONFIG_PATH"


@dataclass(frozen=True)
class _ComposerProfile:
    model: str
    base_url: str
    api_key: str
    provider_api: str = "openai-completions"


@dataclass(frozen=True)
class _ReportContext:
    origin: str
    goal: str
    related_facts: list[tuple[str, str]]
    source_intent_description: str | None


def compose_vulnerability_report(
    vulnerability: Vulnerability,
    *,
    use_model: bool = False,
) -> VulnerabilityNarrativeReport:
    """Compose a delivery-grade report draft for one merged vulnerability."""
    context = _load_report_context(vulnerability)
    fallback = _build_template_report(vulnerability, context)
    if not use_model:
        return fallback

    profile = _resolve_report_composer_profile()
    if profile is None:
        return fallback

    enhanced = _compose_with_model(vulnerability, context, fallback, profile)
    return enhanced or fallback


def render_narrative_markdown(report: VulnerabilityNarrativeReport) -> list[str]:
    """Render one composed vulnerability report as Markdown lines."""
    lines = [
        "#### 漏洞概述",
        "",
        report.executive_summary,
        "",
    ]

    if report.attack_surface:
        lines.extend(["#### 攻击面", ""])
        for item in report.attack_surface:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(["#### 漏洞证明", "", report.vulnerability_proof, ""])

    if report.proof_points:
        lines.extend(["##### 证明要点", ""])
        for point in report.proof_points:
            lines.append(f"- **{point.label}**：{point.content}")
        lines.append("")

    lines.extend(
        [
            "#### 影响结论",
            "",
            report.impact,
            "",
            "#### 成因分析",
            "",
            report.root_cause,
            "",
        ]
    )

    if report.evidence_highlights:
        lines.extend(["#### 关键证据", ""])
        for item in report.evidence_highlights:
            lines.append(f"- {item}")
        lines.append("")

    if report.remediation:
        lines.extend(["#### 修复建议", ""])
        for item in report.remediation:
            lines.append(f"- {item}")
        lines.append("")

    if report.operator_notes:
        lines.extend(["#### 说明", ""])
        for item in report.operator_notes:
            lines.append(f"- {item}")
        lines.append("")

    return lines


def render_narrative_plain_lines(report: VulnerabilityNarrativeReport) -> list[str]:
    """Render one composed vulnerability report as wrapped plain-text lines."""
    lines = [
        "漏洞概述：",
        report.executive_summary,
        "",
    ]
    if report.attack_surface:
        lines.append("攻击面：")
        for item in report.attack_surface:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(["漏洞证明：", report.vulnerability_proof, ""])
    if report.proof_points:
        lines.append("证明要点：")
        for point in report.proof_points:
            lines.append(f"- {point.label}：{point.content}")
        lines.append("")

    lines.extend(["影响结论：", report.impact, "", "成因分析：", report.root_cause, ""])
    if report.evidence_highlights:
        lines.append("关键证据：")
        for item in report.evidence_highlights:
            lines.append(f"- {item}")
        lines.append("")
    if report.remediation:
        lines.append("修复建议：")
        for item in report.remediation:
            lines.append(f"- {item}")
        lines.append("")
    if report.operator_notes:
        lines.append("说明：")
        for item in report.operator_notes:
            lines.append(f"- {item}")
    return lines


def _build_template_report(
    vulnerability: Vulnerability,
    context: _ReportContext,
) -> VulnerabilityNarrativeReport:
    full_text = _full_text(vulnerability, context)
    vuln_type = _classify_vulnerability(full_text)
    front_entry, backend_entry = _entry_points(full_text, vulnerability.proof_packets or [])
    param_names = _parameter_names(full_text, vulnerability.proof_packets or [])
    observed = _observed_result(vulnerability, context)
    root_cause = _root_cause(vuln_type, full_text)
    impact = _impact_statement(vuln_type, vulnerability, observed)
    proof_points = _proof_points(
        front_entry=front_entry,
        backend_entry=backend_entry,
        param_names=param_names,
        root_cause=root_cause,
        observed=observed,
        impact=impact,
    )
    attack_surface = _attack_surface(front_entry, backend_entry, param_names, context.origin)
    evidence_highlights = _evidence_highlights(vulnerability, context)
    remediation = _remediation(vuln_type)
    severity_label = _severity_label(vulnerability.severity)
    source_label = "已忽略" if vulnerability.status == "ignored" else "已确认"
    executive_summary = (
        f"{vulnerability.project_name} 中已整理出一条{severity_label}级别的{vuln_type}报告草稿。"
        f" 当前状态为{source_label}，确认事实为 {vulnerability.fact_id}，"
        f"证据显示该问题已经进入可稳定描述的交付阶段。"
    )
    vulnerability_proof = _proof_paragraph(
        vuln_type=vuln_type,
        front_entry=front_entry,
        backend_entry=backend_entry,
        param_names=param_names,
        root_cause=root_cause,
        observed=observed,
        impact=impact,
    )
    notes = [
        "该报告仅基于当前项目内已确认漏洞、关联事实、证明数据包与过程记录整理。",
    ]
    if any(packet.get("note") for packet in vulnerability.proof_packets or []):
        notes.append("部分请求/响应数据包来自事实重构，不是原始抓包，复测时应以真实流量覆盖。")
    if context.goal:
        notes.append(f"项目目标：{_first_line(context.goal, 140)}")

    return VulnerabilityNarrativeReport(
        vulnerability_id=vulnerability.id,
        project_id=vulnerability.project_id,
        project_name=vulnerability.project_name,
        title=vulnerability.title,
        severity=vulnerability.severity,
        status=vulnerability.status,
        vulnerability_type=vuln_type,
        generated_at=_now_iso(),
        composer_source="template",
        composer_model=None,
        executive_summary=executive_summary,
        attack_surface=attack_surface,
        proof_points=proof_points,
        vulnerability_proof=vulnerability_proof,
        impact=impact,
        root_cause=root_cause,
        evidence_highlights=evidence_highlights,
        remediation=remediation,
        operator_notes=notes,
    )


def _compose_with_model(
    vulnerability: Vulnerability,
    context: _ReportContext,
    fallback: VulnerabilityNarrativeReport,
    profile: _ComposerProfile,
) -> VulnerabilityNarrativeReport | None:
    prompt_payload = _model_prompt_payload(vulnerability, context, fallback)
    system = (
        "你是 Rabbit 的报告写作 worker。"
        " 你的职责是把给定的已确认漏洞材料整理成正式、清晰、可交付的中文报告草稿。"
        " 严格只使用输入中已经出现的事实，不得编造接口、参数、时间、权限、结果或因果链。"
        " 不得输出可直接执行的利用脚本、payload、命令、shell、武器化步骤。"
        " 可以把证明链描述清楚，但必须停留在报告表达层。"
        " 返回一个 JSON 对象，不要加 Markdown 代码块，不要输出额外说明。"
    )
    user = (
        "请基于下面的 JSON 上下文，围绕 template_report 做润色重写，生成一个更像正式安全报告的 JSON 对象。"
        " 保持漏洞类型、证明方向、影响结论和修复建议与模板版一致，只提升表达清晰度。"
        ' 只能返回这些字段：executive_summary、attack_surface、proof_points、vulnerability_proof、'
        'impact、root_cause、evidence_highlights、remediation、operator_notes。'
        " 其中 attack_surface、evidence_highlights、remediation、operator_notes 为字符串数组，"
        ' proof_points 为对象数组，每个对象只包含 label 和 content。'
        " proof_points 需要优先覆盖：前台入口、命中接口、关键参数、危险逻辑、可观测结果、影响结论。"
        " 如果某项证据不存在，就保留保守表达，不要补造。"
        " 输出尽量紧凑，单段正文控制在 2 到 4 句，数组每项一句话即可。"
        "\n\n上下文：\n"
        + json.dumps(prompt_payload, ensure_ascii=False, separators=(",", ":"))
    )
    for _attempt in range(2):
        content = _request_model_json(system=system, user=user, profile=profile)
        if content is None:
            continue
        payload = _extract_json_payload(content)
        if not isinstance(payload, dict):
            continue

        merged = fallback.model_dump(mode="json")
        merged["composer_source"] = "model"
        merged["composer_model"] = profile.model
        merged["generated_at"] = _now_iso()
        for field in (
            "executive_summary",
            "vulnerability_proof",
            "impact",
            "root_cause",
        ):
            value = payload.get(field)
            if isinstance(value, str) and value.strip():
                merged[field] = value.strip()
        for field in ("attack_surface", "evidence_highlights", "remediation", "operator_notes"):
            value = payload.get(field)
            if isinstance(value, list):
                merged[field] = _string_list(value)
        proof_points = payload.get("proof_points")
        if isinstance(proof_points, list):
            normalized_points: list[dict[str, str]] = []
            for item in proof_points:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or "").strip()
                content_value = str(item.get("content") or "").strip()
                if not label or not content_value:
                    continue
                normalized_points.append({"label": label, "content": content_value})
            if normalized_points:
                merged["proof_points"] = normalized_points
        try:
            return VulnerabilityNarrativeReport.model_validate(merged)
        except Exception:
            continue
    return None


def _request_model_json(
    *,
    system: str,
    user: str,
    profile: _ComposerProfile,
) -> str | None:
    if profile.provider_api != "openai-completions":
        return None
    endpoint = profile.base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"
    payload = {
        "model": profile.model,
        "temperature": 0.2,
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {profile.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=_REPORT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
    except Exception:
        return None


def _model_prompt_payload(
    vulnerability: Vulnerability,
    context: _ReportContext,
    fallback: VulnerabilityNarrativeReport,
) -> dict[str, Any]:
    return {
        "report_target": {
            "id": vulnerability.id,
            "project_id": vulnerability.project_id,
            "project_name": vulnerability.project_name,
            "title": vulnerability.title,
            "severity": vulnerability.severity,
            "status": vulnerability.status,
            "fact_id": vulnerability.fact_id,
            "vulnerability_type": fallback.vulnerability_type,
        },
        "grounding": {
            "description": _trim(vulnerability.description, 360),
            "source_intent_id": vulnerability.source_intent_id,
            "source_intent_description": _trim(
                vulnerability.source_intent_description or "",
                140,
            ),
            "source_worker": vulnerability.source_worker,
            "evidence_highlights": _evidence_highlights(vulnerability, context)[:4],
            "process_highlights": [
                {
                    "label": str(step.get("label") or step.get("type") or step.get("id") or "过程"),
                    "worker": str(step.get("worker") or "").strip() or None,
                    "description": _trim(str(step.get("description") or ""), 120),
                }
                for step in (vulnerability.process or [])[:3]
                if str(step.get("description") or "").strip()
            ],
            "proof_packets": [
                {
                    "title": _trim(str(packet.get("title") or f"证明 {index + 1}"), 80),
                    "request_target": _trim(_request_target(packet), 140),
                    "response": _trim(_first_line(str(packet.get("response") or ""), 160), 160),
                    "note": _trim(str(packet.get("note") or ""), 100),
                }
                for index, packet in enumerate((vulnerability.proof_packets or [])[:2])
            ],
        },
        "project_context": {
            "origin": _trim(context.origin, 120),
            "goal": _trim(context.goal, 120),
            "related_facts": [
                {"id": fact_id, "description": _trim(description, 140)}
                for fact_id, description in context.related_facts[:3]
            ],
            "source_intent_description": _trim(context.source_intent_description or "", 140),
        },
        "template_report": {
            "vulnerability_type": fallback.vulnerability_type,
            "executive_summary": _trim(fallback.executive_summary, 160),
            "attack_surface": fallback.attack_surface[:4],
            "proof_points": [
                {"label": point.label, "content": _trim(point.content, 120)}
                for point in fallback.proof_points[:6]
            ],
            "vulnerability_proof": _trim(fallback.vulnerability_proof, 220),
            "impact": _trim(fallback.impact, 160),
            "root_cause": _trim(fallback.root_cause, 160),
            "evidence_highlights": fallback.evidence_highlights[:4],
            "remediation": fallback.remediation[:3],
            "operator_notes": fallback.operator_notes[:2],
        },
    }

    try:
        choices = body.get("choices") or []
        message = choices[0].get("message") or {}
        content = message.get("content")
    except Exception:
        return None

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip() if parts else None
    return None


def _resolve_report_composer_profile() -> _ComposerProfile | None:
    explicit_model = os.environ.get("CAIRN_REPORT_COMPOSER_MODEL", "").strip()
    explicit_base = os.environ.get("CAIRN_REPORT_COMPOSER_BASE_URL", "").strip()
    explicit_key = os.environ.get("CAIRN_REPORT_COMPOSER_API_KEY", "").strip()
    explicit_provider = os.environ.get("CAIRN_REPORT_COMPOSER_PROVIDER_API", "").strip() or "openai-completions"
    if explicit_model and explicit_base and explicit_key:
        return _ComposerProfile(
            model=explicit_model,
            base_url=explicit_base,
            api_key=explicit_key,
            provider_api=explicit_provider,
        )

    for path in _candidate_config_paths():
        if not path.exists():
            continue
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        workers = payload.get("workers")
        if not isinstance(workers, list):
            continue
        candidates: list[tuple[int, _ComposerProfile]] = []
        for item in workers:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "pi" or item.get("enabled") is False:
                continue
            env = item.get("env") or {}
            if not isinstance(env, dict):
                continue
            model = str(env.get("PI_MODEL") or "").strip()
            base_url = str(env.get("PI_BASE_URL") or "").strip()
            api_key = str(env.get("PI_API_KEY") or "").strip()
            provider_api = str(env.get("PI_PROVIDER_API") or "openai-completions").strip()
            if not model or not base_url or not api_key:
                continue
            score = 0
            lowered = model.lower()
            if lowered == "gpt-5.4":
                score += 100
            if "5.4" in lowered:
                score += 40
            if "chat5.5" in str(item.get("name") or "").lower():
                score += 15
            score -= int(item.get("priority") or 0)
            candidates.append(
                (
                    score,
                    _ComposerProfile(
                        model=model,
                        base_url=base_url,
                        api_key=api_key,
                        provider_api=provider_api or "openai-completions",
                    ),
                )
            )
        if candidates:
            candidates.sort(key=lambda pair: pair[0], reverse=True)
            return candidates[0][1]
    return None


def _candidate_config_paths() -> list[Path]:
    paths: list[Path] = []
    raw = os.environ.get(_CONFIG_PATH_ENV, "").strip()
    if raw:
        paths.append(Path(raw))
    paths.append(Path("/cairn/dispatch.yaml"))
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "dispatch.yaml"
        if candidate not in paths:
            paths.append(candidate)
    return paths


def _load_report_context(vulnerability: Vulnerability) -> _ReportContext:
    with get_conn() as conn:
        fact_rows = conn.execute(
            "SELECT id, description FROM facts WHERE project_id = ? ORDER BY rowid",
            (vulnerability.project_id,),
        ).fetchall()
        intent_description = vulnerability.source_intent_description
        if not intent_description and vulnerability.source_intent_id:
            intent_row = conn.execute(
                "SELECT description FROM intents WHERE project_id = ? AND id = ?",
                (vulnerability.project_id, vulnerability.source_intent_id),
            ).fetchone()
            if intent_row:
                intent_description = str(intent_row["description"] or "").strip()

    fact_by_id = {str(row["id"]): str(row["description"] or "").strip() for row in fact_rows}
    related_ids = _unique(
        [
            vulnerability.fact_id,
            *(vulnerability.related_fact_ids or []),
            *(vulnerability.source_fact_ids or []),
        ]
    )
    related_facts = [(fact_id, fact_by_id[fact_id]) for fact_id in related_ids if fact_id in fact_by_id]
    return _ReportContext(
        origin=fact_by_id.get("origin", ""),
        goal=fact_by_id.get("goal", ""),
        related_facts=related_facts,
        source_intent_description=intent_description,
    )


def _full_text(vulnerability: Vulnerability, context: _ReportContext) -> str:
    parts = [
        vulnerability.title,
        vulnerability.description,
        context.origin,
        context.goal,
        context.source_intent_description or "",
        *(description for _fact_id, description in context.related_facts),
        *(vulnerability.evidence or []),
        *[
            " ".join(str(step.get(key) or "") for key in ("label", "type", "id", "description", "worker"))
            for step in (vulnerability.process or [])
        ],
        *[
            " ".join(
                str(packet.get(key) or "")
                for key in ("title", "request", "response", "note")
            )
            for packet in (vulnerability.proof_packets or [])
        ],
    ]
    return "\n".join(part for part in parts if part).strip()


def _classify_vulnerability(text: str) -> str:
    rules = (
        ("命令注入", (r"命令注入", r"command injection", r"shell=true", r"subprocess", r"randomstring")),
        ("SQL 注入", (r"sql 注入", r"sql injection", r"\bsqli\b")),
        ("反序列化", (r"反序列化", r"deseriali[sz]ation", r"\bphar\b", r"\bserialize\b")),
        ("文件上传", (r"文件上传", r"upload", r"\.phar\b", r"multipart/form-data")),
        ("路径穿越/任意文件读取", (r"路径穿越", r"path traversal", r"directory traversal", r"任意文件读取", r"\.\./")),
        ("未授权访问/越权", (r"未授权", r"越权", r"idor", r"authentication bypass", r"鉴权缺失")),
        ("SSRF", (r"\bssrf\b", r"server-side request forgery")),
        ("XXE", (r"\bxxe\b", r"xml external entity")),
        ("跨站脚本", (r"\bxss\b", r"跨站脚本", r"script 注入")),
        ("信息泄露", (r"信息泄露", r"information disclosure", r"泄露", r"直接返回")),
        ("远程代码执行", (r"\brce\b", r"远程代码执行", r"远程命令执行")),
    )
    lowered = text.lower()
    for label, patterns in rules:
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns):
            return label
    return "通用安全缺陷"


def _entry_points(text: str, proof_packets: list[dict[str, str]]) -> tuple[str | None, str | None]:
    paths = _extract_paths(text)
    packet_targets = [_request_target(packet) for packet in proof_packets]
    combined = _unique([*packet_targets, *paths])
    if not combined:
        return None, None

    front_entry = next(
        (
            item
            for item in combined
            if item.endswith("/")
            and not any(token in item.lower() for token in ("cgi-bin", "/api/", ".php", ".jsp", ".py"))
        ),
        None,
    )
    backend_entry = next(
        (
            item
            for item in combined
            if any(token in item.lower() for token in ("cgi-bin", "/api/", ".php", ".jsp", ".py", ".action"))
        ),
        None,
    )
    if front_entry is None and combined:
        front_entry = combined[0]
    if backend_entry is None:
        backend_entry = combined[0] if combined else None
    return front_entry, backend_entry


def _attack_surface(
    front_entry: str | None,
    backend_entry: str | None,
    param_names: list[str],
    origin: str,
) -> list[str]:
    items: list[str] = []
    if front_entry:
        items.append(f"前台入口：{front_entry}")
    if backend_entry:
        items.append(f"命中接口：{backend_entry}")
    if param_names:
        items.append(f"关键参数：{', '.join(param_names[:6])}")
    if origin:
        items.append(f"目标起点：{_first_line(origin, 160)}")
    return _unique(items)


def _proof_points(
    *,
    front_entry: str | None,
    backend_entry: str | None,
    param_names: list[str],
    root_cause: str,
    observed: str,
    impact: str,
) -> list[VulnerabilityProofPoint]:
    points: list[VulnerabilityProofPoint] = []
    if front_entry:
        points.append(VulnerabilityProofPoint(label="前台入口", content=front_entry))
    if backend_entry:
        points.append(VulnerabilityProofPoint(label="命中接口", content=backend_entry))
    if param_names:
        points.append(VulnerabilityProofPoint(label="关键参数", content="、".join(param_names[:6])))
    points.append(VulnerabilityProofPoint(label="危险逻辑", content=root_cause))
    points.append(VulnerabilityProofPoint(label="可观测结果", content=observed))
    points.append(VulnerabilityProofPoint(label="影响结论", content=impact))
    return points


def _proof_paragraph(
    *,
    vuln_type: str,
    front_entry: str | None,
    backend_entry: str | None,
    param_names: list[str],
    root_cause: str,
    observed: str,
    impact: str,
) -> str:
    route_bits = []
    if front_entry:
        route_bits.append(f"前台入口位于 {front_entry}")
    if backend_entry and backend_entry != front_entry:
        route_bits.append(f"最终命中处理点 {backend_entry}")
    param_text = f"关键输入集中在 {', '.join(param_names[:6])}" if param_names else "当前材料未完整记录参数名称"
    return (
        f"本次{vuln_type}报告基于现有事实整理出的主证明链为："
        f"{'，'.join(route_bits) if route_bits else '已确认存在可达入口'}，{param_text}。"
        f"从已记录证据看，{root_cause}，因此系统表现出了“{observed}”这一可观测结果，"
        f"据此可以支撑“{impact}”的风险结论。"
    )


def _root_cause(vuln_type: str, text: str) -> str:
    if vuln_type == "命令注入":
        if re.search(r"shell\s*=\s*true|subprocess", text, re.IGNORECASE):
            return "服务端将外部可控内容拼接进 shell 命令执行链，且没有完成命令边界隔离"
        return "服务端在命令执行链路中直接消费了外部输入，缺少严格的参数隔离与白名单约束"
    if vuln_type == "SQL 注入":
        return "服务端在数据库查询链路中对外部输入缺少参数化处理或等价的安全绑定"
    if vuln_type == "反序列化":
        return "服务端对不可信序列化数据执行了解析/反序列化，缺少对象类型和来源约束"
    if vuln_type == "文件上传":
        return "上传链路的文件类型、解析范围或落地位置约束不足，导致不安全文件进入后续处理流程"
    if vuln_type == "路径穿越/任意文件读取":
        return "路径规范化或目录边界校验不足，外部输入可以突破预期访问目录"
    if vuln_type == "未授权访问/越权":
        return "接口缺少有效的身份校验或对象级授权检查，导致非授权主体可直接访问目标资源"
    if vuln_type == "SSRF":
        return "服务端发起对外请求时未限制可访问目标、协议或地址段"
    if vuln_type == "XXE":
        return "XML 解析配置允许外部实体解析，且输入源未被安全限制"
    if vuln_type == "跨站脚本":
        return "输出链路对可控内容缺少上下文相关的编码和过滤"
    if vuln_type == "信息泄露":
        return "敏感资源或内部状态被直接暴露到可访问接口，缺少最小暴露控制"
    if vuln_type == "远程代码执行":
        return "服务端关键执行链路对可控输入缺少足够约束，最终触发了代码或命令执行能力"
    return "关键处理链路对外部输入缺少足够约束，导致安全边界被突破"


def _impact_statement(
    vuln_type: str,
    vulnerability: Vulnerability,
    observed: str,
) -> str:
    evidence_blob = "\n".join(vulnerability.evidence or [])
    text = f"{vulnerability.title}\n{vulnerability.description}\n{observed}\n{evidence_blob}"
    if re.search(r"\buid=0\b|whoami.*root|root\s*权限", text, re.IGNORECASE):
        return "攻击者可借此获得系统级命令执行能力，并可能进一步取得高权限控制"
    if re.search(r"www-data|whoami.*www-data|interactive shell", text, re.IGNORECASE):
        return "攻击者可借此取得 Web 进程上下文的命令执行能力，并继续向系统权限扩展"
    if vuln_type in {"路径穿越/任意文件读取", "未授权访问/越权", "信息泄露"}:
        return "攻击者可在未授权条件下读取他人结果、内部文件或敏感业务数据"
    if vuln_type == "SQL 注入":
        return "攻击者可突破正常数据访问边界，读取、篡改或进一步影响后端数据库数据"
    if vuln_type == "文件上传":
        return "攻击者可借上传链路将不安全文件带入服务器侧处理流程，进一步引发代码执行或文件落地风险"
    if vuln_type in {"命令注入", "远程代码执行", "反序列化"}:
        return "攻击者可将外部输入推进到服务端执行链路，形成对主机或应用的高危控制风险"
    return f"该问题已经对目标系统造成可验证的安全暴露，当前可观测结果为：{observed}"


def _observed_result(vulnerability: Vulnerability, context: _ReportContext) -> str:
    candidates = []
    candidates.extend(vulnerability.evidence or [])
    candidates.append(vulnerability.description)
    candidates.extend(description for _fact_id, description in context.related_facts)
    for packet in vulnerability.proof_packets or []:
        response = str(packet.get("response") or "").strip()
        if response:
            candidates.append(response)
    positive = [
        item
        for item in candidates
        if re.search(
            r"返回\s*200|\b200\b|whoami|uid=|www-data|root|直接返回|成功|回显|正文为|读取|泄露|application/",
            item,
            re.IGNORECASE,
        )
    ]
    source = positive[0] if positive else (candidates[0] if candidates else "当前材料未记录稳定的回显内容")
    return _first_line(source, 220)


def _parameter_names(text: str, proof_packets: list[dict[str, str]]) -> list[str]:
    params: list[str] = []
    seen: set[str] = set()
    for packet in proof_packets:
        request = str(packet.get("request") or "")
        target = _request_target(packet)
        if target and "?" in target:
            for key, _value in parse_qsl(target.split("?", 1)[1], keep_blank_values=True):
                key = key.strip()
                if key and key not in seen:
                    seen.add(key)
                    params.append(key)
        if "\n\n" in request:
            body = request.split("\n\n", 1)[1]
            for key, _value in parse_qsl(body, keep_blank_values=True):
                key = key.strip()
                if key and key not in seen:
                    seen.add(key)
                    params.append(key)
    for match in re.finditer(r"(?<![\w.-])([A-Za-z_][\w.\[\]-]{0,80})=([^\s&]+)", text):
        key = match.group(1).strip()
        if key.lower() in {"http", "https", "host"} or key in seen:
            continue
        seen.add(key)
        params.append(key)
    for match in re.finditer(r"(?:上传字段|字段|参数)[:：]\s*([A-Za-z_][\w.\[\]-]{0,80})", text):
        key = match.group(1).strip()
        if key and key not in seen:
            seen.add(key)
            params.append(key)
    return params[:8]


def _attack_surface_lines(vulnerability: Vulnerability) -> list[str]:
    return [packet.get("title") or "" for packet in (vulnerability.proof_packets or [])]


def _extract_paths(text: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
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
            cleaned = raw.strip(_TRAILING_PUNCTUATION)
            if not cleaned.startswith("/") or cleaned.startswith("//") or cleaned in seen:
                continue
            seen.add(cleaned)
            values.append(cleaned)
    return values[:8]


def _request_target(packet: dict[str, str]) -> str:
    request = str(packet.get("request") or "").strip()
    if not request:
        return ""
    first_line = request.splitlines()[0].strip()
    parts = first_line.split()
    if len(parts) >= 2 and parts[1].startswith("/"):
        return parts[1].strip()
    return ""


def _evidence_highlights(
    vulnerability: Vulnerability,
    context: _ReportContext,
) -> list[str]:
    candidates = _unique(
        [
            *_attack_surface_lines(vulnerability),
            *(vulnerability.evidence or []),
            *[description for _fact_id, description in context.related_facts],
            vulnerability.description,
        ]
    )
    scored = sorted(candidates, key=_evidence_score, reverse=True)
    return [_first_line(item, 220) for item in scored[:6] if item]


def _remediation(vuln_type: str) -> list[str]:
    mapping = {
        "命令注入": [
            "禁止将外部输入直接拼接到 shell 命令中，改用参数化执行接口并关闭 shell=True 类模式。",
            "对涉及命令、脚本、任务名和路径的输入建立严格白名单，并在服务端完成边界校验。",
            "为高风险执行链增加审计日志和最小权限隔离，避免 Web 进程直接拥有高价值系统能力。",
        ],
        "SQL 注入": [
            "将动态 SQL 改为参数化查询或预编译语句，避免字符串拼接。",
            "对查询条件和排序字段建立白名单，禁止将任意可控字段直接进入 SQL 语句。",
            "补充数据库最小权限和异常审计，降低单点注入的横向影响面。",
        ],
        "反序列化": [
            "禁止对不可信输入执行反序列化，优先改为安全的数据交换格式。",
            "若业务必须解析复杂对象，应限制允许的类型集合并校验数据来源。",
            "移除危险 gadget 依赖或在运行时关闭相关自动装载能力。",
        ],
        "文件上传": [
            "将上传链路改为后端重新命名与隔离存储，禁止用户可控文件名直接落地到解析目录。",
            "在服务端同时校验 MIME、扩展名与内容签名，避免单点校验绕过。",
            "对上传目录和后续处理程序做最小权限隔离，防止解析型文件被直接执行。",
        ],
        "路径穿越/任意文件读取": [
            "对访问路径做规范化并强制限制在预期目录内，拒绝包含目录跳转片段的输入。",
            "服务端不要信任用户提交的相对路径、压缩包成员名或文件引用路径。",
            "对可读资源增加对象级授权校验，避免跨用户或跨任务读取。",
        ],
        "未授权访问/越权": [
            "在接口入口补全身份鉴别与对象级授权检查，禁止仅依赖前端可见性控制。",
            "对项目、任务、文件等资源的读取与下载增加属主校验。",
            "为高价值接口建立未授权访问监控与访问频率告警。",
        ],
        "SSRF": [
            "限制服务端可访问的协议、域名和地址段，阻断对内网与元数据地址的访问。",
            "对用户提供的目标地址做解析后校验，避免 DNS 重绑定和协议绕过。",
            "为外连请求增加最小权限网络出口策略和异常审计。",
        ],
        "XXE": [
            "关闭 XML 解析器的外部实体、DTD 与网络加载能力。",
            "若业务必须处理 XML，改用安全解析模式并限制可处理文档大小。",
            "对上传或导入的 XML 内容增加格式校验与隔离处理。",
        ],
        "跨站脚本": [
            "在输出位置按上下文做 HTML/属性/脚本级别编码，避免直接回显可控内容。",
            "对富文本和模板变量建立统一过滤策略，并补充 CSP 等缓解措施。",
            "对历史数据做一次性清理，避免已落库的恶意内容继续触发。",
        ],
        "信息泄露": [
            "收紧对敏感接口、调试信息和内部状态页的访问控制，默认不对外暴露。",
            "从响应中移除不必要的内部路径、版本、凭证线索和业务细节。",
            "补充最小暴露原则和基于角色的访问控制，避免匿名访问敏感内容。",
        ],
        "远程代码执行": [
            "从执行链路上移除对外部输入的直接控制能力，并对高危执行点做白名单约束。",
            "对承载执行能力的组件实施最小权限与隔离运行，避免突破后直接影响主机。",
            "增加进程级审计和异常行为检测，及时发现执行链路被触达的迹象。",
        ],
    }
    return mapping.get(
        vuln_type,
        [
            "梳理该漏洞命中的关键处理链路，明确外部输入进入点并补齐边界校验。",
            "对相关接口增加最小权限控制、输入约束与异常审计，避免同类问题重复出现。",
            "在修复后补充回归测试，确保同一攻击面不再出现等价绕过。",
        ],
    )


def _extract_json_payload(text: str) -> Any:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", candidate, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _evidence_score(value: str) -> tuple[int, int]:
    score = 0
    for pattern, weight in (
        (r"whoami|uid=|root|www-data|interactive shell", 80),
        (r"返回\s*200|\b200\b|直接返回|正文为|回显|Content-Type", 40),
        (r"已确认|真实存在|证明", 30),
        (r"失败|不可利用|未观察到|未发现|尚未", -60),
    ):
        if re.search(pattern, value, re.IGNORECASE):
            score += weight
    return (score, len(value))


def _first_line(value: str, limit: int) -> str:
    text = next((line.strip() for line in str(value or "").splitlines() if line.strip()), "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _string_list(values: list[Any]) -> list[str]:
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result[:8]


def _trim(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _severity_label(severity: str) -> str:
    return {
        "critical": "严重",
        "high": "高危",
        "medium": "中危",
        "low": "低危",
    }.get(severity, severity)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
