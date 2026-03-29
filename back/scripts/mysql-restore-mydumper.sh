#!/usr/bin/env bash
#
# 使用 myloader（与 mydumper v0.21.x 配套）将 mydumper 备份目录还原到目标 MySQL。
#
# 依赖：本机已安装 myloader。备份会话根目录下 mydumper 输出在 data/（含 metadata、*.sql.zst），
# 与 meta/ 同级；兼容旧版 metadata 位于会话根目录的布局。
#
# 典型用法：
#   mysql-restore-mydumper.sh -H 127.0.0.1 -P 3306 -u root -p pass \
#     -d mall_restore -s /app/backup/data/mall_20260322_120000 \
#     --source-db mall
#

set -eo pipefail

DB_HOST="127.0.0.1"
DB_PORT="3306"
DB_USER="root"
DB_PASS=""
# 目标库名（还原写入的库）
TARGET_DB=""
# mydumper 备份会话根目录绝对路径（其下应有 data/metadata 或旧版 metadata）
SOURCE_DIR=""
# 实际传给 myloader --directory 的路径（data 子目录或会话根）
MYLOADER_DIR=""
# 备份文件中的源库名（与 mydumper 导出时库名一致；与目标库不同时需指定）
SOURCE_DB=""
THREADS="${THREADS:-4}"
# myloader 0.21+：--drop-table=DROP|NONE|FAIL|TRUNCATE|DELETE，默认 DROP（覆盖前删表）
DROP_TABLE_MODE="${DROP_TABLE_MODE:-DROP}"

MYLOADER_BIN="${MYLOADER_BIN:-myloader}"
LOG_FILE=""

show_usage() {
  echo "用法: $0 [选项]"
  echo ""
  echo "选项:"
  echo "  -H, --host           MySQL 主机"
  echo "  -P, --port           端口"
  echo "  -u, --user           用户"
  echo "  -p, --password       密码"
  echo "  -d, --database       目标数据库名（还原到该库）"
  echo "  -s, --source-dir     备份会话根目录绝对路径（其下为 data/ 或旧版根目录 metadata）"
  echo "      --source-db      备份中的源库名（与 -d 不同时必须指定，用于 myloader --source-db）"
  echo "      --threads        线程数（默认 ${THREADS}）"
  echo "      --drop-table     删表策略，默认 DROP；可设为 NONE（表已存在则失败）"
  echo "  -t, --tables         仅还原指定表，逗号分隔短名（自动拼成 db.table）"
  echo "  -i, --ignore         不还原的表，逗号分隔；使用 myloader --omit-from-file"
  echo "  -h, --help           帮助"
  echo ""
  echo "环境变量: MYLOADER_BIN、DROP_TABLE_MODE、THREADS"
}

TABLES_INCLUDE=""
TABLES_EXCLUDE=""
while [ $# -gt 0 ]; do
  case "${1}" in
    -h | --help)
      show_usage
      exit 0
      ;;
    -H | --host)
      DB_HOST="${2:?}"
      shift 2
      ;;
    -P | --port)
      DB_PORT="${2:?}"
      shift 2
      ;;
    -u | --user)
      DB_USER="${2:?}"
      shift 2
      ;;
    -p | --password)
      DB_PASS="${2:?}"
      shift 2
      ;;
    -d | --database)
      TARGET_DB="${2:?}"
      shift 2
      ;;
    -s | --source-dir)
      SOURCE_DIR="${2:?}"
      shift 2
      ;;
    --source-db)
      SOURCE_DB="${2:?}"
      shift 2
      ;;
    --threads)
      THREADS="${2:?}"
      shift 2
      ;;
    --drop-table)
      DROP_TABLE_MODE="${2:?}"
      shift 2
      ;;
    -t | --tables)
      TABLES_INCLUDE="${2:?}"
      shift 2
      ;;
    -i | --ignore)
      TABLES_EXCLUDE="${2:?}"
      shift 2
      ;;
    *)
      echo "错误: 未知选项 ${1}"
      show_usage
      exit 1
      ;;
  esac
done

if ! command -v "${MYLOADER_BIN}" >/dev/null 2>&1; then
  echo "错误: 未找到 myloader（MYLOADER_BIN=${MYLOADER_BIN}）"
  exit 1
fi

if [ -z "${TARGET_DB}" ] || [ -z "${SOURCE_DIR}" ]; then
  echo "错误: 必须指定 -d 目标数据库 与 -s 备份目录"
  exit 1
fi

if [ ! -d "${SOURCE_DIR}" ]; then
  echo "错误: 备份目录不存在: ${SOURCE_DIR}"
  exit 1
fi

if [ -f "${SOURCE_DIR}/data/metadata" ]; then
  MYLOADER_DIR="${SOURCE_DIR}/data"
elif [ -f "${SOURCE_DIR}/metadata" ]; then
  MYLOADER_DIR="${SOURCE_DIR}"
else
  echo "错误: 未找到 mydumper metadata，请确认 ${SOURCE_DIR}/data/metadata（新版）或 ${SOURCE_DIR}/metadata（旧版）"
  exit 1
fi

[ -n "${TABLES_INCLUDE}" ] && TABLES_INCLUDE=$(echo "${TABLES_INCLUDE}" | tr ',' ' ')
[ -n "${TABLES_EXCLUDE}" ] && TABLES_EXCLUDE=$(echo "${TABLES_EXCLUDE}" | tr ',' ' ')

# 日志：默认写在备份目录下，便于对照
[ -z "${LOG_FILE}" ] && LOG_FILE="${SOURCE_DIR}/restore.log"

run_myloader() {
  echo ""
  echo "======================================================================"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] myloader 还原"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 备份会话目录: ${SOURCE_DIR}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] myloader 读取目录: ${MYLOADER_DIR}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 目标库: ${TARGET_DB}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 源库名(备份内): ${SOURCE_DB:-<与目标相同或按文件>}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 日志: ${LOG_FILE}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $("${MYLOADER_BIN}" --version 2>&1 | head -n1)"

  MYL_ARGS=(
    --directory="${MYLOADER_DIR}"
    --host="${DB_HOST}"
    --port="${DB_PORT}"
    --user="${DB_USER}"
    --password="${DB_PASS}"
    --threads="${THREADS}"
    --verbose=3
  )

  if [ -n "${DROP_TABLE_MODE}" ]; then
    MYL_ARGS+=(--drop-table="${DROP_TABLE_MODE}")
  fi

  if [ -n "${SOURCE_DB}" ] && [ "${SOURCE_DB}" != "${TARGET_DB}" ]; then
    MYL_ARGS+=(--source-db="${SOURCE_DB}")
  fi
  MYL_ARGS+=(--database="${TARGET_DB}")

  # 与 dump 文件名前缀一致（备份时的库名）；用于 --tables-list / omit
  FILE_DB="${SOURCE_DB:-${TARGET_DB}}"

  OMIT_TMP=""
  cleanup() {
    [ -n "${OMIT_TMP}" ] && [ -f "${OMIT_TMP}" ] && rm -f "${OMIT_TMP}"
  }
  trap cleanup EXIT

  if [ -n "${TABLES_INCLUDE}" ]; then
    list=""
    sep=""
    for t in ${TABLES_INCLUDE}; do
      [ -z "${t}" ] && continue
      list="${list}${sep}${FILE_DB}.${t}"
      sep=","
    done
    MYL_ARGS+=(--tables-list="${list}")
  elif [ -n "${TABLES_EXCLUDE}" ]; then
    OMIT_TMP=$(mktemp)
    for ex in ${TABLES_EXCLUDE}; do
      echo "${FILE_DB}.${ex}" >>"${OMIT_TMP}"
    done
    MYL_ARGS+=(--omit-from-file="${OMIT_TMP}")
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 执行: ${MYLOADER_BIN} ${MYL_ARGS[*]//--password=*/--password=***}"
  "${MYLOADER_BIN}" "${MYL_ARGS[@]}"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 还原完成。"
}

set +e
set -o pipefail
run_myloader 2>&1 | tee -a "${LOG_FILE}"
PIPE_EXIT=${PIPESTATUS[0]:-1}
set -e
exit "${PIPE_EXIT}"
