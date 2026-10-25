// Runs the shared filter vectors against the dashboard's folder.js (the Python side is tests/unit/test_filter_vectors.py).
// Usage: node tests/js/check_vectors.js
"use strict";
const fs = require("fs");
const path = require("path");
const F = require("../../src/hippocampus/ui/folder.js");
const V = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "vectors", "filter_vectors.json"), "utf8"));

let failed = 0, total = 0;
function check(name, got, want) {
  total++;
  if (JSON.stringify(got) !== JSON.stringify(want)) { failed++; console.error(`FAIL ${name}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`); }
}

for (const c of V.normalize_path) {
  let got;
  try { got = { out: F.normalizePath(c.in) }; } catch (e) { got = { error: e.message }; }
  check(`normalize_path ${JSON.stringify(c.in)}`, got, c.error ? { error: c.error } : { out: c.out });
}
for (const c of V.normalize_text) check(`normalize_text ${JSON.stringify(c.in)}`, F.normalizeText(c.in), c.out);
for (const c of V.check_path) {
  const r = c.rules || {};
  const flt = F.makeFilter({ include: r.include, exclude: r.exclude, extensions: r.extensions, defaultExcludes: r.default_excludes,
    gitignore: r.gitignore, roots: r.roots, excludeParts: r.exclude_parts });
  const d = F.checkPath(flt, c.path, c.size);
  check(`check_path ${c.path} ${JSON.stringify(r)}`, [d.outcome, d.reason ?? null], c.expect);
}
for (const c of V.check_content) {
  const d = F.checkContent("a.md", c.text);
  check(`check_content ${JSON.stringify(c.text.slice(0, 30))}`, [d.outcome, d.reason ?? null], c.expect);
}
// the fixture vault: the same folder must give the same set of indexed paths here and on the server
const vault = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "vectors", "vault_paths.json"), "utf8"));
const vflt = F.makeFilter({});
const vdec = Object.entries(vault.files).map(([p, text]) => {
  const d = F.checkPath(vflt, p, text.length);
  return d.outcome === "indexed" ? F.checkContent(d.path, F.normalizeText(text)) : d;
});
check("vault indexed", vdec.filter((d) => d.outcome === "indexed").map((d) => d.path).sort(), vault.indexed_by_default);
check("vault rejected", vdec.filter((d) => d.outcome === "rejected").map((d) => d.path), vault.rejected_by_default);

console.log(`${total - failed}/${total} vectors agree`);
process.exit(failed ? 1 : 0);
