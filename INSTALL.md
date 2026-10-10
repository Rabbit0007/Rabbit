# Rabbit 安装与升级

## 环境要求

- Docker Engine/Desktop，Docker Compose v2（`docker compose version` 可用）
- Git、可访问 GitHub/GHCR 和模型服务的网络
- 可用的 GLM-5.3 或其他 OpenAI-compatible 模型密钥
- Docker 守护进程可用；Dispatcher 会挂载 Docker socket 来启动任务容器，请仅在受信任的主机运行

默认仅将 Web 服务绑定到 `127.0.0.1:8000`。如需远程访问，请自行配置反向代理和访问控制。

## 首次安装 v0.3.1

```bash
git clone https://github.com/Rabbit0007/Rabbit.git
cd Rabbit
git checkout v0.3.1
cp .env.example .env
cp dispatch.yaml dispatch.local.yaml
chmod 600 .env dispatch.local.yaml
```

编辑 `.env`，为以下三个服务令牌分别填写不同的随机值；可以用 `openssl rand -hex 32` 生成。不要把模型密钥放进 `.env`。

```dotenv
CAIRN_INTERNAL_TOKEN=<随机值 A>
CAIRN_DISPATCHER_INTERNAL_TOKEN=<随机值 B>
CAIRN_WORKER_EGRESS_PROXY_TOKEN=<随机值 C>
```

编辑 `dispatch.local.yaml`，将 `PI_API_KEY: replace-with-your-model-api-key` 改为自己的模型密钥。4 个 Worker 共用 YAML 锚点；只需在第一个 Worker 的 `env` 里修改一次。如更换模型，还需同步修改 `PI_MODEL`、`PI_BASE_URL`、`PI_PROVIDER_API`。

`dispatch.local.yaml` 和 `.env` 已被 Git 忽略。不要把它们上传到公开仓库或作为工单附件。若容器无法连接 Server，检查本地配置中的 `TSEC_SERVER_HOST` 是否为任务容器实际可访问的地址。系统服务之间的地址由 Compose 配置，不应随意修改 `server: http://rabbit-pentest-server:8000`。

```bash
docker compose config -q
docker build --platform linux/amd64 -f container/Dockerfile.pi -t rabbit-pentest-worker:pi-0.84.4 .
docker compose up -d --build
docker compose ps
```

浏览器打开 `http://127.0.0.1:8000/`，注册第一个账号。启动日志可用 `docker compose logs -f pentest-server pentest-dispatcher` 查看。首次构建会下载 Node、Python 依赖和 Worker 镜像，时间取决于网络。

镜像构建会执行 `npm ci` 和 `npm run build`，无需在宿主机预装 Node。使用 Docker Desktop 时，要允许访问项目目录，并确保 Docker 守护进程可启动任务容器。

## 从现有仓库升级

1. 停止正在执行的项目，记录 `docker compose ps` 和本地 `git status`。升级前备份 `datas/cairn/`、Docker volume `rabbit-pentest-artifacts`、`.env` 和本地 Worker 配置。
2. 如果原先直接在受 Git 跟踪的 `dispatch.yaml` 中配置了模型密钥，先复制为 `dispatch.local.yaml`，确认文件已被 Git 忽略且内容正确。不要把该密钥提交到 Git。
3. 清理或妥善保存其他本地未提交改动后，执行 `git fetch --tags origin` 和 `git checkout v0.3.1`。比较 `.env.example` 与已有 `.env`，确保三个服务令牌均已设置。
4. 执行 `docker compose up -d --build`，再用 `docker compose ps`、浏览器登录和 Worker 连接测试验证升级。

升级不会自动删除 `datas/cairn/` 或命名 volume。不要用 `docker compose down -v`，除非确实要清空持久数据。

## 日常操作

```bash
docker compose ps
docker compose logs -f pentest-server pentest-dispatcher
docker compose down
```

Server 数据库位于 `./datas/cairn/`；部分运行证据位于 Docker volume `rabbit-pentest-artifacts`。Worker 配置保存在 `dispatch.local.yaml`，Web 管理页对 Worker 的修改也会写入该本地文件。前端或后端源码变动后需要重新执行 `docker compose up -d --build`。

## 常见问题

- **页面缺少样式或脚本**：确认使用 v0.3.1 的 Dockerfile 重建镜像；本版本在镜像构建时生成前端静态资源。
- **Worker 测试失败**：检查模型密钥、模型名和 Base URL；确认任务容器能访问 `TSEC_SERVER_HOST` 指向的 Server。模型服务不可用不会阻止 Web 页面启动。
- **服务无法启动**：检查 `docker compose config -q`、`docker compose ps` 与相应服务日志；确认 8000 端口未被其他程序占用。
- **已有本地配置不生效**：Compose 默认挂载 `dispatch.local.yaml`。如使用别的文件，可设置 `CAIRN_DISPATCH_CONFIG` 为仓库根目录内的配置文件名。
