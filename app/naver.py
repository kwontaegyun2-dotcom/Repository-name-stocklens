# -*- coding: utf-8 -*-
"""네이버 증권 비공식 API 클라이언트 — 국내(코스피/코스닥) + 미국주식.

시장 자동 감지: 6자리 숫자=국내(005930), 그 외=미국 reutersCode(AAPL.O).
"""
import re
import time
from datetime import datetime, timedelta

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_cache: dict = {}

M = "https://m.stock.naver.com/api"   # 국내
A = "https://api.stock.naver.com"     # 해외


def is_us(code: str) -> bool:
    return not str(code).isdigit()


def _get(url: str, ttl: int = 60):
    now = time.time()
    hit = _cache.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    _cache[url] = (now, data)
    return data


def search(query: str, market: str = None):
    """market: 'KR'|'US'|None(둘 다)"""
    url = f"https://ac.stock.naver.com/ac?q={requests.utils.quote(query)}&target=stock"
    data = _get(url, ttl=3600)
    items = []
    for it in data.get("items", []):
        nat = it.get("nationCode")
        if nat == "KOR":
            mk, code = "KR", it["code"]
        elif nat == "USA":
            mk, code = "US", it.get("reutersCode") or it["code"]
        else:
            continue
        if market and mk != market:
            continue
        items.append({
            "code": code,
            "name": it["name"],
            "market": it.get("typeName", ""),
            "nation": mk,
        })
    return items[:12]


def basic(code: str):
    # ttl을 짧게 유지 — 시세 뱃지가 "실시간이 아닌 것 같다"는 지적(2026-08-20)이 있었다.
    # 이 캐시는 서버 전체가 공유하므로(방문자 수와 무관하게 코드당 최대 초당 1회 상한),
    # ttl을 낮춰도 네이버 쪽 부하는 늘지 않고 화면 갱신 지연만 줄어든다.
    if is_us(code):
        return _get(f"{A}/stock/{code}/basic", ttl=2)
    return _get(f"{M}/stock/{code}/basic", ttl=2)


def integration(code: str):
    if is_us(code):
        return _get(f"{A}/stock/{code}/integration", ttl=120)
    return _get(f"{M}/stock/{code}/integration", ttl=120)


def finance(code: str, period: str = "annual"):
    if is_us(code):
        return _get(f"{A}/stock/{code}/finance/{period}", ttl=3600)
    return _get(f"{M}/stock/{code}/finance/{period}", ttl=3600)


def news(code: str, size: int = 20):
    base = A if is_us(code) else M
    data = _get(f"{base}/news/stock/{code}?pageSize={size}&page=1", ttl=300)
    items = []
    for group in data:
        for it in group.get("items", []):
            items.append({
                "title": it.get("titleFull") or it.get("title", ""),
                "body": it.get("body", ""),
                "press": it.get("officeName", ""),
                "datetime": it.get("datetime", ""),
                "url": it.get("mobileNewsUrl", ""),
            })
    return items


def research(code: str, size: int = 10):
    if is_us(code):
        return []   # 미국은 국내 증권사 리서치 목록 없음
    data = _get(f"{M}/research/stock/{code}?pageSize={size}&page=1", ttl=3600)
    return [{
        "title": it.get("title", ""),
        "broker": it.get("brokerName", ""),
        "date": it.get("writeDate", ""),
        "preview": it.get("previewContent", ""),
    } for it in data]


def trend(code: str):
    """외국인/기관/개인 매매 동향 (국내 전용).
    ⚠️ pageSize 파라미터 없이 호출하면 네이버가 최근 10영업일치만 준다(트레이딩엔진
    설계서 지적 — 수급 오더플로우·다이버전스 계산엔 더 긴 시계열이 필요). 이 엔드포인트는
    실측 결과 pageSize=61 이상은 400을 반환하고 page 파라미터는 더 과거로 페이징되지
    않아 60이 사실상 상한이다(설계서의 120일 목표는 이 API로는 달성 불가 — 60이 최대)."""
    if is_us(code):
        return []
    return _get(f"{M}/stock/{code}/trend?pageSize=60&page=1", ttl=600)


_FX_RE = re.compile(r'FX_USDKRW".*?<span class="value">([\d,]+\.?\d*)</span>', re.S)


def usd_krw_rate():
    """원/달러 환율(하나은행 고시 기준, finance.naver.com/marketindex 페이지를 파싱).
    전용 API 엔드포인트가 없어(시도해본 후보들 전부 404/409) HTML을 정규식으로 읽는다 —
    candles()의 fchart 파싱과 같은 방식. 실패하면 None(호출부인 portfolio.py가 폴백 처리:
    환율을 못 구하면 해당 미국 종목은 그 요청에서만 제외하고 다음 조회 때 재시도)."""
    now = time.time()
    hit = _cache.get("fx:usdkrw")
    if hit and now - hit[0] < 600:
        return hit[1]
    try:
        r = requests.get("https://finance.naver.com/marketindex/", headers=HEADERS, timeout=8)
        r.raise_for_status()
        m = _FX_RE.search(r.text)
        rate = float(m.group(1).replace(",", "")) if m else None
    except Exception:
        rate = None
    if rate is not None:
        _cache["fx:usdkrw"] = (now, rate)
    return rate


def home_majors():
    """국내+해외 주요지수 요약(코스피·코스닥·다우·나스닥·상해·니케이 등) — 마켓 브리핑용."""
    return _get(f"{M}/home/majors", ttl=60)


def world_index(reuters_code: str):
    """해외지수 상세 — S&P500(.INX)·다우(.DJI)·나스닥종합(.IXIC)·니케이(.N225) 등.
    실측: 국내 지수처럼 m.stock.naver.com이 아니라 api.stock.naver.com/index/{code}/basic
    경로에서만 응답한다(다른 경로는 전부 404/409)."""
    return _get(f"https://api.stock.naver.com/index/{reuters_code}/basic", ttl=60)


# finance.naver.com/marketindex/ 페이지의 각 항목은 <a ... class="head usd" ...><h3 class=
# "h_lst"><span class="blind">미국 USD</span></h3> … <div class="head_info point_up|point_dn|
# point_same">…<span class="value">값</span>…<span class="change">전일대비 절대값</span> 구조다.
#
# ⚠️ 실측(2026-08-25)에서 라이브(오라클)가 원/달러 자리에 엔화 값을 담는 등 항목이 밀린 채
# 서비스되고 있었다 — 두 가지 원인이 겹친 것이었다.
#   1) 예전엔 항목 순서가 항상 고정이라 보고 순번(zip)으로만 매핑했는데, 페이지에 뜨는
#      항목 수가 어떤 이유로든 기대보다 하나 적어지는 순간 그 뒤로 전체가 밀린다.
#      → class="head <이름>"(marketindexCd와 1:1로 고정된 식별자)으로 매핑하도록 변경.
#   2) 그런데 클래스로 찾아도, "class=\"head X\" ... 다음에 나오는 head_info 블록"을
#      정규식 하나로 통째로 매칭(.*?로 다음 항목까지 건너뛸 수 있음)하면, X 자신의
#      head_info가 기대한 형태(point_up/point_dn/point_same)가 아닐 때 정규식이 X를
#      건너뛰고 그다음 항목(X+1)의 head_info를 X의 값으로 잘못 붙잡는다. 실측으로 확인한
#      트리거: 오늘 등락이 정확히 0.00일 때 원/달러 항목만 class="head_info head_info"라는
#      (point_up/dn/same이 아닌) 특이 케이스를 쓴다 — 다른 항목의 "보합"은 point_same을
#      정상적으로 쓰는데 원/달러만 이렇게 나온 사례를 실측함(Naver 쪽 템플릿 특이사항으로
#      추정, 원인까지는 알 수 없음). → head_info 뒤에 오는 방향 토큰에 head_info 자체도
#      허용해 이 케이스를 "보합(변화 없음)"으로 처리하고, 그래도 못 찾으면(진짜 형식이
#      다른 경우) 그 항목만 조용히 비우지 다른 항목 값을 훔쳐오지 않도록 앵커(class="head
#      X")를 기준으로 페이지를 항목별로 먼저 잘라(split) 각 조각 안에서만 값을 찾는다 —
#      한 항목이 통째로 깨져도 그 항목만 빠질 뿐 옆 항목을 오염시키지 않는다.
_MKT_ANCHOR_RE = re.compile(r'class="head (\w+)"')
_MKT_VALUE_RE = re.compile(
    r'<div class="head_info (point_up|point_dn|point_same|head_info)">\s*'
    r'<span class="value">([\d,]+\.?\d*)</span>.*?'
    r'<span class="change">\s*([\d,]+\.?\d*)</span>', re.S)
_MKT_CLASS_KEYS = {
    "usd": "usdkrw", "jpy": "jpykrw100", "eur": "eurkrw", "cny": "cnykrw",
    "jpy_usd": "usdjpy", "usd_eur": "eurusd", "usd_gbp": "gbpusd", "usd_idx": "dxy",
    "wti": "wti", "gasoline": "gasoline", "gold_inter": "gold_intl", "gold_domestic": "gold_domestic",
}


def market_index_page():
    """환율·유가·금 시세 12종(값+전일대비 등락) — 전용 API가 없어
    finance.naver.com/marketindex/를 파싱한다(usd_krw_rate()와 같은 방식, 한 번의
    요청으로 12개를 모두 얻도록 확장). 각 항목은 {value, change, rate}."""
    now = time.time()
    hit = _cache.get("mktidx:page")
    if hit and now - hit[0] < 300:
        return hit[1]
    out = {}
    try:
        r = requests.get("https://finance.naver.com/marketindex/", headers=HEADERS, timeout=8)
        r.raise_for_status()
        parts = _MKT_ANCHOR_RE.split(r.text)
        # split 결과는 [머리말, 클래스1, 조각1, 클래스2, 조각2, ...] 형태다.
        for i in range(1, len(parts), 2):
            key = _MKT_CLASS_KEYS.get(parts[i])
            if not key:
                continue
            chunk = parts[i + 1] if i + 1 < len(parts) else ""
            m = _MKT_VALUE_RE.search(chunk)
            if not m:
                continue
            direction, value_s, change_s = m.groups()
            value = float(value_s.replace(",", ""))
            change = float(change_s.replace(",", ""))
            if direction == "point_dn":
                change = -change
            prev = value - change
            rate = round(change / prev * 100, 2) if prev else None
            out[key] = {"value": value, "change": round(change, 4), "rate": rate}
    except Exception:
        out = {}
    if out:
        _cache["mktidx:page"] = (now, out)
    return out


def index_breadth():
    """코스피·코스닥 상승/하락/보합·상한가/하한가 종목수(시장 체력 지표용).
    m.stock.naver.com/api/index/majors가 국내 지수 상세를 한 번에 준다(KOSPI·KOSDAQ 등)."""
    data = _get(f"{M}/index/majors", ttl=60)
    out = {}
    for it in data or []:
        code = it.get("itemCode")
        if code in ("KOSPI", "KOSDAQ"):
            out[code] = {
                "rise": it.get("riseCount"), "fall": it.get("fallCount"),
                "steady": it.get("steadyCount"), "upper": it.get("upperCount"),
                "lower": it.get("lowerCount"),
            }
    return out


_ITEM_RE = re.compile(r'<item data="([^"]+)"')


def _resample(daily, timeframe):
    """일봉 리스트 → 주봉/월봉 집계 (미국용 — 네이버가 미국 주/월봉을 안 줌).
    주: ISO 주차 / 월: 연-월 기준. OHLC 규칙(시=첫날 시가, 고/저=구간 max/min,
    종=마지막날 종가, 거래량=합)."""
    if timeframe == "day" or not daily:
        return daily
    buckets = {}
    order = []
    for c in daily:
        dt = c["date"]           # YYYYMMDD
        y, m, d = int(dt[:4]), int(dt[4:6]), int(dt[6:8])
        if timeframe == "month":
            key = f"{y:04d}{m:02d}"
        else:  # week — ISO 주차
            iso = datetime(y, m, d).isocalendar()
            key = f"{iso[0]:04d}W{iso[1]:02d}"
        if key not in buckets:
            buckets[key] = {"date": dt, "open": c["open"], "high": c["high"],
                            "low": c["low"], "close": c["close"], "volume": c["volume"]}
            order.append(key)
        else:
            b = buckets[key]
            b["high"] = max(b["high"], c["high"])
            b["low"] = min(b["low"], c["low"])
            b["close"] = c["close"]
            b["volume"] += c["volume"]
            b["date"] = dt          # 구간 마지막 날짜로 표기
    return [buckets[k] for k in order]


def candles(code: str, count: int = 260, timeframe: str = "day"):
    """캔들 → [{date, open, high, low, close, volume}] (오름차순).
    timeframe: day | week | month"""
    if is_us(code):
        # 미국은 네이버가 주/월봉을 안 줘서 일봉을 받아 리샘플. 넉넉히 받는다.
        need = count if timeframe == "day" else count * (7 if timeframe == "week" else 24)
        daily = _us_candles(code, min(need, 1300))
        return _resample(daily, timeframe)
    url = (f"https://fchart.stock.naver.com/sise.nhn?symbol={code}"
           f"&timeframe={timeframe}&count={count}&requestType=0")
    key = f"candle:{url}"
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < 300:
        return hit[1]
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    out = []
    for m in _ITEM_RE.finditer(r.text):
        parts = m.group(1).split("|")
        if len(parts) < 6:
            continue
        try:
            out.append({
                "date": parts[0],
                "open": float(parts[1]), "high": float(parts[2]),
                "low": float(parts[3]), "close": float(parts[4]),
                "volume": float(parts[5]),
            })
        except ValueError:
            continue
    _cache[key] = (now, out)
    return out


def index_candles(symbol: str = "KOSPI", count: int = 1300):
    """지수 일봉 (상대강도 벤치마크용). 국내 지수는 fchart 로 조회 가능.
    symbol: KOSPI | KOSDAQ | KPI200"""
    url = (f"https://fchart.stock.naver.com/sise.nhn?symbol={symbol}"
           f"&timeframe=day&count={count}&requestType=0")
    key = f"idx:{url}"
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < 1800:
        return hit[1]
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    out = []
    for m in _ITEM_RE.finditer(r.text):
        parts = m.group(1).split("|")
        if len(parts) < 5:
            continue
        try:
            out.append(float(parts[4]))     # 종가만 필요
        except ValueError:
            continue
    _cache[key] = (now, out)
    return out


def _us_candles(rc: str, count: int):
    key = f"uscandle:{rc}:{count}"
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < 300:
        return hit[1]
    end = datetime.now()
    start = end - timedelta(days=int(count * 1.6) + 40)   # 거래일→달력일 여유
    url = (f"{A}/chart/foreign/item/{rc}/day"
           f"?startDateTime={start:%Y%m%d}&endDateTime={end:%Y%m%d}")
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    out = []
    for it in r.json():
        try:
            out.append({
                "date": it["localDate"],
                "open": float(it["openPrice"]), "high": float(it["highPrice"]),
                "low": float(it["lowPrice"]), "close": float(it["closePrice"]),
                "volume": float(it.get("accumulatedTradingVolume") or 0),
            })
        except (ValueError, KeyError, TypeError):
            continue
    out = out[-count:]
    _cache[key] = (now, out)
    return out
