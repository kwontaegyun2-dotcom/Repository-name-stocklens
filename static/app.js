/* StockLens frontend */
const $ = (id) => document.getElementById(id);
let currentCode = null;
let priceTimer = null;
let chart = null;

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
function renderFavBoard() {
  const favs = getFavs();
  const el = $("fav-board");
  if (!favs.length) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.innerHTML = `<h2>⭐ 관심종목</h2><div class="fav-chips">` +
    favs.map((f) => `<button class="fav-chip" data-code="${f.code}">${f.name}<span class="x" data-x="${f.code}">✕</span></button>`).join("") +
    `</div>`;
  el.querySelectorAll(".fav-chip").forEach((c) => {
    c.onclick = (e) => {
      if (e.target.classList.contains("x")) { removeFav(e.target.dataset.x); renderFavBoard(); return; }
      analyze(c.dataset.code);
    };
  });
}
function updateFavBtn() {
  const b = $("fav-btn");
  const on = isFav(currentCode);
  b.textContent = on ? "★" : "☆";
  b.classList.toggle("on", on);
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
function goHome() {
  clearInterval(priceTimer);
  currentCode = null;
  $("report").classList.add("hidden");
  $("compare-view").classList.add("hidden");
  $("screener-view").classList.add("hidden");
  $("loading").classList.add("hidden");
  $("landing").classList.remove("hidden");
  window.scrollTo({ top: 0 });
  renderFavBoard();
  loadRanking(currentSector);
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
    if ((!d.items || d.items.length === 0) && d.computing) {
      $("rank-list").innerHTML = `<div class="rank-loading"><div class="spinner sm"></div><span>랭킹 집계 중… (최초 실행 시 30초~1분, 자동 갱신)</span></div>`;
      clearTimeout(rankPollTimer);
      rankPollTimer = setTimeout(() => loadRanking(currentSector), 5000);
      return;
    }
    renderRanking(d);
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
    $("rank-updated").textContent = `· ${dt.getHours()}시 ${String(dt.getMinutes()).padStart(2, "0")}분 기준`;
  }
  if (!d.items.length) {
    $("rank-list").innerHTML = `<div class="rank-loading"><span>해당 섹터 데이터가 없습니다.</span></div>`;
    return;
  }
  rankAll = d.items;
  rankShown = 5;
  paintRanking();
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

  /* score */
  drawGauge(d.total.total_score);
  $("grade").textContent = d.total.grade;
  $("grade").style.color = scoreColor(d.total.total_score);
  $("grade-desc").textContent = d.total.grade_desc + " · " + d.total.total_score + "점";

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

  /* backtest */
  renderBacktest(d.backtest);

  /* tech summary */
  if (tech.available) {
    $("tech-summary").innerHTML = `
      <div class="tech-item"><label>기술 점수</label><div style="color:${scoreColor(tech.score)}">${tech.score}점</div></div>
      <div class="tech-item"><label>RSI(14)</label><div>${tech.rsi ?? "-"}</div></div>
      <div class="tech-item"><label>SMA20</label><div>${fmt(tech.sma["20"])}</div></div>
      <div class="tech-item"><label>SMA60</label><div>${fmt(tech.sma["60"])}</div></div>
      <div class="tech-item"><label>52주 위치</label><div>${tech.pos_52w}%</div></div>
      <div class="tech-item"><label>거래량(5d/20d)</label><div>${tech.volume_ratio ?? "-"}배</div></div>`;
    $("tech-signals").innerHTML = tech.signals.map((s) => `<li class="${s.type}">${s.text}</li>`).join("");
  } else {
    $("tech-summary").innerHTML = "<p class='hint-p'>차트 데이터가 부족합니다.</p>";
    $("tech-signals").innerHTML = "";
  }

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
function renderChart(d) {
  const el = $("chart-container");
  el.innerHTML = "";
  $("chart-controls").innerHTML = "";
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
    timeScale: { borderVisible: false, rightOffset: 5, minBarSpacing: 2, fixRightEdge: true },
    crosshair: {
      mode: LC.CrosshairMode.Normal,
      vertLine: { color: crossC, width: 1, style: LC.LineStyle.Dashed, labelBackgroundColor: "#6366f1" },
      horzLine: { color: crossC, width: 1, style: LC.LineStyle.Dashed, labelBackgroundColor: "#6366f1" },
    },
    // 마우스휠은 페이지 스크롤로(차트 확대 방해 X) · 드래그로 이동 · 기간버튼/핀치로 확대
    handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { axisPressedMouseMove: true, mouseWheel: false, pinch: true },
    height: 440,
    autoSize: true,
  });

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

  // 기간 선택 (스크롤/줌 대신 클릭 한 번으로 보기)
  const len = d.candles.length;
  const setRange = (bars) => {
    if (!bars || bars >= len) chart.timeScale().fitContent();
    else chart.timeScale().setVisibleLogicalRange({ from: len - bars, to: len - 1 });
  };
  const periods = [["3개월", 66], ["6개월", 125], ["1년", 250], ["전체", 0]];
  $("chart-controls").innerHTML = periods.map(([label, bars], i) =>
    `<button data-bars="${bars}" class="${i === 1 ? "active" : ""}">${label}</button>`).join("");
  $("chart-controls").querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      $("chart-controls").querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      setRange(+b.dataset.bars);
    };
  });
  setRange(125);
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
  window.scrollTo({ top: 0 });
  drawCompareRadar();
  renderCompareTable();
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

/* ---------------- screener ---------------- */
let screenerRows = [];
let scrPreset = "value";
let scrPollTimer = null;
const PRESETS = {
  value:    { pbr: 1.0, per: 15, roe: 8, div: 0, mcap: 3000, flow: false, sort: "pbr" },
  rerating: { pbr: 2.0, per: 40, roe: 5, div: 0, mcap: 2000, flow: true, sort: "rerating" },
  sector:   { pbr: 10, per: 100, roe: 0, div: 0, mcap: 2000, flow: false, sort: "roe_pct", sectorMode: true },
  dividend: { pbr: 1.5, per: 20, roe: 0, div: 3, mcap: 3000, flow: false, sort: "div" },
};

function openScreener() {
  clearInterval(priceTimer);
  $("landing").classList.add("hidden");
  $("report").classList.add("hidden");
  $("compare-view").classList.add("hidden");
  $("screener-view").classList.remove("hidden");
  window.scrollTo({ top: 0 });
  fetchScreener();
}
async function fetchScreener() {
  try {
    const d = await api("/api/screener");
    if ((!d.rows || !d.rows.length) && d.computing) {
      $("scr-table").innerHTML = `<div class="rank-loading"><div class="spinner sm"></div><span>스크리너 집계 중… (최초 1~2분 소요, 자동 갱신)</span></div>`;
      clearTimeout(scrPollTimer);
      scrPollTimer = setTimeout(fetchScreener, 6000);
      return;
    }
    screenerRows = d.rows || [];
    if (d.updated_at) {
      const dt = new Date(d.updated_at * 1000);
      $("scr-updated").textContent = `· ${dt.getHours()}시 ${String(dt.getMinutes()).padStart(2, "0")}분 기준 · ${d.count}종목 스캔`;
    }
    applyPreset(scrPreset, false);
  } catch {
    $("scr-table").innerHTML = `<div class="rank-loading"><span>스크리너를 불러오지 못했습니다.</span></div>`;
  }
}
function applyPreset(name, setInputs = true) {
  scrPreset = name;
  document.querySelectorAll("#scr-presets button").forEach((b) => b.classList.toggle("active", b.dataset.preset === name));
  const p = PRESETS[name];
  if (setInputs) {
    $("f-pbr").value = p.pbr; $("f-per").value = p.per; $("f-roe").value = p.roe;
    $("f-div").value = p.div; $("f-mcap").value = p.mcap; $("f-flow").checked = p.flow;
  }
  renderScreener();
}
function renderScreener() {
  const pbr = parseFloat($("f-pbr").value) || 999;
  const per = parseFloat($("f-per").value) || 9999;
  const roe = parseFloat($("f-roe").value) || -999;
  const div = parseFloat($("f-div").value) || 0;
  const mcap = parseFloat($("f-mcap").value) || 0;
  const flowOnly = $("f-flow").checked;
  const p = PRESETS[scrPreset];

  let rows = screenerRows.filter((r) =>
    r.pbr != null && r.pbr <= pbr &&
    r.per != null && r.per > 0 && r.per <= per &&
    (r.roe == null ? roe <= 0 : r.roe >= roe) &&
    (r.div == null ? div <= 0 : r.div >= div) &&
    (r.mcap == null || r.mcap >= mcap) &&
    (!flowOnly || (r.foreign20 > 0 && r.inst20 > 0)));
  if (p.sectorMode) rows = rows.filter((r) => r.pbr_pct != null && r.pbr_pct < 0.3 && r.roe_pct != null && r.roe_pct > 0.7);

  rows.sort((a, b) => {
    if (p.sort === "pbr") return (a.pbr ?? 99) - (b.pbr ?? 99);
    if (p.sort === "div") return (b.div ?? 0) - (a.div ?? 0);
    if (p.sort === "roe_pct") return (b.roe_pct ?? 0) - (a.roe_pct ?? 0);
    return (b.rerating ?? -99) - (a.rerating ?? -99);
  });
  rows = rows.slice(0, 60);
  $("scr-count").textContent = `조건 부합 ${rows.length}종목 (상위 60 표시)`;

  const flowCell = (r) =>
    `${r.foreign20 > 0 ? '<span class="up">외인▲</span>' : '<span class="down">외인▽</span>'} ${r.inst20 > 0 ? '<span class="up">기관▲</span>' : '<span class="down">기관▽</span>'}`;
  $("scr-table").innerHTML = rows.length ? tableHTML(
    ["종목명", "현재가", "PBR", "PER", "ROE", "배당", "시총(억)", "20일 수급", "리레이팅"],
    rows.map((r) => [
      `<b class="scr-name" data-code="${r.code}">${r.name}</b>`,
      fmt(r.price),
      r.pbr != null ? r.pbr.toFixed(2) : "-",
      r.per != null ? r.per.toFixed(1) : "-",
      r.roe != null ? `<span class="${r.roe >= 10 ? "up" : ""}">${r.roe.toFixed(1)}%</span>` : "-",
      r.div != null ? r.div.toFixed(2) + "%" : "-",
      fmt(r.mcap),
      flowCell(r),
      `<b>${r.rerating != null ? r.rerating.toFixed(2) : "-"}</b>`,
    ])) : "<p class='hint-p'>조건에 맞는 종목이 없습니다. 필터를 완화해 보세요.</p>";
  $("scr-table").querySelectorAll(".scr-name").forEach((el) => el.onclick = () => analyze(el.dataset.code));
}

/* ---------------- backtest ---------------- */
function renderBacktest(bt) {
  const card = $("backtest-card");
  if (!bt || !bt.available) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  const months = Math.max(1, Math.round(bt.period_days / 21));
  $("bt-period").textContent = `최근 약 ${months}개월 · 단순보유 시 ${sign(bt.buy_hold, 1)}%`;

  const strat = (title, desc, s) => {
    if (!s) return `<div class="bt-strat"><div class="bt-name">${title}</div><div class="bt-empty">해당 기간 신호 발생 없음</div></div>`;
    if (!s.trades) {
      const op = s.open_return != null ? `미청산 포지션 평가손익 <b class="${s.open_return >= 0 ? "up" : "down"}">${sign(s.open_return, 1)}%</b>` : "완료된 매매 없음";
      return `<div class="bt-strat"><div class="bt-name">${title} <small>${desc}</small></div><div class="bt-empty">${op} · 추세 지속 중</div></div>`;
    }
    const wrColor = s.win_rate >= 60 ? "var(--green)" : s.win_rate >= 40 ? "var(--amber)" : "var(--up)";
    return `<div class="bt-strat">
      <div class="bt-name">${title} <small>${desc}</small></div>
      <div class="bt-stats">
        <div><label>매매</label><span>${s.trades}회</span></div>
        <div><label>승률</label><span style="color:${wrColor};font-weight:800">${s.win_rate}%</span></div>
        <div><label>평균수익</label><span class="${s.avg_return >= 0 ? "up" : "down"}">${sign(s.avg_return, 1)}%</span></div>
        <div><label>최고/최저</label><span><span class="up">${sign(s.best, 0)}%</span> <span class="down">${sign(s.worst, 0)}%</span></span></div>
      </div>
    </div>`;
  };
  $("backtest-body").innerHTML =
    strat("골든/데드크로스", "20·60일선 교차", bt.ma_cross) +
    strat("RSI 과매도·과매수", "RSI 30 매수 · 70 매도", bt.rsi);
}

/* ---------------- finance ---------------- */
function renderFinance(rows) {
  const rev = rows["매출액"] || [], op = rows["영업이익"] || [];
  const c = $("finance-chart"), ctx = c.getContext("2d");
  c.width = c.parentElement.clientWidth - 48;
  ctx.clearRect(0, 0, c.width, c.height);

  const periods = rev.map((r) => r.period);
  if (periods.length === 0) { $("finance-table").innerHTML = "<p class='hint-p'>재무 데이터가 없습니다.</p>"; return; }

  const vals = [...rev, ...op].map((r) => r.value).filter((v) => v != null);
  const maxV = Math.max(...vals, 1);
  const minV = Math.min(...vals, 0);
  const range = maxV - minV || 1;
  const W = c.width, H = c.height, pad = 30;
  const groupW = (W - pad * 2) / periods.length;
  const y = (v) => H - 30 - ((v - minV) / range) * (H - 60);

  ctx.strokeStyle = "#1f2635";
  ctx.beginPath(); ctx.moveTo(pad, y(0)); ctx.lineTo(W - pad, y(0)); ctx.stroke();

  periods.forEach((p, i) => {
    const x0 = pad + i * groupW;
    const bw = Math.min(groupW / 3.2, 34);
    const rv = rev[i]?.value, ov = op[i]?.value;
    const isCns = rev[i]?.consensus;
    if (rv != null) {
      ctx.fillStyle = isCns ? "rgba(79,140,255,.45)" : "#4f8cff";
      ctx.fillRect(x0 + groupW / 2 - bw - 3, Math.min(y(rv), y(0)), bw, Math.abs(y(rv) - y(0)));
    }
    if (ov != null) {
      ctx.fillStyle = isCns ? "rgba(46,204,113,.45)" : "#2ecc71";
      ctx.fillRect(x0 + groupW / 2 + 3, Math.min(y(ov), y(0)), bw, Math.abs(y(ov) - y(0)));
    }
    ctx.fillStyle = "#8a93a6"; ctx.font = "11px sans-serif"; ctx.textAlign = "center";
    const label = `${p.slice(0, 4)}${isCns ? "(E)" : ""}`;
    ctx.fillText(label, x0 + groupW / 2, H - 10);
  });
  ctx.textAlign = "left";
  ctx.fillStyle = "#4f8cff"; ctx.fillRect(pad, 6, 10, 10);
  ctx.fillStyle = "#8a93a6"; ctx.fillText("매출액", pad + 14, 15);
  ctx.fillStyle = "#2ecc71"; ctx.fillRect(pad + 70, 6, 10, 10);
  ctx.fillStyle = "#8a93a6"; ctx.fillText("영업이익", pad + 84, 15);

  const rowNames = Object.keys(rows).filter((k) => rows[k] && rows[k].length);
  $("finance-table").innerHTML = tableHTML(
    ["항목", ...periods.map((p, i) => `${p.slice(0, 4)}.${p.slice(4)}${rev[i]?.consensus ? "(E)" : ""}`)],
    rowNames.map((name) => [
      name,
      ...rows[name].map((cell) => (cell.value != null ? fmt(cell.value, 2) : "-")),
    ]));
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
  let h = md
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/^### (.*)$/gm, "<h3>$1</h3>")
    .replace(/^## (.*)$/gm, "<h2>$1</h2>")
    .replace(/^# (.*)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/^\s*[-*] (.*)$/gm, "<li>$1</li>")
    .replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, "<ul>$1</ul>");
  return h.split(/\n{2,}/).map((p) =>
    p.startsWith("<h") || p.startsWith("<ul") ? p : `<p>${p.replace(/\n/g, "<br>")}</p>`
  ).join("");
}

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

// 스크리너
$("screener-btn").onclick = openScreener;
$("scr-back").onclick = goHome;
document.querySelectorAll("#scr-presets button").forEach((b) => (b.onclick = () => applyPreset(b.dataset.preset)));
["f-pbr", "f-per", "f-roe", "f-div", "f-mcap"].forEach((id) => ($(id).oninput = renderScreener));
$("f-flow").onchange = renderScreener;

initTheme();
renderFavBoard();
loadRanking();
