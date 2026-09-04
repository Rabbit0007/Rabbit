from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import os
import re
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TaskType = Literal["decide", "execute"]
WorkerType = Literal["claudecode", "codex", "pi", "mock"]
CompletedAction = Literal["remove", "stop"]
WorkerHealthcheckMode = Literal["startup_and_task", "startup_only", "disabled"]
ExecutionMode = Literal["container", "local"]
LocalCompletedAction = Literal["keep", "remove"]

WORKER_ENV_KEYS: dict[WorkerType, tuple[str, ...]] = {
    "claudecode": (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
    ),
    "codex": (
        "CODEX_MODEL",
        "CODEX_BASE_URL",
        "OPENAI_API_KEY",
    ),
    "pi": (
        "PI_MODEL",
        "PI_BASE_URL",
        "PI_API_KEY",
        "PI_PROVIDER_API",
    ),
    "mock": (),
}

DEFAULT_PROMPT_REQUIRED_TOKENS: dict[str, tuple[str, ...]] = {
    "decide.md": ("{graph_yaml}", "{fact_ids}", "{open_steps}", "{max_steps}"),
    "execute.md": ("{graph_yaml}", "{step_id}", "{step_description}"),
    "execute_conclude.md": ("{graph_yaml}", "{step_id}", "{step_description}"),
}

PROMPT_REQUIRED_TOKENS_BY_GROUP: dict[str, dict[str, tuple[str, ...]]] = {
    "mock": {
        "decide.md": ("{fact_ids}", "{open_steps}", "{max_steps}"),
        "execute.md": ("{step_id}",),
        "execute_conclude.md": ("{step_id}",),
    }
}

MOCK_ALLOWED_OUTCOMES: dict[str, frozenset[str]] = {
    "healthcheck": frozenset({"ok", "fail"}),
    "decide": frozenset({"complete", "steps", "noop", "rejected", "invalid_json", "invalid_payload", "command_fail"}),
    "execute": frozenset({"fact", "fact_with_finding", "rejected", "invalid_json", "invalid_payload", "command_fail"}),
    "execute_conclude": frozenset({"fact", "rejected", "invalid_json", "invalid_payload", "command_fail"}),
}

MOCK_DEFAULT_BEHAVIOR: dict[str, dict[str, Any]] = {
    "healthcheck": {
        "delay": [0.05, 0.15],
        "outcomes": {"ok": "1.0", "fail": "0.0"},
    },
    "decide": {
        "delay": [0.05, 0.3],
        "outcomes": {
            "complete": "0.0",
            "steps": "1.0",
            "noop": "0.0",
            "rejected": "0.0",
            "invalid_json": "0.0",
            "invalid_payload": "0.0",
            "command_fail": "0.0",
        },
    },
    "execute": {
        "delay": [0.05, 0.3],
        "outcomes": {
            "fact": "1.0",
            "fact_with_finding": "0.0",
            "rejected": "0.0",
            "invalid_json": "0.0",
            "invalid_payload": "0.0",
            "command_fail": "0.0",
        },
    },
    "execute_conclude": {
        "delay": [0.05, 0.3],
        "outcomes": {
            "fact": "1.0",
            "rejected": "0.0",
            "invalid_json": "0.0",
            "invalid_payload": "0.0",
            "command_fail": "0.0",
        },
    },
}

MOCK_ALLOWED_ENV_KEYS = frozenset(
    {f"MOCK_{phase.upper()}" for phase in MOCK_ALLOWED_OUTCOMES}
)


class DecideTaskConfig(BaseModel):
    timeout: int = Field(gt=0)
    max_steps: int = Field(gt=0, default=3)


class ExecuteTaskConfig(BaseModel):
    timeout: int = Field(gt=0)
    conclude_timeout: int = Field(gt=0)


class TasksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decide: DecideTaskConfig
    execute: ExecuteTaskConfig

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_tasks(cls, value: Any) -> Any:
        """Convert a persisted Cairn task block into Cairn-Y once at load time."""
        if not isinstance(value, dict) or "decide" in value or "execute" in value:
            return value
        reason = value.get("reason") if isinstance(value.get("reason"), dict) else {}
        explore = value.get("explore") if isinstance(value.get("explore"), dict) else {}
        bootstrap = value.get("bootstrap") if isinstance(value.get("bootstrap"), dict) else {}
        decide = {
            "timeout": reason.get("timeout", bootstrap.get("timeout")),
            "max_steps": reason.get("max_steps", reason.get("max_intents", 3)),
        }
        execute_source = explore or bootstrap
        execute = {
            "timeout": execute_source.get("timeout"),
            "conclude_timeout": execute_source.get("conclude_timeout", execute_source.get("timeout")),
        }
        return {"decide": decide, "execute": execute}


class ContainerConfig(BaseModel):
    image: str
    network_mode: str
    completed_action: CompletedAction
    cap_add: list[str] = Field(default_factory=list)
    # Rabbit runs beside other Cairn deployments on the same Docker daemon.
    # Keep its worker namespace and host aliases configurable instead of using
    # the upstream Cairn defaults.
    name_prefix: str = Field(default="cairn-dispatch-", min_length=1)
    startup_name_prefix: str = Field(
        default="cairn-startup-healthcheck-", min_length=1
    )
    extra_hosts: dict[str, str] = Field(default_factory=dict)


class LocalConfig(BaseModel):
    workspace_root: str | None = None
    completed_action: LocalCompletedAction = "keep"


class RuntimeConfig(BaseModel):
    max_workers: int = Field(gt=0)
    max_running_projects: int = Field(gt=0)
    max_project_workers: int = Field(gt=0)
    interval: int = Field(gt=0)
    healthcheck_timeout: int = Field(gt=0)
    worker_healthcheck: WorkerHealthcheckMode = "startup_only"
    execution: ExecutionMode = "container"
    prompt_group: str = Field(min_length=1)


class WorkerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: WorkerType
    enabled: bool = True
    task_types: list[TaskType]
    max_running: int = Field(gt=0)
    priority: int = Field(ge=0)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("task_types", mode="before")
    @classmethod
    def validate_task_types(cls, value):
        # One-way config migration. Runtime state only ever sees decide/execute.
        if isinstance(value, list):
            migrated: list[str] = []
            mapping = {"reason": ("decide",), "explore": ("execute",), "bootstrap": ("decide", "execute")}
            for item in value:
                for canonical in mapping.get(item, (item,)):
                    if canonical not in migrated:
                        migrated.append(canonical)
            value = migrated
        if not value:
            raise ValueError("task_types must not be empty")
        if len(set(value)) != len(value):
            raise ValueError("task_types must be unique")
        return value

    @model_validator(mode="after")
    def validate_env(self) -> "WorkerConfig":
        if self.type == "pi":
            provider_api = self.env.get("PI_PROVIDER_API")
            if provider_api in {"openai", "openai-chat-completions"}:
                # Pi's custom-provider schema calls the Chat Completions
                # adapter ``openai-completions``.  Accept common operator
                # spellings but keep one canonical value at runtime.
                self.env = {**self.env, "PI_PROVIDER_API": "openai-completions"}
            _validate_optional_positive_int_env(self.name, self.env, "PI_MODEL_CONTEXT_WINDOW")
        if self.type == "mock":
            resolve_mock_behavior(self.name, self.env)
        return self


class DispatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: str
    runtime: RuntimeConfig
    tasks: TasksConfig
    container: ContainerConfig | None = None
    local: LocalConfig | None = None
    common_env: dict[str, str] = Field(default_factory=dict)
    workers: list[WorkerConfig]

    @model_validator(mode="before")
    @classmethod
    def merge_common_env(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        common_env = data.get("common_env")
        if common_env is None:
            common_env = {}
        workers = data.get("workers")
        if not isinstance(common_env, dict) or not isinstance(workers, list):
            return data

        merged = dict(data)
        merged_workers: list[Any] = []
        for worker in workers:
            if not isinstance(worker, dict):
                merged_workers.append(worker)
                continue
            worker_env = worker.get("env")
            if worker_env is None:
                worker_env = {}
            if not isinstance(worker_env, dict):
                merged_workers.append(worker)
                continue
            worker_copy = dict(worker)
            worker_copy["env"] = {**common_env, **worker_env}
            merged_workers.append(worker_copy)
        merged["workers"] = merged_workers
        return merged

    @model_validator(mode="after")
    def validate_workers(self) -> "DispatchConfig":
        names = [worker.name for worker in self.workers]
        if len(set(names)) != len(names):
            raise ValueError("worker names must be unique")
        if not self.workers:
            raise ValueError("workers must not be empty")
        if self.runtime.max_project_workers > self.runtime.max_workers:
            raise ValueError("max_project_workers cannot exceed max_workers")
        for activity in ("decide", "execute"):
            if not any(worker.enabled and activity in worker.task_types for worker in self.workers):
                raise ValueError(f"at least one worker must support {activity}")
        return self

    @model_validator(mode="after")
    def validate_execution_mode(self) -> "DispatchConfig":
        if self.runtime.execution == "container":
            if self.container is None:
                raise ValueError("container config is required when runtime.execution is container")
            for worker in self.workers:
                required = WORKER_ENV_KEYS[worker.type]
                missing = [key for key in required if not worker.env.get(key)]
                if missing:
                    raise ValueError(f"worker {worker.name} missing env keys: {', '.join(missing)}")
        else:  # local: workers reuse the host CLI config
            if self.local is None:
                self.local = LocalConfig()
        return self

    @classmethod
    def load(cls, path: Path) -> "DispatchConfig":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        _expand_env_placeholders(data)
        config = cls.model_validate(data)
        validate_prompt_resources(config.runtime.prompt_group)
        return config


_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            value[key] = _expand_env_placeholders(item)
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _expand_env_placeholders(item)
        return value
    if isinstance(value, str):
        return _ENV_PLACEHOLDER_RE.sub(
            lambda match: os.environ.get(match.group(1), match.group(0)), value
        )
    return value


def _validate_optional_positive_int_env(worker_name: str, env: dict[str, str], key: str) -> None:
    value = env.get(key)
    if value is None or not value.strip():
        return
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"worker {worker_name} env {key} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"worker {worker_name} env {key} must be greater than 0")


def validate_prompt_resources(prompt_group: str) -> None:
    prompts_dir = resources.files("cairn.dispatcher.prompts")
    group_dir = prompts_dir.joinpath(prompt_group)
    if not group_dir.is_dir():
        raise ValueError(f"missing prompt group: {prompt_group}")
    required_tokens = PROMPT_REQUIRED_TOKENS_BY_GROUP.get(prompt_group, DEFAULT_PROMPT_REQUIRED_TOKENS)
    for name, tokens in required_tokens.items():
        try:
            content = group_dir.joinpath(name).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # New-style prompts may not exist in mock group
        missing = [token for token in tokens if token not in content]
        if missing:
            raise ValueError(f"prompt group {prompt_group} resource {name} missing placeholders: {', '.join(missing)}")


def resolve_mock_behavior(worker_name: str, env: dict[str, str]) -> dict[str, dict[str, Any]]:
    unknown = sorted(key for key in env if key.startswith("MOCK_") and key not in MOCK_ALLOWED_ENV_KEYS)
    if unknown:
        raise ValueError(f"worker {worker_name} has unsupported mock env keys: {', '.join(unknown)}")

    behavior: dict[str, dict[str, Any]] = {}
    for phase, allowed_outcomes in MOCK_ALLOWED_OUTCOMES.items():
        prefix = _mock_env_prefix(phase)
        payload = _parse_mock_phase_payload(worker_name, env, prefix, MOCK_DEFAULT_BEHAVIOR[phase])
        min_delay, max_delay = _parse_mock_delay_range(worker_name, prefix, payload.get("delay"))
        if max_delay < min_delay:
            raise ValueError(f"worker {worker_name} {prefix}.delay[1] must be >= delay[0]")
        raw_outcomes = payload.get("outcomes")
        if not isinstance(raw_outcomes, dict):
            raise ValueError(f"worker {worker_name} {prefix}.outcomes must be an object")
        unknown_outcomes = sorted(set(raw_outcomes) - allowed_outcomes)
        if unknown_outcomes:
            raise ValueError(f"worker {worker_name} {prefix}.outcomes has unsupported keys: {', '.join(unknown_outcomes)}")
        outcomes: dict[str, float] = {}
        total = Decimal("0")
        for outcome in sorted(allowed_outcomes):
            weight = _parse_mock_probability(worker_name, prefix, raw_outcomes, outcome)
            outcomes[outcome] = float(weight)
            total += weight
        if total != Decimal("1"):
            raise ValueError(f"worker {worker_name} {prefix}.outcomes probabilities must sum to 1.0, got {total}")
        behavior[phase] = {
            "delay": {"min": min_delay, "max": max_delay},
            "outcomes": outcomes,
        }
        rules = payload.get("rules")
        if rules is not None:
            if not isinstance(rules, list):
                raise ValueError(f"worker {worker_name} {prefix}.rules must be an array")
            normalized_rules: list[dict[str, Any]] = []
            for index, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    raise ValueError(f"worker {worker_name} {prefix}.rules[{index}] must be an object")
                force = rule.get("force")
                if not isinstance(force, str) or force not in allowed_outcomes:
                    raise ValueError(
                        f"worker {worker_name} {prefix}.rules[{index}].force must be one of: {', '.join(sorted(allowed_outcomes))}"
                    )
                entry: dict[str, Any] = {"force": force}
                if "fact_ids_gte" in rule:
                    value = rule["fact_ids_gte"]
                    if not isinstance(value, int) or value < 0:
                        raise ValueError(f"worker {worker_name} {prefix}.rules[{index}].fact_ids_gte must be a non-negative integer")
                    entry["fact_ids_gte"] = value
                if "fact_ids_lte" in rule:
                    value = rule["fact_ids_lte"]
                    if not isinstance(value, int) or value < 0:
                        raise ValueError(f"worker {worker_name} {prefix}.rules[{index}].fact_ids_lte must be a non-negative integer")
                    entry["fact_ids_lte"] = value
                if "open_steps_empty" in rule:
                    value = rule["open_steps_empty"]
                    if not isinstance(value, bool):
                        raise ValueError(f"worker {worker_name} {prefix}.rules[{index}].open_steps_empty must be boolean")
                    entry["open_steps_empty"] = value
                normalized_rules.append(entry)
            behavior[phase]["rules"] = normalized_rules
    return behavior


def _mock_env_prefix(phase: str) -> str:
    return f"MOCK_{phase.upper()}"


def _parse_mock_phase_payload(worker_name: str, env: dict[str, str], key: str, default: dict[str, Any]) -> dict[str, Any]:
    raw = env.get(key)
    if raw is None:
        return json.loads(json.dumps(default))
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"worker {worker_name} {key} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"worker {worker_name} {key} must be a JSON object")
    return value


def _parse_mock_delay_range(worker_name: str, key: str, value: Any) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"worker {worker_name} {key}.delay must be a two-element number array")
    min_delay = _coerce_mock_seconds(worker_name, f"{key}.delay[0]", value[0])
    max_delay = _coerce_mock_seconds(worker_name, f"{key}.delay[1]", value[1])
    return min_delay, max_delay


def _coerce_mock_seconds(worker_name: str, key: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"worker {worker_name} {key} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"worker {worker_name} {key} must be a number") from exc
    if parsed < 0:
        raise ValueError(f"worker {worker_name} {key} must be non-negative")
    return parsed


def _parse_mock_probability(worker_name: str, phase_key: str, outcomes: dict[str, Any], outcome: str) -> Decimal:
    raw = outcomes.get(outcome, MOCK_DEFAULT_BEHAVIOR[phase_key.removeprefix("MOCK_").lower()]["outcomes"][outcome])
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError(f"worker {worker_name} {phase_key}.outcomes.{outcome} must be a decimal probability") from exc
    if value < 0 or value > 1:
        raise ValueError(f"worker {worker_name} {phase_key}.outcomes.{outcome} must be between 0 and 1")
    return value
