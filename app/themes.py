# -*- coding: utf-8 -*-
"""종목이 아니라 '테마/산업' 단위로 묶어 본다 — 국내+미국을 섞은 테마도 있다.

app/ranking.py가 이미 백그라운드로 전 종목(국내 182 + 미국 190)을 채점해 캐시해두므로,
여기서는 그 캐시에서 코드로 조회만 한다(추가 네트워크 호출 없음). 그래서 테마에 넣는
종목은 반드시 ranking.UNIVERSE/US_UNIVERSE에 이미 등록된 코드여야 안전하게 조회된다.
"""
from app import ranking

# "AI 반도체" 테마 = ranking.py의 "반도체" 섹터 태그(표준 업종 분류, 자동 파생 —
# 신규 반도체 종목이 추가돼도 손 유지보수 불필요) + 반도체를 직접 설계·제조하진
# 않지만 AI 반도체 수요에 실질적으로 노출된 관련 종목(수작업 편입, 사유 명시).
#
# ⚠️ 6차 진단리포트(2026-09-23) 4-1 — 예전엔 이 관련 종목(슈퍼마이크로)을 업종 태그
# 자체를 "반도체"로 바꿔서 포함시켰다. 그 결과 포트폴리오 화면의 "업종별 비중"과
# "테마 노출(실질 노출)"이 완전히 같은 숫자만 냈다 — 두 지표가 서로 다른 걸 보여줘야
# 의미가 있는데 사실상 하나를 두 번 보여준 것(2026-09-18 진단 이전엔 반대로 이
# 종목이 아예 빠져서 두 숫자가 안 맞는 문제가 있었음 — 두 실패 모두 "업종=테마"로
# 취급한 게 원인). 이제 업종(ranking.py)은 표준 분류("AI서버·인프라")로 분리해 두고,
# 테마에서만 아래처럼 명시적으로 다시 포함시킨다.
_RELATED_CODES = [
    ("US", "SMCI.O", "AI GPU 서버 제조 — 반도체 직접생산은 아니지만 엔비디아 GPU 수요에 연동"),
]

_SEMICONDUCTOR_CODES = [
    ("KR", code) for code, _name, sector in ranking.UNIVERSE if sector == "반도체"
] + [
    ("US", code) for code, _name, sector in ranking.US_UNIVERSE if sector == "반도체"
] + [(market, code) for market, code, _reason in _RELATED_CODES]

# 편입 근거 — 프론트에서 "이 종목이 왜 이 테마에 포함됐는지" 보여줄 때 쓴다.
# 순수 섹터 파생 종목은 "표준 업종 분류: 반도체", 수작업 편입 종목은 개별 사유.
THEME_INCLUSION_REASON = {
    code: "표준 업종 분류: 반도체"
    for code, _name, sector in (ranking.UNIVERSE + ranking.US_UNIVERSE) if sector == "반도체"
}
THEME_INCLUSION_REASON.update({code: reason for _market, code, reason in _RELATED_CODES})

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
