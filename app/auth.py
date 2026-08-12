# -*- coding: utf-8 -*-
"""회원가입/로그인/세션/관리자 — 표준 라이브러리만 사용 (sqlite3 + hashlib + hmac)."""
import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from threading import Lock

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_lock = Lock()
_db_path = None
_secret = None


def init(data_dir: Path):
    """서버 시작 시 1회 호출. data_dir: DB·시크릿 파일을 저장할 디렉터리."""
    global _db_path, _secret
    data_dir.mkdir(parents=True, exist_ok=True)
    _db_path = data_dir / "users.db"

    secret_path = data_dir / "auth_secret.key"
    if not secret_path.exists():
        secret_path.write_text(os.urandom(32).hex())
    _secret = bytes.fromhex(secret_path.read_text().strip())

    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_login REAL,
            is_admin INTEGER NOT NULL DEFAULT 0
        )""")
    _promote_admins_from_env()


def _conn():
    c = sqlite3.connect(_db_path)
    c.row_factory = sqlite3.Row
    return c


def _admin_emails():
    raw = os.environ.get("STOCKLENS_ADMIN_EMAIL", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _promote_admins_from_env():
    emails = _admin_emails()
    if not emails:
        return
    with _lock, _conn() as c:
        for email in emails:
            c.execute("UPDATE users SET is_admin=1 WHERE lower(email)=?", (email,))


# ---------------------------------------------------------------- password
def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 260_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2$sha256${iterations}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, digest, iterations, salt_hex, hash_hex = stored.split("$")
        iterations = int(iterations)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac(digest, password.encode(), salt, iterations)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------------------------------------------------------------- users
def _row_to_user(row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "created_at": row["created_at"],
        "last_login": row["last_login"],
        "is_admin": bool(row["is_admin"]),
    }


def signup(email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValueError("올바른 이메일 형식이 아닙니다.")
    if not password or len(password) < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다.")

    is_admin = 1 if email in _admin_emails() else 0
    with _lock, _conn() as c:
        existing = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            raise ValueError("이미 가입된 이메일입니다.")
        now = time.time()
        cur = c.execute(
            "INSERT INTO users (email, password_hash, created_at, last_login, is_admin) VALUES (?,?,?,?,?)",
            (email, _hash_password(password), now, now, is_admin),
        )
        row = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_user(row)


def authenticate(email: str, password: str) -> dict | None:
    email = (email or "").strip().lower()
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row or not _verify_password(password, row["password_hash"]):
            return None
        c.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), row["id"]))
        row = c.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
    return _row_to_user(row)


def get_user(user_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def list_users() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [_row_to_user(r) for r in rows]


def stats() -> dict:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        now = time.time()
        today = c.execute("SELECT COUNT(*) n FROM users WHERE created_at >= ?", (now - 86400,)).fetchone()["n"]
        week = c.execute("SELECT COUNT(*) n FROM users WHERE created_at >= ?", (now - 7 * 86400,)).fetchone()["n"]
        active_today = c.execute("SELECT COUNT(*) n FROM users WHERE last_login >= ?", (now - 86400,)).fetchone()["n"]
    return {"total": total, "signups_24h": today, "signups_7d": week, "active_24h": active_today}


# ---------------------------------------------------------------- session token (서명된 쿠키 값)
_SESSION_DAYS = 30


def create_session_token(user_id: int) -> str:
    payload = json.dumps({"uid": user_id, "exp": time.time() + _SESSION_DAYS * 86400}).encode()
    b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(_secret, b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_session_token(token: str) -> int | None:
    if not token or "." not in token:
        return None
    b64, sig = token.rsplit(".", 1)
    expected = hmac.new(_secret, b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        pad = "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(b64 + pad))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload.get("uid")
