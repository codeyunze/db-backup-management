# 数据库备份管理

包含 MySQL 备份/还原脚本的 Docker 镜像，内置 mysql、mysqldump、Python3，提供 HTTP API 接口。

## 构建镜像

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

## 运行 API 服务

默认启动 Flask API，对外提供备份与还原接口：

```bash
docker run -d -p 8081:8081 -v /宿主机/备份目录:/data/backup/mysql --name db-backup db-backup-management:latest
```

### Web 管理界面

访问 `http://localhost:8081/` 可使用可视化界面：
- **新建备份**：填写数据库连接信息后执行备份
- **备份列表**：查看已有备份，支持按数据库名筛选
- **数据还原**：选择备份目录并填写目标数据库连接信息后执行还原

### HTTP 接口

**POST /db/test-connection** - 测试数据库连接

```bash
curl -X POST http://localhost:8081/db/test-connection -H "Content-Type: application/json" -d '{"host":"127.0.0.1","port":3306,"user":"root","password":"密码","database":"mall"}'
```

**POST /db/backup** - 执行备份

```bash
curl -X POST http://localhost:8081/db/backup -H "Content-Type: application/json" -d '{"host":"MySQL主机","port":3306,"user":"root","password":"密码","database":"mall","backup_dir":"/data/backup/mysql"}'
```

可选参数：`tables`（白名单表）、`ignore_tables`（黑名单表）、`clean_days`（清理 N 天前备份）

**POST /db/restore** - 执行还原

```bash
curl -X POST http://localhost:8081/db/restore -H "Content-Type: application/json" -d '{"backup_dir":"/data/backup/mysql/mall_20250209_020000","target_db":"mall_restored","host":"MySQL主机","user":"root","password":"密码"}'
```

可选参数：`tables`（仅恢复指定表）、`ignore_tables`（不恢复的表）、`overwrite_tables`（覆盖的表）

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


### `mysql-restore-schema-data.sh`（还原脚本）

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
