# -*- coding: utf-8 -*-
"""고급 차트 분석 — 실전 트레이딩에서 쓰이는 기법들을 점수화.

기존 technical_analysis(추세·모멘텀·위치·거래량)를 보완하는 상위 레이어.
5년(약 1300봉) 데이터를 전제로 장기 구조까지 본다.

담은 기법:
  1. 스테이지 분석 (Weinstein) — 시장 국면 4단계
  2. 상대강도 RS (O'Neil/Minervini) — 시장 대비 초과성과
  3. 추세 템플릿 (Minervini Trend Template) — 8개 조건 체크리스트
  4. 변동성 수축 VCP (Minervini) — 돌파 직전 눌림 패턴
  5. 베이스/박스권 돌파 (Darvas Box, O'Neil base)
  6. ATR 변동성·리스크 (Wilder)
  7. OBV 매집/분산 (Granville)
  8. 피보나치 되돌림 (Elliott/Fibonacci)
  9. 이동평균 이격도 (평균회귀)
 10. 신고가 근접도 (52주 신고가 모멘텀)
"""
import math

from app.analysis import _clamp, sma


# ---------------------------------------------------------------- 기본 지표
def atr(candles, n=14):
    """Average True Range — 변동성의 절대 크기."""
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return None
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n      # Wilder 평활
    return a


def obv(candles):
    """On Balance Volume — 종가 방향으로 거래량을 누적(매집/분산 추적)."""
    if len(candles) < 2:
        return []
    out = [0.0]
    for i in range(1, len(candles)):
        v = candles[i]["volume"]
        if candles[i]["close"] > candles[i - 1]["close"]:
            out.append(out[-1] + v)
        elif candles[i]["close"] < candles[i - 1]["close"]:
            out.append(out[-1] - v)
        else:
            out.append(out[-1])
    return out


def _lin_slope(vals):
    """단순 선형회귀 기울기(정규화). 값 추세의 방향·강도."""
    n = len(vals)
    if n < 3:
        return 0.0
    xm = (n - 1) / 2
    ym = sum(vals) / n
    num = sum((i - xm) * (v - ym) for i, v in enumerate(vals))
    den = sum((i - xm) ** 2 for i in range(n)) or 1.0
    slope = num / den
    scale = abs(ym) or 1.0
    return slope / scale * n * 100.0     # 구간 전체 변화율(%) 근사


# ---------------------------------------------------------------- 개별 기법
def stage_analysis(closes, ma30w):
    """와인스타인 스테이지 — 30주(150일)선 기준 4국면.

    1국면 바닥다지기 / 2국면 상승(매수) / 3국면 천장 / 4국면 하락(회피)
    """
    if not ma30w or len(ma30w) < 25:
        return None
    price = closes[-1]
    ma = ma30w[-1]
    slope = _lin_slope(ma30w[-25:])     # 30주선 자체의 방향
    above = price > ma
    if above and slope > 1.5:
        stage, label = 2, "2국면 상승 — 주도주 구간, 매수 우위"
    elif above and slope <= 1.5:
        stage, label = 3, "3국면 천장권 — 상승 탄력 둔화, 이익실현 관점"
    elif not above and slope < -1.5:
        stage, label = 4, "4국면 하락 — 추세 훼손, 신규 진입 회피"
    else:
        stage, label = 1, "1국면 바닥다지기 — 방향성 대기, 돌파 확인 후 진입"
    return {"stage": stage, "label": label, "ma30w": round(ma, 1), "ma30w_slope": round(slope, 2)}


def relative_strength(closes, bench_closes):
    """상대강도 — 벤치마크(코스피/S&P) 대비 초과수익률.

    오닐의 RS Rating 개념을 단순화: 최근 3·6·12개월 초과성과 가중평균.
    ⚠️ 종목과 지수는 상장일·거래일수가 달라 배열 길이가 다르다.
       인덱스를 그대로 쓰면 서로 다른 시점을 비교하게 되므로(초과성과 190%p 같은
       비현실적 값의 원인) **뒤에서부터 공통 길이만큼 잘라 정렬**한 뒤 비교한다.
    """
    if not bench_closes or len(closes) < 60 or len(bench_closes) < 60:
        return None
    n = min(len(closes), len(bench_closes))
    s_arr = closes[-n:]
    b_arr = bench_closes[-n:]
    out = {}
    weights = [(63, 0.4), (126, 0.3), (252, 0.3)]   # 3M·6M·12M
    tot_w, acc = 0.0, 0.0
    for days, w in weights:
        if n <= days:
            continue
        s0, b0 = s_arr[-days - 1], b_arr[-days - 1]
        if not s0 or not b0:
            continue
        s_ret = (s_arr[-1] - s0) / abs(s0) * 100
        b_ret = (b_arr[-1] - b0) / abs(b0) * 100
        excess = s_ret - b_ret
        out[f"{days}d"] = round(excess, 1)
        acc += excess * w
        tot_w += w
    if not tot_w:
        return None
    rs = acc / tot_w
    # 초과수익 → 점수. 강세장에선 지수 자체가 100% 넘게 오르기도 해서
    # ±30%p 척도는 곧바로 포화된다(전 종목 0/100점화). 로그 스케일로 완만하게 매핑:
    # +10%p≈65점, +30%p≈78점, +100%p≈93점 / 대칭으로 하락도 동일.
    sgn = 1.0 if rs >= 0 else -1.0
    score = _clamp(50 + sgn * math.log10(1 + abs(rs) / 5.0) * 32.0)
    return {"excess": round(rs, 1), "detail": out, "score": round(score, 1)}


def trend_template(closes, mas, hi52, lo52):
    """미너비니 추세 템플릿 — 주도주가 만족하는 8개 조건 체크리스트."""
    price = closes[-1]
    m50, m150, m200 = mas.get(50), mas.get(150), mas.get(200)
    checks = []

    def add(ok, text):
        checks.append({"ok": bool(ok), "text": text})

    add(m150 and m200 and price > m150 and price > m200, "주가가 150일·200일선 위")
    add(m150 and m200 and m150 > m200, "150일선이 200일선 위")
    # 200일선이 최소 1개월 이상 상승 중
    s200 = None
    if m200 is not None:
        s200 = mas.get("_s200_slope")
    add(s200 is not None and s200 > 0, "200일선이 상승 추세")
    add(m50 and m150 and m200 and m50 > m150 > m200, "50일 > 150일 > 200일 정배열")
    add(m50 and price > m50, "주가가 50일선 위")
    add(lo52 and price >= lo52 * 1.3, "52주 저가 대비 30% 이상 상승")
    add(hi52 and price >= hi52 * 0.75, "52주 고가의 75% 이내 근접")
    add(True, "충분한 거래 이력(5년 데이터 기준)")

    passed = sum(1 for c in checks if c["ok"])
    return {
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "score": round(passed / len(checks) * 100, 1),
    }


def vcp(candles, lookback=120):
    """변동성 수축 패턴(VCP) — 조정 폭이 점점 줄며 거래량도 마르는 구간.

    돌파 직전 매물 소화가 끝났음을 시사(미너비니 핵심 셋업).
    """
    if len(candles) < lookback:
        return None
    seg = candles[-lookback:]
    # 3개 구간으로 나눠 각 구간의 (고가-저가)/고가 변동폭과 평균 거래량 비교
    third = lookback // 3
    parts = [seg[:third], seg[third:third * 2], seg[third * 2:]]
    ranges, vols = [], []
    for p in parts:
        hi = max(c["high"] for c in p)
        lo = min(c["low"] for c in p)
        ranges.append((hi - lo) / hi * 100 if hi else 0)
        vols.append(sum(c["volume"] for c in p) / len(p))
    contracting = ranges[0] > ranges[1] > ranges[2]
    vol_drying = vols[0] > vols[2]
    tight = ranges[2] < 15          # 마지막 구간 변동폭이 15% 미만이면 타이트
    score = 50.0
    if contracting:
        score += 25
    if vol_drying:
        score += 15
    if tight:
        score += 10
    return {
        "contracting": contracting,
        "vol_drying": vol_drying,
        "tight": tight,
        "ranges": [round(r, 1) for r in ranges],
        "score": round(_clamp(score), 1),
    }


def darvas_box(candles, lookback=90):
    """다바스 박스 — 최근 박스권 상·하단과 돌파 여부."""
    if len(candles) < lookback:
        return None
    seg = candles[-lookback:]
    highs = [c["high"] for c in seg]
    lows = [c["low"] for c in seg]
    price = seg[-1]["close"]
    # 최근 절반 구간의 고점/저점을 박스로 본다
    half = seg[len(seg) // 2:]
    top = max(c["high"] for c in half)
    bottom = min(c["low"] for c in half)
    width = (top - bottom) / bottom * 100 if bottom else 0
    breakout = price >= top * 0.995
    breakdown = price <= bottom * 1.005
    return {
        "top": round(top, 1), "bottom": round(bottom, 1),
        "width_pct": round(width, 1),
        "breakout": breakout, "breakdown": breakdown,
        "range_high": round(max(highs), 1), "range_low": round(min(lows), 1),
    }


def obv_trend(candles, n=60):
    """OBV 추세 — 매집(상승) vs 분산(하락). 가격과의 다이버전스도 판단."""
    o = obv(candles)
    if len(o) < n + 1:
        return None
    o_slope = _lin_slope(o[-n:])
    closes = [c["close"] for c in candles]
    p_slope = _lin_slope(closes[-n:])
    # 가격은 빠지는데 OBV는 오르면 = 매집(강세 다이버전스)
    diverge = None
    if p_slope < -3 and o_slope > 3:
        diverge = "bullish"
    elif p_slope > 3 and o_slope < -3:
        diverge = "bearish"
    score = _clamp(50 + o_slope * 0.5)
    if diverge == "bullish":
        score = _clamp(score + 15)
    elif diverge == "bearish":
        score = _clamp(score - 15)
    return {"slope": round(o_slope, 1), "divergence": diverge, "score": round(score, 1)}


def fibonacci(candles, lookback=250):
    """피보나치 되돌림 — 직전 주요 스윙(저→고) 기준 되돌림 레벨과 현재 위치."""
    if len(candles) < 30:
        return None
    seg = candles[-min(lookback, len(candles)):]
    hi = max(c["high"] for c in seg)
    lo = min(c["low"] for c in seg)
    if hi <= lo:
        return None
    price = seg[-1]["close"]
    diff = hi - lo
    levels = {
        "0.0": hi,
        "23.6": hi - diff * 0.236,
        "38.2": hi - diff * 0.382,
        "50.0": hi - diff * 0.5,
        "61.8": hi - diff * 0.618,
        "100.0": lo,
    }
    # 현재가가 어느 구간에 있는지 (되돌림 %)
    retrace = (hi - price) / diff * 100
    if retrace <= 23.6:
        zone = "고점권 (되돌림 23.6% 이내)"
    elif retrace <= 38.2:
        zone = "얕은 되돌림 (23.6~38.2%) — 강세 조정"
    elif retrace <= 61.8:
        zone = "황금 되돌림 구간 (38.2~61.8%) — 눌림목 매수 관심"
    else:
        zone = "깊은 되돌림 (61.8% 초과) — 추세 훼손 주의"
    return {
        "high": round(hi, 1), "low": round(lo, 1),
        "levels": {k: round(v, 1) for k, v in levels.items()},
        "retrace_pct": round(retrace, 1),
        "zone": zone,
    }


def disparity(price, mas):
    """이격도 — 이동평균 대비 현재가 괴리율(평균회귀 관점)."""
    out = {}
    for n in (20, 60, 120):
        m = mas.get(n)
        if m:
            out[str(n)] = round((price / m - 1) * 100, 1)
    return out or None


# ---------------------------------------------------------------- 통합
def analyze(candles, bench_closes=None):
    """고급 차트 분석 통합 실행 → 기법별 결과 + 종합 점수."""
    if not candles or len(candles) < 120:
        return {"available": False}

    closes = [c["close"] for c in candles]
    price = closes[-1]

    mas = {}
    for n in (20, 50, 60, 120, 150, 200):
        s = sma(closes, n)
        mas[n] = s[-1] if s else None
    s200_series = sma(closes, 200)
    mas["_s200_slope"] = _lin_slope(s200_series[-25:]) if len(s200_series) >= 25 else None
    ma30w = sma(closes, 150)     # 30주 ≈ 150거래일

    lookback = min(len(closes), 252)
    hi52 = max(c["high"] for c in candles[-lookback:])
    lo52 = min(c["low"] for c in candles[-lookback:])

    stage = stage_analysis(closes, ma30w)
    rs = relative_strength(closes, bench_closes) if bench_closes else None
    tt = trend_template(closes, mas, hi52, lo52)
    v = vcp(candles)
    box = darvas_box(candles)
    ob = obv_trend(candles)
    fib = fibonacci(candles)
    a = atr(candles)
    disp = disparity(price, mas)

    atr_pct = round(a / price * 100, 2) if a and price else None

    # ---- 종합 점수: 기법별 점수를 가중 결합
    parts, signals = {}, []

    if stage:
        st_score = {2: 90.0, 1: 55.0, 3: 40.0, 4: 15.0}[stage["stage"]]
        parts["스테이지"] = st_score
        signals.append(("bull" if stage["stage"] == 2 else
                        "bear" if stage["stage"] == 4 else "neutral", stage["label"]))
    if tt:
        parts["추세템플릿"] = tt["score"]
        if tt["passed"] >= 6:
            signals.append(("bull", f"미너비니 추세 템플릿 {tt['passed']}/{tt['total']} 충족 — 주도주 요건"))
        elif tt["passed"] <= 3:
            signals.append(("bear", f"추세 템플릿 {tt['passed']}/{tt['total']}만 충족 — 추세 미형성"))
    if rs:
        parts["상대강도"] = rs["score"]
        if rs["excess"] > 10:
            signals.append(("bull", f"시장 대비 {rs['excess']:+.0f}%p 초과성과 — 상대강도 우수"))
        elif rs["excess"] < -10:
            signals.append(("bear", f"시장 대비 {rs['excess']:+.0f}%p 열위 — 상대강도 부진"))
    if v:
        parts["VCP"] = v["score"]
        if v["contracting"] and v["vol_drying"]:
            signals.append(("bull", "변동성 수축(VCP) — 매물 소화 후 돌파 대기 구간"))
    if ob:
        parts["OBV"] = ob["score"]
        if ob["divergence"] == "bullish":
            signals.append(("bull", "가격 대비 OBV 강세 다이버전스 — 세력 매집 정황"))
        elif ob["divergence"] == "bearish":
            signals.append(("bear", "가격 대비 OBV 약세 다이버전스 — 분산 정황"))
    if box:
        if box["breakout"]:
            parts["박스"] = 85.0
            signals.append(("bull", f"박스권 상단({box['top']:,.0f}) 돌파 — 신고가 시도"))
        elif box["breakdown"]:
            parts["박스"] = 20.0
            signals.append(("bear", f"박스권 하단({box['bottom']:,.0f}) 이탈 — 지지 붕괴"))
        else:
            parts["박스"] = 50.0
    if fib:
        signals.append(("neutral", f"피보나치 {fib['retrace_pct']:.0f}% 되돌림 — {fib['zone']}"))

    weights = {"스테이지": 0.30, "추세템플릿": 0.25, "상대강도": 0.20,
               "VCP": 0.10, "OBV": 0.10, "박스": 0.05}
    tw = sum(w for k, w in weights.items() if k in parts)
    score = sum(parts[k] * weights[k] for k in parts) / tw if tw else 50.0

    if atr_pct and atr_pct > 5:
        signals.append(("warn", f"ATR 변동성 {atr_pct}% — 일간 등락이 큼, 포지션 크기 축소 권장"))

    return {
        "available": True,
        "score": round(_clamp(score), 1),
        "parts": {k: round(v, 1) for k, v in parts.items()},
        "stage": stage,
        "relative_strength": rs,
        "trend_template": tt,
        "vcp": v,
        "box": box,
        "obv": ob,
        "fibonacci": fib,
        "atr": round(a, 1) if a else None,
        "atr_pct": atr_pct,
        "disparity": disp,
        "signals": [{"type": t, "text": s} for t, s in signals],
        "bars": len(candles),
    }
