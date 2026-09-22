const PAGE_SIZE = 20;
const app = document.getElementById("app");
const metaLine = document.getElementById("meta-line");
let meta = { years: [2024, 2025, 2026], score_version: "score-v1.7" };

const RANK_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h18v4H3V4zm0 6h12v4H3v-4zm0 6h7v4H3v-4z"/></svg>`;
const STATS_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18h4v-7H4v7zm6 0h4V5h-4v13zm6 0h4v-4h-4v4z"/></svg>`;
const BACK_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>`;
const BLOG_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h9l5 5v13H6V3zm8 1.6V9h4.4L14 4.6zM8 12h8v1.6H8V12zm0 3.2h8V16.8H8V15.2z"/></svg>`;
const SEARCH_RETURN_KEY = "iclr-search-return";
const mastActions = document.getElementById("mast-actions");

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function fmt(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 });
}

function fmtRate(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(Number(value) * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

function labeled(name, value) {
  return `${name} ${fmt(value)}`;
}

function sortPapers(papers, mode) {
  const copy = (papers || []).slice();
  const missingLast = (a, b) => {
    const aMissing = a == null, bMissing = b == null;
    if (aMissing === bMissing) return 0;
    return aMissing ? 1 : -1;
  };
  copy.sort((left, right) => {
    if (mode.startsWith("year")) {
      const yearDelta = mode === "year-asc" ? left.year - right.year : right.year - left.year;
      if (yearDelta) return yearDelta;
    } else {
      const missing = missingLast(left.R_p, right.R_p);
      if (missing) return missing;
      const scoreDelta = mode === "score-asc" ? left.R_p - right.R_p : right.R_p - left.R_p;
      if (scoreDelta) return scoreDelta;
    }
    if (left.year !== right.year) return right.year - left.year;
    const missing = missingLast(left.R_p, right.R_p);
    if (missing) return missing;
    if (left.R_p !== right.R_p) return right.R_p - left.R_p;
    return String(left.paper_id).localeCompare(String(right.paper_id));
  });
  return copy;
}

function paperCard(paper) {
  return `
    <article class="paper">
      <div class="paper-top">
        <a href="${esc(paper.forum_url)}" target="_blank" rel="noopener">${esc(paper.paper_id)}</a>
        <div class="meta">${paper.year} · ${esc(paper.decision || "unknown")} · ${labeled("Paper Score", paper.R_p)} · ${labeled("Deficit", paper.p_p)} · ${labeled("Surplus", paper.g_p)}</div>
      </div>
      <div class="reviews">${(paper.reviews || []).length
        ? paper.reviews.map((row) => `Review Score ${fmt(row.score)} / Confidence ${fmt(row.confidence)}`).join(" · ")
        : "No review scores"}</div>
    </article>`;
}

function yearLine(years) {
  return (years || [])
    .filter((row) => row.A !== null && row.A !== undefined)
    .map((row) => `${row.year} Annual Score ${fmt(row.A)}`)
    .join("  ·  ");
}

function authorHref(id) {
  return `#/author/${encodeURIComponent(id)}`;
}

function parseRoute() {
  const raw = (location.hash || "#/").replace(/^#/, "") || "/";
  const url = new URL(raw, "http://local.invalid");
  const parts = url.pathname.split("/").filter(Boolean);
  return { parts, params: url.searchParams };
}

async function api(path) {
  const res = await fetch(path);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function searchForm(query) {
  return `<form class="search-row" id="search-form">
    <input name="q" value="${esc(query)}" placeholder="Search by author name or author ID" autocomplete="off" autofocus>
    <button type="submit" hidden>Search</button>
    <div class="search-actions">
      <a class="icon-btn" href="#/rank" data-label="Rankings" aria-label="Rankings">${RANK_ICON}</a>
      <a class="icon-btn" href="#/stats" data-label="Statistics" aria-label="Statistics">${STATS_ICON}</a>
    </div>
  </form>`;
}

function rememberSearch(query) {
  const hash = query ? `#/search?q=${encodeURIComponent(query)}` : "#/";
  sessionStorage.setItem(SEARCH_RETURN_KEY, hash);
}

function searchReturnHref() {
  const saved = sessionStorage.getItem(SEARCH_RETURN_KEY) || "#/";
  if (saved === "#/" || saved.startsWith("#/search?")) return saved;
  return "#/";
}

function setMastActions(mode, href) {
  if (!mastActions) return;
  mastActions.hidden = false;
  if (mode === "blog") {
    mastActions.innerHTML = `<a class="icon-btn" href="/essay.html" data-label="Blog" aria-label="Blog">${BLOG_ICON}</a>`;
    return;
  }
  const target = href || "#/";
  mastActions.innerHTML = `<a class="icon-btn" href="${esc(target)}" data-label="Search" aria-label="Back to search">${BACK_ICON}</a>`;
}

function pageHead(title, extra) {
  return `<div class="page-head">
    <h2 class="page-title">${esc(title)}</h2>
    ${extra || ""}
  </div>`;
}

function renderHome(query, results, error) {
  rememberSearch(query);
  setMastActions("blog");
  const centered = !query && !results;
  const list = (results || []).map((row) => `
    <article class="slot">
      <div>
        <a class="slot-id" href="${authorHref(row.canonical_profile_id)}">${esc(row.canonical_profile_id)}</a>
        <div class="years">${esc(yearLine(row.year_scores) || "No annual scores")}</div>
      </div>
      <div>
        <div class="score-s"><small>Peak Score</small>${fmt(row.S)}</div>
      </div>
    </article>`).join("");
  app.innerHTML = `
    <section class="${centered ? "home" : ""}">
      ${searchForm(query)}
      <p class="hint">Try a family name, given name, reversed order, or profile ID. Same names stay as separate IDs.</p>
      ${error ? `<p class="error">${esc(error)}</p>` : ""}
      ${query && !error ? `<div class="slots">${list || `<p class="msg">No matching authors.</p>`}</div>` : ""}
    </section>`;
  document.getElementById("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const next = new FormData(event.target).get("q").trim();
    location.hash = next ? `#/search?q=${encodeURIComponent(next)}` : "#/";
  });
}

function renderAuthor(report) {
  setMastActions("back", searchReturnHref());
  const current = report.current_score || {};
  const summary = report.summary || {};
    const years = (report.years || []).slice().reverse().filter((row) => (row.papers || 0) > 0);
  const papers = report.papers || [];
  const stats = [
    ["Peak Score", current.S],
    ["Peak Year", current.year],
    ["Flagged Papers", current.N_bad],
    ["Submissions", summary.submissions],
    ["Accepted", summary.accepted],
  ];
  app.innerHTML = `
    ${pageHead("Author")}
    <section class="author-head">
      <p class="author-name"><a class="ext" href="${esc(report.profile_url)}" target="_blank" rel="noopener">${esc(report.display_name)}</a></p>
      <p class="author-id">${esc(report.author.canonical_profile_id)}</p>
    </section>
    <div class="stats">${stats.map(([k, v]) => `
      <div class="stat"><div class="k">${k}</div><div class="v">${k === "Peak Year" ? (v ?? "—") : fmt(v)}</div></div>`).join("")}
    </div>
    <h3>Yearly scores</h3>
    <div class="year-grid">${years.map((row) => `
      <article class="year-card">
        <div class="yr">${row.year}</div>
        <div class="metrics">
          <span>${labeled("Annual Score", row.A)}</span>
          <span>${labeled("Low-quality Score", row.B)}</span>
          <span>${labeled("High-quality Surplus", row.G)}</span>
          <span>${labeled("Desk-reject Score", row.D)}</span>
          <span>${labeled("Flagged Papers", row.N_bad)}</span>
          <span>${fmt(row.accepted)} accepted / ${fmt(row.papers)} submissions</span>
        </div>
      </article>`).join("") || `<p class="msg">No yearly records.</p>`}
    </div>
    <div class="papers-bar">
      <h3>Papers</h3>
      <label>Sort
        <select id="paper-sort">
          <option value="score-desc">Paper Score, high to low</option>
          <option value="score-asc">Paper Score, low to high</option>
          <option value="year-desc">Year, newest first</option>
          <option value="year-asc">Year, oldest first</option>
        </select>
      </label>
    </div>
    <div class="papers" id="paper-list">${sortPapers(papers, "score-desc").map(paperCard).join("") || `<p class="msg">No linked papers.</p>`}</div>`;
  const select = document.getElementById("paper-sort");
  const list = document.getElementById("paper-list");
  if (select && list) {
    select.addEventListener("change", () => {
      list.innerHTML = sortPapers(papers, select.value).map(paperCard).join("") || `<p class="msg">No linked papers.</p>`;
    });
  }
}

function renderRank(data, metric, year, page) {
  setMastActions("back", "#/");
  const rows = data.ranking || [];
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const current = Math.min(Math.max(page, 1), pages);
  const slice = rows.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);
  const annual = metric === "A";
  const options = [
    `<option value="S"${metric === "S" ? " selected" : ""}>Peak Score</option>`,
  ].concat((meta.years || []).slice().reverse().map((item) =>
    `<option value="A:${item}"${annual && Number(year) === Number(item) ? " selected" : ""}>${item} Annual Score</option>`))
    .join("");
  const scoreLabel = annual ? `${year} Annual Score` : "Peak Score";
  const rightValue = (row) => (annual ? fmt(row.A) : fmt(row.S));
  app.innerHTML = `
    ${pageHead("Rankings", `<label>Show <select id="metric">${options}</select></label>`)}
    <div class="slots">${slice.map((row) => `
      <article class="slot rank-row">
        <div class="rank-n">${row.rank}</div>
        <div>
          <a class="slot-id" href="${authorHref(row.canonical_profile_id)}">${esc(row.canonical_profile_id)}</a>
          <div class="years">${esc(yearLine(row.year_scores))}</div>
        </div>
        <div>
          <div class="score-s"><small>${esc(scoreLabel)}</small>${rightValue(row)}</div>
          <div class="counts">${fmt(row.accepted)} accepted / ${fmt(row.submissions)} submissions</div>
        </div>
      </article>`).join("") || `<p class="msg">No ranking rows for this view.</p>`}
    </div>
    <div class="pager">
      <button type="button" id="prev" ${current <= 1 ? "disabled" : ""}>Previous</button>
      <span>${current} / ${pages}</span>
      <button type="button" id="next" ${current >= pages ? "disabled" : ""}>Next</button>
    </div>`;
  document.getElementById("metric").addEventListener("change", (event) => {
    const value = event.target.value;
    if (value === "S") {
      location.hash = `#/rank?metric=${value}`;
    } else {
      location.hash = `#/rank?metric=A&year=${value.split(":")[1]}`;
    }
  });
  const go = (nextPage) => {
    const query = metric === "A"
      ? `metric=A&year=${year}&page=${nextPage}`
      : `metric=${metric}&page=${nextPage}`;
    location.hash = `#/rank?${query}`;
  };
  document.getElementById("prev").addEventListener("click", () => go(current - 1));
  document.getElementById("next").addEventListener("click", () => go(current + 1));
}

function fmtPct(share) {
  if (share === null || share === undefined || Number.isNaN(Number(share))) return "—";
  const percent = Number(share) * 100;
  const digits = percent >= 10 ? 1 : percent >= 1 ? 2 : 3;
  return `${percent.toLocaleString(undefined, { maximumFractionDigits: digits })}%`;
}

const CCDF = { W: 840, H: 320, yMin: 0.0005, m: { l: 58, r: 16, t: 28, b: 46 } };

function ccdfLayout(data) {
  const { W, H, yMin, m } = CCDF;
  const innerW = W - m.l - m.r;
  const innerH = H - m.t - m.b;
  const xMax = Math.max(Number(data.x_max) || 1, 1);
  const logMin = Math.log10(yMin);
  const xOf = (score) => m.l + (Math.min(Math.max(score, 0), xMax) / xMax) * innerW;
  const yOf = (share) => {
    const value = Math.min(Math.max(Number(share) || 0, yMin), 1);
    const t = (Math.log10(value) - logMin) / (0 - logMin);
    return m.t + innerH * (1 - t);
  };
  return { W, H, m, innerW, innerH, xMax, xOf, yOf };
}

function ccdfChart(data) {
  const points = data.ccdf || [];
  if (!points.length) return `<p class="msg">No scores for this view.</p>`;
  const { W, H, m, innerH, xMax, xOf, yOf } = ccdfLayout(data);
  const yTicks = [1, 0.1, 0.01, 0.001];
  const grid = yTicks.map((share) => {
    const y = yOf(share);
    return `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${y}" y2="${y}"></line>
      <text class="tick" x="${m.l - 8}" y="${y + 4}" text-anchor="end">${fmtPct(share)}</text>`;
  }).join("");
  const step = xMax <= 16 ? 2 : xMax <= 40 ? 5 : 10;
  const xLabels = [];
  for (let value = 0; value <= xMax + 1e-9; value += step) xLabels.push(value);
  const xAxis = xLabels.map((value) => {
    const x = xOf(value);
    return `<text class="tick" x="${x}" y="${H - 22}" text-anchor="middle">${fmt(value)}</text>`;
  }).join("");
  let path = "";
  points.forEach((point, index) => {
    const x = xOf(point.threshold);
    const y = yOf(point.share);
    if (index === 0) path += `M ${x} ${y}`;
    else path += ` H ${x} V ${y}`;
  });
  const last = points[points.length - 1];
  path += ` H ${xOf(xMax)}`;
  const placed = [];
  const marks = (data.references || []).filter((row) => row.value <= xMax).map((row) => {
    const x = xOf(row.value);
    const text = row.p === 0.5 ? "Median" : row.label;
    const width = text.length * 6.4 + 8;
    const crowded = placed.some((prior) => Math.abs(prior - x) < Math.max(width, 78));
    placed.push(x);
    const labelY = m.t + (crowded ? 24 : 12);
    const anchor = x < m.l + 28 ? "start" : x > W - m.r - 28 ? "end" : "middle";
    const labelX = anchor === "start" ? x + 4 : anchor === "end" ? x - 4 : x;
    return `<line class="pct" x1="${x}" x2="${x}" y1="${m.t}" y2="${m.t + innerH}"></line>
      <text class="pct-label" x="${labelX}" y="${labelY}" text-anchor="${anchor}">${esc(text)}</text>`;
  }).join("");
  return `<div class="ccdf-wrap">
    <svg id="ccdf-chart" class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Share of authors at or above each score">
      ${grid}
      ${marks}
      <path class="ccdf" d="${path}"></path>
      <line id="ccdf-guide" class="guide" x1="${m.l}" x2="${m.l}" y1="${m.t}" y2="${m.t + innerH}" visibility="hidden"></line>
      <circle id="ccdf-dot" class="dot" cx="${xOf(last.threshold)}" cy="${yOf(last.share)}" r="3.5" visibility="hidden"></circle>
      <line class="axis" x1="${m.l}" x2="${m.l}" y1="${m.t}" y2="${m.t + innerH}"></line>
      <line class="axis" x1="${m.l}" x2="${W - m.r}" y1="${m.t + innerH}" y2="${m.t + innerH}"></line>
      ${xAxis}
      <text class="axis-name" x="${(m.l + W - m.r) / 2}" y="${H - 4}" text-anchor="middle">Score threshold</text>
    </svg>
    <div id="ccdf-tip" class="ccdf-tip" hidden></div>
  </div>`;
}

function bindCcdf(data) {
  const svg = document.getElementById("ccdf-chart");
  const tip = document.getElementById("ccdf-tip");
  const guide = document.getElementById("ccdf-guide");
  const dot = document.getElementById("ccdf-dot");
  const points = data.ccdf || [];
  if (!svg || !tip || !points.length) return;
  const { W, m, innerW, xMax, xOf, yOf } = ccdfLayout(data);
  const hide = () => {
    tip.hidden = true;
    guide.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
  };
  const show = (clientX, clientY) => {
    const rect = svg.getBoundingClientRect();
    const viewX = ((clientX - rect.left) / rect.width) * W;
    const score = Math.min(Math.max(((viewX - m.l) / innerW) * xMax, 0), xMax);
    let chosen = points[0];
    for (const point of points) {
      if (point.threshold <= score + 1e-9) chosen = point;
      else break;
    }
    const x = xOf(chosen.threshold);
    guide.setAttribute("x1", x);
    guide.setAttribute("x2", x);
    guide.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x);
    dot.setAttribute("cy", yOf(chosen.share));
    dot.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.innerHTML = `<strong>Score ≥ ${esc(fmt(chosen.threshold))}</strong><br>${fmt(chosen.count)} authors<br>${fmtPct(chosen.share)} of authors`;
    const wrap = svg.parentElement.getBoundingClientRect();
    let left = clientX - wrap.left + 12;
    let top = clientY - wrap.top + 12;
    if (left + tip.offsetWidth > wrap.width) left = clientX - wrap.left - tip.offsetWidth - 12;
    if (top + tip.offsetHeight > wrap.height) top = Math.max(8, clientY - wrap.top - tip.offsetHeight - 12);
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${Math.max(0, top)}px`;
  };
  svg.addEventListener("mousemove", (event) => show(event.clientX, event.clientY));
  svg.addEventListener("mouseleave", hide);
}

function bandPct(share) {
  if (share === null || share === undefined || Number.isNaN(Number(share))) return "—";
  return `${(Number(share) * 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}%`;
}

function bandChart(data) {
  const bands = data.bands || [];
  if (!bands.length) return "";
  const maxCount = Math.max(...bands.map((band) => band.count), 1);
  return `<div class="bands">${bands.map((band, index) => {
    const width = band.count ? Math.max((band.count / maxCount) * 100, 0.6) : 0;
    const ink = 0.45 + (index / Math.max(bands.length - 1, 1)) * 0.55;
    return `<div class="band-row">
      <span class="band-label">${esc(band.label)}</span>
      <span class="band-track"><span class="band-fill" style="width:${width}%;opacity:${ink.toFixed(3)}"></span></span>
      <span class="band-count">${fmt(band.count)}</span>
      <span class="band-pct">${bandPct(band.share)}</span>
    </div>`;
  }).join("")}</div>`;
}

function renderStats(data, metric, year) {
  setMastActions("back", "#/");
  const annual = metric === "A";
  const options = [
    `<option value="S"${metric === "S" ? " selected" : ""}>Peak Score</option>`,
  ].concat((meta.years || []).slice().reverse().map((item) =>
    `<option value="A:${item}"${annual && Number(year) === Number(item) ? " selected" : ""}>${item} Annual Score</option>`))
    .join("");
  const byP = Object.fromEntries((data.percentiles || []).map((row) => [row.p, row]));
  const cards = [
    ["Authors with a score", fmt(data.n)],
    ["Mean", fmt(data.mean)],
    ["Median", fmt(byP[0.5] && byP[0.5].value)],
    ["75th percentile", fmt(byP[0.75] && byP[0.75].value)],
    ["90th percentile", fmt(byP[0.9] && byP[0.9].value)],
    ["99th percentile", fmt(byP[0.99] && byP[0.99].value)],
  ];
  const axisNote = data.axis_truncated
    ? `The horizontal axis runs through the top 0.1% of scores (to ${fmt(data.x_max)}). The maximum is ${fmt(data.max)}.`
    : `The horizontal axis is the score. The maximum is ${fmt(data.max)}.`;
  app.innerHTML = `
    ${pageHead("Statistics", `<label>Show <select id="metric">${options}</select></label>`)}
    <p class="hint">The distribution is highly right-skewed: most authors have low scores, while a small tail accounts for unusually high values.</p>
    <div class="stats">${cards.map(([name, value]) => `
      <div class="stat"><div class="k">${esc(name)}</div><div class="v">${esc(value)}</div></div>`).join("")}
    </div>
    <h3>Share at or above a score</h3>
    <div class="chart-wrap">${ccdfChart(data)}</div>
    <p class="hint">Each point is the share of authors whose score is at least that threshold. The vertical axis is logarithmic. Dashed lines mark the median, 75th, 90th, and 99th percentiles. ${esc(axisNote)}</p>
    <h3>Score bands</h3>
    ${bandChart(data)}
    <h3>Tail focus</h3>
    <div class="stats">${(data.tail || []).map((row) => `
      <div class="stat">
        <div class="k">${esc(row.label)}</div>
        <div class="v">≥ ${esc(fmt(row.value))}</div>
        <div class="share">${fmt(row.count)} authors · ${fmtPct(row.share)}</div>
      </div>`).join("")}
    </div>`;
  document.getElementById("metric").addEventListener("change", (event) => {
    const value = event.target.value;
    if (value === "S") location.hash = "#/stats?metric=S";
    else location.hash = `#/stats?metric=A&year=${value.split(":")[1]}`;
  });
  bindCcdf(data);
}

async function route() {
  const { parts, params } = parseRoute();
  try {
    if (parts[0] === "author" && parts[1]) {
      renderAuthor(await api(`/api/author?id=${encodeURIComponent(decodeURIComponent(parts[1]))}`));
      return;
    }
    if (parts[0] === "rank") {
      const metric = (() => {
        const value = (params.get("metric") || "S").toUpperCase();
        return value === "A" ? "A" : "S";
      })();
      const year = params.get("year") || (meta.years || []).slice(-1)[0];
      const page = Number(params.get("page") || 1);
      const path = metric === "A"
        ? `/api/rank?metric=A&year=${encodeURIComponent(year)}`
        : `/api/rank?metric=${encodeURIComponent(metric)}`;
      renderRank(await api(path), metric, year, page);
      return;
    }
    if (parts[0] === "stats") {
      const raw = (params.get("metric") || "S").toUpperCase();
      const metric = raw === "A" ? raw : "S";
      const year = params.get("year") || (meta.years || []).slice(-1)[0];
      const path = metric === "A"
        ? `/api/stats?metric=A&year=${encodeURIComponent(year)}`
        : `/api/stats?metric=${metric}`;
      renderStats(await api(path), metric, year);
      return;
    }
    const query = (params.get("q") || "").trim();
    if (!query) {
      renderHome("");
      return;
    }
    const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
    renderHome(query, data.results);
  } catch (err) {
    if (parts[0] === "author" || parts[0] === "rank" || parts[0] === "stats") {
      setMastActions("back", parts[0] === "author" ? searchReturnHref() : "#/");
      app.innerHTML = `${pageHead("Error")}<p class="error">${esc(err.message)}</p>`;
    } else {
      renderHome(params.get("q") || "", [], err.message);
    }
  }
}

async function boot() {
  try {
    meta = await api("/api/meta");
    const stamp = (meta.source_date || "").slice(0, 10);
    metaLine.textContent = `${meta.score_version || "score-v1.7"}${stamp ? " · " + stamp : ""}`;
  } catch (_) {
    /* keep defaults if the API is not ready */
  }
  window.addEventListener("hashchange", route);
  route();
}

boot();
