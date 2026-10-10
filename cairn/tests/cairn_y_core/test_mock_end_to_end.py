from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import os
import subprocess
import threading
from typing import Any

from fastapi.testclient import TestClient
from pydantic import TypeAdapter
import pytest

from cairn.dispatcher.config import DispatchConfig
from cairn.dispatcher.models import DecideCheckpoint, RunningTask
from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.process import ProcessResult
from cairn.dispatcher.scheduler.loop import DispatcherLoop
from cairn.server import db
from cairn.server.app import app
from cairn.server.models import ProjectDetail, ProjectSummary, Settings
from conftest import configure_authenticated_test_client


class InProcessClient:
    def __init__(self, http: TestClient):
        self.http = http
        self._summaries = TypeAdapter(list[ProjectSummary])

    def close(self) -> None:
        return None

    def list_projects(self) -> list[ProjectSummary]:
        response = self.http.get("/projects")
        response.raise_for_status()
        return self._summaries.validate_python(response.json())

    def get_project(self, project_id: str) -> ProjectDetail:
        response = self.http.get(f"/projects/{project_id}")
        response.raise_for_status()
        return ProjectDetail.model_validate(response.json())

    def get_settings(self) -> Settings:
        response = self.http.get("/settings")
        response.raise_for_status()
        return Settings.model_validate(response.json())

    def export_project(self, project_id: str) -> str:
        response = self.http.get(f"/projects/{project_id}/export?format=yaml")
        response.raise_for_status()
        return response.text

    # ── Steps ──────────────────────────────────────────────────────────

    def step_heartbeat(self, project_id: str, step_id: str, worker: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/steps/{step_id}/heartbeat", {"worker": worker})

    heartbeat = step_heartbeat  # backward compat

    def release_step(self, project_id: str, step_id: str, worker: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/steps/{step_id}/release", {"worker": worker})

    release = release_step

    def conclude_step(self, project_id: str, step_id: str, worker: str, description: str, finding=None) -> ApiResult:
        return self._post(
            f"/projects/{project_id}/steps/{step_id}/conclude",
            {"worker": worker, "description": description},
        )

    conclude = conclude_step

    def create_step(self, project_id: str, from_ids: list[str], description: str, creator: str,
                    goal_id=None, priority=0) -> ApiResult:
        body: dict = {"from": from_ids, "description": description, "creator": creator, "worker": None}
        if goal_id is not None:
            body["goal_id"] = goal_id
        return self._post(f"/projects/{project_id}/steps", body)

    create_intent = create_step

    def update_step(self, project_id: str, step_id: str, priority=None, goal_id=None, abandoned=None) -> ApiResult:
        body: dict = {}
        if priority is not None:
            body["priority"] = priority
        if abandoned is not None:
            body["abandoned"] = abandoned
        return self._post(f"/projects/{project_id}/steps/{step_id}", body, method="PATCH")

    # ── Decide ─────────────────────────────────────────────────────────

    def claim_decide(self, project_id: str, worker: str, trigger: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/decide/claim", {"worker": worker, "trigger": trigger})

    claim_reason = claim_decide

    def decide_heartbeat(self, project_id: str, worker: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/decide/heartbeat", {"worker": worker})

    reason_heartbeat = decide_heartbeat

    def release_decide(self, project_id: str, worker: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/decide/release", {"worker": worker})

    release_reason = release_decide

    # ── Goals ──────────────────────────────────────────────────────────

    def create_goal(self, project_id: str, description: str, creator: str,
                    parent_goal_id=None, priority=0) -> ApiResult:
        body: dict = {"description": description, "creator": creator, "priority": priority}
        if parent_goal_id is not None:
            body["parent_goal_id"] = parent_goal_id
        return self._post(f"/projects/{project_id}/goals", body)

    def delete_goal(self, project_id: str, goal_id: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/goals/{goal_id}", {}, method="DELETE")

    def complete_goal(self, project_id: str, goal_id: str, from_ids: list[str],
                      description: str, worker: str) -> ApiResult:
        return self._post(
            f"/projects/{project_id}/goals/{goal_id}/complete",
            {"from": from_ids, "description": description, "worker": worker},
        )

    def complete(self, project_id: str, from_ids: list[str], description: str, worker: str) -> ApiResult:
        return self._post(
            f"/projects/{project_id}/complete",
            {"from": from_ids, "description": description, "worker": worker},
        )

    def _post(self, path: str, payload: dict[str, Any], method: str = "POST") -> ApiResult:
        if method == "PATCH":
            response = self.http.patch(path, json=payload)
        elif method == "DELETE":
            response = self.http.delete(path)
        else:
            response = self.http.post(path, json=payload)
        data = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
        return ApiResult(response.status_code, data, response.text)


class LocalProcess:
    def __init__(self, command: list[str], env: dict[str, str]):
        self.command = command
        self.env = env
        self._process: subprocess.Popen[str] | None = None
        self._cancel_reason: str | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            self._process = subprocess.Popen(
                self.command,
                env={**os.environ, **self.env},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

    def communicate(self, timeout: float | None) -> ProcessResult:
        assert self._process is not None
        timed_out = False
        try:
            stdout, stderr = self._process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.kill()
            stdout, stderr = self._process.communicate()
        return ProcessResult(
            returncode=self._process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            cancelled=self._cancel_reason is not None,
            cancel_reason=self._cancel_reason,
        )

    def kill(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    def cancel(self, reason: str) -> None:
        if self._cancel_reason is None:
            self._cancel_reason = reason
        self.kill()


class LocalContainerManager:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    def close(self) -> None:
        return None

    def container_name(self, project_id: str) -> str:
        return f"local-{project_id}"

    def ensure_running(self, project_id: str) -> str:
        return self.container_name(project_id)

    def build_exec_process(
        self,
        _container_name: str,
        env: dict[str, str],
        command: list[str],
        timeout_seconds: int | None = None,
        kill_after_seconds: int = 5,
    ) -> LocalProcess:
        assert timeout_seconds is not None
        assert kill_after_seconds == 5
        return LocalProcess(command, env)

    def write_text_file(self, container_name: str, path: str, content: str) -> None:
        self.writes.append((container_name, path, content))

    def needs_completed_cleanup(self, _project_id: str) -> bool:
        return False

    def needs_stopped_cleanup(self, _project_id: str) -> bool:
        return False

    def managed_container_names(self) -> list[str]:
        return []


@pytest.fixture
def http_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(tmp_path / "cairn.db")
    headers = configure_authenticated_test_client(monkeypatch)
    with TestClient(app, headers=headers) as client:
        yield client


# Allowed outcomes per phase (must match config.py MOCK_ALLOWED_OUTCOMES)
_DECIDE_OUTCOMES = ["complete", "steps", "noop", "rejected", "invalid_json", "invalid_payload", "command_fail"]
_EXECUTE_OUTCOMES = ["fact", "fact_with_finding", "rejected", "invalid_json", "invalid_payload", "command_fail"]
_EXECUTE_CONCLUDE_OUTCOMES = ["fact", "rejected", "invalid_json", "invalid_payload", "command_fail"]

def _phase(
    outcome: str,
    *,
    rules: list[dict[str, Any]] | None = None,
    zero_outcomes: list[str] | None = None,
    all_outcomes: list[str] | None = None,
) -> str:
    """Create a mock phase config. If all_outcomes is provided, all unspecified outcomes default to 0."""
    if all_outcomes is not None:
        outcomes = {name: 0.0 for name in all_outcomes}
        outcomes[outcome] = 1.0
    else:
        outcomes = {name: 0 for name in zero_outcomes or []}
        outcomes[outcome] = 1
    payload: dict[str, Any] = {"delay": [0, 0], "outcomes": outcomes}
    if rules is not None:
        payload["rules"] = rules
    return json.dumps(payload)


def _config(
    *,
    decide: str,
    execute: str,
    task_types: list[str] | None = None,
    worker_healthcheck: str = "startup_only",
    healthcheck: str | None = None,
    max_workers: int = 1,
) -> DispatchConfig:
    return DispatchConfig.model_validate(
        {
            "server": "in-process",
            "runtime": {
                "interval": 1,
                "max_workers": max_workers,
                "max_running_projects": 1,
                "max_project_workers": max_workers,
                "healthcheck_timeout": 2,
                "worker_healthcheck": worker_healthcheck,
                "prompt_group": "mock",
            },
            "tasks": {
                "decide": {"timeout": 2, "max_steps": 1},
                "execute": {"timeout": 2, "conclude_timeout": 2},
            },
            "container": {
                "image": "unused",
                "network_mode": "host",
                "completed_action": "stop",
            },
            "workers": [
                {
                    "name": "mock-worker",
                    "type": "mock",
                    "task_types": task_types or ["decide", "execute"],
                    "max_running": 1,
                    "priority": 0,
                    "env": {
                        "MOCK_HEALTHCHECK": healthcheck or _phase("ok"),
                        "MOCK_DECIDE": decide,
                        "MOCK_EXECUTE": execute,
                        "MOCK_EXECUTE_CONCLUDE": _phase("fact"),
                    },
                }
            ],
        }
    )


def _loop(config: DispatchConfig, client: InProcessClient, containers: LocalContainerManager) -> DispatcherLoop:
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.config = config
    loop.client = client
    loop.container_manager = containers
    loop.executor = ThreadPoolExecutor(max_workers=config.runtime.max_workers)
    loop.cleanup_executor = ThreadPoolExecutor(max_workers=1)
    loop.futures = {}
    loop.cleanup_futures = {}
    loop.decide_checkpoints = {}
    loop.runtime_project_ids = set()
    loop.worker_unhealthy_until = {}
    loop.worker_rejected_until = {}
    loop.worker_failed_until = {}
    loop.worker_failure_counts = {}
    loop._log_state = {}
    loop._cleanup_pending = set()
    loop._inactive_cleanup_done = {}
    loop.project_cursor = 0
    loop._settings_checked = False
    loop._startup_healthchecks_checked = False
    return loop


def _dispatch_and_wait(loop: DispatcherLoop) -> None:
    loop._reap_futures()
    summaries = loop.client.list_projects()
    loop._initialize_decide_checkpoints(summaries)
    loop._refresh_runtime_projects(summaries)
    loop._cancel_inactive_tasks(summaries)
    loop._queue_container_cleanups(summaries)
    loop._dispatch_available(summaries)
    if loop.futures:
        for future in list(loop.futures):
            future.result(timeout=5)
        loop._reap_futures()


def _create_project(http: TestClient) -> str:
    response = http.post(
        "/projects",
        json={"title": "integration", "origin": "start", "goal": "finish"},
    )
    assert response.status_code == 201
    return response.json()["project"]["id"]


def _get_goal_id(http: TestClient, project_id: str) -> str:
    return http.get(f"/projects/{project_id}").json()["goals"][0]["id"]


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_mock_scheduler_decide_completes_project_end_to_end(http_client: TestClient) -> None:
    """Decide sees the initial project, determines the goal is already met, completes it."""
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(
        _config(
            decide=_phase("complete", all_outcomes=_DECIDE_OUTCOMES),
            execute=_phase("fact", all_outcomes=_EXECUTE_OUTCOMES),
        ),
        client,
        containers,
    )
    project_id = _create_project(http_client)

    try:
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    assert project.project.status == "completed"
    # Goal satisfaction is linked directly to evidence Facts; it is not a
    # synthetic Step because Steps must represent Fact-producing execution.
    assert project.steps == []
    assert [g.status for g in project.goals] == ["completed"]
    assert project.goals[0].from_ == ["origin"]


def test_mock_scheduler_runs_decide_execute_decide_complete_chain(http_client: TestClient) -> None:
    """Decide proposes a step, Execute runs it, Decide sees new fact and completes."""
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(
        _config(
            decide=_phase("steps", all_outcomes=_DECIDE_OUTCOMES, rules=[{"fact_ids_gte": 3, "force": "complete"}]),
            execute=_phase("fact", all_outcomes=_EXECUTE_OUTCOMES),
        ),
        client,
        containers,
    )
    project_id = _create_project(http_client)
    goal_id = _get_goal_id(http_client, project_id)

    # Seed a fact so the project has something to work with
    seed = client.create_step(project_id, ["origin"], "seed step", "seed-worker", goal_id=goal_id)
    assert seed.ok
    sid = seed.data["id"] if seed.data else "s001"
    client.step_heartbeat(project_id, sid, "seed-worker")
    client.conclude_step(project_id, sid, "seed-worker", "seed fact")

    try:
        # Round 1: Decide → steps (fact_count=2, will propose 1 step)
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
        assert len(project.facts) >= 2  # origin + seed fact

        # Round 2: Execute the step → produces a new fact
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
        assert len(project.facts) >= 3  # now 3 facts, rule triggers "complete"

        # Round 3: Decide → complete (fact_ids_gte=3 rule fires)
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    assert project.project.status == "completed"
    assert [g.status for g in project.goals] == ["completed"]


def test_config_rejects_missing_decide_activity_worker() -> None:
    """A Cairn-Y runtime must be able to perform both core activities."""
    with pytest.raises(ValueError, match="worker must support decide"):
        _config(
            decide=_phase("complete", all_outcomes=_DECIDE_OUTCOMES),
            execute=_phase("fact", all_outcomes=_EXECUTE_OUTCOMES),
            task_types=["execute"],
        )


def test_task_healthcheck_healthy_worker_completes_end_to_end(http_client: TestClient) -> None:
    """With startup_and_task healthcheck, a healthy worker completes the project."""
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(
        _config(
            decide=_phase("complete", all_outcomes=_DECIDE_OUTCOMES),
            execute=_phase("fact", all_outcomes=_EXECUTE_OUTCOMES),
            worker_healthcheck="startup_and_task",
        ),
        client,
        containers,
    )
    project_id = _create_project(http_client)

    try:
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    assert project.project.status == "completed"


def test_task_healthcheck_failure_aborts_task_and_cools_down_worker(http_client: TestClient) -> None:
    """Unhealthy worker → task aborted, worker put on cooldown."""
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(
        _config(
            decide=_phase("complete", all_outcomes=_DECIDE_OUTCOMES),
            execute=_phase("fact", all_outcomes=_EXECUTE_OUTCOMES),
            worker_healthcheck="startup_and_task",
            healthcheck=_phase("fail", all_outcomes=["ok", "fail"]),
        ),
        client,
        containers,
    )
    project_id = _create_project(http_client)

    try:
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    assert project.project.status == "active"
    assert "mock-worker" in loop.worker_unhealthy_until


def test_failed_task_uses_project_worker_backoff(http_client: TestClient) -> None:
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(
        _config(
            decide=_phase("complete", all_outcomes=_DECIDE_OUTCOMES),
            execute=_phase("fact", all_outcomes=_EXECUTE_OUTCOMES),
        ),
        client,
        containers,
    )
    future: Future[str] = Future()
    future.set_result("failed")
    loop.futures[future] = RunningTask(
        "proj_001", "decide", "mock-worker", TaskCancellation(),
    )

    try:
        loop._reap_futures()
        key = ("proj_001", "decide", "mock-worker")
        assert loop.worker_failure_counts[key] == 1
        assert loop.worker_failed_until[key] > 0
        selection = loop._select_worker("proj_001", "decide")
    finally:
        loop.close()

    assert selection.worker is None
    assert selection.blocked_failed


def _failover_config() -> DispatchConfig:
    def worker(name: str, priority: int, healthcheck: str) -> dict:
        return {
            "name": name,
            "type": "mock",
            "task_types": ["decide", "execute"],
            "max_running": 1,
            "priority": priority,
            "env": {
                "MOCK_HEALTHCHECK": healthcheck,
                "MOCK_DECIDE": _phase("complete", all_outcomes=_DECIDE_OUTCOMES),
                "MOCK_EXECUTE": _phase("fact", all_outcomes=_EXECUTE_OUTCOMES),
                "MOCK_EXECUTE_CONCLUDE": _phase("fact", all_outcomes=_EXECUTE_CONCLUDE_OUTCOMES),
            },
        }

    return DispatchConfig.model_validate(
        {
            "server": "in-process",
            "runtime": {
                "interval": 1,
                "max_workers": 1,
                "max_running_projects": 1,
                "max_project_workers": 1,
                "healthcheck_timeout": 2,
                "worker_healthcheck": "startup_and_task",
                "prompt_group": "mock",
            },
            "tasks": {
                "decide": {"timeout": 2, "max_steps": 1},
                "execute": {"timeout": 2, "conclude_timeout": 2},
            },
            "container": {"image": "unused", "network_mode": "host", "completed_action": "stop"},
            "workers": [
                worker("bad", 0, _phase("fail", all_outcomes=["ok", "fail"])),
                worker("good", 1, _phase("ok")),
            ],
        }
    )


def test_unhealthy_worker_fails_over_to_healthy_worker(http_client: TestClient) -> None:
    """Bad worker fails health check, good worker takes over and completes."""
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(_failover_config(), client, containers)
    project_id = _create_project(http_client)

    try:
        # Round 1: 'bad' (priority 0) chosen first, health check fails → cooldown
        _dispatch_and_wait(loop)
        assert "bad" in loop.worker_unhealthy_until
        assert client.get_project(project_id).project.status == "active"

        # Round 2: 'bad' still cooling down → 'good' takes over and completes
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    assert project.project.status == "completed"
    assert project.goals[0].completed_by == "good"
