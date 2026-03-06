"""
数据库备份管理 API
- POST /db/backup             执行全量备份
- POST /db/restore            执行全量还原
- POST /db/backup-incremental 基于全量备份执行 binlog 增量备份
- GET  /db/backups            查询已备份文件列表
- GET  /db/incrementals       查询指定全量备份下的增量备份列表
- DELETE /db/backups/<dir>    删除指定备份
"""
import os
import re
import json
import shutil
import subprocess
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# 脚本与备份根目录，支持环境变量覆盖（本地运行时可设置为本项目 scripts 与备份目录）
SCRIPT_DIR = os.environ.get("SCRIPT_DIR", "/scripts")
BACKUP_ROOT = os.environ.get("BACKUP_ROOT", "/data/backup/mysql")

# 备份目录命名格式：{数据库名}_YYYYMMDD_HHMMSS
BACKUP_DIR_PATTERN = re.compile(r"^(.+)_(\d{8})_(\d{6})$")


def _run_script(script_name, args_list, timeout=None):
    """执行脚本，返回 (success, stdout, stderr, returncode)"""
    script_path = os.path.join(SCRIPT_DIR, script_name)
    if not os.path.isfile(script_path):
        return False, "", f"脚本不存在: {script_path}", -1
    # 保证子进程有包含 /usr/bin 的 PATH，避免 mysqlbinlog 等系统命令 command not found
    path_env = os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    if "/usr/bin" not in path_env:
        path_env = "/usr/bin:" + path_env
    try:
        result = subprocess.run(
            [script_path] + args_list,
            capture_output=True,
            text=True,
            timeout=timeout or 3600,
            env={**os.environ, "PATH": path_env},
        )
        return (
            result.returncode == 0,
            result.stdout or "",
            result.stderr or "",
            result.returncode,
        )
    except subprocess.TimeoutExpired:
        return False, "", "执行超时", -1
    except Exception as e:
        return False, "", str(e), -1


@app.route("/db/test-connection", methods=["POST"])
def test_connection():
    """
    测试数据库连接是否可用
    请求体 JSON：host, port, user, password, database（可选，有则验证数据库存在）
    """
    try:
        data = request.get_json() or {}
        host = data.get("host", "").strip()
        user = data.get("user", "").strip()
        password = data.get("password", "")
        port = str(data.get("port", "3306"))
        database = data.get("database", "").strip()

        if not all([host, user]):
            return jsonify({"code": 400, "msg": "缺少 host 或 user", "data": None}), 400

        db_safe = database.replace("`", "``") if database else ""
        # 使用镜像内提供的 MySQL 官方客户端（已在 PATH 中）
        mysql_bin = "mysql"
        cmd = [
            mysql_bin,
            "-h", host,
            "-P", port,
            "-u", user,
            "-e", "SELECT 1" if not db_safe else f"USE `{db_safe}`; SELECT 1",
        ]
        env = {**os.environ, "MYSQL_PWD": password}
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            if result.returncode == 0:
                return jsonify({
                    "code": 200,
                    "msg": "连接成功" + (f"，数据库 {database} 可访问" if database else ""),
                    "data": None,
                })
            err = (result.stderr or result.stdout or "").strip()
            return jsonify({
                "code": 500,
                "msg": "连接失败: " + (err[:200] if err else "未知错误"),
                "data": None,
            }), 500
        except subprocess.TimeoutExpired:
            return jsonify({"code": 500, "msg": "连接超时", "data": None}), 500
        except FileNotFoundError:
            return jsonify({"code": 500, "msg": "未找到 mysql 客户端", "data": None}), 500
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


@app.route("/db/backup", methods=["POST"])
def backup():
    """
    执行数据库备份
    请求体 JSON：
    - host: 数据库主机（必填）
    - port: 端口，默认 3306
    - user: 用户名（必填）
    - password: 密码（必填）
    - database: 数据库名（必填）
    - backup_dir: 备份根目录，默认 /data/backup/mysql
    - tables: 白名单表，逗号分隔（可选）
    - ignore_tables: 黑名单表，逗号分隔（可选）
    - clean_days: 清理 N 天前的备份，0 不清理（可选）
    """
    try:
        data = request.get_json() or {}
        host = data.get("host")
        user = data.get("user")
        password = data.get("password")
        database = data.get("database")

        if not all([host, user, password, database]):
            return jsonify({
                "code": 400,
                "msg": "缺少必要参数: host, user, password, database",
                "data": None,
            }), 400

        args = [
            "-H", str(host),
            "-P", str(data.get("port", "3306")),
            "-u", str(user),
            "-p", str(password),
            "-d", str(database),
            "-b", data.get("backup_dir") or BACKUP_ROOT,
        ]

        if data.get("tables"):
            args.extend(["-t", data["tables"]])
        if data.get("ignore_tables"):
            args.extend(["-i", data["ignore_tables"]])
        if data.get("clean_days") is not None and int(data["clean_days"]) > 0:
            args.extend(["-c", str(int(data["clean_days"]))])

        success, stdout, stderr, returncode = _run_script("mysql-backup-schema-data.sh", args)

        if success:
            return jsonify({
                "code": 200,
                "msg": "备份成功",
                "data": {"stdout": stdout, "stderr": stderr},
            })
        return jsonify({
            "code": 500,
            "msg": "备份失败",
            "data": {"stdout": stdout, "stderr": stderr, "returncode": returncode},
        }), 500

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e),
            "data": None,
        }), 500


@app.route("/db/restore", methods=["POST"])
def restore():
    """
    执行数据库还原。
    若传入 incremental_dir：先还原全量，再按顺序应用从第一个到所选（含）的增量。
    请求体 JSON：
    - backup_dir: 备份目录路径（必填）
    - target_db: 目标数据库名（必填）
    - host, port, user, password（必填/可选）
    - incremental_dir: 选中的增量备份目录（可选）；若填则执行全量+到该增量为止的所有增量
    - tables, ignore_tables, overwrite_tables: 仅在全量还原时生效（可选）
    """
    try:
        data = request.get_json() or {}
        backup_dir = data.get("backup_dir")
        target_db = data.get("target_db")
        host = data.get("host")
        user = data.get("user")
        password = data.get("password")
        incremental_dir = (data.get("incremental_dir") or "").strip()

        if not all([backup_dir, target_db, host, user, password]):
            return jsonify({
                "code": 400,
                "msg": "缺少必要参数: backup_dir, target_db, host, user, password",
                "data": None,
            }), 400

        norm_backup = os.path.normpath(backup_dir)
        port = str(data.get("port", "3306"))

        if incremental_dir:
            # 校验所选增量属于该全量备份，并得到「从第一个到所选（含）」的增量列表
            norm_inc = os.path.normpath(incremental_dir)
            incr_root = os.path.join(norm_backup, "incremental")
            # 仅允许使用当前全量备份目录下 incremental 子目录中的增量备份
            incr_root_prefix = incr_root + os.sep
            if not norm_inc.startswith(incr_root_prefix):
                return jsonify({
                    "code": 400,
                    "msg": "所选增量备份目录不属于该全量备份",
                    "data": None,
                }), 400
            all_incrs = _get_incremental_dirs_ordered(backup_dir)
            # 统一用 normpath 比较
            all_incrs_norm = [os.path.normpath(p) for p in all_incrs]
            if norm_inc not in all_incrs_norm:
                return jsonify({
                    "code": 400,
                    "msg": "未找到该增量备份或该增量不属于本全量备份",
                    "data": None,
                }), 400
            idx = all_incrs_norm.index(norm_inc)
            to_restore_incrs = all_incrs[: idx + 1]
            incr_dirs_str = ",".join(to_restore_incrs)
            log_file = os.path.join(backup_dir, "restore.log")
            args = [
                # 由增量还原脚本内部先调用全量还原，再顺序回放增量
                "-b", str(backup_dir),
                "-d", str(target_db),
                "-i", incr_dirs_str,
                "-H", str(host),
                "-P", port,
                "-u", str(user),
                "-p", str(password),
                "-l", log_file,
            ]
            success, stdout, stderr, returncode = _run_script("mysql-restore-incremental.sh", args)
        else:
            args = [
                "-b", str(backup_dir),
                "-d", str(target_db),
                "-H", str(host),
                "-P", port,
                "-u", str(user),
                "-p", str(password),
            ]
            if data.get("tables"):
                args.extend(["-t", data["tables"]])
            if data.get("ignore_tables"):
                args.extend(["-i", data["ignore_tables"]])
            if data.get("overwrite_tables"):
                args.extend(["-o", data["overwrite_tables"]])
            success, stdout, stderr, returncode = _run_script("mysql-restore-schema-data.sh", args)

        if success:
            return jsonify({
                "code": 200,
                "msg": "还原成功",
                "data": {"stdout": stdout, "stderr": stderr},
            })
        return jsonify({
            "code": 500,
            "msg": "还原失败",
            "data": {"stdout": stdout, "stderr": stderr, "returncode": returncode},
        }), 500

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e),
            "data": None,
        }), 500


def _get_incremental_dirs_ordered(full_backup_dir, database=None):
    """
    返回某次全量备份下的增量目录列表，按时间升序（先产生的在前）。
    用于还原时按顺序应用：全量 + 增量1 + 增量2 + ... + 所选增量。
    返回: list[str] 增量目录绝对路径
    """
    incr_root = os.path.join(os.path.normpath(full_backup_dir), "incremental")
    if not os.path.isdir(incr_root):
        return []
    items = []
    for name in os.listdir(incr_root):
        path = os.path.join(incr_root, name)
        if not os.path.isdir(path) or "_inc_" not in name:
            continue
        db_name = name.split("_inc_", 1)[0]
        if database and db_name != database:
            continue
        meta_from_path = os.path.join(path, "meta", "binlog_from.json")
        if not os.path.isfile(meta_from_path):
            continue
        try:
            with open(meta_from_path, "r", encoding="utf-8") as f:
                meta_from = json.load(f)
        except Exception:
            continue
        rec_at = (meta_from or {}).get("recorded_at") or ""
        sort_key = rec_at or name
        items.append((sort_key, path))
    items.sort(key=lambda x: x[0])
    return [p for _, p in items]


def _get_binlog_start_from_full_backup(full_backup_dir):
    """
    从全量备份目录的 meta/tables-binlog.json 中解析出增量起始位点。
    使用 recorded_at 最晚的那条记录作为起点，保证不遗漏全量后的变更。
    返回 (binlog_file, binlog_pos) 或 (None, None)。
    """
    meta_path = os.path.join(full_backup_dir, "meta", "tables-binlog.json")
    if not os.path.isfile(meta_path):
        return None, None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, None
    if not data or not isinstance(data, dict):
        return None, None
    latest_file = None
    latest_pos = None
    latest_ts = None
    for table_name, meta in data.items():
        if not isinstance(meta, dict):
            continue
        f = meta.get("binlog_file")
        p = meta.get("binlog_pos")
        ts = meta.get("recorded_at")
        if f and p is not None and ts:
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
                latest_file = f
                latest_pos = int(p)
    return latest_file, latest_pos


def _get_last_incremental_end_position(full_backup_dir, database=None):
    """
    若该全量备份下已有增量备份，返回「最后一个增量」的结束位点（binlog_to），
    作为下一次增量的起始位点，使增量链连续：全量 → inc1 → inc2 → inc3 → ...
    增量目录位于 full_backup_dir/incremental/ 下。
    返回 (binlog_file, binlog_pos) 或 (None, None)。
    """
    incr_root = os.path.join(os.path.normpath(full_backup_dir), "incremental")
    if not os.path.isdir(incr_root):
        return None, None
    candidates = []
    for name in os.listdir(incr_root):
        path = os.path.join(incr_root, name)
        if not os.path.isdir(path) or "_inc_" not in name:
            continue
        db_name = name.split("_inc_", 1)[0]
        if database and db_name != database:
            continue
        meta_to_path = os.path.join(path, "meta", "binlog_to.json")
        if not os.path.isfile(meta_to_path):
            continue
        try:
            with open(meta_to_path, "r", encoding="utf-8") as f:
                meta_to = json.load(f)
        except Exception:
            continue
        bf = meta_to.get("binlog_file")
        bp = meta_to.get("binlog_pos")
        if bf is None or bp is None:
            continue
        ts = meta_to.get("recorded_at") or ""
        candidates.append((ts or name, bf, int(bp)))
    if not candidates:
        return None, None
    # 按时间升序，取最后一个（链尾）的结束位点
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1], candidates[-1][2]


@app.route("/db/backup-incremental", methods=["POST"])
def backup_incremental():
    """
    基于已有的全量备份目录执行一次 binlog 增量备份。

    请求体 JSON：
    - host, port, user, password, database: 连接与库名（必填）
    - full_backup_dir: 所属全量备份目录的绝对路径（必填）
    - start_file / start_pos: 可选；不传时：若该全量下已有增量则从「最后一个增量」的 meta/binlog_to.json 取结束位点，否则从全量 meta/tables-binlog.json 读取
    - stop_datetime: binlog 截止时间（可选）
    """
    try:
        data = request.get_json() or {}
        host = data.get("host")
        user = data.get("user")
        password = data.get("password")
        database = data.get("database")
        full_backup_dir = data.get("full_backup_dir")
        start_file = data.get("start_file")
        start_pos = data.get("start_pos")

        if not all([host, user, password, database, full_backup_dir]):
            return jsonify({
                "code": 400,
                "msg": "缺少必要参数: host, user, password, database, full_backup_dir",
                "data": None,
            }), 400

        if not start_file or start_pos is None:
            # 若该全量下已有增量，则从「最后一个增量」的结束位点开始；否则从全量 meta 的 tables-binlog 开始
            start_file, start_pos = _get_last_incremental_end_position(
                full_backup_dir, data.get("database")
            )
            if not start_file or start_pos is None:
                start_file, start_pos = _get_binlog_start_from_full_backup(full_backup_dir)
            if not start_file or start_pos is None:
                return jsonify({
                    "code": 400,
                    "msg": "未找到起始位点：请确认全量备份目录下存在 meta/tables-binlog.json 且内容有效，或手动传入 start_file、start_pos",
                    "data": None,
                }), 400

        start_pos_int = int(start_pos)

        args = [
            "-H", str(host),
            "-P", str(data.get("port", "3306")),
            "-u", str(user),
            "-p", str(password),
            "-d", str(database),
            "-b", BACKUP_ROOT,
            "-F", str(full_backup_dir),
            "--start-file", str(start_file),
            "--start-pos", str(start_pos_int),
        ]

        stop_dt = data.get("stop_datetime")
        if stop_dt:
            args.extend(["--stop-datetime", str(stop_dt)])

        success, stdout, stderr, returncode = _run_script("mysql-backup-incremental.sh", args)

        if success:
            return jsonify({
                "code": 200,
                "msg": "增量备份成功",
                "data": {"stdout": stdout, "stderr": stderr},
            })
        return jsonify({
            "code": 500,
            "msg": "增量备份失败",
            "data": {"stdout": stdout, "stderr": stderr, "returncode": returncode},
        }), 500

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e),
            "data": None,
        }), 500


@app.route("/db/backup-options", methods=["GET"])
def get_backup_options():
    """
    获取指定全量备份目录的 meta/backup-options.json（表过滤条件），供增量模式下只读展示。
    查询参数：full_backup_dir 或 dir，为全量备份目录绝对路径（必须在 BACKUP_ROOT 下）。
    """
    try:
        full_backup_dir = (
            request.args.get("full_backup_dir") or request.args.get("dir") or ""
        ).strip()
        if not full_backup_dir:
            return jsonify({
                "code": 400,
                "msg": "缺少参数: full_backup_dir 或 dir",
                "data": None,
            }), 400
        full_backup_dir = os.path.normpath(full_backup_dir)
        if not full_backup_dir.startswith(os.path.normpath(BACKUP_ROOT) + os.sep) and full_backup_dir != os.path.normpath(BACKUP_ROOT):
            return jsonify({
                "code": 400,
                "msg": "目录必须在备份根目录下",
                "data": None,
            }), 400
        meta_path = os.path.join(full_backup_dir, "meta", "backup-options.json")
        if not os.path.isfile(meta_path):
            return jsonify({
                "code": 200,
                "msg": "ok",
                "data": {"tables_include": "", "tables_exclude": ""},
            })
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({
            "code": 200,
            "msg": "ok",
            "data": {
                "tables_include": data.get("tables_include", "") or "",
                "tables_exclude": data.get("tables_exclude", "") or "",
            },
        })
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e),
            "data": None,
        }), 500


@app.route("/db/backups", methods=["GET"])
def list_backups():
    """
    查询已备份文件列表
    返回：备份时间、数据库名、备份目录路径
    可选查询参数：database（按数据库名筛选）
    """
    try:
        database_filter = request.args.get("database", "").strip()

        if not os.path.isdir(BACKUP_ROOT):
            return jsonify({
                "code": 200,
                "msg": "ok",
                "data": {"items": [], "total": 0},
            })

        items = []
        for name in os.listdir(BACKUP_ROOT):
            path = os.path.join(BACKUP_ROOT, name)
            if not os.path.isdir(path):
                continue
            m = BACKUP_DIR_PATTERN.match(name)
            if not m:
                continue
            db_name, date_str, time_str = m.group(1), m.group(2), m.group(3)
            if database_filter and db_name != database_filter:
                continue
            backup_time = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
            try:
                size = sum(
                    os.path.getsize(os.path.join(r, f))
                    for r, _, files in os.walk(path)
                    for f in files
                )
            except OSError:
                size = 0

            items.append({
                "database": db_name,
                "backupTime": backup_time,
                "backupDir": path,
                "dirName": name,
                "size": size,
            })

        # 按备份时间倒序（新的在前）
        items.sort(key=lambda x: x["backupTime"], reverse=True)

        return jsonify({
            "code": 200,
            "msg": "ok",
            "data": {"items": items, "total": len(items)},
        })

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e),
            "data": None,
        }), 500


@app.route("/db/incrementals", methods=["GET"])
def list_incrementals():
    """
    查询某次全量备份下的增量备份列表。

    查询参数：
    - full_backup_dir: 全量备份目录绝对路径（必填）
    - database: 数据库名（可选，用于按库过滤）
    """
    try:
        full_backup_dir = request.args.get("full_backup_dir", "").strip()
        db_filter = request.args.get("database", "").strip()

        if not full_backup_dir:
            return jsonify({
                "code": 400,
                "msg": "缺少参数: full_backup_dir",
                "data": None,
            }), 400

        # 增量目录改为放在对应全量备份目录下的 incremental 子目录中
        incr_root = os.path.join(os.path.normpath(full_backup_dir), "incremental")
        if not os.path.isdir(incr_root):
            return jsonify({
                "code": 200,
                "msg": "ok",
                "data": {"items": [], "total": 0},
            })

        items = []
        for name in os.listdir(incr_root):
            path = os.path.join(incr_root, name)
            if not os.path.isdir(path):
                continue

            # 约定目录名格式: <db>_inc_YYYYMMDD_HHMMSS
            if "_inc_" not in name:
                continue
            db_name = name.split("_inc_", 1)[0]
            if db_filter and db_name != db_filter:
                continue

            meta_from_path = os.path.join(path, "meta", "binlog_from.json")
            if not os.path.isfile(meta_from_path):
                continue
            try:
                with open(meta_from_path, "r", encoding="utf-8") as f:
                    meta_from = json.load(f)
            except Exception:
                continue

            base_full = meta_from.get("base_full_backup_dir") or meta_from.get("full_backup_dir")
            # 目录结构已经限定在 full_backup_dir 下，这里仅作为健壮性校验，不再用于筛选

            meta_to_path = os.path.join(path, "meta", "binlog_to.json")
            meta_to = None
            if os.path.isfile(meta_to_path):
                try:
                    with open(meta_to_path, "r", encoding="utf-8") as f:
                        meta_to = json.load(f)
                except Exception:
                    meta_to = None

            item = {
                "database": db_name,
                "incrementalDir": path,
                "dirName": name,
                "binlogFrom": meta_from,
                "binlogTo": meta_to,
            }
            items.append(item)

        # 按 from.recorded_at 或目录名倒序
        def _sort_key(it):
            rec = (it.get("binlogFrom") or {}).get("recorded_at") or ""
            return rec or it.get("dirName", "")

        items.sort(key=_sort_key, reverse=True)

        return jsonify({
            "code": 200,
            "msg": "ok",
            "data": {"items": items, "total": len(items)},
        })

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str(e),
            "data": None,
        }), 500


@app.route("/db/backups/<dir_name>/log", methods=["GET"])
def get_backup_log(dir_name):
    """
    获取备份/还原日志内容（用于实时查看）
    查询参数：type=backup|restore
    """
    log_type = request.args.get("type", "backup")
    if log_type not in ("backup", "restore"):
        return jsonify({"code": 400, "msg": "type 只能是 backup 或 restore", "data": None}), 400
    if "/" in dir_name or ".." in dir_name or not BACKUP_DIR_PATTERN.match(dir_name):
        return jsonify({"code": 400, "msg": "无效的备份目录名", "data": None}), 400

    log_file = "backup.log" if log_type == "backup" else "restore.log"
    log_path = os.path.join(BACKUP_ROOT, dir_name, log_file)

    if not os.path.isfile(log_path):
        return jsonify({
            "code": 200,
            "msg": "ok",
            "data": {"content": "(日志文件暂未生成)", "exists": False},
        })

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return jsonify({
            "code": 200,
            "msg": "ok",
            "data": {"content": content, "exists": True},
        })
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


@app.route("/db/backups/<dir_name>/tables", methods=["GET"])
def get_backup_tables(dir_name):
    """
    获取备份目录中包含的表/视图列表
    """
    if "/" in dir_name or ".." in dir_name or not BACKUP_DIR_PATTERN.match(dir_name):
        return jsonify({"code": 400, "msg": "无效的备份目录名", "data": None}), 400

    schema_dir = os.path.join(BACKUP_ROOT, dir_name, "schema")
    if not os.path.isdir(schema_dir):
        return jsonify({
            "code": 200,
            "msg": "ok",
            "data": {"tables": [], "views": []},
        })

    views_set = set()
    views_file = os.path.join(schema_dir, ".views")
    if os.path.isfile(views_file):
        try:
            with open(views_file, "r", encoding="utf-8") as f:
                for line in f:
                    name = line.strip()
                    if name:
                        views_set.add(name)
        except Exception:
            pass

    tables = []
    views = []
    for fn in sorted(os.listdir(schema_dir)):
        if fn.startswith(".") or not fn.endswith(".sql"):
            continue
        name = fn[:-4]
        if name in views_set:
            views.append(name)
        else:
            tables.append(name)

    return jsonify({
        "code": 200,
        "msg": "ok",
        "data": {"tables": tables, "views": views},
    })


@app.route("/db/backups/<dir_name>", methods=["DELETE"])
def delete_backup(dir_name):
    """
    删除指定备份目录
    """
    if "/" in dir_name or ".." in dir_name or not BACKUP_DIR_PATTERN.match(dir_name):
        return jsonify({"code": 400, "msg": "无效的备份目录名", "data": None}), 400

    path = os.path.join(BACKUP_ROOT, dir_name)
    if not os.path.isdir(path):
        return jsonify({"code": 404, "msg": "备份目录不存在", "data": None}), 404

    try:
        shutil.rmtree(path)
        return jsonify({"code": 200, "msg": "删除成功", "data": None})
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


@app.route("/")
def index():
    """Web 管理界面"""
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    """健康检查"""
    return jsonify({"code": 200, "msg": "ok", "data": None})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081, debug=False)
