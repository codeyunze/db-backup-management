#!/bin/bash

# 基于 binlog 的增量备份脚本（按数据库 & 全量备份链路）
#
# 设计目标（与 OPTIMIZATION_PLAN.md 对应）：
# - 在一次已完成的全量备份基础上，生成「从某个 binlog 起点开始」的增量 SQL（changes.sql）
# - 增量与全量绑定：必须指定所属全量备份目录，用于后续恢复链路管理
# - 当前版本聚焦于功能打通，暂不处理跨多个 binlog 文件的复杂场景（留待后续迭代）

# =============== 配置区（默认值，可通过命令行参数覆盖） ===============
DB_HOST="127.0.0.1"
DB_PORT="3306"
DB_USER="root"
DB_PASS="123456"
DB_NAME="db_name"

# 增量备份根目录（默认与全量备份同一根下的 incremental/ 子目录）
BACKUP_ROOT="/data/backup/mysql"

# =============== 帮助说明 ===============
show_usage() {
    echo "用法: $0 [选项] -F <full_backup_dir> --start-file <binlog_file> --start-pos <pos>"
    echo ""
    echo "基于 MySQL binlog 生成增量备份（changes.sql），并与指定的全量备份目录绑定。"
    echo ""
    echo "必选参数："
    echo "  -F, --full-backup-dir  全量备份目录路径，如: -F /data/backup/mysql/mall_20260302_222859"
    echo "      --start-file       binlog 起始文件名，如: --start-file master-bin.000014"
    echo "      --start-pos        binlog 起始位置（数字），如: --start-pos 82529784"
    echo ""
    echo "可选参数："
    echo "  -H, --host             数据库主机，默认 127.0.0.1"
    echo "  -P, --port             数据库端口，默认 3306"
    echo "  -u, --user             数据库用户，默认 root"
    echo "  -p, --password         数据库密码，默认 123456"
    echo "  -d, --database         数据库名（用于 --database 过滤），默认 db_name"
    echo "  -b, --backup-root      增量备份根目录（父目录），默认 /data/backup/mysql"
    echo "      --stop-datetime    binlog 截止时间（可选），如: \"2026-03-02 23:59:59\""
    echo "  -h, --help             显示此帮助信息"
    echo ""
    echo "示例："
    echo "  $0 -H 10.0.0.1 -u backup -p pass -d mall \\"
    echo "     -F /data/backup/mysql/mall_20260302_222859 \\"
    echo "     --start-file master-bin.000014 --start-pos 82529784"
    echo ""
    echo "说明："
    echo "  - 起始 binlog 文件/位置通常来自上一次全量备份记录的 meta/tables-binlog.json 或"
    echo "    手工查询 SHOW MASTER STATUS 的结果。"
    echo "  - 当前实现假定从单个 binlog 文件中提取增量，跨文件场景留待后续迭代。"
}

# =============== 解析命令行参数 ===============
FULL_BACKUP_DIR=""
START_FILE=""
START_POS=""
STOP_DATETIME=""

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            show_usage
            exit 0
            ;;
        -H|--host)
            [ -n "${2:-}" ] || { echo "错误: -H/--host 需要指定主机"; exit 1; }
            DB_HOST="$2"
            shift 2
            ;;
        -P|--port)
            [ -n "${2:-}" ] || { echo "错误: -P/--port 需要指定端口"; exit 1; }
            DB_PORT="$2"
            shift 2
            ;;
        -u|--user)
            [ -n "${2:-}" ] || { echo "错误: -u/--user 需要指定用户"; exit 1; }
            DB_USER="$2"
            shift 2
            ;;
        -p|--password)
            [ -n "${2:-}" ] || { echo "错误: -p/--password 需要指定密码"; exit 1; }
            DB_PASS="$2"
            shift 2
            ;;
        -d|--database)
            [ -n "${2:-}" ] || { echo "错误: -d/--database 需要指定数据库名"; exit 1; }
            DB_NAME="$2"
            shift 2
            ;;
        -b|--backup-root)
            [ -n "${2:-}" ] || { echo "错误: -b/--backup-root 需要指定目录"; exit 1; }
            BACKUP_ROOT="$2"
            shift 2
            ;;
        -F|--full-backup-dir)
            [ -n "${2:-}" ] || { echo "错误: -F/--full-backup-dir 需要指定全量备份目录"; exit 1; }
            FULL_BACKUP_DIR="$2"
            shift 2
            ;;
        --start-file)
            [ -n "${2:-}" ] || { echo "错误: --start-file 需要指定 binlog 文件名"; exit 1; }
            START_FILE="$2"
            shift 2
            ;;
        --start-pos)
            [ -n "${2:-}" ] || { echo "错误: --start-pos 需要指定 binlog 位置"; exit 1; }
            START_POS="$2"
            shift 2
            ;;
        --stop-datetime)
            [ -n "${2:-}" ] || { echo "错误: --stop-datetime 需要指定时间字符串"; exit 1; }
            STOP_DATETIME="$2"
            shift 2
            ;;
        *)
            echo "错误: 未知选项 $1"
            echo "使用 $0 -h 查看帮助"
            exit 1
            ;;
    esac
done

if [ -z "${FULL_BACKUP_DIR}" ]; then
    echo "错误: 必须通过 -F/--full-backup-dir 指定所属全量备份目录。"
    exit 1
fi
if [ -z "${START_FILE}" ] || [ -z "${START_POS}" ]; then
    echo "错误: 必须通过 --start-file 与 --start-pos 指定 binlog 起始位置。"
    exit 1
fi

if [ ! -d "${FULL_BACKUP_DIR}" ]; then
    echo "错误: 全量备份目录不存在: ${FULL_BACKUP_DIR}"
    exit 1
fi

# =============== 前置校验：binlog / binlog_format / 库名一致性 ===============

# 通过环境变量传递密码，避免在命令行上暴露
export MYSQL_PWD="${DB_PASS}"
MYSQL_CMD="mysql -h${DB_HOST} -P${DB_PORT} -u${DB_USER} -N"

# 1）检查是否开启 binlog
BINLOG_ENABLED=$(${MYSQL_CMD} -e "SHOW VARIABLES LIKE 'log_bin';" 2>/dev/null | awk 'NR==1{print $2}')
if [ "${BINLOG_ENABLED}" != "ON" ]; then
    echo "错误: 当前 MySQL 实例未开启 binlog（log_bin=OFF），无法执行增量备份。"
    echo "请在 MySQL 配置中开启 binlog（推荐 ROW 模式）后，再尝试增量备份。"
    exit 1
fi

# 2）检查 binlog_format 是否为 ROW
BINLOG_FORMAT=$(${MYSQL_CMD} -e "SHOW VARIABLES LIKE 'binlog_format';" 2>/dev/null | awk 'NR==1{print $2}')
if [ "${BINLOG_FORMAT}" != "ROW" ]; then
    echo "错误: 当前 binlog_format=${BINLOG_FORMAT:-未知}，本工具的增量备份仅支持 ROW 模式。"
    echo "请将 binlog_format 调整为 ROW 后，再尝试增量备份。"
    exit 1
fi

# 3）检查所属全量备份目录中的库名是否与本次增量数据库名一致
BASE_NAME="$(basename "${FULL_BACKUP_DIR}")"
# 全量目录命名为 <db>_YYYYMMDD_HHMMSS，去掉最后两个下划线片段即为库名
BASE_DB="${BASE_NAME%_*_*}"
if [ -n "${BASE_DB}" ] && [ "${BASE_DB}" != "${DB_NAME}" ]; then
    echo "错误: 全量备份目录中的库名 (${BASE_DB}) 与本次增量备份的数据库名 (${DB_NAME}) 不一致，无法执行增量备份。"
    echo "请确认选择了正确的全量备份目录，或在同一数据库名下执行全量 + 增量链路。"
    exit 1
fi

# =============== 增量备份目录准备 ===============
set -e

# 增量目录改为直接放在对应全量备份目录下的 incremental 子目录中：
#   <FULL_BACKUP_DIR>/incremental/<DB_NAME>_inc_YYYYMMDD_HHMMSS
INCR_ROOT="${FULL_BACKUP_DIR%/}/incremental"
TS="$(date +%Y%m%d_%H%M%S)"
INCR_DIR="${INCR_ROOT}/${DB_NAME}_inc_${TS}"

mkdir -p "${INCR_DIR}/meta"

# 日志：不再单独使用 incremental.log，而是直接写入所属全量备份目录下的 backup.log
FULL_LOG="${FULL_BACKUP_DIR%/}/backup.log"
LOG_FILE="${FULL_LOG}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo ""
echo "======================================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始增量备份数据库: ${DB_NAME}"
echo "  - 全量备份目录: ${FULL_BACKUP_DIR}"
echo "  - 起始 binlog: ${START_FILE}:${START_POS}"
[ -n "${STOP_DATETIME}" ] && echo "  - 截止时间: ${STOP_DATETIME}"

# =============== 使用 mysqlbinlog 提取增量 ===============

# 优先使用绝对路径，避免 API 子进程环境中 PATH 未包含 mysqlbinlog 导致 command not found
MYSQLBINLOG_BIN=""
if command -v mysqlbinlog >/dev/null 2>&1; then
  MYSQLBINLOG_BIN="mysqlbinlog"
elif [ -x "/usr/bin/mysqlbinlog" ]; then
  MYSQLBINLOG_BIN="/usr/bin/mysqlbinlog"
elif [ -x "/usr/bin/mariadb-binlog" ]; then
  MYSQLBINLOG_BIN="/usr/bin/mariadb-binlog"
else
  echo "错误: 未找到 mysqlbinlog，请安装 MySQL/MariaDB 客户端（如 default-mysql-client）。"
  exit 1
fi

CHANGES_SQL="${INCR_DIR}/changes.sql"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 正在通过 mysqlbinlog 提取增量变更..."

export MYSQL_PWD="${DB_PASS}"

MYSQLBINLOG_CMD=("${MYSQLBINLOG_BIN}"
  --read-from-remote-server
  --host="${DB_HOST}"
  --port="${DB_PORT}"
  --user="${DB_USER}"
  --raw=false
  --verbose
  --start-position="${START_POS}"
  --database="${DB_NAME}"
)

if [ -n "${STOP_DATETIME}" ]; then
  MYSQLBINLOG_CMD+=(--stop-datetime="${STOP_DATETIME}")
fi

MYSQLBINLOG_CMD+=("${START_FILE}")

if ! "${MYSQLBINLOG_CMD[@]}" > "${CHANGES_SQL}"; then
  echo "错误: mysqlbinlog 提取增量失败。"
  exit 1
fi

echo "  - 增量 SQL 文件: ${CHANGES_SQL}"

# =============== 记录起止位点元数据 ===============

META_FROM="${INCR_DIR}/meta/binlog_from.json"
META_TO="${INCR_DIR}/meta/binlog_to.json"

FROM_TS="$(date '+%Y-%m-%dT%H:%M:%S%z')"

cat > "${META_FROM}" <<EOF
{
  "binlog_file": "${START_FILE}",
  "binlog_pos": ${START_POS},
  "recorded_at": "${FROM_TS}",
  "base_full_backup_dir": "${FULL_BACKUP_DIR}",
  "database": "${DB_NAME}"
}
EOF

# 结束位点：使用 SHOW MASTER STATUS 作为当前链条的终点（用于下一次增量的起点）
END_FILE=""
END_POS=""
read END_FILE END_POS <<EOF
$(mysql -h"${DB_HOST}" -P"${DB_PORT}" -u"${DB_USER}" -p"${DB_PASS}" -N -e "SHOW MASTER STATUS" 2>/dev/null | awk '{print $1, $2}' | head -n1)
EOF

TO_TS="$(date '+%Y-%m-%dT%H:%M:%S%z')"

if [ -n "${END_FILE}" ] && [ -n "${END_POS}" ]; then
  cat > "${META_TO}" <<EOF
{
  "binlog_file": "${END_FILE}",
  "binlog_pos": ${END_POS},
  "recorded_at": "${TO_TS}",
  "database": "${DB_NAME}"
}
EOF
  echo "  - 当前 binlog 结束位点: ${END_FILE}:${END_POS}"
else
  echo "  警告: 无法获取当前 binlog 结束位点（SHOW MASTER STATUS 失败），binlog_to.json 未生成。"
fi

# 沿用全量备份的表过滤条件，供恢复与后续逻辑使用（增量必须与全量一致）
FULL_OPTIONS="${FULL_BACKUP_DIR}/meta/backup-options.json"
if [ -f "${FULL_OPTIONS}" ]; then
  cp "${FULL_OPTIONS}" "${INCR_DIR}/meta/backup-options.json"
  echo "  - 已沿用全量备份的表过滤条件: meta/backup-options.json"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 增量备份完成。"
echo "  - 增量目录: ${INCR_DIR}"
echo "  - 日志: ${LOG_FILE}"

