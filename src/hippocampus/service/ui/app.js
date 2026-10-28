// DocuSync dashboard (HIPPO-E05); the folder drop is in sync.js and its rules in folder.js. Everything from the API or the crawled web is untrusted:
// it is only ever written with textContent, never innerHTML.
"use strict";

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "hippo-token";
const NEW = "__new__";
let collections = [];
let stream = null; // AbortController of the running job-log stream
let me = { name: "anonymous", admin: false, read: [], write: [] };

// The token lives in sessionStorage only (cleared with the tab); storage can be blocked, so every access is guarded.
function getToken() { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return ""; } }
function setToken(value) {
  try { if (value) sessionStorage.setItem(TOKEN_KEY, value); else sessionStorage.removeItem(TOKEN_KEY); } catch (_) { /* ignore */ }
}
// X-Hippo-Token, not Authorization: the reverse proxy in front of the dashboard may set its own Authorization header.
const authHeaders = () => (getToken() ? { "X-Hippo-Token": getToken() } : {});

function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids) node.append(kid);
  return node;
}

function banner(message) {
  $("banner").hidden = !message;
  $("banner").textContent = message || "";
}

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, { credentials: "same-origin", redirect: "manual", ...options,
      headers: { "Content-Type": "application/json", ...authHeaders(), ...(options.headers || {}) } });
  } catch (err) {
    throw new Error("Network error. If this persists, reload the page to sign in again.");
  }
  if (res.type === "opaqueredirect") {
    throw new Error("Session expired. Reload the page to sign in again.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const body = await res.json(); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch (_) { /* not JSON */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString() : "");
const fmtNum = (n) => Number(n).toLocaleString();

// --- status pills ---

async function loadStatus() {
  const box = $("pills");
  box.replaceChildren();
  const pill = (text, cls = "") => box.append(el("span", { class: `pill ${cls}` }, text));
  try {
    const s = await api("/v1/status");
    pill(`DB ok ${s.db_latency_ms} ms`, "ok");
    pill(`${s.embedder.model} (${s.embedder.dimensions}d)`);
    pill(`${fmtNum(s.collections)} collections`);
    pill(`${fmtNum(s.sources)} sources`);
    pill(`${fmtNum(s.chunks)} chunks`);
    pill(`${s.active_jobs} active job${s.active_jobs === 1 ? "" : "s"}`, s.active_jobs ? "ok" : "");
    banner("");
  } catch (err) {
    pill("API unreachable", "bad");
    banner(err.message);
  }
}

// --- collections ---

async function loadCollections() {
  try {
    collections = (await api("/v1/collections")).collections;
  } catch (err) {
    banner(err.message);
    return;
  }
  const body = $("collections");
  body.replaceChildren();
  if (!collections.length) {
    body.append(el("tr", {}, el("td", { colspan: 7, class: "muted" }, "No collections you can see yet. Start a crawl and create one.")));
  }
  for (const c of collections) {
    body.append(el("tr", {},
      el("td", { class: "mono" }, c.slug),
      el("td", {}, c.name),
      el("td", {}, el("span", { class: `badge ${c.visibility}` }, c.visibility)),
      el("td", { class: "num" }, fmtNum(c.source_count)),
      el("td", { class: "num" }, fmtNum(c.chunk_count)),
      el("td", { class: "muted" }, fmtDate(c.created_at)),
      el("td", { class: "right" },
        el("button", { onclick: () => showSources(c.slug) }, "Sources"), " ",
        el("button", { onclick: () => openSearch(c.slug) }, "Search"), " ",
        ...(me.admin ? [el("button", { onclick: () => toggleVisibility(c) },
          c.visibility === "shared" ? "Restrict" : "Share"), " "] : []),
        el("button", { class: "danger", onclick: () => deleteCollection(c) }, "Delete"))));
  }
  fillSelect($("collection"), true);
  fillSelect($("searchScope"), false);
  fillFolderCollections();
}

function fillSelect(select, withNew) {
  const previous = select.value;
  select.replaceChildren();
  if (!withNew) select.append(el("option", { value: "" }, "All collections"));
  for (const c of collections) select.append(el("option", { value: c.slug }, `${c.slug} (${c.name})`));
  if (withNew) select.append(el("option", { value: NEW }, "New collection..."));
  if ([...select.options].some((o) => o.value === previous)) select.value = previous;
  else if (withNew && !collections.length) select.value = NEW;
  if (withNew) $("newCollection").hidden = select.value !== NEW;
}

async function toggleVisibility(c) {
  const to = c.visibility === "shared" ? "restricted" : "shared";
  const warn = to === "shared" ? `Make "${c.slug}" visible to every client?` : `Restrict "${c.slug}" to clients with a grant?`;
  if (!confirm(warn)) return;
  try {
    await api(`/v1/collections/${encodeURIComponent(c.slug)}`, { method: "PATCH", body: JSON.stringify({ visibility: to }) });
    banner("");
    await refreshAll();
  } catch (err) { banner(err.message); }
}

async function deleteCollection(c) {
  if (!confirm(`Delete collection "${c.slug}" and all ${fmtNum(c.chunk_count)} chunks? This cannot be undone.`)) return;
  try {
    await api(`/v1/collections/${encodeURIComponent(c.slug)}`, { method: "DELETE" });
    if ($("sourcesTitle").textContent === c.slug) $("sourcesSection").hidden = true;
    await refreshAll();
  } catch (err) { banner(err.message); }
}

async function showSources(slug) {
  try {
    const { sources } = await api(`/v1/collections/${encodeURIComponent(slug)}/sources?limit=500`);
    $("sourcesTitle").textContent = slug;
    const body = $("sources");
    body.replaceChildren();
    if (!sources.length) body.append(el("tr", {}, el("td", { colspan: 6, class: "muted" }, "No sources indexed.")));
    for (const s of sources) {
      body.append(el("tr", {},
        el("td", { class: "uri mono" }, s.uri),
        el("td", {}, s.title || ""),
        el("td", {}, el("span", { class: `badge ${s.status}` }, s.status)),
        el("td", { class: "num" }, fmtNum(s.chunk_count)),
        el("td", { class: "mono muted" }, s.sha256),
        el("td", { class: "muted" }, fmtDate(s.last_indexed_at))));
    }
    $("sourcesSection").hidden = false;
    $("sourcesSection").scrollIntoView({ behavior: "smooth" });
  } catch (err) { banner(err.message); }
}

// --- jobs ---

function setJob(status) {
  $("jobStatus").textContent = status;
  $("jobStatus").className = `badge ${status}`;
}

function logLine(text) {
  const log = $("log");
  log.append(`[${new Date().toLocaleTimeString()}] ${text}\n`);
  log.scrollTop = log.scrollHeight;
}

// EventSource cannot send an Authorization header, so the log is read with fetch and parsed as server-sent events.
async function watchJob(jobId, label) {
  if (stream) stream.abort();
  const ctl = (stream = new AbortController());
  $("jobSection").hidden = false;
  $("jobSection").scrollIntoView({ behavior: "smooth", block: "nearest" });
  $("log").textContent = "";
  $("jobMeta").textContent = `${label} - job ${jobId}`;
  setJob("running");
  let ended = false;
  try {
    const res = await fetch(`/v1/jobs/${encodeURIComponent(jobId)}/events`,
      { credentials: "same-origin", redirect: "manual", headers: authHeaders(), signal: ctl.signal });
    if (!res.ok || !res.body) throw new Error(`log stream failed (${res.status})`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const isEnd = block.startsWith("event: end");
        const data = block.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
        if (isEnd) ended = true; else if (data) logLine(data);
      }
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    logLine(err.message);
  }
  if (stream === ctl) stream = null;
  try {
    const job = await api(`/v1/jobs/${encodeURIComponent(jobId)}`);
    setJob(job.status);
    if (job.error) logLine(`ERROR ${job.error}`);
    if (job.result) logLine(`result ${JSON.stringify(job.result)}`);
  } catch (err) { logLine(err.message); }
  if (!ended) logLine("stream closed before the job finished");
  await refreshAll();
}

async function startCrawl(event) {
  event.preventDefault();
  const btn = $("crawlBtn");
  btn.disabled = true;
  try {
    let slug = $("collection").value;
    if (slug === NEW) {
      slug = $("newSlug").value.trim();
      const name = $("newName").value.trim() || slug;
      await api("/v1/collections", { method: "POST", body: JSON.stringify({ slug, name, visibility: $("newVisibility").value }) });
    }
    const body = {
      url: $("url").value.trim(), collection: slug, depth: Number($("depth").value), engine: $("engine").value,
      match_pattern: $("pattern").value.trim() || null,
    };
    const job = await api("/v1/crawl", { method: "POST", body: JSON.stringify(body) });
    banner("");
    watchJob(job.job_id, `crawl ${body.url} -> ${slug}`);
    await loadCollections();
    $("collection").value = slug; // the collection now exists: do not try to create it again on the next crawl
    $("newCollection").hidden = true;
    $("newSlug").required = false;
  } catch (err) { banner(err.message); }
  finally { btn.disabled = false; }
}

async function resyncDocs() {
  try {
    const job = await api("/v1/sync/docs", { method: "POST" });
    banner("");
    watchJob(job.job_id, "Docs repo resync");
  } catch (err) { banner(err.message); }
}

// --- search sandbox ---

function openSearch(slug) {
  showTab("search");
  if (slug !== undefined) $("searchScope").value = slug;
  $("query").focus();
}

async function runSearch(event) {
  event.preventDefault();
  const scope = $("searchScope").value;
  const results = $("results");
  $("searchStats").textContent = "Searching...";
  results.replaceChildren();
  const t0 = performance.now();
  try {
    const res = await api("/v1/search", { method: "POST", body: JSON.stringify({
      query: $("query").value, collections: scope ? [scope] : [], top_k: Number($("topK").value) || 5 }) });
    $("searchStats").textContent = `${res.results.length} result${res.results.length === 1 ? "" : "s"} in ${Math.round(performance.now() - t0)} ms (cosine)`;
    for (const h of res.results) {
      results.append(el("div", { class: "hit" },
        el("div", { class: "meta" },
          el("span", { class: "score" }, `score ${h.score.toFixed(3)}`),
          el("span", { class: "mono" }, `${h.collection} | ${h.title || h.uri}`)),
        el("div", { class: "meta mono" }, [h.uri, h.header_path].filter(Boolean).join("  #  ")),
        el("pre", {}, h.content)));
    }
    banner("");
  } catch (err) {
    $("searchStats").textContent = "";
    banner(err.message);
  }
}

// --- access: token, identity, clients ---

const grants = (text) => text.split(",").map((g) => g.trim()).filter(Boolean);

async function loadWho() {
  try {
    me = await api("/v1/whoami");
  } catch (err) {
    me = { name: "anonymous", admin: false, read: [], write: [] };
    banner(err.message);
  }
  $("who").textContent = me.name === "anonymous" ? "anonymous" : `${me.name}${me.admin ? " (admin)" : ""}`;
  $("whoDetail").textContent = me.name === "anonymous"
    ? "No token: shared collections only."
    : `${me.name}: ${me.admin ? "admin, sees everything" : `read ${me.read.join(", ") || "-"}; write ${me.write.join(", ") || "-"}`}`;
  $("clientsBox").hidden = !me.admin;
  if (me.admin) await loadClients();
}

async function loadClients() {
  const body = $("clients");
  try {
    const { clients } = await api("/v1/clients");
    body.replaceChildren();
    if (!clients.length) body.append(el("tr", {}, el("td", { colspan: 6, class: "muted" }, "No clients.")));
    for (const c of clients) {
      body.append(el("tr", {},
        el("td", { class: "mono" }, c.name, c.revoked ? el("span", { class: "badge error" }, "revoked") : ""),
        el("td", {}, c.admin ? "yes" : ""),
        el("td", { class: "mono" }, c.read.join(", ")),
        el("td", { class: "mono" }, c.write.join(", ")),
        el("td", { class: "muted" }, c.last_used_at ? fmtDate(c.last_used_at) : "never"),
        el("td", { class: "right" }, ...(c.revoked ? [] : [
          el("button", { onclick: () => editClient(c) }, "Edit grants"), " ",
          el("button", { onclick: () => rotateClient(c) }, "Rotate token"), " ",
          el("button", { class: "danger", onclick: () => revokeClient(c) }, "Revoke")]))));
    }
  } catch (err) { banner(err.message); }
}

async function createClient(event) {
  event.preventDefault();
  try {
    const made = await api("/v1/clients", { method: "POST", body: JSON.stringify({
      name: $("clientName").value.trim(), admin: $("clientAdmin").checked, read: grants($("clientRead").value),
      write: grants($("clientWrite").value), note: $("clientNote").value.trim() || null }) });
    $("clientForm").reset();
    showTokenOnce(made.name, made.token);
    banner("");
    await loadClients();
  } catch (err) { banner(err.message); }
}

function showTokenOnce(name, token, note = "") {
  $("tokenOnceName").textContent = name;
  $("tokenOnceValue").textContent = token;
  $("tokenOnceNote").textContent = note;
  $("tokenOnce").hidden = false;
}

async function rotateClient(c) {
  if (!confirm(`Rotate the token for "${c.name}"? The current token stops working immediately, so update everything that uses it.`)) return;
  try {
    const made = await api(`/v1/clients/${encodeURIComponent(c.name)}/rotate`, { method: "POST" });
    let note = "The old token no longer works. Store this one now.";
    if (c.name === me.name) { // rotating the token this tab is using: keep the dashboard working with the new one
      setToken(made.token);
      note += " This tab is now using the new token.";
    }
    showTokenOnce(made.name, made.token, note);
    banner("");
    await refreshAll();
  } catch (err) { banner(err.message); }
}

async function editClient(c) {
  const read = prompt(`Read grants for ${c.name} (comma separated; empty for none)`, c.read.join(", "));
  if (read === null) return;
  const write = prompt(`Write grants for ${c.name}`, c.write.join(", "));
  if (write === null) return;
  try {
    await api(`/v1/clients/${encodeURIComponent(c.name)}`, { method: "PATCH", body: JSON.stringify({ read: grants(read), write: grants(write) }) });
    banner("");
    await refreshAll();
  } catch (err) { banner(err.message); }
}

async function revokeClient(c) {
  if (!confirm(`Revoke "${c.name}"? Its token stops working immediately.`)) return;
  try {
    await api(`/v1/clients/${encodeURIComponent(c.name)}`, { method: "DELETE" });
    banner("");
    await refreshAll();
  } catch (err) { banner(err.message); }
}

function useToken(event) {
  event.preventDefault();
  setToken($("token").value.trim());
  $("token").value = "";
  refreshAll();
}

// --- tabs: Collections, Search, Local, Web, Access, API ---

const TABS = ["collections", "search", "local", "web", "access", "api"];

function showTab(name, focus = false, fromHash = false) {
  if (!TABS.includes(name)) name = "collections";
  for (const t of TABS) {
    const on = t === name;
    $(`panel-${t}`).hidden = !on;
    $(`tab-${t}`).setAttribute("aria-selected", String(on));
    $(`tab-${t}`).tabIndex = on ? 0 : -1;
  }
  if (name === "api" && !$("apiFrame").getAttribute("src")) $("apiFrame").src = "api.html"; // the 1.7 MB bundle loads on first use
  if (focus) $(`tab-${name}`).focus();
  if (!fromHash && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
}

// --- wiring ---

async function refreshAll() { await loadWho(); await Promise.all([loadStatus(), loadCollections(), loadSavedSources()]); }

$("crawlForm").addEventListener("submit", startCrawl);
$("collection").addEventListener("change", () => { $("newCollection").hidden = $("collection").value !== NEW; $("newSlug").required = $("collection").value === NEW; });
$("searchForm").addEventListener("submit", runSearch);
$("closeSources").addEventListener("click", () => { $("sourcesSection").hidden = true; });
$("refresh").addEventListener("click", refreshAll);
$("openAccess").addEventListener("click", () => showTab("access", true));
for (const tab of document.querySelectorAll("[role=tab]")) {
  tab.addEventListener("click", () => showTab(tab.dataset.tab));
  tab.addEventListener("keydown", (e) => { // arrow keys move between tabs, as in the WAI-ARIA tabs pattern
    const names = TABS, i = names.indexOf(tab.dataset.tab);
    const to = { ArrowRight: names[(i + 1) % names.length], ArrowLeft: names[(i + names.length - 1) % names.length],
      Home: names[0], End: names[names.length - 1] }[e.key];
    if (to) { e.preventDefault(); showTab(to, true); }
  });
}
window.addEventListener("hashchange", () => showTab(location.hash.slice(1), false, true));
$("tokenForm").addEventListener("submit", useToken);
$("clearToken").addEventListener("click", () => { setToken(""); $("tokenOnce").hidden = true; refreshAll(); });
$("clientForm").addEventListener("submit", createClient);
$("dismissToken").addEventListener("click", () => { $("tokenOnce").hidden = true; $("tokenOnceValue").textContent = ""; $("tokenOnceNote").textContent = ""; });
$("resyncDocs").addEventListener("click", resyncDocs);

initFolderSync();
showTab(location.hash.slice(1), false, true);
refreshAll();
setInterval(loadStatus, 15000);
