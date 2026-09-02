"""
Base Tool Abstraction
=====================
定义 Cairn_Y 的 Tool 系统基础。

核心理念：
- Decide 只能用 GRAPH tools（操作图）
- Execute 只能用 WORLD tools（操作外部世界）
- 统一的接口让 Agent Loop 可以无差别调用
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class ToolCategory(Enum):
    """Tool 类别定义 Agent Loop 的权限边界"""

    GRAPH = "graph"  # 只能操作 FGS 图：读写 Fact/Step/Goal/Finding
    WORLD = "world"  # 只能与外部世界交互：bash/http/file 等


class Tool(ABC):
    """统一 Tool 抽象

    所有 Tool 必须：
    1. 声明自己的 category（GRAPH 或 WORLD）
    2. 提供 name 和 description
    3. 实现 execute() 方法
    4. 提供 get_schema() 用于 LLM function calling
    """

    category: ToolCategory
    name: str
    description: str

    @abstractmethod
    async def execute(self, **params) -> Any:
        """执行 Tool 的具体逻辑

        Args:
            **params: Tool 参数（来自 LLM 的 function call）

        Returns:
            Tool 执行结果

        Raises:
            Exception: 执行失败时抛出异常
        """
        pass

    @abstractmethod
    def get_schema(self) -> dict:
        """返回 Tool 的 JSON Schema

        用于 LLM function calling，定义：
        - 参数类型
        - 必需参数
        - 参数描述

        Returns:
            符合 OpenAI function calling 格式的 schema
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} category={self.category.value} name={self.name}>"
