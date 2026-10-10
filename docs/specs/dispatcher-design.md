# Rabbit Dispatcher 设计

## 定位

Rabbit Dispatcher 是项目执行控制面。它不负责保存协议真相，真相仍在 Rabbit Server；Dispatcher 负责把项目状态转成可执行任务，把任务交给 Worker，并把 Worker 的结构化输出写回 Server。

Dispatcher 解决的问题：

- 多个项目如何并发推进。
- 同一项目内如何避免重复 Decide 或重复 claim。
- Worker 掉线、超时、输出格式错误时如何收敛。
- 项目停止、完成、删除时如何清理本地任务和容器。
- Agent 输出如何变成 Fact、Finding 或 Goal 完成。

## 组件关系

```text
                  +---------------------+
                  |    Rabbit Server    |
                  |---------------------|
                  | Project / Fact      |
                  | Goal / Step         |
                  | Finding / Hint      |
                  | Auth / Templates    |
                  | Vulnerabilities     |
                  +----------^----------+
                             |
                      HTTP API read/write
                             |
+--------------------------------------------------+
|                Rabbit Dispatcher                 |
|--------------------------------------------------|
| Scheduler / Worker Selection / Heartbeat         |
| Timeout / Output Parsing / Container Lifecycle   |
+------------------------+-------------------------+
                         |
             start process inside container
                         |
             +-----------v------------+
             |   Project Container    |
             |------------------------|
             | Worker / Agent CLI     |
             | Tools / Workspace      |
             +------------------------+
```

## 基本原则

1. Server 是唯一协议真相源。
2. Dispatcher 是唯一协议写入者和控制面。
3. Worker 不直接 claim Step，不直接 heartbeat，不直接调用 Server API。
4. Worker 只接收任务上下文，输出结构化 JSON。
5. Dispatcher 负责校验输出、处理失败、释放资源和写回协议。

这样做的原因是：Agent 输出不可完全信任，协议写入必须集中在可测试、可控的代码路径里。

## 任务类型

### decide

当项目初始、图谱发生变化或开放 Step 耗尽时，Dispatcher 触发 `decide`。

`decide` 负责判断：

- 当前图是否已经满足某个 Goal。
- 是否应该创建新的 Step。
- 是否调整现有 Step 的优先级，或放弃不再相关的 Step。
- 是否创建或删除子 Goal。

同一个项目同一时间只允许一个 `decide`，由项目级 decide lease 保证。Decide 租约存在期间，不启动新的 Execute 认领。

### execute

当项目存在未认领 Step 时，Dispatcher 选择一个 Worker 执行 `execute`。

流程：

1. Dispatcher 先 claim 目标 Step。
2. claim 成功后启动 Worker。
3. Worker 输出结论描述和可选 Finding。
4. Dispatcher 通过 conclude 把结论写成 Fact 并结束该 Step。
5. 如果失败或超时，Dispatcher 释放 Step 或等待 Server 超时回收。

## 调度循环

每轮调度大致执行：

1. 读取 Rabbit Server 的项目列表。
2. 过滤非 active 项目。
3. 同步本地运行任务和远端项目状态。
4. 对 stopped/completed/deleted 项目执行取消和容器清理。
5. 计算全局并发、项目并发和 Worker 并发余量。
6. 为每个可运行项目选择任务类型。
7. 选择可用 Worker。
8. 启动任务并登记本地运行状态。
9. 周期性 heartbeat。
10. 处理任务完成、失败、超时和输出解析。

## Worker 选择

Worker 配置来自 `dispatch.yaml`。

选择顺序：

1. 任务类型匹配。
2. Worker 未达到 `max_running`。
3. Worker 不在短暂不可选窗口。
4. `priority` 更小者优先。
5. 当前运行任务数更少者优先。
6. 仍相同则随机选择。

这保证慢 Worker 不会被压垮，快 Worker 也不会被单个项目独占。

## 容器生命周期

每个项目对应一个项目容器。容器提供工具链、网络环境和工作目录。

容器行为由配置控制：

- `container.image`：基础镜像。
- `container.network_mode`：网络模式。
- `container.completed_action`：项目完成后保留还是删除容器。
- `container.cap_add`：必要时增加 Linux capability。

项目状态变化时：

- `stopped`：取消本地任务，停止容器。
- `completed`：取消不再需要的任务，进入收尾清理。
- `deleted`：取消任务并删除 orphan 容器。
- `reopen`：下一轮按普通 active 项目继续调度。

## 输出契约

Worker 必须输出 Dispatcher 可解析的 JSON。Dispatcher 只接受明确结构，不从自由文本里猜测协议写入。

允许的结果类型：

`decide` 任务：

- `complete`：判定某个 Goal 已由现有 Fact 满足。
- `steps` / `step_updates` / `sub_goals`：创建 Step、调整现有 Step、管理子 Goal。
- `noop`：本轮不写入。
- `rejected`：Worker 明确拒绝或无法执行。

`execute` 任务：

- `fact`：写入新的结论 Fact 描述，可携带一个结构化 `finding`。
- `rejected`：Worker 明确拒绝或无法执行。

非法 JSON、字段缺失、类型不匹配都会被视为任务失败。Decide 的图变更在写入前还会校验 Fact / Goal / Step 引用的有效性。

## 超时策略

不同任务有不同超时：

- `decide.timeout`
- `execute.timeout`
- `execute.conclude_timeout`

`execute` 有两阶段模式：主阶段失败或超时后，Dispatcher 让同一 session 进入 conclude 阶段，尽量把已确认信息整理成 Fact。

`decide` 不做二阶段收尾。它失败就放弃本轮，避免写入不可靠的全局判断。

## 配置模型

示例字段：

```yaml
server: "http://rabbit-pentest-server:8000"

runtime:
  interval: 3
  max_workers: 4
  max_running_projects: 1
  max_project_workers: 4
  healthcheck_timeout: 60
  prompt_group: "default"

tasks:
  decide:
    timeout: 300
    max_steps: 3
  execute:
    timeout: 300
    conclude_timeout: 90

workers:
  - name: "glm-5.3-1"
    type: "pi"
    task_types: [decide, execute]
    max_running: 1
    priority: 0
```

旧配置中的 `bootstrap` / `reason` / `explore` 任务名会在加载时映射为 `decide` / `execute`。

## 可观测性

Dispatcher 应记录这些事件：

- 容器创建、启动、停止和删除。
- Worker 健康检查结果。
- 任务派发、完成、失败和超时。
- claim、release、conclude、complete 写回结果。
- 项目状态变化导致的取消和清理。

稳定轮询和正常 heartbeat 不需要刷屏，避免日志被噪声淹没。

## 已知限制

- 当前按单 Dispatcher 实例设计。
- Step 只记录当前/最终 Worker，不保存完整 Worker 历史。
- Worker 输出质量依赖 prompt 和模型能力，Dispatcher 只负责结构校验和协议写回。
- 项目容器的安全边界取决于部署方式，生产环境需要自行配置网络、权限和密钥管理。

## 设计目标

Rabbit Dispatcher 的核心目标不是让 Agent “直接控制系统”，而是让 Agent 在可审计、可回滚、可调度的边界内工作。协议写入集中在 Dispatcher，探索判断交给 Worker，最终所有结果都回到 Rabbit Server 的事实图里。
