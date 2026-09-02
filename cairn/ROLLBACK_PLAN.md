# 回退计划 - 回到正确的 Cairn_Y 路线

## ❌ 需要回退的错误工作

### Week 2 Day 7 的错误（babdfc7 提交）
- ❌ Tool Schemas (schemas.py) - Cairn 不需要这个
- ❌ Tool 执行函数重构 - 不需要纯函数
- ❌ decide_v2.md / execute_v2.md - 错误的 tool_use 格式
- ❌ Agent Loop 的整个思路 - Cairn 根本没有循环

### 需要保留的正确工作
- ✅ Week 1 Day 1-2: 术语统一 (Intent→Step, Reason→Decide)
- ✅ Week 2 Day 5-6: 调度器集成框架（虽然还没实现，但方向对）

## ✅ Cairn_Y 的正确理解

### Cairn 的核心架构（保持不变）
```
1. Reason/Decide: 
   - LLM 一次调用
   - 返回 JSON: {"accepted": true, "data": {"intents": [...]}} 或 {"complete": {...}}
   - 系统解析 JSON，调用 API 创建 Intent

2. Explore/Execute:
   - LLM 一次调用（可以用 bash/http 工具，由 Worker 提供）
   - 返回 JSON: {"description": "发现了..."}
   - 系统创建 Fact
```

### Cairn_Y 的改动（最小化）

#### 1. Decide (原 Reason)
**JSON 输出格式变化**：
```json
// Cairn 原版
{"accepted": true, "data": {
  "intents": [
    {"from": ["f1"], "description": "测试 SQL 注入"}
  ]
}}

// Cairn_Y
{"accepted": true, "data": {
  "steps": [  // intents → steps
    {"from": ["f1"], "description": "测试 SQL 注入"}
  ],
  "findings": [  // 新增：记录漏洞
    {
      "title": "SQL 注入漏洞",
      "description": "...",
      "severity": "high",
      "evidence_fact_ids": ["f2", "f3"]
    }
  ]
}}
```

#### 2. Execute (原 Explore)
**保持不变** - 仍然返回 description

#### 3. Prompt 改动
- decide.md: 指导输出 `steps` 和 `findings`
- execute.md: 基本不变

## 📋 正确的实施步骤

### Step 1: 回退错误提交
```bash
git revert babdfc7  # 回退 Day 7 的错误工作
```

### Step 2: 修改 Decide Prompt
- 编辑 `prompts/default/reason.md` → `decide.md`
- 改输出格式：`intents` → `steps`，增加 `findings` 字段

### Step 3: 修改 contracts.py
- `validate_reason_payload()` → `validate_decide_payload()`
- 支持解析 `steps` 和 `findings`

### Step 4: 修改 API 调用
- `reason.py` 中，解析 `steps`，调用 `client.create_step()`
- 解析 `findings`，调用 `client.create_finding()`

### Step 5: 测试
- 用 Mock Worker 测试
- 验证 JSON 格式正确
- 验证 API 调用正确

## 🎯 关键点

1. **不要引入 Agent Loop** - Cairn 就是一次性调用
2. **不要引入 Tool Schema** - Worker 自己提供工具（bash/http）
3. **只改 JSON 格式** - 从 intents 改为 steps，加 findings
4. **保持简单** - Cairn 的架构已经很好了

## ❓ 需要确认的问题

1. 是否回退到 Week 2 Day 5-6 之后（保留调度器集成框架）？
2. 还是回退到 Week 1 完成的状态？
3. Findings 的 API 是否已经存在（client.create_finding()）？

请告诉我从哪里开始回退。
