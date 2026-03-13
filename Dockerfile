# ========== 阶段 1：从 MySQL 官方镜像中提取客户端工具及依赖 ==========
FROM mysql:8.0.45-debian AS mysql-client
RUN set -eux; \
    mkdir -p /out/bin /out/lib; \
    cp /usr/bin/mysql /usr/bin/mysqldump /usr/bin/mysqlbinlog /out/bin/; \
    for bin in /out/bin/*; do \
        ldd "$bin" 2>/dev/null | awk '/=> \//{print $3}' | while read -r so; do \
            [ -f "$so" ] && cp -n "$so" /out/lib/ 2>/dev/null || true; \
        done; \
    done; \
    chmod +x /out/bin/*; \
    echo "MySQL client layer prepared."

# ========== 阶段 2：最小运行镜像（仅 Python + 复制的 MySQL 客户端） ==========
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-flask \
        bash \
        gzip \
        ca-certificates \
        tzdata \
        libaio1 \
        cron \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && mkdir -p /scripts /data/backup/mysql

# 仅复制 MySQL 客户端可执行文件及其依赖库（不含整个 MySQL 服务端）
COPY --from=mysql-client /out/bin/ /usr/local/bin/
COPY --from=mysql-client /out/lib/ /usr/local/lib/mysql-client/
ENV LD_LIBRARY_PATH="/usr/local/lib/mysql-client:${LD_LIBRARY_PATH:-}"

COPY scripts/mysql-backup-schema-data.sh /scripts/
COPY scripts/mysql-restore-schema-data.sh /scripts/
COPY scripts/mysql-backup-incremental.sh /scripts/
COPY scripts/mysql-restore-incremental.sh /scripts/

RUN chmod +x /scripts/mysql-backup-schema-data.sh /scripts/mysql-restore-schema-data.sh \
    /scripts/mysql-backup-incremental.sh /scripts/mysql-restore-incremental.sh

COPY api/management.py /app/
COPY api/templates /app/templates

WORKDIR /app

ENV TZ=Asia/Shanghai

EXPOSE 8081

VOLUME ["/scripts", "/data/backup/mysql"]

CMD ["python3", "management.py"]
