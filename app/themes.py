# -*- coding: utf-8 -*-
"""종목이 아니라 '테마/산업' 단위로 묶어 본다 — 국내+미국을 섞은 테마도 있다.

app/ranking.py가 이미 백그라운드로 전 종목(국내 182 + 미국 190)을 채점해 캐시해두므로,
여기서는 그 캐시에서 코드로 조회만 한다(추가 네트워크 호출 없음). 그래서 테마에 넣는
종목은 반드시 ranking.UNIVERSE/US_UNIVERSE에 이미 등록된 코드여야 안전하게 조회된다.
"""
from app import ranking

# "AI 반도체" 테마는 손으로 고른 일부 종목이 아니라 ranking.py의 "반도체" 섹터 태그를
# 그대로 끌어와 자동 구성한다. 2026-09-18 진단 — 손으로 골랐던 목록엔 슈퍼마이크로(SMCI.O,
# AI서버용 반도체 관련주)가 빠져 있어서, 포트폴리오 화면에서 "실질 노출(테마) 44.6%"와
# "업종별 비중 72.6%"이 같은 화면에 동시에 표시되는 모순이 생겼다(포트폴리오 결함 리포트
# 2026-09-18 7장). portfolio.py의 업종별 비중도 이 ranking 섹터 태그(_SECTOR_MAP)로
# 계산하므로, 테마도 같은 소스에서 파생시키면 신규 반도체 종목이 추가돼도 다시 벌어지지
# 않는다(손 유지보수 불필요).
_SEMICONDUCTOR_CODES = [
    ("KR", code) for code, _name, sector in ranking.UNIVERSE if sector == "반도체"
] + [
    ("US", code) for code, _name, sector in ranking.US_UNIVERSE if sector == "반도체"
]

# (market, code) — market은 ranking.get()이 쓰는 "KR"/"US" 그대로.
THEMES = {
    "AI 반도체": _SEMICONDUCTOR_CODES,
    "2차전지": [
        ("KR", "373220"), ("KR", "006400"), ("KR", "247540"), ("KR", "086520"),
        ("KR", "003670"), ("KR", "051910"), ("KR", "348370"),
    ],
    "바이오·헬스케어": [
        ("KR", "207940"), ("KR", "068270"), ("KR", "196170"), ("KR", "000100"),
        ("US", "LLY"), ("US", "UNH"), ("US", "JNJ"), ("US", "MRNA.O"), ("US", "ISRG.O"),
    ],
    "빅테크·플랫폼": [
        ("KR", "035420"), ("KR", "035720"),
        ("US", "AAPL.O"), ("US", "MSFT.O"), ("US", "GOOGL.O"), ("US", "AMZN.O"),
        ("US", "META.O"), ("US", "NFLX.O"),
    ],
    "자동차·모빌리티": [
        ("KR", "005380"), ("KR", "000270"), ("KR", "012330"),
        ("US", "TSLA.O"), ("US", "F"), ("US", "GM"),
    ],
    "금융": [
        ("KR", "105560"), ("KR", "055550"), ("KR", "086790"), ("KR", "323410"),
        ("US", "JPM"), ("US", "V"), ("US", "MA"), ("US", "GS"),
    ],
    "엔터·미디어": [
        ("KR", "352820"), ("KR", "035900"), ("KR", "041510"),
        ("US", "DIS"), ("US", "NFLX.O"), ("US", "SPOT.K"),
    ],
}


def list_themes():
    return list(THEMES.keys())


def get_theme(name: str, limit: int = 10):
    codes = THEMES.get(name)
    if not codes:
        return None

    by_code = {}
    computing = False
    for market in ("KR", "US"):
        r = ranking.get(market)
        computing = computing or r.get("computing", False)
        for item in r["items"]:
            by_code[item["code"]] = item

    items = []
    for market, code in codes:
        it = by_code.get(code)
        if it:
            items.append(dict(it))   # 랭킹 캐시 원본을 건드리지 않도록 복사본에만 표시

    items.sort(key=lambda x: x["score"], reverse=True)
    for i, it in enumerate(items, 1):
        it["theme_rank"] = i

    return {
        "name": name,
        "items": items[:limit],
        "total": len(codes),
        "missing": len(codes) - len(items),   # 아직 랭킹 계산 중이면 일시적으로 빠질 수 있음
        "computing": computing,
    }
