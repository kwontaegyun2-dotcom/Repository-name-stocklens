# -*- coding: utf-8 -*-
"""밸류에이션·리레이팅 스크리너 — 시총 상위 종목 스냅샷(밸류+수급+업종).

데이터: 네이버 벌크(시총순) + 종목별 integration 1콜(PER/PBR·수급·업종코드).
축1(밸류) + 축2(실적변곡, DART 영업이익 YoY) + 축3(수급).
DART 키가 없으면 축2를 빼고 기존 공식으로 자동 폴백한다.
"""
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app import dart, naver
from app.analysis import to_num, parse_eok

# 시총 상위 (KOSPI 3페이지=300 + KOSDAQ 1페이지=100 = 400종목)
UNIVERSE_PAGES = [("KOSPI", 3), ("KOSDAQ", 1)]
REFRESH_SEC = 21600  # 6시간
MIN_TVAL_EOK = 10    # 거래대금 하한 10억(유동성) — 밸류트랩·상폐 리스크 제외

_lock = threading.Lock()
_state = {"rows": [], "updated_at": 0, "computing": False, "dart": False, "period": ""}
_started = False


def _bulk_universe():
    out = []
    for market, pages in UNIVERSE_PAGES:
        for pg in range(1, pages + 1):
            try:
                data = naver._get(
                    f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={pg}&pageSize=100",
                    ttl=1800)
            except Exception:
                continue
            for s in data.get("stocks", []):
                out.append({
                    "code": s.get("itemCode"),
                    "name": s.get("stockName"),
                    "market": market,
                    "price": to_num(s.get("closePrice")),
                    "rate": to_num(s.get("fluctuationsRatio")),
                    "mcap": to_num(s.get("marketValue")),                 # 억원
                    "tval": (to_num(s.get("accumulatedTradingValueRaw")) or 0) / 1e8,  # 억원
                })
    return out


def _enrich(u):
    try:
        ig = naver.integration(u["code"])
        ti = {i.get("code"): i.get("value") for i in ig.get("totalInfos", [])}
        per = to_num(ti.get("per"))
        pbr = to_num(ti.get("pbr"))
        eps = to_num(ti.get("eps"))
        bps = to_num(ti.get("bps"))
        div = to_num(ti.get("dividendYieldRatio"))
        roe = round(eps / bps * 100, 2) if (eps and bps) else None
        dti = ig.get("dealTrendInfos") or []
        f20 = sum(to_num(d.get("foreignerPureBuyQuant")) or 0 for d in dti[:20])
        i20 = sum(to_num(d.get("organPureBuyQuant")) or 0 for d in dti[:20])
        return {
            **u, "per": per, "pbr": pbr, "eps": eps, "bps": bps, "div": div, "roe": roe,
            "industry": str(ig.get("industryCode") or ""),
            "foreign20": f20, "inst20": i20,
            "flow": (1 if f20 > 0 else 0) + (1 if i20 > 0 else 0),
        }
    except Exception:
        return None


def _zscores(rows, key, invert=False):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if len(vals) < 3:
        return {}
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    sd = math.sqrt(var) or 1.0
    out = {}
    for r in rows:
        v = r.get(key)
        if v is not None:
            z = (v - mean) / sd
            out[r["code"]] = -z if invert else z
    return out


def _compute():
    with _lock:
        if _state["computing"]:
            return
        _state["computing"] = True
    try:
        universe = _bulk_universe()
        # 유동성 필터 (거래대금)
        universe = [u for u in universe if (u.get("tval") or 0) >= MIN_TVAL_EOK and u.get("code")]
        rows = []
        with ThreadPoolExecutor(max_workers=9) as ex:
            for r in ex.map(_enrich, universe):
                # PER는 필수 조건에서 뺀다 — 적자기업(PER 없음)이 곧 턴어라운드 후보라
                # 여기서 걸러내면 축2가 잡아야 할 종목이 통째로 사라진다.
                if r and r.get("pbr"):
                    rows.append(r)

        # 축2: DART 영업이익 YoY (키 없으면 빈 dict → 폴백)
        dq = dart.get([r["code"] for r in rows])
        for r in rows:
            d = dq.get(r["code"]) or {}
            r["op_yoy"] = d.get("op_yoy")
            r["turnaround"] = d.get("turnaround", False)

        # 리레이팅 스코어: 밸류(저PBR) + 실적변곡 + 수급 z결합
        pbr_z = _zscores(rows, "pbr", invert=True)   # 낮을수록 높은 점수
        flow_z = _zscores([{"code": r["code"], "flowv": r["foreign20"] + r["inst20"]} for r in rows], "flowv")
        op_z = _zscores(rows, "op_yoy") if dq else {}
        # 업종 상대 백분위 (PBR 오름차순 낮을수록 저평가, ROE 높을수록)
        by_ind = {}
        for r in rows:
            by_ind.setdefault(r["industry"], []).append(r)
        pbr_pct, roe_pct = {}, {}
        for ind, grp in by_ind.items():
            gp = [r for r in grp if r.get("pbr") is not None]
            gp_sorted = sorted(gp, key=lambda r: r["pbr"])
            for i, r in enumerate(gp_sorted):
                pbr_pct[r["code"]] = round((i + 0.5) / len(gp_sorted), 3) if len(gp_sorted) > 1 else 0.5
            gr = [r for r in grp if r.get("roe") is not None]
            gr_sorted = sorted(gr, key=lambda r: r["roe"])
            for i, r in enumerate(gr_sorted):
                roe_pct[r["code"]] = round((i + 0.5) / len(gr_sorted), 3) if len(gr_sorted) > 1 else 0.5

        for r in rows:
            if op_z:
                # 설계서 가중치: 실적 변곡이 리레이팅의 핵심 동인
                rr = (pbr_z.get(r["code"], 0) * 0.3
                      + op_z.get(r["code"], 0) * 0.4
                      + flow_z.get(r["code"], 0) * 0.3)
            else:
                rr = pbr_z.get(r["code"], 0) * 0.55 + flow_z.get(r["code"], 0) * 0.45
            r["rerating"] = round(rr, 2)
            r["pbr_pct"] = pbr_pct.get(r["code"])
            r["roe_pct"] = roe_pct.get(r["code"])

        rows.sort(key=lambda r: r["rerating"], reverse=True)
        with _lock:
            _state["rows"] = rows
            _state["updated_at"] = time.time()
            _state["dart"] = bool(op_z)
            _state["period"] = dart.period() if op_z else ""
    finally:
        with _lock:
            _state["computing"] = False


def _loop():
    while True:
        _compute()
        time.sleep(REFRESH_SEC)


def get():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, daemon=True).start()
    with _lock:
        rows = list(_state["rows"])
        meta = {"updated_at": _state["updated_at"], "computing": _state["computing"],
                "dart": _state["dart"], "period": _state["period"]}
    if not rows:
        meta["computing"] = True
    return {"rows": rows, "count": len(rows), **meta}
