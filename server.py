#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
医学知识智能问答 · 本地服务端（问答5.0 版）
================================================
纯 Python 标准库实现，零第三方依赖。

能力：
  1. 用户注册 / 登录 / 登出（PBKDF2 加盐哈希 + Token 会话，落 SQLite）
  2. 多会话管理：新建 / 列表 / 切换 / 删除，消息与知识来源全部持久化
  3. 代理转发 Dify 应用（对话流 chatflow / 工作流 workflow）
     —— API 密钥只保存在服务端，永远不会下发到浏览器
  4. **知识文件上传**：转发到 Dify /files/upload，拿到 upload_file_id 后
     随本次提问塞进 files[] → 触发工作流的「知识文件入库」分支
  5. 健康探测（读取 Dify /info，拿到应用名与模式）
  6. 在线读写 Dify 配置（config.json），密钥以掩码回显
  7. **知识库管理**：列表 / 编辑（名称、描述、检索参数）/ 删除 / 禁用启用
     （整库批量启停文档）、文档启停与删除、分段内容编辑与删除
  8. 托管前端静态页面，浏览器访问 http://本机IP:端口 即可使用

启动：
    python server.py
"""

import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import hashlib
import hmac
import secrets
import traceback
import uuid
import concurrent.futures
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, quote, parse_qs
import urllib.request
import urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get("MQ50_DB") or os.path.join(DATA_DIR, "app.db")
PID_PATH = os.path.join(DATA_DIR, "server.pid")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "_说明": "问答5.0 前端配置：改完这里直接重启 server.py 即可生效，也可以在网页的「接口设置」里在线修改。",
    "server": {"host": "0.0.0.0", "port": 8001},
    "dify": {
        "base_url": "http://127.0.0.1/v1",
        "api_key": "app-xxxxxxxxxxxxxxxxxxxxxxxx",
        "dataset_api_key": "dataset-xxxxxxxxxxxxxxxxxxxxxxxx",
        "app_type": "chatflow",
        "input_var": "query",
        "extra_inputs": {},
        "stream": True,
        "timeout": 900,
    },
    "session": {"ttl_hours": 72},
}

# 允许上传的知识文件后缀（与 Dify 应用「问答5.0」的文件上传配置一致）
UPLOAD_EXTS = (
    ".txt", ".md", ".markdown", ".pdf", ".html", ".htm", ".xlsx", ".xls",
    ".docx", ".doc", ".csv", ".eml", ".msg", ".pptx", ".ppt", ".xml",
    ".epub", ".json",
)
MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 30MB，Dify 侧限制 15MB，这里放宽兜底
DEFAULT_INGEST_QUERY = "请处理我上传的医学知识文件"

CONFIG_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def _deep_merge(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _deep_merge(cfg, json.load(f))
        except Exception as e:
            print("[!] config.json 解析失败，使用默认配置：%s" % e)
    return cfg


CONFIG = load_config()


def save_config():
    """把当前 CONFIG 写回 config.json（保持可读缩进与中文）。"""
    with CONFIG_LOCK:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


def mask_key(key):
    key = str(key or "")
    if not key:
        return ""
    if len(key) <= 14:
        return key[:4] + "****"
    return key[:8] + "…" + key[-4:]


def _key_is_real(key, prefix):
    """判断密钥是否像"真的"。占位符（例：app-xxxx、'在这里填 app- 开头的应用密钥'）一律视为未配置。

    这样首次启动时页面会明确显示「未配置密钥」，
    而不是拿占位符去请求 Dify、再抛一个看不懂的 401。
    """
    key = str(key or "").strip()
    if not key.startswith(prefix):
        return False
    rest = key[len(prefix):]
    if not re.match(r"^[A-Za-z0-9_\-]{8,}$", rest):
        return False
    if len(set(rest)) <= 2:      # 全是同一个字符（xxxxxx / 111111）→ 占位符
        return False
    return True


def dify_ready():
    d = CONFIG.get("dify") or {}
    base = (d.get("base_url") or "").strip()
    return bool(base) and _key_is_real(d.get("api_key"), "app-")


def dataset_ready():
    d = CONFIG.get("dify") or {}
    base = (d.get("base_url") or "").strip()
    return bool(base) and _key_is_real(d.get("dataset_api_key"), "dataset-")


# --------------------------------------------------------------------------
# Dify Service API 通用调用（知识库管理用）
# --------------------------------------------------------------------------
class DifyApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def dify_api(method, path, body=None, timeout=60, key_kind="dataset"):
    """调用 Dify Service API，返回 (status, obj)；网络异常时直接抛 DifyApiError。"""
    d = CONFIG.get("dify") or {}
    base = (d.get("base_url") or "").rstrip("/")
    key = d.get("dataset_api_key") if key_kind == "dataset" else d.get("api_key")
    if not base or not key:
        raise DifyApiError(0, "服务端尚未配置 Dify 地址或知识库密钥")
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer %s" % key, "Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8", "ignore")
        return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "ignore")
        except Exception:
            pass
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                obj = {"message": raw[:400]}
        except Exception:
            obj = {"message": (raw or str(e.reason))[:400]}
        return e.code, obj
    except Exception as e:
        raise DifyApiError(0, "无法连接 Dify（%s）：%s" % (base, e))


def dify_ok(method, path, body=None, timeout=60, key_kind="dataset"):
    status, obj = dify_api(method, path, body, timeout, key_kind)
    if status >= 400 or status == 0:
        err = obj.get("error")
        msg = obj.get("message") or err or ("HTTP %s" % status)
        if isinstance(err, dict) and err.get("message"):
            msg = err["message"]
        raise DifyApiError(status, str(msg))
    return obj


# Dify 在文档「正在索引」时会拒绝状态变更（"is being indexed, please try again later"）。
# 启用文档会触发重新索引，所以连着点两次很容易撞上；这里自动退避重试。
INDEXING_RETRY_HINT = ("is being indexed", "being indexed", "indexing, please")


def dify_ok_retry(method, path, body=None, timeout=60, tries=5, delay=2.5):
    last = None
    for i in range(tries):
        try:
            return dify_ok(method, path, body, timeout)
        except DifyApiError as e:
            last = e
            low = (e.message or "").lower()
            if e.status in (400, 409) and any(h in low for h in INDEXING_RETRY_HINT):
                time.sleep(delay)
                continue
            raise
    raise last


# ---- 知识库「启用 / 禁用」状态缓存（避免列表页逐库请求过慢）----
_KB_STAT_TTL = 15.0
_kb_stat_cache = {}
_kb_stat_lock = threading.Lock()


def kb_stat(ds_id):
    """返回 {'available':n, 'disabled':n, 'archived':n}；取不到时为 None。"""
    now = time.time()
    with _kb_stat_lock:
        hit = _kb_stat_cache.get(ds_id)
        if hit and now - hit[0] < _KB_STAT_TTL:
            return hit[1]
    out = {}
    for st in ("available", "disabled"):
        try:
            obj = dify_ok("GET",
                          "/datasets/%s/documents?page=1&limit=1&status=%s" % (ds_id, st),
                          timeout=20)
            out[st] = obj.get("total")
        except DifyApiError:
            out[st] = None
    with _kb_stat_lock:
        _kb_stat_cache[ds_id] = (now, out)
    return out


def kb_stat_invalidate(ds_id=None):
    with _kb_stat_lock:
        if ds_id is None:
            _kb_stat_cache.clear()
        else:
            _kb_stat_cache.pop(ds_id, None)


def norm_retrieval(rm):
    rm = rm or {}
    rerank = rm.get("reranking_model") or {}
    return {
        "searchMethod": rm.get("search_method") or "semantic_search",
        "rerankingEnable": bool(rm.get("reranking_enable")),
        "rerankingMode": rm.get("reranking_mode") or "",
        "rerankingProvider": rerank.get("reranking_provider_name") or "",
        "rerankingModel": rerank.get("reranking_model_name") or "",
        "topK": rm.get("top_k"),
        "scoreThresholdEnabled": bool(rm.get("score_threshold_enabled")),
        "scoreThreshold": rm.get("score_threshold"),
    }


def norm_dataset(it, stats=None):
    it = it or {}
    doc_count = it.get("document_count") or 0
    avail = (stats or {}).get("available")
    dis = (stats or {}).get("disabled")
    if stats:
        if doc_count == 0:
            state = "empty"
        elif avail is None and dis is None:
            state = "unknown"
        elif avail == 0 and (dis or 0) > 0:
            state = "disabled"
        elif (dis or 0) > 0:
            state = "partial"
        else:
            state = "enabled"
    else:
        state = "unknown"
    return {
        "id": it.get("id"),
        "name": it.get("name") or "",
        "description": it.get("description") or "",
        "documentCount": doc_count,
        "wordCount": it.get("word_count") or 0,
        "appCount": it.get("app_count") or 0,
        "indexingTechnique": it.get("indexing_technique") or "",
        "embeddingModel": it.get("embedding_model") or "",
        "embeddingProvider": it.get("embedding_model_provider") or "",
        "embeddingAvailable": it.get("embedding_available"),
        "docForm": it.get("doc_form") or "",
        "permission": it.get("permission") or "",
        "authorName": it.get("author_name") or "",
        "createdAt": it.get("created_at"),
        "updatedAt": it.get("updated_at"),
        "retrieval": norm_retrieval(it.get("retrieval_model_dict") or {}),
        "tags": [t.get("name") for t in (it.get("tags") or []) if isinstance(t, dict)],
        "state": state,
        "enabledDocuments": avail,
        "disabledDocuments": dis,
        "archivedDocuments": (stats or {}).get("archived"),
    }


def norm_document(d):
    d = d or {}
    return {
        "id": d.get("id"),
        "name": d.get("name") or "",
        "position": d.get("position"),
        "enabled": bool(d.get("enabled")),
        "archived": bool(d.get("archived")),
        "displayStatus": d.get("display_status") or "",
        "indexingStatus": d.get("indexing_status") or "",
        "wordCount": d.get("word_count") or 0,
        "tokens": d.get("tokens") or 0,
        "hitCount": d.get("hit_count") or 0,
        "docForm": d.get("doc_form") or "",
        "createdAt": d.get("created_at"),
        "error": d.get("error"),
    }


def norm_segment(s):
    s = s or {}
    return {
        "id": s.get("id"),
        "position": s.get("position"),
        "content": s.get("content") or "",
        "answer": s.get("answer"),
        "keywords": s.get("keywords") or [],
        "wordCount": s.get("word_count") or 0,
        "tokens": s.get("tokens") or 0,
        "hitCount": s.get("hit_count") or 0,
        "enabled": bool(s.get("enabled")),
        "status": s.get("status") or "",
        "error": s.get("error"),
    }


# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_db()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                created_at    REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id                   TEXT PRIMARY KEY,
                user_id              INTEGER NOT NULL,
                title                TEXT NOT NULL,
                dify_conversation_id TEXT,
                created_at           REAL NOT NULL,
                updated_at           REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                user_id         INTEGER NOT NULL,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                meta            TEXT,
                sources         TEXT,
                is_error        INTEGER DEFAULT 0,
                error_title     TEXT,
                created_at      REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cv_user ON conversations(user_id, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_cv ON messages(conversation_id, id)")
    conn.close()


# --------------------------------------------------------------------------
# 密码 / 会话
# --------------------------------------------------------------------------
def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200000)
    return dk.hex(), salt


def verify_password(password, password_hash, salt):
    calc, _ = hash_password(password, salt)
    return hmac.compare_digest(calc, password_hash)


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    now = time.time()
    ttl = float((CONFIG.get("session") or {}).get("ttl_hours", 72)) * 3600
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, now, now + ttl),
        )
    conn.close()
    return token


def user_from_token(token):
    if not token:
        return None
    conn = get_db()
    row = conn.execute(
        """
        SELECT u.id, u.username, s.expires_at
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token = ?
        """,
        (token,),
    ).fetchone()
    result = None
    if row and row["expires_at"] > time.time():
        result = {"id": row["id"], "username": row["username"]}
    elif row:
        with conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.close()
    return result


def delete_session(token):
    if not token:
        return
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.close()


# --------------------------------------------------------------------------
# Dify 输出归一化
# --------------------------------------------------------------------------
PREFERRED_KEYS = ("answer", "text", "result", "output", "content", "reply", "response")


def pick_answer(outputs):
    if not isinstance(outputs, dict):
        return str(outputs or "").strip()
    for k in PREFERRED_KEYS:
        v = outputs.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    parts = [v.strip() for v in outputs.values() if isinstance(v, str) and v.strip()]
    if parts:
        return "\n\n".join(parts)
    return json.dumps(outputs, ensure_ascii=False)


def normalize_sources(raw):
    """把 Dify 的 retriever_resources 精简成前端需要的字段。"""
    out = []
    if not isinstance(raw, list):
        return out
    for r in raw:
        if not isinstance(r, dict):
            continue
        out.append({
            "name": r.get("document_name") or r.get("title") or "知识片段",
            "dataset": r.get("dataset_name") or "",
            "score": r.get("score") if isinstance(r.get("score"), (int, float)) else None,
            "position": r.get("position"),
            "wordCount": r.get("word_count"),
            "content": (r.get("content") or r.get("segment") or "").strip(),
        })
    return out


# --------------------------------------------------------------------------
# HTTP 处理
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "MedicalQA/2.0"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---------------- 基础工具 ----------------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def bearer_token(self):
        auth = self.headers.get("Authorization") or ""
        return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    def require_user(self):
        user = user_from_token(self.bearer_token())
        if not user:
            self.send_json({"ok": False, "error": "未登录或登录已过期"}, 401)
            return None
        return user

    # ---------------- 路由 ----------------
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/kb"):
                return self.handle_kb("GET", path)

            if path == "/api/me":
                user = self.require_user()
                return None if not user else self.send_json({"ok": True, "user": user})

            if path == "/api/health":
                return self.handle_health()

            if path == "/api/settings":
                if not self.require_user():
                    return
                return self.handle_get_settings()

            if path == "/api/conversations":
                user = self.require_user()
                return None if not user else self.handle_list_conversations(user)

            m = re.match(r"^/api/conversations/([A-Za-z0-9_\-]+)/messages$", path)
            if m:
                user = self.require_user()
                return None if not user else self.handle_list_messages(user, m.group(1))

            return self.serve_static(path)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        """支持 HEAD：部分浏览器 / 安全软件会用探测请求判断资源是否更新。"""
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/"):
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self.serve_static(path)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._safe_error()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/kb"):
                return self.handle_kb("POST", path)

            if path == "/api/register":
                return self.handle_register()
            if path == "/api/login":
                return self.handle_login()
            if path == "/api/logout":
                return self.handle_logout()
            if path == "/api/chat":
                return self.handle_chat()
            if path == "/api/upload":
                return self.handle_upload()
            if path == "/api/settings":
                if not self.require_user():
                    return
                return self.handle_save_settings()
            if path == "/api/conversations":
                user = self.require_user()
                return None if not user else self.handle_create_conversation(user)
            self.send_json({"ok": False, "error": "接口不存在"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._safe_error()

    def do_PATCH(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/kb"):
                return self.handle_kb("PATCH", path)
            self.send_json({"ok": False, "error": "接口不存在"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._safe_error()

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/kb"):
                return self.handle_kb("DELETE", path)

            m = re.match(r"^/api/conversations/([A-Za-z0-9_\-]+)$", path)
            if m:
                user = self.require_user()
                return None if not user else self.handle_delete_conversation(user, m.group(1))
            self.send_json({"ok": False, "error": "接口不存在"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._safe_error()

    def _safe_error(self):
        try:
            self.send_json({"ok": False, "error": "服务器内部错误"}, 500)
        except Exception:
            pass

    # ---------------- 静态文件 ----------------
    MIME = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".ico": "image/x-icon",
        ".woff2": "font/woff2",
    }

    def serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = unquote(path).lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR):
            return self.send_json({"ok": False, "error": "非法路径"}, 403)
        if not os.path.isfile(full):
            return self.send_json({"ok": False, "error": "文件不存在"}, 404)

        ext = os.path.splitext(full)[1].lower()

        # ---- 缓存校验器：ETag 基于「自身 mtime + 关联资源 mtime」 ----
        # 这样改了 style.css / app.js，index.html 的 ETag 也会变，
        # 浏览器必定重新拉取，不会再出现「两个地址各缓存一个版本」的情况。
        def mtime(p):
            try:
                return int(os.stat(p).st_mtime)
            except OSError:
                return 0

        if ext in (".html", ".htm"):
            stamp = "%x" % mtime(full)
            for asset in ("style.css", "app.js", "kb.js"):
                stamp += "-%x" % mtime(os.path.join(STATIC_DIR, asset))
        else:
            stamp = "%x-%x" % (mtime(full), os.path.getsize(full))
        etag = '"%s"' % stamp
        last_mod = self.date_time_string(mtime(full))

        inm = (self.headers.get("If-None-Match") or "").strip()
        ims = (self.headers.get("If-Modified-Since") or "").strip()
        if inm == etag or (not inm and ims == last_mod):
            self.send_response(304)
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", last_mod)
            self.end_headers()
            return

        with open(full, "rb") as f:
            body = f.read()

        # ---- HTML 里给 css/js 自动打版本号，彻底绕开旧缓存 ----
        if ext in (".html", ".htm"):
            body = self._bust_assets(body)

        self.send_response(200)
        self.send_header("Content-Type", self.MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", last_mod)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _bust_assets(self, body):
        """把 HTML 中的 /style.css、/app.js 改写成带 mtime 版本号的 URL。"""
        try:
            text = body.decode("utf-8")
        except Exception:
            return body

        def repl(m):
            lead, url, _old, tail = m.group(1), m.group(2), m.group(3), m.group(4)
            target = os.path.normpath(os.path.join(STATIC_DIR, url.lstrip("/")))
            if not (target.startswith(STATIC_DIR) and os.path.isfile(target)):
                return m.group(0)
            try:
                v = "%x" % int(os.stat(target).st_mtime)
            except OSError:
                return m.group(0)
            return "%s%s?v=%s%s" % (lead, url, v, tail)

        text = re.sub(r'((?:href|src)=")(/[^"?]+\.(?:css|js))(\?v=[^"]*)?(")', repl, text)
        return text.encode("utf-8")

    # ---------------- 账号 ----------------
    USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,20}$")

    def handle_register(self):
        data = self.read_json()
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        if not self.USERNAME_RE.match(username):
            return self.send_json({"ok": False, "error": "用户名需为 2-20 位中文、字母、数字或下划线"}, 400)
        if len(password) < 6:
            return self.send_json({"ok": False, "error": "密码至少 6 位"}, 400)

        pwd_hash, salt = hash_password(password)
        conn = get_db()
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?,?,?,?)",
                    (username, pwd_hash, salt, time.time()),
                )
                user_id = cur.lastrowid
        except sqlite3.IntegrityError:
            conn.close()
            return self.send_json({"ok": False, "error": "该用户名已被注册"}, 409)
        conn.close()

        token = create_session(user_id)
        self.send_json({"ok": True, "token": token, "user": {"id": user_id, "username": username}})

    def handle_login(self):
        data = self.read_json()
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        conn = get_db()
        row = conn.execute(
            "SELECT id, username, password_hash, salt FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()
        if not row or not verify_password(password, row["password_hash"], row["salt"]):
            return self.send_json({"ok": False, "error": "用户名或密码错误"}, 401)
        token = create_session(row["id"])
        self.send_json({"ok": True, "token": token, "user": {"id": row["id"], "username": row["username"]}})

    def handle_logout(self):
        delete_session(self.bearer_token())
        self.send_json({"ok": True})

    # ---------------- 会话 ----------------
    def handle_list_conversations(self, user):
        conn = get_db()
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS cnt
            FROM conversations c WHERE c.user_id = ?
            ORDER BY c.updated_at DESC
            """,
            (user["id"],),
        ).fetchall()
        conn.close()
        self.send_json({
            "ok": True,
            "conversations": [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "createdAt": r["created_at"],
                    "updatedAt": r["updated_at"],
                    "count": r["cnt"],
                }
                for r in rows
            ],
        })

    def handle_create_conversation(self, user):
        data = self.read_json()
        title = (data.get("title") or "新对话").strip()[:40] or "新对话"
        cid = uuid.uuid4().hex
        now = time.time()
        conn = get_db()
        with conn:
            conn.execute(
                "INSERT INTO conversations (id, user_id, title, dify_conversation_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (cid, user["id"], title, "", now, now),
            )
        conn.close()
        self.send_json({"ok": True, "conversation": {"id": cid, "title": title, "count": 0}})

    def handle_delete_conversation(self, user, cid):
        conn = get_db()
        row = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?", (cid, user["id"])
        ).fetchone()
        if not row:
            conn.close()
            return self.send_json({"ok": False, "error": "会话不存在"}, 404)
        with conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
        conn.close()
        self.send_json({"ok": True})

    def handle_list_messages(self, user, cid):
        conn = get_db()
        cv = conn.execute(
            "SELECT id, title, dify_conversation_id FROM conversations WHERE id = ? AND user_id = ?",
            (cid, user["id"]),
        ).fetchone()
        if not cv:
            conn.close()
            return self.send_json({"ok": False, "error": "会话不存在"}, 404)
        rows = conn.execute(
            "SELECT role, content, meta, sources, is_error, error_title, created_at"
            " FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (cid,),
        ).fetchall()
        conn.close()
        msgs = []
        for r in rows:
            msgs.append({
                "role": r["role"],
                "content": r["content"],
                "meta": json.loads(r["meta"]) if r["meta"] else None,
                "sources": json.loads(r["sources"]) if r["sources"] else [],
                "error": bool(r["is_error"]),
                "errorTitle": r["error_title"] or "",
                "createdAt": r["created_at"],
            })
        self.send_json({
            "ok": True,
            "title": cv["title"],
            "difyConversationId": cv["dify_conversation_id"] or "",
            "messages": msgs,
        })

    # ---------------- 知识库管理（转发 Dify Service API） ----------------
    KB_ROUTES = (
        ("list", re.compile(r"^/api/kb$")),
        ("toggle", re.compile(r"^/api/kb/([A-Za-z0-9_\-]+)/toggle$")),
        ("docs", re.compile(r"^/api/kb/([A-Za-z0-9_\-]+)/documents$")),
        ("segs", re.compile(r"^/api/kb/([A-Za-z0-9_\-]+)/documents/([A-Za-z0-9_\-]+)/segments$")),
        ("seg", re.compile(r"^/api/kb/([A-Za-z0-9_\-]+)/documents/([A-Za-z0-9_\-]+)/segments/([A-Za-z0-9_\-]+)$")),
        ("doc", re.compile(r"^/api/kb/([A-Za-z0-9_\-]+)/documents/([A-Za-z0-9_\-]+)$")),
        ("detail", re.compile(r"^/api/kb/([A-Za-z0-9_\-]+)$")),
    )

    def qs(self):
        raw = parse_qs(urlparse(self.path).query)
        return {k: (v[0] if v else "") for k, v in raw.items()}

    @staticmethod
    def to_int(v, default):
        try:
            return int(str(v).strip())
        except Exception:
            return default

    def kb_match(self, path):
        for name, rx in self.KB_ROUTES:
            m = rx.match(path)
            if m:
                return name, m.groups()
        return None

    def handle_kb(self, method, path):
        """知识库管理的统一入口；已处理则返回（调用方直接 return）。"""
        user = self.require_user()
        if not user:
            return
        if not dataset_ready():
            return self.send_json(
                {"ok": False, "error": "服务端尚未配置知识库 API 密钥（dataset-…），请在「接口设置」中填写"}, 500
            )
        hit = self.kb_match(path)
        if not hit:
            return self.send_json({"ok": False, "error": "接口不存在"}, 404)
        name, g = hit
        try:
            if name == "list" and method == "GET":
                return self.kb_list()
            if name == "detail":
                return self.kb_detail(g[0], method)
            if name == "toggle" and method == "POST":
                return self.kb_toggle(g[0])
            if name == "docs" and method == "GET":
                return self.kb_docs(g[0])
            if name == "doc":
                return self.kb_doc(g[0], g[1], method)
            if name == "segs" and method == "GET":
                return self.kb_segments(g[0], g[1])
            if name == "seg":
                return self.kb_segment(g[0], g[1], g[2], method)
            return self.send_json({"ok": False, "error": "方法不支持：%s %s" % (method, path)}, 405)
        except DifyApiError as e:
            code = 502 if e.status == 0 else (e.status if 400 <= e.status < 600 else 500)
            print("[kb] %s %s -> %s %s" % (method, path, e.status, e.message))
            return self.send_json({"ok": False, "error": e.message, "difyStatus": e.status}, code)
        except Exception as e:
            traceback.print_exc()
            return self.send_json({"ok": False, "error": "服务器内部错误：%s" % e}, 500)

    def kb_list(self):
        q = self.qs()
        page = max(1, self.to_int(q.get("page"), 1))
        limit = min(100, max(1, self.to_int(q.get("limit"), 20)))
        keyword = (q.get("keyword") or "").strip()
        path = "/datasets?page=%d&limit=%d" % (page, limit)
        if keyword:
            path += "&keyword=" + quote(keyword)
        obj = dify_ok("GET", path, timeout=40)
        items = obj.get("data") or []
        ids = [it.get("id") for it in items if it.get("id")]
        stats = {}
        if ids:
            # 每个库两次小查询（只取 total），并发跑，结果缓存 15 秒
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(ids))) as ex:
                for i, s in zip(ids, ex.map(kb_stat, ids)):
                    stats[i] = s
        self.send_json({
            "ok": True,
            "page": page,
            "limit": limit,
            "total": obj.get("total"),
            "hasMore": bool(obj.get("has_more")),
            "datasets": [norm_dataset(it, stats.get(it.get("id"))) for it in items],
        })

    def kb_detail(self, ds_id, method):
        if method == "GET":
            obj = dify_ok("GET", "/datasets/" + ds_id)
            return self.send_json({"ok": True, "dataset": norm_dataset(obj, kb_stat(ds_id))})
        if method == "PATCH":
            return self.kb_update(ds_id)
        if method == "DELETE":
            dify_ok("DELETE", "/datasets/" + ds_id)
            kb_stat_invalidate(ds_id)
            print("[kb] 已删除知识库 %s" % ds_id)
            return self.send_json({"ok": True})
        return self.send_json({"ok": False, "error": "方法不支持"}, 405)

    def kb_update(self, ds_id):
        body = self.read_json()
        payload = {}
        if "name" in body:
            name = str(body.get("name") or "").strip()
            if not name:
                return self.send_json({"ok": False, "error": "知识库名称不能为空"}, 400)
            payload["name"] = name[:40]
        if "description" in body:
            payload["description"] = str(body.get("description") or "").strip()[:400]

        rt = body.get("retrieval")
        if isinstance(rt, dict) and rt:
            cur = dify_ok("GET", "/datasets/" + ds_id)
            rm = dict(cur.get("retrieval_model_dict") or {})
            if rt.get("topK") is not None:
                rm["top_k"] = max(1, min(20, int(rt["topK"])))
            if rt.get("scoreThresholdEnabled") is not None:
                rm["score_threshold_enabled"] = bool(rt["scoreThresholdEnabled"])
            if rt.get("scoreThreshold") is not None:
                rm["score_threshold"] = round(max(0.0, min(1.0, float(rt["scoreThreshold"]))), 4)
            if rt.get("rerankingEnable") is not None:
                rm["reranking_enable"] = bool(rt["rerankingEnable"])
            rm.setdefault("search_method", "semantic_search")
            rm.setdefault("top_k", 3)
            rm.setdefault("score_threshold_enabled", False)
            rm.setdefault("score_threshold", 0.0)
            rm.setdefault("reranking_enable", False)
            # 注意：关闭 rerank 时**不要**把 reranking_model 清掉，
            # 否则用户再次打开就得重新选模型（Dify 在 enable=false 时会忽略它）。
            if rm.get("reranking_enable") and not rm.get("reranking_mode"):
                rm["reranking_mode"] = "reranking_model"
            payload["retrieval_model"] = {
                "search_method": rm.get("search_method"),
                "reranking_enable": rm.get("reranking_enable"),
                "reranking_model": rm.get("reranking_model"),
                "reranking_mode": rm.get("reranking_mode"),
                "top_k": rm.get("top_k"),
                "score_threshold_enabled": rm.get("score_threshold_enabled"),
                "score_threshold": rm.get("score_threshold"),
                "weights": rm.get("weights"),
            }

        if not payload:
            return self.send_json({"ok": False, "error": "没有需要修改的字段"}, 400)
        obj = dify_ok("PATCH", "/datasets/" + ds_id, payload, timeout=60)
        kb_stat_invalidate(ds_id)
        print("[kb] 已更新知识库 %s 字段=%s" % (ds_id, list(payload.keys())))
        self.send_json({"ok": True, "dataset": norm_dataset(obj, kb_stat(ds_id))})

    def kb_collect_docs(self, ds_id, want_enabled):
        """收集「当前启用状态 == want_enabled」且未归档的文档 id（分页扫全量）。"""
        out, page = [], 1
        while page <= 100:
            obj = dify_ok("GET", "/datasets/%s/documents?page=%d&limit=100" % (ds_id, page), timeout=40)
            items = obj.get("data") or []
            for d in items:
                if bool(d.get("enabled")) == want_enabled and not d.get("archived"):
                    out.append(d.get("id"))
            if not obj.get("has_more") or not items:
                break
            page += 1
        return out

    def kb_batch_status(self, ds_id, action, doc_ids):
        if not doc_ids:
            return 0
        total = 0
        for i in range(0, len(doc_ids), 200):
            chunk = doc_ids[i:i + 200]
            dify_ok_retry("PATCH", "/datasets/%s/documents/status/%s" % (ds_id, action),
                          {"document_ids": chunk}, timeout=180)
            total += len(chunk)
        return total

    def kb_toggle(self, ds_id):
        """整库启用 / 禁用：批量改该库所有文档的 enabled。"""
        body = self.read_json()
        enabled = bool(body.get("enabled"))
        action = "enable" if enabled else "disable"
        to_change = self.kb_collect_docs(ds_id, not enabled)
        n = self.kb_batch_status(ds_id, action, to_change)
        kb_stat_invalidate(ds_id)
        # 已启用状态下又点「启用」这类空操作，也让前端拿到明确回执
        self.send_json({"ok": True, "action": action, "affected": n, "enabled": enabled})

    def kb_docs(self, ds_id):
        q = self.qs()
        page = max(1, self.to_int(q.get("page"), 1))
        limit = min(100, max(1, self.to_int(q.get("limit"), 20)))
        status = (q.get("status") or "").strip()
        keyword = (q.get("keyword") or "").strip()
        path = "/datasets/%s/documents?page=%d&limit=%d" % (ds_id, page, limit)
        if status:
            path += "&status=" + quote(status)
        if keyword:
            path += "&keyword=" + quote(keyword)
        obj = dify_ok("GET", path, timeout=40)
        self.send_json({
            "ok": True,
            "page": page,
            "limit": limit,
            "total": obj.get("total"),
            "hasMore": bool(obj.get("has_more")),
            "documents": [norm_document(d) for d in (obj.get("data") or [])],
        })

    def kb_doc(self, ds_id, doc_id, method):
        if method == "PATCH":
            body = self.read_json()
            if "enabled" in body:
                action = "enable" if bool(body.get("enabled")) else "disable"
            elif "archived" in body:
                action = "archive" if bool(body.get("archived")) else "un_archive"
            else:
                return self.send_json({"ok": False, "error": "需要 enabled 或 archived 字段"}, 400)
            dify_ok_retry("PATCH", "/datasets/%s/documents/status/%s" % (ds_id, action),
                          {"document_ids": [doc_id]}, timeout=60)
            kb_stat_invalidate(ds_id)
            return self.send_json({"ok": True, "action": action})
        if method == "DELETE":
            dify_ok("DELETE", "/datasets/%s/documents/%s" % (ds_id, doc_id))
            kb_stat_invalidate(ds_id)
            print("[kb] 已删除文档 %s / %s" % (ds_id, doc_id))
            return self.send_json({"ok": True})
        return self.send_json({"ok": False, "error": "方法不支持"}, 405)

    def kb_segments(self, ds_id, doc_id):
        q = self.qs()
        page = max(1, self.to_int(q.get("page"), 1))
        limit = min(100, max(1, self.to_int(q.get("limit"), 10)))
        keyword = (q.get("keyword") or "").strip()
        path = "/datasets/%s/documents/%s/segments?page=%d&limit=%d" % (ds_id, doc_id, page, limit)
        if keyword:
            path += "&keyword=" + quote(keyword)
        obj = dify_ok("GET", path, timeout=40)
        self.send_json({
            "ok": True,
            "page": page,
            "limit": limit,
            "total": obj.get("total"),
            "hasMore": bool(obj.get("has_more")),
            "segments": [norm_segment(s) for s in (obj.get("data") or [])],
        })

    def kb_segment(self, ds_id, doc_id, seg_id, method):
        base = "/datasets/%s/documents/%s/segments/%s" % (ds_id, doc_id, seg_id)
        if method == "DELETE":
            dify_ok_retry("DELETE", base)
            print("[kb] 已删除分段 %s" % seg_id)
            return self.send_json({"ok": True})
        if method == "POST":
            body = self.read_json()
            seg = {}
            if "content" in body:
                content = str(body.get("content") or "")
                if not content.strip():
                    return self.send_json({"ok": False, "error": "分段内容不能为空"}, 400)
                seg["content"] = content
            if isinstance(body.get("keywords"), list):
                seg["keywords"] = [str(k) for k in body["keywords"]]
            elif isinstance(body.get("keywords"), str):
                seg["keywords"] = [k for k in re.split(r"[,，\s]+", body["keywords"]) if k]
            if "enabled" in body:
                seg["enabled"] = bool(body.get("enabled"))
            if not seg:
                return self.send_json({"ok": False, "error": "没有需要修改的字段"}, 400)

            # Dify 的 update_segment（dataset_service.py:3635）只在「内容未变」时才应用 keywords：
            #   content = args.content or segment.content
            #   if segment.content == content:  → 才处理 keywords
            # 一旦同时传了新内容，就会走重建索引分支、把 keywords 重算掉。
            # 所以这里**拆成两次提交**：先改内容，再单独设关键词。
            if "content" in seg and "keywords" in seg:
                first = {k: v for k, v in seg.items() if k != "keywords"}
                dify_ok_retry("POST", base, {"segment": first}, timeout=180)
                obj = dify_ok_retry("POST", base, {"segment": {"keywords": seg["keywords"]}}, timeout=180)
            else:
                obj = dify_ok_retry("POST", base, {"segment": seg}, timeout=180)
            return self.send_json({"ok": True, "segment": norm_segment(obj.get("data") or {})})
        return self.send_json({"ok": False, "error": "方法不支持"}, 405)

    # ---------------- 健康 / 设置 ----------------
    def probe_dify(self, base, key, timeout=6):
        url = base.rstrip("/") + "/info"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % key}, method="GET")
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            data = json.loads(r.read().decode("utf-8", "ignore"))
            return {"name": data.get("name") or "应用", "mode": data.get("mode") or ""}
        except urllib.error.HTTPError as e:
            return {"error": "HTTP %s（密钥或地址不正确）" % e.code}
        except Exception as e:
            return {"error": "无法连接：%s" % e}

    def handle_health(self):
        d = CONFIG.get("dify") or {}
        if not dify_ready():
            return self.send_json({
                "ok": False, "configured": False,
                "error": "服务端尚未配置 Dify 地址或密钥，请在「接口设置」中填写",
                "baseUrl": d.get("base_url") or "", "appType": d.get("app_type") or "",
            })
        info = self.probe_dify(d.get("base_url"), d.get("api_key"))
        ok = "error" not in info
        self.send_json({
            "ok": ok,
            "configured": True,
            "name": info.get("name", ""),
            "mode": info.get("mode", ""),
            "error": info.get("error", ""),
            "baseUrl": d.get("base_url"),
            "appType": d.get("app_type"),
        })

    def handle_get_settings(self):
        d = CONFIG.get("dify") or {}
        # 只在密钥"确实像真的"时才回显掩码；占位符一律显示为空 → 页面显示「未设置」
        app_ok = dify_ready()
        ds_ok = dataset_ready()
        self.send_json({
            "ok": True,
            "baseUrl": d.get("base_url") or "",
            "appType": d.get("app_type") or "chatflow",
            "inputVar": d.get("input_var") or "query",
            "extraInputs": d.get("extra_inputs") or {},
            "stream": bool(d.get("stream", True)),
            "timeout": d.get("timeout", 300),
            "apiKeyMasked": mask_key(d.get("api_key")) if app_ok else "",
            "hasKey": app_ok,
            "datasetIdMasked": mask_key(d.get("dataset_api_key")) if ds_ok else "",
            "hasDatasetKey": ds_ok,
            "hint": "本页所有 Dify 请求都由本机服务端转发，密钥不会下发到浏览器。",
        })

    def handle_save_settings(self):
        data = self.read_json()
        d = CONFIG.setdefault("dify", {})

        base = (data.get("baseUrl") or "").strip()
        if base:
            if not re.match(r"^https?://", base, re.I):
                base = "http://" + base
            d["base_url"] = base.rstrip("/")

        key = (data.get("apiKey") or "").strip()
        if key:
            d["api_key"] = key

        ds_key = (data.get("datasetApiKey") or "").strip()
        if ds_key:
            d["dataset_api_key"] = ds_key

        app_type = (data.get("appType") or "").strip().lower()
        if app_type in ("chatflow", "workflow"):
            d["app_type"] = app_type

        iv = (data.get("inputVar") or "").strip()
        if iv:
            d["input_var"] = iv

        if "extraInputs" in data:
            ei = data.get("extraInputs")
            if isinstance(ei, dict):
                d["extra_inputs"] = ei
            elif isinstance(ei, str):
                try:
                    parsed = json.loads(ei or "{}")
                    if isinstance(parsed, dict):
                        d["extra_inputs"] = parsed
                except Exception:
                    return self.send_json({"ok": False, "error": "extraInputs 不是合法 JSON"}, 400)

        if "stream" in data:
            d["stream"] = bool(data["stream"])
        if data.get("timeout"):
            try:
                d["timeout"] = max(10, min(1200, int(data["timeout"])))
            except Exception:
                pass

        save_config()
        info = self.probe_dify(d.get("base_url"), d.get("api_key"))
        self.send_json({
            "ok": True,
            "saved": True,
            "healthy": "error" not in info,
            "name": info.get("name", ""),
            "message": info.get("error", ""),
            "apiKeyMasked": mask_key(d.get("api_key")),
        })

    # ---------------- 知识文件上传（转发 Dify /files/upload） ----------------
    def parse_multipart(self):
        """极简 multipart/form-data 解析（不依赖 cgi / email 模块）。

        返回 [(name, filename, content_type, bytes), ...]
        """
        ctype = self.headers.get("Content-Type") or ""
        m = re.search(r'boundary="?([^";,]+)"?', ctype)
        if not m:
            return []
        boundary = m.group(1).encode("utf-8")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return []
        raw = self.rfile.read(length)
        out = []
        for part in raw.split(b"--" + boundary):
            if not part or part.strip() in (b"", b"--"):
                continue
            part = part.lstrip(b"\r\n")
            if part.startswith(b"--"):
                continue
            idx = part.find(b"\r\n\r\n")
            if idx < 0:
                continue
            head = part[:idx].decode("utf-8", "ignore")
            body = part[idx + 4:]
            if body.endswith(b"\r\n"):
                body = body[:-2]
            name = filename = ""
            ctype_part = "application/octet-stream"
            mm = re.search(r'name="([^"]*)"', head)
            if mm:
                name = mm.group(1)
            mf = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]*)"?', head, re.I)
            if mf:
                try:
                    filename = unquote(mf.group(1))
                except Exception:
                    filename = mf.group(1)
            mc = re.search(r"(?mi)^Content-Type:\s*(.+?)\s*$", head)
            if mc:
                ctype_part = mc.group(1)
            out.append((name, filename, ctype_part, body))
        return out

    def handle_upload(self):
        user = self.require_user()
        if not user:
            return
        if not dify_ready():
            return self.send_json(
                {"ok": False, "error": "服务端尚未配置 Dify 地址或密钥，请在「接口设置」中填写"}, 500
            )

        parts = self.parse_multipart()
        target = None
        for name, filename, ctype, body in parts:
            if name == "file" and filename and body:
                target = (filename, ctype, body)
                break
        if target is None:
            return self.send_json({"ok": False, "error": "没有收到文件"}, 400)

        filename, ctype_, content = target
        ext = os.path.splitext(filename)[1].lower()
        if ext and ext not in UPLOAD_EXTS:
            return self.send_json(
                {"ok": False, "error": "不支持的文件类型 %s（支持 txt/md/pdf/docx/xlsx/pptx 等）" % ext}, 400
            )
        if len(content) > MAX_UPLOAD_BYTES:
            return self.send_json({"ok": False, "error": "文件超过 %dMB 限制" % (MAX_UPLOAD_BYTES // 1024 // 1024)}, 413)

        d = CONFIG["dify"]
        url = (d.get("base_url") or "").rstrip("/") + "/files/upload"
        boundary = "----MedQA50" + secrets.token_hex(12)
        buf = bytearray()

        def add_field(fname, fval):
            buf.extend(("--%s\r\n" % boundary).encode("utf-8"))
            buf.extend(('Content-Disposition: form-data; name="%s"\r\n\r\n' % fname).encode("utf-8"))
            buf.extend(str(fval).encode("utf-8"))
            buf.extend(b"\r\n")

        # 文件名可能含中文：同时给 filename 与 filename*（RFC 2231），让 werkzeug 取到正确名字
        ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "")
        buf.extend(("--%s\r\n" % boundary).encode("utf-8"))
        buf.extend(
            ('Content-Disposition: form-data; name="file"; filename="%s"; filename*=UTF-8\'\'%s\r\n'
             % (ascii_name, quote(filename, safe=""))).encode("utf-8")
        )
        buf.extend(("Content-Type: %s\r\n\r\n" % (ctype_ or "application/octet-stream")).encode("utf-8"))
        buf.extend(content)
        buf.extend(b"\r\n")
        add_field("user", "web-%s" % user["id"])
        buf.extend(("--%s--\r\n" % boundary).encode("utf-8"))

        req = urllib.request.Request(
            url,
            data=bytes(buf),
            headers={
                "Authorization": "Bearer %s" % (d.get("api_key") or ""),
                "Content-Type": "multipart/form-data; boundary=%s" % boundary,
                "Content-Length": str(len(buf)),
            },
            method="POST",
        )
        try:
            r = urllib.request.urlopen(req, timeout=180)
            info = json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            return self.send_json(
                {"ok": False, "error": "Dify 上传失败 %s：%s" % (e.code, detail or e.reason)}, 502
            )
        except Exception as e:
            return self.send_json({"ok": False, "error": "无法连接 Dify：%s" % e}, 502)

        print("[upload] %s (%d bytes) -> %s" % (filename, len(content), info.get("id")))
        self.send_json({
            "ok": True,
            "file": {
                "id": info.get("id"),
                "name": info.get("name") or filename,
                "size": info.get("size") or len(content),
                "extension": info.get("extension") or ext.lstrip("."),
            },
        })

    # ---------------- 对话（代理 Dify） ----------------
    def handle_chat(self):
        user = self.require_user()
        if not user:
            return

        data = self.read_json()
        query = (data.get("message") or "").strip()
        cid = (data.get("conversationId") or "").strip()

        # ---- 本次携带的知识文件（已通过 /api/upload 拿到 Dify upload_file_id）----
        files_in = data.get("files")
        attach = []
        if isinstance(files_in, list):
            for f in files_in:
                if not isinstance(f, dict):
                    continue
                fid = (f.get("id") or f.get("uploadFileId") or "").strip()
                if fid:
                    attach.append({"id": fid, "name": (f.get("name") or "").strip() or "知识文件"})
        ingest = bool(attach)
        if not query and ingest:
            query = DEFAULT_INGEST_QUERY
        if not query:
            return self.send_json({"ok": False, "error": "消息不能为空"}, 400)

        conn = get_db()
        cv = conn.execute(
            "SELECT id, title, dify_conversation_id FROM conversations WHERE id = ? AND user_id = ?",
            (cid, user["id"]),
        ).fetchone()
        if not cv:
            conn.close()
            return self.send_json({"ok": False, "error": "会话不存在，请刷新页面"}, 404)

        if not dify_ready():
            conn.close()
            return self.send_json(
                {"ok": False, "error": "服务端尚未配置 Dify 地址或密钥，请在「接口设置」中填写"}, 500
            )

        d = CONFIG["dify"]
        app_type = (d.get("app_type") or "chatflow").lower()
        base = (d.get("base_url") or "").rstrip("/")
        api_key = d.get("api_key") or ""
        stream_mode = bool(d.get("stream", True))
        dify_user = "web-%s" % user["id"]
        dify_conv = cv["dify_conversation_id"] or ""

        if app_type == "workflow":
            url = base + "/workflows/run"
            inputs = dict(d.get("extra_inputs") or {})
            inputs[d.get("input_var") or "query"] = query
            payload = {"inputs": inputs, "response_mode": "streaming" if stream_mode else "blocking",
                       "user": dify_user}
        else:
            url = base + "/chat-messages"
            payload = {
                "inputs": dict(d.get("extra_inputs") or {}),
                "query": query,
                "response_mode": "streaming" if stream_mode else "blocking",
                "conversation_id": dify_conv,
                "user": dify_user,
                "auto_generate_name": False,
            }
            if attach:
                # 对话流附件 → Dify 会把它注入 sys.files，触发工作流的「知识文件入库」分支
                payload["files"] = [
                    {"type": "document", "transfer_method": "local_file", "upload_file_id": a["id"]}
                    for a in attach
                ]

        # 首轮提问用问题前 18 字作为会话标题（纯传文件时用文件名）
        first_turn = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE conversation_id = ?", (cid,)
        ).fetchone()["c"] == 0
        title = cv["title"]
        if first_turn:
            hint = (data.get("titleHint") or "").strip()
            if hint:
                title = hint[:40]
            elif ingest and query == DEFAULT_INGEST_QUERY:
                title = ("📎 " + attach[0]["name"])[:40]
            else:
                title = (query[:18] + "…") if len(query) > 18 else query
        now = time.time()
        user_meta = {"files": attach} if attach else None
        with conn:
            conn.execute(
                "INSERT INTO messages (conversation_id, user_id, role, content, meta, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (cid, user["id"], "user", query,
                 json.dumps(user_meta, ensure_ascii=False) if user_meta else None, now),
            )
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, now, cid)
            )
        conn.close()

        # 开启 SSE 下行
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        self.close_connection = True

        self.sse("title", {"title": title})

        state = {"text": [], "conv": dify_conv, "meta": {}}
        if ingest:
            # 本次是「知识文件入库」分支：没有检索来源是正常的，前端据此换一个标记
            state["meta"]["ingest"] = True
            state["meta"]["ingestFiles"] = [a["name"] for a in attach]
        self.proxy_dify(url, api_key, payload, state, app_type, stream_mode)

        # 对话流靠增量拼接；纯工作流可能不发增量，只在 workflow_finished 里给完整 outputs
        answer = "".join(state["text"]).strip() or (state.get("final") or "").strip()
        conn = get_db()
        if answer:
            with conn:
                conn.execute(
                    "INSERT INTO messages (conversation_id, user_id, role, content, meta, sources, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (cid, user["id"], "assistant", answer,
                     json.dumps(state["meta"], ensure_ascii=False),
                     json.dumps(state["meta"].get("sources") or [], ensure_ascii=False),
                     time.time()),
                )
                conn.execute(
                    "UPDATE conversations SET dify_conversation_id = ?, updated_at = ? WHERE id = ?",
                    (state["conv"] or "", time.time(), cid),
                )
        else:
            with conn:
                conn.execute(
                    "UPDATE messages SET is_error = 1, error_title = ? WHERE id ="
                    " (SELECT MAX(id) FROM messages WHERE conversation_id = ?)",
                    ("未返回内容", cid),
                )
        conn.close()

    def sse(self, event, obj):
        self.wfile.write(
            ("event: %s\ndata: %s\n\n" % (event, json.dumps(obj, ensure_ascii=False))).encode("utf-8")
        )
        self.wfile.flush()

    def proxy_dify(self, url, api_key, payload, state, app_type, stream_mode):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer %s" % api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream_mode else "application/json",
            },
            method="POST",
        )
        timeout = float((CONFIG.get("dify") or {}).get("timeout", 300))

        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:400]
            except Exception:
                pass
            self.sse("error", {"message": "Dify 返回错误 %s：%s" % (e.code, detail or e.reason)})
            return
        except Exception as e:
            self.sse("error", {"message": "无法连接 Dify（%s）：%s" % (url, e)})
            return

        content_type = (resp.headers.get("Content-Type") or "").lower()

        try:
            # ------- 非流式（blocking） -------
            if not stream_mode or "event-stream" not in content_type:
                raw = resp.read().decode("utf-8", "ignore")
                try:
                    obj = json.loads(raw)
                except Exception:
                    self.sse("error", {"message": "Dify 返回了非预期内容（Content-Type: %s）：%s"
                                                % (content_type or "无", raw.strip()[:300])})
                    return
                if "data" in obj and isinstance(obj["data"], dict) and obj.get("event"):
                    data = obj["data"]
                else:
                    data = obj
                answer = data.get("answer") or pick_answer(data.get("outputs") or {})
                if answer:
                    state["text"].append(answer)
                    self.sse("final", {"text": answer})
                if data.get("conversation_id"):
                    state["conv"] = data["conversation_id"]
                self.apply_meta(state, data.get("metadata") or data)
                self.sse("meta", state["meta"])
                self.sse("end", {"conversationId": state["conv"]})
                return

            # ------- 流式（SSE） -------
            for raw in resp:
                line = raw.decode("utf-8", "ignore").rstrip("\r\n")
                if not line or line.startswith(":") or line.startswith("event:"):
                    continue
                payload_line = line[5:].strip() if line.startswith("data:") else line.strip()
                if not payload_line or payload_line == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload_line)
                except Exception:
                    continue
                if obj.get("conversation_id"):
                    state["conv"] = obj["conversation_id"]
                self.handle_dify_event(obj, state)
        except (BrokenPipeError, ConnectionResetError):
            raise
        except Exception as e:
            try:
                self.sse("error", {"message": "读取 Dify 响应失败：%s" % e})
            except Exception:
                pass
        finally:
            try:
                resp.close()
            except Exception:
                pass

        self.sse("meta", state["meta"])
        self.sse("end", {"conversationId": state["conv"]})

    def apply_meta(self, state, container):
        """从 message_end.metadata / blocking 响应里提取耗时、tokens、知识来源。"""
        if not isinstance(container, dict):
            return
        usage = container.get("usage") if isinstance(container.get("usage"), dict) else {}
        if not usage:
            usage = container
        meta = state["meta"]
        if usage.get("total_tokens"):
            meta["tokens"] = usage["total_tokens"]
        if usage.get("latency"):
            meta["latencyMs"] = int(float(usage["latency"]) * 1000)
        if usage.get("time_to_first_token"):
            meta["firstTokenMs"] = int(float(usage["time_to_first_token"]) * 1000)
        if usage.get("completion_tokens"):
            meta["completionTokens"] = usage["completion_tokens"]

        raw_sources = container.get("retriever_resources")
        if raw_sources is None and isinstance(container.get("metadata"), dict):
            raw_sources = container["metadata"].get("retriever_resources")
        if raw_sources is None and isinstance(usage.get("retriever_resources"), list):
            raw_sources = usage.get("retriever_resources")
        if isinstance(raw_sources, list):
            sources = normalize_sources(raw_sources)
            meta["sources"] = sources
            meta["kb"] = len(sources)

        model = container.get("model_name") or container.get("model")
        if model:
            meta["model"] = model

    def handle_dify_event(self, obj, state):
        event = obj.get("event") or ""
        data = obj.get("data") if isinstance(obj.get("data"), dict) else {}

        if event in ("message", "agent_message"):
            chunk = obj.get("answer") or ""
            if chunk:
                state["text"].append(chunk)
                self.sse("delta", {"text": chunk})
            return

        if event == "text_chunk":
            chunk = data.get("text") or ""
            if chunk:
                state["text"].append(chunk)
                self.sse("delta", {"text": chunk})
            return

        if event == "message_replace":
            text = obj.get("answer") or ""
            state["text"] = [text]
            self.sse("replace", {"text": text})
            return

        if event == "message_end":
            self.apply_meta(state, obj.get("metadata") or {})
            if obj.get("conversation_id"):
                state["conv"] = obj["conversation_id"]
            return

        if event == "workflow_finished":
            outputs = data.get("outputs") or {}
            answer = pick_answer(outputs)
            if answer:
                state["final"] = answer
                self.sse("final", {"text": answer})
            if data.get("total_tokens") and not state["meta"].get("tokens"):
                state["meta"]["tokens"] = data["total_tokens"]
            if not state["meta"].get("sources") and isinstance(outputs.get("files"), list):
                pass
            return

        if event == "error":
            msg = obj.get("message") or data.get("error") or "Dify 执行出错"
            self.sse("error", {"message": msg})
            return


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def port_in_use(host, port):
    """本机是否已经有服务在监听这个端口。

    为什么要主动探一下：Windows 下 socket 默认带 SO_REUSEADDR，
    两个进程可以**同时绑定同一个端口**，表现为"启动了但访问时好时坏"，
    比直接报「端口被占用」难查得多。所以绑定前先连一下。
    """
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "", "::", "*") else host
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.6)
    try:
        return s.connect_ex((probe_host, port)) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def main():
    host = (CONFIG.get("server") or {}).get("host", "0.0.0.0")
    # 允许用环境变量临时覆盖端口 / 数据库（供自检脚本用，不污染真实数据）
    port = int(os.environ.get("MQ50_PORT") or (CONFIG.get("server") or {}).get("port", 8001))

    # 先查端口，再打印横幅 —— 否则用户会先看到一堆"启动信息"、最后才发现失败
    if port_in_use(host, port):
        print("")
        print("  [启动失败] 端口 %d 上已经有一个服务在跑了。" % port)
        print("  请任选其一：")
        print("    1) 直接打开浏览器用它：http://127.0.0.1:%d" % port)
        print("    2) 先停止旧服务：stop.bat（Windows）/ ./stop.sh（macOS、Linux）")
        print("    3) 换一个端口：改 config.json 里的 server.port")
        print("")
        return 1

    init_db()
    d = CONFIG.get("dify") or {}
    ip = get_local_ip()

    def _show(real, key):
        return mask_key(key) if real else "未配置（可在网页「接口设置」里填写）"

    print("=" * 62)
    print("  医学知识智能问答 · 服务已启动")
    print("=" * 62)
    print("  本机访问  : http://127.0.0.1:%d" % port)
    print("  局域网访问: http://%s:%d   <- 手机/其它电脑用这个" % (ip, port))
    print("-" * 62)
    print("  Dify 地址 : %s" % (d.get("base_url") or "未配置"))
    print("  应用类型  : %s" % (d.get("app_type") or "chatflow"))
    print("  流式输出  : %s" % ("开" if d.get("stream", True) else "关"))
    print("  应用密钥  : %s" % _show(dify_ready(), d.get("api_key")))
    print("  知识库密钥: %s" % _show(dataset_ready(), d.get("dataset_api_key")))
    print("  数据库    : %s" % DB_PATH)
    print("-" * 62)
    if not dify_ready():
        print("  [!] 还没配置 Dify 密钥。")
        print("      请打开上面的「本机访问」地址 → 右下角「接口设置」→ 填好密钥并保存。")
        print("-" * 62)
    print("  按 Ctrl+C 停止服务")
    print("=" * 62)

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        # 端口被占用 / 权限不足时给出可操作的提示，而不是一长串 traceback
        print("")
        print("  [启动失败] 无法监听 %s:%d —— %s" % (host, port, e))
        if getattr(e, "errno", None) in (48, 98, 10048):
            print("  该端口已被占用。请任选其一：")
            print("    1) 先运行 stop.bat（Windows）/ stop.sh（macOS、Linux）停掉旧服务")
            print("    2) 改 config.json 里的 server.port 换一个端口")
        else:
            print("  请检查 config.json 里的 server.host / server.port 是否正确。")
        print("")
        return 1

    server.daemon_threads = True

    # 写 PID 文件：停止脚本据此定位进程，不依赖写死的端口
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PID_PATH, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        print("  [!] 无法写入 PID 文件（不影响使用）：%s" % e)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()
        try:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
