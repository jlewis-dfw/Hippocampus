#!/usr/bin/env python3
"""Database migration helper for the checks (needs the `db` extra).

  migrate_check.py apply    apply pending db/migrations/*.sql to the database in HIPPO_TEST_DSN, tracked in
                            public.schema_migrations (the same table scripts/dev-db.sh uses)
  migrate_check.py scratch  create a throwaway database on the same server, apply every migration from zero, check the
                            schema, and drop it: proves the migrations work on a fresh server, not only on a database
                            that has been migrated step by step

Exit status is non-zero on any failure.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg import conninfo, sql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from hippocampus.migrate import apply_all  # noqa: E402  (the same code the compose migrate service runs)

# What the application code relies on; extend it when a migration adds something the code reads.
EXPECTED = {
    "collections": {"id", "slug", "name", "description", "created_at", "visibility"},
    "sources": {"id", "collection_id", "uri", "sha256_hash", "chunk_count", "status", "last_indexed_at"},
    "document_chunks": {"id", "source_id", "chunk_index", "header_path", "content", "embedding"},
    "api_clients": {"id", "name", "token_sha256", "read_grants", "write_grants", "is_admin", "revoked_at"},
}


def check_schema(conn: psycopg.Connection) -> None:
    for table, want in EXPECTED.items():
        have = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='hippocampus' AND table_name=%s", (table,))}
        if not have:
            raise SystemExit(f"table hippocampus.{table} is missing after the migrations")
        if want - have:
            raise SystemExit(f"hippocampus.{table} lacks columns {sorted(want - have)}")
    if not conn.execute("SELECT 1 FROM pg_indexes WHERE schemaname='hippocampus' AND indexname='idx_document_chunks_embedding_hnsw'").fetchone():
        raise SystemExit("the HNSW vector index is missing")
    vis = conn.execute("SELECT column_default FROM information_schema.columns WHERE table_schema='hippocampus' AND table_name='collections' AND column_name='visibility'").fetchone()[0]
    if "shared" not in vis:
        raise SystemExit("collections.visibility must default to 'shared' so existing collections stay as they were")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("apply", "scratch"):
        print(__doc__)
        return 2
    dsn = os.environ.get("HIPPO_TEST_DSN")
    if not dsn:
        print("HIPPO_TEST_DSN is not set", file=sys.stderr)
        return 2
    if sys.argv[1] == "apply":
        with psycopg.connect(dsn, autocommit=True) as conn:
            applied = apply_all(conn)
        print("applied: " + (", ".join(applied) if applied else "nothing (already up to date)"))
        return 0
    # scratch: a new database on the same server, from zero
    name = f"hippo_check_{uuid.uuid4().hex[:10]}"
    admin = conninfo.make_conninfo(dsn, dbname="postgres")
    scratch = conninfo.make_conninfo(dsn, dbname=name)
    with psycopg.connect(admin, autocommit=True) as a:
        a.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        with psycopg.connect(scratch, autocommit=True) as conn:
            applied = apply_all(conn)
            check_schema(conn)
        print(f"fresh database: {len(applied)} migration(s) applied, schema as expected")
    finally:
        with psycopg.connect(admin, autocommit=True) as a:
            a.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
