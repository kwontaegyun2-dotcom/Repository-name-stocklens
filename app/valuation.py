# -*- coding: utf-8 -*-
"""밸류에이션 분석 — "지금 비싼가?"를 6가지 관점으로 점수화.

핵심 철학(사용자 지정 우선순위):
  1순위. 현재 FPER ÷ 과거 평균 FPER  ← 가장 먼저 보는 지표
  2순위. PEG (성장률 대비 밸류)
  3순위. 동종업계 대비
  4순위. EV/EBITDA
  5순위. 목표주가 상승여력
  6순위. 실적 추정치 방향

⚠️ 데이터 한계(네이버 기준, 실측으로 확인함)
  - 연도별 PER/EPS는 **실적 3년 + 컨센서스 1년** 만 제공된다. "5년 평균"을 그대로
    만들 수 없어, **5년 일봉 × 연도별 EPS로 연도별 평균 PER을 직접 계산**한다.
    (연말 스냅샷 PER보다 그 해 실제 밸류를 더 잘 대표한다)
  - 과거 시점의 '컨센서스 EPS'는 제공되지 않는다. 따라서 과거 FPER은
    **실현 선행 PER**(그 해 평균주가 ÷ 다음 해 실제 EPS)로 계산한다.
    추정이 아닌 결과를 쓰므로 낙관 편향이 없고, 정의도 명확하다.
  - EBITDA는 **미국 종목만** 제공된다(국내 미제공). 순차입금 데이터가 없어
    EV는 시가총액 기준 근사이며, 라벨에 그 사실을 명시한다.
"""
import datetime

from app.analysis import _clamp, to_num, parse_eok, _score_low


def _fy_year(key):
    """'202512' / '2025.09.27' → 2025"""
    s = str(key or "")
    digits = "".join(ch for ch in s if ch.isdigit())
    return int(digits[:4]) if len(digits) >= 4 else None


def _year_avg_price(candles, year):
    """해당 연도 일봉 종가 평균. 데이터 없으면 None."""
    vals = [c["close"] for c in candles if str(c.get("date", ""))[:4] == str(year)]
    return sum(vals) / len(vals) if vals else None


def per_history(fin_rows, candles):
    """연도별 평균 PER / 실현 선행 PER 계산.

    fin_rows: analysis._finance_rows() 결과 {행이름: [{period,value,consensus}]}
    반환: {"years":[{year, eps, avg_price, per, fper}], "avg_per":..., "avg_fper":...}
    """
    eps_series = None
    per_series = None
    for name, series in (fin_rows or {}).items():
        n = name.strip()
        if n == "EPS":
            eps_series = series
        elif n == "PER":
            per_series = series
    if not candles:
        return None
    if not eps_series:
        # 미국 종목은 재무제표에 EPS 행이 없다(실측 확인). 대신 **PER 행이 직접 제공**되므로
        # 그 값을 연도별 PER로 그대로 쓴다. 연평균 주가 기반이 아니라 정확도는 낮지만,
        # 1순위 지표(현재 ÷ 과거평균)를 미국에서도 쓸 수 있게 해준다.
        return _per_history_from_per_row(per_series)

    # 연도 → EPS (실적/컨센서스 구분)
    rows = []
    for s in eps_series:
        y = _fy_year(s.get("period"))
        if y and s.get("value") is not None:
            rows.append({"year": y, "eps": s["value"], "consensus": s.get("consensus", False)})
    if not rows:
        return None
    rows.sort(key=lambda r: r["year"])

    # 다음 해 EPS는 **실적(컨센서스 아님)** 만 쓴다.
    # 컨센서스 EPS를 섞으면 "실현 선행 PER" 정의가 깨지고, 전망이 공격적인 해에는
    # 선행PER이 비정상적으로 낮게 나와(예: 삼성전자 2025→2026E 1.5배) 평균을 망친다.
    actual_eps_by_year = {r["year"]: r["eps"] for r in rows if not r["consensus"]}
    out = []
    for r in rows:
        y = r["year"]
        avg_p = _year_avg_price(candles, y)
        per = None
        if avg_p and r["eps"] and r["eps"] > 0:
            per = round(avg_p / r["eps"], 2)
        # 실현 선행 PER = 그 해 평균주가 ÷ 다음 해 '실제' EPS
        nxt = actual_eps_by_year.get(y + 1)
        fper = None
        if avg_p and nxt and nxt > 0:
            fper = round(avg_p / nxt, 2)
        out.append({
            "year": y,
            "eps": r["eps"],
            "consensus": r["consensus"],
            "avg_price": round(avg_p) if avg_p else None,
            "per": per,
            "fper": fper,
        })

    # 과거 평균: 실적연도만(컨센서스 연도 제외), PER>0 인 것만
    hist_per = [o["per"] for o in out if o["per"] and not o["consensus"]]
    hist_fper = [o["fper"] for o in out if o["fper"] and not o["consensus"]]
    return {
        "years": out,
        "avg_per": round(sum(hist_per) / len(hist_per), 2) if hist_per else None,
        "avg_fper": round(sum(hist_fper) / len(hist_fper), 2) if hist_fper else None,
        "per_count": len(hist_per),
        "fper_count": len(hist_fper),
    }


def _per_history_from_per_row(per_series):
    """EPS 행이 없는 시장(미국)용 폴백 — 제공되는 PER 행을 그대로 연도별 PER로 사용."""
    if not per_series:
        return None
    out = []
    for s in per_series:
        y = _fy_year(s.get("period"))
        v = s.get("value")
        if y and v and v > 0:
            out.append({"year": y, "eps": None, "consensus": s.get("consensus", False),
                        "avg_price": None, "per": round(v, 2), "fper": None})
    if not out:
        return None
    out.sort(key=lambda r: r["year"])
    hist = [o["per"] for o in out if not o["consensus"]]
    return {
        "years": out,
        "avg_per": round(sum(hist) / len(hist), 2) if hist else None,
        "avg_fper": None,
        "per_count": len(hist),
        "fper_count": 0,
        "from_per_row": True,     # 연평균 주가 기반이 아님을 프론트에 알림
    }


def _band_score(ratio):
    """현재 ÷ 과거평균 배수를 점수로. 1.0=적정(60점), 낮을수록 고점.
    0.7배 이하 → 90+ / 1.0배 → 60 / 1.5배 → 30 / 2.0배 이상 → 12"""
    if ratio is None or ratio <= 0:
        return None
    if ratio <= 0.7:
        return 92.0
    if ratio >= 2.0:
        return 12.0
    # 0.7~2.0 구간 선형
    return _clamp(92.0 - (ratio - 0.7) / 1.3 * 80.0)


PEG_GROWTH_CAP = 50.0   # 성장률 상한(%)


def peg_analysis(fper, per, growth_fwd, growth_hist):
    """PEG = PER ÷ 향후 EPS 성장률. 선행 PER 우선, 성장률은 전망 우선.

    ⚠️ 저기반 회복(적자→흑자, 반도체 사이클 등)에서는 성장률이 수백 %로 튄다.
       그대로 나누면 PEG가 0.01처럼 무의미해지므로 **50%로 상한**을 둔다.
       (성장률 50%면 PER 50배까지도 PEG 1 — 충분히 관대한 기준)
    """
    base_per = fper if (fper and fper > 0) else per
    growth = growth_fwd if (growth_fwd is not None and growth_fwd > 0) else growth_hist
    if not base_per or base_per <= 0 or growth is None or growth <= 0:
        return None
    capped = min(growth, PEG_GROWTH_CAP)
    peg = base_per / capped
    # 사용자 기준: 0.7이하 저평가 / 1 전후 적정 / 1.5이상 다소고평가 / 2이상 상당고평가
    if peg <= 0.7:
        label, score = "저평가", 92.0
    elif peg <= 1.0:
        label, score = "적정", 75.0
    elif peg <= 1.5:
        label, score = "다소 고평가", 50.0
    elif peg <= 2.0:
        label, score = "고평가", 30.0
    else:
        label, score = "상당한 고평가", 14.0
    return {
        "peg": round(peg, 2),
        "per_used": round(base_per, 2),
        "growth_used": round(capped, 1),
        "growth_raw": round(growth, 1),
        "capped": growth > PEG_GROWTH_CAP,
        "is_forward": bool(fper and fper > 0),
        "label": label,
        "score": score,
    }


def peer_comparison(my_per, peers_per):
    """동종업계 PER 비교. peers_per: [{name, per}]

    ⚠️ 평균(mean)을 쓰면 적자 직후·턴어라운드 종목의 PER 1000배가 업종 평균을
       통째로 오염시킨다(실제로 주성엔지니어링 1036배 → 업종평균 238배가 됨).
       → **중앙값**을 쓰고, PER 200배 초과는 이상치로 제외한다.
    """
    # ⚠️ 비교군에 자기 자신이 섞여 있으면(main.py가 편의상 peers_per에 self:True로 끼워 넣음)
    # 중앙값이 항상 my_per 쪽으로 끌려가 "업종 평균 수준"만 나오는 무의미한 비교가 된다
    # (실측 확인: SK하이닉스 "14.72배 vs 14.72배"). 계산·표시 목록 모두에서 제외한다.
    vals = sorted(p["per"] for p in (peers_per or [])
                  if p.get("per") and 0 < p["per"] <= 200 and not p.get("self"))
    if not my_per or my_per <= 0 or len(vals) < 2:
        return None
    n = len(vals)
    avg = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    ratio = my_per / avg
    # 업종 평균 대비 0.7배 이하면 저평가, 1.5배 이상이면 고평가
    score = _band_score(ratio)
    if ratio <= 0.85:
        label = "업종 평균 대비 저평가"
    elif ratio <= 1.15:
        label = "업종 평균 수준"
    else:
        label = "업종 평균 대비 고평가"
    return {
        "my_per": round(my_per, 2),
        "peer_avg": round(avg, 2),          # 중앙값
        "is_median": True,
        "ratio": round(ratio, 2),
        "label": label,
        "score": score,
        "peers": sorted([p for p in peers_per if p.get("per") and 0 < p["per"] <= 200 and not p.get("self")],
                        key=lambda p: p["per"])[:6],
    }


def ev_ebitda(market_cap_eok, fin_rows, debt_ratio, bps, price, market="KR"):
    """EV/EBITDA. EBITDA는 미국 종목만 제공되므로 국내는 None.

    ⚠️ 단위 함정(실측으로 확인): 미국 종목은 **시가총액=억 USD, 재무제표=백만 USD** 라
       그대로 나누면 100배 어긋난다(AAPL 34배가 0.34배로 나왔음).
       1억 = 100백만 이므로 미국 EBITDA는 100으로 나눠 억 단위로 맞춘다.
       (국내는 시총·재무 모두 억원이라 환산 불필요)

    순차입금 데이터가 없어 EV는 근사:
      EV ≈ 시가총액 + 총부채(자본×부채비율)  ← 현금 차감 불가(과대 추정)
    부채비율이 없으면 EV = 시가총액으로 둔다.
    """
    ebitda_series = None
    for name, series in (fin_rows or {}).items():
        if name.strip() in ("EBITDA",):
            ebitda_series = series
            break
    if not ebitda_series or not market_cap_eok:
        return None
    actuals = [s["value"] for s in ebitda_series if not s.get("consensus") and s.get("value")]
    if not actuals:
        return None
    unit = 100.0 if market == "US" else 1.0     # 백만 USD → 억 USD
    actuals = [v / unit for v in actuals]
    ebitda = actuals[-1]
    if ebitda <= 0:
        return None

    ev = market_cap_eok
    approx = "시가총액 기준(순차입금 미반영)"
    if debt_ratio and bps and price and price > 0:
        # 자본 ≈ 시총 × (BPS/주가) → 부채 = 자본 × 부채비율
        equity = market_cap_eok * (bps / price)
        ev = market_cap_eok + equity * (debt_ratio / 100.0)
        approx = "부채 반영·현금 미차감 근사"
    ratio = ev / ebitda
    # 과거 EBITDA 평균 대비도 계산
    hist = [v for v in actuals if v > 0]
    hist_ratio = None
    if len(hist) >= 2:
        avg_ebitda = sum(hist) / len(hist)
        hist_ratio = round(ev / avg_ebitda, 2) if avg_ebitda > 0 else None
    score = _score_low(ratio, best=6, worst=25, floor=15, top=92)
    return {
        "ev_ebitda": round(ratio, 2),
        "ebitda": round(ebitda, 1),
        "vs_hist_avg": hist_ratio,
        "basis": approx,
        "score": score,
    }


def fair_buy_price(price, band, peer, peg, cons):
    """PER/PEG/업종평균/애널리스트 목표주가를 종합해 '적정가'를 추정하고,
    안전마진을 차등 적용해 보수적/기준/낙관적 3단계 매수가를 산출한다.

    각 관점은 "현재가를 그 배수 괴리만큼 되돌린 가격"으로 환산해 공통 척도(원)로 맞춘다:
      - 역사적밸류: price ÷ (현재/과거평균 배수)
      - 동종업계:   price ÷ (내 PER/업종중앙값 배수)
      - PEG:        price × (PEG=1이 되는 목표배수 ÷ 현재 사용배수)
      - 애널리스트 목표주가: 그대로 사용(가장 느슨한 가중치 — 후행 경향 때문)
    가중평균(사용 가능한 항목만)으로 '적정가'를 만들고, 20%/10%/0% 안전마진을 적용한다.
    """
    if not price or price <= 0:
        return None

    # 각 관점의 '괴리 배수'는 저PER+고성장 상한(50%) 조합 등에서 극단값(수배)이 나올 수 있다.
    # 개별 관점이 종합 적정가를 통째로 왜곡하지 않도록, 현재가의 0.5~1.6배 범위로 clamp한다
    # (동일한 이유로 peer_comparison은 PER 200배 초과 제외, PEG는 성장률 50% 상한을 이미 적용 중).
    lo, hi = price * 0.5, price * 1.6

    components = []  # (fair_price, weight)
    if band and band.get("ratio") and band["ratio"] > 0:
        components.append((_clamp(price / band["ratio"], lo, hi), 0.35))
    if peer and peer.get("ratio") and peer["ratio"] > 0:
        components.append((_clamp(price / peer["ratio"], lo, hi), 0.20))
    if peg and peg.get("per_used") and peg["per_used"] > 0 and peg.get("growth_used"):
        components.append((_clamp(price * (peg["growth_used"] / peg["per_used"]), lo, hi), 0.20))
    target = cons.get("target_price") if cons else None
    if target and target > 0:
        # 괴리 과대로 플래그된 목표주가(analysis.consensus_info)는 적정가 산정에서도
        # 가중치를 낮춘다 — 이상치 검증이 재무 추정치에만 적용되고 목표주가에는
        # 빠져 있던 문제(2차 진단리포트 4-1)를 여기서도 함께 막는다.
        w = 0.25 * (cons.get("upside_weight", 1.0) if cons else 1.0)
        components.append((_clamp(target, lo, hi), w))

    if not components:
        return None

    tw = sum(w for _, w in components)
    fair_value = sum(p * w for p, w in components) / tw

    conservative = fair_value * 0.80
    base = fair_value * 0.90
    optimistic = fair_value * 1.00

    def _upside(v):
        return round((v - price) / price * 100, 1)

    return {
        "fair_value": round(fair_value),
        "sources": len(components),
        "conservative": {"price": round(conservative), "margin": 20, "upside": _upside(conservative)},
        "base": {"price": round(base), "margin": 10, "upside": _upside(base)},
        "optimistic": {"price": round(optimistic), "margin": 0, "upside": _upside(optimistic)},
    }


def per_backtest(hist, current_per):
    """"이 종목이 과거에 지금과 비슷한 PER이었을 때, 그 이후 1년 수익률은?"

    per_history()가 만든 연도별 평균PER·평균주가를 재사용한다(추가 데이터 조회 없음).
    실적연도(컨센서스 제외)만 후보로 쓰고, 현재 PER과의 괴리가 작은 순으로 최대 4개를
    골라 "그 해 평균주가 → 다음 해 평균주가" 수익률을 계산한다. 5년치 일봉만 있어 표본이
    적으므로, 괴리 50% 이내인 후보가 하나도 없으면 available=False로 솔직하게 알린다.
    """
    if not hist or not current_per or current_per <= 0:
        return None
    years = hist.get("years") or []
    by_year = {y["year"]: y for y in years}
    this_year = datetime.date.today().year   # 진행 중인 해는 평균주가가 아직 완성되지 않아 제외

    candidates = []
    for y in years:
        if y.get("consensus") or not y.get("per") or not y.get("avg_price"):
            continue
        nxt = by_year.get(y["year"] + 1)
        # 다음 해가 아직 진행 중이면 "1년 후 수익률"이 아니라 "지금까지의 수익률"이 되어
        # 최근 상승분이 그대로 끼어든다 — 완결된 해만 종료 시점으로 인정한다.
        if not nxt or not nxt.get("avg_price") or nxt["year"] >= this_year:
            continue
        diff_ratio = abs(y["per"] - current_per) / current_per
        if diff_ratio > 0.5:
            continue
        ret = (nxt["avg_price"] - y["avg_price"]) / y["avg_price"] * 100
        candidates.append({
            "year": y["year"], "per": y["per"], "avg_price": y["avg_price"],
            "return_1y": round(ret, 1), "_diff": diff_ratio,
        })
    if not candidates:
        return {"available": False}

    candidates.sort(key=lambda c: c["_diff"])
    matches = candidates[:4]
    for m in matches:
        del m["_diff"]
    matches.sort(key=lambda m: m["year"])

    avg_return = sum(m["return_1y"] for m in matches) / len(matches)
    win_rate = round(sum(1 for m in matches if m["return_1y"] > 0) / len(matches) * 100)
    return {
        "available": True,
        "current_per": round(current_per, 2),
        "matches": matches,
        "avg_return_1y": round(avg_return, 1),
        "win_rate": win_rate,
    }


def analyze(metrics, fin_rows, candles, cons, peers_per=None, market_cap=None, price=None, market="KR"):
    """밸류에이션 종합 분석 → 지표 + 6개 항목 점수 + 종합.

    metrics: analysis.fundamental_analysis()['metrics']
    cons:    analysis.consensus_info() 결과
    peers_per: [{name, per}] 동종업계
    """
    per = metrics.get("per")
    fper = metrics.get("cns_per")
    hist = per_history(fin_rows, candles)

    parts, signals, checklist = {}, [], []

    if metrics.get("consensus_flagged"):
        signals.append(("warn", f"⚠️ 컨센서스 추정치 이상치 감지({metrics.get('consensus_flag_reason')}) "
                                "— 선행PER·PEG·실적방향 점수에서 제외하고 실적(trailing) 기준으로만 평가합니다"))
        checklist.append({
            "item": "컨센서스 추정치가 신뢰할 만한가?",
            "verdict": "검증 필요",
            "ok": False,
            "detail": metrics.get("consensus_flag_reason") or "-",
        })

    # ── 1순위: 현재 FPER ÷ 과거 평균 FPER ────────────────────────
    band = None
    backtest = None
    if hist:
        cur = fper if (fper and fper > 0) else per
        backtest = per_backtest(hist, cur)
        avg = hist["avg_fper"] if (fper and fper > 0 and hist["avg_fper"]) else hist["avg_per"]
        kind = "FPER" if (fper and fper > 0 and hist["avg_fper"]) else "PER"
        cnt = hist["fper_count"] if kind == "FPER" else hist["per_count"]
        if cur and cur > 0 and avg and avg > 0:
            ratio = cur / avg
            sc = _band_score(ratio)
            band = {
                "kind": kind, "current": round(cur, 2), "hist_avg": round(avg, 2),
                "ratio": round(ratio, 2), "years": cnt, "score": sc,
            }
            parts["역사적밸류"] = sc
            if ratio >= 1.5:
                band["label"] = f"과거 평균보다 {(ratio - 1) * 100:.0f}% 비쌈 — 경계 구간"
                signals.append(("bear", f"현재 {kind} {cur:.1f}배가 과거 {cnt}년 평균({avg:.1f}배)보다 "
                                        f"{(ratio - 1) * 100:.0f}% 높습니다. 성장이 뒷받침되는지 확인 필요"))
            elif ratio >= 1.2:
                band["label"] = f"과거 평균보다 {(ratio - 1) * 100:.0f}% 비쌈"
                signals.append(("warn", f"현재 {kind}가 과거 평균 대비 {(ratio - 1) * 100:.0f}% 높은 수준"))
            elif ratio <= 0.8:
                band["label"] = f"과거 평균보다 {(1 - ratio) * 100:.0f}% 쌈 — 저평가 구간"
                signals.append(("bull", f"현재 {kind} {cur:.1f}배가 과거 평균({avg:.1f}배)보다 "
                                        f"{(1 - ratio) * 100:.0f}% 낮습니다"))
            else:
                band["label"] = "과거 평균 수준"
                signals.append(("neutral", f"현재 {kind}가 과거 {cnt}년 평균과 비슷한 수준"))
            checklist.append({
                "item": f"{kind}가 과거 평균보다 비싼가?",
                "verdict": "비쌈" if ratio > 1.15 else ("쌈" if ratio < 0.85 else "비슷"),
                "ok": ratio <= 1.15,
                "detail": f"현재 {cur:.1f}배 vs 평균 {avg:.1f}배 ({ratio:.2f}배)",
            })

    # ── 2순위: PEG ──────────────────────────────────────────────
    peg = peg_analysis(fper, per, metrics.get("op_growth_fwd"), metrics.get("op_growth"))
    if peg:
        parts["PEG"] = peg["score"]
        tag = "선행PER" if peg["is_forward"] else "PER"
        signals.append(("bull" if peg["peg"] <= 1.0 else "bear" if peg["peg"] > 1.5 else "neutral",
                        f"PEG {peg['peg']} ({tag} {peg['per_used']}배 ÷ 성장률 {peg['growth_used']}%) — {peg['label']}"))
        checklist.append({
            "item": "PEG가 1 이하인가?",
            "verdict": peg["label"],
            "ok": peg["peg"] <= 1.0,
            "detail": f"PEG {peg['peg']} = {peg['per_used']}배 ÷ {peg['growth_used']}%",
        })

    # ── 3순위: 동종업계 ─────────────────────────────────────────
    peer = peer_comparison(per, peers_per)
    if peer:
        parts["동종업계"] = peer["score"]
        signals.append(("bull" if peer["ratio"] < 0.85 else "bear" if peer["ratio"] > 1.15 else "neutral",
                        f"업종 평균 PER {peer['peer_avg']}배 대비 {peer['ratio']}배 — {peer['label']}"))
        checklist.append({
            "item": "경쟁사보다 비싼가?",
            "verdict": peer["label"],
            "ok": peer["ratio"] <= 1.15,
            "detail": f"내 PER {peer['my_per']}배 vs 업종 평균 {peer['peer_avg']}배",
        })

    # ── 4순위: EV/EBITDA ────────────────────────────────────────
    ev = ev_ebitda(market_cap, fin_rows, metrics.get("debt_ratio"), metrics.get("bps"), price, market)
    if ev and ev.get("score") is not None:
        parts["EV/EBITDA"] = ev["score"]
        signals.append(("neutral", f"EV/EBITDA {ev['ev_ebitda']}배 ({ev['basis']})"))
        checklist.append({
            "item": "EV/EBITDA가 부담스러운 수준인가?",
            "verdict": "양호" if ev["ev_ebitda"] <= 12 else "부담",
            "ok": ev["ev_ebitda"] <= 12,
            "detail": f"{ev['ev_ebitda']}배 · {ev['basis']}",
        })

    # ── 5순위: 목표주가 상승여력 ────────────────────────────────
    upside = cons.get("upside") if cons else None
    if upside is not None:
        # 목표주가는 후행 경향이 있어 과신 금물 → 상승여력 60%에서 상한(92점)
        sc = _clamp(50 + max(-60.0, min(upside, 60.0)) * 0.7)
        parts["목표주가"] = sc
        signals.append(("bull" if upside > 15 else "bear" if upside < 0 else "neutral",
                        f"애널리스트 목표주가 대비 상승여력 {upside:+.1f}%"
                        + (" (목표주가는 후행 경향이 있어 참고용)" if upside > 0 else "")))
        checklist.append({
            "item": "목표주가 상승 여력이 있는가?",
            "verdict": f"{upside:+.1f}%",
            "ok": upside > 0,
            "detail": f"목표 {cons.get('target_price'):,.0f}" if cons.get("target_price") else "-",
        })

    # ── 6순위: 실적 추정치 방향 ─────────────────────────────────
    # 과거 컨센서스 이력이 없어 '상향 추세'는 추적 불가.
    # 대신 컨센서스 전망이 직전 실적 대비 성장하는지로 방향을 본다.
    gf = metrics.get("op_growth_fwd")
    if gf is not None:
        # 저기반 회복(적자→흑자 등)의 세자릿수 성장이 곧바로 만점이 되지 않게 50%에서 상한.
        # ⚠️ 점수(sc)만 상한을 적용하고 신호 텍스트는 원본 gf를 그대로 보여주던 실수가
        # 있었다(실측 확인: 삼성전자 "컨센서스 영업이익 전망 +796.6%" 같은 왜곡값이 그대로
        # 사용자에게 노출됨). anomaly.py의 GROWTH_DISPLAY_CAP과 같은 원칙으로 **표시값도
        # PEG_GROWTH_CAP(50%)으로 캡**하고, 원본이 그보다 크면 "저기반 효과로 상한 표시"임을
        # 명시한다 — 점수 계산에 쓰는 실제 상한과 항상 같은 값을 쓸 것(따로 관리하면 또 어긋남).
        sc = _clamp(50 + max(-50.0, min(gf, 50.0)) * 0.8)
        parts["실적방향"] = sc
        gf_disp = max(-PEG_GROWTH_CAP, min(gf, PEG_GROWTH_CAP))
        gf_capped = abs(gf) > PEG_GROWTH_CAP
        note = " (저기반 효과로 상한 표시, 실제 수치는 이보다 더 극단적)" if gf_capped else ""
        signals.append(("bull" if gf > 10 else "bear" if gf < 0 else "neutral",
                        f"컨센서스 영업이익 전망 {gf_disp:+.1f}%{note} "
                        + ("— 실적 개선 기대" if gf > 0 else "— 실적 둔화 전망")))
        checklist.append({
            "item": "실적 추정치가 성장 방향인가?",
            "verdict": f"{gf_disp:+.1f}%{note}",
            "ok": gf > 0,
            "detail": "컨센서스 영업이익 전망 (과거 추정치 이력은 미제공)",
        })

    if not parts:
        return {"available": False}

    # 종합: 사용자 우선순위대로 가중
    weights = {"역사적밸류": 0.30, "PEG": 0.25, "동종업계": 0.15,
               "EV/EBITDA": 0.10, "목표주가": 0.10, "실적방향": 0.10}
    tw = sum(w for k, w in weights.items() if k in parts)
    score = sum(parts[k] * weights[k] for k in parts) / tw if tw else 50.0

    if score >= 72:
        verdict, vcls = "저평가 구간", "buy"
    elif score >= 58:
        verdict, vcls = "적정 수준", "accumulate"
    elif score >= 42:
        verdict, vcls = "다소 부담", "hold"
    else:
        verdict, vcls = "고평가 경계", "avoid"

    return {
        "available": True,
        "score": round(_clamp(score), 1),
        "verdict": verdict,
        "verdict_class": vcls,
        "parts": {k: round(v, 1) for k, v in parts.items()},
        "band": band,
        "history": hist,
        "backtest": backtest,
        "peg": peg,
        "peer": peer,
        "ev_ebitda": ev,
        "fair_buy": fair_buy_price(price, band, peer, peg, cons),
        "current": {"per": per, "fper": fper, "pbr": metrics.get("pbr"),
                    "eps": metrics.get("eps"), "cns_eps": metrics.get("cns_eps")},
        "checklist": checklist,
        "signals": [{"type": t, "text": s} for t, s in signals],
    }
