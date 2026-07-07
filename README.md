# 📊 StockLens — 국내 주식 종합 분석 플랫폼

종목을 검색하면 투자지표·재무·차트·뉴스·증권사 리포트를 종합 분석해 **점수화**하고,
**목표주가와 진입 타이밍**까지 제시하는 웹 대시보드입니다.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/kwontaegyun2-dotcom/Repository-name-stocklens)

> 기존 `stock-analyzer` 폴더의 Streamlit 분석기와는 별개의 새 프로젝트입니다.

## 실행 방법

```powershell
cd C:\Users\권태균\stocklens
pip install fastapi uvicorn requests
python -m uvicorn main:app --port 8899
```

브라우저에서 http://127.0.0.1:8899 접속.

### 휴대폰·태블릿에서 접속 (같은 와이파이)

서버를 `--host 0.0.0.0`으로 실행하면 같은 공유기에 연결된 기기에서
`http://<PC의 IP>:8899` 로 접속할 수 있습니다.

```powershell
# PC IP 확인
ipconfig   # Wi-Fi 어댑터의 IPv4 주소 (예: 10.10.101.239)

# 모든 기기 허용으로 실행
python -m uvicorn main:app --host 0.0.0.0 --port 8899
```

- Windows 방화벽 인바운드 규칙 "StockLens 8899" (TCP 8899 허용)가 등록되어 있어야 합니다.
- PC가 켜져 있고 서버가 실행 중이어야 접속됩니다.
- 집 밖(외부망)에서도 쓰려면 [Tailscale](https://tailscale.com) 같은 VPN 앱을 PC와 휴대폰에 설치하는 방법이 가장 안전합니다.

## 주요 기능

| 기능 | 설명 |
|---|---|
| 🔍 종목 검색 | 종목명/코드 자동완성 (코스피·코스닥) |
| 💯 종합 점수 | 가치평가·수익성·성장성·재무안정성·기술적추세·수급심리 6개 부문 점수화 → S~F 등급 |
| 📐 투자지표 | PER·선행PER·PBR·ROE·EPS·BPS·배당수익률·부채비율 등 |
| 🎯 목표주가 | 애널리스트 컨센서스 목표가 + 기술적 목표가, 상승여력 % |
| ⏱ 진입 타이밍 | 이동평균·RSI·MACD·볼린저밴드 기반 매수구간/지지선/저항선/손절가 제안 |
| 📈 차트 분석 | 캔들차트 + SMA20/60/120 + 거래량 + 목표주가/지지/저항 라인 |
| 📰 뉴스 감성 | 최근 뉴스 긍정/부정 자동 분류 → 시장 심리 점수 |
| 📑 증권사 리포트 | 최신 리포트 목록 + 투자의견 컨센서스 |
| 🌊 수급 동향 | 외국인/기관/개인 순매수 최근 10일 |
| 🏭 동일업종 비교 | 같은 업종 주요 종목 시세·시총 비교 |
| ⚡ 실시간 시세 | 4초 간격 자동 갱신 (한국투자증권 API 연동 시 실시간, 기본은 네이버 시세) |
| 🤖 AI 심층 분석 | (선택) Claude API로 뉴스·리포트 기반 미래 사업가치 심층 리포트 생성 |

## 점수 산정 방식 (100점)

| 부문 | 가중치 | 사용 지표 |
|---|---|---|
| 가치평가 | 18% | PER·PBR·배당수익률 |
| 수익성 | 20% | ROE·영업이익률·순이익률 |
| 성장성 | 22% | 매출/영업이익 성장률(과거+컨센서스 전망) |
| 재무안정성 | 12% | 부채비율·유보율 |
| 기술적추세 | 16% | 이평 배열·골든/데드크로스·RSI·MACD·볼린저밴드 |
| 수급·심리 | 12% | 외국인/기관 순매수·뉴스 감성·애널리스트 투자의견 |

등급: S(85+) · A(75+) · B(65+) · C(50+) · D(35+) · F

## 한국투자증권 API 연동 (선택)

1. [KIS Developers](https://apiportal.koreainvestment.com)에서 앱키/시크릿 발급
2. 화면 우측 상단 **⚙ 한국투자증권 API** 버튼 → 키 입력 → 저장 & 연결 테스트
3. 이후 실시간 시세가 KIS API로 제공됩니다 (키는 `kis_config.json`에 로컬 저장 — 공유 금지)

## Claude AI 심층 분석 (선택)

```powershell
pip install anthropic
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python -m uvicorn main:app --port 8899
```

키가 설정되면 리포트 하단에 "AI 심층 분석 생성" 버튼이 나타납니다.

## 데이터 소스

- 시세/재무/뉴스/리포트/컨센서스: 네이버 증권 (비공식 API)
- 실시간 시세(선택): 한국투자증권 오픈API
- AI 분석(선택): Anthropic Claude API

> ⚠️ 모든 분석 결과는 투자 참고용입니다. 투자 판단과 책임은 투자자 본인에게 있습니다.
