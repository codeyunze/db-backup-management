# 数据库备份管理后端

## 项目概述

本项目是一个基于 Python Flask 实现的数据库备份管理后端，主要功能包括：

- 数据库实例管理
- 备份任务调度（支持定时备份）
- 备份文件管理（查看、下载、删除）
- 用户认证与授权
- 支持 MySQL 数据库的备份与还原（使用 mydumper/myloader 工具）

## 目录结构

```
back/
├── __pycache__/          # Python 编译缓存
├── scripts/              # 备份和还原脚本
│   ├── README.md         # 脚本说明
│   ├── mysql-backup-mydumper.sh    # 备份脚本
│   └── mysql-restore-mydumper.sh   # 还原脚本
├── Dockerfile            # Docker 构建文件
├── README.md             # 项目说明文档
└── db_instance_api.py    # 主 API 实现
```

## 环境变量配置

| 环境变量 | 说明 | 默认值 |
| --- | --- | --- |
| BACK_DIR | 后端持久化基准目录 | /app/backup_data |
| BACKUP_CRON_SECRET | 用于放行定时任务调用 `POST /api/backup-jobs/<id>/execute` 的密钥；定时脚本会在请求头携带 `X-Backup-Cron-Secret` | 空（未设置不会携带请求头） |
| BACKUP_ALLOW_LOCAL_EXECUTE | 当且仅当请求来自本机（`127.0.0.1` / `::1`）时，允许无 token 放行 `POST /api/backup-jobs/<id>/execute` | 1（允许） |
| BACKUP_DOWNLOAD_ROOTS | 允许下载的备份根路径 | 空 |
| AUTH_RSA_TTL_SECONDS | RSA 登录密钥有效期 | 300 |
| AUTO_SYNC_CRONTAB | 启动时自动同步 crontab | 1 |

## 认证方式

- 登录接口：`/api/auth/login`，返回 accessToken
- 其他受保护接口：需要在请求头中添加 `Authorization: Bearer <accessToken>`
- 定时任务执行（`POST /api/backup-jobs/<id>/execute`）：该接口不要求用户 token 放行。优先设置 `BACKUP_CRON_SECRET` 并让请求携带 `X-Backup-Cron-Secret`；若未设置密钥，则当请求来源为本机（`127.0.0.1` / `::1`）时，可通过 `BACKUP_ALLOW_LOCAL_EXECUTE=1` 放行
- **登录态与 RSA 临时密钥（多进程）**：`accessToken` / `refreshToken` 映射写入 `${BACK_DIR}/json/auth-tokens.json`；RSA 登录用的临时私钥写入 `rsa-login-sessions.json`。这样多个 Gunicorn worker 或前后请求落到不同进程时，`GET /api/auth/rsa` 与 `POST /api/auth/login` 仍能对应，且登录后立即请求 `/api/user/info` 能校验到 token（若仅存在进程内存，会出现「RSA 密钥无效」或登录后一直超时）。
- **Apifox / curl 调登录**：请求体使用 **`username` + `password`** 即可（见下方示例）。不要粘贴浏览器里带 **`encryptedPassword` + `keyId`** 的旧数据，否则会报「RSA 密钥无效或已过期」。

## API 接口文档

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

| 接口                            | 方法   | 描述                           |
| ------------------------------- | ------ | ------------------------------ |
| `/api/backup-jobs`              | GET    | 获取备份任务列表               |
| `/api/backup-jobs`              | POST   | 新增备份任务                   |
| `/api/backup-jobs/<id>`         | PUT    | 编辑备份任务                   |
| `/api/backup-jobs/delete/<id>`  | POST（推荐） | 删除备份任务；勿在浏览器地址栏 GET 访问 |
| `/api/backup-jobs/<id>`         | DELETE | 删除备份任务（兼容旧版客户端） |
| `/api/backup-jobs/<id>/run`     | POST   | 运行备份任务                   |
| `/api/backup-jobs/<id>/stop`    | POST   | 停止备份任务                   |
| `/api/backup-jobs/<id>/execute` | POST   | 执行备份任务（供定时任务调用） |
| `/api/backup-jobs/<id>/log`     | GET    | 查看备份任务日志               |

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

## 数据存储结构

### 备份数据目录结构

```
backup_data/
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

## 使用示例

### 构建镜像

```shell
docker build -t back-mydumper:latest .
```

### 运行镜像

```shell
docker run -d -p 8081:8081 --name back-mydumper \
  -v "/宿主机/备份目录/backup_data:/app/backup_data" \
  back-mydumper:latest
```

### 登录系统

默认账号：admin / 123456

### 创建数据库实例

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

### 创建备份任务

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

### 执行立即备份

```bash
curl -X POST http://localhost:8081/api/db-instances/db_1/backup \
  -H "Authorization: Bearer <accessToken>" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## 部署说明

1. 确保 Docker 环境已安装
2. 构建镜像：`docker build -t back-mydumper:latest .`
3. 运行容器，挂载必要的目录
4. 访问 `http://localhost:8081` 进行操作
5. 首次登录使用默认账号：admin / 123456
6. 建议修改默认密码以提高安全性
