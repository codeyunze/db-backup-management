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
# 备份计划配置文件路径（JSON 持久化）
BACKUP_PLANS_FILE = os.environ.get(
    "BACKUP_PLANS_FILE", os.path.join(BACKUP_ROOT, "backup-plans.json")
)
JOB_LOGS_DIR = os.path.join(BACKUP_ROOT, "job-logs")
JOB_SCRIPTS_DIR = os.path.join(BACKUP_ROOT, "jobs")
CRON_MARK_PREFIX = "# db-backup-management job "

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


def _load_backup_plans():
    """
    从 JSON 文件加载备份计划列表。
    返回列表，每个元素为 dict，至少包含 id/name 基本信息。
    """
    path = BACKUP_PLANS_FILE
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        # 过滤掉明显不合法的项
        cleaned = []
        for item in data:
            if isinstance(item, dict) and item.get("id") and item.get("name"):
                cleaned.append(item)
        return cleaned
    except Exception:
        return []


def _save_backup_plans(plans):
    """
    将备份计划列表写回 JSON 文件。
    """
    path = BACKUP_PLANS_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(plans, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _append_full_backup_file_record(plans, plan_id: str, job_id: str, backup_name: str, backup_dir: str, backup_time: str) -> None:
    """
    在内存中的 plans 结构上，为指定的全量 job 追加一条 backup_files 记录（最多保留 20 条）。
    调用方负责在修改后调用 _save_backup_plans。
    """
    try:
        for p in plans:
            if p.get("id") != plan_id:
                continue
            jobs = p.get("jobs") or []
            if not isinstance(jobs, list):
                jobs = []
                p["jobs"] = jobs
            for j in jobs:
                if j.get("id") != job_id:
                    continue
                if (j.get("backup_type") or "full") != "full":
                    return
                entry = {
                    "backup_name": backup_name,
                    "backup_dir": backup_dir,
                    "backup_time": backup_time,
                }
                lst = j.get("backup_files") or []
                if not isinstance(lst, list):
                    lst = []
                lst.insert(0, entry)
                if len(lst) > 20:
                    lst = lst[:20]
                j["backup_files"] = lst
                return
    except Exception:
        return


def _resolve_full_backup_dir_for_incremental(plans, linked_full_backup_job_id: str):
    """
    根据增量任务记录的 linked_full_backup_job_id，在内存中的 plans 结构中解析本次应使用的 full_backup_dir。

    规则：
    - 在所有 plan 的 jobs 中查找 id == linked_full_backup_job_id 的 job；
    - 仅当该 job 的 backup_type == "full" 时才有效；
    - 从该 job 的 backup_files（若存在）中，按 backup_time 倒序取第一条的 backup_dir 作为 full_backup_dir；
    - 若不存在或无有效 backup_files，则返回 None。
    """
    if not linked_full_backup_job_id:
        return None
    try:
        best_dir = None
        best_time = None
        for p in plans:
            jobs = p.get("jobs") or []
            if not isinstance(jobs, list):
                continue
            for j in jobs:
                if j.get("id") != linked_full_backup_job_id:
                    continue
                if (j.get("backup_type") or "full") != "full":
                    return None
                bf_list = j.get("backup_files") or []
                if not isinstance(bf_list, list) or not bf_list:
                    return None
                for entry in bf_list:
                    if not isinstance(entry, dict):
                        continue
                    bdir = (entry.get("backup_dir") or "").strip()
                    btime = (entry.get("backup_time") or "").strip()
                    if not bdir:
                        continue
                    # backup_files 由最近到最早维护，这里简单取第一条即可；为健壮性仍比较 backup_time
                    if best_dir is None:
                        best_dir = bdir
                        best_time = btime
                    else:
                        if btime and (not best_time or btime > best_time):
                            best_dir = bdir
                            best_time = btime
                return best_dir
        return best_dir
    except Exception:
        return None


def _notify_full_backup_completed(plan_id: str, job_id: str, backup_name: str, backup_dir: str, backup_time: str) -> None:
    """
    供内部调用：在备份脚本或 API 得知全量备份成功后，更新 backup-plans.json 中对应 job 的 backup_files。
    """
    try:
        plans = _load_backup_plans()
        _append_full_backup_file_record(
            plans,
            plan_id=plan_id,
            job_id=job_id,
            backup_name=backup_name,
            backup_dir=backup_dir,
            backup_time=backup_time,
        )
        _save_backup_plans(plans)
    except Exception:
        return


@app.route("/internal/jobs/<plan_id>/<job_id>/full-backup", methods=["POST"])
def internal_full_backup_callback(plan_id, job_id):
    """
    内部回调接口：由定时全量备份脚本在成功完成备份后调用，用于记录 backup_files。

    请求体 JSON：
    - backup_name: 备份目录名（可选，缺省时从 backup_dir 的 basename 推导）
    - backup_dir: 备份目录绝对路径（必填）
    - backup_time: 备份完成时间字符串（可选，缺省为当前时间）
    """
    try:
        data = request.get_json() or {}
        backup_dir = (data.get("backup_dir") or "").strip()
        if not backup_dir:
            return jsonify({"code": 400, "msg": "缺少 backup_dir", "data": None}), 400
        backup_name = (data.get("backup_name") or "").strip() or os.path.basename(backup_dir.rstrip("/"))
        if not backup_name:
            return jsonify({"code": 400, "msg": "无法确定 backup_name", "data": None}), 400
        backup_time = (data.get("backup_time") or "").strip()
        if not backup_time:
            import time as _time

            backup_time = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime())

        _notify_full_backup_completed(
            plan_id=plan_id,
            job_id=job_id,
            backup_name=backup_name,
            backup_dir=backup_dir,
            backup_time=backup_time,
        )
        return jsonify({"code": 200, "msg": "ok", "data": None}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


def _generate_plan_id():
    """
    简单生成一个字符串 ID（避免额外依赖 uuid 库）。
    """
    import time

    return f"plan_{int(time.time() * 1000)}"


def _generate_job_id():
    """
    简单生成一个定时任务 ID。
    """
    import time

    return f"job_{int(time.time() * 1000)}"


def _append_job_log(job_id: str, message: str) -> None:
    """
    将一条定时任务运行日志追加写入到 job-logs/<job_id>.log 中。
    """
    try:
        os.makedirs(JOB_LOGS_DIR, exist_ok=True)
        log_path = os.path.join(JOB_LOGS_DIR, f"{job_id}.log")
        import time as _time

        ts = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime())
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        # 日志记录失败不影响主流程
        return


def _read_crontab_lines() -> list[str]:
    """
    读取当前用户 crontab（按行返回）。若不存在 crontab，则返回空列表。
    """
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
            env=os.environ,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            # 没有 crontab 的典型输出
            if "no crontab" in stderr.lower() or "no crontab" in stdout.lower():
                return []
            return []
        return stdout.splitlines()
    except subprocess.TimeoutExpired:
        return []
    except Exception:
        return []


def _write_crontab_lines(lines: list[str]) -> None:
    """
    覆盖写入 crontab 内容。
    """
    try:
        text = "\n".join(lines).rstrip() + "\n" if lines else ""
        subprocess.run(
            ["crontab", "-"],
            input=text,
            text=True,
            timeout=10,
            env=os.environ,
        )
    except Exception:
        # 写 crontab 失败不影响主流程，但会导致系统级定时任务不同步
        return


def _sync_job_crontab(plan_id: str, job: dict, remove_only: bool = False) -> None:
    """
    根据 job 的 enabled / schedule，在系统 crontab 中添加或移除对应的条目。
    - 每个 job 使用一行标记注释 + 一行实际 cron 命令；
    - 标记行格式:  "# db-backup-management job <job_id> plan=<plan_id>"
    - 命令行目前仅写一条 echo 到对应 job 的 cron 日志文件，用于验证调度是否生效。
    """
    try:
        job_id = job.get("id")
        if not job_id:
            return
        schedule = (job.get("schedule") or "").strip()
        enabled = bool(job.get("enabled", True))

        lines = _read_crontab_lines()
        new_lines: list[str] = []
        skip_next = False
        marker_sub = f"{CRON_MARK_PREFIX}{job_id}"

        for line in lines:
            if skip_next:
                # 跳过标记后的下一行（实际 cron 命令）
                skip_next = False
                continue
            if line.strip().startswith(CRON_MARK_PREFIX) and job_id in line:
                # 找到该 job 的标记行，跳过它和下一行
                skip_next = True
                continue
            new_lines.append(line)

        # 仅在 enabled 且非 remove_only 时重新添加真实备份命令
        if not remove_only and enabled and schedule:
            # 重新加载 plan 信息以获取连接参数
            plans = _load_backup_plans()
            plan = next((p for p in plans if p.get("id") == plan_id), None)
            if not plan:
                return
            host = (plan.get("host") or "").strip()
            user = (plan.get("user") or "").strip()
            password = plan.get("password") or ""
            port = int(plan.get("port") or 3306)
            database = (plan.get("database") or "").strip()
            if not (host and user and database):
                return

            os.makedirs(JOB_LOGS_DIR, exist_ok=True)
            os.makedirs(JOB_SCRIPTS_DIR, exist_ok=True)
            cron_log_path = os.path.join(JOB_LOGS_DIR, f"{job_id}.run.log")
            meta_log_path = os.path.join(JOB_LOGS_DIR, f"{job_id}.log")
            job_script_path = os.path.join(JOB_SCRIPTS_DIR, f"{job_id}.sh")

            # tables/ignore_tables/clean_days/enable_gzip 以 job 为准，缺省回退到 plan
            tables = (job.get("tables") or plan.get("tables") or "").strip()
            ignore_tables = (job.get("ignore_tables") or plan.get("ignore_tables") or "").strip()
            clean_days = int(job.get("clean_days") if job.get("clean_days") is not None else plan.get("clean_days") or 0)
            enable_gzip = bool(
                job.get("enable_gzip")
                if job.get("enable_gzip") is not None
                else plan.get("enable_gzip") or False
            )

            # 生成单独的 job 执行脚本 jobs/job_<id>.sh
            # 先保持逻辑简单可靠：仅负责真实执行全量备份，后续再在更安全的路径下接入 backup_files 记录。
            script_lines = [
                "#!/bin/bash",
                f'echo "$(date +\'%Y-%m-%d %H:%M:%S\') 调度触发备份 job={job_id} plan={plan_id}" >> "{meta_log_path}"',
                'PATH="/usr/local/bin:/usr/bin:/bin:$PATH"',
                "/scripts/mysql-backup-schema-data.sh "
                + f"-H {host} -P {port} -u {user} -p \"{password}\" -d {database} "
                + (f"--tables '{tables}' " if tables else "")
                + (f"--ignore-tables '{ignore_tables}' " if ignore_tables else "")
                + (f"-c {clean_days} " if clean_days else "")
                + ("--gzip " if enable_gzip else "")
                + f'>> "{cron_log_path}" 2>&1',
                "",
            ]
            try:
                with open(job_script_path, "w", encoding="utf-8") as sf:
                    sf.write("\n".join(script_lines))
                os.chmod(job_script_path, 0o755)
            except Exception:
                return

            new_lines.append(f"{CRON_MARK_PREFIX}{job_id} plan={plan_id}")
            new_lines.append(f"{schedule} sh \"{job_script_path}\"")

        _write_crontab_lines(new_lines)
    except Exception:
        return


@app.route("/backup-plans", methods=["GET"])
def list_backup_plans():
    """
    列出所有备份计划（数据库实例信息 + 其下的定时任务）。
    """
    plans = _load_backup_plans()
    # 为安全起见，默认不回显密码字段
    safe_plans = []
    for p in plans:
        q = dict(p)
        if "password" in q:
            q["password"] = None
        safe_plans.append(q)
    return jsonify({"code": 200, "msg": "ok", "data": {"items": safe_plans}}), 200


@app.route("/backup-plans", methods=["POST"])
def create_backup_plan():
    """
    创建一个新的备份计划（数据库实例信息，仅记录连接配置）。
    请求体 JSON 建议字段：
    - name: 实例名称（必填，唯一或业务上区分）
    - host, port, user, password, database: 数据库连接信息（必填）
    - backup_dir: 备份根目录（可选，默认 BACKUP_ROOT）
    具体的备份策略参数（tables/clean_days/enable_gzip 等）下沉到 jobs 中记录。
    """
    try:
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        host = (data.get("host") or "").strip()
        user = (data.get("user") or "").strip()
        password = data.get("password") or ""
        database = (data.get("database") or "").strip()
        if not all([name, host, user, database]):
            return (
                jsonify(
                    {
                        "code": 400,
                        "msg": "缺少必要参数: name, host, user, database",
                        "data": None,
                    }
                ),
                400,
            )
        plans = _load_backup_plans()
        # 简单防重复：同名计划不允许重复创建
        for p in plans:
            if p.get("name") == name:
                return (
                    jsonify(
                        {
                            "code": 400,
                            "msg": "已存在同名备份计划",
                            "data": None,
                        }
                    ),
                    400,
                )
        plan_id = _generate_plan_id()
        # 简化后的实例信息仅保留连接相关字段和 jobs 列表
        plan = {
            "id": plan_id,
            "name": name,
            "host": host,
            "port": int(data.get("port") or 3306),
            "user": user,
            "password": password,
            "database": database,
            "backup_dir": data.get("backup_dir") or BACKUP_ROOT,
            # 针对该连接配置下的定时任务列表
            "jobs": [],
        }
        plans.append(plan)
        _save_backup_plans(plans)
        # 返回时隐藏密码
        safe_plan = dict(plan)
        safe_plan["password"] = None
        return jsonify({"code": 200, "msg": "创建成功", "data": safe_plan}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


@app.route("/backup-plans/<plan_id>", methods=["GET"])
def get_backup_plan(plan_id):
    """
    获取单个数据库实例信息（包含密码，用于前端在“数据备份”中自动填充）。
    """
    try:
        plans = _load_backup_plans()
        for p in plans:
            if p.get("id") == plan_id:
                return jsonify({"code": 200, "msg": "ok", "data": p}), 200
        return (
            jsonify({"code": 404, "msg": "未找到指定备份计划", "data": None}),
            404,
        )
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


@app.route("/backup-plans/<plan_id>", methods=["PUT"])
def update_backup_plan(plan_id):
    """
    更新指定备份计划。
    - 支持部分字段更新；未提供的字段保持不变。
    - 密码字段 password 如需更新，请显式传入新值；否则保持原值。
    """
    try:
        data = request.get_json() or {}
        plans = _load_backup_plans()
        found = False
        for p in plans:
            if p.get("id") == plan_id:
                found = True
                # 可更新字段（仅连接/实例信息）
                for key in [
                    "name",
                    "host",
                    "port",
                    "user",
                    "database",
                    "backup_dir",
                ]:
                    if key in data and data[key] is not None:
                        if key in ("port", "clean_days"):
                            try:
                                p[key] = int(data[key])
                            except Exception:
                                continue
                        else:
                            p[key] = data[key]
                # 密码单独处理：允许显式更新或保持原值
                if "password" in data and data["password"] is not None:
                    p["password"] = data["password"]
                break
        if not found:
            return (
                jsonify({"code": 404, "msg": "未找到指定备份计划", "data": None}),
                404,
            )
        _save_backup_plans(plans)
        safe_plan = None
        for p in plans:
            if p.get("id") == plan_id:
                safe_plan = dict(p)
                if "password" in safe_plan:
                    safe_plan["password"] = None
                break
        return jsonify({"code": 200, "msg": "更新成功", "data": safe_plan}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


@app.route("/backup-plans/<plan_id>", methods=["DELETE"])
def delete_backup_plan(plan_id):
    """
    删除指定备份计划。
    """
    try:
        plans = _load_backup_plans()
        new_plans = []
        found = False
        has_jobs = False
        for p in plans:
            if p.get("id") == plan_id:
                found = True
                jobs = p.get("jobs") or []
                if isinstance(jobs, list) and jobs:
                    has_jobs = True
                continue
            new_plans.append(p)
        if not found:
            return (
                jsonify({"code": 404, "msg": "未找到指定备份计划", "data": None}),
                404,
            )
        if has_jobs:
            return (
                jsonify(
                    {
                        "code": 400,
                        "msg": "该实例下仍存在定时任务，请先在“定时任务列表”中清理完定时任务后再删除实例信息。",
                        "data": None,
                    }
                ),
                400,
            )
        _save_backup_plans(new_plans)
        return jsonify({"code": 200, "msg": "删除成功", "data": None}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


@app.route("/backup-plans/<plan_id>/jobs", methods=["POST"])
def create_backup_job(plan_id):
    """
    在指定备份实例（plan）下新增一个定时任务记录。

    请求体示例：
    {
        "schedule": "0 0 12 * *",
        "backup_type": "full",        # full / incremental
        "tables": "user,order",
        "ignore_tables": "order_log",
        "clean_days": 7,
        "enable_gzip": true
    }

    这里只负责把“执行计划”及当时的备份参数持久化到 backup-plans.json，不负责真正写入 crontab。
    """
    try:
        data = request.get_json() or {}
        schedule = (data.get("schedule") or "").strip()
        if not schedule:
            return (
                jsonify({"code": 400, "msg": "缺少调度策略 schedule", "data": None}),
                400,
            )
        backup_type = (data.get("backup_type") or "full").strip() or "full"
        plans = _load_backup_plans()
        found = False
        for p in plans:
            if p.get("id") == plan_id:
                found = True
                jobs = p.get("jobs") or []
                if not isinstance(jobs, list):
                    jobs = []
                job_id = _generate_job_id()
                import time

                job = {
                    "id": job_id,
                    "name": (data.get("name") or "").strip()
                    if isinstance(data.get("name"), str)
                    else "",
                    "schedule": schedule,
                    "backup_type": backup_type,
                    "tables": data.get("tables") or p.get("tables") or "",
                    "ignore_tables": data.get("ignore_tables")
                    or p.get("ignore_tables")
                    or "",
                    "clean_days": int(data.get("clean_days") or p.get("clean_days") or 0),
                    "enable_gzip": bool(
                        data.get("enable_gzip")
                        if data.get("enable_gzip") is not None
                        else p.get("enable_gzip") or False
                    ),
                    # 初始默认为运行状态，可在前端切换运行/停止
                    # 新建任务默认处于“停止”状态，由用户在前端点击“运行”后再写入 crontab
                    "enabled": bool(
                        data.get("enabled")
                        if data.get("enabled") is not None
                        else False
                    ),
                    "created_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime()
                    ),
                }
                if backup_type == "full":
                    job["backup_files"] = []
                elif backup_type == "incremental":
                    linked_full_id = (data.get("linked_full_backup_job_id") or "").strip()
                    if linked_full_id:
                        job["linked_full_backup_job_id"] = linked_full_id
                jobs.append(job)
                p["jobs"] = jobs
                _append_job_log(
                    job_id,
                    f"创建定时任务: plan_id={plan_id}, name={job['name']!r}, schedule={schedule!r}, backup_type={backup_type}",
                )
                break
        if not found:
            return (
                jsonify({"code": 404, "msg": "未找到指定备份计划", "data": None}),
                404,
            )
        _save_backup_plans(plans)
        safe_plan = None
        for p in plans:
            if p.get("id") == plan_id:
                safe_plan = dict(p)
                if "password" in safe_plan:
                    safe_plan["password"] = None
                break
        return (
            jsonify({"code": 200, "msg": "创建定时任务成功", "data": safe_plan}),
            200,
        )
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


def _parse_crontab_l():
    """
    执行 crontab -l 并解析为任务列表。
    返回 list[dict]，每项含: name, database, backup_type, schedule, clean_days, last_run_at, next_run_at, enabled, raw_command。
    """
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
            env=os.environ,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        # 无 crontab 时 exit code 为 1，且常有 "no crontab for ..."
        if result.returncode != 0:
            if "no crontab" in stderr.lower() or "no crontab" in stdout.lower():
                return []
            return []
        lines = stdout.split("\n")
        items = []
        last_comment = ""
        for line in lines:
            raw = line
            line = line.strip()
            if not line:
                last_comment = ""
                continue
            if line.startswith("#"):
                last_comment = line.lstrip("#").strip()
                continue
            # 有效 cron 行：前 5 段为 分 时 日 月 周，其余为命令
            parts = line.split()
            if len(parts) < 6:
                continue
            schedule = " ".join(parts[0:5])
            command = " ".join(parts[5:])
            name = last_comment if last_comment else (command[:80] + ("..." if len(command) > 80 else ""))
            last_comment = ""
            # 根据命令推断备份类型、数据库（本项目的脚本特征）；前端用 incremental/full 区分显示
            backup_type = "incremental" if "incremental" in command or "backup-incremental" in command else "full"
            database = ""
            for opt in ["--database", "-d", "database="]:
                if opt in command:
                    try:
                        if "=" in opt:
                            i = command.find(opt) + len(opt)
                            rest = command[i:]
                            end = rest.find(" ") if " " in rest else len(rest)
                            database = rest[:end].strip("'\"").strip()
                        else:
                            idx = command.find(opt)
                            after = command[idx + len(opt):].strip()
                            if after.startswith("="):
                                after = after[1:].strip()
                            database = (after.split() or [""])[0].strip("'\"")
                        if database:
                            break
                    except Exception:
                        pass
            if not database and ("mysql-backup" in command or "mall_" in command):
                m = re.search(r"mall[_\-]?\w*|([a-zA-Z0-9_]+)_\d{8}_", command)
                if m:
                    database = (m.group(0) or m.group(1) or "").strip("_")
            items.append({
                "id": f"cron_{len(items)}",
                "name": name,
                "database": database or "-",
                "backup_type": backup_type,
                "cron_expr": schedule,
                "schedule": schedule,
                "clean_days": None,
                "last_run_at": "-",
                "next_run_at": "-",
                "enabled": True,
                "raw_command": command,
            })
        return items
    except subprocess.TimeoutExpired:
        return []
    except Exception:
        return []


@app.route("/scheduled-tasks", methods=["GET"])
def list_scheduled_tasks():
    """
    定时任务列表。

    当前版本：优先展示 backup-plans.json 中各实例下配置的 jobs，
    每个 job 表示一条“备份调度记录”，不直接从 crontab 解析。
    """
    try:
        plans = _load_backup_plans()
        items = []
        for p in plans:
            plan_id = p.get("id")
            plan_name = p.get("name") or ""
            database = p.get("database") or ""
            jobs = p.get("jobs") or []
            if not isinstance(jobs, list):
                continue
            for job in jobs:
                j = dict(job or {})
                j_id = j.get("id") or ""
                schedule = j.get("schedule") or ""
                backup_type = j.get("backup_type") or "full"
                job_name = j.get("name") or ""
                clean_days = j.get("clean_days")
                enable_gzip = j.get("enable_gzip")
                created_at = j.get("created_at") or "-"
                enabled = j.get("enabled")
                if enabled is None:
                    enabled = True
                item = {
                    "id": j_id,
                    "name": job_name or plan_name,
                    "plan_id": plan_id,
                    "plan_name": plan_name,
                    "database": database,
                    "backup_type": backup_type,
                    "cron_expr": schedule,
                    "schedule": schedule,
                    "clean_days": clean_days,
                    "enable_gzip": enable_gzip,
                    "created_at": created_at,
                    "last_run_at": "-",
                    "next_run_at": "-",
                    "enabled": enabled,
                }
                if backup_type == "incremental":
                    lf = j.get("linked_full_backup_job_id")
                    if lf:
                        item["linked_full_backup_job_id"] = lf
                elif backup_type == "full":
                    bf_list = j.get("backup_files") or []
                    item["backup_files"] = bf_list
                items.append(item)
        return jsonify({"code": 200, "msg": "ok", "data": {"items": items}}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {"items": []}}), 500


@app.route("/backup-plans/<plan_id>/jobs/<job_id>", methods=["PUT"])
def update_backup_job(plan_id, job_id):
    """
    更新指定备份计划下的一条定时任务。
    可更新字段：name, schedule, backup_type, tables, ignore_tables, clean_days, enable_gzip, enabled, linked_full_backup_job_id。
    """
    try:
        data = request.get_json() or {}
        plans = _load_backup_plans()
        found = False
        for p in plans:
            if p.get("id") != plan_id:
                continue
            jobs = p.get("jobs") or []
            if not isinstance(jobs, list):
                jobs = []
            for j in jobs:
                if j.get("id") != job_id:
                    continue
                found = True
                old_enabled = bool(j.get("enabled", True))
                old_schedule = j.get("schedule") or ""
                old_name = j.get("name") or ""
                for key in [
                    "name",
                    "schedule",
                    "backup_type",
                    "tables",
                    "ignore_tables",
                    "clean_days",
                    "enable_gzip",
                    "enabled",
                    "linked_full_backup_job_id",
                ]:
                    if key in data and data[key] is not None:
                        if key == "clean_days":
                            try:
                                j[key] = int(data[key])
                            except Exception:
                                continue
                        elif key in ("enable_gzip", "enabled"):
                            j[key] = bool(data[key])
                        else:
                            j[key] = data[key]
                new_enabled = bool(j.get("enabled", True))
                new_schedule = j.get("schedule") or ""
                new_name = j.get("name") or ""
                # 记录状态/计划变更日志
                if new_enabled != old_enabled:
                    _append_job_log(
                        job_id,
                        f"更新定时任务状态: plan_id={plan_id}, name={new_name!r}, enabled={new_enabled}",
                    )
                if new_schedule != old_schedule:
                    _append_job_log(
                        job_id,
                        f"更新定时任务调度: plan_id={plan_id}, name={new_name!r}, schedule={new_schedule!r}",
                    )
                # 同步系统 crontab
                _sync_job_crontab(plan_id, j)
                break
            p["jobs"] = jobs
            if found:
                break
        if not found:
            return (
                jsonify({"code": 404, "msg": "未找到指定定时任务", "data": None}),
                404,
            )
        _save_backup_plans(plans)
        return jsonify({"code": 200, "msg": "更新定时任务成功", "data": None}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


@app.route("/backup-plans/<plan_id>/jobs/<job_id>", methods=["DELETE"])
def delete_backup_job(plan_id, job_id):
    """
    删除指定备份计划下的一条定时任务。
    若任务处于运行状态（enabled=True），不允许删除。
    """
    try:
        plans = _load_backup_plans()
        found = False
        enabled = False
        for p in plans:
            if p.get("id") != plan_id:
                continue
            jobs = p.get("jobs") or []
            if not isinstance(jobs, list):
                jobs = []
            new_jobs = []
            for j in jobs:
                if j.get("id") == job_id:
                    found = True
                    enabled = bool(j.get("enabled", True))
                    # 删除前，确保从 crontab 中移除对应条目
                    _sync_job_crontab(plan_id, j, remove_only=True)
                    # 不立即删除，先根据状态判断
                    continue
                new_jobs.append(j)
            if found:
                if enabled:
                    return (
                        jsonify(
                            {
                                "code": 400,
                                "msg": "运行中的定时任务不能删除，请先停止任务。",
                                "data": None,
                            }
                        ),
                        400,
                    )
                p["jobs"] = new_jobs
                _append_job_log(job_id, f"删除定时任务: plan_id={plan_id}")
                break
        if not found:
            return (
                jsonify({"code": 404, "msg": "未找到指定定时任务", "data": None}),
                404,
            )
        _save_backup_plans(plans)
        return jsonify({"code": 200, "msg": "删除定时任务成功", "data": None}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": None}), 500


@app.route("/scheduled-tasks/<job_id>/log", methods=["GET"])
def get_scheduled_task_log(job_id):
    """
    读取指定定时任务的日志：
    - 元日志: job-logs/<job_id>.log（创建/更新/删除/触发记录）
    - 运行日志: job-logs/<job_id>.run.log（备份脚本标准输出/错误）
    """
    try:
        os.makedirs(JOB_LOGS_DIR, exist_ok=True)
        meta_path = os.path.join(JOB_LOGS_DIR, f"{job_id}.log")
        run_path = os.path.join(JOB_LOGS_DIR, f"{job_id}.run.log")
        meta = ""
        run = ""
        if os.path.isfile(meta_path):
            with open(meta_path, "r", encoding="utf-8", errors="ignore") as f:
                meta = f.read()
        if os.path.isfile(run_path):
            with open(run_path, "r", encoding="utf-8", errors="ignore") as f:
                run = f.read()
        if not meta and not run:
            content = "(暂无日志)"
        else:
            parts = []
            if meta:
                parts.append("==== 元日志 (job.log) ====\n" + meta.rstrip())
            if run:
                parts.append("==== 运行日志 (job.run.log) ====\n" + run.rstrip())
            content = "\n\n".join(parts)
        return jsonify({"code": 200, "msg": "ok", "data": {"content": content}}), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e), "data": {"content": str(e)}}), 500


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
    - enable_gzip: 是否启用 gzip 压缩（可选，默认 false）
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
        if data.get("enable_gzip"):
            args.append("--gzip")

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
