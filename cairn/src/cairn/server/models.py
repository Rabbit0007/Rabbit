from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from cairn.server.text_normalization import normalize_hint_content


class Settings(BaseModel):
    step_timeout: int = Field(ge=5)
    decide_timeout: int = Field(ge=5)
    worker_unhealthy_retry_after_seconds: int = Field(ge=1, le=3600)
    worker_rejected_retry_after_seconds: int = Field(ge=1, le=3600)
    max_failed_login_attempts: int = Field(ge=1, le=50)
    rate_limit_window_minutes: int = Field(ge=1, le=1440)
    session_duration_hours: int = Field(ge=1, le=168)
    log_retention_days: int = Field(ge=1, le=3650)
    export_retention_days: int = Field(ge=1, le=3650)
    notification_retention_days: int = Field(ge=1, le=3650)
    project_idle_alert_hours: int = Field(ge=1, le=720)

    @property
    def intent_timeout(self) -> int:
        return self.step_timeout

    @property
    def reason_timeout(self) -> int:
        return self.decide_timeout


class Fact(BaseModel):
    id: str
    description: str


class Goal(BaseModel):
    """A goal or sub-goal that describes a completion condition."""
    id: str
    description: str
    parent_goal_id: str | None = None
    status: Literal["active", "completed"] = "active"
    priority: int = 0
    created_at: str
    completed_at: str | None = None
    completion_description: str | None = None
    completed_by: str | None = None
    from_: list[str] = Field(default_factory=list, alias="from")

    model_config = {"populate_by_name": True}


class Step(BaseModel):
    """A step describing how to produce new facts from existing facts."""
    id: str
    from_: list[str] = Field(alias="from")
    to: str | None = None
    description: str
    goal_id: str | None = None
    priority: int = 0
    creator: str
    worker: str | None = None
    last_heartbeat_at: str | None = None
    created_at: str
    concluded_at: str | None = None
    abandoned: bool = False

    model_config = {"populate_by_name": True}


class Finding(BaseModel):
    """A durable, structured artifact discovered during the search."""
    id: str
    title: str
    description: str
    severity: Literal["critical", "high", "medium", "low", "info"] = "info"
    kind: str = "finding"
    data: dict[str, Any] = Field(default_factory=dict)
    fact_id: str | None = None
    created_at: str


class Hint(BaseModel):
    id: str
    content: str
    creator: str
    created_at: str

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        text = normalize_hint_content(value)
        if not text:
            raise ValueError("must not be empty")
        return text


# ── Backward-compat aliases ────────────────────────────────────────────
Intent = Step


class ProjectDecide(BaseModel):
    """Decide activity lease on a project."""
    worker: str
    trigger: str
    started_at: str
    last_heartbeat_at: str


ProjectReason = ProjectDecide


class ProjectMeta(BaseModel):
    id: str
    title: str
    status: Literal["active", "stopped", "completed"]
    bootstrap_enabled: bool
    created_at: str
    decide: ProjectDecide | None = None


class ProjectSummary(ProjectMeta):
    fact_count: int
    intent_count: int
    working_intent_count: int
    unclaimed_intent_count: int
    step_count: int = 0
    working_step_count: int = 0
    unclaimed_step_count: int = 0
    goal_count: int = 0
    finding_count: int = 0
    hint_count: int


class ProjectDetail(BaseModel):
    project: ProjectMeta
    facts: list[Fact]
    steps: list[Step]
    goals: list[Goal]
    findings: list[Finding]
    hints: list[Hint]


# ── Backward-compat: expose intents as steps ───────────────────────────
def _project_detail_intents_get(self: ProjectDetail) -> list[Step]:
    return self.steps


ProjectDetail.intents = property(_project_detail_intents_get)


class CreateHintInline(BaseModel):
    content: str
    creator: str

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        text = normalize_hint_content(value)
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateProjectRequest(BaseModel):
    title: str
    origin: str
    goal: str
    hints: list[CreateHintInline] | None = None

    @field_validator("title", "origin", "goal")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text



class CreateHintRequest(BaseModel):
    content: str
    creator: str

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        text = normalize_hint_content(value)
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateGoalRequest(BaseModel):
    description: str
    parent_goal_id: str | None = None
    priority: int = 0
    creator: str

    @field_validator("description", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateStepRequest(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    description: str
    goal_id: str | None = None
    priority: int = 0
    creator: str
    worker: str | None = None

    model_config = {"populate_by_name": True}

    @field_validator("description", "creator", "worker")
    @classmethod
    def validate_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("from_")
    @classmethod
    def validate_fact_ids(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("fact ids must not be empty")
            cleaned.append(text)
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("fact ids must not contain duplicates")
        return cleaned


CreateIntentRequest = CreateStepRequest


class UpdateStepRequest(BaseModel):
    priority: int | None = None
    goal_id: str | None = None
    abandoned: Literal[True] | None = None

    model_config = {"extra": "forbid"}


class UpdateGoalRequest(BaseModel):
    priority: int | None = None
    description: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("description")
    @classmethod
    def validate_optional_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateFindingRequest(BaseModel):
    title: str
    description: str
    severity: Literal["critical", "high", "medium", "low", "info"] = "info"
    kind: str = "finding"
    data: dict[str, Any] = Field(default_factory=dict)
    fact_id: str | None = None

    @field_validator("title", "description", "kind")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class HeartbeatRequest(BaseModel):
    worker: str

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class DecideClaimRequest(BaseModel):
    worker: str
    trigger: str

    @field_validator("worker", "trigger")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


ReasonClaimRequest = DecideClaimRequest


class ConcludeRequest(BaseModel):
    worker: str
    description: str
    finding: CreateFindingRequest | None = None

    @field_validator("worker", "description")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CompleteRequest(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    description: str
    worker: str

    model_config = {"populate_by_name": True}

    @field_validator("description", "worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("from_")
    @classmethod
    def validate_fact_ids(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("fact ids must not be empty")
            cleaned.append(text)
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("fact ids must not contain duplicates")
        return cleaned


class ConcludeResponse(BaseModel):
    fact: Fact | None = None
    step: Step | None = None
    finding: Finding | None = None


class UpdateProjectStatusRequest(BaseModel):
    status: Literal["active", "stopped"]


class UpdateProjectTitleRequest(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReopenRequest(BaseModel):
    description: str
    creator: str

    @field_validator("description", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReopenResponse(BaseModel):
    project: ProjectMeta
    fact: Fact
    step: Step
    goal: Goal | None = None


class CompleteGoalRequest(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    description: str
    worker: str

    model_config = {"populate_by_name": True}

    @field_validator("description", "worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("from_")
    @classmethod
    def validate_fact_ids(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("fact ids must not be empty")
            cleaned.append(text)
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("fact ids must not contain duplicates")
        return cleaned
