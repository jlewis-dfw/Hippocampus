#!/usr/bin/env python3
"""A read-only inventory of what is indexed, with the things worth a look flagged: empty collections, failed or never-run crawls,
sources that errored, and collections not touched for a while.

  scripts/inventory.py --token-var HIPPO_TOKEN_ADMIN [--stale-days 90]

Reads GET /v1/collections, /v1/collections/<slug>/sources (the newest 1000) and /v1/web-sources, for what the token may read. It
suggests nothing it does it: removal is a decision for the user, in the dashboard.
Exit status: 0 nothing flagged, 1 something flagged, 2 could not run.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hippo_client import add_connection_args, api, detail, fail, read_token


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(ap)
    ap.add_argument("--stale-days", type=int, default=90)
    a = ap.parse_args()
    token = read_token(a.token_var, "an account that can read the collections; an admin sees all")
    st, body = api(a.url, token, "GET", "/v1/collections")
    if st != 200:
        fail(f"cannot list collections: HTTP {st} {detail(body)}")
    _, web = api(a.url, token, "GET", "/v1/web-sources")
    crawls: dict[str, list[dict]] = {}
    for w in web.get("web_sources", []):
        crawls.setdefault(w["collection"], []).append(w)
    cutoff = datetime.now(UTC) - timedelta(days=a.stale_days)
    flags = 0
    for c in body["collections"]:
        slug = c["slug"]
        _, src = api(a.url, token, "GET", f"/v1/collections/{slug}/sources?limit=1000")
        sources = src.get("sources", [])
        newest = max((s["last_indexed_at"] for s in sources), default=None)
        notes = []
        if not c["chunk_count"]:
            notes.append("no chunks")
        bad = [s for s in sources if s["status"] != "indexed" or s["error"]]
        if bad:
            notes.append(f"{len(bad)} source(s) not indexed or with an error, e.g. {bad[0]['uri']}: {bad[0]['error']}")
        if newest and datetime.fromisoformat(newest) < cutoff:
            notes.append(f"nothing indexed since {newest[:10]} (over {a.stale_days} days)")
        for w in crawls.get(slug, []):
            if w["last_status"] != "ok":
                notes.append(f"saved crawl {w['id'][:8]} {w['url']}: {w['last_status'] or 'never run'}")
        print(f"{slug:<24}{c['visibility']:<11}{c['source_count']:>6} sources {c['chunk_count']:>7} chunks  "
              f"{'/'.join(c['source_types']) or '-':<14} last {newest[:10] if newest else '-'}")
        for n in notes:
            print(f"    ! {n}")
        flags += bool(notes)
    print(f"\n{len(body['collections'])} collection(s), {flags} with something to look at")
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
