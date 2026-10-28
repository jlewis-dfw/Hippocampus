#!/usr/bin/env python3
"""Check that a Hippocampus setup works end to end, and say plainly what does not.

  scripts/verify_setup.py                                       the service, the token, the collections
  scripts/verify_setup.py --token-var HIPPO_TOKEN_READER --collection hippocampus-docs --query "how do I back up?"
  scripts/verify_setup.py --mcp                                  also look for the MCP server in `claude mcp list`
  scripts/verify_setup.py --json                                 machine-readable result

Checks, each PASS, FAIL, WARN (worth knowing, not a failure: no collection yet) or SKIP: the service answers (GET /v1/status); the token is accepted (GET /v1/whoami); the collections it
can read and their sizes; a search over --collection returns at least one result (--query); the MCP server is registered and
connected (--mcp). The token comes from HIPPO_TOKEN or from --token-var in token.env and is never printed.
Exit status: 0 nothing failed, 1 a check failed, 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from token_env import env_path, read_vars


def call(url: str, token: str | None, method: str, path: str, body: dict | None = None):
    headers = {"Content-Type": "application/json", **({"X-Hippo-Token": token} if token else {})}
    req = urllib.request.Request(url.rstrip("/") + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (urllib.error.URLError, OSError):
        return 0, {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("HIPPO_URL", "http://127.0.0.1:8090"))
    ap.add_argument("--token-var", default=None)
    ap.add_argument("--collection", default=None)
    ap.add_argument("--query", default=None)
    ap.add_argument("--mcp", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    token = os.environ.get("HIPPO_TOKEN")
    if a.token_var:
        token = read_vars(env_path()).get(a.token_var)
        if not token:
            print(f"{a.token_var} is not set in {env_path()}", file=sys.stderr)
            return 2
    checks: list[dict] = []

    def add(name: str, status: str, detail: str = "") -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    st, body = call(a.url, None, "GET", "/v1/status")
    add("service answers", "PASS" if st == 200 else "FAIL", f"{a.url} HTTP {st}" if st else f"{a.url} did not answer")
    if st == 200:
        st, who = call(a.url, token, "GET", "/v1/whoami")
        if token:
            add("token accepted", "PASS" if st == 200 else "FAIL",
                f"{who.get('name')} admin={who.get('admin')} read={who.get('read')} write={who.get('write')}" if st == 200 else f"HTTP {st}")
        else:
            add("token accepted", "SKIP", "no token given: checking as anonymous")
        st, cols = call(a.url, token, "GET", "/v1/collections")
        rows = cols.get("collections", []) if st == 200 else []
        add("collections readable", "PASS" if rows else "WARN",
            ", ".join(f"{c['slug']} ({c['source_count']} sources, {c['chunk_count']} chunks)" for c in rows) or "none yet: index something")
        if a.query:
            payload = {"query": a.query, "top_k": 3, **({"collection": a.collection} if a.collection else {})}
            st, res = call(a.url, token, "POST", "/v1/search", payload)
            hits = res.get("results", []) if st == 200 else []
            add("search returns results", "PASS" if hits else "FAIL",
                "; ".join(f"{h['score']:.2f} {h['uri']}" for h in hits) or f"HTTP {st}, no results")
    if a.mcp:
        if shutil.which("claude") is None:
            add("MCP server connected", "SKIP", "the claude command is not on PATH")
        else:
            out = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True, timeout=120).stdout
            line = next((ln for ln in out.splitlines() if ln.lower().startswith("hippocampus")), "")
            add("MCP server connected", "PASS" if re.search(r"connected|✓", line, re.I) and "fail" not in line.lower() else "FAIL",
                line.strip() or "hippocampus is not registered")
    failed = any(c["status"] == "FAIL" for c in checks)
    if a.json:
        print(json.dumps({"ok": not failed, "checks": checks}, indent=1))
    else:
        for c in checks:
            print(f"{c['status']:<5} {c['check']}: {c['detail']}")
        print("RESULT:", "problems found" if failed else "ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
