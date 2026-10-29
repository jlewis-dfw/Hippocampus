#!/usr/bin/env python3
"""Back up the Hippocampus database to a dump file, list and check dumps, and prove one restores (stdlib only).

  scripts/backup.py backup                  pg_dump of the running stack into backups/hippocampus-<YYYYMMDD-HHMMSS>.sql
  scripts/backup.py list                    the dumps in backups/, newest first, with size and whether each looks complete
  scripts/backup.py verify [FILE]           check that a dump is complete (default: the newest)
  scripts/backup.py restore-test [FILE]     restore a dump into a throwaway pgvector container, print the row count per table,
                                            and remove the container; the real database is never touched

``backup`` runs ``pg_dump`` inside the stack's postgres container through ``<engine> compose``, writes to a temporary file and
renames it only when the dump ends with the "database dump complete" trailer, so a failed run never leaves a half file that
looks like a backup. The file is created with mode 600. The folder backups/ is git-ignored; a dump holds every document, the
account hashes and restricted collections, so keep it out of git, chats and shared folders. The embedding model is not in it
(it is in the image).

There is deliberately no command that restores into the real database: that replaces everything, so it is a step you run
yourself (docs/User_Guide/operations.md#backups). ``restore-test`` proves the dump would work.

Docker or Podman is used (CONTAINER_ENGINE=docker|podman overrides detection; the first engine whose daemon answers ``info``
wins). Options: --out DIR (default backups/), --keep N (backup only: delete the oldest dumps beyond N; default: keep all).
Exit status: 0 ok, 1 the dump is incomplete or the restore failed, 2 could not run.
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "hippocampus.env"
DEFAULT_OUT = ROOT / "backups"
IMAGE = "docker.io/pgvector/pgvector:pg16"  # the image compose.yaml uses
DB_USER = DB_NAME = "hippocampus"  # fixed in compose.yaml
TRAILER = b"PostgreSQL database dump complete"
READY_TIMEOUT = 90

COUNTS_SQL = (
    "SELECT table_schema || '.' || table_name, "
    "(xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text "
    "FROM information_schema.tables WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema') "
    "ORDER BY 1"
)


def find_engine() -> str | None:
    wanted = os.environ.get("CONTAINER_ENGINE")
    for name in [wanted] if wanted else ["docker", "podman"]:
        if shutil.which(name) and subprocess.run([name, "info"], capture_output=True).returncode == 0:
            return name
    return None


def need_engine() -> str:
    engine = find_engine()
    if engine is None:
        print("no running Docker or Podman was found (is the daemon or the Podman machine started?)", file=sys.stderr)
        sys.exit(2)
    return engine


def complete(path: Path) -> bool:
    """A pg_dump ends with a comment line naming itself; a cut-off file lacks it."""
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - 512))
        return TRAILER in f.read()


def dumps(out: Path) -> list[Path]:
    return sorted(out.glob("hippocampus-*.sql"), key=lambda p: p.name, reverse=True) if out.is_dir() else []


def size(path: Path) -> str:
    n = path.stat().st_size
    return f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{n / 1024:.1f} KB"


def pick(arg: str | None, out: Path) -> Path:
    if arg:
        path = Path(arg)
        if not path.is_file():
            print(f"{path} is not a file", file=sys.stderr)
            sys.exit(2)
        return path
    found = dumps(out)
    if not found:
        print(f"no dumps in {out}: run scripts/backup.py backup first", file=sys.stderr)
        sys.exit(2)
    return found[0]


def backup(out: Path, keep: int | None) -> int:
    if not ENV_FILE.is_file():
        print("hippocampus.env is missing: the stack has never been started here (run ./hippocampus up)", file=sys.stderr)
        return 2
    engine = need_engine()
    out.mkdir(parents=True, exist_ok=True)
    final = out / f"hippocampus-{datetime.now():%Y%m%d-%H%M%S}.sql"
    tmp = final.with_suffix(".sql.part")
    cmd = [engine, "compose", "--env-file", str(ENV_FILE), "exec", "-T", "postgres", "pg_dump", "-U", DB_USER, DB_NAME]
    old_umask = os.umask(0o077)
    try:
        with tmp.open("wb") as f:
            run = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.PIPE)
    finally:
        os.umask(old_umask)
    if run.returncode != 0:
        tmp.unlink(missing_ok=True)
        print(f"pg_dump failed (is the stack running? try ./hippocampus status): {run.stderr.decode(errors='replace').strip()}",
              file=sys.stderr)
        return 2
    if not complete(tmp):
        tmp.unlink(missing_ok=True)
        print("the dump is incomplete (no end-of-dump marker); nothing was kept", file=sys.stderr)
        return 1
    tmp.replace(final)
    print(f"backup written: {final} ({size(final)})")
    print("It holds every document, the account hashes and restricted collections: keep it private and out of git.")
    if keep is not None:
        for old in dumps(out)[keep:]:
            old.unlink()
            print(f"removed old dump {old.name} (--keep {keep})")
    return 0


def listing(out: Path) -> int:
    found = dumps(out)
    if not found:
        print(f"no dumps in {out}")
        return 0
    for p in found:
        print(f"{p.name}  {size(p):>9}  {'complete' if complete(p) else 'INCOMPLETE'}")
    return 0


def verify(path: Path) -> int:
    ok = complete(path)
    print(f"{path.name}: {size(path)}, {'complete (end-of-dump marker present)' if ok else 'INCOMPLETE (no end-of-dump marker)'}")
    return 0 if ok else 1


def restore_test(path: Path) -> int:
    if not complete(path):
        print(f"{path.name} is incomplete: not restoring it", file=sys.stderr)
        return 1
    engine = need_engine()
    name, password = f"hippo-restore-test-{secrets.token_hex(4)}", secrets.token_hex(12)
    run = subprocess.run([engine, "run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={password}",
                          "-e", f"POSTGRES_USER={DB_USER}", "-e", "POSTGRES_DB=hippocampus_test", IMAGE],
                         capture_output=True, text=True)
    if run.returncode != 0:
        print(f"could not start the throwaway database: {run.stderr.strip()}", file=sys.stderr)
        return 2

    def psql(*args: str, stdin=None) -> subprocess.CompletedProcess:
        return subprocess.run([engine, "exec", "-i", name, "psql", "-U", DB_USER, "-d", "hippocampus_test", "-v", "ON_ERROR_STOP=1",
                               *args], stdin=stdin, capture_output=True, text=True)

    try:
        deadline, streak = time.time() + READY_TIMEOUT, 0
        while streak < 2:  # the image restarts the server once after initialising
            if time.time() > deadline:
                print("the throwaway database did not become ready in time", file=sys.stderr)
                return 2
            streak = streak + 1 if psql("-c", "SELECT 1").returncode == 0 else 0
            time.sleep(1)
        with path.open("rb") as f:
            res = subprocess.run([engine, "exec", "-i", name, "psql", "-U", DB_USER, "-d", "hippocampus_test", "-q"], stdin=f,
                                 capture_output=True, text=True)
        errors = [line for line in res.stderr.splitlines() if "ERROR" in line]
        if errors:
            print(f"restore of {path.name} reported {len(errors)} error(s); the first:", file=sys.stderr)
            print("  " + errors[0], file=sys.stderr)
            return 1
        counts = psql("-At", "-F", "\t", "-c", COUNTS_SQL)
        if counts.returncode != 0:
            print(f"restored, but the check query failed: {counts.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"{path.name} restored into a throwaway database with no errors. Rows per table:")
        for line in counts.stdout.splitlines():
            table, _, n = line.partition("\t")
            print(f"  {table:<40}{n:>8}")
        return 0
    finally:
        subprocess.run([engine, "rm", "-f", name], capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["backup", "list", "verify", "restore-test"])
    ap.add_argument("file", nargs="?", help="a dump (verify, restore-test); default: the newest in --out")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--keep", type=int, default=None, metavar="N", help="backup: keep only the newest N dumps")
    a = ap.parse_args()
    if a.keep is not None and a.keep < 1:
        print("--keep must be at least 1", file=sys.stderr)
        return 2
    if a.action == "backup":
        return backup(a.out, a.keep)
    if a.action == "list":
        return listing(a.out)
    path = pick(a.file, a.out)
    return verify(path) if a.action == "verify" else restore_test(path)


if __name__ == "__main__":
    sys.exit(main())
