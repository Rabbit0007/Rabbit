FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY ./cairn/frontend/package.json ./cairn/frontend/package-lock.json ./
RUN npm ci
COPY ./cairn/frontend/ ./
RUN VITE_OUT_DIR=/frontend-dist npm run build

FROM ghcr.io/astral-sh/uv:python3.13-trixie

COPY ./cairn/pyproject.toml /cairn/pyproject.toml
COPY ./cairn/uv.lock /cairn/uv.lock
WORKDIR /cairn
RUN uv sync --frozen --no-install-project -i https://mirrors.aliyun.com/pypi/simple/

COPY ./cairn /cairn
COPY --from=frontend /frontend-dist/ /cairn/src/cairn/server/static/
COPY ./.rabbit /cairn/.rabbit
RUN uv sync --frozen -i https://mirrors.aliyun.com/pypi/simple/

ENV TZ=Asia/Shanghai
