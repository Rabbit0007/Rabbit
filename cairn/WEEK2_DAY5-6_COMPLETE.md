# Week 2 Day 5-6: 集成到调度器 - 完成

## ✅ 已完成

### 1. 任务函数重命名和签名匹配

**目标**: 让调度器能够调用我们的新任务

**完成内容**:

#### `tasks/decide.py` - Decide 任务
- ✅ 重命名为 `run_decide_task()` (调度器期望的名字)
- ✅ 匹配调度器签名:
  ```python
  def run_decide_task(
      config: DispatchConfig,
      client: CairnClient,
      container_manager: ContainerManager,
      project: ProjectDetail,
      export_yaml: str,
      worker: WorkerConfig,
      cancellation: TaskCancellation,
  ) -> str:
  ```
- ✅ 保留原有工程化逻辑:
  - 容器管理 (`container_manager.ensure_running()`)
  - 心跳机制 (`HeartbeatLease.for_reason()`)
  - 健康检查 (`run_healthcheck()`)
  - 取消处理 (`cancellation`)
- ✅ 预留 AgentLoop 集成点 (TODO 标记)

#### `tasks/execute.py` - Execute 任务
- ✅ 重命名为 `run_execute_task()` (调度器期望的名字)
- ✅ 匹配调度器签名:
  ```python
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
  ```
- ✅ 保留原有工程化逻辑:
  - 容器管理
  - 心跳机制 (`HeartbeatLease.for_intent()`)
  - 健康检查
  - 取消处理
- ✅ 预留 AgentLoop 集成点 (TODO 标记)

### 2. 调度器集成验证

**调度器已经在使用新任务**:
- Line 18: `from cairn.dispatcher.tasks.decide import run_decide_task` ✅
- Line 19: `from cairn.dispatcher.tasks.execute import run_execute_task` ✅
- Line 320: 调用 `run_decide_task()` - Reason 阶段 ✅
- Line 387: 调用 `run_decide_task()` - Bootstrap 阶段 ✅
- Line 445: 调用 `run_execute_task()` - Explore 阶段 ✅

**导入测试**: ✅ 成功
```
✅ All imports successful!
✅ DispatcherLoop imports: run_decide_task, run_execute_task
✅ Tasks have correct signatures matching scheduler expectations
```

## 🎯 当前状态

### ✅ 已集成
1. **调度器识别** - 调度器已经在导入和调用新任务
2. **签名匹配** - 新任务的签名完全匹配调度器期望
3. **工程化保留** - 容器、心跳、健康检查、取消机制全部保留
4. **向后兼容** - 旧的 `reason.py` 和 `explore.py` 保留，可随时回退

### 🔄 下一步
**AgentLoop 集成** - 在 TODO 标记处集成 AgentLoop:
1. 实现 `_call_llm()` - 调用 Worker 的 LLM API
2. 实现 Tool 执行逻辑
3. 实现结果处理和状态更新

## 📊 架构决策

### 为什么保留工程化逻辑？

**原因**:
1. **容器管理**: 隔离的执行环境，安全必需
2. **心跳机制**: 任务超时检测，避免僵尸任务
3. **健康检查**: Worker 可用性验证
4. **取消处理**: 项目停止时优雅退出

这些不是 AgentLoop 的职责，而是调度层的职责。

### 架构分层
```
Scheduler (loop.py)
    ↓ 调度和资源管理
Task Wrapper (decide.py, execute.py)
    ↓ 容器、心跳、健康检查
AgentLoop (agent_loop.py)
    ↓ Tool 权限控制和 LLM 调用
Tools (graph_tools.py, world_tools.py)
    ↓ 实际操作
```

## 📝 代码差异

### 旧方式 (reason.py/explore.py)
```python
# 直接调用 Worker 的 LLM
prompt = render_prompt(...)
command = driver.build_execute(worker, prompt, session)
result = run_worker_process(...)
```

### 新方式 (decide.py/execute.py)
```python
# 通过 AgentLoop 调用，带 Tool 权限控制
# TODO: 在这里集成 AgentLoop
loop = AgentLoop(
    mode="decide",  # 或 "execute"
    project_id=project.project.id,
    worker=worker,
    client=client,
)
result = await loop.run()
```

## 🚀 下一步行动

**Day 7-9: AgentLoop 接口适配**

需要实现:
1. `AgentLoop._call_llm()` - 适配 Worker Driver API
2. `AgentLoop._execute_tools()` - 执行 Tool calls
3. `AgentLoop._update_graph()` - 更新图状态
4. Graph Tools 的实际 API 调用

**目标**: 完整的 Loop 流程可以运行
