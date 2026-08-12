# -*- coding: utf-8 -*-
"""StockLens — 국내 주식 종합 분석 대시보드 서버."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import naver, kis, analysis, ai, ranking, chart_pro, valuation, auth

BASE = Path(__file__).resolve().parent
app = FastAPI(title="StockLens")

# 회원 DB는 배포 코드 동기화 경로 밖에 둔다 (오라클 rsync 재배포 시 덮어써지지 않도록).
_DATA_DIR = Path(os.environ.get("STOCKLENS_DATA_DIR") or (BASE / "data"))


@app.on_event("startup")
def _startup():
    ranking.start_background()
    auth.init(_DATA_DIR)

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


# ---------------------------------------------------------------- candles (일/주/월봉 전환)
@app.get("/api/candles/{code}")
def api_candles(code: str, tf: str = "day", request: Request = None):
    _rate_limit(request, limit=60, window=60)
    if tf not in ("day", "week", "month"):
        tf = "day"
    count = 1300 if tf == "day" else (520 if tf == "week" else 240)
    try:
        return {"candles": naver.candles(code, count, tf), "timeframe": tf}
    except Exception as e:
        raise HTTPException(502, f"차트 조회 실패: {e}")


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

    # 독립적인 네트워크 호출을 병렬로 수집한다 (순차 실행 대비 응답시간 대폭 단축).
    # 뉴스·증권사 리포트는 상위 3건만 받아 전송량과 AI 프롬프트 크기를 줄인다.
    with ThreadPoolExecutor(max_workers=7) as ex:
        f_integ = ex.submit(safe, lambda: naver.integration(code), {})
        f_fin = ex.submit(safe, lambda: naver.finance(code, "annual"), {})
        f_news = ex.submit(safe, lambda: naver.news(code, 3), [])
        f_research = ex.submit(safe, lambda: naver.research(code, 3), [])
        f_trend = ex.submit(safe, lambda: naver.trend(code), [])
        f_candles = ex.submit(safe, lambda: naver.candles(code, 1300), [])   # 약 5년
        # 상대강도 벤치마크: 국내=코스피지수 / 미국=SPY(S&P500 ETF)
        if us:
            f_bench = ex.submit(safe, lambda: [c["close"] for c in naver.candles("SPY", 1300)], [])
        else:
            f_bench = ex.submit(safe, lambda: naver.index_candles("KOSPI", 1300), [])
        integ = f_integ.result()
        fin_annual = f_fin.result()
        news_items = f_news.result()
        research_items = f_research.result()
        deal_trend = f_trend.result()
        candle_data = f_candles.result()
        bench = f_bench.result()

    # 지표 소스: 미국=basic.stockItemTotalInfos, 국내=integration.totalInfos
    src = (b.get("stockItemTotalInfos") if us else integ.get("totalInfos")) or []
    infos = {i.get("code"): i.get("value") for i in src}

    tech = analysis.technical_analysis(candle_data)

    # 고급 차트 분석 (스테이지·상대강도·추세템플릿·VCP·OBV 등)
    pro = safe(lambda: chart_pro.analyze(candle_data, bench), {"available": False})
    fund = analysis.fundamental_analysis(infos, fin_annual, market="US" if us else "KR")
    senti = analysis.news_sentiment(news_items)
    cons = analysis.consensus_info(integ, price)
    total = analysis.total_evaluation(fund, tech, senti, cons, deal_trend, pro)
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

    # 동종업계 PER — peers 응답에는 PER이 없어 종목별로 조회한다(상위 2개만, 병렬).
    # (본인 포함 중앙값 비교용이라 2개로도 충분하며 요청당 네트워크 호출 2건 절감)
    def _peer_per(p):
        try:
            pc = p["code"]
            if naver.is_us(pc):
                src2 = (naver.basic(pc).get("stockItemTotalInfos")) or []
            else:
                src2 = (naver.integration(pc).get("totalInfos")) or []
            v = {i.get("code"): i.get("value") for i in src2}
            return {"name": p["name"], "code": pc, "per": analysis.to_num(v.get("per"))}
        except Exception:
            return None

    peers_per = []
    targets_peers = [p for p in peers if p.get("code")][:2]
    if targets_peers:
        with ThreadPoolExecutor(max_workers=2) as ex:
            peers_per = [r for r in ex.map(_peer_per, targets_peers) if r and r.get("per")]
    # 본인도 비교표에 포함
    if fund["metrics"].get("per"):
        peers_per.append({"name": name, "code": code, "per": fund["metrics"]["per"], "self": True})

    val = safe(lambda: valuation.analyze(
        fund["metrics"], fund.get("all_rows") or {}, candle_data, cons,
        peers_per=peers_per, market_cap=fund["metrics"].get("market_cap"), price=price,
        market="US" if us else "KR"),
        {"available": False})
    targets["fair_buy"] = val.get("fair_buy") if val.get("available") else None

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
        "chart_pro": pro,
        "valuation": val,
        "targets": targets,
        "consensus": cons,
        "sentiment": {"score": senti["score"], "label": senti["label"]},
        "news": senti["items"][:3],
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


# ---------------------------------------------------------------- auth
SESSION_COOKIE = "sl_session"


class SignupBody(BaseModel):
    email: str
    password: str


class LoginBody(BaseModel):
    email: str
    password: str


def _current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    uid = auth.verify_session_token(token)
    if not uid:
        return None
    return auth.get_user(uid)


def _set_session_cookie(response: Response, user_id: int, request: Request):
    token = auth.create_session_token(user_id)
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=30 * 86400, httponly=True, samesite="lax",
        secure=request.url.scheme == "https",
    )


@app.post("/api/auth/signup")
def api_signup(body: SignupBody, response: Response, request: Request):
    _rate_limit(request, limit=10, window=300)
    try:
        user = auth.signup(body.email, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _set_session_cookie(response, user["id"], request)
    return {"user": user}


@app.post("/api/auth/login")
def api_login(body: LoginBody, response: Response, request: Request):
    _rate_limit(request, limit=10, window=300)
    user = auth.authenticate(body.email, body.password)
    if not user:
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다.")
    _set_session_cookie(response, user["id"], request)
    return {"user": user}


@app.post("/api/auth/logout")
def api_logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def api_me(request: Request):
    user = _current_user(request)
    return {"user": user}


# ---------------------------------------------------------------- admin
@app.get("/api/admin/users")
def api_admin_users(request: Request):
    user = _current_user(request)
    if not user or not user["is_admin"]:
        raise HTTPException(403, "관리자만 접근할 수 있습니다.")
    return {"users": auth.list_users(), "stats": auth.stats()}


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
