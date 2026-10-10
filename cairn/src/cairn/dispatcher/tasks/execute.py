from __future__ import annotations

import logging
import time

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.contracts import parse_json_output, validate_execute_payload
from cairn.dispatcher.output_parser import serialization_diagnostic
from cairn.dispatcher.prompting import load_prompt, render_prompt, operator_context, serialize_graph
from cairn.dispatcher.runtime.diagnostics import response_failure_reason
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease
from cairn.dispatcher.runtime.step_recovery import worker_identity
from cairn.dispatcher.runtime.local_backend import LocalBackend
from cairn.dispatcher.tasks.common import (
    best_effort_release,
    cancel_reason,
    did_timeout,
    lease_failure_outcome,
    project_allows_conclude_fallback,
    run_worker_process,
    task_healthcheck_enabled,
    write_conclude_result,
    write_graph_snapshot_reference,
)
from cairn.dispatcher.workers.registry import get_driver
from cairn.server.models import Step, ProjectDetail

LOG = logging.getLogger(__name__)


def run_execute_task(config, client, container_manager, project, export_yaml, step,
                     worker, cancellation, *, recovery_store=None) -> str:
    if recovery_store is None:
        return _run_execute_task_once(config, client, container_manager, project,
                                      export_yaml, step, worker, cancellation)
    pid = project.project.id
    record = recovery_store.for_step(project, step)
    if record is not None and (not recovery_store.ready(record)
            or record.get("worker_identity") != worker_identity(worker)):
        best_effort_release(client, pid, step.id, worker.name)
        return "needs_attention" if record.get("status") != "summary_pending" else "summary_pending"
    if record is None:
        record = {"project_id": pid, "step_id": step.id, "status": "running",
                  "instance": [project.project.created_at, step.created_at],
                  "worker": worker.name, "worker_identity": worker_identity(worker),
                  "session": None, "graph_yaml": export_yaml, "summary_attempts": 0,
                  "commit_attempts": 0, "created_at": time.time()}
        recovery_store.save(record)  # Before any environment action.

    def checkpoint(**updates):
        record.update(updates)
        recovery_store.save(record)

    try:
        result = _run_execute_task_once(config, client, container_manager, project,
                    record["graph_yaml"], step, worker, cancellation,
                    recovery=record, checkpoint=checkpoint)
    except Exception:
        LOG.exception("步骤检查点任务失败 项目=%s 步骤=%s", pid, step.id)
        best_effort_release(client, pid, step.id, worker.name)
        result = "failed"
    if result == "success":
        checkpoint(status="done")
        return result
    if record.get("payload"):
        pending = "commit_pending"
        attempts = record.get("commit_attempts", 0)
    elif record.get("session") and worker.type == "pi":
        pending = "summary_pending"
        attempts = record.get("summary_attempts", 0)
    else:
        pending, attempts = "needs_attention", 0
    if result == "rejected" or attempts >= config.tasks.execute.recovery_attempts:
        pending = "needs_attention"
    delay = min(300, 5 * (2 ** min(attempts, 6)))
    checkpoint(status=pending, retry_after=time.time() + delay, last_outcome=result)
    LOG.log(logging.ERROR if pending == "needs_attention" else logging.INFO,
            "步骤恢复状态 项目=%s 步骤=%s 状态=%s 尝试=%s 重试=%ss 不重新执行原动作",
            pid, step.id, pending, attempts, delay)
    return "cancelled" if result == "cancelled" else pending


def _run_execute_task_once(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    project: ProjectDetail,
    export_yaml: str,
    step: Step,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
    *, recovery=None, checkpoint=None,
) -> str:
    driver = get_driver(worker.type, config.runtime.execution)
    task_started = time.perf_counter()
    healthcheck_timeout = config.runtime.healthcheck_timeout
    lease = HeartbeatLease.for_step(client, project.project.id, step.id, worker.name, config.runtime.interval)
    lease.start()
    try:
        container_name = container_manager.ensure_running(project.project.id)
        if cancellation.is_cancelled or project.project.status != "active":
            best_effort_release(client, project.project.id, step.id, worker.name)
            return "cancelled"
        if recovery and recovery.get("payload"):
            checkpoint(commit_attempts=recovery.get("commit_attempts", 0) + 1)
            _, data = validate_execute_payload({"accepted": True, "data": recovery["payload"]})
            try:
                return write_conclude_result(client, project.project.id, step.id, worker.name,
                    data["description"], finding=data.get("finding"), source="commit_replay")
            finally:
                best_effort_release(client, project.project.id, step.id, worker.name)
        if recovery and recovery.get("session"):
            return _try_conclude_fallback(config, client, container_manager, container_name,
                worker, driver, project.project.id, step, export_yaml, recovery["session"],
                lease, cancellation, config.tasks.execute.conclude_timeout,
                task_started=task_started, checkpoint=checkpoint, recovery=recovery)

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
                return lease_failure_outcome(lease)
            if not health.ok:
                best_effort_release(client, project.project.id, step.id, worker.name)
                return "unhealthy"

        execute_timeout = config.tasks.execute.timeout if config.tasks.execute else 300
        conclude_timeout = config.tasks.execute.conclude_timeout if config.tasks.execute else 90
        reserve = min(config.tasks.execute.conclude_reserve_seconds, execute_timeout // 2)
        phase_timeout = execute_timeout - reserve if worker.type == "pi" else execute_timeout

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

        if worker.type != "mock":
            prompt = operator_context(project) + prompt
            prompt += (f"\nExecution time budget: {phase_timeout} seconds before mandatory summary. "
                       "Return the required JSON as soon as this bounded Step has a confirmed result. "
                       "Use finite tool timeouts within the remaining budget. Do not start another "
                       "investigation after this Step is answered; leave follow-up work to Decide.\n")
        if config.runtime.execution == "local" and worker.type != "mock":
            prompt += ('Local runtime: use "$CAIRN_PYTHON" for Python commands. '
                       'Check optional executables before using them; do not assume convert/magick '
                       'is installed. Pillow is the declared Python image-processing dependency.\n')
        if config.runtime.execution == "local" and worker.type != "mock":
            environment = LocalBackend.environment_summary()
            modules = {**environment["modules"], **environment["optional_modules"]}
            prompt += "Detected Python module availability: " + ", ".join(
                f"{name}={'available' if present else 'absent'}" for name, present in modules.items()) + ".\n"
        session = driver.prepare_session()
        execute = driver.build_execute(worker, prompt, session)
        session = execute.session
        execute_started = time.perf_counter()
        first = _run_process(
            container_manager, container_name, worker, execute.argv,
            phase="execute", timeout=phase_timeout, lease=lease, cancellation=cancellation,
        )
        execute_ms = int((time.perf_counter() - execute_started) * 1000)
        session = driver.extract_session(session, first.stdout, first.stderr)
        if checkpoint:
            checkpoint(session=session, status="summary_pending" if session else "needs_attention")
        cancelled = cancel_reason(first, cancellation)
        if cancelled is not None:
            if checkpoint:
                checkpoint(interruption_reason=cancellation.reason or "cancelled")
            best_effort_release(client, project.project.id, step.id, worker.name)
            return "cancelled"
        if lease.failure is not None:
            best_effort_release(client, project.project.id, step.id, worker.name)
            return lease_failure_outcome(lease)
        if not did_timeout(first) and first.returncode == 0:
            try:
                model_output = driver.extract_response_text(first.stdout, first.stderr)
                payload = parse_json_output(model_output)
                kind, data = validate_execute_payload(payload)
            except Exception as exc:
                reason = response_failure_reason(first.stdout) if worker.type == "pi" else "invalid_response"
                LOG.warning(
                    "执行解析失败 项目=%s 步骤=%s 错误=%s",
                    project.project.id, step.id, exc,
                )
                return _try_conclude_fallback(
                    config, client, container_manager, container_name, worker, driver,
                    project.project.id, step, export_yaml, session, lease, cancellation,
                    conclude_timeout, task_started=task_started, checkpoint=checkpoint, recovery=recovery,
                    interruption_reason=reason,
                )
            if kind == "rejected":
                best_effort_release(client, project.project.id, step.id, worker.name)
                return "rejected"
            if checkpoint:
                checkpoint(payload=data, status="commit_pending", commit_attempts=1)
            return write_conclude_result(
                client, project.project.id, step.id, worker.name,
                data["description"],
                source="execute",
                phase_ms=execute_ms,
                total_ms=int((time.perf_counter() - task_started) * 1000),
                finding=data.get("finding"),
            )
        if did_timeout(first):
            if phase_timeout < execute_timeout:
                LOG.info("执行预算到达，转入只读收尾 项目=%s 步骤=%s 探索=%ss 原上限=%ss",
                         project.project.id, step.id, phase_timeout, execute_timeout)
            else:
                LOG.warning("执行超时 项目=%s 步骤=%s", project.project.id, step.id)
            return _try_conclude_fallback(
                config, client, container_manager, container_name, worker, driver,
                project.project.id, step, export_yaml, session, lease, cancellation,
                conclude_timeout, task_started=task_started, checkpoint=checkpoint, recovery=recovery,
                interruption_reason="execution_timeout",
            )
        LOG.warning("执行命令失败 项目=%s 步骤=%s 返回码=%s", project.project.id, step.id, first.returncode)
        if checkpoint:
            checkpoint(interruption_reason="process_exit")
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
    *, task_started=None, checkpoint=None, recovery=None, interruption_reason=None,
) -> str:
    reason = interruption_reason or (recovery or {}).get("interruption_reason", "unknown_interruption")
    if checkpoint:
        checkpoint(interruption_reason=reason)
    conclude_started = time.perf_counter()
    task_started = conclude_started if task_started is None else task_started
    if not driver.supports_conclude() or not session:
        LOG.warning("收尾不可用 项目=%s 步骤=%s 原因=%s", project_id, step.id,
                    "no_session" if not session else "unsupported_driver")
        best_effort_release(client, project_id, step.id, worker.name)
        return "failed"
    if lease.failure is not None or cancellation.is_cancelled:
        best_effort_release(client, project_id, step.id, worker.name)
        return "cancelled" if cancellation.is_cancelled else lease_failure_outcome(lease)

    if not project_allows_conclude_fallback(client, project_id, worker_name=worker.name, step_id=step.id):
        best_effort_release(client, project_id, step.id, worker.name)
        return "cancelled"

    container_name = container_manager.ensure_running(project_id)
    # Summary is read-only, so it can safely use the newest guidance/evidence
    # without replaying any action from the persisted original graph.
    latest_project = client.get_project(project_id)
    if latest_project.project.status != "active":
        best_effort_release(client, project_id, step.id, worker.name)
        return "cancelled"
    export_yaml = serialize_graph(latest_project)
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
    if worker.type != "mock":
        prompt = operator_context(latest_project) + prompt
    prompt += (f"\nDispatcher interruption reason: {reason}. "
               "Only execution_timeout means the execution deadline was reached. output_limit "
               "means the provider response exhausted its output allowance; invalid_response "
               "means the final answer failed parsing or schema validation. operator_update "
               "means newer operator guidance interrupted the old task. Do not relabel these "
               "as timeouts or invent a network failure. If no new result was verified, state "
               "that explicitly and distinguish the execution limitation from domain facts.\n")
    if checkpoint:
        checkpoint(status="summary_pending", session=session,
                   summary_attempts=recovery.get("summary_attempts", 0) + 1)
    conclude_argv = driver.build_conclude(worker, prompt, session)
    LOG.info("starting conclude fallback project=%s step=%s reason=%s", project_id, step.id, reason)
    result = _run_process(
        container_manager, container_name, worker, conclude_argv,
        phase="execute_conclude", timeout=conclude_timeout, lease=lease, cancellation=cancellation,
    )
    format_retries = config.tasks.execute.format_retries if worker.type == "pi" else 0
    for attempt in range(format_retries + 1):
        cancelled = cancel_reason(result, cancellation)
        if cancelled is not None:
            if checkpoint and cancellation.reason:
                checkpoint(interruption_reason=cancellation.reason)
            best_effort_release(client, project_id, step.id, worker.name)
            return "cancelled"
        if lease.failure is not None:
            best_effort_release(client, project_id, step.id, worker.name)
            return lease_failure_outcome(lease)
        if did_timeout(result) or result.returncode != 0:
            LOG.warning("收尾失败 项目=%s 步骤=%s 原因=%s 返回码=%s 耗时=%sms",
                        project_id, step.id, "timeout" if did_timeout(result) else "process_exit",
                        result.returncode, int((time.perf_counter() - conclude_started) * 1000))
            best_effort_release(client, project_id, step.id, worker.name)
            return "failed"
        try:
            model_output = driver.extract_response_text(result.stdout, result.stderr)
        except Exception as exc:
            LOG.warning("收尾响应读取失败 项目=%s 步骤=%s 错误=%s", project_id, step.id, exc)
            best_effort_release(client, project_id, step.id, worker.name)
            return "failed"
        try:
            payload = parse_json_output(model_output)
            kind, data = validate_execute_payload(payload)
            break
        except ValueError as exc:
            remaining = int(conclude_timeout - (time.perf_counter() - conclude_started))
            LOG.warning("收尾解析失败 项目=%s 步骤=%s 错误=%s 修复次数=%s 剩余=%ss",
                        project_id, step.id, exc, attempt, remaining)
            if attempt >= format_retries or remaining < 1:
                best_effort_release(client, project_id, step.id, worker.name)
                return "failed"
            repair = (
                "Your summary failed JSON/schema validation. Return one COMPLETE raw JSON "
                "object: accepted=true, data containing description and an optional finding. "
                "Preserve confirmed evidence and all fields of any existing Finding; do not "
                "invent results or run new commands. Only repair serialization, close all "
                "brackets and escape string quotes. No prose or Markdown fences. "
                + serialization_diagnostic(model_output)
            )
            result = _run_process(
                container_manager, container_name, worker,
                driver.build_conclude(worker, repair, session),
                phase="execute_conclude_format", timeout=remaining,
                lease=lease, cancellation=cancellation,
            )
    if kind == "rejected":
        best_effort_release(client, project_id, step.id, worker.name)
        return "rejected"
    if checkpoint:
        checkpoint(payload=data, status="commit_pending", commit_attempts=1)
    return write_conclude_result(
        client, project_id, step.id, worker.name, data["description"],
        source="execute_conclude",
        phase_ms=int((time.perf_counter() - conclude_started) * 1000),
        total_ms=int((time.perf_counter() - task_started) * 1000),
        finding=data.get("finding"),
    )


def _run_process(container_manager, container_name, worker, argv, *, phase, timeout, lease, cancellation):
    return run_worker_process(
        container_manager, container_name, worker, argv,
        phase=phase, timeout_seconds=timeout, lease=lease, cancellation=cancellation,
    )
