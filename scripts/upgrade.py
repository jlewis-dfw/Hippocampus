#!/usr/bin/env python3
"""Upgrade the running stack to the code in this checkout: a backup first, then rebuild and restart, then check it came back.

  scripts/upgrade.py                  plan: git state, migrations not yet applied to the database, the latest backup; changes nothing
  scripts/upgrade.py --apply          back up, rebuild the image, restart (data stays), wait for the service, confirm all migrations applied

It does not pull code (``git pull`` is yours to run) and never resets or removes data. ``--apply`` stops with exit status 2 if the
backup fails, so an upgrade never starts without one. The rebuild is ``<engine> compose up -d --build``: the ``migrate`` service
applies pending db/migrations before the API starts. Needs the stack to have been started once (hippocampus.env exists).
Options: --url, --apply, --keep N (passed to the backup), --wait SECONDS (default 300).
Exit status: 0 ok, 1 the service did not come back healthy or a migration is missing, 2 could not run.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup import DEFAULT_OUT, ENV_FILE, ROOT, backup, dumps, find_engine, size
from hippo_client import api


def applied(engine: str) -> list[str] | None:
    run = subprocess.run([engine, "compose", "--env-file", str(ENV_FILE), "exec", "-T", "postgres", "psql", "-U", "hippocampus",
                          "-d", "hippocampus", "-At", "-c", "SELECT name FROM public.schema_migrations ORDER BY name"],
                         cwd=ROOT, capture_output=True, text=True)
    return run.stdout.split() if run.returncode == 0 else None


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8090")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--keep", type=int, default=None)
    ap.add_argument("--wait", type=int, default=300)
    a = ap.parse_args()
    if not ENV_FILE.is_file():
        print("hippocampus.env is missing: start the stack once with ./hippocampus up", file=sys.stderr)
        return 2
    engine = find_engine()
    if engine is None:
        print("no running Docker or Podman", file=sys.stderr)
        return 2

    on_disk = sorted(p.name for p in (ROOT / "db" / "migrations").glob("*.sql"))
    done = applied(engine)
    pending = [m for m in on_disk if done is None or m not in done]
    print(f"branch {git('rev-parse', '--abbrev-ref', 'HEAD')} at {git('rev-parse', '--short', 'HEAD')}; "
          f"{len(git('status', '--porcelain').splitlines())} uncommitted change(s)")
    print("pending migrations: " + (", ".join(pending) if pending else "none") + ("" if done is not None else "  (database not reachable: all listed)"))
    newest = dumps(DEFAULT_OUT)[:1]
    print(f"latest backup: {newest[0].name} ({size(newest[0])})" if newest else "latest backup: none")
    if not a.apply:
        print("(plan only: nothing was changed; --apply takes a backup, rebuilds and restarts)")
        return 0

    if done is not None and backup(DEFAULT_OUT, a.keep) != 0:
        print("the backup failed: not upgrading", file=sys.stderr)
        return 2
    print("rebuilding and restarting (data stays)...")
    up = subprocess.run([engine, "compose", "--env-file", str(ENV_FILE), "up", "-d", "--build"], cwd=ROOT)
    if up.returncode != 0:
        print("compose up failed", file=sys.stderr)
        return 1
    deadline = time.time() + a.wait
    while time.time() < deadline:
        st, _ = api(a.url, None, "GET", "/v1/status", timeout=5) if _reachable(a.url) else (0, {})
        if st in (200, 401):
            break
        time.sleep(3)
    else:
        print(f"the service did not answer within {a.wait} s: see ./hippocampus logs api", file=sys.stderr)
        return 1
    done = applied(engine) or []
    missing = [m for m in on_disk if m not in done]
    print("all migrations applied" if not missing else f"NOT applied: {', '.join(missing)}")
    return 1 if missing else 0


def _reachable(url: str) -> bool:
    try:
        urllib.request.urlopen(url.rstrip("/") + "/v1/status", timeout=3)
        return True
    except HTTPError:
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
