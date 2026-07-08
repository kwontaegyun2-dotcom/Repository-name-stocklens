# -*- coding: utf-8 -*-
"""종합 분석 엔진: 기본적/기술적 분석, 뉴스 감성, 점수화, 목표주가·진입타이밍."""
from __future__ import annotations

import math
import re


# ---------------------------------------------------------------- helpers
def to_num(v):
    """'311,500' / '46.76%' / '23.08배' / '12,372원' / 'N/A' → float | None"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    for unit in (",", "%", "+", "배", "원", "주", "％", " "):
        s = s.replace(unit, "")
    s = s.strip()
    if s in ("", "-", "N/A", "―", "N/A배"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_eok(v):
    """'1,669조 1,125억' → 억원 단위 float. 순수 숫자면 그대로 반환."""
    if v is None:
        return None
    s = str(v).replace(",", "").replace(" ", "")
    jo = re.search(r"([\d.]+)조", s)
    eok = re.search(r"([\d.]+)억", s)
    if jo or eok:
        return (float(jo.group(1)) * 10000 if jo else 0) + (float(eok.group(1)) if eok else 0)
    return to_num(s)


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def _scale(value, worst, best):
    """value를 worst→0점, best→100점 선형 스케일 (역방향 지원)."""
    if value is None:
        return None
    if worst == best:
        return 50.0
    t = (value - worst) / (best - worst)
    return _clamp(t * 100.0)


# ---------------------------------------------------------------- indicators
def sma(closes, n):
    if len(closes) < n:
        return []
    out = []
    s = sum(closes[:n])
    out.append(s / n)
    for i in range(n, len(closes)):
        s += closes[i] - closes[i - n]
        out.append(s / n)
    return out  # 길이 len(closes)-n+1, 마지막이 최신


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - 100.0 / (1.0 + rs)


def _ema_series(values, n):
    k = 2.0 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None
    ef = _ema_series(closes, fast)
    es = _ema_series(closes, slow)
    line = [f - s for f, s in zip(ef, es)]
    sig = _ema_series(line[slow - 1:], signal)
    hist = line[-1] - sig[-1]
    hist_prev = line[-2] - sig[-2] if len(sig) >= 2 else hist
    return {"macd": line[-1], "signal": sig[-1], "hist": hist, "hist_prev": hist_prev}


def bollinger(closes, n=20, k=2.0):
    if len(closes) < n:
        return None
    window = closes[-n:]
    mid = sum(window) / n
    var = sum((c - mid) ** 2 for c in window) / n
    sd = math.sqrt(var)
    upper, lower = mid + k * sd, mid - k * sd
    pos = (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return {"upper": upper, "mid": mid, "lower": lower, "pct_b": pos}


# ---------------------------------------------------------------- technical
def technical_analysis(candles: list) -> dict:
    if not candles or len(candles) < 30:
        return {"available": False}
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    vols = [c["volume"] for c in candles]
    price = closes[-1]

    mas = {}
    for n in (5, 20, 60, 120):
        s = sma(closes, n)
        mas[n] = s[-1] if s else None

    r = rsi(closes)
    m = macd(closes)
    bb = bollinger(closes)

    lookback = min(len(closes), 250)
    hi52 = max(highs[-lookback:])
    lo52 = min(lows[-lookback:])
    pos52 = (price - lo52) / (hi52 - lo52) * 100 if hi52 != lo52 else 50

    # 지지/저항: 최근 60일 피벗 근사
    recent_h = highs[-60:]
    recent_l = lows[-60:]
    resistance = max(recent_h)
    support = min(recent_l)

    # 골든/데드 크로스 (SMA20 vs SMA60, 최근 15일 내)
    cross = None
    s20 = sma(closes, 20)
    s60 = sma(closes, 60)
    if s20 and s60:
        pair = list(zip(s20[-min(len(s20), len(s60)):], s60[-min(len(s20), len(s60)):]))
        for i in range(max(1, len(pair) - 15), len(pair)):
            prev_d = pair[i - 1][0] - pair[i - 1][1]
            cur_d = pair[i][0] - pair[i][1]
            if prev_d <= 0 < cur_d:
                cross = "golden"
            elif prev_d >= 0 > cur_d:
                cross = "dead"

    # 거래량: 최근 5일 평균 vs 20일 평균
    vol_ratio = None
    if len(vols) >= 20:
        v5 = sum(vols[-5:]) / 5
        v20 = sum(vols[-20:]) / 20
        vol_ratio = v5 / v20 if v20 else None

    # ---- 신호 및 점수
    signals = []
    score = 50.0

    if mas[20] and mas[60]:
        if price > mas[20] > mas[60]:
            score += 12
            signals.append(("bull", "주가가 20·60일 이동평균선 위 — 정배열 상승 추세"))
        elif price < mas[20] < mas[60]:
            score -= 12
            signals.append(("bear", "주가가 20·60일 이동평균선 아래 — 역배열 하락 추세"))
        elif price > mas[20]:
            score += 5
            signals.append(("bull", "주가가 20일선 위 — 단기 추세 양호"))
        else:
            score -= 5
            signals.append(("bear", "주가가 20일선 아래 — 단기 추세 약화"))

    if cross == "golden":
        score += 8
        signals.append(("bull", "최근 골든크로스(20일선이 60일선 상향 돌파) 발생"))
    elif cross == "dead":
        score -= 8
        signals.append(("bear", "최근 데드크로스(20일선이 60일선 하향 이탈) 발생"))

    if r is not None:
        if r >= 70:
            score -= 8
            signals.append(("warn", f"RSI {r:.0f} — 단기 과열 구간, 추격 매수 주의"))
        elif r <= 30:
            score += 8
            signals.append(("bull", f"RSI {r:.0f} — 과매도 구간, 기술적 반등 가능성"))
        else:
            signals.append(("neutral", f"RSI {r:.0f} — 중립 구간"))

    if m:
        if m["hist"] > 0 and m["hist_prev"] <= 0:
            score += 8
            signals.append(("bull", "MACD 히스토그램 양전환 — 상승 모멘텀 발생"))
        elif m["hist"] < 0 and m["hist_prev"] >= 0:
            score -= 8
            signals.append(("bear", "MACD 히스토그램 음전환 — 하락 모멘텀 발생"))
        elif m["hist"] > 0:
            score += 4
            signals.append(("bull", "MACD 상승 모멘텀 유지 중"))
        else:
            score -= 4
            signals.append(("bear", "MACD 하락 모멘텀 유지 중"))

    if bb:
        if bb["pct_b"] >= 1.0:
            score -= 4
            signals.append(("warn", "볼린저밴드 상단 돌파 — 변동성 확대·과열 주의"))
        elif bb["pct_b"] <= 0.0:
            score += 4
            signals.append(("bull", "볼린저밴드 하단 이탈 — 낙폭 과대 반등 관찰"))

    if vol_ratio and vol_ratio > 1.5:
        signals.append(("info", f"최근 거래량이 20일 평균 대비 {vol_ratio:.1f}배 — 시장 관심 증가"))

    score = _clamp(score)

    # ---- 진입 타이밍 판단
    if score >= 70:
        verdict, verdict_cls = "매수 우위", "buy"
        timing = "추세와 모멘텀이 모두 긍정적입니다. 눌림목(20일선 부근) 분할 매수 전략이 유효합니다."
    elif score >= 55:
        verdict, verdict_cls = "분할 매수 관점", "accumulate"
        timing = "완만한 상승 흐름입니다. 한 번에 매수하기보다 2~3회 분할 진입을 권장합니다."
    elif score >= 40:
        verdict, verdict_cls = "관망", "hold"
        timing = "방향성이 뚜렷하지 않습니다. 지지선 확인 후 진입해도 늦지 않습니다."
    else:
        verdict, verdict_cls = "보수적 접근", "avoid"
        timing = "하락 추세가 우세합니다. 신규 진입은 추세 전환 신호(골든크로스, RSI 반등) 확인 후 고려하세요."

    entry_low = mas[20] if mas[20] and mas[20] < price else support
    entry = {
        "buy_zone_low": round(min(entry_low, price) * 0.99),
        "buy_zone_high": round(price * 1.005) if score >= 55 else round(min(entry_low, price) * 1.02),
        "stop_loss": round(support * 0.97),
        "resistance": round(resistance),
        "support": round(support),
    }

    tech_target = round(max(resistance, price + (price - support) * 0.618))

    return {
        "available": True,
        "price": price,
        "sma": {str(k): (round(v, 1) if v else None) for k, v in mas.items()},
        "rsi": round(r, 1) if r is not None else None,
        "macd": {k: round(v, 2) for k, v in m.items()} if m else None,
        "bollinger": {k: round(v, 2) for k, v in bb.items()} if bb else None,
        "high_52w": hi52, "low_52w": lo52, "pos_52w": round(pos52, 1),
        "support": round(support), "resistance": round(resistance),
        "cross": cross,
        "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
        "score": round(score, 1),
        "signals": [{"type": t, "text": s} for t, s in signals],
        "verdict": verdict, "verdict_class": verdict_cls,
        "timing_comment": timing,
        "entry": entry,
        "tech_target": tech_target,
    }


# ---------------------------------------------------------------- fundamentals
def _finance_rows(finance_data) -> dict:
    """finance API → {행이름: [(기간key, 값, isConsensus)]} (기간 오름차순)"""
    info = (finance_data or {}).get("financeInfo") or {}
    titles = info.get("trTitleList") or []
    keys = [(t["key"], t.get("isConsensus") == "Y") for t in titles]
    rows = {}
    for row in info.get("rowList", []):
        name = row.get("title", "")
        cols = row.get("columns", {})
        series = []
        for k, is_cns in keys:
            v = to_num((cols.get(k) or {}).get("value"))
            series.append({"period": k, "value": v, "consensus": is_cns})
        rows[name] = series
    return rows


def _row_match(rows: dict, *keywords):
    for name, series in rows.items():
        if all(kw in name for kw in keywords):
            return name, series
    return None, None


def _last_actual(series):
    actuals = [s for s in (series or []) if not s["consensus"] and s["value"] is not None]
    return actuals[-1]["value"] if actuals else None


def _prev_actual(series):
    actuals = [s for s in (series or []) if not s["consensus"] and s["value"] is not None]
    return actuals[-2]["value"] if len(actuals) >= 2 else None


def _consensus_val(series):
    cns = [s for s in (series or []) if s["consensus"] and s["value"] is not None]
    return cns[0]["value"] if cns else None


def _growth(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100.0


def fundamental_analysis(integration: dict, fin_annual: dict) -> dict:
    infos = {i.get("code"): i.get("value") for i in (integration or {}).get("totalInfos", [])}
    per = to_num(infos.get("per"))
    cns_per = to_num(infos.get("cnsPer"))
    pbr = to_num(infos.get("pbr"))
    eps = to_num(infos.get("eps"))
    bps = to_num(infos.get("bps"))
    dividend_yield = to_num(infos.get("dividendYieldRatio"))
    market_cap = parse_eok(infos.get("marketValue"))  # 억원

    rows = _finance_rows(fin_annual)
    _, roe_s = _row_match(rows, "ROE")
    _, rev_s = _row_match(rows, "매출액")
    _, op_s = _row_match(rows, "영업이익")
    if op_s:
        # '영업이익률'이 먼저 잡히지 않도록 정확 매칭 재시도
        for name, series in rows.items():
            if name.strip() == "영업이익":
                op_s = series
                break
    _, opm_s = _row_match(rows, "영업이익률")
    _, npm_s = _row_match(rows, "순이익률")
    _, debt_s = _row_match(rows, "부채비율")
    _, retain_s = _row_match(rows, "유보율")

    roe = _last_actual(roe_s)
    opm = _last_actual(opm_s)
    npm = _last_actual(npm_s)
    debt = _last_actual(debt_s)
    retain = _last_actual(retain_s)

    rev_cur, rev_prev, rev_cns = _last_actual(rev_s), _prev_actual(rev_s), _consensus_val(rev_s)
    op_cur, op_prev, op_cns = _last_actual(op_s), _prev_actual(op_s), _consensus_val(op_s)
    rev_growth = _growth(rev_cur, rev_prev)
    op_growth = _growth(op_cur, op_prev)
    rev_growth_fwd = _growth(rev_cns, rev_cur)
    op_growth_fwd = _growth(op_cns, op_cur)

    # ---- 점수 계산
    # 가치평가: PER 낮을수록, PBR 낮을수록, 배당 높을수록
    per_score = _scale(per, 40, 5) if per and per > 0 else (20 if per is not None else None)
    pbr_score = _scale(pbr, 5, 0.5) if pbr and pbr > 0 else None
    div_score = _scale(dividend_yield, 0, 4) if dividend_yield is not None else None
    value_parts = [s for s in (per_score, pbr_score, div_score) if s is not None]
    value_score = sum(value_parts) / len(value_parts) if value_parts else 50

    # 수익성
    roe_score = _scale(roe, 0, 20)
    opm_score = _scale(opm, 0, 25)
    npm_score = _scale(npm, 0, 20)
    prof_parts = [s for s in (roe_score, opm_score, npm_score) if s is not None]
    prof_score = sum(prof_parts) / len(prof_parts) if prof_parts else 50

    # 성장성 (과거 + 컨센서스 전망 모두 반영)
    g_parts = [s for s in (
        _scale(rev_growth, -10, 25),
        _scale(op_growth, -20, 50),
        _scale(rev_growth_fwd, -10, 25),
        _scale(op_growth_fwd, -20, 50),
    ) if s is not None]
    growth_score = sum(g_parts) / len(g_parts) if g_parts else 50

    # 안정성
    debt_score = _scale(debt, 250, 30)
    retain_score = _scale(retain, 0, 3000)
    st_parts = [s for s in (debt_score, retain_score) if s is not None]
    stability_score = sum(st_parts) / len(st_parts) if st_parts else 50

    return {
        "metrics": {
            "per": per, "cns_per": cns_per, "pbr": pbr, "eps": eps, "bps": bps,
            "dividend_yield": dividend_yield, "market_cap": market_cap,
            "roe": roe, "op_margin": opm, "net_margin": npm,
            "debt_ratio": debt, "retention_ratio": retain,
            "rev_growth": round(rev_growth, 1) if rev_growth is not None else None,
            "op_growth": round(op_growth, 1) if op_growth is not None else None,
            "rev_growth_fwd": round(rev_growth_fwd, 1) if rev_growth_fwd is not None else None,
            "op_growth_fwd": round(op_growth_fwd, 1) if op_growth_fwd is not None else None,
        },
        "finance_rows": {
            "매출액": rev_s, "영업이익": op_s,
            "ROE": roe_s, "부채비율": debt_s,
        },
        "scores": {
            "value": round(value_score, 1),
            "profitability": round(prof_score, 1),
            "growth": round(growth_score, 1),
            "stability": round(stability_score, 1),
        },
    }


# ---------------------------------------------------------------- sentiment
POS_WORDS = ["상승", "급등", "호실적", "최대", "신기록", "돌파", "개선", "성장", "확대", "수주",
             "호조", "상향", "매수", "기대", "반등", "흑자", "역대", "질주", "강세", "훈풍",
             "낙관", "회복", "증가", "신고가", "목표가↑", "목표주가 상향", "어닝서프라이즈"]
NEG_WORDS = ["하락", "급락", "부진", "적자", "감소", "우려", "리스크", "하향", "매도", "약세",
             "충격", "쇼크", "규제", "소송", "파산", "위기", "불황", "침체", "경고", "악재",
             "신저가", "손실", "감원", "구조조정", "어닝쇼크"]


def news_sentiment(news_items: list) -> dict:
    tagged = []
    total = 0
    for it in news_items:
        text = (it.get("title", "") + " " + it.get("body", ""))[:300]
        p = sum(1 for w in POS_WORDS if w in text)
        n = sum(1 for w in NEG_WORDS if w in text)
        if p > n:
            senti = "positive"
            total += 1
        elif n > p:
            senti = "negative"
            total -= 1
        else:
            senti = "neutral"
        tagged.append({**it, "body": it.get("body", "")[:120], "sentiment": senti})
    count = len(tagged) or 1
    ratio = total / count  # -1 ~ 1
    score = _clamp(50 + ratio * 60)
    if score >= 65:
        label = "긍정적"
    elif score <= 35:
        label = "부정적"
    else:
        label = "중립적"
    return {"items": tagged, "score": round(score, 1), "label": label}


# ---------------------------------------------------------------- consensus / report
def consensus_info(integration: dict, price: float):
    c = (integration or {}).get("consensusInfo") or {}
    target = to_num(c.get("priceTargetMean"))
    recomm = to_num(c.get("recommMean"))
    upside = None
    if target and price:
        upside = round((target - price) / price * 100, 1)
    opinion = None
    if recomm is not None:
        # 네이버 recommMean: 5점 척도(5=적극매수)
        if recomm >= 4.5: opinion = "적극 매수"
        elif recomm >= 3.75: opinion = "매수"
        elif recomm >= 3.0: opinion = "중립(보유)"
        elif recomm >= 2.0: opinion = "비중 축소"
        else: opinion = "매도"
    return {
        "target_price": target,
        "recomm_mean": recomm,
        "opinion": opinion,
        "upside": upside,
        "date": c.get("createDate"),
    }


# ---------------------------------------------------------------- total
GRADE_TABLE = [(85, "S", "최상위 우량"), (75, "A", "우수"), (65, "B", "양호"),
               (50, "C", "보통"), (35, "D", "주의"), (0, "F", "위험")]


def total_evaluation(fund: dict, tech: dict, senti: dict, cons: dict, deal_trend: list) -> dict:
    fs = fund["scores"]

    # 수급 점수: 최근 5일 외국인+기관 순매수 일수
    flow_score = 50.0
    try:
        days = deal_trend[:5]
        buy_days = 0
        for d in days:
            f = to_num(d.get("foreignerPureBuyQuant")) or 0
            o = to_num(d.get("organPureBuyQuant")) or 0
            if f + o > 0:
                buy_days += 1
        flow_score = _clamp(20 + buy_days * 15)
    except Exception:
        pass

    tech_score = tech.get("score", 50) if tech.get("available") else 50

    # 시장심리: 뉴스 감성 + 애널리스트 컨센서스
    mkt_score = senti["score"]
    if cons.get("recomm_mean"):
        mkt_score = (mkt_score + _clamp((cons["recomm_mean"] - 1) / 4 * 100)) / 2

    categories = {
        "가치평가": fs["value"],
        "수익성": fs["profitability"],
        "성장성": fs["growth"],
        "재무안정성": fs["stability"],
        "기술적추세": tech_score,
        "수급·심리": round((flow_score + mkt_score) / 2, 1),
    }
    weights = {"가치평가": 0.18, "수익성": 0.20, "성장성": 0.22,
               "재무안정성": 0.12, "기술적추세": 0.16, "수급·심리": 0.12}
    total = sum(categories[k] * weights[k] for k in categories)

    grade, grade_desc = "F", "위험"
    for th, g, desc in GRADE_TABLE:
        if total >= th:
            grade, grade_desc = g, desc
            break

    return {
        "total_score": round(total, 1),
        "grade": grade,
        "grade_desc": grade_desc,
        "categories": categories,
        "flow_score": round(flow_score, 1),
    }


def build_opinion(name: str, fund: dict, tech: dict, senti: dict, cons: dict, total: dict) -> dict:
    """규칙 기반 종합 의견 / 미래 사업가치 서술 생성."""
    m = fund["metrics"]
    lines = []

    # 밸류에이션
    if m["per"] is not None:
        if m["cns_per"] and m["per"] and m["cns_per"] < m["per"] * 0.75:
            lines.append(f"현재 PER {m['per']:.1f}배 대비 컨센서스 기준 선행 PER은 {m['cns_per']:.1f}배로, "
                         f"이익 성장이 실현되면 밸류에이션 부담이 크게 낮아지는 구조입니다.")
        elif m["per"] < 10:
            lines.append(f"PER {m['per']:.1f}배로 절대 저평가 영역에 있습니다.")
        elif m["per"] > 30:
            lines.append(f"PER {m['per']:.1f}배로 높은 성장 기대가 이미 주가에 반영되어 있어, 실적 미달 시 조정 위험이 있습니다.")

    # 성장성
    if m["op_growth_fwd"] is not None:
        if m["op_growth_fwd"] > 20:
            lines.append(f"증권가 컨센서스는 내년 영업이익이 {m['op_growth_fwd']:.0f}% 증가할 것으로 전망하며, "
                         "미래 사업가치 측면에서 강한 성장 모멘텀이 기대됩니다.")
        elif m["op_growth_fwd"] < 0:
            lines.append(f"컨센서스 기준 영업이익이 {abs(m['op_growth_fwd']):.0f}% 감소할 것으로 전망되어 실적 둔화 우려가 있습니다.")
    if m["roe"] is not None:
        if m["roe"] >= 15:
            lines.append(f"ROE {m['roe']:.1f}%로 자본 효율성이 우수합니다.")
        elif m["roe"] < 5:
            lines.append(f"ROE {m['roe']:.1f}%로 자본 효율성이 낮은 편입니다.")

    # 재무
    if m["debt_ratio"] is not None and m["debt_ratio"] < 60:
        lines.append(f"부채비율 {m['debt_ratio']:.0f}%로 재무구조가 안정적입니다.")
    elif m["debt_ratio"] is not None and m["debt_ratio"] > 200:
        lines.append(f"부채비율 {m['debt_ratio']:.0f}%로 재무 레버리지가 높아 금리 환경에 유의해야 합니다.")

    # 심리/수급
    lines.append(f"최근 뉴스 흐름은 {senti['label']}이며, "
                 + (f"애널리스트 평균 투자의견은 '{cons['opinion']}'"
                    + (f", 목표주가 평균 대비 상승여력은 {cons['upside']}%입니다." if cons.get("upside") is not None else "입니다.")
                    if cons.get("opinion") else "증권사 컨센서스 데이터는 제한적입니다."))

    # 기술적
    if tech.get("available"):
        lines.append(f"기술적으로는 '{tech['verdict']}' 구간으로 판단됩니다. {tech['timing_comment']}")

    score = total["total_score"]
    if score >= 75:
        head = f"{name}은(는) 종합점수 {score}점({total['grade']}등급)으로 펀더멘털과 시장 모멘텀이 모두 견조한 종목입니다."
    elif score >= 60:
        head = f"{name}은(는) 종합점수 {score}점({total['grade']}등급)으로 전반적으로 양호하나 일부 지표의 확인이 필요합니다."
    elif score >= 45:
        head = f"{name}은(는) 종합점수 {score}점({total['grade']}등급)으로 강점과 약점이 혼재되어 선별적 접근이 필요합니다."
    else:
        head = f"{name}은(는) 종합점수 {score}점({total['grade']}등급)으로 보수적인 접근을 권합니다."

    return {"headline": head, "points": lines}
