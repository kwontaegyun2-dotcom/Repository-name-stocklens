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
                rec = {"date": today, "market": market, "code": r["code"], "name": r["name"],
                       "score": r["score"], "grade": r["grade"], "price": r["price"], "bench": bench}
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


def dashboard():
    records = _load_all()
    if not records:
        return {"available": False, "start_date": None, "days_collected": 0, "periods": []}

    by_date = {}
    for r in records:
        by_date.setdefault(r["date"], []).append(r)
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
        bench_returns = {}
        for rec in base_recs:
            key = (rec["market"], rec["code"])
            cur = latest_by_key.get(key)
            if not cur or not rec.get("price") or not cur.get("price"):
                continue
            ret = (cur["price"] - rec["price"]) / rec["price"] * 100
            for name, min_score in _BUCKETS:
                if rec["score"] >= min_score:
                    buckets[name].append(ret)
                    break
            if rec["market"] not in bench_returns and rec.get("bench") and latest_bench.get(rec["market"]):
                bench_returns[rec["market"]] = (latest_bench[rec["market"]] - rec["bench"]) / rec["bench"] * 100

        bench_avg = round(sum(bench_returns.values()) / len(bench_returns), 2) if bench_returns else None
        bucket_stats = []
        for name, _ in _BUCKETS:
            vals = buckets[name]
            if vals:
                avg = sum(vals) / len(vals)
                bucket_stats.append({
                    "grade": name, "count": len(vals),
                    "avg_return": round(avg, 2),
                    "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
                    "excess_vs_bench": round(avg - bench_avg, 2) if bench_avg is not None else None,
                })
            else:
                bucket_stats.append({"grade": name, "count": 0, "avg_return": None, "win_rate": None, "excess_vs_bench": None})
        periods_out.append({
            "label": label, "days": days, "available": True, "base_date": base_date,
            "sample_size": len(base_recs), "bench_return": bench_avg, "buckets": bucket_stats,
        })

    return {
        "available": True, "start_date": start_date, "days_collected": days_collected,
        "latest_date": latest_date, "periods": periods_out,
    }
