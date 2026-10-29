#!/usr/bin/env python3
"""The saved crawls (web sources): list them, re-run one, or count the pages a depth change would remove. Never removes anything.

  scripts/crawls.py list [--token-var NAME]                      id, collection, start URL, depth, pattern, engine, pages, last result
  scripts/crawls.py recrawl ID [--token-var NAME]                crawl again with the saved settings (unchanged pages are skipped); streams the log
  scripts/crawls.py prune-preview ID --depth N [--token-var NAME]   how many pages found deeper than N a prune would remove (dry run)

ID may be the first characters of the id shown by ``list``. Removing pages is a separate, confirmed step in the dashboard
(collection view, "Remove deeper pages"): this script only ever sends ``dry_run: true``.
Exit status: 0 ok, 1 the job failed, 2 could not run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hippo_client import add_connection_args, api, detail, fail, read_token, run_job


def find(url: str, token: str | None, prefix: str) -> dict:
    st, body = api(url, token, "GET", "/v1/web-sources")
    if st != 200:
        fail(f"cannot list saved crawls: HTTP {st} {detail(body)}")
    hits = [w for w in body["web_sources"] if w["id"].startswith(prefix)]
    if len(hits) != 1:
        fail(f"{len(hits)} saved crawls match {prefix!r}; run `list` and use more characters" if hits else f"no saved crawl matches {prefix!r}")
    return hits[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "recrawl", "prune-preview"])
    ap.add_argument("id", nargs="?")
    ap.add_argument("--depth", type=int, choices=range(1, 6), metavar="1-5")
    add_connection_args(ap)
    a = ap.parse_args()
    token = read_token(a.token_var, "any account that can read the collection; recrawl needs a write grant")

    if a.action == "list":
        st, body = api(a.url, token, "GET", "/v1/web-sources")
        if st != 200:
            fail(f"cannot list saved crawls: HTTP {st} {detail(body)}")
        rows = body["web_sources"]
        for w in rows:
            print(f"{w['id'][:8]}  {w['collection']:<20} depth {w['depth']} {w['engine']:<7} {w['pages']:>4} pages  "
                  f"{w['last_status'] or 'never run':<9} {w['url']}  pattern={w['match_pattern']!r}")
        print(f"{len(rows)} saved crawl(s)")
        return 0
    if not a.id:
        fail(f"{a.action} needs an ID (see `list`)")
    w = find(a.url, token, a.id)
    if a.action == "prune-preview":
        if a.depth is None:
            fail("prune-preview needs --depth N")
        st, res = api(a.url, token, "POST", f"/v1/web-sources/{w['id']}/prune", {"depth": a.depth, "dry_run": True})
        if st != 200:
            fail(f"preview failed: HTTP {st} {detail(res)}")
        print(f"{w['url']} in {w['collection']}: {res['pages']} page(s) found deeper than {a.depth} would be removed (dry run: nothing was).")
        return 0
    st, job = api(a.url, token, "POST", f"/v1/web-sources/{w['id']}/crawl")
    if st != 202:
        fail(f"cannot start the crawl: HTTP {st} {detail(job)}")
    print(f"re-crawling {w['url']} into {w['collection']} (job {job['job_id']})")
    status, view = run_job(a.url, token, job["job_id"])
    if status != "done":
        fail(f"crawl {status}: {view.get('error')}", 1)
    print(f"done: {view.get('result')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
