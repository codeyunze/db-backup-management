#!/usr/bin/env bash
#
# 基于 mysqlbinlog 的增量备份：
# - 起点：--start-log-file/--start-log-pos
# - 终点：--end-log-file/--end-log-pos
# - 结果：会话目录下生成 binlog/*.sql 与 meta/increment-info.json
#

set -eo pipefail

DB_HOST="127.0.0.1"
DB_PORT="3306"
DB_USER="root"
DB_PASS=""
DB_NAME=""
BACKUP_ROOT="/app/backup/data"
SESSION_DIR=""

START_LOG_FILE=""
START_LOG_POS=""
END_LOG_FILE=""
END_LOG_POS=""
FULL_BACKUP_FILE_ID=""

MYSQL_BIN="${MYSQL_BIN:-mysql}"
MYSQLBINLOG_BIN="${MYSQLBINLOG_BIN:-mysqlbinlog}"

show_usage() {
  echo "用法: $0 [选项]"
  echo "  -H, --host"
  echo "  -P, --port"
  echo "  -u, --user"
  echo "  -p, --password"
  echo "  -d, --database"
  echo "  -b, --backup-dir"
  echo "      --session-dir"
  echo "      --start-log-file / --start-log-pos"
  echo "      --end-log-file / --end-log-pos"
  echo "      --full-backup-file-id"
}

while [ $# -gt 0 ]; do
  case "${1}" in
    -H | --host) DB_HOST="${2:?}"; shift 2 ;;
    -P | --port) DB_PORT="${2:?}"; shift 2 ;;
    -u | --user) DB_USER="${2:?}"; shift 2 ;;
    -p | --password) DB_PASS="${2:?}"; shift 2 ;;
    -d | --database) DB_NAME="${2:?}"; shift 2 ;;
    -b | --backup-dir) BACKUP_ROOT="${2:?}"; shift 2 ;;
    --session-dir) SESSION_DIR="${2:?}"; shift 2 ;;
    --start-log-file) START_LOG_FILE="${2:?}"; shift 2 ;;
    --start-log-pos) START_LOG_POS="${2:?}"; shift 2 ;;
    --end-log-file) END_LOG_FILE="${2:?}"; shift 2 ;;
    --end-log-pos) END_LOG_POS="${2:?}"; shift 2 ;;
    --full-backup-file-id) FULL_BACKUP_FILE_ID="${2:-}"; shift 2 ;;
    -h | --help) show_usage; exit 0 ;;
    *) echo "未知参数: ${1}"; show_usage; exit 1 ;;
  esac
done

[ -n "${DB_NAME}" ] || { echo "错误: --database 不能为空"; exit 1; }
[ -n "${START_LOG_FILE}" ] || { echo "错误: --start-log-file 不能为空"; exit 1; }
[ -n "${END_LOG_FILE}" ] || { echo "错误: --end-log-file 不能为空"; exit 1; }
[ -n "${START_LOG_POS}" ] || { echo "错误: --start-log-pos 不能为空"; exit 1; }
[ -n "${END_LOG_POS}" ] || { echo "错误: --end-log-pos 不能为空"; exit 1; }

if [ -n "${SESSION_DIR}" ]; then
  BACKUP_DIR="${SESSION_DIR}"
else
  BACKUP_DIR="${BACKUP_ROOT}/${DB_NAME}_inc_$(date +%Y%m%d_%H%M%S)"
fi

mkdir -p "${BACKUP_DIR}/meta" "${BACKUP_DIR}/binlog"
LOG_FILE="${BACKUP_DIR}/backup.log"

command -v "${MYSQL_BIN}" >/dev/null 2>&1 || { echo "错误: 未找到 mysql"; exit 1; }
command -v "${MYSQLBINLOG_BIN}" >/dev/null 2>&1 || { echo "错误: 未找到 mysqlbinlog"; exit 1; }

MYSQL_QUERY=("${MYSQL_BIN}" "-h${DB_HOST}" "-P${DB_PORT}" "-u${DB_USER}" "-N")
export MYSQL_PWD="${DB_PASS}"

mapfile -t ALL_LOGS < <("${MYSQL_QUERY[@]}" -e "SHOW BINARY LOGS" | awk '{print $1}')
[ ${#ALL_LOGS[@]} -gt 0 ] || { echo "错误: 未查询到二进制日志"; unset MYSQL_PWD; exit 1; }

start_idx=-1
end_idx=-1
for i in "${!ALL_LOGS[@]}"; do
  if [ "${ALL_LOGS[$i]}" = "${START_LOG_FILE}" ]; then
    start_idx=$i
  fi
  if [ "${ALL_LOGS[$i]}" = "${END_LOG_FILE}" ]; then
    end_idx=$i
  fi
done

[ "${start_idx}" -ge 0 ] || { echo "错误: 起始 binlog 不存在: ${START_LOG_FILE}"; unset MYSQL_PWD; exit 1; }
[ "${end_idx}" -ge 0 ] || { echo "错误: 结束 binlog 不存在: ${END_LOG_FILE}"; unset MYSQL_PWD; exit 1; }
[ "${start_idx}" -le "${end_idx}" ] || { echo "错误: 起始 binlog 晚于结束 binlog"; unset MYSQL_PWD; exit 1; }

echo "[$(date '+%F %T')] 增量备份开始"
echo "  start: ${START_LOG_FILE}:${START_LOG_POS}"
echo "  end  : ${END_LOG_FILE}:${END_LOG_POS}"

for ((i=start_idx; i<=end_idx; i++)); do
  log_name="${ALL_LOGS[$i]}"
  out_file="${BACKUP_DIR}/binlog/${log_name}.sql"
  start_pos=4
  stop_pos=""
  if [ "${log_name}" = "${START_LOG_FILE}" ]; then
    start_pos="${START_LOG_POS}"
  fi
  if [ "${log_name}" = "${END_LOG_FILE}" ]; then
    stop_pos="${END_LOG_POS}"
  fi

  cmd=(
    "${MYSQLBINLOG_BIN}"
    "--read-from-remote-server"
    "-h${DB_HOST}"
    "-P${DB_PORT}"
    "-u${DB_USER}"
    "--password=${DB_PASS}"
    # 导出可直接被 mysql 回放的事件流（包含 BINLOG 事件），
    # 不能使用 --base64-output=DECODE-ROWS/-v（那样会变成注释可读格式，无法真正执行）。
    "--start-position=${start_pos}"
  )
  if [ -n "${stop_pos}" ]; then
    cmd+=("--stop-position=${stop_pos}")
  fi
  cmd+=("${log_name}")

  echo "[$(date '+%F %T')] 导出 ${log_name} (${start_pos}${stop_pos:+ -> ${stop_pos}})"
  "${cmd[@]}" >"${out_file}"
done

cat >"${BACKUP_DIR}/meta/increment-info.json" <<EOF
{
  "tool": "mysqlbinlog",
  "database": "${DB_NAME}",
  "full_backup_file_id": "${FULL_BACKUP_FILE_ID}",
  "start_log_file": "${START_LOG_FILE}",
  "start_log_pos": ${START_LOG_POS},
  "end_log_file": "${END_LOG_FILE}",
  "end_log_pos": ${END_LOG_POS}
}
EOF

echo "[$(date '+%F %T')] 增量备份完成。"
unset MYSQL_PWD
