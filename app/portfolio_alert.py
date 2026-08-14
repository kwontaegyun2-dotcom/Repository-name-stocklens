# -*- coding: utf-8 -*-
"""내 포트폴리오 자동 감시 — 보유 종목의 매수 적정구간 진입 / 매도 신호를 주기적으로
점검해 웹푸시로 알린다.

app/portfolio.py의 compute()가 이미 계산해둔 today_actions·score_diff·buy_discount_pct·
sell_reasons를 그대로 재사용한다(추가 분석·네트워크 호출 없음). 같은 종목·같은 신호는
24시간 내 재알림하지 않는다(watch.py와 동일한 쿨다운 패턴).
"""
import sqlite3
import threading
import time
from pathlib import Path

from app import portfolio, watch

CHECK_INTERVAL_SEC = 30 * 60      # 30분마다 재평가 (관심종목 알림보다 느슨해도 충분)
COOLDOWN_SEC = 24 * 3600
BUY_SCORE_MIN = 65.0              # 낮은 점수 종목의 알림 피로도를 줄이기 위한 문턱값

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
        c.execute("""CREATE TABLE IF NOT EXISTS portfolio_alert_fires (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            signal TEXT NOT NULL,
            fired_at REAL NOT NULL
        )""")
    threading.Thread(target=_loop, daemon=True).start()


def _recently_fired(user_id: int, code: str, signal: str) -> bool:
    with _conn() as c:
        row = c.execute(
            "SELECT fired_at FROM portfolio_alert_fires WHERE user_id=? AND code=? AND signal=? "
            "ORDER BY fired_at DESC LIMIT 1",
            (user_id, code, signal),
        ).fetchone()
    return bool(row) and (time.time() - row["fired_at"] < COOLDOWN_SEC)


def _record_fire(user_id: int, code: str, signal: str):
    with _lock, _conn() as c:
        c.execute("INSERT INTO portfolio_alert_fires (user_id, code, signal, fired_at) VALUES (?,?,?,?)",
                   (user_id, code, signal, time.time()))


def _distinct_users() -> list:
    with _conn() as c:
        rows = c.execute("SELECT DISTINCT user_id FROM portfolio").fetchall()
    return [r["user_id"] for r in rows]


def _alerts_for_item(it):
    """(signal_key, 제목, 본문) 목록. 가격이 적정매수가 이하이면서 동시에 목표가
    이상일 수는 없으므로, 종목 하나에서 매수·매도 신호가 함께 나오는 경우는 없다."""
    out = []
    if (it.get("buy_discount_pct") is not None and it["buy_discount_pct"] <= -5
            and it.get("score", 0) >= BUY_SCORE_MIN):
        score_line = (f"종합점수 {it['prev_score']:.0f} → {it['score']:.0f}점"
                      if it.get("prev_score") is not None else f"종합점수 {it['score']:.0f}점")
        out.append((
            "buy",
            f"🔔 {it['name']} 매수 적정구간 진입",
            f"현재가 {it['price']:,.0f}원 · 적정매수가 대비 {it['buy_discount_pct']:.0f}%\n"
            f"{score_line}\nAI 판단: 분할매수 고려",
        ))
    if it.get("sell_reasons"):
        out.append((
            "sell",
            f"🔴 {it['name']} 매도 신호",
            "\n".join(it["sell_reasons"]) + "\nAI 판단: 일부 차익실현 고려",
        ))
    return out


def check_now() -> int:
    """보유종목이 있는 모든 사용자를 재평가해 매수/매도 신호가 있으면 푸시 발송.
    발송 건수를 반환한다."""
    fired = 0
    for user_id in _distinct_users():
        rows = portfolio.list_for_user(user_id)
        if not rows:
            continue
        try:
            result = portfolio.compute(user_id, rows, _analyze_fn)
        except Exception:
            continue
        if not result.get("available"):
            continue
        for it in result["items"]:
            for signal, title, body in _alerts_for_item(it):
                if _recently_fired(user_id, it["code"], signal):
                    continue
                payload = {
                    "title": title,
                    "body": body,
                    "url": "/",
                    "tag": f"stocklens-pf-{it['code']}-{signal}",
                    "renotify": True,
                }
                sent, total = watch.send_to_user(user_id, payload)
                if sent:
                    _record_fire(user_id, it["code"], signal)
                    fired += 1
    return fired


def _loop():
    time.sleep(180)   # 서버 기동 직후 부하 몰림 방지 (watch.py보다 늦게 시작)
    while True:
        try:
            check_now()
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL_SEC)
