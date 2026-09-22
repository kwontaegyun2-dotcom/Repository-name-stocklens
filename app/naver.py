# -*- coding: utf-8 -*-
"""네이버 증권 비공식 API 클라이언트 — 국내(코스피/코스닥) + 미국주식.

시장 자동 감지: 6자리 숫자=국내(005930), 그 외=미국 reutersCode(AAPL.O).
"""
import re
import threading
import time
from datetime import datetime, timedelta

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_cache: dict = {}

# ⚠️ 2026-09-22 — 태블릿에서 포트폴리오 로딩 1분+ 재지적(강한 항의). 실측 원인 둘:
# 1) 이 _cache가 TTL을 "읽을 때만" 검사하고 지난 항목을 절대 지우지 않아 무한히 커졌다
#    (검색 자동완성은 사용자가 입력하는 글자 조합마다 새 키가 생김·차트는 종목×기간
#    조합마다 새 키가 생김 — 며칠 실서비스 트래픽이면 수백~수천 개 고유 키가 쌓임).
#    실측: 배포 3일 뒤 오라클 프로세스 RSS가 87MB→480MB로 자라며 스왑까지 점유
#    (VmSwap 186MB) — 956MB짜리 공유 VM에서 스왑이 잡히면 그 자체로 전체가 느려진다.
# 2) 포트폴리오 조회는 보유종목마다 analyze()를 병렬로 돌리고, analyze() 자신도 내부에서
#    또 병렬로 하위 조회를 돌린다(중첩 스레드풀). 보유종목 여러 개가 동시에 캐시미스면
#    실제 동시 네이버 요청이 수십 개까지 치솟는데, 이미 CPU 스틸타임이 70~80%인 이
#    공유 VM에서는 요청을 몰아서 쏘는 것보다 줄 세우는 쪽이 전체적으로 더 빠르다
#    (스레드 컨텍스트 스위칭·GIL 경합 자체가 비용이라 무제한 동시성이 오히려 손해).
# 두 가지 다 고친다: 캐시는 크기 상한+오래된 항목 정리, 실제 HTTP 요청은 전역
# 세마포어로 동시 개수를 제한한다.
_CACHE_MAX = 1500                 # 이 개수를 넘으면 가장 오래 전에 채워진 항목부터 정리
_CACHE_TRIM_TO = 1200              # 정리할 때 이 개수까지 줄인다(매번 딱 1개씩만 지우지 않도록 여유)
_HTTP_SEMAPHORE = threading.Semaphore(6)   # 앱 전체에서 네이버로 나가는 동시 요청 상한


def _cache_set(key: str, value):
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX:
        # dict는 삽입 순서를 보존하므로 앞에서부터 지우면 곧 "가장 오래 전에 넣은 것부터"가
        # 된다 — true LRU는 아니지만(마지막 접근이 아니라 마지막 삽입 기준) 추가 자료구조
        # 없이 무한 성장만 막으면 되는 목적엔 충분하다.
        excess = len(_cache) - _CACHE_TRIM_TO
        for k in list(_cache.keys())[:excess]:
            _cache.pop(k, None)


def _http_get(url: str, **kwargs):
    """requests.get()을 그대로 감싸되, 앱 전체 동시 네이버 요청 수를 제한한다."""
    with _HTTP_SEMAPHORE:
        return requests.get(url, **kwargs)


M = "https://m.stock.naver.com/api"   # 국내
A = "https://api.stock.naver.com"     # 해외


def is_us(code: str) -> bool:
    """국내(코스피/코스닥) vs 미국 판별.
    ⚠️ 2026-09-18 — 예전엔 "숫자면 국내, 아니면 미국"이었는데, 최근 상장되는 국내
    액티브 ETF는 코드에 영문이 섞인다(예: TIGER 삼성전자단일종목레버리지=0195R0,
    TIGER 리츠부동산인프라TOP10액티브=0086B0). 이런 코드가 숫자만 있지 않다는
    이유로 미국 API(api.stock.naver.com)로 라우팅되어 404/409가 났고, 포트폴리오에서
    "미지원 종목(ETF·ETN 등으로 추정)"으로 통째로 빠지는 원인이었다(실측 확인:
    ETF 자체가 미지원이 아니라 라우팅 오류). 네이버 검색 API를 실측해보면 국내
    코드는 항상 정확히 6자리(숫자 또는 숫자+영문 혼용, 점(.) 없음)이고, 해외
    reutersCode는 항상 "TICKER.거래소"(AAPL.O, 164A.T 등) 형태로 점을 포함한다 —
    이 차이로 판별한다. "SPY" 같은 내부 벤치마크용 짧은 티커(길이 6 아님)는
    그대로 미국으로 분류된다."""
    code = str(code)
    if len(code) == 6 and "." not in code:
        return False
    return True


def _get(url: str, ttl: int = 60):
    now = time.time()
    hit = _cache.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    r = _http_get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()
    _cache_set(url, data)
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
        r = _http_get("https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X",
                       params={"interval": "1d", "range": "5d"}, headers=HEADERS, timeout=8)
        r.raise_for_status()
        rate = r.json()["chart"]["result"][0]["meta"].get("regularMarketPrice")
    except Exception:
        rate = None
    if rate is not None:
        _cache_set("fx:usdkrw", rate)
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
    r = _http_get(url, headers=HEADERS, timeout=10)
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
    _cache_set(key, out)
    return out


def index_candles(symbol: str = "KOSPI", count: int = 1300):
    """지수 일봉 (상대강도 벤치마크용). 국내 지수는 fchart 로 조회 가능.
    symbol: KOSPI | KOSDAQ | KPI200

    ⚠️ 2026-09-18 — 예전엔 [float, ...](종가만)을 반환했는데, candles()는 같은 fchart
    엔드포인트·같은 파싱을 쓰면서 [{date,open,high,low,close,volume}, ...]를 반환해
    두 함수의 반환 형태가 달랐다. 호출부(app/backtest.py `_bench_price()`)가 이 차이를
    모르고 index_candles() 결과에도 candles()처럼 c[-1]["close"]를 시도해 TypeError가
    나고 except로 조용히 삼켜져, 코스피 벤치마크가 한 번도 계산된 적이 없었다(실측:
    backtest_snapshots.jsonl의 KR 레코드 1067건 전부 bench=null). candles()와 같은
    dict 형태로 통일해 이 함정 자체를 없앤다 — 날짜(parts[0])도 원본 데이터에 이미
    있었는데 버려지고 있었을 뿐이라 추가 조회 없이 그대로 채울 수 있다."""
    url = (f"https://fchart.stock.naver.com/sise.nhn?symbol={symbol}"
           f"&timeframe=day&count={count}&requestType=0")
    key = f"idx:{url}"
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < 1800:
        return hit[1]
    r = _http_get(url, headers=HEADERS, timeout=10)
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
    _cache_set(key, out)
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
    r = _http_get(url, headers=HEADERS, timeout=10)
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
    _cache_set(key, out)
    return out
