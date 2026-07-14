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
    if is_us(code):
        return _get(f"{A}/stock/{code}/basic", ttl=5)
    return _get(f"{M}/stock/{code}/basic", ttl=5)


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
    """외국인/기관/개인 매매 동향 (국내 전용)."""
    if is_us(code):
        return []
    return _get(f"{M}/stock/{code}/trend", ttl=600)


_ITEM_RE = re.compile(r'<item data="([^"]+)"')


def candles(code: str, count: int = 260, timeframe: str = "day"):
    """일봉 → [{date, open, high, low, close, volume}] (오름차순)."""
    if is_us(code):
        return _us_candles(code, count)
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
