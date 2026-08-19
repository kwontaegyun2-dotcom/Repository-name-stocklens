# -*- coding: utf-8 -*-
"""이상징후 탐지 — "좋은 종목"이 아니라 "뭔가 이상한 종목"을 찾는다.

app/ranking.py가 이미 백그라운드로 전종목을 채점하면서 함께 캐시해둔 원신호
(RSI·실적전망·외국인수급·PER 배수)를 스캔만 한다 — 추가 네트워크 호출 없음.

- 🔥 저평가 확대: 펀더멘털·수급은 개선되는데 주가가 못 따라가는 괴리
- ⚠️ 단기 과열: 펀더멘털 뒷받침 없이 주가만 급등한 괴리
아래 신호 중 3개 이상 동시에 겹칠 때만 플래그한다(단일 지표 하나로는 오탐이 너무 많다).
"""
from app import ranking

GROWTH_DISPLAY_CAP = 50.0   # PEG·실적반영도와 동일 — 저기반 회복 왜곡 방지


def _fmt_growth(g):
    return min(g, GROWTH_DISPLAY_CAP)


def _classify(item):
    rate = item.get("rate")
    rsi = item.get("rsi")
    upside = item.get("upside")
    growth = item.get("op_growth_fwd")
    per_ratio = item.get("per_ratio")
    fdir = item.get("foreign_dir")

    bull, bear = [], []

    if growth is not None and growth > 15:
        bull.append(f"컨센서스 영업이익 전망 {_fmt_growth(growth):+.0f}%")
    if rate is not None and rate < -1.0:
        bull.append(f"오늘 주가 {rate:+.1f}%")
    if per_ratio is not None and per_ratio < 0.85:
        bull.append(f"PER이 과거 평균보다 {(1 - per_ratio) * 100:.0f}% 낮음")
    if fdir == "buy":
        bull.append("외국인 최근 5일 연속 순매수")
    if upside is not None and upside > 20:
        bull.append(f"목표주가 상승여력 {upside:+.0f}%")
    if rsi is not None and rsi < 45:
        bull.append(f"RSI {rsi:.0f} (과매도권)")

    if rate is not None and rate > 5:
        bear.append(f"오늘 주가 {rate:+.1f}% 급등")
    if per_ratio is not None and per_ratio > 1.3:
        bear.append(f"PER이 과거 평균보다 {(per_ratio - 1) * 100:.0f}% 높음")
    if rsi is not None and rsi > 70:
        bear.append(f"RSI {rsi:.0f} (과매수권)")
    if fdir == "sell":
        bear.append("외국인 최근 5일 연속 순매도")
    if growth is not None and growth < 0:
        bear.append(f"컨센서스 영업이익 전망 {growth:+.0f}% (둔화)")

    if len(bull) >= 3:
        return "bull", bull
    if len(bear) >= 3:
        return "bear", bear
    return None, []


def classify_item(item):
    """단일 종목(랭킹 캐시 아이템)에 대한 (bull/bear/None, 근거리스트) — watch.py 이상징후
    알림 조건이 scan()과 동일 로직을 재사용하기 위한 공개 진입점."""
    return _classify(item)


def scan(limit: int = 8):
    items = ranking.get("KR")["items"] + ranking.get("US")["items"]
    bulls, bears = [], []
    for it in items:
        kind, reasons = _classify(it)
        if kind == "bull":
            bulls.append({**it, "anomaly_reasons": reasons})
        elif kind == "bear":
            bears.append({**it, "anomaly_reasons": reasons})

    bulls.sort(key=lambda x: (-len(x["anomaly_reasons"]), -x["score"]))
    bears.sort(key=lambda x: (-len(x["anomaly_reasons"]), -x["score"]))
    return {"bull": bulls[:limit], "bear": bears[:limit]}
