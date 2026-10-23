#!/usr/bin/env python3
"""Index a folder of Markdown files into a Hippocampus collection: by default this repository's own docs/ folder, so the
documentation becomes searchable with the service it describes.

  scripts/index_docs.py                         docs/ into the collection "hippocampus-docs" at http://127.0.0.1:8090
  scripts/index_docs.py --plan                  list what would be indexed; changes nothing
  scripts/index_docs.py --dir notes --collection team-notes --restricted

What it does: creates the collection if it does not exist (shared, or restricted with --restricted), then pushes every *.md file
under --dir (recursively) with POST /v1/ingest/file. The source URI is "<prefix><path relative to --dir>", for example
"docs/User_Guide/ingesting.md", and the title is the file's first "# " heading. Unchanged files are skipped by the server, so
running it again after an edit re-embeds only the files that changed. Files over 1,000,000 bytes are skipped with a warning.
It never deletes anything: a file you removed stays in the collection until the collection is deleted (the API has no
per-source delete).

The token (an admin token, or an account with a write grant on the collection) comes from HIPPO_TOKEN, or is typed hidden.
It is never printed. Creating a restricted collection needs an admin token.

Options: --url (or HIPPO_URL), --dir, --collection, --name, --prefix, --restricted, --plan.
Exit status: 0 all files indexed, 1 some failed, 2 could not run.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

MAX_BYTES = 1_000_000


def api(url: str, token: str | None, method: str, path: str, body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Hippo-Token"] = token
    req = urllib.request.Request(url.rstrip("/") + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except urllib.error.URLError as e:
        print(f"cannot reach {url}: {e.reason}", file=sys.stderr)
        sys.exit(2)


def title_of(text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+?)\s*#*\s*$", text, re.MULTILINE)
    return (m.group(1) if m else fallback)[:512]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("HIPPO_URL", "http://127.0.0.1:8090"))
    ap.add_argument("--dir", type=Path, default=Path(__file__).resolve().parent.parent / "docs")
    ap.add_argument("--collection", default="hippocampus-docs")
    ap.add_argument("--name", default=None, help="display name for a new collection")
    ap.add_argument("--prefix", default=None, help="source URI prefix (default: the folder name and a slash)")
    ap.add_argument("--restricted", action="store_true", help="create the collection as restricted (admin token)")
    ap.add_argument("--plan", action="store_true", help="list the files and stop")
    a = ap.parse_args()

    if not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", a.collection):
        print(f"invalid collection slug {a.collection!r} (lowercase letters, digits and dashes, up to 64)", file=sys.stderr)
        return 2
    if not a.dir.is_dir():
        print(f"{a.dir} is not a folder", file=sys.stderr)
        return 2
    prefix = a.prefix if a.prefix is not None else a.dir.name + "/"
    files = sorted(p for p in a.dir.rglob("*.md") if p.is_file())
    if not files:
        print(f"no Markdown files under {a.dir}")
        return 0
    print(f"{len(files)} Markdown file(s) under {a.dir} -> collection {a.collection} at {a.url}")
    if a.plan:
        for p in files:
            print(f"  {prefix}{p.relative_to(a.dir).as_posix()}  ({p.stat().st_size} bytes)")
        print("(plan only: nothing was sent)")
        return 0

    token = os.environ.get("HIPPO_TOKEN") or getpass.getpass("Token (admin, or a write grant on the collection; hidden): ").strip() or None

    st, body = api(a.url, token, "GET", "/v1/collections")
    if st == 401:
        print("the token was rejected (401)", file=sys.stderr)
        return 2
    if st != 200:
        print(f"cannot list collections: HTTP {st} {body.get('detail', '')}", file=sys.stderr)
        return 2
    if a.collection not in {c["slug"] for c in body.get("collections", [])}:
        st, body = api(a.url, token, "POST", "/v1/collections", {
            "slug": a.collection, "name": a.name or a.collection.replace("-", " ").title(),
            "visibility": "restricted" if a.restricted else "shared"})
        if st != 201:
            print(f"cannot create collection {a.collection}: HTTP {st} {body.get('detail', '')}", file=sys.stderr)
            return 2
        print(f"created collection {a.collection} ({'restricted' if a.restricted else 'shared'})")

    added = changed = unchanged = failed = skipped = 0
    for p in files:
        uri = prefix + p.relative_to(a.dir).as_posix()
        if p.stat().st_size > MAX_BYTES:
            print(f"  skip   {uri}: larger than {MAX_BYTES} bytes")
            skipped += 1
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        st, res = api(a.url, token, "POST", "/v1/ingest/file",
                      {"collection": a.collection, "uri": uri, "title": title_of(text, p.stem), "text": text})
        if st != 200:
            print(f"  FAIL   {uri}: HTTP {st} {res.get('detail', '')}")
            failed += 1
            continue
        state = "added" if res.get("added") else "changed" if res.get("changed") else "unchanged"
        added += state == "added"
        changed += state == "changed"
        unchanged += state == "unchanged"
        if state != "unchanged":
            print(f"  {state:<10}{uri}")
    print(f"done: {added} added, {changed} changed, {unchanged} unchanged, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
