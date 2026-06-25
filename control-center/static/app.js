const state = {
  summary: null,
  inventory: [],
  containers: [],
  tailPath: "",
  activeView: "overview",
};

const titles = {
  overview: ["Overview", "Fleet health, recent fires, and monitor activity."],
  inventory: ["Inventory", "Bot-by-bot activity, config, and quick filters."],
  events: ["Bot Events", "Search and inspect normalized bot JSONL rows."],
  funding: ["Funding", "Monitor spreads, opportunities, and cost-adjusted paper PnL."],
  files: ["Files", "Inspect log freshness and raw tails."],
  ops: ["Ops", "Collector state and safe maintenance actions."],
};

function $(selector) {
  return document.querySelector(selector);
}

function $all(selector) {
  return [...document.querySelectorAll(selector)];
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.error) message = body.error;
    } catch (_) {}
    throw new Error(message);
  }
  return res.json();
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 2600);
}

function fmtNumber(value, digits = 0) {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtMoney(value) {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}$${number.toFixed(2)}`;
}

function fmtTime(value) {
  if (!value) return "—";
  return value.replace("T", " ").replace("+00:00", "Z").replace(".000", "");
}

function fmtSize(bytes) {
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(bytes || 0);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function rawButton(raw) {
  const encoded = encodeURIComponent(JSON.stringify(raw));
  return `<button class="secondary" data-raw="${encoded}">Raw</button>`;
}

function bindRawButtons() {
  $all("[data-raw]").forEach((button) => {
    button.addEventListener("click", () => {
      const raw = JSON.parse(decodeURIComponent(button.dataset.raw));
      toast(JSON.stringify(raw, null, 2).slice(0, 900));
    });
  });
}

function setView(view) {
  state.activeView = view;
  $all(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $all(".view").forEach((node) => node.classList.toggle("active", node.id === view));
  $("#view-title").textContent = titles[view][0];
  $("#view-subtitle").textContent = titles[view][1];
}

async function loadSummary() {
  state.summary = await api("/api/summary");
  renderSummary(state.summary);
}

async function loadInventory() {
  const data = await api("/api/inventory");
  state.inventory = data.items;
  renderInventory(data.items);
  populateBotFilter(data.items);
}

async function loadContainers() {
  const data = await api("/api/containers");
  state.containers = data.items;
  renderContainers(data.items, data.error);
}

function renderSummary(summary) {
  const fires = countEvent(summary.event_counts, "fire");
  const skips = countEvent(summary.event_counts, "skip");
  const crashes = countEvent(summary.event_counts, "bot_crashed");
  const opps = summary.funding_counts
    .filter((row) => row.event === "opportunity")
    .reduce((sum, row) => sum + Number(row.n || 0), 0);

  $("#last-sync").textContent = summary.last_sync?.ts || "not synced";
  $("#metrics").innerHTML = [
    metric("Fires", fires, "new-schema bot events"),
    metric("Skips", skips, "gates and rejected signals"),
    metric("Crashes", crashes, "logged bot_crashed rows"),
    metric("Funding Opps", opps, "paper monitor events"),
  ].join("");

  $("#bot-table").innerHTML = summary.bot_counts.map((bot) => `
    <tr>
      <td><strong>${escapeHtml(bot.bot)}</strong></td>
      <td>${fmtNumber(bot.fires)}</td>
      <td>${fmtNumber(bot.skips)}</td>
      <td>${fmtNumber(bot.crashes)}</td>
      <td class="mono">${fmtTime(bot.last_ts)}</td>
    </tr>
  `).join("");

  renderEventMix(summary.event_counts);
  renderRecentFires(summary.recent_fires);
  renderRecentOpps(summary.recent_opportunities);
  renderOps(summary);
}

function renderContainers(items, error) {
  const errorNode = $("#containers-error");
  if (error) {
    errorNode.hidden = false;
    errorNode.textContent = error;
  } else {
    errorNode.hidden = true;
    errorNode.textContent = "";
  }

  $("#containers-table").innerHTML = items.length ? items.map((container) => {
    const stateName = String(container.state || "unknown").toLowerCase();
    return `
      <tr>
        <td>
          <strong>${escapeHtml(container.name)}</strong><br>
          <span class="muted mono">${escapeHtml(container.service || container.image || container.id)}</span>
        </td>
        <td>
          <span class="badge status ${escapeHtml(stateName)}">${escapeHtml(container.state || "unknown")}</span>
          <div class="muted">${escapeHtml(container.status || "")}</div>
        </td>
        <td>
          <strong>${escapeHtml(container.strategy || "unknown")}</strong><br>
          <span class="muted">${escapeHtml(container.bot || "")}</span>
        </td>
        <td><div class="config-line">${escapeHtml(runtimeSummary(container))}</div></td>
        <td class="mono">${fmtTime(container.started_at)}</td>
        <td>
          ${fmtNumber(container.restart_count)}
          ${Number(container.exit_code || 0) ? `<div class="negative">exit ${fmtNumber(container.exit_code)}</div>` : ""}
          ${container.oom_killed ? `<div class="negative">OOM killed</div>` : ""}
        </td>
      </tr>
    `;
  }).join("") : `
    <tr>
      <td colspan="6" class="muted">No matching bot containers found.</td>
    </tr>
  `;
}

function renderInventory(items) {
  $("#inventory-table").innerHTML = items.map((bot) => {
    const skipReasons = bot.skip_reasons.length
      ? bot.skip_reasons.map((row) => `<span class="badge skip">${escapeHtml(row.reason)} ${fmtNumber(row.n)}</span>`).join(" ")
      : `<span class="muted">none</span>`;
    const latestFire = bot.latest_fire
      ? `${fmtTime(bot.latest_fire.ts)}<br><span class="muted">${escapeHtml(bot.latest_fire.outcome_name || "")} @ ${fmtNumber(bot.latest_fire.limit_price, 3)} · ${escapeHtml(bot.latest_fire.market_title || "")}</span>`
      : `<span class="muted">none indexed</span>`;
    const config = configSummary(bot.config);
    return `
      <tr>
        <td>
          <strong>${escapeHtml(bot.bot)}</strong><br>
          <span class="muted mono">${fmtTime(bot.first_ts)} -> ${fmtTime(bot.last_ts)}</span>
        </td>
        <td>
          <span class="badge fire">${fmtNumber(bot.fires)} fires</span>
          <span class="badge skip">${fmtNumber(bot.skips)} skips</span>
          <span class="badge ${Number(bot.crashes) ? "bot_crashed" : ""}">${fmtNumber(bot.crashes)} crashes</span>
          <div class="muted">${fmtNumber(bot.files)} files · ${fmtNumber(bot.total_events)} rows</div>
        </td>
        <td><div class="skip-list">${skipReasons}</div></td>
        <td class="mono">${latestFire}</td>
        <td><div class="config-line">${escapeHtml(config)}</div></td>
        <td>
          <div class="actions">
            <button class="secondary" data-filter-bot="${escapeHtml(bot.bot)}" data-filter-event="">Events</button>
            <button class="secondary" data-filter-bot="${escapeHtml(bot.bot)}" data-filter-event="fire">Fires</button>
            <button class="secondary" data-filter-bot="${escapeHtml(bot.bot)}" data-filter-event="skip">Skips</button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
  bindInventoryButtons();
}

function runtimeSummary(container) {
  const parts = [];
  const pairs = [
    ["asset", container.asset],
    ["timeframe", container.timeframe_min ? `${container.timeframe_min}m` : ""],
    ["variant", container.variant_suffix],
    ["threshold", container.threshold],
    ["sweet", container.sweet_lo || container.sweet_hi ? `${container.sweet_lo || "?"}-${container.sweet_hi || "?"}` : ""],
    ["confirm", container.require_confirm],
    ["dry_run", container.dry_run],
  ];
  for (const [key, value] of pairs) {
    if (value !== undefined && value !== null && value !== "") parts.push(`${key}=${value}`);
  }
  return parts.length ? parts.join(" · ") : "no runtime env config";
}

function configSummary(config) {
  if (!config) return "no boot config indexed";
  const params = config.params || config.config || config;
  const parts = [];
  for (const key of ["asset", "timeframe_min", "threshold", "threshold_pct", "sweet_lo", "sweet_hi", "dry_run", "require_confirm"]) {
    if (params[key] !== undefined) parts.push(`${key}=${params[key]}`);
  }
  if (parts.length) return parts.join(" · ");
  return JSON.stringify(config).slice(0, 220);
}

function populateBotFilter(items) {
  const select = $("#event-bot");
  const current = select.value;
  select.innerHTML = `<option value="">all bots</option>` + items.map((bot) => (
    `<option value="${escapeHtml(bot.bot)}">${escapeHtml(bot.bot)}</option>`
  )).join("");
  if (items.some((bot) => bot.bot === current)) {
    select.value = current;
  }
}

function bindInventoryButtons() {
  $all("[data-filter-bot]").forEach((button) => {
    button.addEventListener("click", async () => {
      $("#event-bot").value = button.dataset.filterBot;
      $("#event-type").value = button.dataset.filterEvent;
      $("#event-reason").value = "";
      setView("events");
      await loadEvents();
    });
  });
}

function metric(label, value, sub) {
  return `<div class="metric"><span>${label}</span><strong>${fmtNumber(value)}</strong><small>${sub}</small></div>`;
}

function countEvent(rows, event) {
  const row = rows.find((item) => item.event === event);
  return Number(row?.n || 0);
}

function renderEventMix(rows) {
  const max = Math.max(1, ...rows.map((row) => Number(row.n || 0)));
  $("#event-mix").innerHTML = rows.map((row) => {
    const width = (Number(row.n || 0) / max) * 100;
    return `
      <div class="bar-row">
        <span class="badge ${escapeHtml(row.event)}">${escapeHtml(row.event)}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
        <span class="mono">${fmtNumber(row.n)}</span>
      </div>
    `;
  }).join("");
}

function renderRecentFires(rows) {
  $("#recent-fires").innerHTML = rows.length ? rows.map((row) => `
    <div class="event-item">
      <div class="event-main">
        <strong>${escapeHtml(row.bot)} ${escapeHtml(row.outcome_name || "")} @ ${fmtNumber(row.limit_price, 3)}</strong>
        <span class="mono">${fmtTime(row.ts)}</span>
      </div>
      <div class="event-sub">${escapeHtml(row.market_title || "unknown market")} · ${row.dry_run ? "dry-run" : "live"} · order ${row.order_ok ? "ok" : "not ok"}</div>
    </div>
  `).join("") : `<div class="event-item muted">No fires indexed.</div>`;
}

function renderRecentOpps(rows) {
  $("#recent-opps").innerHTML = rows.length ? rows.map((row) => `
    <div class="event-item">
      <div class="event-main">
        <strong>${escapeHtml(row.asset)} ${escapeHtml(row.long_perp)} / ${escapeHtml(row.short_perp)}</strong>
        <span class="mono">${fmtTime(row.ts)}</span>
      </div>
      <div class="event-sub">spread ${fmtNumber(row.spread_bps_8h, 3)} bps · net ${fmtMoney(row.net_pnl_8h_usdc)} · APR ${fmtNumber(row.annualized_apr_pct_net, 2)}%</div>
    </div>
  `).join("") : `<div class="event-item muted">No opportunities indexed.</div>`;
}

function renderOps(summary) {
  $("#collector-facts").innerHTML = `
    <dt>Time</dt><dd class="mono">${escapeHtml(summary.time)}</dd>
    <dt>Database</dt><dd class="mono">${escapeHtml(summary.db_path)}</dd>
    <dt>Polymarket root</dt><dd class="mono">${escapeHtml(summary.roots.polymarket)}</dd>
    <dt>Hyperliquid root</dt><dd class="mono">${escapeHtml(summary.roots.hyperliquid)}</dd>
    <dt>Files tracked</dt><dd>${fmtNumber(summary.file_stats.files)} (${fmtSize(summary.file_stats.bytes)})</dd>
    <dt>Last sync</dt><dd class="mono">${escapeHtml(summary.last_sync?.ts || "not synced")}</dd>
    <dt>Last error</dt><dd>${escapeHtml(summary.last_sync?.error || "none")}</dd>
  `;
}

async function loadEvents() {
  const params = new URLSearchParams();
  const bot = $("#event-bot").value.trim();
  const event = $("#event-type").value;
  const reason = $("#event-reason").value.trim();
  if (bot) params.set("bot", bot);
  if (event) params.set("event", event);
  if (reason) params.set("reason", reason);
  params.set("limit", "200");
  const data = await api(`/api/events?${params}`);
  $("#events-table").innerHTML = data.items.map((row) => {
    const raw = JSON.parse(row.raw_json || "{}");
    return `
      <tr>
        <td class="mono">${fmtTime(row.ts)}</td>
        <td>${escapeHtml(row.bot)}</td>
        <td><span class="badge ${escapeHtml(row.event)}">${escapeHtml(row.event)}</span></td>
        <td>${escapeHtml(row.reason || "")}</td>
        <td>${escapeHtml(row.market_title || row.market_id || "")}</td>
        <td>${escapeHtml(row.side || row.outcome_name || "")}</td>
        <td>${fmtNumber(row.limit_price, 3)}</td>
        <td>${rawButton(raw)}</td>
      </tr>
    `;
  }).join("");
  bindRawButtons();
}

async function loadFunding() {
  const params = new URLSearchParams();
  const event = $("#funding-event").value;
  const asset = $("#funding-asset").value;
  if (event) params.set("event", event);
  if (asset) params.set("asset", asset);
  params.set("limit", "200");
  const data = await api(`/api/funding?${params}`);
  $("#funding-table").innerHTML = data.items.map((row) => {
    const raw = JSON.parse(row.raw_json || "{}");
    const pair = row.long_perp || row.short_perp ? `${row.long_perp || ""} / ${row.short_perp || ""}` : row.best_cex || "";
    const spread = row.spread_bps_8h ?? row.best_spread_bps_8h;
    return `
      <tr>
        <td class="mono">${fmtTime(row.ts)}</td>
        <td>${escapeHtml(row.asset)}</td>
        <td>${escapeHtml(pair)}</td>
        <td>${fmtNumber(spread, 3)} bps</td>
        <td class="${Number(row.net_pnl_8h_usdc) >= 0 ? "positive" : "negative"}">${fmtMoney(row.net_pnl_8h_usdc)}</td>
        <td>${fmtNumber(row.annualized_apr_pct_net, 2)}%</td>
        <td>${rawButton(raw)}</td>
      </tr>
    `;
  }).join("");
  bindRawButtons();
}

async function loadFiles() {
  const params = new URLSearchParams();
  const family = $("#file-family").value;
  const limit = $("#file-limit").value;
  if (family) params.set("family", family);
  if (limit) params.set("limit", limit);
  const data = await api(`/api/files?${params}`);
  $("#files-table").innerHTML = data.items.map((row) => `
    <tr>
      <td class="mono">${escapeHtml(row.path)}</td>
      <td>${escapeHtml(row.family)}</td>
      <td>${fmtSize(row.size)}</td>
      <td class="mono">${escapeHtml(row.mtime_utc)}</td>
      <td><button class="secondary" data-tail="${encodeURIComponent(row.path)}">Tail</button></td>
    </tr>
  `).join("");
  $all("[data-tail]").forEach((button) => {
    button.addEventListener("click", async () => {
      await loadTail(button.dataset.tail);
    });
  });
}

async function loadTail(encodedPath = state.tailPath) {
  if (!encodedPath) return;
  state.tailPath = encodedPath;
  const lines = $("#tail-lines").value;
  const data = await api(`/api/tail?path=${encodedPath}&lines=${encodeURIComponent(lines)}`);
  $("#tail-title").textContent = data.path.split("/").slice(-2).join("/");
  $("#tail-output").textContent = data.lines.join("\n");
}

async function runResync() {
  toast("Resync started");
  const result = await api("/api/resync", { method: "POST" });
  toast(`Resync complete: ${result.rows_changed} changed rows`);
  await loadSummary();
  if (state.activeView === "events") await loadEvents();
  if (state.activeView === "funding") await loadFunding();
  if (state.activeView === "files") await loadFiles();
}

function bindUI() {
  $all(".nav-button").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  $all("[data-refresh]").forEach((button) => button.addEventListener("click", loadSummary));
  $("#sync-now").addEventListener("click", runResync);
  $("#ops-resync").addEventListener("click", runResync);
  $("#load-containers").addEventListener("click", loadContainers);
  $("#load-inventory").addEventListener("click", loadInventory);
  $("#load-events").addEventListener("click", loadEvents);
  $("#load-funding").addEventListener("click", loadFunding);
  $("#load-files").addEventListener("click", loadFiles);
  $("#file-limit").addEventListener("change", loadFiles);
  $("#tail-lines").addEventListener("change", () => loadTail());
}

async function boot() {
  bindUI();
  await loadSummary();
  await loadContainers();
  await loadInventory();
  await loadEvents();
  await loadFunding();
  await loadFiles();
  setInterval(loadSummary, 30000);
  setInterval(loadContainers, 30000);
}

boot().catch((error) => {
  console.error(error);
  toast(error.message);
});
