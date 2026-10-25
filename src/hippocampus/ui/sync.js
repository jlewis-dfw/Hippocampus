// Folder indexing in the dashboard: drop or choose a folder, pick root folders, set rules, preview, upload in batches,
// and keep saved sources. Loaded before app.js and started from it (initFolderSync), so $, el, api, banner and the
// collection list come from app.js. File names and contents are untrusted: they are only ever written with textContent.
"use strict";

const SLUG_OK = /^[a-z0-9][a-z0-9-]{0,63}$/;
const HARD_PRUNE = new Set([".git", "node_modules", ".svn", ".hg"]); // never worth walking into, whatever the rules say
const MAX_SCAN_FILES = 50000;
const MAX_ELIGIBLE_FILES = 20000; // the server's limit for one run
const BATCH_FILES = 200;
const BATCH_CHARS = 4000000; // well under the server's 20 MB per request
const HF = globalThis.HippoFolder;

const fs = {
  root: "",            // the folder's name
  files: [],           // {path, size, getFile}
  gitignore: "",       // text of the folder's own .gitignore, if it has one
  pruned: 0,           // folders not walked (.git, node_modules ...)
  handle: null,        // a FileSystemDirectoryHandle (Chromium) kept for one-click re-sync
  source: null,        // the saved source being refreshed, if any
  tree: null,          // the folders and files of the scan, built once
  running: false,
  cancel: false,
  sessions: new Set(), // open upload sessions, so a cancel can close them
};

const slugify = (text) => text.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
const plural = (n, word) => `${fmtNum(n)} ${word}${n === 1 ? "" : "s"}`;

// --- reading a folder: drop (entries), choose (webkitdirectory), or a directory handle (Chromium) ---

function readAllEntries(reader) {
  return new Promise((resolve, reject) => {
    const all = [];
    const next = () => reader.readEntries((batch) => { if (!batch.length) resolve(all); else { all.push(...batch); next(); } }, reject);
    next();
  });
}

const fileOf = (entry) => new Promise((resolve, reject) => entry.file(resolve, reject));

async function walkEntry(entry, prefix, out, state) {
  if (out.length >= MAX_SCAN_FILES) { state.truncated = true; return; }
  if (entry.isFile) {
    const file = await fileOf(entry);
    out.push({ path: prefix + entry.name, size: file.size, getFile: async () => file });
    return;
  }
  if (HARD_PRUNE.has(entry.name)) { state.pruned++; return; }
  for (const child of await readAllEntries(entry.createReader())) await walkEntry(child, `${prefix}${entry.name}/`, out, state);
}

async function walkHandle(dir, prefix, out, state) {
  for await (const [name, h] of dir.entries()) {
    if (out.length >= MAX_SCAN_FILES) { state.truncated = true; return; }
    if (h.kind === "file") {
      const file = await h.getFile();
      out.push({ path: prefix + name, size: file.size, getFile: async () => h.getFile() });
    } else if (HARD_PRUNE.has(name)) state.pruned++;
    else await walkHandle(h, `${prefix}${name}/`, out, state);
  }
}

async function scanDropped(entries, handlePromises) {
  const entry = entries.find((e) => e && e.isDirectory);
  if (!entry) { setFolderStatus("That is not a folder. Drop a folder, or use Choose folder."); return; }
  setFolderStatus(`Reading ${entry.name} ...`);
  const out = [], state = { pruned: 0, truncated: false };
  for (const child of await readAllEntries(entry.createReader())) await walkEntry(child, "", out, state);
  let handle = null;
  try { handle = (await Promise.all(handlePromises)).find((h) => h && h.kind === "directory") || null; } catch (_) { /* none */ }
  await loadScan(entry.name, out, state, handle);
}

async function scanInput(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const name = (files[0].webkitRelativePath || "").split("/")[0] || "folder";
  const out = [], state = { pruned: 0, truncated: false };
  for (const f of files) {
    const rel = (f.webkitRelativePath || f.name).split("/").slice(1).join("/");
    if (!rel) continue;
    if (rel.split("/").slice(0, -1).some((p) => HARD_PRUNE.has(p))) { state.pruned++; continue; }
    if (out.length >= MAX_SCAN_FILES) { state.truncated = true; break; }
    out.push({ path: rel, size: f.size, getFile: async () => f });
  }
  await loadScan(name, out, state, null);
}

async function scanHandle(handle) {
  setFolderStatus(`Reading ${handle.name} ...`);
  const out = [], state = { pruned: 0, truncated: false };
  await walkHandle(handle, "", out, state);
  await loadScan(handle.name, out, state, handle);
}

async function loadScan(name, files, state, handle) {
  fs.root = name;
  fs.files = files;
  fs.pruned = state.pruned;
  fs.handle = handle;
  fs.gitignore = "";
  const gi = files.find((f) => f.path === ".gitignore");
  if (gi && gi.size <= 100000) { try { fs.gitignore = await (await gi.getFile()).text(); } catch (_) { /* ignore */ } }
  setFolderStatus(`${plural(files.length, "file")} found in ${name}` + (fs.pruned ? ` (${plural(fs.pruned, "folder")} such as .git or node_modules not read)` : "")
    + (state.truncated ? `. Stopped at ${fmtNum(MAX_SCAN_FILES)} files: pick a smaller folder or exclude more.` : "."));
  $("fGitignoreRow").hidden = !fs.gitignore;
  if (!fs.source) $("fName").value = slugify(name) || "folder";
  $("fPrefix").value = $("fName").value;
  buildTree();
  applyPrefix();
  $("folderPanel").hidden = false;
  $("fSummary").hidden = true;
  recompute();
}

function setFolderStatus(text) { $("folderStatus").textContent = text; }

// --- rules and the tree of folders and files ---

// Why a file is out because of what the user chose (a rule or an unticked folder), as opposed to a built-in reason.
const USER_REASONS = new Set(["excluded-by-rule", "not-included"]);
const MAX_ROWS = 3000;      // rows drawn at once; a folder shows its first files and says how many more
const FILES_PER_FOLDER = 300;
const byName = (a, b) => a.name.localeCompare(b.name);

function readRules(roots = []) {
  const ext = $("fExt").value.trim();
  return {
    include: HF.splitLines($("fInclude").value), exclude: HF.splitLines($("fExclude").value),
    extensions: ext === "" ? null : ext === "*" ? [] : HF.splitList(ext),
    defaultExcludes: $("fDefaults").checked,
    gitignore: $("fGitignore").checked ? fs.gitignore : "", roots,
  };
}

const tickedRoots = () => [...fs.ticked];

// Once per scan: the folder structure, and the state the controls start from (or the saved source's).
function buildTree() {
  const top = { name: "", path: "", dirs: new Map(), files: [] };
  for (const f of fs.files) {
    const parts = f.path.replace(/\\/g, "/").split("/");
    let node = top;
    for (const seg of parts.slice(0, -1)) {
      let next = node.dirs.get(seg);
      if (!next) { next = { name: seg, path: node.path ? `${node.path}/${seg}` : seg, dirs: new Map(), files: [] }; node.dirs.set(seg, next); }
      node = next;
    }
    node.files.push({ key: f.path, name: parts[parts.length - 1], path: parts.join("/"), size: f.size });
  }
  fs.tree = top;
  fs.topNode = { name: ".", label: "(files at the top)", path: ".", dirs: new Map(), files: top.files };
  fs.decisions = new Map();
  fs.expanded = new Set();
  fs.ticked = new Set();
  fs.seen = new Set();
  fs.slugs = new Map();
  fs.vis = new Map();
  const mapping = fs.source ? fs.source.folder_collections : {};
  for (const [root, slug] of Object.entries(mapping)) fs.slugs.set(root, { value: slug, edited: true });
  $("fPerFolder").checked = Object.keys(mapping).length > 0;
  $("fPrefixLabel").hidden = !$("fPerFolder").checked;
  $("fRootName").textContent = `${fs.root}/`;
}

// The top-level rows: the files at the top, then each folder. A root not seen before is ticked unless it looks private.
function rootNodes() {
  const saved = fs.source ? fs.source.settings.folders : [];
  const defaults = $("fDefaults").checked;
  const out = [];
  if (fs.topNode.files.length) out.push(fs.topNode);
  for (const d of [...fs.tree.dirs.values()].sort(byName)) if (!(defaults && HF.isDefaultExcludedDir(d.name))) out.push(d);
  for (const n of out) {
    if (fs.seen.has(n.name)) continue;
    fs.seen.add(n.name);
    if (saved.length ? saved.includes(n.name) : n.name === "." || !HF.PRIVATE_LOOKING.test(n.name)) fs.ticked.add(n.name);
  }
  return out;
}

function applyPrefix() {
  const prefix = slugify($("fPrefix").value);
  for (const name of fs.tree ? fs.tree.dirs.keys() : []) {
    const cur = fs.slugs.get(name);
    if (!cur || !cur.edited) fs.slugs.set(name, { value: slugify(`${prefix}-${name}`), edited: false });
  }
}

const slugOf = (root) => (fs.slugs.get(root) || { value: "" }).value.trim();
const exists = (slug) => collections.some((c) => c.slug === slug);

// What goes where: one group per collection, each with the root folders that belong to it.
function plan() {
  const roots = tickedRoots();
  const base = targetSlug();
  const baseGroup = (rs) => ({ collection: base, roots: rs, new: !exists(base), visibility: $("fNewVisibility").value });
  if (!$("fPerFolder").checked) return [baseGroup(roots)];
  const groups = [];
  if (roots.includes(".")) groups.push(baseGroup(["."]));
  for (const r of roots.filter((x) => x !== ".")) {
    groups.push({ collection: slugOf(r), roots: [r], new: !exists(slugOf(r)), visibility: fs.vis.get(r) || "shared" });
  }
  return groups;
}

const targetSlug = () => ($("fCollection").value === NEW ? $("fNewSlug").value.trim() : $("fCollection").value);

// --- the tree: counts, checkboxes, and the status of every file ---

function annotate(node) {
  node.on = 0; node.off = 0; node.total = 0;
  for (const f of node.files) {
    f.d = fs.decisions.get(f.key);
    node.total++;
    if (f.d.outcome === "indexed") node.on++;
    else if (USER_REASONS.has(f.d.reason)) node.off++;
  }
  for (const c of node.dirs.values()) { annotate(c); node.on += c.on; node.off += c.off; node.total += c.total; }
}

const treeLine = (path, isDir) => `/${path}${isDir ? "/" : ""}`;
const fmtSize = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

// A box below the top level adds or removes a line in the Exclude rules, so the choice is visible and is saved with the source.
function toggleExclude(node, isDir, include) {
  const line = treeLine(node.path, isDir);
  let lines = HF.splitLines($("fExclude").value).filter((l) => l.trim() !== "");
  if (include) lines = lines.filter((l) => l.trim() !== line && !(isDir && l.trim().startsWith(line)));
  else if (!lines.some((l) => l.trim() === line)) lines.push(line);
  $("fExclude").value = lines.join("\n");
  recompute();
}

function toggleRoot(name, on) {
  if (on) fs.ticked.add(name); else fs.ticked.delete(name);
  recompute();
}

function renderTree() {
  const body = $("fTree");
  body.replaceChildren();
  const ctx = { rows: 0, show: $("fShow").value, perFolder: $("fPerFolder").checked,
    lines: new Set(HF.splitLines($("fExclude").value).map((l) => l.trim())) };
  for (const th of document.querySelectorAll("th.perFolderCell")) th.hidden = !ctx.perFolder;
  for (const r of rootNodes()) addNode(body, r, 0, true, false, ctx);
  if (ctx.rows >= MAX_ROWS) body.append(el("tr", {}, el("td", { colspan: 5, class: "muted" },
    `Only the first ${fmtNum(MAX_ROWS)} rows are drawn. Collapse folders, or use the Exclude rules.`)));
}

function addNode(body, node, depth, isRoot, blocked, ctx) {
  if (ctx.rows >= MAX_ROWS) return;
  ctx.rows++;
  const open = fs.expanded.has(node.path);
  const kids = node.dirs.size + node.files.length > 0;
  const rootOn = isRoot && fs.ticked.has(node.name);
  const isTop = node.name === "." && isRoot;
  const box = el("input", { type: "checkbox", "aria-label": `Index ${isTop ? "the files at the top" : node.path}` });
  if (isRoot) {
    box.setAttribute("data-root", node.name);
    box.checked = rootOn;
    box.indeterminate = rootOn && node.off > 0;
    box.addEventListener("change", () => toggleRoot(node.name, box.checked));
  } else {
    box.checked = node.on > 0 && node.off === 0;
    box.indeterminate = node.on > 0 && node.off > 0;
    box.disabled = blocked || (node.on === 0 && node.off === 0);
    box.title = box.disabled && !blocked ? "Nothing in this folder can be indexed" : "";
    box.addEventListener("change", () => toggleExclude(node, true, box.checked));
  }
  const twisty = kids ? el("button", { class: "twisty", type: "button", "aria-expanded": String(open),
    "aria-label": `${open ? "Collapse" : "Expand"} ${node.path}`, onclick: () => { if (open) fs.expanded.delete(node.path); else fs.expanded.add(node.path); renderTree(); } },
  open ? "▾" : "▸") : el("span", { class: "twisty" }, "");
  const name = el("td", { title: isTop ? "Files directly in the folder" : node.path }, twisty,
    el("span", { class: "mono" }, isTop ? node.label : `${node.name}/`),
    isRoot && HF.PRIVATE_LOOKING.test(node.name) ? el("span", { class: "badge restricted" }, "looks private") : "");
  name.style.paddingLeft = `${8 + depth * 20}px`;
  const out = node.total - node.on;
  const summary = el("td", { class: "num" }, `${fmtNum(node.on)} to index`,
    el("span", { class: "muted" }, ` · ${fmtNum(out)} not`));
  const slugCell = el("td", { class: "perFolderCell" });
  const visCell = el("td", { class: "perFolderCell" });
  slugCell.hidden = visCell.hidden = !ctx.perFolder;
  if (isRoot && !isTop) {
    const slug = el("input", { type: "text", "data-slug": node.name, pattern: "[a-z0-9][a-z0-9\\-]{0,63}", placeholder: "collection" });
    slug.value = slugOf(node.name);
    slug.addEventListener("input", () => { fs.slugs.set(node.name, { value: slug.value, edited: true }); renderPreview(); });
    const vis = el("select", { "data-vis": node.name }, el("option", { value: "shared" }, "shared"), el("option", { value: "restricted" }, "restricted"));
    vis.value = fs.vis.get(node.name) || "shared";
    vis.disabled = exists(slug.value);
    vis.addEventListener("change", () => fs.vis.set(node.name, vis.value));
    slugCell.append(slug);
    visCell.append(vis);
  }
  body.append(el("tr", { class: `tnode${isRoot ? " troot" : ""}`, "data-path": node.path }, el("td", {}, box), name, summary, slugCell, visCell));
  if (!open) return;
  const childBlocked = blocked || (isRoot ? !rootOn : node.on === 0 && node.off > 0);
  for (const d of [...node.dirs.values()].sort(byName)) addNode(body, d, depth + 1, false, childBlocked, ctx);
  const files = node.files.filter((f) => ctx.show === "all" || (ctx.show === "on") === (f.d.outcome === "indexed"));
  for (const f of files.slice(0, FILES_PER_FOLDER)) addFile(body, f, depth + 1, childBlocked, ctx);
  if (files.length > FILES_PER_FOLDER) body.append(el("tr", {}, el("td", {}), el("td", { colspan: 4, class: "muted" },
    `... and ${fmtNum(files.length - FILES_PER_FOLDER)} more files in ${node.path}`)));
}

function addFile(body, f, depth, blocked, ctx) {
  if (ctx.rows >= MAX_ROWS) return;
  ctx.rows++;
  const d = f.d, on = d.outcome === "indexed";
  const line = treeLine(f.path, false);
  const box = el("input", { type: "checkbox", "aria-label": `Index ${f.path}` });
  box.checked = on;
  // a file can be toggled when only the user's choices keep it out, and not by a rule that is not a line of its own
  box.disabled = blocked || !(on || (d.reason === "excluded-by-rule" && ctx.lines.has(line)));
  if (!on && d.reason === "excluded-by-rule" && !ctx.lines.has(line)) box.title = "Excluded by a rule you wrote; edit the rule";
  box.addEventListener("change", () => toggleExclude(f, false, box.checked));
  const name = el("td", { title: f.path }, el("span", { class: "twisty" }, ""), el("span", { class: "mono" }, f.name),
    on ? "" : el("span", { class: `badge ${d.outcome === "rejected" ? "error" : "skipped"}` }, d.reason));
  name.style.paddingLeft = `${8 + depth * 20}px`;
  body.append(el("tr", { class: `tfile${on ? "" : " tout"}`, "data-path": f.path }, el("td", {}, box), name,
    el("td", { class: "num muted" }, fmtSize(f.size)), el("td", { class: "perFolderCell", hidden: "" }), el("td", { class: "perFolderCell", hidden: "" })));
}

function expandAll(open) {
  fs.expanded.clear();
  if (!open) { renderTree(); return; }
  const walk = (n) => { for (const c of n.dirs.values()) { fs.expanded.add(c.path); walk(c); } };
  for (const r of rootNodes()) { fs.expanded.add(r.path); walk(r); }
  renderTree();
}

// --- the live preview (local rules; the server repeats every check) ---

function recompute() {
  rootNodes(); // ticks any root that appeared (for instance when the default exclusions are turned off)
  const roots = tickedRoots();
  const flt = HF.makeFilter(readRules(roots.length ? roots : ["\0"])); // no roots would mean "all of them"
  const counts = { indexed: 0, skipped: 0, rejected: 0 };
  fs.reasons = new Map();
  fs.rejected = [];
  fs.decisions = new Map();
  for (const f of fs.files) {
    const d = HF.checkPath(flt, f.path, f.size);
    fs.decisions.set(f.path, d);
    counts[d.outcome]++;
    if (d.outcome !== "indexed") {
      fs.reasons.set(d.reason, (fs.reasons.get(d.reason) || 0) + 1);
      if (d.outcome === "rejected" && fs.rejected.length < 20) fs.rejected.push(d);
    }
  }
  fs.eligible = counts.indexed;
  fs.counts = counts;
  annotate(fs.topNode);
  for (const d of fs.tree.dirs.values()) annotate(d);
  renderTree();
  renderPreview();
}

function renderPreview() {
  const counts = fs.counts;
  const box = $("fPreview");
  box.replaceChildren(el("p", {}, el("strong", {}, `${fmtNum(counts.indexed)} files to index`),
    `, ${fmtNum(counts.skipped)} skipped, ${fmtNum(counts.rejected)} rejected. Files are also read for secrets just before upload.`));
  const list = el("ul", { class: "reasons" });
  for (const [reason, n] of [...fs.reasons].sort((a, b) => b[1] - a[1])) list.append(el("li", {}, `${fmtNum(n)} ${reason}`));
  box.append(list);
  if (fs.rejected.length) {
    box.append(el("p", { class: "muted" }, "Rejected (path and rule):"),
      el("ul", { class: "reasons mono" }, ...fs.rejected.map((d) => el("li", {}, `${d.path}: ${d.reason}`))));
  }
  const problem = problems();
  $("fProblem").textContent = problem;
  $("fStart").disabled = fs.running || !!problem;
  $("fCheckServer").disabled = fs.running;
}

function problems() {
  if (!fs.files.length) return "";
  const name = $("fName").value.trim();
  if (!/^[A-Za-z0-9._-]{1,100}$/.test(name)) return "The source name uses letters, digits, . _ - (up to 100).";
  if (!tickedRoots().length) return "Tick at least one folder.";
  if (fs.eligible > MAX_ELIGIBLE_FILES) return `${fmtNum(fs.eligible)} files is over the limit of ${fmtNum(MAX_ELIGIBLE_FILES)} for one run: untick folders or add exclude rules.`;
  const groups = plan();
  for (const g of groups) if (!SLUG_OK.test(g.collection)) return `Collection name "${g.collection}" must be lowercase letters, digits and dashes.`;
  if (new Set(groups.map((g) => g.collection)).size !== groups.length) return "Two folders go to the same collection name; give each its own.";
  return "";
}

// --- checking with the server (dry run) ---

async function checkServer() {
  const base = targetSlug();
  const existing = collections.find((c) => c.slug === base);
  if (!existing) { $("fServerCheck").textContent = "The server check needs an existing collection to ask about. Pick one, or just start."; return; }
  const roots = tickedRoots();
  const flt = HF.makeFilter(readRules(roots));
  const items = fs.files.filter((f) => HF.checkPath(flt, f.path, f.size).outcome === "indexed").map((f) => ({ path: f.path, size: f.size }));
  const all = fs.files.map((f) => ({ path: f.path, size: f.size }));
  $("fServerCheck").textContent = "Asking the server ...";
  const counts = { indexed: 0, skipped: 0, rejected: 0 };
  try {
    for (let i = 0; i < all.length; i += 500) {
      const res = await api("/v1/ingest/files", { method: "POST", body: JSON.stringify({
        collection: base, source: $("fName").value.trim(), dry_run: true, files: all.slice(i, i + 500), filter: filterBody(roots) }) });
      for (const k of Object.keys(counts)) counts[k] += res.counts[k];
    }
    const same = counts.indexed === items.length;
    $("fServerCheck").textContent = same
      ? `The server agrees: ${fmtNum(counts.indexed)} files would be indexed.`
      : `The server would index ${fmtNum(counts.indexed)} files; this page counted ${fmtNum(items.length)}. The server's answer is the one that counts.`;
    banner("");
  } catch (err) { $("fServerCheck").textContent = ""; banner(err.message); }
}

function filterBody(roots) {
  const r = readRules(roots);
  return { include: r.include.filter((l) => l.trim()), exclude: r.exclude.filter((l) => l.trim()), extensions: r.extensions,
    default_excludes: r.defaultExcludes, gitignore: r.gitignore || null, folders: roots };
}

// --- running a sync ---

async function ensureCollection(g) {
  if (!g.new) return;
  await api("/v1/collections", { method: "POST", body: JSON.stringify({ slug: g.collection, name: g.collection, visibility: g.visibility }) });
  g.new = false;
}

async function saveSource(groups) {
  const roots = tickedRoots();
  const mapping = {};
  if ($("fPerFolder").checked) for (const g of groups) if (g.roots[0] !== ".") mapping[g.roots[0]] = g.collection;
  const body = { collection: targetSlug(), settings: filterBody(roots), folder_collections: mapping };
  const name = $("fName").value.trim();
  if (fs.source || savedList.some((s) => s.name === name)) {
    await api(`/v1/sources/${encodeURIComponent(name)}`, { method: "PATCH", body: JSON.stringify(body) });
  } else {
    await api("/v1/sources", { method: "POST", body: JSON.stringify({ name, kind: "upload", ...body }) });
  }
}

async function runSync() {
  if (fs.running) return;
  const problem = problems();
  if (problem) { banner(problem); return; }
  fs.running = true;
  fs.cancel = false;
  $("fStart").disabled = true;
  $("fCancel").hidden = false;
  $("fSummary").hidden = true;
  const bar = $("fProgress");
  bar.hidden = false;
  const name = $("fName").value.trim();
  const groups = plan();
  const total = { added: 0, changed: 0, unchanged: 0, removed: 0, skipped: 0, rejected: 0 };
  const notes = [];       // {path, outcome, reason}
  const removedPaths = [];
  let done = 0, failed = null;
  const before = savedList.find((s) => s.name === name); // the source as it was, to clear what a changed plan leaves behind
  const left = before ? [before.collection, ...Object.values(before.folder_collections)] : [];
  try {
    for (const g of groups) await ensureCollection(g);
    if ($("fModeSave").checked) await saveSource(groups);
    const wanted = groups.reduce((n, g) => n + fileCount(g), 0);
    bar.max = Math.max(wanted, 1);
    for (const g of groups) {
      if (fs.cancel) break;
      const flt = HF.makeFilter(readRules(g.roots));
      const mine = fs.files.filter((f) => HF.checkPath(flt, f.path, f.size).outcome === "indexed");
      let session = null, batch = [], chars = 0;
      const send = async (final) => {
        const res = await api("/v1/ingest/files", { method: "POST", body: JSON.stringify({
          collection: g.collection, source: name, files: batch, session, final, filter: filterBody(g.roots) }) });
        if (session) fs.sessions.delete(session);
        session = res.session;
        if (session) fs.sessions.add(session);
        for (const row of res.files) if (row.outcome !== "indexed" || row.warning) notes.push(row);
        for (const p of res.removed) removedPaths.push(p);
        if (final) for (const k of Object.keys(total)) total[k] += res.totals[k] || 0;
        batch = []; chars = 0;
      };
      for (const f of mine) {
        if (fs.cancel) break;
        let text;
        try { text = HF.normalizeText(await (await f.getFile()).text()); } catch (err) {
          notes.push({ path: f.path, outcome: "skipped", reason: "unreadable" }); total.skipped++; done++; bar.value = done; continue;
        }
        const d = HF.checkContent(f.path, text);
        done++; bar.value = done;
        if (d.outcome !== "indexed") { notes.push(d); total[d.outcome]++; continue; }
        batch.push({ path: f.path, content: text });
        chars += text.length;
        if (batch.length >= BATCH_FILES || chars >= BATCH_CHARS) await send(false);
      }
      if (fs.cancel) { if (session) await abortSession(session); break; }
      await send(true);
    }
    if (fs.cancel) await abortAll();
    else if ($("fModeSave").checked) { // collections the source no longer feeds (a folder unticked or moved)
      for (const slug of new Set(left)) {
        if (groups.some((g) => g.collection === slug) || !collections.some((c) => c.slug === slug)) continue;
        const res = await api("/v1/ingest/files", { method: "POST", body: JSON.stringify({
          collection: slug, source: name, files: [], final: true, filter: filterBody([]) }) });
        total.removed += res.removed.length;
        for (const p of res.removed) removedPaths.push(p);
      }
    }
  } catch (err) {
    failed = err;
    await abortAll();
  }
  fs.running = false;
  $("fCancel").hidden = true;
  bar.hidden = true;
  showSummary({ total, notes, removedPaths, cancelled: fs.cancel, failed, name, groups });
  if (!failed && !fs.cancel && fs.handle && $("fModeSave").checked) await idbPut(name, fs.handle);
  await Promise.all([loadCollections(), loadSavedSources(), loadStatus()]);
  recompute();
}

function fileCount(g) {
  const flt = HF.makeFilter(readRules(g.roots));
  return fs.files.filter((f) => HF.checkPath(flt, f.path, f.size).outcome === "indexed").length;
}

async function abortSession(id) {
  fs.sessions.delete(id);
  try { await api(`/v1/ingest/files/${encodeURIComponent(id)}?reason=${encodeURIComponent(fs.cancel ? "cancelled" : "failed")}`, { method: "DELETE" }); } catch (_) { /* gone already */ }
}

async function abortAll() { for (const id of [...fs.sessions]) await abortSession(id); }

function showSummary({ total, notes, removedPaths, cancelled, failed, name, groups }) {
  const box = $("fSummary");
  box.hidden = false;
  box.replaceChildren();
  if (failed) {
    banner(failed.message);
    box.append(el("p", {}, el("strong", {}, "The run stopped. "), "Nothing was removed; the index keeps what it had. ", failed.message));
  } else if (cancelled) {
    box.append(el("p", {}, el("strong", {}, "Cancelled. "), "Nothing was removed; the index keeps what it had."));
  } else {
    banner("");
    box.append(el("p", {}, el("strong", {}, `${name} is up to date. `),
      `${fmtNum(total.added)} added, ${fmtNum(total.changed)} changed, ${fmtNum(total.unchanged)} unchanged, `
      + `${fmtNum(total.removed)} removed, ${fmtNum(total.skipped)} skipped, ${fmtNum(total.rejected)} rejected `
      + `in ${groups.map((g) => g.collection).join(", ")}.`));
    if (!$("fModeSave").checked) box.append(el("p", { class: "muted" }, "Imported once: no source was saved, so there is nothing to refresh later."));
  }
  const interesting = notes.filter((n) => n.outcome === "rejected" || n.warning).slice(0, 50);
  if (interesting.length) {
    box.append(el("p", { class: "muted" }, "Rejected or flagged (path and rule, never content):"),
      el("ul", { class: "reasons mono" }, ...interesting.map((n) => el("li", {}, `${n.path}: ${n.reason || n.warning}`))));
  }
  if (removedPaths.length) {
    box.append(el("p", { class: "muted" }, `Removed from the index (${fmtNum(removedPaths.length)}):`),
      el("ul", { class: "reasons mono" }, ...removedPaths.slice(0, 50).map((p) => el("li", {}, p))));
  }
}

// --- saved sources ---

let savedList = [];

async function loadSavedSources() {
  const body = $("savedSources");
  try { savedList = (await api("/v1/sources")).sources; } catch (err) { banner(err.message); return; }
  $("savedSection").hidden = !savedList.length;
  body.replaceChildren();
  for (const s of savedList) {
    const cols = [s.collection, ...Object.values(s.folder_collections)].filter((v, i, a) => a.indexOf(v) === i);
    const sum = s.last_summary || {};
    const text = s.last_status === "ok"
      ? `${sum.collection}: ${fmtNum(sum.added || 0)} added, ${fmtNum(sum.changed || 0)} changed, ${fmtNum(sum.removed || 0)} removed, ${fmtNum(sum.rejected || 0)} rejected`
      : s.last_status === "error" ? `failed: ${sum.error || "unknown"}` : "";
    const actions = el("td", { class: "right" });
    body.append(el("tr", {},
      el("td", { class: "mono" }, s.name),
      el("td", { class: "mono" }, cols.join(", ")),
      el("td", {}, s.settings.folders.length ? s.settings.folders.join(", ") : "all"),
      el("td", {}, s.last_run_at ? fmtDate(s.last_run_at) : "never",
        s.last_status ? el("span", { class: `badge ${s.last_status === "ok" ? "done" : "error"}` }, s.last_status) : "",
        el("div", { class: "muted" }, text)),
      actions));
    idbGet(s.name).then((h) => {
      if (h && window.showDirectoryPicker) actions.append(el("button", { onclick: () => resyncFromHandle(s, h) }, "Re-sync"), " ");
      actions.append(el("button", { onclick: () => refreshSource(s) }, "Drop again"), " ",
        el("button", { class: "danger", onclick: () => deleteSource(s) }, "Forget"));
    });
  }
}

// Open the folder panel with this source's rules and ask for the folder again.
function refreshSource(s) {
  fs.source = s;
  $("fName").value = s.name;
  $("fName").readOnly = true;
  $("fModeSave").checked = true;
  const set = s.settings;
  $("fInclude").value = set.include.join("\n");
  $("fExclude").value = set.exclude.join("\n");
  $("fExt").value = set.extensions === null ? "" : set.extensions.length ? set.extensions.join(", ") : "*";
  $("fDefaults").checked = set.default_excludes;
  $("fGitignore").checked = !!set.gitignore;
  $("fCollection").value = s.collection;
  $("fPerFolder").checked = Object.keys(s.folder_collections).length > 0;
  $("folderPanel").hidden = true;
  setFolderStatus(`Drop or choose the folder for "${s.name}" to refresh it. Its saved rules are applied.`);
  $("folderSection").scrollIntoView({ behavior: "smooth" });
  $("dropZone").focus();
}

async function resyncFromHandle(s, handle) {
  try {
    if ((await handle.requestPermission({ mode: "read" })) !== "granted") { banner("Read access to the folder was not granted."); return; }
    refreshSource(s);
    await scanHandle(handle);
    await runSync();
  } catch (err) { banner(err.message); }
}

async function deleteSource(s) {
  if (!confirm(`Forget the source "${s.name}"? Its indexed files stay searchable unless you also remove them.`)) return;
  const purge = confirm(`Also remove the files "${s.name}" indexed from the collection? OK removes them, Cancel keeps them.`);
  try {
    await api(`/v1/sources/${encodeURIComponent(s.name)}${purge ? "?purge=true" : ""}`, { method: "DELETE" });
    await idbDel(s.name);
    banner("");
    await Promise.all([loadSavedSources(), loadCollections()]);
  } catch (err) { banner(err.message); }
}

// --- remembered folder handles (Chromium only); a failure just means "drop again" ---

function idb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("hippocampus", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("handles");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function idbOp(mode, fn) {
  try {
    const db = await idb();
    return await new Promise((resolve) => {
      const tx = db.transaction("handles", mode);
      const req = fn(tx.objectStore("handles"));
      tx.oncomplete = () => resolve(req ? req.result : null);
      tx.onerror = tx.onabort = () => resolve(null);
    });
  } catch (_) { return null; }
}
const idbGet = (key) => idbOp("readonly", (s) => s.get(key));
const idbPut = (key, value) => idbOp("readwrite", (s) => s.put(value, key));
const idbDel = (key) => idbOp("readwrite", (s) => s.delete(key));

// --- wiring ---

function initFolderSync() {
  const zone = $("dropZone");
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("over");
    // Entries and handles must be taken now: the list is emptied once this handler yields.
    const items = [...e.dataTransfer.items].filter((i) => i.kind === "file");
    const entries = items.map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null));
    const handles = items.map((i) => (i.getAsFileSystemHandle ? i.getAsFileSystemHandle() : null));
    scanDropped(entries, handles).catch((err) => setFolderStatus(err.message));
  });
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("folderInput").click(); } });
  $("chooseFolder").addEventListener("click", () => $("folderInput").click());
  $("folderInput").addEventListener("change", (e) => { scanInput(e.target.files).catch((err) => setFolderStatus(err.message)); e.target.value = ""; });
  for (const id of ["fInclude", "fExclude", "fExt", "fDefaults", "fGitignore"]) $(id).addEventListener("input", recompute);
  for (const id of ["fName", "fCollection", "fNewSlug", "fNewVisibility"]) $(id).addEventListener("input", renderPreview);
  $("fShow").addEventListener("change", renderTree);
  $("fExpand").addEventListener("click", () => expandAll(true));
  $("fCollapse").addEventListener("click", () => expandAll(false));
  $("fPrefix").addEventListener("input", () => { applyPrefix(); renderTree(); renderPreview(); });
  $("fName").addEventListener("input", () => { if (!fs.source) { $("fPrefix").value = $("fName").value; applyPrefix(); renderTree(); } });
  $("fPerFolder").addEventListener("change", () => { $("fPrefixLabel").hidden = !$("fPerFolder").checked; applyPrefix(); renderTree(); renderPreview(); });
  $("fCollection").addEventListener("change", () => { $("fNewBox").hidden = $("fCollection").value !== NEW; renderPreview(); });
  $("fStart").addEventListener("click", runSync);
  $("fCancel").addEventListener("click", () => { fs.cancel = true; });
  $("fCheckServer").addEventListener("click", checkServer);
  $("fClose").addEventListener("click", resetFolder);
  loadSavedSources();
}

function resetFolder() {
  fs.files = []; fs.source = null; fs.handle = null; fs.gitignore = "";
  $("fName").readOnly = false;
  $("folderPanel").hidden = true;
  setFolderStatus("");
}

function fillFolderCollections() {
  const select = $("fCollection");
  const previous = select.value;
  select.replaceChildren();
  for (const c of collections) select.append(el("option", { value: c.slug }, `${c.slug} (${c.name})`));
  select.append(el("option", { value: NEW }, "New collection..."));
  if ([...select.options].some((o) => o.value === previous)) select.value = previous;
  else if (!collections.length) select.value = NEW;
  $("fNewBox").hidden = select.value !== NEW;
}
