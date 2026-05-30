# Rabbit 协作探索协议

## 定位

Rabbit 把一次智能渗透测试或目标导向探索建模为一张持续增长的事实图。系统不把 Agent 的输出当成一次性答案，而是要求每个关键结论都落成可追溯的节点和边。

协议的目标是：

- 让多人和多个 Agent 在同一个项目里协作。
- 让每个发现都能追溯到它的前置事实和探索动作。
- 让项目可以停止、恢复、重开，而不是只能一次性跑完。
- 让漏洞报告、时间线和复盘材料都来自同一份结构化记录。

## 核心对象

### Project

Project 是一个完整测试任务。

每个 Project 至少包含：

- `title`：项目名称。
- `status`：项目状态。
- `origin`：起点事实。
- `goal`：目标事实。
- facts：已确认事实集合。
- intents：探索动作集合。
- hints：提示集合。

项目状态：

| 状态 | 含义 |
| --- | --- |
| `active` | 正在运行，允许 Agent 和人工继续写入探索结果 |
| `stopped` | 硬停止，不再派发新任务，可恢复 |
| `completed` | 当前目标已完成，不再接受探索写入 |

`completed` 不是永久终局。如果后续人工确认完成判断不充分，可以通过 reopen 回到 `active`。

### Fact

Fact 是已经确认的事实。Rabbit 里的事实只追加，不覆盖历史。

示例：

```text
f001: 目标 10.0.0.5 开放 80 和 8080 端口
f002: 8080 暴露 Spring Boot Actuator
f003: /env 泄露数据库连接信息
```

Fact 应该写结论，不应该直接塞大段原始日志。原始扫描结果可以用文件路径或摘要引用。

### Intent

Intent 是一次探索动作，表示从一个或多个 Fact 出发，尝试得到新的 Fact。

Intent 的典型生命周期：

```text
created -> claimed -> heartbeat -> concluded
                  \\-> released
                  \\-> timeout
```

字段语义：

- `from`：一个或多个源 Fact。
- `description`：要探索的问题。
- `creator`：谁提出这个方向。
- `worker`：当前谁在执行，或最终由谁产出结论。
- `to`：结论 Fact，未完成时为空。

多个 `from` 表示一次探索依赖多个已知事实，Rabbit 会把它视作同一个探索动作的多源输入，而不是丢掉其中某个事实。

### Hint

Hint 是提示，不是事实。

适合放：

- 人工给 Agent 的方向建议。
- 当前阶段的策略判断。
- 不确定但值得尝试的线索。
- 项目停止期间补充的上下文。

Hint 不参与因果链，不能替代 Fact。一个发现只有被确认后才应该写成 Fact。

## 协作规则

### 事实只追加

Rabbit 不修改历史事实。状态变化也通过追加新 Fact 表达。

例如：

```text
f010: 已拿到 host-a 的 shell
f018: host-a shell 已断开，最后一次可用时间为 14:32
```

后续 Agent 同时看到 `f010` 和 `f018`，自行判断当前可用性。

### Intent 需要认领

Agent 执行探索前需要 claim Intent。claim 成功后，系统通过 heartbeat 判断 Worker 是否仍然存活。

如果 Worker 超时，Intent 会回到可认领状态，其他 Worker 可以继续处理。

### 结论必须落 Fact

探索完成时，Intent 不能只写一段日志。它必须产出一个新的 Fact，并通过 `to` 指向这个 Fact。

这保证了：

- 图上能看到探索路径。
- 漏洞报告有结构化来源。
- 时间线能还原执行过程。

### Project Reason Lease

Rabbit 支持项目级 reason lease，用来表示“某个 Worker 正在对整个项目做一次态势判断”。

它不是 Fact，也不是 Intent，只是并发协调状态。它的作用是避免同一个项目里多个 Worker 同时做全局判断、重复创建 Intent 或重复完成项目。

## 任务语义

Rabbit Dispatcher 会把项目状态转成三类任务：

| 任务 | 使用场景 | 期望输出 |
| --- | --- | --- |
| `bootstrap` | 新项目刚开始，尝试快速获得第一批关键事实 | Fact，必要时直接 complete |
| `reason` | 当前没有可执行 Intent，判断是否完成或创建新 Intent | complete、intent 或 noop |
| `explore` | 已有 open Intent，执行具体探索方向 | 一个结论 Fact |

这些任务是 Dispatcher 和 Worker 之间的约定，不改变 Server 协议本身。

## API 分组

Rabbit Server 提供以下接口分组：

- Auth：注册、登录、退出、当前用户、修改密码。
- Projects：项目创建、列表、详情、状态变更、完成和重开。
- Facts / Intents / Hints：图数据写入和协作控制。
- Export：导出项目图快照，供 Worker Prompt 使用。
- Vulnerabilities：漏洞提取、过滤、统计和导出。
- Workers：Worker 状态和任务历史。
- Templates：内置模板和自定义模板。
- Timeline：项目攻击过程时间线。
- Settings：心跳超时等全局配置。

## 安全边界

Rabbit 的浏览器端使用服务端 Session 和 HTTP-only Cookie。受保护接口需要有效 Session；Dispatcher 这类机器客户端可以使用内部 Token 走服务间认证。

本地开发时，如果使用 `http://127.0.0.1`，Cookie 会按本地 HTTP 规则设置；生产部署应使用 HTTPS。

## 写入约束

- 空字符串、纯空白标题、空 Fact id 会被拒绝。
- 已完成的 Intent 不允许再次修改。
- stopped/completed 项目不接受新的探索写入。
- Hint 可以在 stopped/completed 状态继续补充，用于后续恢复或复盘。
- 删除项目会级联清理该项目的事实、意图、提示和漏洞结果。

## 设计原则

Rabbit 协议坚持三条原则：

1. 事实和动作分离：Fact 描述确认结果，Intent 描述探索行为。
2. 协作和推理分离：Server 维护一致性，Worker 执行判断和推理。
3. 结果和过程同等重要：最终报告必须能回到图上解释它是如何产生的。
