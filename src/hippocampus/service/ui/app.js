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
    $("crawlAckRow").hidden = !s.crawl_require_ack; // HIPPO_CRAWL_REQUIRE_ACK: a tick before a crawl; off, only the notice shows
    $("crawlAck").required = !!s.crawl_require_ack;
    $("resyncDocs").hidden = !s.docs_sync; // the docs sync is optional: show its button only when it is configured
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

const SOURCE_LABELS = { web: "Web", local: "Local", repo: "Repo", other: "Other" };

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
    body.append(el("tr", {}, el("td", { colspan: 8, class: "muted" }, "No collections you can see yet. Start a crawl and create one.")));
  }
  const only = $("sourceFilter").value;
  for (const c of collections) {
    if (only && !(c.source_types || []).includes(only)) continue;
    body.append(el("tr", { class: "clickable", title: "Open sources and settings", onclick: (e) => { if (!e.target.closest("button")) showSources(c.slug); } },
      el("td", { class: "mono" }, c.slug),
      el("td", {}, c.name),
      el("td", {}, el("span", { class: `badge ${c.visibility}` }, c.visibility)),
      el("td", {}, ...(c.source_types || []).flatMap((t) => [el("span", { class: "badge" }, SOURCE_LABELS[t] || t), " "])),
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
    if (view.slug === c.slug) $("sourcesSection").hidden = true;
    await refreshAll();
  } catch (err) { banner(err.message); }
}

// The collection view: every source (any type) with filter, per-row and bulk remove, and the saved web crawls above them.
const view = { slug: "", sources: [], selected: new Set() };
const REMOVE_NOTES = {
  web: "A removed web page is fetched again by the next crawl that reaches it.",
  local: "A removed local file is indexed again by the next sync of its folder unless an exclude rule skips it (Local tab).",
  repo: "A removed repository file returns the next time that repository is indexed.",
};

async function showSources(slug, scroll = true) {
  try {
    const c = collections.find((x) => x.slug === slug);
    const [{ sources }, { web_sources }] = await Promise.all([
      api(`/v1/collections/${encodeURIComponent(slug)}/sources?limit=1000`),
      api("/v1/web-sources").catch(() => ({ web_sources: [] })),
    ]);
    if (view.slug !== slug) { $("acSiteCheck").hidden = true; view.selected = new Set(); $("srcType").value = ""; $("srcFilter").value = ""; }
    view.slug = slug;
    view.sources = sources;
    view.selected = new Set([...view.selected].filter((u) => sources.some((s) => s.uri === u)));
    $("sourcesTitle").textContent = slug;
    $("collectionSummary").textContent = c
      ? `${c.name} - ${c.visibility} - ${fmtNum(c.source_count)} sources, ${fmtNum(c.chunk_count)} chunks` : "";
    const web = web_sources.filter((w) => w.collection === slug);
    const list = $("webList");
    list.replaceChildren();
    if (web.length) {
      list.append(el("h3", {}, `Web crawls (${web.length})`),
        el("p", { class: "muted" }, "A re-crawl adds new pages and skips unchanged ones. A lower depth does not remove pages already indexed: " +
          "use Remove deeper pages. Pages crawled before depths were recorded have no depth and are never removed that way."));
      for (const w of web) list.append(webCard(w));
    }
    renderSources();
    prefillAddCrawl(sources, web.length > 0);
    $("sourcesSection").hidden = false;
    if (scroll) $("sourcesSection").scrollIntoView({ behavior: "smooth" });
  } catch (err) { banner(err.message); }
}

// A collection crawled before settings were saved has pages but no saved crawl: offer the form open, with a guess from its pages.
function prefillAddCrawl(sources, hasSaved) {
  const webPages = sources.filter((s) => s.type === "web").map((s) => s.uri).sort((a, b) => a.length - b.length || a.localeCompare(b));
  const box = $("addCrawl");
  box.open = !hasSaved && webPages.length > 0;
  if (view.prefilled === view.slug) return; // keep what the user typed while the view refreshes
  view.prefilled = view.slug;
  $("acUrl").value = webPages[0] || "";
  let path = "";
  try { path = new URL(webPages[0]).pathname.replace(/[^/]*$/, ""); } catch (_) { /* no web page yet */ }
  $("acPattern").value = path.length > 1 ? path : "";
}

async function addCrawl(event) {
  event.preventDefault();
  if ($("crawlAck").required && !confirm("Do you have the right to copy this content?")) return;
  try {
    const body = { url: $("acUrl").value.trim(), collection: view.slug, depth: Number($("acDepth").value), engine: $("acEngine").value,
      match_pattern: $("acPattern").value.trim() || null };
    const job = await api("/v1/crawl", { method: "POST", body: JSON.stringify(body) });
    banner("");
    watchJob(job.job_id, `crawl ${body.url} -> ${view.slug}`);
  } catch (err) { banner(err.message); }
}

// Re-read the open collection's rows and totals without rebuilding the crawl cards (the user may be editing one).
async function refreshSourceRows() {
  if (!view.slug || $("sourcesSection").hidden) return;
  const slug = view.slug;
  try {
    const { sources } = await api(`/v1/collections/${encodeURIComponent(slug)}/sources?limit=1000`);
    if (view.slug !== slug) return;
    view.sources = sources;
    renderSources();
    await loadCollections();
    const c = collections.find((x) => x.slug === slug);
    if (c) $("collectionSummary").textContent = `${c.name} - ${c.visibility} - ${fmtNum(c.source_count)} sources, ${fmtNum(c.chunk_count)} chunks`;
  } catch (_) { /* the final refresh when the job ends reports any real problem */ }
}

function renderSources() {
  const type = $("srcType").value;
  const text = $("srcFilter").value.trim().toLowerCase();
  const shown = view.sources.filter((s) => (!type || s.type === type) &&
    (!text || s.uri.toLowerCase().includes(text) || (s.title || "").toLowerCase().includes(text)));
  const body = $("sources");
  body.replaceChildren();
  if (!shown.length) body.append(el("tr", {}, el("td", { colspan: 9, class: "muted" }, view.sources.length ? "No source matches the filter." : "No sources indexed.")));
  for (const s of shown) {
    const box = el("input", { type: "checkbox", "aria-label": `Select ${s.uri}` });
    box.checked = view.selected.has(s.uri);
    box.addEventListener("change", () => { if (box.checked) view.selected.add(s.uri); else view.selected.delete(s.uri); updateRemove(shown); });
    body.append(el("tr", {},
      el("td", {}, box),
      el("td", { class: "uri mono" }, s.uri),
      el("td", {}, s.title || ""),
      el("td", {}, el("span", { class: "badge" }, SOURCE_LABELS[s.type] || s.type)),
      el("td", { class: "num" }, s.crawl_depth ? String(s.crawl_depth) : ""),
      el("td", {}, el("span", { class: `badge ${s.status}` }, s.status)),
      el("td", { class: "num" }, fmtNum(s.chunk_count)),
      el("td", { class: "muted" }, fmtDate(s.last_indexed_at)),
      el("td", { class: "right" }, el("button", { class: "danger", onclick: () => removeSources([s.uri]) }, "Remove"))));
  }
  $("srcCount").textContent = shown.length === view.sources.length ? `${fmtNum(shown.length)} sources` : `${fmtNum(shown.length)} of ${fmtNum(view.sources.length)} shown`;
  const kinds = [...new Set(view.sources.map((s) => s.type))].filter((t) => REMOVE_NOTES[t]);
  $("removeNote").textContent = kinds.map((t) => REMOVE_NOTES[t]).join(" ");
  $("removeNote").hidden = !kinds.length;
  updateRemove(shown);
}

function updateRemove(shown) {
  $("removeSelected").disabled = !view.selected.size;
  $("removeSelected").textContent = view.selected.size ? `Remove selected (${view.selected.size})` : "Remove selected";
  const all = $("srcAll");
  all.checked = shown.length > 0 && shown.every((s) => view.selected.has(s.uri));
  all._shown = shown;
}

async function removeSources(uris) {
  const what = uris.length === 1 ? uris[0] : `${fmtNum(uris.length)} sources`;
  if (!confirm(`Remove ${what} from "${view.slug}" with its chunks? This cannot be undone.`)) return;
  try {
    const out = await api(`/v1/collections/${encodeURIComponent(view.slug)}/sources`, { method: "DELETE", body: JSON.stringify({ uris }) });
    banner("");
    uris.forEach((u) => view.selected.delete(u));
    await loadCollections();
    await showSources(view.slug);
    if (out.removed !== uris.length) banner(`${out.removed} of ${uris.length} existed and were removed.`);
  } catch (err) { banner(err.message); }
}

function webCard(w) {
  const depth = el("select", {}, ...[1, 2, 3, 4, 5].map((n) => el("option", { value: String(n) }, n === 1 ? "1 (start page only)" : String(n))));
  depth.value = String(w.depth);
  const pattern = el("input", { type: "text", placeholder: "/docs/" });
  pattern.value = w.match_pattern || "";
  const engine = el("select", {}, el("option", { value: "static" }, "static (httpx)"), el("option", { value: "browser" }, "browser (Chromium)"));
  engine.value = w.engine;
  const last = w.last_run_at
    ? `last crawl ${fmtDate(w.last_run_at)}: ${w.last_status}` + (w.last_status === "error" ? ` (${(w.last_summary || {}).error || ""})` : "")
    : "not crawled since it was saved";
  const save = async () => {
    await api(`/v1/web-sources/${w.id}`, { method: "PATCH", body: JSON.stringify({ depth: Number(depth.value), match_pattern: pattern.value.trim(), engine: engine.value }) });
  };
  const run = (fn) => async () => { try { banner(""); await fn(); } catch (err) { banner(err.message); } };
  return el("div", { class: "webcard" },
    el("div", { class: "mono" }, w.url),
    el("div", { class: "muted" }, `${fmtNum(w.pages)} pages with a recorded depth` + (w.deepest ? `, deepest at level ${w.deepest}` : "") + `; ${last}`),
    el("form", { onsubmit: (e) => e.preventDefault() },
      el("label", {}, "Depth", depth), el("label", {}, "Engine", engine),
      el("label", { class: "wide" }, "URL match pattern (regex, optional)", pattern),
      el("div", { class: "actions" },
        el("button", { type: "button", onclick: run(async () => { await save(); await showSources(w.collection); }) }, "Save settings"),
        el("button", { type: "button", class: "primary", onclick: run(async () => {
          await save();
          const job = await api(`/v1/web-sources/${w.id}/crawl`, { method: "POST" });
          watchJob(job.job_id, `re-crawl ${w.url} -> ${w.collection}`);
        }) }, "Save and re-crawl"),
        el("button", { type: "button", onclick: run(async () => {
          const n = Number(depth.value);
          const { pages } = await api(`/v1/web-sources/${w.id}/prune`, { method: "POST", body: JSON.stringify({ depth: n, dry_run: true }) });
          if (!pages) { banner(`No pages were found deeper than level ${n}. Pages crawled before depths were recorded are never removed.`); return; }
          if (!confirm(`Remove ${fmtNum(pages)} pages found deeper than level ${n} from "${w.collection}"? A page that another crawl of this collection reached earlier is also removed if this crawl ran last. This cannot be undone.`)) return;
          await api(`/v1/web-sources/${w.id}/prune`, { method: "POST", body: JSON.stringify({ depth: n, dry_run: false }) });
          await refreshAll();
          await showSources(w.collection);
        }) }, "Remove deeper pages"),
        el("button", { type: "button", class: "danger", onclick: run(async () => {
          if (!confirm(`Forget these crawl settings? The ${fmtNum(w.pages)} pages stay in "${w.collection}".`)) return;
          await api(`/v1/web-sources/${w.id}`, { method: "DELETE" });
          await showSources(w.collection);
        }) }, "Forget settings"))));
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
        if (/^embedded \d+\/\d+ pages/.test(data)) refreshSourceRows(); // pages are stored as they are embedded: show them arriving
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
  if (view.slug && !$("sourcesSection").hidden) await showSources(view.slug, false); // the open collection view shows the new pages
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

// Check site: the read-only probe (robots.txt, JavaScript need, size, suggested settings) before anything is crawled.
// ``f`` names the form's fields: {url, pattern, depth, engine, box, btn} are element ids; ``pickCollection`` lets the Web tab
// switch its collection menu to one that already holds the host.
const WEB_FORM = { url: "url", pattern: "pattern", depth: "depth", engine: "engine", box: "siteCheck", btn: "checkSite", pickCollection: true };
const VIEW_FORM = { url: "acUrl", pattern: "acPattern", depth: "acDepth", engine: "acEngine", box: "acSiteCheck", btn: "acCheckSite" };

async function checkSite(f = WEB_FORM) {
  const url = $(f.url).value.trim();
  const box = $(f.box);
  if (!url) { banner("Enter a start URL first."); return; }
  const btn = $(f.btn);
  btn.disabled = true;
  box.hidden = false;
  box.textContent = "Checking the site (up to four requests)...";
  try {
    const r = await api("/v1/crawl/probe", { method: "POST", body: JSON.stringify({ url, match_pattern: $(f.pattern).value.trim() || null }) });
    banner("");
    const s = r.suggest;
    const page = r.start_page || {};
    const facts = [
      `robots.txt: ${r.robots.found ? (r.robots.allowed ? "allows this path" : "DISALLOWS this path") : "not found"}` +
        (r.robots.crawl_delay ? `, Crawl-delay ${r.robots.crawl_delay} s (the crawler ignores it)` : "") +
        (r.robots.content_signal ? `, Content-Signal: ${r.robots.content_signal}` : ""),
      page.fetched ? `start page: HTTP ${page.status}, ${page.words} words, ${page.same_host_links} links on this host` : "start page: not fetched",
      r.sitemap.found && !r.sitemap.index ? `sitemap: ${r.sitemap.matching} matching of ${r.sitemap.urls} URLs` : "sitemap: size unknown",
      s.pages_estimate ? `estimate: about ${s.pages_estimate} pages, roughly ${Math.max(1, Math.round(s.fetch_seconds / 60))} min to fetch, plus embedding time (cap ${s.max_pages})` : `cap ${s.max_pages} pages`,
    ];
    box.replaceChildren(
      el("strong", {}, "Site check"),
      el("ul", {}, ...facts.map((f) => el("li", {}, f))),
      ...(r.already_indexed.length ? [el("p", { class: "warn" },
        `Already indexed in: ${r.already_indexed.map((h) => `${h.collection} (${fmtNum(h.sources)} pages)`).join(", ")}. A re-crawl into it skips unchanged pages.`)] : []),
      ...(r.warnings.length ? [el("ul", { class: "warn" }, ...r.warnings.map((w) => el("li", {}, w)))] : []),
      el("p", { class: "muted" }, `Suggested: depth ${s.depth} (${s.depth_basis}), engine ${s.engine}` + (s.match_pattern ? `, pattern ${s.match_pattern}` : "")),
      el("div", { class: "actions" }, el("button", { type: "button", onclick: () => applySuggestion(s, r, f) }, "Use suggested settings")));
  } catch (err) {
    // next to the button, where the user is looking; the banner at the top of the page may be out of view
    box.replaceChildren(el("strong", {}, "Site check failed"), el("p", { class: "warn" }, err.message));
  } finally { btn.disabled = false; }
}

function applySuggestion(s, r, f) {
  if ([...$(f.depth).options].some((o) => o.value === String(s.depth))) $(f.depth).value = String(s.depth);
  $(f.engine).value = s.engine;
  if (s.match_pattern) $(f.pattern).value = s.match_pattern;
  if (f.pickCollection) {
    if (r.already_indexed.length && $("collection").value === NEW) $("collection").value = r.already_indexed[0].collection;
    $("newCollection").hidden = $("collection").value !== NEW;
  }
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
$("addCrawlForm").addEventListener("submit", addCrawl);
$("srcType").addEventListener("change", renderSources);
$("srcFilter").addEventListener("input", renderSources);
$("srcAll").addEventListener("change", () => {
  for (const s of $("srcAll")._shown || []) { if ($("srcAll").checked) view.selected.add(s.uri); else view.selected.delete(s.uri); }
  renderSources();
});
$("removeSelected").addEventListener("click", () => removeSources([...view.selected]));
$("collectionSearch").addEventListener("click", () => openSearch(view.slug));
$("checkSite").addEventListener("click", () => checkSite(WEB_FORM));
$("acCheckSite").addEventListener("click", () => checkSite(VIEW_FORM));
$("sourceFilter").addEventListener("change", loadCollections);
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
