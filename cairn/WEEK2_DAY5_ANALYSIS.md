# Week 2 Day 5-6: 集成到调度器

## 📋 分析现状

从 `scheduler/loop.py` 代码可以看到：

### 现有调度逻辑
1. **已经导入了新任务**：
   - Line 18: `from cairn.dispatcher.tasks.decide import run_decide_task`
   - Line 19: `from cairn.dispatcher.tasks.execute import run_execute_task`

2. **已经在使用新任务**：
   - Line 320: `run_decide_task(...)` - Decide 任务调度
   - Line 387: `run_decide_task(...)` - Bootstrap 任务调度
   - Line 445: `run_execute_task(...)` - Execute 任务调度

3. **任务类型识别**：
   - `task_type == "decide"` - Decide/Reason 阶段
   - `task_type == "execute"` - Execute/Explore 阶段

### ⚠️ 问题发现

**调度器已经在调用 `run_decide_task` 和 `run_execute_task`，但这两个函数还不存在！**

我们在 Week 1 创建的是：
- `tasks/decide.py` 导出 `run_decide()`
- `tasks/execute.py` 导出 `run_execute()`

但调度器期望的是：
- `run_decide_task()`
- `run_execute_task()`

## 🎯 需要做的事

### 选项 1: 重命名我们的函数（推荐）
```python
# tasks/decide.py
async def run_decide_task(...):  # 改名
    ...

# tasks/execute.py  
async def run_execute_task(...):  # 改名
    ...
```

### 选项 2: 在现有任务中集成 AgentLoop

更好的方案是：**不创建新文件，而是修改现有的 `reason.py` 和 `explore.py`，集成我们的 AgentLoop！**

理由：
1. 调度器已经在用这些函数
2. 保持现有的容器管理、心跳、健康检查逻辑
3. 只需要替换核心的 LLM 调用部分

## 📝 实施计划

我建议采用 **选项 2**：

1. 保留 `reason.py` 和 `explore.py` 的外层逻辑（容器、心跳、健康检查）
2. 在它们内部调用我们的 `AgentLoop`
3. `tasks/decide.py` 和 `tasks/execute.py` 作为辅助模块

这样可以：
- ✅ 复用现有的工程化代码
- ✅ 最小化改动
- ✅ 保持向后兼容
