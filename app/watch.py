# -*- coding: utf-8 -*-
"""관심종목 매수 기회 알림 — 백그라운드로 조건을 재평가하고 웹푸시로 알린다.

알림 조건(내장, 커스텀 없음 — v1): 매수 매력도 종합점수 65점 이상 이면서
현재가가 적정 매수가(기준, 안전마진 10%) 이하로 내려왔을 때. 같은 종목에
같은 이유로는 24시간 내 재알림하지 않는다(쿨다운).
"""
import sqlite3
import threading
import time
from pathlib import Path

from app import push

CHECK_INTERVAL_SEC = 15 * 60      # 15분마다 재평가
COOLDOWN_SEC = 24 * 3600          # 같은 종목 재알림 최소 간격
SCORE_MIN = 65.0

_db_path = None
_analyze_fn = None
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(_db_path, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init(data_dir: Path, analyze_fn):
    """서버 시작 시 1회 호출. analyze_fn(code) -> main.api_analyze와 동일한 dict를 반환해야 함."""
    global _db_path, _analyze_fn
    _db_path = data_dir / "users.db"
    _analyze_fn = analyze_fn

    with _lock, _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(user_id, code)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS push_subs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            endpoint TEXT UNIQUE NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            ua TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            fail_count INTEGER NOT NULL DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS alert_fires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id INTEGER NOT NULL,
            fired_at REAL NOT NULL,
            reason TEXT NOT NULL
        )""")
    threading.Thread(target=_loop, daemon=True).start()


# ---------------------------------------------------------------- watchlist
def add(user_id: int, code: str, name: str):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO watchlist (user_id, code, name, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, code) DO NOTHING",
            (user_id, code, name, time.time()),
        )


def remove(user_id: int, code: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM watchlist WHERE user_id=? AND code=?", (user_id, code))


def list_for_user(user_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT code, name, created_at FROM watchlist WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def is_watched(user_id: int, code: str) -> bool:
    with _conn() as c:
        row = c.execute("SELECT 1 FROM watchlist WHERE user_id=? AND code=?", (user_id, code)).fetchone()
    return row is not None


# ---------------------------------------------------------------- push subs
def save_sub(user_id: int, endpoint: str, p256dh: str, auth_key: str, ua: str):
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO push_subs (user_id, endpoint, p256dh, auth, ua, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(endpoint) DO UPDATE SET
                 user_id=excluded.user_id, p256dh=excluded.p256dh,
                 auth=excluded.auth, ua=excluded.ua, fail_count=0""",
            (user_id, endpoint, p256dh, auth_key, ua[:200], time.time()),
        )
    return device_count(user_id)


def drop_sub(endpoint: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))


def device_count(user_id: int) -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) n FROM push_subs WHERE user_id=?", (user_id,)).fetchone()["n"]


def _subs_of(user_id: int):
    with _conn() as c:
        return c.execute("SELECT * FROM push_subs WHERE user_id=?", (user_id,)).fetchall()


def send_to_user(user_id: int, payload: dict) -> tuple[int, int]:
    subs = _subs_of(user_id)
    ok = 0
    for sub in subs:
        success, msg = push.send_one(sub, payload)
        if success:
            with _lock, _conn() as c:
                c.execute("UPDATE push_subs SET fail_count=0 WHERE endpoint=?", (sub["endpoint"],))
            ok += 1
        elif msg == "expired":
            drop_sub(sub["endpoint"])
        else:
            with _lock, _conn() as c:
                c.execute("UPDATE push_subs SET fail_count=fail_count+1 WHERE endpoint=?", (sub["endpoint"],))
    return ok, len(subs)


# ---------------------------------------------------------------- 조건 평가 + 발송
def _condition(analysis: dict):
    """(충족여부, 이유문구) 반환."""
    total = analysis.get("total") or {}
    score = total.get("total_score")
    price = analysis.get("price")
    fb = ((analysis.get("targets") or {}).get("fair_buy") or {}).get("base")
    if score is None or price is None or not fb:
        return False, None
    if score >= SCORE_MIN and price <= fb["price"]:
        return True, f"매수 매력도 {score}점 · 현재가가 적정 매수가 이하로 하락"
    return False, None


def _recently_fired(watch_id: int) -> bool:
    with _conn() as c:
        row = c.execute(
            "SELECT fired_at FROM alert_fires WHERE watch_id=? ORDER BY fired_at DESC LIMIT 1",
            (watch_id,),
        ).fetchone()
    return bool(row) and (time.time() - row["fired_at"] < COOLDOWN_SEC)


def _record_fire(watch_id: int, reason: str):
    with _lock, _conn() as c:
        c.execute("INSERT INTO alert_fires (watch_id, fired_at, reason) VALUES (?,?,?)",
                   (watch_id, time.time(), reason))


def check_now() -> int:
    """모든 관심종목을 재평가해 조건 충족 시 알림 발송. 발송 건수를 반환."""
    with _conn() as c:
        rows = c.execute("SELECT * FROM watchlist").fetchall()
    if not rows:
        return 0

    by_code: dict = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)

    fired = 0
    for code, watchers in by_code.items():
        try:
            analysis = _analyze_fn(code)
        except Exception:
            continue
        ok, reason = _condition(analysis)
        if not ok:
            continue
        for w in watchers:
            if _recently_fired(w["id"]):
                continue
            payload = {
                "title": f"🚨 {w['name']} 매수 기회",
                "body": reason,
                "url": "/",
                "tag": f"stocklens-{w['code']}",
                "renotify": True,
            }
            sent, total = send_to_user(w["user_id"], payload)
            if sent:
                _record_fire(w["id"], reason)
                fired += 1
    return fired


def _loop():
    time.sleep(120)   # 서버 기동 직후 부하 몰림 방지
    while True:
        try:
            check_now()
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL_SEC)
