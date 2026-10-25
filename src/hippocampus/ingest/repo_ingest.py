"""Repomix pack ingester with a secret-scanning pre-flight (HIPPO-E03-F2-S01).

Consumes the text output of ``repomix --style xml`` or ``--style markdown``, splits it into files, and
runs every file through a regex pre-flight BEFORE anything is chunked or embedded. A file that matches a
secret shape, or whose path looks like a credential store, is rejected whole (never redacted-and-embedded).
Reports name the file and the rule, never the matched text.

The regex set is deliberately small and accepted as weaker than a maintained scanner (gitleaks,
detect-secrets); see AGENTS.md section 4. Pack content is untrusted data.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..core.diff import Document, sync_collection
from .sync_core import (  # noqa: F401  (re-exported: the rules live in sync_core)
    MAX_FILE_BYTES,
    MAX_FILES,
    SECRET_PATH_RE,
    SECRET_RULES,
    exclude_path_parts,
    scan_secrets,
)

_log = logging.getLogger("hippocampus")
_XML_FILE_RE = re.compile(r'<file path="([^"]+)">\n(.*?)\n</file>', re.DOTALL)
_MD_FILE_RE = re.compile(r"^## File: ([^\n]+)\n(`{3,})[^\n]*\n(.*?)\n\2[ \t]*$", re.DOTALL | re.MULTILINE)
_LANG = {".py": "python", ".cs": "csharp", ".ts": "typescript", ".js": "javascript", ".json": "json",
         ".yml": "yaml", ".yaml": "yaml", ".sh": "bash", ".tf": "hcl", ".sql": "sql", ".razor": "razor",
         ".html": "html", ".css": "css", ".toml": "toml", ".xml": "xml", ".csproj": "xml"}


def parse_pack(pack: str) -> list[tuple[str, str]]:
    """(path, content) for every file in a Repomix xml or markdown pack."""
    if '<file path="' in pack:
        return [(m.group(1), m.group(2)) for m in _XML_FILE_RE.finditer(pack)]
    if "## File: " in pack:
        return [(m.group(1).strip(), m.group(3)) for m in _MD_FILE_RE.finditer(pack)]
    raise ValueError("unrecognised pack format (use `repomix --style xml` or `--style markdown`)")


def _to_markdown(path: str, content: str) -> str:
    if path.lower().endswith((".md", ".markdown")):
        return content
    longest = max((len(m) for m in re.findall(r"`+", content)), default=0)
    fence = "`" * max(3, longest + 1)
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return f"# {path}\n\n{fence}{_LANG.get(ext, '')}\n{content}\n{fence}"


@dataclass
class PackScan:
    documents: dict[str, Document] = field(default_factory=dict)
    rejected: list[dict] = field(default_factory=list)  # {"path", "reasons"}; never the secret itself


def scan_pack(repo: str, pack: str) -> PackScan:
    """Parse and pre-flight a pack. Only files that pass end up in ``documents`` (uri ``repo://<repo>/<path>``)."""
    files = parse_pack(pack)
    if len(files) > MAX_FILES:
        raise ValueError(f"pack has {len(files)} files (limit {MAX_FILES})")
    out = PackScan()
    for path, content in files:
        reasons: list[str] = []
        if any(p in path.lower() for p in exclude_path_parts()):
            reasons.append("excluded-source")
        if SECRET_PATH_RE.search(path):
            reasons.append("secret-file-name")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            reasons.append("too-large")
        if not reasons:
            reasons = scan_secrets(content)
        if reasons:
            out.rejected.append({"path": path, "reasons": reasons})
            continue
        if content.strip():
            out.documents[f"repo://{repo}/{path}"] = Document(path, _to_markdown(path, content))
    return out


def ingest_pack(collection: str, repo: str, pack: str, store, embedder,
                log: Callable[[str], None] = lambda m: None) -> dict:
    """Pre-flight, then sync the clean files. Sources of this repo that are no longer in the pack, including
    files now rejected for a secret, are removed from the collection."""
    if any(p in repo.lower() for p in exclude_path_parts()):
        raise ValueError("this source is excluded from ingestion")
    scan = scan_pack(repo, pack)
    for r in scan.rejected:
        log(f"rejected {r['path']}: {', '.join(r['reasons'])}")
        _log.warning("file rejected: %s (rule %s)", r["path"], ", ".join(r["reasons"]))
    log(f"embedding {len(scan.documents)} files")
    result = sync_collection(collection, scan.documents, store, embedder, prune=False)
    prefix = f"repo://{repo}/"
    stale = [u for u in store.source_hashes(collection) if u.startswith(prefix) and u not in scan.documents]
    for uri in stale:
        store.delete_source(collection, uri)
    log(f"done: {len(result.added)} added, {len(result.changed)} changed, {len(result.unchanged)} unchanged, "
        f"{len(stale)} removed, {len(scan.rejected)} rejected")
    return {"files": len(scan.documents), "added": len(result.added), "changed": len(result.changed),
            "unchanged": len(result.unchanged), "removed": len(stale), "rejected": scan.rejected}
