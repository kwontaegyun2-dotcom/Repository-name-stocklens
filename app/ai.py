# -*- coding: utf-8 -*-
"""선택 기능: Claude AI 심층 분석 리포트 (ANTHROPIC_API_KEY 설정 시 활성화)."""
import os


def available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def deep_report(name: str, code: str, payload: dict) -> str:
    """뉴스·리포트·재무지표를 Claude에 전달해 심층 분석 마크다운 리포트 생성."""
    import anthropic

    client = anthropic.Anthropic()

    news_lines = "\n".join(
        f"- [{it.get('press')}] {it.get('title')} :: {it.get('body', '')[:100]}"
        for it in payload.get("news", [])[:15])
    report_lines = "\n".join(
        f"- [{r.get('broker')}] {r.get('title')} ({r.get('date')}) :: {r.get('preview', '')[:150]}"
        for r in payload.get("research", [])[:8])

    m = payload.get("metrics", {})
    cons = payload.get("consensus", {})
    tech = payload.get("technical", {})

    # 적자→흑자 등 저기반 회복 시 컨센서스 성장률이 수백%로 튈 수 있다(실측: 삼성전자
    # +796%). 값을 숨기지 않고 그대로 넘기되(AI가 맥락을 판단할 정보이므로 임의로 캡하지
    # 않음), 극단값이면 주의 문구를 붙여 AI가 "실제 폭발적 성장"처럼 서술하지 않게 한다.
    gf = m.get("op_growth_fwd")
    if m.get("consensus_flagged"):
        gf = m.get("op_growth_fwd_raw")
        gf_note = f"(주의: 컨센서스 이상치로 검증 보류된 수치 — {m.get('consensus_flag_reason')}. 신뢰할 수 있는 사실처럼 강조하지 말고 반드시 '검증 필요'라고 언급할 것)"
    else:
        gf_note = "(주의: 저기반 회복 등으로 왜곡됐을 수 있는 수치 — 액면 그대로 강조하지 말 것)" \
            if isinstance(gf, (int, float)) and abs(gf) > 100 else ""

    prompt = f"""당신은 한국 주식시장 전문 애널리스트입니다. 아래 데이터를 바탕으로 {name}({code})에 대한 심층 분석 리포트를 한국어 마크다운으로 작성하세요.

## 투자지표
PER {m.get('per')}배 / 선행PER {m.get('cns_per')}배 / PBR {m.get('pbr')}배 / ROE {m.get('roe')}% / 영업이익률 {m.get('op_margin')}% / 부채비율 {m.get('debt_ratio')}% / 배당수익률 {m.get('dividend_yield')}%
매출성장률(전년) {m.get('rev_growth')}% / 영업이익성장률(전년) {m.get('op_growth')}% / 컨센서스 영업이익성장률(내년) {gf}%{gf_note}

## 애널리스트 컨센서스
목표주가 평균 {cons.get('target_price')}원, 투자의견 {cons.get('opinion')}, 상승여력 {cons.get('upside')}%

## 기술적 분석
현재가 {tech.get('price')}원, RSI {tech.get('rsi')}, 52주 위치 {tech.get('pos_52w')}%, 판단: {tech.get('verdict')}

## 최근 뉴스
{news_lines}

## 증권사 리포트
{report_lines}

다음 구성으로 작성하세요:
1. **핵심 요약** (3줄 이내)
2. **미래 사업가치 및 성장 동력** — 뉴스와 리포트에서 읽히는 사업 방향성 분석
3. **리스크 요인**
4. **밸류에이션 판단**
5. **투자 전략 제안** — 진입 타이밍과 시나리오별 대응

과장 없이 데이터에 근거해 쓰고, 마지막에 '본 리포트는 투자 참고용이며 투자 판단의 책임은 투자자 본인에게 있습니다.'를 덧붙이세요."""

    with client.messages.stream(
        model="claude-opus-4-8",
        max_tokens=4000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()

    return next((b.text for b in message.content if b.type == "text"), "")


def market_commentary(snap: dict, valuation: dict) -> str:
    """마켓 브리핑 AI 한줄평 — 30분 캐시로 재사용되므로(app/market.py) 가벼운 모델·
    짧은 답변으로 비용을 낮춘다. AI_ALLOWED가 꺼져 있으면(공개배포 기본값) 호출부가
    이 함수 대신 규칙기반 문구를 쓴다 — CLAUDE.md 5번 규칙(공개모드 AI비용 차단) 준수."""
    import anthropic

    client = anthropic.Anthropic()
    idx, fx, cmd, bonds, sent, breadth, composite, val = (
        snap.get("indices", {}), snap.get("fx", {}), snap.get("commodities", {}),
        snap.get("bonds", {}), snap.get("sentiment", {}), snap.get("breadth", {}),
        snap.get("composite", {}), snap.get("valuation", {}))

    def _row(label, it):
        if not it:
            return f"{label}: 데이터 없음"
        return f"{label}: {it.get('price')} ({it.get('rate'):+.2f}%)" if it.get("rate") is not None else f"{label}: {it.get('price')}"

    def _fx(label, it):
        if not it or it.get("value") is None:
            return f"{label}: 데이터 없음"
        return f"{label}: {it['value']} ({it['rate']:+.2f}%)" if it.get("rate") is not None else f"{label}: {it['value']}"

    def _gauge_row(label, g):
        if not g or g.get("value") is None:
            return f"{label}: 데이터 없음"
        return f"{label}: {g['value']} ({g['label']})"

    buffett = valuation.get("buffett")
    kospi_b, kosdaq_b = breadth.get("kospi"), breadth.get("kosdaq")
    prompt = f"""아래는 오늘 수집한 시장 데이터입니다. 이 숫자만 근거로 오늘 시장 분위기를
한국어 1~2문장(120자 이내)으로 요약하세요. 과장 없이 담백하게, 숫자를 인용하며 쓰세요.
데이터가 없는 항목은 언급하지 마세요.

## 지수
{_row("코스피", idx.get("kospi"))}
{_row("코스닥", idx.get("kosdaq"))}
{_row("S&P500", idx.get("sp500"))}
{_row("나스닥", idx.get("nasdaq"))}

## 자산·환율
{_fx("원/달러", fx.get("usdkrw"))}
{_fx("WTI", cmd.get("wti"))}
{_fx("국제 금", cmd.get("gold_intl"))}

## 밸류에이션
{_gauge_row("S&P500 PER", val.get("sp500_per"))}
{_gauge_row("Shiller CAPE", val.get("cape"))}
버핏지수: {f"{buffett['value']:.0f}%({buffett['label']})" if buffett else "데이터 없음"}

## 심리·체력·신용
VIX: {sent.get("vix")}
코스피 상승/하락 종목수: {f"{kospi_b['rise']}/{kospi_b['fall']}" if kospi_b else "데이터 없음"}
美 10Y-2Y 금리차: {bonds.get("spread_10y2y")}%p
美 10Y-3M 금리차: {bonds.get("spread_10y3m")}%p

## StockLens 종합 시장온도(0~100, 높을수록 과열)
{composite.get("overall")}점 (밸류에이션 {composite.get("valuation")} · 심리 {composite.get("sentiment")} · 체력 {composite.get("strength")} · 신용 {composite.get("credit")})

문장만 출력하고 다른 설명은 붙이지 마세요."""

    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return next((b.text for b in message.content if b.type == "text"), "").strip()
