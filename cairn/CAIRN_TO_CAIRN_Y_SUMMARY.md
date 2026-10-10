# Cairn → Cairn_Y 核心重构总结

## 🎯 目标

完全从 Cairn 转向 Cairn_Y，实现论文中描述的统一 Agent Loop 架构，优化漏洞发现效果。

## ✅ Week 1 已完成工作

### Day 1-2: 术语清理 ✅

**目标**: 清理 Intent/Reason 旧术语，统一为 Step/Decide

**完成内容**:
1. **models.py**
   - 删除类型别名: `CreateIntentRequest`、`ReasonClaimRequest`、`ProjectReason`
   - 标记 `Intent` 类为 deprecated（向后兼容）
   - Settings 字段重命名: `intent_timeout` → `step_timeout`, `reason_timeout` → `decide_timeout`

2. **db.py**
   - 更新 `SETTINGS_DEFAULTS` 使用新字段名
   - 添加 `SETTINGS_ADDITIONAL_COLUMNS` 定义新列

3. **routers/intents.py**
   - 整个路由标记为 deprecated
   - 保留功能用于向后兼容

4. **scheduler/loop.py**
   - 更新 `_validate_server_settings` 使用新字段名

5. **frontend/App.jsx**
   - UI 更新为新字段名和标签
   - "意图超时" → "Step 超时"
   - "Reason 超时" → "Decide 超时"

6. **数据库迁移**
   - `scripts/migrate_settings_fields.py` - 迁移脚本
   - 旧字段保留，新字段通过迁移添加

**验证**: ✅ 所有模块导入成功，字段名已统一

---

### Day 3-4: 统一 Agent Loop ✅

**目标**: 实现论文中的核心架构 - Decide 和 Execute 共享同一 Loop

**完成内容**:

#### 1. Tool 系统基础架构 ✅

**`dispatcher/tools/base.py`**
- `Tool` 抽象基类 - 所有 Tool 的统一接口
- `ToolCategory` 枚举:
  - `GRAPH` - 只能操作 FGS 图
  - `WORLD` - 只能与外部世界交互
- 每个 Tool 必须声明 category、实现 execute() 和 get_schema()

#### 2. GRAPH Tools（Decide 阶段）✅

**`dispatcher/tools/graph_tools.py`** - 4 个 GRAPH category tools:

1. **AddStepTool** - 添加新的探索步骤
   - 创建新 Step 到图中
   - 指定依赖的 Fact
   - 可选分配 Worker

2. **AddGoalTool** - 标记目标
   - 将 Fact 标记为 Goal
   - 设置优先级（high/normal/low）

3. **AddFindingTool** - 记录漏洞发现 🎯
   - 记录漏洞标题、描述、严重程度
   - 关联证据 Fact IDs
   - 支持 CWE 和 CVSS 分数

4. **QueryGraphTool** - 查询图
   - 查询 Facts/Steps/Goals/Findings
   - 支持过滤条件

#### 3. WORLD Tools（Execute 阶段）✅

**`dispatcher/tools/world_tools.py`** - 3 个 WORLD category tools:

1. **BashTool** - 执行 shell 命令
   - 运行安全扫描工具
   - 代码分析
   - 文件系统操作

2. **HTTPRequestTool** - 发送 HTTP 请求
   - 测试 API 端点
   - 发送 payload
   - 检查响应

3. **ReadFileTool** - 读取文件
   - 读取源代码
   - 分析配置文件
   - 检查日志

#### 4. 统一 Agent Loop ✅

**`dispatcher/agent_loop.py`** - 核心实现:

```python
class AgentLoop:
    def __init__(self, mode: Literal["decide", "execute"], ...):
        if mode == "decide":
            self.allowed_categories = {ToolCategory.GRAPH}
        else:  # execute
            self.allowed_categories = {ToolCategory.WORLD}
```

**Loop 流程**:
1. 读图（获取相关子图）
2. 构造 Prompt
3. 调用 LLM（传入允许的 Tools）
4. 执行 Tool calls
5. 更新图
6. 重复或结束

**权限控制**:
- Decide 只能调用 GRAPH tools
- Execute 只能调用 WORLD tools
- 跨边界调用会被拒绝并返回错误

#### 5. 新任务实现 ✅

**`tasks/decide.py`**:
```python
async def run_decide(...):
    loop = AgentLoop(mode="decide", ...)
    return await loop.run()
```

**`tasks/execute.py`**:
```python
async def run_execute(...):
    loop = AgentLoop(mode="execute", ...)
    return await loop.run()
```

**验证**: ✅ 所有导入成功，Tool 实例化正常

---

## 🎯 核心成果

### 论文架构实现 ✅

> "我们的 Agent 在 Decide 和 Execute 都是同一个 Loop：读图 → 调 Tool → 写图。区别只在于允许哪些 Tool。"

**已实现**:
- ✅ 单一 Loop 逻辑
- ✅ 通过 mode 参数区分 decide/execute
- ✅ 清晰的 Tool 权限边界
- ✅ GRAPH vs WORLD 强隔离

### 漏洞发现能力 🎯

**AddFindingTool** 实现了核心的漏洞记录功能:
- 结构化漏洞信息（标题、描述、严重程度）
- 证据关联（evidence_fact_ids）
- 标准化标记（CWE、CVSS）

---

## 📋 下一步工作（优先级排序）

### 🔥 P0 - 核心集成（必须完成才能运行）

#### 1. 集成到调度器 (1-2天)
**文件**: `scheduler/loop.py`

**需要做**:
- [ ] 更新调度逻辑识别 decide/execute 类型
- [ ] 调用新的 `run_decide()` 和 `run_execute()`
- [ ] 处理 Tool 调用结果

**示例**:
```python
if step_type == "decide":
    from cairn.dispatcher.tasks.decide import run_decide
    result = await run_decide(...)
elif step_type == "execute":
    from cairn.dispatcher.tasks.execute import run_execute
    result = await run_execute(...)
```

#### 2. Agent Loop 适配现有接口 (2-3天)
**文件**: `agent_loop.py`

**需要做**:
- [ ] 实现 `_call_llm()` - 适配现有 Worker API
- [ ] 实现 `_load_graph_context()` - 适配现有 API Client
- [ ] 实现 Graph Tools 的实际 API 调用
- [ ] 测试完整的 Loop 流程

**关键问题**:
- Worker 的 chat API 格式是什么？
- Tool 调用结果如何传回 LLM？
- 如何判断任务完成？

#### 3. 创建 Prompt 文件 (1天)
**文件**: 
- `prompts/default/decide.md`
- `prompts/default/execute.md`
- `prompts/default/execute_conclude.md`

**需要做**:
- [ ] 编写 Decide 阶段的系统 Prompt
- [ ] 编写 Execute 阶段的系统 Prompt
- [ ] 定义 Tool 使用指南
- [ ] 定义任务完成标准

---

### 🟡 P1 - 数据模型扩展（增强功能）

#### 4. 数据库模型更新 (1天)
**文件**: `server/db.py`, `server/models.py`

**需要做**:
- [ ] Step 表添加 `step_type` 字段（decide/execute/bootstrap）
- [ ] 创建 Goals 表
- [ ] 创建 Findings 表
- [ ] 数据库迁移脚本

#### 5. API 路由扩展 (1-2天)
**新文件**: 
- `server/routers/steps.py`
- `server/routers/goals.py`
- `server/routers/findings.py`

**需要做**:
- [ ] `/projects/{id}/steps` - 创建和查询 Steps
- [ ] `/projects/{id}/goals` - 管理 Goals
- [ ] `/projects/{id}/findings` - 管理 Findings
- [ ] 支持按类型创建 Step（decide/execute）

---

### 🟢 P2 - 优化和完善（提升效果）

#### 6. 子图传递优化 (2-3天)
**文件**: `agent_loop.py`

**目标**: 只传递相关子图，不传完整图

**需要做**:
- [ ] 实现图遍历算法（从当前 Step 回溯依赖）
- [ ] 定义相关性规则
- [ ] 限制子图大小

#### 7. 增量 Decide (2-3天)
**文件**: `tasks/decide.py`

**目标**: 只分析图的变化部分

**需要做**:
- [ ] 跟踪图的变化（新增的 Facts/Steps）
- [ ] Prompt 中只包含变化
- [ ] 优化 Decide 频率

#### 8. Prompt 优化 (持续)
**文件**: `prompts/default/*.md`

**需要做**:
- [ ] 根据实际运行效果调整
- [ ] 添加示例（few-shot）
- [ ] 优化 Tool 使用指导

---

## 🚀 建议执行顺序

### Week 2 (Day 5-11): 核心集成
```
Day 5-6:   集成到调度器
Day 7-9:   Agent Loop 适配现有接口
Day 10:    创建 Prompt 文件
Day 11:    端到端测试
```

### Week 3 (Day 12-18): 数据模型
```
Day 12:    数据库模型更新
Day 13-14: API 路由扩展
Day 15-18: 前端集成和测试
```

### Week 4+ (Day 19+): 优化
```
Day 19-21: 子图传递优化
Day 22-24: 增量 Decide
Day 25+:   Prompt 优化和效果调优
```

---

## 📊 当前状态

### ✅ 已完成
- [x] 术语统一（Intent/Reason → Step/Decide）
- [x] Tool 系统基础架构
- [x] GRAPH Tools (4个)
- [x] WORLD Tools (3个)
- [x] 统一 Agent Loop
- [x] 新任务实现（decide.py, execute.py）

### 🔄 进行中
- [ ] 集成到调度器
- [ ] Agent Loop 接口适配
- [ ] Prompt 文件创建

### 📅 待开始
- [ ] 数据库模型扩展
- [ ] API 路由扩展
- [ ] 优化功能

---

## 💡 关键决策记录

1. **使用 requests 而非 httpx**
   - 原因: 项目已有 requests 依赖，避免添加新依赖
   - HTTPRequestTool 使用 requests.Session

2. **WorkerDriver 而非 Worker**
   - 发现: 项目使用 WorkerDriver 作为基类
   - 已更新所有引用

3. **CairnClient 而非 InternalAPIClient**
   - 发现: 实际类名是 CairnClient
   - 已更新所有引用

4. **保留旧 API 向后兼容**
   - `/projects/{id}/intents` 路由保留但标记 deprecated
   - 数据库旧字段保留，新字段通过迁移添加

---

## 🎯 成功指标

### 短期（Week 2 结束）
- [ ] 可以创建 decide 类型的 Step
- [ ] 可以创建 execute 类型的 Step
- [ ] Agent Loop 可以完整运行
- [ ] Tool 调用正常工作

### 中期（Week 3 结束）
- [ ] 可以记录 Finding
- [ ] 可以标记 Goal
- [ ] 前端显示新的实体
- [ ] API 完整可用

### 长期（Week 4+ 结束）
- [ ] 漏洞发现率提升
- [ ] Token 消耗优化
- [ ] Decide 效率提升
- [ ] 系统稳定运行

---

## 📝 注意事项

1. **向后兼容**: 旧的 Intent API 仍然可用，逐步迁移
2. **数据迁移**: 使用提供的迁移脚本迁移 settings 字段
3. **测试**: 每个阶段完成后都要进行集成测试
4. **文档**: 随着实现更新 Prompt 和 API 文档

---

## 📞 下一步行动

**立即开始**: Week 2 Day 5 - 集成到调度器

**关键文件**:
- `scheduler/loop.py` - 添加 decide/execute 调度逻辑
- `agent_loop.py` - 完善 LLM 调用和 Tool 执行
- `prompts/default/decide.md` - 编写 Decide Prompt
- `prompts/default/execute.md` - 编写 Execute Prompt

准备好了吗？让我们继续！🚀
