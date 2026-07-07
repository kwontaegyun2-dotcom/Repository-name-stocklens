/* StockLens frontend */
const $ = (id) => document.getElementById(id);
let currentCode = null;
let priceTimer = null;
let chart = null;

/* ---------------- utils ---------------- */
const fmt = (n, digits = 0) =>
  n == null || isNaN(n) ? "-" : Number(n).toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
const won = (n) => (n == null ? "-" : fmt(n) + "원");

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
        d.innerHTML = `<b>${it.name}</b><small>${it.code} · ${it.market}</small>`;
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
  $("loading").classList.add("hidden");
  $("landing").classList.remove("hidden");
  window.scrollTo({ top: 0 });
  loadRanking(currentSector);
}

/* ---------------- ranking board ---------------- */
let currentSector = "전체";
let rankPollTimer = null;

async function loadRanking(sector = "전체") {
  currentSector = sector;
  try {
    const d = await api(`/api/ranking${sector && sector !== "전체" ? `?sector=${encodeURIComponent(sector)}` : ""}`);
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

function renderRanking(d) {
  if (d.updated_at) {
    const dt = new Date(d.updated_at * 1000);
    $("rank-updated").textContent = `· ${dt.getHours()}시 ${String(dt.getMinutes()).padStart(2, "0")}분 기준`;
  }
  if (!d.items.length) {
    $("rank-list").innerHTML = `<div class="rank-loading"><span>해당 섹터 데이터가 없습니다.</span></div>`;
    return;
  }
  $("rank-list").innerHTML = d.items.map((r, i) => {
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
        <div class="p">${won(r.price)}</div>
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
  document.querySelectorAll("#rank-list .rank-row").forEach((row) => {
    row.onclick = () => analyze(row.dataset.code);
  });
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
      $("live-price").textContent = won(p.price);
      const cls = updownClass(p.change);
      $("live-price").className = "live-price " + cls;
      $("live-change").className = "live-change " + cls;
      $("live-change").textContent = `${sign(p.change)}원 (${sign(p.rate, 2)}%)`;
      $("source-badge").textContent = p.source === "KIS" ? "한국투자증권 실시간" : "네이버 시세";
    }
  } catch {}
}

/* ---------------- render ---------------- */
function render(d) {
  /* header */
  $("stock-logo").src = d.logo || "";
  $("stock-logo").style.display = d.logo ? "" : "none";
  $("stock-name").textContent = d.name;
  $("stock-code").textContent = d.code;
  $("stock-market").textContent = d.market || "";
  $("live-price").textContent = won(d.price);
  const cls = updownClass(d.change);
  $("live-price").className = "live-price " + cls;
  $("live-change").className = "live-change " + cls;
  $("live-change").textContent = `${sign(d.change)}원 (${sign(d.rate, 2)}%)`;
  $("live-badge").textContent = d.market_status === "OPEN" ? "장중" : "장마감";
  $("source-badge").textContent = d.kis_enabled ? "한국투자증권 실시간" : "네이버 시세";
  if (d.public) $("kis-btn").classList.add("hidden");

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
  $("target-consensus").textContent = t.consensus ? won(t.consensus) : "데이터 없음";
  $("target-consensus-upside").textContent = t.consensus_upside != null ? `상승여력 ${sign(t.consensus_upside, 1)}%` : "";
  $("target-consensus-upside").className = "target-upside " + updownClass(t.consensus_upside);
  $("target-tech").textContent = t.technical ? won(t.technical) : "-";
  $("target-tech-upside").textContent = t.technical_upside != null ? `상승여력 ${sign(t.technical_upside, 1)}%` : "";
  $("target-tech-upside").className = "target-upside " + updownClass(t.technical_upside);

  const tech = d.technical;
  if (tech.available) {
    $("verdict").textContent = tech.verdict;
    $("verdict").className = "verdict " + tech.verdict_class;
    $("timing-comment").textContent = tech.timing_comment;
    $("cons-opinion").textContent = d.consensus.opinion ? `애널리스트: ${d.consensus.opinion} (${d.consensus.recomm_mean}/5)` : "";
    $("entry-grid").innerHTML = `
      <div class="entry-item"><label>매수 관심 구간</label><div>${fmt(tech.entry.buy_zone_low)}~${fmt(tech.entry.buy_zone_high)}</div></div>
      <div class="entry-item"><label>지지선</label><div class="down">${won(tech.entry.support)}</div></div>
      <div class="entry-item"><label>저항선</label><div>${won(tech.entry.resistance)}</div></div>
      <div class="entry-item"><label>손절 참고가</label><div class="down">${won(tech.entry.stop_loss)}</div></div>`;
  }

  /* chart */
  renderChart(d);

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
  $("metrics-grid").innerHTML = metricDefs.map(([label, v, unit, sub]) => `
    <div class="metric"><label>${label}</label>
      <div>${v != null ? fmt(v, 2) + unit : "-"} ${sub ? `<br><small>${sub}</small>` : ""}</div>
    </div>`).join("") +
    `<div class="metric"><label>시가총액</label><div>${m.market_cap ? fmt(m.market_cap / 10000, 1) + "조원" : "-"}</div></div>`;

  /* finance */
  renderFinance(d.finance_rows);

  /* flows */
  $("flow-table").innerHTML = tableHTML(
    ["일자", "종가", "외국인", "기관", "개인", "외국인 보유율"],
    d.flows.map((f) => [
      f.date ? `${f.date.slice(4, 6)}/${f.date.slice(6, 8)}` : "-",
      fmt(f.close),
      numCell(f.foreigner), numCell(f.organ), numCell(f.individual),
      f.foreigner_ratio || "-",
    ]));

  /* research */
  $("research-consensus").textContent = d.consensus.opinion
    ? `컨센서스: ${d.consensus.opinion} · 목표가 ${won(d.consensus.target_price)}` : "컨센서스 없음";
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
  $("peers-table").innerHTML = tableHTML(
    ["종목명", "현재가", "등락률", "시가총액(억원)"],
    d.peers.map((p) => [
      p.name, fmt(p.price),
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
  if (!window.LightweightCharts || !d.candles || d.candles.length === 0) {
    el.innerHTML = "<p class='hint-p'>차트 라이브러리를 불러올 수 없습니다 (인터넷 연결 확인).</p>";
    return;
  }
  chart = LightweightCharts.createChart(el, {
    layout: { background: { color: "transparent" }, textColor: "#8a93a6" },
    grid: { vertLines: { color: "#161c2a" }, horzLines: { color: "#161c2a" } },
    rightPriceScale: { borderColor: "#1f2635" },
    timeScale: { borderColor: "#1f2635" },
    height: 420,
    autoSize: true,
  });

  const toDate = (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
  const candles = d.candles.map((c) => ({
    time: toDate(c.date), open: c.open, high: c.high, low: c.low, close: c.close,
  }));

  const candleSeries = chart.addCandlestickSeries({
    upColor: "#ff4d4d", downColor: "#4d7cff",
    borderUpColor: "#ff4d4d", borderDownColor: "#4d7cff",
    wickUpColor: "#ff4d4d", wickDownColor: "#4d7cff",
  });
  candleSeries.setData(candles);

  const volSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" }, priceScaleId: "vol",
  });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  volSeries.setData(d.candles.map((c) => ({
    time: toDate(c.date), value: c.volume,
    color: c.close >= c.open ? "rgba(255,77,77,.35)" : "rgba(77,124,255,.35)",
  })));

  const closes = d.candles.map((c) => c.close);
  const addMA = (n, color) => {
    if (closes.length < n) return;
    const line = chart.addLineSeries({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
    const data = [];
    let sum = 0;
    for (let i = 0; i < closes.length; i++) {
      sum += closes[i];
      if (i >= n) sum -= closes[i - n];
      if (i >= n - 1) data.push({ time: toDate(d.candles[i].date), value: sum / n });
    }
    line.setData(data);
  };
  addMA(20, "#f5a623");
  addMA(60, "#2ecc71");
  addMA(120, "#9b59b6");

  const addPriceLine = (price, color, title, style) => {
    if (!price) return;
    candleSeries.createPriceLine({
      price, color, lineWidth: 1,
      lineStyle: style ?? LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true, title,
    });
  };
  addPriceLine(d.targets.consensus, "#ff4d4d", "목표주가(컨센서스)");
  if (d.technical.available) {
    addPriceLine(d.technical.support, "#4d7cff", "지지");
    addPriceLine(d.technical.resistance, "#8a93a6", "저항");
  }
  chart.timeScale().fitContent();
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
loadRanking();
