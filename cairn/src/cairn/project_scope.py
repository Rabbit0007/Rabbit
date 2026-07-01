from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import ipaddress
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import yaml

from cairn.project_context_files import DEFAULT_CONTEXT_FILENAME, project_context_path, resolve_context_root
from cairn.server.text_normalization import normalize_hint_content

LOCAL_HOST_ALIASES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "host.docker.internal",
        "gateway.docker.internal",
        "docker.for.mac.host.internal",
    }
)
SCOPE_BLOCKED_FACT_PREFIX = "范围策略阻断："
LOOPBACK_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)
PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fc00::/7"),
)
LINK_LOCAL_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)
METADATA_NETWORKS = (
    ipaddress.ip_network("169.254.169.254/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
)
URL_PATTERN = re.compile(r"https?://[^\s'\"<>()]+", re.I)
IP_PATTERN = re.compile(r"(?<![\w:])(?:\d{1,3}\.){3}\d{1,3}(?![\w:])")
HOST_PATTERN = re.compile(
    r"(?<![\w.-])(?:localhost|host\.docker\.internal|gateway\.docker\.internal|docker\.for\.mac\.host\.internal|"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})(?![\w.-])",
    re.I,
)
SINGLE_TARGET_HINT_MARKERS = (
    "只渗透",
    "只测",
    "只测试",
    "仅渗透",
    "仅测",
    "仅测试",
    "限定",
    "只允许",
    "only target",
    "only test",
    "scope only",
)
DENY_LOCAL_HINT_MARKERS = (
    "不要碰宿主机",
    "不要跑到宿主机",
    "不要访问宿主机",
    "禁止宿主机",
    "不要碰本机",
    "不要访问本机",
    "不要碰 localhost",
    "不要碰localhost",
    "不要访问localhost",
    "不要碰 127.0.0.1",
    "不要碰127.0.0.1",
    "禁止 localhost",
    "禁止 127.0.0.1",
)
DENY_PRIVATE_HINT_MARKERS = (
    "不要碰内网",
    "不要访问内网",
    "禁止内网",
    "不要碰私网",
    "不要访问私网",
    "禁止私网",
)


@dataclass(slots=True)
class HintRecord:
    id: str | None
    content: str
    creator: str | None = None
    created_at: str | None = None


@dataclass(slots=True)
class ScopeEvaluation:
    allowed: bool
    reasons: list[str]
    matched_targets: list[str]


def _load_yaml_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _context_root_default_file() -> Path | None:
    root = resolve_context_root()
    if root is None:
        return None
    return root / DEFAULT_CONTEXT_FILENAME


def _normalize_host(value: str) -> str:
    return value.strip().strip(".").lower()


def _normalize_target(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"http://{text}")
    host = parsed.hostname
    if host:
        return _normalize_host(host)
    return _normalize_host(text.split("/", 1)[0].split(":", 1)[0])


def _valid_ip(candidate: str) -> str | None:
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def extract_target_tokens(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in URL_PATTERN.finditer(text):
        host = urlparse(match.group(0)).hostname
        if host:
            normalized = _normalize_host(host)
            if normalized not in seen:
                seen.add(normalized)
                found.append(normalized)
    for match in IP_PATTERN.finditer(text):
        ip = _valid_ip(match.group(0))
        if ip and ip not in seen:
            seen.add(ip)
            found.append(ip)
    for match in HOST_PATTERN.finditer(text):
        host = _normalize_host(match.group(0))
        if host not in seen:
            seen.add(host)
            found.append(host)
    return found


def _safe_project_context(project_id: str, origin: str, goal: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project": {
            "project_id": project_id,
            "target_summary": origin,
            "goal": goal,
            "notes": "",
        },
        "authorization": {},
        "scope": {},
        "output": {},
        "prompt_preamble": "",
    }


def _apply_context_document(
    context: dict[str, Any],
    payload: dict[str, Any],
    *,
    allow_override: bool,
) -> dict[str, Any]:
    merged = deepcopy(context)
    for section in ("authorization", "scope", "output"):
        value = payload.get(section)
        if isinstance(value, dict):
            merged[section] = _deep_merge(merged.get(section, {}), value)
    project_value = payload.get("project")
    if isinstance(project_value, dict):
        merged["project"] = _deep_merge(merged.get("project", {}), project_value)
    preamble = payload.get("prompt_preamble")
    if isinstance(preamble, str) and preamble.strip():
        merged["prompt_preamble"] = preamble.strip()
    if allow_override:
        override = payload.get("override")
        if isinstance(override, dict):
            for section in ("authorization", "scope", "output"):
                value = override.get(section)
                if isinstance(value, dict):
                    merged[section] = _deep_merge(merged.get(section, {}), value)
    return merged


def load_project_context(project_id: str, origin: str, goal: str) -> dict[str, Any]:
    context = _safe_project_context(project_id, origin, goal)
    context = _apply_context_document(context, _load_yaml_file(_context_root_default_file()), allow_override=False)
    context = _apply_context_document(
        context,
        _load_yaml_file(project_context_path(project_id)),
        allow_override=True,
    )
    project_section = context.setdefault("project", {})
    if not project_section.get("project_id"):
        project_section["project_id"] = project_id
    if not project_section.get("target_summary"):
        project_section["target_summary"] = origin
    if not project_section.get("goal"):
        project_section["goal"] = goal
    project_section.setdefault("notes", "")
    return context


def normalize_hint_records(hints: Iterable[HintRecord | dict[str, Any]]) -> list[HintRecord]:
    normalized: list[HintRecord] = []
    for hint in hints:
        if isinstance(hint, HintRecord):
            normalized.append(
                HintRecord(
                    id=hint.id,
                    content=normalize_hint_content(hint.content),
                    creator=hint.creator.strip() if isinstance(hint.creator, str) else hint.creator,
                    created_at=hint.created_at,
                )
            )
            continue
        normalized.append(
            HintRecord(
                id=hint.get("id"),
                content=normalize_hint_content(str(hint.get("content", ""))),
                creator=str(hint.get("creator")).strip() if hint.get("creator") is not None else None,
                created_at=hint.get("created_at"),
            )
        )
    return normalized


def _context_scope_targets(scope: dict[str, Any]) -> set[str]:
    targets: set[str] = set()
    for key in ("allowed_targets", "allowed_hosts", "allowed_ips"):
        value = scope.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    normalized = _normalize_target(item)
                    if normalized:
                        targets.add(normalized)
    return targets


def _hint_scope_overrides(hints: list[HintRecord]) -> dict[str, Any]:
    restrict_to_declared_targets = False
    allowed_targets: set[str] = set()
    deny_local = False
    deny_private = False
    hard_rules: list[str] = []

    for hint in hints:
        content = hint.content
        lowered = content.lower()
        tokens = extract_target_tokens(content)
        if any(marker in content or marker in lowered for marker in SINGLE_TARGET_HINT_MARKERS) and tokens:
            restrict_to_declared_targets = True
            allowed_targets.update(tokens)
            hard_rules.append(f"只允许围绕声明目标推进：{', '.join(sorted(tokens))}")
        if "宿主机" in content or any(marker in content or marker in lowered for marker in DENY_LOCAL_HINT_MARKERS):
            deny_local = True
            hard_rules.append("禁止访问宿主机、本机、localhost、127.0.0.1 与 host.docker.internal。")
        if any(marker in content or marker in lowered for marker in DENY_PRIVATE_HINT_MARKERS):
            deny_private = True
            hard_rules.append("禁止扩展到未声明的私网/内网地址空间。")

    return {
        "restrict_to_declared_targets": restrict_to_declared_targets,
        "allowed_targets": sorted(allowed_targets),
        "deny_local": deny_local,
        "deny_private": deny_private,
        "hard_rules": hard_rules,
    }


def build_scope_policy(
    project_id: str,
    origin: str,
    goal: str,
    hints: Iterable[HintRecord | dict[str, Any]],
) -> dict[str, Any]:
    context = load_project_context(project_id, origin, goal)
    normalized_hints = normalize_hint_records(hints)
    scope = context.get("scope", {}) if isinstance(context.get("scope"), dict) else {}
    origin_targets = set(extract_target_tokens(origin))
    explicit_targets = _context_scope_targets(scope)
    hint_overrides = _hint_scope_overrides(normalized_hints)

    restrict_to_declared_targets = bool(scope.get("restrict_to_origin_targets", False))
    if hint_overrides["restrict_to_declared_targets"]:
        restrict_to_declared_targets = True

    allowed_targets = set(origin_targets)
    allowed_targets.update(explicit_targets)
    allowed_targets.update(hint_overrides["allowed_targets"])

    allow_loopback = bool(scope.get("allow_loopback_targets", False))
    allow_private = bool(scope.get("allow_private_targets", False))
    allow_link_local = bool(scope.get("allow_link_local_targets", False))
    if hint_overrides["deny_local"]:
        allow_loopback = False
        allow_link_local = False
    if hint_overrides["deny_private"]:
        allow_private = False

    blocked_hosts = set(LOCAL_HOST_ALIASES)
    value = scope.get("blocked_hosts")
    if isinstance(value, list):
        blocked_hosts.update(_normalize_target(item) for item in value if isinstance(item, str))

    hard_rules = [
        "Hints are not facts. Treat operator-provided paths and parameters as unverified assertions until evidence confirms them.",
        "Never treat localhost, 127.0.0.0/8, ::1, host.docker.internal, link-local, metadata endpoints, or host-side artifacts as target facts unless the project scope explicitly allows them.",
    ]
    if restrict_to_declared_targets and allowed_targets:
        hard_rules.append(f"Only pursue declared targets: {', '.join(sorted(allowed_targets))}.")
    hard_rules.extend(rule for rule in hint_overrides["hard_rules"] if rule not in hard_rules)

    assertions = [
        {
            "id": hint.id,
            "content": hint.content,
            "creator": hint.creator,
            "created_at": hint.created_at,
            "source": "user-hint",
            "confidence": "asserted",
            "requires_verification": True,
        }
        for hint in normalized_hints
    ]
    return {
        "project_context": context,
        "scope_policy": {
            "restrict_to_declared_targets": restrict_to_declared_targets,
            "allowed_targets": sorted(allowed_targets),
            "allow_loopback_targets": allow_loopback,
            "allow_private_targets": allow_private,
            "allow_link_local_targets": allow_link_local,
            "blocked_hosts": sorted(blocked_hosts),
            "hard_rules": hard_rules,
        },
        "user_assertions": assertions,
    }


def _classify_ip_target(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _target_allowed(target: str, allowed_targets: set[str]) -> bool:
    return _normalize_target(target) in allowed_targets


def evaluate_text_scope(
    project_id: str,
    origin: str,
    goal: str,
    hints: Iterable[HintRecord | dict[str, Any]],
    text: str,
) -> ScopeEvaluation:
    policy_bundle = build_scope_policy(project_id, origin, goal, hints)
    policy = policy_bundle["scope_policy"]
    allowed_targets = {_normalize_target(item) for item in policy.get("allowed_targets", [])}
    blocked_hosts = {_normalize_target(item) for item in policy.get("blocked_hosts", [])}
    restrict_to_declared_targets = bool(policy.get("restrict_to_declared_targets", False))
    allow_loopback = bool(policy.get("allow_loopback_targets", False))
    allow_private = bool(policy.get("allow_private_targets", False))
    allow_link_local = bool(policy.get("allow_link_local_targets", False))

    reasons: list[str] = []
    matched: list[str] = []
    seen_reasons: set[str] = set()
    for token in extract_target_tokens(text):
        normalized = _normalize_target(token)
        target_ip = _classify_ip_target(normalized)
        if target_ip is not None:
            if any(target_ip in network for network in METADATA_NETWORKS):
                if "metadata" not in seen_reasons:
                    reasons.append("metadata")
                    seen_reasons.add("metadata")
                matched.append(normalized)
                continue
            if any(target_ip in network for network in LOOPBACK_NETWORKS):
                if not allow_loopback and not _target_allowed(normalized, allowed_targets):
                    if "loopback" not in seen_reasons:
                        reasons.append("loopback")
                        seen_reasons.add("loopback")
                    matched.append(normalized)
                    continue
            if any(target_ip in network for network in LINK_LOCAL_NETWORKS):
                if not allow_link_local and not _target_allowed(normalized, allowed_targets):
                    if "link-local" not in seen_reasons:
                        reasons.append("link-local")
                        seen_reasons.add("link-local")
                    matched.append(normalized)
                    continue
            if any(target_ip in network for network in PRIVATE_NETWORKS):
                if not allow_private and not _target_allowed(normalized, allowed_targets):
                    if "private" not in seen_reasons:
                        reasons.append("private")
                        seen_reasons.add("private")
                    matched.append(normalized)
                    continue
            if restrict_to_declared_targets and allowed_targets and not _target_allowed(normalized, allowed_targets):
                if "out-of-scope-target" not in seen_reasons:
                    reasons.append("out-of-scope-target")
                    seen_reasons.add("out-of-scope-target")
                matched.append(normalized)
            continue

        if normalized in blocked_hosts and not _target_allowed(normalized, allowed_targets):
            if "localhost-alias" not in seen_reasons:
                reasons.append("localhost-alias")
                seen_reasons.add("localhost-alias")
            matched.append(normalized)
            continue
        if restrict_to_declared_targets and allowed_targets and normalized and normalized not in allowed_targets:
            if "out-of-scope-target" not in seen_reasons:
                reasons.append("out-of-scope-target")
                seen_reasons.add("out-of-scope-target")
            matched.append(normalized)

    lower = text.lower()
    if "宿主机" in text and "host-machine" not in seen_reasons:
        reasons.append("host-machine")
        seen_reasons.add("host-machine")
    if any(alias in lower for alias in LOCAL_HOST_ALIASES) and "localhost-alias" not in seen_reasons:
        reasons.append("localhost-alias")
        seen_reasons.add("localhost-alias")

    return ScopeEvaluation(
        allowed=not reasons,
        reasons=reasons,
        matched_targets=sorted(set(matched)),
    )


def scope_blocked_fact_description(
    project_id: str,
    origin: str,
    goal: str,
    hints: Iterable[HintRecord | dict[str, Any]],
) -> str:
    policy_bundle = build_scope_policy(project_id, origin, goal, hints)
    allowed_targets = policy_bundle["scope_policy"].get("allowed_targets") or extract_target_tokens(origin)
    target_clause = ""
    if allowed_targets:
        target_clause = f"；后续仅允许围绕声明目标 {', '.join(allowed_targets)} 继续推进"
    return (
        f"{SCOPE_BLOCKED_FACT_PREFIX}本次探索触达了宿主机、本地地址、私网或其他非声明目标环境伪影，"
        "该结果不计入目标事实，不应继续沿此方向扩展"
        f"{target_clause}。"
    )


def scope_violation_detail(result: ScopeEvaluation) -> str:
    reason_text = ", ".join(result.reasons) if result.reasons else "unknown"
    matched = ", ".join(result.matched_targets) if result.matched_targets else "n/a"
    return f"scope_violation reasons={reason_text} matched={matched}"


def is_scope_blocked_fact(description: str) -> bool:
    return description.strip().startswith(SCOPE_BLOCKED_FACT_PREFIX)
