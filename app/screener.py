# -*- coding: utf-8 -*-
"""조건 조합 스크리너 — app/ranking.py가 이미 백그라운드로 전종목 채점하며 캐시해둔
지표(PER·PBR·ROE·부채비율·배당수익률·외국인 수급 등)만으로 필터링한다.
추가 네트워크 호출·재계산 없음(CLAUDE.md의 "무거운 집계는 백그라운드+캐시" 원칙).
"""
from app import ranking

# (조건 키, 비교 방향) — "min"은 field >= value, "max"는 field <= value
_NUMERIC_FIELDS = {
    "score_min": ("score", "min"),
    "per_max": ("per", "max"),
    "per_min": ("per", "min"),
    "pbr_max": ("pbr", "max"),
    "roe_min": ("roe", "min"),
    "debt_max": ("debt_ratio", "max"),
    "div_min": ("dividend_yield", "min"),
    "upside_min": ("upside", "min"),
}
_GRADE_ORDER = ["F", "D", "C", "B", "A", "S"]


def run(conditions: dict, limit: int = 100) -> dict:
    market = (conditions.get("market") or "전체").upper()
    markets = ["KR", "US"] if market not in ("KR", "US") else [market]
    items = []
    for m in markets:
        items.extend(ranking.get(m)["items"])

    sector = conditions.get("sector")
    if sector and sector != "전체":
        items = [r for r in items if r.get("sector") == sector]

    grade_min = conditions.get("grade_min")
    if grade_min and grade_min in _GRADE_ORDER:
        min_idx = _GRADE_ORDER.index(grade_min)
        items = [r for r in items if r.get("grade") in _GRADE_ORDER and _GRADE_ORDER.index(r["grade"]) >= min_idx]

    for key, (field, direction) in _NUMERIC_FIELDS.items():
        val = conditions.get(key)
        if val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if direction == "min":
            items = [r for r in items if r.get(field) is not None and r[field] >= val]
        else:
            items = [r for r in items if r.get(field) is not None and r[field] <= val]

    if conditions.get("foreign_buy"):
        items = [r for r in items if r.get("foreign_dir") == "buy"]

    items = sorted(items, key=lambda r: r["score"], reverse=True)
    return {
        "items": items[:limit],
        "total_matched": len(items),
        "universe_size": sum(len(ranking.get(m)["items"]) for m in markets),
    }
