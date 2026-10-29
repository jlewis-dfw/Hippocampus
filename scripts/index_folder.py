#!/usr/bin/env python3
"""Index a local folder into a collection and keep it in step: only changed files are re-embedded, files no longer in the folder
are removed from the index (never from disk), secrets are rejected by the server.

  scripts/index_folder.py ~/notes --collection team-notes --plan                 preview what the server would accept (nothing written)
  scripts/index_folder.py ~/notes --collection team-notes --token-var HIPPO_TOKEN_ADMIN --yes --query "how do we back up?"

The folder's label is the source name (--source, default: the folder name): documents are stored as files://<source>/<path>.
Files are sent in batches to POST /v1/ingest/files; the last batch closes the run and removes indexed files that were not sent.
Only files with a text extension (default .md .markdown .txt .rst; --ext adds more) are read, and the usual junk folders (.git,
node_modules, ...) are skipped before sending; the server applies the full rules (secrets, 200,000-byte limit, --exclude globs).

Options: --url, --token-var, --collection, --name, --restricted, --source, --ext, --exclude (repeatable), --plan, --yes, --query.
Exit status: 0 done, 1 some files were rejected or failed, 2 could not run or declined.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hippo_client import add_connection_args, api, detail, ensure_collection, fail, read_token, verify_search

SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "dist", "build", "__pycache__", ".venv", "venv", ".tox"}
DEFAULT_EXT = (".md", ".markdown", ".txt", ".rst")
BATCH_FILES, BATCH_BYTES = 200, 5_000_000


def collect(folder: Path, exts: tuple[str, ...]) -> list[tuple[str, Path]]:
    found = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]) or not p.is_file() or p.suffix.lower() not in exts:
            continue
        found.append((rel.as_posix(), p))
    return found


def batches(files: list[tuple[str, Path]]):
    batch: list[dict] = []
    total = 0
    for rel, p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        if batch and (len(batch) >= BATCH_FILES or total + len(text) > BATCH_BYTES):
            yield batch
            batch, total = [], 0
        batch.append({"path": rel, "content": text, "size": p.stat().st_size})
        total += len(text)
    yield batch


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    add_connection_args(ap)
    ap.add_argument("--collection", required=True)
    ap.add_argument("--name", default=None, help="display name for a new collection")
    ap.add_argument("--restricted", action="store_true", help="create the collection as restricted (admin token)")
    ap.add_argument("--source", default=None, help="the folder's label (default: its name)")
    ap.add_argument("--ext", action="append", default=[], help="an extra extension to index, such as .py (repeatable)")
    ap.add_argument("--exclude", action="append", default=[], help="a gitignore-style glob to skip (repeatable)")
    ap.add_argument("--plan", action="store_true", help="dry run: decide only, write nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--query", action="append", default=[], help="a question to search for afterwards (repeatable)")
    a = ap.parse_args()

    folder = a.folder.expanduser().resolve()
    if not folder.is_dir():
        fail(f"{folder} is not a folder")
    source = a.source or folder.name
    exts = DEFAULT_EXT + tuple(e.lower() if e.startswith(".") else "." + e.lower() for e in a.ext)
    files = collect(folder, exts)
    if not files:
        print(f"no files with {', '.join(exts)} under {folder}")
        return 0
    spec = {"exclude": a.exclude, "extensions": [e.lstrip(".") for e in exts]}
    token = read_token(a.token_var)
    print(f"{len(files)} candidate file(s) under {folder} -> collection {a.collection} as files://{source}/")

    if a.plan:
        _, cols = api(a.url, token, "GET", "/v1/collections")
        if a.collection not in {c["slug"] for c in cols.get("collections", [])}:
            for rel, p in files[:50]:
                print(f"  {rel}  ({p.stat().st_size} bytes)")
            print(f"(collection {a.collection!r} does not exist yet, so the server cannot preview it; it is created when you run for real)")
            return 0
        body = {"collection": a.collection, "source": source, "dry_run": True, "filter": spec,
                "files": [{"path": rel, "size": p.stat().st_size} for rel, p in files[:500]]}
        st, res = api(a.url, token, "POST", "/v1/ingest/files", body)
        if st != 200:
            fail(f"preview failed: HTTP {st} {detail(res)}")
        print("server decision:", res["counts"])
        for d in res["files"]:
            if d.get("outcome") != "indexed":
                print(f"  {d['outcome']:<9}{d['path']}  {d.get('reason') or ''}")
        if len(files) > 500:
            print(f"(previewed the first 500 of {len(files)})")
        print("(plan only: nothing was written)")
        return 0

    if not a.yes and input(f"Index {len(files)} file(s) and remove indexed files of this folder that are gone? [y/N] ").lower() != "y":
        print("not indexing.")
        return 2
    ensure_collection(a.url, token, a.collection, a.name, a.restricted)

    session, totals, problems = None, {}, 0
    chunks = list(batches(files))
    for i, batch in enumerate(chunks):
        last = i == len(chunks) - 1
        st, res = api(a.url, token, "POST", "/v1/ingest/files", {
            "collection": a.collection, "source": source, "files": batch, "filter": spec, "session": session, "final": last},
            timeout=600)
        if st != 200:
            if session:
                api(a.url, token, "DELETE", f"/v1/ingest/files/{session}?reason=client-error")
            fail(f"batch {i + 1}/{len(chunks)} failed: HTTP {st} {detail(res)}", 1)
        session = res.get("session")
        totals = res.get("totals", {})
        problems += sum(1 for f in res.get("files", []) if f.get("outcome") == "rejected")
        print(f"  batch {i + 1}/{len(chunks)}: {len(batch)} file(s)")
        if last and res.get("removed"):
            print(f"  removed from the index (no longer in the folder): {len(res['removed'])}")
    print(f"done: {totals}")
    verify_search(a.url, token, a.collection, a.query)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
