# Cairn → Cairn_Y 重构清单

**开始日期**：2026-09-02  
**预计完成**：2026-09-30 (4周)  
**分支**：refactor/cairn-to-cairn-y

---

## Week 1: 架构清理与概念统一 (2026-09-02 ~ 09-08)

### Day 1-2: 清理类型别名和数据模型 ✅ 进行中

**目标**：移除所有 Intent/Reason 概念，统一为 Step/Decide

- [ ] **models.py 清理**
  - [ ] 删除 `CreateIntentRequest = CreateStepRequest`
  - [ ] 删除 `ReasonCheckpoint = DecideCheckpoint`
  - [ ] 删除 `ProjectReason = ProjectDecide` 重复定义
  - [ ] 检查 Intent 类是否还在使用，如果不用则删除
  - [ ] 确保所有请求/响应模型使用 Step 术语

- [ ] **contracts.py 清理**
  - [ ] 删除 `validate_reason_payload` 函数（如果存在）
  - [ ] 确保只有 `validate_decide_payload`
  - [ ] 删除 bootstrap 相关的旧逻辑（后续会重构）

- [ ] **db.py 清理**
  - [ ] 标记 `_migrate_intents_to_steps` 为 deprecated（注释说明）
  - [ ] 确认 intents 表已完全迁移到 steps 表
  - [ ] 检查是否还有 reason_* 列的引用

- [ ] **运行测试**
  ```bash
  cd cairn && pytest tests/ -v
  ```

### Day 3: 清理 API 路由和服务

- [ ] **routers/ 目录检查**
  - [ ] 确认没有 `/intents` 端点
  - [ ] 确认所有端点使用 `/steps`
  - [ ] 检查 `routers/steps.py` 中的函数命名

- [ ] **services.py 清理**
  - [ ] 搜索所有 `intent` 变量名，改为 `step`
  - [ ] 搜索所有 `reason` 变量名，改为 `decide`
  - [ ] 更新日志消息中的术语

- [ ] **运行测试**
  ```bash
  cd cairn && pytest tests/ -v --tb=short
  ```

### Day 4: 清理提示词和配置

- [ ] **提示词目录重组**
  - [ ] 移动 `prompts/mock/` → `prompts/legacy/`
  - [ ] 在 legacy 目录添加 README 说明这是旧架构
  - [ ] 确认 `prompts/default/` 只包含 FGS 术语提示词
  - [ ] 检查提示词中是否还有 Intent/Reason 术语

- [ ] **配置文件检查**
  - [ ] `config.py` 中搜索 intent/reason 相关配置
  - [ ] 确保配置项使用 step/decide 命名

### Day 5: 测试和文档更新

- [ ] **完整测试**
  ```bash
  cd cairn && pytest tests/ -v --cov=cairn/src/cairn
  ```

- [ ] **文档更新**
  - [ ] 更新 README.md 架构说明
  - [ ] 更新 API 文档（如果有）
  - [ ] 添加 ARCHITECTURE.md 描述 FGS 图

- [ ] **提交 Week 1 成果**
  ```bash
  git add .
  git commit -m "Week 1: 清理 Intent/Reason 概念，统一为 Step/Decide"
  ```

---

## Week 2: 实现统一 Agent Loop (2026-09-09 ~ 09-15)

### Day 1-2: Agent Loop 核心抽象

- [ ] **创建 agent_loop.py**
  - [ ] 定义 Tool 协议
  - [ ] 定义 AgentContext 数据类
  - [ ] 定义 AgentResult 数据类
  - [ ] 实现 AgentLoop 类
  - [ ] 实现提示词构建逻辑

- [ ] **单元测试**
  ```bash
  # tests/test_agent_loop.py
  pytest tests/test_agent_loop.py -v
  ```

### Day 3: Tool 系统实现

- [ ] **创建 tools.py**
  - [ ] 实现 BaseTool 基类
  - [ ] 实现 Decide 工具集（5个工具）
    - [ ] ReadGraphTool
    - [ ] CreateStepTool
    - [ ] UpdateStepTool
    - [ ] CreateGoalTool
    - [ ] CompleteGoalTool
  - [ ] 实现 Execute 工具集（6个工具）
    - [ ] ReadGraphTool
    - [ ] BashTool
    - [ ] ReadFileTool
    - [ ] WriteFileTool
    - [ ] SubmitFactTool
    - [ ] SubmitFindingTool

- [ ] **工具测试**
  ```bash
  pytest tests/test_tools.py -v
  ```

### Day 4: 重构 Decide 使用新抽象

- [ ] **创建 decide_v2.py**
  - [ ] 使用 AgentLoop 实现
  - [ ] 使用 DECIDE_TOOLS
  - [ ] 保持与旧版本相同的 API

- [ ] **配置开关**
  ```python
  # config.py
  use_agent_loop_v2: bool = False  # 先测试
  ```

- [ ] **对比测试**
  - [ ] 同一个项目分别用 v1 和 v2 跑
  - [ ] 对比结果是否一致

### Day 5: 重构 Execute 使用新抽象

- [ ] **创建 execute_v2.py**
  - [ ] 使用 AgentLoop 实现
  - [ ] 使用 EXECUTE_TOOLS
  - [ ] 保持与旧版本相同的 API

- [ ] **集成测试**
  ```bash
  # 启用 v2
  # config: use_agent_loop_v2 = True
  pytest tests/integration/ -v
  ```

- [ ] **提交 Week 2 成果**
  ```bash
  git commit -m "Week 2: 实现统一 Agent Loop 和 Tool 系统"
  ```

---

## Week 3: 重构 Bootstrap + 优化提示词 (2026-09-16 ~ 09-22)

### Day 1-2: Bootstrap 重构

- [ ] **重构项目创建流程**
  - [ ] 修改 `services.py::create_project()`
  - [ ] 改为 `create_project_with_bootstrap()`
  - [ ] 创建 origin 和 goal Fact
  - [ ] 创建主 Goal
  - [ ] 触发第一次 Decide

- [ ] **删除旧 Bootstrap 任务**
  - [ ] 标记 `tasks/bootstrap.py` 为 deprecated
  - [ ] 从调度器中移除 bootstrap 任务类型
  - [ ] 更新相关测试

### Day 3: 优化 Decide 提示词

- [ ] **增强 decide.md**
  - [ ] 添加 Bootstrap 场景特殊处理
  - [ ] 添加初始 Steps 生成指导
  - [ ] 添加示例（侦察、认证、输入验证等）

- [ ] **A/B 测试**
  - [ ] 创建 `prompts/v2_optimization/decide.md`
  - [ ] 在测试靶场上对比效果
  - [ ] 记录改进数据

### Day 4: 优化 Execute 提示词

- [ ] **强化 execute.md**
  - [ ] 添加"彻底探索"指导
  - [ ] 添加浅层 vs 深层探索示例
  - [ ] 强化 Finding 报告要求
  - [ ] 添加严重级别指南

- [ ] **测试深度探索**
  - [ ] 在 DVWA 上测试 SQL 注入检测
  - [ ] 验证是否尝试多种 payload
  - [ ] 验证是否有完整的利用证明

### Day 5: 集成测试

- [ ] **端到端测试**
  ```bash
  pytest tests/integration/test_full_flow.py -v -s
  ```

- [ ] **提交 Week 3 成果**
  ```bash
  git commit -m "Week 3: Bootstrap 重构 + 提示词深度优化"
  ```

---

## Week 4: Sub Goal 调度 + 验证 (2026-09-23 ~ 09-30)

### Day 1-2: 智能调度器

- [ ] **创建 goal_aware_scheduler.py**
  - [ ] 实现 GoalAwareScheduler 类
  - [ ] 实现 select_next_step() 方法
  - [ ] 实现 should_trigger_decide() 方法
  - [ ] 实现停滞检测逻辑

- [ ] **集成到调度循环**
  - [ ] 修改 `scheduler/loop.py`
  - [ ] 使用新调度器选择 Step
  - [ ] 使用新逻辑触发 Decide

### Day 3: 测试和调优

- [ ] **单元测试**
  ```bash
  pytest tests/test_goal_scheduler.py -v
  ```

- [ ] **集成测试**
  - [ ] 创建需要 Sub Goal 的复杂场景
  - [ ] 验证是否聚焦完成一个 Goal 再切换
  - [ ] 验证停滞检测是否工作

### Day 4: 靶场验证

- [ ] **WebGoat 测试**
  - [ ] 创建项目，运行完整流程
  - [ ] 统计发现的漏洞数量
  - [ ] 检查 Finding 质量

- [ ] **DVWA 测试**
  - [ ] 低、中、高难度各测一次
  - [ ] 对比发现率

- [ ] **记录指标**
  - [ ] 完成率
  - [ ] 发现漏洞数量
  - [ ] Finding 详细程度

### Day 5: 文档和发布

- [ ] **完善文档**
  - [ ] 更新 README.md
  - [ ] 编写 ARCHITECTURE.md
  - [ ] 编写 CONTRIBUTING.md

- [ ] **合并到主分支**
  ```bash
  git checkout main
  git merge refactor/cairn-to-cairn-y
  git tag v2.0.0-cairn-y
  git push origin main --tags
  ```

- [ ] **清理工作**
  - [ ] 删除所有 deprecated 代码
  - [ ] 删除 v1 实现（decide.py, execute.py）
  - [ ] 删除 legacy 提示词

---

## 验收标准

### 代码质量
- [ ] 所有测试通过
- [ ] 测试覆盖率 > 70%
- [ ] 无 Intent/Reason 概念残留
- [ ] 类型检查通过（mypy）

### 功能验收
- [ ] WebGoat 发现至少 5 个漏洞
- [ ] DVWA 在中等难度下发现至少 3 个漏洞
- [ ] Finding 包含完整的标题、严重级别、描述
- [ ] Bootstrap 生成 3-5 个合理的初始 Steps

### 性能验收
- [ ] Decide 响应 < 60s
- [ ] Execute 不超时
- [ ] 系统连续运行 4 小时无崩溃

---

## 问题和风险记录

### 遇到的问题

**问题 1**：  
**解决方案**：

**问题 2**：  
**解决方案**：

### 风险跟踪

- [ ] 重构破坏现有功能 → 渐进式切换，保留 v1
- [ ] 性能下降 → 基准测试监控
- [ ] 提示词效果不佳 → A/B 测试，保留多版本

---

## 进度跟踪

| Week | 任务 | 状态 | 完成日期 |
|------|-----|------|---------|
| 1 | 架构清理 | ⏸️ 待开始 | - |
| 2 | Agent Loop | ⏸️ 待开始 | - |
| 3 | Bootstrap + 提示词 | ⏸️ 待开始 | - |
| 4 | 调度 + 验证 | ⏸️ 待开始 | - |

**当前进度**：0% (0/4 周完成)

---

## 下一步行动

**立即执行**（今天完成）：
1. ✅ 创建重构分支
2. ✅ 创建此清单
3. ⏸️ 开始 Week 1 Day 1 任务：清理 models.py

**本周目标**：
- 完成 Week 1 所有任务
- 代码中不再有 Intent/Reason 概念

**需要的资源**：
- 测试靶场环境（WebGoat, DVWA）
- 足够的 API 调用额度（Claude/GPT）
- 开发时间（每天 4-6 小时）
