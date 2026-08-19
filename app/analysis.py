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


def _score_low(value, best, worst, floor=15.0, top=96.0):
    """낮을수록 좋은 지표(PER·PBR): best 이하→top점, worst 이상→floor점, 사이는 완만하게.
    극단값도 0점으로 떨어지지 않도록 하한(floor)을 둔다."""
    if value is None or value <= 0:
        return None
    if value <= best:
        return top
    if value >= worst:
        return floor
    t = (value - best) / (worst - best)  # 0..1
    return top - t * (top - floor)


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


def _slope_pct(series, lookback=10):
    """이동평균선의 최근 기울기를 % 로 반환 (양수=상승 중, 음수=하락 중).
    series: sma() 결과(오름차순, 마지막이 최신). 데이터 부족 시 None."""
    if not series or len(series) < lookback + 1:
        return None
    old = series[-lookback - 1]
    new = series[-1]
    if not old:
        return None
    return (new - old) / abs(old) * 100.0


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


def _support_resistance(highs, lows, price, lookback=140, span=4):
    """스윙 고점/저점 기반 지지·저항.
    현재가 아래에서 가장 가까운(=가장 높은) 스윙 저점 = 지지,
    현재가 위에서 가장 가까운(=가장 낮은) 스윙 고점 = 저항.
    근접 레벨은 군집화해 여러 번 눌린 강한 레벨을 우선한다."""
    n = len(highs)
    start = max(0, n - lookback)
    swing_hi, swing_lo = [], []
    for i in range(start + span, n - span):
        if highs[i] >= max(highs[i - span:i + span + 1]):
            swing_hi.append(highs[i])
        if lows[i] <= min(lows[i - span:i + span + 1]):
            swing_lo.append(lows[i])

    def cluster(levels):
        clusters = []
        for lv in sorted(levels):
            if clusters and abs(lv - clusters[-1]["level"]) / clusters[-1]["level"] < 0.015:
                c = clusters[-1]
                c["touches"] += 1
                c["level"] = (c["level"] * (c["touches"] - 1) + lv) / c["touches"]
            else:
                clusters.append({"level": lv, "touches": 1})
        return clusters

    lo_c = cluster(swing_lo)
    hi_c = cluster(swing_hi)
    below = [c["level"] for c in lo_c if c["level"] < price * 0.997]
    above = [c["level"] for c in hi_c if c["level"] > price * 1.003]

    support = max(below) if below else min(lows[start:])
    resistance = min(above) if above else max(highs[start:])
    # 안전장치: 지지<현재가<저항 강제
    if support >= price:
        support = min(lows[start:])
    if resistance <= price:
        resistance = max(price * 1.05, max(highs[start:]))
    return support, resistance


def _timing_comment(trend, momentum, r, macd_v, vol_ratio, pos52, cross, bb, verdict_cls) -> str:
    """실제 계산된 지표값 중 가장 두드러진 근거를 골라 종목별로 다른 한줄평을 만든다.
    (verdict 4구간만으로 문장을 고르면 같은 구간 종목이 전부 똑같은 문장이 되는 문제 방지)"""
    reasons = []  # (우선순위 낮을수록 먼저 채택, 문장)

    if cross == "golden" and vol_ratio and vol_ratio >= 1.2:
        reasons.append((1, "거래량을 동반한 골든크로스가 나와 추세 전환 신뢰도가 높은 편입니다"))
    elif cross == "golden":
        reasons.append((2, "골든크로스가 나왔지만 거래량 동반이 약해 좀 더 지켜볼 필요가 있습니다"))
    elif cross == "dead":
        reasons.append((1, "데드크로스가 발생해 단기 추세가 꺾인 상태입니다"))

    if r is not None:
        if r >= 75:
            reasons.append((1, f"RSI {r:.0f}로 과열 구간이라 추격 매수보다 되돌림을 기다리는 편이 안전합니다"))
        elif r >= 70:
            reasons.append((2, f"RSI {r:.0f}로 과열권에 진입해 단기 조정 가능성이 있습니다"))
        elif r <= 25:
            reasons.append((1, f"RSI {r:.0f}로 과매도 구간이라 기술적 반등을 노려볼 만합니다"))
        elif r <= 30:
            reasons.append((2, f"RSI {r:.0f}로 과매도권에 근접해 반등 여부를 지켜볼 만합니다"))

    if macd_v:
        if macd_v["hist"] > 0 and macd_v["hist_prev"] <= 0:
            reasons.append((1, "MACD가 막 상승 전환해 초기 매수 신호로 볼 수 있습니다"))
        elif macd_v["hist"] < 0 and macd_v["hist_prev"] >= 0:
            reasons.append((1, "MACD가 막 하락 전환해 주의가 필요한 시점입니다"))

    if trend >= 58 and momentum <= 42:
        reasons.append((1, "추세는 살아있지만 모멘텀(MACD·RSI)이 둔화되고 있어 속도조절 구간으로 보입니다"))
    elif trend <= 42 and momentum >= 58:
        reasons.append((1, "추세는 아직 약하지만 모멘텀 지표는 개선되고 있어 바닥을 다지는 신호일 수 있습니다"))

    if pos52 is not None:
        if pos52 >= 90:
            reasons.append((2, f"52주 최고가 대비 {100 - pos52:.0f}%밖에 안 남아 신고가 돌파 시도 구간입니다"))
        elif pos52 <= 15:
            reasons.append((2, f"52주 최저가권({pos52:.0f}%)까지 눌려 있어 낙폭과대 반등 가능성을 열어둘 자리입니다"))

    if vol_ratio:
        rising = trend >= 50
        if vol_ratio >= 1.5:
            reasons.append((2, f"최근 거래량이 평균 대비 {vol_ratio:.1f}배로 {'매수세 유입' if rising else '매도 물량 출회'}이 뚜렷합니다"))
        elif vol_ratio < 0.6:
            reasons.append((3, "거래량이 크게 위축돼 있어 방향성 확인이 더 필요한 구간입니다"))

    if bb:
        if bb.get("pct_b", 0.5) >= 1.0:
            reasons.append((2, "볼린저밴드 상단을 이탈해 단기 변동성이 커진 상태입니다"))
        elif bb.get("pct_b", 0.5) <= 0.0:
            reasons.append((2, "볼린저밴드 하단을 이탈해 낙폭이 과도한 상태입니다"))

    # 위 강한 신호가 하나도 없는(=진짜 잔잔한) 종목은, 그마저도 완전히 같은 문장이
    # 되지 않도록 추세 방향·RSI 정도로 약한 단서라도 짚어준다.
    if not reasons:
        if trend >= 54:
            reasons.append((4, "단기 이동평균이 완만하게 우상향 중이지만 뚜렷한 매매 신호는 아직 없습니다"))
        elif trend <= 46:
            reasons.append((4, "단기 이동평균이 살짝 눌려 있지만 추세 이탈로 보긴 이른 수준입니다"))
        elif r is not None:
            reasons.append((4, f"RSI {r:.0f}로 과열도 과매도도 아닌 중립 구간이라 특별한 신호가 없는 상태입니다"))

    reasons.sort(key=lambda x: x[0])
    core = reasons[0][1] if reasons else "뚜렷한 방향성 신호가 없어 다음 신호를 기다리는 구간입니다"

    action = {
        "buy": "현재가 부근이나 20일선 눌림목에서 분할 매수를 고려할 만합니다.",
        "accumulate": "한 번에 매수하기보다 지지선 부근에서 2~3회 나눠 진입하는 편이 유리합니다.",
        "hold": "지지선·저항선 사이 박스권으로, 지지선 이탈 시 관망하고 저항 재돌파 시 추격을 고려하세요.",
        "avoid": "저항선 회복이나 추세 전환 신호(골든크로스·RSI 반등) 전까지는 신규 진입을 미루는 편이 안전합니다.",
    }[verdict_cls]

    return f"{core}. {action}"


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
    ma_series = {}
    for n in (5, 20, 60, 120):
        s = sma(closes, n)
        ma_series[n] = s
        mas[n] = s[-1] if s else None

    # 이동평균 기울기(추세 방향): 20일선·60일선의 최근 10봉 변화율
    slope20 = _slope_pct(ma_series[20], 10)
    slope60 = _slope_pct(ma_series[60], 10)

    r = rsi(closes)
    m = macd(closes)
    bb = bollinger(closes)

    lookback = min(len(closes), 250)
    hi52 = max(highs[-lookback:])
    lo52 = min(lows[-lookback:])
    pos52 = (price - lo52) / (hi52 - lo52) * 100 if hi52 != lo52 else 50

    # 지지/저항: 스윙 고점·저점 기반 (현재가에 가장 가까운 유효 레벨)
    support, resistance = _support_resistance(highs, lows, price)

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
    # 4개 축을 별도로 산출(각 0~100) 후 가중 결합한다:
    #   추세(45%)·모멘텀(30%)·변동성/위치(15%)·거래량(10%)
    # 단순 가감산이 아니라 축별 정규화라, 한쪽 신호가 과도하게 쌓이는 걸 막는다.
    signals = []
    vol_confirm = bool(vol_ratio and vol_ratio >= 1.2)   # 거래량 동반 여부(신호 신뢰도)

    # ── 1) 추세 축: 이평 배열 + 기울기 + 장기(120)선 ──────────────
    trend = 50.0
    if mas[20] and mas[60]:
        if price > mas[20] > mas[60]:
            trend += 18
            signals.append(("bull", "주가가 20·60일선 위 — 정배열 상승 추세"))
            if mas[120] and mas[60] > mas[120]:
                trend += 8
                signals.append(("bull", "20>60>120일선 완전 정배열 — 장기 상승 구조"))
        elif price < mas[20] < mas[60]:
            trend -= 18
            signals.append(("bear", "주가가 20·60일선 아래 — 역배열 하락 추세"))
            if mas[120] and mas[60] < mas[120]:
                trend -= 8
                signals.append(("bear", "20<60<120일선 완전 역배열 — 장기 하락 구조"))
        elif price > mas[20]:
            trend += 7
            signals.append(("bull", "주가가 20일선 위 — 단기 추세 양호"))
        else:
            trend -= 7
            signals.append(("bear", "주가가 20일선 아래 — 단기 추세 약화"))
    # 이동평균 기울기(방향) — 위/아래보다 '지금 올라가는 중인가'가 핵심
    if slope20 is not None:
        if slope20 > 1.0:
            trend += 8
            signals.append(("bull", f"20일선이 우상향(+{slope20:.1f}%) — 상승 추세 강화"))
        elif slope20 < -1.0:
            trend -= 8
            signals.append(("bear", f"20일선이 우하향({slope20:.1f}%) — 하락 추세 지속"))
    if slope60 is not None:
        if slope60 > 0.5:
            trend += 5
        elif slope60 < -0.5:
            trend -= 5
    if cross == "golden":
        bonus = 10 if vol_confirm else 6
        trend += bonus
        signals.append(("bull", "최근 골든크로스(20일선 상향 돌파)"
                        + (" — 거래량 동반으로 신뢰도 높음" if vol_confirm else "")))
    elif cross == "dead":
        trend -= 10
        signals.append(("bear", "최근 데드크로스(20일선 하향 이탈) 발생"))
    trend = _clamp(trend)

    # ── 2) 모멘텀 축: MACD + RSI ─────────────────────────────────
    momentum = 50.0
    if m:
        if m["hist"] > 0 and m["hist_prev"] <= 0:
            momentum += 20
            signals.append(("bull", "MACD 히스토그램 양전환 — 상승 모멘텀 발생"))
        elif m["hist"] < 0 and m["hist_prev"] >= 0:
            momentum -= 20
            signals.append(("bear", "MACD 히스토그램 음전환 — 하락 모멘텀 발생"))
        elif m["hist"] > 0:
            momentum += 10
            signals.append(("bull", "MACD 상승 모멘텀 유지 중"))
        else:
            momentum -= 10
            signals.append(("bear", "MACD 하락 모멘텀 유지 중"))
    if r is not None:
        if r >= 75:
            momentum -= 16
            signals.append(("warn", f"RSI {r:.0f} — 과열 구간, 추격 매수 주의"))
        elif r >= 70:
            momentum -= 8
            signals.append(("warn", f"RSI {r:.0f} — 과열 진입, 단기 조정 가능"))
        elif r <= 25:
            momentum += 16
            signals.append(("bull", f"RSI {r:.0f} — 과매도, 기술적 반등 가능성"))
        elif r <= 30:
            momentum += 8
            signals.append(("bull", f"RSI {r:.0f} — 과매도 진입, 반등 관찰"))
        elif 50 <= r < 70:
            momentum += 5     # 중립 상단: 건강한 상승 모멘텀
            signals.append(("neutral", f"RSI {r:.0f} — 상승 우위의 중립 구간"))
        else:
            signals.append(("neutral", f"RSI {r:.0f} — 중립 구간"))
    momentum = _clamp(momentum)

    # ── 3) 변동성·위치 축: 볼린저 + 52주 위치 ────────────────────
    posn = 50.0
    if bb:
        if bb["pct_b"] >= 1.0:
            posn -= 12
            signals.append(("warn", "볼린저밴드 상단 돌파 — 변동성 확대·과열 주의"))
        elif bb["pct_b"] <= 0.0:
            posn += 12
            signals.append(("bull", "볼린저밴드 하단 이탈 — 낙폭 과대 반등 관찰"))
        elif bb["pct_b"] >= 0.8:
            posn -= 4
        elif bb["pct_b"] <= 0.2:
            posn += 4
    # 52주 위치: 신고가권(모멘텀) vs 신저가권(약세). 극단 과열은 소폭 감점
    if pos52 >= 90:
        posn += 6
        signals.append(("bull", f"52주 최고가 근접({pos52:.0f}%) — 강한 상승 모멘텀"))
    elif pos52 >= 65:
        posn += 10
    elif pos52 <= 15:
        posn -= 10
        signals.append(("bear", f"52주 최저가 근접({pos52:.0f}%) — 약세 흐름"))
    posn = _clamp(posn)

    # ── 4) 거래량 축: 추세 방향으로의 거래량 확인 ────────────────
    vol_axis = 50.0
    if vol_ratio:
        rising = price > (mas[20] or price)
        if vol_ratio >= 1.5 and rising:
            vol_axis += 22
            signals.append(("info", f"거래량 20일 평균 {vol_ratio:.1f}배 + 상승 — 매수세 유입"))
        elif vol_ratio >= 1.5 and not rising:
            vol_axis -= 18
            signals.append(("warn", f"거래량 {vol_ratio:.1f}배 + 하락 — 매도 물량 출회"))
        elif vol_ratio >= 1.2:
            vol_axis += 8 if rising else -6
        elif vol_ratio < 0.6:
            signals.append(("neutral", "거래량 위축 — 관망세, 방향성 대기"))
    vol_axis = _clamp(vol_axis)

    score = _clamp(trend * 0.45 + momentum * 0.30 + posn * 0.15 + vol_axis * 0.10)

    # ---- 진입 타이밍 판단
    if score >= 70:
        verdict, verdict_cls = "매수 우위", "buy"
    elif score >= 55:
        verdict, verdict_cls = "분할 매수 관점", "accumulate"
    elif score >= 40:
        verdict, verdict_cls = "관망", "hold"
    else:
        verdict, verdict_cls = "보수적 접근", "avoid"

    # 한줄평: 종합점수 구간 4개로만 나누면 같은 구간 종목이 전부 똑같은 문장이 된다
    # (특히 '관망' 구간에 몰림). 실제로 계산된 RSI·거래량·크로스·추세/모멘텀 상충 여부 등
    # 구체적 근거 중 가장 두드러진 것을 골라 종목마다 다른 문장을 만든다.
    timing = _timing_comment(trend, momentum, r, m, vol_ratio, pos52, cross, bb, verdict_cls)

    # 매수 관심 구간: 지지선 ~ 현재가 아래 가장 가까운 지지(스윙 지지·20일선·60일선)
    anchors = [x for x in (support, mas.get(20), mas.get(60)) if x and x < price * 0.999]
    buy_anchor = max(anchors) if anchors else support
    entry = {
        "support": round(support),
        "resistance": round(resistance),
        "buy_zone_low": round(support),
        "buy_zone_high": round(buy_anchor * 1.01),
        "sell_zone_low": round(resistance * 0.98),
        "sell_zone_high": round(resistance),
        "stop_loss": round(support * 0.96),
    }

    # 1차 목표가 = 가장 가까운 저항(현실적 도달선)
    tech_target = round(resistance if resistance > price * 1.015
                        else price + max(price - support, price * 0.04))

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
        "ma20_slope": round(slope20, 2) if slope20 is not None else None,
        "ma60_slope": round(slope60, 2) if slope60 is not None else None,
        "score": round(score, 1),
        "score_parts": {
            "추세": round(trend, 1), "모멘텀": round(momentum, 1),
            "위치": round(posn, 1), "거래량": round(vol_axis, 1),
        },
        "signals": [{"type": t, "text": s} for t, s in signals],
        "verdict": verdict, "verdict_class": verdict_cls,
        "timing_comment": timing,
        "entry": entry,
        "tech_target": tech_target,
    }


# ---------------------------------------------------------------- fundamentals
def _finance_rows(finance_data) -> dict:
    """finance API → {행이름: [(기간key, 값, isConsensus)]} (기간 오름차순)
    국내: {financeInfo: {trTitleList, rowList}} / 미국: {trTitleList, rowList} 최상위"""
    info = (finance_data or {}).get("financeInfo") or finance_data or {}
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


def _exact_row(rows, *names):
    for name, series in rows.items():
        if name.strip() in names:
            return series
    return None


# 재무 하이라이트에 노출할 행 — (표시이름, [원본 행 이름 후보들], 단위힌트)
# 단위힌트: "eok"=금액(억), "pct"=%, "x"=배수, "won"=원
_HIGHLIGHT_SPEC = [
    ("매출액", ["매출액"], "eok"),
    ("영업이익", ["영업이익", "EBIT"], "eok"),
    ("당기순이익", ["당기순이익", "세후손익", "순이익"], "eok"),
    ("영업이익률", ["영업이익률"], "pct"),
    ("순이익률", ["순이익률"], "pct"),
    ("ROE", ["ROE"], "pct"),
    ("ROA", ["ROA"], "pct"),
    ("EPS", ["EPS"], "won"),
    ("PER", ["PER"], "x"),
    ("PBR", ["PBR"], "x"),
    ("부채비율", ["부채비율"], "pct"),
]


def _highlight_rows(rows: dict, market: str = "KR") -> dict:
    """전체 재무 행에서 핵심 지표만 지정 순서로 추출. 각 행에 unit 힌트를 부착.

    ⚠️ 단위 함정: 미국 종목은 finance API 금액 행(매출액·영업이익·당기순이익)이
    백만 USD로 온다(EV/EBITDA와 동일 — valuation.py의 ev_ebitda 참고). 그대로
    "eok"(억) 단위로 표시하면 100배 부풀려진다 → US는 eok 행만 100으로 나눠 맞춘다.
    """
    out = {}
    for disp, names, unit in _HIGHLIGHT_SPEC:
        series = None
        for nm in names:
            if nm in rows and rows[nm]:
                series = rows[nm]
                break
            for k in rows:
                if k.strip() == nm and rows[k]:
                    series = rows[k]
                    break
            if series:
                break
        if series and any(s.get("value") is not None for s in series):
            if market == "US" and unit == "eok":
                series = [{**s, "value": s["value"] / 100.0 if s["value"] is not None else None} for s in series]
            out[disp] = {"unit": unit, "series": series}
    return out


def fundamental_analysis(infos: dict, fin_annual: dict, market: str = "KR") -> dict:
    """infos: {code: value} (국내=integration.totalInfos, 미국=basic.stockItemTotalInfos)"""
    infos = infos or {}
    per = to_num(infos.get("per"))
    cns_per = to_num(infos.get("cnsPer"))
    pbr = to_num(infos.get("pbr"))
    eps = to_num(infos.get("eps"))
    bps = to_num(infos.get("bps"))
    dividend_yield = to_num(infos.get("dividendYieldRatio"))
    market_cap = parse_eok(infos.get("marketValue"))  # 억(현지통화)

    rows = _finance_rows(fin_annual)
    _, rev_s = _row_match(rows, "매출액")
    # 영업이익: 국내 '영업이익' / 미국 'EBIT'
    op_s = _exact_row(rows, "영업이익", "EBIT")
    # 순이익: 국내 '당기순이익' / 미국 '당기순이익','세후손익'
    ni_s = _exact_row(rows, "당기순이익", "세후손익", "순이익")
    # 수익성 지표: ROE(국내). 미국은 ROE 미제공 → 마진/ROA로 대체(아래)
    _, roe_s = _row_match(rows, "ROE")
    _, roa_s = _row_match(rows, "ROA")
    _, opm_s = _row_match(rows, "영업이익률")
    _, npm_s = _row_match(rows, "순이익률")
    _, debt_s = _row_match(rows, "부채비율")
    _, retain_s = _row_match(rows, "유보율")

    roe = _last_actual(roe_s)
    # 미국은 ROE 원본 데이터가 없다(Naver API 확인 완료, 영구적 한계) → EPS/BPS로 근사.
    # ROE = 순이익/자기자본 = (순이익/주식수)÷(자기자본/주식수) = EPS/BPS 이므로 유효한 근사치.
    # 이게 없으면 미국은 수익성 점수를 영업이익률·순이익률 2개 평균으로만 매겨 100점
    # saturate가 국내(ROE 포함 3개 평균)보다 훨씬 쉬워지는 시장간 불공정이 생긴다.
    if roe is None and eps is not None and bps and bps > 0:
        roe = eps / bps * 100.0
    roa = _last_actual(roa_s)
    opm = _last_actual(opm_s)
    npm = _last_actual(npm_s)
    debt = _last_actual(debt_s)
    retain = _last_actual(retain_s)

    rev_cur, rev_prev, rev_cns = _last_actual(rev_s), _prev_actual(rev_s), _consensus_val(rev_s)
    op_cur, op_prev, op_cns = _last_actual(op_s), _prev_actual(op_s), _consensus_val(op_s)
    ni_cur, ni_prev = _last_actual(ni_s), _prev_actual(ni_s)

    # 마진 미제공(미국) 시 원자료로 계산
    if opm is None and op_cur is not None and rev_cur:
        opm = op_cur / rev_cur * 100.0
    if npm is None and ni_cur is not None and rev_cur:
        npm = ni_cur / rev_cur * 100.0

    rev_growth = _growth(rev_cur, rev_prev)
    op_growth = _growth(op_cur, op_prev)
    if op_growth is None:            # 미국: 영업이익 성장 없으면 순이익 성장
        op_growth = _growth(ni_cur, ni_prev)
    rev_growth_fwd = _growth(rev_cns, rev_cur)
    op_growth_fwd = _growth(op_cns, op_cur)
    ni_cns = _consensus_val(ni_s)

    # ---- 컨센서스 이상치 검증(2026-08 진단리포트 지적사항) ------------------------
    # Naver 컨센서스는 소수 애널리스트 추정치의 단순평균이라, 이상값 하나가 그대로
    # 평균에 섞여 물리적으로 불가능한 전망(예: SK하이닉스 2026 영업이익률 77%)이
    # 나오는 경우가 실측으로 확인됨. 이 값이 선행PER·PEG·성장점수로 그대로 전파되면
    # "검증 안 된 숫자 하나"가 종합점수 85점·A등급까지 끌어올리는 문제가 생긴다.
    # → 점수·밸류에이션 계산에서는 제외하고, 원본 값은 화면 표시용으로만 별도 보존한다.
    cns_per_raw, op_growth_fwd_raw, rev_growth_fwd_raw = cns_per, op_growth_fwd, rev_growth_fwd
    cns_opm = (op_cns / rev_cns * 100.0) if (rev_cns and op_cns is not None) else None
    cns_npm = (ni_cns / rev_cns * 100.0) if (rev_cns and ni_cns is not None) else None
    flag_reasons = []
    if cns_opm is not None and cns_opm > 60:
        flag_reasons.append(f"추정 영업이익률 {cns_opm:.0f}%")
    if cns_npm is not None and cns_npm > 50:
        flag_reasons.append(f"추정 순이익률 {cns_npm:.0f}%")
    if rev_growth_fwd is not None and rev_growth_fwd > 100:
        flag_reasons.append(f"매출 전망 {rev_growth_fwd:+.0f}%")
    consensus_flagged = bool(flag_reasons)
    if consensus_flagged:
        cns_per, op_growth_fwd, rev_growth_fwd = None, None, None

    # ---- 점수 계산
    # 가치평가: PER·PBR 낮을수록, 배당 높을수록. 성장주는 선행(추정) PER을 반영.
    if per and per > 0 and cns_per and cns_per > 0:
        per_eval = per * 0.4 + cns_per * 0.6   # 선행 실적 기대를 더 크게 반영
    elif per and per > 0:
        per_eval = per
    elif cns_per and cns_per > 0:
        per_eval = cns_per
    else:
        per_eval = None
    per_score = _score_low(per_eval, best=8, worst=60)
    if per_score is None and eps is not None and eps < 0:
        per_score = 28.0  # 적자: 저평가 아님(리스크), 다만 0은 아님
    pbr_score = _score_low(pbr, best=0.8, worst=8)
    div_score = _scale(dividend_yield, 0, 4.5) if dividend_yield is not None else None
    # 가중 평균(PER·PBR 비중 크게, 배당은 보조)
    vw = []
    if per_score is not None: vw.append((per_score, 0.45))
    if pbr_score is not None: vw.append((pbr_score, 0.40))
    if div_score is not None: vw.append((div_score, 0.15))
    value_score = (sum(s * w for s, w in vw) / sum(w for _, w in vw)) if vw else 50

    # PEG 보정: 성장 대비 밸류. 고성장주의 높은 PER을 일부 정당화하되,
    # 저기반(적자→흑자·회복 구간)에서 나온 폭발적 성장률이 밸류를 부풀리지 않도록
    # ① 실적 성장을 우선하고 ② 전망 성장률은 40%로 상한 ③ 보정 강도도 완화한다.
    peg_cands = [g for g in (op_growth, rev_growth) if g is not None and g > 0]
    if op_growth_fwd is not None and op_growth_fwd > 0:
        peg_cands.append(min(op_growth_fwd, 40.0))       # 전망치는 상한
    growth_for_peg = None
    if peg_cands:
        peg_cands.sort()
        growth_for_peg = min(40.0, peg_cands[len(peg_cands) // 2])   # 중앙값·40 상한
    if per_eval and per_eval > 0 and growth_for_peg and growth_for_peg > 5:
        peg = per_eval / growth_for_peg
        peg_score = _score_low(peg, best=0.8, worst=3.0)  # PEG 0.8이하 최고·3이상 최저
        value_score = value_score * 0.65 + peg_score * 0.35  # 보정 완화(0.5→0.35)

    # 수익성
    roe_score = _scale(roe, 0, 20)
    opm_score = _scale(opm, 0, 25)
    npm_score = _scale(npm, 0, 20)
    prof_parts = [s for s in (roe_score, opm_score, npm_score) if s is not None]
    prof_score = sum(prof_parts) / len(prof_parts) if prof_parts else 50

    # 성장성 (과거 + 컨센서스 전망 모두 반영)
    # 상한을 높여(25→30, 50→60) 저기반 회복성 폭증이 손쉽게 만점을 받지 않게 한다.
    g_parts = [s for s in (
        _scale(rev_growth, -10, 30),
        _scale(op_growth, -25, 60),
        _scale(rev_growth_fwd, -10, 30),
        _scale(op_growth_fwd, -25, 60),
    ) if s is not None]
    growth_score = sum(g_parts) / len(g_parts) if g_parts else 50

    # 안정성: 국내는 부채비율·유보율, 미국(부채 데이터 없음)은 흑자·자산효율 프록시
    # ⚠️ 은행·보험·지주는 부채비율이 구조적으로 1000%+ 라 이를 '위험'으로 보면 안 된다.
    #    → 극단적 고부채(>500%)는 금융업 추정으로 중립(45) 처리하고,
    #      일반 기업은 완만한 척도(≤50%→우수, ≥300%→하한)로 평가한다.
    if debt is None:
        debt_score = None
    elif debt > 500:
        debt_score = 45.0
    else:
        debt_score = _score_low(debt, best=50, worst=300, floor=20, top=100)
    retain_score = _scale(retain, 0, 3000)
    st_parts = [s for s in (debt_score, retain_score) if s is not None]
    if st_parts:
        stability_score = sum(st_parts) / len(st_parts)
        # 수익성 쿠션: 꾸준한 흑자·양호한 ROE는 상환능력의 방증 → 안정성 하한을 올린다.
        # (약점 기업을 끌어올리는 '바닥'이지, 이미 높은 점수를 더 올리진 않는다 — 상한 60)
        if roe is not None and roe > 0:
            stability_score = max(stability_score, min(60.0, _clamp(35 + roe * 1.5)))
    else:
        proxy = []
        if npm is not None:
            proxy.append(_clamp(45 + npm * 1.6))   # 순이익률 0→45, 34%→100
        if roa is not None:
            proxy.append(_clamp(40 + roa * 2.2))   # ROA 0→40, 27%→100
        stability_score = sum(proxy) / len(proxy) if proxy else 62

    return {
        "metrics": {
            "per": per, "cns_per": cns_per, "pbr": pbr, "eps": eps, "bps": bps,
            "cns_eps": to_num(infos.get("cnsEps")),
            "dividend_yield": dividend_yield, "market_cap": market_cap,
            "roe": roe, "op_margin": opm, "net_margin": npm,
            "debt_ratio": debt, "retention_ratio": retain,
            "rev_growth": round(rev_growth, 1) if rev_growth is not None else None,
            "op_growth": round(op_growth, 1) if op_growth is not None else None,
            "rev_growth_fwd": round(rev_growth_fwd, 1) if rev_growth_fwd is not None else None,
            "op_growth_fwd": round(op_growth_fwd, 1) if op_growth_fwd is not None else None,
            "consensus_flagged": consensus_flagged,
            "consensus_flag_reason": " · ".join(flag_reasons) if flag_reasons else None,
            "cns_per_raw": round(cns_per_raw, 2) if cns_per_raw else None,
            "op_growth_fwd_raw": round(op_growth_fwd_raw, 1) if op_growth_fwd_raw is not None else None,
            "rev_growth_fwd_raw": round(rev_growth_fwd_raw, 1) if rev_growth_fwd_raw is not None else None,
        },
        # 재무 하이라이트 표: 연도별 추이를 보여줄 핵심 지표들(있는 것만, 지정 순서대로).
        # 국내/미국 행 이름이 달라(영업이익/EBIT, 당기순이익/세후손익 등) 별칭으로 매칭한다.
        "finance_rows": _highlight_rows(rows, market),
        # 밸류에이션 분석용 전체 행(EPS·PER·EBITDA 등) — 응답에는 싣지 않는다
        "all_rows": rows,
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


def news_sentiment(news_items: list, stock_name: str = None) -> dict:
    """뉴스 감성 분석. stock_name이 주어지면 제목·본문에 종목명이 등장하는지로 관련성을
    판정해, 무관한 기사(예: 증권사 프로모션·타 상품 세미나 안내가 특정 종목 뉴스탭에
    잘못 태깅된 경우)가 심리 점수를 오염시키지 않게 한다(진단리포트 실측 사례: SK하이닉스
    뉴스탭에 "카카오페이증권 주식 축의금 캠페인" 등 무관 기사가 섞여 "시장 심리 긍정적
    70점"의 근거로 쓰이고 있었음). 화면에서는 숨기지 않고 "관련성 낮음" 표시만 한다 —
    사용자가 직접 판단할 수 있게 정직하게 보여주는 편이 낫다(이 프로젝트의 일관된 원칙)."""
    tagged = []
    total = 0
    for it in news_items:
        text = (it.get("title", "") + " " + it.get("body", ""))[:300]
        relevant = True if not stock_name else (stock_name in text)
        p = sum(1 for w in POS_WORDS if w in text)
        n = sum(1 for w in NEG_WORDS if w in text)
        if p > n:
            senti = "positive"
        elif n > p:
            senti = "negative"
        else:
            senti = "neutral"
        if relevant:   # 심리 점수 집계에는 관련성 있는 기사만 반영
            if senti == "positive": total += 1
            elif senti == "negative": total -= 1
        tagged.append({**it, "body": it.get("body", "")[:120], "sentiment": senti, "relevant": relevant})
    relevant_count = sum(1 for t in tagged if t["relevant"]) or len(tagged) or 1
    ratio = total / relevant_count  # -1 ~ 1
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


def total_evaluation(fund: dict, tech: dict, senti: dict, cons: dict, deal_trend: list,
                     pro: dict = None) -> dict:
    fs = fund["scores"]

    # 수급 점수: 최근 5일 외국인+기관 순매수 일수 (국내 전용, 데이터 없으면 중립)
    flow_score = None
    days = (deal_trend or [])[:5]
    if days:
        buy_days = 0
        for d in days:
            f = to_num(d.get("foreignerPureBuyQuant")) or 0
            o = to_num(d.get("organPureBuyQuant")) or 0
            if f + o > 0:
                buy_days += 1
        flow_score = _clamp(20 + buy_days * 15)

    tech_score = tech.get("score", 50) if tech.get("available") else 50
    # 고급 차트 분석(스테이지·상대강도·추세템플릿 등)이 있으면 기술적추세에 절반 반영.
    # 단기 지표(technical)와 장기 구조(chart_pro)를 함께 보게 하는 장치.
    if pro and pro.get("available") and pro.get("score") is not None:
        tech_score = tech_score * 0.5 + pro["score"] * 0.5

    # 시장심리: 뉴스 감성 + 애널리스트 컨센서스
    mkt_score = senti["score"]
    if cons.get("recomm_mean"):
        mkt_score = (mkt_score + _clamp((cons["recomm_mean"] - 1) / 4 * 100)) / 2

    # 수급·심리: 수급 데이터 있으면 반영, 없으면(미국) 심리만
    flow_senti = round((flow_score + mkt_score) / 2, 1) if flow_score is not None else round(mkt_score, 1)
    categories = {
        "가치평가": fs["value"],
        "수익성": fs["profitability"],
        "성장성": fs["growth"],
        "재무안정성": fs["stability"],
        "기술적추세": tech_score,
        "수급·심리": flow_senti,
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
        "flow_score": round(flow_score, 1) if flow_score is not None else None,
    }


# ---------------------------------------------------------------- AI 최종판단(5단계)
VERDICT_TIERS = [
    (80, "buy", "매수", "🟢"),
    (65, "accumulate", "분할매수", "🟡"),
    (45, "hold", "보유", "🔵"),
    (30, "reduce", "분할매도", "🟠"),
    (0, "sell", "매도", "🔴"),
]


def final_verdict(total: dict, valuation: dict = None, cons: dict = None) -> dict:
    """종합점수 하나만 보여주는 대신, 5단계 매매판단(매수~매도)과 확신도로 번역한다.

    확신도는 점수를 다시 보여주는 게 아니라 **신호 간 합치도**를 잰다 — total_evaluation의
    6개 부문점수(+가능하면 밸류에이션 점수·컨센서스 상승여력)가 판단 방향과 얼마나
    일치하는지를 센다. 부문점수가 들쭉날쭉하면(강점·약점 혼재) 종합점수는 중간이어도
    확신도는 낮게 나온다. 과신을 피하기 위해 50~95% 범위로만 제한한다(100% 확신은
    절대 표시하지 않음).
    """
    score = total["total_score"]
    tier = label = emoji = None
    for th, t, lb, em in VERDICT_TIERS:
        if score >= th:
            tier, label, emoji = t, lb, em
            break

    signals = list(total["categories"].values())
    if valuation and valuation.get("available") and valuation.get("score") is not None:
        signals.append(valuation["score"])
    if cons and cons.get("upside") is not None:
        signals.append(_clamp(50 + cons["upside"]))

    if tier in ("buy", "accumulate"):
        agree = sum(1 for s in signals if s >= 55)
    elif tier in ("reduce", "sell"):
        agree = sum(1 for s in signals if s <= 45)
    else:
        agree = sum(1 for s in signals if 40 <= s <= 65)

    ratio = agree / len(signals) if signals else 0.5
    confidence = round(_clamp(50 + ratio * 45, 50, 95))

    return {"tier": tier, "label": label, "emoji": emoji, "confidence": confidence}


# 저기반(적자→흑자 등) 회복 시 성장률이 수백%로 튀는 걸 방지하는 표시 상한.
# app/anomaly.py GROWTH_DISPLAY_CAP·app/valuation.py PEG_GROWTH_CAP과 같은 50%를 쓴다 —
# 점수 계산(analysis.py 위쪽 _scale 호출들)은 이미 -25~60 구간으로 사실상 상한이 있지만,
# 서술 문장(build_opinion)은 원본을 그대로 찍고 있었다(실측 확인: 삼성전자 "영업이익
# 796.6% 증가" 문장이 그대로 노출됨) — 반드시 이 상수로 캡한 값만 문장에 넣을 것.
GROWTH_DISPLAY_CAP = 50.0


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
    if m.get("consensus_flagged"):
        lines.append(f"⚠️ 컨센서스 실적 추정치가 이상치로 감지되어({m.get('consensus_flag_reason')}) "
                     "밸류에이션·성장성 점수 반영에서 제외했습니다. 재무 탭의 원본 추정치는 검증 전 참고용입니다.")
    elif m["op_growth_fwd"] is not None:
        gf_disp = max(-GROWTH_DISPLAY_CAP, min(m["op_growth_fwd"], GROWTH_DISPLAY_CAP))
        note = " (저기반 효과로 상한 표시)" if abs(m["op_growth_fwd"]) > GROWTH_DISPLAY_CAP else ""
        if m["op_growth_fwd"] > 20:
            lines.append(f"증권가 컨센서스는 내년 영업이익이 {gf_disp:.0f}%{note} 증가할 것으로 전망하며, "
                         "미래 사업가치 측면에서 강한 성장 모멘텀이 기대됩니다.")
        elif m["op_growth_fwd"] < 0:
            lines.append(f"컨센서스 기준 영업이익이 {abs(gf_disp):.0f}%{note} 감소할 것으로 전망되어 실적 둔화 우려가 있습니다.")
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
