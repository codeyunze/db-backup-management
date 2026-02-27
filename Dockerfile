# 数据库备份管理镜像
# 包含：mysql/mysqldump、Python3，可执行备份/还原脚本
# 挂载说明：
#   -v /宿主机/脚本目录:/scripts          备份与还原脚本（可覆盖镜像内脚本）
#   -v /宿主机/备份目录:/data/backup/mysql 备份文件持久化
#
# 构建时若官方源 502，可指定国内镜像：docker build --build-arg APT_MIRROR=aliyun -t ...
FROM debian:bookworm-slim

ARG APT_MIRROR=default

# 可选：切换到国内镜像以规避 502 Bad Gateway
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

# 安装 mysql-client（含 mysql、mysqldump）、python3-flask、bash、tzdata 等
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        default-mysql-client \
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

# 赋予执行权限
RUN chmod +x /scripts/mysql-backup-schema-data.sh /scripts/mysql-restore-schema-data.sh

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

# 启动 API 服务，提供 POST /db/backup 和 POST /db/restore 接口
CMD ["python3", "management.py"]
