from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease
from cairn.dispatcher.runtime.process import ProcessResult

PROCESS_COMMUNICATE_GRACE_SECONDS = 15
LOG_PREVIEW_LIMIT = 1200
GRAPH_SNAPSHOT_ROOT = "/tmp/cairn-prompts"
LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class ConcludeWriteResult:
    status: str
    fact_id: str | None = None


def preview(text: str, limit: int = LOG_PREVIEW_LIMIT) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def did_timeout(result: ProcessResult) -> bool:
    return not result.cancelled and (result.timed_out or result.returncode in (124, 137))


def cancel_reason(result: ProcessResult, cancellation: TaskCancellation | None = None) -> str | None:
    if result.cancelled:
        return result.cancel_reason or "cancelled"
    if cancellation is not None:
        return cancellation.reason
    return None


def communicate_timeout(timeout_seconds: int, grace_seconds: int = PROCESS_COMMUNICATE_GRACE_SECONDS) -> int:
    return timeout_seconds + grace_seconds


def task_healthcheck_enabled(config: DispatchConfig) -> bool:
    if config.runtime.execution == "local":
        return False
    return config.runtime.worker_healthcheck == "startup_and_task"


def write_graph_snapshot_reference(
    container_manager: ContainerManager,
    container_name: str,
    graph_yaml: str,
    *,
    phase: str,
) -> str:
    path = f"{GRAPH_SNAPSHOT_ROOT}/{phase}-{uuid.uuid4().hex[:12]}/graph.yaml"
    container_manager.write_text_file(container_name, path, graph_yaml)
    return (
        "The graph YAML snapshot is stored in this file inside the current container:\n\n"
        f"{path}\n\n"
        "Before using the graph, read the entire file and treat its contents as the YAML snapshot "
        "for this Graph section."
    )


def run_worker_process(
    container_manager: ContainerManager,
    container_name: str,
    worker: WorkerConfig,
    argv: list[str],
    *,
    phase: str,
    timeout_seconds: int,
    lease: HeartbeatLease | None = None,
    cancellation: TaskCancellation | None = None,
) -> ProcessResult:
    LOG.info(
        "开始执行 容器=%s 工人=%s 阶段=%s 超时=%s秒",
        container_name, worker.name, phase, timeout_seconds,
    )
    process = container_manager.build_exec_process(
        container_name, dict(worker.env), argv, timeout_seconds=timeout_seconds,
    )
    process.start()
    if lease is not None:
        lease.attach_process(process)
    if cancellation is not None:
        cancellation.attach_process(process)
    try:
        return process.communicate(timeout=communicate_timeout(timeout_seconds))
    finally:
        if lease is not None:
            lease.attach_process(None)
        if cancellation is not None:
            cancellation.attach_process(None)


def project_allows_conclude_fallback(
    client: CairnClient, project_id: str, *, worker_name: str, step_id: str | None = None, intent_id: str | None = None,
) -> bool:
    sid = step_id or intent_id
    project = client.get_project(project_id)
    if project.project.status == "active":
        return True
    LOG.info(
        "跳过总结回退 项目已非活跃 项目=%s 步骤=%s 工人=%s 状态=%s",
        project_id, sid, worker_name, project.project.status,
    )
    return False


# ── Decide release ─────────────────────────────────────────────────────

def best_effort_release_decide(client: CairnClient, project_id: str, worker_name: str) -> None:
    response = client.release_decide(project_id, worker_name)
    if not response.ok and response.status_code not in (403, 409):
        LOG.warning("决策释放失败 项目=%s 工人=%s 状态=%s", project_id, worker_name, response.status_code)
    elif response.ok:
        LOG.info("released decide project=%s worker=%s", project_id, worker_name)


best_effort_release_reason = best_effort_release_decide


# ── Step release ───────────────────────────────────────────────────────

def best_effort_release(client: CairnClient, project_id: str, step_id: str, worker_name: str) -> None:
    response = client.release_step(project_id, step_id, worker_name)
    if not response.ok and response.status_code not in (403, 409):
        LOG.warning("释放失败 项目=%s 步骤=%s 工人=%s 状态=%s", project_id, step_id, worker_name, response.status_code)
    elif response.ok:
        LOG.info("释放步骤 项目=%s 步骤=%s 工人=%s", project_id, step_id, worker_name)


# ── Conclude write ─────────────────────────────────────────────────────

def write_conclude_result(
    client: CairnClient,
    project_id: str,
    step_id: str,
    worker_name: str,
    description: str,
    *,
    source: str,
    phase_ms: int | None = None,
    total_ms: int | None = None,
    finding: dict | None = None,
) -> str:
    return write_conclude_result_with_fact_id(
        client, project_id, step_id, worker_name, description,
        source=source, phase_ms=phase_ms, total_ms=total_ms, finding=finding,
    ).status


def write_conclude_result_with_fact_id(
    client: CairnClient,
    project_id: str,
    step_id: str,
    worker_name: str,
    description: str,
    *,
    source: str,
    phase_ms: int | None = None,
    total_ms: int | None = None,
    finding: dict | None = None,
) -> ConcludeWriteResult:
    response = client.conclude_step(project_id, step_id, worker_name, description, finding=finding)
    if response.ok:
        fact_id: str | None = None
        if isinstance(response.data, dict):
            fact = response.data.get("fact")
            if isinstance(fact, dict):
                candidate = fact.get("id")
                if isinstance(candidate, str) and candidate:
                    fact_id = candidate
        LOG.info(
            "步骤完成 项目=%s 步骤=%s 工人=%s 来源=%s 耗时=%sms 总计=%sms",
            project_id, step_id, worker_name, source, phase_ms, total_ms,
        )
        return ConcludeWriteResult(status="success", fact_id=fact_id)
    if response.status_code == 403:
        LOG.info("项目已非活跃 步骤总结 项目=%s 步骤=%s", project_id, step_id)
    else:
        LOG.warning("步骤总结写入失败 项目=%s 步骤=%s 工人=%s 状态=%s", project_id, step_id, worker_name, response.status_code)
    best_effort_release(client, project_id, step_id, worker_name)
    return ConcludeWriteResult(status="failed", fact_id=None)
