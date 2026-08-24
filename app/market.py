# -*- coding: utf-8 -*-
"""마켓 브리핑 — 국내외 지수·환율·원자재·채권·심리·밸류에이션 지표를 한 화면에.

전부 외부 무료 소스(네이버 비공식 API, FRED 공식 CSV, 공개 페이지)를 조합한다 —
이 프로젝트가 이미 네이버 비공식 API 하나에 전면 의존하는 것과 같은 성격의 선택.
값을 못 구하면 그 항목만 None으로 비우고 절대 지어내지 않는다(CLAUDE.md 원칙과 동일).

⚠️ CNN 공포탐욕지수(production.dataviz.cnn.io)는 봇 차단(418 "I'm a teapot")이 걸려
있어 우회하지 않고 포기했다 — 대신 공식·표준 지표인 VIX(CBOE 변동성지수, FRED 제공)를
심리 지표로 쓴다. 은(실버)은 네이버에 아예 없어(실측 확인) Yahoo Finance 차트 API로
보충한다."""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from app import ai, naver
from app.analysis import to_num

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

REFRESH_SEC = 300            # 지수·환율·원자재(네이버) — 5분, 실시간성이 중요한 값들
# ⚠️ FRED(미국채·VIX)·Yahoo(은)는 원본 자체가 하루 1회 정도만 갱신되는데, 5분마다 계속
# 두드리면(하루 수천 건) 불필요할뿐더러 실측 중 FRED가 일시적으로 연결을 끊는 것도
# 겪었다(레이트리밋 추정) — 이 값들만 30분 주기로 따로 캐시한다.
MACRO_REFRESH_SEC = 30 * 60
SLOW_REFRESH_SEC = 6 * 3600  # S&P PER·버핏지수 — 원본 자체가 하루~분기 단위로만 바뀜
COMMENTARY_REFRESH_SEC = 1800  # AI/룰기반 한줄평 — 30분

_lock = threading.Lock()
_state = {"data": None, "updated_at": 0}
_macro = {"bonds": None, "sentiment": None, "silver": None, "updated_at": 0}
_slow = {"sp500_per": None, "buffett": None, "updated_at": 0}
_commentary = {"text": None, "source": None, "updated_at": 0}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ---------------------------------------------------------------- 외부 소스별 조회
# ⚠️ 처음엔 미국채 10·30년물·VIX를 FRED 공식 CSV(API 키 불필요, fredgraph.csv)로
# 받았는데, 로컬에서는 됐지만 **오라클 배포 서버에서는 fred.stlouisfed.org 자체가
# 연결 안 됨**을 실측으로 확인했다(status=000, 다른 소스는 정상 — 이 서버 IP 대역이
# 막힌 것으로 추정). Yahoo Finance는 로컬·오라클 둘 다 정상이라 전부 이쪽으로 통일.
def _yahoo_quote(symbol: str):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                      params={"interval": "1d", "range": "5d"}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose")
    rate = round((price - prev) / prev * 100, 2) if (price and prev) else None
    return {"price": price, "rate": rate}


_SP500_PER_RE = re.compile(r'Current S&P 500 PE Ratio is ([\d.]+)')


def _sp500_per():
    r = requests.get("https://www.multpl.com/s-p-500-pe-ratio", headers=HEADERS, timeout=15)
    r.raise_for_status()
    m = _SP500_PER_RE.search(r.text)
    return float(m.group(1)) if m else None


_BUFFETT_RE = re.compile(
    r'we calculate the Buffett Indicator as ([\d.]+)%,.*?'
    r'suggesting that the US stock market is ([A-Za-z ]+)\.', re.S)
_BUFFETT_DATE_RE = re.compile(r'As of ([A-Za-z]+ \d{1,2}, \d{4}) we calculate')
_BUFFETT_LABEL_KO = {
    "strongly overvalued": "상당히 고평가", "overvalued": "고평가",
    "modestly overvalued": "다소 고평가", "fair valued": "적정 수준",
    "modestly undervalued": "다소 저평가", "undervalued": "저평가",
    "strongly undervalued": "상당히 저평가",
}


def _buffett_indicator():
    r = requests.get("https://www.currentmarketvaluation.com/models/buffett-indicator.php",
                      headers=HEADERS, timeout=15)
    r.raise_for_status()
    m = _BUFFETT_RE.search(r.text)
    if not m:
        return None
    d = _BUFFETT_DATE_RE.search(r.text)
    label_en = m.group(2).strip()
    return {
        "value": float(m.group(1)),
        "label": _BUFFETT_LABEL_KO.get(label_en.lower(), label_en),
        "as_of": d.group(1) if d else None,
    }


def _major_item(majors: list, code: str):
    it = next((x for x in majors if (x.get("itemCode") or x.get("reutersCode")) == code), None)
    if not it:
        return None
    return {"name": it.get("stockName") or it.get("name"),
            "price": to_num(it.get("closePrice")), "rate": to_num(it.get("fluctuationsRatio"))}


def _world_index_item(code: str, label: str):
    d = naver.world_index(code)
    if not d:
        return None
    return {"name": label, "price": to_num(d.get("closePrice")), "rate": to_num(d.get("fluctuationsRatio"))}


# ---------------------------------------------------------------- 스냅샷 조립
def _build_naver():
    """네이버 기반 값만 — 지수·환율·원자재(은 제외). 5분마다 불려도 부담 없다."""
    majors = (_safe(lambda: naver.home_majors(), {}) or {}).get("homeMajors", [])
    mkt = _safe(lambda: naver.market_index_page(), {}) or {}
    sp500 = _safe(lambda: _world_index_item(".INX", "S&P 500"))

    return {
        "indices": {
            "kospi": _major_item(majors, "KOSPI"),
            "kosdaq": _major_item(majors, "KOSDAQ"),
            "sp500": sp500,
            "dow": _major_item(majors, ".DJI"),
            "nasdaq": _major_item(majors, ".IXIC"),
            "nikkei": _major_item(majors, ".N225"),
            "shanghai": _major_item(majors, ".SSEC"),
        },
        "fx": {
            "usdkrw": mkt.get("usdkrw"), "jpykrw100": mkt.get("jpykrw100"),
            "eurkrw": mkt.get("eurkrw"), "cnykrw": mkt.get("cnykrw"),
            "usdjpy": mkt.get("usdjpy"), "dxy": mkt.get("dxy"),
        },
        "commodities": {
            "wti": mkt.get("wti"), "gasoline": mkt.get("gasoline"),
            "gold_intl": mkt.get("gold_intl"), "gold_domestic": mkt.get("gold_domestic"),
        },
    }


def _refresh_macro():
    """미국채10·30년(^TNX·^TYX)·VIX(^VIX)·은(SI=F) — 전부 Yahoo, 30분 주기 전용 캐시."""
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_us10y = ex.submit(_safe, lambda: _yahoo_quote("^TNX"))
        f_us30y = ex.submit(_safe, lambda: _yahoo_quote("^TYX"))
        f_vix = ex.submit(_safe, lambda: _yahoo_quote("^VIX"))
        f_silver = ex.submit(_safe, lambda: _yahoo_quote("SI=F"))
        us10y, us30y, vix, silver = f_us10y.result(), f_us30y.result(), f_vix.result(), f_silver.result()

    now_str = time.strftime("%Y-%m-%d")
    with _lock:
        if us10y or us30y:
            _macro["bonds"] = {
                "us10y": us10y.get("price") if us10y else (_macro["bonds"] or {}).get("us10y"),
                "us30y": us30y.get("price") if us30y else (_macro["bonds"] or {}).get("us30y"),
                "as_of": now_str,
            }
        if vix:
            _macro["sentiment"] = {"vix": vix.get("price"), "vix_date": now_str}
        if silver:
            _macro["silver"] = silver
        _macro["updated_at"] = time.time()


def _refresh_slow():
    per = _safe(_sp500_per)
    buffett = _safe(_buffett_indicator)
    with _lock:
        if per is not None:
            _slow["sp500_per"] = per
        if buffett is not None:
            _slow["buffett"] = buffett
        _slow["updated_at"] = time.time()


def _rule_commentary(snap: dict) -> str:
    """실제 AI 호출 없이(공개 배포 비용 보호, CLAUDE.md 5번 규칙) 수집한 숫자로만
    조립하는 한줄평 — AI_ALLOWED가 꺼진 배포본(현재 오라클 기본값)에서도 항상 뭔가는
    보여주기 위한 폴백. AI가 켜지면 market_commentary_ai()가 대신 이 자리를 채운다."""
    idx = snap["indices"]
    parts = []
    kospi, kosdaq, sp500 = idx.get("kospi"), idx.get("kosdaq"), idx.get("sp500")

    def _word(r, up="상승", down="하락", big=3.0, big_up="급등", big_down="급락"):
        if r is None:
            return None
        if r >= big:
            return big_up
        if r <= -big:
            return big_down
        if r > 0:
            return up
        if r < 0:
            return down
        return "보합"

    if kospi and kospi.get("rate") is not None:
        parts.append(f"코스피 {_word(kospi['rate'])}({kospi['rate']:+.2f}%)")
    if kosdaq and kosdaq.get("rate") is not None:
        parts.append(f"코스닥 {kosdaq['rate']:+.2f}%")
    if sp500 and sp500.get("rate") is not None:
        parts.append(f"S&P500 전일 {_word(sp500['rate'])}({sp500['rate']:+.2f}%)")
    lead = " · ".join(parts) if parts else "주요 지수 데이터를 불러오지 못했습니다"

    notes = []
    vix = snap["sentiment"].get("vix")
    if vix is not None:
        if vix >= 30:
            notes.append(f"VIX {vix:.1f}로 시장이 크게 불안한 상태")
        elif vix >= 20:
            notes.append(f"VIX {vix:.1f}로 변동성이 다소 높은 편")
        else:
            notes.append(f"VIX {vix:.1f}로 변동성은 안정적인 수준")
    usdkrw = snap["fx"].get("usdkrw")
    if usdkrw is not None:
        notes.append(f"원/달러 {usdkrw:,.1f}원")
    buffett = _slow.get("buffett")
    if buffett:
        notes.append(f"버핏지수 {buffett['value']:.0f}%({buffett['label']})")

    tail = " · ".join(notes)
    return lead + (f". {tail}." if tail else ".")


def _refresh_commentary(snap: dict, ai_allowed: bool):
    text, source = None, "rule"
    if ai_allowed:
        text = _safe(lambda: ai.market_commentary(snap, _slow))
        if text:
            source = "ai"
    if not text:
        text = _rule_commentary(snap)
        source = "rule"
    with _lock:
        _commentary["text"] = text
        _commentary["source"] = source
        _commentary["updated_at"] = time.time()


def _compute(ai_allowed: bool):
    fast = _safe(_build_naver)
    if not fast:
        return

    with _lock:
        need_macro = time.time() - _macro["updated_at"] > MACRO_REFRESH_SEC
        need_slow = _slow["updated_at"] == 0 or time.time() - _slow["updated_at"] > SLOW_REFRESH_SEC
    if need_macro:
        _refresh_macro()
    if need_slow:
        _refresh_slow()

    with _lock:
        fast["bonds"] = _macro["bonds"] or {"us10y": None, "us30y": None, "as_of": None}
        fast["sentiment"] = _macro["sentiment"] or {"vix": None, "vix_date": None}
        silver = _macro["silver"]
        fast["commodities"]["silver"] = silver.get("price") if silver else None
        fast["commodities"]["silver_rate"] = silver.get("rate") if silver else None
        fast["valuation"] = {"sp500_per": _slow.get("sp500_per"), "buffett": _slow.get("buffett")}
        _state["data"] = fast
        _state["updated_at"] = time.time()

    if (_commentary["updated_at"] == 0
            or time.time() - _commentary["updated_at"] > COMMENTARY_REFRESH_SEC):
        _refresh_commentary(fast, ai_allowed)


def _loop(ai_allowed: bool):
    while True:
        _safe(lambda: _compute(ai_allowed))
        time.sleep(REFRESH_SEC)


def start_background(ai_allowed: bool = False):
    threading.Thread(target=_loop, args=(ai_allowed,), daemon=True).start()


def get():
    with _lock:
        data = _state["data"]
        commentary = dict(_commentary)
    if not data:
        return {"available": False, "updated_at": 0}
    out = dict(data)
    out["available"] = True
    out["updated_at"] = _state["updated_at"]
    out["commentary"] = commentary.get("text")
    out["commentary_source"] = commentary.get("source")
    return out
