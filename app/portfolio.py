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

from app import analysis, backtest, naver, ranking, themes

_KST = ZoneInfo("Asia/Seoul")

_db_path = None
_SECTOR_MAP = {code: sector for code, _name, sector in ranking.UNIVERSE + ranking.US_UNIVERSE}

# 2026-09-18 속도 진단 — compute()가 보유종목마다 analyze_fn을 매 GET /api/portfolio
# 요청마다 처음부터 다시 돌려(실측 7종목 17초) "종목 추가"가 느리게 느껴졌었다.
# 여기 있던 종목별 60초 캐시는 2026-09-19에 main.api_analyze() 자체로 옮겼다 —
# 워치·이벤트알림 등 analyze_fn을 부르는 다른 곳도 다 같이 득을 보게 하려는
# 목적(공유종목 기준)이라, 여기서 또 캐시하면 그냥 중복이라 뺐다.


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
        if "snapshot_verdict_tier" not in cols:
            # 결함 리포트 Tier1 1-2 — score_diff는 있었지만 "판단(AI등급) 자체가 바뀐
            # 종목"은 추적하지 않았다. watch.py의 added_verdict_tier와 같은 목적으로,
            # 점수 스냅샷과 같은 타이밍(하루 1회, snapshot_date 갱신 시)에 같이 저장한다.
            c.execute("ALTER TABLE portfolio ADD COLUMN snapshot_verdict_tier TEXT")
        if "avg_fx_rate" not in cols:
            c.execute("ALTER TABLE portfolio ADD COLUMN avg_fx_rate REAL")
        # 현금 — 결함 리포트 2026-09-18 10장: 현금 항목이 없어 보유종목 비중이 실제보다
        # 부풀려지고("100% 다 주식") "얼마를 더 사라"는 리밸런싱 제안의 재원 근거가 없었다.
        # 종목 테이블과 분리한 별도 테이블(주당수량·평균단가 개념이 없는 단순 금액이라
        # portfolio 테이블 스키마에 억지로 끼워맞추지 않는다).
        c.execute("""CREATE TABLE IF NOT EXISTS portfolio_cash (
            user_id INTEGER PRIMARY KEY,
            amount REAL NOT NULL,
            updated_at REAL NOT NULL
        )""")
        # 일별 평가금액 스냅샷 — 결함 리포트 Tier1 1-3: "내 자산이 한 달 전보다 늘었나"를
        # 볼 수 없었다. app/backtest.py가 이미 랭킹 전체를 매일 스냅샷해 등급별 성과를
        # 추적하는 것과 같은 패턴을 포트폴리오 단위로 적용 — compute()가 하루 첫 호출 때만
        # (idempotent) 기록한다. 사용자가 그날 한 번도 안 들어와도 portfolio_alert.py의
        # 30분 주기 백그라운드 체크가 모든 보유자를 훑으면서 자동으로 기록해준다.
        c.execute("""CREATE TABLE IF NOT EXISTS portfolio_snapshot (
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            total_value REAL NOT NULL,
            cash REAL NOT NULL DEFAULT 0,
            kospi REAL,
            spy REAL,
            PRIMARY KEY (user_id, date)
        )""")


def _conn():
    c = sqlite3.connect(_db_path, timeout=10)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------- 보유종목 CRUD
def upsert(user_id: int, code: str, name: str, shares: float, avg_price: float | None = None,
           avg_fx_rate: float | None = None):
    """이미 보유 중인 종목을 다시 담으면 추가 매수로 간주해 수량을 더한다
    (버튼이 "추가"인데 값을 덮어쓰면 기존 보유분이 사라진 것처럼 보이는 문제 방지).
    평균단가도 함께 입력되면 (기존수량*기존단가 + 신규수량*신규단가) 가중평균으로 갱신한다.
    한쪽에만 단가가 있으면(과거에 단가 없이 담았던 경우 등) 정확한 가중평균을 낼 수 없으므로
    새로 입력된 단가를 그대로 쓴다 — 두 값 다 없으면 단가 없이 수량만 누적한다.

    avg_fx_rate(미국 종목 매입 시점 원/달러 환율)도 같은 방식으로 갱신하되, 가중치는
    "달러 매입금액"(shares*avg_price)이다 — 환율 자체를 주수로 평균내면 매입 규모가
    다른 두 차례 매수를 동등하게 취급하게 되어 틀린 평균이 나온다."""
    if shares <= 0:
        raise ValueError("수량은 0보다 커야 합니다.")
    if avg_price is not None and avg_price <= 0:
        raise ValueError("평균단가는 0보다 커야 합니다.")
    if avg_fx_rate is not None and avg_fx_rate <= 0:
        raise ValueError("매입 시점 환율은 0보다 커야 합니다.")
    with _conn() as c:
        row = c.execute(
            "SELECT shares, avg_price, avg_fx_rate FROM portfolio WHERE user_id=? AND code=?", (user_id, code)
        ).fetchone()
        if row:
            old_shares, old_avg, old_fx = row["shares"], row["avg_price"], row["avg_fx_rate"]
            total_shares = old_shares + shares
            if avg_price is not None and old_avg is not None:
                total_avg = (old_shares * old_avg + shares * avg_price) / total_shares
            elif avg_price is not None:
                total_avg = avg_price
            else:
                total_avg = old_avg
            if avg_fx_rate is not None and old_fx is not None and old_avg is not None and avg_price is not None:
                old_cost = old_shares * old_avg
                new_cost = shares * avg_price
                total_fx = (old_cost * old_fx + new_cost * avg_fx_rate) / (old_cost + new_cost) if (old_cost + new_cost) else avg_fx_rate
            elif avg_fx_rate is not None:
                total_fx = avg_fx_rate
            else:
                total_fx = old_fx
        else:
            total_shares, total_avg, total_fx = shares, avg_price, avg_fx_rate
        c.execute(
            """INSERT INTO portfolio (user_id, code, name, shares, avg_price, avg_fx_rate, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id, code) DO UPDATE SET shares=excluded.shares, avg_price=excluded.avg_price,
                 avg_fx_rate=excluded.avg_fx_rate""",
            (user_id, code, name, total_shares, total_avg, total_fx, time.time()),
        )


def set_holding(user_id: int, code: str, name: str, shares: float, avg_price: float | None = None,
                 avg_fx_rate: float | None = None):
    """upsert()와 달리 기존 수량에 더하지 않고 그대로 덮어쓴다 — 잘못 입력한 값을
    수정하는 용도(PUT /api/portfolio/{code})."""
    if shares <= 0:
        raise ValueError("수량은 0보다 커야 합니다.")
    if avg_price is not None and avg_price <= 0:
        raise ValueError("평균단가는 0보다 커야 합니다.")
    if avg_fx_rate is not None and avg_fx_rate <= 0:
        raise ValueError("매입 시점 환율은 0보다 커야 합니다.")
    with _conn() as c:
        c.execute(
            """INSERT INTO portfolio (user_id, code, name, shares, avg_price, avg_fx_rate, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(user_id, code) DO UPDATE SET shares=excluded.shares, avg_price=excluded.avg_price,
                 avg_fx_rate=excluded.avg_fx_rate""",
            (user_id, code, name, shares, avg_price, avg_fx_rate, time.time()),
        )


def remove(user_id: int, code: str):
    with _conn() as c:
        c.execute("DELETE FROM portfolio WHERE user_id=? AND code=?", (user_id, code))


# ---------------------------------------------------------------- 평가금액 추이
def _bench_prices():
    """코스피·SPY 현재가. app/backtest.py의 _bench_price()와 같은 목적, 같은 방식 —
    실패하면 None(그 지수 비교는 건너뛰고 포트폴리오 값만 기록)."""
    kospi = spy = None
    try:
        c = naver.index_candles("KOSPI", 3)
        kospi = c[-1]["close"] if c else None
    except Exception:
        pass
    try:
        c = naver.candles("SPY", 3)
        spy = c[-1]["close"] if c else None
    except Exception:
        pass
    return kospi, spy


def _record_snapshot(user_id: int, total_value: float, cash: float):
    """하루 한 번만 실제로 기록(idempotent, PRIMARY KEY(user_id,date)로 강제) —
    compute()가 매 요청마다 호출해도 상관없다(app/backtest.py의 snapshot()과 동일 패턴)."""
    today_str = datetime.now(_KST).date().isoformat()
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM portfolio_snapshot WHERE user_id=? AND date=?", (user_id, today_str)
        ).fetchone()
        if row:
            return
    kospi, spy = _bench_prices()
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO portfolio_snapshot (user_id, date, total_value, cash, kospi, spy) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, today_str, total_value, cash, kospi, spy),
        )


def get_history(user_id: int) -> list[dict]:
    """평가금액 추이 + 코스피/SPY 동시점 지수 — 프론트가 시작일=100 기준 지수로
    정규화해 "내 포트폴리오 vs 코스피 vs S&P500" 비교선을 그린다(결함 리포트 Tier1 1-3)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT date, total_value, cash, kospi, spy FROM portfolio_snapshot "
            "WHERE user_id=? ORDER BY date", (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_cash(user_id: int) -> float:
    with _conn() as c:
        row = c.execute("SELECT amount FROM portfolio_cash WHERE user_id=?", (user_id,)).fetchone()
    return row["amount"] if row else 0.0


def set_cash(user_id: int, amount: float):
    if amount < 0:
        raise ValueError("현금은 0보다 작을 수 없습니다.")
    with _conn() as c:
        c.execute(
            """INSERT INTO portfolio_cash (user_id, amount, updated_at) VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET amount=excluded.amount, updated_at=excluded.updated_at""",
            (user_id, amount, time.time()),
        )


def list_for_user(user_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT code, name, shares, avg_price, avg_fx_rate, snapshot_score, snapshot_date, "
            "snapshot_verdict_tier FROM portfolio WHERE user_id=? ORDER BY created_at",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _update_snapshots(user_id: int, updates: list[tuple]):
    """updates: [(code, score, verdict_tier, date_str)]. 오늘 처음 조회한 종목만
    (compute()에서) 전달됨."""
    if not updates:
        return
    with _conn() as c:
        for code, score, verdict_tier, date_str in updates:
            c.execute(
                "UPDATE portfolio SET snapshot_score=?, snapshot_verdict_tier=?, snapshot_date=? "
                "WHERE user_id=? AND code=?",
                (score, verdict_tier, date_str, user_id, code),
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
    """"그래서 오늘 뭘 하지?"에 답하는 카드 목록.

    ⚠️ 목표 비중은 반드시 AI 리밸런싱 섹션(_recommend_weights)의 target_weight와
    같은 숫자를 써야 한다. 예전엔 여기서만 "균등분산 기준"(100%/종목수)을 따로 계산해
    같은 페이지 안에서 목표 비중이 두 가지로 제시되는 모순이 있었다(2차 진단리포트
    3-4: "삼성전자 38%p 축소 검토" vs "88%→44%" AI 리밸런싱이 한 화면에 동시 표시).
    compute()에서 이 함수는 target_weight가 이미 채워진 뒤 호출된다."""
    n = len(items)
    if not n:
        return []
    cards = []
    for it in items:
        tw = it.get("target_weight")
        weight_gap = it["weight"] - tw if tw is not None else None
        overweight = weight_gap is not None and weight_gap >= 15
        # 목표비중보다 "그래도 의미 있게" 높은 종목에는 red 정도로 다급하진 않아도
        # green 추가매수 카드는 억제한다 — 아래 참고.
        above_target = (weight_gap is not None and weight_gap > 0
                        and _meaningfully_different(it["weight"], tw))
        if overweight:
            cards.append({
                "level": "red", "code": it["code"], "name": it["name"], "title": "비중 과다",
                "detail": f"AI 권장 비중 {tw:.0f}% → 현재 {it['weight']:.0f}%",
                "action": f"{it['name']} {it['weight'] - tw:.0f}%p 비중 축소 검토",
            })
        if it.get("score_diff") is not None and it["score_diff"] <= -8:
            cards.append({
                "level": "yellow", "code": it["code"], "name": it["name"], "title": "매수 타이밍 악화",
                "detail": f"종합점수 {it['prev_score']:.0f} → {it['score']:.0f}",
                "action": f"{it['name']} 보유 비중 재검토 필요",
            })
        # 4차 진단리포트 6장 — 비중 과다(red)와 추가매수 기회(green)가 같은 종목에
        # 동시에 뜨면 "줄여라"·"더 사라"는 정반대 지시가 나란히 표시돼 사용자가 뭘
        # 해야 할지 알 수 없다. 목표비중보다 이미 above_target인(=_rebalance_note()가
        # "비중 축소를 고려해볼 만합니다"라고 말하는) 종목엔 추가매수 카드를 아예
        # 숨긴다 — red 문턱(15%p)이 아니라 리밸런싱 노트와 같은 문턱을 써야, 그 사이
        # (3~15%p) 구간에서 "줄여라"·"더 사라"가 같은 화면에 동시에 뜨지 않는다
        # (가격은 매력적이어도 지금은 분산이 우선이라는 판단).
        if not above_target and it.get("buy_discount_pct") is not None and it["buy_discount_pct"] <= -5:
            cards.append({
                "level": "green", "code": it["code"], "name": it["name"], "title": "추가매수 기회",
                "detail": f"매수 적정가 대비 {it['buy_discount_pct']:.0f}%",
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
_MAX_STOCK_WEIGHT = 30.0
# 종목별 점수 상대비교만으로는 "업종 전체가 좋다"고 나오면 업종 쏠림이 전혀 줄지 않는다
# (아래 _recommend_weights() 2026-09-19 개정 참고). _risk_flags()가 업종 쏠림을 경고하는
# 문턱(50%)보다 낮게 잡아 "경고했는데 리밸런싱은 그대로"인 모순을 막는다.
_SECTOR_MAX_WEIGHT = 45.0
# 상관계수 0.7 이상으로 묶인 종목군(=_risk_flags()가 "사실상 같은 베팅"으로 플래그하는
# 기준과 동일)의 합산 목표비중 상한. _risk_flags()의 플래그 문턱(20%)보다는 높게 잡아
# "어느 정도 상관된 종목을 같이 담는 것"까지 막지는 않되, 실제로 의미 있게 줄어들게 한다.
_CLUSTER_MAX_WEIGHT = 30.0
# 종합점수(0~100, 이미 기본적/기술적/감성/밸류에이션을 다 반영한 값)가 포트폴리오
# 평균보다 이만큼(점) 높으면, 균등분산 몫(100/n)의 1/_SCORE_SENSITIVITY만큼을
# 현재 비중에 더해준다. 아래 _recommend_weights() 참고.
_SCORE_SENSITIVITY = 4.0
# 목표비중보다 이만큼(%p) 이상 높으면 "비중을 줄이는 쪽이 낫다"로 본다.
# _rebalance_note()와 _today_actions()가 반드시 이 값을 같이 써야 한다 — 예전엔
# _today_actions()의 "추가매수 카드 억제" 조건이 red 카드와 같은 15%p였는데,
# _rebalance_note()는 3%p만 벌어져도 "비중 축소를 고려해볼 만합니다"라고 말해서,
# 목표비중과 3~15%p 벌어진 종목은 같은 화면에서 "줄여라"(리밸런싱 노트)·"더
# 사라"(오늘의 할일 green 카드)가 동시에 뜨는 모순이 생겼다(사용자 실측 제보,
# 2026-09-18: SMCI 보유비중 27% vs 목표 14%로 12.8%p 벌어져 red 15%p엔 안 걸리면서
# green 추가매수 카드가 떴음).
_REBALANCE_MEANINGFUL_DIFF = 3.0


def _meaningfully_different(weight: float, target_weight: float) -> bool:
    """목표비중과 현재비중이 "반올림 오차 수준"을 넘어 실제로 조정할 만해 보이는지.

    ⚠️ 2026-09-19 — _recommend_weights()를 현재비중 anchor 방식으로 바꾼 뒤, 원래
    비중이 작은 종목은 절대 %p 차이만으로는 안 걸리는 사각지대가 생겼다(예: 3.3%→0.8%는
    "70% 넘게 줄이라"는 뚜렷한 신호인데 절대 차이는 2.5%p뿐이라 3%p 문턱에 못 미침).
    절대 %p 차이(큰 비중 종목용)와 상대 변화율(작은 비중 종목용) 중 하나라도 크면
    "의미 있는 차이"로 본다."""
    diff = abs(weight - target_weight)
    if diff >= _REBALANCE_MEANINGFUL_DIFF:
        return True
    return weight > 0 and diff / weight >= 0.3


def _correlation_clusters(items, corr):
    """corr(items와 같은 순서의 상관행렬)에서 상관계수 0.7 이상인 종목들을 union-find로
    묶어 2개 이상인 그룹만 [code, ...] 리스트로 반환한다. _risk_flags()의 "상관관계 과다"
    플래그와 같은 문턱(0.7)을 쓴다 — 여기서 묶이는 종목군이 곧 거기서 "사실상 같은
    베팅"이라고 경고하는 종목군이어야 리밸런싱과 경고가 같은 기준을 말하게 된다."""
    if not corr:
        return []
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
    return [[items[i]["code"] for i in members] for members in groups.values() if len(members) >= 2]


def _clamp_stock_cap(target, max_weight):
    """개별 종목 상한(max_weight)을 넘는 초과분을 나머지 종목에 현재 비중 비례로
    재분배한다. 그룹 상한 적용 뒤 다시 개별 상한을 넘는 종목이 생길 수 있어(예: 업종
    상한으로 줄인 만큼을 업종 밖 1종목이 떠안으면 그 종목이 30%를 넘을 수 있음) 그룹
    상한 루프 안에서 반복 호출된다."""
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


def _clamp_group_caps(target, groups, cap, max_weight, capped=None):
    """groups(각각 [code,...]인 그룹 리스트)의 합산 비중이 cap을 넘으면 그룹 내부는
    현재 비율을 유지한 채 비례 축소하고, 초과분은 그룹 밖 종목에 현재 목표비중 비례로
    돌려준다. 재분배가 개별 종목 상한을 다시 깰 수 있어 매 반복마다 _clamp_stock_cap을
    같이 돌려 수렴시킨다. capped(집합)가 주어지면 실제로 축소당한 종목 코드를 기록해
    _rebalance_note()가 "점수가 낮아서"가 아니라 "업종/상관관계 상한 때문"이라고
    정확히 설명할 수 있게 한다(점수 자체는 평균 이상이어도 업종 상한에 걸려 줄어들
    수 있음 — 그런 종목에 "점수가 낮다"는 설명을 붙이면 틀린 설명이 된다)."""
    for _ in range(4):
        changed = False
        for codes in groups:
            codes = [c for c in codes if c in target]
            group_total = sum(target[c] for c in codes)
            if group_total <= cap or group_total <= 0:
                continue
            scale = cap / group_total
            excess = group_total - cap
            for c in codes:
                target[c] *= scale
                if capped is not None:
                    capped.add(c)
            other_codes = [c for c in target if c not in codes]
            other_total = sum(target[c] for c in other_codes)
            if other_total > 0:
                for c in other_codes:
                    target[c] += excess * (target[c] / other_total)
            changed = True
        _clamp_stock_cap(target, max_weight)
        if not changed:
            break


def _recommend_weights(items, corr=None):
    """현재 비중을 기준(anchor)으로 삼아, 종합점수가 포트폴리오 평균보다 높은 종목은
    비중을 늘리고 낮은 종목은 줄이는 방향으로 "조정"한다 — 처음부터 다시 나눠 담는
    게 아니다.

    ⚠️ 2026-09-19 — 예전엔 현재 비중을 아예 무시하고 AI판단 티어(8단계) 배점만으로
    전량 재분배했다. 문제는 보유종목 대부분이 buy/watch_buy처럼 비슷한 티어에
    몰리기 쉬워서(이 프로젝트 점수화가 극단값을 잘 안 주는 편), 실제로는 44%·27%·
    11%처럼 제각각이던 비중이 죄다 14% 안팎으로 수렴해버렸다(사용자 실측 제보:
    "25%→14%로 다 몰아넣으면 이게 숫자놀이지, 종목별로 종합적으로 분석해서 좋은
    종목은 비중이 높아도 놔두고 나쁜 종목만 낮춰야 리밸런싱이지"). 티어는 8단계뿐이라
    미묘한 우열을 못 담지만, 종합점수(0~100)는 이미 연속값이라 종목 간 실제 품질
    차이를 그대로 보여준다 — 그 점수를 현재 비중에 대한 "가감"으로만 쓰면, 이미
    비중이 크더라도 평균 이상으로 좋은 종목은 그대로 크게 두고, 평균에 못 미치는
    종목만 실제로 깎인다.

    ⚠️ 종목 수가 적으면 고정 30% 상한이 수학적으로 불가능해진다(예: 3종목 중 2개가
    상한(30%)에 걸리면 나머지 1종목이 무조건 40%를 떠안는데, 그 1종목이 하필
    '매도' 등급이면 매도 등급 종목이 가장 높은 권장비중을 받는 모순이 생긴다 —
    실제로 이 계산으로 잡아낸 버그). 상한을 `100%/종목수`에 여유를 둔 값과 30% 중
    큰 쪽으로 동적으로 완화해 이 모순을 막는다. 재분배도 1회가 아니라 반복해
    (재분배 후 새로 상한을 넘는 경우까지) 수렴시킨다.

    ⚠️ 2026-09-18 — 개별 종목 상한만으로는 "업종 전체가 다 buy 등급"인 경우를 못
    잡는다. 종목별로는 하나도 30%를 안 넘어도 업종 합산은 그대로 70%대에 머물 수
    있고, 상관계수 0.85로 묶인 두 종목처럼 "사실상 같은 베팅"인 조합이 오히려 둘 다
    늘어나는 방향으로 나올 수도 있다(포트폴리오 결함 리포트 2026-09-18 2장: "반도체
    73% 경고해놓고 리밸런싱해도 69.2%", "상관관계 과다 경고 종목인데 리밸런싱은 비중
    확대 제안"). items의 sector로 업종 그룹, corr로 상관관계 클러스터를 구해 각각
    합산 비중 상한을 추가로 건다 — 경고(risk_flags)와 처방(리밸런싱)이 같은 기준
    (섹터·상관관계)을 보게 되어 서로 반대 방향을 가리키는 일이 없어진다.

    반환값은 (target_weights, capped_reason) 튜플이다. capped_reason[code]는 그 종목의
    비중이 업종/상관관계 상한 때문에 깎였으면 "sector"/"cluster"(둘 다 걸렸으면
    "cluster"가 우선 — 더 좁고 구체적인 사유), 순수 점수 비교만으로 정해졌으면 없음
    (키 자체가 없음). _rebalance_note()가 이걸로 "점수가 낮아서 줄이라는 건지 업종/
    상관관계 상한 때문인지"를 구분해 설명한다 — 안 그러면 점수는 평균 이상인데 업종
    상한에 걸려 줄어든 종목에 "점수가 낮다"는 틀린 설명이 붙는다.
    """
    if not items:
        return {}, {}
    n = len(items)
    max_weight = max(_MAX_STOCK_WEIGHT, 100.0 / n * 1.4)
    equal_share = 100.0 / n

    scored = [it["score"] for it in items if it.get("score") is not None]
    avg_score = sum(scored) / len(scored) if scored else None

    raw = {}
    for it in items:
        score = it.get("score")
        if score is None or avg_score is None:
            # 점수를 못 구한 종목(일시적 조회 실패 등)은 가감 없이 현재 비중을 그대로 둔다.
            raw[it["code"]] = it["weight"]
            continue
        delta = equal_share * (score - avg_score) / 10.0 / _SCORE_SENSITIVITY
        raw[it["code"]] = max(0.0, it["weight"] + delta)

    total = sum(raw.values())
    if total <= 0:
        return {it["code"]: round(100 / n, 1) for it in items}, {}
    target = {code: v / total * 100 for code, v in raw.items()}

    _clamp_stock_cap(target, max_weight)

    groups = []
    by_sector: dict = {}
    for it in items:
        by_sector.setdefault(it["sector"], []).append(it["code"])
    # 업종 자체가 "1종목뿐"이면 상한을 걸어도 재분배할 같은 업종 내 다른 종목이 없어
    # 무의미하다(오히려 그 1종목만 부당하게 깎일 수 있음) — 2개 이상인 업종만 그룹화.
    groups.extend(codes for codes in by_sector.values() if len(codes) >= 2)
    sector_capped: set = set()
    if groups:
        _clamp_group_caps(target, groups, _SECTOR_MAX_WEIGHT, max_weight, capped=sector_capped)

    clusters = _correlation_clusters(items, corr)
    cluster_capped: set = set()
    if clusters:
        _clamp_group_caps(target, clusters, _CLUSTER_MAX_WEIGHT, max_weight, capped=cluster_capped)

    capped_reason = {c: "sector" for c in sector_capped}
    capped_reason.update({c: "cluster" for c in cluster_capped})   # 상관관계 쪽이 더 구체적인 사유라 우선
    return {c: round(w, 1) for c, w in target.items()}, capped_reason


def _rebalance_note(it, n_holdings, capped_reason=None):
    """현재비중 vs 권장비중 차이를 '왜'까지 담아 한 줄로 설명한다.

    ⚠️ 보유종목이 1~2개면 재분배할 다른 종목이 없어 권장비중이 늘 현재비중과 같게
    나온다(1종목이면 무조건 100%). 이걸 그냥 "AI판단에 대체로 부합합니다"라고 하면
    포트폴리오 종합점수는 집중도로 크게 감점하면서 바로 아래 리밸런싱은 "적정"이라고
    말하는 모순이 생긴다(3차 진단리포트 4장: "위에서는 집중도로 25점을 깎고 아래에서는
    그 집중도가 적정하다고 하는 셈"). 종목 수가 부족해 비교 자체가 무의미할 때는
    그렇다고 명시한다."""
    tw = it.get("target_weight")
    if tw is None:
        return None
    if n_holdings <= 2:
        return "보유 종목이 적어 AI 리밸런싱은 종목 간 비중 배분만 제안합니다 — 분산이 부족한지는 위 리스크 감점·경고를 참고하세요."
    diff = round(tw - it["weight"], 1)
    verdict = it.get("ai_verdict") or {}
    tier_label = verdict.get("label") or "보통"
    if not _meaningfully_different(it["weight"], tw):
        return f"현재 비중이 AI판단({tier_label})에 대체로 부합합니다."
    reason = (capped_reason or {}).get(it["code"])
    # ⚠️ 2026-09-18 — 업종/상관관계 상한 때문에 줄어든 종목은 그 종목 자체의 점수가
    # 평균 이상이어도 줄어들 수 있다(업종 전체가 좋아도 업종 쏠림은 그대로 위험이라서).
    # 이런 종목에 "포트폴리오 내 다른 종목보다 종합점수가 낮아서"라고 하면 틀린
    # 설명이 된다 — capped_reason으로 실제 사유(업종/상관관계 상한)를 먼저 확인한다.
    if diff < 0 and reason == "sector":
        sector = it.get("sector", "동일 업종")
        return (f"종목 자체 점수와 무관하게 {sector} 업종 비중이 과도해(업종 상한 초과) "
                f"비중 축소를 고려해볼 만합니다 ({it['weight']:.0f}%→{tw:.0f}%).")
    if diff < 0 and reason == "cluster":
        return (f"종목 자체 점수와 무관하게 포트폴리오 내 다른 종목과 상관관계가 높아 "
                f"(사실상 같은 베팅) 비중 축소를 고려해볼 만합니다 ({it['weight']:.0f}%→{tw:.0f}%).")
    # ⚠️ 2026-09-19 — _recommend_weights()가 현재비중 anchor + 종합점수 비교 방식으로
    # 바뀌면서, 위 업종/상관관계 상한에 안 걸린 "비중 축소" 권고의 실제 이유는 항상
    # "포트폴리오 내 다른 종목 대비 종합점수가 낮다"이지 "이미 비중이 커서"가 아니다
    # (작게 담은 종목도 점수가 낮으면 더 줄이라고 나올 수 있음 — 예전 문구 "비중이
    # 이미 높아"는 그런 경우 틀린 설명이 된다). tier 라벨은 참고로만 병기하고, 실제
    # 근거(상대 점수)를 명시한다.
    if diff > 0:
        return (f"포트폴리오 내 다른 종목보다 종합점수가 높아(AI판단 '{tier_label}') "
                f"비중 확대 여지가 있습니다 ({it['weight']:.0f}%→{tw:.0f}%).")
    return (f"AI판단은 '{tier_label}'이지만 포트폴리오 내 다른 종목보다 종합점수가 낮아, "
            f"비중을 줄이고 점수가 높은 종목 비중을 늘리는 쪽을 고려해볼 만합니다 "
            f"({it['weight']:.0f}%→{tw:.0f}%).")


def _actionable_rebalance(it, total_value: float):
    """"몇 주를 사고팔아야 하는가"로 번역한다 — 결함 리포트 Tier1 1-4: 권장 비중만 %로
    제시하고 실제로 뭘 해야 하는지는 화면 어디에도 없었다("한화오션 3.3%→0.7%"만 보고
    사용자가 직접 20주 중 몇 주를 팔지 계산해야 했음).

    비중 차이(%p) → 원화 금액 → 주수로 변환한다. 미국 종목은 원화 금액을 종목 통화(달러)
    환산 후 price_native로 나눠야 한다(원화 가격으로 나누면 단위가 안 맞아 주수가
    1000배 가까이 틀어진다 — 이 파일 상단 모듈 docstring의 price_native 경고와 같은 함정).
    거래비용은 홈 백테스트(app/backtest.py)가 쓰는 것과 같은 왕복 가정치를 그대로
    재사용해 일관성을 맞춘다(새 가정을 또 만들지 않음)."""
    tw = it.get("target_weight")
    if tw is None or total_value <= 0:
        return None
    diff_pct = tw - it["weight"]
    if abs(diff_pct) < 0.5:   # 반올림 오차 수준이면 "0주" 같은 무의미한 액션을 보여주지 않는다
        return None
    diff_value_krw = diff_pct / 100 * total_value
    if it["currency"] == "USD" and it.get("fx_rate"):
        diff_value_native = diff_value_krw / it["fx_rate"]
        price_native = it.get("price_native")
    else:
        diff_value_native = diff_value_krw
        price_native = it["price"]
    if not price_native:
        return None
    shares = round(diff_value_native / price_native)
    if shares == 0:
        return None
    cost_est = round(abs(diff_value_krw) * backtest.ROUNDTRIP_COST_PCT / 100)
    unit_price = f"${price_native:,.2f}" if it["currency"] == "USD" else f"{price_native:,.0f}원"
    action = "매수" if shares > 0 else "매도"
    return {
        "shares": abs(shares), "direction": action,
        "value_krw": round(abs(diff_value_krw)),
        "cost_est": cost_est,
        "text": f"{it['name']} {abs(shares)}주 {action} (주당 {unit_price} 기준, 약 {won(abs(diff_value_krw))}원, "
                f"거래비용 약 {won(cost_est)}원 가정)",
    }


def won(v):
    """정수 원화 3자리 콤마 포맷 — 이 파일 안에서만 쓰는 간단한 표시용(프론트 fmt()와 별개)."""
    return f"{v:,.0f}"


# ---------------------------------------------------------------- 리스크 감점
def _risk_penalty(items, vol, mdd):
    """포트폴리오 종합점수 = 종목 품질 점수(quality_score, 보유종목 가중평균)만으로는
    "경고를 세 개 띄우면서도 등급은 양호"라는 모순이 생긴다(2차 진단리포트 3-2: 삼성전자
    100% 집중·연변동성 73%·평가손실 -22.7%인데 "B등급·양호"로 표시됨). 집중도·변동성·
    최대낙폭을 감점으로 반영해 quality_score에서 뺀 값을 최종 score로 쓴다.

    ⚠️ 초판(감점 상한 25/20/20, 선형)은 과잉 교정이었다(3차 진단리포트 4장: 품질 68.8점
    · 종목 하나만 보유한 정상적인 케이스가 감점 56.5점을 맞아 F등급·12.3점까지 떨어짐 —
    감점이 품질점수의 82%를 잡아먹음). 두 가지를 고쳤다:
    1. 집중도는 볼록 곡선(지수 1.6)으로 — 3~5종목의 "정상적인" HHI(0.2~0.35) 구간에서는
       완만하고, 진짜 극단적 집중(단일 종목 100%, HHI→1.0)에서만 가파르게 오른다.
    2. 변동성과 최대낙폭은 같은 하락에서 파생되는 상관관계가 매우 높은 지표라 그냥
       더하면 사실상 같은 위험을 두 번 깎는다 — 큰 쪽은 전부, 작은 쪽은 30%만 반영한다.
    """
    penalty = 0.0
    detail = []

    hhi = sum((it["weight"] / 100) ** 2 for it in items)
    hhi_excess = max(0.0, hhi - 0.25)   # 0.25 ≈ 4종목 균등분산 — 이하는 정상 범위로 감점 없음
    if hhi_excess > 0:
        p = min(18.0, (hhi_excess ** 1.6) * 45)
        if p >= 0.5:
            penalty += p
            detail.append(f"집중도(HHI·허핀달지수, 1에 가까울수록 소수 종목 쏠림 {hhi:.2f}) -{p:.0f}점")

    vol_p = min(15.0, (vol - 25) * 0.5) if (vol is not None and vol > 25) else 0.0
    dd_p = min(15.0, (abs(mdd) - 20) * 0.5) if (mdd is not None and mdd < -20) else 0.0
    if vol_p > 0 or dd_p > 0:
        combined = max(vol_p, dd_p) + min(vol_p, dd_p) * 0.3
        penalty += combined
        parts = []
        if vol_p > 0:
            parts.append(f"변동성 연 {vol:.0f}%")
        if dd_p > 0:
            parts.append(f"최대낙폭 {mdd:.0f}%")
        detail.append(f"{'·'.join(parts)}(이중계산 방지 적용) -{combined:.0f}점")

    return round(penalty, 1), detail


# ---------------------------------------------------------------- 종합 계산
def compute(user_id: int, holding_rows: list[dict], analyze_fn, cash: float = 0.0) -> dict:
    """holding_rows: list_for_user() 결과. analyze_fn(code) == main.api_analyze.
    cash: get_cash(user_id) 결과(원화) — 결함 리포트 10장, _recommend_weights.py 위 주석 참고."""
    if not holding_rows:
        return {"available": False, "reason": "담긴 종목이 없습니다."}

    results, excluded = [], []

    def _fetch(row):
        # ⚠️ 여기서 던진 예외를 호출부가 통째로 삼키면 "저장은 됐는데 화면에서 조용히
        # 사라지는" 무음 실패가 된다(2차 진단리포트 3-8, ETF 추가 사례로 실제 발견).
        # 반드시 (row, 결과, 에러사유) 3-튜플로 돌려줘 실패도 excluded에 이유와 함께 남긴다.
        try:
            return row, analyze_fn(row["code"]), None
        except Exception as e:
            raw = str(e) or e.__class__.__name__
            # 종합진단리포트(2026-08-24) 4-14 — URL만 지워도 "404: 종목을 찾을 수 없습니다:
            # 409 Client Error: Conflict for url" 같은 원본 예외 문구(HTTP 상태코드·requests
            # 라이브러리 문구)가 그대로 남아 개발자용 에러가 사용자 화면에 노출됐다.
            # ETF·ETN처럼 애초에 분석을 지원하지 않는 자산인지를 먼저 판별해 그에 맞는
            # 안내문으로 완전히 대체하고, 그 외 오류도 원본 예외 텍스트를 노출하지 않는다.
            if "404" in raw or "찾을 수 없" in raw:
                msg = "미지원 종목(ETF·ETN 등으로 추정) — 현재 이 종목 유형은 분석을 지원하지 않아 평가금액 계산에서 제외됩니다."
            else:
                msg = "일시적인 조회 오류 — 다음 새로고침 때 다시 시도됩니다."
            return row, None, msg

    # ⚠️ 2026-09-22 속도 재지적 — 보유종목마다 analyze_fn()을 병렬로 부르는데, analyze_fn()
    # 자신도 내부에서 또 8개짜리 스레드풀을 쓴다(main.py _analyze_impl). 보유종목 전부가
    # 캐시미스면(포트폴리오는 남들이 잘 안 보는 종목 조합일 때가 많아 실제로 자주 이렇게
    # 됨) 8종목×8하위작업=최대 64개 스레드가 동시에 뜬다 — CPU 스틸타임이 70~80%인
    # 공유 VM에서는 이게 병렬 이득보다 스레드 경합 손해가 커서 오히려 느려진다(실측:
    # 태블릿에서 포트폴리오 로딩 1분+). naver.py에 전역 세마포어(동시 요청 6개 상한)를
    # 추가한 것과 별개로, 여기 바깥쪽 동시성 자체도 낮춰 중첩 폭발의 최댓값을 줄인다.
    with ThreadPoolExecutor(max_workers=min(4, len(holding_rows))) as ex:
        futs = [ex.submit(_fetch, r) for r in holding_rows]
        for fut in as_completed(futs):
            row, d, err = fut.result()
            if err or not d:
                excluded.append({"code": row["code"], "name": row["name"], "reason": err or "분석 실패"})
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
        # ⚠️ 2026-09-18 — 매입원가를 항상 "오늘 환율"로 환산하면 환차손익이 통째로
        # 사라진다(포트폴리오 결함 리포트 2026-09-18 5장: 슈퍼마이크로 255주 실측 사례 —
        # 매입원가를 매입 시점이 아니라 오늘 환율로 계산해서 6.8%(달러 기준 수익률)와
        # 910,915원(오늘 환율 기준 금액)이 서로 다른 기준인데 나란히 표시됨). 매입 시점
        # 환율(avg_fx_rate, 사용자가 종목 추가 시 선택 입력)이 있으면 그걸로 원가를
        # 계산해 "주가 변동에 따른 손익"과 "환율 변동에 따른 손익"을 분리한다. 없으면
        # (과거 방식 그대로) 오늘 환율로 근사 — 분리 표시는 생략하고 근사치임을 프론트에서
        # "(환산)" 표기로 알린다(기존 동작 유지, 신규 회귀 아님).
        avg_fx_rate = row.get("avg_fx_rate") if is_us else None
        cost_fx = avg_fx_rate or (fx_rate if is_us else 1)
        cost = row["shares"] * avg_price_native * cost_fx if avg_price_native else None
        price_pnl = fx_pnl = None
        if is_us and avg_fx_rate and avg_price_native and cost is not None:
            # 총손익 = 가격변동분 + 환율변동분으로 정확히 나눈다(가격변동분을 매입환율
            # 고정으로 먼저 계산하고, 환율변동분은 "나머지"로 정의해 두 값의 합이 항상
            # 총손익과 정확히 일치하게 한다 — 교차항을 어느 한쪽에 몰아넣는 대신 잔차
            # 없이 딱 맞게 만드는 쪽을 택함).
            price_pnl = round((price_native - avg_price_native) * avg_fx_rate * row["shares"])
            fx_pnl = round((value - cost) - price_pnl)
        score = d["total"]["total_score"]
        verdict_tier = (d.get("ai_verdict") or {}).get("tier")

        snap_score, snap_date = row.get("snapshot_score"), row.get("snapshot_date")
        snap_verdict_tier = row.get("snapshot_verdict_tier")
        if snap_date != today_str:
            score_diff = round(score - snap_score, 1) if snap_score is not None else None
            prev_score = snap_score
            # 결함 리포트 Tier1 1-2 — 점수뿐 아니라 "AI판단 등급 자체가 바뀌었는지"도
            # 추적한다(watch.py의 added_verdict_tier와 같은 목적). 처음 담긴 날(스냅샷
            # 없음)은 비교 기준이 없어 오탐 방지를 위해 False.
            verdict_changed = bool(snap_verdict_tier and verdict_tier and verdict_tier != snap_verdict_tier)
            snapshot_updates.append((row["code"], score, verdict_tier, today_str))
        else:
            score_diff, prev_score, verdict_changed = None, None, False

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

        # 손절선 도달 — 결함 리포트 Tier1 1-1(포트폴리오 알림 5종 중 하나). 단일 신호라도
        # 손절은 "겹칠 때만 신호"(위 sell_reasons)와 달리 그 자체로 명확한 리스크 경고라
        # 별도 필드로 둔다. entry.stop_loss는 종목 상세페이지 매수 계획과 같은 값(app/main.py
        # _analyze_impl의 손절가 정합성 보정을 그대로 물려받음).
        stop_loss_native = (tech.get("entry") or {}).get("stop_loss") if tech.get("available") else None
        stop_loss_hit = bool(stop_loss_native and price_native <= stop_loss_native)

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
            "avg_price": avg_price_native, "avg_fx_rate": avg_fx_rate, "cost": cost,
            "pnl": round(value - cost) if cost is not None else None,
            "pnl_pct": round((value - cost) / cost * 100, 1) if cost else None,
            "price_pnl": price_pnl, "fx_pnl": fx_pnl,
            "score": score,
            "score_diff": score_diff, "prev_score": prev_score,
            "buy_discount_pct": buy_discount_pct,
            "sell_reasons": sell_reasons,
            "stop_loss_hit": stop_loss_hit,
            "verdict_changed": verdict_changed, "prev_verdict_tier": snap_verdict_tier,
            "ai_verdict": d.get("ai_verdict"),
            "change": d.get("change", 0) * fx_mult if d.get("change") is not None else None,
            "val_score": (d.get("valuation") or {}).get("score"),
            "upside": (d.get("targets") or {}).get("consensus_upside"),
            # 목표주가 괴리가 커서(analysis.TARGET_UPSIDE_OUTLIER) 상세페이지가 이미
            # 신뢰도를 낮춰둔 종목인지 — 그대로 원화 가중평균에 넣으면 "D등급인데
            # 기대수익률 +70.8%" 같은 모순이 난다(UI/UX 검증보고서 6-7). d.consensus는
            # main.py가 이미 계산해둔 걸 통째로 실어주므로 새 계산 없이 그대로 읽는다.
            "upside_flagged": (d.get("consensus") or {}).get("upside_flagged", False),
            "upside_weight": (d.get("consensus") or {}).get("upside_weight", 1.0),
            "sector": _SECTOR_MAP.get(row["code"], "미분류"),
            "price_by_date": {c["date"]: c["close"] * fx_mult for c in (d.get("candles") or [])},
        })

    if total_value <= 0:
        return {"available": False, "reason": "평가금액을 계산할 수 없습니다.", "excluded": excluded}

    for it in items:
        it["weight"] = round(it["value"] / total_value * 100, 1)
    items.sort(key=lambda x: -x["weight"])

    sector_weight: dict = {}
    for it in items:
        sector_weight[it["sector"]] = round(sector_weight.get(it["sector"], 0) + it["weight"], 1)
    sector_weight = dict(sorted(sector_weight.items(), key=lambda kv: -kv[1]))

    # ⚠️ 2026-09-18 — 상관관계 행렬(corr)을 리밸런싱보다 먼저 계산해 _recommend_weights()에
    # 넘긴다. 예전엔 리밸런싱이 종목별 점수만 보고 섹터·상관관계를 전혀 몰라서 "반도체 73%
    # 쏠림"이라 경고해 놓고 리밸런싱을 그대로 따라도 69.2%로 3.4%p밖에 안 줄고, "삼성전자+
    # SK하이닉스는 사실상 같은 베팅"이라 경고하면서 SK하이닉스 비중은 오히려 늘리라고
    # 하는 모순이 있었다(포트폴리오 결함 리포트 2026-09-18 2장). price_by_date가 아직
    # 삭제되기 전이라 여기서 호출 가능 — 원래 위치(경고 문구 생성 이후)에서 그대로 당겨왔다.
    corr, contrib = _correlation_and_risk(items)

    target_weights, capped_reason = _recommend_weights(items, corr=corr)
    for it in items:
        it["target_weight"] = target_weights.get(it["code"])
        it["rebalance_note"] = _rebalance_note(it, len(items), capped_reason)
        it["rebalance_action"] = _actionable_rebalance(it, total_value)

    def _wavg(key):
        pairs = [(it["weight"], it[key]) for it in items if it.get(key) is not None]
        tw = sum(w for w, _ in pairs)
        return round(sum(w * v for w, v in pairs) / tw, 1) if tw else None

    def _wavg_upside():
        # 일반 _wavg와 달리 종목별 upside_weight(목표가 괴리 검증 가중치)까지 곱해서
        # 신뢰도 낮은 목표가가 포트폴리오 기대수익률을 부풀리지 않게 한다.
        pairs = [(it["weight"] * it.get("upside_weight", 1.0), it["upside"])
                 for it in items if it.get("upside") is not None]
        tw = sum(w for w, _ in pairs)
        return round(sum(w * v for w, v in pairs) / tw, 1) if tw else None

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

    # 결함 리포트 Tier1 1-2 — "어제 대비 판단이 바뀐 보유종목" 한 줄 요약(관심종목 페이지의
    # watchSummaryHtml()과 같은 목적). _TIER_ORDER는 0=가장 공격적 매수 ~ 뒤로 갈수록
    # 보수적이라, 인덱스가 줄면 상향(개선)·늘면 하향(악화)이다.
    improved = worsened = 0
    for it in items:
        if not it.get("verdict_changed"):
            continue
        cur_tier = (it.get("ai_verdict") or {}).get("tier")
        prev_tier = it.get("prev_verdict_tier")
        try:
            if analysis._TIER_ORDER.index(cur_tier) < analysis._TIER_ORDER.index(prev_tier):
                improved += 1
            else:
                worsened += 1
        except ValueError:
            pass
    changes_summary = {"improved": improved, "worsened": worsened}

    quality_score = _wavg("score")
    risk_penalty, risk_penalty_detail = _risk_penalty(items, vol, mdd)
    pf_score = round(max(0.0, quality_score - risk_penalty), 1) if quality_score is not None else None
    grade, grade_desc = "F", "위험"
    if pf_score is not None:
        for th, g, desc in analysis.GRADE_TABLE:
            if pf_score >= th:
                grade, grade_desc = g, desc
                break

    # ⚠️ 2026-09-18 — 현금(cash)은 여기서 종목 weight%·리밸런싱·리스크 지표 계산에 섞지
    # 않는다. 섞으면 HHI·섹터상한·상관관계클러스터·리밸런싱 목표비중(100%로 재정규화하는
    # 로직 전체)이 "투자금 대비"에서 "현금 포함 총자산 대비"로 전부 의미가 바뀌어야 하고,
    # 그 파급이 이번에 막 고친 리밸런싱 섹터/상관관계 상한 로직 전체를 다시 건드리게 된다.
    # 대신 "총자산(현금 포함)"과 "현금 비중"을 별도 필드로 노출한다 — 결함 리포트 10장이
    # 지적한 "현금이 없어 비중이 왜곡된다"·"매수 재원 근거가 없다"는 총자산·현금 규모를
    # 보여주는 것만으로 해소되고, 종목 간 상대 비중(리밸런싱의 기준)은 원래 의미(투자금
    # 대비 종목 배분) 그대로 유지하는 게 더 안전하다.
    total_assets = total_value + cash
    cash_weight = round(cash / total_assets * 100, 1) if total_assets > 0 else None

    _record_snapshot(user_id, total_assets, cash)

    return {
        "available": True,
        "total_value": round(total_value),
        "cash": round(cash),
        "cash_weight": cash_weight,
        "total_assets": round(total_assets),
        "total_cost": round(total_cost) if total_cost else None,
        "total_pnl": round(total_pnl) if total_pnl is not None else None,
        "total_pnl_pct": total_pnl_pct,
        "today_pnl": today_pnl,
        "today_pnl_pct": today_pnl_pct,
        "changes_summary": changes_summary,
        "items": items,
        "sector_weight": sector_weight,
        "score": pf_score,
        "quality_score": quality_score,
        "risk_penalty": risk_penalty,
        "risk_penalty_detail": risk_penalty_detail,
        "grade": grade,
        "grade_desc": grade_desc,
        "valuation_score": _wavg("val_score"),
        "expected_return": _wavg_upside(),
        "expected_return_flagged": any(it.get("upside_flagged") for it in items),
        "volatility": vol,
        "max_drawdown": mdd,
        "warnings": warnings,
        "excluded": excluded,
        "today_actions": today_actions,
        "risk_flags": risk_flags,
        "theme_exposure": theme_exposure,
        "correlation": corr_table,
    }
