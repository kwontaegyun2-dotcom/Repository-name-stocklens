# -*- coding: utf-8 -*-
"""내 포트폴리오 — 보유 종목의 평가금액·비중·업종분산·변동성·최대낙폭을 계산한다.

국내+미국 종목을 함께 지원한다. 미국 종목은 `naver.usd_krw_rate()`(하나은행 고시
환율, 실시간 조회)로 원화 환산해 KR 보유분과 합산한다 — 총자산·비중·업종분산·
상관관계 등 "합산이 필요한" 계산은 전부 원화 기준. 다만 적정매수가·목표주가·RSI
등 종목 자체의 판단 신호는 analyze_fn()이 이미 종목 통화(달러) 그대로 계산해둔
값이므로, 그 신호와 비교할 때는(예: 매수 타이밍 판단) 반드시 원화 환산 전
`price_native`(달러)를 써야 한다 — 환산가와 비교하면 단위가 안 맞아 전부 틀린다.

환율 조회가 실패하면(네트워크 문제 등) 그 미국 종목만 이번 계산에서 제외하고
사유를 명시한다(다음 새로고침 때 재시도되므로 일시적 문제일 뿐).
"""
import sqlite3
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app import analysis, naver, ranking, themes

_KST = ZoneInfo("Asia/Seoul")

_db_path = None
_SECTOR_MAP = {code: sector for code, _name, sector in ranking.UNIVERSE + ranking.US_UNIVERSE}


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
            avg_price REAL,
            created_at REAL NOT NULL,
            UNIQUE(user_id, code)
        )""")
        cols = {row["name"] for row in c.execute("PRAGMA table_info(portfolio)")}
        if "avg_price" not in cols:
            c.execute("ALTER TABLE portfolio ADD COLUMN avg_price REAL")
        if "snapshot_score" not in cols:
            c.execute("ALTER TABLE portfolio ADD COLUMN snapshot_score REAL")
        if "snapshot_date" not in cols:
            c.execute("ALTER TABLE portfolio ADD COLUMN snapshot_date TEXT")


def _conn():
    c = sqlite3.connect(_db_path, timeout=10)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------- 보유종목 CRUD
def upsert(user_id: int, code: str, name: str, shares: float, avg_price: float | None = None):
    """이미 보유 중인 종목을 다시 담으면 추가 매수로 간주해 수량을 더한다
    (버튼이 "추가"인데 값을 덮어쓰면 기존 보유분이 사라진 것처럼 보이는 문제 방지).
    평균단가도 함께 입력되면 (기존수량*기존단가 + 신규수량*신규단가) 가중평균으로 갱신한다.
    한쪽에만 단가가 있으면(과거에 단가 없이 담았던 경우 등) 정확한 가중평균을 낼 수 없으므로
    새로 입력된 단가를 그대로 쓴다 — 두 값 다 없으면 단가 없이 수량만 누적한다."""
    if shares <= 0:
        raise ValueError("수량은 0보다 커야 합니다.")
    if avg_price is not None and avg_price <= 0:
        raise ValueError("평균단가는 0보다 커야 합니다.")
    with _conn() as c:
        row = c.execute(
            "SELECT shares, avg_price FROM portfolio WHERE user_id=? AND code=?", (user_id, code)
        ).fetchone()
        if row:
            old_shares, old_avg = row["shares"], row["avg_price"]
            total_shares = old_shares + shares
            if avg_price is not None and old_avg is not None:
                total_avg = (old_shares * old_avg + shares * avg_price) / total_shares
            elif avg_price is not None:
                total_avg = avg_price
            else:
                total_avg = old_avg
        else:
            total_shares, total_avg = shares, avg_price
        c.execute(
            """INSERT INTO portfolio (user_id, code, name, shares, avg_price, created_at) VALUES (?,?,?,?,?,?)
               ON CONFLICT(user_id, code) DO UPDATE SET shares=excluded.shares, avg_price=excluded.avg_price""",
            (user_id, code, name, total_shares, total_avg, time.time()),
        )


def remove(user_id: int, code: str):
    with _conn() as c:
        c.execute("DELETE FROM portfolio WHERE user_id=? AND code=?", (user_id, code))


def list_for_user(user_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT code, name, shares, avg_price, snapshot_score, snapshot_date "
            "FROM portfolio WHERE user_id=? ORDER BY created_at",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _update_snapshots(user_id: int, updates: list[tuple]):
    """updates: [(code, score, date_str)]. 오늘 처음 조회한 종목만 (compute()에서) 전달됨."""
    if not updates:
        return
    with _conn() as c:
        for code, score, date_str in updates:
            c.execute(
                "UPDATE portfolio SET snapshot_score=?, snapshot_date=? WHERE user_id=? AND code=?",
                (score, date_str, user_id, code),
            )


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


# ---------------------------------------------------------------- 상관관계 · 위험기여도
def _return_series(dates, price_by_date):
    vals = [price_by_date[d] for d in dates]
    return [(vals[i] - vals[i - 1]) / vals[i - 1] for i in range(1, len(vals)) if vals[i - 1]]


def _pearson(a, b):
    n = min(len(a), len(b))
    if n < 20:
        return None
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    return round(cov / (va * vb) ** 0.5, 2)


def _correlation_and_risk(items):
    """items: price_by_date를 아직 갖고 있는 상태의 items 리스트(가중치 계산 이후).
    공통 거래일 수익률로 상관계수 행렬과, 각 종목이 포트폴리오 전체 변동성에서
    차지하는 비중(위험기여도, 합=100%)을 계산한다. 종목이 1개뿐이거나 공통 거래일이
    30일 미만이면 계산하지 않고 솔직히 None을 반환한다(억지로 안 채움)."""
    n = len(items)
    if n < 2:
        return None, None
    date_sets = [set(it["price_by_date"].keys()) for it in items]
    common = sorted(set.intersection(*date_sets))[-252:]
    if len(common) < 30:
        return None, None

    returns = [_return_series(common, it["price_by_date"]) for it in items]
    corr = [[1.0 if i == j else None for j in range(n)] for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            c = _pearson(returns[i], returns[j])
            corr[i][j] = corr[j][i] = c

    stdevs = [statistics.pstdev(r) if len(r) >= 20 else None for r in returns]
    if any(s is None for s in stdevs) or any(any(row[j] is None for row in corr) for j in range(n)):
        return corr, None
    cov = [[corr[i][j] * stdevs[i] * stdevs[j] for j in range(n)] for i in range(n)]
    w = [it["weight"] / 100 for it in items]
    Sw = [sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]
    port_var = sum(w[i] * Sw[i] for i in range(n))
    if port_var <= 0:
        return corr, None
    contrib = {items[i]["code"]: round(w[i] * Sw[i] / port_var * 100, 1) for i in range(n)}
    return corr, contrib


def _theme_exposure(items):
    """themes.py에 큐레이션된 테마(국내+미국 모두 매칭)에 보유 비중을 합산해 "실질 노출"을
    계산한다. 신규 데이터·네트워크 호출 없음."""
    exposure = {}
    for name, codes in themes.THEMES.items():
        theme_codes = {code for _market, code in codes}
        w = round(sum(it["weight"] for it in items if it["code"] in theme_codes), 1)
        if w > 0:
            exposure[name] = w
    return dict(sorted(exposure.items(), key=lambda kv: -kv[1]))


def _risk_flags(items, sector_weight, corr, contrib):
    """"좋은 종목"이 아니라 "위험한 조합"을 잡아낸다 — 종목 쏠림/업종 쏠림/상관관계 과다.
    상관관계 클러스터는 상관계수 0.7 이상인 종목들을 묶어(union-find) 합산 비중이
    20% 이상이면 "사실상 같은 베팅"으로 플래그한다."""
    flags = []
    if items:
        top = items[0]
        if top["weight"] >= 30:
            c = contrib.get(top["code"]) if contrib else None
            extra = f" → 포트폴리오 변동성의 {c:.0f}%를 차지" if c is not None else ""
            flags.append({"type": "종목 쏠림", "detail": f"{top['name']} {top['weight']:.0f}%{extra}"})
    if sector_weight:
        top_sector, sw = next(iter(sector_weight.items()))
        if sw >= 50:
            flags.append({"type": "업종 쏠림", "detail": f"{top_sector} {sw:.0f}%"})

    if corr:
        n = len(items)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(n):
            for j in range(i + 1, n):
                if corr[i][j] is not None and corr[i][j] >= 0.7:
                    union(i, j)
        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)
        for members in groups.values():
            if len(members) < 2:
                continue
            w = round(sum(items[i]["weight"] for i in members), 1)
            if w >= 20:
                names = " + ".join(items[i]["name"] for i in members)
                flags.append({
                    "type": "상관관계 과다",
                    "detail": f"{names} → 종목은 {len(members)}개지만 상관계수가 높아 "
                              f"사실상 같은 베팅입니다 (합산 비중 {w:.0f}%)",
                })
    return flags


def _today_actions(items):
    """"그래서 오늘 뭘 하지?"에 답하는 카드 목록. 목표 비중은 종목별 맞춤 목표가
    없으므로 "균등분산 기준"(100%/종목수)을 쓴다 — 정밀한 자산배분 조언이 아니라
    쏠림을 알아채기 위한 참고선임을 문구에 명시한다."""
    n = len(items)
    if not n:
        return []
    equal_w = 100 / n
    cards = []
    for it in items:
        if it["weight"] >= max(equal_w * 1.5, 25):
            cards.append({
                "level": "red", "code": it["code"], "name": it["name"], "title": "비중 과다",
                "detail": f"목표(균등분산 기준) {equal_w:.0f}% → 현재 {it['weight']:.0f}%",
                "action": f"{it['name']} {it['weight'] - equal_w:.0f}%p 비중 축소 검토",
            })
        if it.get("score_diff") is not None and it["score_diff"] <= -8:
            cards.append({
                "level": "yellow", "code": it["code"], "name": it["name"], "title": "매수 타이밍 악화",
                "detail": f"종합점수 {it['prev_score']:.0f} → {it['score']:.0f}",
                "action": f"{it['name']} 보유 비중 재검토 필요",
            })
        if it.get("buy_discount_pct") is not None and it["buy_discount_pct"] <= -5:
            cards.append({
                "level": "green", "code": it["code"], "name": it["name"], "title": "추가매수 기회",
                "detail": f"적정매수가 대비 {it['buy_discount_pct']:.0f}%",
                "action": f"{it['name']} 추가매수 검토",
            })
        if it.get("sell_reasons"):
            cards.append({
                "level": "red", "code": it["code"], "name": it["name"], "title": "매도 신호",
                "detail": " · ".join(it["sell_reasons"]),
                "action": f"{it['name']} 일부 차익실현 고려",
            })
    order = {"red": 0, "yellow": 1, "green": 2}
    cards.sort(key=lambda c: order[c["level"]])
    return cards


# ---------------------------------------------------------------- AI 리밸런싱
_TIER_WEIGHT = {"buy": 1.5, "accumulate": 1.2, "hold": 1.0, "reduce": 0.6, "sell": 0.3}
_MAX_STOCK_WEIGHT = 30.0


def _recommend_weights(items):
    """종목별 AI판단(ai_verdict.tier)에 배점을 매겨 권장 비중을 만든다. 정밀한 포트폴리오
    최적화가 아니라 "확신도가 높은 종목에 더 담되 한 종목에 쏠리지 않게"라는 단순하고
    설명 가능한 원칙만 적용한다.

    ⚠️ 종목 수가 적으면 고정 30% 상한이 수학적으로 불가능해진다(예: 3종목 중 2개가
    상한(30%)에 걸리면 나머지 1종목이 무조건 40%를 떠안는데, 그 1종목이 하필
    '매도' 등급이면 매도 등급 종목이 가장 높은 권장비중을 받는 모순이 생긴다 —
    실제로 이 계산으로 잡아낸 버그). 상한을 `100%/종목수`에 여유를 둔 값과 30% 중
    큰 쪽으로 동적으로 완화해 이 모순을 막는다. 재분배도 1회가 아니라 반복해
    (재분배 후 새로 상한을 넘는 경우까지) 수렴시킨다.
    """
    if not items:
        return {}
    n = len(items)
    max_weight = max(_MAX_STOCK_WEIGHT, 100.0 / n * 1.4)
    mult = {it["code"]: _TIER_WEIGHT.get((it.get("ai_verdict") or {}).get("tier"), 1.0) for it in items}
    total = sum(mult.values())
    if total <= 0:
        return {it["code"]: round(100 / n, 1) for it in items}
    target = {code: v / total * 100 for code, v in mult.items()}

    for _ in range(4):
        over = {c: w for c, w in target.items() if w > max_weight}
        if not over:
            break
        excess = sum(w - max_weight for w in over.values())
        for c in over:
            target[c] = max_weight
        under_codes = [c for c in target if c not in over]
        under_total = sum(target[c] for c in under_codes)
        if under_total <= 0:
            break
        for c in under_codes:
            target[c] += excess * (target[c] / under_total)

    return {c: round(w, 1) for c, w in target.items()}


def _rebalance_note(it):
    """현재비중 vs 권장비중 차이를 '왜'까지 담아 한 줄로 설명한다."""
    tw = it.get("target_weight")
    if tw is None:
        return None
    diff = round(tw - it["weight"], 1)
    tier_label = (it.get("ai_verdict") or {}).get("label") or "보통"
    if abs(diff) < 3:
        return f"현재 비중이 AI판단({tier_label})에 대체로 부합합니다."
    if diff > 0:
        return f"AI판단이 '{tier_label}'이라 비중 확대 여지가 있습니다 ({it['weight']:.0f}%→{tw:.0f}%)."
    return f"AI판단이 '{tier_label}'이거나 종목 집중도가 높아 비중 축소를 고려해볼 만합니다 ({it['weight']:.0f}%→{tw:.0f}%)."


# ---------------------------------------------------------------- 종합 계산
def compute(user_id: int, holding_rows: list[dict], analyze_fn) -> dict:
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
            if not d.get("price"):
                excluded.append({"code": row["code"], "name": row["name"], "reason": "시세 조회 실패"})
                continue
            results.append((row, d))

    # 미국 보유분이 하나라도 있을 때만 환율을 조회한다(순수 국내 포트폴리오는 네트워크 호출 추가 없음).
    fx_rate = None
    if any(d.get("nation") == "US" for _row, d in results):
        fx_rate = naver.usd_krw_rate()

    kept = []
    for row, d in results:
        if d.get("nation") == "US" and fx_rate is None:
            excluded.append({"code": row["code"], "name": row["name"], "reason": "환율 조회 실패(다음 새로고침 때 재시도됩니다)"})
            continue
        kept.append((row, d))
    results = kept

    if not results:
        return {"available": False, "reason": "계산 가능한 보유 종목이 없습니다.", "excluded": excluded}

    today_str = datetime.now(_KST).date().isoformat()
    snapshot_updates = []
    items = []
    total_value = 0.0
    for row, d in results:
        is_us = d.get("nation") == "US"
        currency = "USD" if is_us else "KRW"
        # price_native: 종목 통화 그대로(달러/원) — 적정매수가·목표가·RSI 등 analyze_fn()이
        # 이미 계산해둔 판단 신호와 반드시 이 값으로 비교해야 한다(둘 다 네이티브 통화).
        # price: 원화 환산가 — 평가금액·비중 등 "합산"에만 쓴다.
        price_native = d["price"]
        price = price_native * fx_rate if is_us else price_native
        value = row["shares"] * price
        total_value += value
        avg_price_native = row["avg_price"]
        cost = row["shares"] * avg_price_native * (fx_rate if is_us else 1) if avg_price_native else None
        score = d["total"]["total_score"]

        snap_score, snap_date = row.get("snapshot_score"), row.get("snapshot_date")
        if snap_date != today_str:
            score_diff = round(score - snap_score, 1) if snap_score is not None else None
            prev_score = snap_score
            snapshot_updates.append((row["code"], score, today_str))
        else:
            score_diff, prev_score = None, None

        fair_buy = (d.get("targets") or {}).get("fair_buy") or {}
        base_price = (fair_buy.get("base") or {}).get("price")
        buy_discount_pct = round((price_native - base_price) / base_price * 100, 1) if base_price else None

        # 매도 신호 후보: 목표가 도달 / 기술적 과열(RSI) / 외국인 순매도 전환.
        # 단일 신호는 오탐이 많아(anomaly.py와 동일 원칙) 2개 이상 겹칠 때만 신호로 인정한다.
        # (미국은 naver.trend()가 데이터를 안 줘서 flows가 항상 비어 외국인 신호는 자동 제외됨.)
        tech = d.get("technical") or {}
        rsi = tech.get("rsi") if tech.get("available") else None
        target_price = (d.get("targets") or {}).get("consensus")
        flows5 = [f.get("foreigner") for f in (d.get("flows") or [])[:5] if f.get("foreigner") is not None]
        foreign_sell = len(flows5) >= 3 and all(f < 0 for f in flows5)
        sell_candidates = []
        if target_price and price_native >= target_price:
            target_disp = f"${target_price:,.2f}" if is_us else f"{target_price:,.0f}원"
            sell_candidates.append(f"목표가({target_disp}) 도달")
        if rsi is not None and rsi >= 70:
            sell_candidates.append(f"기술적 과열(RSI {rsi:.0f})")
        if foreign_sell:
            sell_candidates.append("외국인 순매도 전환")
        sell_reasons = sell_candidates if len(sell_candidates) >= 2 else []

        # 변동성·상관관계 계산용 시계열도 원화 환산(현재 환율을 과거에 균일 적용하는 근사치).
        # 수익률(%)·상관계수는 스케일 불변이라 이 근사가 계산 자체를 왜곡하진 않는다 — 실제
        # 과거 환율 변동만 반영이 안 될 뿐(환율 이력 소스가 없어 여기까지는 범위 밖).
        fx_mult = fx_rate if is_us else 1
        items.append({
            "code": row["code"], "name": row["name"], "shares": row["shares"],
            "currency": currency,
            "price": price, "price_native": price_native if is_us else None,
            "fx_rate": fx_rate if is_us else None,
            "value": value,
            "avg_price": avg_price_native, "cost": cost,
            "pnl": round(value - cost) if cost is not None else None,
            "pnl_pct": round((value - cost) / cost * 100, 1) if cost else None,
            "score": score,
            "score_diff": score_diff, "prev_score": prev_score,
            "buy_discount_pct": buy_discount_pct,
            "sell_reasons": sell_reasons,
            "ai_verdict": d.get("ai_verdict"),
            "change": d.get("change", 0) * fx_mult if d.get("change") is not None else None,
            "val_score": (d.get("valuation") or {}).get("score"),
            "upside": (d.get("targets") or {}).get("consensus_upside"),
            "sector": _SECTOR_MAP.get(row["code"], "미분류"),
            "price_by_date": {c["date"]: c["close"] * fx_mult for c in (d.get("candles") or [])},
        })

    if total_value <= 0:
        return {"available": False, "reason": "평가금액을 계산할 수 없습니다.", "excluded": excluded}

    for it in items:
        it["weight"] = round(it["value"] / total_value * 100, 1)
    items.sort(key=lambda x: -x["weight"])

    target_weights = _recommend_weights(items)
    for it in items:
        it["target_weight"] = target_weights.get(it["code"])
        it["rebalance_note"] = _rebalance_note(it)

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

    corr, contrib = _correlation_and_risk(items)
    if contrib:
        for it in items:
            it["risk_contrib_pct"] = contrib.get(it["code"])
    theme_exposure = _theme_exposure(items)
    risk_flags = _risk_flags(items, sector_weight, corr, contrib)
    today_actions = _today_actions(items)
    corr_table = {"labels": [it["name"] for it in items], "matrix": corr} if corr else None

    _update_snapshots(user_id, snapshot_updates)

    for it in items:
        del it["price_by_date"]

    priced = [it for it in items if it["cost"] is not None]
    total_cost = sum(it["cost"] for it in priced) or None
    total_pnl = (sum(it["value"] for it in priced) - total_cost) if total_cost else None
    total_pnl_pct = round(total_pnl / total_cost * 100, 1) if total_cost else None

    changed = [it for it in items if it.get("change") is not None]
    today_pnl = round(sum(it["shares"] * it["change"] for it in changed)) if changed else None
    prev_value = total_value - today_pnl if today_pnl is not None else None
    today_pnl_pct = round(today_pnl / prev_value * 100, 2) if prev_value else None

    pf_score = _wavg("score")
    grade, grade_desc = "F", "위험"
    if pf_score is not None:
        for th, g, desc in analysis.GRADE_TABLE:
            if pf_score >= th:
                grade, grade_desc = g, desc
                break

    return {
        "available": True,
        "total_value": round(total_value),
        "total_cost": round(total_cost) if total_cost else None,
        "total_pnl": round(total_pnl) if total_pnl is not None else None,
        "total_pnl_pct": total_pnl_pct,
        "today_pnl": today_pnl,
        "today_pnl_pct": today_pnl_pct,
        "items": items,
        "sector_weight": sector_weight,
        "score": pf_score,
        "grade": grade,
        "grade_desc": grade_desc,
        "valuation_score": _wavg("val_score"),
        "expected_return": _wavg("upside"),
        "volatility": vol,
        "max_drawdown": mdd,
        "warnings": warnings,
        "excluded": excluded,
        "today_actions": today_actions,
        "risk_flags": risk_flags,
        "theme_exposure": theme_exposure,
        "correlation": corr_table,
    }
