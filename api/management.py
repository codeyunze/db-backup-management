"""
数据库备份管理 API
- POST /db/backup   执行备份
- POST /db/restore 执行还原
- GET  /db/backups  查询已备份文件列表
- DELETE /db/backups/<dir_name> 删除指定备份
"""
import os
import re
import shutil
import subprocess
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

SCRIPT_DIR = "/scripts"
BACKUP_ROOT = "/data/backup/mysql"

# 备份目录命名格式：{数据库名}_YYYYMMDD_HHMMSS
BACKUP_DIR_PATTERN = re.compile(r"^(.+)_(\d{8})_(\d{6})$")


def _run_script(script_name, args_list, timeout=None):
    """执行脚本，返回 (success, stdout, stderr, returncode)"""
    script_path = os.path.join(SCRIPT_DIR, script_name)
    if not os.path.isfile(script_path):
        return False, "", f"脚本不存在: {script_path}", -1
    try:
        result = subprocess.run(
            [script_path] + args_list,
            capture_output=True,
            text=True,
            timeout=timeout or 3600,
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
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
        cmd = [
            "mysql",
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
    执行数据库还原
    请求体 JSON：
    - backup_dir: 备份目录路径，如 /data/backup/mysql/mall_20250209_020000（必填）
    - target_db: 目标数据库名（必填）
    - host: 数据库主机（必填）
    - port: 端口，默认 3306
    - user: 用户名（必填）
    - password: 密码（必填）
    - tables: 仅恢复指定表，逗号分隔（可选）
    - ignore_tables: 不恢复的表，逗号分隔（可选）
    - overwrite_tables: 覆盖的表，逗号分隔（可选）
    """
    try:
        data = request.get_json() or {}
        backup_dir = data.get("backup_dir")
        target_db = data.get("target_db")
        host = data.get("host")
        user = data.get("user")
        password = data.get("password")

        if not all([backup_dir, target_db, host, user, password]):
            return jsonify({
                "code": 400,
                "msg": "缺少必要参数: backup_dir, target_db, host, user, password",
                "data": None,
            }), 400

        args = [
            "-b", str(backup_dir),
            "-d", str(target_db),
            "-H", str(host),
            "-P", str(data.get("port", "3306")),
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
