/* StockLens frontend */
const $ = (id) => document.getElementById(id);
let currentCode = null;
let priceTimer = null;
let chart = null;
let watchedCodes = new Set();

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
function scoreColor(s) {
  if (s >= 75) return "#2ecc71";
  if (s >= 60) return "#4f8cff";
  if (s >= 45) return "#f5a623";
  return "#ff4d4d";
}
function verdictColor(tier) {
  return { buy: "#2ee6a6", accumulate: "#f5c518", hold: "#4f8cff", reduce: "#f5a623", sell: "#ff4d6d" }[tier] || "#9aa3ba";
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
function applyTheme(t) {
  document.body.classList.toggle("light", t === "light");
  $("theme-btn").textContent = t === "light" ? "☀️" : "🌙";
}
function initTheme() {
  applyTheme(localStorage.getItem(THEME_KEY) || "dark");
}
$("theme-btn").onclick = () => {
  const t = document.body.classList.contains("light") ? "dark" : "light";
  localStorage.setItem(THEME_KEY, t);
  applyTheme(t);
};

/* ---------------- favorites ---------------- */
const FAV_KEY = "stocklens_favs";
const getFavs = () => { try { return JSON.parse(localStorage.getItem(FAV_KEY)) || []; } catch { return []; } };
const setFavs = (a) => localStorage.setItem(FAV_KEY, JSON.stringify(a));
const isFav = (code) => getFavs().some((f) => f.code === code);
function toggleFav(code, name) {
  let a = getFavs();
  a = isFav(code) ? a.filter((f) => f.code !== code) : [...a, { code, name }];
  setFavs(a);
  return isFav(code);
}
function removeFav(code) { setFavs(getFavs().filter((f) => f.code !== code)); }
async function renderFavBoard() {
  const favs = getFavs();
  const el = $("fav-board");
  if (!favs.length) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.innerHTML = `<h2>⭐ 관심종목</h2>
    <div id="fav-rows" class="fav-rows"><div class="rank-loading"><div class="spinner sm"></div><span>불러오는 중...</span></div></div>`;

  // 관심종목은 국내/미국이 섞일 수 있어 두 시장 랭킹을 모두 조회해 매칭한다
  // (둘 다 백그라운드에서 이미 채점된 데이터라 추가 계산 없음).
  let byCode = {};
  try {
    const [kr, us] = await Promise.all([api("/api/ranking?market=KR"), api("/api/ranking?market=US")]);
    [...(kr.items || []), ...(us.items || [])].forEach((r) => { byCode[r.code] = r; });
  } catch {}

  el.querySelector("#fav-rows").innerHTML = favs.map((f) => {
    const r = byCode[f.code];
    if (!r) {
      return `<div class="fav-row" data-code="${f.code}">
        <span class="fav-name">${f.name}</span><span class="fav-na">데이터 준비 중</span>
        <button class="fav-x" data-x="${f.code}">✕</button></div>`;
    }
    const v = r.ai_verdict || {};
    return `<div class="fav-row" data-code="${f.code}">
      <span class="fav-name">${r.name}</span>
      <span class="fav-price">${pw(r.price, r.currency)}</span>
      <span class="fav-verdict" style="color:${verdictColor(v.tier)}">${v.emoji || ""} ${v.label || "-"}</span>
      <button class="fav-x" data-x="${f.code}">✕</button>
    </div>`;
  }).join("");
  el.querySelectorAll(".fav-row").forEach((row) => {
    row.onclick = (e) => {
      if (e.target.classList.contains("fav-x")) { removeFav(e.target.dataset.x); renderFavBoard(); return; }
      analyze(row.dataset.code);
    };
  });
}
function updateFavBtn() {
  const b = $("fav-btn");
  const on = isFav(currentCode);
  b.textContent = on ? "★" : "☆";
  b.classList.toggle("on", on);
}
function updateWatchBtn() {
  const b = $("watch-btn");
  const on = currentCode && watchedCodes.has(currentCode);
  b.textContent = on ? "🔔 알림 켜짐" : "🔔 알림";
  b.classList.toggle("on", !!on);
}

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
document.querySelectorAll(".quick-picks button").forEach((b) => {
  b.onclick = () => analyze(b.dataset.code);
});

/* ---------------- navigation ---------------- */
function setActiveNav(view) {
  document.querySelectorAll("#main-nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
}

function goHome() {
  clearInterval(priceTimer);
  currentCode = null;
  $("report").classList.add("hidden");
  $("compare-view").classList.add("hidden");
  $("admin-view").classList.add("hidden");
  $("portfolio-view").classList.add("hidden");
  $("loading").classList.add("hidden");
  $("landing").classList.remove("hidden");
  window.scrollTo({ top: 0 });
  setActiveNav("home");
  renderFavBoard();
  loadRanking(currentSector);
}

document.querySelectorAll("#main-nav button").forEach((b) => {
  b.onclick = () => {
    const view = b.dataset.view;
    if (view === "home") { goHome(); return; }
    if (view === "portfolio") {
      if (!currentUser) { openAuthModal("login"); return; }
      showPortfolio();
      return;
    }
    if (view === "stock") {
      if (currentCode) {
        clearInterval(priceTimer);
        $("landing").classList.add("hidden");
        $("compare-view").classList.add("hidden");
        $("admin-view").classList.add("hidden");
        $("portfolio-view").classList.add("hidden");
        $("report").classList.remove("hidden");
        setActiveNav("stock");
        priceTimer = setInterval(refreshPrice, 4000);
        window.scrollTo({ top: 0 });
      } else {
        goHome();
        $("search-input").focus();
      }
    }
  };
});

/* ---------------- 이상징후 탐지 ---------------- */
async function loadAnomalies() {
  try {
    const r = await api("/api/anomalies");
    const cards = [
      ...r.bull.map((it) => ({ ...it, kind: "bull" })),
      ...r.bear.map((it) => ({ ...it, kind: "bear" })),
    ];
    if (!cards.length) { $("anomaly-board").classList.add("hidden"); return; }
    $("anomaly-board").classList.remove("hidden");
    $("anomaly-list").innerHTML = cards.map((it) => {
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
        <div class="rank-score-chip" style="color:${col};background:${col}22">${r.score}</div>
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

function renderRankFilters(sectors) {
  if (!sectors || !sectors.length) return;
  const all = ["전체", ...sectors];
  $("rank-filters").innerHTML = all.map((s) =>
    `<button class="${s === currentSector ? "active" : ""}" data-sector="${s}">${s}</button>`).join("");
  document.querySelectorAll("#rank-filters button").forEach((b) => {
    b.onclick = () => loadRanking(b.dataset.sector);
  });
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

async function renderTodayPick(items) {
  const board = $("today-board");
  if (!items || !items.length) { board.classList.add("hidden"); return; }
  board.classList.remove("hidden");
  const top = items.slice(0, 5);
  const best = top[0];
  const bv = best.ai_verdict || {};
  const bestUp = best.upside != null ? `${sign(best.upside, 1)}%` : "-";
  $("today-hero").innerHTML = `
    <div class="today-pick-label">🔥 TODAY'S PICK</div>
    <div class="today-pick-name">${best.name} <small>${best.code}</small></div>
    <div class="today-pick-price-row">
      <span class="today-pick-price">${pw(best.price, best.currency)}</span>
      <span class="today-pick-score">종합점수 ${best.score}</span>
    </div>
    <div class="today-pick-verdict" style="color:${verdictColor(bv.tier)}">${bv.emoji || ""} ${bv.label || ""} · 확신도 ${bv.confidence ?? "-"}%</div>
    <div class="today-pick-stats">
      <div><label>적정매수가</label><span id="today-pick-fair">불러오는 중…</span></div>
      <div><label>목표가</label><span>${best.target_price ? pw(best.target_price, best.currency) : "-"}</span></div>
      <div><label>상승여력</label><span class="${updownClass(best.upside)}">${bestUp}</span></div>
    </div>
    <button class="primary-btn today-pick-btn" id="today-pick-btn">종목 자세히 보기</button>`;
  $("today-pick-btn").onclick = () => analyze(best.code);

  // "왜 주목하는가" — 이미 계산된 6개 부문점수 + RSI를 그대로 재사용(추가 계산 없음)
  const cats = Object.entries(best.categories || {});
  const reasonsHtml = cats.map(([name, score]) => {
    const d = directionTag(score);
    return `<div class="today-why-row ${d.cls}"><span class="today-why-name">${CATEGORY_ICON[name] || "•"} ${name}</span><span class="today-why-dir">${d.arrow} ${d.text}</span></div>`;
  }).join("");
  const overheatWarn = best.rsi != null && best.rsi >= 70
    ? `<div class="today-why-warn">⚠️ 단기 과열 가능성 (RSI ${best.rsi.toFixed(0)})</div>` : "";
  $("today-why").innerHTML = `<div class="today-why-title">AI가 오늘 ${best.name}${josa(best.name, "을", "를")} 주목한 이유</div>${reasonsHtml}${overheatWarn}`;

  $("today-rows").innerHTML = top.map((r) => {
    const v = r.ai_verdict || {};
    const up = r.upside != null ? `${sign(r.upside, 1)}%` : "-";
    const target = r.target_price ? pw(r.target_price, r.currency) : "-";
    return `<div class="today-row" data-code="${r.code}">
      <span class="today-judge" style="color:${verdictColor(v.tier)}">${v.emoji || ""} ${v.label || "-"}</span>
      <span class="today-name">${r.name}</span>
      <span class="today-price">${pw(r.price, r.currency)}</span>
      <span class="today-fair">${target}</span>
      <span class="today-upside ${updownClass(r.upside)}">${up}</span>
    </div>`;
  }).join("");
  $("today-rows").querySelectorAll(".today-row").forEach((row) => {
    row.onclick = () => analyze(row.dataset.code);
  });

  // 적정매수가는 랭킹 백그라운드 채점에 없음(peers 조회 등 비용 때문에 의도적으로 생략)
  // — TODAY'S PICK 1종목에 한해서만 추가로 조회한다.
  try {
    const d = await api(`/api/analyze/${best.code}`);
    const fb = (d.targets && d.targets.fair_buy) ? d.targets.fair_buy.base : null;
    const el = $("today-pick-fair");
    if (el) el.textContent = fb ? pw(fb.price, best.currency) : "-";
  } catch {
    const el = $("today-pick-fair");
    if (el) el.textContent = "-";
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
      <div class="rank-score-chip" style="color:${col};background:${col}22">${r.score}</div>
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
async function analyze(code) {
  currentCode = code;
  clearInterval(priceTimer);
  clearTimeout(rankPollTimer);
  $("landing").classList.add("hidden");
  $("report").classList.add("hidden");
  $("loading").classList.remove("hidden");
  try {
    const d = await api(`/api/analyze/${code}`);
    render(d);
    $("loading").classList.add("hidden");
    $("report").classList.remove("hidden");
    setActiveNav("stock");
    window.scrollTo({ top: 0 });
    priceTimer = setInterval(refreshPrice, 4000);
  } catch (err) {
    $("loading").classList.add("hidden");
    $("landing").classList.remove("hidden");
    alert("분석 실패: " + err.message);
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

/* ---------------- 적정 매수가 구간별 행동 가이드 ---------------- */
function renderFairBuyBands(fb, price) {
  const c = fb.conservative.price, b = fb.base.price, o = fb.optimistic.price;
  const hi = Math.round(o * 1.1);
  const bands = [
    { max: c, label: "적극매수", cls: "band-strong-buy", emoji: "🟢" },
    { max: b, label: "매수", cls: "band-buy", emoji: "🟢" },
    { max: o, label: "분할매수", cls: "band-partial", emoji: "🟡" },
    { max: hi, label: "관망", cls: "band-watch", emoji: "🟠" },
    { max: Infinity, label: "고평가", cls: "band-avoid", emoji: "🔴" },
  ];
  let lo = 0;
  $("fb-bands").innerHTML = bands.map((band) => {
    const rangeText = band.max === Infinity ? `${pw(lo)} 이상` :
      (lo === 0 ? `${pw(band.max)} 이하` : `${pw(lo)} ~ ${pw(band.max)}`);
    const active = price != null && price > lo && price <= band.max;
    lo = band.max;
    return `<div class="fb-band-row ${band.cls} ${active ? "active" : ""}">
      <span class="fb-band-range">${rangeText}</span>
      <span class="fb-band-judge">${band.emoji} ${band.label}</span>
    </div>`;
  }).join("");
}

/* ---------------- 단계별 매수 전략 (1차/2차/3차 분할매수) ---------------- */
function renderBuyPlan(fb, targetPrice, stopLoss) {
  $("buy-plan-box").classList.remove("hidden");
  const stages = [
    { label: "1차 매수", pct: 30, price: fb.optimistic.price },
    { label: "2차 매수", pct: 30, price: fb.base.price },
    { label: "3차 매수", pct: 40, price: fb.conservative.price },
  ];
  $("buy-plan-stages").innerHTML = stages.map((s) => `
    <div class="buy-stage">
      <div class="buy-stage-label">${s.label} <span class="buy-stage-pct">${s.pct}%</span></div>
      <div class="buy-stage-price">${pw(s.price)}</div>
    </div>`).join("");
  $("buy-plan-target").textContent = targetPrice ? pw(targetPrice) : "-";
  $("buy-plan-stop").textContent = stopLoss ? pw(stopLoss) : "-";
}

/* ---------------- render ---------------- */
function render(d) {
  lastAnalysis = d;
  curCur = d.currency || "KRW";
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
  if (d.public) $("kis-btn").classList.add("hidden");
  updateFavBtn();
  updateWatchBtn();
  $("watch-msg").classList.add("hidden");
  $("watch-msg").textContent = "";

  /* score */
  drawGauge(d.total.total_score);
  $("grade").textContent = d.total.grade;
  $("grade").style.color = scoreColor(d.total.total_score);
  $("grade-desc").textContent = d.total.grade_desc + " · " + d.total.total_score + "점";

  /* 매수 매력도 요약 (한눈에 보기) — 이미 계산된 targets/technical/valuation/flows를 재사용 */
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
    $("ai-verdict-confidence").textContent = v.confidence ?? "-";

    const fbBase = t.fair_buy ? t.fair_buy.base : null;
    if (fbBase && d.price) {
      const diffPct = (d.price - fbBase.price) / fbBase.price * 100;
      if (diffPct <= -3) $("hl-discount").textContent = `현재 가격은 적정가 대비 ${Math.abs(diffPct).toFixed(1)}% 저평가되어 있습니다.`;
      else if (diffPct >= 3) $("hl-discount").textContent = `현재 가격은 적정가 대비 ${diffPct.toFixed(1)}% 고평가되어 있습니다.`;
      else $("hl-discount").textContent = `현재 가격은 적정가와 비슷한 수준입니다.`;
    } else {
      $("hl-discount").textContent = "";
    }
    const stopLoss = (tech.available && tech.entry) ? tech.entry.stop_loss : null;
    const items = [
      { label: "현재가", value: pw(d.price) },
      { label: "적정 매수가", value: fbBase ? pw(fbBase.price) : "-" },
      { label: "목표주가", value: t.consensus ? pw(t.consensus) : "-" },
      { label: "손절 고려가", value: stopLoss ? pw(stopLoss) : "-" },
      { label: "상승여력", value: t.consensus_upside != null ? sign(t.consensus_upside, 1) + "%" : "-",
        cls: updownClass(t.consensus_upside) },
    ];
    $("hl-grid").innerHTML = items.map((it) => `
      <div class="hl-item"><label>${it.label}</label><div class="${it.cls || ""}">${it.value}</div></div>`).join("");

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
  $("category-bars").innerHTML = Object.entries(d.total.categories).map(([k, v]) => `
    <div class="cat-bar">
      <div class="cat-label"><b>${k}</b><span>${v}점</span></div>
      <div class="cat-track"><div class="cat-fill" style="width:${v}%;background:${scoreColor(v)}"></div></div>
    </div>`).join("");

  /* targets */
  const t = d.targets;
  $("target-consensus").textContent = t.consensus ? pw(t.consensus) : "데이터 없음";
  $("target-consensus-upside").textContent = t.consensus_upside != null ? `상승여력 ${sign(t.consensus_upside, 1)}%` : "";
  $("target-consensus-upside").className = "target-upside " + updownClass(t.consensus_upside);
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
    renderFairBuyBands(fb, d.price);
    const stopLoss = (d.technical.available && d.technical.entry) ? d.technical.entry.stop_loss : null;
    renderBuyPlan(fb, t.consensus, stopLoss);
  } else {
    $("fair-buy-box").classList.add("hidden");
    $("fb-bands").innerHTML = "";
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
      <div class="entry-item buy"><label>🟢 매수 관심 구간</label><div>${pwRange(e.buy_zone_low, e.buy_zone_high)}</div></div>
      <div class="entry-item sell"><label>🔴 매도·차익실현 구간</label><div>${pwRange(e.sell_zone_low, e.sell_zone_high)}</div></div>
      <div class="entry-item"><label>지지선</label><div class="up">${pw(e.support)}</div></div>
      <div class="entry-item"><label>저항선</label><div class="down">${pw(e.resistance)}</div></div>
      <div class="entry-item"><label>손절 참고가</label><div class="down">${pw(e.stop_loss)}</div></div>`;
  }

  /* chart */
  renderChart(d);


  /* tech summary */
  if (tech.available) {
    const slopeTxt = (v) => v == null ? "-" :
      `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "▲" : v < 0 ? "▼" : ""}${Math.abs(v).toFixed(1)}%</span>`;
    $("tech-summary").innerHTML = `
      <div class="tech-item"><label>기술 점수</label><div style="color:${scoreColor(tech.score)}">${tech.score}점</div></div>
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
  $("news-list").innerHTML = d.news.map((n) => `
    <div class="news-item">
      <div class="n-top">
        <b><span class="senti-tag ${n.sentiment}">${n.sentiment === "positive" ? "긍정" : n.sentiment === "negative" ? "부정" : "중립"}</span>
        <a href="${n.url}" target="_blank">${n.title}</a></b>
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
  ctx.lineWidth = 12; ctx.lineCap = "round";
  ctx.strokeStyle = "#1f2635";
  ctx.beginPath(); ctx.arc(cx, cy, r, Math.PI * 0.75, Math.PI * 2.25); ctx.stroke();
  ctx.strokeStyle = scoreColor(score);
  ctx.beginPath();
  ctx.arc(cx, cy, r, Math.PI * 0.75, Math.PI * (0.75 + 1.5 * (score / 100)));
  ctx.stroke();
  ctx.fillStyle = "#e6e9f0"; ctx.textAlign = "center";
  ctx.font = "800 30px sans-serif";
  ctx.fillText(Math.round(score), cx, cy + 8);
  ctx.font = "12px sans-serif"; ctx.fillStyle = "#8a93a6";
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
let chartCtx = { code: null, candles: [], targets: {}, technical: {}, tf: "day" };
let chartApi = null;             // lightweight-charts 인스턴스
let rsState = { lo: 0, hi: 1 };  // 구간 슬라이더 위치(0~1)
let rsSync = false;              // 슬라이더↔차트 되먹임 방지

function renderChart(d) {
  chartCtx = { code: d.code, candles: d.candles || [], targets: d.targets || {},
               technical: d.technical || {}, tf: "day" };
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

  chart = LC.createChart(el, {
    layout: { background: { color: "transparent" }, textColor: txt, fontFamily: "Pretendard, sans-serif", fontSize: 11 },
    grid: { vertLines: { color: gridC }, horzLines: { color: gridC } },
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
  });
  candleSeries.setData(d.candles.map((c) => ({ time: toDate(c.date), open: c.open, high: c.high, low: c.low, close: c.close })));

  const volSeries = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.86, bottom: 0 } });
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
  if (markers.length) candleSeries.setMarkers(markers);

  const addPriceLine = (price, color, title) => {
    if (!price) return;
    candleSeries.createPriceLine({ price, color, lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title });
  };
  addPriceLine(d.targets.consensus, "#f6465d", "목표주가");
  if (d.technical.available) {
    addPriceLine(d.technical.support, "#3e7bfa", "지지");
    addPriceLine(d.technical.resistance, "#9aa3ba", "저항");
  }

  // 기간 선택 — 봉 주기에 따라 기본 구간(bars 수)이 달라진다
  const len = d.candles.length;
  const tf = chartCtx.tf;
  const PERIODS = {
    day:   [["3개월", 66], ["6개월", 125], ["1년", 250], ["3년", 750], ["5년", 1250], ["전체", 0]],
    week:  [["6개월", 26], ["1년", 52], ["2년", 104], ["3년", 156], ["5년", 260], ["전체", 0]],
    month: [["1년", 12], ["2년", 24], ["3년", 36], ["5년", 60], ["10년", 120], ["전체", 0]],
  };
  const defBars = { day: 125, week: 52, month: 24 }[tf];
  const setRange = (bars) => {
    if (!bars || bars >= len) {
      chart.timeScale().fitContent();
      rsState.lo = 0; rsState.hi = 1;
    } else {
      chart.timeScale().setVisibleLogicalRange({ from: len - bars, to: len - 1 });
      rsState.lo = Math.max(0, (len - bars) / (len - 1));
      rsState.hi = 1;
    }
    paintSlider();
  };
  const periods = PERIODS[tf].filter(([, bars]) => bars === 0 || bars <= len * 1.1);
  $("chart-controls").innerHTML = periods.map(([label, bars]) =>
    `<button data-bars="${bars}" class="${bars === defBars ? "active" : ""}">${label}</button>`).join("");
  $("chart-controls").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      $("chart-controls").querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      setRange(+b.dataset.bars);
    };
  });

  // 차트↔슬라이더 동기화: 차트를 드래그/줌하면 슬라이더 핸들을 따라 움직인다
  chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
    if (!r || rsSync || len < 2) return;
    rsState.lo = Math.max(0, Math.min(1, r.from / (len - 1)));
    rsState.hi = Math.max(0, Math.min(1, r.to / (len - 1)));
    paintSlider();
  });

  setRange(defBars);
}

/* ---- 구간 설정 슬라이더 ---- */
function applySliderToChart() {
  if (!chart || chartCtx.candles.length < 2) return;
  const len = chartCtx.candles.length;
  rsSync = true;
  chart.timeScale().setVisibleLogicalRange({
    from: rsState.lo * (len - 1), to: rsState.hi * (len - 1),
  });
  rsSync = false;
}
function paintSlider() {
  const loEl = $("rs-lo"), hiEl = $("rs-hi"), fill = $("rs-fill");
  if (!loEl) return;
  const loPct = rsState.lo * 100, hiPct = rsState.hi * 100;
  loEl.style.left = `${loPct}%`;
  hiEl.style.left = `${hiPct}%`;
  fill.style.left = `${loPct}%`;
  fill.style.width = `${hiPct - loPct}%`;
  const cs = chartCtx.candles;
  if (cs.length) {
    const at = (p) => { const c = cs[Math.min(cs.length - 1, Math.round(p * (cs.length - 1)))]; const s = c.date; return `${s.slice(2, 4)}.${s.slice(4, 6)}`; };
    $("rs-label-lo").textContent = at(rsState.lo);
    $("rs-label-hi").textContent = at(rsState.hi);
  }
}
function initRangeSlider() {
  const track = document.querySelector("#range-slider .rs-track");
  if (!track) return;
  let dragging = null;
  const onMove = (clientX) => {
    if (!dragging) return;
    const rect = track.getBoundingClientRect();
    let p = (clientX - rect.left) / rect.width;
    p = Math.max(0, Math.min(1, p));
    if (dragging === "lo") rsState.lo = Math.min(p, rsState.hi - 0.02);
    else rsState.hi = Math.max(p, rsState.lo + 0.02);
    paintSlider();
    applySliderToChart();
  };
  track.querySelectorAll(".rs-handle").forEach((h) => {
    h.addEventListener("mousedown", (e) => { dragging = h.dataset.h; e.preventDefault(); });
    h.addEventListener("touchstart", (e) => { dragging = h.dataset.h; }, { passive: true });
  });
  document.addEventListener("mousemove", (e) => onMove(e.clientX));
  document.addEventListener("touchmove", (e) => { if (dragging && e.touches[0]) onMove(e.touches[0].clientX); }, { passive: true });
  document.addEventListener("mouseup", () => { dragging = null; });
  document.addEventListener("touchend", () => { dragging = null; });
}

/* ---------------- compare ---------------- */
let compareList = [];
let lastAnalysis = null;
const CMP_COLORS = ["#6366f1", "#22d3ee", "#ff6b9d"];

function addCompare(d) {
  if (!compareList.some((x) => x.code === d.code)) {
    if (compareList.length >= 3) { alert("비교는 최대 3종목까지 가능합니다."); return; }
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
  if (compareList.length < 2) { alert("2종목 이상 담아주세요."); return; }
  clearInterval(priceTimer);
  $("landing").classList.add("hidden");
  $("report").classList.add("hidden");
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
  const upsideTxt = best.upside != null ? ` · 목표주가 상승여력 ${sign(best.upside, 1)}%` : "";
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

  /* 기법별 점수 바 */
  $("pro-parts").innerHTML = Object.entries(p.parts || {}).map(([k, v]) =>
    `<div class="tp-bar"><span class="tp-label wide">${k}</span>
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
  if (p.box) cells.push(["박스권", `${fmt(p.box.bottom)} ~ ${fmt(p.box.top)}`,
    p.box.breakout ? "상단 돌파 중" : p.box.breakdown ? "하단 이탈" : `폭 ${p.box.width_pct}%`]);
  if (p.atr_pct != null) cells.push(["ATR 변동성", `${p.atr_pct}%`,
    p.atr_pct > 5 ? "고변동 — 비중 축소 권장" : "일간 평균 등락폭"]);
  if (p.obv) cells.push(["OBV 자금흐름", `<span class="${p.obv.slope >= 0 ? "up" : "down"}">${sign(p.obv.slope, 1)}</span>`,
    p.obv.divergence === "bullish" ? "강세 다이버전스(매집)" : p.obv.divergence === "bearish" ? "약세 다이버전스(분산)" : "추세 동행"]);
  if (p.disparity) cells.push(["이격도(20/60/120)",
    `${sign(p.disparity["20"] ?? 0, 1)}% / ${sign(p.disparity["60"] ?? 0, 1)}% / ${sign(p.disparity["120"] ?? 0, 1)}%`,
    "이동평균 대비 괴리율"]);
  if (p.vcp) cells.push(["VCP 변동성 수축", p.vcp.contracting ? "수축 진행" : "미형성",
    `구간 변동폭 ${(p.vcp.ranges || []).join("% → ")}%`]);
  $("pro-grid").innerHTML = cells.map(([label, val, sub]) =>
    `<div class="pro-item"><label>${label}</label><div class="pro-val">${val}</div><small>${sub}</small></div>`).join("");

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
    alert("AI 리포트 생성 실패: " + e.message);
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
  updateWatchBtn();
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
    updateWatchBtn();
  } catch (e) {
    $("auth-msg").textContent = "오류: " + e.message;
  }
};
$("logout-btn").onclick = async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch {}
  currentUser = null;
  watchedCodes = new Set();
  renderAuthUI();
  updateWatchBtn();
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

/* ---------------- 관심종목 매수 기회 알림 (웹푸시) ---------------- */
async function loadWatchlist() {
  if (!currentUser) { watchedCodes = new Set(); return; }
  try {
    const r = await api("/api/watch");
    watchedCodes = new Set(r.items.map((it) => it.code));
  } catch {
    watchedCodes = new Set();
  }
}

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

async function ensurePushSubscribed() {
  if (!window.isSecureContext) throw new Error("HTTPS로 접속해야 알림을 켤 수 있습니다.");
  if (!pushSupported()) throw new Error("이 브라우저는 웹 알림을 지원하지 않습니다.");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("알림이 차단되어 있습니다. 브라우저 설정에서 이 사이트의 알림을 허용해주세요.");
  const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  const key = (await (await fetch("/api/push/key")).json()).key;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) });
  }
  await api("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sub.toJSON()),
  });
}

$("watch-btn").onclick = async () => {
  if (!currentCode) return;
  if (!currentUser) { openAuthModal("login"); return; }
  const msg = $("watch-msg");
  msg.classList.remove("hidden");
  try {
    if (watchedCodes.has(currentCode)) {
      await api(`/api/watch/${currentCode}`, { method: "DELETE" });
      watchedCodes.delete(currentCode);
      msg.textContent = "매수 기회 알림을 껐습니다.";
    } else {
      msg.textContent = "알림 권한을 요청하는 중...";
      await ensurePushSubscribed();
      await api(`/api/watch/${currentCode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: $("stock-name").textContent }),
      });
      watchedCodes.add(currentCode);
      msg.textContent = "🔔 매수 매력도 65점 이상 + 현재가가 적정 매수가 이하가 되면 알려드립니다 (최대 15분 지연, 같은 종목은 24시간에 한 번). 확인용 테스트 알림을 보냈습니다 — 안 뜨면 브라우저 알림 설정을 확인해주세요.";
      api("/api/push/test", { method: "POST" }).catch(() => {});
    }
    updateWatchBtn();
  } catch (e) {
    msg.textContent = "오류: " + e.message;
  }
};

/* ---------------- admin ---------------- */
async function showAdmin() {
  $("landing").classList.add("hidden");
  $("report").classList.add("hidden");
  $("compare-view").classList.add("hidden");
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

async function showPortfolio() {
  $("landing").classList.add("hidden");
  $("report").classList.add("hidden");
  $("compare-view").classList.add("hidden");
  $("admin-view").classList.add("hidden");
  $("portfolio-view").classList.remove("hidden");
  setActiveNav("portfolio");
  window.scrollTo({ top: 0 });
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
    renderPortfolio(p);
  } catch (e) {
    $("pf-summary-card").classList.remove("hidden");
    $("pf-total").textContent = "";
    $("pf-metrics").innerHTML = `<p class="hint-p">불러오기 실패: ${e.message}</p>`;
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
  $("pf-holdings-card").classList.toggle("hidden", !p.available);
  renderTodayActions(p);
  renderRiskFlags(p);
  renderExposure(p);
  renderCorrelation(p);
  renderRebalance(p);

  if (!p.available) {
    if (hasHoldings) {
      $("pf-total").textContent = "";
      $("pf-metrics").innerHTML = `<p class="hint-p">${p.reason || "계산할 수 없습니다."}</p>`;
      $("pf-warnings").innerHTML = "";
    }
  } else {
    const pnlHTML = p.total_pnl != null
      ? `<span class="${updownClass(p.total_pnl)}">${sign(p.total_pnl)}원 (${sign(p.total_pnl_pct, 1)}%)</span>`
      : `<span class="hint">평균단가를 입력한 종목이 없습니다</span>`;
    const gradeHTML = p.grade
      ? `<span class="pf-grade" style="color:${scoreColor(p.score)}">${p.grade}등급 · ${p.grade_desc} (${p.score}점)</span>` : "";
    const todayHTML = p.today_pnl != null
      ? `<div class="pf-today">오늘 <span class="${updownClass(p.today_pnl)}">${sign(p.today_pnl)}원 (${sign(p.today_pnl_pct, 2)}%)</span></div>` : "";
    $("pf-total").innerHTML = `<label>총 평가금액</label><b>${won(p.total_value)}</b><span class="pf-pnl">${pnlHTML}</span>${gradeHTML}${todayHTML}`;
    const cells = [
      ["종합점수", p.score != null ? `${p.score}점` : "-"],
      ["밸류에이션 점수", p.valuation_score != null ? `${p.valuation_score}점` : "-"],
      ["기대수익률 (목표주가 기준)", p.expected_return != null ? `${sign(p.expected_return, 1)}%` : "-"],
      ["변동성 (연환산)", p.volatility != null ? `${p.volatility}%` : "데이터 부족"],
      ["최대 낙폭 (최근 1년)", p.max_drawdown != null ? `${p.max_drawdown}%` : "데이터 부족"],
    ];
    $("pf-metrics").innerHTML = cells.map(([l, v]) =>
      `<div class="pro-item"><label>${l}</label><div class="pro-val">${v}</div></div>`).join("");
    $("pf-warnings").innerHTML = (p.warnings || []).map((w) => `<li>${w}</li>`).join("");

    $("pf-sector-bars").innerHTML = Object.entries(p.sector_weight || {}).map(([sector, w]) => `
      <div class="pf-sector-row">
        <span class="pf-sector-name">${sector}</span>
        <div class="pf-sector-track"><div class="pf-sector-fill" style="width:${w}%"></div></div>
        <span class="pf-sector-pct">${w}%</span>
      </div>`).join("");

    $("pf-holdings-table").innerHTML = tableHTML(
      ["종목", "수량", "평균단가", "현재가", "평가금액", "평가손익", "현재비중", "권장비중", "AI판단", ""],
      p.items.map((it) => {
        const v = it.ai_verdict || {};
        return [
          it.name,
          fmt(it.shares) + "주",
          it.avg_price != null ? won(it.avg_price) : "-",
          won(it.price),
          won(it.value),
          it.pnl != null
            ? `<span class="${updownClass(it.pnl)}">${sign(it.pnl)}원 (${sign(it.pnl_pct, 1)}%)</span>`
            : "-",
          `${it.weight}%`,
          it.target_weight != null ? `${it.target_weight}%` : "-",
          `<span style="color:${verdictColor(v.tier)}">${v.emoji || ""} ${v.label || "-"}</span>`,
          `<button class="ghost-btn small" data-pf-rm="${it.code}">삭제</button>`,
        ];
      }));
    $("pf-holdings-table").querySelectorAll("[data-pf-rm]").forEach((b) => {
      b.onclick = async () => {
        await api(`/api/portfolio/${b.dataset.pfRm}`, { method: "DELETE" });
        loadPortfolio();
      };
    });
  }

  $("pf-excluded").textContent = (p.excluded && p.excluded.length)
    ? "제외됨: " + p.excluded.map((x) => `${x.name}(${x.reason})`).join(", ")
    : "";
}

/* 종목 검색 (메인 검색과 별개의 작은 드롭다운) */
const pfInput = $("pf-search-input");
const pfDropdown = $("pf-search-dropdown");
let pfSearchTimer = null;
pfInput.addEventListener("input", () => {
  clearTimeout(pfSearchTimer);
  pfSelected = null;
  $("pf-add-btn").disabled = true;
  const q = pfInput.value.trim();
  if (!q) { pfDropdown.classList.add("hidden"); return; }
  pfSearchTimer = setTimeout(async () => {
    try {
      const { items } = await api(`/api/search?q=${encodeURIComponent(q)}&market=KR`);
      pfDropdown.innerHTML = "";
      items.filter((it) => it.nation !== "US").forEach((it) => {
        const d = document.createElement("div");
        d.innerHTML = `<b>${it.name}</b><small>${it.code} · ${it.market}</small>`;
        d.onclick = () => {
          pfDropdown.classList.add("hidden");
          pfInput.value = it.name;
          pfSelected = { code: it.code, name: it.name };
          $("pf-add-btn").disabled = false;
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
  try {
    await api(`/api/portfolio/${pfSelected.code}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: pfSelected.name, shares, avg_price }),
    });
    pfInput.value = ""; $("pf-shares").value = ""; $("pf-price").value = ""; pfSelected = null;
    $("pf-add-msg").textContent = "추가됨. 포트폴리오 불러오는 중...";
    await loadPortfolio();
    $("pf-add-msg").textContent = "";
  } catch (e) {
    $("pf-add-btn").disabled = false;
    $("pf-add-msg").textContent = "오류: " + e.message;
  }
};
$("pf-back").onclick = goHome;

/* ---------------- KIS modal ---------------- */
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
  } catch (e) {
    $("kis-msg").textContent = "오류: " + e.message;
  }
};

/* ---------------- navigation + init ---------------- */
$("logo-home").onclick = goHome;
$("back-btn").onclick = goHome;
$("fav-btn").onclick = () => {
  if (!currentCode) return;
  toggleFav(currentCode, $("stock-name").textContent);
  updateFavBtn();
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
initRangeSlider();

initTheme();
renderFavBoard();
loadRanking();
loadMe();
loadThemeChips();
loadAnomalies();
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}
