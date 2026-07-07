# -*- coding: utf-8 -*-
"""주요 종목 실시간 랭킹 — 백그라운드로 채점·캐싱해 즉시 순위 제공."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app import naver, analysis

# (종목코드, 표시명, 섹터)
UNIVERSE = [
    ("005930", "삼성전자", "반도체"), ("000660", "SK하이닉스", "반도체"),
    ("009150", "삼성전기", "반도체"), ("042700", "한미반도체", "반도체"),
    ("000990", "DB하이텍", "반도체"),
    ("373220", "LG에너지솔루션", "2차전지"), ("006400", "삼성SDI", "2차전지"),
    ("247540", "에코프로비엠", "2차전지"), ("086520", "에코프로", "2차전지"),
    ("003670", "포스코퓨처엠", "2차전지"), ("051910", "LG화학", "2차전지"),
    ("207940", "삼성바이오로직스", "바이오"), ("068270", "셀트리온", "바이오"),
    ("196170", "알테오젠", "바이오"), ("028300", "HLB", "바이오"),
    ("000100", "유한양행", "바이오"),
    ("005380", "현대차", "자동차"), ("000270", "기아", "자동차"),
    ("012330", "현대모비스", "자동차"),
    ("035420", "NAVER", "인터넷·게임"), ("035720", "카카오", "인터넷·게임"),
    ("259960", "크래프톤", "인터넷·게임"), ("251270", "넷마블", "인터넷·게임"),
    ("036570", "엔씨소프트", "인터넷·게임"),
    ("105560", "KB금융", "금융"), ("055550", "신한지주", "금융"),
    ("086790", "하나금융지주", "금융"), ("138040", "메리츠금융지주", "금융"),
    ("000810", "삼성화재", "금융"), ("032830", "삼성생명", "금융"),
    ("005490", "POSCO홀딩스", "철강·소재"), ("010130", "고려아연", "철강·소재"),
    ("015760", "한국전력", "에너지·화학"), ("096770", "SK이노베이션", "에너지·화학"),
    ("010950", "S-Oil", "에너지·화학"),
    ("012450", "한화에어로스페이스", "방산·조선·기계"),
    ("329180", "HD현대중공업", "방산·조선·기계"),
    ("034020", "두산에너빌리티", "방산·조선·기계"),
    ("064350", "현대로템", "방산·조선·기계"),
    ("028260", "삼성물산", "지주·기타"), ("034730", "SK", "지주·기타"),
    ("066570", "LG전자", "지주·기타"), ("018260", "삼성에스디에스", "지주·기타"),
    ("033780", "KT&G", "지주·기타"), ("011200", "HMM", "지주·기타"),
]

_lock = threading.Lock()
_state = {"items": [], "updated_at": 0, "computing": False}
REFRESH_SEC = 1800  # 30분마다 갱신


def _score(entry):
    code, disp, sector = entry
    try:
        b = naver.basic(code)
        name = b.get("stockName", disp)
        price = analysis.to_num(b.get("closePrice"))
        rate = analysis.to_num(b.get("fluctuationsRatio"))

        def safe(fn, d):
            try:
                return fn()
            except Exception:
                return d

        integ = safe(lambda: naver.integration(code), {})
        fin_a = safe(lambda: naver.finance(code, "annual"), {})
        news = safe(lambda: naver.news(code, 15), [])
        trend = safe(lambda: naver.trend(code), [])
        candles = safe(lambda: naver.candles(code, 260), [])

        tech = analysis.technical_analysis(candles)
        fund = analysis.fundamental_analysis(integ, fin_a)
        senti = analysis.news_sentiment(news)
        cons = analysis.consensus_info(integ, price)
        total = analysis.total_evaluation(fund, tech, senti, cons, trend)
        return {
            "code": code, "name": name, "sector": sector,
            "price": price, "rate": rate,
            "score": total["total_score"], "grade": total["grade"],
            "grade_desc": total["grade_desc"],
            "categories": total["categories"],
            "upside": cons.get("upside"),
            "verdict": tech.get("verdict") if tech.get("available") else None,
        }
    except Exception:
        return None


def _compute():
    with _lock:
        if _state["computing"]:
            return
        _state["computing"] = True
    try:
        out = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            for r in ex.map(_score, UNIVERSE):
                if r:
                    out.append(r)
        out.sort(key=lambda x: x["score"], reverse=True)
        for i, r in enumerate(out, 1):
            r["rank"] = i
        with _lock:
            _state["items"] = out
            _state["updated_at"] = time.time()
    finally:
        with _lock:
            _state["computing"] = False


def _loop():
    while True:
        _compute()
        time.sleep(REFRESH_SEC)


def start_background():
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def get(sector: str = None):
    with _lock:
        items = list(_state["items"])
        meta = {"updated_at": _state["updated_at"], "computing": _state["computing"]}
    sectors = sorted({r["sector"] for r in items})
    if sector and sector != "전체":
        items = [r for r in items if r["sector"] == sector]
        for i, r in enumerate(items, 1):
            r = dict(r)
    return {"items": items, "sectors": sectors, **meta}
