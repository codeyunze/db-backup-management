#!/bin/bash

# 用于恢复由 mysql-backup-schema-data.sh 生成的备份（含 schema/ 与 data/，大表可能拆分为多个 .sql）

# =============== 配置区（默认值，可通过命令行参数覆盖） ===============
DB_HOST="localhost"
DB_PORT="3306"
DB_USER="root"    # 需要有 CREATE DATABASE 权限的用户
DB_PASS="123456"  # 对应用户的密码
# 日志：留空则使用备份目录下的 restore.log
LOG_FILE=""
# 日志超过该大小(MB)时自动备份为 .YYYYMMDD_HHMMSS.bak，然后重新记录
LOG_SIZE_LIMIT_MB=10

# 若当前日志文件超过限制则重命名为带时间戳的 .bak
rotate_log_if_needed() {
    [ -n "${1:-}" ] || return 0
    local f="$1" limit=$((LOG_SIZE_LIMIT_MB * 1024 * 1024)) size
    [ -f "$f" ] || return 0
    size=$(wc -c < "$f" 2>/dev/null) || size=0
    [ "${size:-0}" -ge "$limit" ] || return 0
    mv "$f" "${f}.$(date +%Y%m%d_%H%M%S).bak"
}

# =============== -h 帮助说明 ===============
show_help() {
    echo "用法: $0 [选项] [备份目录路径] [新数据库名]"
    echo ""
    echo "从 mysql-backup-schema-data.sh 生成的备份目录恢复数据库（含 schema/ 与 data/）。"
    echo ""
    echo "参数（可用 -b/-d 指定，或按位置传参，未指定则必须用选项）："
    echo "  备份目录路径    由备份脚本生成的目录，如 /backup/mydb_20240520_120000"
    echo "  新数据库名      恢复后的数据库名，如 mydb_restored"
    echo ""
    echo "选项（未指定时使用脚本内配置区默认值）:"
    echo "  -b, --backup-dir 备份目录路径，如: -b /backup/mydb_20240520_120000"
    echo "  -d, --database   要恢复到的新数据库名，如: -d mydb_restored"
    echo "  -H, --host       数据库主机，如: -H 127.0.0.1"
    echo "  -P, --port       数据库端口，如: -P 3306"
    echo "  -u, --user       数据库用户，如: -u root"
    echo "  -p, --password   数据库密码，如: -p yourpass"
    echo "  -l, --log-file   恢复日志文件路径（不传则用备份目录下 restore.log）"
    echo "  -L, --log-limit  日志超过该大小(MB)时轮转备份，如: -L 10"
    echo "  -h, --help       显示此帮助信息"
    echo "  -t, --tables     仅恢复指定的表，多个表用逗号分隔"
    echo "  -i, --ignore     不恢复的表，多个表用逗号分隔；与 -t 同时使用时从 -t 中排除"
    echo "  -o, --overwrite  用备份数据覆盖的表；仅填 -o 未填 -t 时，自动将 -o 的表作为 -t"
    echo ""
    echo "示例:"
    echo "  $0 /backup/mydb_20240520_120000 mydb_restored              # 位置参数"
    echo "  $0 -b /backup/mydb_20240520_120000 -d mydb_restored         # 选项指定备份路径与库名"
    echo "  $0 -o user,order /backup/... mydb_restored                 # 仅恢复并覆盖 user、order"
    echo ""
    echo "说明: 未通过 -l 设置 LOG_FILE 时，恢复日志写入备份目录下的 restore.log。"
}

# =============== 解析选项与参数 ===============
TARGET_TABLES=""
IGNORE_TABLES=""
OVERWRITE_TABLES=""
BACKUP_DIR=""
NEW_DB_NAME=""
while [ $# -gt 0 ]; do
    case "${1}" in
        -h|--help)
            show_help
            exit 0
            ;;
        -b|--backup-dir)
            [ -n "${2:-}" ] || { echo "错误: -b/--backup-dir 需要指定备份目录路径"; exit 1; }
            BACKUP_DIR="${2}"
            shift 2
            ;;
        -d|--database)
            [ -n "${2:-}" ] || { echo "错误: -d/--database 需要指定新数据库名"; exit 1; }
            NEW_DB_NAME="${2}"
            shift 2
            ;;
        -H|--host)
            [ -n "${2:-}" ] || { echo "错误: -H/--host 需要指定主机"; exit 1; }
            DB_HOST="${2}"
            shift 2
            ;;
        -P|--port)
            [ -n "${2:-}" ] || { echo "错误: -P/--port 需要指定端口"; exit 1; }
            DB_PORT="${2}"
            shift 2
            ;;
        -u|--user)
            [ -n "${2:-}" ] || { echo "错误: -u/--user 需要指定用户"; exit 1; }
            DB_USER="${2}"
            shift 2
            ;;
        -p|--password)
            [ -n "${2:-}" ] || { echo "错误: -p/--password 需要指定密码"; exit 1; }
            DB_PASS="${2}"
            shift 2
            ;;
        -l|--log-file)
            [ -n "${2:-}" ] || { echo "错误: -l/--log-file 需要指定日志路径"; exit 1; }
            LOG_FILE="${2}"
            shift 2
            ;;
        -L|--log-limit)
            [ -n "${2:-}" ] || { echo "错误: -L/--log-limit 需要指定大小(MB)"; exit 1; }
            LOG_SIZE_LIMIT_MB="${2}"
            shift 2
            ;;
        -t|--tables)
            [ -n "${2:-}" ] || { echo "错误: -t/--tables 需要指定表名"; exit 1; }
            TARGET_TABLES="${2}"
            shift 2
            ;;
        -i|--ignore)
            [ -n "${2:-}" ] || { echo "错误: -i/--ignore 需要指定表名"; exit 1; }
            IGNORE_TABLES="${2}"
            shift 2
            ;;
        -o|--overwrite)
            [ -n "${2:-}" ] || { echo "错误: -o/--overwrite 需要指定表名"; exit 1; }
            OVERWRITE_TABLES="${2}"
            shift 2
            ;;
        -*)
            echo "错误: 未知选项 ${1}"
            echo "使用 $0 -h 查看帮助"
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

# 未通过 -b/-d 指定时，从位置参数取备份目录和新数据库名
[ -z "${BACKUP_DIR}" ] && [ $# -ge 1 ] && BACKUP_DIR="$1" && shift
[ -z "${NEW_DB_NAME}" ] && [ $# -ge 1 ] && NEW_DB_NAME="$1" && shift

if [ -z "${BACKUP_DIR}" ] || [ -z "${NEW_DB_NAME}" ]; then
    echo "用法: $0 [选项] [备份目录路径] [新数据库名]"
    echo "需要备份目录路径和新数据库名（可用 -b、-d 指定，或按位置传参）"
    echo "使用 $0 -h 查看完整帮助"
    exit 1
fi

# 将逗号分隔的表名转为空格分隔，便于后续循环
[ -n "${TARGET_TABLES}" ] && TARGET_TABLES=$(echo "${TARGET_TABLES}" | tr ',' ' ')
[ -n "${IGNORE_TABLES}" ] && IGNORE_TABLES=$(echo "${IGNORE_TABLES}" | tr ',' ' ')
[ -n "${OVERWRITE_TABLES}" ] && OVERWRITE_TABLES=$(echo "${OVERWRITE_TABLES}" | tr ',' ' ')
# 若只填了 -o 未填 -t，则将 -o 的表名同时作为 -t（仅恢复并覆盖这些表）
[ -z "${TARGET_TABLES}" ] && [ -n "${OVERWRITE_TABLES}" ] && TARGET_TABLES="${OVERWRITE_TABLES}"

# 检查备份目录及 schema/data 子目录是否存在
if [ ! -d "${BACKUP_DIR}" ] || [ ! -d "${BACKUP_DIR}/schema" ] || [ ! -d "${BACKUP_DIR}/data" ]; then
    echo "错误: 备份目录 '${BACKUP_DIR}' 不存在或格式不正确（需包含 schema/ 与 data/）。"
    exit 1
fi

[ -z "${LOG_FILE}" ] && LOG_FILE="${BACKUP_DIR}/restore.log"
rotate_log_if_needed "${LOG_FILE}"
exec > >(tee -a "${LOG_FILE}") 2>&1

set -e

MYSQL_CMD="mysql -h${DB_HOST} -P${DB_PORT} -u${DB_USER} -p${DB_PASS} -N"


# 读取视图列表（备份时创建的 .views 文件）
VIEW_LIST=""
[ -f "${BACKUP_DIR}/schema/.views" ] && VIEW_LIST=$(grep -v '^$' "${BACKUP_DIR}/schema/.views" 2>/dev/null | tr '\n' ' ')

# 判断是否为视图
is_view() {
    local t="$1"
    [ -z "${VIEW_LIST}" ] && return 1
    for v in ${VIEW_LIST}; do
        [ "$v" = "$t" ] && return 0
    done
    return 1
}
# 判断表是否在“覆盖”列表中
is_overwrite_table() {
    local t="$1"
    [ -z "${OVERWRITE_TABLES}" ] && return 1
    for o in ${OVERWRITE_TABLES}; do
        [ "$o" = "$t" ] && return 0
    done
    return 1
}

# 判断目标库中表是否已存在
table_exists_in_db() {
    local t="$1"
    local cnt
    cnt=$(${MYSQL_CMD} -e "SELECT COUNT(1) FROM information_schema.TABLES WHERE TABLE_SCHEMA='${NEW_DB_NAME}' AND TABLE_NAME='${t}';" 2>/dev/null) || echo "0"
    [ "${cnt:-0}" -ge 1 ] 2>/dev/null
}

# 对单表/视图执行：导入结构 + 导入数据（视图无数据）
restore_one_table() {
    local TABLE="$1"
    local schema_file="${BACKUP_DIR}/schema/${TABLE}.sql"
    local SINGLE="${BACKUP_DIR}/data/${TABLE}.sql"

    if [ ! -f "${schema_file}" ]; then
        echo "  跳过（无结构文件）: ${TABLE}"
        return 0
    fi

    if is_view "${TABLE}"; then
        echo "  -> 导入视图: ${TABLE}"
        ${MYSQL_CMD} "${NEW_DB_NAME}" < "${schema_file}"
        return 0
    fi

    echo "  -> 导入表结构: ${TABLE}"
    ${MYSQL_CMD} "${NEW_DB_NAME}" < "${schema_file}"

    if [ -f "${SINGLE}" ]; then
        echo "  -> 导入数据: ${TABLE}.sql"
        ${MYSQL_CMD} "${NEW_DB_NAME}" < "${SINGLE}"
    else
        PART_FILES=$(find "${BACKUP_DIR}/data" -maxdepth 1 -name "${TABLE}_*.sql" -type f 2>/dev/null | sort -V)
        if [ -z "${PART_FILES}" ]; then
            echo "  跳过（无数据文件）: ${TABLE}"
        else
            while IFS= read -r data_file; do
                [ -n "${data_file}" ] || continue
                echo "  -> 导入数据: $(basename "${data_file}")"
                ${MYSQL_CMD} "${NEW_DB_NAME}" < "${data_file}"
            done <<< "${PART_FILES}"
        fi
    fi
}

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 日志文件: ${LOG_FILE}"
if [ -n "${TARGET_TABLES}" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始从 '${BACKUP_DIR}' 恢复数据库到 '${NEW_DB_NAME}'（仅表: ${TARGET_TABLES}）"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始从 '${BACKUP_DIR}' 恢复数据库到 '${NEW_DB_NAME}'"
fi
[ -n "${IGNORE_TABLES}" ] && echo "[$(date '+%Y-%m-%d %H:%M:%S')] 排除表（不恢复）: ${IGNORE_TABLES}"
[ -n "${OVERWRITE_TABLES}" ] && echo "[$(date '+%Y-%m-%d %H:%M:%S')] 覆盖表（先 DROP 再恢复）: ${OVERWRITE_TABLES}"

# 1. 创建新数据库
echo "1. 正在创建新数据库 '${NEW_DB_NAME}'..."
${MYSQL_CMD} -e "CREATE DATABASE IF NOT EXISTS \`${NEW_DB_NAME}\`;"

# 2. 获取待恢复表/视图列表
if [ -n "${TARGET_TABLES}" ]; then
    TABLES_TO_RESTORE="${TARGET_TABLES}"
else
    TABLES_TO_RESTORE=""
    for schema_file in "${BACKUP_DIR}"/schema/*.sql; do
        [ -f "${schema_file}" ] || continue
        name=$(basename "${schema_file}" .sql)
        [ "${name}" = ".views" ] && continue
        TABLES_TO_RESTORE="${TABLES_TO_RESTORE} ${name}"
    done
fi

# 应用 -i 排除：从 TABLES_TO_RESTORE 中移除 IGNORE_TABLES 中的表/视图
if [ -n "${IGNORE_TABLES}" ]; then
    FILTERED=""
    for t in ${TABLES_TO_RESTORE}; do
        SKIP=0
        for ex in ${IGNORE_TABLES}; do
            [ "$t" = "$ex" ] && SKIP=1 && break
        done
        [ "${SKIP}" -eq 1 ] && continue
        FILTERED="${FILTERED} ${t}"
    done
    TABLES_TO_RESTORE="${FILTERED}"
fi

# 分离基表与视图：基表先恢复，视图最后恢复
TABLES_ONLY=""
VIEWS_ONLY=""
for t in ${TABLES_TO_RESTORE}; do
    [ -z "${t}" ] && continue
    if is_view "${t}"; then
        VIEWS_ONLY="${VIEWS_ONLY} ${t}"
    else
        TABLES_ONLY="${TABLES_ONLY} ${t}"
    fi
done

# 3. 先恢复基表，再恢复视图
echo "2. 正在按表恢复（结构+数据）..."
for TABLE in ${TABLES_ONLY}; do
    [ -z "${TABLE}" ] && continue
    if is_overwrite_table "${TABLE}"; then
        echo "  [覆盖] ${TABLE}: 先删除再恢复"
        ${MYSQL_CMD} -e "DROP TABLE IF EXISTS \`${NEW_DB_NAME}\`.\`${TABLE}\`;"
        restore_one_table "${TABLE}"
    else
        if table_exists_in_db "${TABLE}"; then
            echo "  [跳过] ${TABLE}: 表已存在，不覆盖"
        else
            restore_one_table "${TABLE}"
        fi
    fi
done

if [ -n "${VIEWS_ONLY}" ]; then
    echo "3. 正在恢复视图..."
    for TABLE in ${VIEWS_ONLY}; do
        [ -z "${TABLE}" ] && continue
        if is_overwrite_table "${TABLE}"; then
            echo "  [覆盖] ${TABLE}: 先删除再恢复"
            ${MYSQL_CMD} -e "DROP VIEW IF EXISTS \`${NEW_DB_NAME}\`.\`${TABLE}\`;"
            restore_one_table "${TABLE}"
        else
            if table_exists_in_db "${TABLE}"; then
                echo "  [跳过] ${TABLE}: 视图已存在，不覆盖"
            else
                restore_one_table "${TABLE}"
            fi
        fi
    done
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 恢复完成！"
echo "新数据库名: ${NEW_DB_NAME}"
echo "  - 日志: ${LOG_FILE}"

