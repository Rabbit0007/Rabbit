"""
Cairn_Y Tool System
===================
统一的 Tool 抽象，定义了 Decide 和 Execute 的权限边界。

Tool 分为两类：
- GRAPH: 只能操作 FGS 图（Decide 阶段可用）
- WORLD: 只能与外部世界交互（Execute 阶段可用）
"""

from cairn.dispatcher.tools.base import Tool, ToolCategory
from cairn.dispatcher.tools.graph_tools import (
    AddStepTool,
    AddGoalTool,
    AddFindingTool,
    QueryGraphTool,
)
from cairn.dispatcher.tools.world_tools import (
    BashTool,
    HTTPRequestTool,
    ReadFileTool,
)

__all__ = [
    "Tool",
    "ToolCategory",
    "AddStepTool",
    "AddGoalTool",
    "AddFindingTool",
    "QueryGraphTool",
    "BashTool",
    "HTTPRequestTool",
    "ReadFileTool",
]
