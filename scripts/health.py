#!/usr/bin/env python3
"""One-screen health check of the running stack: containers, the service, jobs, collections, and recent errors in the logs.

  scripts/health.py                          containers + GET /v1/status (no token needed when auth is off; else --token-var)
  scripts/health.py --token-var HIPPO_TOKEN_ADMIN --logs 200

Prints PASS / WARN / FAIL per check and a verdict. Read-only: it never starts, stops or changes anything. The log check looks at the
last --logs lines (default 200) of the api and postgres containers for ERROR, CRITICAL, Traceback and "out of memory".
Exit status: 0 all pass, 1 a warning or failure, 2 could not run.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup import ENV_FILE, ROOT, find_engine
from hippo_client import add_connection_args, api, read_token

PATTERN = re.compile(r"\b(ERROR|CRITICAL|Traceback|out of memory|FATAL)\b")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(ap)
    ap.add_argument("--logs", type=int, default=200, help="how many log lines per container to scan")
    a = ap.parse_args()
    results: list[tuple[str, str, str]] = []

    def check(level: str, name: str, text: str) -> None:
        results.append((level, name, text))
        print(f"{level:<5} {name:<12} {text}")

    engine = find_engine()
    if engine is None:
        check("FAIL", "engine", "no running Docker or Podman")
    elif not ENV_FILE.is_file():
        check("FAIL", "stack", "hippocampus.env is missing: the stack was never started here (./hippocampus up)")
    else:
        ps = subprocess.run([engine, "compose", "--env-file", str(ENV_FILE), "ps", "--format", "{{.Service}} {{.State}} {{.Health}}"],
                            cwd=ROOT, capture_output=True, text=True)
        rows = [line.split() for line in ps.stdout.splitlines() if line.strip()]
        for r in rows:
            svc, state, health = r[0], r[1] if len(r) > 1 else "?", r[2] if len(r) > 2 else ""
            ok = (state == "running" and health in ("", "healthy")) or (svc == "migrate" and state == "exited")
            check("PASS" if ok else "FAIL", svc, f"{state} {health}".strip())
        if not rows:
            check("FAIL", "stack", "no containers: run ./hippocampus up")
        for svc in ("api", "postgres"):
            logs = subprocess.run([engine, "compose", "--env-file", str(ENV_FILE), "logs", "--no-color", "--tail", str(a.logs), svc],
                                  cwd=ROOT, capture_output=True, text=True)
            bad = [line for line in (logs.stdout + logs.stderr).splitlines() if PATTERN.search(line)]
            check("WARN" if bad else "PASS", f"{svc} log", f"{len(bad)} error line(s) in the last {a.logs}" + (f"; latest: {bad[-1][-160:]}" if bad else ""))

    token = None
    st, body = api(a.url, None, "GET", "/v1/status", timeout=10)
    if st == 401:
        token = read_token(a.token_var, "any valid account")
        st, body = api(a.url, token, "GET", "/v1/status", timeout=10)
    if st != 200:
        check("FAIL", "service", f"GET /v1/status answered HTTP {st}")
    else:
        slow = body["db_latency_ms"] > 200
        check("WARN" if slow else "PASS", "database", f"round trip {body['db_latency_ms']} ms")
        check("PASS", "service", f"model {body['embedder']['model']}, {body['active_jobs']} job(s) running, docs_sync={body['docs_sync']}")
        t = {k: v for k, v in body.items() if k not in ("db_latency_ms", "active_jobs", "embedder", "docs_sync", "crawl_require_ack")}
        if t:
            check("PASS", "content", ", ".join(f"{k} {v}" for k, v in t.items()))
        st, cols = api(a.url, token, "GET", "/v1/collections")
        empty = [c["slug"] for c in cols.get("collections", []) if not c.get("chunk_count")]
        if empty:
            check("WARN", "collections", f"{len(empty)} with no chunks: {', '.join(empty[:5])}")

    worst = "FAIL" if any(r[0] == "FAIL" for r in results) else "WARN" if any(r[0] == "WARN" for r in results) else "PASS"
    print(f"\nverdict: {worst}")
    return 0 if worst == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
