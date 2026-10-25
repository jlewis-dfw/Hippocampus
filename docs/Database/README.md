# Database

Hippocampus keeps everything in one PostgreSQL 16 database with the **pgvector** extension: collections, sources, chunks (text and
vectors) and the access-control accounts. This page is the overview; the tables are in [schema.md](schema.md) and the migration process in
[migrations.md](migrations.md).

This is a living document. It describes the schema as of migration `0003_sync_sources`; update it alongside the migrations.

## Contents

- [schema.md](schema.md) — every table and column, constraints, indexes, and what the application writes where.
- [migrations.md](migrations.md) — how the schema is versioned and applied, the rules for adding a migration, and how it is checked.

## At a glance

| Item | Value |
|---|---|
| Server | PostgreSQL 16 with pgvector (the image `pgvector/pgvector:pg16`) |
| Database name | `hippocampus` (the local stack); `hippocampus_dev` for the development database from `scripts/dev-db.sh` |
| Schema | `hippocampus` (all application tables); `public.schema_migrations` records applied migrations |
| Extension | `vector` (pgvector), created by migration 0001 |
| Embedding column | `vector(384)`, the width of the fixed embedding model |
| Vector index | HNSW, cosine distance |
| Connections | A pool of autocommit connections (`psycopg_pool`), one taken per `with store_factory()` block and returned at its end. Size and wait: `HIPPO_DB_POOL_MIN`, `_MAX`, `_TIMEOUT` (default 1, 5, 5 s). When none is free after the wait the API answers `503` with `Retry-After: 1`. The admin tool opens its own single connection |
| Access from the application | Only `store.py` runs SQL |

## Entity relationships

```
collections 1 ──< sources 1 ──< document_chunks          api_clients  (stands alone)
 (slug, visibility)  (uri, sha256_hash)   (embedding vector(384))     (token_sha256, grants)
```

- Deleting a collection **cascades** to its sources, which cascade to their chunks (`ON DELETE CASCADE` on both foreign keys).
- A source is unique per collection by URI, and a chunk is unique per source by index.
- `api_clients` has no foreign keys: grants are plain text patterns matched against collection slugs by the application.

## Who connects

| Connection | By | Needs |
|---|---|---|
| The API and jobs | `PgStore` through `connect_store(dsn)` | read and write on the `hippocampus` schema |
| The migration runner | `python -m hippocampus.migrate` | create schema, tables and the `vector` extension (a superuser or a role that may create extensions) |
| The admin tool | `python -m hippocampus.admin` | read and write on `hippocampus.api_clients` and `hippocampus.collections` |
| You | `psql` | anything |

The local stack uses the database's superuser for all of them, which is acceptable for a single-machine setup. For a shared database,
create a role for the API with only `SELECT, INSERT, UPDATE, DELETE` on the schema's tables and run migrations as an owner role.

## A SQL cookbook

Open a prompt with `docker compose --env-file hippocampus.env exec postgres psql -U hippocampus hippocampus` (use `podman compose` if that
is your engine).

```sql
-- what is in each collection
SELECT k.slug, k.visibility, count(DISTINCT s.id) AS sources, count(c.id) AS chunks
FROM hippocampus.collections k
LEFT JOIN hippocampus.sources s ON s.collection_id = k.id
LEFT JOIN hippocampus.document_chunks c ON c.source_id = s.id
GROUP BY k.slug, k.visibility ORDER BY k.slug;

-- the most recently indexed sources
SELECT k.slug, s.uri, s.chunk_count, s.last_indexed_at
FROM hippocampus.sources s JOIN hippocampus.collections k ON k.id = s.collection_id
ORDER BY s.last_indexed_at DESC LIMIT 20;

-- remove ONE source (its chunks go with it): the API has no per-source delete
DELETE FROM hippocampus.sources s USING hippocampus.collections k
WHERE s.collection_id = k.id AND k.slug = 'docs' AND s.uri = 'https://docs.example.com/old-page';

-- accounts and when each was last used (never tokens: only hashes exist)
SELECT name, is_admin, read_grants, write_grants, last_used_at, revoked_at FROM hippocampus.api_clients ORDER BY name;

-- the database size
SELECT pg_size_pretty(pg_database_size(current_database()));
```

After removing sources by hand, nothing else needs updating: counts are computed, not stored. Changing `visibility` directly in SQL is
equivalent to the admin tool or the API (`UPDATE hippocampus.collections SET visibility = 'restricted' WHERE slug = '...'`), except that the
reserved-name rule is enforced by the application, not the database.

## Backup and restore

See [../User_Guide/operations.md](../User_Guide/operations.md#backups). Everything, including the accounts' token hashes, is in the
dump; the embedding model is not (it is in the image).

## Known limits

- **Column lengths are strict.** `sources.uri` is `VARCHAR(1024)`, `sources.title` is `VARCHAR(512)` and `document_chunks.header_path` is
  `VARCHAR(512)`. A longer value (a crawled page with a very long title, a very long URL or a deeply nested heading path) makes that
  source's insert fail, and the job reports the database error. The API limits `uri` to 1024 and `title` to 512 on the file route, but the
  crawler and the repository ingester do not trim.
- **`sources.status` is always `indexed`** and `error_message` is always empty today; they are reserved for per-source failure tracking.
- **`document_chunks.token_count` is an estimate** (words and punctuation), not the model's tokens.
- **One embedding model per database.** The `vector(384)` width and the data assume `bge-small-en-v1.5`.
- **No row-level security.** Visibility is enforced by the application ([../Api/authorization.md](../Api/authorization.md)).
