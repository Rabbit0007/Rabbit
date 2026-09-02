"""
Decide Task - Cairn_Y
=====================
Decide 阶段：分析图，决定下一步，只能操作图。

这是统一 Agent Loop 的第一个应用：
- mode="decide"
- 只能使用 GRAPH tools
- 负责战略决策
"""

import logging
from cairn.dispatcher.agent_loop import AgentLoop
from cairn.dispatcher.workers.base import WorkerDriver
from cairn.dispatcher.protocol.client import CairnClient

LOG = logging.getLogger(__name__)


async def run_decide(
    project_id: str,
    step_id: str,
    worker: WorkerDriver,
    client: CairnClient,
) -> str:
    """运行 Decide 阶段

    Decide 负责：
    1. 分析当前 FGS 图状态
    2. 决定下一步探索方向
    3. 创建新的 Step
    4. 标记 Goal
    5. 记录 Finding

    Args:
        project_id: 项目 ID
        step_id: 当前 Step ID
        worker: Worker 实例
        client: API 客户端

    Returns:
        最终的结论 Fact

    Raises:
        RuntimeError: Loop 超时或失败
    """
    LOG.info(f"Starting Decide task for project {project_id}, step {step_id}")

    loop = AgentLoop(
        mode="decide",
        project_id=project_id,
        step_id=step_id,
        worker=worker,
        client=client,
    )

    result = await loop.run()

    LOG.info(f"Decide task completed for step {step_id}")
    return result
