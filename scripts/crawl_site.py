#!/usr/bin/env python3
"""Crawl a website into a Hippocampus collection: probe it, create the collection, crawl, wait, and check the result.

  scripts/crawl_site.py https://docs.example.com/guide/ --collection example-docs --plan
  scripts/crawl_site.py https://docs.example.com/guide/ --collection example-docs --yes --query "how do I install it?"

First it asks the service to probe the site (POST /v1/crawl/probe: robots.txt, whether the page needs the browser engine, the
size from a sitemap, and a suggested depth, pattern and engine) and prints what it found. --plan stops there and sends nothing
else. Otherwise it asks you to confirm (or pass --yes), creates the collection if it does not exist (shared, or restricted with
--restricted), starts the crawl with the suggested settings unless you set --depth, --pattern or --engine, streams the job log,
and finishes by listing the collection's totals and running each --query as a search.

It stops without crawling when robots.txt disallows the start URL. It never deletes anything and does not read the site's terms
of use: read docs/User_Guide/legal-and-ethics.md first.

The token comes from HIPPO_TOKEN, or from NAME in token.env (--token-var NAME), or is typed hidden. It is never printed.
An account with a write grant on the collection is enough; creating a restricted collection needs an admin token.

Options: --url (or HIPPO_URL), --collection, --name, --restricted, --depth, --pattern, --engine, --token-var, --query (repeatable),
--plan, --yes.
Exit status: 0 done, 1 the crawl failed or found nothing, 2 could not run or declined.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from token_env import env_path, read_vars


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


def read_token(var: str | None) -> str | None:
    """--token-var wins over the environment; with neither, ask (hidden)."""
    if var:
        values = read_vars(env_path())
        if var not in values:
            print(f"{var} is not set in {env_path()}", file=sys.stderr)
            sys.exit(2)
        return values[var]
    if os.environ.get("HIPPO_TOKEN"):
        return os.environ["HIPPO_TOKEN"]
    try:
        return getpass.getpass("Token (write grant on the collection, or admin; hidden): ").strip() or None
    except (EOFError, KeyboardInterrupt):
        print("\nno token: set HIPPO_TOKEN, or pass --token-var NAME (see scripts/token_env.py list)", file=sys.stderr)
        sys.exit(2)


def stream_log(url: str, token: str | None, job_id: str) -> str:
    """Print the job's log as it arrives; return the final status."""
    req = urllib.request.Request(url.rstrip("/") + f"/v1/jobs/{job_id}/events", headers={"X-Hippo-Token": token or ""})
    status, event = "error", ""
    with urllib.request.urlopen(req, timeout=3600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                if event == "end":
                    status = line[5:].strip()
                else:
                    print("  " + line[5:].strip())
    return status


def show_probe(p: dict) -> None:
    r, sp, sm, sg = p["robots"], p["start_page"], p["sitemap"], p["suggest"]
    print(f"robots.txt : {'found' if r['found'] else 'none'}, start URL {'allowed' if r['allowed'] else 'DISALLOWED'}"
          + (f", Crawl-delay {r['crawl_delay']:g}s" if r["crawl_delay"] else "")
          + (f", Content-Signal: {r['content_signal']}" if r["content_signal"] else ""))
    if sp["fetched"]:
        print(f"start page : HTTP {sp['status']}, {sp['words']} words, {sp['same_host_links']} same-host links")
    if sm["found"]:
        print("sitemap    : " + (f"an index of {sm['sitemaps']} sitemaps" if sm["index"]
                                  else f"{sm['urls']} URLs, {sm['matching']} match the pattern"))
    else:
        print("sitemap    : none found")
    print(f"suggested  : engine={sg['engine']} depth={sg['depth']} ({sg['depth_basis']}) pattern={sg['match_pattern']!r}"
          + (f", about {sg['pages_estimate']} pages, {sg['fetch_seconds']}s of fetching" if sg["pages_estimate"] else "")
          + f", cap {sg['max_pages']} pages")
    for w in p["warnings"]:
        print(f"  ! {w}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("start_url")
    ap.add_argument("--url", default=os.environ.get("HIPPO_URL", "http://127.0.0.1:8090"))
    ap.add_argument("--collection", required=True)
    ap.add_argument("--name", default=None, help="display name for a new collection")
    ap.add_argument("--restricted", action="store_true", help="create the collection as restricted (admin token)")
    ap.add_argument("--depth", type=int, choices=[1, 2, 3, 4, 5], default=None)
    ap.add_argument("--pattern", default=None, help="regex a link must match to be followed")
    ap.add_argument("--engine", choices=["static", "browser"], default=None)
    ap.add_argument("--token-var", default=None, help="read the token from this variable in token.env")
    ap.add_argument("--query", action="append", default=[], help="a question to search for afterwards (repeatable)")
    ap.add_argument("--plan", action="store_true", help="probe the site and stop")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args()

    if not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", a.collection):
        print(f"invalid collection slug {a.collection!r} (lowercase letters, digits and dashes, up to 64)", file=sys.stderr)
        return 2
    token = read_token(a.token_var)

    st, probe = api(a.url, token, "POST", "/v1/crawl/probe", {"url": a.start_url, "match_pattern": a.pattern})
    if st != 200:
        print(f"probe failed: HTTP {st} {probe.get('detail', '')}", file=sys.stderr)
        return 2
    show_probe(probe)
    if not probe["robots"]["allowed"]:
        print("robots.txt disallows this URL: not crawling.", file=sys.stderr)
        return 2
    sg = probe["suggest"]
    depth, pattern, engine = a.depth or sg["depth"], a.pattern or sg["match_pattern"], a.engine or sg["engine"]
    print(f"\nplan       : crawl {a.start_url} into '{a.collection}' with engine={engine} depth={depth} pattern={pattern!r}")
    if a.plan:
        print("(plan only: nothing was crawled or created)")
        return 0
    if not a.yes and input("Crawl copies other people's content (docs/User_Guide/legal-and-ethics.md). Go ahead? [y/N] ").lower() != "y":
        print("not crawling.")
        return 2

    st, body = api(a.url, token, "GET", "/v1/collections")
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

    st, job = api(a.url, token, "POST", "/v1/crawl", {"url": a.start_url, "collection": a.collection, "depth": depth,
                                                      "match_pattern": pattern, "engine": engine})
    if st != 202:
        print(f"cannot start the crawl: HTTP {st} {job.get('detail', '')}", file=sys.stderr)
        return 2
    print(f"job {job['job_id']} started (embedding runs on the CPU and can take minutes after the last page)")
    status = stream_log(a.url, token, job["job_id"])
    st, view = api(a.url, token, "GET", f"/v1/jobs/{job['job_id']}")
    if status != "done":
        print(f"crawl {status}: {view.get('error')}", file=sys.stderr)
        return 1
    res = view.get("result") or {}
    print(f"crawl done: {res.get('pages')} pages, {res.get('added')} added, {res.get('changed')} changed, "
          f"{res.get('unchanged')} unchanged")

    st, body = api(a.url, token, "GET", "/v1/collections")
    row = next((c for c in body.get("collections", []) if c["slug"] == a.collection), None)
    if row:
        print(f"collection {a.collection}: {row['source_count']} sources, {row['chunk_count']} chunks")
    for q in a.query:
        st, out = api(a.url, token, "POST", "/v1/search", {"query": q, "collection": a.collection, "top_k": 3})
        print(f"\nsearch: {q}")
        for hit in out.get("results", []) if st == 200 else []:
            print(f"  {hit.get('score', 0):.2f}  {hit.get('uri')}  {hit.get('header_path') or ''}")
        if st != 200:
            print(f"  search failed: HTTP {st}")
    return 0 if res.get("pages") else 1


if __name__ == "__main__":
    sys.exit(main())
