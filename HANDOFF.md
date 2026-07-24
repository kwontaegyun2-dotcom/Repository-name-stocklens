# StockLens 인계 문서 (HANDOFF)

> 이 문서만 읽고 바로 이어서 개발할 수 있도록 작성됨.
> 최종 갱신: 2026-07-24 · 캐시버전 **v12** · 축2(DART 실적변곡) 구현 중 — 아래 3장 참조

---

## 1. 프로젝트 개요

**StockLens** — 국내(코스피/코스닥) + 미국 주식 종합 분석 웹앱.
종목을 6개 부문으로 점수화(S~F 등급)하고, 목표주가·매수/매도 타점·차트·뉴스·백테스트·스크리너를 제공.

| 항목 | 값 |
|---|---|
| 로컬 경로 | `C:\Users\권태균\stocklens` |
| 라이브 URL | https://stocklens-mpr6.onrender.com |
| GitHub | https://github.com/kwontaegyun2-dotcom/Repository-name-stocklens (main 브랜치) |
| 배포 | Render.com, Docker, 싱가포르 리전, **무료 플랜** (push → 자동 재배포) |
| Render serviceId | `srv-d968qp77f7vs73d5hd80` |
| 스택 | FastAPI + 바닐라 JS (프레임워크·빌드 없음), 외부 CDN: lightweight-charts, Pretendard |

> ⚠️ **별개 프로젝트 주의**: `C:\Users\권태균\stock-analyzer` 는 예전 Streamlit 분석기.
> 이 프로젝트와 무관하니 건드리지 말 것.

---

## 2. 완료된 부분 (파일별)

### 백엔드

| 파일 | 역할 | 상태 |
|---|---|---|
| `main.py` | FastAPI 엔트리. 전 엔드포인트, 공개모드 게이팅, 레이트리밋 | ✅ 완료 |
| `app/naver.py` | 네이버 비공식 API 클라이언트. **국내/미국 자동 라우팅** | ✅ 완료 |
| `app/analysis.py` | 분석 엔진 전체 (기술적·펀더멘털·감성·점수화·백테스트) | ✅ 완료 |
| `app/ranking.py` | 국내 132 / 미국 147종목 백그라운드 채점·캐싱 | ✅ 완료 |
| `app/screener.py` | 밸류에이션·리레이팅 스크리너 스냅샷 | ✅ 축1+축2+축3 배선 완료 |
| `app/dart.py` | DART 주요계정 → 영업이익 YoY·흑자전환 (축2) | ⏳ 코드 완료 / **키 미검증** |
| `app/kis.py` | 한국투자증권 실시간 시세 (로컬 전용) | ✅ 완료 |
| `app/ai.py` | Claude AI 심층 리포트 (선택, 키 필요) | ✅ 완료 (공개모드 비활성) |

**엔드포인트 목록**
```
GET  /                      메인 HTML
GET  /api/search?q=&market= 통합 검색(국내+미국), market=KR|US 필터
GET  /api/ranking?market=&sector=   랭킹 (KR 즉시 / US 지연로드)
GET  /api/screener          스크리너 스냅샷 (지연로드, 6h 캐시)
GET  /api/price/{code}      실시간 시세 (KIS→네이버 폴백)
GET  /api/analyze/{code}    종합 분석 (핵심 엔드포인트)
GET  /api/kis/status        KIS 설정 여부
POST /api/kis/config        KIS 키 저장 (공개모드 403)
GET  /api/ai/status         AI 사용 가능 여부
POST /api/ai/report/{code}  AI 리포트 (공개모드 403)
```

### 프론트엔드 (`static/`)

| 파일 | 내용 |
|---|---|
| `index.html` | 4개 뷰: `#landing`(히어로+관심종목+랭킹) / `#report`(분석) / `#compare-view`(비교) / `#screener-view`(스크리너) + 비교트레이 + KIS모달 |
| `app.js` | 전 로직. 주요 블록: 통화포맷 → 테마 → 즐겨찾기 → 검색 → 네비 → 랭킹 → 분석/렌더 → 비교 → 스크리너 → 백테스트 → 차트 → 재무 → AI → KIS → init |
| `style.css` | CSS 변수 기반 다크/라이트 테마, 글래스모피즘 |

### 구현된 기능 (전부 라이브 검증 완료)

- **종합 점수화**: 가치평가·수익성·성장성·재무안정성·기술적추세·수급심리 6부문 → S/A/B/C/D/F
- **랭킹**: 국내 132 / 미국 147종목, 섹터 필터, Top5 + 더보기(10개씩)
- **종목 분석**: 한줄평 배너(최상단) · 게이지/레이더 · 목표주가 · 매수/매도 타점 · 캔들차트 · 기술적분석 · 백테스트 · 투자지표 · 재무 · 수급 · 리포트 · 뉴스 · 동일업종
- **차트**: SMA20/60/120, 지지/저항/목표가 라인, 골든/데드크로스 매수·매도 마커, 기간버튼(3M/6M/1Y/전체), 마우스휠=페이지스크롤
- **백테스트**: 골든/데드크로스 + RSI(30/70) 전략의 매매횟수·승률·평균수익 + 바이앤홀드 대조
- **종목 비교**: 최대 3종목 레이더 겹침 + 지표표(최우수 강조)
- **스크리너**: 프리셋 4종 + 수동필터, 업종 상대 백분위, 리레이팅 z스코어
- **기타**: 관심종목(localStorage), 다크/라이트 테마, 국내/미국 토글, 모바일 반응형

---

## 3. 지금 작업 중이던 지점 ⭐

### 3축 진행 상황
| 축 | 내용 | 상태 |
|---|---|---|
| 축 1 | 밸류에이션 (저PBR/PER/고ROE/배당) | ✅ 완료·배포 |
| 축 2 | **실적 변곡 (영업이익 YoY 턴어라운드)** | ⏳ **코드 완료 / 키 대기** |
| 축 3 | 수급 확인 (외국인·기관 순매수) | ✅ 완료·배포 |

설계서 원문: *"이게 핵심입니다. 리레이팅은 대부분 적자→흑자 또는 이익 증가율 가속 시점에서 시작됩니다."*

### 축2에서 **이미 짠 것** (2026-07-24)
- `app/dart.py` 신규 — corpCode.xml(zip) 파싱으로 종목코드→corp_code 매핑(1일 캐시),
  `fnlttMultiAcnt.json` 을 **100개씩 배치** 호출(400종목=4콜). 연결(CFS) 우선, 분기 단독금액 우선·누적 폴백.
  `op_yoy`(전년동기 대비 %, ±300 클리핑)와 `turnaround`(적자→흑자) 산출.
  **전년이 적자여도 분모를 `abs(prev)` 로 둬서 부호가 뒤집히지 않게 함** (적자축소=+, 적자확대=−).
- `app/screener.py` — `dart.get()` 결과가 있으면 리레이팅 공식을
  `PBR_z*0.3 + OP_YoY_z*0.4 + 수급_z*0.3` 으로 교체, 없으면 **기존 0.55/0.45로 자동 폴백**.
  응답에 `dart`(bool)·`period`(예 "2026 1분기") 메타 추가.
- **적자기업 누락 문제 동시 해결**: `_compute()` 의 종목 필터에서 `per` 필수 조건을 제거.
  (적자기업이 곧 턴어라운드 후보인데 이전엔 통째로 빠져 있었음)
- 프론트 — `📈 실적 턴어라운드` 프리셋(흑자전환 또는 OP_YoY≥20%, 흑자전환 최상단 정렬),
  `영업이익 YoY` 컬럼 + `흑자전환` 배지. **DART 미가동이면 프리셋 버튼·컬럼 자체가 숨겨짐.**
  적자기업은 `allowLoss` 프리셋에서만 PER·ROE 하한을 건너뛰고 통과 (기존 프리셋 동작은 그대로).
- 캐시버전 v11 → **v12**, `.gitignore` 에 `dart_key.txt` 추가.

### ⛔ 지금 막혀 있는 단 하나
**DART API 키가 없어 실제 응답으로 검증하지 못함.** (키 없는 폴백 경로만 검증됨)
- 키 발급: https://opendart.fss.or.kr → 인증키 신청 (무료, 즉시)
- 키 주입: `C:\Users\권태균\stocklens\dart_key.txt` 에 한 줄로 저장 (gitignore됨) **또는** 환경변수 `DART_API_KEY`
- 배포 반영: Render 대시보드 → Environment 에 `DART_API_KEY` 추가 (코드 푸시로는 안 됨)

**키가 들어오면 가장 먼저 할 일**: 응답 필드명 실물 확인.
`fnlttMultiAcnt` 의 `thstrm_amount`/`frmtrm_amount`(분기 단독)와 `_add_amount`(누적) 중
어느 쪽이 실제로 채워져 오는지, `frmtrm_`이 정말 **전년 동기**인지를 raw 응답으로 확인한 뒤
`app/dart.py` 의 `_parse()` 를 맞출 것. **이 부분이 유일한 추정 구간이다.**

---

## 4. 다음 작업 순서 (권장 순)

### 1️⃣ 축 2 마무리 — 키 주입 후 실물 검증 (**여기서 이어서 시작할 것**)
1. `dart_key.txt` 또는 `DART_API_KEY` 에 키를 넣고 서버 재시작
2. **raw 응답부터 확인** (추정으로 짠 유일한 부분):
   ```python
   from app import dart
   cm = dart.corp_map(); print(len(cm), cm.get("005930"))      # 매핑 정상?
   items = dart._fetch_batch([cm["005930"], cm["000660"]], 2026, "11013")
   print([i for i in items if "영업이익" in i["account_nm"]][:2])  # 필드명·전년동기 여부 확인
   ```
3. 위 결과에 맞춰 `dart._parse()` 의 필드 선택 로직을 확정
4. `curl /api/screener` 로 `dart:true`, `period`, `op_yoy` 채워지는지 확인
5. Render 대시보드 → Environment 에 `DART_API_KEY` 추가 후 재배포 (**코드 푸시만으론 안 됨**)
6. 라이브에서 `📈 실적 턴어라운드` 프리셋이 나타나는지 확인
- **주의**: DART 호출 한도 20,000/일. 현재 구조는 6시간마다 4~6콜이라 여유 충분

### 2️⃣ 스냅샷 누적 + 스크리너 백테스트
- **문제**: Render 무료 플랜은 디스크 비영속 → SQLite가 재배포마다 초기화됨
- 해결안: 외부 무료 Postgres(Supabase/Neon) 연결 → 일별 스냅샷 누적
- 스키마는 설계서의 `fundamental` / `financials` / `trading_flow` 3테이블 그대로 사용
- 목적: "이 스크리너가 뽑은 종목의 6개월 후 수익률" 검증 (이게 없으면 스크리너 유효성 검증 불가)

### 3️⃣ 스크리너 유니버스 확대 (400 → 600+)
- `app/screener.py` 의 `UNIVERSE_PAGES = [("KOSPI", 3), ("KOSDAQ", 1)]` 수정 (1페이지=100종목)
- 트레이드오프: 종목당 integration 1콜이라 600종목이면 첫 집계 ~3분

### 4️⃣ 기타 백로그 (사용자 미요청, 여유 시)
- 미국 종목 ROE·부채비율 부재 → 현재 "-" 표시 (네이버 미제공). 대체 소스 검토
- 백테스트 기간 확대 (현재 ~1년 260봉 → 2~3년) 시 표본 신뢰도 개선
- 애널리스트 목표가 분포(최고/평균/최저) 시각화 — 사용자가 예전에 후보로 봤으나 미선택
- 미국주식 원화 병기 (환율 환산) — 위와 동일

---

## 5. 핵심 설계 결정 (건드리기 전에 반드시 읽을 것)

### 5.1 데이터 소스 = 네이버 비공식 API (pykrx 아님)
- **pykrx는 쓰지 말 것.** 이 환경에서 `get_market_fundamental` / `get_market_ticker_list` 가
  KRX로부터 **빈 응답**을 받아 실패함(로컬·배포 모두). OHLCV만 작동.
- 대신 네이버 사용. 싱가포르(Render) IP에서도 **정상 작동 검증됨**.
- 국내: `m.stock.naver.com/api` · 미국: `api.stock.naver.com` (자동 라우팅은 `naver.is_us()`)

### 5.2 국내/미국 분기 (`naver.is_us(code)`)
- 판별: **6자리 숫자면 국내**, 그 외는 미국 reutersCode
- 미국 코드 형식: NASDAQ=`AAPL.O`, NYSE=`JPM`(bare) 또는 `ORCL.K` — **불규칙하니 반드시 검색 API로 확인**
  (`https://ac.stock.naver.com/ac?q=TICKER&target=stock` 의 `reutersCode`)

| 데이터 | 국내 | 미국 |
|---|---|---|
| 지표(PER/PBR) | `integration.totalInfos` | `basic.stockItemTotalInfos` |
| 재무 구조 | `{financeInfo:{rowList}}` | `{rowList}` **최상위** |
| 재무 항목 | 매출액/영업이익/ROE/부채비율/유보율 | 매출액/EBIT/당기순이익/ROA (**부채비율·ROE 없음**) |
| 차트 | `fchart` XML | `chart/foreign/item/{rc}/day` JSON |
| 수급·리서치 | 있음 | **없음** (빈 배열 반환) |

### 5.3 값 파싱 함정 ⚠️ (과거 실제 버그)
네이버는 값에 **단위를 붙여서** 줌: `"23.08배"`, `"12,372원"`, `"1,669조 1,125억"`.
→ `analysis.to_num()` 이 콤마·%·배·원·주 제거, `analysis.parse_eok()` 가 조/억 → 억 단위 변환.
**새 필드 파싱 시 반드시 이 두 함수를 쓸 것.** (직접 float() 하면 전부 None 됨)

### 5.4 점수화 로직 (`analysis.py`)
- 부문 가중치: 가치18% · 수익20% · 성장22% · 안정12% · 기술16% · 수급심리12%
- 등급: **S 85+ / A 75+ / B 65+ / C 50+ / D 35+ / F**
- **PEG 보정**: 고성장주의 높은 PER 정당화 (`value*0.5 + peg_score*0.5`). 이거 없으면 성장주 가치점수가 0에 붙음
- **미국 재무안정성 프록시**: 부채 데이터가 없어서 순이익률·ROA 기반으로 산출.
  이게 없으면 미국 전종목이 50점 고정되어 **A등급이 구조적으로 불가능**해짐 (실제 겪은 문제)
- **지지/저항**: 단순 min/max 아님. `_support_resistance()` 가 스윙 저점/고점 군집화 후
  **현재가 아래 최근접 지지 / 위 최근접 저항**을 선택하고 `지지 < 현재가 < 저항`을 강제 보장

### 5.5 공개 배포 모드 (`STOCKLENS_PUBLIC=1`, Dockerfile에 내장)
- KIS 키 저장 **403 차단** (낯선 사람이 개인 브로커 키를 서버에 저장 못 하게)
- AI 리포트 **403 차단** (소유자 API 키로 비용 발생 방지)
- IP별 레이트리밋 활성
- 로컬 실행 시엔 이 값이 없어 전 기능 사용 가능

### 5.6 백그라운드 집계 전략 (무료 서버 부하 분산)
| 대상 | 시점 | 주기 | 소요 |
|---|---|---|---|
| 국내 랭킹 | 서버 시작 시 즉시 | 30분 | ~80초 |
| 미국 랭킹 | **첫 요청 시 지연 시작** | 30분 | ~200초 |
| 스크리너 | **첫 요청 시 지연 시작** | 6시간 | ~70초 |
- 집계 중이면 API가 `computing:true` 반환 → 프론트가 5~6초 간격 폴링하며 "집계 중" 표시
- Render 무료는 15분 미접속 시 슬립 → 깨어나면 재집계됨(정상)

---

## 6. 실행 / 테스트 방법

### 로컬 실행
```bash
cd C:\Users\권태균\stocklens
pip install -r requirements.txt
python -m uvicorn main:app --port 8899
```
→ http://127.0.0.1:8899

`start.bat` 더블클릭해도 됨 (브라우저 자동 오픈 + 휴대폰 접속 주소 표시).

**⚠️ `--reload` 없음 → 백엔드(.py) 수정 시 서버를 반드시 재시작해야 반영됨.**

### 선택 기능 활성화 (로컬 전용)
```powershell
# AI 리포트
pip install anthropic
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# KIS 실시간 시세는 화면 우상단 ⚙ 버튼에서 키 입력
```

### API 빠른 테스트
```powershell
$b = "http://127.0.0.1:8899"
Invoke-RestMethod "$b/api/analyze/005930"     # 국내
Invoke-RestMethod "$b/api/analyze/AAPL.O"     # 미국
Invoke-RestMethod "$b/api/ranking?market=US"  # 미국 랭킹(첫 호출은 집계 시작)
Invoke-RestMethod "$b/api/screener"           # 스크리너
```
> PowerShell은 **한글 키 접근이 안 됨**(`$a.total.categories.'가치평가'` → 빈값).
> 부문 점수 확인은 Python 스크립트로 할 것.

### 프론트엔드 검증 (중요)
- **스크린샷 도구는 이 프로젝트에서 계속 타임아웃됨** (외부 CDN 로드 대기 때문).
  → `javascript_tool` 로 **DOM을 직접 eval 검증**할 것. 이 방식은 안정적으로 동작함.
- 예: `document.querySelectorAll('#rank-list .rank-row').length`, `getComputedStyle(...)` 등
- 분석 렌더는 **8~9초** 기다려야 완료됨 (7초는 부족했던 사례 있음)

### 🚨 CSS/JS 수정 시 반드시 캐시 버전 올리기
`static/index.html` 의 `?v=11` 을 **+1** 할 것 (css·js 둘 다).
**이걸 빼먹어서 "고쳤는데 반영이 안 된다"고 여러 번 헤맴.** 현재 v11.

### 배포
```bash
git add -A && git commit -m "..." && git push origin main
```
→ Render 자동 재배포 (2~4분). 확인:
```powershell
(Invoke-WebRequest "https://stocklens-mpr6.onrender.com/" -UseBasicParsing).Content -match 'app\.js\?v=11'
```
> 사용자 기본 기대: **수정 요청 = 로컬 구현에서 끝내지 말고 배포·반영확인까지** 완료.

---

## 7. 알려진 제약 / 미해결

| 항목 | 내용 |
|---|---|
| 미국 ROE·부채비율 | 네이버 미제공 → "-" 표시. 안정성은 프록시로 대체 |
| ~~스크리너 적자기업~~ | ✅ 2026-07-24 해결 — PER 필수 조건 제거로 적자기업 49종목 편입(226→280). 기존 프리셋은 `allowLoss` 없으면 여전히 제외 |
| 백테스트 표본 | ~1년 데이터라 추세주는 완료 매매 1~2회뿐. 화면에 "참고용" 명시함 |
| 스냅샷 영속성 | Render 무료 디스크 비영속 → 과거 데이터 누적/백테스트 불가 |
| 무료 서버 슬립 | 15분 미접속 시 슬립, 재기동 시 첫 로딩 ~50초 + 랭킹 재집계 |
| 데이터 출처 | 네이버 **비공식** API. 스펙 변경 시 파싱 깨질 수 있음(과거 실제 발생) |
