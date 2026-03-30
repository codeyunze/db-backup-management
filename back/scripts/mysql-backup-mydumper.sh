#!/usr/bin/env bash
#
# 使用 mydumper（面向 v0.21.3-2）进行 MySQL 逻辑备份。
#
# 相对 mysql-backup-schema-data.sh（mysqldump）的简化点：
# - 使用 mydumper 替代 mysqldump，不再自行 gzip；由 mydumper 默认输出 .sql.zst（ ZSTD ）。
# - 默认按表分文件，结构与数据分离（mydumper 默认行为），无需脚本内拆分大表/合并 INSERT。
# - 不实现按表 binlog 位点探测（若需增量链路请结合 mydumper 元数据或其它方案）。
#
# 依赖：本机已安装 mydumper，且能连接目标 MySQL。
#
# 典型输出目录结构（示例，具体命名以 mydumper 版本为准）：
#   ${BACKUP_DIR}/                    # 本次备份会话根目录（写入 backup-files 的路径）
#     meta/backup-options.json        # 脚本写入的备份选项
#     backup.log                      # 含 mydumper 输出；默认另由脚本追加「数据表备份完成: 库.表」
#     data/                           # mydumper --outputdir，与 meta/ 同级
#       *.sql.zst                     # 数据
#       *-schema.sql.zst              # 表结构（及视图等）
#       metadata                        # mydumper 元数据
#

# =============== 配置区（可通过命令行覆盖） ===============
DB_HOST="127.0.0.1"
DB_PORT="3306"
DB_USER="root"
DB_PASS=""
DB_NAME="db_name"
BACKUP_ROOT="/app/backup/data"

# mydumper 可执行文件（可通过环境变量 MYDUMPER_BIN 覆盖）
MYDUMPER_BIN="${MYDUMPER_BIN:-mydumper}"

# 并发线程数
THREADS="${THREADS:-4}"

# 单表并行上限。ZSTD 压缩 + 多线程写同一分片时，mydumper 可能报
# 「file already open: ... .sql」（见 https://github.com/mydumper/mydumper/issues/1944 等）。
# 默认 1 最稳；需加速可改为 2 并观察，或通过提高 --threads（多表并行）代替。
MAX_THREADS_PER_TABLE="${MAX_THREADS_PER_TABLE:-1}"

# 输出压缩：mydumper 0.21.x 常用 zstd（与「默认 .sql.zst」一致，显式写出便于排查）
COMPRESS="${COMPRESS:-zstd}"

# 日志：留空则使用本次备份目录下的 backup.log
LOG_FILE=""
LOG_SIZE_LIMIT_MB=10

# 每表完成一行写入 backup.log：监控 data/ 下文件稳定（默认约 2s 无变化）后记一条；mydumper 结束时再补记未触发稳定的表。
# 设为 1 可关闭。稳定窗口（秒）可用 TABLE_MONITOR_STABLE_SEC 覆盖（默认 2）。
DISABLE_TABLE_PROGRESS_MONITOR="${DISABLE_TABLE_PROGRESS_MONITOR:-0}"
TABLE_MONITOR_STABLE_SEC="${TABLE_MONITOR_STABLE_SEC:-2}"
TABLE_MONITOR_POLL_SEC="${TABLE_MONITOR_POLL_SEC:-0.5}"

rotate_log_if_needed() {
  [ -n "${1:-}" ] || return 0
  local f="$1" limit=$((LOG_SIZE_LIMIT_MB * 1024 * 1024)) size
  [ -f "$f" ] || return 0
  size=$(wc -c < "$f" 2>/dev/null) || size=0
  [ "${size:-0}" -ge "$limit" ] || return 0
  mv "$f" "${f}.$(date +%Y%m%d_%H%M%S).bak"
}

# ---------- 每表备份完成进度（写入 LOG_FILE，与 mydumper 输出并行追加） ----------

_mstat_mtime() {
  local f="$1"
  if stat -f%m "$f" >/dev/null 2>&1; then
    stat -f%m "$f"
  else
    stat -c%Y "$f"
  fi
}

_mstat_size() {
  local f="$1"
  if stat -f%z "$f" >/dev/null 2>&1; then
    stat -f%z "$f"
  else
    stat -c%s "$f"
  fi
}

_table_progress_key() {
  printf '%s' "$1" | cksum | awk '{print $1}'
}

_discover_tables_from_data() {
  local dir="$1" db="$2"
  {
    local f base t rest
    shopt -s nullglob
    for f in "${dir}/${db}".*-schema.sql.zst; do
      base=$(basename "$f")
      t=${base#"${db}."}
      t=${t%-schema.sql.zst}
      [ -n "$t" ] && printf '%s\n' "$t"
    done
    for f in "${dir}/${db}".*.sql.zst; do
      base=$(basename "$f")
      [[ "$base" == *-schema.sql.zst ]] && continue
      rest=${base#"${db}."}
      [[ "$rest" == "$base" ]] && continue
      t=$(printf '%s' "$rest" | sed 's/\.[0-9][0-9]*\.sql\.zst$//')
      [ -n "$t" ] && printf '%s\n' "$t"
    done
    shopt -u nullglob
  } | sort -u
}

_table_fingerprint() {
  local dir="$1" db="$2" tbl="$3" schema f line=""
  schema="${dir}/${db}.${tbl}-schema.sql.zst"
  [ -f "$schema" ] &&
    line="${line}$(basename "$schema") $(_mstat_size "$schema") $(_mstat_mtime "$schema")"$'\n'
  shopt -s nullglob
  for f in "${dir}/${db}.${tbl}".*.sql.zst; do
    [[ "$(basename "$f")" == *-schema.sql.zst ]] && continue
    line="${line}$(basename "$f") $(_mstat_size "$f") $(_mstat_mtime "$f")"$'\n'
  done
  shopt -u nullglob
  printf '%s' "$line" | LC_ALL=C sort
}

_table_monitor_append_done_log() {
  local logf="$1" db="$2" tbl="$3" ts
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  printf '[%s] 数据表备份完成: %s.%s\n' "$ts" "$db" "$tbl" >>"${logf}"
}

_table_monitor_finalize_all() {
  local data_dir="$1" db="$2" logf="$3" statedir="$4"
  local t k
  while IFS= read -r t; do
    [ -z "${t}" ] && continue
    k=$(_table_progress_key "${t}")
    [ -f "${statedir}/${k}.done" ] && continue
    _table_monitor_append_done_log "${logf}" "${db}" "${t}"
    : >"${statedir}/${k}.done"
  done < <(_discover_tables_from_data "${data_dir}" "${db}")
}

# 后台运行：成功结束时 stop → 补记尚未稳定的表；失败时 abort → 直接退出（避免误标「完成」）。
table_progress_monitor_loop() {
  set +e
  local data_dir="$1" db="$2" logf="$3" stopf="$4" abortf="$5"
  local statedir k t fp last cnt stable_rounds sleep_s
  sleep_s="${TABLE_MONITOR_POLL_SEC}"
  # 稳定轮数 = ceil(STABLE_SEC / POLL)；至少 2 轮
  stable_rounds=$(awk -v s="${TABLE_MONITOR_STABLE_SEC}" -v p="${sleep_s}" \
    'BEGIN { r = int(s/p + 0.999); print (r < 2 ? 2 : r) }')

  statedir=$(mktemp -d "${TMPDIR:-/tmp}/mydumper_tabmon.XXXXXX")
  trap 'rm -rf "${statedir}"' EXIT

  while true; do
    if [ -f "${abortf}" ]; then
      break
    fi
    if [ -f "${stopf}" ]; then
      _table_monitor_finalize_all "${data_dir}" "${db}" "${logf}" "${statedir}"
      break
    fi
    while IFS= read -r t; do
      [ -z "${t}" ] && continue
      k=$(_table_progress_key "${t}")
      printf '%s' "${t}" >"${statedir}/${k}.name"
      fp=$(_table_fingerprint "${data_dir}" "${db}" "${t}")
      [ -z "${fp}" ] && continue
      last=""
      [ -f "${statedir}/${k}.last" ] && last=$(cat "${statedir}/${k}.last")
      cnt=0
      [ -f "${statedir}/${k}.cnt" ] && cnt=$(cat "${statedir}/${k}.cnt")
      cnt=${cnt:-0}
      if [ "${fp}" = "${last}" ]; then
        cnt=$((cnt + 1))
      else
        cnt=0
        printf '%s' "${fp}" >"${statedir}/${k}.last"
      fi
      echo "${cnt}" >"${statedir}/${k}.cnt"
      if [ "${cnt}" -ge "${stable_rounds}" ] && [ ! -f "${statedir}/${k}.done" ]; then
        _table_monitor_append_done_log "${logf}" "${db}" "${t}"
        : >"${statedir}/${k}.done"
      fi
    done < <(_discover_tables_from_data "${data_dir}" "${db}")

    sleep "${sleep_s}"
  done
}

show_usage() {
  echo "用法: $0 [选项]"
  echo ""
  echo "使用 mydumper 备份单库；默认 ZSTD 压缩、按表分文件、结构/数据分离（与 v0.21.x 默认行为一致）。"
  echo ""
  echo "选项:"
  echo "  -H, --host        数据库主机"
  echo "  -P, --port        端口"
  echo "  -u, --user        用户"
  echo "  -p, --password    密码"
  echo "  -d, --database    数据库名"
  echo "  -b, --backup-dir  备份根目录（最终目录为 \${BACKUP_ROOT}/\${DB_NAME}_时间戳）"
  echo "      --session-dir  指定本次会话目录绝对路径（须位于 -b 根目录下；与自动时间戳二选一，供服务端预登记）"
  echo "  -t, --tables      仅备份指定表，逗号分隔"
  echo "  -i, --ignore      不备份的表，逗号分隔；仅 -i 时使用 mydumper --omit-from-file"
  echo "  -c, --clean       备份完成后清理 N 天前的同库备份目录（名称前缀 \${DB_NAME}_）"
  echo "      --threads     mydumper 线程数（默认 ${THREADS}）"
  echo "      --max-threads-per-table  单表并行线程上限（默认 ${MAX_THREADS_PER_TABLE}，建议保持 1 避免 file already open）"
  echo "      --compress    压缩算法，如 zstd（默认 ${COMPRESS}）；设为 none 可关闭压缩（若版本支持）"
  echo "  -h, --help        帮助"
  echo ""
  echo "环境变量: MYDUMPER_BIN；THREADS、MAX_THREADS_PER_TABLE、COMPRESS 可覆盖默认值"
  echo "  DISABLE_TABLE_PROGRESS_MONITOR=1 关闭「每表一行」backup.log 进度"
  echo "  TABLE_MONITOR_STABLE_SEC / TABLE_MONITOR_POLL_SEC 调整稳定检测窗口与轮询间隔"
}

CLEAN_DAYS=""
TABLES_INCLUDE=""
TABLES_EXCLUDE=""
SESSION_DIR=""
while [ $# -gt 0 ]; do
  case "${1}" in
    -h | --help)
      show_usage
      exit 0
      ;;
    -H | --host)
      [ -n "${2:-}" ] || { echo "错误: -H 需要参数"; exit 1; }
      DB_HOST="${2}"
      shift 2
      ;;
    -P | --port)
      [ -n "${2:-}" ] || { echo "错误: -P 需要参数"; exit 1; }
      DB_PORT="${2}"
      shift 2
      ;;
    -u | --user)
      [ -n "${2:-}" ] || { echo "错误: -u 需要参数"; exit 1; }
      DB_USER="${2}"
      shift 2
      ;;
    -p | --password)
      [ -n "${2:-}" ] || { echo "错误: -p 需要参数"; exit 1; }
      DB_PASS="${2}"
      shift 2
      ;;
    -d | --database)
      [ -n "${2:-}" ] || { echo "错误: -d 需要参数"; exit 1; }
      DB_NAME="${2}"
      shift 2
      ;;
    -b | --backup-dir)
      [ -n "${2:-}" ] || { echo "错误: -b 需要参数"; exit 1; }
      BACKUP_ROOT="${2}"
      shift 2
      ;;
    -t | --tables)
      [ -n "${2:-}" ] || { echo "错误: -t 需要参数"; exit 1; }
      TABLES_INCLUDE="${2}"
      shift 2
      ;;
    -i | --ignore)
      [ -n "${2:-}" ] || { echo "错误: -i 需要参数"; exit 1; }
      TABLES_EXCLUDE="${2}"
      shift 2
      ;;
    -c | --clean)
      [ -n "${2:-}" ] || { echo "错误: -c 需要参数"; exit 1; }
      CLEAN_DAYS="${2}"
      shift 2
      ;;
    --threads)
      [ -n "${2:-}" ] || { echo "错误: --threads 需要参数"; exit 1; }
      THREADS="${2}"
      shift 2
      ;;
    --max-threads-per-table)
      [ -n "${2:-}" ] || { echo "错误: --max-threads-per-table 需要参数"; exit 1; }
      MAX_THREADS_PER_TABLE="${2}"
      shift 2
      ;;
    --compress)
      [ -n "${2:-}" ] || { echo "错误: --compress 需要参数"; exit 1; }
      COMPRESS="${2}"
      shift 2
      ;;
    --session-dir)
      [ -n "${2:-}" ] || { echo "错误: --session-dir 需要参数"; exit 1; }
      SESSION_DIR="${2}"
      shift 2
      ;;
    *)
      echo "错误: 未知选项 ${1}"
      show_usage
      exit 1
      ;;
  esac
done

[ -n "${TABLES_INCLUDE}" ] && TABLES_INCLUDE=$(echo "${TABLES_INCLUDE}" | tr ',' ' ')
[ -n "${TABLES_EXCLUDE}" ] && TABLES_EXCLUDE=$(echo "${TABLES_EXCLUDE}" | tr ',' ' ')

if [ -n "${SESSION_DIR}" ]; then
  BACKUP_DIR="${SESSION_DIR}"
else
  BACKUP_DIR="${BACKUP_ROOT}/${DB_NAME}_$(date +%Y%m%d_%H%M%S)"
fi

set -e

if ! command -v "${MYDUMPER_BIN}" >/dev/null 2>&1; then
  echo "错误: 未找到 mydumper（MYDUMPER_BIN=${MYDUMPER_BIN}），请先安装或设置环境变量。"
  exit 1
fi

# 与后端 BACK_DIR/data 一致：新部署时该目录常不存在，仅 json/ 会先被写入，须自动创建
if ! mkdir -p "${BACKUP_ROOT}"; then
  echo "错误: 无法创建备份根目录: ${BACKUP_ROOT}"
  exit 1
fi

if [ -n "${SESSION_DIR}" ]; then
  case "${BACKUP_DIR}/" in
    "${BACKUP_ROOT}/"*) ;;
    *)
      echo "错误: --session-dir 必须位于备份根目录之下: ${BACKUP_ROOT}"
      exit 1
      ;;
  esac
fi

export MYSQL_PWD="${DB_PASS}"
MYSQL_CMD="mysql -h${DB_HOST} -P${DB_PORT} -u${DB_USER} -N"

mkdir -p "${BACKUP_DIR}/meta" "${BACKUP_DIR}/data"
[ -z "${LOG_FILE}" ] && LOG_FILE="${BACKUP_DIR}/backup.log"
rotate_log_if_needed "${LOG_FILE}"

# 勿使用 exec > >(tee)：部分环境下 bash 退出时会取 tee 子进程状态，导致已成功备份仍返回 exit 1。
# 使用「函数 + 管道」：失败用 return，避免在管道组内 exit 误杀整个 shell。
run_mydumper_backup() {
echo ""
echo "======================================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] mydumper 备份数据库: ${DB_NAME}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 输出目录: ${BACKUP_DIR}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] mydumper 数据目录: ${BACKUP_DIR}/data"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 日志: ${LOG_FILE}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] $( "${MYDUMPER_BIN}" --version 2>&1 | head -n1 )"

# 基表 + 视图名列表（换行 -> 空格）
ALL_OBJECTS=$(${MYSQL_CMD} -e "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='${DB_NAME}' AND TABLE_TYPE IN ('BASE TABLE','VIEW');" 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//') || true

if [ -z "${ALL_OBJECTS}" ]; then
  echo "错误: 无法获取库 '${DB_NAME}' 的表/视图列表，请检查连接与库名。"
  return 1
fi

filter_objects() {
  local ALL_OBJS="$1"
  local result=""
  if [ -n "${TABLES_INCLUDE}" ]; then
    for t in ${TABLES_INCLUDE}; do
      for at in ${ALL_OBJS}; do
        if [ "${t}" = "${at}" ]; then
          result="${result} ${t}"
          break
        fi
      done
    done
  else
    result="${ALL_OBJS}"
  fi
  if [ -n "${TABLES_EXCLUDE}" ]; then
    local filtered=""
    for t in ${result}; do
      SKIP=0
      for ex in ${TABLES_EXCLUDE}; do
        if [ "${t}" = "${ex}" ]; then
          SKIP=1
          break
        fi
      done
      [ "${SKIP}" -eq 1 ] && continue
      filtered="${filtered} ${t}"
    done
    result="${filtered}"
  fi
  echo "${result}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

TABLES_FINAL=$(filter_objects "${ALL_OBJECTS}")

if [ -z "${TABLES_FINAL}" ]; then
  echo "错误: 经过 -t/-i 过滤后没有可备份对象。"
  return 1
fi

_esc_json() { echo "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
printf '{\n  "tables_include": "%s",\n  "tables_exclude": "%s",\n  "tool": "mydumper"\n}\n' \
  "$(_esc_json "${TABLES_INCLUDE:-}")" \
  "$(_esc_json "${TABLES_EXCLUDE:-}")" \
  >"${BACKUP_DIR}/meta/backup-options.json"

# mydumper 官方参数为 --tables-list，格式：db.t1,db.t2（见 mydumper_usage.html）
build_tables_list() {
  local db="$1"
  local first=1 s="" t
  for t in ${2}; do
    [ -z "${t}" ] && continue
    if [ "${first}" -eq 1 ]; then
      s="${db}.${t}"
      first=0
    else
      s="${s},${db}.${t}"
    fi
  done
  echo "${s}"
}

OMIT_TMP=""
cleanup() {
  [ -n "${OMIT_TMP}" ] && [ -f "${OMIT_TMP}" ] && rm -f "${OMIT_TMP}"
}
trap cleanup EXIT

MYD_ARGS=(
  --host="${DB_HOST}"
  --port="${DB_PORT}"
  --user="${DB_USER}"
  --password="${DB_PASS}"
  --database="${DB_NAME}"
  --outputdir="${BACKUP_DIR}/data"
  # 本脚本会先创建 meta/、data/、backup.log；data 非空需 --dirty
  --dirty
  --threads="${THREADS}"
  --max-threads-per-table="${MAX_THREADS_PER_TABLE}"
  --verbose=2
)

# 压缩：none 时不传 --compress（若需完全明文可再配合版本文档调整）
if [ -n "${COMPRESS}" ] && [ "${COMPRESS}" != "none" ]; then
  MYD_ARGS+=(--compress="${COMPRESS}")
fi

# 过滤策略：
# - 仅 -i：omit-from-file，每行 database.table
# - 含 -t：对最终对象列表使用 --tables-list（db.table,db.table）
if [ -n "${TABLES_INCLUDE}" ]; then
  TABLES_LIST=$(build_tables_list "${DB_NAME}" "${TABLES_FINAL}")
  MYD_ARGS+=(--tables-list="${TABLES_LIST}")
elif [ -n "${TABLES_EXCLUDE}" ]; then
  OMIT_TMP=$(mktemp)
  for ex in ${TABLES_EXCLUDE}; do
    echo "${DB_NAME}.${ex}" >>"${OMIT_TMP}"
  done
  MYD_ARGS+=(--omit-from-file="${OMIT_TMP}")
fi

# 清理 data 内上次中断遗留的裸 .sql（压缩管道临时文件）；与残留文件同开易触发 file already open
if [ -d "${BACKUP_DIR}/data" ]; then
  find "${BACKUP_DIR}/data" -maxdepth 1 -type f -name '*.sql' ! -name '*.sql.zst' -delete 2>/dev/null || true
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 执行: ${MYDUMPER_BIN} ${MYD_ARGS[*]//--password=*/--password=***}"

TABLE_MON_STOP="${BACKUP_DIR}/data/.table_monitor_stop"
TABLE_MON_ABORT="${BACKUP_DIR}/data/.table_monitor_abort"
TABLE_MON_PID=""
rm -f "${TABLE_MON_STOP}" "${TABLE_MON_ABORT}"
if [ "${DISABLE_TABLE_PROGRESS_MONITOR}" != "1" ]; then
  table_progress_monitor_loop "${BACKUP_DIR}/data" "${DB_NAME}" "${LOG_FILE}" "${TABLE_MON_STOP}" "${TABLE_MON_ABORT}" &
  TABLE_MON_PID=$!
fi

set +e
"${MYDUMPER_BIN}" "${MYD_ARGS[@]}"
MYD_EXIT=$?
set -e

if [ -n "${TABLE_MON_PID}" ]; then
  if [ "${MYD_EXIT}" -eq 0 ]; then
    touch "${TABLE_MON_STOP}"
  else
    touch "${TABLE_MON_ABORT}"
  fi
  wait "${TABLE_MON_PID}" 2>/dev/null || true
  rm -f "${TABLE_MON_STOP}" "${TABLE_MON_ABORT}"
fi

[ "${MYD_EXIT}" -ne 0 ] && return "${MYD_EXIT}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 备份完成。"
echo "  会话目录: ${BACKUP_DIR}"
echo "  数据文件: ${BACKUP_DIR}/data（*.sql.zst、metadata）"
echo "  说明: 结构与数据一般为独立 .sql.zst（取决于 mydumper 版本默认）；请用 myloader 按官方文档恢复。"

if [ -n "${CLEAN_DAYS}" ] && [ "${CLEAN_DAYS}" -gt 0 ] 2>/dev/null; then
  MINUTES_AGO=$((CLEAN_DAYS * 24 * 60))
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 清理 ${DB_NAME} 的 ${CLEAN_DAYS} 天前备份（目录名 ${DB_NAME}_*）..."
  DELETED=0
  while IFS= read -r old_dir; do
    [ -z "${old_dir}" ] || [ ! -d "${old_dir}" ] && continue
    # 不删除当前本次目录
    [ "${old_dir}" = "${BACKUP_DIR}" ] && continue
    rm -rf "${old_dir}"
    echo "  -> 已删除: ${old_dir}"
    DELETED=$((DELETED + 1))
  done < <(find "${BACKUP_ROOT}" -maxdepth 1 -mindepth 1 -type d -name "${DB_NAME}_*" -mmin +"${MINUTES_AGO}" 2>/dev/null)
  [ "${DELETED}" -eq 0 ] && echo "  (无满足条件的旧备份)"
fi

}

# set -e 下管道失败会先终止脚本，无法取 $?；此处临时关闭 -e 并用 PIPESTATUS 取函数（左侧）退出码
set +e
set -o pipefail
run_mydumper_backup 2>&1 | tee -a "${LOG_FILE}"
PIPE_EXIT=${PIPESTATUS[0]:-1}
set -e
unset MYSQL_PWD
exit "${PIPE_EXIT}"
