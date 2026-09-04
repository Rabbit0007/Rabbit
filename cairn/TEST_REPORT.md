# Rabbit Cairn-Y 最终测试报告

测试日期：2026-09-04

## 1. 结论

当前 Cairn-Y 重构已通过后端全量测试、前端生产构建、旧数据库迁移副本验证、临时端口端到端验证和 8000 端口真实部署验证。

```text
Backend:  349 passed in 34.86s
Frontend: 1891 modules transformed, built in 7.56s
Runtime:  Server healthy; Dispatcher running; Egress proxy healthy
Workers:  4/4 healthy, HTTP 200
Restarts: 0
```

前端仅有 Vite 对单个大于 500 kB chunk 的性能提示，不影响构建成功或运行正确性。

## 2. 后端测试覆盖

全量测试命令：

```bash
cd /Users/rabbit/Desktop/Rabbit/cairn
uv run pytest -q
```

结果：

```text
349 passed in 34.86s
```

关键覆盖范围：

- Decide 输出合同、串行决策和 Goal 完成证据。
- Execute 输出合同、Fact 收敛和可选 Finding。
- Dispatcher 的租约、并发、取消、心跳、超时和任务历史。
- Docker / local backend 行为一致性。
- Worker 注册、健康检查、Pi provider 与模型配置。
- 内部 API Token 透传和鉴权。
- 带前置说明和空对象文本的 Pi 输出仍能提取最后一个 JSON 代码块。
- FGS API、来源边与旧接口兼容层。
- Finding 唯一真相源及 vulnerabilities 投影。
- 旧 intents 数据迁移和遗留表删除。
- 前端所需项目、时间线、报告和 Worker API。

## 3. 前端构建

生产构建成功：

```text
✓ 1891 modules transformed
✓ built in 7.56s
```

生成内容已写入 Server 静态资源目录，包含真实 FGS 图谱、Inspector 和 Finding 展示。

## 4. 端到端闭环验证

在独立临时数据库和 Mock Worker 上完成可重复闭环：

```text
Decide
→ Step s001 from origin
→ Execute
→ Fact f001
→ Decide
→ Goal completed from f001
```

验证项目结果：

```json
{
  "project_id": "proj_061",
  "status": "completed",
  "facts": ["origin", "f001"],
  "goal_sources": ["f001"],
  "step_edges": [[["origin"], "f001"]],
  "findings": 0
}
```

该验证证明 Decide 只改变 FGS，Execute 始终产出 Fact，Goal 只能在存在证据 Fact 时完成。

## 5. 数据库迁移验证

部署前已复制真实数据库，并在副本执行完整迁移。副本验证结果：

```text
legacy tables: 0
projects:       10
facts:          340
goals:          10
steps:          337
step_sources:   782
findings:       17
vulnerabilities:17
```

真实部署启动后的数据库结果：

```text
legacy tables: 0
projects:       10
facts:          344
goals:          10
steps:          342
step_sources:   799
findings:       17
vulnerabilities:17
```

以上真实库计数观测于 2026-09-04 14:16。它比迁移副本多出的 Fact、Step 和来源边来自正在运行的 `proj_058`，属于正常业务写入。

## 6. 真实部署验证

执行：

```bash
cd /Users/rabbit/Desktop/Rabbit
docker compose build pentest-server
docker compose up -d --force-recreate
```

容器状态：

```text
rabbit-pentest-server        running, healthy, restart=0
rabbit-pentest-dispatcher    running, restart=0
rabbit-pentest-egress-proxy  running, healthy, restart=0
```

Dispatcher 启动结果：

```text
workers=4 parallelism=4
deepseek-v4-pro-1 HTTP 200
deepseek-v4-pro-2 HTTP 200
deepseek-v4-pro-3 HTTP 200
deepseek-v4-pro-4 HTTP 200
healthy=4 unhealthy=0
```

最终部署日志中没有配置解析错误和内部 API 401。Dispatcher 已成功恢复活动项目 `proj_058`，并继续派发未完成 Step。

### 真实 Pi 输出回归

第一次部署观察到 Pi 会在最终 JSON 前输出说明文字，其中存在 `Scope Policy={}`。旧提取顺序会误把该空对象识别为 Decide 结果。修复后使用同一份真实 Pi 输出重放：

```text
captured_pi_replay_kind=steps
captured_pi_replay_steps=3
```

对应回归用例已加入全量测试，修复后的镜像已重新构建并部署。

## 7. 隔离和回滚验证

- Rabbit 仅监听 `127.0.0.1:8000`。
- 18000 端口的另一套 Cairn 服务未被本次 compose 操作管理。
- 部署前数据库已备份并计算 SHA-256。
- 临时验证数据库与真实数据库分离。

备份：

```text
/Users/rabbit/Desktop/Rabbit/.rabbit/backups/pre-rabbit-cairn-y-deploy-20260904-091949/cairn.db
SHA-256: 6b16fc4ae0c305cf6c5b4a5c782b104452dff8697f4e7bc55e1b0a135169860b
```

## 8. 验收结论

本次实现满足目标架构：Server 是唯一真相源，Dispatcher 只负责编排，Worker 保持通用，Decide 串行修改 FGS，Execute 将结果收敛为 Fact，Finding 由 Execute 结构化产生。旧执行分叉和旧数据库模型已完成收口，当前部署运行稳定。

## 9. 全新系统重置

2026-09-04 14:30 已清除活动系统中的全部历史测试项目和产物，并重新初始化数据库：

```text
projects:        0
facts:           0
goals:           0
steps:           0
step_sources:    0
findings:        0
vulnerabilities: 0
project containers: 0
```

版本库仅保留 `_template.project-context.yaml` 作为项目上下文模板；运行态项目上下文、数据库和备份均不纳入 Git。Server、Dispatcher 和 Egress Proxy 已重新启动，4 个 Pi Worker 均通过 HTTP 200 健康检查。

## 10. GitHub 发布前验证

2026-09-04 15:04 以公开仓库默认配置重新验证：

```text
docker compose config: ok
Pi-only workers:        4
max_workers:            4
max_running_projects:   1
max_project_workers:    3
Backend:                349 passed
Frontend:               production build passed
HTTP:                   200
Container restarts:     0
```
