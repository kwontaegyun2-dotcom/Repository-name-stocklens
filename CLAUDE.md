# CLAUDE.md — StockLens 작업 규칙

국내(코스피/코스닥) + 미국 주식 종합 분석 웹앱. FastAPI + 바닐라 JS, Render 무료 플랜 배포.
**상세 인계·현재 진행상황은 반드시 [HANDOFF.md](HANDOFF.md) 를 먼저 읽을 것.**

---

## 프로젝트 구조

```
main.py              FastAPI 엔트리 · 전 엔드포인트 · 공개모드 게이팅 · 레이트리밋
app/naver.py         네이버 비공식 API 클라이언트 (국내/미국 자동 라우팅)
app/analysis.py      분석 엔진 (기술적·펀더멘털·감성·점수화)
app/ranking.py       랭킹 유니버스(국내 182/미국 190) 백그라운드 채점
app/chart_pro.py     고급 차트 분석 (스테이지·상대강도·추세템플릿·VCP·OBV·피보나치 등)
app/valuation.py     밸류에이션 6항목 (역사적PER·PEG·동종업계·EV/EBITDA·목표주가·실적방향)
app/dart.py          DART 주요계정 → 영업이익 YoY·흑자전환 (키 없으면 자동 비활성)
app/kis.py           한국투자증권 실시간 시세 (로컬 전용)
app/ai.py            Claude AI 심층 리포트 (선택)
static/index.html    4개 뷰 + 모달
static/app.js        전 프론트 로직
static/style.css     CSS 변수 기반 다크/라이트 테마
```

## 실행

```bash
python -m uvicorn main:app --port 8899        # http://127.0.0.1:8899
```
`--reload` 없음 → **백엔드(.py) 수정 시 서버 재시작 필수.**

---

## 🚨 반드시 지킬 규칙

### 1. CSS/JS를 수정하면 캐시 버전을 올린다
`static/index.html` 의 `style.css?v=N` / `app.js?v=N` 을 **둘 다 +1**.
안 올리면 브라우저가 옛 파일을 계속 써서 "고쳤는데 반영 안 됨" 상황이 생긴다. (이 프로젝트에서 반복 발생한 실수)

### 2. 값 파싱은 반드시 `to_num()` / `parse_eok()` 를 쓴다
네이버는 값에 단위를 붙여 준다 — `"23.08배"`, `"12,372원"`, `"1,669조 1,125억"`.
`float()` 를 직접 쓰면 전부 `None` 이 되어 지표가 통째로 비는 버그가 난다. (실제 발생 사례)

### 3. 국내/미국은 데이터 구조가 다르다
`naver.is_us(code)` 로 분기 (6자리 숫자=국내). 새 필드를 다룰 땐 양쪽 모두 확인할 것.
- 지표: 국내 `integration.totalInfos` / 미국 `basic.stockItemTotalInfos`
- 재무: 국내 `{financeInfo:{rowList}}` / 미국 `{rowList}` 최상위
- 미국은 **부채비율·ROE·수급·리서치가 없다** (빈 값/빈 배열 처리 필요)

### 4. pykrx를 쓰지 않는다
이 환경에서 KRX 펀더멘털/티커 엔드포인트가 빈 응답을 반환해 실패한다. 데이터는 네이버로 통일.

### 5. 공개 배포 모드를 깨지 않는다
`STOCKLENS_PUBLIC=1`(Dockerfile 내장)일 때 KIS 키 저장·AI 리포트는 **403으로 차단**되어야 한다.
낯선 사람이 개인 브로커 키를 서버에 저장하거나 소유자 API 키로 비용을 발생시키는 걸 막는 장치다.

### 6. 비밀정보를 커밋하지 않는다
`kis_config.json`, `kis_token.json`, `dart_key.txt` 는 `.gitignore` 대상. 키는 환경변수(또는 gitignore된 파일)로만 다룬다.

### 7. 외부 키가 필요한 기능은 키 없이도 서버가 돌아가야 한다
`app/dart.py` 처럼 키가 없으면 빈 결과를 반환하고, 호출부가 **기존 공식으로 폴백**하도록 짠다.
프론트도 마찬가지로 해당 컬럼·프리셋을 숨긴다. 공개 배포본은 키가 없는 상태로도 정상 동작해야 한다.

### 8. 밸류에이션 계산의 4가지 함정 (2026-07-24, 전부 실제로 밟음)
`app/valuation.py` 수정 시 아래 방지장치를 되돌리지 말 것:
1. **선행PER에 컨센서스 EPS를 섞지 않는다** — 다음 해가 실적인 경우만 계산.
2. **PEG 성장률 상한 50%** — 저기반 회복(+779%)을 그대로 나누면 PEG 0.01.
3. **동종업계는 중앙값** + PER 200배 초과 제외 — 이상치 1종목이 업종평균을 238배로 만든다.
4. **미국 단위 환산** — 시총=억 USD, 재무제표=백만 USD. EBITDA는 100으로 나눠야 한다.

### 9. 점수화 편향을 재도입하지 말 것 (2026-07-24 교정)
점수 로직을 바꾸면 **반드시 섹터가 다른 종목 묶음으로 재검증**한다(바이오·금융·자동차·통신 등).
과거 두 편향을 겪었고 `analysis.py` 에 방지장치가 있으니 되돌리지 말 것:
- **PEG 보정**은 성장률을 그대로 곱하지 않는다 — 저기반(적자→흑자) 폭증이 밸류를 부풀린다.
  전망 성장률 상한(40%)·중앙값·완화된 보정강도(0.35)를 유지.
- **부채비율 >500%는 금융업 구조로 보고 중립 처리** — 은행·지주(부채 1000%+)를 재무위험으로
  깎으면 안 된다. ROE 쿠션(상한 60)은 약점기업 하한선이지 고득점 부스터가 아니다.
검증 스크립트 패턴은 [HANDOFF.md](HANDOFF.md) 6장 참고(Python으로 여러 종목 부문점수 출력).

---

## 검증 방법

### 프론트엔드
**스크린샷 도구는 이 프로젝트에서 계속 타임아웃된다**(외부 CDN 로드 대기). 대신 `javascript_tool` 로 DOM을 직접 eval 검증할 것 — 안정적으로 동작한다.

```js
document.querySelectorAll('#rank-list .rank-row').length
getComputedStyle(document.body).backgroundColor
```
- 종목 분석 렌더는 **8~9초** 대기 후 확인 (7초는 부족했던 사례 있음)
- 테마 전환 직후 색상 측정은 `transition` 때문에 옛 값이 잡힌다 → transition을 잠시 끄고 측정

### 백엔드
```powershell
Invoke-RestMethod "http://127.0.0.1:8899/api/analyze/005930"   # 국내
Invoke-RestMethod "http://127.0.0.1:8899/api/analyze/AAPL.O"   # 미국
```
PowerShell은 **한글 키 접근이 안 된다**(`$a.total.categories.'가치평가'` → 빈값). 부문 점수 확인은 Python으로.

---

## 배포

작업은 **로컬 구현에서 멈추지 않고 배포·반영확인까지** 완료한다 (사용자 기본 기대).

> ⚠️ **Render는 폐기됨(2026-08-13, 사용자가 직접 일시정지).** 지금은 **오라클이 유일한
> 라이브 배포처**이고, git push만으로는 반영되지 않는다 — SSH/SCP 수동 배포가 필수다.
> 상세 절차는 [HANDOFF.md](HANDOFF.md) 6장 참고.

```bash
git add -A && git commit -m "..." && git push origin main
```
→ (GitHub 백업용, 자동배포 아님) → 오라클에 SCP+SSH로 수동 배포 → 라이브에서 캐시버전·기능 실제 확인.

라이브: https://stocklens.161-33-201-126.nip.io

---

## 코드 스타일

- 주석·UI 문구는 **한국어**. 커밋 메시지도 한국어.
- 프레임워크·빌드 도구 추가 금지 (바닐라 JS 유지). 의존성은 꼭 필요할 때만 `requirements.txt` 에 추가.
- CSS는 **변수(`--bg`, `--surface`, `--text` 등)** 를 쓴다. 색상 하드코딩 시 라이트 테마가 깨진다.
- 새 무거운 집계는 **백그라운드 + 캐시 + 지연로드** 패턴을 따른다 (`ranking.py` 참고).
  무료 서버라 요청 경로에서 오래 걸리는 작업을 하면 안 된다.
