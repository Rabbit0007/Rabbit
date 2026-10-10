# Cairn → Cairn_Y 重构进度

## ✅ 已完成 (Week 1)

### Day 1-2: 术语清理
- ✅ models.py: 删除类型别名，统一为 Step/Decide
- ✅ db.py: 字段重命名 (intent_timeout → step_timeout)
- ✅ frontend: UI 更新
- ✅ 数据库迁移脚本

### Day 3-4: 统一 Agent Loop
- ✅ Tool 系统基础架构 (base.py, ToolCategory)
- ✅ GRAPH Tools: AddStepTool, AddGoalTool, AddFindingTool, QueryGraphTool
- ✅ WORLD Tools: BashTool, HTTPRequestTool, ReadFileTool
- ✅ AgentLoop 核心实现
- ✅ decide.py 和 execute.py 任务

**提交记录**:
- `d192370` Week1 Day1: 清理 Intent/Reason 术语，统一为 Step/Decide
- `7481259` Week1 Day3-4: 实现统一 Agent Loop 和 Tool 系统

---

## 🔄 下一步 (Week 2)

### P0 - 核心集成（必须完成）

#### 1. 集成到调度器
**文件**: `scheduler/loop.py`
**预计**: 1-2天

#### 2. Agent Loop 适配
**文件**: `agent_loop.py`
**预计**: 2-3天

#### 3. Prompt 文件
**文件**: `prompts/default/decide.md`, `execute.md`
**预计**: 1天

---

查看完整计划: `CAIRN_TO_CAIRN_Y_SUMMARY.md`
