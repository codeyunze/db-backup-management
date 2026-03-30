##
# 发布镜像：playground 静态站点 + /api 均由 nginx 对外监听 :5555；Flask 仅容器内 :8081
#
# 构建（仓库根目录为上下文；前端在镜像内 pnpm 构建，无需宿主机先打包）：
#   docker build -t vben-backup-web .
# 运行（对外只映射 5555）：
#   docker run -p 5555:5555 vben-backup-web
#
# 仅后端本地开发仍可使用 back/Dockerfile（构建上下文为 back/ 目录）。
##

# syntax=docker/dockerfile:1

# ---------- 阶段 1：在容器内构建 playground（.env.production 中 VITE_GLOB_API_URL=/api） ----------
# 静态站点与目标 CPU 无关；多架构构建时若在此阶段跑 linux/arm64，会在 QEMU 下执行 pnpm/vite，
# 极易 OOM 或异常退出。固定用构建机原生平台（Buildx 注入的 BUILDPLATFORM，Actions 上多为 amd64）。
ARG BUILDPLATFORM
FROM --platform=$BUILDPLATFORM registry.cn-guangzhou.aliyuncs.com/devyunze/node:22.22-slim AS frontend-builder

ENV TZ=Asia/Shanghai \
    NODE_OPTIONS=--max-old-space-size=8192 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    CI=true

WORKDIR /app/web

# 使用国内 Debian 源，避免 deb.debian.org / security.debian.org 跨境过慢
# （不用 find -exec：部分基础镜像里 find/转义易失败；用 for 遍历即可）
RUN set -eux; \
    if [ -f /etc/apt/sources.list ]; then \
      sed -i \
        -e 's|deb.debian.org|mirrors.aliyun.com|g' \
        -e 's|security.debian.org/debian-security|mirrors.aliyun.com/debian-security|g' \
        /etc/apt/sources.list; \
    fi; \
    if [ -d /etc/apt/sources.list.d ]; then \
      for f in /etc/apt/sources.list.d/*; do \
        [ -f "$f" ] || continue; \
        sed -i \
          -e 's|deb.debian.org|mirrors.aliyun.com|g' \
          -e 's|security.debian.org/debian-security|mirrors.aliyun.com/debian-security|g' \
          "$f"; \
      done; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# 完整 monorepo（配合仓库根 .dockerignore 排除 node_modules / dist / .turbo）
COPY web/ .

RUN corepack enable && corepack prepare pnpm@10.22.0 --activate

RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm config set store-dir /pnpm/store \
    && pnpm install --frozen-lockfile

RUN pnpm run build --filter=@vben/playground

# ---------- 阶段 2：运行镜像（mydumper 基础 + nginx + Flask） ----------
FROM registry.cn-guangzhou.aliyuncs.com/devyunze/mydumper:v0.21.3-2

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN set -eux; \
    if command -v apk >/dev/null 2>&1; then \
      apk add --no-cache \
        python3 \
        py3-pip \
        bash \
        ca-certificates \
        tzdata \
        mariadb-client \
        nginx; \
      (apk add --no-cache cronie || apk add --no-cache dcron || true); \
    elif command -v apt-get >/dev/null 2>&1; then \
      apt-get update; \
      apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        bash \
        ca-certificates \
        tzdata \
        default-mysql-client \
        nginx; \
      (apt-get install -y --no-install-recommends cron || true); \
      rm -rf /var/lib/apt/lists/*; \
    elif command -v dnf >/dev/null 2>&1; then \
      dnf install -y --disablerepo=mydumper \
        python3 \
        python3-pip \
        bash \
        ca-certificates \
        tzdata \
        mariadb \
        nginx; \
      (dnf install -y --disablerepo=mydumper cronie || true); \
      dnf clean all; \
    elif command -v yum >/dev/null 2>&1; then \
      yum install -y --disablerepo=mydumper \
        python3 \
        python3-pip \
        bash \
        ca-certificates \
        tzdata \
        mariadb \
        nginx; \
      (yum install -y --disablerepo=mydumper cronie || true); \
      yum clean all; \
    else \
      echo "No supported package manager found"; \
      exit 1; \
    fi; \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime; \
    echo "Asia/Shanghai" > /etc/timezone; \
    python3 -m pip install --no-cache-dir flask werkzeug cryptography; \
    rm -rf /etc/nginx/conf.d/default.conf 2>/dev/null || true; \
    mkdir -p /var/log/nginx /var/lib/nginx/tmp

WORKDIR /app/backup

COPY back/db_instance_api.py /app/backup/db_instance_api.py
COPY back/scripts /app/backup/scripts

RUN chmod +x /app/backup/scripts/*.sh

COPY --from=frontend-builder /app/web/playground/dist /usr/share/nginx/html
COPY web/scripts/deploy/nginx.fullstack.conf /etc/nginx/nginx.conf

EXPOSE 5555

CMD ["sh", "-c", "(crond -f -l 8 || cron -f || /usr/sbin/crond -n) & python3 /app/backup/db_instance_api.py & exec nginx -g \"daemon off;\""]
