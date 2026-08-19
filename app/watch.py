# -*- coding: utf-8 -*-
"""관심종목 — 담기(★)와 매수 기회 알림(🔔)을 하나의 서버 저장소로 통합.

⚠️ 과거엔 ★가 localStorage(브라우저 로컬)에, 🔔가 이 테이블(서버)에 따로 저장돼
"로그인하면 관심종목 저장·기기간 동기화"라는 안내가 실제로는 지켜지지 않았다
(사용자 지적: 홈에 뜬 종목이 /api/watch에는 없음). 지금은 ★ 클릭 = 이 테이블에
저장이고, 로그인하지 않은 사용자는 로그인 모달로 유도한다(프론트 app.js).

담을 때 가격·점수·판단을 스냅샷으로 같이 저장해(added_*) "담은 뒤 뭐가
달라졌나"를 보여줄 수 있게 한다. last_* 는 매 백그라운드 체크마다 갱신되는
"직전 확인값"으로, 알림 조건의 "돌파/이탈"(크로스) 감지에만 쓴다.

알림 조건은 종목마다 독립적으로 켤 수 있다:
- alert_buy: 기존 기본 조건(매수 매력도 65점↑ + 현재가가 적정매수가 이하)
- alert_price_target: 지정 가격을 위/아래로 돌파했을 때
- alert_score_threshold: 지정 점수를 위/아래로 돌파했을 때
- alert_verdict_change: AI 판단 등급이 바뀌었을 때
- alert_anomaly: 이상징후 탐지(app/anomaly.py)에 포착됐을 때 — 랭킹 캐시를
  재사용해 추가 네트워크 호출 없음.
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

# 새 컬럼(added_*/last_*/alert_*)은 기존 배포본 DB에 없을 수 있어 마이그레이션으로 추가한다.
_NEW_COLUMNS = {
    "added_price": "REAL",
    "added_score": "REAL",
    "added_verdict": "TEXT",
    "added_verdict_tier": "TEXT",
    "memo": "TEXT NOT NULL DEFAULT ''",
    "tags": "TEXT NOT NULL DEFAULT ''",
    "last_price": "REAL",
    "last_score": "REAL",
    "last_verdict": "TEXT",
    "last_verdict_tier": "TEXT",
    "last_checked_at": "REAL",
    "alert_buy": "INTEGER NOT NULL DEFAULT 1",
    "alert_price_target": "REAL",
    "alert_score_threshold": "REAL",
    "alert_verdict_change": "INTEGER NOT NULL DEFAULT 0",
    "alert_anomaly": "INTEGER NOT NULL DEFAULT 0",
}


def _conn():
    c = sqlite3.connect(_db_path, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _migrate(c):
    existing = {row["name"] for row in c.execute("PRAGMA table_info(watchlist)")}
    for col, decl in _NEW_COLUMNS.items():
        if col not in existing:
            c.execute(f"ALTER TABLE watchlist ADD COLUMN {col} {decl}")


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
        _migrate(c)
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
def add(user_id: int, code: str, name: str, price=None, score=None, verdict=None, verdict_tier=None):
    # ON CONFLICT DO NOTHING — 이미 담은 종목이면 added_* 베이스라인(처음 담았을 때 스냅샷)을
    # 덮어쓰지 않는다. "담은 뒤 뭐가 달라졌나"는 최초 담은 시점 기준이어야 의미가 있다.
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO watchlist (user_id, code, name, created_at, added_price, added_score, "
            "added_verdict, added_verdict_tier) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, code) DO NOTHING",
            (user_id, code, name, time.time(), price, score, verdict, verdict_tier),
        )


def remove(user_id: int, code: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM watchlist WHERE user_id=? AND code=?", (user_id, code))


def update_settings(user_id: int, code: str, memo: str, tags: str, alert_buy: bool,
                     alert_price_target, alert_score_threshold,
                     alert_verdict_change: bool, alert_anomaly: bool):
    with _lock, _conn() as c:
        c.execute(
            """UPDATE watchlist SET memo=?, tags=?, alert_buy=?, alert_price_target=?,
               alert_score_threshold=?, alert_verdict_change=?, alert_anomaly=?
               WHERE user_id=? AND code=?""",
            (memo, tags, int(alert_buy), alert_price_target, alert_score_threshold,
             int(alert_verdict_change), int(alert_anomaly), user_id, code),
        )


def list_for_user(user_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM watchlist WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = [t for t in (d.get("tags") or "").split(",") if t]
        out.append(d)
    return out


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
    """기본 조건(alert_buy): (충족여부, 이유문구) 반환."""
    total = analysis.get("total") or {}
    score = total.get("total_score")
    price = analysis.get("price")
    fb = ((analysis.get("targets") or {}).get("fair_buy") or {}).get("base")
    if score is None or price is None or not fb:
        return False, None
    if score >= SCORE_MIN and price <= fb["price"]:
        return True, f"매수 매력도 {score}점 · 현재가가 적정 매수가 이하로 하락"
    return False, None


def _crossed(prev, cur, threshold):
    """직전 값과 현재 값이 threshold를 사이에 두고 반대편에 있으면(=돌파) True.
    prev가 없으면(최초 체크·마이그레이션 직후) 오탐 방지를 위해 무조건 False."""
    if prev is None or cur is None or threshold is None:
        return False
    return (prev < threshold) != (cur < threshold)


def _evaluate(row: sqlite3.Row, analysis: dict, anomaly_map: dict):
    total = analysis.get("total") or {}
    score = total.get("total_score")
    price = analysis.get("price")
    verdict = analysis.get("ai_verdict") or {}
    tier = verdict.get("tier")
    label = verdict.get("label")

    reasons = []

    if row["alert_buy"]:
        ok, reason = _condition(analysis)
        if ok:
            reasons.append(reason)

    target = row["alert_price_target"]
    if target is not None and _crossed(row["last_price"], price, target):
        reasons.append(f"목표가 {target:,.0f} 돌파/이탈 (현재 {price:,.0f})")

    threshold = row["alert_score_threshold"]
    if threshold is not None and _crossed(row["last_score"], score, threshold):
        reasons.append(f"종합점수 {threshold:.0f}점 돌파/이탈 (현재 {score:.1f}점)")

    if row["alert_verdict_change"] and row["last_verdict_tier"] and tier and tier != row["last_verdict_tier"]:
        reasons.append(f"판단 변경: {row['last_verdict'] or '-'} → {label or '-'}")

    if row["alert_anomaly"]:
        item = anomaly_map.get(row["code"])
        if item:
            from app import anomaly
            kind, why = anomaly.classify_item(item)
            if kind == "bull":
                reasons.append("이상징후(저평가 확대): " + ", ".join(why[:2]))
            elif kind == "bear":
                reasons.append("이상징후(단기 과열): " + ", ".join(why[:2]))

    snapshot = {"price": price, "score": score, "verdict": label, "verdict_tier": tier}
    return reasons, snapshot


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

    # 이상징후 조건용 — 랭킹 백그라운드 채점 캐시를 재사용(추가 네트워크 호출 없음).
    anomaly_map = {}
    try:
        from app import ranking
        for market in ("KR", "US"):
            for item in ranking.get(market).get("items", []):
                anomaly_map[item["code"]] = item
    except Exception:
        pass

    fired = 0
    for w in rows:
        try:
            analysis = _analyze_fn(w["code"])
        except Exception:
            continue
        reasons, snap = _evaluate(w, analysis, anomaly_map)

        # 알림 발송 여부와 무관하게 매 체크마다 최신 스냅샷을 저장 — 다음 체크의
        # 돌파/이탈(크로스) 감지 기준선이 된다.
        with _lock, _conn() as c:
            c.execute(
                "UPDATE watchlist SET last_price=?, last_score=?, last_verdict=?, "
                "last_verdict_tier=?, last_checked_at=? WHERE id=?",
                (snap["price"], snap["score"], snap["verdict"], snap["verdict_tier"], time.time(), w["id"]),
            )

        if not reasons or _recently_fired(w["id"]):
            continue
        body = " · ".join(reasons)
        payload = {
            "title": f"🔔 {w['name']}",
            "body": body,
            "url": "/",
            "tag": f"stocklens-{w['code']}",
            "renotify": True,
        }
        sent, total = send_to_user(w["user_id"], payload)
        if sent:
            _record_fire(w["id"], body)
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
