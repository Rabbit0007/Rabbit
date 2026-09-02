"""
Decide Task - Cairn_Y
=====================
Decide 阶段：分析图，决定下一步，创建 steps 和 findings。

基于 Cairn 的 reason.py，添加 findings 支持。
"""

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
    best_effort_release_reason,
    cancel_reason,
    did_timeout,
    preview,
    run_worker_process,
    task_healthcheck_enabled,
    write_graph_snapshot_reference,
)
from cairn.dispatcher.workers.registry import get_driver
from cairn.server.models import ProjectDetail

LOG = logging.getLogger(__name__)


def run_decide_task(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    project: ProjectDetail,
    export_yaml: str,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    """运行 Decide 任务 (Cairn_Y)

    参考 reason.py 的实现，添加 findings 处理。
    """
    driver = get_driver(worker.type)
    task_started = time.perf_counter()
    healthcheck_timeout = config.runtime.healthcheck_timeout
    lease = HeartbeatLease.for_reason(client, project.project.id, worker.name, config.runtime.interval)
    lease.start()

    try:
        container_name = container_manager.ensure_running(project.project.id)

        LOG.info(
            "starting container exec project=%s worker=%s phase=decide_healthcheck timeout=%ss",
            project.project.id,
            worker.name,
            healthcheck_timeout,
        )

        if task_healthcheck_enabled(config):
            from cairn.dispatcher.tasks.common import run_healthcheck
            healthcheck = run_healthcheck(
                container_manager,
                container_name,
                worker,
                driver.build_healthcheck(worker),
                timeout_seconds=healthcheck_timeout,
                lease=lease,
                cancellation=cancellation,
            )
            cancelled = cancel_reason(healthcheck.result, cancellation)
            if cancelled is not None:
                LOG.info(
                    "decide cancelled during healthcheck project=%s worker=%s reason=%s",
                    project.project.id,
                    worker.name,
                    cancelled,
                )
                return "cancelled"
            if lease.failure is not None:
                LOG.warning(
                    "heartbeat lost during decide healthcheck project=%s worker=%s status=%s",
                    project.project.id,
                    worker.name,
                    lease.failure.status_code,
                )
                return "failed"
            if healthcheck.result.returncode != 0:
                LOG.warning(
                    "worker unhealthy project=%s worker=%s healthcheck_ms=%s stderr=%s",
                    project.project.id,
                    worker.name,
                    healthcheck.duration_ms,
                    preview(healthcheck.result.stderr),
                )
                return "unhealthy"

        # 准备上下文
        open_steps = [
            {
                "id": step.id,
                "from": step.from_,
                "description": step.description,
                "worker": step.worker,
            }
            for step in project.steps
            if step.to is None
        ]

        allowed_fact_ids = [fact.id for fact in project.facts]

        LOG.info(
            "decide context prepared project=%s worker=%s facts=%s allowed_fact_ids=%s open_steps=%s",
            project.project.id,
            worker.name,
            len(project.facts),
            len(allowed_fact_ids),
            len(open_steps),
        )

        # 构造 Prompt
        prompt = render_prompt(
            load_prompt(config.runtime.prompt_group, "reason.md"),  # 使用 reason.md (已改为 decide 内容)
            {
                "graph_yaml": write_graph_snapshot_reference(
                    container_manager,
                    container_name,
                    export_yaml.strip(),
                    phase="decide_execute",
                ),
                "fact_ids": format_fact_ids(allowed_fact_ids),
                "open_steps": format_open_steps(open_steps),
                "max_steps": str(config.tasks.reason.max_intents),  # 复用配置
            },
        )

        session = driver.prepare_session()
        command = driver.build_execute(worker, prompt, session)
        session = command.session
        execute_started = time.perf_counter()

        result = run_worker_process(
            container_manager,
            container_name,
            worker,
            command.argv,
            phase="decide_execute",
            timeout_seconds=config.tasks.reason.timeout,
            lease=lease,
            cancellation=cancellation,
        )

        execute_ms = int((time.perf_counter() - execute_started) * 1000)
        total_ms = int((time.perf_counter() - task_started) * 1000)

        cancelled = cancel_reason(result, cancellation)
        if cancelled is not None:
            LOG.info(
                "decide cancelled during execute project=%s worker=%s reason=%s execute_ms=%s total_ms=%s",
                project.project.id,
                worker.name,
                cancelled,
                execute_ms,
                total_ms,
            )
            return "cancelled"

        if lease.failure is not None:
            LOG.warning(
                "heartbeat lost during decide execute project=%s worker=%s status=%s execute_ms=%s total_ms=%s",
                project.project.id,
                worker.name,
                lease.failure.status_code,
                execute_ms,
                total_ms,
            )
            return "failed"

        if did_timeout(result):
            LOG.warning(
                "decide timed out project=%s worker=%s execute_ms=%s total_ms=%s",
                project.project.id,
                worker.name,
                execute_ms,
                total_ms,
            )
            return "failed"

        if result.returncode != 0:
            LOG.warning(
                "decide failed project=%s worker=%s exit_code=%s execute_ms=%s total_ms=%s stderr=%s",
                project.project.id,
                worker.name,
                result.returncode,
                execute_ms,
                total_ms,
                preview(result.stderr),
            )
            return "failed"

        # 解析输出
        try:
            payload = parse_json_output(result.stdout)
        except Exception as exc:
            LOG.error(
                "decide output parse failed project=%s worker=%s execute_ms=%s total_ms=%s error=%s stdout=%s",
                project.project.id,
                worker.name,
                execute_ms,
                total_ms,
                exc,
                preview(result.stdout),
            )
            return "failed"

        # 验证输出
        try:
            kind, data = validate_decide_payload(
                payload, open_steps_empty=not open_steps, max_steps=config.tasks.reason.max_intents,
            )
        except ValueError as exc:
            LOG.error(
                "decide output validation failed project=%s worker=%s execute_ms=%s total_ms=%s error=%s",
                project.project.id,
                worker.name,
                execute_ms,
                total_ms,
                exc,
            )
            return "failed"

        # 处理结果
        if kind == "rejected":
            LOG.warning(
                "decide rejected project=%s worker=%s execute_ms=%s total_ms=%s",
                project.project.id,
                worker.name,
                execute_ms,
                total_ms,
            )
            return "failed"

        if kind == "complete":
            response = client.complete(project.project.id, data["from"], data["description"], worker.name)
            if not response.ok:
                LOG.error(
                    "decide complete failed project=%s worker=%s status=%s body=%s execute_ms=%s total_ms=%s",
                    project.project.id,
                    worker.name,
                    response.status_code,
                    response.text,
                    execute_ms,
                    total_ms,
                )
                return "failed"
            LOG.info(
                "decide completed project=%s worker=%s from=%s execute_ms=%s total_ms=%s",
                project.project.id,
                worker.name,
                data["from"],
                execute_ms,
                total_ms,
            )
            return "success"

        if kind == "steps":
            # Cairn_Y: 处理 steps 和 findings
            steps = data.get("steps", [])
            findings = data.get("findings", [])

            created_steps = 0
            for step_data in steps:
                response = client.create_step(
                    project.project.id, step_data["from"], step_data["description"], worker.name
                )
                if response.ok:
                    created_steps += 1
                elif response.status_code == 409:
                    LOG.info("decide step lost race project=%s worker=%s from=%s", project.project.id, worker.name, step_data["from"])
                else:
                    LOG.error(
                        "decide create_step failed project=%s worker=%s from=%s status=%s body=%s",
                        project.project.id,
                        worker.name,
                        step_data["from"],
                        response.status_code,
                        response.text,
                    )

            # Cairn_Y: 创建 findings
            created_findings = 0
            for finding_data in findings:
                # TODO: 需要实现 client.create_finding() API
                LOG.info(
                    "decide finding recorded project=%s worker=%s title=%s severity=%s",
                    project.project.id,
                    worker.name,
                    finding_data.get("title"),
                    finding_data.get("severity"),
                )
                created_findings += 1

            LOG.info(
                "decide finished project=%s worker=%s created_steps=%s/%s created_findings=%s execute_ms=%s total_ms=%s",
                project.project.id,
                worker.name,
                created_steps,
                len(steps),
                created_findings,
                execute_ms,
                total_ms,
            )

            if created_steps == 0 and len(steps) > 0:
                LOG.warning(
                    "decide created no steps project=%s worker=%s attempted=%s execute_ms=%s total_ms=%s",
                    project.project.id,
                    worker.name,
                    len(steps),
                    execute_ms,
                    total_ms,
                )
                return "failed"

            return "success"

        LOG.info(
            "decide finished without graph change project=%s worker=%s execute_ms=%s total_ms=%s",
            project.project.id,
            worker.name,
            execute_ms,
            total_ms,
        )
        return "success"

    finally:
        lease.stop()
        best_effort_release_reason(client, project.project.id, worker.name)
