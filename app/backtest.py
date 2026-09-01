# -*- coding: utf-8 -*-
"""점수 백테스트 — "이 점수가 실제로 통했는가"를 매일 실제로 추적해서 보여준다.

⚠️ 과거 데이터를 흉내 내 가짜 백테스트를 만들지 않는다. 현재 시스템엔 과거 시점의
재무제표·컨센서스·기술지표를 그 시점 기준으로 재구성할 방법이 없어(모두 "현재" 값만
제공됨), 진짜 과거 백테스트는 불가능하다 — 대신 오늘부터 매일 랭킹 스냅샷(종목·점수·
등급·가격)을 실제로 쌓고, 그 이후 실현된 가격으로 "그때 그 점수를 받은 종목들이 그 뒤
얼마나 올랐는가"를 계산한다. 데이터가 쌓이기 전까지는 정직하게 "집계 중"이라고 보여준다
(진단리포트 9장: "결과가 좋지 않아도 상관없다, 그 정직함 자체가 차별점이 된다").
"""
import json
import time
import datetime
import threading
from pathlib import Path

from app import naver

_lock = threading.Lock()
_snap_path = None
_state_path = None


def init(data_dir: Path):
    global _snap_path, _state_path
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    _snap_path = data_dir / "backtest_snapshots.jsonl"
    _state_path = data_dir / "backtest_state.json"


def _today():
    return datetime.date.today().isoformat()


def _read_state():
    if not _state_path or not _state_path.exists():
        return {}
    try:
        return json.loads(_state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state):
    _state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _bench_price(market):
    """벤치마크 지수 현재가 — 국내 KOSPI / 미국 SPY. 실패하면 None(그 시장 초과수익률
    비교는 건너뛰고 종목 수익률만 보여준다)."""
    try:
        if market == "US":
            c = naver.candles("SPY", 3)
        else:
            c = naver.index_candles("KOSPI", 3)
        return c[-1]["close"] if c else None
    except Exception:
        return None


def snapshot(market: str, items: list):
    """market의 오늘 랭킹을 스냅샷으로 남긴다. 하루 한 번만 실제로 기록(idempotent) —
    ranking.py의 30분 주기 재계산마다 호출돼도 상관없다."""
    if not _snap_path or not items:
        return
    with _lock:
        state = _read_state()
        if state.get(market) == _today():
            return
        today = _today()
        bench = _bench_price(market)
        with open(_snap_path, "a", encoding="utf-8") as f:
            for r in items:
                if not r.get("price"):
                    continue
                # 진단리포트(2026-08-31) 7번 — 동일가중/시가총액가중 분리를 요구했는데
                # 그동안 market_cap을 스냅샷에 안 남겨서 과거분은 시가총액가중을 소급
                # 계산할 수 없다. 오늘부터라도 남겨서 데이터가 쌓이는 대로 지원한다.
                rec = {"date": today, "market": market, "code": r["code"], "name": r["name"],
                       "score": r["score"], "grade": r["grade"], "price": r["price"], "bench": bench,
                       "market_cap": r.get("market_cap")}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        state[market] = today
        _write_state(state)


def _load_all():
    if not _snap_path or not _snap_path.exists():
        return []
    out = []
    with open(_snap_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


_PERIODS = [("1주", 7), ("1개월", 30), ("3개월", 90), ("6개월", 180), ("12개월", 365)]
_BUCKETS = [("S", 85), ("A", 75), ("B", 65), ("C 이하", 0)]

# 진단리포트(2026-08-31) 7번 — "결과가 통계적으로 의미 있다"고 주장하지 않기 위한
# 최소 기준. 이 미만이면 dashboard()가 period별로 reliable=False를 내려보내고,
# 프론트는 그 기준 미달을 숨기지 않고 그대로 보여준다.
MIN_RELIABLE_SAMPLE = 100    # 등급별 최소 독립 표본
MIN_RELIABLE_DAYS = 180      # 최소 추적 기간(6개월)
ROUNDTRIP_COST_PCT = 0.3     # 거래비용+슬리피지 가정치(왕복, %) — 매수+매도 수수료·호가스프레드 근사치


def _mdd_vol_sharpe(index_series):
    """index_series: [index_value, ...] (등락률로 환산된 동일가중 포트폴리오 지수, 시작=100).
    반환: (최대낙폭%, 연환산변동성%, 샤프지수) — 무위험수익률 0% 가정(단순화, 화면에 고지)."""
    if len(index_series) < 3:
        return None, None, None
    peak = index_series[0]
    mdd = 0.0
    for v in index_series:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak * 100)
    daily_rets = [(index_series[i] - index_series[i - 1]) / index_series[i - 1]
                  for i in range(1, len(index_series)) if index_series[i - 1]]
    if len(daily_rets) < 2:
        return round(mdd, 1), None, None
    mean_r = sum(daily_rets) / len(daily_rets)
    var = sum((r - mean_r) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
    sd = var ** 0.5
    vol_annual = sd * (252 ** 0.5) * 100
    sharpe = round((mean_r * 252) / (sd * (252 ** 0.5)), 2) if sd > 0 else None
    return round(mdd, 1), round(vol_annual, 1), sharpe


def dashboard():
    records = _load_all()
    if not records:
        return {"available": False, "start_date": None, "days_collected": 0, "periods": []}

    by_date = {}
    price_by_key = {}   # (market, code) -> {date: price}  — MDD/변동성 계산용 일별 가격 인덱스
    for r in records:
        by_date.setdefault(r["date"], []).append(r)
        if r.get("price"):
            price_by_key.setdefault((r["market"], r["code"]), {})[r["date"]] = r["price"]
    dates = sorted(by_date.keys())
    start_date = dates[0]
    latest_date = dates[-1]
    latest_by_key = {(r["market"], r["code"]): r for r in by_date[latest_date]}
    latest_bench = {r["market"]: r.get("bench") for r in by_date[latest_date]}
    today = datetime.date.today()
    days_collected = (today - datetime.date.fromisoformat(start_date)).days + 1

    periods_out = []
    for label, days in _PERIODS:
        target = (today - datetime.timedelta(days=days)).isoformat()
        candidates = [d for d in dates if d <= target]
        if not candidates:
            periods_out.append({"label": label, "days": days, "available": False})
            continue
        base_date = candidates[-1]
        base_recs = by_date[base_date]
        buckets = {name: [] for name, _ in _BUCKETS}
        excluded = {name: 0 for name, _ in _BUCKETS}   # 생존편향 고지용 — 매칭 실패(상장폐지·거래정지 포함 가능) 개수
        bench_returns = {}
        period_dates = [d for d in dates if base_date <= d <= latest_date]
        for rec in base_recs:
            key = (rec["market"], rec["code"])
            grade_name = next((name for name, min_score in _BUCKETS if rec["score"] >= min_score), None)
            cur = latest_by_key.get(key)
            if not cur or not rec.get("price") or not cur.get("price"):
                # ⚠️ 진단리포트 7번 — 여기서 그냥 건너뛰면 상장폐지·거래정지된 종목이
                # 조용히 사라져 생존편향이 생긴다(살아남은 종목만으로 계산). 현재 데이터로는
                # "왜" 사라졌는지 구분할 방법이 없어(상장폐지 API 미연동) 되살릴 순 없지만,
                # 최소한 몇 종목이 빠졌는지는 숨기지 않고 excluded로 공개한다.
                if grade_name:
                    excluded[grade_name] += 1
                continue
            ret = (cur["price"] - rec["price"]) / rec["price"] * 100
            if grade_name:
                # ⚠️ 예전엔 여기서 수익률(ret)만 남기고 종목명·코드를 버렸다 — "S등급이
                # 실제로 어떤 종목들인지, 얼마나 올랐는지 보고 싶다"는 요청으로
                # rec 자체를 들고 있다가 아래서 종목명 리스트로 노출한다. market도 함께
                # 들고 있어야 한다 — 버킷 하나에 국내+미국 종목이 섞여 있어(각 period가
                # 시장 구분 없이 전체를 다룸) code만으로는 가격 인덱스를 못 찾는다.
                buckets[grade_name].append({"market": rec["market"], "code": rec["code"],
                                            "name": rec["name"], "return": round(ret, 2)})
            if rec["market"] not in bench_returns and rec.get("bench") and latest_bench.get(rec["market"]):
                bench_returns[rec["market"]] = (latest_bench[rec["market"]] - rec["bench"]) / rec["bench"] * 100

        bench_avg = round(sum(bench_returns.values()) / len(bench_returns), 2) if bench_returns else None
        bucket_stats = []
        for name, _ in _BUCKETS:
            vals = buckets[name]
            if vals:
                rets = [v["return"] for v in vals]
                avg = sum(rets) / len(rets)
                keys = [(v["market"], v["code"]) for v in vals]
                # 등급 버킷 전체의 일별 동일가중 지수를 재구성해 MDD·변동성·샤프를 계산한다
                # (시작~끝 수익률만으로는 그 사이의 하락폭·변동성을 알 수 없다는 지적).
                index_series = []
                for d in period_dates:
                    ratios = [price_by_key[k][d] / price_by_key[k][base_date]
                              for k in keys
                              if price_by_key.get(k, {}).get(d) and price_by_key.get(k, {}).get(base_date)]
                    if ratios:
                        index_series.append(sum(ratios) / len(ratios) * 100)
                mdd, vol, sharpe = _mdd_vol_sharpe(index_series)
                stocks_sorted = sorted(vals, key=lambda v: v["return"], reverse=True)
                bucket_stats.append({
                    "grade": name, "count": len(vals),
                    "avg_return": round(avg, 2),
                    "net_avg_return": round(avg - ROUNDTRIP_COST_PCT, 2),   # 거래비용 가정 반영(7번)
                    "win_rate": round(sum(1 for v in rets if v > 0) / len(rets) * 100, 1),
                    "excess_vs_bench": round(avg - bench_avg, 2) if bench_avg is not None else None,
                    "mdd": mdd, "volatility": vol, "sharpe": sharpe,
                    "excluded_count": excluded[name],
                    # 수익률 높은 순 최대 10개만 노출(B·C등급은 최대 수백 종목이라 전부
                    # 보여주면 화면이 감당 안 된다) — 나머지는 more_count로 개수만 알려준다.
                    "stocks": stocks_sorted[:10],
                    "more_count": max(0, len(stocks_sorted) - 10),
                })
            else:
                bucket_stats.append({"grade": name, "count": 0, "avg_return": None, "net_avg_return": None,
                                      "win_rate": None, "excess_vs_bench": None, "mdd": None, "volatility": None,
                                      "sharpe": None, "excluded_count": excluded[name], "stocks": [], "more_count": 0})
        sample_size = len(base_recs)
        periods_out.append({
            "label": label, "days": days, "available": True, "base_date": base_date,
            "sample_size": sample_size, "bench_return": bench_avg, "buckets": bucket_stats,
            # 진단리포트 7번 — "통계적으로 의미 있다"고 과장하지 않기 위한 최소 기준 미달 여부.
            # 표본(등급당 최소 100)과 기간(6개월) 둘 다 채워야 reliable=True.
            "reliable": sample_size >= MIN_RELIABLE_SAMPLE and days_collected >= max(days, MIN_RELIABLE_DAYS),
        })

    return {
        "available": True, "start_date": start_date, "days_collected": days_collected,
        "latest_date": latest_date, "periods": periods_out,
        "methodology": {
            "weighting": "동일가중(종목당 균등 비중) — 시가총액가중은 2026-09-01부터 스냅샷에 시가총액을 "
                         "함께 기록하기 시작해 데이터가 쌓이는 대로 추가 지원 예정입니다.",
            "grade_reassignment": "각 기간은 그 기간 시작 시점(base_date)의 등급을 기준으로 분류합니다. "
                                  "등급은 매 기간마다 그 시점 점수로 다시 매겨지므로, 같은 종목이 기간에 "
                                  "따라 다른 등급 버킷에 속할 수 있습니다(예: 1주 전엔 A등급, 1개월 전엔 B등급).",
            "cost_assumption": f"거래비용·슬리피지는 왕복 {ROUNDTRIP_COST_PCT}%로 가정해 net_avg_return에 "
                               "반영했습니다(실제 비용은 증권사·거래 규모에 따라 다릅니다).",
            "survivorship": "추적 중 최신 스냅샷에서 가격을 찾지 못한 종목(상장폐지·거래정지·일시적 "
                            "데이터 누락 포함)은 수익률 계산에서 제외하고, 그 개수를 excluded_count로 "
                            "공개합니다 — 사유를 구분해 반영하지는 못합니다(상장폐지 감지 API 미연동).",
            "no_hindsight": "매일 그 시점에 실제로 계산된 점수·등급을 그대로 기록합니다 — 이후 확정된 "
                            "재무제표나 사후 데이터로 과거 점수를 다시 계산하지 않습니다.",
            "min_reliable_sample": MIN_RELIABLE_SAMPLE, "min_reliable_days": MIN_RELIABLE_DAYS,
        },
    }
