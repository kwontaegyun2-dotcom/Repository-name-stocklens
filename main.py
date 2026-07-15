# -*- coding: utf-8 -*-
"""StockLens — 국내 주식 종합 분석 대시보드 서버."""
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import naver, kis, analysis, ai, ranking

BASE = Path(__file__).resolve().parent
app = FastAPI(title="StockLens")


@app.on_event("startup")
def _startup():
    ranking.start_background()

# 공개 배포 모드: 개인 KIS 키 저장 금지, AI 리포트 남용 방지
PUBLIC = os.environ.get("STOCKLENS_PUBLIC") == "1"
# 공개 모드에서 AI 리포트 허용 여부 (기본 차단 — 소유자 비용 보호)
AI_ALLOWED = (not PUBLIC) or os.environ.get("STOCKLENS_ALLOW_AI") == "1"

# 간단한 IP별 요청 제한 (공개 모드 남용 방지)
_hits: dict = {}


def _rate_limit(request, limit: int = 30, window: int = 60):
    if not PUBLIC or request is None:
        return
    ip = request.client.host if request.client else "?"
    now = time.time()
    bucket = [t for t in _hits.get(ip, []) if now - t < window]
    if len(bucket) >= limit:
        raise HTTPException(429, "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
    bucket.append(now)
    _hits[ip] = bucket


# ---------------------------------------------------------------- search
@app.get("/api/search")
def api_search(q: str, request: Request, market: str = None):
    _rate_limit(request, limit=60, window=60)
    try:
        return {"items": naver.search(q, market)}
    except Exception as e:
        raise HTTPException(502, f"검색 실패: {e}")


# ---------------------------------------------------------------- ranking
@app.get("/api/ranking")
def api_ranking(market: str = "KR", sector: str = None, request: Request = None):
    _rate_limit(request, limit=60, window=60)
    return ranking.get(market, sector)


# ---------------------------------------------------------------- realtime price
@app.get("/api/price/{code}")
def api_price(code: str):
    # 1순위: 한국투자증권 API (국내·설정 시)
    if not naver.is_us(code) and kis.is_configured():
        try:
            return kis.current_price(code)
        except Exception:
            pass  # KIS 실패 시 네이버 폴백
    try:
        b = naver.basic(code)
        return {
            "source": "NAVER",
            "price": analysis.to_num(b.get("closePrice")),
            "change": analysis.to_num(b.get("compareToPreviousClosePrice")),
            "rate": analysis.to_num(b.get("fluctuationsRatio")),
            "direction": (b.get("compareToPreviousPrice") or {}).get("name"),
            "market_status": b.get("marketStatus"),
            "currency": (b.get("currencyType") or {}).get("code") or "KRW",
            "traded_at": b.get("localTradedAt"),
        }
    except Exception as e:
        raise HTTPException(502, f"시세 조회 실패: {e}")


# ---------------------------------------------------------------- full analysis
@app.get("/api/analyze/{code}")
def api_analyze(code: str, request: Request = None):
    _rate_limit(request, limit=30, window=60)
    try:
        b = naver.basic(code)
    except Exception as e:
        raise HTTPException(404, f"종목을 찾을 수 없습니다: {e}")

    us = naver.is_us(code)
    name = b.get("stockName", code)
    price = analysis.to_num(b.get("closePrice"))
    currency = (b.get("currencyType") or {}).get("code") or "KRW"

    def safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    integ = safe(lambda: naver.integration(code), {})
    fin_annual = safe(lambda: naver.finance(code, "annual"), {})
    news_items = safe(lambda: naver.news(code, 20), [])
    research_items = safe(lambda: naver.research(code, 10), [])
    deal_trend = safe(lambda: naver.trend(code), [])
    candle_data = safe(lambda: naver.candles(code, 260), [])

    # 지표 소스: 미국=basic.stockItemTotalInfos, 국내=integration.totalInfos
    src = (b.get("stockItemTotalInfos") if us else integ.get("totalInfos")) or []
    infos = {i.get("code"): i.get("value") for i in src}

    tech = analysis.technical_analysis(candle_data)
    bt = analysis.backtest(candle_data)
    fund = analysis.fundamental_analysis(infos, fin_annual, market="US" if us else "KR")
    senti = analysis.news_sentiment(news_items)
    cons = analysis.consensus_info(integ, price)
    total = analysis.total_evaluation(fund, tech, senti, cons, deal_trend)
    opinion = analysis.build_opinion(name, fund, tech, senti, cons, total)

    # 목표주가: 컨센서스 우선, 기술적 목표 병기
    targets = {
        "consensus": cons.get("target_price"),
        "consensus_upside": cons.get("upside"),
        "technical": tech.get("tech_target") if tech.get("available") else None,
    }
    if targets["technical"] and price:
        targets["technical_upside"] = round((targets["technical"] - price) / price * 100, 1)

    # 동일업종 비교 (상위 5개) — 미국은 industryCompareInfo.globalStocks
    raw_peers = integ.get("industryCompareInfo")
    if isinstance(raw_peers, dict):
        raw_peers = raw_peers.get("globalStocks") or raw_peers.get("domesticStocks") or []
    peers = []
    for p in (raw_peers or [])[:6]:
        peers.append({
            "name": p.get("stockName"),
            "code": p.get("itemCode") or p.get("reutersCode"),
            "price": analysis.to_num(p.get("closePrice")),
            "rate": analysis.to_num(p.get("fluctuationsRatio")),
            "market_cap": analysis.parse_eok(p.get("marketValue")) if us else analysis.to_num(p.get("marketValue")),
        })

    # 수급 요약 테이블 (최근 10일)
    flows = []
    for d in (deal_trend or [])[:10]:
        flows.append({
            "date": d.get("bizdate"),
            "close": analysis.to_num(d.get("closePrice")),
            "foreigner": analysis.to_num(d.get("foreignerPureBuyQuant")),
            "foreigner_ratio": d.get("foreignerHoldRatio"),
            "organ": analysis.to_num(d.get("organPureBuyQuant")),
            "individual": analysis.to_num(d.get("individualPureBuyQuant")),
        })

    return {
        "code": code,
        "name": name,
        "nation": "US" if us else "KR",
        "currency": currency,
        "market": b.get("stockExchangeName") or (b.get("stockExchangeType") or {}).get("nameKor"),
        "logo": b.get("itemLogoPngUrl"),
        "price": price,
        "change": analysis.to_num(b.get("compareToPreviousClosePrice")),
        "rate": analysis.to_num(b.get("fluctuationsRatio")),
        "direction": (b.get("compareToPreviousPrice") or {}).get("name"),
        "market_status": b.get("marketStatus"),
        "total": total,
        "opinion": opinion,
        "metrics": fund["metrics"],
        "finance_rows": fund["finance_rows"],
        "technical": tech,
        "backtest": bt,
        "targets": targets,
        "consensus": cons,
        "sentiment": {"score": senti["score"], "label": senti["label"]},
        "news": senti["items"][:12],
        "research": research_items,
        "peers": peers,
        "flows": flows,
        "candles": candle_data,
        "kis_enabled": kis.is_configured() and not PUBLIC,
        "ai_enabled": ai.available() and AI_ALLOWED,
        "public": PUBLIC,
    }


# ---------------------------------------------------------------- KIS config
class KisConfig(BaseModel):
    app_key: str
    app_secret: str
    is_paper: bool = False


@app.get("/api/kis/status")
def kis_status():
    return {"configured": kis.is_configured()}


@app.post("/api/kis/config")
def kis_config(cfg: KisConfig):
    if PUBLIC:
        raise HTTPException(403, "공개 배포 환경에서는 보안상 KIS 키 저장을 지원하지 않습니다. "
                                 "실시간 시세는 개인 PC에서 실행할 때만 사용하세요.")
    kis.save_config(cfg.app_key.strip(), cfg.app_secret.strip(), cfg.is_paper)
    # 즉시 검증: 삼성전자 시세 1회 조회
    try:
        kis.current_price("005930")
        return {"ok": True, "message": "한국투자증권 API 연결 성공! 이제 실시간 시세가 KIS로 제공됩니다."}
    except Exception as e:
        return {"ok": False, "message": f"저장했지만 연결 확인 실패: {e}"}


# ---------------------------------------------------------------- AI report
@app.get("/api/ai/status")
def ai_status():
    return {"available": ai.available()}


@app.post("/api/ai/report/{code}")
def ai_report(code: str, request: Request):
    if not AI_ALLOWED:
        raise HTTPException(403, "공개 환경에서 AI 리포트는 비활성화되어 있습니다.")
    _rate_limit(request, limit=5, window=300)
    if not ai.available():
        raise HTTPException(400, "ANTHROPIC_API_KEY가 설정되지 않았거나 anthropic 패키지가 없습니다.")
    data = api_analyze(code)
    try:
        md = ai.deep_report(data["name"], code, {
            "news": data["news"],
            "research": data["research"],
            "metrics": data["metrics"],
            "consensus": data["consensus"],
            "technical": data["technical"],
        })
        return {"report": md}
    except Exception as e:
        raise HTTPException(502, f"AI 리포트 생성 실패: {e}")


# ---------------------------------------------------------------- static
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8899))
    host = "0.0.0.0" if (PUBLIC or os.environ.get("PORT")) else "127.0.0.1"
    uvicorn.run(app, host=host, port=port)
