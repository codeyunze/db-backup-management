# back/scripts

## mysql-backup-mydumper.sh

使用 [mydumper](https://github.com/mydumper/mydumper)（建议 v0.21.x）对**单个数据库**做逻辑备份；输出为 mydumper 默认的分表文件（常见为 `.sql.zst`），结构与数据分离。

**目录布局**：在备份根目录下生成会话目录 `库名_YYYYMMDD_HHMMSS/`，其内含 `meta/backup-options.json`（脚本写入）、`backup.log`，以及 **`data/`** 子目录（mydumper 的 `--outputdir`，与 `meta/` 同级），其内存放 `metadata`、各 `*.sql.zst` 等。

**依赖**：`mydumper`、`mysql` 客户端（用于从 `information_schema` 拉取表/视图列表）。

**示例**：

```bash
./mysql-backup-mydumper.sh \
  -H 127.0.0.1 -P 3306 -u root -p 'secret' \
  -d mydb -b /app/backup/data \
  -c 7
```

环境变量：`MYDUMPER_BIN` 指定 mydumper 可执行文件路径；`THREADS`、`COMPRESS` 可覆盖默认线程数与压缩算法（`zstd` / `gzip`）。

**`--session-dir`**：指定本次备份会话目录的绝对路径（须位于 `-b` 备份根之下）。服务端在调用 mydumper 前先写入 `backup-files.json`，再传此参数使输出目录与登记一致；一般手工无需使用。

**`file already open: ... .sql`**：多为 mydumper 在 **ZSTD + 多线程** 下对同一输出文件的竞态（[issue #1944](https://github.com/mydumper/mydumper/issues/1944)）。本脚本默认 **`--max-threads-per-table=1`**，并在每次运行前删除 `data/` 下残留的裸 `.sql` 临时文件；仍失败时可尝试降低 `--threads` 或升级 mydumper。

**`backup.log` 每表一行**：mydumper 本身在 `--verbose=2` 下通常不逐表打印完成信息。脚本会在备份进行时**轮询 `data/`**（按库前缀的 `*-schema.sql.zst` 与 `*.NNNN.sql.zst`），当某表相关文件的**大小+mtime 指纹**在约 `TABLE_MONITOR_STABLE_SEC` 秒（默认 2）内不变时，向 `backup.log` 追加一行 `数据表备份完成: 库名.表名`；**仅当 mydumper 退出码为 0** 时，在结束后**补记**尚未触发「稳定」的表（失败则不再补记，避免误报）。多线程下各表文件可能交错写入，日志表示「该表在磁盘上的导出文件已稳定」，与官方「任务完成」语义接近但不保证与 mydumper 内部线程顺序一致。`DISABLE_TABLE_PROGRESS_MONITOR=1` 可关闭；`TABLE_MONITOR_POLL_SEC` 可调轮询间隔（默认 `0.5`）。

详见脚本文件头注释。

## mysql-restore-mydumper.sh

使用 [myloader](https://github.com/mydumper/mydumper)（建议 v0.21.x）将 mydumper 备份目录还原到目标 MySQL 库。

**依赖**：`myloader`；`-s` 传入**会话根目录**（与 `backup-files` 中 `backupDir` 一致）。新版须存在 `data/metadata` 及 `data/*.sql.zst`；旧版兼容会话根下直接放 `metadata` 的布局。

**示例**：

```bash
./mysql-restore-mydumper.sh \
  -H 127.0.0.1 -P 3306 -u root -p 'secret' \
  -d mall_restore \
  -s /app/backup/data/mall_20260322_120000 \
  --source-db mall
```

当目标库名与备份内库名一致时，可省略 `--source-db`（脚本内由调用方传入）。环境变量：`MYLOADER_BIN`、`THREADS`、`DROP_TABLE_MODE`（默认 `DROP`，对应 myloader `--drop-table`）。

还原日志默认追加到备份目录下的 `restore.log`。

**旧备份迁移**：若历史备份的 `metadata` 与 `*.sql.zst` 仍在会话根目录，可保持不动（还原脚本与接口均兼容）；若需与新目录规范一致，可在会话目录内执行 `mkdir -p data && mv metadata *.sql.zst *-schema.sql.zst data/`（按实际文件名调整，勿移动 `meta/`、`backup.log`）。
