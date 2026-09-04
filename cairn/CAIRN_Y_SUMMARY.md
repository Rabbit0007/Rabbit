# Rabbit Cairn-Y 架构总结

更新时间：2026-09-04

## 1. 最终架构

Rabbit 当前采用 Cairn-Y 的单一执行闭环：

```text
Server = 唯一状态真相源
Dispatcher = 调度、并发、租约和生命周期
Worker = 通用执行单元

Fact → Decide → Goal / Step → Execute → Fact
                                  └── Finding（可选）
```

核心约束：

- **Server 是唯一状态真相源**：项目、Fact、Goal、Step、Finding 和来源边都由 Server 持久化。
- **Dispatcher 不保存第二份业务状态**：只负责选取任务、获取租约、选择 Worker、执行、心跳、超时、取消和重试。
- **Worker 是通用执行单元**：Worker 不绑定固定角色；同一 Worker 可承担 Decide 或 Execute。
- **Decide 串行运行**：读取当前 FGS 图谱，只能创建 Step 或完成 Goal，不直接执行动作，也不产出 Finding。
- **Execute 可并行运行**：执行一个 Step，任何结果都必须收敛为新的 Fact；如果发现成立，可同时提交结构化 Finding。
- **Finding 是 Execute 的原生产物**：`findings` 是唯一发现真相源，`vulnerabilities` 只作为 UI 和报告兼容投影存在。

## 2. FGS 数据模型

### Fact

对已发生事实、执行结果和证据的不可变记录。每次 Execute 无论成功、失败或信息不足，都要创建 Fact，避免执行结果悬空。

### Goal

项目希望达到的终态。Goal 的完成只能由 Decide 根据已有 Fact 明确判定，并记录完成证据来源。

### Step

由 Decide 基于当前 Fact 和 Goal 生成的下一步工作。Step 通过 `step_sources` 关联其依据 Fact；执行完成后关联结果 Fact。

### Finding

Execute 在证据充分时创建的结构化发现，包含标题、说明、严重性、证据 Fact、CWE/CVSS 等字段。报告和旧漏洞视图均从 Finding 投影，不再从自然语言 Fact 猜测发现。

## 3. 运行链路

1. Server 创建项目、初始 Fact 和 Goal。
2. Dispatcher 检测项目需要决策，为 Decide 获取项目级租约。
3. Decide 读取完整 FGS 上下文，创建 Step，或使用证据 Fact 完成 Goal。
4. Dispatcher 为可执行 Step 获取租约，将其派发给任意健康 Worker。
5. Execute 执行 Step，提交 Fact；存在发现时在同一结果中提交 Finding。
6. Dispatcher 释放租约并再次触发 Decide。
7. 所有 Goal 均完成且没有未收敛 Step 后，项目进入完成状态。

这条链路只有 `decide`、`execute` 和 `execute_conclude` 三类实际推理输出；健康检查独立存在。旧任务名只在配置加载时做单向兼容转换，不进入运行态。

## 4. 已完成实现

### Dispatcher

- Decide / Execute 两阶段调度与项目级串行决策。
- Step 级并发、租约、心跳、取消、超时、重试和生命周期管理。
- Docker 与本地进程两种执行后端，共用统一 Backend 接口。
- Worker 注册、能力检测、启动健康检查和 Pi/Codex/Claude Code/Mock 适配。
- Pi provider 名自动规范化为 `openai-completions`。
- 内部请求自动携带 `X-Cairn-Internal-Token`，避免 Dispatcher 访问 Server 出现 401。
- 模型输出解析优先采用最后一个 JSON 代码块，避免说明文字中的 `{}` 抢先遮蔽真实 Decide 结果。
- 对旧 `bootstrap/reason/explore` 配置做加载期单向映射，运行时只产生 Decide/Execute。

### Server

- FGS、来源边、租约、任务结果和 Finding 的完整 API 与持久化。
- Goal 完成证据校验，防止无 Fact 支撑的完成操作。
- Execute 响应严格限定为 `description` 和可选 `finding`，禁止越权修改 Goal。
- Finding 到 vulnerabilities 的确定性投影。
- YAML Hint 内容统一归一化。
- 内部 API Token 校验和 Dispatcher 状态接口。

### 前端与报告

- 项目页展示真实 Fact / Goal / Step / Finding 图谱。
- Inspector 展示节点详情、状态、来源证据和执行关系。
- Finding 列表、详情、报告数据均读取结构化 Finding。
- 删除依据 Fact 文本关键词推测漏洞的旧路径。

### 数据库迁移

启动迁移会完成：

1. 旧 `intents` 数据迁移到 `steps`。
2. 旧 Goal 类 Fact 迁移到 `goals`。
3. 迁移 Goal 完成证据和 Step 来源边。
4. 删除 `intent_sources` 和 `intents`，消除双模型与双真相源。

迁移可重复执行，旧库先备份，再在副本上验证，最后应用到真实数据库。

## 5. 已删除的错误分叉

- 独立的第二套循环执行内核。
- 旧 `bootstrap`、`reason`、`explore` 任务实现和对应 Prompt。
- 不再使用的 dispatcher 内部 graph/world tools。
- 基于 Fact 文本关键词自动生成漏洞的提取器。

删除这些分叉后，项目只保留一条可解释、可测试、可恢复的运行路径。

## 6. 当前部署状态

Rabbit 服务：

```text
rabbit-pentest-server        127.0.0.1:8000
rabbit-pentest-dispatcher
rabbit-pentest-egress-proxy
```

当前 Pi Worker：4 个，启动健康检查均为 HTTP 200。全局并发为 4，单项目执行并发为 3，同时只运行 1 个项目；第四个 Worker 提供调度和故障切换余量。

`18000` 端口上的另一套 Cairn 服务与 Rabbit 独立，本次没有修改或重启。

当前全新数据库：

```text
legacy tables: 0
projects:       0
facts:          0
goals:          0
steps:          0
step_sources:   0
findings:       0
vulnerabilities:0
```

以上为 2026-09-04 14:30 执行全新系统重置后的观测值。所有旧项目、项目容器、项目上下文、验证夹具和报告产物均已从活动系统清除。

## 7. 回滚点

部署前数据库备份：

```text
/Users/rabbit/Desktop/Rabbit/.rabbit/backups/pre-rabbit-cairn-y-deploy-20260904-091949/cairn.db
SHA-256: 6b16fc4ae0c305cf6c5b4a5c782b104452dff8697f4e7bc55e1b0a135169860b
```

全新系统重置前备份：

```text
/Users/rabbit/Desktop/Rabbit/.rabbit/backups/pre-fresh-reset-20260904-143031
database SHA-256: f2e45a28e5121dc73343880026765ec49c2cf37050aad5a397a6068dcf6ebfc2
```

回滚时应先停止 Rabbit 的三个容器，保留当前数据库副本，再恢复备份并用同一版本重新启动。不要操作 18000 端口服务。
