from __future__ import annotations

import logging
import time

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.contracts import parse_json_output, validate_execute_payload
from cairn.dispatcher.prompting import load_prompt, render_prompt
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease
from cairn.dispatcher.tasks.common import (
    best_effort_release,
    cancel_reason,
    did_timeout,
    project_allows_conclude_fallback,
    run_worker_process,
    task_healthcheck_enabled,
    write_conclude_result,
    write_graph_snapshot_reference,
)
from cairn.dispatcher.workers.registry import get_driver
from cairn.server.models import Step, ProjectDetail

LOG = logging.getLogger(__name__)


def run_execute_task(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    project: ProjectDetail,
    export_yaml: str,
    step: Step,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    driver = get_driver(worker.type, config.runtime.execution)
    task_started = time.perf_counter()
    healthcheck_timeout = config.runtime.healthcheck_timeout
    lease = HeartbeatLease.for_step(client, project.project.id, step.id, worker.name, config.runtime.interval)
    lease.start()
    try:
        container_name = container_manager.ensure_running(project.project.id)

        if task_healthcheck_enabled(config):
            LOG.info(
                "检查工人健康 项目=%s 步骤=%s 工人=%s",
                project.project.id, step.id, worker.name,
            )
            health = driver.check_health(worker, timeout=healthcheck_timeout)
            if cancellation.is_cancelled:
                best_effort_release(client, project.project.id, step.id, worker.name)
                return "cancelled"
            if lease.failure is not None:
                best_effort_release(client, project.project.id, step.id, worker.name)
                return "failed"
            if not health.ok:
                best_effort_release(client, project.project.id, step.id, worker.name)
                return "unhealthy"

        execute_timeout = config.tasks.execute.timeout if config.tasks.execute else 300
        conclude_timeout = config.tasks.execute.conclude_timeout if config.tasks.execute else 90

        prompt = render_prompt(
            load_prompt(config.runtime.prompt_group, "execute.md"),
            {
                "graph_yaml": write_graph_snapshot_reference(
                    container_manager, container_name, export_yaml.strip(), phase="execute",
                ),
                "step_id": step.id,
                "step_description": step.description,
            },
        )

        session = driver.prepare_session()
        execute = driver.build_execute(worker, prompt, session)
        session = execute.session
        execute_started = time.perf_counter()
        first = _run_process(
            container_manager, container_name, worker, execute.argv,
            phase="execute", timeout=execute_timeout, lease=lease, cancellation=cancellation,
        )
        execute_ms = int((time.perf_counter() - execute_started) * 1000)
        session = driver.extract_session(session, first.stdout, first.stderr)
        cancelled = cancel_reason(first, cancellation)
        if cancelled is not None:
            best_effort_release(client, project.project.id, step.id, worker.name)
            return "cancelled"
        if lease.failure is not None:
            best_effort_release(client, project.project.id, step.id, worker.name)
            return "failed"
        if not did_timeout(first) and first.returncode == 0:
            try:
                model_output = driver.extract_response_text(first.stdout, first.stderr)
                payload = parse_json_output(model_output)
                kind, data = validate_execute_payload(payload)
            except Exception as exc:
                LOG.warning(
                    "执行解析失败 项目=%s 步骤=%s 错误=%s",
                    project.project.id, step.id, exc,
                )
                return _try_conclude_fallback(
                    config, client, container_manager, container_name, worker, driver,
                    project.project.id, step, export_yaml, session, lease, cancellation,
                    conclude_timeout,
                )
            if kind == "rejected":
                best_effort_release(client, project.project.id, step.id, worker.name)
                return "rejected"
            return write_conclude_result(
                client, project.project.id, step.id, worker.name,
                data["description"],
                source="execute",
                phase_ms=execute_ms,
                total_ms=int((time.perf_counter() - task_started) * 1000),
                finding=data.get("finding"),
            )
        if did_timeout(first):
            LOG.warning("执行超时 项目=%s 步骤=%s", project.project.id, step.id)
            return _try_conclude_fallback(
                config, client, container_manager, container_name, worker, driver,
                project.project.id, step, export_yaml, session, lease, cancellation,
                conclude_timeout,
            )
        LOG.warning("执行命令失败 项目=%s 步骤=%s 返回码=%s", project.project.id, step.id, first.returncode)
        best_effort_release(client, project.project.id, step.id, worker.name)
        return "failed"
    except Exception:
        LOG.exception("执行任务崩溃 项目=%s 步骤=%s", project.project.id, step.id)
        best_effort_release(client, project.project.id, step.id, worker.name)
        return "failed"
    finally:
        lease.stop()


def _try_conclude_fallback(
    config, client, container_manager, container_name, worker, driver,
    project_id, step, export_yaml, session, lease, cancellation, conclude_timeout,
) -> str:
    if not driver.supports_conclude() or not session:
        best_effort_release(client, project_id, step.id, worker.name)
        return "failed"
    if lease.failure is not None or cancellation.is_cancelled:
        best_effort_release(client, project_id, step.id, worker.name)
        return "cancelled" if cancellation.is_cancelled else "failed"

    if not project_allows_conclude_fallback(client, project_id, worker_name=worker.name, step_id=step.id):
        best_effort_release(client, project_id, step.id, worker.name)
        return "failed"

    container_name = container_manager.ensure_running(project_id)
    prompt = render_prompt(
        load_prompt(config.runtime.prompt_group, "execute_conclude.md"),
        {
            "graph_yaml": write_graph_snapshot_reference(
                container_manager, container_name, export_yaml.strip(), phase="execute_conclude",
            ),
            "step_id": step.id,
            "step_description": step.description,
        },
    )
    conclude_argv = driver.build_conclude(worker, prompt, session)
    LOG.info("starting conclude fallback project=%s step=%s", project_id, step.id)
    result = _run_process(
        container_manager, container_name, worker, conclude_argv,
        phase="execute_conclude", timeout=conclude_timeout, lease=lease, cancellation=cancellation,
    )
    cancelled = cancel_reason(result, cancellation)
    if cancelled is not None:
        best_effort_release(client, project_id, step.id, worker.name)
        return "cancelled"
    if lease.failure is not None or result.timed_out or result.returncode != 0:
        best_effort_release(client, project_id, step.id, worker.name)
        return "failed"
    try:
        model_output = driver.extract_response_text(result.stdout, result.stderr)
        payload = parse_json_output(model_output)
        kind, data = validate_execute_payload(payload)
    except Exception:
        best_effort_release(client, project_id, step.id, worker.name)
        return "failed"
    if kind == "rejected":
        best_effort_release(client, project_id, step.id, worker.name)
        return "rejected"
    return write_conclude_result(
        client, project_id, step.id, worker.name, data["description"],
        source="execute_conclude",
        finding=data.get("finding"),
    )


def _run_process(container_manager, container_name, worker, argv, *, phase, timeout, lease, cancellation):
    return run_worker_process(
        container_manager, container_name, worker, argv,
        phase=phase, timeout_seconds=timeout, lease=lease, cancellation=cancellation,
    )
