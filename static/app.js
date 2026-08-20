/* StockLens frontend */
const $ = (id) => document.getElementById(id);
let currentCode = null;
let priceTimer = null;
let chart = null;
let watchedCodes = new Set();
let watchMap = {};   // code -> 서버 관심종목 행(added_*·memo·tags·alert_* 등, /api/watch 응답)

/* ---------------- utils ---------------- */
const fmt = (n, digits = 0) =>
  n == null || isNaN(n) ? "-" : Number(n).toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
const won = (n) => (n == null ? "-" : fmt(n) + "원");

/* 통화 대응 가격 포맷 (현재 분석 종목 기준) */
let curCur = "KRW";
function pw(n, cur) {
  cur = cur || curCur;
  if (n == null || isNaN(n)) return "-";
  return cur === "USD"
    ? "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : fmt(n) + "원";
}
function pwRange(a, b) {
  return curCur === "USD" ? `$${fmt(a)}~$${fmt(b)}` : `${fmt(a)}~${fmt(b)}`;
}
function changeStr(chg, rate) {
  if (chg == null) return `${sign(rate, 2)}%`;
  const money = curCur === "USD"
    ? (chg >= 0 ? "+$" : "-$") + Math.abs(chg).toFixed(2)
    : sign(chg) + "원";
  return `${money} (${sign(rate, 2)}%)`;
}

function updownClass(v) {
  if (v == null || v === 0) return "flat";
  return v > 0 ? "up" : "down";
}
function sign(v, digits = 0) {
  if (v == null) return "-";
  return (v > 0 ? "+" : "") + fmt(v, digits);
}
// 값 배열 → 인라인 SVG 미니 추세선(오더플로우 누적 델타처럼 숫자 하나로는 안 보이는
// 흐름을 pro-item 카드 안에 작게 곁들일 때 사용).
function sparklineSvg(values, w = 70, h = 20) {
  if (!values || values.length < 2) return "";
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - lo) / span) * h}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="${up ? "#2ee6a6" : "#ff4d6d"}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
  </svg>`;
}
/* ⚠️ 점수색·판단색은 style="color:..."로 직접 박히기 때문에 CSS의 body.light 변수
   오버라이드가 닿지 않는다 — 라이트 모드에서 이 색들이 흰 배경 위에 그대로 올라가
   명암비 1.70~2.76:1(AA 기준 4.5 미달)로 실측됐다(UI/UX 진단보고서 5장, 등급칩·
   판단배지 등 30곳 이상). 테마별 팔레트를 따로 두고 현재 테마에 맞춰 고른다.
   isLight()를 매번 호출해도 classList 조회라 비용은 무시할 수준. */
function isLight() { return document.body.classList.contains("light"); }
/* CSS 변수 "참조"를 그대로 돌려준다 — style="color:var(--sc-good)"로 박히면 테마를
   바꿀 때 브라우저가 스스로 다시 해석하므로, 다시 렌더링하지 않은 화면(이상징후·
   테마목록 등)의 색까지 자동으로 따라온다. canvas처럼 var()를 못 쓰는 곳은
   아래 cssVar()로 실제 hex를 뽑아 쓸 것. */
function cssVar(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}
function scoreColor(s) {
  return s >= 75 ? "var(--sc-excellent)"
       : s >= 60 ? "var(--sc-good)"
       : s >= 45 ? "var(--sc-fair)"
       :           "var(--sc-poor)";
}
function scoreColorHex(s) {   // canvas 전용 — var() 문자열을 실제 색으로 해석
  return cssVar(s >= 75 ? "--sc-excellent" : s >= 60 ? "--sc-good" : s >= 45 ? "--sc-fair" : "--sc-poor");
}
function verdictColor(tier) {
  // analysis.py VERDICT_TIERS(8단계)와 짝 맞춤 — 기존 5색(#2ee6a6/#f5c518/#4f8cff/#f5a623/#ff4d6d)은
  // 그대로 두고 사이 3단계(strong_buy·watch_buy·watch_sell)에 보간색을 추가했다.
  return {
    strong_buy: "var(--vd-strong-buy)", buy: "var(--vd-buy)", watch_buy: "var(--vd-watch-buy)",
    accumulate: "var(--vd-accumulate)",
    hold: "var(--vd-hold)",
    watch_sell: "var(--vd-watch-sell)", reduce: "var(--vd-reduce)", sell: "var(--vd-sell)",
  }[tier] || "var(--vd-none)";
}
function gradeEmoji(grade) {
  return { S: "🟢", A: "🟢", B: "🟡", C: "🟡", D: "🔴", F: "🔴" }[grade] || "⚪";
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

/* ---------------- theme ---------------- */
const THEME_KEY = "stocklens_theme";
function applyTheme(t, repaint) {
  document.body.classList.toggle("light", t === "light");
  // 이모지 대신 스프라이트 아이콘을 갈아 끼운다(진단보고서 4-3 — 이모지는 OS마다
  // 모양이 달라 톤을 통제할 수 없고 스크린리더가 그대로 읽는다).
  $("theme-btn").innerHTML = `<svg class="i" aria-hidden="true"><use href="#i-${t === "light" ? "sun" : "moon"}"/></svg>`;
  $("theme-btn").setAttribute("aria-label", t === "light" ? "어두운 테마로 전환" : "밝은 테마로 전환");
  if (!repaint) return;
  /* ⚠️ 클래스만 토글하면 CSS 변수를 쓰는 요소만 바뀌고, "그릴 때의 테마 색을 굳혀 버리는"
     것들은 어두운 배경용 색을 그대로 유지한다 — 캔버스로 그린 차트, SVG 게이지,
     style="color:..."로 색을 직접 박는 점수칩·판단배지가 그렇다. 그래서 라이트로
     바꿔도 차트가 다크 테마로 남아 있었다(UI/UX 진단보고서 5장). 화면에 떠 있는
     것만 다시 그린다. */
  const activeTab = document.querySelector("#detail-tabs button.active");
  const tabName = activeTab && activeTab.dataset.tab;
  if (lastAnalysis && !$("report").classList.contains("hidden")) {
    render(lastAnalysis);
    if (tabName) showDetailTab(tabName);   // render()가 "종합"으로 리셋하므로 보던 탭 복구
  }
  if (!$("landing").classList.contains("hidden")) loadRanking(currentSector);
}
function initTheme() {
  applyTheme(localStorage.getItem(THEME_KEY) || "dark", false);
}
$("theme-btn").onclick = () => {
  const t = document.body.classList.contains("light") ? "dark" : "light";
  localStorage.setItem(THEME_KEY, t);
  applyTheme(t, true);
};

/* ---------------- 관심종목 (서버 저장 — ★담기 = 🔔알림과 같은 저장소) ---------------- */
// ⚠️ 예전엔 ★가 localStorage에만, 🔔가 서버(/api/watch)에만 저장돼 로그인해도 기기간
// 동기화가 안 되고 홈 관심종목과 알림 대상이 서로 어긋났다(사용자 지적: 홈엔 떠 있는데
// /api/watch는 빈 배열). 지금은 ★ 하나만 남기고 서버(/api/watch)에 통합 저장한다.
// 로그인이 필요하며(guest는 로그인 모달로 유도), 🔔는 이미 담은 종목의 "옵션"(알림 조건
// 설정)으로 재배치했다 — openWatchSettings() 참고.
const TIER_ORDER = ["strong_buy", "buy", "watch_buy", "accumulate", "hold", "watch_sell", "reduce", "sell"];
const tierRank = (t) => { const i = TIER_ORDER.indexOf(t); return i === -1 ? 4 : i; };

async function addToWatch(code, name, price, score, verdictLabel, verdictTier) {
  await api(`/api/watch/${code}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, price, score, verdict: verdictLabel || null, verdict_tier: verdictTier || null }),
  });
  watchedCodes.add(code);
  await loadWatchlist();
}
async function removeFromWatch(code) {
  await api(`/api/watch/${code}`, { method: "DELETE" });
  watchedCodes.delete(code);
  await loadWatchlist();
}

// ⚠️ 이전 버전은 이름·현재가·AI판단 3개뿐이라 "관심종목을 등록했으면 알고 싶은 것"에
// 정작 답을 못 했다(종합점수·상승여력이 없어 지금 볼 만한지 판단 불가) — 사용자 지적으로
// 실시간 랭킹(.rank-row)·오늘의 PICK(.today-row)과 같은 밀도의 정보형 행으로 다시 짬.
// + "담은 뒤 뭐가 달라졌나"(added_* 스냅샷 대비 변화)와 메모·태그·정리 제안을 추가.
let favExpanded = false;
const WATCH_BUY_TIERS = new Set(["strong_buy", "buy", "watch_buy", "accumulate"]);

/* 관심종목 데이터 준비 — 홈 카드(#fav-board)와 전용 페이지(/watchlist)가 같은 걸 쓴다.
   국내/미국이 섞일 수 있어 두 시장 랭킹을 모두 조회해 매칭한다(둘 다 백그라운드에서
   이미 채점된 데이터라 추가 계산 없음). */
async function fetchWatchData() {
  const items = Object.values(watchMap);
  let byCode = {};
  try {
    const [kr, us] = await Promise.all([api("/api/ranking?market=KR"), api("/api/ranking?market=US")]);
    [...(kr.items || []), ...(us.items || [])].forEach((r) => { byCode[r.code] = r; });
  } catch {}

  // 변화 요약 + 정리 후보(90일 이상 + 점수 하락) 계산 — added_* 스냅샷과 현재값 비교.
  let improved = 0, worsened = 0;
  const staleCandidates = [];
  const nowMs = Date.now();
  items.forEach((it) => {
    const r = byCode[it.code];
    if (!r) return;
    const curTier = (r.ai_verdict || {}).tier;
    if (it.added_verdict_tier && curTier && curTier !== it.added_verdict_tier) {
      if (tierRank(curTier) < tierRank(it.added_verdict_tier)) improved++;
      else worsened++;
    }
    const scoreDiff = it.added_score != null ? r.score - it.added_score : null;
    const ageDays = (nowMs - it.created_at * 1000) / 86400000;
    if (ageDays >= 90 && scoreDiff != null && scoreDiff <= -5) {
      staleCandidates.push({ code: it.code, name: it.name });
    }
  });
  const buyNow = items.filter((it) => {
    const r = byCode[it.code];
    return r && WATCH_BUY_TIERS.has((r.ai_verdict || {}).tier);
  }).length;
  return { items, byCode, improved, worsened, staleCandidates, buyNow };
}

/* 한 줄 요약 — 예전엔 "담은 뒤 판단이 바뀐 종목"이 있을 때만 떴다. 그래서 변화가
   없는 날엔 요약이 통째로 사라져, 매일 들어와 확인할 이유를 못 만들었다(UI/UX
   진단보고서 7장: "요약 헤더 없음"). 지금 몇 종목이 매수 구간인지를 항상 보여준다. */
function watchSummaryHtml(d) {
  // ⚠️ 조각을 join으로 이으면 상향만 있을 때 "1종목은 판단이 상향됐고"처럼 문장이
  // 끊긴 채 끝난다(실제로 그렇게 나왔다). 경우별로 완결된 문장을 만든다.
  let tail = "";
  if (d.improved && d.worsened) tail = `${d.improved}종목은 판단이 상향, ${d.worsened}종목은 하향됐습니다`;
  else if (d.improved) tail = `${d.improved}종목은 판단이 상향됐습니다`;
  else if (d.worsened) tail = `${d.worsened}종목은 판단이 하향됐습니다`;
  const head = d.buyNow
    ? `${d.items.length}종목 중 <b>${d.buyNow}종목이 매수 구간</b>입니다`
    : `${d.items.length}종목 모두 지금은 매수 구간이 아닙니다`;
  return `📌 ${head}${tail ? " · " + tail : "."}`;
}

function favRowHtml(it, r) {
  if (!r) {
    return `<div class="fav-row" data-code="${it.code}">
      <span class="fav-cell-check"><input type="checkbox" class="fav-check" data-code="${it.code}" aria-label="${it.name} 비교 대상으로 선택"></span>
      <span class="fav-judge"></span>
      <span class="fav-name-col"><span class="fav-name">${it.name}</span><small class="hint">${it.code}</small></span>
      <span class="fav-hide-mobile"></span>
      <span class="fav-price-col"><span class="fav-na">데이터 준비 중</span></span>
      <span class="fav-hide-mobile"></span>
      <span class="fav-row-actions"><button class="fav-x" data-x="${it.code}" title="관심종목에서 제거" aria-label="${it.name} 관심종목에서 제거"><svg class="i" aria-hidden="true"><use href="#i-close"/></svg></button></span>
    </div>`;
  }
  const v = r.ai_verdict || {};
  const up = r.upside != null ? `${sign(r.upside, 1)}%` : "-";
  const flag = r.currency === "USD" ? "🇺🇸 " : "";
  const scoreDiff = it.added_score != null ? +(r.score - it.added_score).toFixed(1) : null;
  const priceDiffPct = it.added_price ? (r.price - it.added_price) / it.added_price * 100 : null;
  const changeBits = [];
  if (priceDiffPct != null) changeBits.push(`담은 뒤 <span class="${updownClass(priceDiffPct)}">${sign(priceDiffPct, 1)}%</span>`);
  if (scoreDiff != null) changeBits.push(`점수 ${it.added_score.toFixed(1)} → ${r.score} (${sign(scoreDiff, 1)})`);
  if (it.added_verdict && v.label && it.added_verdict !== v.label) changeBits.push(`판단 ${it.added_verdict} → ${v.label}`);
  const changeLine = changeBits.length ? `<div class="fav-change">${changeBits.join(" · ")}</div>` : "";
  const memoLine = it.memo ? `<div class="fav-memo">📝 ${it.memo}</div>` : "";
  const tagsLine = (it.tags && it.tags.length) ? `<div class="fav-tags">${it.tags.map((t) => `<span class="fav-tag">${t}</span>`).join("")}</div>` : "";
  const alertOn = it.alert_buy || it.alert_price_target != null || it.alert_score_threshold != null || it.alert_verdict_change || it.alert_anomaly;
  return `<div class="fav-row" data-code="${it.code}">
    <span class="fav-cell-check"><input type="checkbox" class="fav-check" data-code="${it.code}" aria-label="${it.name} 비교 대상으로 선택"></span>
    <span class="fav-judge" style="color:${verdictColor(v.tier)}">${v.emoji || ""} ${v.label || "-"}</span>
    <span class="fav-name-col">
      <span class="fav-name">${flag}${r.name}</span><small class="hint">${r.code}</small>
      ${changeLine}${memoLine}${tagsLine}
    </span>
    <span class="fav-score fav-hide-mobile" style="color:${scoreColor(r.score)}">${r.score}</span>
    <span class="fav-price-col">
      <span class="fav-price">${pw(r.price, r.currency)}</span>
      <span class="fav-rate ${updownClass(r.rate)}">${sign(r.rate, 2)}%</span>
    </span>
    <span class="fav-upside fav-hide-mobile ${updownClass(r.upside)}">${up}</span>
    <span class="fav-row-actions">
      <button class="fav-icon-btn ${alertOn ? "on" : ""}" data-gear="${it.code}" title="알림·메모 설정" aria-label="${r.name} 알림·메모 설정"><svg class="i" aria-hidden="true"><use href="#i-gear"/></svg></button>
      <button class="fav-icon-btn" data-buy="${it.code}" title="매수했어요 → 포트폴리오에 등록" aria-label="${r.name} 매수 기록 — 포트폴리오에 등록"><svg class="i" aria-hidden="true"><use href="#i-cart"/></svg></button>
      <button class="fav-x" data-x="${it.code}" title="관심종목에서 제거" aria-label="${r.name} 관심종목에서 제거"><svg class="i" aria-hidden="true"><use href="#i-close"/></svg></button>
    </span>
  </div>`;
}

// 행 클릭·아이콘 버튼 배선 — 홈 카드와 전용 페이지가 같은 걸 쓴다.
function wireFavRows(el, rerender) {
  el.querySelectorAll(".fav-row:not(.fav-row-head)").forEach((row) => {
    row.onclick = (e) => {
      if (e.target.closest(".fav-x, .fav-icon-btn, .fav-check")) return;
      analyze(row.dataset.code);
    };
  });
  el.querySelectorAll(".fav-x").forEach((b) => {
    b.onclick = async (e) => { e.stopPropagation(); await removeFromWatch(b.dataset.x); rerender(); };
  });
  el.querySelectorAll("[data-gear]").forEach((b) => {
    b.onclick = (e) => { e.stopPropagation(); openWatchSettings(b.dataset.gear); };
  });
  el.querySelectorAll("[data-buy]").forEach((b) => {
    b.onclick = (e) => { e.stopPropagation(); buyFromWatch(b.dataset.buy); };
  });
  el.querySelectorAll(".fav-check").forEach((cb) => {
    cb.onclick = (e) => e.stopPropagation();
    cb.onchange = updateFavCmpBar;
  });
  updateFavCmpBar();
}

const FAV_TABLE_HEAD = `<div class="fav-row fav-row-head">
  <span></span><span>판단</span><span>종목</span><span class="fav-hide-mobile">종합점수</span><span>현재가</span><span class="fav-hide-mobile">상승여력</span><span></span>
</div>`;

async function renderFavBoard() {
  const el = $("fav-board");
  if (!currentUser) { el.classList.add("hidden"); return; }
  const items0 = Object.values(watchMap);
  if (!items0.length) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.innerHTML = `<h2>⭐ 관심종목 <button class="sec-link" data-route="/watchlist">전체보기 →</button></h2>
    <p id="fav-summary" class="fav-summary hidden"></p>
    <div id="fav-stale" class="fav-stale hidden"></div>
    <div id="fav-cmp-bar" class="fav-cmp-bar hidden"></div>
    <div class="fav-table">
      ${FAV_TABLE_HEAD}
      <div id="fav-rows"><div class="rank-loading"><div class="spinner sm"></div><span>불러오는 중...</span></div></div>
    </div>
    <button id="fav-more" class="sec-more hidden" aria-expanded="false"></button>`;

  const d = await fetchWatchData();
  const { items, byCode, staleCandidates } = d;

  const summaryEl = $("fav-summary");
  summaryEl.innerHTML = watchSummaryHtml(d);
  summaryEl.classList.remove("hidden");

  if (staleCandidates.length) {
    const staleEl = $("fav-stale");
    staleEl.classList.remove("hidden");
    staleEl.innerHTML = `⏳ ${staleCandidates.map((x) => x.name).join(", ")} — 담은 지 90일 넘었고 점수도 떨어졌어요. `
      + `<button id="fav-stale-clean" class="ghost-btn small">정리하기</button>`;
    $("fav-stale-clean").onclick = async () => {
      if (!confirm(`${staleCandidates.length}개 종목을 관심종목에서 제거할까요?`)) return;
      await Promise.all(staleCandidates.map((x) => removeFromWatch(x.code)));
      renderFavBoard();
    };
  }

  /* 담은 종목이 많으면 홈이 그만큼 길어진다 — 20종목을 담으면 20행이 그대로 나와
     "접기 기능이 없다"는 지적을 받았다(UI/UX 진단보고서 3-1). 5개만 펼치고 접는다.
     전부 보려면 위의 "전체보기"로 전용 페이지(/watchlist)에 간다. */
  const FAV_LIMIT = 5;
  const favShown = favExpanded ? items : items.slice(0, FAV_LIMIT);
  const mb = $("fav-more");
  if (mb) {
    const rest = items.length - favShown.length;
    mb.classList.toggle("hidden", items.length <= FAV_LIMIT);
    mb.textContent = favExpanded ? "접기 ▴" : `관심종목 ${rest}개 더 보기 ▾`;
    mb.setAttribute("aria-expanded", favExpanded ? "true" : "false");
    mb.onclick = () => { favExpanded = !favExpanded; renderFavBoard(); };
  }
  $("fav-rows").innerHTML = favShown.map((it) => favRowHtml(it, byCode[it.code])).join("");
  wireFavRows(el, renderFavBoard);
}

/* ---------------- 관심종목 전용 페이지 (/watchlist) ----------------
   홈 카드엔 5개만 나오고 정렬·필터가 없어 "갈 곳이 없으니 모든 기능이 홈으로
   몰린다"는 지적을 받았다(UI/UX 진단보고서 3-1·7장). 전체 목록 + 정렬 + 필터. */
let watchSort = "added";     // added | score | upside | rate | name
let watchFilter = "all";     // all | buy | alert | up | down

async function showWatchlist() {
  if (!currentUser) { openAuthModal("login"); return; }
  hideAllViews();
  $("watchlist-view").classList.remove("hidden");
  setActiveNav("watchlist");
  window.scrollTo({ top: 0 });
  document.title = "관심종목 — StockLens";
  await renderWatchlistPage();
}

async function renderWatchlistPage() {
  const el = $("watchlist-view");
  const d = await fetchWatchData();
  const { items, byCode } = d;

  if (!items.length) {
    el.innerHTML = `<button class="back-btn" data-route="/">← 홈으로</button>
      <section class="card">
        <h2>⭐ 관심종목</h2>
        <p class="hint-p">아직 담은 종목이 없습니다. 종목 상세 화면에서 ☆를 눌러 담아보세요.</p>
      </section>`;
    return;
  }

  const SORTS = [["added", "담은 순"], ["score", "점수 높은 순"], ["upside", "상승여력 순"],
                 ["rate", "등락률 순"], ["name", "이름 순"]];
  const FILTERS = [["all", "전체"], ["buy", "매수 구간만"], ["alert", "알림 설정한 것만"],
                   ["up", "오늘 상승"], ["down", "오늘 하락"]];

  // 필터 → 정렬 순서로 적용. 랭킹 데이터가 아직 없는 종목(byCode 미매칭)은 맨 뒤로 보낸다.
  let list = items.filter((it) => {
    const r = byCode[it.code];
    if (watchFilter === "buy") return r && WATCH_BUY_TIERS.has((r.ai_verdict || {}).tier);
    if (watchFilter === "alert") return it.alert_buy || it.alert_price_target != null
      || it.alert_score_threshold != null || it.alert_verdict_change || it.alert_anomaly;
    if (watchFilter === "up") return r && r.rate > 0;
    if (watchFilter === "down") return r && r.rate < 0;
    return true;
  });
  const num = (v) => (v == null ? -Infinity : v);
  list.sort((a, b) => {
    const ra = byCode[a.code], rb = byCode[b.code];
    if (!ra && !rb) return 0;
    if (!ra) return 1;
    if (!rb) return -1;
    if (watchSort === "score") return num(rb.score) - num(ra.score);
    if (watchSort === "upside") return num(rb.upside) - num(ra.upside);
    if (watchSort === "rate") return num(rb.rate) - num(ra.rate);
    if (watchSort === "name") return String(ra.name).localeCompare(String(rb.name), "ko");
    return (b.created_at || 0) - (a.created_at || 0);   // added(기본): 최근에 담은 것부터
  });

  el.innerHTML = `<button class="back-btn" data-route="/">← 홈으로</button>
    <section class="card">
      <h2>⭐ 관심종목 <small class="hint">${items.length}종목</small></h2>
      <p class="fav-summary">${watchSummaryHtml(d)}</p>
      <div class="wl-controls">
        <div class="wl-group" role="group" aria-label="정렬">
          <span class="wl-label">정렬</span>
          ${SORTS.map(([k, t]) => `<button class="wl-chip ${watchSort === k ? "active" : ""}" data-sort="${k}">${t}</button>`).join("")}
        </div>
        <div class="wl-group" role="group" aria-label="필터">
          <span class="wl-label">필터</span>
          ${FILTERS.map(([k, t]) => `<button class="wl-chip ${watchFilter === k ? "active" : ""}" data-filter="${k}">${t}</button>`).join("")}
        </div>
      </div>
      <div id="fav-cmp-bar" class="fav-cmp-bar hidden"></div>
      <div class="fav-table">
        ${FAV_TABLE_HEAD}
        <div id="wl-rows">${list.length
          ? list.map((it) => favRowHtml(it, byCode[it.code])).join("")
          : `<p class="hint-p">조건에 맞는 종목이 없습니다.</p>`}</div>
      </div>
    </section>`;

  el.querySelectorAll("[data-sort]").forEach((b) => {
    b.onclick = () => { watchSort = b.dataset.sort; renderWatchlistPage(); };
  });
  el.querySelectorAll("[data-filter]").forEach((b) => {
    b.onclick = () => { watchFilter = b.dataset.filter; renderWatchlistPage(); };
  });
  wireFavRows(el, renderWatchlistPage);
}

// 관심종목 여러 개 체크해 이미 있는 종목비교(⚖️)로 바로 연결 — P3: "비교담기와 연결".
function updateFavCmpBar() {
  const bar = $("fav-cmp-bar");
  if (!bar) return;
  const checked = [...document.querySelectorAll(".fav-check:checked")].map((c) => c.dataset.code);
  // 2개 미만이면 아무것도 안 뜨던 탓에 "체크해도 아무 일이 없다"고 읽혔다(진단보고서 7장).
  // 1개만 골랐을 때도 무엇을 할 수 있는지 알려준다.
  if (checked.length === 1) {
    bar.classList.remove("hidden");
    bar.innerHTML = `1개 선택됨 · 종목을 하나 더 고르면 <b>비교</b>할 수 있어요`;
    return;
  }
  if (!checked.length) { bar.classList.add("hidden"); bar.innerHTML = ""; return; }
  bar.classList.remove("hidden");
  bar.innerHTML = `${checked.length}개 선택됨 (최대 3개) · <button id="fav-cmp-go" class="ghost-btn small">⚖️ 비교하기</button>`;
  $("fav-cmp-go").onclick = async () => {
    const codes = checked.slice(0, 3);
    try {
      const results = await Promise.all(codes.map((c) => api(`/api/analyze/${c}`)));
      compareList = [];
      results.forEach((d) => addCompare(d));
      showCompare();
    } catch (e) { showError("비교 데이터를 불러오지 못했습니다: " + e.message); }
  };
}

// 관심종목 → 포트폴리오: "매수했어요"는 지금까지 완전히 단절돼 있던 두 화면을 잇는다(P3).
async function buyFromWatch(code) {
  const it = watchMap[code];
  if (!it) return;
  if (location.pathname !== "/portfolio") history.pushState(null, "", "/portfolio");
  await showPortfolio();
  const nation = /^\d{6}$/.test(code) ? "KR" : "US";
  pfSelected = { code, name: it.name, nation };
  $("pf-search-input").value = it.name;
  $("pf-add-btn").disabled = false;
  $("pf-price").placeholder = nation === "US" ? "평균단가(달러, 선택)" : "평균단가(원, 선택)";
  $("pf-shares").focus();
}

function updateFavBtn() {
  const b = $("fav-btn");
  const on = currentCode && watchedCodes.has(currentCode);
  b.textContent = on ? "★" : "☆";
  b.classList.toggle("on", on);
}
function updateWatchBtn() {
  const b = $("watch-btn");
  const on = currentCode && watchedCodes.has(currentCode);
  b.classList.toggle("hidden", !on);
  const it = on && watchMap[currentCode];
  const alertOn = it && (it.alert_buy || it.alert_price_target != null || it.alert_score_threshold != null || it.alert_verdict_change || it.alert_anomaly);
  b.classList.toggle("on", !!alertOn);
}

/* ---------------- 관심종목 메모·알림 설정 모달 ---------------- */
let watchModalCode = null;
function openWatchSettings(code) {
  const it = watchMap[code];
  if (!it) return;
  watchModalCode = code;
  $("watch-modal-name").textContent = it.name;
  $("watch-memo").value = it.memo || "";
  $("watch-tags").value = (it.tags || []).join(", ");
  $("watch-alert-buy").checked = !!it.alert_buy;
  $("watch-alert-price-on").checked = it.alert_price_target != null;
  $("watch-alert-price").value = it.alert_price_target != null ? it.alert_price_target : "";
  $("watch-alert-score-on").checked = it.alert_score_threshold != null;
  $("watch-alert-score").value = it.alert_score_threshold != null ? it.alert_score_threshold : "";
  $("watch-alert-verdict").checked = !!it.alert_verdict_change;
  $("watch-alert-anomaly").checked = !!it.alert_anomaly;
  $("watch-modal-msg").textContent = "";
  $("watch-modal").classList.remove("hidden");
}
$("watch-modal-close").onclick = () => $("watch-modal").classList.add("hidden");
$("watch-modal").addEventListener("click", (e) => {
  if (e.target === $("watch-modal")) $("watch-modal").classList.add("hidden");
});
$("watch-modal-save").onclick = async () => {
  if (!watchModalCode) return;
  const msg = $("watch-modal-msg");
  const btn = $("watch-modal-save");
  // 알림 권한 요청은 클릭 직후 다른 await 없이 먼저 호출해야 user-activation을 브라우저가
  // 인정한다(watch-btn 옛 로직과 동일 이유 — 3차 진단리포트 5장 회귀 사례).
  const body = {
    memo: $("watch-memo").value.trim(),
    tags: $("watch-tags").value.split(",").map((s) => s.trim()).filter(Boolean).join(","),
    alert_buy: $("watch-alert-buy").checked,
    alert_price_target: ($("watch-alert-price-on").checked && $("watch-alert-price").value !== "")
      ? Number($("watch-alert-price").value) : null,
    alert_score_threshold: ($("watch-alert-score-on").checked && $("watch-alert-score").value !== "")
      ? Number($("watch-alert-score").value) : null,
    alert_verdict_change: $("watch-alert-verdict").checked,
    alert_anomaly: $("watch-alert-anomaly").checked,
  };
  const anyAlert = body.alert_buy || body.alert_price_target != null || body.alert_score_threshold != null
    || body.alert_verdict_change || body.alert_anomaly;
  let pushWarn = "";
  if (anyAlert && window.isSecureContext && pushSupported() && Notification.permission !== "denied") {
    try { await ensurePushSubscribed(); } catch (e) { pushWarn = ` ⚠️ 알림 권한 설정 실패: ${e.message}`; }
  } else if (anyAlert && Notification.permission === "denied") {
    pushWarn = " ⚠️ 브라우저에서 알림이 차단돼 있어 조건이 충족돼도 알림이 오지 않습니다.";
  }
  btn.disabled = true;
  msg.textContent = "저장 중...";
  try {
    await api(`/api/watch/${watchModalCode}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await loadWatchlist();
    updateWatchBtn();
    renderFavBoard();
    msg.textContent = "저장했습니다." + pushWarn;
    setTimeout(() => $("watch-modal").classList.add("hidden"), pushWarn ? 2000 : 600);
  } catch (e) {
    msg.textContent = "오류: " + e.message;
  } finally {
    btn.disabled = false;
  }
};

/* ---------------- search ---------------- */
const input = $("search-input");
const dropdown = $("search-dropdown");
let searchTimer = null;

input.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = input.value.trim();
  if (!q) { dropdown.classList.add("hidden"); return; }
  searchTimer = setTimeout(async () => {
    try {
      const { items } = await api(`/api/search?q=${encodeURIComponent(q)}`);
      dropdown.innerHTML = "";
      items.forEach((it) => {
        const d = document.createElement("div");
        const flag = it.nation === "US" ? "🇺🇸" : "🇰🇷";
        d.innerHTML = `<b>${flag} ${it.name}</b><small>${it.code} · ${it.market}</small>`;
        d.onclick = () => { dropdown.classList.add("hidden"); input.value = it.name; analyze(it.code); };
        dropdown.appendChild(d);
      });
      dropdown.classList.toggle("hidden", items.length === 0);
    } catch { dropdown.classList.add("hidden"); }
  }, 250);
});
input.addEventListener("keydown", async (e) => {
  if (e.key === "Enter") {
    dropdown.classList.add("hidden");
    const q = input.value.trim();
    if (/^\d{6}$/.test(q)) return analyze(q);
    try {
      const { items } = await api(`/api/search?q=${encodeURIComponent(q)}`);
      if (items.length) analyze(items[0].code);
    } catch {}
  }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) dropdown.classList.add("hidden");
});
/* ---------------- navigation ---------------- */
function setActiveNav(view) {
  document.querySelectorAll("#main-nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
}

/* 화면 전환 — 예전엔 화면을 열 때마다 나머지 6개를 일일이 hidden 처리하는 코드가
   goHome·showPortfolio·showScreener·popstate 등 여러 곳에 복사돼 있었다. 화면을
   하나 추가할 때(이번 /watchlist) 그 목록을 전부 찾아 고쳐야 해서 빠뜨리기 쉽다
   — 한 곳에 모은다. */
const ALL_VIEWS = ["landing", "report", "compare-view", "admin-view",
                   "portfolio-view", "screener-view", "watchlist-view", "loading"];
function hideAllViews() {
  clearInterval(priceTimer);
  ALL_VIEWS.forEach((id) => { const e = $(id); if (e) e.classList.add("hidden"); });
}

function goHome(fromPopstate) {
  currentCode = null;
  hideAllViews();
  $("landing").classList.remove("hidden");
  window.scrollTo({ top: 0 });
  setActiveNav("home");
  renderFavBoard();
  loadRanking(currentSector);
  loadMyPortfolioWidget();
  if (!fromPopstate && location.pathname !== "/") history.pushState(null, "", "/");
  document.title = "StockLens — 주식 종합 분석";
}

document.querySelectorAll("#main-nav button").forEach((b) => {
  b.onclick = () => {
    const view = b.dataset.view;
    // 화면 전환은 전부 navigate()를 거쳐 주소가 함께 바뀌게 한다(북마크·뒤로가기 가능).
    if (view === "home") { navigate("/"); return; }
    if (view === "watchlist") {
      if (!currentUser) { openAuthModal("login"); return; }
      navigate("/watchlist");
      return;
    }
    if (view === "portfolio") {
      if (!currentUser) { openAuthModal("login"); return; }
      navigate("/portfolio");
      return;
    }
    if (view === "screener") { navigate("/screener"); return; }
    if (view === "stock") {
      if (currentCode) {
        clearInterval(priceTimer);
        $("landing").classList.add("hidden");
        $("compare-view").classList.add("hidden");
        $("admin-view").classList.add("hidden");
        $("portfolio-view").classList.add("hidden");
        $("screener-view").classList.add("hidden");
        $("report").classList.remove("hidden");
        setActiveNav("stock");
        priceTimer = setInterval(refreshPrice, 2000);
        window.scrollTo({ top: 0 });
      } else {
        goHome();
        const wrap = $("search-input").closest(".search-wrap");
        $("search-input").focus();
        wrap.classList.remove("pulse-hint");
        void wrap.offsetWidth;   // 리플로우 강제 → 같은 애니메이션 재실행 가능하게
        wrap.classList.add("pulse-hint");
        setTimeout(() => wrap.classList.remove("pulse-hint"), 900);
      }
    }
  };
});


/* ---------------- 홈 3영역 재편 도우미 (UI/UX 진단보고서 3-1) ---------------- */
// 섹션 제목줄(.sec-toggle)을 눌러 본문을 접었다 폈다 한다. 실제 표시/숨김은 CSS가
// aria-expanded 값으로 처리하므로(.sec-toggle[aria-expanded="false"] + .sec-body),
// 여기서는 상태 속성만 바꾸면 된다 — 스크린리더도 같은 값을 읽는다.
function setSectionCollapsed(bodyId, collapsed) {
  // 사용자가 직접 접거나 편 적이 있으면 그 선택을 덮어쓰지 않는다.
  if (bodyId in loadSectionState()) return;
  const btn = document.querySelector(`.sec-toggle[data-toggle="${bodyId}"]`);
  if (btn) btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
}
// 접고 편 상태는 기억한다 — 매번 다시 접히면 "둘러보기" 영역을 즐겨 보는 사용자가
// 매 방문마다 같은 클릭을 반복해야 한다. 기본값은 아래 SECTION_DEFAULT_COLLAPSED.
const SEC_KEY = "stocklens_sections";
const SECTION_DEFAULT_COLLAPSED = { "theme-body": true };   // 테마는 접힌 채 시작(둘러보기 영역)
function loadSectionState() {
  try { return JSON.parse(localStorage.getItem(SEC_KEY)) || {}; } catch { return {}; }
}
function saveSectionState(id, collapsed) {
  const st = loadSectionState();
  st[id] = collapsed;
  try { localStorage.setItem(SEC_KEY, JSON.stringify(st)); } catch {}
}
document.querySelectorAll(".sec-toggle").forEach((btn) => {
  const id = btn.dataset.toggle;
  const saved = loadSectionState();
  const collapsed = id in saved ? saved[id] : !!SECTION_DEFAULT_COLLAPSED[id];
  btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  btn.onclick = () => {
    const open = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", open ? "false" : "true");
    saveSectionState(id, open);   // open이었다면 이제 접힌 상태
  };
});

// 히어로는 로그인 사용자에게 숨긴다 — 재방문자에게 첫 화면의 절반이 마케팅 문구였다.
function syncHomeForUser() {
  const hero = $("hero");
  if (hero) hero.classList.toggle("hidden", !!currentUser);
  // ⚠️ "내 투자" 영역을 통째로 숨기면 안 된다 — 로그아웃 상태에서도 이 안의
  // #mypf-board가 로그인 유도 카드를 띄우고 있어서, 같이 사라지면 진입점이 없어진다.
  // 라벨만 감춘다(담은 종목도 포트폴리오도 없는데 "내 투자"라고 쓰면 어색하므로).
  const label = document.querySelector("#group-mine .home-group-label");
  if (label) label.classList.toggle("hidden", !currentUser);
}

/* ---------------- 점수 백테스트 ---------------- */
// 진단리포트 9장: "점수의 과거 성과를 상시 공개하는 것이 유료화의 결정적 근거".
// 과거 시점 데이터를 재구성할 방법이 없어(재무·컨센서스·기술지표 모두 "현재값"만 제공)
// 진짜 과거 백테스트는 불가능 — 대신 오늘부터 매일 스냅샷을 쌓아 실제 결과만 보여준다.
async function loadBacktest() {
  try {
    const d = await api("/api/backtest");
    if (!d.available) {
      document.getElementById("bt-body").innerHTML =
        `<div class="bt-empty">📅 오늘부터 점수 추적을 시작했습니다. 매일 자동으로 종가를 기록하고,
         1주일 뒤부터 이 자리에 실제 수익률이 표시됩니다. 과거 데이터를 흉내 내지 않고
         정직하게 실현되는 대로만 보여드립니다.</div>`;
      setSectionCollapsed("bt-body-wrap", true);
      const note0 = document.getElementById("bt-toggle-note");
      if (note0) note0.textContent = "추적 시작 단계 — 아직 결과 없음";
      return;
    }
    document.getElementById("bt-sub").textContent =
      `추적 시작 ${d.start_date} · ${d.days_collected}일째 매일 실제 종가로 기록 중 · 마지막 기록 ${d.latest_date}`;
    /* 아직 어느 기간도 결과가 없으면 "🔒 데이터 부족" 카드 다섯 개가 401px를 그대로
       차지한다(진단보고서 3-1). 그럴 땐 접어 두고 제목줄에 진행 상황만 한 줄로 남긴다. */
    const anyReady = d.periods.some((p) => p.available);
    setSectionCollapsed("bt-body-wrap", !anyReady);
    const note = document.getElementById("bt-toggle-note");
    if (note) note.textContent = anyReady ? "" : `추적 ${d.days_collected}일째 — 1주일 뒤부터 결과 표시`;
    document.getElementById("bt-body").innerHTML = `<div class="bt-grid">${d.periods.map((p) => {
      if (!p.available) {
        return `<div class="bt-period bt-period-locked">
          <div class="bt-period-label">${p.label}</div>
          <div class="bt-period-locked-msg">🔒 아직 데이터 부족<br><small>추적 ${d.days_collected}/${p.days}일째</small></div>
        </div>`;
      }
      const rows = p.buckets.filter((b) => b.count > 0).map((b) => `
        <div class="bt-row">
          <span class="bt-grade">${b.grade}</span>
          <span class="bt-n">${b.count}종목</span>
          <span class="bt-ret ${b.avg_return >= 0 ? "up" : "down"}">${sign(b.avg_return, 1)}%</span>
          <span class="bt-win">승률 ${b.win_rate}%</span>
          ${b.excess_vs_bench != null ? `<span class="bt-excess ${b.excess_vs_bench >= 0 ? "up" : "down"}">지수대비 ${sign(b.excess_vs_bench, 1)}%p</span>` : ""}
        </div>`).join("");
      return `<div class="bt-period">
        <div class="bt-period-label">${p.label} <small>(${p.base_date} 기준 ${p.sample_size}종목)</small></div>
        ${rows || `<p class="hint-p">표본 부족</p>`}
      </div>`;
    }).join("")}</div>`;
  } catch {
    document.getElementById("bt-body").innerHTML = `<p class="hint-p">불러오지 못했습니다.</p>`;
  }
}

/* ---------------- 스크리너 ---------------- */
// 진단리포트 P1 지적사항: "현재 랭킹 필터는 섹터 선택뿐입니다. PER 10배 이하 + ROE 15%
// 이상 + 외국인 5일 연속 순매수 같은 조건 조합이 유료 전환의 1순위 사유입니다."
// app/ranking.py가 이미 백그라운드로 채점해 캐시해둔 지표만 쓰므로 추가 분석 호출이 없다.
let screenerSectorsLoaded = false;

async function showScreener() {
  hideAllViews();
  $("screener-view").classList.remove("hidden");
  setActiveNav("screener");
  window.scrollTo({ top: 0 });
  document.title = "스크리너 — StockLens";
  if (!screenerSectorsLoaded) {
    screenerSectorsLoaded = true;
    try {
      const [kr, us] = await Promise.all([api("/api/ranking?market=KR"), api("/api/ranking?market=US")]);
      const sectors = [...new Set([...(kr.sectors || []), ...(us.sectors || [])])].sort();
      $("scr-sector").innerHTML = `<option value="전체">전체</option>` +
        sectors.map((s) => `<option value="${s}">${s}</option>`).join("");
    } catch { /* 섹터 목록 실패해도 "전체"로는 검색 가능하니 조용히 무시 */ }
  }
}

async function runScreener() {
  const q = new URLSearchParams();
  q.set("market", $("scr-market").value);
  if ($("scr-sector").value && $("scr-sector").value !== "전체") q.set("sector", $("scr-sector").value);
  if ($("scr-grade").value) q.set("grade_min", $("scr-grade").value);
  const numField = (id, key) => { const v = $(id).value; if (v !== "") q.set(key, v); };
  numField("scr-score-min", "score_min");
  numField("scr-per-max", "per_max");
  numField("scr-pbr-max", "pbr_max");
  numField("scr-roe-min", "roe_min");
  numField("scr-debt-max", "debt_max");
  numField("scr-div-min", "div_min");
  numField("scr-upside-min", "upside_min");
  if ($("scr-foreign-buy").checked) q.set("foreign_buy", "true");

  $("scr-results").innerHTML = `<div class="rank-loading"><div class="spinner sm"></div><span>조건에 맞는 종목을 찾는 중…</span></div>`;
  $("scr-count").textContent = "";
  try {
    const d = await api(`/api/screener?${q.toString()}`);
    $("scr-count").textContent = `${d.universe_size}종목 중 ${d.total_matched}개 일치` + (d.total_matched > d.items.length ? ` (상위 ${d.items.length}개 표시)` : "");
    if (!d.items.length) {
      $("scr-results").innerHTML = `<p class="hint-p">조건에 맞는 종목이 없습니다. 조건을 완화해보세요.</p>`;
      return;
    }
    $("scr-results").innerHTML = d.items.map((r) => {
      const col = scoreColor(r.score);
      const up = r.upside != null ? `${sign(r.upside, 0)}%` : "-";
      const flag = r.currency === "USD" ? "🇺🇸" : "🇰🇷";
      return `
      <div class="rank-row" data-code="${r.code}">
        <div class="rank-num" style="color:${col}">${r.grade}</div>
        <div class="rank-info">
          <div class="rank-name">${flag} ${r.name}</div>
          <div class="rank-sector">${r.sector} · ${r.code}<span class="scr-extra">${r.per != null ? ` · PER ${fmt(r.per, 1)}배` : ""}${r.roe != null ? ` · ROE ${fmt(r.roe, 1)}%` : ""}</span></div>
        </div>
        <div class="rank-price">
          <div class="p">${pw(r.price, r.currency)}</div>
          <div class="r ${updownClass(r.rate)}">${sign(r.rate, 2)}%</div>
        </div>
        <div class="rank-score-chip" style="color:${col};background:color-mix(in srgb, ${col} 13%, transparent)">${r.score}</div>
        <div class="rank-tail">
          <div class="rank-grade" style="color:${col}">${r.grade}등급</div>
          <div class="rank-upside">목표가 ${up}</div>
          <div class="rank-bar"><i style="width:${r.score}%;background:${col}"></i></div>
        </div>
      </div>`;
    }).join("");
    $("scr-results").querySelectorAll(".rank-row").forEach((row) => {
      row.onclick = () => analyze(row.dataset.code);
    });
  } catch (e) {
    $("scr-results").innerHTML = `<p class="hint-p">검색 실패: ${e.message}</p>`;
  }
}

function resetScreener() {
  $("scr-form").querySelectorAll("input").forEach((el) => { el.type === "checkbox" ? (el.checked = false) : (el.value = ""); });
  $("scr-market").value = "전체";
  $("scr-sector").value = "전체";
  $("scr-grade").value = "";
  $("scr-count").textContent = "";
  $("scr-results").innerHTML = `<p class="hint-p">조건을 설정하고 "조건으로 검색"을 눌러주세요.</p>`;
}

$("scr-back").onclick = goHome;
$("scr-run").onclick = runScreener;
$("scr-reset").onclick = resetScreener;

/* ---------------- 이상징후 탐지 ---------------- */
let anomalyExpanded = false;
async function loadAnomalies() {
  try {
    const r = await api("/api/anomalies");
    const cards = [
      ...r.bull.map((it) => ({ ...it, kind: "bull" })),
      ...r.bear.map((it) => ({ ...it, kind: "bear" })),
    ];
    if (!cards.length) { $("anomaly-board").classList.add("hidden"); return; }
    $("anomaly-board").classList.remove("hidden");
    /* 카드가 많으면 홈에서만 782px를 잡아먹었다(진단보고서 3-1) — 상위 3개만 펼치고
       나머지는 "더 보기"로 접는다. 목록 자체는 그대로 두고 노출 개수만 조절. */
    const ANOMALY_LIMIT = 3;
    const shown = anomalyExpanded ? cards : cards.slice(0, ANOMALY_LIMIT);
    const moreBtn = $("anomaly-more");
    if (moreBtn) {
      const rest = cards.length - shown.length;
      moreBtn.classList.toggle("hidden", cards.length <= ANOMALY_LIMIT);
      moreBtn.textContent = anomalyExpanded ? "접기 ▴" : `${rest}개 더 보기 ▾`;
      moreBtn.setAttribute("aria-expanded", anomalyExpanded ? "true" : "false");
      moreBtn.onclick = () => { anomalyExpanded = !anomalyExpanded; loadAnomalies(); };
    }
    $("anomaly-list").innerHTML = shown.map((it) => {
      const flag = it.currency === "USD" ? "🇺🇸" : "🇰🇷";
      const bull = it.kind === "bull";
      return `
      <div class="anomaly-card ${it.kind}" data-code="${it.code}">
        <div class="an-head">
          <span class="an-tag">${bull ? "🔥 저평가 확대" : "⚠️ 단기 과열"}</span>
          <span class="an-name">${flag} ${it.name}</span>
          <span class="an-score" style="color:${scoreColor(it.score)}">${it.score}점</span>
        </div>
        <ul class="an-reasons">${it.anomaly_reasons.map((r2) => `<li>${r2}</li>`).join("")}</ul>
      </div>`;
    }).join("");
    $("anomaly-list").querySelectorAll(".anomaly-card").forEach((card) => {
      card.onclick = () => analyze(card.dataset.code);
    });
  } catch {
    $("anomaly-board").classList.add("hidden");
  }
}

/* ---------------- 테마·산업 ---------------- */
let currentTheme = null;

async function loadThemeChips() {
  try {
    const { themes } = await api("/api/themes");
    $("theme-chips").innerHTML = themes.map((t) =>
      `<button data-theme="${t}" class="${t === currentTheme ? "active" : ""}">${t}</button>`).join("");
    $("theme-chips").querySelectorAll("button").forEach((b) => {
      b.onclick = () => selectTheme(b.dataset.theme);
    });
    // 첫 진입 시 칩만 뜨고 결과는 빈 채로 남던 문제(진단리포트 지적) — 첫 테마를 기본 선택해둔다.
    if (themes.length && !currentTheme) selectTheme(themes[0]);
  } catch {}
}

async function selectTheme(name) {
  currentTheme = name;
  $("theme-chips").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.theme === name));
  const box = $("theme-result");
  box.classList.remove("hidden");
  box.innerHTML = `<div class="rank-loading"><div class="spinner sm"></div><span>${name} 분석 중…</span></div>`;
  try {
    const t = await api(`/api/themes/${encodeURIComponent(name)}`);
    if (!t.items.length) {
      box.innerHTML = `<div class="rank-loading"><span>아직 데이터가 준비되지 않았습니다. 잠시 후 다시 시도해주세요.</span></div>`;
      return;
    }
    const rowsHtml = t.items.map((r) => {
      const rank = r.theme_rank;
      const medal = rank <= 3 ? `top g${rank}` : "";
      const col = scoreColor(r.score);
      const up = r.upside != null ? `${sign(r.upside, 0)}%` : "-";
      const flag = r.currency === "USD" ? "🇺🇸" : "🇰🇷";
      return `
      <div class="rank-row" data-code="${r.code}">
        <div class="rank-num ${medal}">${rank}</div>
        <div class="rank-info">
          <div class="rank-name">${flag} ${r.name}</div>
          <div class="rank-sector">${r.sector} · ${r.code}</div>
        </div>
        <div class="rank-price">
          <div class="p">${pw(r.price, r.currency)}</div>
          <div class="r ${updownClass(r.rate)}">${sign(r.rate, 2)}%</div>
        </div>
        <div class="rank-score-chip" style="color:${col};background:color-mix(in srgb, ${col} 13%, transparent)">${r.score}</div>
        <div class="rank-tail">
          <div class="rank-grade" style="color:${col}">${r.grade}등급</div>
          <div class="rank-upside">목표가 ${up}</div>
          <div class="rank-bar"><i style="width:${r.score}%;background:${col}"></i></div>
        </div>
      </div>`;
    }).join("");
    const missingNote = t.missing > 0
      ? `<p class="hint-p">${t.missing}개 종목은 아직 랭킹 집계 중이라 빠져 있습니다 — 잠시 후 다시 확인해보세요.</p>` : "";
    box.innerHTML = rowsHtml + missingNote;
    box.querySelectorAll(".rank-row").forEach((row) => { row.onclick = () => analyze(row.dataset.code); });
  } catch (e) {
    box.innerHTML = `<div class="rank-loading"><span>불러오기 실패: ${e.message}</span></div>`;
  }
}

/* ---------------- ranking board ---------------- */
let currentSector = "전체";
let currentMarket = "KR";
let rankPollTimer = null;

async function loadRanking(sector = "전체") {
  currentSector = sector;
  try {
    const qs = `?market=${currentMarket}` + (sector && sector !== "전체" ? `&sector=${encodeURIComponent(sector)}` : "");
    const d = await api(`/api/ranking${qs}`);
    renderRankFilters(d.sectors);
    if (!d.items || d.items.length === 0) {
      $("rank-list").innerHTML = `<div class="rank-loading"><div class="spinner sm"></div><span>랭킹 집계 중… (종목이 많아 몇 분 걸릴 수 있어요, 이 화면에서 자동으로 채워집니다)</span></div>`;
    } else {
      // 계산이 끝나기 전이라도 완료된 종목부터 순위표를 보여준다(부분 결과).
      renderRanking(d);
    }
    // 백엔드가 아직 채점 중이면(부분 결과 포함) 계속 폴링해서 자동으로 채워나간다.
    clearTimeout(rankPollTimer);
    if (d.computing) {
      rankPollTimer = setTimeout(() => loadRanking(currentSector), 5000);
    }
  } catch {
    $("rank-list").innerHTML = `<div class="rank-loading"><span>랭킹을 불러오지 못했습니다.</span></div>`;
  }
}

const SECTOR_CHIP_LIMIT = 8;   // 섹터 칩 20개가 두 줄을 채워 훑어보기 어려웠다(진단보고서 3-1)
let sectorChipsExpanded = false;
function renderRankFilters(sectors) {
  if (!sectors || !sectors.length) return;
  const all = ["전체", ...sectors];
  // 현재 선택한 섹터가 잘려 나가면 안 되므로 항상 보이도록 앞으로 당긴다.
  const visible = sectorChipsExpanded ? all : (() => {
    const head = all.slice(0, SECTOR_CHIP_LIMIT);
    if (currentSector && !head.includes(currentSector)) head[head.length - 1] = currentSector;
    return head;
  })();
  $("rank-filters").innerHTML = visible.map((s) =>
    `<button class="${s === currentSector ? "active" : ""}" data-sector="${s}">${s}</button>`).join("");
  document.querySelectorAll("#rank-filters button").forEach((b) => {
    b.onclick = () => loadRanking(b.dataset.sector);
  });
  const more = $("rank-filters-more");
  if (more) {
    const hiddenCount = all.length - visible.length;
    more.classList.toggle("hidden", hiddenCount <= 0 && !sectorChipsExpanded);
    more.textContent = sectorChipsExpanded ? "섹터 접기 ▴" : `섹터 ${hiddenCount}개 더 보기 ▾`;
    more.setAttribute("aria-expanded", sectorChipsExpanded ? "true" : "false");
    more.onclick = () => { sectorChipsExpanded = !sectorChipsExpanded; renderRankFilters(sectors); };
  }
}

let rankAll = [];
const RANK_STEP = 10;
let rankShown = 5;

function renderRanking(d) {
  if (d.updated_at) {
    const dt = new Date(d.updated_at * 1000);
    const suffix = d.computing ? ` · 집계 중(${d.items.length}종목 반영됨)…` : "";
    $("rank-updated").textContent = `· ${dt.getHours()}시 ${String(dt.getMinutes()).padStart(2, "0")}분 기준${suffix}`;
  }
  if (!d.items.length) {
    $("rank-list").innerHTML = `<div class="rank-loading"><span>해당 섹터 데이터가 없습니다.</span></div>`;
    return;
  }
  rankAll = d.items;
  rankShown = 5;
  paintRanking();
  renderTodayPick(d.items);
}

/* ---------------- 오늘의 AI 투자기회 (홈) ---------------- */
const CATEGORY_ICON = { "가치평가": "💰", "수익성": "📈", "성장성": "🚀", "재무안정성": "🛡️", "기술적추세": "📊", "수급·심리": "🌊" };

function josa(word, withBatchim, withoutBatchim) {
  const code = (word || "").charCodeAt((word || "").length - 1) - 0xAC00;
  if (code < 0 || code > 11171) return withoutBatchim;
  return code % 28 === 0 ? withoutBatchim : withBatchim;
}

function directionTag(score) {
  if (score >= 80) return { text: "매우 긍정", arrow: "▲▲", cls: "dir-strong-up" };
  if (score >= 62) return { text: "긍정", arrow: "▲", cls: "dir-up" };
  if (score >= 42) return { text: "보통", arrow: "─", cls: "dir-flat" };
  if (score >= 25) return { text: "부정", arrow: "▼", cls: "dir-down" };
  return { text: "매우 부정", arrow: "▼▼", cls: "dir-strong-down" };
}

// 실시간 랭킹(순수 종합점수 순)과 차별화된 "지금이 진입 타이밍인가"용 재정렬.
// 랭킹 그대로 top5를 보여주면 아래 실시간 랭킹표와 완전히 같은 리스트가 되어버리므로,
// 종합점수 상위 후보군(20개) 안에서 밸류·과열여부·수급까지 반영해 다시 추린다.
// 백엔드 추가 호출 없이 /api/ranking이 이미 주는 필드(upside·rsi·per_ratio·foreign_dir·
// op_growth_fwd)만으로 계산 — 랭킹 백그라운드 루프에 무거운 계산을 얹지 않는다(CLAUDE.md 규칙).
// ⚠️ "상승여력(목표주가 기준)이 높다"만으로 사유를 잡으면, 업데이트가 오래된 애널리스트
// 리포트가 그대로 근거가 될 수 있다(사용자 지적: "과거 애널리스트 분석이 업데이트 안
// 됐을 수도 있는데"). 대신 더 "단단한"(hard) 신호 — 최근 실제 수급(외국인 연속 순매수),
// 과거/선행 PER 대비 저평가, 실적전망 개선이 아직 주가에 반영 안 된 괴리 — 를 우선하고
// 목표주가 상승여력은 비중을 크게 낮춰 보조 신호로만 쓴다.
function opportunityScore(r) {
  let s = r.score;
  if (r.upside != null) s += Math.max(-10, Math.min(20, r.upside)) * 0.1;
  if (r.rsi != null) {
    if (r.rsi >= 78) s -= 12;
    else if (r.rsi >= 70) s -= 6;
    else if (r.rsi <= 35) s += 6;
    else if (r.rsi <= 45) s += 3;
  }
  if (r.per_ratio != null) {
    if (r.per_ratio <= 0.8) s += 10;
    else if (r.per_ratio <= 1.0) s += 4;
    else if (r.per_ratio >= 1.3) s -= 6;
  }
  if (r.foreign_dir === "buy") s += 8;
  else if (r.foreign_dir === "sell") s -= 5;
  if (r.op_growth_fwd != null && r.op_growth_fwd >= 20) {
    s += 6;
    // 실적전망은 개선되는데(op_growth_fwd) 아직 PER이 그만큼 재평가(re-rating)되지
    // 않았다면(per_ratio<=1.0) "개선이 주가에 아직 안 반영된" 상태로 보고 추가 가점.
    if (r.per_ratio != null && r.per_ratio <= 1.0) s += 5;
  }
  return s;
}

function opportunityReasons(r) {
  const reasons = [];
  const perLabel = r.per_basis === "fwd" ? "선행PER" : "PER";
  const growthUnpriced = r.op_growth_fwd != null && r.op_growth_fwd >= 20
    && r.per_ratio != null && r.per_ratio <= 1.0;
  // op_growth_fwd는 저기반(적자→흑자) 회복 시 수백%까지 왜곡될 수 있어 표시는 50%로 캡(anomaly.py와 동일 관례).
  if (growthUnpriced) {
    const g = Math.min(r.op_growth_fwd, 50);
    reasons.push({ txt: `실적전망 +${Math.round(g)}%인데 ${perLabel}엔 아직 미반영`, w: 100 + g });
  }
  if (r.foreign_dir === "buy") reasons.push({ txt: "외국인 5일 연속 순매수(수급 유입)", w: 90 });
  if (r.per_ratio != null && r.per_ratio <= 0.85) {
    reasons.push({ txt: `${perLabel} 과거 평균보다 ${Math.round((1 - r.per_ratio) * 100)}% 낮음`, w: 80 + (1 - r.per_ratio) * 40 });
  }
  if (r.rsi != null && r.rsi <= 40) reasons.push({ txt: `RSI ${r.rsi.toFixed(0)} 과매도권`, w: 40 - r.rsi + 15 });
  if (!growthUnpriced && r.op_growth_fwd != null && r.op_growth_fwd >= 20) {
    const g = Math.min(r.op_growth_fwd, 50);
    reasons.push({ txt: `실적전망 +${Math.round(g)}%`, w: g * 0.3 });
  }
  // 목표주가 상승여력은 가장 낮은 우선순위 — 다른 신호가 없을 때만 보조로 노출하고,
  // "목표주가 기준"임을 명시해 다른 신호(수급·저평가 등)와 근거 성격이 다름을 드러낸다.
  if (r.upside != null && r.upside >= 20) {
    reasons.push({ txt: `컨센서스 목표가 기준 상승여력 +${Math.round(r.upside)}%`, w: r.upside * 0.15 });
  }
  reasons.sort((a, b) => b.w - a.w);
  return reasons.slice(0, 2).map((x) => x.txt);
}

// ⚠️ "지금 사기 좋은 종목"인데 AI 판단이 '매수 관심'·'분할 매수' 같은 약한 tier인 종목이
// '적극 매수'보다 위에 뜨는 문제가 있었다(사용자 지적) — opportunityScore가 tier를 전혀
// 안 보고 상승여력·RSI·PER만 재조합해서, 종합점수 상위 20개(items.slice(0,20)) 안의
// 약한 tier 종목이 강한 tier 종목을 역전할 수 있었다. 게다가 그 top-20 제한 자체가
// 후보군을 너무 좁혀서, 실제로는 코스피 전체에 매수/적극매수 tier가 28개나 있는데도
// (score 상위 20위 밖이라는 이유만으로) 셀트리온 1개만 노출되는 경우가 실측됐다.
// AI 판단이 매수/적극매수인 종목을 전체 유니버스에서 먼저 추리고, 그 안에서만
// opportunityScore로 순위를 매긴다.
const OPP_BUY_TIERS = new Set(["strong_buy", "buy"]);
function pickOpportunities(items) {
  const notOverheated = (r) => !(r.rsi != null && r.rsi >= 78);   // 극단적 과열은 "지금이 기회"와 안 맞음
  const strong = items.filter((r) => OPP_BUY_TIERS.has((r.ai_verdict || {}).tier) && notOverheated(r));
  if (strong.length) {
    return [...strong].sort((a, b) => opportunityScore(b) - opportunityScore(a)).slice(0, 5);
  }
  // 매수/적극매수 tier가 하나도 없는 예외적 약세장 등엔 기존처럼 상위권에서 폴백한다
  // (완전히 빈 화면보다는 낫다 — 다만 이 경로에선 tier가 약할 수 있음을 감안할 것).
  const pool = items.slice(0, 20).filter(notOverheated);
  const base = pool.length ? pool : items.slice(0, 5);
  return [...base].sort((a, b) => opportunityScore(b) - opportunityScore(a)).slice(0, 5);
}

async function renderTodayPick(items) {
  const board = $("today-board");
  if (!items || !items.length) { board.classList.add("hidden"); return; }
  board.classList.remove("hidden");
  const top = pickOpportunities(items);
  const best = top[0];
  const bv = best.ai_verdict || {};
  const bestUp = best.upside != null ? `${sign(best.upside, 1)}%` : "-";
  $("today-hero").innerHTML = `
    <div class="today-pick-label">🔥 TODAY'S PICK</div>
    <button class="today-pick-fav" id="today-pick-fav" title="관심종목에 추가/제거">☆</button>
    <div class="today-pick-head">
      <div class="today-pick-name">${best.name} <small>${best.code}</small></div>
      <div class="today-pick-meta">
        <span class="today-pick-price">${pw(best.price, best.currency)}</span>
        <span class="today-pick-score">종합점수 ${best.score}</span>
        <span class="today-pick-verdict" style="color:${verdictColor(bv.tier)}">${bv.emoji || ""} ${bv.label || ""} · 신뢰도 ${bv.confidence ?? "-"}</span>
      </div>
    </div>
    <div class="today-pick-stats">
      <div><label>매수 적정가</label><span id="today-pick-fair">불러오는 중…</span></div>
      <div><label>적정가치 <small class="hint">밸류에이션</small></label><span id="today-pick-fairvalue">불러오는 중…</span></div>
      <div><label>컨센서스 목표가</label><span id="today-pick-target">${best.target_price ? pw(best.target_price, best.currency) : "-"}</span></div>
      <div><label>상승여력</label><span id="today-pick-upside" class="${updownClass(best.upside)}">${bestUp}</span></div>
    </div>
    <button class="primary-btn today-pick-btn" id="today-pick-btn">종목 자세히 보기</button>`;
  $("today-pick-btn").onclick = () => analyze(best.code);

  // 관심종목 담기 — 오늘의 PICK에서 바로 서버 관심종목에 담을 수 있게(로그인 필요).
  const favBtn = $("today-pick-fav");
  const syncFavBtn = () => {
    const on = watchedCodes.has(best.code);
    favBtn.textContent = on ? "★" : "☆";
    favBtn.classList.toggle("on", on);
  };
  syncFavBtn();
  favBtn.onclick = async (e) => {
    e.stopPropagation();
    if (!currentUser) { openAuthModal("login"); return; }
    favBtn.disabled = true;
    try {
      if (watchedCodes.has(best.code)) {
        await removeFromWatch(best.code);
      } else {
        await addToWatch(best.code, best.name, best.price, best.score, bv.label, bv.tier);
      }
      syncFavBtn();
      renderFavBoard();
    } catch (err) {
      showError(err.message);
    } finally {
      favBtn.disabled = false;
    }
  };

  // ⚠️ "종합점수 ≠ 투자기회" 범례 칩과 "지금 사기 좋은 종목"이라는 짧은 태그라인만 있고 왜
  // 이 종목이 오늘의 PICK인지 구체적 근거가 없었다(사용자 지적) — opportunityReasons()로
  // 이미 계산된 근거(상승여력·RSI·PER저평가·수급·실적전망)를 문장으로 풀어 보여준다.
  const pickReasons = opportunityReasons(best);
  const reasonSentence = pickReasons.length ? pickReasons.join(", ") : "종합점수와 최근 가격 흐름";
  const cats = Object.entries(best.categories || {});
  const posCount = cats.filter(([, s]) => s >= 62).length;
  const introText = `${best.name}${josa(best.name, "은", "는")} <b>${reasonSentence}</b>${josa(reasonSentence.split(", ").pop(), "이", "가")} 겹쳐 오늘의 AI 투자기회로 선정됐습니다. `
    + `6개 분석 부문 중 ${posCount}개에서 긍정 신호가 나왔고, AI 최종판단은 '${bv.label || "-"}'`
    + `(판단 신뢰도 ${bv.confidence ?? "-"})입니다.`;

  // "왜 주목하는가" — 이미 계산된 6개 부문점수 + RSI를 그대로 재사용(추가 계산 없음)
  const reasonsHtml = cats.map(([name, score]) => {
    const d = directionTag(score);
    return `<div class="today-why-row ${d.cls}"><span class="today-why-name">${CATEGORY_ICON[name] || "•"} ${name}</span><span class="today-why-dir">${d.arrow} ${d.text}</span></div>`;
  }).join("");
  const overheatWarn = best.rsi != null && best.rsi >= 70
    ? `<div class="today-why-warn">⚠️ 단기 과열 가능성 (RSI ${best.rsi.toFixed(0)})</div>` : "";
  $("today-why").innerHTML = `<div class="today-why-title">AI가 오늘 ${best.name}${josa(best.name, "을", "를")} 주목한 이유</div>`
    + `<p class="today-why-intro">${introText}</p>${reasonsHtml}${overheatWarn}`;

  $("today-rows").innerHTML = top.map((r) => {
    const v = r.ai_verdict || {};
    const up = r.upside != null ? `${sign(r.upside, 1)}%` : "-";
    const reasons = opportunityReasons(r);
    const reasonText = reasons.length ? reasons.join(" · ") : "-";
    return `<div class="today-row" data-code="${r.code}">
      <span class="today-judge" style="color:${verdictColor(v.tier)}">${v.emoji || ""} ${v.label || "-"}</span>
      <span class="today-name">${r.name}</span>
      <span class="today-price">${pw(r.price, r.currency)}</span>
      <span class="today-upside ${updownClass(r.upside)}">${up}</span>
      <span class="today-reason">${reasonText}</span>
    </div>`;
  }).join("");
  $("today-rows").querySelectorAll(".today-row").forEach((row) => {
    row.onclick = () => analyze(row.dataset.code);
  });

  // 적정매수가·밸류에이션 상세는 랭킹 백그라운드 채점에 없음(peers 조회 등 비용 때문에
  // 의도적으로 생략) — TODAY'S PICK 1종목에 한해서만 추가로 조회한다.
  try {
    const d = await api(`/api/analyze/${best.code}`);
    const val = d.valuation;
    const fb = (val && val.available) ? val.fair_buy : null;
    const cons = d.consensus || {};

    $("today-pick-fair").textContent = fb ? pw(fb.base.price, best.currency) : "-";
    $("today-pick-fairvalue").textContent = fb ? pw(fb.fair_value, best.currency) : "-";
    $("today-pick-target").innerHTML = cons.target_price
      ? pw(cons.target_price, best.currency) + (cons.date ? ` <small class="hint">(${cons.date} 기준)</small>` : "")
      : "-";

    // ⚠️ "상승여력이 그냥 애널리스트 목표가 평균이라 의미 없어 보인다"는 지적 — 목표가는
    // 후행 경향이 있어(analysis.py 주석 참고) 그것만 단독으로 보여주면 근거가 약하다.
    // 우리가 직접 계산한 밸류에이션 적정가(fair_value) 기준 상승여력을 주 지표로 쓰고,
    // 애널리스트 목표가 기준은 참고치로 함께 보여준다 — 둘이 크게 다르면 그 자체가
    // "시장 기대 vs 우리 모델"의 괴리라는 정보가 된다. 괴리가 과대해 반영비중이 낮아진
    // 경우(consensus_info.upside_flagged, 2차 진단리포트 4-1 조치)는 배지로 표시.
    const upsideEl = $("today-pick-upside");
    if (fb && fb.optimistic.upside != null) {
      const valUp = fb.optimistic.upside;
      const consUp = cons.upside;
      const flagBadge = cons.upside_flagged
        ? ` <span class="info-dot" tabindex="0">⚠️<span class="tooltip-pop">${cons.upside_flag_reason}</span></span>`
        : "";
      let html = `<span class="${updownClass(valUp)}">${sign(valUp, 1)}%</span> <small class="hint">적정가치 기준</small>`;
      if (consUp != null) {
        html += `<br><span class="${updownClass(consUp)}" style="font-size:12px">${sign(consUp, 1)}% 컨센서스 목표가 기준${flagBadge}</span>`;
      }
      upsideEl.innerHTML = html;
    }

    renderTodayValuation(val, best);
  } catch {
    $("today-pick-fair").textContent = "-";
    $("today-pick-fairvalue").textContent = "-";
    $("today-valuation").classList.add("hidden");
  }
}

// "적정주가는 어떻게 나왔나" — valuation.analyze()가 이미 계산한 과거 PER밴드·동종업계·
// PEG(미래 성장 반영)·EV/EBITDA 근거를 그대로 노출한다(사용자 요청: "과거대비 PER, PBR
// 또는 미래가치, 종합지표 등을 분석했을 때 얼마가 적정주가인지" — PBR은 별도 밸류에이션
// 모델은 없지만 참고 수치로 함께 표기).
function renderTodayValuation(val, best) {
  const el = $("today-valuation");
  if (!val || !val.available || !val.fair_buy) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  el.classList.remove("hidden");

  const rows = [];
  if (val.band) {
    rows.push({ label: `과거 대비 ${val.band.kind}`, ok: val.band.ratio <= 1.15,
      text: `현재 ${val.band.current}배 vs 과거 ${val.band.years}년 평균 ${val.band.hist_avg}배 — ${val.band.label}` });
  }
  if (val.peer) {
    rows.push({ label: "동종업계 PER 비교", ok: val.peer.ratio <= 1.15,
      text: `내 PER ${val.peer.my_per}배 vs 업종 평균 ${val.peer.peer_avg}배 — ${val.peer.label}` });
  }
  if (val.peg) {
    rows.push({ label: "PEG (성장 대비 밸류, 미래가치)", ok: val.peg.peg <= 1.0,
      text: `PEG ${val.peg.peg} = PER ${val.peg.per_used}배 ÷ 성장률 ${val.peg.growth_used}% — ${val.peg.label}` });
  }
  if (val.ev_ebitda) {
    rows.push({ label: "EV/EBITDA", ok: val.ev_ebitda.ev_ebitda <= 12,
      text: `${val.ev_ebitda.ev_ebitda}배 (${val.ev_ebitda.basis})` });
  }
  const rowsHtml = rows.map((r) => `
    <div class="today-val-row ${r.ok ? "ok" : "warn"}">
      <span class="today-val-label">${r.label}</span>
      <span class="today-val-text">${r.text}</span>
    </div>`).join("");

  const fb = val.fair_buy;
  const cur = val.current || {};
  const pbrNote = cur.pbr != null ? ` · PBR ${cur.pbr}배(참고)` : "";
  const summaryHtml = `
    <div class="today-val-summary">
      <b>${val.verdict}</b> — 종합 밸류에이션 ${val.score}점 · 적정가치 <b>${pw(fb.fair_value, best.currency)}</b>
      (지표 ${fb.sources}개 종합, 현재가 대비 ${sign(fb.optimistic.upside, 1)}%)${pbrNote}
    </div>`;

  el.innerHTML = `<div class="today-val-title">📐 적정가치는 어떻게 나왔나</div>${summaryHtml}${rowsHtml}`;
}

/* ---------------- 내 포트폴리오 (홈 위젯 — "오늘 내가 할 일") ---------------- */
function mypfLoggedOutHtml() {
  return `
    <h2>💼 내 포트폴리오</h2>
    <div class="mypf-cta">
      <p>포트폴리오를 등록하면 AI가 보유종목까지 매일 점검해드립니다.</p>
      <button class="primary-btn" id="mypf-login-btn">로그인하고 시작하기</button>
    </div>`;
}

function mypfEmptyHtml() {
  return `
    <h2>💼 내 포트폴리오</h2>
    <div class="mypf-cta">
      <p>아직 등록된 보유 종목이 없어요. 등록하면 AI가 매수/매도 타이밍을 매일 점검해드립니다.</p>
      <button class="primary-btn" id="mypf-add-btn">포트폴리오 등록하기</button>
    </div>`;
}

function mypfActionCardHtml(a) {
  return `<div class="pf-action-card pf-action-${a.level} mypf-action" data-code="${a.code}">
    <div class="pf-action-head">${RISK_LEVEL_ICON[a.level]} <b>${a.name}</b> ${a.title}</div>
    <div class="pf-action-detail">${a.detail} → ${a.action}</div>
  </div>`;
}

function mypfActionsHtml(p) {
  // ⚠️ today_actions만 읽고 warnings(집중도·업종쏠림·변동성 경고)는 무시했더니 종목이
  // 하나뿐일 때 today_actions가 비어(1종목은 "비중 과다" 비교 대상이 없어서) 홈은
  // "적정 수준 유지"라고 하는데 포트폴리오 페이지는 경고 3건 + 손실 -22.7%를 보여주는
  // 정반대 화면이 나왔다(2차 진단리포트 3-3). warnings까지 함께 반영한다.
  const actions = p.today_actions || [];
  const warnings = p.warnings || [];
  const n = actions.length;
  const shown = actions.slice(0, 4);
  const headline = n
    ? `오늘 내 포트폴리오에서 할 일 ${n}건`
    : warnings.length
      ? `주의할 점 ${warnings.length}건`
      : "오늘은 특별한 조치가 필요 없어요";
  const warnHtml = (!n && warnings.length)
    ? `<div class="mypf-warn-list">${warnings.map((w) => `<div class="mypf-warn-item">${w}</div>`).join("")}</div>`
    : "";
  const listHtml = shown.length
    ? `<div class="mypf-list">${shown.map(mypfActionCardHtml).join("")}</div>`
    : (warnings.length ? "" : `<div class="mypf-ok">🟢 현재 등록된 ${p.items.length}개 종목 모두 적정 수준을 유지하고 있어요.</div>`);
  const moreHtml = n > shown.length ? `<p class="mypf-more">+${n - shown.length}건 더 있어요</p>` : "";
  return `
    <h2>💼 내 포트폴리오</h2>
    <p class="mypf-headline">${headline}</p>
    ${warnHtml}${listHtml}${moreHtml}
    <div class="mypf-foot"><button class="ghost-btn" id="mypf-view-btn">포트폴리오 전체보기 →</button></div>`;
}

async function loadMyPortfolioWidget() {
  const board = $("mypf-board");
  if (!board) return;
  if (!currentUser) {
    board.innerHTML = mypfLoggedOutHtml();
    $("mypf-login-btn").onclick = () => openAuthModal("login");
    return;
  }
  try {
    const p = await api("/api/portfolio");
    if (!p.available || !p.items || !p.items.length) {
      board.innerHTML = mypfEmptyHtml();
      $("mypf-add-btn").onclick = () => navigate("/portfolio");
      return;
    }
    board.innerHTML = mypfActionsHtml(p);
    board.querySelectorAll(".mypf-action").forEach((el) => { el.onclick = () => analyze(el.dataset.code); });
    $("mypf-view-btn").onclick = () => navigate("/portfolio");
  } catch {
    board.innerHTML = "";   // 실패해도 홈 진입 자체는 막지 않음(조용히 숨김)
  }
}

function paintRanking() {
  const shown = rankAll.slice(0, rankShown);
  const rowsHtml = shown.map((r, i) => {
    const rank = r.rank || i + 1;
    const medal = rank <= 3 ? `top g${rank}` : "";
    const col = scoreColor(r.score);
    const up = r.upside != null ? `${sign(r.upside, 0)}%` : "-";
    return `
    <div class="rank-row" data-code="${r.code}">
      <div class="rank-num ${medal}">${rank}</div>
      <div class="rank-info">
        <div class="rank-name">${r.name}</div>
        <div class="rank-sector">${r.sector} · ${r.code}</div>
      </div>
      <div class="rank-price">
        <div class="p">${pw(r.price, r.currency)}</div>
        <div class="r ${updownClass(r.rate)}">${sign(r.rate, 2)}%</div>
      </div>
      <div class="rank-score-chip" style="color:${col};background:color-mix(in srgb, ${col} 13%, transparent)">${r.score}</div>
      <div class="rank-tail">
        <div class="rank-grade" style="color:${col}">${r.grade}등급</div>
        <div class="rank-upside">목표가 ${up}</div>
        <div class="rank-bar"><i style="width:${r.score}%;background:${col}"></i></div>
      </div>
    </div>`;
  }).join("");

  let moreHtml = "";
  if (rankAll.length > 5) {
    if (rankShown < rankAll.length) {
      const remain = rankAll.length - rankShown;
      moreHtml = `<button class="rank-more-btn" id="rank-more">더보기 <span>${remain}개</span> ▾</button>`;
    } else {
      moreHtml = `<button class="rank-more-btn collapse" id="rank-more">접기 ▴</button>`;
    }
  }
  $("rank-list").innerHTML = rowsHtml + moreHtml;

  $("rank-list").querySelectorAll(".rank-row").forEach((row) => {
    row.onclick = () => analyze(row.dataset.code);
  });
  const moreBtn = $("rank-more");
  if (moreBtn) moreBtn.onclick = () => {
    if (rankShown < rankAll.length) {
      rankShown = Math.min(rankShown + RANK_STEP, rankAll.length);
    } else {
      rankShown = 5;
      document.querySelector(".rank-board").scrollIntoView({ behavior: "smooth", block: "start" });
    }
    paintRanking();
  };
}

/* ---------------- analyze flow ---------------- */
// fromPopstate: 브라우저 뒤로/앞으로가기로 호출된 경우 true — 이미 주소창이 그 URL이므로
// history.pushState를 또 하면 안 된다(안 그러면 뒤로가기가 앞으로가기 스택을 매번 새로 쌓아
// 한 번 눌러도 제자리인 것처럼 보이는 버그가 생긴다).
async function analyze(code, fromPopstate) {
  currentCode = code;
  clearInterval(priceTimer);
  clearTimeout(rankPollTimer);
  $("landing").classList.add("hidden");
  $("report").classList.add("hidden");
  // ⚠️ 실제로 잡은 버그: 스크리너·포트폴리오 등 홈이 아닌 화면에서 종목 행을 클릭해도
  // analyze()는 원래 "종목 상세"만 보여주고 자신을 호출한 화면은 안 닫았다(랭킹·홈에서만
  // 쓰이던 시절엔 문제없었지만, 스크리너 결과 클릭 시 스크리너 화면이 종목상세 뒤에 계속
  // 깔려 있는 게 실제로 재현됨). 어디서 호출되든 다른 화면을 전부 닫도록 여기서 통일한다.
  $("compare-view").classList.add("hidden");
  $("admin-view").classList.add("hidden");
  $("portfolio-view").classList.add("hidden");
  $("screener-view").classList.add("hidden");
  $("loading").classList.remove("hidden");
  try {
    const d = await api(`/api/analyze/${code}`);
    render(d);
    $("loading").classList.add("hidden");
    $("report").classList.remove("hidden");
    setActiveNav("stock");
    window.scrollTo({ top: 0 });
    priceTimer = setInterval(refreshPrice, 2000);
    // URL 라우팅: 종목 페이지를 북마크·공유·브라우저 뒤로가기로 다시 열 수 있게 주소창을
    // /stock/{code}로 갱신한다(진단리포트 지적사항 — 이전엔 주소가 항상 "/" 그대로였음).
    const path = `/stock/${encodeURIComponent(d.code)}`;
    if (!fromPopstate && location.pathname !== path) history.pushState({ code: d.code }, "", path);
    document.title = `${d.name} — StockLens`;
  } catch (err) {
    $("loading").classList.add("hidden");
    $("landing").classList.remove("hidden");
    // 잘못되거나 지원 안 하는 코드로 /stock/{code} 직행 링크를 열었을 때, 주소창에 죽은
    // 링크가 남아 새로고침해도 계속 실패하지 않도록 홈으로 되돌린다. replaceState를 쓰는 이유:
    // fromPopstate=true(뒤로가기로 들어온 실패)든 아니든 새 히스토리 항목을 쌓지 않고 그
    // 자리에서 주소만 고쳐야 뒤로/앞으로가기 스택이 꼬이지 않는다.
    if (location.pathname !== "/") history.replaceState(null, "", "/");
    showError("분석 실패: " + err.message);
  }
}

async function refreshPrice() {
  if (!currentCode) return;
  try {
    const p = await api(`/api/price/${currentCode}`);
    if (p.price != null) {
      if (p.currency) curCur = p.currency;
      $("live-price").textContent = pw(p.price);
      const cls = updownClass(p.change);
      $("live-price").className = "live-price " + cls;
      $("live-change").className = "live-change " + cls;
      $("live-change").textContent = changeStr(p.change, p.rate);
      $("source-badge").textContent = p.source === "KIS" ? "한국투자증권 실시간" : "네이버 시세";
    }
  } catch {}
}

/* ---------------- 가격별 투자전략 — 막대 위 점 + 아래 범례 ---------------- */
// ⚠️ 기존엔 라벨(가격+태그) 5개를 트랙 위에 절대좌표로 직접 배치하고, 겹치면 세로로
// 어긋나게 쌓는 방식이었다. 이 방식은 (1) 트랙 최소폭(480px/모바일 420px)이 화면보다
// 넓어 좌우 라벨이 화면 밖으로 잘려 가로 스크롤 없인 안 보이고, (2) 겹침 판정 기준을
// JS에 하드코딩한 폭(TRACK_MIN_PX)으로 계산해 실제 CSS 폭(반응형 브레이크포인트마다
// 다름)과 어긋나면 판정 자체가 틀렸다 — "폰·태블릿에서 깨진다"는 지적의 원인.
// 점은 얇은 막대 위에 작게 찍고, 라벨은 막대 아래 자연스럽게 줄바꿈되는 범례 목록으로
// 분리했다 — 화면 폭이 얼마든 가로 스크롤도, 겹침 계산도 필요 없다.
function renderPriceLadder(fb, price, target) {
  const pts = [
    { label: "적극매수", emoji: "🟢", price: fb.conservative.price, cls: "pl-buy" },
    { label: "매수", emoji: "🟢", price: fb.base.price, cls: "pl-buy" },
    { label: "분할매수", emoji: "🟡", price: fb.optimistic.price, cls: "pl-partial" },
  ];
  if (price != null) pts.push({ label: "현재가", emoji: "📍", price, cls: "pl-current" });
  if (target != null) pts.push({ label: "목표가", emoji: "🎯", price: target, cls: "pl-target" });

  const vals = pts.map((p) => p.price);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.1 || hi * 0.05 || 1;
  const rangeLo = lo - pad, span = (hi + pad) - (lo - pad) || 1;

  const sorted = [...pts].sort((a, b) => a.price - b.price)
    .map((p) => ({ ...p, pct: ((p.price - rangeLo) / span) * 100 }));

  $("price-ladder").innerHTML = `
    <div class="pl-bar">
      ${sorted.map((p) => `<div class="pl-dot ${p.cls}" style="left:${p.pct.toFixed(1)}%" title="${p.emoji} ${p.label} ${pw(p.price)}"></div>`).join("")}
    </div>
    <div class="pl-legend">
      ${sorted.map((p) => `
        <div class="pl-legend-item ${p.cls}">
          <i class="pl-legend-dot"></i>${p.emoji} ${p.label} <b>${pw(p.price)}</b>
        </div>`).join("")}
    </div>`;
}

/* ---------------- 단계별 매수 전략 (1차/2차/3차 분할매수) ---------------- */
// ⚠️ 1차→2차→3차는 "가격이 낮은 순서"가 아니라 "낙관적(적정가 100%)→기준(90%)→보수적(80%)"
// 안전마진 순서다. 적정가 전체가 현재가보다 높은 상태(주가가 이미 크게 빠진 뒤)에서는
// 1차·2차 매수가가 현재가보다 비싸게 보여 "1차는 지금보다 비싸게 사라"는 말처럼 읽히는
// 문제가 있었다(진단리포트 지적). 가격 순서 자체는 안전마진 로직상 정상이므로 바꾸지 않고,
// 대신 각 단계가 "이미 도달했는지"를 명시해 실행 가능한 지시로 만든다.
function renderBuyPlan(fb, targetPrice, stopLoss, price) {
  $("buy-plan-box").classList.remove("hidden");
  const stages = [
    { label: "분할 진입 1차", pct: 30, price: fb.optimistic.price },
    { label: "분할 진입 2차", pct: 30, price: fb.base.price },
    { label: "분할 진입 3차", pct: 40, price: fb.conservative.price },
  ];
  let cumPct = 0;
  const reachedIdx = [];
  stages.forEach((s, i) => { if (price != null && price <= s.price) reachedIdx.push(i); });
  $("buy-plan-stages").innerHTML = stages.map((s, i) => {
    const reached = price != null && price <= s.price;
    if (reached) cumPct += s.pct;
    const gap = price != null ? (s.price - price) / price * 100 : null;
    const status = reached
      ? `<div class="buy-stage-status reached">✅ 도달 · 즉시 집행 가능</div>`
      : `<div class="buy-stage-status pending">⏳ 대기 · ${gap.toFixed(1)}% 더 하락 시 도달</div>`;
    return `
    <div class="buy-stage ${reached ? "is-reached" : ""}">
      <div class="buy-stage-label">${s.label} <span class="buy-stage-pct">${s.pct}%</span></div>
      <div class="buy-stage-price">${pw(s.price)}</div>
      ${price != null ? status : ""}
    </div>`;
  }).join("");
  const noteEl = $("buy-plan-reached-note");
  if (reachedIdx.length) {
    noteEl.classList.remove("hidden");
    noteEl.textContent = `🎯 현재가 기준 ${cumPct}% 구간에 이미 도달했습니다 — 해당 비중은 지금 바로 집행할 수 있습니다.`;
  } else {
    noteEl.classList.add("hidden");
    noteEl.textContent = "";
  }
  $("buy-plan-target").textContent = targetPrice ? pw(targetPrice) : "-";
  $("buy-plan-stop").textContent = stopLoss ? pw(stopLoss) : "-";
}

/* ---------------- 종목상세 탭 ---------------- */
function showDetailTab(tab) {
  document.querySelectorAll("#detail-tabs button").forEach((b) => {
    const on = b.dataset.tab === tab;
    b.classList.toggle("active", on);
    // role=tab을 붙였으므로 선택 상태도 같이 알려줘야 스크린리더가 "선택됨"을 읽는다.
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  // "> [data-tab]"(직계 자식만) — 안 그러면 #detail-tabs 안의 탭 버튼도 [data-tab]이라 같이
  // 걸려서 비활성 탭 버튼들이 전부 사라지는 버그가 남(실제로 걸렸던 실수, 되돌리지 말 것).
  document.querySelectorAll("#report > [data-tab]").forEach((el) => el.classList.toggle("tab-hide", el.dataset.tab !== tab));
  // 숨겨진 상태(display:none, width=0)에서 그린 차트는 그 탭이 실제로 보여질 때 다시 그린다.
  // ⚠️ lightweight-charts(메인 캔들차트)는 autoSize:true라 "크기"는 스스로 재조정되지만,
  // setVisibleLogicalRange()로 지정한 "기간 필터"는 되돌아가지 않는다 — width=0일 때
  // 걸어둔 구간이 컨테이너가 보이는 순간 ResizeObserver의 리사이즈 처리에 의해 무시되고
  // 전체 구간으로 되돌아가는 실제 버그가 있었다(진단리포트로 확인). 그래서 finance-chart와
  // 동일하게 탭이 열릴 때 drawChart()를 다시 호출해 컨테이너가 보이는 상태에서 기간 필터를
  // 다시 적용해야 한다.
  if (tab === "finance" && lastAnalysis) renderFinance(lastAnalysis.finance_rows);
  if (tab === "chart" && chartCtx.code) drawChart();
}
document.querySelectorAll("#detail-tabs button").forEach((b) => {
  b.onclick = () => showDetailTab(b.dataset.tab);
});

/* ---------------- render ---------------- */
function render(d) {
  lastAnalysis = d;
  curCur = d.currency || "KRW";
  showDetailTab("overview");   // 종목이 바뀔 때마다 "종합" 탭으로 리셋
  /* header */
  $("stock-logo").src = d.logo || "";
  $("stock-logo").style.display = d.logo ? "" : "none";
  $("stock-name").textContent = d.name;
  $("stock-code").textContent = d.code;
  $("stock-market").textContent = d.market || "";
  $("live-price").textContent = pw(d.price);
  const cls = updownClass(d.change);
  $("live-price").className = "live-price " + cls;
  $("live-change").className = "live-change " + cls;
  $("live-change").textContent = changeStr(d.change, d.rate);
  $("live-badge").textContent = d.market_status === "OPEN" ? "장중" : "장마감";
  $("source-badge").textContent = d.kis_enabled ? "한국투자증권 실시간" : "네이버 시세";
  updateFavBtn();
  updateWatchBtn();
  $("watch-msg").classList.add("hidden");
  $("watch-msg").textContent = "";

  /* score */
  drawGauge(d.total.total_score);
  $("grade").textContent = d.total.grade;
  $("grade").style.color = scoreColor(d.total.total_score);
  $("grade-desc").textContent = d.total.grade_desc + " · " + d.total.total_score + "점";

  /* sticky 요약바 — 스크롤로 종목 헤더가 화면 밖으로 나가도 "지금 어느 종목의 무슨
     판단을 보고 있는지"가 계속 보이게 한다(UI/UX 진단보고서 3-2 권고). */
  {
    const v = d.ai_verdict || {};
    const cls = updownClass(d.change);
    $("sticky-summary").innerHTML =
      `<span class="ss-name">${d.name}</span>` +
      `<span class="ss-price">${pw(d.price, d.currency)}</span>` +
      `<span class="ss-chg ${cls}">${changeStr(d.change, d.rate)}</span>` +
      `<span class="ss-score">종합 ${d.total.total_score}점 · ${d.total.grade}등급</span>` +
      `<span class="ss-verdict" style="color:${verdictColor(v.tier)}">${v.emoji || ""} ${v.label || "-"}</span>`;
  }

  /* AI 최종판단 요약 (한눈에 보기) — 이미 계산된 targets/technical/valuation/flows를 재사용.
     "매수 매력도 점수"(=종합점수)보다 "매수 관심" 같은 행동판단을 훨씬 크게 보여주는 게 핵심 —
     점수가 높다고 지금 사야 하는 건 아니라는 StockLens의 철학(오늘의 AI 투자기회 섹션과 동일). */
  {
    const t = d.targets, tech = d.technical, val = d.valuation;
    const score = d.total.total_score;
    $("highlight-card").classList.remove("hidden");
    $("hl-score").textContent = score;
    $("hl-score").style.color = scoreColor(score);
    $("hl-badge").textContent = score >= 70 ? "🟢" : score >= 55 ? "🟡" : score >= 40 ? "🟠" : "🔴";

    const v = d.ai_verdict || {};
    $("ai-verdict-emoji").textContent = v.emoji || "";
    $("ai-verdict-tier").textContent = v.label || "-";
    $("ai-verdict-tier").style.color = verdictColor(v.tier);

    const fbBase = t.fair_buy ? t.fair_buy.base : null;
    if (fbBase && d.price) {
      const diffPct = (d.price - fbBase.price) / fbBase.price * 100;
      if (diffPct <= -3) $("hl-discount").textContent = `현재 가격은 적정가치 대비 ${Math.abs(diffPct).toFixed(1)}% 저평가되어 있습니다.`;
      else if (diffPct >= 3) $("hl-discount").textContent = `현재 가격은 적정가치 대비 ${diffPct.toFixed(1)}% 고평가되어 있습니다.`;
      else $("hl-discount").textContent = `현재 가격은 적정가치와 비슷한 수준입니다.`;
    } else {
      $("hl-discount").textContent = "";
    }
    // "확신도"는 실제 확률처럼 오해될 수 있어 "판단 신뢰도"로 표기 — 값의 의미(analysis.final_verdict의
    // confidence 필드)는 그대로, 라벨만 바꾼다. 클릭/포커스 시 설명 툴팁 표시.
    const confidenceLabel = `판단 신뢰도 <span class="info-dot" tabindex="0">ⓘ<span class="tooltip-pop">실적·밸류·수급·기술적 지표 등 주요 분석 신호의 일치 정도를 나타냅니다.</span></span>`;
    // 목표주가는 재무 추정치와 달리 이상치 검증·기준일 표시가 빠져 있어, 후행적인 목표가가
    // 그대로 "왜 사야 하나"에 노출되는 문제가 있었다(2차 진단리포트 4-1). 기준일을 함께
    // 보여주고, 괴리가 커 반영 비중을 낮춘 경우(analysis.consensus_info) 배지로 알린다.
    const cons = d.consensus || {};
    const targetLabel = "컨센서스 목표가" + (cons.date ? ` <small class="hint">(${cons.date} 기준)</small>` : "");
    const upsideFlagBadge = cons.upside_flagged
      ? ` <span class="info-dot" tabindex="0">⚠️<span class="tooltip-pop">${cons.upside_flag_reason}</span></span>`
      : "";
    const items = [
      { label: "현재가", value: pw(d.price) },
      { label: "매수 적정가", value: fbBase ? pw(fbBase.price) : "-" },
      { label: targetLabel, value: t.consensus ? pw(t.consensus) : "-" },
      { label: "상승여력", value: (t.consensus_upside != null ? sign(t.consensus_upside, 1) + "%" : "-") + upsideFlagBadge,
        cls: updownClass(t.consensus_upside) },
      { label: confidenceLabel, value: v.confidence != null ? v.confidence : "-" },
    ];
    $("hl-grid").innerHTML = items.map((it) => `
      <div class="hl-item"><label>${it.label}</label><div class="${it.cls || ""}">${it.value}</div></div>`).join("");

    // 부문별 6개 아이콘 요약 — "5초 요약"에서 세부 이유(hl-why-split)까지 안 읽어도 어디가
    // 좋고 나쁜지 색으로 바로 보이게(홈 today-why와 같은 CATEGORY_ICON/점수 구간 재사용).
    // 아이콘+색점만으로는 뭘 뜻하는지 안 보여서(호버 툴팁은 모바일에서 무용지물) 부문명을 항상 텍스트로 같이 표기.
    $("hl-cats").innerHTML = Object.entries(d.total.categories || {}).map(([name, catScore]) => {
      const dot = catScore >= 62 ? "🟢" : catScore >= 42 ? "🟡" : "🔴";
      return `<span class="hl-cat-chip">${CATEGORY_ICON[name] || "•"} ${name} ${dot}</span>`;
    }).join("");

    // 근거: 이미 계산된 기술적·밸류에이션 신호(bull/bear/warn 태그) + 외국인 수급 추세
    const reasons = [];
    const pick = (arr) => (arr || []).forEach((s) => {
      if (s.type === "bull") reasons.push({ good: true, text: s.text });
      else if (s.type === "bear" || s.type === "warn") reasons.push({ good: false, text: s.text });
    });
    pick(val.signals);
    pick(tech.signals);
    const flows5 = (d.flows || []).slice(0, 5).map((f) => f.foreigner).filter((v) => v != null);
    if (flows5.length >= 3) {
      const posDays = flows5.filter((v) => v > 0).length;
      if (posDays === flows5.length) reasons.unshift({ good: true, text: `외국인 최근 ${flows5.length}일 연속 순매수` });
      else if (posDays === 0) reasons.unshift({ good: false, text: `외국인 최근 ${flows5.length}일 연속 순매도` });
    }
    const goodReasons = reasons.filter((r) => r.good).slice(0, 4);
    const badReasons = reasons.filter((r) => !r.good).slice(0, 4);
    $("hl-reasons-good").innerHTML = goodReasons.length
      ? goodReasons.map((r) => `<li class="good">✅ ${r.text}</li>`).join("")
      : `<li>뚜렷한 매수 근거 신호가 없습니다.</li>`;
    $("hl-reasons-bad").innerHTML = badReasons.length
      ? badReasons.map((r) => `<li class="bad">⚠️ ${r.text}</li>`).join("")
      : `<li>뚜렷한 우려 신호는 없습니다.</li>`;
  }

  /* opinion */
  $("opinion-head").textContent = d.opinion.headline;
  $("opinion-points").innerHTML = d.opinion.points.map((p) => `<li>${p}</li>`).join("");
  drawRadar(d.total.categories);
  // ⚠️ 컨센서스 이상치로 추정치를 배제한 종목은 "제외했다"는 배너 아래 성장성이 여전히
  // 높게(100점) 나올 수 있다 — 확정 실적으로 재계산한 결과이기 때문인데, 그 사실이
  // 화면에 안 보여 사용자에겐 모순처럼 읽혔다(3차 진단리포트 3-4). 성장성 막대 옆에
  // 실제로 쓰인 확정 실적 수치를 병기해 "왜 이 점수인지"를 바로 보이게 한다.
  $("category-bars").innerHTML = Object.entries(d.total.categories).map(([k, v]) => {
    let note = "";
    if (k === "성장성" && d.metrics.consensus_flagged) {
      const revTxt = d.metrics.rev_growth != null ? `매출 ${sign(d.metrics.rev_growth, 1)}%` : null;
      const opTxt = d.metrics.op_growth != null ? `영업이익 ${sign(d.metrics.op_growth, 1)}%`
        : (d.metrics.op_growth_status || null);
      const parts = [revTxt, opTxt].filter(Boolean);
      if (parts.length) note = `<div class="cat-note">확정 실적 기준: ${parts.join(", ")}</div>`;
    }
    return `
    <div class="cat-bar">
      <div class="cat-label"><b>${k}</b><span>${v}점</span></div>
      <div class="cat-track"><div class="cat-fill" style="width:${v}%;background:${scoreColor(v)}"></div></div>
      ${note}
    </div>`;
  }).join("");

  /* targets */
  const t = d.targets;
  $("target-consensus").textContent = t.consensus ? pw(t.consensus) : "데이터 없음";
  $("target-consensus-upside").textContent = t.consensus_upside != null ? `상승여력 ${sign(t.consensus_upside, 1)}%` : "";
  $("target-consensus-upside").className = "target-upside " + updownClass(t.consensus_upside);
  // 목표주가 기준일 + 괴리 과대 시 경고 — 목표가가 뒤처져도(후행성) 사용자가 알 수 있게
  // 기준일을 그대로 노출한다(2차 진단리포트 4-1: "consensus.date는 있는데 화면에서 안 씀").
  {
    const c = d.consensus || {};
    const dateNote = c.date ? `기준일 ${c.date}` : "";
    const flagNote = c.upside_flagged ? ` · ⚠️ ${c.upside_flag_reason}` : "";
    $("target-consensus-date").textContent = dateNote + flagNote;
  }
  $("target-tech").textContent = t.technical ? pw(t.technical) : "-";
  $("target-tech-upside").textContent = t.technical_upside != null ? `상승여력 ${sign(t.technical_upside, 1)}%` : "";
  $("target-tech-upside").className = "target-upside " + updownClass(t.technical_upside);

  const fb = t.fair_buy;
  if (fb) {
    $("fair-buy-box").classList.remove("hidden");
    $("fb-sources").textContent = `지표 ${fb.sources}개 종합 · 적정가 ${pw(fb.fair_value)}`;
    const tiers = [
      { key: "conservative", label: "🛡️ 보수적", cls: "cons" },
      { key: "base", label: "⭐ 기준", cls: "base" },
      { key: "optimistic", label: "🚀 낙관적", cls: "opt" },
    ];
    $("fb-grid").innerHTML = tiers.map(({ key, label, cls }) => {
      const v = fb[key];
      return `<div class="fb-item ${cls}">
        <label>${label} <small>(안전마진 ${v.margin}%)</small></label>
        <div class="fb-price">${pw(v.price)}</div>
        <div class="fb-upside ${updownClass(v.upside)}">현재가 대비 ${sign(v.upside, 1)}%</div>
      </div>`;
    }).join("");
    renderPriceLadder(fb, d.price, t.consensus);
    const stopLoss = (d.technical.available && d.technical.entry) ? d.technical.entry.stop_loss : null;
    renderBuyPlan(fb, t.consensus, stopLoss, d.price);
  } else {
    $("fair-buy-box").classList.add("hidden");
    $("price-ladder").innerHTML = "";
    $("buy-plan-box").classList.add("hidden");
  }

  const tech = d.technical;
  if (tech.available) {
    $("verdict").textContent = tech.verdict;
    $("verdict").className = "verdict " + tech.verdict_class;
    $("timing-comment").textContent = tech.timing_comment;
    $("cons-opinion").textContent = d.consensus.opinion ? `애널리스트: ${d.consensus.opinion} (${d.consensus.recomm_mean}/5)` : "";
    const e = tech.entry;
    $("entry-grid").innerHTML = `
      <div class="entry-item buy"><label>🟢 지지 밴드 <small class="hint">기술적 지지 구간</small></label><div>${pwRange(e.buy_zone_low, e.buy_zone_high)}</div></div>
      <div class="entry-item sell"><label>🔴 매도·차익실현 구간</label><div>${pwRange(e.sell_zone_low, e.sell_zone_high)}</div></div>
      <div class="entry-item"><label>지지선</label><div class="up">${pw(e.support)}${tech.support_confluence ? ` <small class="hint">(컨플루언스 ${pw(tech.support_confluence)})</small>` : ""}</div></div>
      <div class="entry-item"><label>저항선</label><div class="down">${pw(e.resistance)}${tech.resistance_confluence ? ` <small class="hint">(컨플루언스 ${pw(tech.resistance_confluence)})</small>` : ""}</div></div>
      <div class="entry-item"><label>손절 참고가</label><div class="down">${pw(e.stop_loss)}</div></div>`;
  }

  /* chart */
  renderChart(d);


  /* tech summary */
  if (tech.available) {
    const slopeTxt = (v) => v == null ? "-" :
      `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "▲" : v < 0 ? "▼" : ""}${Math.abs(v).toFixed(1)}%</span>`;
    $("tech-summary").innerHTML = `
      <div class="tech-item"><label>단기 기술점수 <span class="info-dot" tabindex="0">ⓘ<span class="tooltip-pop">최근 추세·모멘텀·거래량 등 단기 지표만의 점수입니다. 종합 탭의 "기술적추세"는 이 값과 장기 구조 분석(스테이지·상대강도 등)을 절반씩 섞은 값이라 서로 다를 수 있습니다.</span></span></label><div style="color:${scoreColor(tech.score)}">${tech.score}점</div></div>
      <div class="tech-item"><label>RSI(14)</label><div>${tech.rsi ?? "-"}</div></div>
      <div class="tech-item"><label>20일선 방향</label><div>${slopeTxt(tech.ma20_slope)}</div></div>
      <div class="tech-item"><label>60일선 방향</label><div>${slopeTxt(tech.ma60_slope)}</div></div>
      <div class="tech-item"><label>52주 위치</label><div>${tech.pos_52w}%</div></div>
      <div class="tech-item"><label>거래량(5d/20d)</label><div>${tech.volume_ratio ?? "-"}배</div></div>`;
    /* 기술 점수 4축 분해 */
    if (tech.score_parts) {
      const p = tech.score_parts;
      $("tech-parts").innerHTML = ["추세", "모멘텀", "위치", "거래량"].map((k) =>
        `<div class="tp-bar"><span class="tp-label">${k}</span>
           <span class="tp-track"><i style="width:${p[k]}%;background:${scoreColor(p[k])}"></i></span>
           <span class="tp-val">${p[k]}</span></div>`).join("");
      $("tech-parts").classList.remove("hidden");
    } else {
      $("tech-parts").classList.add("hidden");
    }
    $("tech-signals").innerHTML = tech.signals.map((s) => `<li class="${s.type}">${s.text}</li>`).join("");
  } else {
    $("tech-summary").innerHTML = "<p class='hint-p'>차트 데이터가 부족합니다.</p>";
    $("tech-parts").classList.add("hidden");
    $("tech-signals").innerHTML = "";
  }

  renderChartPro(d.chart_pro);
  renderValuation(d.valuation);

  /* metrics */
  const m = d.metrics;
  const metricDefs = [
    ["PER", m.per, "배", "주가수익비율"],
    ["선행 PER", m.cns_per, "배", "컨센서스 기준"],
    ["PBR", m.pbr, "배", "주가순자산비율"],
    ["ROE", m.roe, "%", "자기자본이익률"],
    ["EPS", m.eps, "원", "주당순이익"],
    ["BPS", m.bps, "원", "주당순자산"],
    ["배당수익률", m.dividend_yield, "%", ""],
    ["영업이익률", m.op_margin, "%", ""],
    ["순이익률", m.net_margin, "%", ""],
    ["부채비율", m.debt_ratio, "%", ""],
    ["매출성장률", m.rev_growth, "%", "전년 대비"],
    ["영업이익성장률(E)", m.op_growth_fwd, "%", "컨센서스 내년"],
  ];
  $("metrics-grid").innerHTML = metricDefs.map(([label, v, unit, sub]) => {
    let disp = "-";
    if (v != null) disp = unit === "원" ? pw(v) : fmt(v, 2) + unit;  // EPS/BPS는 통화 대응
    return `<div class="metric"><label>${label}</label>
      <div>${disp} ${sub ? `<br><small>${sub}</small>` : ""}</div></div>`;
  }).join("") +
    `<div class="metric"><label>시가총액</label><div>${m.market_cap ? fmt(m.market_cap / 10000, 1) + (curCur === "USD" ? "조 달러" : "조원") : "-"}</div></div>`;

  /* finance */
  renderFinance(d.finance_rows);

  /* flows (미국은 수급 데이터 없음) */
  const flowCard = $("flow-table").closest(".card");
  $("tab-btn-flow").classList.toggle("hidden", !d.flows.length);
  if (!d.flows.length) {
    flowCard.classList.add("hidden");
  } else {
    flowCard.classList.remove("hidden");
    $("flow-table").innerHTML = tableHTML(
      ["일자", "종가", "외국인", "기관", "개인", "외국인 보유율"],
      d.flows.map((f) => [
        f.date ? `${f.date.slice(4, 6)}/${f.date.slice(6, 8)}` : "-",
        fmt(f.close),
        numCell(f.foreigner), numCell(f.organ), numCell(f.individual),
        f.foreigner_ratio || "-",
      ]));
  }

  /* research */
  $("research-consensus").textContent = d.consensus.opinion
    ? `컨센서스: ${d.consensus.opinion} · 목표가 ${pw(d.consensus.target_price)}` : "컨센서스 없음";
  $("research-list").innerHTML = d.research.length
    ? d.research.map((r) => `
      <div class="research-item">
        <div class="r-top"><b>${r.title}</b><span class="r-meta">${r.broker} · ${r.date}</span></div>
        ${r.preview ? `<div class="r-preview">${r.preview}</div>` : ""}
      </div>`).join("")
    : "<p class='hint-p'>최근 리포트가 없습니다.</p>";

  /* news */
  $("senti-badge").textContent = `시장 심리: ${d.sentiment.label} (${d.sentiment.score}점)`;
  // ⚠️ 진단리포트 지적사항: 종목 뉴스탭에 그 종목과 무관한 기사(증권사 프로모션·타 상품
  // 세미나 등)가 섞여 심리 점수를 오염시키고 있었음. 백엔드(news_sentiment)가 종목명이
  // 제목·본문에 있는지로 관련성을 판정해 점수 집계에서는 이미 제외했고, 화면에서는
  // 숨기지 않되 "관련성 낮음" 배지로 표시한다(정직하게 보여주는 편이 신뢰를 얻는다는
  // 이 프로젝트의 일관된 원칙). 원문 링크(n.url)가 비어 있는 항목은 죽은 링크를 만들지
  // 않도록 제목을 링크로 감싸지 않는다.
  $("news-list").innerHTML = d.news.map((n) => `
    <div class="news-item${n.relevant === false ? " news-irrelevant" : ""}">
      <div class="n-top">
        <b><span class="senti-tag ${n.sentiment}">${n.sentiment === "positive" ? "긍정" : n.sentiment === "negative" ? "부정" : "중립"}</span>
        ${n.relevant === false ? `<span class="senti-tag irrelevant">관련성 낮음</span>` : ""}
        ${n.url ? `<a href="${n.url}" target="_blank" rel="noopener noreferrer">${n.title}</a>` : `<span>${n.title}</span>`}</b>
        <span class="n-meta">${n.press} · ${n.datetime ? n.datetime.slice(4, 6) + "/" + n.datetime.slice(6, 8) : ""}</span>
      </div>
      ${n.body ? `<div class="n-body">${n.body}...</div>` : ""}
    </div>`).join("");

  /* peers */
  const mcapUnit = curCur === "USD" ? "억 달러" : "억원";
  $("peers-table").innerHTML = tableHTML(
    ["종목명", "현재가", "등락률", `시가총액(${mcapUnit})`],
    d.peers.map((p) => [
      p.name, pw(p.price),
      `<span class="${updownClass(p.rate)}">${sign(p.rate, 2)}%</span>`,
      fmt(p.market_cap),
    ]));

  /* AI */
  $("tab-btn-ai").classList.toggle("hidden", !d.ai_enabled);
  $("ai-report").classList.add("hidden");
  $("ai-report").innerHTML = "";
  if (d.ai_enabled) {
    $("ai-hint").textContent = "Claude AI가 뉴스·증권사 리포트·재무지표를 종합해 심층 분석 리포트를 작성합니다.";
    $("ai-btn").classList.remove("hidden");
  } else {
    $("ai-hint").innerHTML = "서버에 <code>ANTHROPIC_API_KEY</code> 환경변수를 설정하고 <code>pip install anthropic</code> 후 재시작하면 Claude AI 심층 분석 기능이 활성화됩니다. (현재는 위의 규칙 기반 종합 평가가 제공됩니다)";
    $("ai-btn").classList.add("hidden");
  }
}

function numCell(v) {
  if (v == null) return "-";
  const cls = v > 0 ? "up" : v < 0 ? "down" : "flat";
  return `<span class="${cls}">${sign(v)}</span>`;
}
function tableHTML(headers, rows) {
  return `<table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

/* ---------------- gauge ---------------- */
function drawGauge(score) {
  const c = $("gauge"), ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  const cx = 75, cy = 80, r = 58;
  // ⚠️ canvas는 CSS var()를 못 쓰므로 cssVar()로 실제 색을 뽑아 쓴다. 예전엔 트랙·숫자·
  // 라벨 색이 전부 다크 배경 전용으로 하드코딩("#1f2635"/"#e6e9f0"/"#8a93a6")돼 있어,
  // 라이트 모드에서 흰 배경 위에 밝은 회색 숫자가 올라가 "종합점수 86"과 등급이 거의
  // 안 읽혔다(UI/UX 진단보고서 5장).
  ctx.lineWidth = 12; ctx.lineCap = "round";
  ctx.strokeStyle = cssVar("--gauge-track");
  ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI * 0.75, Math.PI * 2.25); ctx.stroke();
  ctx.strokeStyle = scoreColorHex(score);
  ctx.beginPath();
  ctx.arc(cx, cy, r, Math.PI * 0.75, Math.PI * (0.75 + 1.5 * (score / 100)));
  ctx.stroke();
  ctx.fillStyle = cssVar("--gauge-text"); ctx.textAlign = "center";
  ctx.font = "800 30px sans-serif";
  ctx.fillText(Math.round(score), cx, cy + 8);
  ctx.font = "12px sans-serif"; ctx.fillStyle = cssVar("--gauge-sub");
  ctx.fillText("종합점수", cx, cy + 28);
}

/* ---------------- radar ---------------- */
function drawRadar(categories) {
  const c = $("radar"), ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  const labels = Object.keys(categories), vals = Object.values(categories);
  const n = labels.length, cx = 170, cy = 155, R = 100;
  const angle = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;

  for (let ring = 1; ring <= 4; ring++) {
    ctx.beginPath();
    for (let i = 0; i <= n; i++) {
      const a = angle(i % n), rr = (R * ring) / 4;
      const x = cx + rr * Math.cos(a), y = cy + rr * Math.sin(a);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.strokeStyle = "#1f2635"; ctx.stroke();
  }
  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const a = angle(i);
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + R * Math.cos(a), cy + R * Math.sin(a));
  }
  ctx.stroke();

  ctx.beginPath();
  for (let i = 0; i <= n; i++) {
    const a = angle(i % n), rr = (R * vals[i % n]) / 100;
    const x = cx + rr * Math.cos(a), y = cy + rr * Math.sin(a);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.fillStyle = "rgba(79,140,255,.25)"; ctx.fill();
  ctx.strokeStyle = "#4f8cff"; ctx.lineWidth = 2; ctx.stroke(); ctx.lineWidth = 1;

  ctx.fillStyle = "#8a93a6"; ctx.font = "12px sans-serif"; ctx.textAlign = "center";
  for (let i = 0; i < n; i++) {
    const a = angle(i);
    const x = cx + (R + 24) * Math.cos(a), y = cy + (R + 20) * Math.sin(a) + 4;
    ctx.fillText(labels[i], x, y);
  }
}

/* ---------------- candle chart ---------------- */
let chartCtx = { code: null, name: "", currency: "KRW", candles: [], targets: {}, technical: {}, pro: {}, tf: "day" };
let chartApi = null;             // lightweight-charts 인스턴스
// 고급 차트 오버레이(AVWAP·유동성 스윕·볼륨 프로파일) on/off 상태 — 종목을 바꿔도 유지.
const chartOverlayState = { avwap: true, sweep: true, vp: true, smart: true };

function renderChart(d) {
  chartCtx = { code: d.code, name: d.name, currency: d.currency || "KRW",
               candles: d.candles || [], targets: d.targets || {},
               technical: d.technical || {}, pro: d.chart_pro || {}, tf: "day" };
  // 봉 주기 버튼 초기화
  $("chart-tf").querySelectorAll("button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tf === "day"));
  drawChart();
}

/* 봉 주기 전환 — 캔들만 다시 받아 차트 재그림 (전체 재분석 X) */
async function switchTimeframe(tf) {
  if (tf === chartCtx.tf || !chartCtx.code) return;
  $("chart-tf").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tf === tf));
  $("chart-container").style.opacity = ".4";
  try {
    const r = await api(`/api/candles/${chartCtx.code}?tf=${tf}`);
    chartCtx.candles = r.candles || [];
    chartCtx.tf = tf;
    drawChart();
  } catch {
    /* 실패 시 조용히 무시 */
  } finally {
    $("chart-container").style.opacity = "1";
  }
}

function drawChart() {
  const d = chartCtx;
  const el = $("chart-container");
  el.innerHTML = "";
  $("chart-controls").innerHTML = "";
  if (chartApi) { try { chartApi.remove(); } catch {} chartApi = null; }
  if (!window.LightweightCharts || !d.candles || d.candles.length === 0) {
    el.innerHTML = "<p class='hint-p'>차트 데이터를 불러올 수 없습니다.</p>";
    return;
  }
  const LC = LightweightCharts;
  const light = document.body.classList.contains("light");
  const txt = light ? "#5a6377" : "#9aa3ba";
  const gridC = light ? "rgba(15,22,45,.06)" : "rgba(255,255,255,.045)";
  const crossC = light ? "#8790a3" : "#66708c";
  const upC = "#f6465d", downC = "#3e7bfa";       // 빨강 상승 · 파랑 하락

  // 4차 진단리포트 5장 — Y축이 "2800000"처럼 천 단위 구분 없이 표시됐다.
  // lightweight-charts는 priceFormat.precision만으론 구분기호를 안 붙여줘 커스텀
  // localization.priceFormatter가 필요하다.
  const fmtAxis = (p) => curCur === "USD" ? p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : Math.round(p).toLocaleString("ko-KR");
  chart = LC.createChart(el, {
    layout: { background: { color: "transparent" }, textColor: txt, fontFamily: "Pretendard, sans-serif", fontSize: 11 },
    grid: { vertLines: { color: gridC }, horzLines: { color: gridC } },
    localization: { priceFormatter: fmtAxis },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.26 } },
    timeScale: { borderVisible: false, rightOffset: 5, minBarSpacing: 1, fixRightEdge: true },
    crosshair: {
      mode: LC.CrosshairMode.Normal,
      vertLine: { color: crossC, width: 1, style: LC.LineStyle.Dashed, labelBackgroundColor: "#6366f1" },
      horzLine: { color: crossC, width: 1, style: LC.LineStyle.Dashed, labelBackgroundColor: "#6366f1" },
    },
    // 드래그로 이동 · 아래 구간 슬라이더/기간버튼/핀치·휠로 폭(구간) 조절
    handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
    height: 440,
    autoSize: true,
  });
  chartApi = chart;

  const toDate = (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
  const candleSeries = chart.addCandlestickSeries({
    upColor: upC, downColor: downC, borderUpColor: upC, borderDownColor: downC,
    wickUpColor: upC, wickDownColor: downC,
    priceLineColor: crossC,
    // ⚠️ 설계서 22번(3차 미해결 항목) — priceFormat을 지정 안 하면 lightweight-charts 기본값
    // (소수점 2자리)이 적용돼 "1,500,000.00원"처럼 원화엔 의미 없는 소수점이 붙는다.
    // 원화는 정수, 달러는 센트 단위 표기가 자연스러워 통화별로 분기한다.
    priceFormat: curCur === "USD" ? { type: "price", precision: 2, minMove: 0.01 } : { type: "price", precision: 0, minMove: 1 },
  });
  candleSeries.setData(d.candles.map((c) => ({ time: toDate(c.date), open: c.open, high: c.high, low: c.low, close: c.close })));
  // ⚠️ lightweight-charts는 createPriceLine()으로 그린 목표주가 선도 기본적으로 자동스케일에
  // 포함시킨다. 목표주가가 현재가보다 훨씬 높으면(하락장에서 흔함) Y축이 그 값까지 억지로
  // 늘어나면서 하단 마진(scaleMargins.bottom=0.26)까지 비례해 늘어나 축이 음수까지 내려가고
  // 정작 캔들은 화면 위쪽 15%에 눌리는 실제 버그가 있었다(진단리포트로 확인). 자동스케일을
  // "현재 보이는 구간의 캔들 고가·저가"만으로 직접 계산해 목표주가·지지/저항선이 스케일에
  // 영향을 주지 않게 한다 — 화면 밖 가격선은 축 가장자리에 라벨만 붙는 표준 동작으로 처리된다.
  // ⚠️ autoscaleInfoProvider 안에서 chart.timeScale().getVisibleLogicalRange()를 직접 호출하면
  // (가격축 계산 중에 시간축 상태를 동기적으로 조회) 라이브러리 내부에서 재진입으로 처리돼
  // setVisibleLogicalRange()로 건 구간 지정이 그 즉시 원래대로 되돌아가는 실제 버그가 있었다
  // (기간 버튼이 완전히 먹통이 됨, 진단리포트의 "기간 필터 작동 안함"과 겹치는 증상이었음).
  // → subscribeVisibleLogicalRangeChange()로 별도 변수에 "마지막으로 통지받은 구간"만 캐싱하고,
  // autoscaleInfoProvider는 그 캐시만 읽는다(시간축을 다시 조회하지 않음) — 재진입을 원천 차단.
  let visibleRangeCache = null;
  chart.timeScale().subscribeVisibleLogicalRangeChange((range) => { visibleRangeCache = range; });
  candleSeries.applyOptions({
    autoscaleInfoProvider: () => {
      let bars = d.candles;
      if (visibleRangeCache) {
        const from = Math.max(0, Math.floor(visibleRangeCache.from));
        const to = Math.min(bars.length - 1, Math.ceil(visibleRangeCache.to));
        if (to >= from) bars = bars.slice(from, to + 1);
      }
      if (!bars.length) return null;
      let lo = Infinity, hi = -Infinity;
      for (const c of bars) { if (c.low < lo) lo = c.low; if (c.high > hi) hi = c.high; }
      if (!isFinite(lo) || !isFinite(hi)) return null;
      return { priceRange: { minValue: lo, maxValue: hi } };
    },
  });

  const volSeries = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false });
  // 4차 진단리포트 5장 — 거래량 패널 축에 "-400,000" 같은 음수 라벨이 남아 있었다.
  // 거래량은 항상 0 이상이라 축 라벨 자체가 필요 없는 보조 패널 — 아예 숨긴다.
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.86, bottom: 0 }, visible: false });
  volSeries.setData(d.candles.map((c) => ({
    time: toDate(c.date), value: c.volume,
    color: c.close >= c.open ? "rgba(246,70,93,.35)" : "rgba(62,123,250,.35)",
  })));

  const closes = d.candles.map((c) => c.close);
  const addMA = (n, color) => {
    if (closes.length < n) return;
    const line = chart.addLineSeries({ color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const data = [];
    let sum = 0;
    for (let i = 0; i < closes.length; i++) {
      sum += closes[i];
      if (i >= n) sum -= closes[i - n];
      if (i >= n - 1) data.push({ time: toDate(d.candles[i].date), value: sum / n });
    }
    line.setData(data);
  };
  addMA(20, "#ffb020");
  addMA(60, "#2ee6a6");
  addMA(120, "#a855f7");

  // 매수/매도 신호 마커: SMA20 × SMA60 골든/데드 크로스
  const sma = (n) => {
    const out = new Array(closes.length).fill(null);
    let sum = 0;
    for (let i = 0; i < closes.length; i++) { sum += closes[i]; if (i >= n) sum -= closes[i - n]; if (i >= n - 1) out[i] = sum / n; }
    return out;
  };
  const tf = chartCtx.tf;
  const s20 = sma(20), s60 = sma(60);
  const markers = [];
  for (let i = 1; i < closes.length; i++) {
    if (s20[i] == null || s60[i] == null || s20[i - 1] == null || s60[i - 1] == null) continue;
    const prev = s20[i - 1] - s60[i - 1], cur = s20[i] - s60[i];
    if (prev <= 0 && cur > 0)
      markers.push({ time: toDate(d.candles[i].date), position: "belowBar", color: "#2ee6a6", shape: "arrowUp", text: "매수" });
    else if (prev >= 0 && cur < 0)
      markers.push({ time: toDate(d.candles[i].date), position: "aboveBar", color: "#f6465d", shape: "arrowDown", text: "매도" });
  }

  // ── 고급 차트 기법(2026-08-19 벤치마킹: Anchored VWAP·Liquidity Sweep·Volume Profile) ──
  // chart_pro는 일봉 기준으로만 계산돼 있어 주봉/월봉으로 바꾸면 앵커 날짜·봉 인덱스가
  // 안 맞는다 — 일봉(tf==="day")일 때만 그린다.
  const pro = chartCtx.pro || {};
  if (tf === "day" && pro.available) {
    // 1) 유동성 스윕 — 골든/데드크로스와 같은 마커 배열에 합쳐서 한 번에 setMarkers() 호출
    //    (lightweight-charts v4는 시리즈당 마커셋이 하나뿐이라 따로따로 부르면 덮어써진다).
    if (chartOverlayState.sweep && pro.liquidity_sweeps) {
      // 강도(strength 0~100)에 따라 마커 크기를 1~2배로 차등하고, 텍스트에 강도 숫자를
      // 같이 표기(호버 툴팁 대신 — lightweight-charts 마커엔 호버 이벤트가 없다). 실패
      // 스윕(3봉 안에 재돌파)은 원래 방향의 반대 화살표로 그려 "진짜 돌파"임을 구분한다
      // (설계서 19번).
      pro.liquidity_sweeps.events.forEach((ev) => {
        const time = toDate(ev.date);
        const size = 1 + (ev.strength || 50) / 100;
        if (ev.status === "breakout_flip") {
          const wasHigh = ev.type === "high_sweep";
          markers.push({ time, position: wasHigh ? "belowBar" : "aboveBar", color: wasHigh ? "#2ee6a6" : "#ff4d6d",
            shape: wasHigh ? "arrowUp" : "arrowDown", text: "돌파전환", size });
        } else if (ev.type === "high_sweep") {
          markers.push({ time, position: "aboveBar", color: "#ff4d6d", shape: "circle", text: `🧲${ev.strength}`, size });
        } else {
          markers.push({ time, position: "belowBar", color: "#2ee6a6", shape: "circle", text: `🧲${ev.strength}`, size });
        }
      });
    }
    // 2) 앵커드 VWAP — 각 앵커 지점부터 끝까지 이어지는 선. 사용자가 어떤 기준점 이후
    //    평균매수단가보다 지금이 위/아래인지 한눈에 보게 한다(브라이언 섀넌 방식).
    //    앵커 7종(2026-08-19 설계서 4-1 확장)마다 고유 색상을 주고, title로 우측 가격축에
    //    앵커명 라벨을 띄운다(lightweight-charts 내장 기능 — 별도 캔버스 텍스트 불필요).
    //    핵심 4개(신고저가·YTD·실적발표)는 굵은 실선, 나머지 3개(갭·거래량폭발·최근스윕)는
    //    가는 점선으로 그려 7개 선이 한꺼번에 겹쳐도 화면이 덜 어지럽게 한다
    //    (설계서 17번: "기본은 3개만 켜고 나머지는 토글"의 저비용 대안 — 전부 그리되
    //    시각적 위계로 구분).
    if (chartOverlayState.avwap && pro.avwap) {
      const AVWAP_COLOR = {
        "52주 신고가": "#ff6b9d", "52주 신저가": "#22d3ee", "연초(YTD)": "#f5c518",
        "최근 실적발표(근사)": "#c084fc", "갭 발생일": "#34d399", "거래량 폭발일": "#fb923c",
        "최근 스윕": "#60a5fa",
      };
      const PRIMARY_ANCHORS = new Set(["52주 신고가", "52주 신저가", "연초(YTD)", "최근 실적발표(근사)"]);
      Object.entries(pro.avwap.lines).forEach(([label, info]) => {
        const anchorIdx = d.candles.findIndex((c) => c.date === info.anchor_date);
        if (anchorIdx < 0 || !info.series || !info.series.length) return;
        const primary = PRIMARY_ANCHORS.has(label);
        // 4차 진단리포트 5장 — 우측 축에 신고저가·외국인평단·현재가·갭·스윕·컨플루언스·
        // 실적발표·연초·지지·신저가까지 라벨 열 개 가까이 겹쳐 판독이 어려웠다.
        // 보조 앵커(갭·거래량폭발·최근스윕) 3개는 선은 그대로 그리되 축 라벨은 끄고,
        // 핵심 4개(신고저가·YTD·실적발표)만 라벨을 남겨 겹침을 절반 이하로 줄인다.
        const line = chart.addLineSeries({
          color: AVWAP_COLOR[label] || "#a855f7",
          lineWidth: primary ? 2 : 1,
          lineStyle: primary ? LC.LineStyle.Solid : LC.LineStyle.Dotted,
          priceLineVisible: false, lastValueVisible: primary, crosshairMarkerVisible: false,
          title: label,
        });
        line.setData(info.series.map((v, i) => ({ time: toDate(d.candles[anchorIdx + i].date), value: v })));
      });
    }
  }
  markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));  // lightweight-charts는 시간 오름차순 필수
  if (markers.length) candleSeries.setMarkers(markers);

  const addPriceLine = (price, color, title) => {
    if (!price) return;
    candleSeries.createPriceLine({ price, color, lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title });
  };
  addPriceLine(d.targets.consensus, "#f6465d", "컨센서스 목표가");
  if (d.technical.available) {
    addPriceLine(d.technical.support, "#3e7bfa", "지지");
    addPriceLine(d.technical.resistance, "#9aa3ba", "저항");
  }

  // 수급 오더플로우 패널 — 누적 스마트머니(외국인+기관) 델타를 차트 하단 서브패널에 겹쳐
  // 그린다(설계서 20번). 별도 차트 인스턴스 없이 priceScaleId로 아래쪽 18%만 차지하게
  // 밀어 넣는 lightweight-charts 표준 트릭. 외국인 평단은 가격선으로 겹쳐 표시.
  if (tf === "day" && chartOverlayState.smart && pro.smart_money) {
    const sm = pro.smart_money;
    const smSeries = chart.addLineSeries({
      color: "#22d3ee", lineWidth: 1.5, priceScaleId: "smart-money",
      lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });
    chart.priceScale("smart-money").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, borderVisible: false });
    const days = sm.smart_delta_series.length;
    const startIdx = d.candles.length - days;
    if (startIdx >= 0) {
      smSeries.setData(sm.smart_delta_series.map((v, i) => ({ time: toDate(d.candles[startIdx + i].date), value: v })));
    }
    if (sm.foreign_avg_cost) addPriceLine(sm.foreign_avg_cost, "#c084fc", "외국인평단");
  }

  // 컨플루언스 레벨 — 근거 2개 이상 겹친 구간을 가격선으로 표시(설계서 21번; 겹칠수록
  // 굵게). 전용 밴드를 새로 그리는 대신 이미 검증된 목표가/지지/저항과 같은 가격선
  // 방식을 재사용한다.
  if (tf === "day" && pro.confluence) {
    // 4차 진단리포트 5장 — 우측 라벨 중첩 완화를 위해 5개→3개(가장 강한 것만)로 축소.
    pro.confluence.filter((c) => c.sources.length >= 2).slice(0, 3).forEach((c) => {
      const color = c.type === "지지" ? "#2ee6a6" : "#ff4d6d";
      const width = Math.min(1 + Math.floor(c.sources.length / 2), 3);
      candleSeries.createPriceLine({
        price: c.price, color, lineWidth: width, lineStyle: LC.LineStyle.LargeDashed,
        axisLabelVisible: true, title: `컨플루언스(${c.sources.length}겹)`,
      });
    });
  }

  // 기간 선택 — 봉 주기에 따라 기본 구간(bars 수)이 달라진다
  const len = d.candles.length;
  const PERIODS = {
    day:   [["3개월", 66], ["6개월", 125], ["1년", 250], ["3년", 750], ["5년", 1250], ["전체", 0]],
    week:  [["6개월", 26], ["1년", 52], ["2년", 104], ["3년", 156], ["5년", 260], ["전체", 0]],
    month: [["1년", 12], ["2년", 24], ["3년", 36], ["5년", 60], ["10년", 120], ["전체", 0]],
  };
  const defBars = { day: 125, week: 52, month: 24 }[tf];
  const setRange = (bars) => {
    if (!bars || bars >= len) chart.timeScale().fitContent();
    else chart.timeScale().setVisibleLogicalRange({ from: len - bars, to: len - 1 });
  };
  const periods = PERIODS[tf].filter(([, bars]) => bars === 0 || bars <= len * 1.1);
  $("chart-controls").innerHTML = periods.map(([label, bars]) =>
    `<button data-bars="${bars}" class="${bars === defBars ? "active" : ""}">${label}</button>`).join("");
  $("chart-controls").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      $("chart-controls").querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      setRange(+b.dataset.bars);
      drawVolumeProfile();
    };
  });

  // ── 볼륨 프로파일 오버레이 ─────────────────────────────────────────────
  // lightweight-charts엔 가격축 히스토그램(볼륨 프로파일) 기능이 없어, 캔들 시리즈의
  // priceToCoordinate()로 각 가격 구간을 실제 화면 y좌표로 변환해 별도 canvas에 직접
  // 그린다. 확대/축소·구간 이동으로 가격축 스케일이 바뀔 때마다 다시 그려야 막대 위치가
  // 안 어긋난다(subscribeVisibleLogicalRangeChange·기간 버튼·최초 렌더 시 호출).
  const vpCanvas = $("vp-overlay");
  function drawVolumeProfile() {
    const vp = pro.volume_profile;
    if (!(tf === "day" && chartOverlayState.vp && vp)) {
      vpCanvas.width = 0; vpCanvas.height = 0;
      vpCanvas.style.width = "0px"; vpCanvas.style.height = "0px";
      return;
    }
    const box = el.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const W = Math.round(box.width * 0.22), H = Math.round(box.height);
    vpCanvas.style.width = `${W}px`; vpCanvas.style.height = `${H}px`;
    vpCanvas.width = Math.round(W * dpr); vpCanvas.height = Math.round(H * dpr);
    const ctx2 = vpCanvas.getContext("2d");
    ctx2.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx2.clearRect(0, 0, W, H);

    const maxVol = Math.max(...vp.levels.map((l) => l.volume), 1);
    const light = document.body.classList.contains("light");
    vp.levels.forEach((lv) => {
      const y = candleSeries.priceToCoordinate(lv.price);
      if (y == null || y < 0 || y > H) return;
      const barW = Math.max(1, (lv.volume / maxVol) * (W - 4));
      const isPoc = Math.abs(lv.price - vp.poc) < (vp.levels[1] ? Math.abs(vp.levels[1].price - vp.levels[0].price) : 1) / 2 + 0.01;
      ctx2.fillStyle = isPoc
        ? "rgba(245,197,24,.55)"
        : (light ? "rgba(99,102,241,.22)" : "rgba(99,102,241,.28)");
      ctx2.fillRect(W - barW, y - 3, barW, 6);
    });
    // POC·가치영역(VAH/VAL) 라인
    const drawHLine = (price, color, dash) => {
      const y = candleSeries.priceToCoordinate(price);
      if (y == null) return;
      ctx2.strokeStyle = color; ctx2.lineWidth = 1;
      ctx2.setLineDash(dash);
      ctx2.beginPath(); ctx2.moveTo(0, y); ctx2.lineTo(W, y); ctx2.stroke();
      ctx2.setLineDash([]);
    };
    drawHLine(vp.poc, "#f5c518", []);
    drawHLine(vp.vah, "#9aa3ba", [3, 3]);
    drawHLine(vp.val, "#9aa3ba", [3, 3]);
  }
  if (tf === "day" && chartOverlayState.vp && pro.volume_profile) {
    drawVolumeProfile();
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => drawVolumeProfile());
  } else {
    vpCanvas.width = 0; vpCanvas.height = 0;
    vpCanvas.style.width = "0px"; vpCanvas.style.height = "0px";
  }

  // 오버레이 켜기/끄기 토글 — 상태만 바꾸고 차트 전체를 다시 그려 반영(가장 단순하고 안전).
  document.querySelectorAll("#chart-overlay-toggles .ovl-toggle").forEach((b) => {
    b.onclick = () => {
      const key = b.dataset.ovl;
      chartOverlayState[key] = !chartOverlayState[key];
      b.classList.toggle("active", chartOverlayState[key]);
      drawChart();
    };
  });

  // 크로스헤어 OHLC 정보 박스 — 기본은 최신 봉, 마우스를 올리면 그 시점 값으로 갱신
  // (TradingView류 차트의 표준 UX. 값 없이 색점+범례만 있던 것보다 훨씬 완성도 있어 보인다)
  const prevCloses = {};
  for (let i = 1; i < d.candles.length; i++) prevCloses[toDate(d.candles[i].date)] = d.candles[i - 1].close;
  // ⚠️ lightweight-charts v4의 seriesData.get()이 주는 값 객체엔 time이 없다(param.time에 따로
  // 있음) — bar.time으로 조회하면 항상 undefined라 전일종가 대비 등락이 절대 안 뜨는 버그가 됨.
  // time은 반드시 별도 인자로 받을 것.
  const updateOhlcLegend = (time, bar, vol) => {
    if (!bar) { $("chart-ohlc-legend").innerHTML = ""; return; }
    const prevClose = prevCloses[time];
    const cls = prevClose != null ? (bar.close >= prevClose ? "up" : "down") : (bar.close >= bar.open ? "up" : "down");
    const chg = prevClose != null ? (bar.close - prevClose) / prevClose * 100 : null;
    $("chart-ohlc-legend").innerHTML = `
      <span class="ol-name">${chartCtx.name}</span>
      <span class="ol-item">시가 <b class="${cls}">${pw(bar.open, chartCtx.currency)}</b></span>
      <span class="ol-item">고가 <b class="${cls}">${pw(bar.high, chartCtx.currency)}</b></span>
      <span class="ol-item">저가 <b class="${cls}">${pw(bar.low, chartCtx.currency)}</b></span>
      <span class="ol-item">종가 <b class="${cls}">${pw(bar.close, chartCtx.currency)}</b></span>
      ${chg != null ? `<span class="ol-chg ${cls}">${sign(chg, 2)}%</span>` : ""}
      ${vol != null ? `<span class="ol-item">거래량 <b>${fmt(vol)}</b></span>` : ""}`;
  };
  const showLatest = () => {
    if (!d.candles.length) return;
    const last = d.candles[d.candles.length - 1];
    const t = toDate(last.date);
    updateOhlcLegend(t, { open: last.open, high: last.high, low: last.low, close: last.close }, last.volume);
  };
  chart.subscribeCrosshairMove((param) => {
    const bar = param.seriesData && param.seriesData.get(candleSeries);
    const vol = param.seriesData && param.seriesData.get(volSeries);
    if (bar && param.time) updateOhlcLegend(param.time, bar, vol && vol.value);
    else showLatest();
  });
  showLatest();

  setRange(defBars);
}

/* ---------------- compare ---------------- */
let compareList = [];
let lastAnalysis = null;
const CMP_COLORS = ["#6366f1", "#22d3ee", "#ff6b9d"];

function addCompare(d) {
  if (!compareList.some((x) => x.code === d.code)) {
    if (compareList.length >= 3) { showError("비교는 최대 3종목까지 가능합니다."); return; }
    compareList.push({
      code: d.code, name: d.name, currency: d.currency, market: d.nation,
      price: d.price, grade: d.total.grade, score: d.total.total_score,
      categories: d.total.categories, metrics: d.metrics, upside: d.targets.consensus_upside,
    });
    const btn = $("compare-btn");
    btn.textContent = "✓ 담김"; setTimeout(() => (btn.textContent = "⚖️ 비교담기"), 1200);
  }
  renderCompareTray();
}
function removeCompare(code) {
  compareList = compareList.filter((x) => x.code !== code);
  renderCompareTray();
  if (!$("compare-view").classList.contains("hidden")) {
    compareList.length >= 2 ? showCompare() : goHome();
  }
}
function renderCompareTray() {
  const tray = $("compare-tray");
  if (!compareList.length) { tray.classList.add("hidden"); return; }
  tray.classList.remove("hidden");
  $("cmp-chips").innerHTML = compareList.map((x, i) =>
    `<span class="cmp-chip"><i style="background:${CMP_COLORS[i]}"></i>${x.name}<b data-rm="${x.code}">✕</b></span>`).join("");
  $("cmp-chips").querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => removeCompare(b.dataset.rm));
  $("cmp-go").textContent = `비교하기 (${compareList.length})`;
  $("cmp-go").disabled = compareList.length < 2;
}
function showCompare() {
  if (compareList.length < 2) { showError("2종목 이상 담아주세요."); return; }
  clearInterval(priceTimer);
  $("landing").classList.add("hidden");
  $("report").classList.add("hidden");
  $("portfolio-view").classList.add("hidden");
  $("admin-view").classList.add("hidden");
  $("screener-view").classList.add("hidden");
  $("compare-view").classList.remove("hidden");
  setActiveNav(null);
  window.scrollTo({ top: 0 });
  renderComparePick();
  drawCompareRadar();
  renderCompareTable();
}
function renderComparePick() {
  // 종합점수를 기본으로 하되, 목표주가 상승여력을 소폭 가감해 "가격 매력도"까지 반영한다
  // (점수는 같은데 이미 목표가를 넘어선 종목이 1등으로 뽑히는 걸 막기 위한 보정).
  let best = null, bestVal = -Infinity;
  compareList.forEach((x) => {
    const bonus = x.upside != null ? Math.max(-20, Math.min(x.upside, 40)) * 0.3 : 0;
    const val = x.score + bonus;
    if (val > bestVal) { bestVal = val; best = x; }
  });
  if (!best) return;
  $("cmp-pick-name").textContent = `🏆 ${best.name}`;
  $("cmp-pick-name").style.color = scoreColor(best.score);
  const upsideTxt = best.upside != null ? ` · 컨센서스 목표가 기준 상승여력 ${sign(best.upside, 1)}%` : "";
  $("cmp-pick-reason").textContent = `종합점수 ${best.score}점 (${best.grade}등급)${upsideTxt}`;
}
function drawCompareRadar() {
  const c = $("cmp-radar"), ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  const labels = Object.keys(compareList[0].categories);
  const n = labels.length, cx = 180, cy = 160, R = 105;
  const angle = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const grid = "rgba(150,160,190,.18)";
  for (let ring = 1; ring <= 4; ring++) {
    ctx.beginPath();
    for (let i = 0; i <= n; i++) { const a = angle(i % n), rr = R * ring / 4; const x = cx + rr * Math.cos(a), y = cy + rr * Math.sin(a); i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
    ctx.strokeStyle = grid; ctx.stroke();
  }
  for (let i = 0; i < n; i++) { const a = angle(i); ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + R * Math.cos(a), cy + R * Math.sin(a)); ctx.strokeStyle = grid; ctx.stroke(); }
  compareList.forEach((x, idx) => {
    const vals = labels.map((l) => x.categories[l]);
    ctx.beginPath();
    for (let i = 0; i <= n; i++) { const a = angle(i % n), rr = R * vals[i % n] / 100; const px = cx + rr * Math.cos(a), py = cy + rr * Math.sin(a); i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py); }
    ctx.fillStyle = CMP_COLORS[idx] + "22"; ctx.fill();
    ctx.strokeStyle = CMP_COLORS[idx]; ctx.lineWidth = 2; ctx.stroke(); ctx.lineWidth = 1;
  });
  ctx.fillStyle = getComputedStyle(document.body).getPropertyValue("--muted") || "#9aa3ba";
  ctx.font = "12px Pretendard, sans-serif"; ctx.textAlign = "center";
  for (let i = 0; i < n; i++) { const a = angle(i); ctx.fillText(labels[i], cx + (R + 26) * Math.cos(a), cy + (R + 20) * Math.sin(a) + 4); }
  $("cmp-legend").innerHTML = compareList.map((x, i) =>
    `<div class="cmp-leg"><i style="background:${CMP_COLORS[i]}"></i><span>${x.name}</span><b style="color:${scoreColor(x.score)}">${x.score}점 ${x.grade}</b></div>`).join("");
}
function renderCompareTable() {
  const L = compareList;
  const cats = Object.keys(L[0].categories);
  const row = (label, vals, fmtFn, best) => {
    let bi = -1;
    if (best) {
      const nums = vals.map((v) => (typeof v === "number" ? v : NaN));
      const valid = nums.filter((v) => !isNaN(v));
      if (valid.length) { const t = best === "max" ? Math.max(...valid) : Math.min(...valid); bi = nums.indexOf(t); }
    }
    return `<tr><th>${label}</th>${vals.map((v, i) => `<td class="${i === bi ? "cmp-best" : ""}">${fmtFn(v, i)}</td>`).join("")}</tr>`;
  };
  const u = (v, s) => (v == null ? "-" : v + s);
  let h = `<table><thead><tr><th>항목</th>${L.map((x, i) => `<th style="color:${CMP_COLORS[i]}">${x.name}</th>`).join("")}</tr></thead><tbody>`;
  h += row("종합점수", L.map((x) => x.score), (v) => `${v}점`, "max");
  h += row("등급", L.map((x) => x.grade), (v) => v, null);
  h += row("현재가", L.map((x) => x.price), (v, i) => pw(v, L[i].currency), null);
  cats.forEach((c) => (h += row(c, L.map((x) => x.categories[c]), (v) => (v == null ? "-" : v), "max")));
  h += row("PER", L.map((x) => x.metrics.per), (v) => u(v, "배"), "min");
  h += row("PBR", L.map((x) => x.metrics.pbr), (v) => u(v, "배"), "min");
  h += row("ROE", L.map((x) => x.metrics.roe), (v) => u(v, "%"), "max");
  h += row("배당수익률", L.map((x) => x.metrics.dividend_yield), (v) => u(v, "%"), "max");
  h += row("목표가 상승여력", L.map((x) => x.upside), (v) => (v == null ? "-" : sign(v, 1) + "%"), "max");
  h += "</tbody></table>";
  $("cmp-table").innerHTML = h;
}

/* ---------------- 밸류에이션 분석 ---------------- */
function renderValuation(v) {
  const card = $("val-card");
  if (!v || !v.available) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");

  $("val-score").textContent = `${v.score}점`;
  $("val-score").style.color = scoreColor(v.score);
  $("val-verdict").innerHTML = `<span class="verdict ${v.verdict_class}">${v.verdict}</span>`;

  $("val-parts").innerHTML = Object.entries(v.parts || {}).map(([k, s]) =>
    `<div class="tp-bar"><span class="tp-label wide">${k}</span>
       <span class="tp-track"><i style="width:${s}%;background:${scoreColor(s)}"></i></span>
       <span class="tp-val">${s}</span></div>`).join("");

  /* 1순위 — 역사적 밸류 비교 배너 */
  const b = v.band;
  if (b) {
    const pct = (b.ratio - 1) * 100;
    const cls = b.ratio > 1.15 ? "down" : b.ratio < 0.85 ? "up" : "";
    $("val-band").innerHTML = `
      <div class="vb-main">
        <div class="vb-col"><label>현재 ${b.kind}</label><b>${b.current}배</b></div>
        <div class="vb-vs">vs</div>
        <div class="vb-col"><label>과거 ${b.years}년 평균 ${b.kind}</label><b>${b.hist_avg}배</b></div>
        <div class="vb-col ratio"><label>배수</label>
          <b class="${cls}">${b.ratio}배 (${sign(pct, 0)}%)</b></div>
      </div>
      <p class="vb-note ${cls}">${b.label}</p>`;
    $("val-band").classList.remove("hidden");
  } else $("val-band").classList.add("hidden");

  /* 연도별 PER 이력 */
  const h = v.history;
  if (h && h.years && h.years.length) {
    $("val-history").innerHTML = tableHTML(
      ["연도", "EPS", "연평균 주가", "PER", "선행 PER"],
      h.years.map((y) => [
        `${y.year}${y.consensus ? " <small>(E)</small>" : ""}`,
        fmt(y.eps),
        fmt(y.avg_price),
        y.per != null ? `${y.per}배` : "-",
        y.fper != null ? `${y.fper}배` : "-",
      ]));
  } else $("val-history").innerHTML = "";

  /* 과거 유사 밸류에이션 시점 백테스트 */
  const bt = v.backtest;
  if (bt && bt.available) {
    $("backtest-box").classList.remove("hidden");
    const rows = bt.matches.map((m) =>
      `<div class="bt-row"><span class="bt-year">${m.year}년</span>
         <span class="bt-per">PER ${m.per}배 (${pw(m.avg_price)})</span>
         <span class="bt-arrow">→ 1년 후</span>
         <span class="bt-ret ${updownClass(m.return_1y)}">${sign(m.return_1y, 1)}%</span></div>`).join("");
    $("backtest-body").innerHTML = `
      <p class="hint-p">현재 PER ${bt.current_per}배와 비슷했던 과거 ${bt.matches.length}개 시점 기준
        (완결된 연도만 사용, 진행 중인 해는 제외)</p>
      <div class="bt-list">${rows}</div>
      <div class="bt-summary">
        <div class="bt-sum-item"><label>평균 1년 후 수익률</label>
          <b class="${updownClass(bt.avg_return_1y)}">${sign(bt.avg_return_1y, 1)}%</b></div>
        <div class="bt-sum-item"><label>상승 확률</label><b>${bt.win_rate}%</b></div>
      </div>
      ${bt.matches.length < 2 ? '<p class="hint-p">⚠️ 최근 5년 데이터 안에서 비교 가능한 시점이 1개뿐이라 참고용입니다.</p>' : ""}`;
  } else if (bt) {
    $("backtest-box").classList.remove("hidden");
    $("backtest-body").innerHTML = `<p class="hint-p">${
      v.history && v.history.from_per_row
        ? "미국 종목은 연도별 평균주가 데이터가 없어 이 비교를 제공하지 않습니다."
        : "최근 5년 안에서 현재와 비슷한 밸류에이션 시점을 찾지 못했습니다 (현재가 과거 대비 이례적인 수준일 수 있습니다)."
    }</p>`;
  } else {
    $("backtest-box").classList.add("hidden");
  }

  /* 핵심 지표 카드 */
  const cells = [];
  const c = v.current || {};
  cells.push(["현재 PER / FPER",
    `${c.per != null ? c.per.toFixed(1) : "-"} / ${c.fper != null ? c.fper.toFixed(1) : "-"}배`,
    c.fper != null ? "FPER=컨센서스 기준 선행" : "선행 PER 미제공"]);
  if (h) cells.push([`과거 평균 PER / 선행PER`,
    `${h.avg_per ?? "-"} / ${h.avg_fper ?? "-"}배`,
    `실적 ${h.per_count}년 기준${h.from_per_row ? " · 공시 PER 사용" : " · 연평균 주가 기반"}`]);
  if (v.peg) cells.push(["PEG",
    `<span class="${v.peg.peg <= 1 ? "up" : v.peg.peg > 1.5 ? "down" : ""}">${v.peg.peg}</span>`,
    `${v.peg.per_used}배 ÷ 성장 ${v.peg.growth_used}%${v.peg.capped ? ` (실제 ${v.peg.growth_raw}% → 상한 적용)` : ""} — ${v.peg.label}`]);
  if (v.peer) cells.push(["동종업계 PER",
    `${v.peer.my_per} vs ${v.peer.peer_avg}배`,
    `${v.peer.label} · 업종 ${v.peer.is_median ? "중앙값" : "평균"}`]);
  if (v.ev_ebitda) cells.push(["EV/EBITDA",
    `${v.ev_ebitda.ev_ebitda}배`, v.ev_ebitda.basis]);
  $("val-grid").innerHTML = cells.map(([l, val, sub]) =>
    `<div class="pro-item"><label>${l}</label><div class="pro-val">${val}</div><small>${sub}</small></div>`).join("");

  /* 동종업계 상세 (있으면 이력 표 아래 붙임) */
  if (v.peer && v.peer.peers && v.peer.peers.length) {
    $("val-history").innerHTML += `<div class="peer-per">` +
      v.peer.peers.map((p) =>
        `<span class="pp ${p.self ? "self" : ""}">${p.name} <b>${p.per.toFixed(1)}배</b></span>`).join("") +
      `</div>`;
  }

  /* 6항목 체크리스트 */
  $("val-check").innerHTML = (v.checklist || []).map((c) =>
    `<div class="vc-row ${c.ok ? "ok" : "no"}">
       <span class="vc-mark">${c.ok ? "✅" : "⚠️"}</span>
       <span class="vc-item">${c.item}</span>
       <span class="vc-verdict">${c.verdict}</span>
       <small class="vc-detail">${c.detail}</small>
     </div>`).join("");

  $("val-signals").innerHTML = (v.signals || []).map((s) => `<li class="${s.type}">${s.text}</li>`).join("");
  $("val-note").textContent =
    "※ 과거 선행 PER은 '그 해 평균주가 ÷ 다음 해 실제 EPS'로 계산한 실현 선행 PER입니다. " +
    "과거 시점의 컨센서스 추정치는 제공되지 않아 결과값으로 대체했습니다.";
}

/* ---------------- 고급 차트 분석 ---------------- */
function renderChartPro(p) {
  const card = $("pro-card"), title = $("pro-head-title");
  if (!p || !p.available) {
    card.classList.add("hidden");
    if (title) title.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  if (title) title.classList.remove("hidden");

  $("pro-score").textContent = `${p.score}점`;
  $("pro-score").style.color = scoreColor(p.score);
  if (p.stage) {
    const cls = p.stage.stage === 2 ? "up" : p.stage.stage === 4 ? "down" : "";
    $("pro-stage").innerHTML = `<span class="${cls}">${p.stage.label}</span>`;
  } else $("pro-stage").textContent = "";

  /* 기법별 점수 바 — 종합점수 반영 비중(%)을 라벨에 병기(설계서 16번: 없는 항목이 있으면
     재분배된 실제 비중이 그대로 드러나야 함, 예: 미국 종목은 수급오더플로우가 아예 빠져
     나머지 항목 비중이 커짐). */
  const wpct = p.weight_pct || {};
  $("pro-parts").innerHTML = Object.entries(p.parts || {}).map(([k, v]) =>
    `<div class="tp-bar"><span class="tp-label wide">${k}${wpct[k] != null ? ` <small class="hint">(${wpct[k]}%)</small>` : ""}</span>
       <span class="tp-track"><i style="width:${v}%;background:${scoreColor(v)}"></i></span>
       <span class="tp-val">${v}</span></div>`).join("");

  /* 핵심 지표 카드 */
  const cells = [];
  if (p.relative_strength) {
    const rs = p.relative_strength;
    cells.push(["상대강도(시장대비)", `<span class="${rs.excess >= 0 ? "up" : "down"}">${sign(rs.excess, 1)}%p</span>`,
                `3개월 ${sign(rs.detail["63d"] ?? 0, 1)}%p · 6개월 ${sign(rs.detail["126d"] ?? 0, 1)}%p`]);
  }
  if (p.fibonacci) cells.push(["피보나치 되돌림", `${p.fibonacci.retrace_pct}%`, p.fibonacci.zone]);
  if (p.box) cells.push([p.box.reliable ? "박스권" : "박스권 <span class=\"down\">⚠️ 추세 구간</span>", `${fmt(p.box.bottom)} ~ ${fmt(p.box.top)}`,
    p.box.reliable ? (p.box.breakout ? "상단 돌파 중" : p.box.breakdown ? "하단 이탈" : `폭 ${p.box.width_pct}%`) : `폭 ${p.box.width_pct}% — 박스권 아님, 참고용`]);
  if (p.atr_pct != null) cells.push(["ATR 변동성", `${p.atr_pct}%`,
    p.atr_pct > 5 ? "고변동 — 비중 축소 권장" : "일간 평균 등락폭"]);
  if (p.obv) cells.push(["OBV 자금흐름", `<span class="${p.obv.slope >= 0 ? "up" : "down"}">${sign(p.obv.slope, 1)}</span>`,
    p.obv.divergence === "bullish" ? "강세 다이버전스(매집)" : p.obv.divergence === "bearish" ? "약세 다이버전스(분산)" : "추세 동행"]);
  if (p.disparity) cells.push(["이격도(20/60/120)",
    `${sign(p.disparity["20"] ?? 0, 1)}% / ${sign(p.disparity["60"] ?? 0, 1)}% / ${sign(p.disparity["120"] ?? 0, 1)}%`,
    "이동평균 대비 괴리율"]);
  if (p.vcp) cells.push(["VCP 변동성 수축", p.vcp.contracting ? "수축 진행" : "미형성",
    `구간 변동폭 ${(p.vcp.ranges || []).join("% → ")}%`]);
  // ── 2026-08-19 벤치마킹 추가: Anchored VWAP · Liquidity Sweep · Volume Profile · Order Flow ──
  if (p.avwap) {
    const rows = Object.entries(p.avwap.lines).map(([label, v]) =>
      `${label} <b class="${v.above ? "up" : "down"}">${v.above ? "위" : "아래"}</b>`).join(" · ");
    cells.push(["앵커드 VWAP(AVWAP)", `${p.avwap.above_count}/${p.avwap.total} 위`, rows]);
  }
  if (p.liquidity_sweeps) {
    const ls = p.liquidity_sweeps;
    const last = ls.recent[ls.recent.length - 1];
    cells.push(["유동성 스윕(최근)",
      last ? `<span class="${last.type === "low_sweep" ? "up" : "down"}">${last.type === "low_sweep" ? "저점 스윕↑" : "고점 스윕↓"}</span>` : "최근 없음",
      last ? `${last.date.slice(0, 4)}-${last.date.slice(4, 6)}-${last.date.slice(6, 8)} · ${fmt(last.level)} 부근` : `최근 ${ls.events.length ? "스윕 감지됨(오래 전)" : "감지 안됨"}`]);
  }
  if (p.volume_profile) {
    const vp = p.volume_profile;
    // 4차 진단리포트 P0-2 — reliability=low인데 카드엔 해석 문구만 보이고 경고가 안 보이면
    // 플래그를 단 의미가 없다. low일 땐 값 칸 자체에 "⚠️ 신뢰도 낮음"을 노출한다.
    const vpVal = vp.reliability === "low" ? `${fmt(vp.poc)} <span class="down">⚠️ 신뢰도 낮음</span>` : fmt(vp.poc);
    // 4차 진단리포트 4-2 — 룩백을 20/60/120일 중 신뢰도가 확보되는 최단 창으로 자동
    // 선택하므로, 어느 창을 쓴 결과인지 병기해야 "왜 이 가치영역인지"가 명확하다.
    cells.push(["볼륨 프로파일 POC", vpVal, `${vp.lookback_days}일 기준 · 가치영역 ${fmt(vp.val)}~${fmt(vp.vah)} · ${vp.position}`]);
  }
  if (p.smart_money) {
    // ⚠️ 예전 "오더플로우 근사"(CLV×거래량)는 캔들 몸통·꼬리를 다시 쓴 것뿐이라 새 정보가
    // 없었다(설계서 지적, 실측 최저점수). 한국거래소만 공개하는 외국인+기관 순매수
    // 누적델타로 교체 — 미국 서비스가 절대 만들 수 없는 한국형 지표.
    const sm = p.smart_money;
    const up = sm.smart_delta_series[sm.smart_delta_series.length - 1] > sm.smart_delta_series[0];
    cells.push(["수급 오더플로우 <span class=\"info-dot\" tabindex=\"0\">ⓘ<span class=\"tooltip-pop\">외국인+기관 순매수 누적델타(최근 60영업일). 체결 데이터는 아니지만 한국거래소만 공개하는 실제 수급 데이터입니다.</span></span>",
      `<span class="${up ? "up" : "down"}">${up ? "매수세 우위" : "매도세 우위"}</span>${sparklineSvg(sm.smart_delta_series)}`,
      sm.divergence === "bullish" ? "가격↓ 델타↑ 강세 다이버전스" : sm.divergence === "bearish" ? "가격↑ 델타↓ 약세 다이버전스" : "가격과 동행"]);
    if (sm.foreign_avg_cost) {
      cells.push(["외국인 추정 평균단가", pw(sm.foreign_avg_cost),
        `현재가 대비 ${sign(sm.foreign_avg_cost_upside, 1)}% ${sm.foreign_avg_cost_upside >= 0 ? "이익" : "손실"} 구간 · 연속매수 ${sm.streak_foreign}일`]);
    }
  }
  $("pro-grid").innerHTML = cells.map(([label, val, sub]) =>
    `<div class="pro-item"><label>${label}</label><div class="pro-val">${val}</div><small>${sub}</small></div>`).join("");

  // 컨플루언스 — 서로 다른 근거 2개 이상 겹친 구간만 노출(1개짜리는 노이즈라 생략).
  const confBox = $("confluence-box");
  const conf = (p.confluence || []).filter((c) => c.sources.length >= 2);
  if (conf.length) {
    confBox.classList.remove("hidden");
    $("confluence-list").innerHTML = conf.map((c) => `
      <div class="confluence-row ${c.type === "지지" ? "up" : "down"}">
        <span class="confluence-price">${pw(c.price)}</span>
        <span class="confluence-type">${c.type}</span>
        <span class="confluence-score">신뢰도 ${c.score.toFixed(1)}</span>
        <span class="confluence-sources">${c.sources.join(" + ")} (${c.sources.length}중 겹침)</span>
      </div>`).join("");
  } else {
    confBox.classList.add("hidden");
  }

  /* 추세 템플릿 체크리스트 */
  const tt = p.trend_template;
  if (tt) {
    $("tt-count").textContent = `${tt.passed}/${tt.total} 충족`;
    $("tt-list").innerHTML = tt.checks.map((c) =>
      `<li class="${c.ok ? "ok" : "no"}">${c.ok ? "✅" : "⬜"} ${c.text}</li>`).join("");
  }

  $("pro-signals").innerHTML = (p.signals || []).map((s) => `<li class="${s.type}">${s.text}</li>`).join("");
}


/* ---------------- finance ---------------- */
const _UNIT_LABEL = { eok: "억", pct: "%", x: "배", won: "원" };

function renderFinance(rows) {
  // rows: { 표시이름: { unit, series:[{period,value,consensus}] } }
  // ⚠️ eok(억) 단위는 국내=원, 미국=달러 그대로다(app/analysis.py _highlight_rows가 미국을
  // 이미 100으로 나눠 "억 달러" 스케일로 맞춰둠) — 라벨을 "억원"으로 고정하면 미국 종목은
  // 통화가 완전히 틀려 보인다(실제 발생 확인: AAPL 매출 "4,162"가 "억원"으로 보이면 약
  // $290M로 오인되지만 실제는 "억 달러" 즉 $416B — 1400배 차이).
  $("finance-unit-hint").textContent = curCur === "USD" ? "단위: 억 달러 · (E)는 컨센서스" : "단위: 억원 · (E)는 컨센서스";
  const cnsFlagged = lastAnalysis && lastAnalysis.metrics && lastAnalysis.metrics.consensus_flagged;
  const warnEl = $("finance-cns-warn");
  if (cnsFlagged) {
    warnEl.classList.remove("hidden");
    warnEl.textContent = `⚠️ (E) 컨센서스 추정치가 이상치로 감지됨 — ${lastAnalysis.metrics.consensus_flag_reason || ""}. 점수 반영에서 제외했으며, 아래 값은 검증 전 원본입니다.`;
  } else {
    warnEl.classList.add("hidden");
    warnEl.innerHTML = "";
  }
  const S = (name) => (rows[name] && rows[name].series) || [];
  const rev = S("매출액"), op = S("영업이익");
  const c = $("finance-chart"), ctx = c.getContext("2d");
  c.width = c.parentElement.clientWidth - 48;
  ctx.clearRect(0, 0, c.width, c.height);

  // 기간 축은 가장 긴 시계열 기준 (미국은 매출액 없을 수도 있어 방어)
  let base = rev.length ? rev : op;
  const rowNames = Object.keys(rows).filter((k) => S(k).length);
  if (!base.length && rowNames.length) base = S(rowNames[0]);
  const periods = base.map((r) => r.period);
  const cnsFlags = base.map((r) => r.consensus);
  if (periods.length === 0) { $("finance-table").innerHTML = "<p class='hint-p'>재무 데이터가 없습니다.</p>"; return; }

  // ── 매출/영업이익 막대 차트 ──
  const vals = [...rev, ...op].map((r) => r.value).filter((v) => v != null);
  if (vals.length) {
    const maxV = Math.max(...vals, 1), minV = Math.min(...vals, 0);
    const range = maxV - minV || 1;
    const W = c.width, H = c.height, pad = 30;
    const groupW = (W - pad * 2) / periods.length;
    const y = (v) => H - 30 - ((v - minV) / range) * (H - 60);
    ctx.strokeStyle = "#1f2635";
    ctx.beginPath(); ctx.moveTo(pad, y(0)); ctx.lineTo(W - pad, y(0)); ctx.stroke();
    periods.forEach((p, i) => {
      const x0 = pad + i * groupW, bw = Math.min(groupW / 3.2, 34);
      const rv = rev[i]?.value, ov = op[i]?.value, isCns = cnsFlags[i];
      if (rv != null) { ctx.fillStyle = isCns ? "rgba(79,140,255,.45)" : "#4f8cff"; ctx.fillRect(x0 + groupW / 2 - bw - 3, Math.min(y(rv), y(0)), bw, Math.abs(y(rv) - y(0))); }
      if (ov != null) { ctx.fillStyle = isCns ? "rgba(46,204,113,.45)" : "#2ecc71"; ctx.fillRect(x0 + groupW / 2 + 3, Math.min(y(ov), y(0)), bw, Math.abs(y(ov) - y(0))); }
      ctx.fillStyle = "#8a93a6"; ctx.font = "11px sans-serif"; ctx.textAlign = "center";
      ctx.fillText(`${p.slice(0, 4)}${isCns ? "(E)" : ""}`, x0 + groupW / 2, H - 10);
    });
    ctx.textAlign = "left";
    ctx.fillStyle = "#4f8cff"; ctx.fillRect(pad, 6, 10, 10);
    ctx.fillStyle = "#8a93a6"; ctx.fillText("매출액", pad + 14, 15);
    ctx.fillStyle = "#2ecc71"; ctx.fillRect(pad + 70, 6, 10, 10);
    ctx.fillStyle = "#8a93a6"; ctx.fillText("영업이익", pad + 84, 15);
  }

  // ── 연도별 지표 표 (매출·이익 + PER/PBR/ROE 등 밸류·수익성 추이) ──
  const fmtCell = (v, unit) => {
    if (v == null) return "-";
    if (unit === "pct") return `${fmt(v, 1)}%`;
    if (unit === "x") return `${fmt(v, 2)}배`;
    if (unit === "won") return fmt(v, 0);
    return fmt(v, 0);       // eok
  };
  const header = ["항목", ...periods.map((p, i) => `${p.slice(0, 4)}${cnsFlags[i] ? "(E)" : ""}`)];
  const bodyRows = rowNames.map((name) => {
    const { unit, series } = rows[name];
    const label = _UNIT_LABEL[unit] && unit !== "eok" ? `${name} <small>(${_UNIT_LABEL[unit]})</small>` : name;
    // period 정렬: base periods 순서에 맞춰 값 매핑
    const byPeriod = {};
    series.forEach((s) => { byPeriod[s.period] = s.value; });
    const lowerBetter = ["PER", "PBR", "부채비율"].includes(name);
    const higherBetter = ["ROE", "ROA", "영업이익률", "순이익률", "EPS", "매출액", "영업이익", "당기순이익"].includes(name);
    const cells = periods.map((p, i) => {
      const v = byPeriod[p];
      if (v == null) return "-";
      const txt = fmtCell(v, unit);
      if (unit === "eok" || unit === "won") return txt;
      // 전년 대비 방향 색상 (밸류지표만)
      const pv = i > 0 ? byPeriod[periods[i - 1]] : null;
      if (pv == null || v === pv) return txt;
      const up = v > pv;
      const good = higherBetter ? up : lowerBetter ? !up : null;
      if (good === null) return txt;
      return `<span class="${good ? "fin-good" : "fin-bad"}">${txt}${up ? " ▲" : " ▼"}</span>`;
    });
    return [label, ...cells];
  });
  $("finance-table").innerHTML = tableHTML(header, bodyRows);
}

/* ---------------- AI report ---------------- */
$("ai-btn").onclick = async () => {
  const btn = $("ai-btn");
  btn.disabled = true;
  btn.textContent = "Claude가 분석 중입니다... (최대 1~2분)";
  try {
    const { report } = await api(`/api/ai/report/${currentCode}`, { method: "POST" });
    $("ai-report").innerHTML = mdToHtml(report);
    $("ai-report").classList.remove("hidden");
  } catch (e) {
    showError("AI 리포트 생성 실패: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "AI 심층 분석 생성";
  }
};

function mdToHtml(md) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");

  const lines = (md || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let list = null;   // "ul" | "ol" | null
  let para = [];     // 현재 문단 줄 모음

  const flushPara = () => {
    if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; }
  };
  const flushList = () => { if (list) { out.push(`</${list}>`); list = null; } };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) { flushPara(); flushList(); continue; }

    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      // #→h2 부터 시작(카드 제목과 충돌 방지)
      flushPara(); flushList();
      const lvl = Math.min(m[1].length + 1, 4);
      out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flushPara();
      if (list !== "ol") { flushList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
      flushPara();
      if (list !== "ul") { flushList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara(); flushList();
  return out.join("");
}

/* ---------------- auth ---------------- */
let currentUser = null;
let authMode = "login";

function renderAuthUI() {
  const loggedIn = !!currentUser;
  $("login-btn").classList.toggle("hidden", loggedIn);
  $("user-chip").classList.toggle("hidden", !loggedIn);
  syncHomeForUser();
  if (loggedIn) {
    $("user-name").textContent = currentUser.username;
    $("admin-link").classList.toggle("hidden", !currentUser.is_admin);
  }
}

async function loadMe() {
  try {
    const r = await api("/api/auth/me");
    currentUser = r.user;
  } catch {
    currentUser = null;
  }
  renderAuthUI();
  await loadWatchlist();
  await migrateLegacyFavs();
  updateFavBtn();
  updateWatchBtn();
  renderFavBoard();
  loadMyPortfolioWidget();
}

function openAuthModal(mode) {
  authMode = mode;
  document.querySelectorAll(".auth-tab").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  $("auth-submit").textContent = mode === "login" ? "로그인" : "회원가입";
  $("auth-confirm-wrap").classList.toggle("hidden", mode !== "signup");
  $("auth-msg").textContent = "";
  $("auth-username").value = "";
  $("auth-password").value = "";
  $("auth-password-confirm").value = "";
  $("auth-modal").classList.remove("hidden");
}

$("login-btn").onclick = () => openAuthModal("login");
$("auth-close").onclick = () => $("auth-modal").classList.add("hidden");
$("auth-modal").addEventListener("click", (e) => {
  if (e.target === $("auth-modal")) $("auth-modal").classList.add("hidden");
});
document.querySelectorAll(".auth-tab").forEach((b) => {
  b.onclick = () => openAuthModal(b.dataset.mode);
});
$("auth-submit").onclick = async () => {
  const username = $("auth-username").value.trim().toLowerCase();
  const password = $("auth-password").value;
  if (!username || !password) { $("auth-msg").textContent = "아이디와 비밀번호를 모두 입력하세요."; return; }
  if (authMode === "signup" && password !== $("auth-password-confirm").value) {
    $("auth-msg").textContent = "비밀번호 확인이 일치하지 않습니다.";
    return;
  }
  $("auth-msg").textContent = "처리 중...";
  try {
    const r = await api(authMode === "login" ? "/api/auth/login" : "/api/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    currentUser = r.user;
    renderAuthUI();
    $("auth-modal").classList.add("hidden");
    await loadWatchlist();
    await migrateLegacyFavs();
    updateFavBtn();
    updateWatchBtn();
    renderFavBoard();
    loadMyPortfolioWidget();
  } catch (e) {
    $("auth-msg").textContent = "오류: " + e.message;
  }
};
$("logout-btn").onclick = async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch {}
  currentUser = null;
  watchedCodes = new Set();
  watchMap = {};
  renderAuthUI();
  updateFavBtn();
  updateWatchBtn();
  renderFavBoard();
  loadMyPortfolioWidget();
  if (!$("admin-view").classList.contains("hidden")) goHome();
};

/* ---------------- 내 정보 / 비밀번호 변경 ---------------- */
$("myinfo-link").onclick = () => {
  if (!currentUser) return;
  $("myinfo-username").textContent = currentUser.username;
  $("myinfo-current").value = "";
  $("myinfo-new").value = "";
  $("myinfo-new-confirm").value = "";
  $("myinfo-msg").textContent = "";
  $("myinfo-modal").classList.remove("hidden");
};
$("myinfo-close").onclick = () => $("myinfo-modal").classList.add("hidden");
$("myinfo-modal").addEventListener("click", (e) => {
  if (e.target === $("myinfo-modal")) $("myinfo-modal").classList.add("hidden");
});
$("myinfo-submit").onclick = async () => {
  const current = $("myinfo-current").value;
  const next = $("myinfo-new").value;
  const confirm = $("myinfo-new-confirm").value;
  if (!current || !next) { $("myinfo-msg").textContent = "현재/새 비밀번호를 모두 입력하세요."; return; }
  if (next !== confirm) { $("myinfo-msg").textContent = "새 비밀번호 확인이 일치하지 않습니다."; return; }
  $("myinfo-msg").textContent = "변경 중...";
  try {
    await api("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: current, new_password: next }),
    });
    $("myinfo-msg").textContent = "비밀번호가 변경되었습니다.";
    $("myinfo-msg").style.color = "#2ecc71";
  } catch (e) {
    $("myinfo-msg").textContent = "오류: " + e.message;
    $("myinfo-msg").style.color = "";
  }
};

/* ---------------- 관심종목 로드 (웹푸시 알림 조건 포함) ---------------- */
async function loadWatchlist() {
  if (!currentUser) { watchedCodes = new Set(); watchMap = {}; return; }
  try {
    const r = await api("/api/watch");
    watchedCodes = new Set(r.items.map((it) => it.code));
    watchMap = {};
    r.items.forEach((it) => { watchMap[it.code] = it; });
  } catch {
    watchedCodes = new Set();
    watchMap = {};
  }
}

// ⚠️ ★/🔔 저장소를 서버로 통합하면서, 예전에 localStorage에만 담겨 있던 관심종목이
// 화면에서 안 보이게 됐다(사용자 지적: "관심종목을 아예 날리면 어떻게 해"). 데이터는
// 브라우저에 그대로 남아있으므로 로그인 시 서버로 1회 자동 이전한다 — 기기별로
// stocklens_favs_migrated 플래그로 중복 이전을 막는다(로컬스토리지 원본은 안전망으로 남겨둠).
const LEGACY_FAV_KEY = "stocklens_favs";
const LEGACY_FAV_MIGRATED_KEY = "stocklens_favs_migrated";
async function migrateLegacyFavs() {
  if (!currentUser || localStorage.getItem(LEGACY_FAV_MIGRATED_KEY)) return;
  let legacy = [];
  try { legacy = JSON.parse(localStorage.getItem(LEGACY_FAV_KEY)) || []; } catch {}
  if (!legacy.length) { localStorage.setItem(LEGACY_FAV_MIGRATED_KEY, "1"); return; }
  let restored = 0;
  for (const f of legacy) {
    if (!f.code || watchedCodes.has(f.code)) continue;
    try {
      await addToWatch(f.code, f.name || f.code, null, null, null, null);
      restored++;
    } catch {}
  }
  localStorage.setItem(LEGACY_FAV_MIGRATED_KEY, "1");
  if (restored) showToast(`⭐ 예전 관심종목 ${restored}개를 복구했습니다.`);
}

function showToast(text, kind) {
  let t = $("app-toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "app-toast";
    t.className = "app-toast";
    // 스크린리더가 토스트 내용을 읽도록 라이브 리전으로 선언(진단보고서 6장: role 0개).
    t.setAttribute("role", "status");
    t.setAttribute("aria-live", "polite");
    document.body.appendChild(t);
  }
  t.textContent = text;
  t.classList.toggle("err", kind === "error");
  t.setAttribute("role", kind === "error" ? "alert" : "status");
  t.classList.add("show");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => t.classList.remove("show"), kind === "error" ? 6000 : 4500);
}
// 브라우저 기본 alert()는 페이지를 멈춰 세우고 디자인도 통제할 수 없어, 이미 도입한
// 토스트로 피드백 방식을 하나로 통일한다(진단보고서 7장 — alert 9건·토스트 7건 혼재).
function showError(text) { showToast(text, "error"); }

function urlBase64ToUint8Array(base64) {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

// 알림 권한 요청은 브라우저에 따라 응답이 영영 안 올 수 있다(사용자 상호작용 맥락이
// 끊기면 브라우저가 프롬프트를 무시하는 경우 등) — 이때 화면은 "요청하는 중..."에서
// 영원히 멈추고 성공도 실패도 알 수 없었다(2차 진단리포트 3-1, 가장 심각한 지적).
// 타임아웃으로 반드시 결과가 나오도록 강제한다.
function withTimeout(promise, ms, timeoutMsg) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(timeoutMsg)), ms)),
  ]);
}

async function ensurePushSubscribed() {
  if (!window.isSecureContext) throw new Error("HTTPS로 접속해야 알림을 켤 수 있습니다.");
  if (!pushSupported()) throw new Error("이 브라우저는 웹 알림을 지원하지 않습니다.");
  if (Notification.permission === "denied") {
    throw new Error("이 브라우저에서 알림이 차단되어 있습니다. 주소창 옆 자물쇠 아이콘에서 알림 권한을 허용으로 바꿔주세요.");
  }
  const permission = await withTimeout(
    Notification.requestPermission(),
    15000,
    "알림 권한 요청에 응답이 없습니다. 브라우저 알림 설정을 확인해주세요.",
  );
  if (permission !== "granted") throw new Error(`알림 권한이 허용되지 않았습니다 (${permission}).`);
  const reg = await withTimeout(
    navigator.serviceWorker.register("/sw.js", { scope: "/" }), 10000, "알림 서비스 등록이 지연되고 있습니다.");
  await withTimeout(navigator.serviceWorker.ready, 10000, "알림 서비스 준비가 지연되고 있습니다.");
  const key = (await (await fetch("/api/push/key")).json()).key;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await withTimeout(
      reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) }),
      10000, "푸시 구독 생성이 지연되고 있습니다.");
  }
  await api("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sub.toJSON()),
  });
}

$("watch-btn").onclick = () => { if (currentCode) openWatchSettings(currentCode); };

/* ---------------- admin ---------------- */
async function showAdmin() {
  $("landing").classList.add("hidden");
  $("report").classList.add("hidden");
  $("compare-view").classList.add("hidden");
  $("portfolio-view").classList.add("hidden");
  $("screener-view").classList.add("hidden");
  $("admin-view").classList.remove("hidden");
  setActiveNav(null);
  window.scrollTo({ top: 0 });
  try {
    const r = await api("/api/admin/users");
    const s = r.stats;
    $("admin-stats").innerHTML = `
      <div class="pro-item"><label>총 가입자</label><div>${fmt(s.total)}명</div></div>
      <div class="pro-item"><label>최근 24시간 가입</label><div>${fmt(s.signups_24h)}명</div></div>
      <div class="pro-item"><label>최근 7일 가입</label><div>${fmt(s.signups_7d)}명</div></div>
      <div class="pro-item"><label>최근 24시간 접속</label><div>${fmt(s.active_24h)}명</div></div>`;
    $("admin-users-table").innerHTML = `<table><thead><tr>
      <th>아이디</th><th>가입일</th><th>최근 로그인</th><th>관리자</th>
      </tr></thead><tbody>${r.users.map((u) => `<tr>
        <td>${u.username}</td>
        <td>${new Date(u.created_at * 1000).toLocaleString("ko-KR")}</td>
        <td>${u.last_login ? new Date(u.last_login * 1000).toLocaleString("ko-KR") : "-"}</td>
        <td>${u.is_admin ? "👑" : ""}</td>
      </tr>`).join("")}</tbody></table>`;
  } catch (e) {
    $("admin-stats").innerHTML = `<p class="hint-p">불러오기 실패: ${e.message}</p>`;
  }
}
$("admin-link").onclick = showAdmin;
$("admin-back").onclick = goHome;

/* ---------------- 내 포트폴리오 ---------------- */
let pfSelected = null;
let pfEditingCode = null;   // 지금 인라인 수정 중인 보유종목 코드(2차 진단리포트 3-8: 수정 기능 없음)
let lastPortfolioData = null;

async function showPortfolio() {
  hideAllViews();
  $("portfolio-view").classList.remove("hidden");
  setActiveNav("portfolio");
  window.scrollTo({ top: 0 });
  document.title = "포트폴리오 — StockLens";
  await loadPortfolio();
}

async function loadPortfolio() {
  $("pf-summary-card").classList.remove("hidden");
  $("pf-total").textContent = "";
  $("pf-metrics").innerHTML = `<p class="hint-p">불러오는 중... (보유 종목이 많으면 몇 초 걸릴 수 있어요)</p>`;
  $("pf-warnings").innerHTML = "";
  $("pf-actions-card").classList.add("hidden");
  $("pf-risk-card").classList.add("hidden");
  $("pf-exposure-card").classList.add("hidden");
  $("pf-corr-card").classList.add("hidden");
  $("pf-rebalance-card").classList.add("hidden");
  try {
    const p = await api("/api/portfolio");
    lastPortfolioData = p;
    renderPortfolio(p);
    return p;
  } catch (e) {
    $("pf-summary-card").classList.remove("hidden");
    $("pf-total").textContent = "";
    $("pf-metrics").innerHTML = `<p class="hint-p">불러오기 실패: ${e.message}</p>`;
    return null;
  }
}

const RISK_LEVEL_ICON = { red: "🔴", yellow: "🟡", green: "🟢" };

function renderTodayActions(p) {
  const actions = p.today_actions || [];
  $("pf-actions-card").classList.toggle("hidden", !p.available || !actions.length);
  if (!p.available || !actions.length) return;

  const n = actions.length;
  $("pf-actions-summary").textContent = `현재 ${p.items.length}개 종목 중 조치가 필요한 항목이 ${n}건 있습니다.`;

  $("pf-actions-list").innerHTML = actions.map((a) => `
    <div class="pf-action-card pf-action-${a.level}">
      <div class="pf-action-head">${RISK_LEVEL_ICON[a.level]} <b>${a.name}</b> ${a.title}</div>
      <div class="pf-action-detail">${a.detail}</div>
    </div>`).join("");

  const todos = actions.map((a) => a.action);
  $("pf-todo-wrap").classList.toggle("hidden", !todos.length);
  $("pf-todo-list").innerHTML = todos.map((t) => `<li>→ ${t}</li>`).join("");
}

function renderRiskFlags(p) {
  const flags = p.risk_flags || [];
  $("pf-risk-card").classList.toggle("hidden", !p.available || !flags.length);
  if (!p.available || !flags.length) return;
  const circled = ["①", "②", "③", "④", "⑤", "⑥"];
  $("pf-risk-list").innerHTML = flags.map((f, i) => `
    <div class="pf-risk-item">
      <span class="pf-risk-tag">${circled[i] || `(${i + 1})`} ${f.type}</span>
      <span class="pf-risk-detail">${f.detail}</span>
    </div>`).join("");
}

function renderExposure(p) {
  const exp = p.theme_exposure || {};
  const entries = Object.entries(exp);
  $("pf-exposure-card").classList.toggle("hidden", !p.available || !entries.length);
  if (!p.available || !entries.length) return;
  $("pf-exposure-bars").innerHTML = entries.map(([theme, w]) => `
    <div class="pf-sector-row">
      <span class="pf-sector-name">${theme}${w >= 50 ? " 🔴" : ""}</span>
      <div class="pf-sector-track"><div class="pf-sector-fill" style="width:${w}%"></div></div>
      <span class="pf-sector-pct">${w}%</span>
    </div>`).join("");
}

function renderCorrelation(p) {
  const c = p.correlation;
  $("pf-corr-card").classList.toggle("hidden", !p.available || !c);
  if (!p.available || !c) return;
  const { labels, matrix } = c;
  const head = "<tr><th></th>" + labels.map((l) => `<th>${l}</th>`).join("") + "</tr>";
  const rows = labels.map((l, i) => "<tr><th>" + l + "</th>" + matrix[i].map((v) => {
    if (v == null) return "<td>-</td>";
    const cls = v >= 0.7 ? "corr-high" : v <= 0 ? "corr-low" : "";
    return `<td class="${cls}">${v.toFixed(2)}</td>`;
  }).join("") + "</tr>").join("");
  $("pf-corr-table").innerHTML = `<table class="corr-table"><thead>${head}</thead><tbody>${rows}</tbody></table>`;

  const highPairs = [];
  for (let i = 0; i < labels.length; i++) {
    for (let j = i + 1; j < labels.length; j++) {
      if (matrix[i][j] != null && matrix[i][j] >= 0.7) highPairs.push(`${labels[i]}·${labels[j]}(${matrix[i][j].toFixed(2)})`);
    }
  }
  $("pf-corr-text").textContent = highPairs.length
    ? `${highPairs.join(", ")} — 상관관계가 높아 실제 분산효과가 제한적입니다.`
    : "종목 간 상관관계가 특별히 높지는 않습니다 — 분산효과가 어느 정도 작동하고 있습니다.";
}

function renderRebalance(p) {
  const items = p.available ? (p.items || []).filter((it) => it.target_weight != null) : [];
  $("pf-rebalance-card").classList.toggle("hidden", !items.length);
  if (!items.length) return;
  $("pf-rebalance-list").innerHTML = items.map((it) => `
    <div class="pf-rebal-item">
      <div class="pf-rebal-name">${it.name}</div>
      <div class="pf-rebal-bar-row">
        <span class="pf-rebal-bar-label">현재</span>
        <div class="pf-rebal-track"><div class="pf-rebal-fill cur" style="width:${it.weight}%"></div></div>
        <span class="pf-rebal-pct">${it.weight}%</span>
      </div>
      <div class="pf-rebal-bar-row">
        <span class="pf-rebal-bar-label">권장</span>
        <div class="pf-rebal-track"><div class="pf-rebal-fill target" style="width:${it.target_weight}%"></div></div>
        <span class="pf-rebal-pct">${it.target_weight}%</span>
      </div>
      <p class="pf-rebal-note">${it.rebalance_note || ""}</p>
    </div>`).join("");
}

function renderPortfolio(p) {
  const hasHoldings = p.available || (p.excluded && p.excluded.length);
  $("pf-summary-card").classList.toggle("hidden", !hasHoldings);
  $("pf-sector-card").classList.toggle("hidden", !p.available);
  $("pf-holdings-table-wrap").classList.toggle("hidden", !p.available);
  renderTodayActions(p);
  renderRiskFlags(p);
  renderExposure(p);
  renderCorrelation(p);
  renderRebalance(p);

  if (!p.available) {
    if (hasHoldings) {
      $("pf-total").textContent = "";
      $("pf-grade-hero").innerHTML = "";
      $("pf-metrics").innerHTML = `<p class="hint-p">${p.reason || "계산할 수 없습니다."}</p>`;
      $("pf-warnings").innerHTML = "";
    }
  } else {
    const pnlHTML = p.total_pnl != null
      ? `<span class="${updownClass(p.total_pnl)}">${sign(p.total_pnl)}원 (${sign(p.total_pnl_pct, 1)}%)</span>`
      : `<span class="hint">평균단가를 입력한 종목이 없습니다</span>`;
    const todayHTML = p.today_pnl != null
      ? `<div class="pf-today">오늘 <span class="${updownClass(p.today_pnl)}">${sign(p.today_pnl)}원 (${sign(p.today_pnl_pct, 2)}%)</span></div>` : "";
    $("pf-total").innerHTML = `<label>총 평가금액</label><b>${won(p.total_value)}</b><span class="pf-pnl">${pnlHTML}</span>${todayHTML}`;

    // AI 포트폴리오 점수 — 종목상세 "AI 최종판단"과 같은 철학: 판단(등급)을 숫자 점수보다 크게.
    // ⚠️ 예전엔 이 점수가 보유종목 점수의 가중평균일 뿐이라 경고를 세 개 띄우고도
    // "B등급·양호"라고 말하는 모순이 있었다(2차 진단리포트 3-2). 이제 집중도·변동성·
    // 최대낙폭 감점(risk_penalty)이 반영된 값이라, 감점이 있으면 그 내역을 바로 옆에 밝힌다.
    if (p.grade) {
      const penaltyNote = p.risk_penalty > 0
        ? `<div class="pf-grade-penalty">종목 품질 ${p.quality_score}점 − 리스크 감점 ${p.risk_penalty}점 (${(p.risk_penalty_detail || []).join(", ")})</div>`
        : "";
      $("pf-grade-hero").innerHTML = `
        <div class="pf-grade-label">AI 포트폴리오 점수</div>
        <div class="pf-grade-main">
          <span class="pf-grade-emoji">${gradeEmoji(p.grade)}</span>
          <span class="pf-grade-score" style="color:${scoreColor(p.score)}">${p.score}<small>/100</small></span>
          <span class="pf-grade-desc" style="color:${scoreColor(p.score)}">${p.grade}등급 · ${p.grade_desc}</span>
        </div>
        ${penaltyNote}`;
    } else {
      $("pf-grade-hero").innerHTML = "";
    }

    const cells = [
      ["종합점수 (리스크 반영)", p.score != null ? `${p.score}점` : "-"],
      ["종목 품질 점수", p.quality_score != null ? `${p.quality_score}점` : "-"],
      ["밸류에이션 점수", p.valuation_score != null ? `${p.valuation_score}점` : "-"],
      ["기대수익률 (컨센서스 목표가 기준)", p.expected_return != null ? `${sign(p.expected_return, 1)}%` : "-"],
      ["변동성 (연환산)", p.volatility != null ? `${p.volatility}%` : "데이터 부족"],
      ["최대 낙폭 (최근 1년)", p.max_drawdown != null ? `${p.max_drawdown}%` : "데이터 부족"],
    ];
    $("pf-metrics").innerHTML = cells.map(([l, v]) =>
      `<div class="pro-item"><label>${l}</label><div class="pro-val">${v}</div></div>`).join("");
    // 경고(warnings)만 있으면 부정적 신호만 나열되어 불안해 보일 수 있어, 밸류에이션이 양호하면
    // 긍정 신호도 한 줄 보태 균형을 맞춘다("경고 목록"이 아니라 "진단 요약"이 되도록).
    const posNote = (p.valuation_score != null && p.valuation_score >= 60)
      ? ["🟢 종목별 밸류에이션은 양호한 편입니다"] : [];
    $("pf-warnings").innerHTML = (p.warnings || []).map((w) => `<li>${w}</li>`).join("")
      + posNote.map((w) => `<li class="pos">${w}</li>`).join("");

    $("pf-sector-bars").innerHTML = Object.entries(p.sector_weight || {}).map(([sector, w]) => `
      <div class="pf-sector-row">
        <span class="pf-sector-name">${sector}</span>
        <div class="pf-sector-track"><div class="pf-sector-fill" style="width:${w}%"></div></div>
        <span class="pf-sector-pct">${w}%</span>
      </div>`).join("");

    // ⚠️ 예전엔 보유 종목 행에 [삭제] 버튼뿐이라 수량·평균단가를 잘못 입력하면 지우고
    // 다시 넣는 수밖에 없었고(매일 쓰는 기능에서 가장 큰 마찰), 삭제도 확인창 없이
    // 즉시·되돌릴 수 없이 실행됐다(2차 진단리포트 3-8). [수정]으로 그 행을 인라인
    // 입력칸으로 바꿔 PUT(덮어쓰기)으로 저장하고, 삭제는 confirm()으로 한 번 더 확인한다.
    $("pf-holdings-table").innerHTML = tableHTML(
      ["종목", "수량", "평균단가", "현재가", "평가금액", "평가손익", "현재비중", "권장비중", "AI판단", ""],
      p.items.map((it) => {
        const v = it.ai_verdict || {};
        const isUS = it.currency === "USD";
        const flag = isUS ? "🇺🇸 " : "";
        // 평균단가·현재가는 종목 통화 그대로(달러는 $ 표기) — 평가금액·평가손익은 합산이 필요해
        // 원화 환산가 그대로 두되, 미국 종목은 "(환산)"을 붙여 환율이 반영된 값임을 알린다.
        const krwNote = isUS ? ` <small class="hint">(환산)</small>` : "";

        if (it.code === pfEditingCode) {
          return [
            flag + it.name,
            `<input type="number" min="0.0001" step="any" class="pf-edit-input" id="pf-edit-shares" value="${it.shares}">`,
            `<input type="number" min="0" step="any" class="pf-edit-input" id="pf-edit-price" value="${it.avg_price != null ? it.avg_price : ""}" placeholder="선택">`,
            isUS ? pw(it.price_native, "USD") : won(it.price),
            "-", "-", `${it.weight}%`,
            it.target_weight != null ? `${it.target_weight}%` : "-",
            `<span style="color:${verdictColor(v.tier)}">${v.emoji || ""} ${v.label || "-"}</span>`,
            `<button class="ghost-btn small primary-btn" data-pf-save="${it.code}">저장</button>
             <button class="ghost-btn small" data-pf-cancel="1">취소</button>`,
          ];
        }
        return [
          flag + it.name,
          fmt(it.shares) + "주",
          it.avg_price != null ? pw(it.avg_price, it.currency) : "-",
          isUS ? pw(it.price_native, "USD") : won(it.price),
          won(it.value) + krwNote,
          it.pnl != null
            ? `<span class="${updownClass(it.pnl)}">${sign(it.pnl)}원 (${sign(it.pnl_pct, 1)}%)</span>${krwNote}`
            : "-",
          `${it.weight}%`,
          it.target_weight != null ? `${it.target_weight}%` : "-",
          `<span style="color:${verdictColor(v.tier)}">${v.emoji || ""} ${v.label || "-"}</span>`,
          `<button class="ghost-btn small" data-pf-edit="${it.code}">수정</button>
           <button class="ghost-btn small" data-pf-rm="${it.code}" data-pf-name="${it.name}">삭제</button>`,
        ];
      }));
    $("pf-holdings-table").querySelectorAll("[data-pf-rm]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm(`${b.dataset.pfName}을(를) 포트폴리오에서 삭제할까요? 거래 이력이 저장되지 않으므로 되돌릴 수 없습니다.`)) return;
        await api(`/api/portfolio/${b.dataset.pfRm}`, { method: "DELETE" });
        loadPortfolio();
      };
    });
    $("pf-holdings-table").querySelectorAll("[data-pf-edit]").forEach((b) => {
      b.onclick = () => { pfEditingCode = b.dataset.pfEdit; renderPortfolio(lastPortfolioData); };
    });
    $("pf-holdings-table").querySelectorAll("[data-pf-cancel]").forEach((b) => {
      b.onclick = () => { pfEditingCode = null; renderPortfolio(lastPortfolioData); };
    });
    $("pf-holdings-table").querySelectorAll("[data-pf-save]").forEach((b) => {
      b.onclick = async () => {
        const code = b.dataset.pfSave;
        const it = p.items.find((x) => x.code === code);
        const shares = Number($("pf-edit-shares").value);
        const priceRaw = $("pf-edit-price").value.trim();
        const avg_price = priceRaw ? Number(priceRaw) : null;
        if (!shares || shares <= 0) { showError("수량은 0보다 큰 숫자로 입력하세요."); return; }
        if (priceRaw && (!avg_price || avg_price <= 0)) { showError("평균단가는 0보다 큰 숫자로 입력하세요."); return; }
        b.disabled = true;
        try {
          await api(`/api/portfolio/${code}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: it.name, shares, avg_price }),
          });
          pfEditingCode = null;
          await loadPortfolio();
        } catch (e) {
          showError("수정 실패: " + e.message);
          b.disabled = false;
        }
      };
    });
  }

  const fxItem = p.available ? (p.items || []).find((it) => it.fx_rate) : null;
  const fxNote = fxItem ? `환율: $1 = ₩${fmt(fxItem.fx_rate, 2)} 기준으로 환산됨` : "";
  const excludedNote = (p.excluded && p.excluded.length)
    ? "제외됨: " + p.excluded.map((x) => `${x.name}(${x.reason})`).join(", ")
    : "";
  $("pf-excluded").innerHTML = [fxNote, excludedNote].filter(Boolean).join("<br>");
}

/* 종목 검색 (메인 검색과 별개의 작은 드롭다운) */
const pfInput = $("pf-search-input");
const pfDropdown = $("pf-search-dropdown");
let pfSearchTimer = null;
pfInput.addEventListener("input", () => {
  clearTimeout(pfSearchTimer);
  pfSelected = null;
  $("pf-add-btn").disabled = true;
  $("pf-price").placeholder = "평균단가(원, 선택)";
  const q = pfInput.value.trim();
  if (!q) { pfDropdown.classList.add("hidden"); return; }
  pfSearchTimer = setTimeout(async () => {
    try {
      const { items } = await api(`/api/search?q=${encodeURIComponent(q)}`);
      pfDropdown.innerHTML = "";
      items.forEach((it) => {
        const flag = it.nation === "US" ? "🇺🇸" : "🇰🇷";
        const d = document.createElement("div");
        d.innerHTML = `<b>${flag} ${it.name}</b><small>${it.code} · ${it.market}</small>`;
        d.onclick = () => {
          pfDropdown.classList.add("hidden");
          pfInput.value = it.name;
          pfSelected = { code: it.code, name: it.name, nation: it.nation };
          $("pf-add-btn").disabled = false;
          $("pf-price").placeholder = it.nation === "US" ? "평균단가(달러, 선택)" : "평균단가(원, 선택)";
        };
        pfDropdown.appendChild(d);
      });
      pfDropdown.classList.toggle("hidden", !pfDropdown.children.length);
    } catch { pfDropdown.classList.add("hidden"); }
  }, 250);
});
$("pf-add-btn").onclick = async () => {
  const shares = Number($("pf-shares").value);
  const priceRaw = $("pf-price").value.trim();
  const avg_price = priceRaw ? Number(priceRaw) : null;
  if (!pfSelected || !shares || shares <= 0) {
    $("pf-add-msg").textContent = "종목과 수량을 모두 입력하세요.";
    return;
  }
  if (priceRaw && (!avg_price || avg_price <= 0)) {
    $("pf-add-msg").textContent = "평균단가는 0보다 큰 숫자로 입력하세요.";
    return;
  }
  $("pf-add-btn").disabled = true;
  $("pf-add-msg").textContent = "추가 중...";
  const addedCode = pfSelected.code;
  try {
    await api(`/api/portfolio/${addedCode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: pfSelected.name, shares, avg_price }),
    });
    pfInput.value = ""; $("pf-shares").value = ""; $("pf-price").value = ""; pfSelected = null;
    $("pf-price").placeholder = "평균단가(원, 선택)";
    $("pf-add-msg").textContent = "추가됨. 포트폴리오 불러오는 중...";
    const p = await loadPortfolio();
    // ⚠️ 서버 저장은 성공했는데 분석(analyze_fn)이 실패하는 종목(ETF 등)은 목록에서
    // 조용히 사라졌었다(2차 진단리포트 3-8, "무음 실패"). excluded에 이유가 남게
    // 고쳤으니(portfolio.compute) 여기서 확인해 사용자에게 그대로 알려준다.
    const excludedHit = p && (p.excluded || []).find((x) => x.code === addedCode);
    $("pf-add-msg").textContent = excludedHit
      ? `⚠️ 저장은 됐지만 목록에는 표시되지 않았습니다 (${excludedHit.reason}). 다른 종목/ETF로 시도해보세요.`
      : "";
  } catch (e) {
    $("pf-add-btn").disabled = false;
    $("pf-add-msg").textContent = "오류: " + e.message;
  }
};
$("pf-back").onclick = goHome;

/* ---------------- KIS modal ---------------- */
// 공개 배포에서는 KIS 키 저장이 백엔드에서 403 차단되므로 버튼 자체를 숨긴다(설정 화면까지
// 들어갔다가 저장 시점에야 막히는 경험을 방지). 로컬 실행이면 연결 여부에 따라 라벨만 바꾼다.
async function refreshKisBtn() {
  try {
    const r = await api("/api/kis/status");
    if (r.public) { $("kis-btn").classList.add("hidden"); return; }
    $("kis-btn").classList.remove("hidden");
    // .kis-label은 좁은 화면에서 CSS로 숨겨 아이콘만 남긴다(헤더가 모바일에서 너무 길어지는 것 방지) —
    // title로 접근성/툴팁은 유지.
    const label = r.configured ? "실시간 시세 연결됨" : "실시간 시세 연동";
    $("kis-btn").innerHTML = `${r.configured ? "⚡" : "⚙"} <span class="kis-label">${label}</span>`;
    $("kis-btn").title = label;
  } catch {}
}
refreshKisBtn();

$("kis-btn").onclick = () => $("kis-modal").classList.remove("hidden");
$("kis-close").onclick = () => $("kis-modal").classList.add("hidden");
$("kis-modal").addEventListener("click", (e) => {
  if (e.target === $("kis-modal")) $("kis-modal").classList.add("hidden");
});
$("kis-save").onclick = async () => {
  const key = $("kis-key").value.trim();
  const secret = $("kis-secret").value.trim();
  if (!key || !secret) { $("kis-msg").textContent = "앱키와 시크릿을 모두 입력하세요."; return; }
  $("kis-msg").textContent = "연결 확인 중...";
  try {
    const r = await api("/api/kis/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ app_key: key, app_secret: secret, is_paper: $("kis-paper").checked }),
    });
    $("kis-msg").textContent = r.message;
    $("kis-msg").style.color = r.ok ? "#2ecc71" : "#f5a623";
    if (r.ok) refreshKisBtn();
  } catch (e) {
    $("kis-msg").textContent = "오류: " + e.message;
  }
};

/* ---------------- navigation + init ---------------- */
$("logo-home").onclick = goHome;
$("back-btn").onclick = goHome;
$("fav-btn").onclick = async () => {
  if (!currentCode) return;
  if (!currentUser) { openAuthModal("login"); return; }
  const b = $("fav-btn");
  const msg = $("watch-msg");
  b.disabled = true;
  try {
    if (watchedCodes.has(currentCode)) {
      await removeFromWatch(currentCode);
      msg.textContent = "관심종목에서 제거했습니다.";
    } else {
      const t = (lastAnalysis && lastAnalysis.total) || {};
      const v = (lastAnalysis && lastAnalysis.ai_verdict) || {};
      await addToWatch(currentCode, $("stock-name").textContent,
        lastAnalysis ? lastAnalysis.price : null, t.total_score, v.label, v.tier);
      msg.textContent = "⭐ 관심종목에 추가했습니다. 옆의 ⚙에서 알림·메모를 설정할 수 있어요.";
    }
    msg.classList.remove("hidden");
    updateFavBtn();
    updateWatchBtn();
  } catch (e) {
    msg.textContent = "오류: " + e.message;
    msg.classList.remove("hidden");
  } finally {
    b.disabled = false;
  }
};

// 국내/미국 랭킹 토글
document.querySelectorAll("#rank-market button").forEach((b) => {
  b.onclick = () => {
    if (b.dataset.market === currentMarket) return;
    currentMarket = b.dataset.market;
    document.querySelectorAll("#rank-market button").forEach((x) => x.classList.toggle("active", x === b));
    clearTimeout(rankPollTimer);
    $("rank-list").innerHTML = `<div class="rank-loading"><div class="spinner sm"></div><span>${currentMarket === "US" ? "미국" : "국내"} 랭킹 집계 중…</span></div>`;
    $("rank-filters").innerHTML = "";
    loadRanking("전체");
  };
});

// 종목 비교
$("compare-btn").onclick = () => { if (lastAnalysis) addCompare(lastAnalysis); };
$("cmp-go").onclick = showCompare;
$("cmp-clear").onclick = () => { compareList = []; renderCompareTray(); if (!$("compare-view").classList.contains("hidden")) goHome(); };
$("cmp-back").onclick = goHome;

// 차트 봉 주기(일/주/월) + 구간 슬라이더
$("chart-tf").querySelectorAll("button").forEach((b) => {
  b.onclick = () => switchTimeframe(b.dataset.tf);
});

initTheme();
syncHomeForUser();   // loadMe() 전에도 한 번 — 로그아웃 기준 초기 상태를 먼저 맞춰 둔다
renderFavBoard();
loadRanking();
// 로그인 확인은 비동기다 — 아래 라우팅이 이 결과를 기다려야 하는 경우가 있어
// Promise를 붙잡아 둔다(_meReady).
const _meReady = loadMe();
loadThemeChips();
loadAnomalies();
loadBacktest();
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}

// 모바일 헤더 축소(진단리포트 지적사항 — 고정 헤더 3줄이 화면의 22%를 항상 차지해 콘텐츠
// 영역이 좁았음). 데스크톱은 손대지 않고, 모바일 폭(720px 이하)에서만 아래로 스크롤하면
// 헤더를 숨기고 위로 스크롤하면 다시 보이게 한다 — 흔한 모바일 웹 패턴.
let _lastScrollY = window.scrollY;
/* sticky 탭바가 상단바 아래에 정확히 붙도록 상단바 실제 높이를 CSS 변수로 넘긴다
   (반응형으로 높이가 달라져서 CSS에 상수로 박을 수 없다). */
function syncTopbarHeight() {
  const tb = document.querySelector(".topbar");
  if (tb) document.documentElement.style.setProperty("--topbar-h", tb.offsetHeight + "px");
}
syncTopbarHeight();
window.addEventListener("resize", syncTopbarHeight, { passive: true });

window.addEventListener("scroll", () => {
  // 종목상세 탭바가 실제로 상단에 "붙었는지" 판정 — 붙었을 때만 요약줄·그림자를 켠다.
  const sticky = $("detail-sticky");
  if (sticky && !$("report").classList.contains("hidden")) {
    const stickTop = window.innerWidth <= 720
      ? 0 : (parseInt(getComputedStyle(document.documentElement).getPropertyValue("--topbar-h")) || 62);
    sticky.classList.toggle("pinned", sticky.getBoundingClientRect().top <= stickTop + 1);
  }
  if (window.innerWidth > 720) return;
  const topbar = document.querySelector(".topbar");
  const y = window.scrollY;
  if (y > _lastScrollY && y > 80) topbar.classList.add("topbar-hide");
  else topbar.classList.remove("topbar-hide");
  _lastScrollY = y;
}, { passive: true });

/* ---------------- URL 라우팅 ----------------
   예전엔 /stock/{code}만 라우팅되고 관심종목·스크리너·포트폴리오는 주소가 없어서
   직접 접속하면 404였다. "갈 곳이 없으니 모든 기능이 홈으로 몰린다"는 지적의
   원인(UI/UX 진단보고서 3-1, 4차 진단리포트 8장 — 4회 연속 지적된 항목).
   경로 표를 한 곳에 두고, 최초 진입·뒤로가기·내부 이동이 모두 이 표를 거치게 한다. */
const ROUTES = [
  { re: /^\/watchlist\/?$/, auth: true,  run: () => showWatchlist() },
  { re: /^\/screener\/?$/,                run: () => showScreener() },
  { re: /^\/portfolio\/?$/, auth: true,  run: () => showPortfolio() },
  { re: /^\/stock\/([^/]+)\/?$/,          run: (m) => analyze(decodeURIComponent(m[1]), true) },
];

function matchRoute(path) {
  for (const r of ROUTES) {
    const m = path.match(r.re);
    if (m) return { route: r, m };
  }
  return null;
}

function applyRoute() {
  const hit = matchRoute(location.pathname);
  if (!hit) { goHome(true); return; }
  if (hit.route.auth && !currentUser) {
    // 로그인 화면으로 보내되 주소는 홈으로 되돌린다 — 죽은 링크가 남아 새로고침할
    // 때마다 계속 모달만 뜨는 상황을 막는다.
    // ⚠️ goHome(true)는 "뒤로가기로 들어온 경우"라 주소를 건드리지 않는다. 여기서는
    // 직접 replaceState로 되돌려야 한다(히스토리에 새 항목을 쌓지 않으려고 push 대신 replace).
    openAuthModal("login");
    goHome(true);
    if (location.pathname !== "/") history.replaceState(null, "", "/");
    return;
  }
  hit.route.run(hit.m);
}

// 내부 이동 — 주소를 바꾸고(뒤로가기 가능) 해당 화면을 연다.
function navigate(path) {
  if (location.pathname !== path) history.pushState(null, "", path);
  applyRoute();
}

// data-route="/경로"가 붙은 요소는 어디에 있든(동적 렌더 포함) 라우팅되게 위임 처리.
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-route]");
  if (!el) return;
  e.preventDefault();
  navigate(el.dataset.route);
});

window.addEventListener("popstate", applyRoute);

/* 최초 진입(공유된 링크·북마크·새로고침)이 "/"가 아니면 그 화면을 바로 연다.
   ⚠️ 로그인 확인(loadMe)은 비동기라, /watchlist·/portfolio를 주소로 직접 열면
   currentUser가 아직 null이어서 "로그인이 필요합니다"가 잘못 떴다. 인증이 필요한
   경로만 확인이 끝난 뒤에 처리한다(나머지는 기다릴 이유가 없으니 즉시 실행). */
if (location.pathname !== "/") {
  const hit = matchRoute(location.pathname);
  if (hit && hit.route.auth) _meReady.then(applyRoute);
  else applyRoute();
}
