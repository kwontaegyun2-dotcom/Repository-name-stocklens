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
 11. 앵커드 VWAP (Anchored VWAP, Brian Shannon) — 주요 기준점 이후 거래량가중평균가
 12. 유동성 스윕 (Liquidity Sweep, ICT/Smart Money Concepts) — 직전 스윙 고/저 훑기
 13. 볼륨 프로파일 (Volume Profile) — 가격대별 거래량 분포, POC·Value Area
 14. 오더플로우 근사 (Order Flow proxy) — CLV 기반 매수/매도세 추정(⚠️ 근사치, 아래 설명)

⚠️ 11~14는 2026-08-19 벤치마킹 요청(Anchored VWAP·Liquidity Sweep·Volume Profile·
Order Flow)으로 추가됨. 이 프로젝트는 네이버의 **일봉 OHLCV**만 쓸 수 있고 틱·호가
(Level 2) 데이터가 없다 — 11·12·13은 일봉만으로도 원래 기법 그대로 정확히 계산 가능하지만,
14(Order Flow)는 원래 매수/매도 체결 데이터(틱)가 있어야 하는 기법이라 **진짜 오더플로우는
계산 불가능**하다. 대신 CLV(Close Location Value, Chaikin Money Flow와 같은 원리)로
근사치를 만들고 화면·데이터 어디에도 "근사치"임을 숨기지 않는다 — 이 프로젝트의 일관된
원칙(정직하게 보여주되 과장하지 않는다).
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


# ---------------------------------------------------------------- 앵커드 VWAP
def _avwap_series(candles, anchor_idx):
    """anchor_idx부터 끝까지, 그 시점 이후 매수한 모든 참여자의 평균 단가(거래량가중).
    typical price = (고가+저가+종가)/3 을 표준으로 쓴다(일반적인 VWAP 관례)."""
    if anchor_idx is None or anchor_idx < 0 or anchor_idx >= len(candles):
        return None
    cum_pv, cum_v = 0.0, 0.0
    out = []
    for c in candles[anchor_idx:]:
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        v = c.get("volume") or 0
        cum_pv += tp * v
        cum_v += v
        out.append(cum_pv / cum_v if cum_v else tp)
    return out


def anchored_vwap(candles):
    """앵커드 VWAP(브라이언 섀넌) — "의미 있는 기준점 이후 평균 매수단가"를 여러 앵커로
    계산한다. 현재가가 AVWAP 위면 그 시점 이후 진입자는 평균적으로 수익 중 → 되돌림 시
    지지로 작용하는 경향, 아래면 저항으로 작용하는 경향(실전에서 널리 쓰이는 해석).

    앵커 선정: 실적발표일 같은 이벤트는 이 프로젝트가 보유한 데이터로 특정할 수 없어(뉴스는
    있지만 "실적발표"로 명확히 태깅되지 않음), 객관적으로 계산 가능한 3개를 쓴다 —
    52주 신고가·52주 신저가(주요 스윙 기준점으로 흔히 쓰임)·연초(YTD, 기관들이 관용적으로
    쓰는 앵커).
    """
    n = len(candles)
    if n < 30:
        return None
    lookback = min(n, 252)
    window = candles[-lookback:]
    base = n - lookback
    hi_off = max(range(len(window)), key=lambda i: window[i]["high"])
    lo_off = min(range(len(window)), key=lambda i: window[i]["low"])
    anchors = {"52주 신고가": base + hi_off, "52주 신저가": base + lo_off}

    cur_year = candles[-1]["date"][:4]
    ytd_idx = next((i for i, c in enumerate(candles) if c["date"][:4] == cur_year), None)
    if ytd_idx is not None and ytd_idx < n - 5:   # 연초 앵커가 최근 5봉 이내면 의미 없음
        anchors["연초(YTD)"] = ytd_idx

    price = candles[-1]["close"]
    lines, above_count = {}, 0
    for label, idx in anchors.items():
        series = _avwap_series(candles, idx)
        if not series:
            continue
        avwap_now = series[-1]
        above = price > avwap_now
        above_count += 1 if above else 0
        lines[label] = {
            "anchor_date": candles[idx]["date"], "value": round(avwap_now, 1),
            "above": above, "series": [round(v, 2) for v in series],
        }
    if not lines:
        return None
    total = len(lines)
    score = _clamp(50 + (above_count - total / 2) / (total / 2) * 40) if total else 50.0
    return {"lines": lines, "above_count": above_count, "total": total, "score": round(score, 1)}


# ---------------------------------------------------------------- 유동성 스윕
def _pivots(candles, window=5):
    """단순 프랙탈 피벗 — 좌우 window개 봉보다 고가가 높으면 스윙고점, 저가가 낮으면 스윙저점."""
    highs, lows = [], []
    n = len(candles)
    for i in range(window, n - window):
        seg = candles[i - window:i + window + 1]
        if candles[i]["high"] == max(c["high"] for c in seg):
            highs.append(i)
        if candles[i]["low"] == min(c["low"] for c in seg):
            lows.append(i)
    return highs, lows


def liquidity_sweeps(candles, pivot_window=5, lookback=180, recent_n=15):
    """유동성 스윕(ICT/Smart Money Concepts) — 직전 스윙 고점/저점 부근에는 손절·역지정가
    주문이 몰려 있다("유동성 풀"). 가격이 그 레벨을 살짝 뚫었다가 곧바로 반대로 마감하면
    "그 유동성만 훑고(sweep) 원래 방향과 반대로 움직인" 것으로 본다 — 스윙고점 스윕 후
    하락 마감은 약세 신호(가짜 돌파로 매수 스탑을 턴 뒤 하락), 스윙저점 스윕 후 상승 마감은
    강세 신호."""
    if len(candles) < pivot_window * 2 + 10:
        return None
    seg_start = max(0, len(candles) - lookback)
    seg = candles[seg_start:]
    highs, lows = _pivots(seg, pivot_window)

    events = []
    for hi in highs:
        level = seg[hi]["high"]
        for j in range(hi + 1, min(hi + 30, len(seg))):
            if seg[j]["high"] > level and seg[j]["close"] < level:
                events.append({"type": "high_sweep", "idx": seg_start + j, "date": seg[j]["date"], "level": round(level, 1)})
                break
    for lo in lows:
        level = seg[lo]["low"]
        for j in range(lo + 1, min(lo + 30, len(seg))):
            if seg[j]["low"] < level and seg[j]["close"] > level:
                events.append({"type": "low_sweep", "idx": seg_start + j, "date": seg[j]["date"], "level": round(level, 1)})
                break
    events.sort(key=lambda e: e["idx"])

    recent_cut = len(candles) - recent_n
    recent = [e for e in events if e["idx"] >= recent_cut]
    bull = sum(1 for e in recent if e["type"] == "low_sweep")
    bear = sum(1 for e in recent if e["type"] == "high_sweep")
    score = _clamp(50 + (bull - bear) * 12)
    return {"events": events[-60:], "recent": recent, "bull_recent": bull, "bear_recent": bear, "score": round(score, 1)}


# ---------------------------------------------------------------- 볼륨 프로파일
def volume_profile(candles, lookback=120, bins=24):
    """볼륨 프로파일 — 가격대별 거래량 분포(시간이 아닌 "가격" 축 히스토그램).
    ⚠️ 진짜 볼륨 프로파일은 틱(체결) 데이터로 만들지만, 이 프로젝트엔 일봉만 있다.
    실전에서도 분봉 이하 데이터가 없을 때 흔히 쓰는 근사법 — 하루 거래량을 그 날의
    고가~저가 범위에 걸쳐 균등 분배해 누적한다. 정확한 틱별 분포는 아니지만, 어느
    가격대에 거래가 몰렸는지(POC)와 가치영역(Value Area, 거래량 70% 구간)의 큰 그림은
    유의미하게 근사된다."""
    if len(candles) < 20:
        return None
    seg = candles[-min(lookback, len(candles)):]
    hi = max(c["high"] for c in seg)
    lo = min(c["low"] for c in seg)
    if hi <= lo:
        return None
    bin_size = (hi - lo) / bins
    vol_bins = [0.0] * bins
    for c in seg:
        c_hi, c_lo, v = c["high"], c["low"], c.get("volume") or 0
        if v <= 0:
            continue
        if c_hi <= c_lo:
            idx = min(max(int((c["close"] - lo) / bin_size), 0), bins - 1)
            vol_bins[idx] += v
            continue
        b_lo = min(max(int((c_lo - lo) / bin_size), 0), bins - 1)
        b_hi = min(max(int((c_hi - lo) / bin_size), 0), bins - 1)
        span = b_hi - b_lo + 1
        per_bin = v / span
        for b in range(b_lo, b_hi + 1):
            vol_bins[b] += per_bin

    total_vol = sum(vol_bins)
    if total_vol <= 0:
        return None
    poc_idx = max(range(bins), key=lambda i: vol_bins[i])
    poc_price = lo + (poc_idx + 0.5) * bin_size

    target = total_vol * 0.70
    acc = vol_bins[poc_idx]
    lo_i, hi_i = poc_idx, poc_idx
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        up_v = vol_bins[hi_i + 1] if hi_i + 1 < bins else -1.0
        down_v = vol_bins[lo_i - 1] if lo_i > 0 else -1.0
        if up_v >= down_v:
            hi_i += 1
            acc += vol_bins[hi_i]
        else:
            lo_i -= 1
            acc += vol_bins[lo_i]
    vah = lo + (hi_i + 1) * bin_size
    val = lo + lo_i * bin_size

    price = candles[-1]["close"]
    if price > vah:
        pos, score = "가치영역 위 — 고평가 구간(되돌림 시 저항 재진입 가능)", 42.0
    elif price < val:
        pos, score = "가치영역 아래 — 저평가 구간(되돌림 시 지지 재진입 가능)", 58.0
    else:
        pos, score = "가치영역 내부 — 적정가 부근(박스권 가능성)", 50.0

    levels = [{"price": round(lo + (i + 0.5) * bin_size, 1), "volume": round(vol_bins[i], 0)} for i in range(bins)]
    return {
        "poc": round(poc_price, 1), "vah": round(vah, 1), "val": round(val, 1),
        "levels": levels, "position": pos, "lookback": len(seg), "score": score,
    }


# ---------------------------------------------------------------- 오더플로우 근사
def order_flow_proxy(candles, n=90):
    """오더플로우 근사 — ⚠️ 진짜 오더플로우(매수/매도 체결 델타)는 호가창·틱 데이터가
    있어야 계산 가능한데 이 프로젝트엔 없다. 대신 CLV(Close Location Value: 그 날
    저가~고가 범위에서 종가가 어디에 마감했는지, -1~+1)에 거래량을 곱해 "매수세/매도세
    추정치"를 근사한다(Chaikin Money Flow와 동일 원리) — 종가 방향만 보는 OBV(이진 가산)
    보다 하루 안에서의 마감 위치까지 반영해 조금 더 정밀하지만, 여전히 근사치일 뿐 실제
    체결 데이터가 아니다. 이 사실을 화면에서 감추지 않는다."""
    if len(candles) < n + 5:
        return None
    deltas = []
    for c in candles:
        h, l, cl, v = c["high"], c["low"], c["close"], c.get("volume") or 0
        rng = h - l
        clv = ((cl - l) - (h - cl)) / rng if rng else 0.0
        deltas.append(clv * v)
    cum = []
    acc = 0.0
    for d in deltas:
        acc += d
        cum.append(acc)

    seg = cum[-n:]
    slope = _lin_slope(seg)
    closes = [c["close"] for c in candles[-n:]]
    p_slope = _lin_slope(closes)
    diverge = None
    if p_slope < -3 and slope > 0:
        diverge = "bullish"
    elif p_slope > 3 and slope < 0:
        diverge = "bearish"
    trend_up = seg[-1] > seg[0]
    score = _clamp(50 + (12 if trend_up else -12) + (15 if diverge == "bullish" else -15 if diverge == "bearish" else 0))
    return {
        "cum_delta_series": [round(x, 0) for x in cum[-min(len(cum), 250):]],
        "today_delta": round(deltas[-1], 0),
        "trend_up": trend_up,
        "divergence": diverge,
        "score": round(score, 1),
    }


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
    avwap = anchored_vwap(candles)
    sweeps = liquidity_sweeps(candles)
    vp = volume_profile(candles)
    flow = order_flow_proxy(candles)

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
    if avwap:
        parts["AVWAP"] = avwap["score"]
        above = [label for label, v_ in avwap["lines"].items() if v_["above"]]
        below = [label for label, v_ in avwap["lines"].items() if not v_["above"]]
        if avwap["above_count"] == avwap["total"]:
            signals.append(("bull", f"모든 앵커드 VWAP({', '.join(avwap['lines'])}) 위 — 매수 진입자 전원 수익권, 지지 기대"))
        elif avwap["above_count"] == 0:
            signals.append(("bear", f"모든 앵커드 VWAP({', '.join(avwap['lines'])}) 아래 — 매수 진입자 전원 손실권, 저항 우려"))
        elif above and below:
            signals.append(("neutral", f"AVWAP 엇갈림 — {', '.join(above)} 위 / {', '.join(below)} 아래"))
    if sweeps:
        parts["유동성스윕"] = sweeps["score"]
    if sweeps and sweeps["recent"]:
        if sweeps["bull_recent"] > sweeps["bear_recent"]:
            e = next(x for x in reversed(sweeps["recent"]) if x["type"] == "low_sweep")
            signals.append(("bull", f"최근 저점 유동성 스윕({e['level']:,.0f} 부근) 후 반등 — 매도 스탑 훑고 상승 전환 신호"))
        elif sweeps["bear_recent"] > sweeps["bull_recent"]:
            e = next(x for x in reversed(sweeps["recent"]) if x["type"] == "high_sweep")
            signals.append(("bear", f"최근 고점 유동성 스윕({e['level']:,.0f} 부근) 후 하락 — 매수 스탑 훑고 하락 전환 신호"))
    if vp:
        parts["볼륨프로파일"] = vp["score"]
        signals.append(("neutral", f"볼륨 프로파일: POC {vp['poc']:,.0f} · 가치영역 {vp['val']:,.0f}~{vp['vah']:,.0f} — {vp['position']}"))
    if flow:
        parts["오더플로우근사"] = flow["score"]
        if flow["divergence"] == "bullish":
            signals.append(("bull", "오더플로우 근사치 강세 다이버전스 — 가격은 하락했지만 추정 매수세 우위(⚠️ 근사치, 체결 데이터 아님)"))
        elif flow["divergence"] == "bearish":
            signals.append(("bear", "오더플로우 근사치 약세 다이버전스 — 가격은 상승했지만 추정 매도세 우위(⚠️ 근사치, 체결 데이터 아님)"))

    weights = {"스테이지": 0.22, "추세템플릿": 0.19, "상대강도": 0.15,
               "VCP": 0.08, "OBV": 0.07, "박스": 0.04,
               "AVWAP": 0.08, "볼륨프로파일": 0.05, "오더플로우근사": 0.06,
               "유동성스윕": 0.06}
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
        "avwap": avwap,
        "liquidity_sweeps": sweeps,
        "volume_profile": vp,
        "order_flow": flow,
        "signals": [{"type": t, "text": s} for t, s in signals],
        "bars": len(candles),
    }
