# -*- coding: utf-8 -*-
"""마켓 브리핑 — 밸류에이션·투자심리·시장체력·경기신용위험·자산시장 5영역 + StockLens
자체 종합 "시장 온도" 0~100.

전부 외부 무료 소스(네이버 비공식 API, Yahoo Finance 비공식 차트 API, multpl·
currentmarketvaluation의 공개 페이지)를 조합한다 — 이 프로젝트가 이미 네이버 비공식
API 하나에 전면 의존하는 것과 같은 성격의 선택. 값을 못 구하면 그 항목만 None으로
비우고 절대 지어내지 않는다(CLAUDE.md 원칙과 동일).

⚠️ 아래는 이번에 확인/포기한 것들 — 다음에 또 시도하기 전에 먼저 볼 것:
  - CNN 공포탐욕지수: production.dataviz.cnn.io가 봇 차단(418 "I'm a teapot") — 우회
    안 하고 포기, 대신 VIX(CBOE 변동성지수)를 심리 지표로 씀.
  - FRED(fred.stlouisfed.org): 로컬에선 되는데 **오라클 배포 서버에서 연결 자체가
    막혀 있음**(status=000) — 미국채·VIX 전부 Yahoo Finance(^TNX·^TYX·^IRX·^VIX)로 통일.
  - Put/Call 비율(CBOE), AAII 개인투자자 심리조사, ICE BofA 하이일드 스프레드,
    S&P500 Forward P/E, 200일선 상회 비율, NYSE 신고가/신저가: 무료로 안정적으로
    긁을 수 있는 소스를 못 찾음(전부 403/404 또는 로그인 필요) — 스킵.
    대신 시장체력은 **코스피·코스닥 등락종목수**(네이버, 이미 확보 가능)로 대체.
"""
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from app import ai, naver, ranking
from app.analysis import to_num

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

REFRESH_SEC = 300              # 지수·환율·원자재·국내 등락(네이버) — 5분
MACRO_REFRESH_SEC = 30 * 60    # 미국채·VIX·은·구리·BTC(Yahoo) — 원본이 자주 안 바뀜
SLOW_REFRESH_SEC = 6 * 3600    # PER·CAPE·PBR·버핏지수(multpl 등) — 하루~분기 단위로만 바뀜
COMMENTARY_REFRESH_SEC = 1800  # AI/룰기반 한줄평 — 30분

_lock = threading.Lock()
_state = {"data": None, "updated_at": 0}
_macro = {"bonds": None, "sentiment": None, "commodities2": None, "crypto": None, "updated_at": 0}
_slow = {"sp500_per": None, "cape": None, "pb": None, "buffett": None, "updated_at": 0}
_commentary = {"text": None, "source": None, "updated_at": 0}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ---------------------------------------------------------------- 게이지(0~100) 산출
# 전부 "일반적으로 통용되는 참고 구간"이며 공식 통계 기관이 발표하는 임계값이 아니다
# (그런 공식 임계값 자체가 존재하지 않는 지표들 — VIX·CAPE 등은 학계·업계에서도 대략적인
# 눈대중 구간만 통용된다). 화면에도 "참고용 구간"이라고 명시한다.
def _zone_score(value, bounds):
    """bounds=[b0,b1,b2,b3] 오름차순 4개 경계 → (zone 0~4, score 0~100 연속값).
    b0=score0, b1=25, b2=50, b3=75, b3+한칸폭=100으로 선형보간, 양끝은 클램프."""
    if value is None:
        return None, None
    b0, b1, b2, b3 = bounds
    if value < b0:
        zone = 0
    elif value < b1:
        zone = 1
    elif value < b2:
        zone = 2
    elif value < b3:
        zone = 3
    else:
        zone = 4
    span = (b3 - b2) or 1
    pts = [(b0, 0), (b1, 25), (b2, 50), (b3, 75), (b3 + span, 100)]
    if value <= pts[0][0]:
        score = 0.0
    elif value >= pts[-1][0]:
        score = 100.0
    else:
        score = 50.0
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= value <= x1:
                score = y0 + (value - x0) / (x1 - x0) * (y1 - y0)
                break
    return zone, round(score, 1)


# (경계 4개, 오름차순 5단계 라벨) — 값이 커질수록 오른쪽 라벨.
_VALUATION_LABELS = ["매우 저평가", "저평가", "중립", "고평가", "매우 고평가"]
_BOUNDS = {
    "cape": ([15, 20, 28, 35], _VALUATION_LABELS),
    "pb": ([2.0, 3.0, 4.0, 5.5], _VALUATION_LABELS),
    "sp_per": ([12, 16, 22, 28], _VALUATION_LABELS),
    "kospi_per": ([7, 10, 14, 18], _VALUATION_LABELS),
    "vix": ([12, 20, 30, 40], ["극단적 낙관(과열 신호)", "안정", "중립", "불안", "공포"]),
    "advance_pct": ([30, 45, 55, 70], ["매우 약세", "약세", "중립", "강세", "매우 강세(과열)"]),
    "spread": ([-1.0, 0.0, 1.0, 2.0], ["심한 역전(경기침체 경고)", "역전", "중립", "정상", "가파른 정상"]),
}


def _gauge(kind: str, value):
    if value is None:
        return None
    bounds, labels = _BOUNDS[kind]
    zone, score = _zone_score(value, bounds)
    return {"value": value, "zone": zone, "score": score, "label": labels[zone],
            "bounds": bounds, "labels": labels}


_BUFFETT_ZONE = {
    "strongly overvalued": 4, "overvalued": 3, "modestly overvalued": 3, "fair valued": 2,
    "modestly undervalued": 1, "undervalued": 1, "strongly undervalued": 0,
}
_BUFFETT_SCORE = {
    "strongly overvalued": 96, "overvalued": 80, "modestly overvalued": 65, "fair valued": 50,
    "modestly undervalued": 35, "undervalued": 20, "strongly undervalued": 4,
}


def _buffett_gauge(buffett: dict):
    if not buffett:
        return None
    key = buffett.get("label_en", "")
    zone = _BUFFETT_ZONE.get(key, 2)
    score = _BUFFETT_SCORE.get(key, 50)
    return {"value": buffett["value"], "zone": zone, "score": score, "label": buffett["label"],
            "labels": _VALUATION_LABELS}


# ---------------------------------------------------------------- 외부 소스별 조회
def _yahoo_quote(symbol: str):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                      params={"interval": "1d", "range": "5d"}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose")
    rate = round((price - prev) / prev * 100, 2) if (price and prev) else None
    return {"price": price, "rate": rate}


_MULTPL_RE = re.compile(r'Current [^"]*? is ([\d.]+)')


def _multpl(slug: str):
    r = requests.get(f"https://www.multpl.com/{slug}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    m = _MULTPL_RE.search(r.text)
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
    label_en = m.group(2).strip().lower()
    return {
        "value": float(m.group(1)), "label": _BUFFETT_LABEL_KO.get(label_en, label_en),
        "label_en": label_en, "as_of": d.group(1) if d else None,
    }


def _kospi_avg_per():
    """StockLens 추적 PER — 공식 KOSPI 평균 PER(KRX)은 세션 인증이 필요해 못 긁는다
    (실측 확인, 403류). 대신 이 서비스가 이미 백그라운드로 채점해둔 국내 유니버스
    (app/ranking.py, 시가총액 상위 위주 182종목)로 근사치를 낸다.

    ⚠️ 개별 종목 PER을 시가총액으로 가중평균하는 방식(예전 구현)은 저이익·고PER
    종목 한둘이 평균을 크게 왜곡한다(실측 50배대까지 튐). 거래소가 실제 쓰는 방식과
    같은 "합산 시가총액 ÷ 합산 순이익"으로 바꾼다 — 종목별 순이익은 시가총액/PER로
    역산(PER=주가/EPS, 시가총액/PER=발행주식수×EPS=순이익)한다. 그래도 전종목 공식
    통계는 아니므로 화면에 "StockLens 추적종목 기준"임을 명시한다."""
    items = ranking.get("KR")["items"]
    valid = [it for it in items if it.get("per") and it["per"] > 0 and it.get("market_cap")]
    if not valid:
        return None
    total_cap = sum(it["market_cap"] for it in valid)
    total_earnings = sum(it["market_cap"] / it["per"] for it in valid)
    if total_earnings <= 0:
        return None
    return {"value": round(total_cap / total_earnings, 2), "count": len(valid)}


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


def _breadth_item(raw: dict):
    if not raw or raw.get("rise") is None:
        return None
    rise, fall, steady = raw.get("rise") or 0, raw.get("fall") or 0, raw.get("steady") or 0
    total = rise + fall + steady
    pct = round(rise / total * 100, 1) if total else None
    out = dict(raw)
    out["advance_pct"] = pct
    out["gauge"] = _gauge("advance_pct", pct)
    return out


# ---------------------------------------------------------------- 스냅샷 조립
def _build_naver():
    """네이버 기반 값만 — 지수·환율·원자재(은·구리 제외)·국내 등락종목수. 5분마다 불려도 부담 없다."""
    majors = (_safe(lambda: naver.home_majors(), {}) or {}).get("homeMajors", [])
    mkt = _safe(lambda: naver.market_index_page(), {}) or {}
    breadth = _safe(lambda: naver.index_breadth(), {}) or {}
    sp500 = _safe(lambda: _world_index_item(".INX", "S&P 500"))

    def fx(key):
        v = mkt.get(key)
        return v or {"value": None, "change": None, "rate": None}

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
            "usdkrw": fx("usdkrw"), "jpykrw100": fx("jpykrw100"), "eurkrw": fx("eurkrw"),
            "cnykrw": fx("cnykrw"), "usdjpy": fx("usdjpy"), "dxy": fx("dxy"),
        },
        "commodities": {
            "wti": fx("wti"), "gasoline": fx("gasoline"),
            "gold_intl": fx("gold_intl"), "gold_domestic": fx("gold_domestic"),
        },
        "breadth": {
            "kospi": _breadth_item(breadth.get("KOSPI")),
            "kosdaq": _breadth_item(breadth.get("KOSDAQ")),
        },
    }


def _refresh_macro():
    """미국채10·30·2년·3개월(^TNX·^TYX·2YY=F·^IRX)·VIX(^VIX)·은(SI=F)·구리(HG=F)·
    BTC(BTC-USD) — 전부 Yahoo, 30분 주기 전용 캐시."""
    symbols = {
        "us10y": "^TNX", "us30y": "^TYX", "us2y": "2YY=F", "us3m": "^IRX",
        "vix": "^VIX", "silver": "SI=F", "copper": "HG=F", "btc": "BTC-USD",
    }
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {k: ex.submit(_safe, lambda s=v: _yahoo_quote(s)) for k, v in symbols.items()}
        got = {k: f.result() for k, f in futs.items()}

    now_str = time.strftime("%Y-%m-%d")
    with _lock:
        us10y = got["us10y"].get("price") if got["us10y"] else (_macro["bonds"] or {}).get("us10y")
        us30y = got["us30y"].get("price") if got["us30y"] else (_macro["bonds"] or {}).get("us30y")
        us2y = got["us2y"].get("price") if got["us2y"] else (_macro["bonds"] or {}).get("us2y")
        us3m = got["us3m"].get("price") if got["us3m"] else (_macro["bonds"] or {}).get("us3m")
        spread_10y2y = round(us10y - us2y, 2) if (us10y is not None and us2y is not None) else None
        spread_10y3m = round(us10y - us3m, 2) if (us10y is not None and us3m is not None) else None
        _macro["bonds"] = {
            "us10y": us10y, "us30y": us30y, "us2y": us2y, "us3m": us3m,
            "spread_10y2y": spread_10y2y, "spread_10y3m": spread_10y3m,
            "spread_10y2y_gauge": _gauge("spread", spread_10y2y),
            "spread_10y3m_gauge": _gauge("spread", spread_10y3m),
            "as_of": now_str,
        }
        if got["vix"]:
            vix_val = got["vix"].get("price")
            _macro["sentiment"] = {"vix": vix_val, "vix_date": now_str, "vix_gauge": _gauge("vix", vix_val)}
        _macro["commodities2"] = {"silver": got["silver"], "copper": got["copper"]}
        _macro["crypto"] = {"btc": got["btc"]}
        _macro["updated_at"] = time.time()


def _refresh_slow():
    per = _safe(lambda: _multpl("s-p-500-pe-ratio"))
    cape = _safe(lambda: _multpl("shiller-pe"))
    pb = _safe(lambda: _multpl("s-p-500-price-to-book"))
    buffett = _safe(_buffett_indicator)
    with _lock:
        if per is not None:
            _slow["sp500_per"] = per
        if cape is not None:
            _slow["cape"] = cape
        if pb is not None:
            _slow["pb"] = pb
        if buffett is not None:
            _slow["buffett"] = buffett
        _slow["updated_at"] = time.time()


def _rule_commentary(snap: dict) -> str:
    """실제 AI 호출 없이(공개 배포 비용 보호, CLAUDE.md 5번 규칙) 수집한 숫자로만
    조립하는 한줄평 — AI_ALLOWED가 꺼진 배포본(현재 오라클 기본값)에서도 항상 뭔가는
    보여주기 위한 폴백. AI가 켜지면 ai.market_commentary()가 대신 이 자리를 채운다."""
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
    usdkrw = (snap["fx"].get("usdkrw") or {}).get("value")
    if usdkrw is not None:
        notes.append(f"원/달러 {usdkrw:,.1f}원")
    buffett = _slow.get("buffett")
    if buffett:
        notes.append(f"버핏지수 {buffett['value']:.0f}%({buffett['label']})")
    composite = snap.get("composite") or {}
    kr_temp = (composite.get("kr") or {}).get("overall")
    us_temp = (composite.get("us") or {}).get("overall")
    if kr_temp is not None:
        notes.append(f"한국시장 온도 {kr_temp}점")
    if us_temp is not None:
        notes.append(f"미국시장 온도 {us_temp}점")

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


def _round_half_up(v):
    """일반적으로 기대하는 반올림(0.5는 항상 올림) — 파이썬 내장 round()는 은행가
    반올림(2.5→2)이라 "화면 숫자 평균과 안 맞는다"는 혼란을 하나 더 만들 수 있다."""
    return None if v is None else math.floor(v + 0.5)


def _composite(valuation: dict, sentiment: dict, breadth: dict, bonds: dict):
    """StockLens 시장온도 — 한국/미국을 하나로 섞지 않고 분리한다.
    예전엔 밸류에이션(대부분 미국) · 심리(VIX, 미국) · 체력(코스피·코스닥, 한국) ·
    신용(미국 금리차)을 통째로 평균해 "이게 한국시장 온도인지 글로벌 온도인지
    불명확하다"는 지적을 받았다. 또 서브점수를 소수로 들고 있다가 화면 표시 시점에만
    반올림하다 보니(예: 92.6→93) "화면에 보이는 서브점수 평균과 종합점수가 안 맞는다"는
    지적도 받았다 — 그래서 여기서부터 정수로 반올림해, 화면에 보이는 숫자 그대로
    평균해도 항상 종합점수와 일치하게 만든다.

    한국 온도는 코스피 PER(밸류에이션)과 코스피·코스닥 등락비율(체력) 2개뿐이라
    데이터가 얕다 — 국내 수급·신용잔고·거래대금 지표를 못 구해서다(모듈 docstring
    참고). 미국 온도는 밸류에이션 4종·VIX·금리차 신용까지 상대적으로 두텁다."""
    def avg(vals):
        vals = [v for v in vals if v is not None]
        return _round_half_up(sum(vals) / len(vals)) if vals else None

    def breadth_score(key):
        # .get("gauge", {}) 의 함정: "gauge" 키가 존재하는데 값이 None이면(등락비율
        # 계산 실패 시 실제로 벌어짐) 기본값 {}가 아니라 그 None이 그대로 반환돼
        # 다음 .get("score")에서 죽는다 — 반드시 or {}로 한 번 더 걸러야 한다.
        b = breadth.get(key) or {}
        g = b.get("gauge") or {}
        return _round_half_up(g.get("score"))

    kr_valuation = _round_half_up((valuation.get("kospi_per") or {}).get("score"))
    kr_strength = avg([breadth_score("kospi"), breadth_score("kosdaq")])
    kr_overall = avg([kr_valuation, kr_strength])

    us_valuation = avg([_round_half_up(g["score"]) for k, g in valuation.items() if k != "kospi_per" and g])
    vix_g = sentiment.get("vix_gauge")
    us_sentiment = _round_half_up(100 - vix_g["score"]) if vix_g else None   # VIX 낮을수록 "과열/낙관"
    g1, g2 = bonds.get("spread_10y2y_gauge"), bonds.get("spread_10y3m_gauge")
    us_credit = avg([
        _round_half_up(100 - g1["score"]) if g1 else None,
        _round_half_up(100 - g2["score"]) if g2 else None,
    ])
    us_overall = avg([us_valuation, us_sentiment, us_credit])

    return {
        "kr": {"valuation": kr_valuation, "strength": kr_strength, "overall": kr_overall},
        "us": {"valuation": us_valuation, "sentiment": us_sentiment, "credit": us_credit, "overall": us_overall},
    }


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

    # ranking.py 캐시를 재사용할 뿐 네트워크 호출이 없어(코스피 평균 PER) 6시간 slow
    # 캐시에 넣을 이유가 없다 — 매 사이클(5분)마다 그냥 새로 계산한다. ranking의 첫 전체
    # 계산이 아직 안 끝났으면(서버 막 기동 직후) None이 나오고, 몇 분 뒤 자동으로 채워진다.
    kospi_per = _safe(_kospi_avg_per)

    with _lock:
        fast["bonds"] = _macro["bonds"] or {}
        fast["sentiment"] = _macro["sentiment"] or {"vix": None, "vix_date": None, "vix_gauge": None}
        cmd2 = _macro["commodities2"] or {}
        silver, copper = cmd2.get("silver"), cmd2.get("copper")
        fast["commodities"]["silver"] = {"value": silver.get("price") if silver else None,
                                          "rate": silver.get("rate") if silver else None}
        fast["commodities"]["copper"] = {"value": copper.get("price") if copper else None,
                                          "rate": copper.get("rate") if copper else None}
        btc = (_macro["crypto"] or {}).get("btc")
        fast["crypto"] = {"btc": {"value": btc.get("price") if btc else None,
                                   "rate": btc.get("rate") if btc else None}}

        buffett = _slow.get("buffett")
        fast["valuation"] = {
            "buffett": _buffett_gauge(buffett) if buffett else None,
            "cape": _gauge("cape", _slow.get("cape")),
            "pb": _gauge("pb", _slow.get("pb")),
            "sp500_per": _gauge("sp_per", _slow.get("sp500_per")),
            "kospi_per": _gauge("kospi_per", (kospi_per or {}).get("value")),
        }
        fast["valuation_raw"] = {
            "buffett_as_of": buffett.get("as_of") if buffett else None,
            "kospi_per_count": (kospi_per or {}).get("count"),
        }
        fast["composite"] = _composite(fast["valuation"], fast["sentiment"], fast["breadth"], fast["bonds"])

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
    out["commentary_updated_at"] = commentary.get("updated_at") or 0
    return out
