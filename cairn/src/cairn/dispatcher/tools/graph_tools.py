"""
Graph Tools - GRAPH Category
=============================
这些 Tool 只能操作 FGS 图，在 Decide 阶段可用。

核心原则：
- 不能执行外部命令
- 不能发送网络请求
- 只能读写图中的 Fact/Step/Goal/Finding
"""

from typing import Any, Optional
from cairn.dispatcher.tools.base import Tool, ToolCategory
from cairn.dispatcher.protocol.client import CairnClient


class AddStepTool(Tool):
    """添加新的 Step 到图中

    Decide 阶段用这个 Tool 来：
    1. 创建新的探索步骤
    2. 分配给特定 Worker
    3. 建立与现有 Fact 的依赖关系
    """

    category = ToolCategory.GRAPH
    name = "add_step"
    description = "Add a new Step to the FGS graph"

    def __init__(self, client: CairnClient, project_id: str):
        self.client = client
        self.project_id = project_id

    async def execute(
        self,
        description: str,
        from_facts: list[str],
        worker: Optional[str] = None,
    ) -> dict[str, Any]:
        """创建新 Step

        Args:
            description: Step 的描述（要做什么）
            from_facts: 依赖的 Fact ID 列表
            worker: 可选的 Worker 名称（如果要立即分配）

        Returns:
            创建的 Step 对象
        """
        step = self.client.create_step(
            project_id=self.project_id,
            from_=from_facts,
            description=description,
            creator="decide",
            worker=worker,
        )
        return {
            "step_id": step.id,
            "description": step.description,
            "from": step.from_,
            "worker": step.worker,
        }

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Step 的描述，说明要做什么",
                        },
                        "from_facts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "依赖的 Fact ID 列表",
                        },
                        "worker": {
                            "type": "string",
                            "description": "可选的 Worker 名称",
                        },
                    },
                    "required": ["description", "from_facts"],
                },
            },
        }


class AddGoalTool(Tool):
    """标记 Goal

    当 Decide 认为某个 Fact 是目标时使用。
    """

    category = ToolCategory.GRAPH
    name = "add_goal"
    description = "Mark a Fact as a Goal in the FGS graph"

    def __init__(self, client: CairnClient, project_id: str):
        self.client = client
        self.project_id = project_id

    async def execute(self, fact_id: str, priority: str = "normal") -> dict[str, Any]:
        """标记 Goal

        Args:
            fact_id: 要标记为 Goal 的 Fact ID
            priority: 优先级（high/normal/low）

        Returns:
            Goal 对象
        """
        goal = self.client.create_goal(
            project_id=self.project_id,
            fact_id=fact_id,
            priority=priority,
        )
        return {
            "goal_id": goal.id,
            "fact_id": goal.fact_id,
            "priority": goal.priority,
        }

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fact_id": {
                            "type": "string",
                            "description": "要标记为 Goal 的 Fact ID",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["high", "normal", "low"],
                            "description": "Goal 的优先级",
                        },
                    },
                    "required": ["fact_id"],
                },
            },
        }


class AddFindingTool(Tool):
    """记录漏洞发现

    这是 Cairn_Y 的核心：当确认漏洞时记录 Finding。
    """

    category = ToolCategory.GRAPH
    name = "add_finding"
    description = "Record a vulnerability finding in the FGS graph"

    def __init__(self, client: CairnClient, project_id: str):
        self.client = client
        self.project_id = project_id

    async def execute(
        self,
        title: str,
        description: str,
        severity: str,
        evidence_fact_ids: list[str],
        cwe: Optional[str] = None,
        cvss_score: Optional[float] = None,
    ) -> dict[str, Any]:
        """记录漏洞

        Args:
            title: 漏洞标题
            description: 漏洞详细描述
            severity: 严重程度（critical/high/medium/low）
            evidence_fact_ids: 证据 Fact ID 列表
            cwe: 可选的 CWE 编号
            cvss_score: 可选的 CVSS 分数

        Returns:
            Finding 对象
        """
        finding = self.client.create_finding(
            project_id=self.project_id,
            title=title,
            description=description,
            severity=severity,
            evidence_fact_ids=evidence_fact_ids,
            cwe=cwe,
            cvss_score=cvss_score,
        )
        return {
            "finding_id": finding.id,
            "title": finding.title,
            "severity": finding.severity,
        }

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "漏洞标题，简明扼要",
                        },
                        "description": {
                            "type": "string",
                            "description": "漏洞详细描述，包括影响和复现步骤",
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "high", "medium", "low"],
                            "description": "严重程度",
                        },
                        "evidence_fact_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "支持这个漏洞的证据 Fact ID 列表",
                        },
                        "cwe": {
                            "type": "string",
                            "description": "CWE 编号（如 CWE-79）",
                        },
                        "cvss_score": {
                            "type": "number",
                            "description": "CVSS 分数（0.0-10.0）",
                        },
                    },
                    "required": ["title", "description", "severity", "evidence_fact_ids"],
                },
            },
        }


class QueryGraphTool(Tool):
    """查询图中的内容

    Decide 用这个来：
    - 查找相关 Fact
    - 检查是否已经探索过
    - 理解当前图的状态
    """

    category = ToolCategory.GRAPH
    name = "query_graph"
    description = "Query Facts, Steps, Goals, and Findings in the FGS graph"

    def __init__(self, client: CairnClient, project_id: str):
        self.client = client
        self.project_id = project_id

    async def execute(
        self,
        query_type: str,
        filters: Optional[dict] = None,
    ) -> dict[str, Any]:
        """查询图

        Args:
            query_type: 查询类型（facts/steps/goals/findings）
            filters: 可选的过滤条件

        Returns:
            查询结果列表
        """
        if query_type == "facts":
            results = self.client.get_facts(self.project_id, filters=filters)
        elif query_type == "steps":
            results = self.client.get_steps(self.project_id, filters=filters)
        elif query_type == "goals":
            results = self.client.get_goals(self.project_id, filters=filters)
        elif query_type == "findings":
            results = self.client.get_findings(self.project_id, filters=filters)
        else:
            raise ValueError(f"Unknown query_type: {query_type}")

        return {
            "query_type": query_type,
            "count": len(results),
            "results": results,
        }

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query_type": {
                            "type": "string",
                            "enum": ["facts", "steps", "goals", "findings"],
                            "description": "要查询的对象类型",
                        },
                        "filters": {
                            "type": "object",
                            "description": "可选的过滤条件",
                        },
                    },
                    "required": ["query_type"],
                },
            },
        }
