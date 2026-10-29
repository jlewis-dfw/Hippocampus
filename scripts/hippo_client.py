"""Shared helpers for the scripts that talk to a running Hippocampus over its REST API (stdlib only, imported, not run)."""
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

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def add_connection_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--url", default=os.environ.get("HIPPO_URL", "http://127.0.0.1:8090"))
    ap.add_argument("--token-var", default=None, help="read the token from this variable in token.env")


def api(url: str, token: str | None, method: str, path: str, body: dict | None = None, timeout: int = 120):
    """Returns (HTTP status, parsed JSON or {}). Exits with status 2 when the service cannot be reached."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Hippo-Token"] = token
    req = urllib.request.Request(url.rstrip("/") + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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


def read_token(var: str | None, needs: str = "a write grant on the collection, or admin") -> str | None:
    """--token-var wins over HIPPO_TOKEN; with neither, ask (hidden). The token is never printed."""
    if var:
        values = read_vars(env_path())
        if var not in values:
            print(f"{var} is not set in {env_path()}", file=sys.stderr)
            sys.exit(2)
        return values[var]
    if os.environ.get("HIPPO_TOKEN"):
        return os.environ["HIPPO_TOKEN"]
    try:
        return getpass.getpass(f"Token ({needs}; hidden): ").strip() or None
    except (EOFError, KeyboardInterrupt):
        print("\nno token: set HIPPO_TOKEN, or pass --token-var NAME (see scripts/token_env.py list)", file=sys.stderr)
        sys.exit(2)


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    sys.exit(code)


def detail(body: dict) -> str:
    d = body.get("detail", "")
    return d if isinstance(d, str) else json.dumps(d)


def ensure_collection(url: str, token: str | None, slug: str, name: str | None, restricted: bool) -> None:
    """Create the collection when it does not exist yet (shared, or restricted with an admin token)."""
    if not SLUG_RE.match(slug):
        fail(f"invalid collection slug {slug!r} (lowercase letters, digits and dashes, up to 64)")
    st, body = api(url, token, "GET", "/v1/collections")
    if st == 401:
        fail("the token was rejected (401)")
    if st != 200:
        fail(f"cannot list collections: HTTP {st} {detail(body)}")
    if slug in {c["slug"] for c in body.get("collections", [])}:
        return
    st, body = api(url, token, "POST", "/v1/collections", {
        "slug": slug, "name": name or slug.replace("-", " ").title(), "visibility": "restricted" if restricted else "shared"})
    if st != 201:
        fail(f"cannot create collection {slug}: HTTP {st} {detail(body)}")
    print(f"created collection {slug} ({'restricted' if restricted else 'shared'})")


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


def run_job(url: str, token: str | None, job_id: str) -> tuple[str, dict]:
    """Stream a job to its end; returns (status, the job view with its result or error)."""
    status = stream_log(url, token, job_id)
    _, view = api(url, token, "GET", f"/v1/jobs/{job_id}")
    return status, view


def collection_row(url: str, token: str | None, slug: str) -> dict | None:
    _, body = api(url, token, "GET", "/v1/collections")
    return next((c for c in body.get("collections", []) if c["slug"] == slug), None)


def verify_search(url: str, token: str | None, slug: str, queries: list[str]) -> None:
    """Run each question as a search scoped to the collection and print the top hits."""
    row = collection_row(url, token, slug)
    if row:
        print(f"collection {slug}: {row['source_count']} sources, {row['chunk_count']} chunks")
    for q in queries:
        st, out = api(url, token, "POST", "/v1/search", {"query": q, "collections": [slug], "top_k": 3})
        print(f"\nsearch: {q}")
        for hit in out.get("results", []) if st == 200 else []:
            print(f"  {hit.get('score', 0):.2f}  {hit.get('uri')}  {hit.get('header_path') or ''}")
        if st != 200:
            print(f"  search failed: HTTP {st}")
