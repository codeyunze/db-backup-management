# 数据库备份管理

包含 MySQL 备份/还原脚本的 Docker 镜像，内置 mysql、mysqldump、Python3，提供 HTTP API 接口。

![image-20260314162628225](docs/images/image-20260314162628225.png)


## 运行服务

对外提供备份与还原接口，以及可视化界面。

你可以直接拉取已经构建好的镜像：

```bash
# 从 Docker Hub 拉取
docker pull codeyunze/db-backup-management:latest

# 从阿里云 ACR 拉取
docker pull registry.cn-guangzhou.aliyuncs.com/devyunze/db-backup-management:latest
```

运行容器示例（任选其一镜像地址）：

```bash
docker run -d -p 8081:8081 \
  -v /宿主机/备份目录:/data/backup/mysql \
  --name db-backup \
  codeyunze/db-backup-management:latest
```

启动后，访问 `http://localhost:8081/` 即可使用 Web 可视化管理界面。


### Web 管理界面

访问 `http://localhost:8081/` 可使用可视化界面：

- **数据备份**  
  - 支持 **全量备份** 与 **增量备份** 两种模式  
    - 全量备份：对单个数据库按表进行一次完整备份  
    - 增量备份：基于某次全量备份，按 binlog 位点生成从“上一次备份结束”到“当前”的变更 SQL，形成连续的增量链  
  - 支持按表白名单 / 黑名单过滤、自动清理 N 天前旧备份、可选 gzip 压缩（支持边备份边压缩，直接生成 `.sql.gz`）
- **备份列表**：查看已有全量备份，支持按数据库名筛选，并可查看其后续增量备份列表
- **数据还原**：  
  - 仅选全量目录：执行全量还原  
  - 同时选全量目录和增量目录：自动执行“全量 + 从第一个增量到所选增量（含）的所有增量”组合还原  
  - 出于 binlog 还原语义限制，**增量还原目前仅支持还原到与备份时相同的数据库名**（UI 会在库名不一致时禁用“执行还原”按钮并给出提示）
- **数据库实例信息**（新增）  
  - 集中管理多套数据库连接配置（名称、主机、端口、用户名、密码、数据库名），保存在 `backup-plans.json` 中  
  - 在“数据备份”“数据还原”中可通过下拉框选择实例，一键带出连接信息，无需重复填写  
  - 支持实例级“测试连接”，删除实例前会检查是否仍存在关联定时任务
- **任务调度**（新增）  
  - 基于某条“数据库实例信息”配置定时全量备份任务（备份类型、表过滤、清理天数、是否 gzip、cron 表达式等）  
  - 状态支持“运行/停止”，运行中由容器内 cron + `mysql-backup-schema-data.sh` 自动按点执行备份  
  - 支持在 UI 中新增 / 编辑 / 运行 / 停止 / 删除任务，并查看单任务运行日志  
  - 所有调度任务以 `jobs` 字段存储在 `backup-plans.json` 中；运行态会在 `/data/backup/mysql/jobs/` 下生成对应 `job_xxx.sh`，并在 `/data/backup/mysql/job-logs/` 下记录触发日志与脚本输出  

> 关于“数据库实例信息”和“任务调度”的完整介绍与截图，可参考 `docs/backup-tool-share-instance-and-schedule.md`。


## 自行构建镜像

```bash
cd db-backup-management
docker build -t db-backup-management:latest .
```

若官方源出现 502，可使用国内镜像构建：

```bash
# 阿里云镜像
docker build --build-arg APT_MIRROR=aliyun -t db-backup-management:latest .

# 清华镜像
docker build --build-arg APT_MIRROR=tsinghua -t db-backup-management:latest .
```

## 挂载说明

| 容器路径 | 说明 |
|---------|------|
| `/scripts` | 备份与还原脚本，可挂载宿主机脚本覆盖镜像内默认脚本 |
| `/data/backup/mysql` | 备份文件存储目录，建议挂载宿主机目录持久化 |

### HTTP 接口

**POST /db/test-connection** - 测试数据库连接

```bash
curl -X POST http://localhost:8081/db/test-connection -H "Content-Type: application/json" -d '{"host":"127.0.0.1","port":3306,"user":"root","password":"密码","database":"mall"}'
```

**POST /db/backup** - 执行全量备份

```bash
curl -X POST http://localhost:8081/db/backup -H "Content-Type: application/json" -d '{"host":"MySQL主机","port":3306,"user":"root","password":"密码","database":"mall","backup_dir":"/data/backup/mysql"}'
```

可选参数：`tables`（白名单表）、`ignore_tables`（黑名单表）、`clean_days`（清理 N 天前备份）

**POST /db/backup-incremental** - 基于某次全量备份执行一次 binlog 增量备份

```bash
curl -X POST http://localhost:8081/db/backup-incremental \
  -H "Content-Type: application/json" \
  -d '{
    "host": "MySQL主机",
    "port": 3306,
    "user": "root",
    "password": "密码",
    "database": "mall",
    "full_backup_dir": "/data/backup/mysql/mall_20260302_222859"
  }'
```

- 不传 `start_file` / `start_pos` 时，起始位点规则为：
  - 若该全量备份目录下**已有增量备份**：从“最后一个增量”的 `meta/binlog_to.json` 中读取结束位点，作为本次增量的起点，形成连续增量链（全量 → inc1 → inc2 → …）；  
  - 否则：从该全量备份目录的 `meta/tables-binlog.json` 中选择最新的记录作为起点。
- 每次增量备份会在对应全量目录下创建：  
  - `<full_backup_dir>/incremental/<db>_inc_YYYYMMDD_HHMMSS/changes.sql`  
  - `meta/binlog_from.json`、`meta/binlog_to.json` 记录起止位点与时间。

**GET /db/incrementals** - 查询某次全量备份下的增量备份列表

```bash
curl "http://localhost:8081/db/incrementals?full_backup_dir=/data/backup/mysql/mall_20260302_222859"
```

返回：该全量备份目录下 `incremental/` 子目录中的所有增量，包含：
- `database`：增量所属数据库名
- `incrementalDir`：增量目录绝对路径
- `binlogFrom` / `binlogTo`：起始/结束位点及时间

**POST /db/restore** - 执行还原

```bash
curl -X POST http://localhost:8081/db/restore \
  -H "Content-Type: application/json" \
  -d '{
    "backup_dir":"/data/backup/mysql/mall_20260302_222859",
    "target_db":"mall",
    "host":"MySQL主机",
    "user":"root",
    "password":"密码",
    "incremental_dir":"/data/backup/mysql/mall_20260302_222859/incremental/mall_inc_20260303_101010"
  }'
```

- 不传 `incremental_dir`：仅执行全量还原；
- 传入某个 `incremental_dir`：后端会：
  - 在该全量备份目录下按时间升序获取所有增量目录；
  - 从第一个增量开始一直到所选增量（含）形成一条连续链；
  - 调用 `mysql-restore-incremental.sh`，内部先执行全量还原，再按顺序回放这条链上的每个 `changes.sql`。

可选参数：`tables`（仅恢复指定表）、`ignore_tables`（不恢复的表）、`overwrite_tables`（覆盖的表）——仅在纯全量还原时生效，含增量还原时会忽略这些表级过滤。

**GET /db/backups** - 查询已备份文件列表

```bash
curl "http://localhost:8081/db/backups"
# 按数据库名筛选
curl "http://localhost:8081/db/backups?database=mall"
```

返回：`database`、`backupTime`、`backupDir`、`dirName`、`size` 等

**GET /db/backups/<dir_name>/tables** - 获取备份包含的表/视图列表

**DELETE /db/backups/<dir_name>** - 删除指定备份

```bash
curl -X DELETE "http://localhost:8081/db/backups/mall_20250209_020000"
```

**GET /db/backups/<dir_name>/log** - 获取某次备份的备份/还原日志（前端用于“备份日志/还原日志”弹窗）

```bash
curl "http://localhost:8081/db/backups/mall_20260302_222859/log?type=backup"
curl "http://localhost:8081/db/backups/mall_20260302_222859/log?type=restore"
```

---

### HTTP 接口：数据库实例信息与任务调度

**GET /backup-plans** - 列出所有数据库实例信息（不返回密码）

```bash
curl "http://localhost:8081/backup-plans"
```

**POST /backup-plans** - 新增数据库实例信息

```bash
curl -X POST http://localhost:8081/backup-plans \
  -H "Content-Type: application/json" \
  -d '{
    "name": "mall-dev",
    "host": "43.138.193.177",
    "port": 3306,
    "user": "root",
    "password": "密码",
    "database": "mall",
    "backup_dir": "/data/backup/mysql"
  }'
```

**GET /backup-plans/<plan_id>** - 获取单个实例详情（包含密码，供前端填充表单）

```bash
curl "http://localhost:8081/backup-plans/plan_1773382601991"
```

**PUT /backup-plans/<plan_id>** - 更新实例信息（仅连接相关字段）

```bash
curl -X PUT http://localhost:8081/backup-plans/plan_1773382601991 \
  -H "Content-Type: application/json" \
  -d '{ "host": "127.0.0.1", "port": 3307 }'
```

**DELETE /backup-plans/<plan_id>** - 删除实例（若仍存在 jobs 会返回 400 并拒绝删除）

```bash
curl -X DELETE "http://localhost:8081/backup-plans/plan_1773382601991"
```

**POST /backup-plans/<plan_id>/jobs** - 在某实例下新增一条定时备份任务

```bash
curl -X POST http://localhost:8081/backup-plans/plan_1773382601991/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "每天凌晨1点全量备份mall数据库",
    "schedule": "0 1 * * *",
    "backup_type": "full",
    "tables": "",
    "ignore_tables": "",
    "clean_days": 0,
    "enable_gzip": true,
    "enabled": false
  }'
```

**PUT /backup-plans/<plan_id>/jobs/<job_id>** - 更新定时任务（名称、cron、表过滤、清理天数、gzip、enabled 等）

```bash
curl -X PUT http://localhost:8081/backup-plans/plan_1773382601991/jobs/job_1773394757637 \
  -H "Content-Type: application/json" \
  -d '{ "schedule": "0 2 * * *", "enabled": true }'
```

**DELETE /backup-plans/<plan_id>/jobs/<job_id>** - 删除定时任务（运行中任务不允许删除）

```bash
curl -X DELETE "http://localhost:8081/backup-plans/plan_1773382601991/jobs/job_1773394757637"
```

**GET /scheduled-tasks** - 查询所有定时备份任务列表（聚合自 `backup-plans.json` 中的 `plans[].jobs[]`）

```bash
curl "http://localhost:8081/scheduled-tasks"
```

**GET /scheduled-tasks/<job_id>/log** - 查看指定任务的合并日志（元事件 + 运行输出）

```bash
curl "http://localhost:8081/scheduled-tasks/job_1773394757637/log"
```

**GET /db/backup-options** - 查询某次全量备份的表过滤条件（供增量备份 UI 继承）

```bash
curl "http://localhost:8081/db/backup-options?full_backup_dir=/data/backup/mysql/mall_20260302_222859"
```

**GET /health** - 健康检查


## 脚本说明

### `mysql-backup-schema-data.sh`（备份脚本）

- **功能**：备份单个 MySQL 数据库的表结构（含视图）和数据，支持大表拆分、多文件备份以及按库名、表名粒度控制。
- **输出目录结构**：`<BACKUP_ROOT>/<数据库名>_YYYYMMDD_HHMMSS/{schema,data,backup.log}`。
- **调用方式**：
  - 容器内直接执行：`bash /scripts/mysql-backup-schema-data.sh [选项]`
  - 通过 HTTP 接口：`POST /db/backup` 会在容器内调用该脚本并传递对应参数。

#### 动态参数（命令行可设置）

| 参数名 | 命令行选项 | 脚本默认值 | 说明 |
|--------|------------|------------|------|
| `DB_HOST` | `-H, --host` | `127.0.0.1` | MySQL 主机地址 |
| `DB_PORT` | `-P, --port` | `3306` | MySQL 端口 |
| `DB_USER` | `-u, --user` | `root` | 连接数据库的用户名 |
| `DB_PASS` | `-p, --password` | `123456` | 对应用户密码 |
| `DB_NAME` | `-d, --database` | `db_name` | 要备份的数据库名 |
| `BACKUP_ROOT` | `-b, --backup-dir` | `/data/backup/mysql` | 备份根目录（不含时间戳） |
| `TABLES_INCLUDE` | `-t, --tables` | 空 | 仅备份指定表，多个表用逗号分隔 |
| `TABLES_EXCLUDE` | `-i, --ignore` | 空 | 不备份的表，多个表用逗号分隔；优先级高于 `TABLES_INCLUDE` |
| `CLEAN_DAYS` | `-c, --clean` | 空（不清理） | 备份完成后清理当前数据库在 `BACKUP_ROOT` 下 **N 天前** 的旧备份目录（按目录修改时间，单位天） |

> 通过 HTTP 接口 `POST /db/backup` 传入的 `host/port/user/password/database/backup_dir/tables/ignore_tables/clean_days`，由后端映射为上述命令行参数。

#### 可调整参数（脚本内配置区）

这些参数在脚本开头的“配置区”中定义，可按环境性能和日志需求手动修改：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ROW_THRESHOLD` | `100000` | 单表行数超过该值时，按多文件拆分备份 |
| `CHUNK_SIZE` | `50000` | 每个数据分片文件最多包含的行数（影响单个 `.sql` 文件大小） |
| `INSERT_BATCH` | `500` | 拆分备份时，每个多行 `INSERT` 中的记录条数，数值越大还原越快但单条 SQL 越长 |
| `LOG_FILE` | 空 | 备份日志文件路径；为空时使用当前备份目录下的 `backup.log` |
| `LOG_SIZE_LIMIT_MB` | `10` | 日志文件超过该大小（MB）时自动轮转为 `*.bak` 再继续写入 |


### `mysql-restore-schema-data.sh`（全量还原脚本）

- **功能**：从 `mysql-backup-schema-data.sh` 生成的备份目录中恢复数据库，支持表/视图分开还原、大表多文件顺序还原，以及按表名白名单 / 黑名单 / 覆盖策略控制。
- **输入目录结构**：需要指向包含 `schema/`、`data/`、`backup.log`/`restore.log` 等文件的备份目录。
- **调用方式**：
  - 容器内直接执行：`bash /scripts/mysql-restore-schema-data.sh [选项]`
  - 通过 HTTP 接口：`POST /db/restore` 会在容器内调用该脚本并传递对应参数。

#### 动态参数（命令行可设置）

| 参数名 | 命令行选项 | 脚本默认值 | 说明 |
|--------|------------|------------|------|
| `BACKUP_DIR` | `-b, --backup-dir`（或位置参数 1） | 无默认值，必填 | 备份目录路径，如 `/data/backup/mysql/mall_20250209_020000` |
| `NEW_DB_NAME` | `-d, --database`（或位置参数 2） | 无默认值，必填 | 恢复到的新数据库名，如 `mall_restored` |
| `DB_HOST` | `-H, --host` | `localhost` | MySQL 主机地址 |
| `DB_PORT` | `-P, --port` | `3306` | MySQL 端口 |
| `DB_USER` | `-u, --user` | `root` | 恢复操作使用的数据库用户，需要有建库/建表权限 |
| `DB_PASS` | `-p, --password` | `123456` | 对应用户密码 |
| `LOG_FILE` | `-l, --log-file` | 空 | 还原日志路径；为空时使用备份目录下的 `restore.log` |
| `LOG_SIZE_LIMIT_MB` | `-L, --log-limit` | `10` | 日志文件超过该大小（MB）时轮转备份 |
| `TARGET_TABLES` | `-t, --tables` | 空 | 仅恢复指定表，多个表用逗号分隔 |
| `IGNORE_TABLES` | `-i, --ignore` | 空 | 不恢复的表，多个表用逗号分隔；与 `TARGET_TABLES` 同时使用时从白名单中排除 |
| `OVERWRITE_TABLES` | `-o, --overwrite` | 空 | 用备份数据覆盖的表名列表；若仅指定 `-o` 未指定 `-t`，则自动把 `OVERWRITE_TABLES` 作为 `TARGET_TABLES` |

#### 可调整参数（脚本内配置区）

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DB_HOST` | `localhost` | 还原脚本默认连接的主机，可通过命令行覆盖 |
| `DB_PORT` | `3306` | 默认端口，可通过命令行覆盖 |
| `DB_USER` | `root` | 默认还原用户，可通过命令行覆盖 |
| `DB_PASS` | `123456` | 默认密码，可通过命令行覆盖 |
| `LOG_FILE` | 空 | 默认还原日志路径，通常保持为空使用备份目录下的 `restore.log` |
| `LOG_SIZE_LIMIT_MB` | `10` | 日志文件超过该大小（MB）时自动轮转为 `*.bak` |

> 日志行为：每次还原会在 `restore.log` 中追加一段带分隔线的记录；当“全量 + 增量还原”时，增量脚本会复用同一份 `restore.log`，整个流程日志连贯。

### `mysql-backup-incremental.sh`（增量备份脚本）

- **功能**：基于某次已完成的全量备份，从指定 binlog 起始位点开始抽取变更并生成增量 SQL 文件 `changes.sql`，同时记录起止位点元数据。
- **目录结构**：  
  - 全量备份目录：`/data/backup/mysql/<db>_YYYYMMDD_HHMMSS/`  
  - 增量备份目录：`/data/backup/mysql/<db>_YYYYMMDD_HHMMSS/incremental/<db>_inc_YYYYMMDD_HHMMSS/`
    - `changes.sql`：当前增量的 binlog 变更（按 `--database=<db>` 过滤）
    - `meta/binlog_from.json`：起始 `binlog_file` / `binlog_pos` / `recorded_at` 等
    - `meta/binlog_to.json`：结束 `binlog_file` / `binlog_pos` / `recorded_at` 等
- **起点选择策略**（不显式指定 `--start-file/--start-pos` 时）：  
  1. 若该全量目录下存在历史增量：从“最后一个增量的 `binlog_to`”开始，此时增量链为：**全量 → inc1 → inc2 → … → 本次 incN**；  
  2. 若不存在历史增量：从全量备份目录 `meta/tables-binlog.json` 中记录的最新位点开始。
- **调用方式**：
  - 容器内直接执行：`bash /scripts/mysql-backup-incremental.sh [选项]`
  - 通过 HTTP 接口：`POST /db/backup-incremental`。

### `mysql-restore-incremental.sh`（全量 + 增量组合还原脚本）

- **功能**：基于某次全量备份 + 一条连续的增量链，自动完成“先全量还原，再顺序回放增量变更”的组合还原。
- **输入参数**（内部由 `/db/restore` 构造）：  
  - `-b, --full-backup-dir`：全量备份目录  
  - `-d, --database`：目标数据库名（当前版本要求与备份时数据库同名）  
  - `-i, --incremental-dirs`：按时间顺序排列的一组增量目录，逗号分隔  
  - 连接信息、日志路径等。
- **核心步骤**：
  1. 解析首个增量的 `meta/binlog_from.json` 获取来源数据库名；
  2. 调用 `mysql-restore-schema-data.sh` 执行一次全量还原（日志写入同一个 `restore.log`）；
  3. 对每个增量目录依次执行 `changes.sql`：
     - 若来源库名与目标库名不同，会在应用前对 `changes.sql` 做轻量重写（调整 `USE \`db\`;` 和 `` `db`. `` 前缀），以尽量保证变更落在目标库；
     - 受 MySQL binlog 语义影响，当前版本仍要求“增量还原仅支持同名库”，异名目标库仅作为内部过渡方案使用，UI 层已做限制。
