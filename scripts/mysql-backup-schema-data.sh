#!/bin/bash

# =============== 配置区（默认值，可通过命令行参数覆盖） ===============
DB_HOST="127.0.0.1"
DB_PORT="3306"
DB_USER="root"      # 替换为你的数据库用户名
DB_PASS="123456"    # 替换为你的数据库密码
DB_NAME="db_name"   # 替换为你要备份的数据库名
BACKUP_ROOT="/data/backup/mysql"   # 备份根目录（不含时间戳）

# 大表拆分配置：当表行数超过以下阈值时，按多个 sql 文件备份
ROW_THRESHOLD=100000   # 超过该行数即拆分为多文件（可根据磁盘/内存调整）
CHUNK_SIZE=50000       # 每个数据文件最多包含的行数
INSERT_BATCH=500       # 每个 INSERT 语句包含的行数（200~1000 为宜，过大可能触发 max_allowed_packet）

# 日志：留空则使用本次备份目录下的 backup.log
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

# =============== 解析命令行参数（未传则使用上方配置区默认值） ===============
show_usage() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项（未指定时使用脚本内配置区默认值）:"
    echo "  -H, --host        数据库主机，如: -H 127.0.0.1"
    echo "  -P, --port        数据库端口，如: -P 3306"
    echo "  -u, --user        数据库用户，如: -u root"
    echo "  -p, --password    数据库密码，如: -p yourpass"
    echo "  -d, --database    数据库名，如: -d mall"
    echo "  -b, --backup-dir  备份根目录，如: -b /data/backup/mysql"
    echo "  -t, --tables      仅备份指定表，多个表用逗号分隔，如: -t user,order,product"
    echo "  -i, --ignore      不备份的表，多个表用逗号分隔；优先级高于 -t"
    echo "  -c, --clean       备份完成后清理 N 天前的旧备份，如: -c 10（不传则不清理）"
    echo "  -h, --help        显示此帮助"
    echo ""
    echo "示例:"
    echo "  $0                                      # 使用配置区默认值，备份库中所有表"
    echo "  $0 -d mall2 -b /backup                  # 备份 mall2 到 /backup"
    echo "  $0 -t user,order -i order_log           # 仅备份 user、order，但排除 order_log"
    echo "  $0 -d mall -c 10                        # 备份 mall 并清理 10 天前备份"
}

CLEAN_DAYS=""
TABLES_INCLUDE=""
TABLES_EXCLUDE=""
while [ $# -gt 0 ]; do
    case "${1}" in
        -h|--help)
            show_usage
            exit 0
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
        -d|--database)
            [ -n "${2:-}" ] || { echo "错误: -d/--database 需要指定数据库名"; exit 1; }
            DB_NAME="${2}"
            shift 2
            ;;
        -b|--backup-dir)
            [ -n "${2:-}" ] || { echo "错误: -b/--backup-dir 需要指定备份根目录"; exit 1; }
            BACKUP_ROOT="${2}"
            shift 2
            ;;
        -t|--tables)
            [ -n "${2:-}" ] || { echo "错误: -t/--tables 需要指定表名列表"; exit 1; }
            TABLES_INCLUDE="${2}"
            shift 2
            ;;
        -i|--ignore)
            [ -n "${2:-}" ] || { echo "错误: -i/--ignore 需要指定表名列表"; exit 1; }
            TABLES_EXCLUDE="${2}"
            shift 2
            ;;
        -c|--clean)
            [ -n "${2:-}" ] || { echo "错误: -c/--clean 需要指定天数"; exit 1; }
            CLEAN_DAYS="${2}"
            shift 2
            ;;
        *)
            echo "错误: 未知选项 ${1}"
            echo "使用 $0 -h 查看帮助"
            exit 1
            ;;
    esac
done

# 处理表名列表：逗号分隔转为空格分隔
[ -n "${TABLES_INCLUDE}" ] && TABLES_INCLUDE=$(echo "${TABLES_INCLUDE}" | tr ',' ' ')
[ -n "${TABLES_EXCLUDE}" ] && TABLES_EXCLUDE=$(echo "${TABLES_EXCLUDE}" | tr ',' ' ')

# 实际备份目录（在参数解析后设置）
BACKUP_DIR="${BACKUP_ROOT}/${DB_NAME}_$(date +%Y%m%d_%H%M%S)"

# =============== 脚本主体 ===============
set -e

# MYSQL_CMD：默认加 -N（不输出列名），用于各种 COUNT/SELECT 等内部查询
MYSQL_CMD="mysql -h${DB_HOST} -P${DB_PORT} -u${DB_USER} -p${DB_PASS} -N"
# MYSQL_CMD_VIEW：保留列名，用于 SHOW CREATE VIEW \G，便于通过列名提取定义
MYSQL_CMD_VIEW="mysql -h${DB_HOST} -P${DB_PORT} -u${DB_USER} -p${DB_PASS}"

DUMP_CMD="mysqldump -h${DB_HOST} -P${DB_PORT} -u${DB_USER} -p${DB_PASS} \
          --skip-comments --skip-add-drop-table --skip-triggers --single-transaction --quick"

# 检查备份根目录是否存在
if [ ! -d "${BACKUP_ROOT}" ]; then
    echo "错误: 备份根目录不存在: ${BACKUP_ROOT}，请先创建或修改 BACKUP_ROOT。"
    exit 1
fi

mkdir -p "${BACKUP_DIR}/schema" "${BACKUP_DIR}/data"
[ -z "${LOG_FILE}" ] && LOG_FILE="${BACKUP_DIR}/backup.log"
rotate_log_if_needed "${LOG_FILE}"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始备份数据库: ${DB_NAME}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 日志文件: ${LOG_FILE}"

# 获取数据库中所有对象：区分基表(BASE TABLE)和视图(VIEW)
ALL_BASE_TABLES=$(${MYSQL_CMD} -e "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='${DB_NAME}' AND TABLE_TYPE='BASE TABLE';" 2>/dev/null) || true
ALL_VIEWS=$(${MYSQL_CMD} -e "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='${DB_NAME}' AND TABLE_TYPE='VIEW';" 2>/dev/null) || true

if [ -z "${ALL_BASE_TABLES}" ] && [ -z "${ALL_VIEWS}" ]; then
    echo "错误: 无法从数据库 '${DB_NAME}' 获取表/视图列表。请检查配置。"
    exit 1
fi

# 通用的 -t/-i 过滤函数
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
    echo "${result}"
}

TABLES=$(filter_objects "${ALL_BASE_TABLES}")
VIEWS=$(filter_objects "${ALL_VIEWS}")

if [ -z "${TABLES}" ] && [ -z "${VIEWS}" ]; then
    echo "错误: 经过 -t/-i 过滤后，没有需要备份的表或视图。"
    exit 1
fi

# 1. 备份每个基表的结构 (不含数据)，使用 mysqldump
echo "正在备份表结构..."
for TABLE in ${TABLES}; do
    echo "  -> 备份表结构: ${TABLE}"
    ${DUMP_CMD} --no-data "${DB_NAME}" "${TABLE}" > "${BACKUP_DIR}/schema/${TABLE}.sql"
done

# 2. 备份视图结构：使用 SHOW CREATE VIEW 输出 DROP VIEW + CREATE VIEW（放在最后）
if [ -n "${VIEWS}" ]; then
    echo "正在备份视图结构..."
    for VIEW in ${VIEWS}; do
        echo "  -> 备份视图: ${VIEW}"
        # 使用带列名输出的 mysql（不加 -N），通过列名提取视图定义
        CREATESQL=$(${MYSQL_CMD_VIEW} -e "SHOW CREATE VIEW \`${DB_NAME}\`.\`${VIEW}\`\G" 2>/dev/null | sed -n 's/^[[:space:]]*Create View:[[:space:]]*//p')
        if [ -n "${CREATESQL}" ]; then
            # 去掉末尾分号，去除 ALGORITHM / DEFINER / SQL SECURITY 等前缀，只保留标准的 CREATE VIEW 语句
            CREATESQL=$(echo "${CREATESQL}" \
                | sed 's/;[[:space:]]*$//' \
                | sed -E 's/^CREATE[[:space:]]+.*[[:space:]]+VIEW[[:space:]]+/CREATE VIEW /I')
            # 去掉当前数据库名作为前缀：`db_name`.`xxx` -> `xxx`，但保留表别名（例如 `u`.`id`）
            CREATESQL=$(echo "${CREATESQL}" | sed -E 's/`'"${DB_NAME}"'`\.//g')
            {
                echo "DROP VIEW IF EXISTS \`${VIEW}\`;"
                echo "${CREATESQL};"
            } > "${BACKUP_DIR}/schema/${VIEW}.sql"
        else
            echo "  警告: 无法获取视图 ${VIEW} 的定义，跳过"
        fi
    done
    # 记录视图列表，供恢复时最后处理
    echo "${VIEWS}" | tr ' ' '\n' | grep -v '^$' > "${BACKUP_DIR}/schema/.views"
fi

# 3. 备份每个基表的数据 (不含结构)，大表拆分为多个 sql
echo "正在备份表数据..."
for TABLE in ${TABLES}; do
    # 获取表行数（InnoDB 为估计值，用于判断是否拆分）
    ROW_COUNT=$(${MYSQL_CMD} -e "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='${DB_NAME}' AND TABLE_NAME='${TABLE}';" 2>/dev/null) || echo "0"
    ROW_COUNT=${ROW_COUNT:-0}
    # 若无法获取或为 NULL，按大表处理以便拆分逻辑可用
    if [ -z "${ROW_COUNT}" ] || [ "${ROW_COUNT}" = "NULL" ]; then
        ROW_COUNT=$(${MYSQL_CMD} -e "SELECT COUNT(*) FROM \`${DB_NAME}\`.\`${TABLE}\`;" 2>/dev/null) || echo "0"
    fi

    if [ "${ROW_COUNT}" -le "${ROW_THRESHOLD}" ] 2>/dev/null; then
        # 小表：单文件
        echo "  -> 备份表数据: ${TABLE} (约 ${ROW_COUNT} 行, 单文件)"
        ${DUMP_CMD} --no-create-info "${DB_NAME}" "${TABLE}" > "${BACKUP_DIR}/data/${TABLE}.sql"
    else
        # 大表：尝试按主键 keyset 分页拆分（每次取实际有数据的批次，避免空文件）
        PK_COL=$(${MYSQL_CMD} -e "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE \
            WHERE TABLE_SCHEMA='${DB_NAME}' AND TABLE_NAME='${TABLE}' AND CONSTRAINT_NAME='PRIMARY' \
            ORDER BY ORDINAL_POSITION LIMIT 1;" 2>/dev/null) || true

        if [ -n "${PK_COL}" ]; then
            # 检查主键是否为数值类型（支持 keyset 分页）
            PK_TYPE=$(${MYSQL_CMD} -e "SELECT DATA_TYPE FROM information_schema.COLUMNS \
                WHERE TABLE_SCHEMA='${DB_NAME}' AND TABLE_NAME='${TABLE}' AND COLUMN_NAME='${PK_COL}';" 2>/dev/null) || true
            if echo "${PK_TYPE}" | grep -qiE '^(int|bigint|mediumint|smallint|tinyint|decimal|numeric)$'; then
                PART=1
                LAST_PK=""
                while true; do
                    if [ -z "${LAST_PK}" ]; then
                        BATCH_MM=$(${MYSQL_CMD} -e "SELECT MIN(\`${PK_COL}\`), MAX(\`${PK_COL}\`) FROM (SELECT \`${PK_COL}\` FROM \`${DB_NAME}\`.\`${TABLE}\` ORDER BY \`${PK_COL}\` LIMIT ${CHUNK_SIZE}) t;" 2>/dev/null) || true
                    else
                        BATCH_MM=$(${MYSQL_CMD} -e "SELECT MIN(\`${PK_COL}\`), MAX(\`${PK_COL}\`) FROM (SELECT \`${PK_COL}\` FROM \`${DB_NAME}\`.\`${TABLE}\` WHERE \`${PK_COL}\` > ${LAST_PK} ORDER BY \`${PK_COL}\` LIMIT ${CHUNK_SIZE}) t;" 2>/dev/null) || true
                    fi
                    BATCH_MIN=$(echo "${BATCH_MM}" | awk '{print $1}')
                    BATCH_MAX=$(echo "${BATCH_MM}" | awk '{print $2}')
                    if [ -z "${BATCH_MIN}" ] || [ -z "${BATCH_MAX}" ] || [ "${BATCH_MIN}" = "NULL" ] || [ "${BATCH_MAX}" = "NULL" ]; then
                        break
                    fi
                    if [ -z "${LAST_PK}" ]; then
                        WHERE="\`${PK_COL}\` >= ${BATCH_MIN} AND \`${PK_COL}\` <= ${BATCH_MAX}"
                    else
                        WHERE="\`${PK_COL}\` > ${LAST_PK} AND \`${PK_COL}\` <= ${BATCH_MAX}"
                    fi
                    PAD=$(printf "%04d" "${PART}")
                    echo "  -> 备份表数据: ${TABLE} 第 ${PART} 部分 (${PK_COL} ${BATCH_MIN}~${BATCH_MAX})"
                    ${DUMP_CMD} --no-create-info --where="${WHERE}" "${DB_NAME}" "${TABLE}" > "${BACKUP_DIR}/data/${TABLE}_${PAD}.sql"
                    # 检查是否为有效数据文件（避免空文件）
                    if [ ! -s "${BACKUP_DIR}/data/${TABLE}_${PAD}.sql" ] || ! grep -q "INSERT INTO" "${BACKUP_DIR}/data/${TABLE}_${PAD}.sql" 2>/dev/null; then
                        rm -f "${BACKUP_DIR}/data/${TABLE}_${PAD}.sql"
                        [ "${PART}" -eq 1 ] 2>/dev/null && PART=0
                        break
                    fi
                    LAST_PK="${BATCH_MAX}"
                    PART=$((PART + 1))
                done
                if [ "${PART}" -gt 1 ] 2>/dev/null; then
                    echo "${TABLE}_*.sql" > "${BACKUP_DIR}/data/.${TABLE}.split"
                    continue
                fi
            fi
        fi

        # 无主键、主键非数值、或 keyset 分页失败：按行 dump 后拆分，并合并为多行 INSERT
        echo "  -> 备份表数据: ${TABLE} (约 ${ROW_COUNT} 行, 按行拆分，每 INSERT ${INSERT_BATCH} 行)"
        TEMP_FILE="${BACKUP_DIR}/data/${TABLE}.tmp.sql"
        ${DUMP_CMD} --no-create-info --extended-insert=false "${DB_NAME}" "${TABLE}" > "${TEMP_FILE}"
        # 按行数拆分，然后将每 INSERT_BATCH 行合并为一个多行 INSERT（提升还原性能）
        split -l "${CHUNK_SIZE}" -a 4 -d "${TEMP_FILE}" "${BACKUP_DIR}/data/${TABLE}_"
        rm -f "${TEMP_FILE}"
        for F in "${BACKUP_DIR}/data/${TABLE}_"[0-9]*; do
            [ -f "${F}" ] || continue
            # 将单行 INSERT 合并为多行 INSERT（每 INSERT_BATCH 行一个 INSERT）
            awk -v batch="${INSERT_BATCH}" '
                /^INSERT INTO/ {
                    pos = index($0, "VALUES ")
                    if (pos > 0) {
                        rest = substr($0, pos + 7)
                        sub(/;[[:space:]]*$/, "", rest)
                        if (rest != "" && substr(rest, 1, 1) == "(") {
                            vals = rest
                            if (n == 0) prefix = substr($0, 1, pos + 6)
                            buf = (buf == "" ? vals : buf "," vals)
                            n++
                            if (n >= batch) {
                                print prefix buf ";"
                                buf = ""; n = 0
                            }
                        }
                    }
                }
                END { if (n > 0 && prefix != "") print prefix buf ";" }
            ' "${F}" > "${F}.merged" && mv "${F}.merged" "${F}"
            mv "${F}" "${F}.sql"
        done
        echo "${TABLE}_*.sql" > "${BACKUP_DIR}/data/.${TABLE}.split"
    fi
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 备份完成！"
echo "备份目录: ${BACKUP_DIR}"
echo "  - 表结构: ${BACKUP_DIR}/schema/"
echo "  - 表数据: ${BACKUP_DIR}/data/"
echo "  - 拆分的表会生成 .split 标记文件，恢复时需按顺序执行对应 *_*.sql"
echo "  - 按行拆分的表：每个 INSERT 合并 ${INSERT_BATCH} 行以提升还原性能"
echo "  - 日志: ${LOG_FILE}"

# 若指定了 -c，则清理指定天数前的旧备份（按目录修改时间 -mmin，且仅清理当前数据库 ${DB_NAME}_* 的备份）
if [ -n "${CLEAN_DAYS}" ] && [ "${CLEAN_DAYS}" -gt 0 ] 2>/dev/null; then
    MINUTES_AGO=$((CLEAN_DAYS * 24 * 60))
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 正在清理数据库 ${DB_NAME} 的 ${CLEAN_DAYS} 天（${MINUTES_AGO} 分钟）前的旧备份..."
    DELETED=0
    while IFS= read -r old_dir; do
        [ -z "${old_dir}" ] || [ ! -d "${old_dir}" ] && continue
        rm -rf "${old_dir}"
        echo "  -> 已删除: ${old_dir}"
        DELETED=$((DELETED + 1))
    done < <(find "${BACKUP_ROOT}" -maxdepth 1 -mindepth 1 -type d -name "${DB_NAME}_*" -mmin +"${MINUTES_AGO}" 2>/dev/null)
    [ "${DELETED}" -eq 0 ] && echo "  (无满足条件的旧备份)"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 旧备份清理完成。"
fi

