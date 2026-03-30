"""
数据库相关管理 API

认证：/api/db-instances、/api/backup-jobs、/api/backup-files 需请求头
Authorization: Bearer <accessToken>（登录接口除外）。
定时任务调 POST /api/backup-jobs/<id>/execute 无用户 token 时：
- 设置 BACKUP_CRON_SECRET，脚本 curl 会带 X-Backup-Cron-Secret；或
- 默认 BACKUP_ALLOW_LOCAL_EXECUTE=1 时仅允许本机 127.0.0.1 / ::1 调用 execute（生产可设为 0 并改用密钥）。

数据库实例信息管理
- GET    /api/db-instances         列表
- POST   /api/db-instances         新增
- PUT    /api/db-instances/<id>    编辑
- DELETE /api/db-instances/<id>    删除
- POST   /api/db-instances/test-connection  校验当前填写的连接信息能否访问 MySQL
- POST   /api/db-instances/<id>/backup   立即执行该实例备份
- POST   /api/db-instances/<id>/restore  myloader 还原备份目录到目标库

任务调度（备份计划/定时任务配置）
- GET    /api/backup-jobs         列表（可选 query keyword）
- POST   /api/backup-jobs         新增
- PUT    /api/backup-jobs/<id>    编辑
- POST   /api/backup-jobs/delete/<id>    删除（推荐）
- DELETE /api/backup-jobs/<id>    删除（兼容旧版）

备份文件
- GET    /api/backup-files                    列表（可选 query keyword）
- DELETE /api/backup-files/<dirName>         删除记录并删除已解析且路径安全的会话备份目录（磁盘上不存在则仅删记录）
- GET    /api/backup-files/<dirName>/download  下载 backupDir 目录打包的 tar.gz
- GET    /api/backup-files/<dirName>/tables   解析 metadata（data/metadata、.partial 及旧版根目录）；目录解析支持 back/backup/<dirName> 回退
- GET    /api/backup-files/<dirName>/logs     读取会话目录下 backup.log、restore.log 文本（有长度上限）
- 执行即时备份：先往 json/backup-files.json 写入基础信息（size=0），接口立即返回；脚本在后台线程执行，结束后更新 size 或移除预登记
- 执行即时还原：接口立即返回，myloader 在后台线程执行（结果写入会话目录 restore.log）

账号（${BACK_DIR}/json/account.json）
- 每条记录字段：account_id、username、password（不再区分 role）
- 注册时自动生成 account_id；旧数据会在加载时自动补齐 account_id 并移除 role
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from typing import Any, Optional, Tuple
from urllib.parse import unquote

from flask import Flask, Response, after_this_request, jsonify, request, send_file
from werkzeug.utils import secure_filename

# --- 登录密码加密（RSA-OAEP-SHA256）---
try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
        load_pem_private_key,
    )
except Exception:  # pragma: no cover
    rsa = None  # type: ignore[assignment]
    padding = None  # type: ignore[assignment]
    hashes = None  # type: ignore[assignment]
    Encoding = None  # type: ignore[assignment]
    PublicFormat = None  # type: ignore[assignment]
    PrivateFormat = None  # type: ignore[assignment]
    NoEncryption = None  # type: ignore[assignment]
    load_pem_private_key = None  # type: ignore[assignment]

app = Flask(__name__)

# 代码运行目录（容器内通常是 /app/backup）
REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# 后端持久化基准目录（json/jobs/data 统一放这里）
BACK_DIR = (
    os.environ.get("BACK_DIR")
    or os.environ.get("APP_BACK_DIR")
    or "/app/backup_data"
)

# 脚本目录默认位于 ${REPO_DIR}/scripts（如 /app/backup/scripts）
SCRIPT_DIR = os.environ.get("SCRIPT_DIR") or os.path.join(REPO_DIR, "scripts")
# 持久化统一放在 ${BACK_DIR}/json 目录
JSON_DIR = os.path.join(BACK_DIR, "json")
DB_INSTANCES_FILE = os.path.join(JSON_DIR, "db-instances.json")
BACKUP_JOBS_FILE = os.path.join(JSON_DIR, "backup-jobs.json")
BACKUP_FILES_FILE = os.path.join(JSON_DIR, "backup-files.json")
ACCOUNT_FILE = os.path.join(JSON_DIR, "account.json")
TIMEZONE_FILE = os.path.join(JSON_DIR, "timezone.json")
AUTH_TOKENS_FILE = os.path.join(JSON_DIR, "auth-tokens.json")
RSA_LOGIN_SESSIONS_FILE = os.path.join(JSON_DIR, "rsa-login-sessions.json")
JOBS_DIR = os.path.join(BACK_DIR, "jobs")
JOB_LOGS_DIR = os.path.join(BACK_DIR, "job-logs")
JOB_SCRIPT_LOGS_DIR = os.path.join(JOBS_DIR, "logs")
CRON_MARK_PREFIX = "# back backup-job "

# 与 mysql-backup-mydumper.sh 默认 -b 一致（未传 backup_dir 时）
_DEFAULT_BACKUP_ROOT = os.path.join(BACK_DIR, "data")

_file_lock = threading.Lock()

_JOB_LOG_MAX_BYTES = 200_000


def _load_auth_tokens_unlocked() -> dict[str, dict[str, str]]:
    if not os.path.isfile(AUTH_TOKENS_FILE):
        return {"access": {}, "refresh": {}}
    try:
        with open(AUTH_TOKENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"access": {}, "refresh": {}}
    return {
        "access": dict((data or {}).get("access") or {}),
        "refresh": dict((data or {}).get("refresh") or {}),
    }


def _save_auth_tokens_unlocked(payload: dict[str, dict[str, str]]) -> None:
    os.makedirs(JSON_DIR, exist_ok=True)
    tmp = f"{AUTH_TOKENS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, AUTH_TOKENS_FILE)


def _auth_tokens_snapshot() -> dict[str, dict[str, str]]:
    with _file_lock:
        return _load_auth_tokens_unlocked()


def _auth_tokens_mutate(mutator) -> None:
    with _file_lock:
        data = _load_auth_tokens_unlocked()
        mutator(data)
        _save_auth_tokens_unlocked(data)


def _make_access_token(username: str) -> str:
    return f"access-{username}-{int(time.time())}-{os.urandom(6).hex()}"


def _make_refresh_token(username: str) -> str:
    return f"refresh-{username}-{int(time.time())}-{os.urandom(8).hex()}"

def _make_account_id() -> str:
    # account_id 使用 UUID：避免基于时间/随机数拼接带来的可预测性
    return str(uuid.uuid4())


def _get_bearer_token() -> str:
    auth = (request.headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return ""
    return auth[7:].strip()


def _get_user_by_access_token() -> Optional[dict]:
    token = _get_bearer_token()
    if not token:
        return None
    snap = _auth_tokens_snapshot()
    username = (snap.get("access") or {}).get(token)
    if not username:
        return None
    with _file_lock:
        accounts = _load_accounts()
    return next((x for x in accounts if (x.get("username") or "").strip() == username), None)


def _get_current_account_id() -> str:
    """
    当前登录用户 account_id。
    对于 cron 放行的内部触发（无 token）场景，返回空字符串。

    注意：内部会获取 _file_lock（读 auth-tokens.json），切勿在已持有 _file_lock 的代码块内调用，否则死锁。
    """
    user = _get_user_by_access_token()
    if not user:
        return ""
    return (user.get("account_id") or "").strip()


def _legacy_default_account_id() -> str:
    """
    兼容历史数据：当 db-instances / backup-jobs / backup-files 记录缺少 account_id 时，
    默认归属到 zhangsan 账号，尽量保证老数据对 zhangsan 可见、其他账号不可见。
    """
    accounts = _load_accounts()
    zhangsan = next(
        (
            x
            for x in accounts
            if (x.get("username") or "").strip().lower() == "zhangsan"
            and (x.get("account_id") or "").strip()
        ),
        None,
    )
    if zhangsan:
        return (zhangsan.get("account_id") or "").strip()
    for x in accounts:
        aid = (x.get("account_id") or "").strip()
        if aid:
            return aid
    return ""


def _get_userinfo_payload_for_account(acc: dict) -> dict:
    """统一为普通用户，并尽量对齐 mock user/info 的字段习惯。"""
    username = (acc.get("username") or "").strip()
    account_id = (acc.get("account_id") or "").strip()
    return {
        "avatar": "",
        "desc": "This is a local account user.",
        "homePath": "/backup/db-instance",
        "id": account_id or username,
        "realName": username,
        "roles": ["user"],
        "token": "",
        "userId": username,
        "username": username,
    }


# 后端权限模式（accessMode=backend）下 /menu/all 使用；结构与 apps/backend-mock 中 MOCK_MENUS 一致
def _backend_menus_for_account(_acc: dict) -> list[dict]:
    return _BACKEND_MENU_TREES["user"]


# 自 mock-data.ts 同步的精简菜单（dashboard + demos/access 下按角色可见页）
_BACKEND_MENU_TREES: dict[str, list[dict]] = {
    "admin": [
        {
            "meta": {"order": -1, "title": "page.dashboard.title"},
            "name": "Dashboard",
            "path": "/dashboard",
            "redirect": "/workspace",
            "children": [
                {
                    "name": "Workspace",
                    "path": "/workspace",
                    "component": "/dashboard/workspace/index",
                    "meta": {"title": "page.dashboard.workspace"},
                },
            ],
        },
    ],
    "user": [
        {
            "meta": {"order": -1, "title": "page.dashboard.title"},
            "name": "Dashboard",
            "path": "/dashboard",
            "redirect": "/workspace",
            "children": [
                {
                    "name": "Workspace",
                    "path": "/workspace",
                    "component": "/dashboard/workspace/index",
                    "meta": {"title": "page.dashboard.workspace"},
                },
            ],
        },
    ],
}


def _success(data: Any = None, message: str = "success"):
    return jsonify({"code": 0, "data": data, "message": message})


def _error(message: str = "error", code: int = 1, http_status: int = 400):
    return jsonify({"code": code, "data": None, "message": message}), http_status


# 需登录才可访问的业务前缀（备份相关）
_BACKUP_PROTECTED_PREFIXES = (
    "/api/db-instances",
    "/api/backup-jobs",
    "/api/backup-files",
)


# --- /api/auth/rsa：临时 RSA 密钥（仅用于登录时前端加密，避免明文密码上传）---
# 会话落盘到 ${JSON_DIR}/rsa-login-sessions.json，便于多 worker / 多进程共享（仅内存会导致 GET /rsa 与 POST /login 落到不同进程时密钥「无效」）。
_RSA_LOGIN_TTL_SECONDS = int(os.environ.get("AUTH_RSA_TTL_SECONDS") or 300)  # 5 min


def _rsa_now() -> float:
    return time.time()


def _load_rsa_sessions_unlocked() -> dict[str, Any]:
    if not os.path.isfile(RSA_LOGIN_SESSIONS_FILE):
        return {}
    try:
        with open(RSA_LOGIN_SESSIONS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_rsa_sessions_unlocked(store: dict[str, Any]) -> None:
    os.makedirs(JSON_DIR, exist_ok=True)
    tmp = f"{RSA_LOGIN_SESSIONS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RSA_LOGIN_SESSIONS_FILE)


def _rsa_gc_expired_store(store: dict[str, Any]) -> None:
    t = _rsa_now()
    for k in list(store.keys()):
        v = store.get(k) or {}
        if float(v.get("expires_at") or 0) <= t:
            store.pop(k, None)


def _rsa_available() -> bool:
    return (
        rsa is not None
        and padding is not None
        and hashes is not None
        and Encoding is not None
        and PublicFormat is not None
        and PrivateFormat is not None
        and NoEncryption is not None
        and load_pem_private_key is not None
    )


def _rsa_issue_login_public_key() -> dict[str, Any]:
    if not _rsa_available():
        raise RuntimeError("cryptography not installed")

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(encoding=Encoding.PEM, format=PublicFormat.SubjectPublicKeyInfo).decode(
        "utf-8", errors="ignore"
    )
    pem_bytes = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    pem_str = pem_bytes.decode("utf-8", errors="ignore")
    key_id = f"k_{int(_rsa_now() * 1000)}_{os.urandom(8).hex()}"
    expires_at = _rsa_now() + max(30, _RSA_LOGIN_TTL_SECONDS)
    with _file_lock:
        store = _load_rsa_sessions_unlocked()
        _rsa_gc_expired_store(store)
        store[key_id] = {"expires_at": expires_at, "private_key_pem": pem_str}
        _save_rsa_sessions_unlocked(store)
    return {
        "algorithm": "RSA-OAEP-SHA256",
        "expiresAt": int(expires_at * 1000),
        "keyId": key_id,
        "publicKey": public_pem,
    }


def _rsa_decrypt_login_password_once(key_id: str, encrypted_b64: str) -> Optional[str]:
    if not _rsa_available():
        return None
    pem_str = ""
    with _file_lock:
        store = _load_rsa_sessions_unlocked()
        _rsa_gc_expired_store(store)
        sess = store.get(key_id)
        if not sess:
            return None
        expires_at = float(sess.get("expires_at") or 0)
        if expires_at <= _rsa_now():
            store.pop(key_id, None)
            _save_rsa_sessions_unlocked(store)
            return None
        pem_str = (sess.get("private_key_pem") or "").strip()
    if not pem_str:
        return None
    try:
        import base64

        private_key = load_pem_private_key(pem_str.encode("utf-8"), password=None)
        cipher = base64.b64decode(encrypted_b64.encode("utf-8"), validate=True)
        plain = private_key.decrypt(
            cipher,
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        with _file_lock:
            store = _load_rsa_sessions_unlocked()
            store.pop(key_id, None)
            _save_rsa_sessions_unlocked(store)
        return plain.decode("utf-8", errors="ignore")
    except Exception:
        return None



def _is_backup_protected_path(path: str) -> bool:
    if not path:
        return False
    return any(path.startswith(p) for p in _BACKUP_PROTECTED_PREFIXES)


def _cron_execute_bypass_ok() -> bool:
    """
    定时任务脚本用 curl 调 POST /api/backup-jobs/<id>/execute，无用户 Bearer。
    放行方式（二选一）：
    - 环境变量 BACKUP_CRON_SECRET 非空，且请求头 X-Backup-Cron-Secret 与其一致；
    - 环境变量 BACKUP_ALLOW_LOCAL_EXECUTE 为 1/true/yes，且请求来自本机 127.0.0.1 / ::1。
    """
    if request.method != "POST":
        return False
    p = (request.path or "").rstrip("/")
    if not p.startswith("/api/backup-jobs/") or not p.endswith("/execute"):
        return False
    secret = (os.environ.get("BACKUP_CRON_SECRET") or "").strip()
    if secret and (request.headers.get("X-Backup-Cron-Secret") or "").strip() == secret:
        return True
    allow_local = (os.environ.get("BACKUP_ALLOW_LOCAL_EXECUTE") or "1").strip().lower()
    if allow_local in ("1", "true", "yes", "on"):
        addr = (request.remote_addr or "").strip()
        if addr in ("127.0.0.1", "::1"):
            return True
    return False


def _api_cors_allow_headers() -> str:
    return (
        "Authorization, Content-Type, Accept-Language, X-Requested-With, "
        "X-Backup-Cron-Secret"
    )


@app.before_request
def _require_login_for_backup_apis():
    path = request.path or ""

    # 跨域预检（含 DELETE/PUT 等非简单请求）：须在鉴权前响应，并声明允许的方法与头
    if request.method == "OPTIONS" and path.startswith("/api/"):
        r = Response(status=204)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, PATCH, OPTIONS"
        r.headers["Access-Control-Allow-Headers"] = _api_cors_allow_headers()
        r.headers["Access-Control-Max-Age"] = "86400"
        return r

    if not _is_backup_protected_path(path):
        return None
    if _cron_execute_bypass_ok():
        return None
    if not _get_user_by_access_token():
        return _error("未登录或 token 无效", code=401, http_status=401)
    return None


@app.after_request
def _add_cors_headers_for_api(resp: Response):
    path = request.path or ""
    if not path.startswith("/api/"):
        return resp
    resp.headers.setdefault("Access-Control-Allow-Origin", "*")
    resp.headers.setdefault(
        "Access-Control-Allow-Methods",
        "GET, POST, PUT, DELETE, PATCH, OPTIONS",
    )
    resp.headers.setdefault("Access-Control-Allow-Headers", _api_cors_allow_headers())
    return resp


@app.route("/api/auth/rsa", methods=["GET"])
def auth_rsa_key():
    if not _rsa_available():
        return _error("服务端未安装 cryptography，无法启用登录密码加密", http_status=501)
    try:
        return _success(_rsa_issue_login_public_key())
    except Exception:
        return _error("生成 RSA 公钥失败", http_status=500)


@app.route("/api/auth/login", methods=["POST"])
def login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    encrypted_password = (payload.get("encryptedPassword") or "").strip()
    key_id = (payload.get("keyId") or "").strip()

    if not username or (not password and not (encrypted_password and key_id)):
        return _error("用户名或密码不能为空", http_status=400)

    if encrypted_password and key_id:
        plain = _rsa_decrypt_login_password_once(key_id, encrypted_password)
        if not plain:
            return _error("RSA 密钥无效或已过期", http_status=400)
        password = plain

    with _file_lock:
        accounts = _load_accounts()

    target = next(
        (
            x
            for x in accounts
            if (x.get("username") or "").strip() == username
            and (x.get("password") or "") == password
        ),
        None,
    )
    if not target:
        return _error("用户名或密码错误", http_status=401)

    access_token = _make_access_token(username)
    refresh_token = _make_refresh_token(username)

    def _login_put_tokens(d: dict[str, dict[str, str]]) -> None:
        d["access"][access_token] = username
        d["refresh"][refresh_token] = username

    _auth_tokens_mutate(_login_put_tokens)

    response = _success({"accessToken": access_token}, "登录成功")
    # 与 mock 行为一致：refresh token 走 httpOnly cookie
    response.set_cookie(
        "refreshToken",
        refresh_token,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


@app.route("/api/auth/register", methods=["POST"])
def register():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    encrypted_password = (payload.get("encryptedPassword") or "").strip()
    key_id = (payload.get("keyId") or "").strip()

    if encrypted_password and key_id:
        plain = _rsa_decrypt_login_password_once(key_id, encrypted_password)
        if not plain:
            return _error("RSA 密钥无效或已过期", http_status=400)
        password = plain

    if not username or not password:
        return _error("用户名或密码不能为空", http_status=400)
    if len(username) < 3:
        return _error("用户名至少 3 位", http_status=400)
    if len(password) < 6:
        return _error("密码至少 6 位", http_status=400)
    with _file_lock:
        accounts = _load_accounts()
        if any((x.get("username") or "").strip() == username for x in accounts):
            return _error("用户名已存在", http_status=400)
        accounts.append(
            {"account_id": _make_account_id(), "password": password, "username": username},
        )
        _save_accounts(accounts)

    return _success({"account_id": (accounts[-1].get("account_id") or ""), "username": username}, "注册成功")


@app.route("/api/auth/password", methods=["POST"])
def change_password():
    """
    密码修改：要求用户已登录（Authorization: Bearer <accessToken>）

    支持明文或 RSA-OAEP(SHA-256) 密文字段：
    - oldPassword / newPassword
    - encryptedOldPassword + oldKeyId
    - encryptedNewPassword + newKeyId
    """
    user = _get_user_by_access_token()
    if not user:
        return _unauthorized_like_nitro_mock()

    payload = request.get_json(silent=True) or {}
    username = (user.get("username") or "").strip()
    old_password = (payload.get("oldPassword") or "").strip()
    new_password = (payload.get("newPassword") or "").strip()

    encrypted_old = (payload.get("encryptedOldPassword") or "").strip()
    old_key_id = (payload.get("oldKeyId") or "").strip()
    encrypted_new = (payload.get("encryptedNewPassword") or "").strip()
    new_key_id = (payload.get("newKeyId") or "").strip()

    if encrypted_old and old_key_id:
        plain_old = _rsa_decrypt_login_password_once(old_key_id, encrypted_old)
        if not plain_old:
            return _error("旧密码 RSA 密钥无效或已过期", http_status=400)
        old_password = plain_old

    if encrypted_new and new_key_id:
        plain_new = _rsa_decrypt_login_password_once(new_key_id, encrypted_new)
        if not plain_new:
            return _error("新密码 RSA 密钥无效或已过期", http_status=400)
        new_password = plain_new

    if not old_password or not new_password:
        return _error("旧密码或新密码不能为空", http_status=400)
    if len(new_password) < 6:
        return _error("密码至少 6 位", http_status=400)

    with _file_lock:
        accounts = _load_accounts()

    changed = False
    for x in accounts:
        if (x.get("username") or "").strip() != username:
            continue
        if (x.get("password") or "") != old_password:
            continue
        x["password"] = new_password
        changed = True
        break

    if not changed:
        return _error("旧密码错误", http_status=401)

    with _file_lock:
        _save_accounts(accounts)

    return _success({}, "密码修改成功")


@app.route("/api/auth/codes", methods=["GET"])
def get_auth_codes():
    user = _get_user_by_access_token()
    if not user:
        return _error("未登录或 token 无效", code=401, http_status=401)
    return _success(["AC_1000001", "AC_1000002"])


@app.route("/api/user/info", methods=["GET"])
def get_user_info():
    user = _get_user_by_access_token()
    if not user:
        return _unauthorized_like_nitro_mock()
    return _success(_get_userinfo_payload_for_account(user))


@app.route("/api/menu/all", methods=["GET"])
def get_menu_all():
    """与 Nitro mock 的 /menu/all 对齐，供 accessMode=backend / mixed 使用（非 JWT token 也可）。"""
    user = _get_user_by_access_token()
    if not user:
        return _error("未登录或 token 无效", code=401, http_status=401)
    menus = _backend_menus_for_account(user)
    return _success(menus)


@app.route("/api/timezone/getTimezone", methods=["GET"])
def get_timezone():
    """
    对齐前端 getTimezoneApi 与 mock 返回：
    - 已设置返回时区字符串
    - 未设置返回 null
    """
    user = _get_user_by_access_token()
    if not user:
        return _error("未登录或 token 无效", code=401, http_status=401)
    username = (user.get("username") or "").strip()
    with _file_lock:
        timezone_map = _load_timezones()
    return _success(timezone_map.get(username))


def _unauthorized_like_nitro_mock():
    """与 apps/backend-mock 中 unAuthorizedResponse 一致，避免前端对响应体格式敏感。"""
    return (
        jsonify(
            {
                "code": -1,
                "data": None,
                "error": "Unauthorized Exception",
                "message": "Unauthorized Exception",
            },
        ),
        401,
    )


def _require_account_for_system() -> Optional[dict]:
    return _get_user_by_access_token()


# --- playground「系统管理」接口：原 Nitro mock 仅校验 JWT，本地 access token 会 401 并触发登出 ---
@app.route("/api/system/role/list", methods=["GET"])
def system_role_list():
    if not _require_account_for_system():
        return _unauthorized_like_nitro_mock()
    page = max(1, int(request.args.get("page") or 1))
    page_size = min(100, max(1, int(request.args.get("pageSize") or 20)))
    # 占位数据；需要持久化时可改为读写 json
    items_all: list[dict] = []
    name_q = (request.args.get("name") or "").strip().lower()
    if name_q:
        items_all = [x for x in items_all if name_q in str(x.get("name", "")).lower()]
    total = len(items_all)
    start = (page - 1) * page_size
    return _success({"items": items_all[start : start + page_size], "total": total})


@app.route("/api/system/menu/list", methods=["GET"])
def system_menu_list():
    if not _require_account_for_system():
        return _unauthorized_like_nitro_mock()
    return _success([])


@app.route("/api/system/menu/name-exists", methods=["GET"])
def system_menu_name_exists():
    if not _require_account_for_system():
        return _unauthorized_like_nitro_mock()
    return _success(False)


@app.route("/api/system/menu/path-exists", methods=["GET"])
def system_menu_path_exists():
    if not _require_account_for_system():
        return _unauthorized_like_nitro_mock()
    return _success(False)


@app.route("/api/system/dept/list", methods=["GET"])
def system_dept_list():
    if not _require_account_for_system():
        return _unauthorized_like_nitro_mock()
    return _success([])


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    token = _get_bearer_token()
    refresh_token = (request.cookies.get("refreshToken") or "").strip()

    def _logout_mut(d: dict[str, dict[str, str]]) -> None:
        if token:
            d["access"].pop(token, None)
        if refresh_token:
            d["refresh"].pop(refresh_token, None)

    _auth_tokens_mutate(_logout_mut)

    response = _success("", "退出成功")
    response.delete_cookie("refreshToken", path="/")
    return response


@app.route("/api/auth/refresh", methods=["POST"])
def refresh_token():
    refresh_token = (request.cookies.get("refreshToken") or "").strip()
    if not refresh_token:
        return ("", 403)

    snap = _auth_tokens_snapshot()
    username = (snap.get("refresh") or {}).get(refresh_token)
    if not username:
        return ("", 403)

    access_token = _make_access_token(username)

    def _refresh_mut(d: dict[str, dict[str, str]]) -> None:
        d["access"][access_token] = username

    _auth_tokens_mutate(_refresh_mut)
    # 与 mock 一致：refresh 接口返回纯字符串 token
    return access_token


def _ensure_store_file() -> None:
    os.makedirs(JSON_DIR, exist_ok=True)
    if not os.path.isfile(DB_INSTANCES_FILE):
        with open(DB_INSTANCES_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)


def _ensure_jobs_store_file() -> None:
    os.makedirs(JSON_DIR, exist_ok=True)
    if not os.path.isfile(BACKUP_JOBS_FILE):
        with open(BACKUP_JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)


def _ensure_account_store_file() -> None:
    os.makedirs(JSON_DIR, exist_ok=True)
    if not os.path.isfile(ACCOUNT_FILE):
        with open(ACCOUNT_FILE, "w", encoding="utf-8") as f:
            # 默认账号：admin / 123456（首次启动可直接登录，后续可手工改文件）
            json.dump(
                [
                    {
                        "account_id": _make_account_id(),
                        "password": "123456",
                        "username": "admin",
                    },
                ],
                f,
                ensure_ascii=False,
                indent=2,
            )


def _load_accounts() -> list[dict]:
    _ensure_account_store_file()
    with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []

    changed = False
    out: list[dict] = []
    for x in data:
        if not isinstance(x, dict):
            continue
        username = (x.get("username") or "").strip()
        password = x.get("password") or ""
        if not username or not isinstance(password, str):
            continue
        account_id = (x.get("account_id") or "").strip()
        if not account_id:
            account_id = _make_account_id()
            changed = True
        if "role" in x:
            changed = True
        out.append(
            {
                "account_id": account_id,
                "password": password,
                "username": username,
            },
        )

    if changed:
        _save_accounts(out)
    return out


def _save_accounts(items: list[dict]) -> None:
    _ensure_account_store_file()
    tmp_file = f"{ACCOUNT_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, ACCOUNT_FILE)


def _ensure_timezone_store_file() -> None:
    os.makedirs(JSON_DIR, exist_ok=True)
    if not os.path.isfile(TIMEZONE_FILE):
        with open(TIMEZONE_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)


def _load_timezones() -> dict[str, str]:
    _ensure_timezone_store_file()
    with open(TIMEZONE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        username = k.strip()
        tz = v.strip()
        if username and tz:
            out[username] = tz
    return out


def _load_instances() -> list[dict]:
    _ensure_store_file()
    with open(DB_INSTANCES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
        legacy_account_id = _legacy_default_account_id()
        changed = False
        for x in items:
            if not (x.get("account_id") or "").strip():
                x["account_id"] = legacy_account_id
                changed = True
        if changed:
            _save_instances(items)
        return items
    return []


def _save_instances(items: list[dict]) -> None:
    _ensure_store_file()
    tmp_file = f"{DB_INSTANCES_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, DB_INSTANCES_FILE)


def _load_jobs() -> list[dict]:
    _ensure_jobs_store_file()
    with open(BACKUP_JOBS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
        legacy_account_id = _legacy_default_account_id()
        changed = False
        for x in items:
            if not (x.get("account_id") or "").strip():
                x["account_id"] = legacy_account_id
                changed = True
        if changed:
            _save_jobs(items)
        return items
    return []


def _save_jobs(items: list[dict]) -> None:
    _ensure_jobs_store_file()
    tmp_file = f"{BACKUP_JOBS_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, BACKUP_JOBS_FILE)


def _read_crontab_lines() -> list[str]:
    """读取当前用户 crontab（按行返回）。"""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
            env=os.environ,
            check=False,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            if "no crontab" in stderr.lower() or "no crontab" in stdout.lower():
                return []
            return []
        return stdout.splitlines()
    except (subprocess.TimeoutExpired, Exception):  # noqa: BLE001
        return []


def _write_crontab_lines(lines: list[str]) -> bool:
    """覆盖写入 crontab 内容。"""
    try:
        text = "\n".join(lines).rstrip() + "\n" if lines else ""
        result = subprocess.run(
            ["crontab", "-"],
            input=text,
            text=True,
            timeout=10,
            env=os.environ,
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):  # noqa: BLE001
        return False


def _crontab_has_job_marker(job_id: str) -> bool:
    """判断当前用户 crontab 中是否存在该 job 的标记行（与 _sync_job_crontab 写入格式一致）。"""
    jid = (job_id or "").strip()
    if not jid:
        return False
    expected = f"{CRON_MARK_PREFIX}{jid}"
    for line in _read_crontab_lines():
        if line.strip() == expected:
            return True
    return False


def _build_job_script(job: dict) -> str:
    job_id = (job.get("id") or "").strip()
    api_url = f"http://127.0.0.1:8081/api/backup-jobs/{job_id}/execute"
    meta_log_path = os.path.join(JOB_SCRIPT_LOGS_DIR, f"{job_id}.log")
    run_log_path = os.path.join(JOB_SCRIPT_LOGS_DIR, f"{job_id}.run.log")
    logs_dir = JOB_SCRIPT_LOGS_DIR
    cron_secret = (os.environ.get("BACKUP_CRON_SECRET") or "").strip()
    curl_extra = ""
    if cron_secret:
        curl_extra = f" -H {shlex.quote(f'X-Backup-Cron-Secret: {cron_secret}')}"
    return "\n".join(
        [
            "#!/bin/bash",
            'PATH="/usr/local/bin:/usr/bin:/bin:$PATH"',
            f'mkdir -p {shlex.quote(logs_dir)}',
            f'echo "$(date +\'%Y-%m-%d %H:%M:%S\') cron trigger job={job_id}" >> {shlex.quote(meta_log_path)}',
            f"curl -sS -X POST{curl_extra} {shlex.quote(api_url)} >> {shlex.quote(run_log_path)} 2>> {shlex.quote(meta_log_path)}",
            "rc=$?",
            'echo "" >> ' + shlex.quote(run_log_path),
            'echo "---" >> ' + shlex.quote(run_log_path),
            'exit "$rc"',
            "",
        ],
    )


def _normalize_cron_schedule_for_system(schedule: str) -> str:
    """
    对部分 cron 实现做兼容：
    - 将 `0/30` 规范为 `*/30`（某些环境不接受前者）
    仅用于写入系统 crontab，不改变业务层原始表达式存储。
    """
    s = (schedule or "").strip()
    if not s:
        return s
    parts = re.split(r"\s+", s)
    if len(parts) != 5:
        return s
    normalized = []
    for p in parts:
        m = re.fullmatch(r"0/(\d+)", p)
        if m:
            normalized.append(f"*/{m.group(1)}")
        else:
            normalized.append(p)
    return " ".join(normalized)


def _sync_job_crontab(job: dict, *, remove_only: bool = False) -> tuple[bool, str]:
    """按 job 配置同步系统 crontab，并确保生成/清理专属脚本。"""
    job_id = (job.get("id") or "").strip()
    if not job_id:
        return False, "job id 不能为空"

    schedule = _normalize_cron_schedule_for_system((job.get("schedule") or "").strip())
    enabled = bool(job.get("enabled"))
    os.makedirs(JOBS_DIR, exist_ok=True)
    os.makedirs(JOB_SCRIPT_LOGS_DIR, exist_ok=True)
    script_path = os.path.join(JOBS_DIR, f"{job_id}.sh")

    # 先移除旧条目
    lines = _read_crontab_lines()
    new_lines: list[str] = []
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        if line.strip().startswith(CRON_MARK_PREFIX) and job_id in line:
            skip_next = True
            continue
        new_lines.append(line)

    if remove_only or not enabled or not schedule:
        _write_crontab_lines(new_lines)
        return True, "已移除定时任务"

    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(_build_job_script(job))
        os.chmod(script_path, 0o755)
    except OSError as exc:
        return False, f"写入任务脚本失败: {exc}"

    new_lines.append(f"{CRON_MARK_PREFIX}{job_id}")
    new_lines.append(f"{schedule} sh {shlex.quote(script_path)}")
    if not _write_crontab_lines(new_lines):
        return False, "写入 crontab 失败"
    return True, "已同步定时任务"


def _ensure_backup_files_store() -> None:
    os.makedirs(JSON_DIR, exist_ok=True)
    if not os.path.isfile(BACKUP_FILES_FILE):
        with open(BACKUP_FILES_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)


def _load_backup_files() -> list[dict]:
    _ensure_backup_files_store()
    with open(BACKUP_FILES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        items = [item for item in data if isinstance(item, dict)]
        legacy_account_id = _legacy_default_account_id()
        changed = False
        for x in items:
            if not (x.get("account_id") or "").strip():
                x["account_id"] = legacy_account_id
                changed = True
            if not (x.get("backup_file_id") or "").strip():
                x["backup_file_id"] = str(uuid.uuid4())
                changed = True
            if not (x.get("backup_type") or "").strip():
                x["backup_type"] = "full"
                changed = True
            if "full_backup_file_id" not in x:
                x["full_backup_file_id"] = ""
                changed = True
            if "job_id" not in x:
                x["job_id"] = ""
                changed = True
            if "db_instance_id" not in x:
                x["db_instance_id"] = ""
                changed = True
        if changed:
            _save_backup_files(items)
        return items
    return []


def _save_backup_files(items: list[dict]) -> None:
    _ensure_backup_files_store()
    tmp_file = f"{BACKUP_FILES_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, BACKUP_FILES_FILE)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_backup_file(item: dict) -> dict:
    size = _to_int(item.get("size"), 0)
    return {
        "account_id": (item.get("account_id") or "").strip(),
        "backup_file_id": (item.get("backup_file_id") or "").strip(),
        "backup_type": (item.get("backup_type") or "full").strip().lower() or "full",
        "full_backup_file_id": (item.get("full_backup_file_id") or "").strip(),
        "job_id": (item.get("job_id") or "").strip(),
        "db_instance_id": (item.get("db_instance_id") or "").strip(),
        "binlog_start_file": (item.get("binlog_start_file") or "").strip(),
        "binlog_start_pos": _to_int(item.get("binlog_start_pos"), 0),
        "binlog_end_file": (item.get("binlog_end_file") or "").strip(),
        "binlog_end_pos": _to_int(item.get("binlog_end_pos"), 0),
        "backupDir": (item.get("backupDir") or "").strip(),
        "backupTime": (item.get("backupTime") or "").strip(),
        "database": (item.get("database") or "").strip(),
        "dirName": (item.get("dirName") or "").strip(),
        "size": size,
    }


def _extract_backup_output_dir_from_script_log(stdout: str) -> str:
    """从 mysql-backup-mydumper.sh 的 tee 日志中解析「输出目录」绝对路径。"""
    if not stdout:
        return ""
    m = re.search(r"输出目录:\s*(\S+)", stdout)
    if m:
        return m.group(1).strip()
    m = re.search(r"^\s*目录:\s*(\S+)", stdout, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


def _dir_name_to_backup_time(dir_name: str) -> str:
    """
    目录名形如 …_{YYYYMMDD}_{HHMMSS}（末尾两段），转为 YYYY-MM-DD HH:MM:SS。
    无法解析则返回当前时间。
    """
    m = re.search(r"_(\d{8})_(\d{6})$", dir_name.strip())
    if not m:
        return _now_str()
    d, t = m.group(1), m.group(2)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}:{t[4:6]}"


def _directory_size_bytes(path: str) -> int:
    if not path or not os.path.isdir(path):
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                continue
    return total


def _allowed_backup_realpath_roots() -> list[str]:
    """允许下载打包的备份根路径（realpath）。可通过环境变量 BACKUP_DOWNLOAD_ROOTS 追加，多个用英文冒号分隔。"""
    candidates = [
        os.path.join(BACK_DIR, "data"),
        # 兼容旧路径：历史数据仍可能落在 /data/backup/mysql
        "/data/backup/mysql",
        # 兼容：如果你把仓库 back/backup 映射进了容器
        os.path.join(REPO_DIR, "backup"),
    ]
    extra = os.environ.get("BACKUP_DOWNLOAD_ROOTS", "").strip()
    if extra:
        candidates.extend([x.strip() for x in extra.split(":") if x.strip()])
    roots: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        try:
            rp = os.path.realpath(c)
        except OSError:
            continue
        if rp not in seen:
            seen.add(rp)
            roots.append(rp)
    return roots


def _is_backup_dir_safe_to_download(resolved_dir: str) -> bool:
    """防止 backup-files.json 被篡改后指向任意系统目录；路径须存在且为目录，且落在允许根下。"""
    try:
        resolved_dir = os.path.realpath(resolved_dir)
    except OSError:
        return False
    if not os.path.isdir(resolved_dir):
        return False
    for root in _allowed_backup_realpath_roots():
        if resolved_dir == root or resolved_dir.startswith(root + os.sep):
            return True
    return False


def _resolve_existing_backup_dir(backup_dir: str, dir_name: str) -> Tuple[Optional[str], Optional[str]]:
    """
    将 backup-files 中的 backupDir 解析为可访问的会话目录 realpath。
    若记录路径不存在，则尝试：
    - ${BACK_DIR}/data/<dirName>
    - 仓库内回退目录：${REPO_DIR}/backup/<dirName>（兼容历史映射）
    """
    seen: set[str] = set()
    candidates: list[str] = []
    # 优先按当前运行环境目录解析，避免历史绝对路径（如 /app/...）指向旧挂载数据
    for p in (
        os.path.join(BACK_DIR, "data", (dir_name or "").strip()),
        (backup_dir or "").strip(),
        os.path.join(REPO_DIR, "backup", (dir_name or "").strip()),
    ):
        if not p:
            continue
        try:
            rp = os.path.realpath(p)
        except OSError:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        candidates.append(rp)

    for resolved in candidates:
        if not os.path.isdir(resolved):
            continue
        if _is_backup_dir_safe_to_download(resolved):
            return resolved, None
    return None, "备份目录不存在或不在允许访问的路径下"


def _mydumper_metadata_path(resolved_backup_root: str) -> Optional[str]:
    """
    返回 mydumper metadata 文件绝对路径（含写入过程中的 .partial）。
    优先：{root}/data/metadata；其次 data/metadata.partial；
    兼容旧版：{root}/metadata、metadata.partial。
    """
    root = (resolved_backup_root or "").strip()
    if not root:
        return None
    for rel in (
        os.path.join("data", "metadata"),
        os.path.join("data", "metadata.partial"),
        "metadata",
        "metadata.partial",
    ):
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            return path
    return None


def _parse_mydumper_metadata_tables(metadata_path: str) -> list[dict[str, Any]]:
    """
    解析 mydumper 生成的 metadata 文件，列出库表与视图。
    节标题形如 [`db`.`name`]；视图块内通常含 is_view = 1。
    """
    items: list[dict[str, Any]] = []
    if not os.path.isfile(metadata_path):
        return items

    section_re = re.compile(r"^\[`([^`]+)`\.`([^`]+)`\]\s*$")
    db_only_re = re.compile(r"^\[`([^`]+)`\]\s*$")

    current: Optional[Tuple[str, str]] = None
    buf: dict[str, str] = {}

    def flush() -> None:
        nonlocal current, buf
        if not current:
            buf = {}
            return
        schema, name = current
        is_view = (buf.get("is_view") or "").strip() == "1"
        rows_raw = (buf.get("rows") or "").strip()
        try:
            rows = int(rows_raw) if rows_raw != "" else 0
        except ValueError:
            rows = 0
        real_name = (buf.get("real_table_name") or name).strip()
        items.append(
            {
                "schema": schema,
                "name": name,
                "real_table_name": real_name,
                "rows": rows,
                "kind": "view" if is_view else "table",
            },
        )
        buf = {}

    with open(metadata_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n\r")
            m = section_re.match(line)
            if m:
                flush()
                current = (m.group(1), m.group(2))
                continue
            mdb = db_only_re.match(line)
            if mdb:
                flush()
                current = None
                continue
            if current and "=" in line and not line.lstrip().startswith("#"):
                key, _, val = line.partition("=")
                k = key.strip()
                if k:
                    buf[k] = val.strip()
        flush()

    return items


def _infer_mydumper_objects_from_data_files(
    resolved_backup_root: str,
) -> list[dict[str, Any]]:
    """
    无完整 metadata（备份中断、仅 metadata.partial 等）时，根据 mydumper 在 data/ 下的
    文件名推断库表清单。命名约定：{schema}.{table}.{分片}.sql.zst、{schema}.{table}-schema.sql.zst。
    """
    root = (resolved_backup_root or "").strip()
    if not root:
        return []
    data_dir = os.path.join(root, "data")
    scan_dir = data_dir if os.path.isdir(data_dir) else root
    if not os.path.isdir(scan_dir):
        return []

    try:
        names = os.listdir(scan_dir)
    except OSError:
        return []

    # mall.pms_product.00000.sql.zst
    re_data = re.compile(r"^([^.]+)\.([^./]+)\.(\d+)\.sql\.zst$")
    # mall.pms_product-schema.sql.zst（排除 mall-schema-create.sql.zst）
    re_tbl_schema = re.compile(r"^([^.]+)\.(.+)-schema\.sql\.zst$")

    seen: dict[tuple[str, str], dict[str, Any]] = {}

    for fn in names:
        if not fn.endswith(".sql.zst"):
            continue
        if fn.endswith("-schema-create.sql.zst"):
            continue
        if fn.startswith("metadata"):
            continue
        m = re_data.match(fn)
        if m:
            schema, tbl = m.group(1), m.group(2)
            key = (schema, tbl)
            if key not in seen:
                seen[key] = {
                    "schema": schema,
                    "name": tbl,
                    "real_table_name": tbl,
                    "rows": 0,
                    "kind": "table",
                }
            continue
        m = re_tbl_schema.match(fn)
        if m:
            schema, tbl = m.group(1), m.group(2)
            key = (schema, tbl)
            if key not in seen:
                seen[key] = {
                    "schema": schema,
                    "name": tbl,
                    "real_table_name": tbl,
                    "rows": 0,
                    "kind": "table",
                }

    return sorted(seen.values(), key=lambda x: (x["schema"], x["name"]))


def _get_backup_record_and_resolved_dir(
    dir_name: str,
) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
    """
    根据 dirName 查 backup-files 记录并解析为允许访问的备份目录 realpath。
    返回 (record, resolved_dir, error_message)；无错误时 error_message 为 None。
    """
    dir_name = unquote(dir_name).strip()
    if not dir_name:
        return None, None, "dirName 无效"
    with _file_lock:
        raw = _load_backup_files()
    target = None
    for x in raw:
        if (x.get("dirName") or "").strip() == dir_name:
            target = _normalize_backup_file(x)
            break
    if not target:
        return None, None, "记录不存在"
    backup_dir = (target.get("backupDir") or "").strip()
    if not backup_dir:
        return None, None, "记录缺少 backupDir"
    resolved, rerr = _resolve_existing_backup_dir(backup_dir, dir_name)
    if rerr or not resolved:
        return None, None, rerr or "备份路径无效"
    return target, resolved, None


def _mysql_show_master_status(instance: dict) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """返回当前实例的 (binlog_file, binlog_pos, err)。"""
    client_bin = shutil.which("mysql") or shutil.which("mariadb")
    if not client_bin:
        return None, None, "未找到 mysql/mariadb 客户端"
    env = os.environ.copy()
    env["MYSQL_PWD"] = str(instance.get("password") or "")
    cmd = [
        client_bin,
        "-h",
        str(instance.get("host") or ""),
        "-P",
        str(instance.get("port") or ""),
        "-u",
        str(instance.get("user") or ""),
        "-N",
        "-e",
        "SHOW MASTER STATUS",
    ]
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, None, "获取 binlog 位点超时"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return None, None, (err or "获取 binlog 位点失败")
    line = ((proc.stdout or "").strip().splitlines() or [""])[0]
    parts = [x for x in line.split("\t") if x != ""]
    if len(parts) < 2:
        return None, None, "未获取到 MASTER STATUS"
    file_name = parts[0].strip()
    pos = _to_int(parts[1], 0)
    if not file_name or pos <= 0:
        return None, None, "MASTER STATUS 无效"
    return file_name, pos, None


def _extract_source_log_point_from_metadata(
    full_backup_dir: str,
) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """从全量备份 metadata 的 [source] 段解析 SOURCE_LOG_FILE/SOURCE_LOG_POS。"""
    metadata_path = _mydumper_metadata_path(full_backup_dir)
    if not metadata_path:
        return None, None, "全量备份缺少 metadata，无法提取 SOURCE_LOG_POS"
    try:
        with open(metadata_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        return None, None, f"读取 metadata 失败: {exc}"

    in_source = False
    log_file = ""
    log_pos = 0
    for raw in lines:
        line = raw.strip()
        if re.match(r"^\[.+\]$", line):
            in_source = line.lower() == "[source]"
            continue
        if not in_source:
            continue
        m_file = re.match(r'^#?\s*SOURCE_LOG_FILE\s*=\s*"?([^"]+)"?\s*$', line, flags=re.IGNORECASE)
        if m_file:
            log_file = (m_file.group(1) or "").strip()
            continue
        m_pos = re.match(r"^#?\s*SOURCE_LOG_POS\s*=\s*([0-9]+)\s*$", line, flags=re.IGNORECASE)
        if m_pos:
            log_pos = _to_int(m_pos.group(1), 0)
            continue
    if not log_file or log_pos <= 0:
        return None, None, "全量备份 metadata 中缺少 SOURCE_LOG_FILE/SOURCE_LOG_POS"
    return log_file, log_pos, None


def _find_latest_increment_for_full_backup(
    *,
    account_id: str,
    full_backup_file_id: str,
) -> Optional[dict]:
    with _file_lock:
        items = [_normalize_backup_file(x) for x in _load_backup_files()]
    candidates = [
        x
        for x in items
        if (x.get("account_id") or "").strip() == (account_id or "").strip()
        and (x.get("backup_type") or "").strip() == "increment"
        and (x.get("full_backup_file_id") or "").strip() == (full_backup_file_id or "").strip()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.get("backupTime") or "", reverse=True)
    return candidates[0]


def _find_backup_file_by_id(*, account_id: str, backup_file_id: str) -> Optional[dict]:
    bid = (backup_file_id or "").strip()
    if not bid:
        return None
    with _file_lock:
        items = [_normalize_backup_file(x) for x in _load_backup_files()]
    return next(
        (
            x
            for x in items
            if (x.get("account_id") or "").strip() == (account_id or "").strip()
            and (x.get("backup_file_id") or "").strip() == bid
        ),
        None,
    )


def _is_session_dir_under_backup_root(session_dir: str, backup_root: str) -> bool:
    """会话目录须在备份根之下（防止路径穿越）。"""
    try:
        s = os.path.realpath(session_dir)
        r = os.path.realpath(backup_root)
    except OSError:
        return False
    return s == r or s.startswith(r + os.sep)


def _background_backup_job(
    *,
    cmd: list[str],
    session_dir_name: str,
    full_path: str,
    timeout_seconds: int,
) -> None:
    """后台线程执行 mydumper；成功则更新 backup-files.json，失败则移除预登记。"""
    proc: Optional[subprocess.CompletedProcess[str]] = None
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[backup] timeout session={session_dir_name!r} timeout={timeout_seconds}s",
            flush=True,
        )
        _mark_backup_file_failed(
            dir_name=session_dir_name,
            backup_dir_path=full_path,
            exit_code=None,
            error_summary=f"timeout {timeout_seconds}s",
        )
        return
    except Exception as exc:  # noqa: BLE001
        print(
            f"[backup] subprocess error session={session_dir_name!r}: {exc}",
            flush=True,
        )
        _mark_backup_file_failed(
            dir_name=session_dir_name,
            backup_dir_path=full_path,
            exit_code=None,
            error_summary=str(exc),
        )
        return

    assert proc is not None
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        parsed_dir_on_fail = (
            (_extract_backup_output_dir_from_script_log(stdout) or "").strip() or full_path
        )
        if _looks_like_backup_completed(backup_dir_path=parsed_dir_on_fail, stdout_text=stdout):
            try:
                _update_backup_file_record_size(
                    dir_name=session_dir_name,
                    backup_dir_path=parsed_dir_on_fail,
                )
            except Exception:  # noqa: BLE001
                pass
            return
        print(
            f"[backup] script failed rc={proc.returncode} session={session_dir_name!r}\n"
            f"stdout tail: {stdout[-4000:]!s}\n"
            f"stderr tail: {stderr[-2000:]!s}",
            flush=True,
        )
        _mark_backup_file_failed(
            dir_name=session_dir_name,
            backup_dir_path=parsed_dir_on_fail,
            exit_code=proc.returncode,
            error_summary="mydumper failed, see logs",
        )
        return

    parsed_dir = (_extract_backup_output_dir_from_script_log(stdout) or "").strip() or full_path
    try:
        _update_backup_file_record_size(
            dir_name=session_dir_name,
            backup_dir_path=parsed_dir,
        )
    except Exception:  # noqa: BLE001
        pass


def _background_restore_job(
    *,
    cmd: list[str],
    timeout_seconds: int,
    env: Optional[dict[str, str]] = None,
    post_cmds: Optional[list[list[str]]] = None,
) -> None:
    """后台线程执行还原流程：先 myloader，再按需回放增量。"""
    start_at = time.time()

    def _remain_timeout() -> int:
        elapsed = int(time.time() - start_at)
        left = timeout_seconds - elapsed
        return left if left > 1 else 1

    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=_remain_timeout(),
            check=False,
            env=env,
        )
        if proc.returncode != 0:
            return
        for post in post_cmds or []:
            proc2 = subprocess.run(
                post,
                cwd=REPO_DIR,
                capture_output=True,
                text=True,
                timeout=_remain_timeout(),
                check=False,
                env=env,
            )
            if proc2.returncode != 0:
                return
    except (subprocess.TimeoutExpired, Exception):  # noqa: BLE001
        return


def _insert_pending_backup_file_record(
    *,
    backup_dir_path: str,
    database: str,
    account_id: str,
    db_instance_id: str = "",
    backup_type: str = "full",
    full_backup_file_id: str = "",
    job_id: str = "",
    binlog_start_file: str = "",
    binlog_start_pos: int = 0,
    binlog_end_file: str = "",
    binlog_end_pos: int = 0,
) -> Optional[dict]:
    """
    备份开始前写入 backup-files.json：含 dirName、backupDir、backupTime、database，size=0。
    同 dirName 会覆盖旧记录。
    """
    backup_dir_path = (backup_dir_path or "").strip()
    if not backup_dir_path:
        return None
    dir_name = os.path.basename(os.path.normpath(backup_dir_path))
    if not dir_name:
        return None
    backup_time = _dir_name_to_backup_time(dir_name)
    record = _normalize_backup_file(
        {
            "account_id": (account_id or "").strip(),
            "db_instance_id": (db_instance_id or "").strip(),
            "backup_file_id": str(uuid.uuid4()),
            "backup_type": (backup_type or "full").strip().lower() or "full",
            "full_backup_file_id": (full_backup_file_id or "").strip(),
            "job_id": (job_id or "").strip(),
            "binlog_start_file": (binlog_start_file or "").strip(),
            "binlog_start_pos": _to_int(binlog_start_pos, 0),
            "binlog_end_file": (binlog_end_file or "").strip(),
            "binlog_end_pos": _to_int(binlog_end_pos, 0),
            "backupDir": backup_dir_path,
            "backupTime": backup_time,
            "database": (database or "").strip(),
            "dirName": dir_name,
            "size": 0,
        },
    )
    with _file_lock:
        items = _load_backup_files()
        items = [x for x in items if (x.get("dirName") or "").strip() != record["dirName"]]
        items.insert(0, record)
        _save_backup_files(items)
    return record


def _update_backup_file_record_size(
    *,
    dir_name: str,
    backup_dir_path: str,
) -> Optional[dict]:
    """备份成功后更新对应记录的 size（及 backupDir，以日志解析路径为准），并标记状态为 success。"""
    dir_name = (dir_name or "").strip()
    backup_dir_path = (backup_dir_path or "").strip()
    if not dir_name or not backup_dir_path:
        return None
    size = _directory_size_bytes(backup_dir_path)
    with _file_lock:
        items = _load_backup_files()
        out: Optional[dict] = None
        for i, x in enumerate(items):
            if (x.get("dirName") or "").strip() != dir_name:
                continue
            merged = {
                **x,
                "status": "success",
                "lastExitCode": 0,
                "lastError": "",
                "backupDir": backup_dir_path,
                "size": size,
            }
            items[i] = _normalize_backup_file(merged)
            out = items[i]
            break
        if out is None:
            return None
        _save_backup_files(items)
    return out


def _mark_backup_file_failed(
    *,
    dir_name: str,
    backup_dir_path: str,
    exit_code: Optional[int] = None,
    error_summary: str = "",
) -> Optional[dict]:
    """
    备份失败或超时：保留预登记记录，但标记状态为 failed，并附带错误摘要 / 退出码，便于排查。
    size 仍为 0（或原值）。
    """
    dir_name = (dir_name or "").strip()
    if not dir_name:
        return None
    backup_dir_path = (backup_dir_path or "").strip()
    with _file_lock:
        items = _load_backup_files()
        out: Optional[dict] = None
        for i, x in enumerate(items):
            if (x.get("dirName") or "").strip() != dir_name:
                continue
            merged = {
                **x,
                "status": "failed",
                "lastExitCode": int(exit_code or 1),
                "lastError": (error_summary or "").strip(),
            }
            if backup_dir_path:
                merged["backupDir"] = backup_dir_path
            items[i] = _normalize_backup_file(merged)
            out = items[i]
            break
        if out is None:
            return None
        _save_backup_files(items)
    return out


def _looks_like_backup_completed(
    *,
    backup_dir_path: str,
    stdout_text: str = "",
) -> bool:
    """
    兜底判断备份是否基本完成：
    - 目录存在且为目录
    - data/metadata（或 metadata.partial）存在
    - stdout 中出现「备份完成。」关键字
    """
    p = (backup_dir_path or "").strip()
    if not p:
        return False
    try:
        rp = os.path.realpath(p)
    except OSError:
        return False
    if not os.path.isdir(rp):
        return False
    data_dir = os.path.join(rp, "data")
    has_metadata = os.path.isfile(os.path.join(data_dir, "metadata")) or os.path.isfile(
        os.path.join(data_dir, "metadata.partial"),
    )
    if not has_metadata:
        return False
    return "备份完成。" in (stdout_text or "")


def _append_backup_file_record(
    *,
    backup_dir_path: str,
    database: str,
    account_id: str = "",
    db_instance_id: str = "",
    backup_type: str = "full",
    full_backup_file_id: str = "",
    job_id: str = "",
    binlog_start_file: str = "",
    binlog_start_pos: int = 0,
    binlog_end_file: str = "",
    binlog_end_pos: int = 0,
) -> Optional[dict]:
    """一次性写入完整记录（含 size）。供兼容旧逻辑或手工补录；正常即时备份请用先 pending 再 update。"""
    backup_dir_path = (backup_dir_path or "").strip()
    if not backup_dir_path:
        return None
    dir_name = os.path.basename(os.path.normpath(backup_dir_path))
    if not dir_name:
        return None
    backup_time = _dir_name_to_backup_time(dir_name)
    size = _directory_size_bytes(backup_dir_path)
    record = _normalize_backup_file(
        {
            "account_id": (account_id or "").strip(),
            "db_instance_id": (db_instance_id or "").strip(),
            "backup_file_id": str(uuid.uuid4()),
            "backup_type": (backup_type or "full").strip().lower() or "full",
            "full_backup_file_id": (full_backup_file_id or "").strip(),
            "job_id": (job_id or "").strip(),
            "binlog_start_file": (binlog_start_file or "").strip(),
            "binlog_start_pos": _to_int(binlog_start_pos, 0),
            "binlog_end_file": (binlog_end_file or "").strip(),
            "binlog_end_pos": _to_int(binlog_end_pos, 0),
            "backupDir": backup_dir_path,
            "backupTime": backup_time,
            "database": (database or "").strip(),
            "dirName": dir_name,
            "size": size,
        },
    )
    with _file_lock:
        items = _load_backup_files()
        items = [x for x in items if (x.get("dirName") or "").strip() != record["dirName"]]
        items.insert(0, record)
        _save_backup_files(items)
    return record


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        return v in {"true", "1", "yes", "y", "on"}
    return False


def _now_str() -> str:
    # 与你给定数据格式一致：YYYY-MM-DD HH:MM:SS
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _normalize_job_payload(payload: dict) -> dict:
    return {
        "id": (payload.get("id") or "").strip(),
        "account_id": (payload.get("account_id") or "").strip(),
        "name": (payload.get("name") or "").strip(),
        "schedule": (payload.get("schedule") or "").strip(),
        "backup_type": (payload.get("backup_type") or "").strip(),
        # 关联的数据库实例 id（与 db-instances.json 一致）
        "db_instance_id": (payload.get("db_instance_id") or "").strip(),
        # 增量任务：关联的全量任务 job id，用于执行时解析基线 full_backup_dir
        "linked_full_backup_job_id": (payload.get("linked_full_backup_job_id") or "").strip(),
        "tables": (payload.get("tables") or "").strip(),
        "ignore_tables": (payload.get("ignore_tables") or "").strip(),
        "clean_days": payload.get("clean_days"),
        "enabled": _to_bool(payload.get("enabled")),
        # 时间字段：允许为空，更新时由后端保留旧值
        "created_at": (payload.get("created_at") or "").strip(),
        "last_run_at": (payload.get("last_run_at") or "").strip(),
    }


def _db_instance_id_exists(instance_id: str, account_id: Optional[str] = None) -> bool:
    iid = (instance_id or "").strip()
    if not iid:
        return False
    with _file_lock:
        items = _load_instances()
    if not account_id:
        return any((x.get("id") or "").strip() == iid for x in items)
    aid = (account_id or "").strip()
    return any(
        (x.get("id") or "").strip() == iid
        and (x.get("account_id") or "").strip() == aid
        for x in items
    )


def _validate_job_payload(data: dict, is_create: bool) -> tuple[bool, str]:
    required_fields = [
        "account_id",
        "name",
        "schedule",
        "backup_type",
        "clean_days",
        "db_instance_id",
    ]

    if is_create:
        required_fields = [*required_fields]

    for field in required_fields:
        value = data.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return False, f"参数缺失或为空: {field}"

    clean_days = data.get("clean_days")
    try:
        data["clean_days"] = int(clean_days)
        if data["clean_days"] < 0:
            return False, "clean_days 必须大于等于 0"
    except (TypeError, ValueError):
        return False, "clean_days 必须是数字"

    # backup_type 规范化 + 增量任务关联校验
    data["backup_type"] = (data.get("backup_type") or "").strip().lower()
    linked_full_id = (data.get("linked_full_backup_job_id") or "").strip()
    if data["backup_type"] == "incremental" and not linked_full_id:
        return False, "参数缺失或为空: linked_full_backup_job_id"

    account_id = str(data.get("account_id") or "").strip()
    if not _db_instance_id_exists(
        str(data.get("db_instance_id") or ""), account_id=account_id
    ):
        return False, "数据库实例不存在或 id 无效"

    if data["backup_type"] == "incremental":
        linked_full_id = (data.get("linked_full_backup_job_id") or "").strip()
        jobs = _load_jobs()
        full_ok = any(
            (j.get("id") or "").strip() == linked_full_id
            and (j.get("backup_type") or "").strip().lower() == "full"
            and (j.get("account_id") or "").strip() == account_id
            for j in jobs
        )
        if not full_ok:
            return False, "关联的全量任务不存在或不属于当前账号"
        if _linked_full_taken_by_other_incremental(
            jobs,
            linked_full_id,
            (data.get("id") or "").strip(),
            account_id,
        ):
            return False, "每个全量备份定时任务仅允许关联一个增量备份定时任务"

    # boolean 字段永远不为空（_to_bool 兜底）
    data["enabled"] = _to_bool(data.get("enabled"))

    return True, ""


def _linked_full_taken_by_other_incremental(
    jobs: list[dict],
    linked_full_id: str,
    exclude_job_id: str,
    account_id: str,
) -> bool:
    """同一全量任务仅允许被一个增量任务关联（exclude_job_id 为当前保存任务，可排除自身）。"""
    lid = (linked_full_id or "").strip()
    if not lid:
        return False
    ex = (exclude_job_id or "").strip()
    for j in jobs:
        if (j.get("id") or "") == ex:
            continue
        if (j.get("account_id") or "").strip() != (account_id or "").strip():
            continue
        if (j.get("backup_type") or "").strip().lower() != "incremental":
            continue
        if (j.get("linked_full_backup_job_id") or "").strip() == lid:
            return True
    return False


def _start_backup_for_instance(instance: dict, payload: dict) -> tuple[bool, Any, int]:
    """
    基于实例配置提交备份任务（全量: mydumper；增量: mysqlbinlog，后台执行）。
    返回 (ok, data_or_message, http_status)。
    """
    backup_mode = (payload.get("backup_type") or "full").strip().lower()
    if backup_mode == "incremental":
        backup_mode = "increment"
    if backup_mode not in {"full", "increment"}:
        return False, "backup_type 仅支持 full 或 increment", 400

    backup_dir = (payload.get("backup_dir") or "").strip()
    tables = (payload.get("tables") or "").strip()
    ignore_tables = (payload.get("ignore_tables") or "").strip()
    clean_days = payload.get("clean_days")
    threads = payload.get("threads")
    max_threads_per_table = payload.get("max_threads_per_table")
    compress = (payload.get("compress") or "").strip()
    timeout_seconds = payload.get("timeout_seconds", 3600)
    job_id = (payload.get("job_id") or "").strip()
    full_backup_file_id = (payload.get("full_backup_file_id") or "").strip()

    db_name = str(instance.get("database") or "").strip()
    account_id = (instance.get("account_id") or "").strip()
    instance_id = (instance.get("id") or "").strip()
    if not db_name:
        return False, "实例 database 不能为空", 400

    backup_root = backup_dir or _DEFAULT_BACKUP_ROOT
    if not os.path.isabs(backup_root):
        backup_root = os.path.abspath(os.path.join(BACK_DIR, backup_root))
    try:
        os.makedirs(backup_root, mode=0o755, exist_ok=True)
    except OSError as exc:
        return False, f"无法创建备份根目录 {backup_root}: {exc}", 500
    session_dir_name = f"{db_name}_{time.strftime('%Y%m%d_%H%M%S')}"
    try:
        full_path = os.path.normpath(os.path.join(backup_root, session_dir_name))
    except OSError:
        return False, "备份会话路径无效", 400

    if not _is_session_dir_under_backup_root(full_path, backup_root):
        return False, "备份会话目录不在备份根路径下", 400

    script_path = os.path.join(
        SCRIPT_DIR,
        "mysql-backup-binlog.sh" if backup_mode == "increment" else "mysql-backup-mydumper.sh",
    )
    if not os.path.isfile(script_path):
        return False, f"备份脚本不存在: {script_path}", 500

    binlog_start_file = ""
    binlog_start_pos = 0
    binlog_end_file = ""
    binlog_end_pos = 0

    if backup_mode == "increment":
        if not full_backup_file_id:
            return False, "增量备份必须传 full_backup_file_id", 400
        full_record = _find_backup_file_by_id(
            account_id=account_id,
            backup_file_id=full_backup_file_id,
        )
        if not full_record:
            return False, "全量备份记录不存在", 404
        if (full_record.get("backup_type") or "").strip() != "full":
            return False, "full_backup_file_id 必须指向全量备份记录", 400

        latest_inc = _find_latest_increment_for_full_backup(
            account_id=account_id,
            full_backup_file_id=full_backup_file_id,
        )
        if latest_inc:
            binlog_start_file = (latest_inc.get("binlog_end_file") or "").strip()
            binlog_start_pos = _to_int(latest_inc.get("binlog_end_pos"), 0)
            if not binlog_start_file or binlog_start_pos <= 0:
                return False, "上一条增量备份缺少结束位点，无法继续增量", 400
        else:
            full_backup_dir = (full_record.get("backupDir") or "").strip()
            if not full_backup_dir:
                return False, "全量备份记录缺少 backupDir", 400
            resolved_full_dir, rerr = _resolve_existing_backup_dir(
                full_backup_dir,
                str(full_record.get("dirName") or ""),
            )
            if rerr or not resolved_full_dir:
                return False, rerr or "全量备份目录无效", 400
            binlog_start_file, binlog_start_pos, perr = _extract_source_log_point_from_metadata(
                resolved_full_dir,
            )
            if perr:
                return False, perr, 400

        binlog_end_file, binlog_end_pos, merr = _mysql_show_master_status(instance)
        if merr:
            return False, merr, 400
        assert binlog_end_file is not None
        assert binlog_end_pos is not None
        if binlog_end_pos <= 0:
            return False, "binlog 结束位点无效", 400
        if not binlog_start_file or binlog_start_pos <= 0:
            return False, "binlog 起始位点无效", 400

    try:
        pending = _insert_pending_backup_file_record(
            backup_dir_path=full_path,
            database=db_name,
            account_id=account_id,
            db_instance_id=instance_id,
            backup_type=backup_mode,
            full_backup_file_id=full_backup_file_id,
            job_id=job_id,
            binlog_start_file=binlog_start_file,
            binlog_start_pos=binlog_start_pos,
            binlog_end_file=binlog_end_file,
            binlog_end_pos=binlog_end_pos,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"写入备份清单失败: {exc}", 500
    if not pending:
        return False, "写入备份清单失败", 500

    if backup_mode == "increment":
        cmd = [
            "bash",
            script_path,
            "-H",
            str(instance.get("host") or ""),
            "-P",
            str(instance.get("port") or ""),
            "-u",
            str(instance.get("user") or ""),
            "-p",
            str(instance.get("password") or ""),
            "-d",
            db_name,
            "-b",
            backup_root,
            "--session-dir",
            full_path,
            "--start-log-file",
            binlog_start_file,
            "--start-log-pos",
            str(binlog_start_pos),
            "--end-log-file",
            binlog_end_file,
            "--end-log-pos",
            str(binlog_end_pos),
            "--full-backup-file-id",
            full_backup_file_id,
        ]
    else:
        cmd = [
            "bash",
            script_path,
            "-H",
            str(instance.get("host") or ""),
            "-P",
            str(instance.get("port") or ""),
            "-u",
            str(instance.get("user") or ""),
            "-p",
            str(instance.get("password") or ""),
            "-d",
            db_name,
            "-b",
            backup_root,
            "--session-dir",
            full_path,
        ]
        if tables:
            cmd.extend(["-t", tables])
        if ignore_tables:
            cmd.extend(["-i", ignore_tables])
        if clean_days is not None and str(clean_days).strip() != "":
            cmd.extend(["-c", str(clean_days)])
        if threads is not None and str(threads).strip() != "":
            cmd.extend(["--threads", str(threads)])
        if max_threads_per_table is not None and str(max_threads_per_table).strip() != "":
            cmd.extend(["--max-threads-per-table", str(max_threads_per_table)])
        if compress:
            cmd.extend(["--compress", compress])

    try:
        timeout_seconds = int(timeout_seconds)
        if timeout_seconds <= 0:
            _remove_backup_file_by_dir_name(session_dir_name)
            return False, "timeout_seconds 必须大于 0", 400
    except (TypeError, ValueError):
        _remove_backup_file_by_dir_name(session_dir_name)
        return False, "timeout_seconds 必须是数字", 400

    threading.Thread(
        target=_background_backup_job,
        kwargs={
            "cmd": cmd,
            "session_dir_name": session_dir_name,
            "full_path": full_path,
            "timeout_seconds": timeout_seconds,
        },
        daemon=False,
        name=f"backup-{session_dir_name}",
    ).start()

    return (
        True,
        {
            "dir_name": session_dir_name,
            "backup_dir": full_path,
            "database": db_name,
            "backup_type": backup_mode,
            "full_backup_file_id": full_backup_file_id,
            "job_id": job_id,
            "async": True,
            "pending": pending,
        },
        200,
    )


@app.route("/api/backup-jobs/<job_id>/run", methods=["POST"])
def run_backup_job(job_id: str):
    """启用任务并同步 crontab：仅当定时任务成功写入 crontab 后才更新 backup-jobs.json 中的运行状态。"""
    current_account_id = _get_current_account_id()
    with _file_lock:
        jobs = _load_jobs()
        target_index = next(
            (index for index, job in enumerate(jobs) if job.get("id") == job_id),
            -1,
        )
        if target_index < 0:
            return _error("记录不存在", http_status=404)

        if (jobs[target_index].get("account_id") or "").strip() != (current_account_id or "").strip():
            return _error("记录不存在", http_status=404)

        base = dict(jobs[target_index])
        updated = {
            **base,
            "enabled": True,
            "last_run_at": _now_str(),
        }

        ok, msg = _sync_job_crontab(updated)
        if not ok:
            return _error(
                f"定时任务未能同步到 crontab，未修改运行状态：{msg}",
                http_status=503,
            )

        schedule = (updated.get("schedule") or "").strip()
        if schedule and not _crontab_has_job_marker(job_id):
            return _error(
                "定时任务未出现在 crontab 中，未修改运行状态",
                http_status=503,
            )

        jobs[target_index] = updated
        _save_jobs(jobs)
        out = dict(updated)

    out2 = dict(out)
    out2["crontab_sync_ok"] = True
    out2["crontab_sync_msg"] = msg
    return _success(out2, "运行成功")


@app.route("/api/backup-jobs/<job_id>/stop", methods=["POST"])
def stop_backup_job(job_id: str):
    out: dict[str, Any] = {}
    current_account_id = _get_current_account_id()
    with _file_lock:
        jobs = _load_jobs()
        target_index = next(
            (index for index, job in enumerate(jobs) if job.get("id") == job_id),
            -1,
        )
        if target_index < 0:
            return _error("记录不存在", http_status=404)

        if (jobs[target_index].get("account_id") or "").strip() != (current_account_id or "").strip():
            return _error("记录不存在", http_status=404)

        jobs[target_index]["enabled"] = False
        _save_jobs(jobs)
        out = dict(jobs[target_index])

    ok, msg = _sync_job_crontab(out, remove_only=True)
    if not ok:
        out2 = dict(out)
        out2["crontab_sync_ok"] = False
        out2["crontab_sync_msg"] = msg
        return _success(out2, f"停止成功（但定时任务同步失败：{msg}）")

    out2 = dict(out)
    out2["crontab_sync_ok"] = True
    out2["crontab_sync_msg"] = msg
    return _success(out2, "停止成功")


@app.route("/api/backup-jobs", methods=["GET"])
def list_backup_jobs():
    current_account_id = _get_current_account_id()
    keyword = (request.args.get("keyword") or "").strip().lower()
    with _file_lock:
        jobs = _load_jobs()

    if current_account_id:
        jobs = [j for j in jobs if (j.get("account_id") or "").strip() == current_account_id]
    else:
        jobs = []

    if keyword:
        jobs = [
            job
            for job in jobs
            if keyword in str(job.get("name", "")).lower()
            or keyword in str(job.get("tables", "")).lower()
            or keyword in str(job.get("backup_type", "")).lower()
            or keyword in str(job.get("db_instance_id", "")).lower()
        ]

    return _success(jobs)


@app.route("/api/backup-jobs", methods=["POST"])
def create_backup_job():
    payload = request.get_json(silent=True) or {}
    data = _normalize_job_payload(payload)
    # 所有写入都强制归属当前账号
    data["account_id"] = _get_current_account_id()

    # 前端不展示 enabled 时，保留默认行为
    if "enabled" not in payload:
        data["enabled"] = True

    if not data["id"]:
        data["id"] = f"job_{int(time.time() * 1000)}"

    if not data["created_at"]:
        data["created_at"] = _now_str()

    data["last_run_at"] = data["last_run_at"] or ""

    ok, msg = _validate_job_payload(data, is_create=True)
    if not ok:
        return _error(msg)

    with _file_lock:
        jobs = _load_jobs()
        if any(x.get("id") == data["id"] for x in jobs):
            return _error("id 已存在，请更换 id")
        if data["backup_type"] == "incremental" and _linked_full_taken_by_other_incremental(
            jobs,
            data["linked_full_backup_job_id"],
            data["id"],
            data["account_id"],
        ):
            return _error("每个全量备份定时任务仅允许关联一个增量备份定时任务")
        jobs.append(data)
        _save_jobs(jobs)

    if data.get("enabled"):
        ok, msg = _sync_job_crontab(data)
        if not ok:
            out = dict(data)
            out["crontab_sync_ok"] = False
            out["crontab_sync_msg"] = msg
            return _success(out, f"新增成功（但定时任务同步失败：{msg}）")

    out = dict(data)
    out["crontab_sync_ok"] = True
    out["crontab_sync_msg"] = "已同步定时任务" if data.get("enabled") else "未启用，未同步"
    return _success(out, "新增成功")


@app.route("/api/backup-jobs/<job_id>", methods=["PUT"])
def update_backup_job(job_id: str):
    payload = request.get_json(silent=True) or {}
    data = _normalize_job_payload(payload)
    data["id"] = job_id
    # 所有写入都强制归属当前账号
    data["account_id"] = _get_current_account_id()

    ok, msg = _validate_job_payload(data, is_create=False)
    if not ok:
        return _error(msg)

    with _file_lock:
        jobs = _load_jobs()
        target_index = next(
            (index for index, job in enumerate(jobs) if job.get("id") == job_id),
            -1,
        )
        if target_index < 0:
            return _error("记录不存在", http_status=404)

        old = jobs[target_index]
        if (old.get("account_id") or "").strip() != (data.get("account_id") or "").strip():
            return _error("记录不存在", http_status=404)

        # 前端不展示 enabled 时，更新时保持旧值不变
        if "enabled" not in payload:
            data["enabled"] = old.get("enabled", True)
        if not data.get("created_at"):
            data["created_at"] = old.get("created_at") or _now_str()
        if data.get("last_run_at") is None or data.get("last_run_at") == "":
            data["last_run_at"] = old.get("last_run_at") or ""
        if "db_instance_id" not in payload:
            data["db_instance_id"] = (old.get("db_instance_id") or "").strip()

        if data["backup_type"] == "incremental" and _linked_full_taken_by_other_incremental(
            jobs,
            data["linked_full_backup_job_id"],
            job_id,
            data["account_id"],
        ):
            return _error("每个全量备份定时任务仅允许关联一个增量备份定时任务")

        jobs[target_index] = data
        _save_jobs(jobs)

    ok, msg = _sync_job_crontab(data, remove_only=not bool(data.get("enabled")))
    if not ok:
        out = dict(data)
        out["crontab_sync_ok"] = False
        out["crontab_sync_msg"] = msg
        return _success(out, f"编辑成功（但定时任务同步失败：{msg}）")

    out = dict(data)
    out["crontab_sync_ok"] = True
    out["crontab_sync_msg"] = msg
    return _success(out, "编辑成功")


def _delete_backup_job_core(job_id: str):
    """删除定时任务：更新 json、移除 crontab 与脚本文件。"""
    # 须在持 _file_lock 之前解析当前账号：_get_current_account_id 内部也会抢同一把锁，嵌套会死锁卡死整进程
    current_account_id = _get_current_account_id()
    old: Optional[dict] = None
    with _file_lock:
        jobs = _load_jobs()
        before = len(jobs)
        old = next((job for job in jobs if job.get("id") == job_id), None)
        if old and (old.get("account_id") or "").strip() != (current_account_id or "").strip():
            return _error("记录不存在", http_status=404)
        jobs = [job for job in jobs if job.get("id") != job_id]
        if len(jobs) == before:
            return _error("记录不存在", http_status=404)
        _save_jobs(jobs)

    _sync_job_crontab(old or {"id": job_id}, remove_only=True)
    try:
        script_path = os.path.join(JOBS_DIR, f"{job_id}.sh")
        if os.path.isfile(script_path):
            os.remove(script_path)
    except OSError:
        pass

    return _success({"id": job_id}, "删除成功")


@app.route("/api/backup-jobs/delete/<job_id>", methods=["POST", "GET"])
def delete_backup_job_post(job_id: str):
    """
    推荐：POST /api/backup-jobs/delete/<id>（避免部分代理对 DELETE 支持不佳）。
    GET 仅用于提示：在浏览器地址栏打开会得到 JSON 405，而非 Flask HTML 404。
    """
    if request.method == "GET":
        return _error(
            "删除定时任务请使用 POST（例如前端按钮或 curl -X POST）；不要在浏览器地址栏直接访问本 URL。",
            http_status=405,
        )
    return _delete_backup_job_core(job_id)


@app.route("/api/backup-jobs/<job_id>", methods=["DELETE"])
def delete_backup_job_legacy(job_id: str):
    """兼容：DELETE /api/backup-jobs/<id> 与旧客户端。"""
    return _delete_backup_job_core(job_id)


@app.route("/api/backup-jobs/<job_id>/execute", methods=["POST"])
def execute_backup_job(job_id: str):
    """
    供 cron 专属脚本触发：按 job 配置执行一次备份。
    """
    with _file_lock:
        jobs = _load_jobs()
        target = next((job for job in jobs if job.get("id") == job_id), None)
        if not target:
            return _error("记录不存在", http_status=404)
        instance_id = (target.get("db_instance_id") or "").strip()
        instances = _load_instances()
        instance = next((x for x in instances if (x.get("id") or "").strip() == instance_id), None)
    if not instance:
        return _error("数据库实例不存在", http_status=404)

    payload = {
        "tables": (target.get("tables") or "").strip(),
        "ignore_tables": (target.get("ignore_tables") or "").strip(),
        "clean_days": target.get("clean_days"),
        # 统一使用 mydumper 默认压缩（sql.zst），不再读取历史 enable_gzip
        "compress": "",
        "job_id": job_id,
    }
    job_backup_type = (target.get("backup_type") or "full").strip().lower()
    if job_backup_type == "incremental":
        full_job_id = (target.get("linked_full_backup_job_id") or "").strip()
        if not full_job_id:
            return _error("增量任务缺少 linked_full_backup_job_id", http_status=400)
        with _file_lock:
            files = [_normalize_backup_file(x) for x in _load_backup_files()]
        full_candidates = [
            x
            for x in files
            if (x.get("account_id") or "").strip() == (target.get("account_id") or "").strip()
            and (x.get("backup_type") or "").strip() == "full"
            and (x.get("job_id") or "").strip() == full_job_id
        ]
        if not full_candidates:
            return _error("未找到该全量任务生成的全量备份，无法执行增量备份", http_status=400)
        full_candidates.sort(key=lambda x: x.get("backupTime") or "", reverse=True)
        payload["backup_type"] = "increment"
        payload["full_backup_file_id"] = str(full_candidates[0].get("backup_file_id") or "")
    else:
        payload["backup_type"] = "full"

    ok, data_or_msg, status = _start_backup_for_instance(instance, payload)
    if not ok:
        return _error(str(data_or_msg), http_status=status)

    with _file_lock:
        jobs2 = _load_jobs()
        for i, job in enumerate(jobs2):
            if job.get("id") == job_id:
                jobs2[i]["last_run_at"] = _now_str()
                break
        _save_jobs(jobs2)

    out = dict(data_or_msg)
    out["job_id"] = job_id
    out["instance_id"] = instance_id
    return _success(out, "已提交备份任务，正在后台执行")


def _read_job_script_log(job_id: str) -> tuple[str, bool]:
    """读取定时任务脚本生成的 job 日志。

    兼容：
    - ${BACK_DIR}/jobs/logs/<job_id>.log（当前）
    - ${BACK_DIR}/job-logs/<job_id>.log（历史）
    - /app/backup/jobs/logs/<job_id>.log（路径迁移兼容）
    """
    jid = (job_id or "").strip()
    if not jid:
        return "(无日志)", False

    try:
        candidates = [
            JOB_SCRIPT_LOGS_DIR,
            JOB_LOGS_DIR,
            "/app/backup/jobs/logs",
            "/app/backup/job-logs",
        ]
        for cdir in candidates:
            rp_dir = os.path.realpath(cdir)
            rp_target = os.path.realpath(os.path.join(cdir, f"{jid}.log"))
            if not rp_target.startswith(rp_dir + os.sep):
                continue
            if not os.path.isfile(rp_target):
                continue
            with open(rp_target, "rb") as f:
                raw = f.read(_JOB_LOG_MAX_BYTES + 1)
            truncated = len(raw) > _JOB_LOG_MAX_BYTES
            chunk = raw[:_JOB_LOG_MAX_BYTES]
            text = chunk.decode("utf-8", errors="replace")
            if truncated:
                text += f"\n\n…（已截断，仅显示前 {_JOB_LOG_MAX_BYTES // 1024} KB）"
            return text, True
    except OSError:
        return "(无日志)", False
    return "(暂无日志)", False


@app.route("/api/backup-jobs/<job_id>/log", methods=["GET"])
def get_backup_job_log(job_id: str):
    """查看定时任务调度记录日志：back/jobs/logs/job_<id>.log"""
    current_account_id = _get_current_account_id()
    with _file_lock:
        jobs = _load_jobs()
        job = next((j for j in jobs if j.get("id") == job_id), None)
    if not job or (job.get("account_id") or "").strip() != (current_account_id or "").strip():
        return _error("记录不存在", http_status=404)

    content, present = _read_job_script_log(job_id)
    return _success(
        {
            "jobLog": content,
            "jobLogPresent": present,
        }
    )


def _normalize_payload(payload: dict) -> dict:
    return {
        "id": (payload.get("id") or "").strip(),
        "account_id": (payload.get("account_id") or "").strip(),
        "name": (payload.get("name") or "").strip(),
        "host": (payload.get("host") or "").strip(),
        "port": payload.get("port"),
        "user": (payload.get("user") or "").strip(),
        "password": payload.get("password") or "",
        "database": (payload.get("database") or "").strip(),
    }


def _validate_payload(data: dict, is_create: bool) -> tuple[bool, str]:
    required_fields = ["account_id", "name", "host", "port", "user", "password", "database"]
    if is_create:
        required_fields = ["id", *required_fields]

    for field in required_fields:
        value = data.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return False, f"参数缺失或为空: {field}"

    port = data.get("port")
    try:
        data["port"] = int(port)
        if data["port"] <= 0:
            return False, "port 必须大于 0"
    except (TypeError, ValueError):
        return False, "port 必须是数字"

    return True, ""


def _normalize_test_connection_payload(payload: dict) -> dict:
    return {
        "host": (payload.get("host") or "").strip(),
        "port": payload.get("port"),
        "user": (payload.get("user") or "").strip(),
        "password": payload.get("password") or "",
        "database": (payload.get("database") or "").strip(),
    }


def _validate_test_connection_payload(data: dict) -> tuple[bool, str]:
    if not data.get("host"):
        return False, "参数缺失或为空: host"
    if not data.get("user"):
        return False, "参数缺失或为空: user"
    if not data.get("database"):
        return False, "参数缺失或为空: database"
    port = data.get("port")
    try:
        data["port"] = int(port)
        if not (1 <= data["port"] <= 65535):
            return False, "port 必须在 1-65535 之间"
    except (TypeError, ValueError):
        return False, "port 必须是数字"
    return True, ""


def _test_mysql_connection(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    timeout: int = 15,
) -> tuple[bool, str]:
    """
    使用系统 mysql 客户端尝试连接并执行 SELECT 1。
    返回 (是否成功, 说明信息)。
    """
    if "`" in database or "\x00" in database:
        return False, "数据库名包含非法字符"
    # mysql 客户端可能在不同发行版中以 mysql 或 mariadb 提供，这里做兼容兜底
    client_bin = shutil.which('mysql') or shutil.which('mariadb')
    if not client_bin:
        return (
            False,
            "未找到 mysql/mariadb 客户端，请在服务器安装 MariaDB/MySQL 客户端或确认 PATH 可用",
        )
    env = os.environ.copy()
    env["MYSQL_PWD"] = password
    cmd = [
        client_bin,
        "-h",
        host,
        "-P",
        str(port),
        "-u",
        user,
        "-N",
        "-e",
        "SELECT 1",
        database,
    ]
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"连接超时（超过 {timeout} 秒）"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if len(err) > 500:
            err = err[:500] + "..."
        return False, err or "连接失败"
    return True, "连接成功"


@app.route("/api/db-instances", methods=["GET"])
def list_db_instances():
    current_account_id = _get_current_account_id()
    keyword = (request.args.get("keyword") or "").strip().lower()
    with _file_lock:
        items = _load_instances()

    if current_account_id:
        items = [x for x in items if (x.get("account_id") or "").strip() == current_account_id]
    else:
        items = []

    if keyword:
        items = [
            item
            for item in items
            if keyword in str(item.get("name", "")).lower()
            or keyword in str(item.get("host", "")).lower()
            or keyword in str(item.get("database", "")).lower()
        ]
    return _success(items)


@app.route("/api/db-instances", methods=["POST"])
def create_db_instance():
    payload = request.get_json(silent=True) or {}
    data = _normalize_payload(payload)
    if not data["id"]:
        data["id"] = f"db_{int(time.time() * 1000)}"
    # 所有写入都强制归属当前账号
    data["account_id"] = _get_current_account_id()

    ok, msg = _validate_payload(data, is_create=True)
    if not ok:
        return _error(msg)

    with _file_lock:
        items = _load_instances()
        if any(x.get("id") == data["id"] for x in items):
            return _error("id 已存在，请更换 id")
        items.append(data)
        _save_instances(items)

    return _success(data, "新增成功")


@app.route("/api/db-instances/test-connection", methods=["POST"])
def test_db_instance_connection():
    """使用 mysql 客户端验证 host/port/user/password 能否访问指定 database。"""
    payload = request.get_json(silent=True) or {}
    data = _normalize_test_connection_payload(payload)
    ok, msg = _validate_test_connection_payload(data)
    if not ok:
        return _error(msg)

    conn_ok, detail = _test_mysql_connection(
        data["host"],
        data["port"],
        data["user"],
        data["password"],
        data["database"],
    )
    if not conn_ok:
        return _error(detail, http_status=400)
    return _success({"ok": True}, detail)


@app.route("/api/db-instances/<instance_id>", methods=["PUT"])
def update_db_instance(instance_id: str):
    payload = request.get_json(silent=True) or {}
    data = _normalize_payload(payload)
    data["id"] = instance_id
    data["account_id"] = _get_current_account_id()

    ok, msg = _validate_payload(data, is_create=False)
    if not ok:
        return _error(msg)

    with _file_lock:
        items = _load_instances()
        target_index = next(
            (index for index, item in enumerate(items) if item.get("id") == instance_id),
            -1,
        )
        if target_index < 0:
            return _error("记录不存在", http_status=404)

        # 防止越权：仅允许更新自己的记录
        old = items[target_index]
        if (old.get("account_id") or "").strip() != data.get("account_id"):
            return _error("记录不存在", http_status=404)
        data["account_id"] = (old.get("account_id") or "").strip()
        items[target_index] = data
        _save_instances(items)

    return _success(data, "编辑成功")


@app.route("/api/db-instances/<instance_id>", methods=["DELETE"])
def delete_db_instance(instance_id: str):
    current_account_id = _get_current_account_id()
    with _file_lock:
        items = _load_instances()
        before = len(items)
        target = next((item for item in items if item.get("id") == instance_id), None)
        if not target:
            return _error("记录不存在", http_status=404)
        if (target.get("account_id") or "").strip() != current_account_id:
            return _error("记录不存在", http_status=404)
        items = [item for item in items if item.get("id") != instance_id]
        if len(items) == before:
            return _error("记录不存在", http_status=404)
        _save_instances(items)

    return _success({"id": instance_id}, "删除成功")


@app.route("/api/db-instances/<instance_id>/backup", methods=["POST"])
def run_db_instance_backup(instance_id: str):
    """
    基于实例配置提交 mydumper 备份任务（后台执行）。
    参数来源：db instance 的 host/port/user/password/database。
    流程：先计算会话目录并写入 backup-files.json（size=0），再启动后台线程调用脚本 --session-dir；
    接口立即返回；线程结束后更新 size 或删除预登记。
    请求体可选：max_threads_per_table（默认由脚本设为 1，缓解 mydumper file already open）。
    """
    with _file_lock:
        items = _load_instances()
        instance = next((x for x in items if x.get("id") == instance_id), None)

    if not instance:
        return _error("数据库实例不存在", http_status=404)
    current_account_id = _get_current_account_id()
    if (instance.get("account_id") or "").strip() != current_account_id:
        return _error("数据库实例不存在", http_status=404)

    payload = request.get_json(silent=True) or {}
    ok, data_or_msg, status = _start_backup_for_instance(instance, payload)
    if not ok:
        return _error(str(data_or_msg), http_status=status)
    out = dict(data_or_msg)
    out["instance_id"] = instance_id
    return _success(out, "已提交备份任务，正在后台执行")


@app.route("/api/db-instances/<instance_id>/restore", methods=["POST"])
def run_db_instance_restore(instance_id: str):
    """
    基于 backup-files 中的 dirName 解析备份目录，提交 mysql-restore-mydumper.sh 还原任务（后台执行）。
    请求体 JSON：
      - dir_name: 必填，与备份文件列表中的目录名一致
      - target_database: 可选，默认使用实例的 database
      - source_database: 可选，默认使用备份记录中的 database（与 mydumper 导出库名一致）
      - tables / ignore_tables: 可选，逗号分隔短表名
      - incremental_dir_name: 可选，指定回放到该增量目录（含）
      - threads / drop_table_mode / timeout_seconds
    """
    with _file_lock:
        items = _load_instances()
        instance = next((x for x in items if x.get("id") == instance_id), None)

    if not instance:
        return _error("数据库实例不存在", http_status=404)
    current_account_id = _get_current_account_id()
    if (instance.get("account_id") or "").strip() != current_account_id:
        return _error("数据库实例不存在", http_status=404)

    script_path = os.path.join(SCRIPT_DIR, "mysql-restore-mydumper.sh")
    if not os.path.isfile(script_path):
        return _error(f"还原脚本不存在: {script_path}", http_status=500)

    payload = request.get_json(silent=True) or {}
    dir_name = (payload.get("dir_name") or payload.get("dirName") or "").strip()
    if not dir_name:
        return _error("dir_name 不能为空")

    target, resolved, err = _get_backup_record_and_resolved_dir(dir_name)
    if err:
        status = 400
        if err == "记录不存在":
            status = 404
        elif "不在允许" in err:
            status = 403
        return _error(err, http_status=status)
    assert target is not None and resolved is not None

    # 防止越权还原：仅允许还原自己账号下的备份记录
    if (target.get("account_id") or "").strip() != (instance.get("account_id") or "").strip():
        return _error("备份记录不存在", http_status=404)
    if (target.get("backup_type") or "full").strip() != "full":
        return _error("请先选择一条全量备份作为还原基线", http_status=400)

    target_db = (payload.get("target_database") or instance.get("database") or "").strip()
    if not target_db:
        return _error("目标数据库名不能为空（请填写实例 database 或传 target_database）")

    source_db = (payload.get("source_database") or target.get("database") or "").strip()
    tables = (payload.get("tables") or "").strip()
    ignore_tables = (payload.get("ignore_tables") or "").strip()
    incremental_dir_name = (
        payload.get("incremental_dir_name") or payload.get("incrementalDirName") or ""
    ).strip()
    threads = payload.get("threads")
    drop_table_mode = (payload.get("drop_table_mode") or "DROP").strip()
    timeout_seconds = payload.get("timeout_seconds", 7200)
    apply_incrementals = bool(payload.get("apply_incrementals", True))
    if incremental_dir_name:
        apply_incrementals = True

    cmd = [
        "bash",
        script_path,
        "-H",
        str(instance.get("host") or ""),
        "-P",
        str(instance.get("port") or ""),
        "-u",
        str(instance.get("user") or ""),
        "-p",
        str(instance.get("password") or ""),
        "-d",
        target_db,
        "-s",
        resolved,
    ]
    if source_db:
        cmd.extend(["--source-db", source_db])
    if tables:
        cmd.extend(["-t", tables])
    if ignore_tables:
        cmd.extend(["-i", ignore_tables])
    if threads is not None and str(threads).strip() != "":
        cmd.extend(["--threads", str(threads)])
    if drop_table_mode:
        cmd.extend(["--drop-table", drop_table_mode])

    post_cmds: list[list[str]] = []
    applied_increment_dirs: list[str] = []
    applied_increment_count = 0
    if apply_incrementals:
        apply_script = os.path.join(SCRIPT_DIR, "mysql-apply-binlog-increment.sh")
        if not os.path.isfile(apply_script):
            return _error(f"增量回放脚本不存在: {apply_script}", http_status=500)
        full_backup_file_id = (target.get("backup_file_id") or "").strip()
        with _file_lock:
            all_backup_files = [_normalize_backup_file(x) for x in _load_backup_files()]
        increments = [
            x
            for x in all_backup_files
            if (x.get("account_id") or "").strip() == (target.get("account_id") or "").strip()
            and (x.get("backup_type") or "").strip() == "increment"
            and (x.get("full_backup_file_id") or "").strip() == full_backup_file_id
        ]
        increments.sort(key=lambda x: x.get("backupTime") or "")
        if incremental_dir_name:
            selected_inc = next(
                (x for x in increments if (x.get("dirName") or "").strip() == incremental_dir_name),
                None,
            )
            if not selected_inc:
                return _error("所选增量备份文件不存在或不属于当前全量基线", http_status=400)
            selected_name = (selected_inc.get("dirName") or "").strip()
            sliced: list[dict] = []
            for inc in increments:
                sliced.append(inc)
                if (inc.get("dirName") or "").strip() == selected_name:
                    break
            increments = sliced
        for inc in increments:
            inc_dir = (inc.get("backupDir") or "").strip()
            inc_name = (inc.get("dirName") or "").strip()
            if not inc_dir:
                continue
            resolved_inc, ierr = _resolve_existing_backup_dir(inc_dir, inc_name)
            if ierr or not resolved_inc:
                return _error(f"增量目录不可用: {inc_name}", http_status=400)
            applied_increment_dirs.append(inc_name)
            post_cmds.append(
                [
                    "bash",
                    apply_script,
                    "-H",
                    str(instance.get("host") or ""),
                    "-P",
                    str(instance.get("port") or ""),
                    "-u",
                    str(instance.get("user") or ""),
                    "-p",
                    str(instance.get("password") or ""),
                    "-d",
                    target_db,
                    "-s",
                    resolved_inc,
                ],
            )
        applied_increment_count = len(post_cmds)

    try:
        timeout_seconds = int(timeout_seconds)
        if timeout_seconds <= 0:
            return _error("timeout_seconds 必须大于 0")
    except (TypeError, ValueError):
        return _error("timeout_seconds 必须是数字")

    threading.Thread(
        target=_background_restore_job,
        kwargs={
            "cmd": cmd,
            "timeout_seconds": timeout_seconds,
            "post_cmds": post_cmds,
        },
        daemon=True,
        name=f"restore-{dir_name}",
    ).start()

    return _success(
        {
            "instance_id": instance_id,
            "dir_name": dir_name,
            "source_dir": resolved,
            "target_database": target_db,
            "source_database": source_db,
            "apply_incrementals": apply_incrementals,
            "applied_increment_count": applied_increment_count,
            "applied_increment_dirs": applied_increment_dirs,
            "incremental_dir_name": incremental_dir_name,
            "async": True,
        },
        "已提交还原任务，正在后台执行",
    )


@app.route("/api/backup-files", methods=["GET"])
def list_backup_files():
    current_account_id = _get_current_account_id()
    keyword = (request.args.get("keyword") or "").strip().lower()
    with _file_lock:
        items = [_normalize_backup_file(x) for x in _load_backup_files()]

    if current_account_id:
        items = [x for x in items if (x.get("account_id") or "").strip() == current_account_id]
    else:
        items = []

    if keyword:
        items = [
            x
            for x in items
            if keyword in str(x.get("database", "")).lower()
            or keyword in str(x.get("dirName", "")).lower()
            or keyword in str(x.get("backupDir", "")).lower()
        ]
    items.sort(key=lambda x: x.get("backupTime") or "", reverse=True)
    return _success(items)


@app.route("/api/backup-files/<path:dir_name>", methods=["DELETE"])
def delete_backup_file(dir_name: str):
    dir_name = unquote(dir_name).strip()
    if not dir_name:
        return _error("dirName 无效")

    with _file_lock:
        items = _load_backup_files()
        raw_target = next(
            (x for x in items if (x.get("dirName") or "").strip() == dir_name),
            None,
        )
    if not raw_target:
        return _error("记录不存在", http_status=404)

    target = _normalize_backup_file(raw_target)
    current_account_id = _get_current_account_id()
    if (target.get("account_id") or "").strip() != (current_account_id or "").strip():
        return _error("记录不存在", http_status=404)

    backup_dir = (target.get("backupDir") or "").strip()

    removed_disk = False
    resolved: Optional[str] = None
    if backup_dir:
        resolved, _rerr = _resolve_existing_backup_dir(backup_dir, dir_name)
        if resolved:
            if not _is_backup_dir_safe_to_download(resolved):
                return _error("备份目录不在允许删除的路径下", http_status=403)
            try:
                shutil.rmtree(resolved)
                removed_disk = True
            except OSError as exc:
                return _error(f"删除备份目录失败: {exc}", http_status=500)

    with _file_lock:
        items = _load_backup_files()
        before = len(items)
        items = [
            x
            for x in items
            if (x.get("dirName") or "").strip() != dir_name
        ]
        if len(items) == before:
            return _error("记录不存在", http_status=404)
        _save_backup_files(items)

    if removed_disk:
        msg = "已删除备份记录及磁盘上的备份目录"
    else:
        msg = "已删除备份记录（磁盘上未找到对应目录，未删除文件）"

    out: dict[str, Any] = {
        "dirName": dir_name,
        "removed_disk": removed_disk,
    }
    if resolved:
        out["resolved_path"] = resolved
    return _success(out, msg)


@app.route("/api/backup-files/<path:dir_name>/tables", methods=["GET"])
def list_backup_file_tables(dir_name: str):
    """根据 mydumper metadata 列出该备份包含的表与视图；无完整 metadata 时从 data/*.sql.zst 文件名推断。"""
    target, resolved, err = _get_backup_record_and_resolved_dir(dir_name)
    if err:
        status = 400
        if err == "记录不存在":
            status = 404
        elif "不在允许" in err:
            status = 403
        return _error(err, http_status=status)
    assert target is not None and resolved is not None
    current_account_id = _get_current_account_id()
    if (target.get("account_id") or "").strip() != (current_account_id or "").strip():
        return _error("备份记录不存在", http_status=404)

    items: list[dict[str, Any]] = []
    items_source = "metadata"
    metadata_path = _mydumper_metadata_path(resolved)
    if metadata_path:
        items = _parse_mydumper_metadata_tables(metadata_path)
    if not items:
        items = _infer_mydumper_objects_from_data_files(resolved)
        if items:
            items_source = "filenames"

    if not items:
        return _error(
            "未能解析备份中的表对象：无有效 metadata 内容，且 data/ 下未找到符合 mydumper 命名的 .sql.zst 文件",
            http_status=404,
        )

    tables = [x for x in items if x.get("kind") == "table"]
    views = [x for x in items if x.get("kind") == "view"]
    return _success(
        {
            "dirName": target.get("dirName"),
            "database": target.get("database"),
            "backupDir": target.get("backupDir"),
            "items": items,
            "tables": tables,
            "views": views,
            "table_count": len(tables),
            "view_count": len(views),
            "itemsSource": items_source,
        },
    )


# 单次响应日志正文上限，避免极大文件拖垮接口
_BACKUP_LOG_MAX_BYTES = 800_000


def _read_session_log_text(session_dir: str, log_basename: str) -> tuple[str, bool]:
    """
    仅允许读取会话根目录下的固定文件名。返回 (正文, 文件是否存在)。
    不存在时正文为空；存在但读取失败时正文为简短错误说明。
    """
    if log_basename not in ("backup.log", "restore.log"):
        return "", False
    try:
        rp_session = os.path.realpath(session_dir)
    except OSError:
        return "", False
    path = os.path.join(rp_session, log_basename)
    try:
        rp_file = os.path.realpath(path)
    except OSError:
        return "", False
    try:
        if os.path.commonpath([rp_session, rp_file]) != rp_session:
            return "", False
    except ValueError:
        return "", False
    if not os.path.isfile(rp_file):
        return "", False
    try:
        with open(rp_file, "rb") as f:
            raw = f.read(_BACKUP_LOG_MAX_BYTES + 1)
    except OSError:
        return "(读取日志失败)", True
    truncated = len(raw) > _BACKUP_LOG_MAX_BYTES
    chunk = raw[:_BACKUP_LOG_MAX_BYTES]
    text = chunk.decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n…（已截断，仅显示前 {_BACKUP_LOG_MAX_BYTES // 1024} KB）"
    return text, True


@app.route("/api/backup-files/<path:dir_name>/logs", methods=["GET"])
def get_backup_file_logs(dir_name: str):
    """读取备份会话目录下的 backup.log 与 restore.log。"""
    target, resolved, err = _get_backup_record_and_resolved_dir(dir_name)
    if err:
        status = 400
        if err == "记录不存在":
            status = 404
        elif "不在允许" in err:
            status = 403
        return _error(err, http_status=status)
    assert target is not None and resolved is not None
    current_account_id = _get_current_account_id()
    if (target.get("account_id") or "").strip() != (current_account_id or "").strip():
        return _error("备份记录不存在", http_status=404)

    backup_text, backup_ok = _read_session_log_text(resolved, "backup.log")
    restore_text, restore_ok = _read_session_log_text(resolved, "restore.log")

    return _success(
        {
            "dirName": target.get("dirName"),
            "database": target.get("database"),
            "backupDir": target.get("backupDir"),
            "backupLog": backup_text,
            "restoreLog": restore_text,
            "backupLogPresent": backup_ok,
            "restoreLogPresent": restore_ok,
        },
    )


@app.route("/api/backup-files/<path:dir_name>/download", methods=["GET"])
def download_backup_file(dir_name: str):
    """下载该条记录 backupDir 对应目录的打包文件（tar.gz）。"""
    _target, resolved, err = _get_backup_record_and_resolved_dir(dir_name)
    if err:
        status = 400
        if err == "记录不存在":
            status = 404
        elif "不在允许" in err:
            status = 403
        elif err == "dirName 无效":
            status = 400
        msg = err
        if err in (
            "备份目录不在允许访问的路径下",
            "备份目录不存在或不在允许访问的路径下",
        ):
            msg = "备份目录不存在或不在允许下载的路径下（可配置环境变量 BACKUP_DOWNLOAD_ROOTS）"
        return _error(msg, http_status=status)
    assert _target is not None
    assert resolved is not None
    current_account_id = _get_current_account_id()
    if (_target.get("account_id") or "").strip() != (current_account_id or "").strip():
        return _error("备份记录不存在", http_status=404)

    fd, tmp_path = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            tar.add(
                resolved,
                arcname=os.path.basename(resolved.rstrip(os.sep)),
                recursive=True,
            )
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return _error(f"打包失败: {exc}", http_status=500)

    safe_name = secure_filename(str(_target.get("dirName") or dir_name)) or "backup"
    download_name = f"{safe_name}_backup.tar.gz"

    @after_this_request
    def _remove_tmp(response: Response) -> Response:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return response

    return send_file(
        tmp_path,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/gzip",
        max_age=0,
    )


if __name__ == "__main__":
    # 默认 8081，和你当前后端端口习惯一致，可按需改
    # 可选：启动时自动把当前 backup-jobs.json 的 enabled/ schedule 同步到系统 crontab，
    # 避免容器重启/路径迁移后 crontab 仍指向旧脚本路径。
    if os.environ.get("AUTO_SYNC_CRONTAB", "1") == "1":
        try:
            jobs = _load_jobs()
            for j in jobs:
                _sync_job_crontab(j)
        except Exception:  # noqa: BLE001
            pass
    for _startup_dir in (
        JSON_DIR,
        _DEFAULT_BACKUP_ROOT,
        JOBS_DIR,
        JOB_SCRIPT_LOGS_DIR,
    ):
        try:
            os.makedirs(_startup_dir, mode=0o755, exist_ok=True)
        except OSError:
            pass
    # 生产镜像默认关闭 debug/reloader：reloader 多进程下后台备份线程行为易异常
    _flask_debug = os.environ.get("FLASK_DEBUG", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("FLASK_RUN_PORT") or "8081"),
        debug=_flask_debug,
        use_reloader=_flask_debug,
    )

