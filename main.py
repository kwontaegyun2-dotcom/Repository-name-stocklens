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

from app import naver, kis, analysis, ai, ranking, chart_pro, valuation, auth, push, watch, portfolio, portfolio_alert, themes, anomaly, event_alert, screener, backtest

BASE = Path(__file__).resolve().parent
app = FastAPI(title="StockLens")

# 회원 DB는 배포 코드 동기화 경로 밖에 둔다 (오라클 rsync 재배포 시 덮어써지지 않도록).
_DATA_DIR = Path(os.environ.get("STOCKLENS_DATA_DIR") or (BASE / "data"))


@app.on_event("startup")
def _startup():
    backtest.init(_DATA_DIR)   # ranking 백그라운드 스레드가 매 계산마다 스냅샷을 남기므로 먼저 초기화
    ranking.start_background()
    auth.init(_DATA_DIR)
    push.init(_DATA_DIR)
    watch.init(_DATA_DIR, api_analyze)
    portfolio.init(_DATA_DIR)
    portfolio_alert.init(_DATA_DIR, api_analyze)
    event_alert.init(_DATA_DIR, api_analyze)

# 공개 배포 모드: 개인 KIS 키 저장 금지, AI 리포트 남용 방지
PUBLIC = os.environ.get("STOCKLENS_PUBLIC") == "1"
# sitemap·OG 태그에 쓸 공개 주소. 정식 도메인으로 옮기면 환경변수만 바꾸면 된다
# (4차 진단리포트 8장 — 지금은 IP 기반 nip.io 주소).
SITE_ORIGIN = os.environ.get("STOCKLENS_ORIGIN", "https://stocklens.161-33-201-126.nip.io").rstrip("/")
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


# ---------------------------------------------------------------- 스크리너
@app.get("/api/screener")
def api_screener(
    market: str = "전체", sector: str = None, grade_min: str = None,
    score_min: float = None, per_min: float = None, per_max: float = None,
    pbr_max: float = None, roe_min: float = None, debt_max: float = None,
    div_min: float = None, upside_min: float = None, foreign_buy: bool = False,
    request: Request = None,
):
    _rate_limit(request, limit=30, window=60)
    conditions = {
        "market": market, "sector": sector, "grade_min": grade_min,
        "score_min": score_min, "per_min": per_min, "per_max": per_max,
        "pbr_max": pbr_max, "roe_min": roe_min, "debt_max": debt_max,
        "div_min": div_min, "upside_min": upside_min, "foreign_buy": foreign_buy,
    }
    return screener.run(conditions)


# ---------------------------------------------------------------- 점수 백테스트
@app.get("/api/backtest")
def api_backtest(request: Request = None):
    _rate_limit(request, limit=30, window=60)
    return backtest.dashboard()


# ---------------------------------------------------------------- 테마·산업
@app.get("/api/themes")
def api_themes():
    return {"themes": themes.list_themes()}


@app.get("/api/themes/{name}")
def api_theme_detail(name: str, request: Request = None):
    _rate_limit(request, limit=60, window=60)
    r = themes.get_theme(name, limit=5)
    if not r:
        raise HTTPException(404, "존재하지 않는 테마입니다.")
    return r


# ---------------------------------------------------------------- 이상징후 탐지
@app.get("/api/anomalies")
def api_anomalies(request: Request = None):
    _rate_limit(request, limit=60, window=60)
    return anomaly.scan()


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

    # 수급 요약 — naver.trend()가 pageSize=60으로 받아오므로 최근 최대 60영업일
    # (트레이딩엔진 설계서: 120일 확장 요청됐으나 네이버 엔드포인트가 60을 넘기면 400을
    # 반환해 60이 실측 상한). chart_pro.analyze()의 수급 오더플로우 모듈이 이 데이터를
    # 쓰므로 응답 조립보다 먼저 만들어 둔다.
    flows = []
    for d in (deal_trend or [])[:60]:
        flows.append({
            "date": d.get("bizdate"),
            "close": analysis.to_num(d.get("closePrice")),
            "foreigner": analysis.to_num(d.get("foreignerPureBuyQuant")),
            "foreigner_ratio": d.get("foreignerHoldRatio"),
            "organ": analysis.to_num(d.get("organPureBuyQuant")),
            "individual": analysis.to_num(d.get("individualPureBuyQuant")),
        })

    # 고급 차트 분석 (스테이지·상대강도·추세템플릿·VCP·OBV·AVWAP·유동성스윕·볼륨프로파일·
    # 수급 오더플로우 등)
    pro = safe(lambda: chart_pro.analyze(candle_data, bench, flows), {"available": False})

    # 4차 진단리포트 4-1/8 — "지지선·저항선을 컨플루언스 결과로 대체하라"던 설계서 요청.
    # ⚠️ 전면 교체는 하지 않는다 — support/resistance는 매수 관심 구간·손절가·기술적
    # 목표주가(tech_target) 계산의 입력값이라, 표시값만 바꾸면 "지지선은 A인데 매수
    # 관심 구간은 B 기준"처럼 이 리포트가 지적한 것과 같은 종류의 새 불일치가 생긴다.
    # 대신 컨플루언스 값을 별도 필드로 노출해 화면에서 "여러 근거가 겹친 지지/저항"을
    # 기존 피벗 기준과 나란히 보여준다(entry_plan 재계산은 별도 검증이 필요해 보류).
    if tech.get("available") and pro.get("available"):
        if pro.get("confluence_support"):
            tech["support_confluence"] = pro["confluence_support"]["price"]
        if pro.get("confluence_resistance"):
            tech["resistance_confluence"] = pro["confluence_resistance"]["price"]

    fund = analysis.fundamental_analysis(infos, fin_annual, market="US" if us else "KR")
    senti = analysis.news_sentiment(news_items, stock_name=name)
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
    ai_verdict = analysis.final_verdict(total, val, cons)

    # ⚠️ 손절가(기술적 지지선 0.96배 기반)와 3차 매수가(밸류에이션 적정가 0.80배 기반)는
    # 서로 다른 모델이라 손절가가 3차 매수가보다 높아지는 모순이 생길 수 있다
    # (2차 진단리포트 4-3: 3차 매수가 1,316,277원 > 손절가 1,318,080원 사례). 손절가는
    # 항상 가장 깊은 매수 단계(3차)보다 아래에 있어야 "계획대로 다 사도 손절선 위"가 된다.
    if tech.get("available") and targets["fair_buy"]:
        conservative_price = targets["fair_buy"]["conservative"]["price"]
        if tech["entry"]["stop_loss"] >= conservative_price:
            tech["entry"]["stop_loss"] = round(conservative_price * 0.97)

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
        "ai_verdict": ai_verdict,
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
    return {"configured": kis.is_configured() and not PUBLIC, "public": PUBLIC}


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
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


def _current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    uid = auth.verify_session_token(token)
    if not uid:
        return None
    return auth.get_user(uid)


def _require_user(request: Request):
    user = _current_user(request)
    if not user:
        raise HTTPException(401, "로그인이 필요합니다.")
    return user


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
        user = auth.signup(body.username, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _set_session_cookie(response, user["id"], request)
    return {"user": user}


@app.post("/api/auth/login")
def api_login(body: LoginBody, response: Response, request: Request):
    _rate_limit(request, limit=10, window=300)
    user = auth.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.")
    _set_session_cookie(response, user["id"], request)
    return {"user": user}


@app.post("/api/auth/logout")
def api_logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.post("/api/auth/change-password")
def api_change_password(body: ChangePasswordBody, request: Request):
    user = _require_user(request)
    try:
        auth.change_password(user["id"], body.current_password, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
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


# ---------------------------------------------------------------- 관심종목 매수 기회 알림
class SubscribeBody(BaseModel):
    endpoint: str
    keys: dict


class UnsubscribeBody(BaseModel):
    endpoint: str


class WatchBody(BaseModel):
    name: str
    price: float | None = None
    score: float | None = None
    verdict: str | None = None
    verdict_tier: str | None = None


class WatchSettingsBody(BaseModel):
    memo: str = ""
    tags: str = ""
    alert_buy: bool = True
    alert_price_target: float | None = None
    alert_score_threshold: float | None = None
    alert_verdict_change: bool = False
    alert_anomaly: bool = False


@app.get("/api/push/key")
def api_push_key():
    return {"key": push.PUBLIC_KEY}


@app.post("/api/push/subscribe")
def api_push_subscribe(body: SubscribeBody, request: Request):
    user = _require_user(request)
    keys = body.keys or {}
    if not body.endpoint or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(400, "구독 정보가 올바르지 않습니다.")
    devices = watch.save_sub(user["id"], body.endpoint, keys["p256dh"], keys["auth"],
                              request.headers.get("user-agent", ""))
    return {"ok": True, "devices": devices}


@app.post("/api/push/unsubscribe")
def api_push_unsubscribe(body: UnsubscribeBody, request: Request):
    _require_user(request)
    watch.drop_sub(body.endpoint)
    return {"ok": True}


@app.get("/api/watch")
def api_watch_list(request: Request):
    user = _require_user(request)
    return {"items": watch.list_for_user(user["id"])}


@app.post("/api/watch/{code}")
def api_watch_add(code: str, body: WatchBody, request: Request):
    user = _require_user(request)
    watch.add(user["id"], code, body.name, body.price, body.score, body.verdict, body.verdict_tier)
    return {"ok": True}


@app.delete("/api/watch/{code}")
def api_watch_remove(code: str, request: Request):
    user = _require_user(request)
    watch.remove(user["id"], code)
    return {"ok": True}


@app.put("/api/watch/{code}/settings")
def api_watch_settings(code: str, body: WatchSettingsBody, request: Request):
    user = _require_user(request)
    watch.update_settings(
        user["id"], code, body.memo.strip()[:200], body.tags.strip()[:200],
        body.alert_buy, body.alert_price_target, body.alert_score_threshold,
        body.alert_verdict_change, body.alert_anomaly,
    )
    return {"ok": True}


@app.post("/api/push/test")
def api_push_test(request: Request):
    user = _require_user(request)
    sent, total = watch.send_to_user(user["id"], {
        "title": "🔔 StockLens 알림 테스트",
        "body": "알림이 정상적으로 켜졌습니다. 관심종목이 매수 기회 조건을 충족하면 이렇게 알려드립니다.",
        "url": "/", "tag": "stocklens-test", "renotify": True,
    })
    if total == 0:
        raise HTTPException(400, "등록된 기기가 없습니다.")
    return {"ok": True, "sent": sent, "total": total}


# ---------------------------------------------------------------- 내 포트폴리오
class PortfolioBody(BaseModel):
    name: str
    shares: float
    avg_price: float | None = None


@app.get("/api/portfolio")
def api_portfolio(request: Request):
    user = _require_user(request)
    rows = portfolio.list_for_user(user["id"])
    return portfolio.compute(user["id"], rows, api_analyze)


@app.post("/api/portfolio/{code}")
def api_portfolio_add(code: str, body: PortfolioBody, request: Request):
    user = _require_user(request)
    try:
        portfolio.upsert(user["id"], code, body.name, body.shares, body.avg_price)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.put("/api/portfolio/{code}")
def api_portfolio_edit(code: str, body: PortfolioBody, request: Request):
    """POST(upsert)는 "추가 매수"로 간주해 수량을 더한다 — 잘못 입력한 값을 고칠 방법이
    없어(지우고 다시 넣는 수밖에) 매일 쓰는 기능에서 가장 큰 마찰이었다(2차 진단리포트 3-8).
    PUT은 더하지 않고 입력값으로 그대로 덮어쓴다."""
    user = _require_user(request)
    try:
        portfolio.set_holding(user["id"], code, body.name, body.shares, body.avg_price)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/portfolio/{code}")
def api_portfolio_remove(code: str, request: Request):
    user = _require_user(request)
    portfolio.remove(user["id"], code)
    return {"ok": True}


@app.get("/sw.js")
def service_worker():
    return FileResponse(BASE / "static" / "sw.js", media_type="application/javascript",
                         headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


# ---------------------------------------------------------------- static
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


# ⚠️ URL 라우팅(2026-08-19, 진단리포트 지적사항): 이전에는 어느 종목을 열어도 주소창이
# 항상 "/" 그대로라 북마크·공유·브라우저 뒤로가기가 전부 불가능했다(자체 "돌아가기" 버튼으로
# 대신하고 있었음). 프런트는 SPA 그대로 두고(바닐라 JS 유지 원칙), /stock/{code} 요청에도
# 같은 index.html을 내려준 뒤 클라이언트에서 location.pathname을 읽어 해당 종목을
# 자동으로 분석·렌더링하도록 한다(static/app.js의 applyRoute 처리 참고).
@app.get("/stock/{code}")
def stock_page(code: str):
    return FileResponse(BASE / "static" / "index.html")


# 관심종목·스크리너·포트폴리오 전용 주소. 종목 상세만 라우팅되고 이 셋은 404라
# "갈 곳이 없으니 모든 기능이 홈으로 몰린다"는 지적을 네 차례 받았다(UI/UX 진단보고서
# 3-1, 4차 진단리포트 8장). SPA라 서버는 같은 index.html만 내려주면 되고, 어떤 화면을
# 열지는 클라이언트의 applyRoute()가 판단한다.
@app.get("/watchlist")
@app.get("/screener")
@app.get("/portfolio")
def spa_page():
    return FileResponse(BASE / "static" / "index.html")


# robots·sitemap·favicon — 세 개 모두 404였다(4차 진단리포트 8장, 4회 연속 지적).
@app.get("/robots.txt")
def robots():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        # 개인 데이터가 걸린 화면은 로그인해야 볼 수 있어 크롤링해도 의미가 없다.
        "Disallow: /portfolio\n"
        "Disallow: /watchlist\n"
        "Disallow: /api/\n"
        f"Sitemap: {SITE_ORIGIN}/sitemap.xml\n"
    )
    return Response(content=body, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    urls = [f"{SITE_ORIGIN}/", f"{SITE_ORIGIN}/screener"]
    # 랭킹 유니버스에 있는 종목 상세는 공개 페이지라 색인 대상에 넣는다.
    for market in ("KR", "US"):
        for entry in ranking.UNIVERSES.get(market, [])[:200]:
            urls.append(f"{SITE_ORIGIN}/stock/{entry[0]}")
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls)
            + "</urlset>\n")
    return Response(content=body, media_type="application/xml")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(BASE / "static" / "favicon.svg", media_type="image/svg+xml")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8899))
    host = "0.0.0.0" if (PUBLIC or os.environ.get("PORT")) else "127.0.0.1"
    uvicorn.run(app, host=host, port=port)
