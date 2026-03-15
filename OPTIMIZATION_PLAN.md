## 后续优化计划

### 1. 备份文件启用 gzip 压缩

- **现状**：
  - 备份结果为纯文本 `.sql` 文件（表结构 + 数据，多行 `INSERT`）。
  - 大库或大表时，单次备份体积较大，占用磁盘与传输带宽。
- **优化思路**：
  - 将备份输出改为 `.sql.gz` 压缩格式，示例：
    - 直接管道：`mysqldump ... | gzip > table.sql.gz`
    - 或备份结束后，对生成的 `.sql` 做统一 gzip 压缩。
  - 预计可将备份体积压缩到原来的约 10%–30%，显著降低存储与传输成本。
- **还原方案**：
  - 还原脚本支持同时识别 `.sql` 与 `.sql.gz`：
    - `.sql`：继续使用 `mysql < file.sql`
    - `.sql.gz`：使用 `gzip -cd file.sql.gz | mysql`
  - 按扩展名或命名约定自动选择解压方式，不影响现有使用方式。
- **影响评估**：
  - 备份阶段：CPU 使用略有增加，磁盘写入大幅减少；整体耗时在大多数场景下不变或略有提升。
  - 还原阶段：MySQL 执行 SQL 仍是主要瓶颈，解压开销相对较小；总体耗时预计变化不大。
  - 需要同步更新文档（README）与 Web 界面文案，说明备份文件后缀变化与还原脚本兼容策略。

### 2. 备份自定义函数与存储过程

- **现状**：
  - 当前 `mysqldump` 仅导出表结构、数据和视图，不包含存储过程、函数和事件。
  - 迁移到新环境时，需要额外手工同步 routines。
- **优化思路**：
  - 在备份脚本的 `DUMP_CMD` 中按需增加 `--routines`（以及可选 `--events`）参数：
    - 支持通过开关控制：例如 `INCLUDE_ROUTINES=1` 时才追加 `--routines`。
  - 评估并确保备份账号具备导出 routines 所需权限（如 `SHOW VIEW`、`SELECT`、`CREATE ROUTINE` 等）。
- **还原方案**：
  - 在还原脚本中，确保执行备份文件中的 `CREATE PROCEDURE` / `CREATE FUNCTION` / `CREATE EVENT` 语句：
    - 需要还原账号拥有相应的 `CREATE ROUTINE` / `ALTER ROUTINE` / `EVENT` 权限。
  - 文档中明确说明：在多环境（测试/生产）间迁移时，routine 会随数据结构一并迁移。
  - 可选：提供单独只导出/导入 routines 的模式，方便独立迁移或排错。

### 3. 基于 binlog 的增量备份（按“单表全量快照”记录位点）

- **目标**：
  - 在现有“全量备份（schema/ + data/）”基础上，增加 **真正意义上的数据库增量备份** 能力：
    - 全量备份：作为基线快照；
    - 增量备份：记录“上一次备份后到现在”的所有 DML 变更（INSERT/UPDATE/DELETE），基于 MySQL binlog。

- **前置条件**：
  - MySQL 已开启 binlog，且推荐使用 `ROW` 模式；
  - 备份账号具备访问 binlog 的权限（通常需要 `REPLICATION SLAVE` 或等价权限）；
  - 可以稳定保存足够时间范围内的 binlog（`expire_logs_days` 或 `binlog_expire_logs_seconds` 需与备份策略匹配）。

==> **特别说明：本工具的“全量备份”是“按表全量”，而不是一次性全库快照**。  
即：每个数据表在自己的 `mysqldump --single-transaction` 一致性快照事务里备份完毕，因此 **每张表的快照时间点并不完全相同** ，需要分别记录各自的 binlog 节点。

- **当前实现简要说明**（2026-03）：  
  - 已在 `mysql-backup-schema-data.sh` 中实现单表快照前记录 binlog 位点，并将所有表的快照位点统一写入全量备份目录的 `meta/tables-binlog.json`；  
  - 已实现增量备份脚本 `mysql-backup-incremental.sh`，基于“全量备份 +（可选）最新增量的 `binlog_to`”自动选择起点，输出连续的增量链（全量 → inc1 → inc2 → …）；  
  - 已实现增量还原脚本 `mysql-restore-incremental.sh` 与 HTTP 接口 `/db/backup-incremental`、`/db/incrementals`、`/db/restore`，并在 Web 界面中支持选择全量备份和某个增量节点，完成“全量 + 至该节点的所有增量”的组合还原；  
  - 出于 MySQL binlog 语义限制，当前版本的增量还原仅支持“目标数据库名与备份时数据库名一致”，Web 界面已在库名不一致时自动禁止执行增量还原并给出提示；  
  - 后续优化方向包括：更细粒度的按表增量回放、基于 `mysqlbinlog --rewrite-db` 的跨库安全还原等（如下文设计部分所述）。

- **总体设计**：
  1. **单表全量备份时记录该表的快照位点**  
     - 在备份脚本中（`scripts/mysql-backup-schema-data.sh`），当前每张表是单独调用 `mysqldump --single-transaction` 进行“单表快照”备份；
     - 计划在 **每次对某张表执行 `mysqldump` 之前** ，在同一连接 / 同一事务上下文内先记录一次 binlog 位点，以保证：
       - “该表快照视图”与“记录的 binlog 位点”是同一时间点的视图；
       - 后续增量可以**针对每张表，从各自的快照位点开始补齐变更**。
     - 元数据记录结构示例（按表维度）：
       - 在备份目录下新增 `meta/tables-binlog.json`（或每表一个独立文件），例如：
         ```json
         {
           "user": {
             "binlog_file": "mysql-bin.000123",
             "binlog_pos": 456789,
             "recorded_at": "2026-02-27T14:30:00+08:00"
           },
           "order": {
             "binlog_file": "mysql-bin.000124",
             "binlog_pos": 123456,
             "recorded_at": "2026-02-27T14:31:10+08:00"
           }
         }
         ```
       - 这样可以精确表达“每张表完成全量快照时对应的 binlog 起点”，后续增量备份脚本可**按表**计算需要回放的变更范围。
  2. **增量备份脚本（新增一个脚本）**  
     - 新增 `scripts/mysql-backup-incremental.sh`（仅设计，不立即实现），职责：
       - 读取最近一次“已完成”的全量备份目录或上一次增量的位点元数据；
       - 调用 `mysqlbinlog` 从指定起点位点提取 binlog 变更；
       - 输出增量备份文件（建议放在独立目录），并记录新的位点。
     - 目录结构示例：
       - `incremental/`
         - `<db>_inc_YYYYMMDD_HHMMSS/`
           - `changes.sql`（或按大小/时间切分为多个：`changes_0001.sql` 等）
           - `meta/`
             - `binlog_from.json`（起始位点）
             - `binlog_to.json`（结束位点）
     - `mysqlbinlog` 调用形态示例（单实例）：
       ```bash
       mysqlbinlog \
         --read-from-remote-server \
         --host=DB_HOST --port=DB_PORT --user=DB_USER --password=DB_PASS \
         --raw=false --verbose \
         --start-position=起始Pos \
         --stop-datetime='截止时间（可选）' \
         binlog_file_name > changes.sql
       ```
     - 需要考虑：
       - 如跨多个 binlog 文件，则根据起止 file/position 依次提取；
       - 可按数据库过滤：`--database=${DB_NAME}`，避免导出无关库的变更；
       - 可配置“最大增量时间窗口/文件大小”，超出则切分为多个增量批次。
  3. **增量备份与全量备份的关联**  
     - 在每个增量目录的 `meta/binlog_from.json` 中，记录它依赖的“前置快照”信息：
       ```json
       {
         "from_binlog_file": "mysql-bin.000123",
         "from_binlog_pos": 456789,
         "base_full_backup_dir": "mysql/<db>_20260227_143000",
         "base_type": "full"   // 或 future: differential
       }
       ```
     - 这样在恢复时可以通过：
       - 目标时间点 → 选最近一次早于该时间的全量备份；
       - 再选择从该全量起到目标时间点之间的若干增量目录，按顺序回放。
  - 全量与增量关联关系的详细说明见：`docs/scheduled-incremental-backup-design.md` 第二节「全量备份与增量备份的关联关系」。

- **恢复设计**：
  1. **扩展现有还原脚本或新增增量还原脚本**  
     - 现有 `scripts/mysql-restore-schema-data.sh` 负责**全量恢复**（schema + data）；  
     - 计划新增 `scripts/mysql-restore-incremental.sh` 或在现有脚本中增加“应用增量”的子命令：
       - 输入：
         - 基线全量备份目录；
         - 一个或多个增量目录（按时间顺序）；
         - 可选目标时间（仅回放到某时间点，需要 `--stop-datetime`）。
       - 核心步骤：
         1. 调用现有全量还原脚本，将基线恢复到目标数据库（新库或覆盖库）；
         2. 对每个增量目录，依次执行：
            - `mysql < changes.sql`（或多个切分文件），按顺序回放；
         3. 若需要“恢复到某一时间点”，则在生成增量时就基于 `--stop-datetime` 切割，或在恢复时使用 `mysqlbinlog` 的时间过滤（需更复杂逻辑）。
  2. **注意事项**：
     - 回放顺序必须严格按照时间（或位点）排序，不能乱序；
     - 如果有跨库事务，使用 `--database=${DB_NAME}` 时可能丢失一部分跨库逻辑，需要在文档里说明此限制；
     - 恢复目标库前，需确认与原库的 `server_id`、`GTID`（如开启）等配置不会导致主从复制混淆（建议在“离线恢复库”里执行）。

- **Web / API 层面的预留设计**：
  - 在 HTTP API 中为增量备份预留接口：
    - `POST /db/backup-incremental`：基于某数据库 + 某一次全量备份执行一次 binlog 增量备份（请求体中必须携带“所属全量备份目录/ID”）；
    - `GET /db/incrementals`：基于**指定的全量备份**查询其后续增量备份列表（请求必须包含 fullBackupId/fullBackupDir 条件，只返回该全量备份链路上的增量，避免跨基线混用）；
  - Web 界面：
    - 在“备份列表”中为某条全量备份展示其后续增量链；
    - 在“数据还原”界面增加选项：“恢复到某时间点（使用全量 + 增量）”。

- **风险与限制说明**：
  - 增量备份强依赖 binlog 的完整性与保留时间：
    - 若 binlog 已被清理（过期），早期的增量将无法再生成/验证；
  - 对于高写入压力的实例，`mysqlbinlog` 远程拉取会增加一定流量开销，需要评估带宽；
  - 若未来启用 GTID，则可考虑基于 GTID 集合做更精细的增量/恢复控制（当前设计以 file/position 为主）。
  - **删除行为约束**：删除全量备份前，必须检查是否存在“依赖该全量备份”的增量备份目录：
    - 若存在增量备份，应在 Web / API 层给出明确提示：“删除该全量备份将同时删除关联的所有增量备份”；
    - 删除操作应同时级联清理这些增量目录，避免遗留“失去基线的孤立增量备份”，并在日志中记录具体被删除的全量/增量列表。
  - **还原期间的业务建议**：执行数据还原（尤其是表结构变更和大批量插入）时，为避免与线上业务产生锁竞争与数据冲突，强烈建议：
    - 优先还原到一个新的“目标数据库”（如 `<db>_restore_tmp`），验证无误后再切换业务；
    - 或在对线上数据库直接还原前，**关闭/下线相关业务服务**，禁止其他客户端在还原期间对目标数据库进行读写操作。

---

## 开发计划

### 4. 自动计划备份（基于操作系统 crontab）

> 说明：在真正做定时备份之前，需要先解决“备份配置（数据库地址、账号、密码、端口、备份参数等）的持久化存储与管理问题”。**配置管理的实现优先级高于定时备份本身**。

- **4.1 备份配置的持久化管理（优先级更高）**：
  - **目标**：
    - 能以结构化方式持久化存储多个“备份配置”，每个配置描述一次完整备份任务所需的所有信息；
    - 支持多套环境（例如 mall-生产、mall-测试），每套有自己的数据库连接和备份策略；
    - 为后续系统 crontab 调度提供统一入口（通过“配置 ID / 名称”调用）。
  - **配置内容（示例字段）**：
    - 基础连接：`name`（配置名称）、`host`、`port`、`user`、`password`、`database`；
    - 备份参数：`backup_dir`、`tables_include`、`tables_exclude`、`clean_days`、`enable_gzip`、`backup_type`（full / incremental / full+incremental 策略）；
    - 调度参数（可选）：`cron` 表达式、是否启用、下次执行时间缓存等。
  - **存储形式建议**：
    - 第一阶段采用 **本地 JSON/YAML 文件** 持久化（例如 `/data/backup/mysql/backup-plans.json`），结构清晰、易于备份；
    - 每个配置一个对象，支持增删改查；后续若需要可迁移到数据库表。
  - **配置管理入口**：
    - 提供一个统一的“配置驱动脚本”（例如 `scripts/run-backup-by-config.sh`）：
      - 接收参数 `--config-name <NAME>` 或 `--config-id <ID>`；
      - 从配置文件中读出对应备份配置，组装为对 `mysql-backup-schema-data.sh` 或 `/db/backup` 的调用参数；
      - 支持可选模式：只做全量、只做增量、全量+增量策略（例如先判断今天是否已有全量，没有则做全量，否则做增量）。
    - 后端 API 预留：将来可通过 `/backup-plans` 系列接口管理这些配置，并从 Web 界面编辑。

- **4.2 基于 crontab 的定时执行方案**：
  - **目标**：
    - 利用操作系统自带的 `crontab` 实现“按固定周期自动执行备份任务”，无需在应用内引入额外调度框架；
    - 计划任务只负责按时间调用“配置驱动脚本”，真正的备份逻辑仍复用现有脚本/API。
  - **调度方式**：
    - 在 `scripts/` 目录下提供示例 crontab 片段或安装脚本（如 `install-cron.sh`），内容类似：
      - 每日 02:00 为 mall 生产库执行一次备份：
        - `0 2 * * * /usr/bin/bash /scripts/run-backup-by-config.sh --config-name mall-prod >> /var/log/db-backup-cron.log 2>&1`
      - 每 4 小时为 mall 测试库执行一次增量备份：
        - `0 */4 * * * /usr/bin/bash /scripts/run-backup-by-config.sh --config-name mall-test-incr >> /var/log/db-backup-cron.log 2>&1`
    - 支持用户根据示例自行编辑系统 crontab，或通过简单安装脚本写入预定义条目。
  - **计划配置与策略**：
    - 配置中可定义备份类型策略：
      - `mode = full_only`：始终执行全量；
      - `mode = incremental_only`：仅执行增量（假设已存在基线全量）；
      - `mode = smart_full_and_incremental`：例如每周一全量，其余天增量（由 `run-backup-by-config.sh` 内部判断当前日期和历史全量情况决定是调 `/db/backup` 还是 `/db/backup-incremental`）。
    - 可选增强（后续）：在执行前检查磁盘空间，在执行后计算并记录备份文件体积、执行耗时、成功/失败状态。
  - **与现有能力衔接**：
    - 定时任务只调用“配置驱动脚本”，后者再根据配置选择：
      - 直接调用 shell：`mysql-backup-schema-data.sh` / `mysql-backup-incremental.sh`；
      - 或通过 HTTP 调用当前容器提供的 `/db/backup`、`/db/backup-incremental` 接口（适用于把调度放在外部节点的场景）。
    - 备份结果仍写入当前备份目录结构（`schema/`、`data/`、`meta/`、`backup.log`），便于与 Web 界面的“备份列表”“数据还原”“查看增量链”打通。
  - **交付物**：
    - `backup-plans.json`（命名可调整）及其数据结构定义与示例；
    - `run-backup-by-config.sh`：根据配置名称/ID 执行一次全量或增量备份的集成脚本；
    - 示例 crontab 片段与可选安装脚本 `install-cron.sh`；
    - 错误与执行日志规范（例如单独的 `/var/log/db-backup-cron.log`），便于排查定时任务问题。

### 5. 系统登入验证功能

- **目标**：
  - 对 Web 管理界面与 API 进行访问控制，仅授权用户可执行备份、还原、计划配置等操作，避免未授权访问与误操作。
- **实现思路**：
  - **认证方式**：
    - 首选：基于 Session 的登录（用户名 + 密码），登录成功后服务端创建 Session，Cookie 携带 SessionID；
    - 可选扩展：支持 API Token（如 Bearer Token），便于脚本或 CI 调用 API 时免登录。
  - **用户与密码**：
    - 初期可支持单用户或少量用户，账号密码可配置化（如环境变量、配置文件或独立用户表）；
    - 密码需加密存储（如 bcrypt/argon2），禁止明文保存。
  - **权限与拦截**：
    - 所有需要写操作或敏感读操作的接口（备份、还原、删除备份、计划配置等）必须校验登录状态；
    - 未登录访问时返回 401，并引导至登录页；Web 前端在入口处检查登录状态，未登录则跳转登录页。
  - **会话与安全**：
    - Session 超时时间可配置；支持“记住我”延长有效期（可选）；
    - 关键操作可考虑操作日志（谁在何时执行了备份/还原），便于审计。
- **交付物**：
  - 登录接口（如 `POST /auth/login`）、登出（`POST /auth/logout`）、当前用户状态（如 `GET /auth/me`）；
  - 登录页 UI（用户名、密码、登录按钮）；
  - 拦截中间件：对受保护路由校验 Session/Token，未通过则 401 或重定向；
  - 配置项：用户列表或单用户账号密码来源、Session 超时、是否启用认证（可配置为“开发环境关闭认证”）。


