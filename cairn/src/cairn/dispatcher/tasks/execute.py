"""
Execute Task - Cairn_Y
======================
Execute 阶段：执行具体操作，只能与外部世界交互。

这是统一 Agent Loop 的第二个应用：
- mode="execute"
- 只能使用 WORLD tools
- 负责战术执行
"""

import logging
from cairn.dispatcher.agent_loop import AgentLoop
from cairn.dispatcher.workers.base import WorkerDriver
from cairn.dispatcher.protocol.client import CairnClient

LOG = logging.getLogger(__name__)


async def run_execute(
    project_id: str,
    step_id: str,
    worker: WorkerDriver,
    client: CairnClient,
) -> str:
    """运行 Execute 阶段

    Execute 负责：
    1. 执行 Step 的具体任务
    2. 运行命令、发送请求
    3. 收集证据和观察结果
    4. 返回 Fact

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
    LOG.info(f"Starting Execute task for project {project_id}, step {step_id}")

    loop = AgentLoop(
        mode="execute",
        project_id=project_id,
        step_id=step_id,
        worker=worker,
        client=client,
    )

    result = await loop.run()

    LOG.info(f"Execute task completed for step {step_id}")
    return result
