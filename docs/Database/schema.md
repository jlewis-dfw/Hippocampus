# Schema reference

Every table, column, constraint and index, as created by `db/migrations/0001_init_hippocampus_schema.sql` and
`db/migrations/0002_access_control.sql`. All application tables live in the schema `hippocampus`; ids are random UUIDs
(`gen_random_uuid()`); timestamps are `TIMESTAMPTZ`.

## `hippocampus.collections`

A named set of sources.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | `gen_random_uuid()` | Primary key |
| `slug` | VARCHAR(64) | no | | **Unique.** Lowercase letters, digits and `-`, starting with a letter or digit (enforced by the API, not the database) |
| `name` | VARCHAR(255) | no | | Display name |
| `description` | TEXT | yes | | Optional |
| `created_at` | TIMESTAMPTZ | no | `NOW()` | |
| `visibility` | VARCHAR(16) | no | `'shared'` | Added in 0002. `CHECK (visibility IN ('shared', 'restricted'))`, constraint `ck_collections_visibility` |

Written by: `POST /v1/collections`, `PATCH /v1/collections/{slug}`, the admin tool (`set-visibility`). Deleted by `DELETE /v1/collections/{slug}`,
which cascades.

## `hippocampus.sources`

One document in a collection.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | `gen_random_uuid()` | Primary key |
| `collection_id` | UUID | no | | Foreign key to `collections(id)`, `ON DELETE CASCADE` |
| `uri` | VARCHAR(1024) | no | | The source's identity within the collection |
| `title` | VARCHAR(512) | yes | | Shown in search results |
| `sha256_hash` | CHAR(64) | no | | Hex SHA-256 of the document text: what makes re-indexing skip unchanged sources |
| `chunk_count` | INT | no | 0 | Number of chunks stored |
| `status` | VARCHAR(32) | no | `'indexed'` | Always `indexed` today |
| `error_message` | TEXT | yes | | Reserved; cleared on every successful index |
| `last_indexed_at` | TIMESTAMPTZ | no | `NOW()` | Set on insert and on every change |

Constraints and indexes: `uq_collection_uri UNIQUE (collection_id, uri)`; `idx_sources_collection` on `collection_id`.

Written by: `PgStore.replace_source` (upsert on `(collection_id, uri)`, in the same transaction as its chunks); deleted by `PgStore.delete_source`
(pruning by the repository ingester and the docs sync).

## `hippocampus.document_chunks`

A passage of a source with its embedding.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | `gen_random_uuid()` | Primary key |
| `source_id` | UUID | no | | Foreign key to `sources(id)`, `ON DELETE CASCADE` |
| `chunk_index` | INT | no | | Position within the source, from 0 |
| `header_path` | VARCHAR(512) | yes | | Heading breadcrumb, for example `Guide > Install > Linux`; empty text before the first heading |
| `content` | TEXT | no | | The passage |
| `token_count` | INT | no | | Estimated tokens (words and punctuation) |
| `embedding` | VECTOR(384) | no | | The chunk's vector (the embedding model's output) |
| `created_at` | TIMESTAMPTZ | no | `NOW()` | |

Constraints and indexes:

- `uq_source_chunk UNIQUE (source_id, chunk_index)`.
- `idx_chunks_source` on `source_id`.
- `idx_document_chunks_embedding_hnsw`: `USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)`.

Chunks are never updated: when a source changes, all of its chunks are deleted and re-inserted in the same transaction as the source row update.

## `hippocampus.api_clients`

Access-control accounts (added in 0002). See [../Api/authorization.md](../Api/authorization.md).

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | UUID | no | `gen_random_uuid()` | Primary key |
| `name` | VARCHAR(64) | no | | **Unique**, including revoked accounts, which keep their name |
| `token_sha256` | CHAR(64) | no | | **Unique.** Hex SHA-256 of the token; the token itself is never stored |
| `read_grants` | TEXT[] | no | `'{}'` | Collection slugs or `*` patterns allowing read |
| `write_grants` | TEXT[] | no | `'{}'` | Grants allowing write (which implies read) |
| `is_admin` | BOOLEAN | no | `FALSE` | |
| `note` | TEXT | yes | | Free text |
| `created_at` | TIMESTAMPTZ | no | `NOW()` | |
| `last_used_at` | TIMESTAMPTZ | yes | | Refreshed at most once a minute |
| `revoked_at` | TIMESTAMPTZ | yes | | Set when revoked; an account is active while this is NULL |

Written by: the client routes and the admin tool. Authentication reads it by `token_sha256` where `revoked_at IS NULL`.

## `public.schema_migrations`

| Column | Type | Notes |
|---|---|---|
| `name` | TEXT | Primary key: the migration file name, for example `0002_access_control.sql` |
| `applied_at` | TIMESTAMPTZ | Default `now()` |

Created by the runner (`CREATE TABLE IF NOT EXISTS`). It is in `public`, not `hippocampus`, so it exists before the schema does.

## What the application writes, where

| Operation | Tables touched |
|---|---|
| Index a source | `sources` (upsert), `document_chunks` (delete then insert), in one transaction |
| Search | reads `document_chunks`, `sources`, `collections` |
| Delete a collection | `collections` (cascades to `sources` and `document_chunks`) |
| Prune a source | `sources` (cascades to its chunks) |
| Create, edit, rotate, revoke an account | `api_clients` |
| Authenticate a token | reads `api_clients`; refreshes `last_used_at` (guarded) |
| Migrate | the schema, `public.schema_migrations` |

## Sizing

A chunk stores its text plus a 384-dimension single-precision vector (384 x 4 bytes = 1,536 bytes) and its share of the HNSW index.
A 400-token chunk of text is roughly 2 to 3 KB, so budget on the order of **5 KB per chunk** including the index, and about
**5 to 15 chunks per document page**. A few thousand pages therefore take tens of megabytes. (Estimates, not measurements: check
`pg_database_size` on your data.)

## Types worth knowing

- `vector(384)`: pgvector's fixed-size float vector. Inserted as the text form `[0.1,0.2,...]` cast to `vector`.
- `<=>`: the cosine distance operator; the HNSW index above serves queries that order by it.
- `TEXT[]`: PostgreSQL arrays; the application passes Python lists.
- `CHAR(64)`: a fixed-width hex digest (both hash columns).
