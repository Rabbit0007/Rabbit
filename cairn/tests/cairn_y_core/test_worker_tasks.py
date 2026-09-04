from __future__ import annotations

from collections.abc import Iterator
import pytest

from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.process import ProcessResult
from cairn.dispatcher.workers.health import HealthResult
from cairn.dispatcher.tasks import decide, execute

from conftest import (
    FakeClient,
    FakeContainerManager,
    FakeDriver,
    FakeLease,
    make_config,
    make_step,
    make_project,
)


def _lease_factory(lease: FakeLease):
    return lambda *_args, **_kwargs: lease


def test_decide_writes_graph_snapshot_and_creates_step(monkeypatch) -> None:
    config = make_config()
    project = make_project()
    client = FakeClient(project)
    containers = FakeContainerManager()
    driver = FakeDriver()
    lease = FakeLease()
    graph_yaml = "project:\n  title: huge\n" + ("x" * 100_000)

    monkeypatch.setattr(decide, "get_driver", lambda *_a, **_k: driver)
    monkeypatch.setattr(decide.HeartbeatLease, "for_decide", _lease_factory(lease))
    monkeypatch.setattr(
        decide,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"steps":[{"from":["f001"],"description":"next step"}]}}',
            "",
        ),
    )

    outcome = decide.run_decide_task(
        config, client, containers, project, graph_yaml, config.workers[0], TaskCancellation(),
    )

    assert outcome == "success"
    assert client.created_steps == [("proj_001", ["f001"], "next step", "test-worker")]
    assert client.released_decides == [("proj_001", "test-worker")]
    assert lease.started and lease.stopped
    assert len(containers.writes) == 1
    container_name, path, content = containers.writes[0]
    assert container_name == "container-proj_001"
    assert path.startswith("/tmp/cairn-prompts/decide-")
    assert path.endswith("/graph.yaml")
    assert content == graph_yaml


def test_execute_early_plain_text_exit_uses_conclude_fallback(monkeypatch) -> None:
    config = make_config()
    step = make_step()
    project = make_project(steps=[step])
    client = FakeClient(project)
    containers = FakeContainerManager()
    driver = FakeDriver()
    lease = FakeLease()
    results: Iterator[ProcessResult] = iter([
        ProcessResult(0, "Need inspect files and keep working.", ""),
        ProcessResult(0, '{"accepted":true,"data":{"description":"confirmed fact"}}', ""),
    ])

    monkeypatch.setattr(execute, "get_driver", lambda *_a, **_k: driver)
    monkeypatch.setattr(execute.HeartbeatLease, "for_step", _lease_factory(lease))
    monkeypatch.setattr(execute, "_run_process", lambda *_args, **_kwargs: next(results))

    outcome = execute.run_execute_task(
        config, client, containers, project, "facts:\n- id: f001\n", step, config.workers[0], TaskCancellation(),
    )

    assert outcome == "success"
    assert ("proj_001", "s001", "test-worker") == (client.concluded[0][0], client.concluded[0][1], client.concluded[0][2])
    assert len(containers.writes) == 2
    assert "/execute-" in containers.writes[0][1]
    assert "/execute_conclude-" in containers.writes[1][1]
    assert len(driver.execute_prompts) == 1
    assert len(driver.conclude_prompts) == 1
    assert lease.started and lease.stopped


def test_execute_conclude_fallback_preserves_finding(monkeypatch) -> None:
    config = make_config()
    step = make_step()
    project = make_project(steps=[step])
    client = FakeClient(project)
    containers = FakeContainerManager()
    driver = FakeDriver()
    lease = FakeLease()
    results: Iterator[ProcessResult] = iter([
        ProcessResult(0, "plain text", ""),
        ProcessResult(
            0,
            '{"accepted":true,"data":{"description":"confirmed","finding":'
            '{"title":"finding","severity":"high","description":"details"}}}',
            "",
        ),
    ])

    monkeypatch.setattr(execute, "get_driver", lambda *_a, **_k: driver)
    monkeypatch.setattr(execute.HeartbeatLease, "for_step", _lease_factory(lease))
    monkeypatch.setattr(execute, "_run_process", lambda *_args, **_kwargs: next(results))

    outcome = execute.run_execute_task(
        config, client, containers, project, "graph", step, config.workers[0], TaskCancellation(),
    )

    assert outcome == "success"
    assert client.concluded == [("proj_001", "s001", "test-worker", "confirmed")]


def test_execute_healthcheck_failure_releases_claim(monkeypatch) -> None:
    config = make_config()
    config.runtime.worker_healthcheck = "startup_and_task"
    step = make_step()
    project = make_project(steps=[step])
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    driver = FakeDriver()
    driver.health = HealthResult(ok=False, status=401, detail="unauthorized")
    monkeypatch.setattr(execute, "get_driver", lambda *_a, **_k: driver)
    monkeypatch.setattr(execute.HeartbeatLease, "for_step", _lease_factory(lease))

    outcome = execute.run_execute_task(
        config, client, containers, project, "graph", step, config.workers[0], TaskCancellation(),
    )

    assert outcome == "unhealthy"
    assert client.released == [("proj_001", "s001", "test-worker")]
    assert containers.writes == []


def test_decide_complete_treats_inactive_project_as_success(monkeypatch) -> None:
    config = make_config()
    project = make_project()
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    def complete_goal(*_args, **_kwargs) -> ApiResult:
        return ApiResult(403, text="inactive")

    client.complete_goal = complete_goal  # type: ignore[method-assign]
    monkeypatch.setattr(decide, "get_driver", lambda *_a, **_k: FakeDriver())
    monkeypatch.setattr(decide.HeartbeatLease, "for_decide", _lease_factory(lease))
    monkeypatch.setattr(
        decide,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"complete":{"goal_id":"g001","from":["f001"],"description":"done"}}}',
            "",
        ),
    )

    outcome = decide.run_decide_task(
        config, client, containers, project, "graph", config.workers[0], TaskCancellation(),
    )

    assert outcome == "success"
    assert client.released_decides == [("proj_001", "test-worker")]


def test_decide_startup_only_mode_skips_task_healthcheck(monkeypatch) -> None:
    config = make_config()
    config.runtime.worker_healthcheck = "startup_only"
    project = make_project()
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    driver = FakeDriver()

    def _boom(*_a, **_k):
        raise AssertionError("task healthcheck should be skipped")

    driver.check_health = _boom  # type: ignore[method-assign]
    monkeypatch.setattr(decide, "get_driver", lambda *_a, **_k: driver)
    monkeypatch.setattr(decide.HeartbeatLease, "for_decide", _lease_factory(lease))
    monkeypatch.setattr(
        decide,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"steps":[{"from":["f001"],"description":"next"}]}}',
            "",
        ),
    )

    outcome = decide.run_decide_task(
        config, client, containers, project, "graph", config.workers[0], TaskCancellation(),
    )

    assert outcome == "success"
    assert client.created_steps == [("proj_001", ["f001"], "next", "test-worker")]


def test_decide_rejects_claimed_step_mutation_before_writing() -> None:
    step = make_step()
    project = make_project(steps=[step])

    with pytest.raises(ValueError, match="claimed step"):
        decide.validate_mutations_against_graph(
            {"step_updates": [{"id": step.id, "action": "abandon"}]},
            project,
        )


def test_decide_rejects_unknown_fact_reference_before_writing() -> None:
    project = make_project()

    with pytest.raises(ValueError, match="unknown facts"):
        decide.validate_mutations_against_graph(
            {"steps": [{"from": ["missing"], "description": "invalid", "priority": 0}]},
            project,
        )
