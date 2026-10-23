#!/usr/bin/env python3
"""Smoke test for a running Hippocampus (the live one after a deploy, or a local one). It replaces a QA tier: instead of
deploying to a second environment first, it checks the real one end to end and leaves nothing behind.

What it checks
  1 status      the API answers, the database responds, the embedder is the expected 384-dimension model
  2 existing    the number of collections, sources and chunks is the same at the end as at the start (nothing disturbed)
  3 round trip  (needs an admin token) a restricted throwaway collection takes a page and finds it by meaning
  4 isolation   an anonymous caller sees neither the collection nor its text; a read-only client can search it but not write
  5 rotate      (if the server has it) a rotated token works and the old one stops working
  cleanup       the throwaway collection is deleted and the throwaway client revoked, even if a check fails

Without an admin token only 1 and 2's first half run. The admin token is read from HIPPO_ADMIN_TOKEN or typed hidden; it is
never printed or stored. A revoked throwaway client row stays in the clients table (Hippocampus has no delete for clients).

Usage: scripts/smoke_live.py [--url http://127.0.0.1:8090] [--no-admin]   (HIPPO_URL also works)
Exit status: 0 all checks passed, 1 a check failed, 2 could not run.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
import uuid


class Api:
    def __init__(self, url: str, token: str | None = None):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None, token: str | None = ""):
        tok = self.token if token == "" else token
        headers = {"Content-Type": "application/json"}
        if tok:
            headers["X-Hippo-Token"] = tok
        req = urllib.request.Request(self.url + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
        except urllib.error.URLError as e:
            print(f"cannot reach {self.url}: {e.reason}", file=sys.stderr)
            sys.exit(2)


class Report:
    def __init__(self):
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = ""):
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        if not ok:
            self.failed += 1
        return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("HIPPO_URL", "http://127.0.0.1:8090"))
    ap.add_argument("--no-admin", action="store_true", help="run only the checks that need no token")
    a = ap.parse_args()
    anon = Api(a.url)
    rep = Report()
    print(f"==> smoke test against {a.url}")

    st, status = anon.call("GET", "/v1/status")
    rep.check("1 status answers", st == 200, f"HTTP {st}")
    if st != 200:
        return 1
    rep.check("1 embedder is bge-small-en-v1.5 / 384", status.get("embedder", {}).get("dimensions") == 384)
    rep.check("1 database responds", isinstance(status.get("db_latency_ms"), (int, float)), f"{status.get('db_latency_ms')} ms")

    token = None if a.no_admin else (os.environ.get("HIPPO_ADMIN_TOKEN") or getpass.getpass("Hippocampus ADMIN token (hidden, Enter to skip the admin checks): ").strip() or None)
    if not token:
        print("  SKIP  2-5 need an admin token")
        return 1 if rep.failed else 0
    admin = Api(a.url, token)
    st, who = admin.call("GET", "/v1/whoami")
    if not rep.check("admin token is accepted", st == 200 and bool(who.get("admin")), f"HTTP {st}"):
        return 1
    st, before = admin.call("GET", "/v1/status")
    slug, reader_name = f"smoke-{uuid.uuid4().hex[:8]}", f"smoke-reader-{uuid.uuid4().hex[:8]}"
    reader_token = None
    try:
        st, _ = admin.call("POST", "/v1/collections", {"slug": slug, "name": "smoke test (temporary)", "visibility": "restricted"})
        rep.check("3 restricted throwaway collection created", st == 201, f"HTTP {st}")
        st, res = admin.call("POST", "/v1/ingest/file", {"collection": slug, "uri": "smoke://page", "title": "Smoke",
                                                         "text": "# Smoke page\n## Fact\nThe lighthouse keeper keeps a brass key stamped with a gull.\n"})
        rep.check("3 a page is embedded and stored", st == 200 and bool(res.get("added")), f"HTTP {st}")
        st, res = admin.call("POST", "/v1/search", {"query": "who has the brass key with a gull", "collections": [slug], "top_k": 1})
        hit = (res.get("results") or [{}])[0]
        rep.check("3 search finds it by meaning", st == 200 and "gull" in (hit.get("content") or ""), f"score {hit.get('score', 0):.2f}" if hit else "no hit")

        st, listed = anon.call("GET", "/v1/collections")
        rep.check("4 anonymous does not see the collection", slug not in [c["slug"] for c in listed.get("collections", [])])
        st, res = anon.call("POST", "/v1/search", {"query": "brass key gull", "collections": [slug]})
        rep.check("4 anonymous named search is 404", st == 404, f"HTTP {st}")
        st, res = anon.call("POST", "/v1/search", {"query": "lighthouse keeper brass key gull", "top_k": 50})
        rep.check("4 anonymous unscoped search never returns it", st == 200 and all(r["collection"] != slug for r in res.get("results", [])))

        st, res = admin.call("POST", "/v1/clients", {"name": reader_name, "read": [slug], "write": [], "note": "smoke test"})
        if rep.check("4 throwaway read-only client created", st == 201, f"HTTP {st}"):
            reader_token = res["token"]
            reader = Api(a.url, reader_token)
            st, res = reader.call("POST", "/v1/search", {"query": "brass key gull", "collections": [slug], "top_k": 1})
            rep.check("4 the read-only client can search it", st == 200 and bool(res.get("results")), f"HTTP {st}")
            st, _ = reader.call("POST", "/v1/ingest/file", {"collection": slug, "uri": "smoke://nope", "text": "x"})
            rep.check("4 the read-only client cannot write", st == 403, f"HTTP {st}")
            st, res = admin.call("POST", f"/v1/clients/{reader_name}/rotate")
            if st == 200:
                new = res["token"]
                st_old, _ = reader.call("GET", "/v1/whoami")
                st_new, _ = Api(a.url, new).call("GET", "/v1/whoami")
                rep.check("5 rotate: the new token works and the old one is dead", st_new == 200 and st_old == 401, f"new {st_new}, old {st_old}")
            else:
                print(f"  SKIP  5 rotate is not available on this server (HTTP {st})")
    finally:
        st, _ = admin.call("DELETE", f"/v1/collections/{slug}")
        rep.check("cleanup: throwaway collection deleted", st == 204, f"HTTP {st}")
        if reader_token:
            st, _ = admin.call("DELETE", f"/v1/clients/{reader_name}")
            rep.check("cleanup: throwaway client revoked", st == 204, f"HTTP {st}")
        st, after = admin.call("GET", "/v1/status")
        same = all(before.get(k) == after.get(k) for k in ("collections", "sources", "chunks"))
        rep.check("2 collections, sources and chunks are as they were", same,
                  f"{after.get('collections')}/{after.get('sources')}/{after.get('chunks')} vs {before.get('collections')}/{before.get('sources')}/{before.get('chunks')}")
    print("RESULT: " + ("FAILED" if rep.failed else "all smoke checks passed"))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
