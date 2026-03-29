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
        mariadb-client; \
      (apk add --no-cache cronie || apk add --no-cache dcron || true); \
    elif command -v apt-get >/dev/null 2>&1; then \
      apt-get update; \
      apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        bash \
        ca-certificates \
        tzdata \
        default-mysql-client; \
      (apt-get install -y --no-install-recommends cron || true); \
      rm -rf /var/lib/apt/lists/*; \
    elif command -v dnf >/dev/null 2>&1; then \
      dnf install -y --disablerepo=mydumper \
        python3 \
        python3-pip \
        bash \
        ca-certificates \
        tzdata \
        mariadb; \
      (dnf install -y --disablerepo=mydumper cronie || true); \
      dnf clean all; \
    elif command -v yum >/dev/null 2>&1; then \
      yum install -y --disablerepo=mydumper \
        python3 \
        python3-pip \
        bash \
        ca-certificates \
        tzdata \
        mariadb; \
      (yum install -y --disablerepo=mydumper cronie || true); \
      yum clean all; \
    else \
      echo "No supported package manager found"; \
      exit 1; \
    fi; \
    ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime; \
    echo "Asia/Shanghai" > /etc/timezone; \
    python3 -m pip install --no-cache-dir flask werkzeug cryptography

WORKDIR /app/backup

COPY db_instance_api.py /app/backup/db_instance_api.py
COPY scripts /app/backup/scripts

RUN chmod +x /app/backup/scripts/mysql-backup-mydumper.sh \
    /app/backup/scripts/mysql-restore-mydumper.sh

EXPOSE 8081

CMD ["sh", "-c", "(crond -f -l 8 || cron -f || /usr/sbin/crond -n) & exec python3 db_instance_api.py"]
