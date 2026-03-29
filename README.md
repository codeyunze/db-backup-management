# 数据库备份管理

包含 MySQL 备份/还原脚本的 Docker 镜像，底层由 mydumper提供备份、myloader提供还原、Vben Admin前端框架、Python3提供 HTTP API 接口。

![image-20260329111833454](docs/images/image-20260329111833454.png)

![image-20260329113433319](docs/images/image-20260329113433319.png)

![image-20260329111930275](docs/images/image-20260329111930275.png)

![image-20260329112722769](docs/images/image-20260329112722769.png)

![image-20260329112918939](docs/images/image-20260329112918939.png)

![image-20260329113153633](docs/images/image-20260329113153633.png)



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
docker run -d -p 5555:5555 \
  -v "/宿主机/备份目录/backup_data:/app/backup_data" \
  --name db-backup \
  codeyunze/db-backup-management:latest
```

启动后，访问 `http://localhost:5555/` 即可使用 Web 可视化管理界面。

默认账号：admin

默认密码：123456

也可自行注册其他账号


### Web 管理界面

访问 `http://localhost:5555/` 可使用可视化界面：

- **数据备份**  
  - 支持 **全量备份** 与 **增量备份** 两种模式  
    - **全量备份**：使用 **mydumper** 对单个库按表导出（默认 `*.sql.zst` 等），会话目录落在持久化卷 `backup_data/data/` 下  
    - **增量备份**：基于某次全量备份目录为基线，按 binlog 位点生成从「上一次备份结束」到「当前」的变更 SQL，形成连续增量链  
  - 支持按表白名单 / 黑名单过滤、自动清理 N 天前旧备份（`clean_days`）
- **备份列表**：查看已有全量备份，支持按关键字筛选，并可展开查看该全量下的增量备份列表与日志
- **数据还原**：  
  - 仅选全量目录：使用 **myloader** 执行全量还原  
  - 同时选择全量与某一增量目录：自动执行「全量 + 从第一个增量到所选增量（含）」的组合还原  
  - 出于 binlog 还原语义限制，**增量还原目前仅支持还原到与备份时相同的数据库名**（UI 会在库名不一致时禁用「执行还原」并提示）
- **数据库实例**  
  - 集中管理多套连接配置（名称、主机、端口、用户、密码、库名），持久化在 **`backup_data/json/db-instances.json`**（按登录账号隔离可见数据）  
  - 在「数据备份」「数据还原」等流程中可下拉选择实例，一键带出连接信息  
  - 支持「测试连接」；删除实例前会检查是否仍有定时任务引用该实例
- **任务调度**  
  - 为指定实例配置 **Cron 表达式**、**全量或增量**备份、表过滤、`clean_days` 等；增量任务需关联一条全量定时任务作为基线  
  - 任务配置保存在 **`backup_data/json/backup-jobs.json`**；启用后由系统 **crontab** 按点执行 **`/app/backup_data/jobs/<job_id>.sh`**（与宿主机挂载目录对应为 `backup_data/jobs/`），脚本通过 `curl` 调用 **`POST /api/backup-jobs/<id>/execute`** 触发后端执行备份（与页面手动备份同源逻辑）  
  - UI 支持新增 / 编辑 / 运行 / 停止 / 删除任务，以及查看单任务调度与执行日志（如 **`backup_data/jobs/logs/`**，容器内 `/app/backup_data/jobs/logs/`）  

> 关于「数据库实例」与「任务调度」的更细说明与截图，可参考 `docs/backup-tool-share-instance-and-schedule.md`；增量定时与链式关系可参考 `docs/backup-tool-scheduled-incremental.md`。


## 自行构建镜像

```bash
cd db-backup-management
docker build -t db-backup-management:latest .
```

## 挂载说明

| 容器路径 | 说明 |
|---------|------|
| /app/backup_data | 账号与鉴权、数据库实例配置、定时任务配置、备份文件元数据、备份数据目录及 `jobs/` 定时脚本等，建议挂载宿主机目录持久化 |

## 数据存储结构

### 备份数据目录结构

```
backup_data/
├── jobs/             # 定时任务：crontab 调用的 job_xxx.sh 与 logs/ 执行日志
├── data/             # 备份数据存储目录
│   ├── mall_20260325_230118/  # 备份会话目录（格式：数据库名_年月日_时分秒）
│   │   ├── data/     # 数据文件目录
│   │   │   ├── mall-schema-create.sql.zst    		 # 数据库创建语句
│   │   │   ├── mall.base_tenant-schema.sql.zst    # 表结构文件
│   │   │   ├── mall.base_user-schema.sql.zst      # 表结构文件
│   │   │   ├── mall.base_user.00000.sql.zst       # 数据文件
│   │   │   ├── ...                  # 其他表结构和数据文件
│   │   │   └── metadata             # mydumper 元数据文件
│   │   └── meta/     # 元数据目录
│   │       └── backup-options.json  # 备份配置选项
│   └── ...           # 其他备份会话目录
└── json/             # 后端持久化数据
    ├── db-instances.json   # 数据库实例配置
    ├── backup-jobs.json    # 备份任务配置
    ├── backup-files.json   # 备份文件记录
    ├── account.json        # 用户账号信息
    └── timezone.json       # 用户时区设置
```

### 数据文件格式

- 表结构文件：`{数据库名}.{表名}-schema.sql.zst`
- 数据文件：`{数据库名}.{表名}.{分片号}.sql.zst`
- 数据库创建文件：`{数据库名}-schema-create.sql.zst`
- 元数据文件：`metadata`（包含表结构和数据信息）

## 登录认证方式

1. **怎么登录**  
   调用 `POST /api/auth/login`，响应里会带上 `accessToken`（浏览器里由前端自动处理；自己写脚本或 Apifox 时把 Token 记下来用即可）。

2. **怎么调需要登录的接口**  
   每个请求加请求头：`Authorization: Bearer <accessToken>`。

3. **定时任务专用接口（不用用户 Token）**  
   `POST /api/backup-jobs/<id>/execute` 给 **cron 里的 shell 脚本**用，**不要**带登录 Token。更安全的方式：设置环境变量 **`BACKUP_CRON_SECRET`**，脚本里用请求头 **`X-Backup-Cron-Secret`** 传同一个值。若不配密钥：仅当请求来自本机 **`127.0.0.1` / `::1`** 时，默认仍可能放行。

4. **Token 和登录加密密钥放哪**  
   会写到持久化目录 **`json/auth-tokens.json`**、**`json/rsa-login-sessions.json`**（与上文 `backup_data` 挂载对应）。这样多台后端进程轮流处理请求时，也不会再出现「RSA 无效」或「刚登录又像没登录」。

5. **Apifox / curl 测登录**  
   请求体只发 **`username`**、**`password`** 两个字段即可，不要从网页里复制加密后的字段（见下文示例）。

### 认证相关

| 接口                 | 方法 | 描述                              |
| -------------------- | ---- | --------------------------------- |
| `/api/auth/rsa`      | GET  | 获取 RSA 公钥（用于登录密码加密） |
| `/api/auth/login`    | POST | 登录，返回 accessToken            |
| `/api/auth/register` | POST | 注册新用户                        |
| `/api/auth/password` | POST | 修改当前登录用户密码              |
| `/api/auth/logout`   | POST | 退出登录                          |
| `/api/auth/refresh`  | POST | 刷新 accessToken                  |
| `/api/auth/codes`    | GET  | 获取认证码                        |
| `/api/user/info`     | GET  | 获取用户信息                      |
| `/api/menu/all`      | GET  | 获取菜单列表                      |

#### 认证相关接口请求案例

1. **获取 RSA 公钥** (`/api/auth/rsa`)

   ```bash
   curl -X GET http://localhost:8081/api/auth/rsa
   ```

2. **登录** (`/api/auth/login`)

   ```bash
   curl -X POST http://localhost:8081/api/auth/login \
     -H "Content-Type: application/json" \
     -d '{
       "username": "admin",
       "password": "123456"
     }'
   ```

3. **注册新用户** (`/api/auth/register`)

   ```bash
   curl -X POST http://localhost:8081/api/auth/register \
     -H "Content-Type: application/json" \
     -d '{
       "username": "user1",
       "password": "123456"
     }'
   ```

4. **修改密码** (`/api/auth/password`)

   ```bash
   curl -X POST http://localhost:8081/api/auth/password \
     -H "Authorization: Bearer <accessToken>" \
     -H "Content-Type: application/json" \
     -d '{
       "oldPassword": "123456",
       "newPassword": "654321"
     }'
   ```

5. **退出登录** (`/api/auth/logout`)

   ```bash
   curl -X POST http://localhost:8081/api/auth/logout \
     -H "Authorization: Bearer <accessToken>"
   ```

6. **刷新 accessToken** (`/api/auth/refresh`)

   ```bash
   curl -X POST http://localhost:8081/api/auth/refresh \
     -H "Cookie: refreshToken=<refreshToken>"
   ```

7. **获取认证码** (`/api/auth/codes`)

   ```bash
   curl -X GET http://localhost:8081/api/auth/codes \
     -H "Authorization: Bearer <accessToken>"
   ```

8. **获取用户信息** (`/api/user/info`)

   ```bash
   curl -X GET http://localhost:8081/api/user/info \
     -H "Authorization: Bearer <accessToken>"
   ```

9. **获取菜单列表** (`/api/menu/all`)

   ```bash
   curl -X GET http://localhost:8081/api/menu/all \
     -H "Authorization: Bearer <accessToken>"
   ```

### 数据库实例管理

| 接口                                | 方法   | 描述               |
| ----------------------------------- | ------ | ------------------ |
| `/api/db-instances`                 | GET    | 获取数据库实例列表 |
| `/api/db-instances`                 | POST   | 新增数据库实例     |
| `/api/db-instances/<id>`            | PUT    | 编辑数据库实例     |
| `/api/db-instances/<id>`            | DELETE | 删除数据库实例     |
| `/api/db-instances/test-connection` | POST   | 测试数据库连接     |
| `/api/db-instances/<id>/backup`     | POST   | 立即执行该实例备份 |
| `/api/db-instances/<id>/restore`    | POST   | 还原备份到目标库   |

#### 数据库实例管理接口请求案例

1. **获取数据库实例列表** (`/api/db-instances`)

   ```bash
   curl -X GET http://localhost:8081/api/db-instances \
     -H "Authorization: Bearer <accessToken>"
   ```

2. **新增数据库实例** (`/api/db-instances`)

   ```bash
   curl -X POST http://localhost:8081/api/db-instances \
     -H "Authorization: Bearer <accessToken>" \
     -H "Content-Type: application/json" \
     -d '{
       "id": "db_1",
       "name": "测试数据库",
       "host": "localhost",
       "port": 3306,
       "user": "root",
       "password": "123456",
       "database": "mall"
     }'
   ```

3. **编辑数据库实例** (`/api/db-instances/<id>`)

   ```bash
   curl -X PUT http://localhost:8081/api/db-instances/db_1 \
     -H "Authorization: Bearer <accessToken>" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "测试数据库（修改）",
       "host": "localhost",
       "port": 3306,
       "user": "root",
       "password": "123456",
       "database": "mall"
     }'
   ```

4. **删除数据库实例** (`/api/db-instances/<id>`)

   ```bash
   curl -X DELETE http://localhost:8081/api/db-instances/db_1 \
     -H "Authorization: Bearer <accessToken>"
   ```

5. **测试数据库连接** (`/api/db-instances/test-connection`)

   ```bash
   curl -X POST http://localhost:8081/api/db-instances/test-connection \
     -H "Authorization: Bearer <accessToken>" \
     -H "Content-Type: application/json" \
     -d '{
       "host": "localhost",
       "port": 3306,
       "user": "root",
       "password": "123456",
       "database": "mall"
     }'
   ```

6. **立即执行实例备份** (`/api/db-instances/<id>/backup`)

   ```bash
   curl -X POST http://localhost:8081/api/db-instances/db_1/backup \
     -H "Authorization: Bearer <accessToken>" \
     -H "Content-Type: application/json" \
     -d '{}'
   ```

7. **还原备份到目标库** (`/api/db-instances/<id>/restore`)

   ```bash
   curl -X POST http://localhost:8081/api/db-instances/db_1/restore \
     -H "Authorization: Bearer <accessToken>" \
     -H "Content-Type: application/json" \
     -d '{
       "dir_name": "mall_20260325_230118",
       "target_database": "mall"
     }'
   ```

### 备份任务管理

| 接口                            | 方法         | 描述                                    |
| ------------------------------- | ------------ | --------------------------------------- |
| `/api/backup-jobs`              | GET          | 获取备份任务列表                        |
| `/api/backup-jobs`              | POST         | 新增备份任务                            |
| `/api/backup-jobs/<id>`         | PUT          | 编辑备份任务                            |
| `/api/backup-jobs/delete/<id>`  | POST（推荐） | 删除备份任务；勿在浏览器地址栏 GET 访问 |
| `/api/backup-jobs/<id>`         | DELETE       | 删除备份任务（兼容旧版客户端）          |
| `/api/backup-jobs/<id>/run`     | POST         | 运行备份任务                            |
| `/api/backup-jobs/<id>/stop`    | POST         | 停止备份任务                            |
| `/api/backup-jobs/<id>/execute` | POST         | 执行备份任务（供定时任务调用）          |
| `/api/backup-jobs/<id>/log`     | GET          | 查看备份任务日志                        |

#### 备份任务管理接口请求案例

1. **获取备份任务列表** (`/api/backup-jobs`)

   ```bash
   curl -X GET http://localhost:8081/api/backup-jobs \
     -H "Authorization: Bearer <accessToken>"
   ```

2. **新增备份任务** (`/api/backup-jobs`)

   ```bash
   curl -X POST http://localhost:8081/api/backup-jobs \
     -H "Authorization: Bearer <accessToken>" \
     -H "Content-Type: application/json" \
     -d '{
       "id": "job_1",
       "name": "每日备份",
       "schedule": "0 0 * * *",
       "backup_type": "full",
       "db_instance_id": "db_1",
       "clean_days": 7,
       "enabled": true
     }'
   ```

3. **编辑备份任务** (`/api/backup-jobs/<id>`)

   ```bash
   curl -X PUT http://localhost:8081/api/backup-jobs/job_1 \
     -H "Authorization: Bearer <accessToken>" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "每日备份（修改）",
       "schedule": "0 1 * * *",
       "backup_type": "full",
       "db_instance_id": "db_1",
       "clean_days": 10,
       "enabled": true
     }'
   ```

4. **删除备份任务**（`POST /api/backup-jobs/delete/<id>`，推荐；或 `DELETE /api/backup-jobs/<id>` 兼容旧版）

   ```bash
   curl -X POST http://localhost:8081/api/backup-jobs/delete/job_1 \
     -H "Authorization: Bearer <accessToken>"
   ```

   若在浏览器地址栏直接打开删除 URL，浏览器会发 **GET**，接口会返回 **405** 与 JSON 提示（不会执行删除）；必须用 **POST** 或兼容的 **DELETE**。

5. **运行备份任务** (`/api/backup-jobs/<id>/run`)

   ```bash
   curl -X POST http://localhost:8081/api/backup-jobs/job_1/run \
     -H "Authorization: Bearer <accessToken>"
   ```

6. **停止备份任务** (`/api/backup-jobs/<id>/stop`)

   ```bash
   curl -X POST http://localhost:8081/api/backup-jobs/job_1/stop \
     -H "Authorization: Bearer <accessToken>"
   ```

7. **执行备份任务（定时任务调用）** (`/api/backup-jobs/<id>/execute`)

   ```bash
   curl -X POST http://localhost:8081/api/backup-jobs/job_1/execute \
     -H "X-Backup-Cron-Secret: <secret>"
   ```

8. **查看备份任务日志** (`/api/backup-jobs/<id>/log`)

   ```bash
   curl -X GET http://localhost:8081/api/backup-jobs/job_1/log \
     -H "Authorization: Bearer <accessToken>"
   ```

### 备份文件管理

| 接口                                   | 方法   | 描述               |
| -------------------------------------- | ------ | ------------------ |
| `/api/backup-files`                    | GET    | 获取备份文件列表   |
| `/api/backup-files/<dirName>`          | DELETE | 删除备份文件       |
| `/api/backup-files/<dirName>/download` | GET    | 下载备份文件       |
| `/api/backup-files/<dirName>/tables`   | GET    | 查看备份文件中的表 |
| `/api/backup-files/<dirName>/logs`     | GET    | 查看备份文件的日志 |

#### 备份文件管理接口请求案例

1. **获取备份文件列表** (`/api/backup-files`)

   ```bash
   curl -X GET http://localhost:8081/api/backup-files \
     -H "Authorization: Bearer <accessToken>"
   ```

2. **删除备份文件** (`/api/backup-files/<dirName>`)

   ```bash
   curl -X DELETE http://localhost:8081/api/backup-files/mall_20260325_230118 \
     -H "Authorization: Bearer <accessToken>"
   ```

3. **下载备份文件** (`/api/backup-files/<dirName>/download`)

   ```bash
   curl -X GET http://localhost:8081/api/backup-files/mall_20260325_230118/download \
     -H "Authorization: Bearer <accessToken>" \
     -o backup.tar.gz
   ```

4. **查看备份文件中的表** (`/api/backup-files/<dirName>/tables`)

   ```bash
   curl -X GET http://localhost:8081/api/backup-files/mall_20260325_230118/tables \
     -H "Authorization: Bearer <accessToken>"
   ```

5. **查看备份文件的日志** (`/api/backup-files/<dirName>/logs`)

   ```bash
   curl -X GET http://localhost:8081/api/backup-files/mall_20260325_230118/logs \
     -H "Authorization: Bearer <accessToken>"
   ```

### 系统管理

| 接口                           | 方法 | 描述                 |
| ------------------------------ | ---- | -------------------- |
| `/api/system/role/list`        | GET  | 获取角色列表         |
| `/api/system/menu/list`        | GET  | 获取菜单列表         |
| `/api/system/menu/name-exists` | GET  | 检查菜单名称是否存在 |
| `/api/system/menu/path-exists` | GET  | 检查菜单路径是否存在 |
| `/api/system/dept/list`        | GET  | 获取部门列表         |

#### 系统管理接口请求案例

1. **获取角色列表** (`/api/system/role/list`)

   ```bash
   curl -X GET http://localhost:8081/api/system/role/list \
     -H "Authorization: Bearer <accessToken>"
   ```

2. **获取菜单列表** (`/api/system/menu/list`)

   ```bash
   curl -X GET http://localhost:8081/api/system/menu/list \
     -H "Authorization: Bearer <accessToken>"
   ```

3. **检查菜单名称是否存在** (`/api/system/menu/name-exists`)

   ```bash
   curl -X GET "http://localhost:8081/api/system/menu/name-exists?name=测试菜单" \
     -H "Authorization: Bearer <accessToken>"
   ```

4. **检查菜单路径是否存在** (`/api/system/menu/path-exists`)

   ```bash
   curl -X GET "http://localhost:8081/api/system/menu/path-exists?path=/test" \
     -H "Authorization: Bearer <accessToken>"
   ```

5. **获取部门列表** (`/api/system/dept/list`)

   ```bash
   curl -X GET http://localhost:8081/api/system/dept/list \
     -H "Authorization: Bearer <accessToken>"
   ```



## 脚本说明

逻辑备份与增量导出脚本位于仓库 [`back/scripts/`](back/scripts/)；镜像内复制到 **`/app/backup/scripts`**。更细的目录约定、myloader 参数与旧版布局兼容说明见 [`back/scripts/README.md`](back/scripts/README.md)。

后端默认从环境变量 **`SCRIPT_DIR`** 读取脚本目录，未设置时使用 **`${REPO_DIR}/scripts`**（与镜像布局一致）。备份数据根目录由业务侧传入 **`-b, --backup-dir`**，与持久化目录 **`${BACK_DIR}/data`**（默认如 `/app/backup_data/data`）对齐；**不再使用** 历史上的 `POST /db/backup`、`POST /db/restore` 等路径。即时备份 / 定时任务 / 还原由 **`POST /api/db-instances/<id>/backup`**、**`POST /api/backup-jobs/<id>/execute`**、**`POST /api/db-instances/<id>/restore`** 等接口驱动，具体见 [`back/db_instance_api.py`](back/db_instance_api.py) 文件头注释。

### `mysql-backup-mydumper.sh`（全量备份，mydumper）

- **功能**：使用 **mydumper** 对单库做逻辑备份；默认 **ZSTD** 压缩、按表分文件、结构/数据分离，不再使用旧版 mysqldump 的大表拆分逻辑。
- **输出目录**（未指定 `--session-dir` 时）：`<BACKUP_ROOT>/<数据库名>_YYYYMMDD_HHMMSS/`，典型包含：
  - `data/`：mydumper `--outputdir`，含 `metadata`、`*.sql.zst`、`*-schema.sql.zst` 等；
  - `meta/backup-options.json`：脚本写入的备份选项；
  - `backup.log`：含 mydumper 输出；可选「每表完成一行」进度（见脚本内说明）。
- **调用示例**：`bash /app/backup/scripts/mysql-backup-mydumper.sh [选项]`

#### 主要命令行参数

| 参数名 | 命令行选项 | 脚本默认值 | 说明 |
|--------|------------|------------|------|
| `DB_HOST` | `-H, --host` | `127.0.0.1` | MySQL 主机 |
| `DB_PORT` | `-P, --port` | `3306` | 端口 |
| `DB_USER` | `-u, --user` | `root` | 用户名 |
| `DB_PASS` | `-p, --password` | 空 | 密码 |
| `DB_NAME` | `-d, --database` | `db_name` | 数据库名 |
| `BACKUP_ROOT` | `-b, --backup-dir` | `/app/backup/data` | 备份根目录；实际会话目录为其下带时间戳的子目录，或由 `--session-dir` 指定 |
| 会话目录 | `--session-dir` | 空 | 本次备份会话**绝对路径**，须位于 `-b` 之下；与自动时间戳目录二选一（供服务端预登记） |
| `TABLES_INCLUDE` | `-t, --tables` | 空 | 仅备份指定表，逗号分隔；与 `-i` 同时存在时先取白名单再在结果上排除 |
| `TABLES_EXCLUDE` | `-i, --ignore` | 空 | 不备份的表，逗号分隔 |
| `CLEAN_DAYS` | `-c, --clean` | 空（不清理） | 备份完成后清理 `BACKUP_ROOT` 下该库前缀目录中 **N 天前** 的旧目录 |
| — | `--threads` | `4` | mydumper 线程数 |
| — | `--max-threads-per-table` | `1` | 单表并行上限（建议保持 1，避免部分版本「file already open」） |
| — | `--compress` | `zstd` | 压缩算法；`none` 可关闭（若版本支持） |

环境变量 **`MYDUMPER_BIN`**、以及脚本内 **`LOG_FILE` / `LOG_SIZE_LIMIT_MB`**、表进度监控相关变量见脚本开头配置区。

### `mysql-restore-mydumper.sh`（全量还原，myloader）

- **功能**：使用 **myloader** 将 mydumper 会话目录还原到目标库；支持 **`data/metadata`**（新版，推荐）与 **`metadata` 在会话根**（旧版）两种布局。
- **必填**：`-d, --database`（目标库）、`-s, --source-dir`（备份会话根目录绝对路径）。
- **源库与目标库不同**：须加 **`--source-db`**，与 myloader 的 `--source-db` 一致。
- **日志**：默认追加写入会话目录下的 **`restore.log`**（脚本内 `LOG_FILE` 可改）。
- **调用示例**：`bash /app/backup/scripts/mysql-restore-mydumper.sh [选项]`

#### 主要命令行参数

| 参数名 | 命令行选项 | 脚本默认值 | 说明 |
|--------|------------|------------|------|
| `DB_HOST` | `-H, --host` | `127.0.0.1` | MySQL 主机 |
| `DB_PORT` | `-P, --port` | `3306` | 端口 |
| `DB_USER` | `-u, --user` | `root` | 用户 |
| `DB_PASS` | `-p, --password` | 空 | 密码 |
| `TARGET_DB` | `-d, --database` | 必填 | 还原到的目标库名 |
| `SOURCE_DIR` | `-s, --source-dir` | 必填 | 备份会话根目录 |
| `SOURCE_DB` | `--source-db` | 空 | 备份中的源库名；与 `-d` 不同时必须指定 |
| — | `--threads` | `4` | myloader 线程数 |
| — | `--drop-table` | `DROP` | 删表策略（如 `NONE` 则表已存在会失败） |
| `TABLES_INCLUDE` | `-t, --tables` | 空 | 仅还原指定表，逗号分隔短表名 |
| `TABLES_EXCLUDE` | `-i, --ignore` | 空 | 不还原的表，逗号分隔（`--omit-from-file`） |

环境变量 **`MYLOADER_BIN`**、**`DROP_TABLE_MODE`**、**`THREADS`** 与脚本说明一致。

### `mysql-backup-binlog.sh`（增量导出，mysqlbinlog）

- **功能**：按给定的 **起始/结束 binlog 文件与位点**，通过 **`mysqlbinlog --read-from-remote-server`** 拉取可 **`mysql` 回放** 的事件流（**不能**使用仅含 `###` 注释的可读格式，否则还原无效；应用脚本会检测并报错）。
- **输出目录**：`<BACKUP_ROOT>/<数据库名>_inc_YYYYMMDD_HHMMSS/`（或使用 **`--session-dir`** 指定已有目录），包含：
  - `binlog/<日志名>.sql`：按文件切分的导出；
  - `meta/increment-info.json`：起止位点、`database`、`full_backup_file_id`（可选）等元数据；
  - `backup.log`。
- **必填**：`-d, --database`，以及 **`--start-log-file` / `--start-log-pos` / `--end-log-file` / `--end-log-pos`**。
- **调用示例**：`bash /app/backup/scripts/mysql-backup-binlog.sh [选项]`

环境变量 **`MYSQL_BIN`**、**`MYSQLBINLOG_BIN`** 可覆盖默认可执行文件。

### `mysql-apply-binlog-increment.sh`（增量回放）

- **功能**：对单个**增量会话目录**下的 **`binlog/*.sql`** 按文件名排序，依次 **`mysql` 导入**到目标库。
- **必填**：`-d, --database`（目标库）、`-s, --source-dir`（增量目录）。
- 若无 `binlog` 目录或无 `.sql` 文件，脚本会**跳过**（视为无增量）。
- **依赖**：脚本内使用 **`rg`** 校验导出格式；需与运行环境一致（项目镜像已包含则无需额外安装）。
- **调用示例**：`bash /app/backup/scripts/mysql-apply-binlog-increment.sh [选项]`

### 组合还原（全量 + 增量）

当前实现为：**先** 调用 **`mysql-restore-mydumper.sh`** 完成全量（myloader），**再** 按业务编排对每个增量目录调用 **`mysql-apply-binlog-increment.sh`**；位点选择与增量链路由后端与 UI 约束，不再使用旧版单脚本 `mysql-restore-incremental.sh` 与 `changes.sql` 方案。
