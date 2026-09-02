"""
Decide Task - Cairn_Y
=====================
Decide 阶段：分析图，决定下一步，只能操作图。

这是统一 Agent Loop 的第一个应用：
- mode="decide"
- 只能使用 GRAPH tools
- 负责战略决策

这个任务被调度器调用，替代原来的 run_reason_task。
"""

import logging
import time

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease
from cairn.dispatcher.tasks.common import (
    cancel_reason,
    preview,
    run_healthcheck,
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
    """运行 Decide 阶段任务

    Decide 负责：
    1. 分析当前 FGS 图状态
    2. 决定下一步探索方向
    3. 创建新的 Step
    4. 标记 Goal
    5. 记录 Finding

    这个函数保留了原有的工程化逻辑（容器管理、心跳、健康检查），
    但核心 LLM 调用会使用 AgentLoop。

    Args:
        config: 调度配置
        client: API 客户端
        container_manager: 容器管理器
        project: 项目详情
        export_yaml: 导出的项目 YAML
        worker: Worker 配置
        cancellation: 取消令牌

    Returns:
        任务结果状态: "success", "failed", "cancelled", "unhealthy"
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

        # TODO: 在这里集成 AgentLoop
        # 现在先返回成功，保持系统可运行
        LOG.warning(
            "decide task called but AgentLoop integration not yet complete project=%s worker=%s",
            project.project.id,
            worker.name,
        )

        # 暂时返回 success，等待后续集成
        return "success"

    finally:
        lease.stop()
        task_elapsed = time.perf_counter() - task_started
        LOG.info(
            "decide task finished project=%s worker=%s elapsed=%.1fs",
            project.project.id,
            worker.name,
            task_elapsed,
        )
