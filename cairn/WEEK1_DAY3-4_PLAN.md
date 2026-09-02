# Week 1 Day 3-4: 实现统一 Agent Loop

## 目标
实现文档中描述的"统一 Agent Loop"抽象，让 Decide 和 Execute 共享同一运行机制。

## 背景
根据 Cairn_Y 论文：
> 我们的 Agent 在 Decide 和 Execute 都是同一个 Loop：读图 → 调 Tool → 写图。区别只在于允许哪些 Tool。

当前状态：
- ✅ `tasks/bootstrap.py` - Bootstrap 任务
- ❌ `tasks/reason.py` - 旧的 Reason 任务（需要重命名为 decide.py）
- ❌ 没有独立的 Execute 任务实现
- ❌ 没有统一的 Loop 抽象

## 实施计划

### Step 1: 创建统一 Tool 系统 (Day 3 上午)
**目标**：定义清晰的 Tool 权限边界

#### 1.1 创建 `dispatcher/tools/base.py`
```python
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

class ToolCategory(Enum):
    """Tool 类别定义 Agent Loop 的权限边界"""
    GRAPH = "graph"      # 只能操作 FGS 图
    WORLD = "world"      # 只能与外部世界交互
    
class Tool(ABC):
    """统一 Tool 抽象"""
    category: ToolCategory
    name: str
    description: str
    
    @abstractmethod
    async def execute(self, **params) -> Any:
        pass
    
    @abstractmethod
    def get_schema(self) -> dict:
        """返回 Tool 的参数 schema（用于 LLM function calling）"""
        pass
```

#### 1.2 创建 `dispatcher/tools/graph_tools.py`
```python
# GRAPH category tools - Decide 阶段可用
class AddStepTool(Tool):
    """添加新的 Step 到图中"""
    category = ToolCategory.GRAPH
    name = "add_step"
    
class AddGoalTool(Tool):
    """标记目标"""
    category = ToolCategory.GRAPH
    name = "add_goal"
    
class AddFindingTool(Tool):
    """记录漏洞发现"""
    category = ToolCategory.GRAPH
    name = "add_finding"
    
class QueryGraphTool(Tool):
    """查询图中的 Fact/Step/Goal"""
    category = ToolCategory.GRAPH
    name = "query_graph"
```

#### 1.3 创建 `dispatcher/tools/world_tools.py`
```python
# WORLD category tools - Execute 阶段可用
class BashTool(Tool):
    """执行 bash 命令"""
    category = ToolCategory.WORLD
    name = "bash"
    
class HTTPRequestTool(Tool):
    """发送 HTTP 请求"""
    category = ToolCategory.WORLD
    name = "http_request"
    
class ReadFileTool(Tool):
    """读取文件内容"""
    category = ToolCategory.WORLD
    name = "read_file"
```

### Step 2: 实现统一 Agent Loop (Day 3 下午)
**目标**：一个 Loop 同时支持 Decide 和 Execute

#### 2.1 创建 `dispatcher/agent_loop.py`
```python
from typing import List, Literal
from cairn.dispatcher.tools.base import Tool, ToolCategory

class AgentLoop:
    """统一的 Agent 运行循环
    
    Cairn_Y 的核心：Decide 和 Execute 是同一个 Loop，
    只是允许的 Tool 不同。
    """
    
    def __init__(
        self,
        mode: Literal["decide", "execute"],
        project_id: str,
        step_id: str,
        worker: Worker,
        client: InternalAPIClient,
    ):
        self.mode = mode
        self.project_id = project_id
        self.step_id = step_id
        self.worker = worker
        self.client = client
        
        # 根据模式选择允许的 Tool 类别
        if mode == "decide":
            self.allowed_categories = {ToolCategory.GRAPH}
        else:  # execute
            self.allowed_categories = {ToolCategory.WORLD}
    
    async def run(self) -> str:
        """运行 Agent Loop 直到任务完成
        
        Loop 流程：
        1. 读图（获取相关子图）
        2. 构造 Prompt
        3. 调用 LLM（传入允许的 Tools）
        4. 执行 Tool calls
        5. 更新图
        6. 重复或结束
        """
        conversation = []
        max_iterations = 50
        
        for i in range(max_iterations):
            # 1. 读图
            graph_context = await self._load_graph_context()
            
            # 2. 构造 Prompt
            if i == 0:
                system_prompt = self._build_system_prompt()
                user_prompt = self._build_initial_prompt(graph_context)
            else:
                user_prompt = self._build_continuation_prompt(graph_context)
            
            # 3. 调用 LLM
            available_tools = self._get_available_tools()
            response = await self.worker.chat(
                messages=conversation + [{"role": "user", "content": user_prompt}],
                tools=[t.get_schema() for t in available_tools],
                system=system_prompt if i == 0 else None,
            )
            
            conversation.append({"role": "assistant", "content": response})
            
            # 4. 执行 Tool calls
            if response.tool_calls:
                tool_results = await self._execute_tools(response.tool_calls, available_tools)
                conversation.append({"role": "tool", "content": tool_results})
            
            # 5. 检查是否完成
            if self._is_task_complete(response):
                final_fact = self._extract_conclusion(response)
                return final_fact
        
        raise RuntimeError(f"Agent Loop 超过最大迭代次数 {max_iterations}")
    
    def _get_available_tools(self) -> List[Tool]:
        """根据模式返回允许的 Tools"""
        all_tools = self._load_all_tools()
        return [t for t in all_tools if t.category in self.allowed_categories]
    
    async def _execute_tools(self, tool_calls, available_tools) -> dict:
        """执行 Tool 调用并返回结果"""
        results = {}
        for call in tool_calls:
            tool = self._find_tool(call.name, available_tools)
            if not tool:
                results[call.id] = {"error": f"Tool {call.name} not allowed in {self.mode} mode"}
                continue
            
            try:
                result = await tool.execute(**call.parameters)
                results[call.id] = {"success": True, "result": result}
            except Exception as e:
                results[call.id] = {"error": str(e)}
        
        return results
```

### Step 3: 重构现有任务使用 Agent Loop (Day 4 上午)

#### 3.1 重命名 `tasks/reason.py` → `tasks/decide.py`
```bash
git mv src/cairn/dispatcher/tasks/reason.py src/cairn/dispatcher/tasks/decide.py
```

#### 3.2 简化 `tasks/decide.py`
```python
from cairn.dispatcher.agent_loop import AgentLoop

async def run_decide(
    project_id: str,
    step_id: str,
    worker: Worker,
    client: InternalAPIClient,
) -> str:
    """Decide 阶段：分析图，决定下一步，只能操作图"""
    loop = AgentLoop(
        mode="decide",
        project_id=project_id,
        step_id=step_id,
        worker=worker,
        client=client,
    )
    return await loop.run()
```

#### 3.3 创建 `tasks/execute.py`
```python
from cairn.dispatcher.agent_loop import AgentLoop

async def run_execute(
    project_id: str,
    step_id: str,
    worker: Worker,
    client: InternalAPIClient,
) -> str:
    """Execute 阶段：执行具体操作，只能与外部世界交互"""
    loop = AgentLoop(
        mode="execute",
        project_id=project_id,
        step_id=step_id,
        worker=worker,
        client=client,
    )
    return await loop.run()
```

### Step 4: 更新调度器 (Day 4 下午)

#### 4.1 更新 `scheduler/loop.py`
```python
# 旧代码
if step_type == "reason":
    from cairn.dispatcher.tasks.reason import run_reason
    result = await run_reason(...)

# 新代码
if step_type == "decide":
    from cairn.dispatcher.tasks.decide import run_decide
    result = await run_decide(...)
elif step_type == "execute":
    from cairn.dispatcher.tasks.execute import run_execute
    result = await run_execute(...)
```

## 验证检查清单

- [ ] Tool 系统：所有 Tool 都有明确的 category
- [ ] Agent Loop：Decide 只能调用 GRAPH tools
- [ ] Agent Loop：Execute 只能调用 WORLD tools
- [ ] 跨边界调用会被拒绝并有清晰错误信息
- [ ] 现有 Bootstrap 任务仍然正常工作
- [ ] Decide 任务可以生成新的 Step
- [ ] Execute 任务可以执行 bash/http 命令

## 文件清单
```
cairn/src/cairn/dispatcher/
├── tools/
│   ├── __init__.py
│   ├── base.py              # NEW: Tool 抽象和类别定义
│   ├── graph_tools.py       # NEW: GRAPH category tools
│   └── world_tools.py       # NEW: WORLD category tools
├── agent_loop.py            # NEW: 统一 Agent Loop
├── tasks/
│   ├── bootstrap.py         # 保持不变
│   ├── decide.py            # RENAMED from reason.py，简化
│   └── execute.py           # NEW: Execute 任务
└── scheduler/
    └── loop.py              # 更新调用逻辑
```

## 下一步 (Week 2)
Week 1 完成后，Week 2 将专注于：
1. 优化 Prompt（decide.md 和 execute.md）
2. 实现增量 Decide（只分析变化部分）
3. 添加子图传递优化（不传完整图）
