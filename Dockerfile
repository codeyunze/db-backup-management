# 数据库备份管理镜像
# 基于官方 mysql:8.0.45-debian，内置 MySQL 8.0.45 客户端（mysql/mysqldump/mysqlbinlog）和 Python3，
# 专注提供备份/还原与增量备份 API，不再额外处理客户端依赖。
#
# 挂载说明：
#   -v /宿主机/脚本目录:/scripts          备份与还原脚本（可覆盖镜像内脚本）
#   -v /宿主机/备份目录:/data/backup/mysql 备份文件持久化
FROM mysql:8.0.45-debian

# 安装 Python3、Flask、bash、tzdata 等运行 API 所需组件
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-flask \
        bash \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && mkdir -p /scripts /data/backup/mysql

# 复制备份与还原脚本到镜像（仓库中位于 scripts/ 目录）
COPY scripts/mysql-backup-schema-data.sh /scripts/
COPY scripts/mysql-restore-schema-data.sh /scripts/
COPY scripts/mysql-backup-incremental.sh /scripts/

# 赋予执行权限
RUN chmod +x /scripts/mysql-backup-schema-data.sh /scripts/mysql-restore-schema-data.sh /scripts/mysql-backup-incremental.sh

# 复制 API 与 Web 管理界面
COPY api/management.py /app/
COPY api/templates /app/templates

WORKDIR /app

# 时区：北京时间（Asia/Shanghai, UTC+8）
ENV TZ=Asia/Shanghai

# 暴露 API 端口
EXPOSE 8081

# 挂载点说明（运行时通过 -v 挂载）
# /scripts              - 备份与还原脚本
# /data/backup/mysql    - 备份文件存储目录
VOLUME ["/scripts", "/data/backup/mysql"]

# 启动 API 服务，提供 POST /db/backup /db/restore /db/backup-incremental 等接口
CMD ["python3", "management.py"]

# 数据库备份管理镜像
# 基于 debian:bookworm-slim + MySQL 官方 8.0.36 客户端二进制（mysql / mysqldump / mysqlbinlog）
# 支持 amd64 / arm64，多架构构建；无需宿主机预装 MySQL。
#
# 挂载说明：
#   -v /宿主机/脚本目录:/scripts          备份与还原脚本（可覆盖镜像内脚本）
#   -v /宿主机/备份目录:/data/backup/mysql 备份文件持久化
FROM debian:bookworm-slim

ARG APT_MIRROR=default

# 可选：切换到国内 Debian 镜像源（仅影响 Debian 包安装）
RUN if [ "$APT_MIRROR" = "aliyun" ]; then \
        rm -f /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
        echo "deb http://mirrors.aliyun.com/debian bookworm main" > /etc/apt/sources.list \
        && echo "deb http://mirrors.aliyun.com/debian bookworm-updates main" >> /etc/apt/sources.list \
        && echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main" >> /etc/apt/sources.list; \
    elif [ "$APT_MIRROR" = "tsinghua" ]; then \
        rm -f /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
        echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm main" > /etc/apt/sources.list \
        && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm-updates main" >> /etc/apt/sources.list \
        && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main" >> /etc/apt/sources.list; \
    fi

# 如有本地 tools 目录，预先拷贝到镜像中（若存在对应 tar.xz 则优先使用本地，避免重复下载）
COPY tools/ /tools/

# 安装基础依赖 + 从 MySQL 官方 CDN 或本地 tools 下载对应架构的 minimal 客户端（含 mysql / mysqldump / mysqlbinlog）
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates wget xz-utils bash python3 python3-flask tzdata libncurses5; \
    rm -rf /var/lib/apt/lists/*; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) \
        local_tar="/tools/mysql-8.0.36-linux-glibc2.17-x86_64-minimal.tar.xz"; \
        remote_url="https://cdn.mysql.com/archives/mysql-8.0/mysql-8.0.36-linux-glibc2.17-x86_64-minimal.tar.xz" \
        ;; \
      arm64) \
        local_tar="/tools/mysql-8.0.36-linux-glibc2.17-aarch64-minimal.tar.xz"; \
        remote_url="https://cdn.mysql.com/archives/mysql-8.0/mysql-8.0.36-linux-glibc2.17-aarch64-minimal.tar.xz" \
        ;; \
      *) echo "不支持的架构: $arch"; exit 1 ;; \
    esac; \
    mkdir -p /opt/mysql; \
    cd /opt/mysql; \
    if [ -f "$local_tar" ]; then \
      cp "$local_tar" mysql-minimal.tar.xz; \
    else \
      wget -q "$remote_url" -O mysql-minimal.tar.xz; \
    fi; \
    tar -xf mysql-minimal.tar.xz --strip-components=1; \
    # 使用 MySQL 官方客户端二进制，统一放在 /usr/local/mysql/bin，并在 PATH 前置该目录 \
    mkdir -p /usr/local/mysql/bin; \
    cp bin/mysql bin/mysqldump bin/mysqlbinlog /usr/local/mysql/bin/; \
    chmod +x /usr/local/mysql/bin/mysql /usr/local/mysql/bin/mysqldump /usr/local/mysql/bin/mysqlbinlog; \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime; \
    echo "Asia/Shanghai" > /etc/timezone; \
    mkdir -p /scripts /data/backup/mysql

ENV PATH="/usr/local/mysql/bin:${PATH}"

# 复制备份与还原脚本到镜像（仓库中位于 scripts/ 目录）
COPY scripts/mysql-backup-schema-data.sh /scripts/
COPY scripts/mysql-restore-schema-data.sh /scripts/
COPY scripts/mysql-backup-incremental.sh /scripts/

# 赋予执行权限
RUN chmod +x /scripts/mysql-backup-schema-data.sh /scripts/mysql-restore-schema-data.sh /scripts/mysql-backup-incremental.sh

# 复制 API 与 Web 管理界面
COPY api/management.py /app/
COPY api/templates /app/templates

WORKDIR /app

# 时区：北京时间（Asia/Shanghai, UTC+8）
ENV TZ=Asia/Shanghai

# 暴露 API 端口
EXPOSE 8081

# 挂载点说明（运行时通过 -v 挂载）
# /scripts              - 备份与还原脚本
# /data/backup/mysql    - 备份文件存储目录
VOLUME ["/scripts", "/data/backup/mysql"]

# 启动 API 服务，提供 POST /db/backup /db/restore /db/backup-incremental 等接口
CMD ["python3", "management.py"]

# 数据库备份管理镜像
# 基于 debian:bookworm-slim，在构建时从 MySQL 官方 CDN 下载 MySQL 8.0.36 minimal 客户端
# （包含 mysql / mysqldump / mysqlbinlog），支持 amd64 / arm64 多架构。
# 挂载说明：
#   -v /宿主机/脚本目录:/scripts          备份与还原脚本（可覆盖镜像内脚本）
#   -v /宿主机/备份目录:/data/backup/mysql 备份文件持久化
FROM debian:bookworm-slim

ARG APT_MIRROR=default

# 可选：切换到国内 Debian 镜像源（仅影响 Debian 包安装）
RUN if [ "$APT_MIRROR" = "aliyun" ]; then \
        rm -f /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
        echo "deb http://mirrors.aliyun.com/debian bookworm main" > /etc/apt/sources.list \
        && echo "deb http://mirrors.aliyun.com/debian bookworm-updates main" >> /etc/apt/sources.list \
        && echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main" >> /etc/apt/sources.list; \
    elif [ "$APT_MIRROR" = "tsinghua" ]; then \
        rm -f /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
        echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm main" > /etc/apt/sources.list \
        && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm-updates main" >> /etc/apt/sources.list \
        && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main" >> /etc/apt/sources.list; \
    fi

# 如果存在本地 @tools 目录，则预先拷贝到镜像中（若有对应 tar 包则优先使用本地，避免重复下载）
COPY tools/ /tools/

# 安装基础依赖 + 从 MySQL 官方 CDN 下载对应架构的 minimal 客户端（含 mysqlbinlog）
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates wget xz-utils bash python3 python3-flask tzdata default-mysql-client; \
    rm -rf /var/lib/apt/lists/*; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) \
        local_tar="/tools/mysql-8.0.36-linux-glibc2.17-x86_64-minimal.tar"; \
        remote_url="https://cdn.mysql.com/archives/mysql-8.0/mysql-8.0.36-linux-glibc2.17-x86_64-minimal.tar" \
        ;; \
      arm64) \
        local_tar="/tools/mysql-8.0.36-linux-glibc2.17-aarch64-minimal.tar"; \
        remote_url="https://cdn.mysql.com/archives/mysql-8.0/mysql-8.0.36-linux-glibc2.17-aarch64-minimal.tar" \
        ;; \
      *) echo "不支持的架构: $arch"; exit 1 ;; \
    esac; \
    mkdir -p /opt/mysql; \
    cd /opt/mysql; \
    if [ -f "$local_tar" ]; then \
      cp "$local_tar" mysql-minimal.tar; \
    else \
      wget -q "$remote_url" -O mysql-minimal.tar; \
    fi; \
    tar -xf mysql-minimal.tar --strip-components=1; \
    # 只使用官方 MySQL 提供的 mysqlbinlog，其它客户端命令使用系统 default-mysql-client 提供的版本 \
    cp bin/mysqlbinlog /usr/local/bin/; \
    chmod +x /usr/local/bin/mysqlbinlog; \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime; \
    echo "Asia/Shanghai" > /etc/timezone; \
    mkdir -p /scripts /data/backup/mysql

# 复制备份与还原脚本到镜像（仓库中位于 scripts/ 目录）
COPY scripts/mysql-backup-schema-data.sh /scripts/
COPY scripts/mysql-restore-schema-data.sh /scripts/
COPY scripts/mysql-backup-incremental.sh /scripts/

# 赋予执行权限
RUN chmod +x /scripts/mysql-backup-schema-data.sh /scripts/mysql-restore-schema-data.sh /scripts/mysql-backup-incremental.sh

# 复制 API 与 Web 管理界面
COPY api/management.py /app/
COPY api/templates /app/templates

WORKDIR /app

# 时区：北京时间（Asia/Shanghai, UTC+8）
ENV TZ=Asia/Shanghai

# 暴露 API 端口
EXPOSE 8081

# 挂载点说明（运行时通过 -v 挂载）
# /scripts              - 备份与还原脚本
# /data/backup/mysql    - 备份文件存储目录
VOLUME ["/scripts", "/data/backup/mysql"]

# 启动 API 服务，提供 POST /db/backup /db/restore /db/backup-incremental 等接口
CMD ["python3", "management.py"]

# 数据库备份管理镜像
# 基于 debian:bookworm-slim，在构建时从 MySQL 官方 CDN 下载客户端二进制
# （mysql/mysqlbinlog/mysqldump），支持 amd64 / arm64 多架构。
# 挂载说明：
#   -v /宿主机/脚本目录:/scripts          备份与还原脚本（可覆盖镜像内脚本）
#   -v /宿主机/备份目录:/data/backup/mysql 备份文件持久化
FROM debian:bookworm-slim

ARG APT_MIRROR=default

# 可选：切换到国内 Debian 镜像源（仅影响 Debian 包安装）
RUN if [ "$APT_MIRROR" = "aliyun" ]; then \
        rm -f /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
        echo "deb http://mirrors.aliyun.com/debian bookworm main" > /etc/apt/sources.list \
        && echo "deb http://mirrors.aliyun.com/debian bookworm-updates main" >> /etc/apt/sources.list \
        && echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main" >> /etc/apt/sources.list; \
    elif [ "$APT_MIRROR" = "tsinghua" ]; then \
        rm -f /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
        echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm main" > /etc/apt/sources.list \
        && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm-updates main" >> /etc/apt/sources.list \
        && echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main" >> /etc/apt/sources.list; \
    fi

# 如有本地 tools 目录，预先拷贝到镜像中（若存在对应 tar 包则优先使用本地，避免重复下载）
COPY tools/ /tools/

# 安装基础依赖 + 从 MySQL 官方 CDN 或本地 tools 下载对应架构的 minimal 客户端（含 mysqlbinlog）
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates wget xz-utils bash python3 python3-flask tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) \
        local_tar="/tools/mysql-8.0.36-linux-glibc2.17-x86_64-minimal.tar.xz"; \
        remote_url="https://cdn.mysql.com/archives/mysql-8.0/mysql-8.0.36-linux-glibc2.17-x86_64-minimal.tar.xz" \
        ;; \
      arm64) \
        local_tar="/tools/mysql-8.0.36-linux-glibc2.17-aarch64-minimal.tar.xz"; \
        remote_url="https://cdn.mysql.com/archives/mysql-8.0/mysql-8.0.36-linux-glibc2.17-aarch64-minimal.tar.xz" \
        ;; \
      *) echo "不支持的架构: $arch"; exit 1 ;; \
    esac; \
    mkdir -p /opt/mysql; \
    cd /opt/mysql; \
    if [ -f "$local_tar" ]; then \
      cp "$local_tar" mysql-minimal.tar; \
    else \
      wget -q "$remote_url" -O mysql-minimal.tar; \
    fi; \
    tar -xf mysql-minimal.tar --strip-components=1; \
    cp bin/mysqlbinlog bin/mysql bin/mysqldump /usr/local/bin/; \
    chmod +x /usr/local/bin/mysqlbinlog /usr/local/bin/mysql /usr/local/bin/mysqldump; \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime; \
    echo "Asia/Shanghai" > /etc/timezone; \
    mkdir -p /scripts /data/backup/mysql

# 复制备份与还原脚本到镜像（仓库中位于 scripts/ 目录）
COPY scripts/mysql-backup-schema-data.sh /scripts/
COPY scripts/mysql-restore-schema-data.sh /scripts/
COPY scripts/mysql-backup-incremental.sh /scripts/

# 赋予执行权限
RUN chmod +x /scripts/mysql-backup-schema-data.sh /scripts/mysql-restore-schema-data.sh /scripts/mysql-backup-incremental.sh

# 复制 API 与 Web 管理界面
COPY api/management.py /app/
COPY api/templates /app/templates

WORKDIR /app

# 时区：北京时间（Asia/Shanghai, UTC+8）
ENV TZ=Asia/Shanghai

# 暴露 API 端口
EXPOSE 8081

# 挂载点说明（运行时通过 -v 挂载）
# /scripts              - 备份与还原脚本
# /data/backup/mysql    - 备份文件存储目录
VOLUME ["/scripts", "/data/backup/mysql"]

# 启动 API 服务，提供 POST /db/backup /db/restore /db/backup-incremental 等接口
CMD ["python3", "management.py"]
