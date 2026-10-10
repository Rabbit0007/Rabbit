# Week 1 Day 1-2 执行计划

## 清理策略

经过分析，系统中同时存在：
- `routers/intents.py` (旧 API)
- `routers/steps.py` (新 API)
- 两者都已注册到应用中

**决策**：
1. **保留** `intents.py` 路由，但标记为 deprecated（向后兼容）
2. **确保** `steps.py` 是主要 API
3. **删除** models.py 中的别名定义（强制使用新类型）
4. **清理** Settings 中的旧字段名

## 执行步骤

### Step 1: 清理 models.py 类型别名
- 删除 `CreateIntentRequest = CreateStepRequest` (行 342)
- 删除 `ReasonClaimRequest = DecideClaimRequest` (行 400)
- 删除 `ProjectReason = ProjectDecide` (行 91)
- 删除重复定义的 `ProjectReason` 类 (行 116-121)

### Step 2: 标记 Intent 类为 deprecated
- 保留 Intent 类但添加 deprecated 注释
- 用于向后兼容 intents.py 路由

### Step 3: 清理 Settings 字段
- 将 `intent_timeout` 重命名为 `step_timeout`
- 将 `reason_timeout` 重命名为 `decide_timeout`

### Step 4: 标记 intents.py 路由为 deprecated
- 添加 deprecated 标记和说明

### Step 5: 运行测试
- 确保所有改动不破坏功能

开始执行...
