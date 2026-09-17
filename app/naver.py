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


def usd_krw_rate():
    """원/달러 환율.
    ⚠️ 2026-09-18 — 예전엔 finance.naver.com/marketindex 페이지를 정규식으로 파싱했는데,
    그 페이지가 이제 stock.naver.com의 새 SPA로 302 리다이렉트되면서(실측 확인) 서버가
    렌더링하는 정적 HTML 자체가 사라져 정규식이 아무것도 못 찾고 계속 None만 반환하고
    있었다(마켓 브리핑 자산시장 카드 전체와 이 함수를 쓰는 portfolio.py 미국 종목 환산이
    둘 다 조용히 깨진 원인). 새 SPA는 클라이언트 사이드에서 내부 전용 API를 호출해 대체
    가능한 공개 JSON 엔드포인트를 찾지 못했다 — 대신 이 프로젝트가 이미 국채·VIX·은·구리·
    BTC에 쓰고 있는 Yahoo Finance 비공식 차트 API로 통일. 실패하면 None(호출부인
    portfolio.py가 폴백 처리: 환율을 못 구하면 해당 미국 종목은 그 요청에서만 제외하고
    다음 조회 때 재시도)."""
    now = time.time()
    hit = _cache.get("fx:usdkrw")
    if hit and now - hit[0] < 600:
        return hit[1]
    try:
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X",
                          params={"interval": "1d", "range": "5d"}, headers=HEADERS, timeout=8)
        r.raise_for_status()
        rate = r.json()["chart"]["result"][0]["meta"].get("regularMarketPrice")
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


# 2026-09-18 — 이전엔 여기에 finance.naver.com/marketindex/ 페이지를 정규식으로 파싱하는
# market_index_page()가 있었다. 그 페이지가 stock.naver.com의 새 SPA로 리다이렉트되며
# 완전히 깨져(usd_krw_rate()와 같은 원인) 제거했다 — 환율·원자재는 이제 app/market.py의
# _refresh_macro()가 Yahoo Finance로 가져온다.


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
