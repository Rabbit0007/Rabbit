# Cairn → Cairn_Y 核心重构方案
## 聚焦：漏洞发现效果最大化

> **目标**：完全转向 Cairn_Y 架构，最大化漏洞发现能力  
> **非目标**：成本优化、安全加固（后续阶段）  
> **时间线**：3-4 周完成核心重构  

---

## 一、核心问题识别

### 影响漏洞发现效果的关键问题

| 问题 | 当前状态 | 对漏洞发现的影响 | 优先级 |
|-----|---------|----------------|--------|
| **Intent/Step 概念混用** | 代码中大量别名和兼容代码 | ❌ 模型理解困难，决策质量下降 | 🔴 P0 |
| **缺少统一 Agent Loop** | Decide/Execute 各自实现 | ❌ 难以统一优化提示词和工具 | 🔴 P0 |
| **Bootstrap 逻辑不清晰** | 独立任务，直接生成 Fact | ❌ 初始探索方向可能偏离 | 🔴 P0 |
| **Finding 定义固化** | 只支持漏洞类型 | ⚠️ CTF/代码审计场景受限 | 🟡 P1 |
| **Sub Goal 调度简陋** | 基本的优先级排序 | ⚠️ 无法阶段性聚焦，容易发散 | 🟡 P1 |
| **FGS 图传递冗余** | 每次传完整图 | ⚠️ 上下文噪音，关键信息淹没 | 🟢 P2 |

### 关键洞察

根据 Cairn_Y 文档，漏洞发现的核心在于：
1. **清晰的状态空间搜索** - FGS 图必须准确反映探索进度
2. **高质量的 Decide 决策** - 能准确识别高价值探索方向
3. **专注的 Execute 执行** - 每个 Step 深入彻底，不浅尝辄止
4. **阶段性目标推进** - Sub Goal 帮助分解复杂任务

**当前最大瓶颈**：概念混乱 + Bootstrap 逻辑不当导致初始探索方向偏差

---

## 二、核心重构方案（3-4周）

### Week 1: 清理架构，统一概念 🔴

#### 任务 1.1：全面清理 Intent/Reason 概念

**目标**：代码中彻底移除旧架构概念

```bash
# 执行计划
1. 创建重构分支
   git checkout -b refactor/cairn-to-cairn-y

2. 删除所有类型别名
   # models.py
   - 删除 CreateIntentRequest = CreateStepRequest
   - 删除 ReasonCheckpoint = DecideCheckpoint  
   - 删除 ProjectReason = ProjectDecide
   - 删除 Intent 类（如果不再需要）

3. 删除数据库迁移代码
   # db.py
   - 删除 _migrate_intents_to_steps()
   - 删除 projects 表的 reason_* 列引用

4. 统一 API 路由
   # routers/
   - 确保所有端点使用 /steps, /goals, /facts, /findings
   - 移除任何 /intents 或 /reason 端点

5. 清理提示词目录
   # dispatcher/prompts/
   - 删除 prompts/mock/ 或重命名为 prompts/legacy/
   - 确保只使用 prompts/default/ 中的 FGS 术语提示词
```

**验收标准**：
```bash
# 全代码库搜索，确保不再出现旧概念
grep -r "Intent" cairn/src/ | grep -v "# legacy" | wc -l  # 应为 0
grep -r "Reason" cairn/src/ | grep -v "Decide" | wc -l     # 应为 0
```

#### 任务 1.2：统一术语和注释

```python
# 所有代码注释和日志统一为：
- Fact: 事实，已确认的客观发现
- Goal: 目标，完成条件（主目标和子目标）
- Step: 步骤，探索动作，如何从事实产生新事实
- Finding: 漏洞或其他关键发现
- Decide: 决策活动，分析 FGS 图并规划
- Execute: 执行活动，执行步骤并产生事实
```

**产出**：
- ✅ 代码库完全使用 Cairn_Y 术语
- ✅ 无旧概念残留
- ✅ 所有测试通过

---

### Week 2: 实现统一 Agent Loop 🔴

#### 任务 2.1：设计 Agent Loop 抽象

**核心设计理念**：Decide 和 Execute 是**同一个 Agent Loop 注入了不同的 Tool 和 Prompt**

```python
# cairn/src/cairn/dispatcher/agent_loop.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

class Tool(Protocol):
    """工具协议"""
    @property
    def name(self) -> str: ...
    
    @property 
    def description(self) -> str: ...
    
    def execute(self, **kwargs) -> Any: ...
    
    def validate_input(self, **kwargs) -> bool:
        """验证输入参数"""
        return True


@dataclass
class AgentContext:
    """Agent 运行上下文"""
    project_id: str
    graph_snapshot: str          # FGS 图的 YAML
    available_tools: list[Tool]  # 可用工具列表
    prompt_template: str         # 提示词模板
    task_specific_vars: dict     # 任务特定变量（如 step_id）
    timeout: int = 300
    session: str | None = None


@dataclass 
class AgentResult:
    """Agent 执行结果"""
    success: bool
    kind: str                    # "complete", "steps", "fact", "rejected" 等
    data: dict | None
    raw_output: str
    duration_ms: int
    session: str | None = None


class AgentLoop:
    """统一的 Agent Loop 实现
    
    核心职责：
    1. 渲染提示词（注入 FGS 图 + 工具说明）
    2. 调用 Worker 执行
    3. 解析和验证输出
    4. 确保只使用被注入的工具
    """
    
    def __init__(self, worker_config, worker_driver):
        self.worker_config = worker_config
        self.driver = worker_driver
    
    def run(self, context: AgentContext) -> AgentResult:
        """运行 Agent Loop"""
        # 1. 构建完整提示词
        prompt = self._build_prompt(context)
        
        # 2. 执行
        result = self._execute_worker(prompt, context)
        
        # 3. 解析输出
        parsed = self._parse_output(result.stdout, result.stderr)
        
        # 4. 验证工具使用（未来扩展）
        # self._validate_tool_usage(parsed, context.available_tools)
        
        return AgentResult(
            success=result.returncode == 0,
            kind=parsed.get("kind"),
            data=parsed.get("data"),
            raw_output=result.stdout,
            duration_ms=result.duration_ms,
            session=result.session,
        )
    
    def _build_prompt(self, context: AgentContext) -> str:
        """构建提示词，注入图和工具"""
        tools_desc = self._format_tools(context.available_tools)
        
        replacements = {
            "graph_yaml": context.graph_snapshot,
            "available_tools": tools_desc,
            **context.task_specific_vars,
        }
        
        return render_prompt(context.prompt_template, replacements)
    
    def _format_tools(self, tools: list[Tool]) -> str:
        """格式化工具说明"""
        lines = ["# Available Tools\n"]
        for tool in tools:
            lines.append(f"## {tool.name}")
            lines.append(f"{tool.description}\n")
        return "\n".join(lines)
    
    def _execute_worker(self, prompt, context):
        """执行 Worker"""
        # 调用现有的 worker 驱动
        pass
    
    def _parse_output(self, stdout, stderr):
        """解析输出"""
        # 使用现有的 parse_json_output
        pass
```

#### 任务 2.2：定义 Tool 系统

```python
# cairn/src/cairn/dispatcher/tools.py

from typing import Any
from cairn.dispatcher.protocol.client import CairnClient


class BaseTool:
    """工具基类"""
    def __init__(self, client: CairnClient, project_id: str):
        self.client = client
        self.project_id = project_id


# ========== Decide 专用工具 ==========

class ReadGraphTool(BaseTool):
    """读取 FGS 图（只读）"""
    name = "read_graph"
    description = "Read the current FGS graph snapshot"
    
    def execute(self) -> str:
        """返回图的 YAML 表示"""
        # 实际上图已经在提示词中，这个工具可能是冗余的
        pass


class CreateStepTool(BaseTool):
    """创建新步骤"""
    name = "create_step"
    description = "Create a new exploration step"
    
    def execute(self, from_facts: list[str], description: str, 
                goal_id: str = None, priority: int = 0) -> dict:
        resp = self.client.create_step(
            self.project_id, from_facts, description, 
            "decide", goal_id=goal_id, priority=priority
        )
        return {"success": resp.ok, "step_id": resp.json().get("id")}


class UpdateStepTool(BaseTool):
    """更新步骤（调整优先级或废弃）"""
    name = "update_step"
    description = "Update step priority or abandon a step"
    
    def execute(self, step_id: str, priority: int = None, 
                abandoned: bool = None) -> dict:
        resp = self.client.update_step(
            self.project_id, step_id, 
            priority=priority, abandoned=abandoned
        )
        return {"success": resp.ok}


class CreateGoalTool(BaseTool):
    """创建子目标"""
    name = "create_goal"
    description = "Create a sub-goal to decompose the problem"
    
    def execute(self, description: str, parent_goal_id: str = None, 
                priority: int = 0) -> dict:
        resp = self.client.create_goal(
            self.project_id, description, "decide",
            parent_goal_id=parent_goal_id, priority=priority
        )
        return {"success": resp.ok, "goal_id": resp.json().get("id")}


class CompleteGoalTool(BaseTool):
    """完成目标"""
    name = "complete_goal"
    description = "Mark a goal as completed"
    
    def execute(self, goal_id: str, from_facts: list[str], 
                description: str) -> dict:
        resp = self.client.complete_goal(
            self.project_id, goal_id, from_facts, description, "decide"
        )
        return {"success": resp.ok}


# ========== Execute 专用工具 ==========

class BashTool(BaseTool):
    """执行 Bash 命令"""
    name = "bash"
    description = "Execute bash command in the container"
    
    def execute(self, command: str) -> dict:
        # 这里需要调用容器执行接口
        # 暂时返回占位符
        return {"stdout": "", "stderr": "", "returncode": 0}


class ReadFileTool(BaseTool):
    """读取文件"""
    name = "read_file"
    description = "Read file content"
    
    def execute(self, path: str) -> str:
        # 容器内读取文件
        pass


class WriteFileTool(BaseTool):
    """写入文件"""
    name = "write_file"
    description = "Write content to file"
    
    def execute(self, path: str, content: str) -> bool:
        # 容器内写入文件
        pass


class SubmitFactTool(BaseTool):
    """提交新事实"""
    name = "submit_fact"
    description = "Submit a new fact discovered during execution"
    
    def execute(self, description: str) -> dict:
        # 这个实际上是通过 conclude API 提交的
        # 这里只是记录意图
        return {"description": description}


class SubmitFindingTool(BaseTool):
    """提交漏洞发现"""
    name = "submit_finding"  
    description = "Submit a security vulnerability or finding"
    
    def execute(self, title: str, severity: str, 
                description: str) -> dict:
        return {
            "finding": {
                "title": title,
                "severity": severity,
                "description": description,
            }
        }


# ========== 工具注册表 ==========

DECIDE_TOOLS = [
    ReadGraphTool,
    CreateStepTool,
    UpdateStepTool,
    CreateGoalTool,
    CompleteGoalTool,
]

EXECUTE_TOOLS = [
    ReadGraphTool,
    BashTool,
    ReadFileTool,
    WriteFileTool,
    SubmitFactTool,
    SubmitFindingTool,
]
```

#### 任务 2.3：重构 Decide 使用 Agent Loop

```python
# cairn/src/cairn/dispatcher/tasks/decide_v2.py

from cairn.dispatcher.agent_loop import AgentLoop, AgentContext
from cairn.dispatcher.tools import DECIDE_TOOLS

def run_decide_task_v2(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    project: ProjectDetail,
    export_yaml: str,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    """使用统一 Agent Loop 的 Decide 实现"""
    
    driver = get_driver(worker.type, config.runtime.execution)
    lease = HeartbeatLease.for_decide(client, project.project.id, 
                                      worker.name, config.runtime.interval)
    lease.start()
    
    try:
        # 准备工具
        tools = [Tool(client, project.project.id) for Tool in DECIDE_TOOLS]
        
        # 准备上下文
        open_steps = [
            {"id": s.id, "from": s.from_, "description": s.description,
             "goal_id": s.goal_id, "priority": s.priority}
            for s in project.steps
            if s.to is None and not s.abandoned
        ]
        
        context = AgentContext(
            project_id=project.project.id,
            graph_snapshot=export_yaml,
            available_tools=tools,
            prompt_template="decide.md",
            task_specific_vars={
                "open_steps": format_open_steps(open_steps),
                "fact_ids": format_fact_ids([f.id for f in project.facts]),
                "max_steps": str(config.tasks.decide.max_steps),
            },
            timeout=config.tasks.decide.timeout,
        )
        
        # 运行 Agent Loop
        loop = AgentLoop(worker, driver)
        result = loop.run(context)
        
        # 处理结果
        if not result.success:
            return "failed"
        
        return _handle_decide_result(client, project, result, worker.name)
        
    finally:
        lease.stop()


def _handle_decide_result(client, project, result, worker_name):
    """处理 Decide 结果"""
    kind = result.kind
    data = result.data
    
    if kind == "complete":
        # 完成目标
        resp = client.complete_goal(
            project.project.id, data["goal_id"], 
            data["from"], data["description"], worker_name
        )
        return "success" if resp.ok else "failed"
    
    if kind == "steps":
        # 创建步骤
        created = 0
        for step_data in data["steps"]:
            resp = client.create_step(
                project.project.id, step_data["from"], 
                step_data["description"], worker_name,
                goal_id=step_data.get("goal_id"),
                priority=step_data.get("priority", 0)
            )
            if resp.ok:
                created += 1
        return "success" if created > 0 else "failed"
    
    if kind == "step_updates":
        # 更新步骤
        for update in data.get("step_updates", []):
            sid = update.get("id")
            if update.get("action") == "abandon":
                client.update_step(project.project.id, sid, abandoned=True)
            else:
                priority = update.get("priority")
                client.update_step(project.project.id, sid, priority=priority)
        return "success"
    
    if kind == "sub_goals":
        # 管理子目标
        for sg in data.get("sub_goals", []):
            if sg.get("action") == "create":
                client.create_goal(
                    project.project.id, sg["description"], worker_name,
                    parent_goal_id=sg.get("parent_goal_id"),
                    priority=sg.get("priority", 0)
                )
            elif sg.get("action") == "complete":
                client.update_goal(project.project.id, sg["id"], 
                                  status="completed")
        return "success"
    
    return "success"  # noop
```

#### 任务 2.4：重构 Execute 使用 Agent Loop

```python
# cairn/src/cairn/dispatcher/tasks/execute_v2.py

from cairn.dispatcher.agent_loop import AgentLoop, AgentContext
from cairn.dispatcher.tools import EXECUTE_TOOLS

def run_execute_task_v2(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    project: ProjectDetail,
    export_yaml: str,
    step: Step,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    """使用统一 Agent Loop 的 Execute 实现"""
    
    driver = get_driver(worker.type, config.runtime.execution)
    lease = HeartbeatLease.for_step(client, project.project.id, 
                                    step.id, worker.name, 
                                    config.runtime.interval)
    lease.start()
    
    try:
        # 准备工具
        tools = [Tool(client, project.project.id) for Tool in EXECUTE_TOOLS]
        
        # 准备上下文
        context = AgentContext(
            project_id=project.project.id,
            graph_snapshot=export_yaml,
            available_tools=tools,
            prompt_template="execute.md",
            task_specific_vars={
                "step_id": step.id,
                "step_description": step.description,
            },
            timeout=config.tasks.execute.timeout,
        )
        
        # 运行 Agent Loop
        loop = AgentLoop(worker, driver)
        result = loop.run(context)
        
        # 处理结果
        if not result.success:
            return "failed"
        
        if result.kind == "rejected":
            return "rejected"
        
        if result.kind == "fact":
            # 提交事实
            return write_conclude_result(
                client, project.project.id, step.id, worker.name,
                result.data["description"],
                finding=result.data.get("finding"),
                source="execute",
            )
        
        return "failed"
        
    finally:
        lease.stop()
```

**产出**：
- ✅ Agent Loop 抽象层
- ✅ Tool 系统（Decide 和 Execute 工具分离）
- ✅ Decide 和 Execute 使用统一实现

---

### Week 3: 重构 Bootstrap + 优化提示词 🔴

#### 任务 3.1：Bootstrap 彻底重构

**当前问题**：Bootstrap 作为独立任务类型，逻辑复杂，不符合 FGS 图纯粹性

**新方案**：Bootstrap 变成"创建初始 Facts + 触发第一次 Decide"

```python
# cairn/src/cairn/server/services.py

def create_project_with_bootstrap(
    title: str,
    origin: str, 
    goal_description: str,
    hints: list[dict] = None,
) -> str:
    """
    创建项目并初始化 FGS 图
    
    流程：
    1. 创建项目
    2. 创建 origin 和 goal 两个初始 Fact
    3. 创建主 Goal
    4. 触发第一次 Decide（空白起始状态）
    5. Decide 分析 origin 和 goal，生成初始探索 Steps
    """
    project_id = _create_project(title)
    
    # 创建初始 Facts
    origin_fact = create_fact(project_id, "origin", origin)
    goal_fact = create_fact(project_id, "goal", goal_description)
    
    # 创建主 Goal
    main_goal = create_goal(
        project_id, 
        description=goal_description,
        creator="system",
        priority=10
    )
    
    # 添加 Hints
    if hints:
        for hint in hints:
            create_hint(project_id, hint["content"], hint["creator"])
    
    # 触发第一次 Decide
    # Decide 会看到：
    # - Facts: origin, goal
    # - Goals: [main_goal]
    # - Steps: []（空）
    # - Hints: [...]
    # 
    # Decide 应该生成初始的探索 Steps，例如：
    # - "从 origin 入口进行信息收集"
    # - "分析 goal 描述的目标类型"
    # - "扫描 origin 的技术栈和攻击面"
    trigger_decide(project_id, trigger="bootstrap")
    
    return project_id
```

**提示词优化**：

```markdown
# prompts/default/decide.md（针对 bootstrap 场景优化）

# Task
You will receive a YAML snapshot of the FGS graph (Facts, Goals, Steps).

## Special Case: Bootstrap (Initial State)

If you see:
- Facts: only "origin" and "goal"  
- Goals: one main goal
- Steps: empty or very few
- This means the project just started

**Your mission**: Propose initial exploration steps to kick off the search.

### Guidelines for Bootstrap Steps

1. **Information Gathering**: Start by reconnaissance
   - Scan the origin target (port scan, tech stack detection, directory enum)
   - Identify attack surface (web/api/network/mobile)
   - Collect initial facts (versions, frameworks, endpoints)

2. **Goal Analysis**: Understand what we're looking for
   - If goal mentions "vulnerabilities", plan security testing steps
   - If goal mentions "flags", plan CTF-style exploitation steps
   - If goal mentions "code audit", plan code analysis steps

3. **Parallel Dimensions**: Propose 3-5 independent initial steps covering:
   - Reconnaissance (passive and active)
   - Authentication analysis
   - Input validation testing
   - Business logic exploration
   - Configuration analysis

4. **Avoid Premature Exploitation**: Don't jump into exploitation before reconnaissance

### Example Bootstrap Steps

For a web application penetration test:
```json
{"accepted": true, "data": {"steps": [
  {"from": ["origin"], "description": "Perform comprehensive port and service scan to identify all exposed services", "priority": 2},
  {"from": ["origin", "goal"], "description": "Spider and map all web application endpoints, forms, and APIs", "priority": 2},
  {"from": ["origin"], "description": "Analyze authentication mechanisms and session management", "priority": 1},
  {"from": ["origin"], "description": "Test for common misconfigurations (CORS, security headers, default credentials)", "priority": 1},
  {"from": ["goal"], "description": "Identify high-value targets based on goal requirements (admin panels, sensitive data endpoints)", "priority": 2}
]}}
```

...（后续保持原 decide.md 内容）
```

#### 任务 3.2：优化 Execute 提示词（深度探索）

**目标**：确保 Execute 不浅尝辄止，彻底探索每个 Step

```markdown
# prompts/default/execute.md（强化版）

# Task
Execute the assigned Step and discover new facts.

## Core Principle: Thorough Exploration

**DO NOT** return early just because you hit one obstacle. A Step is NOT complete until you have:
1. ✅ Tried all reasonable approaches
2. ✅ Followed up on any interesting leads
3. ✅ Documented clear results (success or definitive failure)

## Examples of Thorough vs Shallow Exploration

### ❌ SHALLOW (Don't do this)
Step: "Test login page for SQL injection"
Execute: Tried `' OR 1=1--`, got error, gave up.

### ✅ THOROUGH (Do this)
Step: "Test login page for SQL injection"
Execute:
- Tried various SQL injection payloads (union, boolean, time-based)
- Tested both username and password fields
- Analyzed error messages for information leakage
- Tried bypasses for any WAF detected
- If vulnerable: extracted database schema, dumped credentials
- If not vulnerable: confirmed with multiple payload types
- Result: Clear finding or confirmed "not vulnerable after extensive testing"

## Finding Reporting

When you discover a security issue, ALWAYS include it as a `finding`:

```json
{"accepted": true, "data": {
  "description": "SQL injection confirmed in login username field. Extracted 5 user credentials including admin account.",
  "finding": {
    "title": "SQL Injection in Login Form",
    "severity": "high",
    "description": "Union-based SQL injection in username parameter allows full database extraction. Payload: admin' UNION SELECT 1,2,3,4,5--"
  }
}}
```

### Severity Guidelines
- **critical**: RCE, Authentication bypass, Full system compromise
- **high**: SQL injection, XSS (stored), Sensitive data leak, Privilege escalation
- **medium**: CSRF, XSS (reflected), IDOR, Information disclosure
- **low**: Missing security headers, Verbose errors
- **info**: Observations without direct impact

## When to Stop

Only return when ONE of these is true:
1. ✅ You discovered a finding (vulnerability/flag/evidence)
2. ✅ You exhausted all reasonable attack vectors for this Step
3. ✅ You hit a hard blocker (requires different approach, should create new Step)
4. ✅ You achieved the Step's stated goal

If you're unsure whether you're done, you're probably NOT done. Keep exploring.

...（后续保持原 execute.md 内容）
```

**产出**：
- ✅ Bootstrap 简化为初始化流程
- ✅ Decide 提示词针对 bootstrap 场景优化
- ✅ Execute 提示词强化深度探索

---

### Week 4: Sub Goal 智能调度 + 测试验证 🟡

#### 任务 4.1：智能子目标调度

**目标**：实现文档提到的"阶段性平稳推进"

```python
# cairn/src/cairn/dispatcher/scheduler/goal_aware_scheduler.py

from dataclasses import dataclass
from typing import Literal

@dataclass
class GoalPhase:
    """目标阶段"""
    goal_id: str
    description: str
    priority: int
    status: Literal["active", "completed"]
    related_steps: list[str]  # 关联的 Step IDs
    progress: float  # 0.0 - 1.0


class GoalAwareScheduler:
    """目标感知的调度器
    
    核心思想：
    1. 将 Steps 按 goal_id 分组
    2. 优先完成高优先级 Sub Goal 相关的 Steps
    3. 避免在多个目标间频繁切换（降低上下文切换成本）
    4. 检测目标停滞（某个 Goal 的所有 Steps 都失败）
    """
    
    def __init__(self, client: CairnClient):
        self.client = client
    
    def select_next_step(self, project: ProjectDetail) -> Step | None:
        """选择下一个要执行的 Step
        
        策略：
        1. 按 Goal 分组 Open Steps
        2. 选择最高优先级且有 Open Steps 的 Goal
        3. 在该 Goal 内按 Step priority 选择
        4. 如果某个 Goal 长时间无进展，降低其优先级或创建新 Sub Goal
        """
        open_steps = [s for s in project.steps if s.to is None and not s.abandoned]
        
        if not open_steps:
            return None
        
        # 按 Goal 分组
        steps_by_goal = self._group_by_goal(open_steps, project.goals)
        
        # 选择当前焦点 Goal
        focus_goal = self._select_focus_goal(steps_by_goal, project.goals)
        
        if not focus_goal:
            # 没有 goal_id 的 Steps，按优先级选择
            return max(
                [s for s in open_steps if not s.goal_id],
                key=lambda s: s.priority,
                default=None
            ) or open_steps[0]
        
        # 在焦点 Goal 内选择最高优先级 Step
        goal_steps = steps_by_goal[focus_goal]
        return max(goal_steps, key=lambda s: s.priority)
    
    def _group_by_goal(self, steps: list[Step], goals: list[Goal]) -> dict[str, list[Step]]:
        """按 Goal 分组 Steps"""
        grouped = {}
        for step in steps:
            gid = step.goal_id or "none"
            if gid not in grouped:
                grouped[gid] = []
            grouped[gid].append(step)
        return grouped
    
    def _select_focus_goal(self, steps_by_goal: dict, goals: list[Goal]) -> str | None:
        """选择当前焦点 Goal
        
        优先级规则：
        1. active 状态的 Goal
        2. priority 最高
        3. 有未完成 Steps
        """
        active_goals = [g for g in goals if g.status == "active" and g.id in steps_by_goal]
        if not active_goals:
            return None
        
        return max(active_goals, key=lambda g: g.priority).id
    
    def should_trigger_decide(self, project: ProjectDetail) -> tuple[bool, str]:
        """判断是否应该触发 Decide
        
        触发条件：
        1. 有新 Fact 产生（fact_count 增加）
        2. 所有 Open Steps 都已完成
        3. 某个 Sub Goal 完成，需要规划下一阶段
        4. 检测到探索停滞（长时间无新 Fact）
        
        Returns:
            (should_trigger, trigger_reason)
        """
        open_steps = [s for s in project.steps if s.to is None and not s.abandoned]
        
        # 没有 Open Steps，必须 Decide
        if not open_steps:
            return True, "no_open_steps"
        
        # 检查是否有最近完成的 Sub Goal
        recent_completed = self._check_recent_goal_completion(project)
        if recent_completed:
            return True, f"goal_completed:{recent_completed}"
        
        # 检查是否停滞（所有 Open Steps 都在执行中，但很久没有新 Fact）
        all_working = all(s.worker is not None for s in open_steps)
        if all_working and self._is_stagnant(project):
            return True, "stagnation_detected"
        
        return False, ""
    
    def _check_recent_goal_completion(self, project: ProjectDetail) -> str | None:
        """检查是否有最近完成的 Goal"""
        for goal in project.goals:
            if goal.status == "completed" and goal.completed_at:
                # 检查是否在最近 5 分钟内完成
                # （这里需要时间解析逻辑）
                return goal.id
        return None
    
    def _is_stagnant(self, project: ProjectDetail) -> bool:
        """检测探索是否停滞"""
        # 简化逻辑：如果最近 30 分钟没有新 Fact，认为停滞
        # 实际需要解析 created_at 时间戳
        return False
```

#### 任务 4.2：在调度循环中使用新调度器

```python
# cairn/src/cairn/dispatcher/scheduler/loop.py（修改）

from cairn.dispatcher.scheduler.goal_aware_scheduler import GoalAwareScheduler

def run_scheduler_loop(config, client, container_manager):
    """主调度循环"""
    scheduler = GoalAwareScheduler(client)
    
    while True:
        projects = client.list_active_projects()
        
        for project in projects:
            detail = client.get_project_detail(project.id)
            
            # 1. 检查是否需要 Decide
            should_decide, reason = scheduler.should_trigger_decide(detail)
            if should_decide:
                trigger_decide(config, client, container_manager, 
                              detail, reason)
                continue
            
            # 2. 选择下一个 Step 执行
            next_step = scheduler.select_next_step(detail)
            if next_step and not next_step.worker:
                trigger_execute(config, client, container_manager,
                               detail, next_step)
        
        time.sleep(config.scheduler.poll_interval)
```

#### 任务 4.3：集成测试和验证

创建完整的端到端测试场景：

```python
# tests/integration/test_cairn_y_full_flow.py

def test_web_app_pentest_flow():
    """测试完整的 Web 应用渗透流程"""
    
    # 1. 创建项目
    project_id = create_project_with_bootstrap(
        title="Test Web App Pentest",
        origin="http://vulnerable-app.local",
        goal="Discover all security vulnerabilities",
        hints=[
            {"content": "Focus on authentication and authorization", "creator": "tester"},
            {"content": "Check for common web vulnerabilities", "creator": "tester"},
        ]
    )
    
    # 2. 等待第一次 Decide 完成
    wait_for_decide_complete(project_id)
    
    # 3. 检查是否生成了初始 Steps
    project = get_project_detail(project_id)
    assert len(project.steps) >= 3, "Should generate at least 3 initial steps"
    
    # 4. 运行调度循环，执行 Steps
    run_scheduler_for_duration(seconds=600)
    
    # 5. 验证结果
    project = get_project_detail(project_id)
    assert len(project.findings) > 0, "Should discover at least one vulnerability"
    
    # 6. 检查 Finding 质量
    findings = project.findings
    for finding in findings:
        assert finding.title, "Finding must have title"
        assert finding.severity in ["critical", "high", "medium", "low", "info"]
        assert len(finding.description) > 50, "Finding description should be detailed"


def test_ctf_flag_hunting():
    """测试 CTF Flag 搜索场景"""
    project_id = create_project_with_bootstrap(
        title="CTF Challenge",
        origin="http://ctf-challenge.local:8080",
        goal="Find all flags in the format flag{...}",
    )
    
    run_scheduler_for_duration(seconds=300)
    
    project = get_project_detail(project_id)
    # 检查是否找到 Flag（应该在 Facts 中）
    flag_facts = [f for f in project.facts if "flag{" in f.description]
    assert len(flag_facts) > 0, "Should find at least one flag"
```

**产出**：
- ✅ 目标感知调度器
- ✅ 智能 Step 选择
- ✅ 停滞检测
- ✅ 集成测试覆盖

---

## 三、验收标准

### 代码质量标准

```bash
# 1. 架构一致性
grep -r "Intent\|Reason" cairn/src/ | grep -v "# legacy\|# backward" | wc -l
# 结果应为 0

# 2. 测试覆盖
pytest --cov=cairn --cov-report=term
# 核心模块覆盖率 > 70%

# 3. 类型检查
mypy cairn/src/
# 无错误

# 4. 代码风格
ruff check cairn/src/
# 无严重问题
```

### 功能验收标准

**场景 1：Web 应用渗透测试**
- ✅ Bootstrap 后自动生成 3-5 个合理的初始探索 Steps
- ✅ 至少发现 1 个 SQL 注入或 XSS 漏洞（在已知漏洞的靶场上）
- ✅ Finding 包含标题、严重级别、详细描述和利用证明
- ✅ 探索深度：不只是扫描，要有深入的漏洞验证

**场景 2：CTF Flag 搜索**
- ✅ 能够找到隐藏的 Flag
- ✅ 会尝试多种探索路径（目录遍历、源码分析、弱密码等）
- ✅ 不会在遇到第一个障碍时就放弃

**场景 3：复杂目标（需要阶段推进）**
- ✅ 能够创建合理的 Sub Goal 分解任务
- ✅ 优先完成当前焦点 Goal 的 Steps，不频繁切换
- ✅ 某个 Goal 完成后触发 Decide 规划下一阶段

### 性能验收标准

- Decide 响应时间 < 60s（不考虑模型推理时间）
- Execute 不超时（合理设置 timeout，如 10 分钟）
- 调度器能处理 10+ 并发项目

---

## 四、实施注意事项

### 4.1 渐进式重构

```bash
# 不要一次性删除所有旧代码
# 保留旧实现，逐步切换

# Step 1: 新代码共存
cairn/src/cairn/dispatcher/tasks/
  ├── decide.py          # 旧实现（标记 deprecated）
  ├── decide_v2.py       # 新实现
  ├── execute.py         # 旧实现（标记 deprecated）
  └── execute_v2.py      # 新实现

# Step 2: 配置开关
# config.py
use_v2_agent_loop: bool = True

# Step 3: 测试通过后删除旧代码
```

### 4.2 提示词版本管理

```bash
# 保留提示词的版本历史
cairn/src/cairn/dispatcher/prompts/
  ├── default/           # 当前生产版本
  │   ├── decide.md
  │   └── execute.md
  ├── v2_optimization/   # 新优化版本（实验中）
  │   ├── decide.md
  │   └── execute.md
  └── legacy/            # 旧版本归档
      ├── reason.md
      └── explore.md

# 可以通过配置切换提示词版本
prompt_version: "default"  # 或 "v2_optimization"
```

### 4.3 测试先行

每完成一个任务，立即编写测试：

```python
# 示例：测试 Agent Loop
def test_agent_loop_decide():
    """测试 Decide Agent Loop"""
    context = AgentContext(
        project_id="test_001",
        graph_snapshot=SAMPLE_GRAPH_YAML,
        available_tools=[CreateStepTool, CompleteGoalTool],
        prompt_template="decide.md",
        task_specific_vars={"open_steps": "[]", "fact_ids": "f001,f002"},
    )
    
    loop = AgentLoop(mock_worker_config, mock_driver)
    result = loop.run(context)
    
    assert result.success
    assert result.kind in ["complete", "steps", "noop"]
```

### 4.4 日志和可观测性

```python
# 关键位置添加详细日志

LOG.info(
    "Decide 生成步骤 project=%s steps=%s",
    project_id,
    [{"from": s["from"], "desc": s["description"][:50]} for s in steps]
)

LOG.info(
    "Execute 发现漏洞 project=%s step=%s finding=%s",
    project_id, step_id, finding["title"]
)

LOG.warning(
    "Goal 长时间无进展 project=%s goal=%s open_steps=%s duration_minutes=%s",
    project_id, goal_id, len(open_steps), stagnation_duration
)
```

---

## 五、风险和缓解

| 风险 | 影响 | 缓解措施 |
|-----|------|---------|
| 重构破坏现有功能 | 高 | 渐进式重构，保留旧代码，充分测试 |
| 新 Agent Loop 性能不如旧实现 | 中 | 基准测试，性能监控 |
| 提示词优化效果不明显 | 中 | A/B 测试，保留多版本 |
| Sub Goal 调度过于复杂 | 低 | 先实现简化版，逐步优化 |
| Bootstrap 简化导致初始探索不足 | 中 | 强化 Decide 的 bootstrap 提示词 |

---

## 六、成功指标

### 主观评估（需要人工验证）

在 3 个不同的靶场上测试：
1. **WebGoat** (OWASP 靶场)
2. **DVWA** (Damn Vulnerable Web App)
3. **HackTheBox** 某个简单靶机

评估标准：
- ✅ 能够自动发现至少 50% 的已知漏洞
- ✅ Finding 描述清晰，包含利用方法
- ✅ 探索路径合理，没有明显的遗漏
- ✅ 不会在浅层探索后就停止

### 客观指标

- Decide 生成的 Steps 有效率 > 70%（不是无效或重复的 Steps）
- Execute 完成的 Steps 中，至少 30% 产生了有价值的 Fact
- Finding 覆盖 OWASP Top 10 中至少 5 类漏洞
- 系统稳定性：连续运行 8 小时无崩溃

---

## 七、时间表总结

| Week | 任务 | 产出 | 验收 |
|------|-----|------|------|
| 1 | 清理 Intent/Reason 概念 | 代码统一使用 FGS 术语 | 搜索旧概念数量 = 0 |
| 2 | 实现 Agent Loop + Tool 系统 | 统一抽象层 | Decide/Execute 使用新实现 |
| 3 | 重构 Bootstrap + 优化提示词 | 简化初始化流程 | Bootstrap 生成合理 Steps |
| 4 | Sub Goal 调度 + 测试 | 智能调度器 + 集成测试 | 端到端场景通过 |

**总计**：3-4 周完成核心重构

---

## 八、下一步行动

### 立即开始（今天）

```bash
# 1. 创建重构分支
git checkout -b refactor/cairn-to-cairn-y

# 2. 备份当前数据库
cp ~/.local/share/cairn/cairn.db ~/.local/share/cairn/cairn.db.backup

# 3. 创建任务清单
# 在项目根目录创建 REFACTOR_CHECKLIST.md
```

### 第一周任务清单

```markdown
# Week 1 Checklist

## Day 1-2: 清理类型和别名
- [ ] 删除 models.py 中所有 Intent/Reason 别名
- [ ] 删除 db.py 中的迁移函数
- [ ] 更新所有路由使用 FGS 术语
- [ ] 运行测试，修复破损的测试

## Day 3-4: 清理提示词
- [ ] 移动 prompts/mock/ 到 prompts/legacy/
- [ ] 确认只使用 prompts/default/
- [ ] 更新所有提示词中的术语
- [ ] 提交 Week 1 代码

## Day 5: 测试和文档
- [ ] 运行完整测试套件
- [ ] 更新 README.md 中的架构说明
- [ ] 记录遇到的问题和解决方案
```

### 需要确认的事项

请确认以下问题，我可以据此微调方案：

1. **当前 Worker 类型**：你用的是 Claude？GPT？本地模型？
2. **测试靶场**：你有现成的测试环境吗？
3. **数据迁移**：现有的数据库是否需要保留？
4. **时间安排**：3-4 周时间是否可行？
5. **人力投入**：是你独自开发还是有团队？

---

**准备好开始了吗？让我知道，我可以开始生成具体的代码模板和第一周的详细任务！**
