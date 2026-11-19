/* Local operator UI. Verdicts always come from POST /check. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  view: "lab", mode: "single", history: [], current: null, error: null,
  running: false, health: null, stats: null, records: [], activityError: null,
  statsError: null, filter: "all", search: "", refreshPromise: null,
  sessionRuns: new Map(), sequence: 0, timer: null, toastTimer: null,
  limits: {history_turns: 6, request_bytes: 65536}, resultTab: "evidence"
};
const emptyResult = $("result-body").firstElementChild.cloneNode(true);
const clock = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
});
const views = {
  lab: ["Assessment lab", "Assess intent. Strengthen defense.", "Challenge the firewall. Inspect the evidence. Close the learning loop."],
  activity: ["Activity", "Every assessment, accounted for.", "Review stored outcomes and inspect the requests behind them."],
  analytics: ["Analytics", "See the shape of your defenses.", "Understand recorded verdicts, categories, and response times."]
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}
function duration(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "—";
  return ms < 1000 ? Math.round(ms) + "ms" : (ms / 1000).toFixed(1) + "s";
}
function category(value) {
  const label = String(value || "uncategorized").replaceAll("_", " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}
function recordId(value) {
  return Number.isInteger(Number(value)) ? "CF-" + String(value).padStart(4, "0") : String(value);
}
function parseTime(value) {
  if (!value) return null;
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/i.test(value) ? value : value + "Z";
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}
function timeLabel(value) {
  const date = parseTime(value);
  return date ? clock.format(date) : "—";
}
function notify(message) {
  clearTimeout(state.toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  state.toastTimer = setTimeout(() => {$("toast").hidden = true;}, 3500);
}
function setView(view, updateHash = true) {
  state.view = views[view] ? view : "lab";
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  document.querySelectorAll(".page-view").forEach((node) => {node.hidden = node.id !== "view-" + state.view;});
  $("breadcrumb").textContent = views[state.view][0];
  $("page-title").textContent = views[state.view][1];
  $("page-description").textContent = views[state.view][2];
  VelnastraMotion.page(state.view);
  if (updateHash) window.history.replaceState(null, "", "#" + state.view);
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); }
  catch (_) { throw new Error("The server returned an unreadable response. Refresh engine status and retry."); }
  if (!response.ok) throw new Error(data.reason || "Request failed (" + response.status + ").");
  return data;
}
function renderHealth() {
  const ready = state.health && state.health.status === "ok";
  const checking = state.health === null;
  $("health-btn").className = "health-pill " + (checking ? "checking" : ready ? "" : "offline");
  $("health-text").textContent = checking ? "Checking engines" : ready ? "Engines online" : "Engines unavailable";
  $("qwen-dot").classList.toggle("ready", Boolean(state.health && state.health.cognitive_layer));
  $("qdrant-dot").classList.toggle("ready", Boolean(state.health && state.health.vector_store === "ready"));
  const notice = $("service-notice");
  if (!checking && !ready) {
    notice.textContent = "The local engines are not ready. Startup warms the models before accepting requests. Refresh engine status to check again.";
    notice.hidden = false;
  } else if (state.statsError || state.activityError) {
    notice.textContent = "Some stored workspace data could not be refreshed. The last available values are retained. Refresh to retry.";
    notice.hidden = false;
  } else notice.hidden = true;
  updateInputState();
}
function renderStats() {
  const stats = state.stats;
  $("metric-total").textContent = stats ? Number(stats.total_requests).toLocaleString("en-IN") : "—";
  $("metric-blocked").textContent = stats ? Number(stats.blocked_requests).toLocaleString("en-IN") : "—";
  $("metric-allowed").textContent = stats ? Number(stats.allowed_requests).toLocaleString("en-IN") : "—";
  $("metric-latency").textContent = stats ? duration(Number(stats.average_latency_ms)) : "—";
  renderAnalytics();
}
function renderAnalytics() {
  const distribution = $("distribution");
  const categories = $("category-chart");
  distribution.replaceChildren(); categories.replaceChildren();
  if (!state.stats) {
    distribution.append(el("div", "table-empty", "Stored analytics are not available yet."));
    categories.append(el("div", "table-empty", "No category data available."));
    return;
  }
  const stats = state.stats, total = Number(stats.total_requests);
  const blocked = Number(stats.blocked_requests), allowed = Number(stats.allowed_requests);
  const other = Math.max(0, total - blocked - allowed);
  const ring = el("div", "verdict-donut");
  const blockedAngle = total ? blocked / total * 360 : 0;
  const allowedAngle = total ? (blocked + allowed) / total * 360 : 0;
  ring.style.background = total ? "conic-gradient(#cb465d 0deg " + blockedAngle + "deg, #168264 " + blockedAngle + "deg " + allowedAngle + "deg, #b6b9c9 " + allowedAngle + "deg 360deg)" : "#e5e7ef";
  ring.setAttribute("role", "img");
  ring.setAttribute("aria-label", blocked + " blocked, " + allowed + " allowed, " + other + " other outcomes");
  const center = el("div", "donut-center");
  center.append(el("strong", "", total.toLocaleString("en-IN")), el("span", "", "ASSESSMENTS"));
  ring.append(center); distribution.append(ring);
  const key = el("div", "chart-legend");
  [["Blocked", blocked, "#cb465d"], ["Allowed", allowed, "#168264"], ...(other ? [["Other", other, "#b6b9c9"]] : [])].forEach(([name, count, color]) => {
    const label = el("div", "legend-row"), dot = el("span", "legend-dot");
    dot.style.background = color;
    label.append(dot, el("span", "", name), el("strong", "", count.toLocaleString("en-IN")), el("span", "legend-percent", (total ? Math.round(count / total * 100) : 0) + "%"));
    key.append(label);
  });
  distribution.append(key);
  renderLatencyChart();
  const groups = Array.isArray(stats.top_attack_categories) ? stats.top_attack_categories : [];
  if (!groups.length) categories.append(el("div", "table-empty", "No blocked categories have been recorded."));
  const largest = Math.max(1, ...groups.map((item) => Number(item.count)));
  groups.forEach((item, index) => {
    const row = el("div", "category-row"), heading = el("div");
    heading.append(el("span", "", category(item.category === "unknown" ? "uncategorized" : item.category)), el("span", "mono", item.count));
    const bar = el("div", "category-track"), fill = el("div", "category-fill");
    fill.style.width = (Number(item.count) / largest * 100) + "%";
    fill.style.background = ["#7556d8", "#9a78de", "#b595e9", "#ceb8ee"][index % 4];
    bar.append(fill); row.append(heading, bar); categories.append(row);
  });
}
function renderLatencyChart() {
  const host = $("latency-chart");
  host.replaceChildren();
  const rows = state.records.filter(row => Number.isFinite(Number(row.latency_ms))).slice().reverse();
  if (!rows.length) {host.append(el("div", "table-empty", "No recorded response times yet.")); return;}
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 960 250");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Response time for the latest " + rows.length + " assessments, oldest to newest. Each bar represents one request.");
  const max = Math.max(1000, ...rows.map(row => Number(row.latency_ms)));
  const step = 840 / rows.length;
  function shape(tag, attrs, text) {
    const node = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text;
    return node;
  }
  for (let i = 0; i <= 4; i++) {
    const y = 20 + i * 46;
    svg.append(shape("line", {x1:65,y1:y,x2:925,y2:y,stroke:"#e9eaf2","stroke-dasharray":"4 5"}));
    svg.append(shape("text", {x:53,y:y+4,"text-anchor":"end",fill:"#74798c","font-size":12}, (max * (4-i) / 4000).toFixed(1) + "s"));
  }
  rows.forEach((row, index) => {
    const height = Math.max(2, Number(row.latency_ms) / max * 184);
    const bar = shape("rect", {x:75+index*step,y:204-height,width:Math.max(3,step-7),height,rx:3,fill:row.status === "BLOCK" ? "#cb465d" : row.status === "ALLOW" ? "#168264" : "#b6b9c9",tabindex:0});
    bar.append(shape("title", {}, recordId(row.id) + " · " + row.status + " · " + duration(Number(row.latency_ms))));
    svg.append(bar);
  });
  svg.append(shape("text", {x:75,y:233,fill:"#74798c","font-size":12}, "Oldest"));
  svg.append(shape("text", {x:915,y:233,fill:"#74798c","font-size":12,"text-anchor":"end"}, "Latest"));
  host.append(svg);
}
async function refreshWorkspace(force = false) {
  if (state.refreshPromise) {
    await state.refreshPromise;
    if (!force) return;
  }
  $("refresh-btn").disabled = true; $("refresh-btn").classList.add("spinning");
  const job = Promise.allSettled([
    fetchJSON("/health"), fetchJSON("/api/analytics"), fetchJSON("/api/activity")
  ]);
  state.refreshPromise = job;
  try {
    const [health, stats, activity] = await job;
    state.health = health.status === "fulfilled" ? health.value : {status: "unavailable"};
    if (health.status === "fulfilled" && health.value.limits) state.limits = health.value.limits;
    state.statsError = stats.status === "rejected" ? stats.reason.message : null;
    if (stats.status === "fulfilled") state.stats = stats.value;
    state.activityError = activity.status === "rejected" ? activity.reason.message : null;
    if (activity.status === "fulfilled") state.records = Array.isArray(activity.value.requests) ? activity.value.requests : [];
    renderHealth(); renderStats(); renderActivity();
    $("last-sync").textContent = state.statsError || state.activityError ? "Some data could not be refreshed" : "Updated " + clock.format(new Date()) + " IST";
  } finally {
    state.refreshPromise = null;
    $("refresh-btn").disabled = false; $("refresh-btn").classList.remove("spinning");
  }
}

function requestBody() {
  return {
    prompt: $("prompt").value.trim(),
    history: state.mode === "conversation" ? state.history.slice(-state.limits.history_turns) : []
  };
}
function updateInputState() {
  const raw = $("prompt").value;
  const bytes = new TextEncoder().encode(JSON.stringify(requestBody())).length;
  const tooLarge = bytes > state.limits.request_bytes;
  $("input-count").textContent = raw.length.toLocaleString("en-IN") + " characters · " + raw.split("\n").length + (raw.split("\n").length === 1 ? " line" : " lines");
  $("line-numbers").textContent = Array.from({length: raw.split("\n").length}, (_, i) => i + 1).join("\n");
  $("input-error").textContent = tooLarge ? "The request exceeds 64 KiB. Shorten the input or clear conversation context." : "";
  $("input-error").hidden = !tooLarge;
  $("evaluate-btn").disabled = state.running || tooLarge || !raw.trim() || !(state.health && state.health.status === "ok");
  $("prompt").disabled = state.running;
  $("new-btn").disabled = state.running;
  $("mode-single").disabled = state.running; $("mode-conversation").disabled = state.running;
  $("clear-context-btn").disabled = state.running || !state.history.length;
  $("context-count").textContent = state.mode === "single" ? "No history attached" : state.history.length + " / " + state.limits.history_turns + " prior turns";
  if (state.current && !state.running) {
    const dirty = raw.trim() !== state.current.prompt;
    $("result-id").textContent = state.current.id + (dirty ? " · PREVIOUS INPUT" : "");
  }
}
function setMode(mode) {
  if (state.running) return;
  state.mode = mode;
  ["single", "conversation"].forEach((name) => {
    $("mode-" + name).classList.toggle("selected", name === mode);
    $("mode-" + name).setAttribute("aria-pressed", String(name === mode));
  });
  updateInputState();
}
function renderTiming(run) {
  const target = $("timing-rows"); target.replaceChildren();
  const timing = run && run.result.timings_ms || {};
  const fields = [["embedding", "Embeddings"], ["retrieval", "Retrieval"], ["qwen", "Qwen"], ["learning", "Learning"], ["queue", "Queue"]];
  const largest = Math.max(1, ...fields.map(([key]) => typeof timing[key] === "number" ? timing[key] : 0));
  fields.forEach(([key, label]) => {
    const row = el("div", "timing-row"), bar = el("div", "timing-track"), fill = el("div", "timing-fill");
    fill.style.width = (typeof timing[key] === "number" ? Math.max(0, timing[key]) / largest * 100 : 0) + "%";
    if (key === "qwen") fill.style.background = "#9170c8";
    if (key === "queue") fill.style.background = "#d0c5df";
    bar.append(fill); row.append(el("span", "", label), bar, el("span", "", duration(timing[key])));
    target.append(row);
  });
  $("timing-total").textContent = run ? duration(run.elapsedMs) + " OBSERVED" : "NO RUN YET";
}
function renderRunning() {
  $("result-id").textContent = "ASSESSING INTENT";
  $("evaluate-label").textContent = "Assessing intent";
  const node = el("div", "running-state");
  node.append(el("div", "spinner"), el("span", "eyebrow muted", "ANALYSIS IN PROGRESS"), el("h3", "", "Reading beyond the surface."));
  const elapsed = el("div", "elapsed", "00:00"); elapsed.id = "elapsed-clock"; node.append(elapsed);
  node.append(el("p", "", "Retrieving context and evaluating intent with Qwen. Longer inputs can take more time."));
  $("result-body").replaceChildren(node);
  $("copy-result-btn").disabled = true; $("export-result-btn").disabled = true;
}
function renderResult() {
  const target = $("result-body");
  target.replaceChildren();
  if (state.running) {renderRunning(); return;}
  if (state.error) {
    $("result-id").textContent = "NO VERDICT";
    const node = el("div", "empty-result error-state"), mark = el("div", "shield-orbit");
    mark.append(icon("info"));
    node.append(mark, el("span", "eyebrow muted", "ASSESSMENT UNAVAILABLE"), el("h3", "", "The request did not finish."), el("p", "", state.error));
    target.append(node);
    $("copy-result-btn").disabled = true; $("export-result-btn").disabled = true;
    return;
  }
  if (!state.current) {
    $("result-id").textContent = "NO RUN SELECTED";
    target.append(emptyResult.cloneNode(true));
    $("copy-result-btn").disabled = true; $("export-result-btn").disabled = true;
    return;
  }
  const run = state.current, data = run.result, blocked = data.status === "BLOCK";
  $("result-id").textContent = run.id;
  $("copy-result-btn").disabled = false; $("export-result-btn").disabled = false;
  const summary = el("div", "result-summary"), verdict = el("div", "result-verdict"), main = el("div", "verdict-main");
  const mark = el("span", "verdict-icon" + (blocked ? "" : " allowed")); mark.append(icon(blocked ? "shield" : "check"));
  const words = el("div"); words.append(el("div", "verdict-title" + (blocked ? "" : " allowed"), blocked ? "Blocked" : "Allowed"), el("div", "verdict-caption", "Qwen intent assessment · " + duration(run.elapsedMs)));
  main.append(mark, words); verdict.append(main, el("span", "category-badge", category(data.category)));
  summary.append(verdict, el("p", "verdict-reason", data.reason || "Assessment completed."));
  target.append(summary);
  const tabs = el("div", "result-tabs"); tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "Assessment details");
  const names = ["evidence", "references", "response"];
  names.forEach((name, i) => {
    const button = el("button", name === state.resultTab ? "selected" : "", name.charAt(0).toUpperCase() + name.slice(1));
    button.id = "result-tab-" + name; button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(name === state.resultTab)); button.setAttribute("aria-controls", "result-detail"); button.tabIndex = name === state.resultTab ? 0 : -1;
    const choose = () => {state.resultTab = name; renderResult(); $(button.id).focus();};
    button.addEventListener("click", choose);
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      state.resultTab = names[(i + (event.key === "ArrowRight" ? 1 : 2)) % 3];
      renderResult(); $("result-tab-" + state.resultTab).focus();
    });
    tabs.append(button);
  });
  target.append(tabs);
  const detail = el("div", "result-detail"); detail.id = "result-detail"; detail.setAttribute("role", "tabpanel"); detail.setAttribute("aria-labelledby", "result-tab-" + state.resultTab); detail.tabIndex = 0;
  if (state.resultTab === "evidence") {
    detail.append(el("div", "detail-label", blocked ? "Supporting evidence" : "Assessment finding"));
    detail.append(data.evidence ? el("blockquote", "evidence-quote", data.evidence) : el("p", "", blocked ? "The request was classified as malicious in its full context. No verbatim excerpt was returned." : "No malicious intent was identified in this assessment."));
    const learning = data.learning || {}, info = el("div", "learning-state" + (learning.status === "error" ? " warning" : ""));
    info.append(icon(learning.status === "learned" ? "plus" : learning.status === "error" ? "info" : "layers"));
    const copy = el("div");
    const titles = {learned: "Attack context learned", already_known: "Attack context already stored", not_applicable: "No attack signature added", error: "Learning did not complete"};
    const descriptions = {learned: "The full classified input is saved in Qdrant.", already_known: "This exact context already has a stored record.", not_applicable: "Benign inputs do not become attack signatures.", error: learning.reason || "The verdict stands. Review the storage error."};
    copy.append(el("strong", "", titles[learning.status] || "Learning status unavailable"), el("small", "", descriptions[learning.status] || "No learning outcome was reported."));
    info.append(copy); detail.append(info);
    if (learning.signature_id) detail.append(el("div", "learning-id", "RECORD " + learning.signature_id));
    if (data.telemetry && data.telemetry.status === "error") detail.append(el("p", "", "The verdict completed, but analytics could not be recorded."));
  } else if (state.resultTab === "references") {
    detail.append(el("p", "", "Semantic matches provide context. Their scores are not a verdict or a confidence estimate."));
    const references = data.retrieved_signatures || [];
    if (!references.length) detail.append(el("p", "", "No related reference patterns were returned."));
    references.forEach((item, i) => {
      const row = el("div", "reference"), heading = el("div", "reference-score");
      heading.append(el("span", "", "REFERENCE " + String(i + 1).padStart(2, "0")), el("span", "", Number(item.score).toFixed(4)));
      row.append(heading, el("p", "", item.text)); detail.append(row);
    });
  } else detail.append(el("pre", "response-pre", JSON.stringify(data, null, 2)));
  target.append(detail);
  updateInputState();
}
function chip(status) {
  const node = el("span", "verdict-chip" + (status === "BLOCK" ? " blocked" : status === "ALLOW" ? "" : " error"));
  node.append(el("span", "tiny-dot"), document.createTextNode(status === "BLOCK" ? "Blocked" : status === "ALLOW" ? "Allowed" : "Error"));
  return node;
}
function table(records, target, compact = false) {
  target.replaceChildren();
  if (state.activityError && !state.records.length) {target.append(el("div", "table-empty", "Stored activity is unavailable. Refresh to retry.")); return;}
  if (!records.length) {target.append(el("div", "table-empty", state.records.length ? "No matching assessments in these recent records." : "Your audit trail starts with the first assessment.")); return;}
  const node = el("table"), head = el("thead"), heading = el("tr");
  ["Run", "Input", "Verdict", "Category", "Latency", "Time · IST", ""].forEach((text) => {const th = el("th", "", text); th.scope = "col"; heading.append(th);});
  head.append(heading); node.append(head);
  const body = el("tbody");
  records.forEach((record) => {
    const row = el("tr");
    row.append(el("td", "mono id-cell", recordId(record.id)));
    const prompt = el("td", "prompt-cell", String(record.prompt || "").replace(/\s+/g, " ")); prompt.title = record.prompt || ""; row.append(prompt);
    const verdict = el("td"); verdict.append(chip(record.status)); row.append(verdict);
    row.append(el("td", "", category(record.category)), el("td", "mono", duration(record.latency_ms)), el("td", "mono", timeLabel(record.timestamp)));
    const action = el("td"), button = el("button", "icon-button"); button.setAttribute("aria-label", "Inspect " + recordId(record.id)); button.title = "Inspect assessment"; button.append(icon("eye")); button.addEventListener("click", () => showRecord(record)); action.append(button); row.append(action);
    body.append(row);
  });
  node.append(body); target.append(node);
}
function renderActivity() {
  $("nav-count").textContent = String(state.records.length);
  $("activity-scope").textContent = state.activityError ? "Stored activity could not be refreshed." : "Latest " + state.records.length + " stored records · timestamps in IST";
  $("export-activity-btn").disabled = !state.records.length;
  const search = state.search.trim().toLowerCase();
  const filtered = state.records.filter((record) => (state.filter === "all" || record.status === state.filter) &&
    (!search || [record.prompt, record.category, recordId(record.id)].some((value) => String(value || "").toLowerCase().includes(search))));
  table(state.records.slice(0, 5), $("recent-table"), true);
  table(filtered, $("activity-table"));
}
function showRecord(record) {
  const target = $("record-dialog-body"); target.replaceChildren();
  $("record-dialog-title").textContent = recordId(record.id);
  const meta = el("div", "record-meta"); meta.append(chip(record.status), el("span", "", category(record.category)), el("span", "mono", duration(record.latency_ms)), el("span", "mono", timeLabel(record.timestamp) + " IST"));
  target.append(meta, el("div", "detail-label", "Submitted input"), el("pre", "", record.prompt || "No input text recorded."));
  target.append(el("div", "detail-label", "Recorded finding"), el("p", "", record.reason || "No finding text recorded."));
  const session = state.sessionRuns.get(String(record.id));
  if (session) target.append(el("div", "detail-label", "Response captured this session"), el("pre", "", JSON.stringify(session.result, null, 2)));
  else target.append(el("p", "card-footnote", "Stored audit record. Detailed evidence, learning IDs, and stage timings are shown for assessments run in this browser session."));
  if (!$("record-dialog").open) $("record-dialog").showModal();
}

function saveJSON(data, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}));
  const link = document.createElement("a"); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function assess(event) {
  event.preventDefault();
  if (state.running) return;
  const body = requestBody();
  if (!body.prompt || new TextEncoder().encode(JSON.stringify(body)).length > state.limits.request_bytes) {updateInputState(); return;}
  if (!state.health || state.health.status !== "ok") {notify("The engines are not ready. Recheck their status first."); return;}
  state.running = true; state.error = null; state.current = null; state.resultTab = "evidence";
  state.sequence += 1;
  const started = performance.now(), mode = state.mode;
  updateInputState(); renderRunning(); renderTiming(null);
  state.timer = setInterval(() => {
    const seconds = Math.floor((performance.now() - started) / 1000);
    if ($("elapsed-clock")) $("elapsed-clock").textContent = String(Math.floor(seconds / 60)).padStart(2, "0") + ":" + String(seconds % 60).padStart(2, "0");
  }, 200);
  try {
    const data = await fetchJSON("/check", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    if (!["ALLOW", "BLOCK"].includes(data.status)) throw new Error(data.reason || "The engine did not return a valid verdict.");
    const run = {
      id: Number.isInteger(data.request_id) ? recordId(data.request_id) : "LOCAL-" + String(state.sequence).padStart(3, "0"),
      prompt: body.prompt, history: body.history, mode, result: data,
      elapsedMs: performance.now() - started, finishedAt: new Date().toISOString()
    };
    state.current = run;
    if (Number.isInteger(data.request_id)) state.sessionRuns.set(String(data.request_id), run);
    if (data.status === "ALLOW" && mode === "conversation") {
      state.history.push({role: "user", content: body.prompt});
      state.history = state.history.slice(-state.limits.history_turns);
    }
    notify("Assessment complete · " + (data.status === "BLOCK" ? "Blocked" : "Allowed"));
    await refreshWorkspace(true);
  } catch (error) {
    state.error = error.message || "The assessment could not be completed.";
    notify("Assessment unavailable. Review the engine response.");
  } finally {
    clearInterval(state.timer); state.timer = null; state.running = false;
    $("evaluate-label").textContent = "Evaluate intent";
    renderResult(); renderTiming(state.current); updateInputState();
  }
}

/*
 * Motion adapted for this plain-JavaScript app from React Bits ClickSpark
 * FadeContent, BorderGlow, SpotlightCard, Magnet and GradientText by David Haz (2026). Source and license: react-bits-LICENSE.txt.
 * https://github.com/DavidHDev/react-bits
 * Native Web Animations replace React/GSAP lifecycle dependencies.
 */
const VelnastraMotion = (() => {
  // The user explicitly enabled site motion; retain an independent off switch.
  const preference = new EventTarget();
  preference.matches = false;
  try {preference.matches = localStorage.getItem("velnastra-motion") === "off";} catch (_) {}
  function syncMotionControl() {
    document.documentElement.classList.toggle("motion-off",preference.matches);
    $("motion-toggle").textContent = preference.matches ? "Motion off" : "Motion on";
    $("motion-toggle").setAttribute("aria-pressed",String(!preference.matches));
  }
  syncMotionControl();
  $("motion-toggle").addEventListener("click",() => {
    preference.matches = !preference.matches;
    syncMotionControl();
    try {localStorage.setItem("velnastra-motion",preference.matches ? "off" : "on");} catch (_) {}
    preference.dispatchEvent(new Event("change"));
    if (!preference.matches) page(state.view);
  });
  const active = new Set();
  const ease = "cubic-bezier(.22,1,.36,1)";
  const seen = new WeakSet();
  let chartKey = "", resultKey = "";
  function animate(node, frames, options = {}) {
    if (!node || preference.matches || !node.getClientRects().length || !node.animate) return;
    const animation = node.animate(frames, {duration:420,easing:ease,...options});
    active.add(animation);
    animation.finished.catch(() => {}).finally(() => active.delete(animation));
    return animation;
  }
  function reveal(node, delay = 0) {
    animate(node, [{opacity:0,transform:"translateY(12px)",filter:"blur(3px)"},{opacity:1,transform:"translateY(0)",filter:"blur(0)"}], {delay,fill:"backwards"});
  }
  function charts(force = false) {
    if (state.view !== "analytics") return;
    const key = JSON.stringify([state.stats,state.records.map(row => [row.id,row.latency_ms])]);
    if (!force && key === chartKey) return;
    chartKey = key;
    animate($("distribution").querySelector(".verdict-donut"), [{opacity:.3,transform:"scale(.9) rotate(-14deg)"},{opacity:1,transform:"scale(1) rotate(0deg)"}],{duration:650});
    document.querySelectorAll("#category-chart .category-fill").forEach((node,i) => {
      node.style.transformOrigin = "left";
      animate(node,[{transform:"scaleX(0)"},{transform:"scaleX(1)"}],{duration:650,delay:i*60,fill:"backwards"});
    });
    document.querySelectorAll("#latency-chart rect").forEach((node,i) => {
      node.style.transformBox = "fill-box"; node.style.transformOrigin = "bottom";
      animate(node,[{transform:"scaleY(0)"},{transform:"scaleY(1)"}],{duration:500,delay:i*12,fill:"backwards"});
    });
  }
  function sweep(card) {
    if (preference.matches) return;
    card.classList.add("glow-sweep");
    const effect = animate(card, [
      {"--cursor-angle":"110deg","--glow-presence":"0"},
      {"--cursor-angle":"200deg","--glow-presence":".8",offset:.25},
      {"--cursor-angle":"420deg","--glow-presence":".8",offset:.7},
      {"--cursor-angle":"465deg","--glow-presence":"0"}
    ], {duration:1200});
    if (effect) effect.finished.catch(()=>{}).finally(()=>card.classList.remove("glow-sweep"));
    else card.classList.remove("glow-sweep");
  }
  function page(view) {
    document.querySelectorAll(".page-view").forEach(node => node.getAnimations().forEach(animation => animation.cancel()));
    reveal($("page-title")); reveal($("page-description"),35);
    const host = $("view-"+view);
    host.querySelectorAll(".card").forEach((node,i) => reveal(node,Math.min(i*55,220)));
    host.querySelectorAll(".card").forEach(sweep);
    animate($("page-title"), [{backgroundPosition:"100% 50%"},{backgroundPosition:"0% 50%"}],{duration:1200});
    charts(true);
  }
  preference.addEventListener("change", () => {
    if (preference.matches) {active.forEach(animation => animation.cancel()); document.querySelectorAll(".click-spark-ray").forEach(node => node.remove());}
  });
  document.addEventListener("click", event => {
    const button = event.target.closest("button,a.brand");
    if (!button || button.disabled || preference.matches || button.classList.contains("polished-action")) return;
    const bounds = button.getBoundingClientRect();
    const x = event.detail ? event.clientX : bounds.left+bounds.width/2;
    const y = event.detail ? event.clientY : bounds.top+bounds.height/2;
    // ClickSpark radial geometry and ease-out decay, shortened for operator UI.
    for (let i=0;i<6;i++) {
      const angle = 2*Math.PI*i/6;
      const ray = document.createElement("span");
      ray.className = "click-spark-ray"; ray.setAttribute("aria-hidden","true");
      ray.style.left=x+"px"; ray.style.top=y+"px"; document.body.append(ray);
      const animation = animate(ray,[
        {opacity:.8,transform:"translate(-50%,-50%) rotate("+angle+"rad) translateX(5px) scaleX(1)"},
        {opacity:0,transform:"translate(-50%,-50%) rotate("+angle+"rad) translateX(25px) scaleX(0)"}
      ],{duration:380,easing:"cubic-bezier(.33,1,.68,1)"});
      if (animation) animation.finished.catch(()=>{}).finally(()=>ray.remove()); else ray.remove();
    }
  });
  // BorderGlow edge proximity and cursor angle, adapted from the official source.
  // Only the current hovered panel receives updates; no idle rendering loop.
  let hoveredCard = null, magnet = null;
  function resetPointer() {
    if (hoveredCard) hoveredCard.style.setProperty("--glow-presence","0");
    if (magnet) {
      magnet.style.removeProperty("--specular-angle");
      magnet.style.setProperty("--magnet-x","0px");
      magnet.style.setProperty("--magnet-y","0px");
    }
    hoveredCard = null; magnet = null;
  }
  document.querySelectorAll(".card,.metric").forEach(card => {
    card.classList.add("border-glow-card");
    const light = document.createElement("span");
    light.className = "edge-light"; light.setAttribute("aria-hidden","true");
    card.append(light);
  });
  document.addEventListener("pointermove", event => {
    if (preference.matches || event.pointerType === "touch") return;
    const card = event.target.closest(".metric,.card");
    const button = event.target.closest(".button-primary:not(.polished-action):not(:disabled)");
    if (hoveredCard && hoveredCard !== card) hoveredCard.style.setProperty("--glow-presence","0");
    hoveredCard = card;
    if (card) {
      const rect = card.getBoundingClientRect();
      const x = event.clientX - rect.left, y = event.clientY - rect.top;
      const cx = rect.width / 2, cy = rect.height / 2;
      const edge = Math.min(1, Math.max(Math.abs(x-cx)/Math.max(cx,1),Math.abs(y-cy)/Math.max(cy,1)));
      const angle = Math.atan2(y-cy,x-cx)*180/Math.PI+90;
      card.style.setProperty("--spot-x",(x/rect.width*100)+"%");
      card.style.setProperty("--spot-y",(y/rect.height*100)+"%");
      card.style.setProperty("--cursor-angle",angle+"deg");
      card.style.setProperty("--glow-presence",String(Math.max(0,(edge-.35)/.65)));
    }
    if (magnet && magnet !== button) {
      magnet.style.removeProperty("--specular-angle");
      magnet.style.setProperty("--magnet-x","0px");
      magnet.style.setProperty("--magnet-y","0px");
    }
    magnet = button;
    if (button) {
      const rect = button.getBoundingClientRect();

      button.style.setProperty("--magnet-x",Math.max(-3,Math.min(3,(event.clientX-rect.left-rect.width/2)/12))+"px");
      button.style.setProperty("--magnet-y",Math.max(-2,Math.min(2,(event.clientY-rect.top-rect.height/2)/8))+"px");
    }
  },{passive:true});
  document.documentElement.addEventListener("pointerleave",resetPointer);
  window.addEventListener("blur",resetPointer);
  preference.addEventListener("change",resetPointer);
  const observer = new MutationObserver(records => {
    let result = false, chart = false, activity = false;
    records.forEach(record => {
      const node = record.target;
      if (node.id === "result-body") result = true;
      if (["distribution","category-chart","latency-chart"].includes(node.id)) chart = true;
      if (["activity-table","recent-table"].includes(node.id)) activity = true;
      if (node.id === "toast" && !node.hidden) animate(node,[{opacity:0,transform:"translate(-50%,10px)"},{opacity:1,transform:"translate(-50%,0)"}],{duration:220});
      if (node.tagName === "DIALOG" && node.open) animate(node,[{opacity:0,transform:"translateY(15px) scale(.98)"},{opacity:1,transform:"translateY(0) scale(1)"}],{duration:240});
    });
    document.body.classList.toggle("assessment-running",state.running);
    if (result) {
      const key = [state.running,state.current?.id,state.resultTab,state.error].join("|");
      if (key !== resultKey) {
        resultKey=key; reveal($("result-body").firstElementChild); reveal($("result-detail"),40);
        const panel = $("result-body").closest(".card"); if (panel) sweep(panel);
      }
    }
    if (chart) charts();
    if (activity) document.querySelectorAll("#activity-table tbody tr,#recent-table tbody tr").forEach((row,i) => reveal(row,Math.min(i,8)*20));
  });
  observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:["open","hidden"]});
  if ("IntersectionObserver" in window) {
    const scrollObserver = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting && !seen.has(entry.target)) {seen.add(entry.target); reveal(entry.target); scrollObserver.unobserve(entry.target);}
    }),{threshold:.1});
    document.querySelectorAll(".lower-grid .card,.recent-card").forEach(node => scrollObserver.observe(node));
  }
  return {page};
})();

$("assessment-form").addEventListener("submit", assess);
$("prompt").addEventListener("input", updateInputState);
$("prompt").addEventListener("scroll", () => {$("line-numbers").scrollTop = $("prompt").scrollTop;});
$("prompt").addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {event.preventDefault(); $("assessment-form").requestSubmit();}
});
$("mode-single").addEventListener("click", () => setMode("single"));
$("mode-conversation").addEventListener("click", () => setMode("conversation"));
$("clear-context-btn").addEventListener("click", () => {state.history = []; updateInputState(); notify("Conversation context cleared.");});
$("new-btn").addEventListener("click", () => {
  if (state.running) return;
  state.history = []; state.current = null; state.error = null; $("prompt").value = "";
  setMode("single"); setView("lab"); renderResult(); renderTiming(null); $("prompt").focus();
});
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
window.addEventListener("hashchange", () => setView(window.location.hash.slice(1), false));
$("view-activity-btn").addEventListener("click", () => setView("activity"));
$("activity-search").addEventListener("input", (event) => {state.search = event.target.value; renderActivity();});
document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
  state.filter = button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach((item) => {
    item.classList.toggle("selected", item === button); item.setAttribute("aria-pressed", String(item === button));
  });
  renderActivity();
}));
$("refresh-btn").addEventListener("click", () => refreshWorkspace(true));
$("health-btn").addEventListener("click", () => refreshWorkspace(true));
$("copy-result-btn").addEventListener("click", async () => {
  if (!state.current) return;
  try {await navigator.clipboard.writeText(JSON.stringify(state.current.result, null, 2)); notify("Assessment response copied.");}
  catch (_) {notify("Clipboard access is unavailable. Use the Response tab to select the text.");}
});
$("export-result-btn").addEventListener("click", () => {
  if (state.current) {saveJSON({exportedAt: new Date().toISOString(), assessment: state.current}, "velnastra-" + state.current.id + ".json"); notify("Assessment exported.");}
});
$("export-activity-btn").addEventListener("click", () => {
  if (state.records.length) {saveJSON({exportedAt: new Date().toISOString(), scope: "Latest " + state.records.length + " stored records", records: state.records}, "velnastra-activity.json"); notify("Recent activity exported.");}
});
$("about-btn").addEventListener("click", () => {$("about-dialog").showModal();});
$("close-about-btn").addEventListener("click", () => $("about-dialog").close());
$("close-record-btn").addEventListener("click", () => $("record-dialog").close());
["about-dialog", "record-dialog"].forEach((id) => $(id).addEventListener("click", (event) => {
  const bounds = $(id).getBoundingClientRect();
  if (event.target === $(id) && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) $(id).close();
}));
document.addEventListener("visibilitychange", () => {if (document.visibilityState === "visible") refreshWorkspace();});
setInterval(() => {if (document.visibilityState === "visible" && !state.running) refreshWorkspace();}, 30000);
setView(window.location.hash.slice(1) || "lab", false);
renderTiming(null); renderStats(); renderActivity(); updateInputState();
refreshWorkspace();
