FROM mysql:8.0.45-debian

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

COPY scripts/mysql-backup-schema-data.sh /scripts/
COPY scripts/mysql-restore-schema-data.sh /scripts/
COPY scripts/mysql-backup-incremental.sh /scripts/

RUN chmod +x /scripts/mysql-backup-schema-data.sh /scripts/mysql-restore-schema-data.sh /scripts/mysql-backup-incremental.sh

COPY api/management.py /app/
COPY api/templates /app/templates

WORKDIR /app

ENV TZ=Asia/Shanghai

EXPOSE 8081

VOLUME ["/scripts", "/data/backup/mysql"]

CMD ["python3", "management.py"]
