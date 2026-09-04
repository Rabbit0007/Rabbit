from __future__ import annotations

from dataclasses import dataclass, field

from cairn.dispatcher.config import DispatchConfig
from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.workers.base import DriverResult
from cairn.dispatcher.workers.health import HealthResult
from cairn.server.models import (
    Fact, Goal, Hint, Step, ProjectDetail, ProjectMeta,
)


TEST_INTERNAL_TOKEN = "cairn-y-core-test-token"


def configure_authenticated_test_client(monkeypatch) -> dict[str, str]:
    """Enable Rabbit's machine-auth path for copied Cairn-Y API tests.

    Rabbit intentionally protects the product API.  The Cairn-Y core suite is
    exercising FGS behaviour rather than browser login, so it authenticates as
    an in-process dispatcher instead of weakening the application middleware.
    """
    monkeypatch.setenv("CAIRN_INTERNAL_TOKEN", TEST_INTERNAL_TOKEN)
    return {"X-Cairn-Internal-Token": TEST_INTERNAL_TOKEN}


def make_config() -> DispatchConfig:
    return DispatchConfig.model_validate(
        {
            "server": "http://127.0.0.1:8000",
            "runtime": {
                "interval": 60,
                "max_workers": 2,
                "max_running_projects": 1,
                "max_project_workers": 2,
                "healthcheck_timeout": 5,
                "prompt_group": "default",
            },
            "tasks": {
                "decide": {"timeout": 10, "max_steps": 3},
                "execute": {"timeout": 10, "conclude_timeout": 5},
            },
            "container": {
                "image": "test-image",
                "network_mode": "host",
                "completed_action": "stop",
            },
            "workers": [
                {
                    "name": "test-worker",
                    "type": "mock",
                    "task_types": ["decide", "execute"],
                    "max_running": 1,
                    "priority": 0,
                }
            ],
        }
    )


def make_project(*, steps: list[Step] | None = None) -> ProjectDetail:
    return ProjectDetail(
        project=ProjectMeta(
            id="proj_001",
            title="test",
            status="active",
            bootstrap_enabled=True,
            created_at="2026-01-01T00:00:00Z",
        ),
        facts=[
            Fact(id="origin", description="start"),
            Fact(id="f001", description="known fact"),
        ],
        steps=steps or [],
        goals=[
            Goal(id="g001", description="finish", status="active",
                 priority=0, created_at="2026-01-01T00:00:00Z"),
        ],
        findings=[],
        hints=[
            Hint(
                id="h001",
                content="use the clue",
                creator="human",
                created_at="2026-01-01T00:00:01Z",
            )
        ],
    )


def make_step(step_id: str = "s001") -> Step:
    return Step(
        id=step_id,
        from_=["f001"],
        description="investigate",
        creator="decider",
        worker="test-worker",
        priority=0,
        created_at="2026-01-01T00:00:02Z",
    )


# Backward compat
make_intent = make_step


class FakeLease:
    def __init__(self) -> None:
        self.failure = None
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def attach_process(self, _process) -> None:
        return None


@dataclass
class FakeContainerManager:
    writes: list[tuple[str, str, str]] = field(default_factory=list)

    def ensure_running(self, project_id: str) -> str:
        return f"container-{project_id}"

    def write_text_file(self, container_name: str, path: str, content: str) -> None:
        self.writes.append((container_name, path, content))


@dataclass
class FakeClient:
    project: ProjectDetail
    concluded: list[tuple[str, str, str, str]] = field(default_factory=list)
    completed: list[tuple[str, list[str], str, str]] = field(default_factory=list)
    created_steps: list[tuple[str, list[str], str, str]] = field(default_factory=list)
    created_intents: list[tuple[str, list[str], str, str]] = field(default_factory=list)
    released: list[tuple[str, str, str]] = field(default_factory=list)
    released_decides: list[tuple[str, str]] = field(default_factory=list)
    released_reasons: list[tuple[str, str]] = field(default_factory=list)

    def get_project(self, _project_id: str) -> ProjectDetail:
        return self.project

    def conclude_step(self, project_id: str, step_id: str, worker: str, description: str, finding=None) -> ApiResult:
        self.concluded.append((project_id, step_id, worker, description))
        return ApiResult(200, {"fact": {"id": "f002"}})

    conclude = conclude_step

    def complete(self, project_id: str, from_ids: list[str], description: str, worker: str) -> ApiResult:
        self.completed.append((project_id, from_ids, description, worker))
        return ApiResult(200, {})

    def create_step(self, project_id: str, from_ids: list[str], description: str, creator: str,
                    goal_id=None, priority=0) -> ApiResult:
        self.created_steps.append((project_id, from_ids, description, creator))
        return ApiResult(201, {})

    create_intent = create_step

    def release_step(self, project_id: str, step_id: str, worker: str) -> ApiResult:
        self.released.append((project_id, step_id, worker))
        return ApiResult(200, {})

    release = release_step

    def release_decide(self, project_id: str, worker: str) -> ApiResult:
        self.released_decides.append((project_id, worker))
        return ApiResult(200, {})

    release_reason = release_decide

    def step_heartbeat(self, _project_id: str, _step_id: str, _worker: str) -> ApiResult:
        return ApiResult(200, {})

    heartbeat = step_heartbeat

    def decide_heartbeat(self, _project_id: str, _worker: str) -> ApiResult:
        return ApiResult(200, {})

    reason_heartbeat = decide_heartbeat

    def claim_decide(self, _project_id: str, _worker: str, _trigger: str) -> ApiResult:
        return ApiResult(200, {})

    claim_reason = claim_decide

    def complete_goal(self, _project_id: str, _goal_id: str, _from_ids: list[str],
                      _description: str, _worker: str) -> ApiResult:
        return ApiResult(200, {})

    def update_step(self, _project_id: str, _step_id: str, priority=None, goal_id=None, abandoned=None) -> ApiResult:
        return ApiResult(200, {})

    def create_goal(self, _project_id: str, _description: str, _creator: str,
                    parent_goal_id=None, priority=0) -> ApiResult:
        return ApiResult(201, {})

    def update_goal(self, _project_id: str, _goal_id: str, priority=None) -> ApiResult:
        return ApiResult(200, {})

    def delete_goal(self, _project_id: str, _goal_id: str) -> ApiResult:
        return ApiResult(204, {})


class FakeDriver:
    def __init__(self) -> None:
        self.execute_prompts: list[str] = []
        self.conclude_prompts: list[str] = []
        self.health = HealthResult(ok=True, status=200, detail="")

    def supports_conclude(self) -> bool:
        return True

    def prepare_session(self) -> str:
        return "session-001"

    def check_health(self, _worker, *, timeout: float) -> HealthResult:
        return self.health

    def build_execute(self, _worker, prompt: str, session: str | None) -> DriverResult:
        self.execute_prompts.append(prompt)
        return DriverResult(["execute"], session=session)

    def build_decide(self, _worker, prompt: str, session: str | None) -> DriverResult:
        self.execute_prompts.append(prompt)
        return DriverResult(["decide"], session=session)

    def build_conclude(self, _worker, prompt: str, _session: str) -> list[str]:
        self.conclude_prompts.append(prompt)
        return ["conclude"]

    def extract_session(self, session: str | None, _stdout: str, _stderr: str) -> str | None:
        return session

    def extract_response_text(self, stdout: str, _stderr: str) -> str:
        return stdout
