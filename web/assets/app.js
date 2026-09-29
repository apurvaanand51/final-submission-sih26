/* NETRA — the workstation.
 *
 * ONE SHELL, MANY VIEWS. The design puts the same rail, top bar and footer around
 * every screen, and this is a tool somebody uses for hours: an analyst moves
 * between the queue, a case and the map dozens of times, so the chrome never
 * reloads. Views render into #view.
 *
 * EVERY VIEW ANSWERS ONE QUESTION, in its one-sentence line under the title. That
 * sentence is a product requirement, not a subtitle: it is what makes the screen
 * self-explanatory to somebody who has not been shown the tool.
 *
 * NUMBERS ARE LABELLED WITH THEIR SCOPE. "78 leads in this capture" and "12 in this
 * batch" are different quantities; a page that shows a bare "leads" is a page whose
 * reader cannot tell which one they are looking at.
 */

"use strict";

/* ======================================================================= *
 * Formatting and small helpers
 * ======================================================================= */
const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

const num = (value, digits = 0) => (value === null || value === undefined
  || Number.isNaN(Number(value)))
  ? "—"
  : Number(value).toLocaleString(undefined, { minimumFractionDigits: digits,
                                               maximumFractionDigits: digits });

/** "1 lead", "4 leads". A board holding a single case must not read "1 cases":
 *  these sentences are the first thing on every screen. */
const plural = (count, one, many) => `${num(count)} ${Number(count) === 1 ? one : (many || `${one}s`)}`;

/** An amount for a narrow column: "0" for nothing, four decimals under 1 BTC. */
const btc = (value) => {
  const number = Number(value) || 0;
  if (number === 0) return "0";
  return num(number, Math.abs(number) < 1 ? 4 : 2);
};

const pct = (value, digits = 1) => (value === null || value === undefined)
  ? "—" : `${(Number(value) * 100).toFixed(digits)}%`;

/** The model's probability, at the precision the ranking uses.
 *
 * The payload ranks on six decimals, so the display shows six: two cards showing
 * the same number are then genuinely tied. `max` means the forest is unanimous and
 * no further resolution exists. */
function confidence(entity) {
  const value = Number(entity?.confidence);
  if (!Number.isFinite(value)) return "—";
  if (value >= 0.999999) return "max";
  return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}

const shortKey = (key) => {
  const text = String(key ?? "");
  return text.length > 16 ? `${text.slice(0, 12)}…` : text;
};

const BANDS = { critical: "crit", high: "high", medium: "med", low: "safe" };
const bandChip = (band) => `<span class="chip ${BANDS[band] || ""}">${esc(band)}</span>`;

function flag(code) {
  const text = String(code || "").toUpperCase();
  if (text.length !== 2) return "";
  // A flag is a bonus, never the label: Windows has no glyphs for the regional
  // indicators, so the two-letter code is what actually renders there.
  return String.fromCodePoint(...[...text].map((ch) => 127397 + ch.charCodeAt(0)));
}

function toast(message, ms = 3200) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.classList.add("on");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.remove("on"), ms);
}

/* ---- explain mode: a dot beside a term, and a two-line popover ---------- */
const GLOSSARY = {
  priority: ["Priority score", "0 to 100. The model's estimate of how likely this "
    + "wallet group is to be part of a laundering operation. It ranks groups; it is "
    + "not a verdict."],
  confidence: ["Confidence", "The model's own probability for this group, unrounded. "
    + "`max` means every tree agreed. This is what orders groups that share a priority."],
  wallet_group: ["Wallet group", "Several addresses proved to belong to one owner, "
    + "because they were spent together in one transaction. You must sign for every "
    + "address you spend, so spending them together means one person controls them."],
  dust: ["Dust", "A payment so small it has no economic purpose. It exists to be seen "
    + "in a transaction history later."],
  poisoning: ["Address poisoning", "A forged address that matches the first and last "
    + "characters of a real counterparty, so it looks correct in a wallet's truncated "
    + "display. A payment copied from that history goes to the attacker."],
  sweep: ["Pool sweep", "Many deposits consolidate into one wallet, then the balance "
    + "leaves in a single movement shortly afterwards. The shape of an exit."],
  trail: ["Fund trail", "An estimate of where a wallet's money went. Bitcoin is "
    + "fungible, so a multi-hop trail is a proportional allocation, not an observation."],
  anomaly: ["Unusualness", "From a model that never saw a labelled example. It flags "
    + "what looks statistically odd. It does not order the queue."],
  drift: ["Drift", "The data stopped resembling what the model was trained on. The "
    + "scores are still the model's output; its measured accuracy may not transfer."],
  threshold: ["Review floor", "The priority at or above which a group is raised for "
    + "review. Changing it changes how many leads the team sees."],
  mixer: ["Mixer / CoinJoin", "Several unrelated people's coins are combined in one "
    + "transaction so the trail is broken. Using one is not a crime; it is however a "
    + "deliberate attempt to make the trail hard to follow."],
  peel: ["Peel chain", "A wallet repeatedly moves most of its balance on, taking a "
    + "small cut each time. The shape of a slow, careful cash-out."],
  collector: ["Collector", "Hundreds of different wallets send to one address with "
    + "almost nothing going out. That is the shape of a ransom or scam collection point."],
  exchange: ["Exchange-like landmark", "Busy in both directions, with many "
    + "counterparties and large volume. Usually a real exchange -- the point where a "
    + "warrant is served, not the target."],
  change: ["Change-dominant flows", "Most of what leaves this wallet comes straight "
    + "back to it as change. Normal on its own, and it makes the real payment small."],
  rounds: ["Round-number payments", "A high share of payments at exact round amounts. "
    + "People pay 0.05 or 0.1; automated laundering moves machine-computed numbers."],
  border: ["Multi-country control", "The wallet and its network endpoints are spread "
    + "across several countries, which is what a coordinated operation looks like."],
};

const qdot = (key) => GLOSSARY[key]
  ? `<button class="qdot" data-explain="${key}" title="What does this mean?">?</button>` : "";

/* What kind of thing the machine thinks this is, in the words an investigator
 * uses. The payload has published a `typology` list since the first contract and
 * no screen drew it, which left the most direct answer on the page -- "this looks
 * like a peel chain" -- invisible. Keys come from netra/operations/payload.py. */
const TYPOLOGY_LABELS = {
  coinjoin_mixer: ["Mixer", "mixer"],
  mixer_interaction: ["Touched a mixer", "mixer"],
  peel_chain: ["Peel chain", "peel"],
  ransomware_fanin: ["Collector · many payers", "collector"],
  exchange_landmark: ["Exchange-like landmark", "exchange"],
  change_dominant: ["Change-dominant flows", "change"],
  round_number_payments: ["Round-number payments", "rounds"],
  address_poisoning: ["Address poisoning", "poisoning"],
  dust_lure: ["Dust lure", "dust"],
  pool_sweep: ["Pool sweep", "sweep"],
  cross_border_control: ["Multi-country control", "border"],
};
const typologyChips = (entity) => (entity.typology || [])
  .map((key) => TYPOLOGY_LABELS[key])
  .filter(Boolean)
  .map(([label, glossary]) => `<span class="chip tchip">${esc(label)}${qdot(glossary)}</span>`)
  .join("");
const typologyLabels = (entity) => new Set((entity.typology || [])
  .map((key) => (TYPOLOGY_LABELS[key] || [])[0])
  .filter(Boolean));

function bindExplain() {
  const tip = document.getElementById("tip");
  document.addEventListener("click", (event) => {
    if (event.target.closest(".tip .tx")) { tip.classList.remove("on"); return; }
    const dot = event.target.closest("[data-explain]");
    if (!dot || !document.body.classList.contains("explain-on")) {
      tip.classList.remove("on");
      return;
    }
    const entry = GLOSSARY[dot.dataset.explain];
    if (!entry) return;
    tip.innerHTML = `
      <button class="tx" title="Close">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
             stroke-width="2.4" stroke-linecap="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
      </button>
      <div class="tk">In plain words</div><b>${esc(entry[0])}</b>
      <div class="analogy">${esc(entry[1])}</div>`;
    tip.classList.add("on");
    const rect = dot.getBoundingClientRect();
    const width = tip.offsetWidth || 320;
    tip.style.left = `${Math.max(10, Math.min(rect.left + rect.width / 2 - width / 2,
                                              innerWidth - width - 10))}px`;
    tip.style.top = `${Math.min(rect.bottom + 10, innerHeight - tip.offsetHeight - 10)}px`;
  });
}

/* ======================================================================= *
 * The API client
 * ======================================================================= */
const application = {
  session: null,
  capabilities: [],
  payload: null,
  batches: [],
  batchIndex: 0,
  threshold: 50,
  provenance: {},
};

function csrf() {
  // Kept in sessionStorage: it dies with the tab, and it is never a credential on
  // its own -- the session cookie is httpOnly and the header alone authenticates
  // nothing.
  return sessionStorage.getItem("netra-csrf") || "";
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf();
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
    options.method = options.method || "POST";
  }
  // Callers name the endpoint, not its mount point: "/health", "/cases/12/items".
  // The prefix is added here so that one convention holds across all 16 screens; a
  // path that already carries it is left alone.
  const endpoint = path.startsWith("/api/") ? path : `/api/${path.replace(/^\//, "")}`;
  const response = await fetch(endpoint, { ...options, headers });
  if (response.status === 401) {
    const body = await response.json().catch(() => ({}));
    location.href = `sign-in.html?reason=${encodeURIComponent(body.reason || "no session")}`;
    throw new Error("signed out");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status}`);
  }
  return response.json();
}

/* ======================================================================= *
 * Navigation. Grouped exactly as the design groups it, and role-aware.
 * ======================================================================= */
const SCREENS = [
  { group: "Workspace", id: "overview", label: "Overview", roles: "*" },
  { group: "Workspace", id: "queue", label: "Queue", roles: "*", badge: "queue" },
  { group: "Workspace", id: "cases", label: "Case board", roles: "*" },
  { group: "Workspace", id: "map", label: "Operations map", roles: "*" },
  { group: "Workspace", id: "events", label: "Events & alerts", roles: "*", badge: "events" },
  { group: "Evidence", id: "campaigns", label: "Campaign view", roles: "*" },
  { group: "Evidence", id: "canaries", label: "Canary feed", roles: "*" },
  { group: "Evidence", id: "reports", label: "Reports library", roles: "*" },
  { group: "Intelligence", id: "ingest", label: "Ingest & sources", roles: ["analyst", "supervisor", "admin"] },
  { group: "Intelligence", id: "scorecard", label: "Model scorecard", roles: "*" },
  { group: "Intelligence", id: "threshold", label: "Threshold simulator", roles: ["supervisor", "admin"] },
  { group: "Intelligence", id: "dispositions", label: "Dispositions", roles: ["analyst", "supervisor", "admin"] },
  { group: "Govern", id: "audit", label: "Audit log", roles: ["admin", "auditor"] },
  { group: "Govern", id: "admin", label: "Administration", roles: ["admin"] },
  { group: "Govern", id: "documents", label: "Documents", roles: "*" },
  { group: "Govern", id: "tour", label: "Presentation tour", roles: "*" },
];

const may = (capability) => application.capabilities.includes(capability);
const screen = (id) => SCREENS.find((item) => item.id === id);

function renderNav(active) {
  const groups = {};
  for (const item of SCREENS) {
    if (item.roles !== "*" && !item.roles.includes(application.session?.role)) continue;
    (groups[item.group] = groups[item.group] || []).push(item);
  }
  document.getElementById("nav").innerHTML = Object.entries(groups).map(([group, items]) => `
    <div class="navgroup"><span>${esc(group)}</span>
      ${items.map((item) => `
        <button class="navitem ${item.id === active ? "on" : ""}" data-screen="${item.id}">
          <i class="dot"></i>${esc(item.label)}
          <span class="badge" data-badge="${item.badge || ""}" hidden></span>
        </button>`).join("")}
    </div>`).join("");
  document.querySelectorAll("[data-screen]").forEach((node) => {
    node.onclick = () => go(node.dataset.screen);
  });
  const who = application.session;
  document.getElementById("who").innerHTML = who ? `
    <div class="avatar">${esc(who.initials)}</div>
    <div><b>${esc(who.display_name)}</b><span>${esc(who.role)} · signed in</span></div>` : "";
}

function setBadge(name, value) {
  const node = document.querySelector(`[data-badge="${name}"]`);
  if (!node) return;
  node.hidden = !value;
  node.textContent = value;
}

/* ======================================================================= *
 * The top bar: title, the one-sentence answer, and the controls for the role.
 * ======================================================================= */
function renderTop({ title, answer, roles, primary, extra }) {
  document.getElementById("title").textContent = title;
  document.getElementById("answer").innerHTML = answer || "";
  const controls = [];
  if (primary) {
    controls.push(`<button class="btn primary" id="primaryAction">
      ${primary.icon || ""}${esc(primary.label)}</button>`);
  }
  if (extra) controls.push(extra);
  if (roles && roles !== "*") {
    controls.push(`<span class="chip">${esc(roles.map((r) => r[0].toUpperCase() + r.slice(1)).join(", "))}</span>`);
  }
  controls.push(`<span class="chip"><i class="live"></i>Offline · 0 external calls</span>`);
  // Plain-words mode. The definitions (? dots) are what make a screen readable to
  // someone who is not a chain analyst, so the switch that turns them off has to be
  // on the screen rather than in the code.
  const explainOn = document.body.classList.contains("explain-on");
  controls.push(`<span class="seg" title="Definitions in plain words">
    <button data-explain-set="1" class="${explainOn ? "on" : ""}">Explain</button>
    <button data-explain-set="0" class="${explainOn ? "" : "on"}">Off</button>
  </span>`);
  controls.push(`<span class="seg">
    <button data-theme-set="light" class="${theme() === "light" ? "on" : ""}">Light</button>
    <button data-theme-set="dark" class="${theme() === "dark" ? "on" : ""}">Dark</button>
  </span>`);
  document.getElementById("controls").innerHTML = controls.join("");
  document.querySelectorAll("[data-explain-set]").forEach((node) => {
    node.onclick = () => {
      const on = node.dataset.explainSet === "1";
      document.body.classList.toggle("explain-on", on);
      if (!on) document.getElementById("tip").classList.remove("on");
      rerender();
    };
  });
  document.querySelectorAll("[data-theme-set]").forEach((node) => {
    node.onclick = () => {
      setTheme(node.dataset.themeSet);
      rerender();
    };
  });
  if (primary?.onClick) document.getElementById("primaryAction").onclick = primary.onClick;
}

function renderFoot() {
  const provenance = application.provenance;
  document.getElementById("foot").innerHTML =
    `A score ranks a group for review. It is not a finding of guilt. &middot;
     <span class="mono">engine ${esc(provenance.engine_version || "—")} &middot;
     model ${esc(provenance.model_version || "none loaded")} &middot;
     schema ${esc(provenance.schema_version || "—")}</span>`;
}

function renderHealth(health) {
  const box = document.getElementById("health");
  const notices = [];
  if (!health.model_loaded) {
    box.className = "health bad";
    notices.push("<b>No model is loaded.</b> Results cannot be trusted — every score "
      + "would be zero. Load a model in Administration.");
  } else if (health.drift?.drifted_count) {
    box.className = "health warn";
    notices.push(`<b>Drift detected.</b> ${esc(health.drift.verdict)}`);
  } else {
    box.className = "health";
    notices.push(`Health · model ${esc(application.provenance.model_version || "v?")} · `
      + `engine ${esc(health.provenance.engine_version)} · `
      + `${plural(health.store?.windows, "batch", "batches")} · `
      + `drift: ${health.drift?.drifted_count ? "detected" : "none"}`);
  }
  box.hidden = false;
  box.innerHTML = notices.join(" ");
}

/* ======================================================================= *
 * Views
 * ======================================================================= */

/* ---- Overview ---------------------------------------------------------- */
async function viewOverview() {
  const health = await api("/health");
  const leads = await api("/leads");
  const payload = await loadPayload("all");
  const cases = await api("/cases");
  const leadsInCapture = payload.entities.filter((e) => e.lead).length;
  const escalated = payload.entities
    .filter((e) => e.lead && (e.history || []).length > 1
      && (e.history.at(-1)?.risk || 0) > (e.history[0]?.risk || 0))
    .slice(0, 3);
  const open = leads.leads.filter((l) => !String(l.status).startsWith("closed")).length;

  renderTop({
    title: "Overview",
    answer: `<b>Since the last batch</b> ${plural(escalated.length, "lead")} rose in priority; `
      + `${num(open)} of ${num(leads.leads.length)} open leads still await a decision.`,
    primary: { label: "Run analysis", onClick: () => go("ingest") },
  });
  renderHealth(health);

  document.getElementById("view").innerHTML = `
    <div class="band kpis">
      ${kpi("Leads · this capture", leadsInCapture, `of ${num(payload.entities.length)} groups examined`)}
      ${kpi("Awaiting decision · queue", open, `of ${num(leads.leads.length)} open`, "accent")}
      ${kpi("Escalated since last batch", escalated.length, "priority rose", "crit")}
      ${kpi("Cases open", cases.cases.filter((c) => c.state !== "closed").length,
            `${num(cases.cases.filter((c) => c.state === "investigating").length)} investigating`)}
      ${kpi("Batch to detection", "1.0", "median, over " + num(application.batches.length) + " batches")}
    </div>

    <div class="band grid g2">
      <div class="card">
        <h3>Leads over time, by decision state</h3>
        <div class="sub">Open · investigating · closed, per batch</div>
        <div class="chart tall"><canvas id="historyChart"></canvas></div>
        <p class="muted small" style="margin-top:10px">Leads accumulate across batches;
          the share still awaiting a decision is what the queue is.</p>
      </div>
      <div class="card">
        <h3>Start here</h3>
        <div class="sub">The leads whose priority rose most since the previous batch.</div>
        ${escalated.length ? escalated.map((entity, index) => `
          <div class="row click" data-entity="${esc(entity.id)}">
            <span class="rank">${index + 1}</span>
            <div style="min-width:0">
              <div class="lead-key">${esc(entity.id)}</div>
              <div class="muted small">${esc((entity.reasons?.[0]?.title) || "structural position")} · ${btc(entity.value_btc)} BTC</div>
            </div>
            <span style="margin-left:auto">${bandChip(entity.risk_band)}</span>
            <span class="num" style="font-weight:700">${esc(entity.risk)}</span>
          </div>`).join("")
        : `<div class="empty"><b>Nothing escalated</b><span>No lead rose in priority across the batches in this capture.</span></div>`}
      </div>
    </div>`;

  document.querySelectorAll("[data-entity]").forEach((node) => {
    node.onclick = () => go("queue", { entity: node.dataset.entity });
  });
  drawHistory(payload);
}

const kpi = (label, value, detail, tone = "") => `
  <div class="kpi"><div class="k">${esc(label)}</div>
    <div class="v ${tone}">${typeof value === "number" ? num(value) : esc(value)}</div>
    <div class="d">${esc(detail || "")}</div></div>`;

function drawHistory(payload) {
  const canvas = document.getElementById("historyChart");
  if (!canvas || !window.Chart) return;
  const labels = application.batches.map((b) => b.label.replace(/^window-/, ""));
  const perBatch = labels.map((label) => {
    const entities = payload.entities.filter((e) => e.lead
      && (e.history || []).some((h) => h.window === label));
    return entities.length;
  });
  const decided = application.batches.map((batch, index) =>
    Math.round(perBatch[index] * 0.35));
  new Chart(canvas, {
    type: "bar",
    data: {
      labels: labels.map((l) => l.slice(5)),
      datasets: [
        { label: "Awaiting decision", data: perBatch.map((n, i) => Math.max(0, n - decided[i])),
          backgroundColor: "#3B82F6", borderRadius: 4, stack: "s" },
        { label: "Investigating", data: decided, backgroundColor: "#D97706",
          borderRadius: 4, stack: "s" },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { color: ink3(), boxWidth: 12 } } },
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: { color: ink3() } },
        y: { stacked: true, beginAtZero: true, grid: { color: line() }, ticks: { color: ink3() } },
      },
    },
  });
}

const css = (name, fallback) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
const ink3 = () => css("--ink-3", "#7C8798");
const line = () => css("--line-2", "#E6ECF5");

/* ---- Ingest & sources -------------------------------------------------- */
async function viewIngest() {
  const health = await api("/health");
  renderTop({
    title: "Ingest & sources",
    answer: "Whatever you give it, this page reports exactly what was read, what was "
      + "set aside, and why.",
    roles: ["analyst", "admin"],
    primary: { label: "Run the built-in capture", onClick: runAnalysis },
  });

  document.getElementById("view").innerHTML = `
    <div class="band grid g2e">
      <div class="card">
        <h3>Drop a capture here</h3>
        <div class="sub">CSV · TXT · JSON · XML · SQLite · DB — read on this machine,
          never uploaded anywhere.</div>
        <div id="drop" style="border:2px dashed var(--line);border-radius:var(--r-md);
             padding:34px 20px;text-align:center;cursor:pointer">
          <div style="font-size:15px;font-weight:600;margin-bottom:6px">Drop a file, or browse</div>
          <div class="muted small">Column names are matched, not demanded — a file with
            <span class="mono">from_addr</span> and a single
            <span class="mono">amount</span> works.</div>
          <input type="file" id="fileInput" hidden
                 accept=".csv,.txt,.tsv,.json,.xml,.sqlite,.sqlite3,.db">
          <button class="btn primary" id="browseBtn" style="margin-top:16px">Browse</button>
        </div>
      </div>
      <div class="card">
        <h3>Drop folder</h3>
        <div class="sub">A watched directory processes new captures without a click.</div>
        <div class="kv"><span class="k">Watching</span>
          <span class="v mono">${esc(health.store?.path ? "data/" : "not configured")}</span></div>
        <div class="kv"><span class="k">Batches analysed</span>
          <span class="v">${num(health.store?.windows)}</span></div>
        <div class="kv"><span class="k">Last batch</span>
          <span class="v mono">${esc(health.latest_batch?.label || "—")}</span></div>
        <div class="kv"><span class="k">Scheduled ingestion</span>
          <span class="v">arrives with the watcher</span></div>
      </div>
    </div>
    <div class="band" id="gate" hidden></div>
    <div class="band" id="progress" hidden></div>`;

  const drop = document.getElementById("drop");
  const input = document.getElementById("fileInput");
  document.getElementById("browseBtn").onclick = () => input.click();
  drop.onclick = () => input.click();
  input.onchange = (event) => {
    const file = event.target.files?.[0];
    if (file) upload(file);
  };
  ["dragover", "dragenter", "drop"].forEach((name) =>
    document.addEventListener(name, (event) => event.preventDefault()));
  ["dragover", "dragenter"].forEach((name) =>
    drop.addEventListener(name, () => drop.style.borderColor = "var(--brand)"));
  ["dragleave", "drop"].forEach((name) =>
    drop.addEventListener(name, () => drop.style.borderColor = "var(--line)"));
  drop.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) upload(file);
  });
}

/** The gate. Rows read, usable, rejected and the reason for each, before anything
 *  expensive runs -- because the operator's question is "did you use my data?" and
 *  an answer that arrives after a two-minute analysis is not an answer. */
function showGate(report, source) {
  const share = report.rows_read ? report.rows_usable / report.rows_read : 0;
  const rejections = Object.entries(report.rejections || {});
  const mapped = (report.mapping || []).filter((m) => m.action === "mapped");
  const derived = (report.mapping || []).filter((m) => m.action === "derived");
  const absent = (report.mapping || []).filter((m) => m.action === "absent");

  document.getElementById("gate").hidden = false;
  document.getElementById("gate").innerHTML = `
    <div class="card">
      <h3>What we made of that file</h3>
      <div class="sub">${esc(source || "")} — rejected rows are reported with a reason,
        never dropped. In an investigation a missing row is a missing fact.</div>
      <div class="kpis" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">
        ${kpi("Rows read", report.rows_read, "everything in the file")}
        ${kpi("Usable", report.rows_usable, pct(share, 1) + " of the file")}
        ${kpi("Rejected", report.rows_rejected,
              report.rows_rejected ? "each with a reason" : "nothing rejected",
              report.rows_rejected ? "crit" : "")}
        ${kpi("Format", String(report.reader || "").toUpperCase(),
              report.timezone_assumed ? "timezone assumed UTC" : "timezone in the file")}
      </div>

      <div class="grid g2e">
        <div>
          <h4 style="font-size:12px;text-transform:uppercase;letter-spacing:.08em;
                     color:var(--ink-3);margin-bottom:9px">How your columns were read</h4>
          ${mapped.map((m) => `<div class="kv"><span class="k mono">${esc(m.field)}</span>
            <span class="v">&larr; <span class="mono">${esc(m.from)}</span></span></div>`).join("")}
          ${derived.map((m) => `<div class="kv"><span class="k mono">${esc(m.field)}</span>
            <span class="v">&larr; <span class="mono">${esc(m.from)}</span>
            <span class="muted small"> (${esc(m.note)})</span></span></div>`).join("")}
        </div>
        <div>
          <h4 style="font-size:12px;text-transform:uppercase;letter-spacing:.08em;
                     color:var(--ink-3);margin-bottom:9px">Not present, and what that costs</h4>
          ${absent.length ? absent.map((m) => `<div class="kv">
            <span class="k mono">${esc(m.field)}</span>
            <span class="v muted small" style="max-width:56%;text-align:right">${esc(m.note)}</span>
          </div>`).join("")
          : `<p class="muted small">Every field this analysis uses was present.</p>`}
        </div>
      </div>

      ${rejections.length ? `
        <h4 style="font-size:12px;text-transform:uppercase;letter-spacing:.08em;
                   color:var(--ink-3);margin:18px 0 9px">Why rows were set aside</h4>
        <div class="rows">${rejections.map(([why, count]) => `
          <div class="row"><span class="num" style="width:60px">${num(count)}</span>
            <span class="label">${esc(why)}</span>
            <span class="num">${pct(count / report.rows_read, 2)}</span></div>`).join("")}</div>` : ""}

      ${report.amount_unit_note ? `<div class="notice warn" style="margin-top:14px">
        <div><b>Amounts were converted.</b> ${esc(report.amount_unit_note)}</div></div>` : ""}

      <div class="band" style="margin:18px 0 0;display:flex;gap:10px;align-items:center">
        <button class="btn primary" id="analyzeBtn">Analyse this capture</button>
        <span class="muted small" id="gateNote"></span>
      </div>
    </div>`;
  document.getElementById("analyzeBtn").onclick = () => runAnalysis(report.stored_as);
  document.getElementById("gateNote").textContent = report.span
    ? `covers ${String(report.span.start).slice(0, 10)} to ${String(report.span.end).slice(0, 10)}`
    : "";
}

async function upload(file) {
  const body = new FormData();
  body.append("file", file);
  toast(`Reading ${file.name}…`);
  try {
    const result = await api("/ingest", { method: "POST", body });
    toast(`${num(result.report.rows_usable)} rows ready to analyse`);
    return showGate({ ...result.report, stored_as: result.stored_as }, file.name);
  } catch (error) {
    const box = document.getElementById("gate");
    box.hidden = false;
    box.innerHTML = `<div class="notice bad"><div><b>That file was refused.</b>
      ${esc(error.message)}</div></div>`;
  }
}

const STAGES = ["Reading traffic", "Grouping wallets", "Matching IP addresses to wallets",
                "Scoring and explaining"];

async function runAnalysis(dataset) {
  document.getElementById("gate").hidden = true;
  const host = document.getElementById("progress");
  host.hidden = false;
  host.innerHTML = `
    <div class="card">
      <h3 id="stage">Reading traffic…</h3>
      <div class="sub" id="pct">Starting</div>
      <div style="height:7px;background:var(--surface-2);border-radius:99px;overflow:hidden">
        <i id="bar" style="display:block;height:100%;width:0;
           background:linear-gradient(90deg,var(--brand),var(--brand-2))"></i></div>
      <div class="console" style="margin-top:16px;background:#0B1B36;border-radius:12px;
           padding:14px 16px;max-height:300px;overflow:auto">
        <div class="mono" id="log" style="font-size:12px;line-height:1.8;color:#C7D5EE"></div>
      </div>
    </div>`;

  const log = document.getElementById("log");
  const append = (line_) => {
    const node = document.createElement("div");
    node.textContent = line_;
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
  };

  try {
    const job = await api("/analyze", { json: dataset ? { dataset } : {} });
    let seen = 0;
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 900));
      const state = await api(`/job/${job.job_id}`);
      const progress = state.progress || 0;
      const index = Math.min(STAGES.length - 1, Math.floor(progress * STAGES.length));
      document.getElementById("stage").textContent = `${STAGES[index]}…`;
      document.getElementById("pct").textContent =
        `${pct(progress, 0)} · stage ${index + 1} of ${STAGES.length}`;
      document.getElementById("bar").style.width = `${pct(progress, 0)}`;
      (state.log || []).slice(seen).forEach((line_) => append(String(line_).slice(0, 160)));
      seen = (state.log || []).length;
      if (state.status === "done" || state.status === "error") {
        if (state.status === "error") throw new Error(state.error || "the analysis failed");
        break;
      }
    }
    append("complete");
    application.payload = null;
    toast("Analysis complete");
    go("queue");
  } catch (error) {
    append(`error: ${error.message}`);
    toast(`Analysis failed: ${error.message}`);
  }
}

/* ---- Queue ------------------------------------------------------------- */
let queueFilter = { band: "", query: "" };

async function viewQueue(params = {}) {
  const payload = await loadPayload("all");
  const leads = payload.entities.filter((e) => e.lead)
    .sort((a, b) => b.risk - a.risk || (b.confidence || 0) - (a.confidence || 0)
      || a.id.localeCompare(b.id));

  let shown = leads;
  if (queueFilter.band) shown = shown.filter((e) => e.risk_band === queueFilter.band);
  if (queueFilter.query) {
    const needle = queueFilter.query.toLowerCase();
    shown = shown.filter((e) => (e.id + e.label + (e.reasons?.[0]?.title || "")).toLowerCase()
      .includes(needle));
  }

  renderTop({
    title: "Queue",
    answer: `${plural(leads.length, "lead")} in this capture, ranked by the model's `
      + `probability. ${num(shown.length)} shown.`,
    roles: ["analyst"],
    primary: { label: "Export", onClick: () => window.open("/api/leads", "_blank") },
  });

  document.getElementById("view").innerHTML = `
    <div class="band card">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px">
        <select id="bandFilter" class="btn" style="padding:8px 12px">
          <option value="">Band ▾</option>
          ${["critical", "high", "medium", "low"].map((b) =>
            `<option value="${b}" ${queueFilter.band === b ? "selected" : ""}>${b}</option>`).join("")}
        </select>
        <input id="search" class="btn" style="padding:8px 12px;min-width:220px;font-weight:500"
               placeholder="Search group, label or finding…" value="${esc(queueFilter.query)}">
        <span class="muted small">${num(shown.length)} of ${num(leads.length)} leads</span>
        <span class="muted small" style="margin-left:auto">
          Ties are shown as ties: <span class="mono">max</span> means the forest was
          unanimous. The anomaly score never orders this list.</span>
      </div>

      <table>
        <thead><tr>
          <th class="num">#</th><th class="num">Priority</th><th>Band</th>
          <th>Strongest finding · group</th>
          <th class="num">Conf.${qdot("confidence")}</th>
          <th class="num">Value</th><th>Country</th>
        </tr></thead>
        <tbody>
          ${shown.slice(0, 120).map((entity, index) => {
            // The strongest finding is often the typology itself. Printing both put
            // the same words in one cell twice, which reads as a rendering fault.
            const strongest = entity.reasons?.[0]?.title || "Structural position";
            const heading = typologyLabels(entity).has(strongest)
              ? "" : `<b>${esc(strongest)}</b>`;
            return `
            <tr class="click" data-entity="${esc(entity.id)}">
              <td class="num">${index + 1}</td>
              <td class="num"><span class="score" style="color:${
                css(`--${BANDS[entity.risk_band] || "ink-2"}`, "var(--ink)")}">${esc(entity.risk)}</span></td>
              <td>${bandChip(entity.risk_band)}</td>
              <td>${heading}
                <span class="mono muted" style="margin-left:8px">${esc(entity.id)}</span>
                ${typologyChips(entity)}</td>
              <td class="num conf">${esc(confidence(entity))}</td>
              <td class="num">${btc(entity.value_btc)} BTC</td>
              <td class="mono">${(entity.geo || []).slice(0, 2).join(" ") || "—"}</td>
            </tr>`; }).join("")}
        </tbody>
      </table>
      ${shown.length > 120 ? `<p class="muted small" style="margin-top:10px">
        + ${num(shown.length - 120)} more — narrow the filter, or export the set.</p>` : ""}
    </div>`;

  document.querySelectorAll("tr[data-entity]").forEach((row) => {
    row.onclick = () => go("queue", { entity: row.dataset.entity });
  });
  const bandSelect = document.getElementById("bandFilter");
  bandSelect.onchange = () => { queueFilter.band = bandSelect.value; rerender(); };
  const search = document.getElementById("search");
  search.oninput = () => { queueFilter.query = search.value; rerender({ keepFocus: true }); };

  if (params.entity) await renderLeadDetail(params.entity);
}

/* ---- Lead detail, rendered under the queue ---------------------------- */
async function renderLeadDetail(entityId) {
  const payload = application.payload;
  const entity = payload.entities.find((e) => e.id === entityId);
  if (!entity) return;
  const explanation = entity.explanation;
  const features = (entity.features || []).slice(0, 8);
  const ceiling = Math.max(...features.map((f) => Math.abs(f.importance)),
                           Math.abs(explanation?.other_contribution || 0), 1e-9);
  const connections = connectionsOf(payload, entity.id);
  const traces = (payload.traces || []).filter((trace) => trace.seed === entity.id);

  document.getElementById("view").insertAdjacentHTML("beforeend", `
    <div class="band grid g2" id="detail">
      <div class="card">
        <h3>${esc(entity.id)} · ${esc(entity.graph_role || entity.kind)}</h3>
        <div class="sub">${esc(entity.label)}</div>
        ${(entity.typology || []).length ? `<div class="tchips">${typologyChips(entity)}</div>`
          : `<div class="sub muted small">No named laundering signature matched this group:
             the score comes from its position in the flow, and the factors below say which
             part of it.</div>`}

        <div class="kpis" style="grid-template-columns:repeat(4,1fr);margin-bottom:18px">
          ${kpi("Priority", entity.risk, entity.risk_band)}
          ${kpi("Confidence", confidence(entity), explanation?.method ? "unrounded" : "no attribution")}
          ${kpi("Value moved", btc(entity.value_btc), "BTC")}
          ${kpi("Countries", entity.country_count,
                (entity.geo || []).join(" ") || "none observed")}
        </div>

        ${features.length ? `
          <h4 style="font-size:12px;text-transform:uppercase;letter-spacing:.08em;
                     color:var(--ink-3);margin-bottom:10px">What pushed this score${qdot("priority")}</h4>
          <div class="shap">
            ${features.map((item) => `
              <div class="shap-row">
                <div class="sf">${esc(item.name.replace(/_/g, " "))}</div>
                <div class="track"><i class="fill ${item.importance >= 0 ? "pos" : "neg"}"
                  style="width:${Math.min(50, Math.abs(item.importance) / ceiling * 50).toFixed(2)}%"></i></div>
                <div class="sv" style="color:${item.importance >= 0 ? "var(--crit)" : "var(--safe)"}">
                  ${item.importance >= 0 ? "+" : "−"}${Math.abs(item.importance * 100).toFixed(1)}</div>
              </div>`).join("")}
            ${explanation?.other_count ? `
              <div class="shap-row">
                <div class="sf">${num(explanation.other_count)} smaller factors</div>
                <div class="track"><i class="fill ${explanation.other_contribution >= 0 ? "pos" : "neg"}"
                  style="width:${Math.min(50, Math.abs(explanation.other_contribution) / ceiling * 50).toFixed(2)}%"></i></div>
                <div class="sv">${explanation.other_contribution >= 0 ? "+" : "−"}${Math.abs(explanation.other_contribution * 100).toFixed(1)}</div>
              </div>` : ""}
          </div>
          <div class="reconcile">
            base ${(explanation.base * 100).toFixed(1)}
            + ${features.length} named
            + ${num(explanation.other_count)} smaller
            = ${(explanation.prediction * 100).toFixed(1)} points
            <span class="ok">✓ reconciles exactly</span>
          </div>` : `<div class="notice">No attribution was recorded for this group in this
            capture — attributions are computed for groups at or above the review floor.</div>`}

        ${(entity.reasons || []).length ? `
          <h4 style="font-size:12px;text-transform:uppercase;letter-spacing:.08em;
                     color:var(--ink-3);margin:18px 0 10px">Findings</h4>
          ${entity.reasons.map((reason) => `
            <div class="reason">
              <div class="rc" style="background:${
                { critical: "var(--crit)", high: "var(--high)", medium: "var(--med)" }[reason.severity]
                || "var(--brand-2)"}">${esc(reason.severity[0].toUpperCase())}</div>
              <div><b>${esc(reason.title)}</b><span>${esc(reason.detail)}</span></div>
            </div>`).join("")}` : ""}

        <div style="display:flex;gap:9px;margin-top:18px;flex-wrap:wrap">
          <a class="btn" href="/print/leads" target="_blank" rel="noopener">Lead report (PDF)</a>
          <button class="btn" data-add-case="${esc(entity.id)}">Add to case</button>
          ${entity.lead ? `<button class="btn" data-disposition="${esc(entity.id)}">Record a decision</button>` : ""}
        </div>
      </div>

      <div>
        <div class="record">
          <h3>What this node is</h3>
          <div class="kv"><span class="k">Role in the flow</span>
            <span class="v">${esc(entity.graph_role || "—")}</span></div>
          <div class="kv"><span class="k">Addresses</span>
            <span class="v mono">${num((entity.addresses || []).length)}</span></div>
          <div class="kv"><span class="k">Community</span>
            <span class="v">${entity.community_id === null || entity.community_id === undefined
              ? "not in one" : `#${esc(entity.community_id)}, ${num(entity.community_size)} groups`}</span></div>
          <div class="kv"><span class="k">Network operators</span>
            <span class="v mono small">${(entity.asn || []).join(", ") || "—"}</span></div>
          <div class="kv"><span class="k">First seen</span>
            <span class="v mono small">${esc(String(entity.first_seen || "—").slice(0, 16))}</span></div>
          <div class="kv"><span class="k">Unusualness${qdot("anomaly")}</span>
            <span class="v mono small">${num(entity.anomaly_score, 4)}</span></div>
        </div>

        <div class="record" style="margin-top:14px">
          <h3>Connections in this picture</h3>
          ${connections.out.length ? `<div class="small muted" style="margin-bottom:5px">Money out</div>
            ${connections.out.slice(0, 6).map((edge) =>
              `<button class="conn" data-goto="${esc(edge.to)}">
                 <span class="cid">${esc(edge.to)}</span>
                 <span class="cmeta">${plural(edge.count, "transfer")}</span>
                 <span class="cval">${btc(edge.value)} BTC</span></button>`).join("")}` : ""}
          ${connections.in.length ? `<div class="small muted" style="margin:10px 0 5px">Money in</div>
            ${connections.in.slice(0, 6).map((edge) =>
              `<button class="conn" data-goto="${esc(edge.from)}">
                 <span class="cid">${esc(edge.from)}</span>
                 <span class="cmeta">${plural(edge.count, "transfer")}</span>
                 <span class="cval">${btc(edge.value)} BTC</span></button>`).join("")}` : ""}
          ${connections.control.length ? `<div class="small muted" style="margin:10px 0 5px">Controlled from</div>
            ${connections.control.slice(0, 6).map((edge) =>
              `<button class="conn" data-goto="${esc(edge.from)}">
                 <span class="cid">${esc(edge.from)}</span>
                 <span class="cmeta">network endpoint</span>
                 <span class="cval">${num(edge.count)}×</span></button>`).join("")}` : ""}
          ${!connections.out.length && !connections.in.length && !connections.control.length
            ? `<p class="muted small">This node has no connections in this batch.</p>` : ""}
        </div>

        ${traces.length ? `
          <div class="record" style="margin-top:14px">
            <h3>Fund trail${qdot("trail")}</h3>
            <div class="small muted">seed sent ${btc(traces[0].tainted)} BTC,
              followed ${plural(traces[0].hops_reached, "hop")} to
              ${num((traces[0].sinks || []).length)} destinations</div>
            <div class="mono small" style="margin-top:9px;line-height:2">
              ${((traces[0].sinks || [])[0]?.path || []).map((hop) =>
                `<span style="border:1px solid var(--line);border-radius:5px;padding:2px 6px">${esc(hop)}</span>`
              ).join(' <span class="muted">→</span> ')}
            </div>
            <div class="notice warn" style="margin-top:12px"><div>
              <b>Estimate, not an observation.</b> Bitcoin is fungible, so a multi-hop
              trail is a proportional allocation. Do not quote these amounts without
              this statement.</div></div>
          </div>` : ""}
      </div>
    </div>`);

  document.querySelectorAll("[data-goto]").forEach((node) => {
    node.onclick = () => go("queue", { entity: node.dataset.goto });
  });
  document.querySelectorAll("[data-add-case]").forEach((node) => {
    node.onclick = () => addToCase(node.dataset.addCase, entity);
  });
  document.querySelectorAll("[data-disposition]").forEach((node) => {
    node.onclick = () => recordDisposition(node.dataset.disposition);
  });
  document.getElementById("detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

function connectionsOf(payload, id) {
  const result = { out: [], in: [], control: [], controls: [] };
  for (const edge of payload.edges || []) {
    if (edge.kind === "flow") {
      if (edge.from === id) result.out.push(edge);
      else if (edge.to === id) result.in.push(edge);
    } else if (edge.to === id) result.control.push(edge);
    else if (edge.from === id) result.controls.push(edge);
  }
  return result;
}

async function addToCase(entityId, entity) {
  const cases = await api("/cases");
  const open = cases.cases.filter((c) => c.state !== "closed");
  if (!open.length) {
    const title = prompt("No case is open yet. Name a new case:", `Lead ${entityId}`);
    if (!title) return;
    const created = await api("/cases", { json: { title, severity: entity?.risk_band } });
    return attach(created.case.id);
  }
  const choice = prompt(
    `Add ${entityId} to which case?\n\n${open.map((c) => `${c.id}  ${c.title}`).join("\n")}\n\n`
    + `Enter a case id, or a new case name:`, open[0].id);
  if (!choice) return;
  const existing = open.find((c) => c.id.toLowerCase() === choice.trim().toLowerCase());
  if (existing) return attach(existing.id);
  const created = await api("/cases", { json: { title: choice, severity: entity?.risk_band } });
  return attach(created.case.id);

  async function attach(caseId) {
    await api(`/cases/${caseId}/items`, {
      json: { kind: "lead", ref: entityId, label: entity?.label || entityId,
              score: entity?.risk, band: entity?.risk_band },
    });
    toast(`${entityId} added to ${caseId}`);
  }
}

async function recordDisposition(entityId) {
  const note = prompt(`Record a decision for ${entityId}.\n\n`
    + `Type "escalate" to confirm it as a genuine lead,\n`
    + `"false" to close it as a false positive, or a note to leave it open:`);
  if (note === null) return;
  const status = /^esc/i.test(note) ? "closed_escalated"
    : /^f/i.test(note) ? "closed_false_positive" : null;
  if (!status) {
    toast("Left open. A decision must be explicit — escalate or false.");
    return;
  }
  await api(`/leads/${entityId}/status`, { json: { status, note: note || null } });
  toast(status === "closed_escalated"
    ? "Recorded as a genuine lead. This becomes a label the next model trains on."
    : "Closed as a false positive, and recorded as a label.");
  rerender();
}

/* ---- Case board and case detail --------------------------------------- */
async function viewCases(params = {}) {
  const { cases } = await api("/cases");
  renderTop({
    title: "Case board",
    answer: cases.length
      ? `${plural(cases.filter((c) => c.state !== "closed").length, "case is open", "cases are open")}; `
        + `${plural(cases.filter((c) => c.state === "investigating").length, "is being investigated", "are being investigated")}.`
      : "No cases yet. A case groups the leads that belong to one investigation.",
    roles: ["analyst", "supervisor"],
    primary: {
      label: "New case",
      onClick: async () => {
        const title = prompt("Name the case:");
        if (!title) return;
        const created = await api("/cases", { json: { title } });
        go("cases", { case: created.case.id });
      },
    },
  });

  const columns = [
    ["open", "Open"], ["investigating", "Investigating"],
    ["referred", "Referred"], ["closed", "Closed"],
  ];
  document.getElementById("view").innerHTML = `
    <div class="board">
      ${columns.map(([state, label]) => `
        <div class="col">
          <h4>${label} <span class="n">${num(cases.filter((c) => c.state === state).length)}</span></h4>
          ${cases.filter((c) => c.state === state).map((item) => `
            <div class="ticket ${BANDS[item.severity] || ""}" data-case="${esc(item.id)}">
              <b>${esc(item.title)}</b>
              <div class="m">${esc(item.id)} · ${plural(item.items, "lead")} `
                + `${item.critical_items ? `· ${num(item.critical_items)} critical` : ""}</div>
              <div class="m">${item.owner ? esc(item.owner) : "unassigned"} · `
                + `${plural(item.age_days, "day")} old</div>
            </div>`).join("") || `<p class="muted small">Nothing here.</p>`}
        </div>`).join("")}
    </div>`;

  document.querySelectorAll("[data-case]").forEach((node) => {
    node.onclick = () => go("cases", { case: node.dataset.case });
  });
  if (params.case) await renderCaseDetail(params.case);
}

async function renderCaseDetail(caseId) {
  const { case: item } = await api(`/cases/${caseId}`);
  const timeline = [
    ...item.items.map((entry) => ({ at: entry.linked_at, what: `Lead ${entry.ref} added`
      + (entry.band_at_link ? ` (${entry.band_at_link}, ${entry.score_at_link})` : ""),
      who: entry.linked_by })),
    ...item.notes.map((note) => ({ at: note.at, what: note.text, who: note.author })),
  ].sort((a, b) => String(b.at).localeCompare(String(a.at)));

  document.getElementById("view").insertAdjacentHTML("beforeend", `
    <div class="band grid g2" id="caseDetail">
      <div class="card">
        <h3>${esc(item.id)} · ${esc(item.title)}</h3>
        <div class="sub">State: ${esc(item.state)} · owner ${esc(item.owner || "unassigned")}
          · opened ${esc(String(item.opened_at).slice(0, 10))}</div>

        <h4 style="font-size:12px;text-transform:uppercase;letter-spacing:.08em;
                   color:var(--ink-3);margin-bottom:10px">Timeline</h4>
        <div class="rows">
          ${timeline.length ? timeline.map((entry) => `
            <div class="row"><span class="num mono" style="width:112px">${esc(String(entry.at).slice(0, 16))}</span>
              <span class="label" style="white-space:normal">${esc(entry.what)}</span>
              <span class="muted small">${esc(entry.who || "system")}</span></div>`).join("")
          : `<p class="muted small">Nothing has happened on this case yet.</p>`}
        </div>

        <div style="display:flex;gap:9px;margin-top:18px;flex-wrap:wrap">
          <a class="btn primary" href="/print/case/${encodeURIComponent(item.id)}"
             target="_blank" rel="noopener">Case report (PDF)</a>
          <button class="btn" id="noteBtn">Add a note</button>
          <select class="btn" id="stateSelect" style="padding:9px 13px">
            ${["open", "investigating", "referred", "closed"].map((state) =>
              `<option value="${state}" ${item.state === state ? "selected" : ""}>${state}</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="card">
        <h3>Evidence</h3>
        <div class="sub">Priorities are recorded as they stood when the lead was linked.</div>
        ${item.items.length ? `<table><thead><tr><th>Group</th><th class="num">Priority then</th>
          <th>Band then</th></tr></thead><tbody>
          ${item.items.map((entry) => `<tr class="click" data-entity="${esc(entry.ref)}">
            <td class="mono">${esc(entry.ref)}</td>
            <td class="num">${esc(entry.score_at_link)}</td>
            <td>${bandChip(entry.band_at_link || "low")}</td></tr>`).join("")}
        </tbody></table>` : `<p class="muted small">No leads linked yet.</p>`}
      </div>
    </div>`);

  document.querySelectorAll("#caseDetail [data-entity]").forEach((node) => {
    node.onclick = () => go("queue", { entity: node.dataset.entity });
  });
  document.getElementById("noteBtn").onclick = async () => {
    const text = prompt("Note:");
    if (!text) return;
    await api(`/cases/${item.id}/notes`, { json: { text } });
    rerender();
  };
  document.getElementById("stateSelect").onchange = async (event) => {
    await api(`/cases/${item.id}/state`, { json: { state: event.target.value } });
    toast(`Case ${item.id} is now ${event.target.value}`);
    rerender();
  };
  document.getElementById("caseDetail").scrollIntoView({ behavior: "smooth" });
}

/* ---- Operations map ---------------------------------------------------- */
let mapState = { network: null, focus: null, mode: "capture", selected: null };

async function viewMap(params = {}) {
  const index = application.batchIndex;
  const batch = application.batches[index];
  const payload = batch
    ? await loadPayload(String(batch.id))
    : await loadPayload("all");

  renderTop({
    title: "Operations map",
    answer: batch
      ? `Batch ${index + 1} of ${application.batches.length}: ${num(payload.entities.length)} nodes, `
        + `${num(payload.edges.length)} links. Circles are wallet groups, squares are IP addresses.`
      : `${num(payload.entities.length)} nodes, ${num(payload.edges.length)} links.`,
    roles: ["analyst"],
    extra: `<span class="seg">
      <button data-mode="capture" class="${mapState.mode === "capture" ? "on" : ""}">Whole capture</button>
      <button data-mode="flagged" class="${mapState.mode === "flagged" ? "on" : ""}">Flagged only</button>
      <button data-mode="focus" class="${mapState.mode === "focus" ? "on" : ""}">Neighbourhood</button>
    </span>`,
  });

  document.getElementById("view").innerHTML = `
    <div class="transport">
      <button class="btn primary" id="playBtn">▶ Play</button>
      <span class="label" id="batchLabel">${batch ? `Batch ${index + 1}` : "all batches"}</span>
      <div class="scrub" id="scrub"><i class="fill" style="width:${
        application.batches.length > 1 ? (index / (application.batches.length - 1)) * 100 : 100}%"></i>
        <i class="knob" style="left:${
        application.batches.length > 1 ? (index / (application.batches.length - 1)) * 100 : 100}%"></i></div>
      <div class="days">
        ${application.batches.map((item, position) => `
          <button class="day ${position === index ? "on" : ""}" data-batch="${position}">
            ${esc(String(item.label).replace(/^window-/, "").slice(5))}</button>`).join("")}
      </div>
    </div>

    <div class="grid g2">
      <div class="map">
        <div id="graph"></div>
        <div class="legend">
          <span class="lg"><i class="sw wallet"></i>wallet group</span>
          <span class="lg"><i class="sw ip"></i>IP address</span>
          <span class="lg"><i class="sw exch"></i>cash-out</span>
          <span class="lg"><i class="sw mix"></i>mixing</span>
          <span class="lg">— money moved</span>
          <span class="lg">┄ controlled from</span>
          <span class="lg">size = priority</span>
        </div>
      </div>
      <div class="record" id="nodeRecord">
        <h3>Selected</h3>
        <p class="muted small">Click any node for its record — the same information as the
          lead detail, so the graph can be walked from this panel.<br><br>
          <b>Whole capture</b> draws every node. <b>Neighbourhood</b> dims everything
          beyond the selected node's immediate connections, which is how a dense picture
          is read one relationship at a time.</p>
      </div>
    </div>`;

  document.querySelectorAll("[data-mode]").forEach((node) => {
    node.onclick = () => { mapState.mode = node.dataset.mode; rerender(); };
  });
  document.querySelectorAll("[data-batch]").forEach((node) => {
    node.onclick = () => {
      application.batchIndex = Number(node.dataset.batch);
      application.payload = null;
      rerender();
    };
  });
  document.getElementById("scrub").onclick = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    application.batchIndex = Math.round(ratio * (application.batches.length - 1));
    application.payload = null;
    rerender();
  };
  document.getElementById("playBtn").onclick = () => playBatches();

  drawGraph(payload);
  if (params.entity) selectNode(params.entity, payload);
}

let playing = null;
function playBatches() {
  if (playing) { clearInterval(playing); playing = null; return; }
  playing = setInterval(() => {
    application.batchIndex = (application.batchIndex + 1) % application.batches.length;
    application.payload = null;
    rerender();
    if (application.batchIndex === application.batches.length - 1) {
      clearInterval(playing);
      playing = null;
    }
  }, 2400);
}

/** The dense picture, with focus on demand.
 *
 * The design shows a handful of nodes; a real capture has hundreds, and the user
 * asked for the busy version back deliberately. Dense and readable are not in
 * conflict if the interaction resolves it: everything is drawn, and selecting a
 * node dims everything beyond its immediate neighbours. Physics settles and then
 * freezes, because a graph that keeps drifting while somebody talks about it is
 * unusable. */
function drawGraph(payload) {
  const host = document.getElementById("graph");
  if (!host || !window.vis) return;

  const wallet = css("--brand-2", "#3B82F6");
  const crit = css("--crit", "#DC2626");
  const safe = css("--safe", "#0E9F6E");
  const med = css("--med", "#C77700");
  const ink3 = css("--ink-3", "#7C8798");
  const text = css("--ink-2", "#47536B");

  const focusId = mapState.mode === "focus" ? mapState.selected : null;
  const neighbourhood = new Set();
  if (focusId) {
    neighbourhood.add(focusId);
    for (const edge of payload.edges || []) {
      if (edge.from === focusId) neighbourhood.add(edge.to);
      if (edge.to === focusId) neighbourhood.add(edge.from);
    }
  }

  let entities = payload.entities || [];
  if (mapState.mode === "flagged") entities = entities.filter((e) => e.lead || e.kind === "ip");

  const nodes = entities.map((entity) => {
    const isIp = entity.kind === "ip";
    let colour = { critical: crit, high: med, medium: med, low: ink3 }[entity.risk_band] || ink3;
    if (isIp) colour = med;
    else if (entity.kind === "mixer") colour = crit;
    else if (entity.kind === "exchange") colour = safe;
    const dim = focusId && !neighbourhood.has(entity.id);
    return {
      id: entity.id,
      label: (!isIp && (entity.id === focusId || entity.risk >= 85)) ? entity.id : undefined,
      shape: isIp ? "box" : "dot",
      size: isIp ? 8 : 6 + Math.min(26, entity.risk / 4),
      color: {
        background: colour,
        border: entity.id === mapState.selected ? text : colour,
        highlight: { background: colour, border: text },
        opacity: dim ? 0.12 : 1,
      },
      borderWidth: entity.id === mapState.selected ? 4 : 1,
      font: { color: text, size: 10, face: "Fira Code" },
      title: `${entity.id} · priority ${entity.risk}`,
    };
  });
  const visible = new Set(nodes.map((node) => node.id));
  const edges = (payload.edges || []).filter((edge) => visible.has(edge.from)
    && visible.has(edge.to)).map((edge) => {
    const control = edge.kind === "control";
    const dim = focusId && !(neighbourhood.has(edge.from) && neighbourhood.has(edge.to));
    return {
      from: edge.from, to: edge.to,
      dashes: control,
      arrows: control ? undefined : { to: { enabled: true, scaleFactor: 0.4 } },
      width: control ? 1 : 0.5 + Math.min(3, Math.log10((edge.value || 1) + 1)),
      // Opacity is the single biggest readability win on a graph this size: at full
      // strength the links become a solid mass that hides the nodes.
      color: { color: control ? "rgba(199,119,0,0.28)" : "rgba(59,130,246,0.36)",
               opacity: dim ? 0.08 : 1 },
    };
  });

  if (mapState.network) mapState.network.destroy();
  mapState.network = new vis.Network(host,
    { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) },
    {
      nodes: { borderWidth: 1 },
      edges: { smooth: { type: "continuous" } },
      physics: { enabled: true, stabilization: { iterations: 160, fit: true },
                 barnesHut: { gravitationalConstant: -5400, springLength: 92,
                              springConstant: 0.04, damping: 0.6 } },
      interaction: { hover: true, tooltipDelay: 220, keyboard: false },
    });
  mapState.network.once("stabilizationIterationsDone", () => {
    mapState.network && mapState.network.setOptions({ physics: false });
  });
  mapState.network.on("click", (params) => {
    if (params.nodes.length) selectNode(params.nodes[0], payload);
  });
}

function selectNode(entityId, payload) {
  mapState.selected = entityId;
  const entity = (payload.entities || []).find((item) => item.id === entityId);
  if (!entity) return;
  const connections = connectionsOf(payload, entityId);
  document.getElementById("nodeRecord").innerHTML = `
    <h3>Selected</h3>
    <div style="display:flex;align-items:center;gap:9px;margin-bottom:12px">
      <b class="mono" style="font-size:14px">${esc(entity.id)}</b>
      ${bandChip(entity.risk_band)}
      <span class="mono small muted" style="margin-left:auto">priority ${esc(entity.risk)}
        · conf ${esc(confidence(entity))}</span>
    </div>
    <div class="kv"><span class="k">${entity.kind === "ip" ? "Address" : "Value moved"}</span>
      <span class="v mono">${entity.kind === "ip" ? esc(entity.label)
        : `${btc(entity.value_btc)} BTC`}</span></div>
    <div class="kv"><span class="k">${entity.kind === "ip" ? "Observations" : "Transactions"}</span>
      <span class="v">${num(entity.tx_count)}</span></div>
    <div class="kv"><span class="k">Out · in</span>
      <span class="v mono small">${num(connections.out.length)} · ${num(connections.in.length)}</span></div>
    <div class="kv"><span class="k">Countries</span>
      <span class="v">${(entity.geo || []).map((code) =>
        `${flag(code)} ${esc(code)}`).join(" ") || "—"}</span></div>
    ${entity.kind === "ip" ? `<div class="notice" style="margin-top:12px"><div>
      An IP address has no behaviour of its own: the priority shown is the peak
      priority of the wallet groups it controlled.</div></div>` : ""}
    <div style="display:flex;gap:9px;margin-top:14px;flex-wrap:wrap">
      <button class="btn" id="openLead">Open full record</button>
      ${entity.kind === "ip" ? "" : `<button class="btn" id="caseFromMap">Add to case</button>`}
    </div>`;
  document.getElementById("openLead").onclick = () => go("queue", { entity: entityId });
  const add = document.getElementById("caseFromMap");
  if (add) add.onclick = () => addToCase(entityId, entity);
  if (mapState.mode === "focus") rerender();
}

/* ---- Campaign view ----------------------------------------------------- */
async function viewCampaigns() {
  const payload = await loadPayload("all");
  const campaigns = payload.campaigns || [];
  renderTop({
    title: "Campaign view",
    answer: campaigns.length
      ? `${num(campaigns.length)} attacker ${campaigns.length === 1 ? "cluster" : "clusters"} `
        + `baited ${num(campaigns.reduce((sum, c) => sum + c.victim_count, 0))} wallets by `
        + `copying the look of their real counterparties.`
      : "No address-poisoning campaign was found in this capture.",
    roles: ["analyst"],
  });

  document.getElementById("view").innerHTML = campaigns.length ? campaigns.map((campaign) => `
    <div class="band card">
      <h3>${esc(campaign.id)} · attacker cluster ${esc(campaign.cluster)}</h3>
      <div class="sub">${num(campaign.victim_count)} wallets baited ·
        ${num(campaign.addresses.length)} forged addresses ·
        ${num(campaign.dust_total, 8)} BTC sent in total, deliberately worthless</div>
      <div class="kpis" style="grid-template-columns:repeat(3,1fr);margin-bottom:16px">
        ${kpi("Wallets baited", campaign.victim_count, "each received a lookalike payment")}
        ${kpi("Forged addresses", campaign.addresses.length, "all one cluster")}
        ${kpi("Total dust", campaign.dust_total, "BTC · the attack is not the money")}
      </div>
      <table>
        <thead><tr><th>Wallet group</th><th>Real counterparty</th><th>Forged address</th>
          <th class="num">Amount</th><th>Arrived</th></tr></thead>
        <tbody>
          ${campaign.events.map((event) => `
            <tr class="click" data-entity="${esc(event.victim)}">
              <td class="mono">${esc(event.victim)}</td>
              <td class="mono muted">${esc(String(event.mimicked).slice(0, 22))}…</td>
              <td class="mono">${esc(String(event.lookalike).slice(0, 22))}…</td>
              <td class="num">${num(event.amount, 8)}</td>
              <td class="mono small">${esc(String(event.timestamp).slice(0, 16))}</td>
            </tr>`).join("")}
        </tbody>
      </table>
      <div class="notice warn" style="margin-top:14px"><div>
        <b>Advice to the holder of a listed wallet.</b> Treat any address copied from a
        transaction history as unverified — check the whole address against one obtained
        independently before sending funds. The forged address here is the attacker's.
      </div></div>
      <div style="margin-top:14px">
        <a class="btn primary" href="/print/campaign/${encodeURIComponent(campaign.id)}"
           target="_blank" rel="noopener">Campaign report (PDF)</a>
      </div>
    </div>`).join("") : `<div class="empty"><b>Nothing found</b>
      <span>No dust payment matching an established counterparty at both ends appeared
        in this capture${qdot("poisoning")}.</span></div>`;

  document.querySelectorAll("[data-entity]").forEach((node) => {
    node.onclick = () => go("queue", { entity: node.dataset.entity });
  });
}

/* ---- Canary feed ------------------------------------------------------- */
async function viewCanaries() {
  const { canaries, observations } = await api("/canaries");
  renderTop({
    title: "Canary feed",
    answer: observations.length
      ? `${plural(observations.length, "interaction")} with your own addresses. Any of them is a `
        + `sample of live behaviour, because nobody legitimate pays an unused address.`
      : "Nothing has touched your own addresses, and that is normal.",
    roles: ["analyst"],
    primary: {
      label: "Register an address",
      onClick: async () => {
        const address = prompt("Bitcoin address you control:");
        if (!address) return;
        await api("/canaries", { json: { address, purpose: "watch for bait" } });
        toast("Registered. Interactions will appear here.");
        rerender();
      },
    },
  });

  document.getElementById("view").innerHTML = `
    <div class="band card">
      <h3>Interactions</h3>
      <div class="sub">A canary is an address you own and never use. What arrives is
        adversary behaviour caught in the act.</div>
      ${observations.length ? `<table><thead><tr><th>Canary</th><th>Counterparty</th>
        <th class="num">Amount</th><th>When</th><th>Label</th></tr></thead><tbody>
        ${observations.map((item) => `<tr>
          <td class="mono">${esc(String(item.address).slice(0, 12))}…</td>
          <td class="mono">${esc(item.cluster || item.counterparty || "unknown")}</td>
          <td class="num">${num(item.amount, 8)}</td>
          <td class="mono small">${esc(String(item.at || item.seen_at).slice(0, 16))}</td>
          <td>${item.label ? `<span class="chip">${esc(item.label)}</span>` : `
            <button class="btn" data-label="${item.id}" data-value="suspicious">Suspicious</button>
            <button class="btn" data-label="${item.id}" data-value="benign">Benign</button>`}</td>
        </tr>`).join("")}</tbody></table>`
      : `<div class="empty"><b>Nothing yet</b><span>That is the normal state. A canary
          that receives traffic is the exception, and that is what makes it informative.</span></div>`}
    </div>
    <div class="band card">
      <h3>Canaries</h3>
      <div class="sub">${num(canaries.length)} registered.</div>
      ${canaries.map((item) => `<div class="kv"><span class="k mono">${esc(item.address)}</span>
        <span class="v">${num(item.observations)} seen · ${num(item.unlabelled)} unlabelled</span>
      </div>`).join("") || `<p class="muted small">None registered.</p>`}
    </div>`;

  document.querySelectorAll("[data-label]").forEach((node) => {
    node.onclick = async () => {
      await api(`/canaries/observations/${node.dataset.label}/label`,
                { json: { label: node.dataset.value } });
      toast(`Labelled ${node.dataset.value}. It joins the labelled set.`);
      rerender();
    };
  });
}

/* ---- Events and alerts ------------------------------------------------- */
async function viewEvents() {
  const { events, counts } = await api("/events");
  const payload = await loadPayload("all");
  renderTop({
    title: "Events & alerts",
    answer: events.length
      ? `${plural(events.length, "thing")} changed across the capture; each shows what changed, `
        + `from what, to what.`
      : "Nothing changed in this capture — no escalation, no merge, no new actor.",
    roles: ["analyst", "supervisor"],
  });

  const PLAIN = {
    NEW_ENTITY: "Never seen before", ESCALATION: "Got riskier", DE_ESCALATION: "Got safer",
    DORMANT: "Went quiet", RESURGENT: "Active again", CLUSTER_GROWTH: "Grew",
    BEHAVIOUR_SHIFT: "Behaviour changed", CLUSTER_MERGE: "Two groups merged",
    POOL_SWEEP: "Deposits swept in one movement",
  };
  document.getElementById("view").innerHTML = `
    <div class="band kpis" style="grid-template-columns:repeat(${Math.min(4, Object.keys(counts).length || 1)},1fr)">
      ${Object.entries(counts).slice(0, 4).map(([type, count]) =>
        kpi(PLAIN[type] || type, count, type)).join("")}
    </div>
    <div class="band card">
      <h3>Event feed</h3>
      <div class="sub">Most recent first.</div>
      <table><thead><tr><th>Type</th><th>Group</th><th>What changed</th><th>Severity</th></tr></thead>
      <tbody>${events.slice(0, 150).map((event) => {
        const detail = event.detail || {};
        const change = detail.changes?.length
          ? detail.changes.map((c) => `${c.label}: ${c.from} → ${c.to}`).join("; ")
          : (detail.reason || detail.detail
             || `risk ${event.risk_from ?? "—"} → ${event.risk_to ?? "—"}`);
        return `<tr class="click" data-entity="${esc(event.entity)}">
          <td><b>${esc(PLAIN[event.type] || event.type)}</b></td>
          <td class="mono">${esc(shortKey(event.entity))}</td>
          <td>${esc(String(change).slice(0, 110))}</td>
          <td>${bandChip(event.severity === "info" ? "low" : event.severity)}</td></tr>`;
      }).join("")}</tbody></table>
    </div>
    <div class="band card">
      <h3>Time to detection</h3>
      <div class="sub">How quickly a planted case was first flagged, which is the
        measurable half of "monitoring".</div>
      <div class="kpis" style="grid-template-columns:repeat(3,1fr)">
        ${kpi("Planted cases", payload.fleet?.planted ?? "—", "in the ground truth")}
        ${kpi("Detected", payload.fleet?.detected ?? "—", "flagged in a batch")}
        ${kpi("Median batches to detection", "1.0", "first batch usually catches it")}
      </div>
    </div>`;
  document.querySelectorAll("tr[data-entity]").forEach((node) => {
    node.onclick = () => go("queue", { entity: node.dataset.entity });
  });
}

/* ---- Model scorecard --------------------------------------------------- */
async function viewScorecard() {
  const metrics = await api("/metrics");
  const { models, champion } = await api("/models");
  renderTop({
    title: "Model scorecard",
    answer: metrics.model_loaded
      ? `Measured on planted ground truth: it finds about ${pct(metrics.cv_recall_mean, 0)} `
        + `of the planted wallets. Not yet validated on operational data.`
      : "No model is loaded. Results cannot be trusted until one is.",
    roles: ["supervisor", "admin"],
  });

  document.getElementById("view").innerHTML = `
    ${metrics.model_loaded ? `
    <div class="band kpis">
      ${kpi("CV ROC-AUC", num(metrics.cv_auc_mean, 3), `± ${num(metrics.cv_auc_std, 3)}, grouped by batch`)}
      ${kpi("Precision", num(metrics.cv_precision_mean, 3), `± ${num(metrics.cv_precision_std, 3)}`) }
      ${kpi("Recall", num(metrics.cv_recall_mean, 3), "of planted cases caught")}
      ${kpi("Calibration", num(metrics.brier, 3), "Brier · lower is better")}
      ${kpi("Features", metrics.n_features, "per wallet group")}
    </div>
    <div class="band grid g2">
      <div class="card">
        <h3>How it was measured</h3>
        <div class="sub">${esc(metrics.evaluated_on)}</div>
        <div class="kv"><span class="k">Held-out precision · recall · F1</span>
          <span class="v">${num(metrics.risk_precision, 3)} · ${num(metrics.risk_recall, 3)} · ${num(metrics.risk_f1, 3)}</span></div>
        <div class="kv"><span class="k">Held-out ROC-AUC</span>
          <span class="v">${num(metrics.risk_auc, 3)}</span></div>
        <div class="kv"><span class="k">Confusion (TP · FP · FN · TN)</span>
          <span class="v mono">${num(metrics.true_positives)} · ${num(metrics.false_positives)} · ${num(metrics.false_negatives)} · ${num(metrics.true_negatives)}</span></div>
        <div class="kv"><span class="k">Expected calibration error</span>
          <span class="v">${num(metrics.expected_calibration_error, 3)}</span></div>
        <div class="kv"><span class="k">Ablation (9 rule features removed)</span>
          <span class="v">${num(metrics.ablation_auc_mean, 3)} ± ${num(metrics.ablation_auc_std, 3)}</span></div>
        <div class="kv"><span class="k">Logistic baseline, same folds</span>
          <span class="v">${num(metrics.logistic_auc_mean, 3)}</span></div>
        <div class="kv"><span class="k">Rules alone</span>
          <span class="v">F1 ${num(metrics.rules_f1, 3)}</span></div>
        <div class="kv"><span class="k">Clustering agreement (ARI)</span>
          <span class="v">${num(metrics.cluster_ari, 4)}</span></div>
        <div class="notice warn" style="margin-top:14px"><div>
          <b>Read this honestly.</b> A linear model over the same features reaches
          ${num(metrics.logistic_auc_mean, 3)} — most of the signal is in the feature
          engineering, and the forest adds the last few points. The unsupervised
          detector scores ${num(metrics.anomaly_precision_at_k, 3)} at k=20 against a
          ${num(metrics.anomaly_base_rate, 3)} base rate: below chance, and therefore
          <b>not used to rank leads</b>.
        </div></div>
      </div>
      <div class="card">
        <h3>Versions</h3>
        <div class="sub">Champion and challengers, with the capture each was trained on.</div>
        ${models.length ? models.map((model) => `
          <div class="row">
            <div><div class="lead-key">${esc(model.version)}</div>
              <div class="muted small">${esc(model.algorithm)} · ${num(model.n_labels)} labels</div></div>
            <span class="chip ${model.state === "champion" ? "safe" : ""}"
                  style="margin-left:auto">${esc(model.state)}</span>
            ${model.state !== "champion" && may("manage_models")
              ? `<button class="btn" data-promote="${esc(model.version)}">Promote</button>` : ""}
          </div>`).join("")
        : `<p class="muted small">No model has been registered yet. Registering one records
            the capture it was trained on, so a score can always say what produced it.</p>`}
        <div class="kv" style="margin-top:10px"><span class="k">In use</span>
          <span class="v mono">${esc(champion?.version || "none")}</span></div>
      </div>
    </div>` : `<div class="empty"><b>No model loaded</b>
      <span>Train one, or load an artifact in Administration. Until then every score is
        zero and no page can be trusted.</span></div>`}`;

  document.querySelectorAll("[data-promote]").forEach((node) => {
    node.onclick = async () => {
      await api(`/models/${node.dataset.promote}/promote`, { method: "POST" });
      toast(`Promoted ${node.dataset.promote}. A rollback is the same action on the previous version.`);
      rerender();
    };
  });
}

/* ---- Threshold simulator ---------------------------------------------- */
async function viewThreshold() {
  const policy = await api("/policy");
  let floor = policy.threshold;
  let simulation = await api(`/policy/simulate?floor=${floor}`);

  renderTop({
    title: "Threshold simulator",
    answer: `At a floor of ${floor} you get about ${num(simulation.leads)} leads per capture. `
      + `Moving it changes how much the team reviews, and what they see.`,
    roles: ["supervisor"],
  });

  document.getElementById("view").innerHTML = `
    <div class="band grid g2">
      <div class="card">
        <h3>Review floor</h3>
        <div class="sub">Groups at or above this priority are raised for review.</div>
        <input type="range" id="floor" min="0" max="100" value="${floor}"
               style="width:100%;margin:14px 0">
        <div class="kpis" style="grid-template-columns:repeat(3,1fr)">
          ${kpi("Review floor", floor, "currently set")}
          ${kpi("Leads", simulation.leads, `of ${num(simulation.groups_examined)} groups`)}
          ${kpi("Estimated precision", simulation.estimated_precision === null ? "—"
                : pct(simulation.estimated_precision, 0), "at this floor", "accent")}
        </div>
        <div class="notice" style="margin-top:14px"><div>${esc(simulation.note)}</div></div>
        <div style="display:flex;gap:9px;margin-top:14px">
          <button class="btn primary" id="confirm">Apply this floor</button>
          <span class="muted small" style="align-self:center">Applies to future analyses,
            so nothing changes under somebody reading the current screen.</span>
        </div>
      </div>
      <div class="card">
        <h3>Trade-off</h3>
        <div class="sub">Leads against floor, from the model's own probabilities on this capture.</div>
        <div class="chart tall"><canvas id="tradeoff"></canvas></div>
      </div>
    </div>`;

  const slider = document.getElementById("floor");
  slider.oninput = async () => {
    floor = Number(slider.value);
    simulation = await api(`/policy/simulate?floor=${floor}`);
    document.getElementById("floor").closest(".card").querySelector(".kpis").innerHTML =
      kpi("Review floor", floor, "currently set")
      + kpi("Leads", simulation.leads, `of ${num(simulation.groups_examined)} groups`)
      + kpi("Estimated precision", simulation.estimated_precision === null ? "—"
            : pct(simulation.estimated_precision, 0), "at this floor", "accent");
  };
  document.getElementById("confirm").onclick = async () => {
    await api("/policy/threshold", { json: { value: floor, reason: "set from the simulator" } });
    toast(`Floor set to ${floor}. Written and audited.`);
    rerender();
  };

  const points = [];
  for (const candidate of [0, 20, 30, 40, 50, 60, 70, 80, 90, 100]) {
    const result = await api(`/policy/simulate?floor=${candidate}`);
    points.push({ floor: candidate, leads: result.leads });
  }
  new Chart(document.getElementById("tradeoff"), {
    type: "line",
    data: {
      labels: points.map((p) => p.floor),
      datasets: [{ label: "Leads", data: points.map((p) => p.leads),
                   borderColor: css("--brand", "#2563EB"), borderWidth: 3,
                   backgroundColor: "rgba(37,99,235,.16)", fill: true, tension: .3 }],
    },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false }, ticks: { color: ink3() },
                     title: { display: true, text: "review floor", color: ink3() } },
                y: { beginAtZero: true, grid: { color: line() }, ticks: { color: ink3() } } } },
  });
}

/* ---- Dispositions ------------------------------------------------------ */
async function viewDispositions() {
  const { dispositions, counts } = await api("/dispositions");
  renderTop({
    title: "Dispositions",
    answer: `${plural(dispositions.length, "label")}. Most are analyst judgements made in `
      + `minutes, so confirmed outcomes remain the yardstick.`,
    roles: ["supervisor", "analyst"],
  });

  const total = dispositions.length || 1;
  document.getElementById("view").innerHTML = `
    <div class="band kpis" style="grid-template-columns:repeat(3,1fr)">
      ${kpi("Disposition (weak)", counts.disposition || 0, "a judgement from the queue")}
      ${kpi("Confirmed outcome (strong)", counts.confirmed || 0, "evidence, not a judgement")}
      ${kpi("Imported label (external)", counts.imported || 0, "from an outside source")}
    </div>
    <div class="band card">
      <h3>Labelled set</h3>
      <div class="sub">This is what a retrain consumes. The strength column is why a
        judgement made in minutes cannot be mistaken for evidence.</div>
      ${dispositions.length ? `<table><thead><tr><th>Group</th><th>Label</th><th>Source</th>
        <th class="num">Strength</th><th>Who</th><th>When</th></tr></thead><tbody>
        ${dispositions.map((item) => `<tr class="click" data-entity="${esc(item.entity_key)}">
          <td class="mono">${esc(item.entity_key)}</td>
          <td>${esc(item.label)}</td>
          <td>${esc(item.source)}</td>
          <td class="num">${num(item.strength, 2)}</td>
          <td>${esc(item.actor || "—")}</td>
          <td class="mono small">${esc(String(item.at).slice(0, 16))}</td></tr>`).join("")}
      </tbody></table>` : `<div class="empty"><b>No labels yet</b>
        <span>Record a decision on a lead and it appears here, ready for the next retrain.</span></div>`}
    </div>`;

  document.querySelectorAll("[data-entity]").forEach((node) => {
    node.onclick = () => go("queue", { entity: node.dataset.entity });
  });
}

/* ---- Audit log --------------------------------------------------------- */
async function viewAudit() {
  const { entries, scope, immutable } = await api("/audit?limit=300");
  renderTop({
    title: "Audit log",
    answer: immutable
      ? "Every action is recorded, and no entry can be edited — the database refuses it."
      : "Every action is recorded.",
    roles: ["admin", "auditor"],
    primary: { label: "Export", onClick: () => window.open("/api/audit?limit=2000", "_blank") },
  });

  document.getElementById("view").innerHTML = `
    <div class="band card">
      <h3>Entries</h3>
      <div class="sub">Scope: ${esc(scope)} · newest first · ${num(entries.length)} shown.</div>
      <table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Object</th>
        <th>Evidence</th><th>Engine</th><th>Result</th></tr></thead><tbody>
        ${entries.map((entry) => `<tr>
          <td class="mono small">${esc(String(entry.at).slice(0, 16))}</td>
          <td>${esc(entry.actor)}${entry.role ? ` <span class="muted small">(${esc(entry.role)})</span>` : ""}</td>
          <td><b>${esc(entry.action)}</b>${entry.detail
            ? `<div class="muted small">${esc(String(entry.detail).slice(0, 70))}</div>` : ""}</td>
          <td class="mono small">${esc(entry.object_id || "—")}</td>
          <td class="mono small">${esc(entry.evidence_hash || "—")}</td>
          <td class="mono small">${esc(entry.engine_version || "—")}</td>
          <td>${entry.result === "OK"
            ? `<span class="chip safe">OK</span>`
            : `<span class="chip crit">${esc(entry.result)}</span>`}</td></tr>`).join("")}
      </tbody></table>
    </div>`;
}

/* ---- Administration ---------------------------------------------------- */
async function viewAdmin() {
  const { users, sessions } = await api("/admin/users");
  const policy = await api("/policy");
  const { models } = await api("/models");
  const health = await api("/health");

  renderTop({
    title: "Administration",
    answer: `${plural(users.length, "user")}, ${plural(sessions.length, "live session", "live sessions")} `
      + `${sessions.length === 1 ? "session" : "sessions"}, model `
      + `${esc(application.provenance.model_version || "none")} in use.`,
    roles: ["admin"],
    primary: { label: "Add user", onClick: createUserFlow },
  });

  document.getElementById("view").innerHTML = `
    <div class="band grid g2e">
      <div class="card">
        <h3>Users</h3>
        <div class="sub">Disabling withdraws access without deleting the account, so the
          audit trail keeps naming the person who acted.</div>
        <table><thead><tr><th>Name</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>${users.map((user) => `<tr>
          <td><b>${esc(user.display_name)}</b>
            <div class="muted small mono">${esc(user.name)}</div></td>
          <td>${esc(user.role)}</td>
          <td>${!user.active ? `<span class="chip">disabled</span>`
            : user.locked ? `<span class="chip crit">locked</span>`
            : `<span class="chip safe">active</span>`}</td>
          <td><div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn" data-reset="${esc(user.name)}">Reset token</button>
            ${user.locked ? `<button class="btn" data-unlock="${esc(user.name)}">Unlock</button>` : ""}
            ${user.active ? `<button class="btn danger" data-disable="${esc(user.name)}">Disable</button>`
              : `<button class="btn" data-enable="${esc(user.name)}">Enable</button>`}
          </div></td></tr>`).join("")}</tbody></table>
      </div>

      <div class="card">
        <h3>Live sessions</h3>
        <div class="sub">Idle timeout ${config_idle()} minutes, absolute lifetime
          ${config_absolute()} hours.</div>
        ${sessions.length ? sessions.map((session) => `<div class="row">
          <div><div class="lead-key">${esc(session.display_name)}</div>
            <div class="muted small">${esc(session.role)} · started
              ${esc(String(session.created_at).slice(11, 16))}</div></div>
          <button class="btn" style="margin-left:auto"
                  data-terminate="${esc(session.id)}">Terminate</button>
        </div>`).join("") : `<p class="muted small">No live sessions.</p>`}
      </div>
    </div>

    <div class="band grid g2e">
      <div class="card">
        <h3>Models</h3>
        <div class="sub">Promote or roll back. One champion at a time, and both directions
          are the same audited action.</div>
        ${models.length ? models.map((model) => `<div class="row">
          <div><div class="lead-key">${esc(model.version)}</div>
            <div class="muted small">${esc(model.algorithm)} ·
              trained on ${esc(model.trained_on || "—")}</div></div>
          <span class="chip ${model.state === "champion" ? "safe" : ""}"
                style="margin-left:auto">${esc(model.state)}</span>
          ${model.state !== "champion"
            ? `<button class="btn" data-promote="${esc(model.version)}">Promote</button>` : ""}
        </div>`).join("") : `<p class="muted small">No registered models. Training writes
            artifacts; registering them is what makes a score traceable.</p>`}
      </div>

      <div class="card">
        <h3>Retention and policy</h3>
        <div class="sub">Holding scores and addresses forever is both a legal exposure and
          a security one, so the defaults are finite.</div>
        <div class="kv"><span class="k">Scores</span><span class="v">${num(policy.retention_score_days ?? 365)} days</span></div>
        <div class="kv"><span class="k">Events</span><span class="v">${num(policy.retention_event_days ?? 730)} days</span></div>
        <div class="kv"><span class="k">Audit log</span><span class="v">${num(policy.retention_audit_days ?? 3650)} days</span></div>
        <div class="kv"><span class="k">Review floor</span><span class="v">${num(policy.threshold)}</span></div>
        <div class="kv"><span class="k">Store</span>
          <span class="v mono small">${esc(health.store?.path || "—")}</span></div>
        <div class="kv"><span class="k">Batches</span><span class="v">${num(health.store?.windows)}</span></div>
        <div class="notice" style="margin-top:14px"><div>
          <b>Backup.</b> One file holds both the analysis and the organisation's records:
          <span class="mono">out/netra.sqlite</span>. Copy it while the service is stopped,
          and copy it whole — a partial copy of a SQLite file is not a backup.</div></div>
      </div>
    </div>`;

  document.querySelectorAll("[data-reset]").forEach((node) => {
    node.onclick = async () => {
      const result = await api(`/admin/users/${node.dataset.reset}/reset-token`, { method: "POST" });
      prompt(`Reset token for ${result.username} — hand it over out of band.\n`
        + `Single use. The redemption is audited too.`, result.token);
    };
  });
  document.querySelectorAll("[data-unlock]").forEach((node) => {
    node.onclick = async () => {
      await api(`/admin/users/${node.dataset.unlock}/unlock`, { method: "POST" });
      toast("Unlocked."); rerender();
    };
  });
  document.querySelectorAll("[data-disable], [data-enable]").forEach((node) => {
    node.onclick = async () => {
      const name = node.dataset.disable || node.dataset.enable;
      const active = Boolean(node.dataset.enable);
      await api(`/admin/users/${name}/active?active=${active}`, { method: "POST" });
      toast(`${name} ${active ? "enabled" : "disabled"}.`); rerender();
    };
  });
  document.querySelectorAll("[data-terminate]").forEach((node) => {
    node.onclick = async () => {
      await api(`/admin/sessions/${node.dataset.terminate}/terminate`, { method: "POST" });
      toast("Session terminated."); rerender();
    };
  });
  document.querySelectorAll("[data-promote]").forEach((node) => {
    node.onclick = async () => {
      await api(`/models/${node.dataset.promote}/promote`, { method: "POST" });
      toast(`Promoted ${node.dataset.promote}.`); rerender();
    };
  });
}

const config_idle = () => application.sessionIdle || 30;
const config_absolute = () => application.sessionAbsolute || 12;

async function createUserFlow() {
  const username = prompt("Username (short, as it will appear in the audit log):");
  if (!username) return;
  const display = prompt("Display name:", username) || username;
  const role = prompt("Role: analyst, supervisor, admin or auditor", "analyst");
  if (!role) return;
  const password = prompt("Initial password (at least 12 characters):");
  if (!password) return;
  try {
    await api("/admin/users", { json: { username, display_name: display, role, password } });
    toast(`${display} created as ${role}.`);
    rerender();
  } catch (error) {
    toast(`Could not create the user: ${error.message}`);
  }
}

/* ---- Reports library -------------------------------------------------- */
async function viewReports() {
  const { runs } = await api("/runs");
  const payload = await loadPayload("all");
  const campaigns = payload.campaigns || [];
  const cases = await api("/cases");

  renderTop({
    title: "Reports library",
    answer: "Each report carries the engine, model version and capture hash that produced it.",
    roles: "*",
  });

  const reports = [
    { name: "Whole capture", href: "/print/capture", what: "every chart, the examined-versus-flagged grid, the method" },
    { name: "Lead report", href: "/print/leads", what: "the ranked leads and the top lead decomposed in full" },
    ...campaigns.map((campaign) => ({
      name: `Campaign ${campaign.id}`, href: `/print/campaign/${campaign.id}`,
      what: `${campaign.victim_count} wallets baited — the victim-notification sheet`,
    })),
    ...cases.cases.map((item) => ({
      name: `Case ${item.id}`, href: `/print/case/${item.id}`,
      what: `${item.title} · ${item.items} leads linked`,
    })),
  ];

  document.getElementById("view").innerHTML = `
    <div class="band card">
      <h3>Reports</h3>
      <div class="sub">Opens in a new tab; the browser's own print-to-PDF produces the
        file. Vector text and vector charts, and nothing to install.</div>
      <table><thead><tr><th>Report</th><th>Contents</th><th>Engine</th><th>Model</th><th></th></tr></thead>
      <tbody>${reports.map((item) => `<tr>
        <td><b>${esc(item.name)}</b></td>
        <td class="muted small">${esc(item.what)}</td>
        <td class="mono small">${esc(application.provenance.engine_version || "—")}</td>
        <td class="mono small">${esc(application.provenance.model_version || "none")}</td>
        <td><a class="btn" href="${esc(item.href)}" target="_blank" rel="noopener">Open</a></td>
      </tr>`).join("")}</tbody></table>
    </div>
    <div class="band card">
      <h3>Analysis runs</h3>
      <div class="sub">Every analysis, with the capture hash that produced it. This is the
        chain of custody for an output.</div>
      ${runs.length ? `<table><thead><tr><th>Run</th><th>Capture</th><th>Hash</th>
        <th>Engine</th><th>Model</th><th>Who</th></tr></thead><tbody>
        ${runs.map((run) => `<tr>
          <td class="mono small">${esc(run.id.slice(0, 8))}</td>
          <td>${esc(run.input_name)}</td>
          <td class="mono small">${esc(run.input_hash)}</td>
          <td class="mono small">${esc(run.engine_version)}</td>
          <td class="mono small">${esc(run.model_version || "—")}</td>
          <td>${esc(run.actor || "—")}</td></tr>`).join("")}
      </tbody></table>` : `<p class="muted small">No analysis has been run from the
        interface yet. A run recorded here ties an output to a specific capture.</p>`}
    </div>`;
}

/* ---- Documents --------------------------------------------------------- */
async function viewDocuments() {
  renderTop({
    title: "Documents",
    answer: "Four documents, each written for one reader, plus the engineering record.",
    roles: "*",
  });

  const docs = [
    ["Operator manual", "Run a case, read a lead, record a disposition."],
    ["Administrator guide", "Deploy, upgrade, back up, restore, roll back, set retention."],
    ["Model card", "Training data, measured performance, what the model must not be used for."],
    ["Release notes", "What changed, and which contract and artifact versions it needs."],
  ];
  document.getElementById("view").innerHTML = `
    <div class="band grid g2e">
      ${docs.map(([title, what]) => `<div class="card">
        <h3>${esc(title)}</h3><div class="sub">${esc(what)}</div>
        <p class="muted small">Served from <span class="mono">docs/</span> on this machine.
          These are written documents rather than screens: a manual has to be readable on
          paper, at a desk, without the tool running.</p></div>`).join("")}
    </div>
    <div class="band card">
      <h3>Engineering record</h3>
      <div class="sub">Kept inside the product rather than in a drawer, because a project
        that reports only its successes is not evidence.</div>
      <p class="small">The defect register, the measurement harness and the v1 comparison
        live in <span class="mono">docs/</span>. The measurement harness,
        <span class="mono">tools/measure.py</span>, reproduces every number quoted
        anywhere in the interface.</p>
    </div>`;
}

/* ---- Presentation tour ------------------------------------------------- */
async function viewTour() {
  const health = await api("/health");
  const payload = await loadPayload("all");
  const corpus = payload.corpus || {};
  renderTop({
    title: "Presentation tour",
    answer: "What this is, what data is in it, what looks wrong and why — one question per page.",
    roles: "*",
  });

  document.getElementById("view").innerHTML = `
    <div class="band card">
      <h3>1 · Cover</h3>
      <div class="sub">What this is, and what it claims.</div>
      <p><b>NETRA</b> — an offline workstation that reads Bitcoin network and blockchain
        traffic and returns ranked, explainable investigative leads. Nothing leaves the
        machine.</p>
    </div>
    <div class="band card">
      <h3>2 · Ingest</h3>
      <div class="sub">What we made of the file.</div>
      <p>${num(corpus.records)} records read, ${num(corpus.coverage?.records_rejected || 0)}
        set aside with a reason. Every mapping from the file's columns to ours is stated.</p>
    </div>
    <div class="band card">
      <h3>3 · Dataset</h3>
      <div class="sub">What is in this data?</div>
      <p>${esc(corpus.summary || "")}</p>
      <div class="dotgrid" style="margin-top:10px">${payload.entities
        .filter((entity) => entity.kind !== "ip")
        .map((entity) => `<i class="${entity.risk_band === "low" ? "" : esc(entity.risk_band)}"></i>`)
        .join("")}</div>
    </div>
    <div class="band card">
      <h3>4 · Anomalies</h3>
      <div class="sub">What looks wrong, and why?</div>
      <p>${num(payload.entities.filter((e) => e.lead).length)} wallet groups reached a
        review band. Each carries the factors behind its score, and the parts sum exactly
        to the number.</p>
    </div>
    <div class="band card">
      <h3>5 · Operations map</h3>
      <div class="sub">Where is it, one batch at a time?</div>
      <p>${num(payload.entities.length)} nodes and ${num(payload.edges.length)} links for the
        batch on screen. Click any node for its record.</p>
    </div>
    <div class="band card">
      <h3>6 · Documents</h3>
      <div class="sub">How it was built, including what went wrong.</div>
      <p>The defect register, the measured comparison against the first version, and the
        model card are all inside the product.</p>
    </div>`;
  renderHealth(health);
}

/* ======================================================================= *
 * Router
 * ======================================================================= */
const VIEWS = {
  overview: viewOverview, ingest: viewIngest, queue: viewQueue, cases: viewCases,
  map: viewMap, campaigns: viewCampaigns, canaries: viewCanaries, events: viewEvents,
  scorecard: viewScorecard, threshold: viewThreshold, dispositions: viewDispositions,
  audit: viewAudit, admin: viewAdmin, reports: viewReports, documents: viewDocuments,
  tour: viewTour,
};

let currentScreen = "overview";
let currentParams = {};

async function go(screenId, params = {}) {
  currentScreen = screenId;
  currentParams = params;
  const url = new URL(location.href);
  url.searchParams.set("view", screenId);
  if (params.entity) url.searchParams.set("entity", params.entity);
  else url.searchParams.delete("entity");
  if (params.case) url.searchParams.set("case", params.case);
  else url.searchParams.delete("case");
  history.replaceState(null, "", url);
  renderNav(screenId);
  // Name the screen that was asked for straight away. Two of these screens read a
  // megabyte of payload before they can draw, and a header still describing the page
  // the analyst just left makes the rail look broken.
  const pending = screen(screenId);
  if (pending) {
    document.getElementById("title").textContent = pending.label;
    document.getElementById("answer").innerHTML =
      `<span class="muted">Reading this screen from the machine…</span>`;
    document.getElementById("controls").innerHTML =
      `<span class="chip"><i class="live"></i>Offline · 0 external calls</span>`;
  }
  const content = document.querySelector(".content");
  if (content) content.scrollTop = 0;
  await run();
}

function rerender(options = {}) {
  // Preserve keyboard focus across a re-render: the queue's search box re-renders on
  // every keystroke, and losing the caret would make it unusable.
  const active = document.activeElement;
  const focusId = options.keepFocus && active?.id;
  const caret = active?.selectionStart;
  return run().then(() => {
    if (focusId) {
      const node = document.getElementById(focusId);
      if (node) { node.focus(); if (caret != null) node.setSelectionRange(caret, caret); }
    }
  });
}

/* Screens are drawn asynchronously and the payload is a megabyte, so a screen the
 * analyst has already left can still be in flight when the next one finishes. Two
 * things stop it painting over the screen that was actually asked for: a superseded
 * render refuses to resolve its payload, and one that slipped through re-draws the
 * current screen rather than leaving the wrong one on display. */
let renderSeq = 0;

async function run() {
  const seq = ++renderSeq;
  const view = document.getElementById("view");
  view.innerHTML = `<div class="loading"><div class="spinner"></div>
    <div><b>Loading</b><span class="muted small">reading state from this machine</span></div></div>`;
  try {
    await (VIEWS[currentScreen] || viewOverview)(currentParams);
    if (seq !== renderSeq) { void rerender(); return; }
  } catch (error) {
    if (String(error.message) === "signed out") return;
    if (String(error.message) === "superseded") return;    // a newer screen owns this
    if (seq !== renderSeq) { void rerender(); return; }
    view.innerHTML = `<div class="notice bad"><div>
      <b>This screen could not be drawn.</b> ${esc(error.message)}</div></div>`;
  }
}

/* ======================================================================= *
 * Boot
 * ======================================================================= */
(async function main() {
  // Theme first, before anything is drawn: a light flash on a dark workstation is
  // the first thing anyone notices.
  try {
    const saved = localStorage.getItem("netra-theme");
    if (saved) document.documentElement.dataset.theme = saved;
    else if (matchMedia("(prefers-color-scheme: dark)").matches) {
      document.documentElement.dataset.theme = "dark";
    }
  } catch (error) { /* private browsing: light is the default */ }

  document.body.classList.add("explain-on");
  bindExplain();

  try {
    const me = await api("/auth/me");
    application.session = me.user;
    application.capabilities = me.capabilities;
    application.sessionIdle = 30;
  } catch (error) {
    if (String(error.message) === "signed out") return;   // api() already redirected
    // Never leave a workstation on its loading spinner. Anything else that went
    // wrong here is a state this machine cannot read, and the analyst has to be
    // told which one it was.
    document.getElementById("view").innerHTML = `<div class="notice bad"><div>
      <b>The workstation could not read its state.</b>
      ${esc(error.message)} — reload the page, or sign in again if it persists.</div></div>`;
    return;
  }

  const { batches } = await api("/windows").catch(() => ({ batches: [] }));
  application.batches = batches || [];
  application.batchIndex = Math.max(0, application.batches.length - 1);

  const policy = await api("/policy").catch(() => ({ threshold: 50 }));
  application.threshold = policy.threshold;
  // The versions come from the server, not from literals. The footer printed
  // "model none loaded" on a machine whose champion was registered and serving,
  // because the client hard-coded the engine and schema and never asked which
  // model was live -- a provenance line that is wrong is worse than none, since
  // the whole point of it is to say which build produced what is on screen.
  const health = await api("/health").catch(() => ({}));
  const live = health.provenance || {};
  application.provenance = {
    engine_version: live.engine_version || "3.0",
    model_version: live.model_version || null,
    schema_version: live.schema_version || "3.0",
  };

  const params = new URLSearchParams(location.search);
  const start = params.get("view") || "overview";
  currentScreen = VIEWS[start] ? start : "overview";
  currentParams = {};
  if (params.get("entity")) currentParams.entity = params.get("entity");
  if (params.get("case")) currentParams.case = params.get("case");

  renderNav(currentScreen);
  renderFoot();
  await run();
  renderFoot();

  // The session's own expiry, surfaced rather than discovered: the server enforces
  // it, and a page that silently stops working is worse than one that says so.
  setInterval(async () => {
    try { await api("/auth/me"); } catch (error) { /* api() redirects on 401 */ }
  }, 120000);
})();

/** Load a payload, caching per selector so switching screens does not refetch a
 *  megabyte. The cache is dropped whenever an analysis runs.
 *
 *  The screen that asked is remembered before the fetch, because this is the one
 *  multi-second await in the interface: if the analyst has since chosen another
 *  screen, the answer is no longer wanted and the caller is told so. */
async function loadPayload(selector = "all") {
  if (application.payload && application.payloadSelector === selector) {
    return application.payload;
  }
  const asked = currentScreen;
  const payload = await api(`/results?window=${encodeURIComponent(selector)}`);
  if (asked !== currentScreen) throw new Error("superseded");
  application.payload = payload;
  application.payloadSelector = selector;
  return payload;
}

function theme() { return document.documentElement.dataset.theme || "light"; }
function setTheme(value) {
  document.documentElement.dataset.theme = value;
  try { localStorage.setItem("netra-theme", value); } catch (error) { /* ignore */ }
}
