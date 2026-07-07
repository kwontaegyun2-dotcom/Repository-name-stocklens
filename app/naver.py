# -*- coding: utf-8 -*-
"""네이버 증권 비공식 API 클라이언트 (무료 데이터 소스)."""
import re
import time
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_cache: dict = {}


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


def search(query: str):
    url = f"https://ac.stock.naver.com/ac?q={requests.utils.quote(query)}&target=stock"
    data = _get(url, ttl=3600)
    items = []
    for it in data.get("items", []):
        if it.get("nationCode") != "KOR":
            continue
        items.append({
            "code": it["code"],
            "name": it["name"],
            "market": it.get("typeName", ""),
        })
    return items[:10]


def basic(code: str):
    return _get(f"https://m.stock.naver.com/api/stock/{code}/basic", ttl=5)


def integration(code: str):
    return _get(f"https://m.stock.naver.com/api/stock/{code}/integration", ttl=120)


def finance(code: str, period: str = "annual"):
    return _get(f"https://m.stock.naver.com/api/stock/{code}/finance/{period}", ttl=3600)


def news(code: str, size: int = 20):
    data = _get(f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={size}&page=1", ttl=300)
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
    data = _get(f"https://m.stock.naver.com/api/research/stock/{code}?pageSize={size}&page=1", ttl=3600)
    items = []
    for it in data:
        items.append({
            "title": it.get("title", ""),
            "broker": it.get("brokerName", ""),
            "date": it.get("writeDate", ""),
            "preview": it.get("previewContent", ""),
        })
    return items


def trend(code: str):
    """외국인/기관/개인 매매 동향 (최근 거래일 목록)."""
    return _get(f"https://m.stock.naver.com/api/stock/{code}/trend", ttl=600)


_ITEM_RE = re.compile(r'<item data="([^"]+)"')


def candles(code: str, count: int = 260, timeframe: str = "day"):
    """일/주/월봉. fchart XML → [{date, open, high, low, close, volume}]"""
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
                "open": float(parts[1]),
                "high": float(parts[2]),
                "low": float(parts[3]),
                "close": float(parts[4]),
                "volume": float(parts[5]),
            })
        except ValueError:
            continue
    _cache[key] = (now, out)
    return out
