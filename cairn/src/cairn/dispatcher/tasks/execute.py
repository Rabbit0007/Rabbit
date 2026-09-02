"""
Execute Task - Cairn_Y
======================
Execute 阶段：执行具体操作，只能与外部世界交互。

这是统一 Agent Loop 的第二个应用：
- mode="execute"
- 只能使用 WORLD tools
- 负责战术执行

这个任务被调度器调用，替代原来的 run_explore_task。
"""

import logging
import time

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease
from cairn.dispatcher.tasks.common import (
    best_effort_release,
    cancel_reason,
    preview,
    run_healthcheck,
)
from cairn.dispatcher.workers.registry import get_driver
from cairn.server.models import Intent, ProjectDetail

LOG = logging.getLogger(__name__)


def run_execute_task(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    project: ProjectDetail,
    export_yaml: str,
    intent: Intent,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    """运行 Execute 阶段任务

    Execute 负责：
    1. 执行 Step 的具体任务
    2. 运行命令、发送请求
    3. 收集证据和观察结果
    4. 返回 Fact

    这个函数保留了原有的工程化逻辑（容器管理、心跳、健康检查），
    但核心 LLM 调用会使用 AgentLoop。

    Args:
        config: 调度配置
        client: API 客户端
        container_manager: 容器管理器
        project: 项目详情
        export_yaml: 导出的项目 YAML
        intent: 当前 Intent/Step
        worker: Worker 配置
        cancellation: 取消令牌

    Returns:
        任务结果状态: "success", "failed", "cancelled", "unhealthy"
    """
    driver = get_driver(worker.type)
    task_started = time.perf_counter()
    healthcheck_timeout = config.runtime.healthcheck_timeout
    lease = HeartbeatLease.for_intent(client, project.project.id, intent.id, worker.name, config.runtime.interval)
    lease.start()

    try:
        container_name = container_manager.ensure_running(project.project.id)

        LOG.info(
            "starting container exec project=%s intent=%s worker=%s phase=execute_healthcheck timeout=%ss",
            project.project.id,
            intent.id,
            worker.name,
            healthcheck_timeout,
        )
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
                "execute cancelled during healthcheck project=%s intent=%s worker=%s reason=%s",
                project.project.id,
                intent.id,
                worker.name,
                cancelled,
            )
            best_effort_release(client, project.project.id, intent.id, worker.name)
            return "cancelled"
        if lease.failure is not None:
            LOG.warning(
                "heartbeat lost during execute healthcheck project=%s intent=%s worker=%s status=%s",
                project.project.id,
                intent.id,
                worker.name,
                lease.failure.status_code,
            )
            best_effort_release(client, project.project.id, intent.id, worker.name)
            return "failed"
        if healthcheck.result.returncode != 0:
            LOG.warning(
                "worker unhealthy project=%s intent=%s worker=%s healthcheck_ms=%s stderr=%s",
                project.project.id,
                intent.id,
                worker.name,
                healthcheck.duration_ms,
                preview(healthcheck.result.stderr),
            )
            best_effort_release(client, project.project.id, intent.id, worker.name)
            return "unhealthy"

        # TODO: 在这里集成 AgentLoop
        # 现在先返回成功，保持系统可运行
        LOG.warning(
            "execute task called but AgentLoop integration not yet complete project=%s intent=%s worker=%s",
            project.project.id,
            intent.id,
            worker.name,
        )

        # 暂时返回 success，等待后续集成
        return "success"

    finally:
        lease.stop()
        task_elapsed = time.perf_counter() - task_started
        LOG.info(
            "execute task finished project=%s intent=%s worker=%s elapsed=%.1fs",
            project.project.id,
            intent.id,
            worker.name,
            task_elapsed,
        )
