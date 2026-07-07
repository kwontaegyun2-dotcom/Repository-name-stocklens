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

    prompt = f"""당신은 한국 주식시장 전문 애널리스트입니다. 아래 데이터를 바탕으로 {name}({code})에 대한 심층 분석 리포트를 한국어 마크다운으로 작성하세요.

## 투자지표
PER {m.get('per')}배 / 선행PER {m.get('cns_per')}배 / PBR {m.get('pbr')}배 / ROE {m.get('roe')}% / 영업이익률 {m.get('op_margin')}% / 부채비율 {m.get('debt_ratio')}% / 배당수익률 {m.get('dividend_yield')}%
매출성장률(전년) {m.get('rev_growth')}% / 영업이익성장률(전년) {m.get('op_growth')}% / 컨센서스 영업이익성장률(내년) {m.get('op_growth_fwd')}%

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
