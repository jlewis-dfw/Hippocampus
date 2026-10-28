"""Applies the SQL migrations in db/migrations to the database in HIPPO_DSN (needs the ``db`` extra).

    python -m hippocampus.storage.migrate

Pending files are applied in order, each in a transaction, and recorded in ``public.schema_migrations`` (the table
scripts/dev-db.sh and the homelab migrate step also use), so running it again does nothing. The folder comes from
HIPPO_MIGRATIONS_DIR (the container image sets /app/db/migrations) or, in a source checkout, ``<repo>/db/migrations``.
Used by the ``migrate`` service in compose.yaml before the API starts.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

log = logging.getLogger("hippocampus")
NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def migrations_dir() -> Path:
    env = os.environ.get("HIPPO_MIGRATIONS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "db" / "migrations"


def migration_files(folder: Path | None = None) -> list[Path]:
    folder = folder or migrations_dir()
    files = sorted(folder.glob("*.sql"))
    if not files:
        raise SystemExit(f"no migrations in {folder}")
    numbers = []
    for f in files:
        m = NAME_RE.match(f.name)
        if not m:
            raise SystemExit(f"migration name {f.name!r} must look like 0001_some_name.sql")
        numbers.append(int(m.group(1)))
    if numbers != list(range(1, len(numbers) + 1)):
        raise SystemExit(f"migration numbers must run 0001..N without gaps or repeats, found {numbers}")
    return files


def apply_all(conn, folder: Path | None = None) -> list[str]:
    """Apply every pending migration on an open (autocommit) psycopg connection; returns the names applied."""
    conn.execute("CREATE TABLE IF NOT EXISTS public.schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    done = {r[0] for r in conn.execute("SELECT name FROM public.schema_migrations")}
    applied = []
    for f in migration_files(folder):
        if f.name in done:
            continue
        with conn.transaction():
            conn.execute(f.read_text())
            conn.execute("INSERT INTO public.schema_migrations(name) VALUES (%s)", (f.name,))
        applied.append(f.name)
        log.info("migration applied: %s", f.name)
    return applied


def main() -> int:
    import time

    import psycopg

    from ..service.observability import setup_logging

    setup_logging(os.environ)
    dsn = os.environ.get("HIPPO_DSN")
    if not dsn:
        print("HIPPO_DSN is not set", file=sys.stderr)
        return 2
    last = None
    for _ in range(30):  # the database may still be starting; compose waits for health, plain docker run does not
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                applied = apply_all(conn)
            print("migrations applied: " + (", ".join(applied) if applied else "none pending"))
            return 0
        except psycopg.OperationalError as exc:
            last = exc
            time.sleep(2)
    print(f"cannot reach the database: {last}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
