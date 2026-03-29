#!/usr/bin/env bash
#
# 将增量目录（binlog/*.sql）回放到目标数据库。
#

set -eo pipefail

DB_HOST="127.0.0.1"
DB_PORT="3306"
DB_USER="root"
DB_PASS=""
TARGET_DB=""
INCREMENT_DIR=""

show_usage() {
  echo "用法: $0 -H host -P port -u user -p pass -d target_db -s increment_dir"
}

while [ $# -gt 0 ]; do
  case "${1}" in
    -H | --host) DB_HOST="${2:?}"; shift 2 ;;
    -P | --port) DB_PORT="${2:?}"; shift 2 ;;
    -u | --user) DB_USER="${2:?}"; shift 2 ;;
    -p | --password) DB_PASS="${2:?}"; shift 2 ;;
    -d | --database) TARGET_DB="${2:?}"; shift 2 ;;
    -s | --source-dir) INCREMENT_DIR="${2:?}"; shift 2 ;;
    -h | --help) show_usage; exit 0 ;;
    *) echo "未知参数: ${1}"; show_usage; exit 1 ;;
  esac
done

[ -n "${TARGET_DB}" ] || { echo "错误: 目标数据库不能为空"; exit 1; }
[ -n "${INCREMENT_DIR}" ] || { echo "错误: 增量目录不能为空"; exit 1; }
[ -d "${INCREMENT_DIR}" ] || { echo "错误: 增量目录不存在: ${INCREMENT_DIR}"; exit 1; }

BINLOG_DIR="${INCREMENT_DIR}/binlog"
[ -d "${BINLOG_DIR}" ] || {
  echo "[$(date '+%F %T')] 未找到 binlog 目录，视为无增量变更，跳过: ${BINLOG_DIR}"
  exit 0
}

command -v mysql >/dev/null 2>&1 || { echo "错误: 未找到 mysql 客户端"; exit 1; }

mapfile -t SQL_FILES < <(ls -1 "${BINLOG_DIR}"/*.sql 2>/dev/null | sort)
[ ${#SQL_FILES[@]} -gt 0 ] || {
  echo "[$(date '+%F %T')] 未找到可回放的 binlog sql 文件，视为无增量变更，跳过"
  exit 0
}

export MYSQL_PWD="${DB_PASS}"
for sql in "${SQL_FILES[@]}"; do
  # 防呆：若文件是 mysqlbinlog -v/DECODE-ROWS 生成的“注释可读格式”，
  # mysql 执行会成功但不会真正变更数据。此处直接报错，避免静默失败。
  if rg -n "^### " "${sql}" >/dev/null 2>&1 && ! rg -n "^BINLOG '" "${sql}" >/dev/null 2>&1; then
    echo "错误: 检测到不可回放的 binlog 文件格式（仅含 ### 注释，无 BINLOG 事件）: ${sql}"
    echo "请重新执行一次增量备份（使用已修复的导出脚本）后再还原。"
    exit 1
  fi
  echo "[$(date '+%F %T')] 回放增量: ${sql}"
  mysql "-h${DB_HOST}" "-P${DB_PORT}" "-u${DB_USER}" "${TARGET_DB}" <"${sql}"
done
unset MYSQL_PWD

echo "[$(date '+%F %T')] 增量回放完成。"
