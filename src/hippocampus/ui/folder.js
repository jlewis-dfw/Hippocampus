// The folder rules, in the browser: the same decisions as sync_core.py, so the preview matches what the server will do.
// tests/vectors/filter_vectors.json holds cases that both implementations must agree on (checked by pytest and by
// tests/js/check_vectors.js). The server repeats every check; this only saves uploading what would be refused anyway.
"use strict";

(function (root) {
  const MAX_FILE_BYTES = 200000;
  const MAX_PATH_CHARS = 1000;
  const DEFAULT_EXTENSIONS = [".md", ".markdown", ".txt", ".rst"];
  const DEFAULT_EXCLUDE_DIRS = [".git", ".svn", ".hg", ".obsidian", ".trash", ".Trash*", "node_modules", "__pycache__",
    ".venv", "venv", "dist", "build", ".next", ".cache", ".Spotlight-V100", ".fseventsd", "$RECYCLE.BIN",
    "System Volume Information"];
  const DEFAULT_EXCLUDE_FILES = [".DS_Store", "Thumbs.db", "desktop.ini", "~$*", ".~lock.*", "*.tmp", "*.swp"];
  const PRIVATE_LOOKING = /^(journal|personal|private|diary|secret|confidential)/i;
  const RESERVED = new Set(["con", "prn", "aux", "nul",
    ...Array.from({ length: 9 }, (_, i) => `com${i + 1}`), ...Array.from({ length: 9 }, (_, i) => `lpt${i + 1}`)]);

  const SECRET_RULES = [
    ["aws-access-key-id", /\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b/],
    ["aws-secret-access-key", /aws.{0,20}secret.{0,20}[:=]\s*['"]?[A-Za-z0-9/+=]{40}\b/i],
    ["pem-private-key", /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
    ["bearer-token", /\bBearer\s+[A-Za-z0-9._~+/=-]{20,}/i],
    ["jwt", /\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/],
    ["github-token", /\bgh[pousr]_[A-Za-z0-9]{30,}\b/],
    ["slack-token", /\bxox[abprs]-[A-Za-z0-9-]{10,}/],
    ["azure-storage-key", /AccountKey=[A-Za-z0-9+/=]{40,}/i],
    ["credential-assignment",
      /\b(?:password|passwd|secret|token|api[_-]?key|client[_-]?secret)\b["']?\s*[:=]\s*["'][^"'\s$<{][^"'\s]{7,}["']/i],
  ];
  const SECRET_PATH = new RegExp(
    "(?:^|/)(?:\\.env(?:\\..*)?|\\.vault_pass|vault\\.yml|secrets?\\.auto\\.tfvars|id_(?:rsa|dsa|ecdsa|ed25519)" +
    "|[^/]*\\.(?:pem|key|p12|pfx|tfstate(?:\\.backup)?)|credentials(?:\\.json)?)$", "i");

  class PathError extends Error {}

  function normalizePath(raw) {
    if (typeof raw !== "string" || !raw) throw new PathError("empty-path");
    if (/[\x00-\x1f\x7f]/.test(raw)) throw new PathError("control-character");
    const path = raw.replace(/\\/g, "/").normalize("NFC");
    if (path.startsWith("/")) throw new PathError("absolute-path");
    if (/^[A-Za-z]:/.test(path)) throw new PathError("drive-letter");
    const parts = path.split("/").filter((p) => p !== "" && p !== ".");
    if (!parts.length) throw new PathError("empty-path");
    for (const part of parts) {
      if (part === "..") throw new PathError("path-traversal");
      if (part !== part.replace(/[. ]+$/, "") || RESERVED.has(part.split(".")[0].trimEnd().toLowerCase())) {
        throw new PathError("windows-reserved-name");
      }
    }
    const out = parts.join("/");
    if (out.length > MAX_PATH_CHARS) throw new PathError("path-too-long");
    return out;
  }

  function normalizeText(text) {
    return text.replace(/^﻿/, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  }

  function scanSecrets(text) {
    return SECRET_RULES.filter(([, rx]) => rx.test(text)).map(([name]) => name);
  }

  // gitignore-style globs, as in sync_core.Glob
  function globRegex(pattern) {
    let out = "";
    for (let i = 0; i < pattern.length;) {
      if (pattern.startsWith("**/", i)) { out += "(?:.*/)?"; i += 3; continue; }
      if (pattern.startsWith("**", i)) { out += ".*"; i += 2; continue; }
      const c = pattern[i++];
      out += c === "*" ? "[^/]*" : c === "?" ? "[^/]" : c.replace(/[.*+?^${}()|[\]\\\/-]/g, "\\$&");
    }
    return new RegExp(`^${out}$`);
  }

  function parseGlob(line) {
    line = line.trim().replace(/\\/g, "/");
    if (!line || line.startsWith("#")) return null;
    const negate = line.startsWith("!");
    if (negate) line = line.slice(1);
    const dirOnly = line.endsWith("/");
    line = line.replace(/\/+$/, "");
    const anchored = line.includes("/");
    line = line.replace(/^\/+/, "");
    if (!line) return null;
    return { negate, dirOnly, anchored, rx: globRegex(line) };
  }

  const parseGlobs = (lines) => lines.map(parseGlob).filter(Boolean);

  function globMatches(g, path) {
    const parts = path.split("/");
    for (let i = 0; i < parts.length; i++) {
      if (g.dirOnly && i === parts.length - 1) break;
      if (g.anchored ? g.rx.test(parts.slice(0, i + 1).join("/")) : g.rx.test(parts[i])) return true;
    }
    return false;
  }

  function ignored(globs, path) {
    let hit = false;
    for (const g of globs) if (globMatches(g, path)) hit = !g.negate;
    return hit;
  }

  const DEFAULT_DIR_GLOBS = parseGlobs(DEFAULT_EXCLUDE_DIRS);
  const DEFAULT_FILE_GLOBS = parseGlobs(DEFAULT_EXCLUDE_FILES);

  // true for a folder name that is never worth walking into
  const isDefaultExcludedDir = (name) => DEFAULT_DIR_GLOBS.some((g) => g.rx.test(name));

  const splitLines = (text) => (text || "").split(/\r?\n/);
  const splitList = (text) => (text || "").split(/[,\s]+/).filter(Boolean);

  // rules: {include: [..lines], exclude: [..lines], extensions: [".md"] | null (default) | [] (any), defaultExcludes,
  //         gitignore: text, roots: [top-level names, "." for the top], excludeParts: [lower-case fragments]}
  function makeFilter(rules = {}) {
    const exts = rules.extensions == null ? DEFAULT_EXTENSIONS
      : rules.extensions.map((e) => "." + e.trim().toLowerCase().replace(/^\.+/, "")).filter((e) => e !== ".");
    return {
      include: parseGlobs(rules.include || []),
      exclude: parseGlobs(rules.exclude || []),
      extensions: exts,
      defaultExcludes: rules.defaultExcludes !== false,
      gitignore: parseGlobs(splitLines(rules.gitignore || "")),
      roots: (rules.roots || []).map((r) => r.normalize("NFC")),
      excludeParts: (rules.excludeParts || []).map((p) => p.toLowerCase()),
    };
  }

  const decision = (path, outcome, reason) => (reason ? { path, outcome, reason } : { path, outcome });

  function checkPath(flt, raw, size) {
    let path;
    try { path = normalizePath(raw); } catch (e) {
      if (!(e instanceof PathError)) throw e;
      return decision(String(raw).slice(0, 200), "rejected", `invalid-path:${e.message}`);
    }
    const lower = path.toLowerCase();
    if (flt.excludeParts.some((p) => lower.includes(p))) return decision(path, "rejected", "excluded-source");
    if (SECRET_PATH.test(path)) return decision(path, "rejected", "secret-file-name");
    if (flt.defaultExcludes) {
      const parts = path.split("/");
      if (parts.slice(0, -1).some((p) => DEFAULT_DIR_GLOBS.some((g) => g.rx.test(p)))
        || DEFAULT_FILE_GLOBS.some((g) => g.rx.test(parts[parts.length - 1]))) {
        return decision(path, "skipped", "default-exclude");
      }
    }
    if (flt.extensions.length) {
      const base = lower.slice(lower.lastIndexOf("/") + 1);
      const dot = base.lastIndexOf(".");
      const ext = dot > 0 ? base.slice(dot) : "";
      if (!flt.extensions.includes(ext)) return decision(path, "skipped", "extension");
    }
    if (flt.gitignore.length && ignored(flt.gitignore, path)) return decision(path, "skipped", "gitignored");
    if (flt.roots.length && !flt.roots.includes(path.includes("/") ? path.split("/")[0] : ".")) {
      return decision(path, "skipped", "not-included");
    }
    if (flt.include.length && !flt.include.some((g) => globMatches(g, path))) return decision(path, "skipped", "not-included");
    if (flt.exclude.length && flt.exclude.some((g) => globMatches(g, path))) return decision(path, "skipped", "excluded-by-rule");
    if (size != null && size > MAX_FILE_BYTES) return decision(path, "rejected", "too-large");
    return decision(path, "indexed");
  }

  function checkContent(path, text) {
    if (text.includes("\x00")) return decision(path, "skipped", "binary");
    if (new TextEncoder().encode(text).length > MAX_FILE_BYTES) return decision(path, "rejected", "too-large");
    const hits = scanSecrets(text);
    if (hits.length) return decision(path, "rejected", hits.join(", "));
    if (!text.trim()) return decision(path, "skipped", "empty");
    return decision(path, "indexed");
  }

  // The top-level folder a normalised path belongs to ("." for a file at the top).
  const rootOf = (path) => (path.includes("/") ? path.split("/")[0] : ".");

  const api = { MAX_FILE_BYTES, DEFAULT_EXTENSIONS, PRIVATE_LOOKING, PathError, normalizePath, normalizeText, scanSecrets,
    makeFilter, checkPath, checkContent, isDefaultExcludedDir, splitLines, splitList, rootOf };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.HippoFolder = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
