# -*- coding: utf-8 -*-
"""실적·뉴스·수급 급변 알림 — 관심종목의 상태 변화(직전 체크 대비 델타)를 감지해
웹푸시로 알린다. watch.py의 관심종목·구독 인프라를 그대로 재사용한다(추가 옵트인 UI 불필요).

"급변"은 절대값이 아니라 **직전 체크 대비 변화**로 정의한다 — 그래야 이미 알려진 상태를
매번 재알림하지 않는다. 이번이 그 종목의 첫 체크라면(state 없음) 기준값만 저장하고
알림은 보내지 않는다(배포 직후 기존 상태 전체가 "새 이벤트"로 오탐되는 것 방지).

- 📈📉 실적 급변: 최근 실적 영업이익 YoY(`metrics.op_growth`)가 새로 갱신되며 ±30%를 넘을 때
- 📰 뉴스 급변: 새 뉴스 기사가 나오고 감성이 긍정/부정으로 뚜렷할 때(중립 제외)
- 🔄 수급 급변: 외국인+기관 최근 5일 순매수 방향이 매수↔매도로 전환될 때(국내 전용)
"""
import sqlite3
import threading
import time
from pathlib import Path

from app import watch

CHECK_INTERVAL_SEC = 20 * 60      # 20분마다 재평가
COOLDOWN_SEC = 24 * 3600
EARNINGS_SURPRISE_PCT = 30.0      # 실적 서프라이즈/쇼크 문턱값

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
        c.execute("""CREATE TABLE IF NOT EXISTS event_state (
            code TEXT PRIMARY KEY,
            op_growth REAL,
            news_url TEXT,
            flow_dir TEXT,
            updated_at REAL NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS event_alert_fires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            signal TEXT NOT NULL,
            fired_at REAL NOT NULL
        )""")
    threading.Thread(target=_loop, daemon=True).start()


# ---------------------------------------------------------------- state
def _get_state(code: str):
    with _conn() as c:
        row = c.execute("SELECT * FROM event_state WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


def _save_state(code: str, state: dict):
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO event_state (code, op_growth, news_url, flow_dir, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 op_growth=excluded.op_growth, news_url=excluded.news_url,
                 flow_dir=excluded.flow_dir, updated_at=excluded.updated_at""",
            (code, state.get("op_growth"), state.get("news_url"), state.get("flow_dir"), time.time()),
        )


def _flow_direction(flows: list):
    """flows[:5](최근 5일, 최신순)의 외국인+기관 순매수 합 부호. 데이터 부족하면 None."""
    days = (flows or [])[:5]
    if len(days) < 3:
        return None
    total = 0.0
    has_data = False
    for d in days:
        f, o = d.get("foreigner"), d.get("organ")
        if f is not None or o is not None:
            has_data = True
        total += (f or 0) + (o or 0)
    if not has_data or total == 0:
        return None
    return "buy" if total > 0 else "sell"


# ---------------------------------------------------------------- 이벤트 감지
def _detect(code: str, analysis: dict):
    """(새 state, [(signal, title, body), ...]) 반환. state 없던 종목은 기준값만 세팅하고 무알림."""
    prev = _get_state(code)
    name = analysis.get("name", code)
    new_state = {}
    events = []

    # 1) 실적 급변
    op_growth = (analysis.get("metrics") or {}).get("op_growth")
    if op_growth is not None:
        op_growth = round(op_growth, 1)
        new_state["op_growth"] = op_growth
        if prev and prev.get("op_growth") is not None and abs(op_growth - prev["op_growth"]) >= 0.1 \
                and abs(op_growth) >= EARNINGS_SURPRISE_PCT:
            if op_growth > 0:
                events.append(("earnings", f"📈 {name} 실적 서프라이즈",
                                f"최근 실적 영업이익 전년比 {op_growth:+.0f}%"))
            else:
                events.append(("earnings", f"📉 {name} 실적 쇼크",
                                f"최근 실적 영업이익 전년比 {op_growth:+.0f}%"))
    elif prev:
        new_state["op_growth"] = prev.get("op_growth")

    # 2) 뉴스 급변
    top_news = (analysis.get("news") or [None])[0]
    if top_news and top_news.get("url"):
        new_state["news_url"] = top_news["url"]
        if prev and prev.get("news_url") and prev["news_url"] != top_news["url"] \
                and top_news.get("sentiment") in ("positive", "negative"):
            tag = "긍정" if top_news["sentiment"] == "positive" else "부정"
            events.append(("news", f"📰 {name} 새 뉴스({tag})", top_news.get("title", "")[:80]))
    elif prev:
        new_state["news_url"] = prev.get("news_url")

    # 3) 수급 급변 (국내 전용 — 미국은 flows가 항상 비어 있어 자동으로 스킵됨)
    flow_dir = _flow_direction(analysis.get("flows"))
    if flow_dir is not None:
        new_state["flow_dir"] = flow_dir
        if prev and prev.get("flow_dir") and prev["flow_dir"] != flow_dir:
            arrow = "매도세 → 매수세" if flow_dir == "buy" else "매수세 → 매도세"
            events.append(("flow", f"🔄 {name} 수급 전환", f"외국인+기관 최근 5일 수급이 {arrow}로 전환"))
    elif prev:
        new_state["flow_dir"] = prev.get("flow_dir")

    return new_state, events


def _recently_fired(user_id: int, code: str, signal: str) -> bool:
    with _conn() as c:
        row = c.execute(
            "SELECT fired_at FROM event_alert_fires WHERE user_id=? AND code=? AND signal=? "
            "ORDER BY fired_at DESC LIMIT 1",
            (user_id, code, signal),
        ).fetchone()
    return bool(row) and (time.time() - row["fired_at"] < COOLDOWN_SEC)


def _record_fire(user_id: int, code: str, signal: str):
    with _lock, _conn() as c:
        c.execute("INSERT INTO event_alert_fires (user_id, code, signal, fired_at) VALUES (?,?,?,?)",
                   (user_id, code, signal, time.time()))


def check_now() -> int:
    """관심종목을 종목당 1회만 재분석해 실적/뉴스/수급 급변을 감지, 그 종목의 관심등록자 전원에게
    푸시 발송. 발송 건수를 반환한다."""
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
        new_state, events = _detect(code, analysis)
        _save_state(code, new_state)
        if not events:
            continue
        for signal, title, body in events:
            payload = {
                "title": title, "body": body, "url": "/",
                "tag": f"stocklens-ev-{code}-{signal}", "renotify": True,
            }
            for w in watchers:
                if _recently_fired(w["user_id"], code, signal):
                    continue
                sent, total = watch.send_to_user(w["user_id"], payload)
                if sent:
                    _record_fire(w["user_id"], code, signal)
                    fired += 1
    return fired


def _loop():
    time.sleep(240)   # watch.py(120s)·portfolio_alert.py(180s)보다 늦게 시작해 기동 부하 분산
    while True:
        try:
            check_now()
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL_SEC)
