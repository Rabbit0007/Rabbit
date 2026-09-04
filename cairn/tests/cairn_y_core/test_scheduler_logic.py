from __future__ import annotations

from unittest.mock import patch

from cairn.dispatcher.models import DecideCheckpoint
from cairn.dispatcher.scheduler.loop import DispatcherLoop
from cairn.server.models import ProjectDecide, ProjectMeta, ProjectSummary, Step

from conftest import make_config, make_step, make_project


def test_decide_trigger_detects_new_facts_and_open_step_completion() -> None:
    config = make_config()
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.decide_checkpoints = {
        "proj_001": DecideCheckpoint(
            fact_count=2, hint_count=1, goal_count=1, open_step_count=0,
        )
    }

    project = make_project(steps=[])
    project.facts.append(project.facts[0].__class__(id="f002", description="new fact"))

    trigger = loop._decide_trigger(project)
    assert trigger is not None
    assert "facts:2->3" in trigger


def test_decide_trigger_returns_none_when_graph_is_unchanged() -> None:
    config = make_config()
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.decide_checkpoints = {
        "proj_001": DecideCheckpoint(
            fact_count=2, hint_count=1, goal_count=1, open_step_count=0,
        )
    }

    project = make_project(steps=[])
    trigger = loop._decide_trigger(project)
    assert trigger is None


def test_refresh_runtime_projects_discards_active_and_changed_cleanup_markers() -> None:
    config = make_config()
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.runtime_project_ids = {"proj_001", "proj_002"}
    loop._inactive_cleanup_done = {"proj_001": "completed", "proj_003": "completed"}

    from cairn.server.models import ProjectSummary
    summaries = [
        ProjectSummary(
            id="proj_001", title="a", status="active", bootstrap_enabled=True,
            created_at="2026-01-01T00:00:00Z", decide=None,
            fact_count=1, intent_count=0, working_intent_count=0, unclaimed_intent_count=0,
            step_count=0, working_step_count=0, unclaimed_step_count=0,
            goal_count=1, finding_count=0, hint_count=0,
        ),
        ProjectSummary(
            id="proj_002", title="b", status="stopped", bootstrap_enabled=True,
            created_at="2026-01-01T00:00:00Z", decide=None,
            fact_count=1, intent_count=0, working_intent_count=0, unclaimed_intent_count=0,
            step_count=0, working_step_count=0, unclaimed_step_count=0,
            goal_count=1, finding_count=0, hint_count=0,
        ),
    ]

    loop._refresh_runtime_projects(summaries)
    assert loop.runtime_project_ids == {"proj_001"}
    assert loop._inactive_cleanup_done == {}


def test_choose_worker_prefers_priority_then_lower_running_count() -> None:
    from cairn.dispatcher.scheduler.worker_select import choose_worker
    from cairn.dispatcher.config import WorkerConfig

    workers = [
        WorkerConfig(name="w1", type="mock", task_types=["decide"], max_running=2, priority=10),
        WorkerConfig(name="w2", type="mock", task_types=["decide"], max_running=2, priority=0),
        WorkerConfig(name="w3", type="mock", task_types=["decide"], max_running=2, priority=0),
    ]
    counts = {"w2": 1, "w3": 0}

    ordered = choose_worker(workers, counts)
    # w2 (priority 0, busy) should come after w3 (priority 0, idle)
    assert ordered[0].name == "w3"


def test_new_fact_dispatches_decide_before_unclaimed_execute_step() -> None:
    """When a new fact appears AND there are unclaimed steps, Decide should fire first."""
    from cairn.server.models import Fact
    config = make_config()
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.config = config

    # Create a project with one unclaimed step, and a checkpoint behind on facts
    project = make_project(steps=[make_step()])
    project.steps[0].worker = None  # unclaimed
    project.facts.append(Fact(id="f002", description="new fact"))

    loop.decide_checkpoints = {
        "proj_001": DecideCheckpoint(
            fact_count=2, hint_count=1, goal_count=1, open_step_count=1,
        )
    }
    loop.futures = {}
    loop.runtime_project_ids = set()
    loop._cleanup_pending = set()
    loop._log_state = {}
    loop.project_cursor = 0
    loop._project_running_task_count = lambda _pid: 0
    loop._project_has_running_decide = lambda _pid: False
    loop._project_running_execute_steps = lambda _pid: set()

    # _decide_trigger should fire because facts changed (2→3)
    trigger = loop._decide_trigger(project)
    assert trigger is not None
    assert "facts:2->3" in trigger

    # _is_initial_project should be False (has steps)
    assert loop._is_initial_project(project) is False

    # project.decide is None → decide path is open
    assert project.project.decide is None


def test_active_decide_lease_blocks_new_execute_claims() -> None:
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.config = make_config()
    loop.futures = {}
    loop._cleanup_pending = set()
    project = make_project(steps=[make_step()])
    project.steps[0].worker = None
    project.project.decide = ProjectDecide(
        worker="decider",
        trigger="new_facts",
        started_at="2026-01-01T00:00:03Z",
        last_heartbeat_at="2026-01-01T00:00:03Z",
    )
    loop.client = type("Client", (), {"get_project": lambda _self, _id: project})()
    loop.container_manager = type("Containers", (), {"container_name": lambda _self, pid: f"container-{pid}"})()
    loop._dispatch_execute = lambda *_args: (_ for _ in ()).throw(AssertionError("must not dispatch execute"))

    summary = ProjectSummary(
        id="proj_001", title="test", status="active", bootstrap_enabled=True,
        created_at="2026-01-01T00:00:00Z", decide=project.project.decide,
        fact_count=2, intent_count=1, working_intent_count=0, unclaimed_intent_count=1,
        step_count=1, working_step_count=0, unclaimed_step_count=1,
        goal_count=1, finding_count=0, hint_count=1,
    )

    assert loop._try_dispatch_project(summary) is False

def test_is_initial_project_detects_new_project_without_steps() -> None:
    from cairn.server.models import Fact, Goal, ProjectDetail, ProjectMeta
    config = make_config()
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.config = config

    # Initial: only origin fact, one goal, no steps
    project = ProjectDetail(
        project=ProjectMeta(id="proj_001", title="t", status="active", bootstrap_enabled=True,
                            created_at="2026-01-01T00:00:00Z"),
        facts=[Fact(id="origin", description="start")],
        steps=[],
        goals=[Goal(id="g001", description="finish", status="active", priority=0,
                   created_at="2026-01-01T00:00:00Z")],
        findings=[],
        hints=[],
    )
    assert loop._is_initial_project(project) is True

    # Not initial: has a step
    project = make_project(steps=[make_step()])
    assert loop._is_initial_project(project) is False


def test_cancel_inactive_tasks_marks_stopped_and_deleted_projects() -> None:
    config = make_config()
    loop = DispatcherLoop.__new__(DispatcherLoop)
    from cairn.dispatcher.models import RunningTask
    from cairn.dispatcher.runtime.cancellation import TaskCancellation

    task1 = RunningTask("proj_001", "decide", "w1", TaskCancellation())
    task2 = RunningTask("proj_002", "execute", "w2", TaskCancellation(), step_id="s001")
    loop.futures = {0: task1, 1: task2}  # type: ignore

    from cairn.server.models import ProjectSummary
    summaries = [
        ProjectSummary(
            id="proj_001", title="a", status="stopped", bootstrap_enabled=True,
            created_at="2026-01-01T00:00:00Z", decide=None,
            fact_count=1, intent_count=0, working_intent_count=0, unclaimed_intent_count=0,
            step_count=0, working_step_count=0, unclaimed_step_count=0,
            goal_count=1, finding_count=0, hint_count=0,
        ),
        ProjectSummary(
            id="proj_002", title="b", status="active", bootstrap_enabled=True,
            created_at="2026-01-01T00:00:00Z", decide=None,
            fact_count=1, intent_count=0, working_intent_count=0, unclaimed_intent_count=0,
            step_count=0, working_step_count=0, unclaimed_step_count=0,
            goal_count=1, finding_count=0, hint_count=0,
        ),
    ]

    loop._cancel_inactive_tasks(summaries)
    assert task1.cancellation.reason == "stopped"
    assert task2.cancellation.reason is None


def test_initialize_decide_checkpoint_only_for_active_projects_with_open_steps() -> None:
    config = make_config()
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.decide_checkpoints = {}

    from cairn.server.models import ProjectSummary
    summaries = [
        ProjectSummary(
            id="proj_001", title="a", status="active", bootstrap_enabled=True,
            created_at="2026-01-01T00:00:00Z", decide=None,
            fact_count=2, intent_count=0, working_intent_count=0, unclaimed_intent_count=0,
            step_count=1, working_step_count=1, unclaimed_step_count=0,
            goal_count=1, finding_count=0, hint_count=0,
        ),
        ProjectSummary(
            id="proj_002", title="b", status="completed", bootstrap_enabled=True,
            created_at="2026-01-01T00:00:00Z", decide=None,
            fact_count=5, intent_count=0, working_intent_count=0, unclaimed_intent_count=0,
            step_count=3, working_step_count=0, unclaimed_step_count=0,
            goal_count=1, finding_count=0, hint_count=0,
        ),
    ]

    loop._initialize_decide_checkpoints(summaries)
    assert "proj_001" in loop.decide_checkpoints
    assert "proj_002" not in loop.decide_checkpoints
    assert loop.decide_checkpoints["proj_001"].open_step_count == 1
    assert loop.decide_checkpoints["proj_001"].goal_count == 1
