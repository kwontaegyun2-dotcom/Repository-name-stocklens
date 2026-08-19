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
    """anchor_idx부터 끝까지, 그 시점 이후 매수한 모든 참여자의 평균 단가(거래량가중) +
    거래량가중 표준편차(밴드용). typical price = (고가+저가+종가)/3 을 표준으로 쓴다."""
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
    if cum_v > 0:
        avwap_now = out[-1]
        var = sum((c.get("volume") or 0) * (((c["high"] + c["low"] + c["close"]) / 3.0) - avwap_now) ** 2
                   for c in candles[anchor_idx:]) / cum_v
        std = var ** 0.5
    else:
        std = 0.0
    return out, std


def _find_extra_anchors(candles):
    """실적발표(근사)·갭·거래량폭발 앵커 — 전부 이미 가진 일봉 데이터만으로 계산 가능
    (2026-08-19 설계서 4-1의 8앵커 확장 중 "사용자 지정"을 뺀 나머지)."""
    import datetime as _dt
    anchors = {}
    n = len(candles)

    # 캔들 date 필드가 "20260819"(구분자 없음) 형식이라 숫자만 남겨 %Y%m%d로 파싱한다.
    def _pd(s):
        digits = "".join(ch for ch in str(s) if ch.isdigit())[:8]
        return _dt.datetime.strptime(digits, "%Y%m%d")

    # 실적발표일 근사: consensus·research 날짜 태깅이 없어 "가장 최근 지난 분기말+45일"로
    # 근사한다(설계서 4-1 표에 명시된 대체 규칙).
    last_date = _pd(candles[-1]["date"])
    q_ends = []
    for y in (last_date.year, last_date.year - 1):
        for m, d in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q_ends.append(_dt.datetime(y, m, d))
    q_ends = sorted(q for q in q_ends if q + _dt.timedelta(days=45) <= last_date)
    if q_ends:
        target = q_ends[-1] + _dt.timedelta(days=45)
        idx = next((i for i, c in enumerate(candles) if _pd(c["date"]) >= target), None)
        if idx is not None and idx < n - 1:
            anchors["최근 실적발표(근사)"] = idx

    search_from = max(0, n - 252)
    # 갭 발생일 — |시가-전일종가|/전일종가 ≥ 3%, 가장 최근 것.
    for i in range(n - 1, search_from, -1):
        prev_close = candles[i - 1]["close"]
        if prev_close and abs(candles[i]["open"] - prev_close) / prev_close >= 0.03:
            anchors["갭 발생일"] = i
            break

    # 거래량 폭발일 — Vol ≥ 3×MA20(Vol) 이고 등락률 ≥ 3%, 가장 최근 것.
    for i in range(n - 1, max(20, search_from), -1):
        window = candles[i - 20:i]
        avg_vol = sum(c.get("volume") or 0 for c in window) / 20
        if avg_vol <= 0:
            continue
        prev_close = candles[i - 1]["close"]
        rate = abs(candles[i]["close"] - prev_close) / prev_close if prev_close else 0
        if (candles[i].get("volume") or 0) >= avg_vol * 3 and rate >= 0.03:
            anchors["거래량 폭발일"] = i
            break

    return anchors


def anchored_vwap(candles, sweeps=None):
    """앵커드 VWAP(브라이언 섀넌) — "의미 있는 기준점 이후 평균 매수단가"를 여러 앵커로
    계산한다. 현재가가 AVWAP 위면 그 시점 이후 진입자는 평균적으로 수익 중 → 되돌림 시
    지지로 작용하는 경향, 아래면 저항으로 작용하는 경향(실전에서 널리 쓰이는 해석).

    2026-08-19 설계서 4-1 반영 — 기존엔 앵커가 3개뿐이고 점수도 "위에 있는 개수÷전체"라
    거리·기울기가 전혀 반영되지 않았다. 앵커를 7개(52주 신고/저가·YTD·실적발표 근사·갭·
    거래량폭발·최근 스윕)로 늘리고, 점수는 거리(dist)·기울기(slope)를 앵커 중요도로
    가중평균한다. 리클레임/상실/수렴(밀집) 이벤트와 "3회 넘게 시험된 지지선은 약해짐"
    경고도 함께 계산한다."""
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

    anchors.update(_find_extra_anchors(candles))

    if sweeps and sweeps.get("events"):
        anchors["최근 스윕"] = sweeps["events"][-1]["idx"]

    importance = {
        "최근 실적발표(근사)": 1.5, "거래량 폭발일": 1.3, "갭 발생일": 1.2,
        "52주 신고가": 1.0, "52주 신저가": 1.0, "최근 스윕": 1.0, "연초(YTD)": 0.7,
    }

    price = candles[-1]["close"]
    lines, above_count = {}, 0
    weighted_sum, weight_total = 0.0, 0.0
    events_out = []
    for label, idx in anchors.items():
        result = _avwap_series(candles, idx)
        if not result:
            continue
        series, std = result
        avwap_now = series[-1]
        above = price > avwap_now
        above_count += 1 if above else 0

        dist = (price - avwap_now) / avwap_now if avwap_now else 0
        pos = _clamp(50 + dist * 500)
        if len(series) > 20 and series[-21]:
            slope = (series[-1] - series[-21]) / series[-21]
            trd = _clamp(50 + slope * 500)
        else:
            slope, trd = 0.0, pos
        sub = 0.6 * pos + 0.4 * trd
        w = importance.get(label, 1.0)
        weighted_sum += sub * w
        weight_total += w

        # 리클레임(전일 아래→오늘 위)/상실(전일 위→오늘 아래) — 전환 후보 이벤트.
        event = None
        if len(series) >= 2 and candles[idx + len(series) - 2]["close"] and series[-2]:
            prev_close = candles[idx + len(series) - 2]["close"]
            prev_avwap = series[-2]
            if prev_close < prev_avwap and price > avwap_now:
                event = "reclaim"
                events_out.append(f"{label} 리클레임({avwap_now:,.0f}원) — 강세 전환 후보")
            elif prev_close > prev_avwap and price < avwap_now:
                event = "lost"
                events_out.append(f"{label} 상실({avwap_now:,.0f}원) — 약세 전환 후보")

        # 시험 횟수 — 종가가 이 AVWAP선을 넘나든 횟수(부호 전환). 3회 초과면 "약해진 지지/저항".
        touches, prev_sign = 0, None
        for i, avw in enumerate(series):
            sign = candles[idx + i]["close"] >= avw
            if prev_sign is not None and sign != prev_sign:
                touches += 1
            prev_sign = sign
        weakened = touches > 3

        lines[label] = {
            "anchor_date": candles[idx]["date"], "value": round(avwap_now, 1),
            "above": above, "series": [round(v, 2) for v in series],
            "band_upper": round(avwap_now + std, 1), "band_lower": round(avwap_now - std, 1),
            "slope_pct": round(slope * 100, 2), "touches": touches, "weakened": weakened,
            "event": event,
        }
    if not lines:
        return None

    # 수렴(압축) — 서로 다른 앵커 3개 이상이 현재가 ±3% 이내에 모임 → 방향 결정 구간.
    clustered = [lbl for lbl, ln in lines.items() if abs(ln["value"] - price) / price <= 0.03]
    if len(clustered) >= 3:
        events_out.append(f"{price:,.0f}원 부근에 AVWAP {len(clustered)}개 밀집({', '.join(clustered)}) — 방향 결정 구간")

    total = len(lines)
    score = round(_clamp(weighted_sum / weight_total) if weight_total else 50.0, 1)
    return {"lines": lines, "above_count": above_count, "total": total, "score": score, "events": events_out}


# ---------------------------------------------------------------- 유동성 스윕
def _sweep_dir(e):
    """스윕 이벤트의 실질 방향. 실패 스윕(breakout_flip)은 원래 타입의 반대 신호로 본다
    (설계서 4-2 부가 지적 — "3봉 안에 되돌아오면 진짜 돌파")."""
    if e["status"] == "breakout_flip":
        return "bull" if e["type"] == "high_sweep" else "bear"
    return "bull" if e["type"] == "low_sweep" else "bear"


def liquidity_sweeps(candles, lookback=180, ref_window=20, vol_window=20):
    """유동성 스윕(ICT/Smart Money Concepts) — 직전 N봉 고점/저점(참조 레벨) 부근에 몰린
    손절·역지정가 주문을 스탑헌팅한 뒤 반대로 튕기는 패턴. 판별 핵심은 "뚫었는가"가 아니라
    "뚫고 되돌아왔는가"라서 윗꼬리/아랫꼬리 비율과 거래량 동반 여부로 강도까지 점수화한다.

    2026-08-19 설계서 4-2 반영 — 기존 프랙탈 피벗 방식은 type·idx·date·level뿐이라 강도를
    알 수 없었다. 참조레벨(직전 N봉 고저) 돌파+되돌림+윗/아랫꼬리+거래량 배수로 판정을
    교체하고, strength(0~100)·사후 3/5/10일 수익률·실패 스윕(돌파 전환) 판별을 추가했다.

    ⚠️ 원래 개념은 장중(틱) 기준이다 — 일봉으로는 "전일 고점·저점 사냥"이라는 스윙
    관점으로 재정의해 쓴다."""
    n = len(candles)
    if n < ref_window + vol_window + 10:
        return None
    seg_start = max(0, n - lookback)
    seg = candles[seg_start:]
    m = len(seg)

    events = []
    for t in range(ref_window, m):
        atr14 = atr(seg[max(0, t - 60):t + 1], 14)
        if not atr14:
            continue
        ma_vol = sum(seg[i].get("volume") or 0 for i in range(t - vol_window, t)) / vol_window
        if ma_vol <= 0:
            continue
        c = seg[t]
        rng = max(c["high"] - c["low"], 1e-9)
        vol_t = c.get("volume") or 0

        ref_high = max(seg[i]["high"] for i in range(t - ref_window, t))
        if c["high"] > ref_high and c["close"] < ref_high:
            upper_wick = (c["high"] - max(c["open"], c["close"])) / rng
            if upper_wick >= 0.5 and vol_t >= 1.5 * ma_vol:
                depth = (c["high"] - ref_high) / atr14
                reject = (c["high"] - c["close"]) / rng
                volx = vol_t / ma_vol
                touches = sum(1 for i in range(max(0, t - 60), t)
                               if abs(seg[i]["high"] - ref_high) / ref_high <= 0.005)
                strength = _clamp(25 * min(depth, 2) + 35 * reject + 20 * min(volx / 3, 1) + 20 * min(touches / 3, 1))
                events.append({"type": "high_sweep", "idx": seg_start + t, "date": c["date"],
                                "level": round(ref_high, 1), "strength": round(strength, 1),
                                "depth_atr": round(depth, 2), "reject_ratio": round(reject, 2),
                                "vol_x": round(volx, 2), "touches": touches, "status": "sweep"})
                continue   # 한 봉에서 고점·저점 스윕이 동시에 뜨는 건 노이즈라 우선 처리

        ref_low = min(seg[i]["low"] for i in range(t - ref_window, t))
        if c["low"] < ref_low and c["close"] > ref_low:
            lower_wick = (min(c["open"], c["close"]) - c["low"]) / rng
            if lower_wick >= 0.5 and vol_t >= 1.5 * ma_vol:
                depth = (ref_low - c["low"]) / atr14
                reject = (c["close"] - c["low"]) / rng
                volx = vol_t / ma_vol
                touches = sum(1 for i in range(max(0, t - 60), t)
                               if abs(seg[i]["low"] - ref_low) / ref_low <= 0.005)
                strength = _clamp(25 * min(depth, 2) + 35 * reject + 20 * min(volx / 3, 1) + 20 * min(touches / 3, 1))
                events.append({"type": "low_sweep", "idx": seg_start + t, "date": c["date"],
                                "level": round(ref_low, 1), "strength": round(strength, 1),
                                "depth_atr": round(depth, 2), "reject_ratio": round(reject, 2),
                                "vol_x": round(volx, 2), "touches": touches, "status": "sweep"})

    events.sort(key=lambda e: e["idx"])

    # 실패 스윕 → 돌파 전환: 스윕 판정 후 3봉 안에 종가가 레벨을 다시 넘으면 가짜 돌파가
    # 아니라 진짜 돌파였다는 뜻(설계서 부가 지적). 영원히 스윕으로 남기지 않는다.
    for e in events:
        rel_idx = e["idx"] - seg_start
        for j in range(rel_idx + 1, min(rel_idx + 4, m)):
            if e["type"] == "high_sweep" and seg[j]["close"] > e["level"]:
                e["status"] = "breakout_flip"; break
            if e["type"] == "low_sweep" and seg[j]["close"] < e["level"]:
                e["status"] = "breakout_flip"; break

    # 사후 성과(3/5/10일 수익률) — 스윕 소급 백테스트·적중률 집계의 재료.
    for e in events:
        rel_idx = e["idx"] - seg_start
        base_close = seg[rel_idx]["close"]
        for days, key in ((3, "ret_3d"), (5, "ret_5d"), (10, "ret_10d")):
            j = rel_idx + days
            e[key] = round((seg[j]["close"] - base_close) / base_close * 100, 2) if j < m else None

    recent_cut_idx = n - 15
    recent = [e for e in events if e["idx"] >= recent_cut_idx]
    bull = sum(1 for e in recent if _sweep_dir(e) == "bull")
    bear = sum(1 for e in recent if _sweep_dir(e) == "bear")
    sweep_recent = [e for e in recent if e["status"] == "sweep"]
    avg_strength = sum(e["strength"] for e in sweep_recent) / len(sweep_recent) if sweep_recent else 50.0
    base_score = 50 + (bull - bear) * 12
    score = _clamp(base_score * 0.6 + avg_strength * 0.4) if sweep_recent else _clamp(base_score)

    def _hit_rate(kind):
        sample = [e for e in events if e["type"] == kind and e["status"] == "sweep" and e.get("ret_5d") is not None]
        if not sample:
            return None
        positive = kind == "low_sweep"   # 저점 스윕=반등(+) 기대, 고점 스윕=하락(-) 기대
        hits = sum(1 for e in sample if (e["ret_5d"] > 0) == positive)
        return {"n": len(sample), "hits": hits, "rate": round(hits / len(sample) * 100, 1)}

    backtest = {"low_sweep_5d": _hit_rate("low_sweep"), "high_sweep_5d": _hit_rate("high_sweep")}

    return {"events": events[-60:], "recent": recent, "bull_recent": bull, "bear_recent": bear,
            "score": round(score, 1), "backtest": backtest}


# ---------------------------------------------------------------- 볼륨 프로파일
def volume_profile(candles, lookback=120):
    """볼륨 프로파일 — 가격대별 거래량 분포(시간이 아닌 "가격" 축 히스토그램).
    ⚠️ 진짜 볼륨 프로파일은 틱(체결) 데이터로 만들지만, 이 프로젝트엔 일봉만 있다 —
    "일봉 기반 추정 프로파일"임을 신호 문구에 항상 명시한다.

    2026-08-19 설계서 지적으로 재구현(트레이딩엔진 설계서 4-3):
    - 구간 24개 고정은 너무 성겨(한 칸이 현재가의 3%대) POC 정밀도가 없었다 →
      ATR14 기준 동적 구간수(50~150)로 변경.
    - 봉 거래량을 고가~저가에 균등 분배하면 변동성 큰 봉이 넓게 퍼져 프로파일이
      뭉개진다 → 종가·시가 중심가(TP) 근처에 더 많이 배분하는 삼각가중으로 변경.
    - 위 두 결함이 겹쳐 추세 구간(예: 1년간 6배 오른 종목)에 하나의 프로파일을
      씌우면 가치영역이 전체 가격범위(현재가의 158%)를 뒤덮는 오류가 실측됐다 →
      가치영역 폭이 현재가의 40%를 넘으면 reliability='low'로 표시하고 판단·점수
      반영에서 제외한다(값 자체는 계산해 참고용으로만 노출).
    """
    if len(candles) < 20:
        return None
    seg = candles[-min(lookback, len(candles)):]
    hi = max(c["high"] for c in seg)
    lo = min(c["low"] for c in seg)
    if hi <= lo:
        return None

    atr14 = atr(seg, 14) or (hi - lo) * 0.02
    n_bins = int(round((hi - lo) / max(atr14 * 0.25, 1e-9)))
    n_bins = min(max(n_bins, 50), 150)
    bin_size = (hi - lo) / n_bins
    vol_bins = [0.0] * n_bins

    for c in seg:
        c_hi, c_lo, v = c["high"], c["low"], c.get("volume") or 0
        if v <= 0:
            continue
        tp = (c_hi + c_lo + c["close"]) / 3
        if c_hi <= c_lo:
            idx = min(max(int((c["close"] - lo) / bin_size), 0), n_bins - 1)
            vol_bins[idx] += v
            continue
        b_lo = min(max(int((c_lo - lo) / bin_size), 0), n_bins - 1)
        b_hi = min(max(int((c_hi - lo) / bin_size), 0), n_bins - 1)
        rng = max(c_hi - c_lo, bin_size * 0.01)
        # 종가 근처(TP)에 더 많이 배분하는 삼각가중 — 균등분배보다 실제 분포에 가깝다.
        ws = [max(1 - abs((lo + (b + 0.5) * bin_size) - tp) / rng, 0.05) for b in range(b_lo, b_hi + 1)]
        wsum = sum(ws)
        for b, w in zip(range(b_lo, b_hi + 1), ws):
            vol_bins[b] += v * (w / wsum)

    total_vol = sum(vol_bins)
    if total_vol <= 0:
        return None
    poc_idx = max(range(n_bins), key=lambda i: vol_bins[i])
    poc_price = lo + (poc_idx + 0.5) * bin_size

    # Value Area — TradingView 표준 70% 확장법(POC에서 거래량 큰 쪽으로 번갈아 흡수).
    target = total_vol * 0.70
    acc = vol_bins[poc_idx]
    lo_i, hi_i = poc_idx, poc_idx
    while acc < target and (lo_i > 0 or hi_i < n_bins - 1):
        up_v = vol_bins[hi_i + 1] if hi_i + 1 < n_bins else -1.0
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

    # 신뢰도 플래그 — 추세 구간(가격이 크게 움직인 구간)에 프로파일을 씌우면 가치영역이
    # 비정상적으로 넓어진다(실측 사례: SK하이닉스 158%). 40% 초과 시 low로 표시.
    va_width_pct = (vah - val) / price if price else 1.0
    reliability = "low" if va_width_pct > 0.40 else "high"
    reliability_note = "추세 구간 — 가치영역 해석 주의" if reliability == "low" else None

    # HVN(고거래량 노드)/LVN(저거래량 노드) — 국소 극대·극소점 중 상위 3개.
    sorted_vols = sorted(vol_bins)
    hvn_cut = sorted_vols[int(n_bins * 0.8)]
    lvn_cut = sorted_vols[int(n_bins * 0.2)]
    hvn_cands, lvn_cands = [], []
    for i in range(1, n_bins - 1):
        v = vol_bins[i]
        mid = round(lo + (i + 0.5) * bin_size, 1)
        if v >= vol_bins[i - 1] and v >= vol_bins[i + 1] and v >= hvn_cut:
            hvn_cands.append((v, mid))
        if 0 < v <= vol_bins[i - 1] and v <= vol_bins[i + 1] and v <= lvn_cut:
            lvn_cands.append((v, mid))
    hvn_cands.sort(key=lambda x: -x[0])
    lvn_cands.sort(key=lambda x: x[0])

    levels = [{"price": round(lo + (i + 0.5) * bin_size, 1), "volume": round(vol_bins[i], 0)} for i in range(n_bins)]
    return {
        "poc": round(poc_price, 1), "vah": round(vah, 1), "val": round(val, 1),
        "levels": levels, "position": pos, "lookback": len(seg), "score": score,
        "bins": n_bins, "reliability": reliability, "reliability_note": reliability_note,
        "hvn": [p for _, p in hvn_cands[:3]], "lvn": [p for _, p in lvn_cands[:3]],
    }


# ---------------------------------------------------------------- 수급 오더플로우
def smart_money_flow(flows, closes):
    """수급 오더플로우 — 진짜 오더플로우(매수/매도 체결 델타)는 호가창·틱 데이터가 있어야
    계산 가능한데 이 프로젝트엔 없다. 예전엔 CLV(종가 마감 위치)로 흉내 냈지만, 그건
    결국 그날 캔들의 몸통·꼬리를 다시 쓴 것이라 새로운 정보가 없었다(2026-08-19 설계서
    4-4 지적 — 실측 점수 23점으로 10개 기법 중 최저).

    대신 한국거래소가 공개하는 "투자자별(외국인·기관) 순매수"를 스마트머니의 누적
    델타로 쓴다 — 미국 시장엔 이 데이터 자체가 없어 미국 서비스가 절대 만들 수 없는
    한국형 지표다. flows가 없으면(미국 종목 등) None을 반환하고, analyze()가 이
    항목을 종합점수에서 자연스럽게 제외한다(가중치 재분배).

    flows: main.py가 만든 리스트, index 0이 최신 날짜(내림차순)라고 가정.
    closes: 일봉 종가 리스트(오름차순, candles와 같은 순서)."""
    if not flows or len(flows) < 20:
        return None
    days = list(reversed(flows))   # 오름차순(과거→현재)으로 정렬

    cum_foreign, cum_organ, smart_delta = [], [], []
    cf = co = 0.0
    for d in days:
        cf += d.get("foreigner") or 0
        co += d.get("organ") or 0
        cum_foreign.append(cf)
        cum_organ.append(co)
        smart_delta.append(cf + co)

    # 다이버전스 — 최근 20일 가격 변화 vs 스마트머니 누적델타 변화(부호만 비교; 유통주식
    # 수를 몰라 절대량 정규화는 불가능해 방향성 신호로만 쓴다).
    divergence = None
    if len(closes) >= 21 and closes[-21]:
        price_chg = (closes[-1] - closes[-21]) / closes[-21]
        smart_chg = smart_delta[-1] - smart_delta[-21] if len(smart_delta) >= 21 else 0
        if price_chg < -0.05 and smart_chg > 0:
            divergence = "bullish"
        elif price_chg > 0.05 and smart_chg < 0:
            divergence = "bearish"

    # 외국인 추정 평균단가 — 순매수한 날의 종가를 순매수 수량으로 가중평균.
    buy_days = [d for d in days if (d.get("foreigner") or 0) > 0 and d.get("close")]
    foreign_avg_cost = None
    if buy_days:
        den = sum(d["foreigner"] for d in buy_days)
        foreign_avg_cost = sum(d["close"] * d["foreigner"] for d in buy_days) / den if den else None

    # 연속성 — 외국인 연속 순매수 일수(최신부터), 최근 5일 집중도(전체 20일 대비).
    streak = 0
    for d in reversed(days):
        if (d.get("foreigner") or 0) > 0:
            streak += 1
        else:
            break
    last5 = sum(abs(d.get("foreigner") or 0) for d in days[-5:])
    last20 = sum(abs(d.get("foreigner") or 0) for d in days[-20:])
    concentration = round(last5 / last20, 2) if last20 else None

    price = closes[-1] if closes else None
    cost_upside = round((price - foreign_avg_cost) / foreign_avg_cost * 100, 1) if (foreign_avg_cost and price) else None

    score = 50.0
    if divergence == "bullish":
        score += 20
    elif divergence == "bearish":
        score -= 20
    score += _clamp(streak * 2, 0, 10) - 5
    if concentration is not None:
        score += (concentration - 0.25) * 20
    score = _clamp(score)

    return {
        "cum_foreign": round(cum_foreign[-1], 0), "cum_organ": round(cum_organ[-1], 0),
        "smart_delta_series": [round(v, 0) for v in smart_delta],
        "divergence": divergence,
        "foreign_avg_cost": round(foreign_avg_cost, 1) if foreign_avg_cost else None,
        "foreign_avg_cost_upside": cost_upside,
        "streak_foreign": streak, "concentration": concentration,
        "days": len(days), "score": round(score, 1),
    }


# ---------------------------------------------------------------- 컨플루언스 엔진
_AVWAP_IMPORTANCE = {
    "최근 실적발표(근사)": 1.5, "거래량 폭발일": 1.3, "갭 발생일": 1.2,
    "52주 신고가": 1.0, "52주 신저가": 1.0, "최근 스윕": 1.0, "연초(YTD)": 0.7,
}


def confluence(avwap, vp, sweeps, smart, price):
    """컨플루언스 엔진(설계서 5장) — AVWAP·볼륨프로파일·유동성스윕·외국인평단이 "같은
    가격대"에서 겹치는 지점을 자동으로 찾는다. 네 기법을 따로 나열만 하던 걸, "서로 다른
    근거가 같은 가격대에서 겹칠 때" 신뢰도 높은 지지/저항으로 묶어준다 — 기존 지지선·
    저항선이 "왜 이 가격인지" 설명이 없었던 문제(3차 진단리포트)를 근거 목록으로 답한다.

    ⚠️ "미방문 POC"(과거 POC 중 이후 한 번도 되돌아가지 않은 지점)는 일별 POC 이력을
    누적 저장해야 계산 가능한 상태 저장형 기능이라 이번 범위에서는 제외했다."""
    if not price:
        return []
    levels = []
    if avwap:
        for label, ln in avwap["lines"].items():
            levels.append({"price": ln["value"], "src": f"AVWAP({label})", "w": _AVWAP_IMPORTANCE.get(label, 1.0)})
    if vp and vp["reliability"] != "low":
        levels.append({"price": vp["poc"], "src": "POC", "w": 1.3})
        levels.append({"price": vp["vah"], "src": "VAH", "w": 1.0})
        levels.append({"price": vp["val"], "src": "VAL", "w": 1.0})
        for h in vp.get("hvn", []):
            levels.append({"price": h, "src": "HVN", "w": 1.1})
    if sweeps:
        for e in sweeps.get("recent", []):
            if e["status"] != "sweep":
                continue
            src = "스윕저점" if e["type"] == "low_sweep" else "스윕고점"
            levels.append({"price": e["level"], "src": src, "w": e["strength"] / 100 * 1.2})
    if smart and smart.get("foreign_avg_cost"):
        levels.append({"price": smart["foreign_avg_cost"], "src": "외국인평단", "w": 1.2})
    if not levels:
        return []

    # ±1.5% 이내를 하나의 클러스터로 묶는다(가격 정렬 후 인접 병합).
    tol = 0.015 * price
    levels.sort(key=lambda x: x["price"])
    clusters = []
    for lv in levels:
        if clusters and lv["price"] - clusters[-1]["prices"][-1] <= tol:
            clusters[-1]["prices"].append(lv["price"])
            clusters[-1]["items"].append(lv)
        else:
            clusters.append({"prices": [lv["price"]], "items": [lv]})

    out = []
    for c in clusters:
        avg_price = sum(c["prices"]) / len(c["prices"])
        sources = [it["src"] for it in c["items"]]
        w_sum = sum(it["w"] for it in c["items"])
        score = w_sum * (1 + 0.15 * (len(sources) - 1))   # 겹칠수록 가산
        out.append({"price": round(avg_price, 1), "type": "지지" if avg_price < price else "저항",
                     "score": round(score, 2), "sources": sources})
    out.sort(key=lambda x: -x["score"])
    return out[:8]


# ---------------------------------------------------------------- 통합
def analyze(candles, bench_closes=None, flows=None):
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
    sweeps = liquidity_sweeps(candles)   # AVWAP의 "최근 스윕" 앵커가 이 결과를 참조하므로 먼저 계산
    avwap = anchored_vwap(candles, sweeps)
    vp = volume_profile(candles)
    smart = smart_money_flow(flows, closes)
    conf = confluence(avwap, vp, sweeps, smart, price)

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
        for ev_txt in avwap.get("events", []):
            tone = "bull" if ("리클레임" in ev_txt or "돌파" in ev_txt) else ("bear" if "상실" in ev_txt else "neutral")
            signals.append((tone, ev_txt))
        weakened = [label for label, v_ in avwap["lines"].items() if v_.get("weakened")]
        if weakened:
            signals.append(("warn", f"{', '.join(weakened)} — 3회 넘게 시험돼 지지/저항으로서 신뢰도 약화"))
    if sweeps:
        parts["유동성스윕"] = sweeps["score"]
    if sweeps and sweeps["recent"]:
        if sweeps["bull_recent"] > sweeps["bear_recent"]:
            e = next(x for x in reversed(sweeps["recent"]) if _sweep_dir(x) == "bull")
            if e["status"] == "breakout_flip":
                signals.append(("bull", f"{e['level']:,.0f} 부근 저항 돌파 전환(스윕 실패 → 진짜 돌파) — 상승 신호"))
            else:
                signals.append(("bull", f"최근 저점 유동성 스윕({e['level']:,.0f} 부근, 강도 {e['strength']:.0f}) 후 반등 — 매도 스탑 훑고 상승 전환 신호"))
        elif sweeps["bear_recent"] > sweeps["bull_recent"]:
            e = next(x for x in reversed(sweeps["recent"]) if _sweep_dir(x) == "bear")
            if e["status"] == "breakout_flip":
                signals.append(("bear", f"{e['level']:,.0f} 부근 지지 이탈 전환(스윕 실패 → 진짜 붕괴) — 하락 신호"))
            else:
                signals.append(("bear", f"최근 고점 유동성 스윕({e['level']:,.0f} 부근, 강도 {e['strength']:.0f}) 후 하락 — 매수 스탑 훑고 하락 전환 신호"))
    if vp:
        # 신뢰도 낮음(추세 구간 등)이면 점수 반영에서 제외 — 값은 참고용으로만 노출.
        if vp["reliability"] != "low":
            parts["볼륨프로파일"] = vp["score"]
        note = f" ⚠️{vp['reliability_note']}" if vp.get("reliability_note") else ""
        signals.append(("neutral", f"볼륨 프로파일(일봉 기반 추정): POC {vp['poc']:,.0f} · 가치영역 {vp['val']:,.0f}~{vp['vah']:,.0f} — {vp['position']}{note}"))
    if smart:
        parts["수급오더플로우"] = smart["score"]
        if smart["divergence"] == "bullish":
            signals.append(("bull", f"수급 오더플로우 강세 다이버전스 — 가격 하락에도 외국인+기관 누적 순매수 우위(연속 {smart['streak_foreign']}일)"))
        elif smart["divergence"] == "bearish":
            signals.append(("bear", f"수급 오더플로우 약세 다이버전스 — 가격 상승에도 외국인+기관 누적 순매도 우위"))
        if smart.get("foreign_avg_cost") and smart.get("foreign_avg_cost_upside") is not None:
            tone = "bull" if smart["foreign_avg_cost_upside"] >= 0 else "warn"
            signals.append((tone, f"외국인 추정 평균단가 {smart['foreign_avg_cost']:,.0f}원 — 현재가 대비 {smart['foreign_avg_cost_upside']:+.1f}% {'이익' if smart['foreign_avg_cost_upside'] >= 0 else '손실'} 구간"))
        if smart["streak_foreign"] >= 5:
            signals.append(("bull", f"외국인 {smart['streak_foreign']}일 연속 순매수 — 수급 유입 지속"))

    # 컨플루언스 — 서로 다른 근거 2개 이상이 겹친 구간만 신호로 노출(1개짜리는 노이즈).
    for c in [x for x in conf if len(x["sources"]) >= 2][:3]:
        signals.append(("neutral", f"{c['price']:,.0f}원 ({c['type']}, 신뢰도 {c['score']:.1f}) — {' + '.join(c['sources'])} {len(c['sources'])}중 겹침"))

    weights = {"스테이지": 0.22, "추세템플릿": 0.19, "상대강도": 0.15,
               "VCP": 0.08, "OBV": 0.07, "박스": 0.04,
               "AVWAP": 0.08, "볼륨프로파일": 0.05, "수급오더플로우": 0.06,
               "유동성스윕": 0.06}
    tw = sum(w for k, w in weights.items() if k in parts)
    score = sum(parts[k] * weights[k] for k in parts) / tw if tw else 50.0
    # 화면에 실제 반영 비중을 병기하기 위해 정규화(재분배 반영)된 %로 노출한다
    # (설계서 16번 — 없는 항목 때문에 재분배된 비중이 그대로 드러나야 함).
    weight_pct = {k: round(weights[k] / tw * 100, 1) for k in parts if k in weights} if tw else {}

    if atr_pct and atr_pct > 5:
        signals.append(("warn", f"ATR 변동성 {atr_pct}% — 일간 등락이 큼, 포지션 크기 축소 권장"))

    return {
        "available": True,
        "score": round(_clamp(score), 1),
        "parts": {k: round(v, 1) for k, v in parts.items()},
        "weight_pct": weight_pct,
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
        "smart_money": smart,
        "confluence": conf,
        "signals": [{"type": t, "text": s} for t, s in signals],
        "bars": len(candles),
    }
