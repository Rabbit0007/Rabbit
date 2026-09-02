"""
Unified Agent Loop
==================
Cairn_Y 的核心实现：Decide 和 Execute 共享同一个 Loop。

核心理念：
- 同一个 Loop 逻辑
- 区别只在于允许的 Tool 类别
- Decide 只能用 GRAPH tools
- Execute 只能用 WORLD tools
"""

import logging
from typing import Literal, Optional
from dataclasses import dataclass

from cairn.dispatcher.tools.base import Tool, ToolCategory
from cairn.dispatcher.tools import (
    AddStepTool,
    AddGoalTool,
    AddFindingTool,
    QueryGraphTool,
    BashTool,
    HTTPRequestTool,
    ReadFileTool,
)
from cairn.dispatcher.workers.base import WorkerDriver
from cairn.dispatcher.protocol.client import CairnClient


LOG = logging.getLogger(__name__)


@dataclass
class AgentLoopConfig:
    """Agent Loop 配置"""

    max_iterations: int = 50
    max_tool_calls_per_iteration: int = 10


class AgentLoop:
    """统一的 Agent 运行循环

    Cairn_Y 的核心：Decide 和 Execute 是同一个 Loop，
    只是允许的 Tool 不同。

    Loop 流程：
    1. 读图（获取相关子图）
    2. 构造 Prompt
    3. 调用 LLM（传入允许的 Tools）
    4. 执行 Tool calls
    5. 更新图
    6. 重复或结束
    """

    def __init__(
        self,
        mode: Literal["decide", "execute"],
        project_id: str,
        step_id: str,
        worker: WorkerDriver,
        client: CairnClient,
        config: Optional[AgentLoopConfig] = None,
    ):
        self.mode = mode
        self.project_id = project_id
        self.step_id = step_id
        self.worker = worker
        self.client = client
        self.config = config or AgentLoopConfig()

        # 根据模式选择允许的 Tool 类别
        if mode == "decide":
            self.allowed_categories = {ToolCategory.GRAPH}
            LOG.info(f"Agent Loop [decide] for step {step_id}: only GRAPH tools allowed")
        else:  # execute
            self.allowed_categories = {ToolCategory.WORLD}
            LOG.info(f"Agent Loop [execute] for step {step_id}: only WORLD tools allowed")

        # 初始化 Tools
        self._init_tools()

    def _init_tools(self):
        """初始化所有可用的 Tools"""
        # GRAPH tools
        self.graph_tools = [
            AddStepTool(self.client, self.project_id),
            AddGoalTool(self.client, self.project_id),
            AddFindingTool(self.client, self.project_id),
            QueryGraphTool(self.client, self.project_id),
        ]

        # WORLD tools
        self.world_tools = [
            BashTool(),
            HTTPRequestTool(),
            ReadFileTool(),
        ]

        # 所有 Tools
        self.all_tools = self.graph_tools + self.world_tools

    def _get_available_tools(self) -> list[Tool]:
        """根据模式返回允许的 Tools"""
        return [t for t in self.all_tools if t.category in self.allowed_categories]

    def _find_tool(self, name: str, available_tools: list[Tool]) -> Optional[Tool]:
        """根据名称查找 Tool"""
        for tool in available_tools:
            if tool.name == name:
                return tool
        return None

    async def run(self) -> str:
        """运行 Agent Loop 直到任务完成

        Returns:
            最终的结论 Fact

        Raises:
            RuntimeError: 超过最大迭代次数
        """
        conversation = []
        available_tools = self._get_available_tools()

        LOG.info(
            f"Starting Agent Loop [{self.mode}] for step {self.step_id}, "
            f"available tools: {[t.name for t in available_tools]}"
        )

        for iteration in range(self.config.max_iterations):
            LOG.debug(f"Agent Loop [{self.mode}] iteration {iteration + 1}/{self.config.max_iterations}")

            # 1. 读图 - 获取当前项目状态
            graph_context = await self._load_graph_context()

            # 2. 构造 Prompt
            if iteration == 0:
                system_prompt = self._build_system_prompt()
                user_prompt = self._build_initial_prompt(graph_context)
            else:
                system_prompt = None
                user_prompt = self._build_continuation_prompt(graph_context)

            # 3. 调用 LLM
            try:
                response = await self._call_llm(
                    conversation=conversation,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    available_tools=available_tools,
                )
            except Exception as e:
                LOG.error(f"LLM call failed in iteration {iteration}: {e}")
                raise

            # 添加到会话历史
            conversation.append({"role": "user", "content": user_prompt})
            conversation.append({"role": "assistant", "content": response})

            # 4. 执行 Tool calls（如果有）
            if hasattr(response, "tool_calls") and response.tool_calls:
                LOG.info(f"Executing {len(response.tool_calls)} tool calls")
                tool_results = await self._execute_tools(response.tool_calls, available_tools)
                conversation.append({"role": "tool", "content": tool_results})

            # 5. 检查是否完成
            if self._is_task_complete(response):
                final_fact = self._extract_conclusion(response)
                LOG.info(f"Agent Loop [{self.mode}] completed after {iteration + 1} iterations")
                return final_fact

        # 超过最大迭代次数
        raise RuntimeError(
            f"Agent Loop [{self.mode}] exceeded max iterations ({self.config.max_iterations})"
        )

    async def _load_graph_context(self) -> dict:
        """加载图上下文

        TODO: 优化为只加载相关子图
        """
        # 简化版：加载所有内容
        facts = self.client.get_facts(self.project_id)
        steps = self.client.get_steps(self.project_id)
        goals = self.client.get_goals(self.project_id)
        findings = self.client.get_findings(self.project_id)

        return {
            "facts": facts,
            "steps": steps,
            "goals": goals,
            "findings": findings,
        }

    def _build_system_prompt(self) -> str:
        """构造系统 Prompt"""
        if self.mode == "decide":
            return """You are the Decide agent in the Cairn_Y system.

Your job:
- Analyze the FGS graph
- Decide what to explore next
- Create new Steps
- Mark Goals
- Record Findings when vulnerabilities are confirmed

You can ONLY use GRAPH tools:
- add_step: Create a new exploration step
- add_goal: Mark a fact as a goal
- add_finding: Record a confirmed vulnerability
- query_graph: Search the graph

You CANNOT execute commands or send requests. That's Execute's job.
"""
        else:  # execute
            return """You are the Execute agent in the Cairn_Y system.

Your job:
- Execute the Step's task
- Run commands, send requests, read files
- Collect evidence and observations
- Return Facts

You can ONLY use WORLD tools:
- bash: Execute shell commands
- http_request: Send HTTP requests
- read_file: Read file contents

You CANNOT modify the graph. That's Decide's job.
"""

    def _build_initial_prompt(self, graph_context: dict) -> str:
        """构造初始 Prompt"""
        # 获取当前 Step 信息
        step = self.client.get_step(self.project_id, self.step_id)

        prompt = f"""Current Step: {step.description}

Graph Context:
- Facts: {len(graph_context['facts'])}
- Steps: {len(graph_context['steps'])}
- Goals: {len(graph_context['goals'])}
- Findings: {len(graph_context['findings'])}
"""

        if self.mode == "decide":
            prompt += """
Your task: Analyze the graph and decide what to do next.
"""
        else:  # execute
            prompt += f"""
Your task: Execute "{step.description}" and return observations.
"""

        return prompt

    def _build_continuation_prompt(self, graph_context: dict) -> str:
        """构造继续 Prompt"""
        return "Continue based on the tool results above."

    async def _call_llm(
        self,
        conversation: list,
        system_prompt: Optional[str],
        user_prompt: str,
        available_tools: list[Tool],
    ) -> dict:
        """调用 LLM

        TODO: 适配实际的 Worker API
        """
        messages = conversation.copy()
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        tools = [t.get_schema() for t in available_tools]

        # 这里需要适配实际的 Worker chat API
        response = await self.worker.chat(messages=messages, tools=tools)
        return response

    async def _execute_tools(
        self,
        tool_calls: list,
        available_tools: list[Tool],
    ) -> dict:
        """执行 Tool 调用并返回结果"""
        results = {}

        for call in tool_calls:
            tool = self._find_tool(call.name, available_tools)

            if not tool:
                # Tool 不在允许列表中
                error_msg = f"Tool '{call.name}' not allowed in {self.mode} mode"
                LOG.warning(error_msg)
                results[call.id] = {"error": error_msg}
                continue

            # 执行 Tool
            try:
                LOG.debug(f"Executing tool: {call.name} with params: {call.parameters}")
                result = await tool.execute(**call.parameters)
                results[call.id] = {"success": True, "result": result}
                LOG.debug(f"Tool {call.name} succeeded: {result}")
            except Exception as e:
                LOG.error(f"Tool {call.name} failed: {e}")
                results[call.id] = {"error": str(e)}

        return results

    def _is_task_complete(self, response: dict) -> bool:
        """检查任务是否完成

        TODO: 定义完成条件
        """
        # 简化版：检查响应中是否有 "DONE" 标记
        content = response.get("content", "")
        return "DONE" in content or "COMPLETE" in content

    def _extract_conclusion(self, response: dict) -> str:
        """提取最终结论

        TODO: 定义结论格式
        """
        return response.get("content", "Task completed")
