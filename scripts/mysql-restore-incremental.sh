#!/bin/bash

# 基于全量备份 + binlog 增量的还原脚本
#
# 用途：
#   1. 先调用现有的 mysql-restore-schema-data.sh，将某次全量备份恢复到目标数据库；
#   2. 再按顺序回放一个或多个增量备份目录下的 changes.sql，得到指定时间点的数据。
#
# 注意：
#   - 强烈建议目标数据库为新库（如 <db>_restore_tmp），或在还原前关闭业务写入。
#   - 本脚本不做 binlog 位点校验，只按传入的增量目录顺序依次回放。

# =============== 配置区（默认值，可通过命令行参数覆盖） ===============
DB_HOST="127.0.0.1"
DB_PORT="3306"
DB_USER="root"
DB_PASS="123456"

# 目标数据库名（必填，通过 -d/--database 指定）
TARGET_DB=""

# 全量备份目录（必填，通过 -b/--full-backup-dir 指定）
FULL_BACKUP_DIR=""

# 增量备份目录列表（必填，通过 -i/--incremental-dirs 指定，逗号分隔）
INCR_DIRS_RAW=""

LOG_FILE=""

# =============== 帮助说明 ===============
show_usage() {
  echo "用法: $0 [选项] -b <full_backup_dir> -d <target_db> -i <inc_dir1,inc_dir2,...>"
  echo ""
  echo "基于一次全量备份和若干增量备份目录，恢复 MySQL 数据库到目标库。"
  echo ""
  echo "必选参数："
  echo "  -b, --full-backup-dir   全量备份目录路径，如: -b /data/backup/mysql/mall_20260302_222859"
  echo "  -d, --database          目标数据库名，如: -d mall_restore_tmp"
  echo "  -i, --incremental-dirs  增量备份目录列表，逗号分隔，如:"
  echo "                          -i /data/backup/mysql/incremental/mall_inc_20260302_230537,/data/backup/mysql/incremental/mall_inc_20260302_230716"
  echo ""
  echo "可选参数："
  echo "  -H, --host              数据库主机，默认 127.0.0.1"
  echo "  -P, --port              数据库端口，默认 3306"
  echo "  -u, --user              数据库用户，默认 root"
  echo "  -p, --password          数据库密码，默认 123456"
  echo "  -l, --log-file          还原日志路径（不传则输出到标准输出，可由外部重定向）"
  echo "  -h, --help              显示此帮助信息"
  echo ""
  echo "示例："
  echo "  $0 -H 10.0.0.1 -u backup -p pass -d mall_restore_tmp \\"
  echo "     -b /data/backup/mysql/mall_20260302_222859 \\"
  echo "     -i /data/backup/mysql/incremental/mall_inc_20260302_230537,/data/backup/mysql/incremental/mall_inc_20260302_230716"
  echo ""
  echo "说明："
  echo "  - 会先调用同目录下的 mysql-restore-schema-data.sh 恢复全量备份；"
  echo "  - 然后按顺序对传入的增量目录依次执行 changes.sql。"
}

# =============== 解析命令行参数 ===============
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
      [ -n "${2:-}" ] || { echo "错误: -d/--database 需要指定目标数据库名"; exit 1; }
      TARGET_DB="$2"
      shift 2
      ;;
    -b|--full-backup-dir)
      [ -n "${2:-}" ] || { echo "错误: -b/--full-backup-dir 需要指定全量备份目录"; exit 1; }
      FULL_BACKUP_DIR="$2"
      shift 2
      ;;
    -i|--incremental-dirs)
      [ -n "${2:-}" ] || { echo "错误: -i/--incremental-dirs 需要指定增量目录列表"; exit 1; }
      INCR_DIRS_RAW="$2"
      shift 2
      ;;
    -l|--log-file)
      [ -n "${2:-}" ] || { echo "错误: -l/--log-file 需要指定日志路径"; exit 1; }
      LOG_FILE="$2"
      shift 2
      ;;
    *)
      echo "错误: 未知选项 $1"
      echo "使用 $0 -h 查看帮助"
      exit 1
      ;;
  esac
done

if [ -z "${FULL_BACKUP_DIR}" ] || [ -z "${TARGET_DB}" ] || [ -z "${INCR_DIRS_RAW}" ]; then
  echo "错误: 必须指定全量备份目录(-b)、目标数据库(-d)和增量目录列表(-i)。"
  echo "使用 $0 -h 查看帮助"
  exit 1
fi

if [ ! -d "${FULL_BACKUP_DIR}" ]; then
  echo "错误: 全量备份目录不存在: ${FULL_BACKUP_DIR}"
  exit 1
fi

# 处理增量目录列表（逗号分隔）
IFS=',' read -r -a INCR_DIRS <<< "${INCR_DIRS_RAW}"
if [ "${#INCR_DIRS[@]}" -eq 0 ]; then
  echo "错误: 解析增量目录列表失败。"
  exit 1
fi

set -e

# 日志输出
if [ -n "${LOG_FILE}" ]; then
  mkdir -p "$(dirname "${LOG_FILE}")"
  exec > >(tee -a "${LOG_FILE}") 2>&1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始基于全量 + 增量的还原。"
echo "  - 目标库: ${TARGET_DB}"
echo "  - 全量备份目录: ${FULL_BACKUP_DIR}"
echo "  - 增量目录个数: ${#INCR_DIRS[@]}"

# 尝试从首个增量的 meta/binlog_from.json 中解析原始数据库名（生成增量时使用的 DB_NAME）
SRC_DB=""
for inc_dir in "${INCR_DIRS[@]}"; do
  [ -z "${inc_dir}" ] && continue
  meta_from_file="${inc_dir}/meta/binlog_from.json"
  if [ -f "${meta_from_file}" ]; then
    SRC_DB=$(grep -m1 '"database"' "${meta_from_file}" 2>/dev/null | sed -E 's/.*"database"[[:space:]]*:[[:space:]]*"([^"\\]+)".*/\1/')
    break
  fi
done
[ -n "${SRC_DB}" ] && echo "  - 增量来源库: ${SRC_DB}"

# 解析当前脚本所在目录，以便找到全量还原脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESTORE_FULL_SCRIPT="${SCRIPT_DIR}/mysql-restore-schema-data.sh"

if [ ! -x "${RESTORE_FULL_SCRIPT}" ]; then
  echo "错误: 找不到全量还原脚本: ${RESTORE_FULL_SCRIPT}"
  exit 1
fi

# 1. 先执行全量还原（若本脚本指定了 -l，则全量还原也写入同一日志文件）
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 步骤 1/2: 执行全量还原..."

FULL_RESTORE_ARGS=(
  -b "${FULL_BACKUP_DIR}"
  -d "${TARGET_DB}"
  -H "${DB_HOST}"
  -P "${DB_PORT}"
  -u "${DB_USER}"
  -p "${DB_PASS}"
)
[ -n "${LOG_FILE}" ] && FULL_RESTORE_ARGS+=(-l "${LOG_FILE}")

"${RESTORE_FULL_SCRIPT}" "${FULL_RESTORE_ARGS[@]}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 全量还原完成。"

# 2. 依次回放增量 changes.sql
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 步骤 2/2: 依次回放增量 changes.sql..."

export MYSQL_PWD="${DB_PASS}"

for inc_dir in "${INCR_DIRS[@]}"; do
  [ -z "${inc_dir}" ] && continue
  if [ ! -d "${inc_dir}" ]; then
    echo "  警告: 增量目录不存在，跳过: ${inc_dir}"
    continue
  fi
  CHANGES_SQL="${inc_dir}/changes.sql"
  if [ ! -f "${CHANGES_SQL}" ]; then
    echo "  警告: 未找到 changes.sql，跳过: ${inc_dir}"
    continue
  fi
  echo "  -> 回放增量: ${CHANGES_SQL}"

  # 若增量来源库名与目标库不同，则在应用前对 changes.sql 做一次轻量重写：
  # - 将 "USE `SRC_DB`;" 替换为 "USE `TARGET_DB`;"
  # - 将 "`SRC_DB`." 前缀替换为 "`TARGET_DB`."，使 binlog 变更落在目标库
  if [ -n "${SRC_DB}" ] && [ "${SRC_DB}" != "${TARGET_DB}" ]; then
    TMP_SQL="${CHANGES_SQL}.tmp.$$"
    sed -e "s/USE \`${SRC_DB}\`;/USE \`${TARGET_DB}\`;/Ig" \
        -e "s/\`${SRC_DB}\`\./\`${TARGET_DB}\`./g" "${CHANGES_SQL}" > "${TMP_SQL}" || {
      echo "  警告: 重写增量 SQL 失败，直接使用原始文件。"
      TMP_SQL="${CHANGES_SQL}"
    }
    mysql -h"${DB_HOST}" -P"${DB_PORT}" -u"${DB_USER}" "${TARGET_DB}" < "${TMP_SQL}"
    [ "${TMP_SQL}" != "${CHANGES_SQL}" ] && rm -f "${TMP_SQL}"
  else
    mysql -h"${DB_HOST}" -P"${DB_PORT}" -u"${DB_USER}" "${TARGET_DB}" < "${CHANGES_SQL}"
  fi
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 增量回放完成。"
echo "还原流程结束。"

