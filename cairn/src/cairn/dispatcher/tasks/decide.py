from __future__ import annotations

import logging
import time

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.contracts import parse_json_output, validate_decide_payload
from cairn.dispatcher.prompting import (
    format_fact_ids,
    format_open_steps,
    load_prompt,
    render_prompt,
)
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease
from cairn.dispatcher.tasks.common import (
    best_effort_release_decide,
    cancel_reason,
    did_timeout,
    run_worker_process,
    task_healthcheck_enabled,
    write_graph_snapshot_reference,
)
from cairn.dispatcher.workers.registry import get_driver
from cairn.server.models import ProjectDetail

LOG = logging.getLogger(__name__)


def validate_mutations_against_graph(mutations: dict, project: ProjectDetail) -> None:
    """Reject stale or causally invalid graph edits before the first API write."""
    fact_ids = {fact.id for fact in project.facts}
    goals = {goal.id: goal for goal in project.goals}
    steps = {step.id: step for step in project.steps}

    for step_data in mutations.get("steps", []):
        unknown_facts = set(step_data["from"]) - fact_ids
        if unknown_facts:
            raise ValueError(f"step references unknown facts: {', '.join(sorted(unknown_facts))}")
        goal_id = step_data.get("goal_id")
        if goal_id is not None and (goal_id not in goals or goals[goal_id].status != "active"):
            raise ValueError(f"step references inactive or unknown goal: {goal_id}")

    for update in mutations.get("step_updates", []):
        step = steps.get(update["id"])
        if step is None:
            raise ValueError(f"step update references unknown step: {update['id']}")
        if step.to is not None or step.concluded_at is not None or step.abandoned:
            raise ValueError(f"step update references closed step: {update['id']}")
        if step.worker is not None:
            raise ValueError(f"step update references claimed step: {update['id']}")

    for change in mutations.get("sub_goals", []):
        if change["action"] == "create":
            parent_id = change.get("parent_goal_id")
            if parent_id is not None and (parent_id not in goals or goals[parent_id].status != "active"):
                raise ValueError(f"sub-goal references inactive or unknown parent: {parent_id}")
            continue
        goal = goals.get(change["id"])
        if goal is None or goal.status != "active" or goal.parent_goal_id is None:
            raise ValueError(f"cannot delete goal: {change['id']}")
        if any(candidate.parent_goal_id == goal.id for candidate in project.goals):
            raise ValueError(f"cannot delete goal with children: {goal.id}")
        if any(step.goal_id == goal.id for step in project.steps):
            raise ValueError(f"cannot delete goal referenced by steps: {goal.id}")


def run_decide_task(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    project: ProjectDetail,
    export_yaml: str,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    driver = get_driver(worker.type, config.runtime.execution)
    task_started = time.perf_counter()
    healthcheck_timeout = config.runtime.healthcheck_timeout
    lease = HeartbeatLease.for_decide(client, project.project.id, worker.name, config.runtime.interval)
    lease.start()
    try:
        container_name = container_manager.ensure_running(project.project.id)

        if task_healthcheck_enabled(config):
            LOG.info(
                "检查工人健康 项目=%s 工人=%s 超时=%s秒",
                project.project.id,
                worker.name,
                healthcheck_timeout,
            )
            health = driver.check_health(worker, timeout=healthcheck_timeout)
            if cancellation.is_cancelled:
                LOG.info("决策取消(健康检查中) 项目=%s", project.project.id)
                return "cancelled"
            if lease.failure is not None:
                return "failed"
            if not health.ok:
                LOG.warning("工人不健康 项目=%s 工人=%s", project.project.id, worker.name)
                return "unhealthy"

        open_steps = [
            {
                "id": step.id,
                "from": step.from_,
                "description": step.description,
                "goal_id": step.goal_id,
                "priority": step.priority,
                "worker": step.worker,
            }
            for step in project.steps
            if step.to is None and not step.abandoned
        ]
        allowed_fact_ids = [fact.id for fact in project.facts]
        max_steps = config.tasks.decide.max_steps if config.tasks.decide else 3

        prompt = render_prompt(
            load_prompt(config.runtime.prompt_group, "decide.md"),
            {
                "graph_yaml": write_graph_snapshot_reference(
                    container_manager, container_name, export_yaml.strip(), phase="decide",
                ),
                "fact_ids": format_fact_ids(allowed_fact_ids),
                "open_steps": format_open_steps(open_steps),
                "max_steps": str(max_steps),
            },
        )

        session = driver.prepare_session()
        command = driver.build_decide(worker, prompt, session)
        execute_started = time.perf_counter()
        result = run_worker_process(
            container_manager, container_name, worker, command.argv,
            phase="decide", timeout_seconds=config.tasks.decide.timeout if config.tasks.decide else 300,
            lease=lease, cancellation=cancellation,
        )
        execute_ms = int((time.perf_counter() - execute_started) * 1000)
        total_ms = int((time.perf_counter() - task_started) * 1000)

        cancelled = cancel_reason(result, cancellation)
        if cancelled is not None:
            return "cancelled"
        if lease.failure is not None:
            return "failed"
        if did_timeout(result) or result.returncode != 0:
            LOG.warning("决策失败 项目=%s 返回码=%s", project.project.id, result.returncode)
            return "failed"

        try:
            model_output = driver.extract_response_text(result.stdout, result.stderr)
            payload = parse_json_output(model_output)
            kind, data = validate_decide_payload(
                payload, open_steps_empty=not open_steps, max_steps=max_steps,
            )
        except Exception as exc:
            LOG.warning("决策解析失败 项目=%s 错误=%s", project.project.id, exc)
            return "failed"

        if kind == "rejected":
            return "rejected"

        if kind == "complete":
            goal_id = data["goal_id"]
            response = client.complete_goal(project.project.id, goal_id, data["from"], data["description"], worker.name)
            if response.status_code == 403:
                return "success"
            if not response.ok:
                LOG.warning("决策完成目标失败 项目=%s 目标=%s", project.project.id, goal_id)
                return "failed"
            LOG.info("目标完成 项目=%s 目标=%s", project.project.id, goal_id)
            # A project may have more active Goals. Force a clean Decide run so
            # the next completion condition is evaluated from the updated graph.
            return "continue"

        mutations = data if kind == "mutations" else ({kind: data[kind]} if kind in {"steps", "step_updates", "sub_goals"} else {})

        if mutations:
            try:
                validate_mutations_against_graph(mutations, project)
            except ValueError as exc:
                LOG.warning("决策图变更无效 项目=%s 错误=%s", project.project.id, exc)
                return "failed"
            requested = 0
            applied = 0
            became_inactive = False

            for step_data in mutations.get("steps", []):
                requested += 1
                response = client.create_step(
                    project.project.id, step_data["from"], step_data["description"],
                    worker.name, goal_id=step_data.get("goal_id"), priority=step_data.get("priority", 0),
                )
                if response.status_code == 403:
                    became_inactive = True
                    break
                if response.ok:
                    applied += 1

            if not became_inactive:
                for update in mutations.get("step_updates", []):
                    requested += 1
                    if update.get("action") == "abandon":
                        response = client.update_step(project.project.id, update["id"], abandoned=True)
                    else:
                        response = client.update_step(project.project.id, update["id"], priority=update["priority"])
                    if response.status_code == 403:
                        became_inactive = True
                        break
                    if response.ok:
                        applied += 1

            if not became_inactive:
                for sub_goal in mutations.get("sub_goals", []):
                    requested += 1
                    if sub_goal["action"] == "create":
                        response = client.create_goal(
                            project.project.id, sub_goal["description"], worker.name,
                            parent_goal_id=sub_goal.get("parent_goal_id"), priority=sub_goal.get("priority", 0),
                        )
                    else:
                        response = client.delete_goal(project.project.id, sub_goal["id"])
                    if response.status_code == 403:
                        became_inactive = True
                        break
                    if response.ok:
                        applied += 1

            LOG.info("决策图变更 项目=%s 成功=%s/%s", project.project.id, applied, requested)
            if became_inactive:
                return "success"
            return "success" if applied == requested else "failed"

        LOG.info("决策完成无变化 项目=%s", project.project.id)
        return "success"
    finally:
        lease.stop()
        best_effort_release_decide(client, project.project.id, worker.name)
