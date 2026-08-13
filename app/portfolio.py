# -*- coding: utf-8 -*-
"""내 포트폴리오 — 보유 종목의 평가금액·비중·업종분산·변동성·최대낙폭을 계산한다.

⚠️ v1은 국내 종목만 지원한다. 해외 종목까지 합산하려면 원/달러 환율이 필요한데
이 앱은 환율 소스가 없어(추정치를 쓰면 평가금액이 틀릴 수 있음), 정확하지 않은
숫자를 보여주느니 국내로 범위를 좁혔다 — 해외 보유분은 목록에서 제외하고 그 사실을
명시한다.
"""
import sqlite3
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app import ranking

_db_path = None
_SECTOR_MAP = {code: sector for code, _name, sector in ranking.UNIVERSE}


def init(data_dir: Path):
    global _db_path
    _db_path = data_dir / "users.db"
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            shares REAL NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(user_id, code)
        )""")


def _conn():
    c = sqlite3.connect(_db_path, timeout=10)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------- 보유종목 CRUD
def upsert(user_id: int, code: str, name: str, shares: float):
    """이미 보유 중인 종목을 다시 담으면 추가 매수로 간주해 수량을 더한다
    (버튼이 "추가"인데 값을 덮어쓰면 기존 보유분이 사라진 것처럼 보이는 문제 방지)."""
    if shares <= 0:
        raise ValueError("수량은 0보다 커야 합니다.")
    with _conn() as c:
        row = c.execute(
            "SELECT shares FROM portfolio WHERE user_id=? AND code=?", (user_id, code)
        ).fetchone()
        total_shares = (row["shares"] + shares) if row else shares
        c.execute(
            """INSERT INTO portfolio (user_id, code, name, shares, created_at) VALUES (?,?,?,?,?)
               ON CONFLICT(user_id, code) DO UPDATE SET shares=excluded.shares""",
            (user_id, code, name, total_shares, time.time()),
        )


def remove(user_id: int, code: str):
    with _conn() as c:
        c.execute("DELETE FROM portfolio WHERE user_id=? AND code=?", (user_id, code))


def list_for_user(user_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT code, name, shares FROM portfolio WHERE user_id=? ORDER BY created_at",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 시계열 재구성
def _portfolio_series(holdings):
    """holdings: [{shares, price_by_date}] → 모든 종목에 다 데이터가 있는 날(공통 거래일)만
    골라 일별 평가금액을 합산한다. 개별 종목 지표를 가중평균하는 것보다 실제 분산효과가
    반영된 변동성·최대낙폭이 나온다."""
    if not holdings:
        return []
    date_sets = [set(h["price_by_date"].keys()) for h in holdings]
    common = set.intersection(*date_sets) if date_sets else set()
    if len(common) < 30:
        return []
    dates = sorted(common)[-252:]   # 최근 1년치(거래일 기준)
    series = []
    for d in dates:
        total = sum(h["shares"] * h["price_by_date"][d] for h in holdings)
        series.append(total)
    return series


def _volatility_and_drawdown(values):
    if len(values) < 30:
        return None, None
    rets = [(values[i] - values[i - 1]) / values[i - 1]
            for i in range(1, len(values)) if values[i - 1]]
    if len(rets) < 20:
        return None, None
    vol = statistics.pstdev(rets) * (252 ** 0.5) * 100
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        max_dd = min(max_dd, (v - peak) / peak * 100)
    return round(vol, 1), round(max_dd, 1)


# ---------------------------------------------------------------- 종합 계산
def compute(holding_rows: list[dict], analyze_fn) -> dict:
    """holding_rows: list_for_user() 결과. analyze_fn(code) == main.api_analyze."""
    if not holding_rows:
        return {"available": False, "reason": "담긴 종목이 없습니다."}

    results, excluded = [], []

    def _fetch(row):
        return row, analyze_fn(row["code"])

    with ThreadPoolExecutor(max_workers=min(8, len(holding_rows))) as ex:
        futs = [ex.submit(_fetch, r) for r in holding_rows]
        for fut in as_completed(futs):
            try:
                row, d = fut.result()
            except Exception:
                continue
            if d.get("nation") == "US":
                excluded.append({"code": row["code"], "name": row["name"], "reason": "해외 종목(환율 미지원)"})
                continue
            if not d.get("price"):
                excluded.append({"code": row["code"], "name": row["name"], "reason": "시세 조회 실패"})
                continue
            results.append((row, d))

    if not results:
        return {"available": False, "reason": "계산 가능한 국내 보유 종목이 없습니다.", "excluded": excluded}

    items = []
    total_value = 0.0
    for row, d in results:
        price = d["price"]
        value = row["shares"] * price
        total_value += value
        items.append({
            "code": row["code"], "name": row["name"], "shares": row["shares"],
            "price": price, "value": value,
            "score": d["total"]["total_score"],
            "val_score": (d.get("valuation") or {}).get("score"),
            "upside": (d.get("targets") or {}).get("consensus_upside"),
            "sector": _SECTOR_MAP.get(row["code"], "미분류"),
            "price_by_date": {c["date"]: c["close"] for c in (d.get("candles") or [])},
        })

    if total_value <= 0:
        return {"available": False, "reason": "평가금액을 계산할 수 없습니다.", "excluded": excluded}

    for it in items:
        it["weight"] = round(it["value"] / total_value * 100, 1)

    def _wavg(key):
        pairs = [(it["weight"], it[key]) for it in items if it.get(key) is not None]
        tw = sum(w for w, _ in pairs)
        return round(sum(w * v for w, v in pairs) / tw, 1) if tw else None

    sector_weight: dict = {}
    for it in items:
        sector_weight[it["sector"]] = round(sector_weight.get(it["sector"], 0) + it["weight"], 1)
    sector_weight = dict(sorted(sector_weight.items(), key=lambda kv: -kv[1]))

    series = _portfolio_series([{"shares": it["shares"], "price_by_date": it["price_by_date"]} for it in items])
    vol, mdd = _volatility_and_drawdown(series)

    warnings = []
    for sector, w in sector_weight.items():
        if w >= 40:
            warnings.append(f"⚠️ {sector} 비중 {w:.0f}% — 업종 분산이 부족합니다")
    for it in items:
        if it["weight"] >= 30:
            warnings.append(f"⚠️ {it['name']} 비중 {it['weight']:.0f}% — 특정 종목 집중도가 높습니다")
    if vol is not None and vol >= 30:
        warnings.append(f"⚠️ 포트폴리오 변동성이 높은 편입니다 (연 {vol}%)")

    for it in items:
        del it["price_by_date"]
    items.sort(key=lambda x: -x["weight"])

    return {
        "available": True,
        "total_value": round(total_value),
        "items": items,
        "sector_weight": sector_weight,
        "score": _wavg("score"),
        "valuation_score": _wavg("val_score"),
        "expected_return": _wavg("upside"),
        "volatility": vol,
        "max_drawdown": mdd,
        "warnings": warnings,
        "excluded": excluded,
    }
