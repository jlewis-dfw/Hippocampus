# Migrations

How the schema is versioned, applied and checked. Source: `src/hippocampus/storage/migrate.py`, `db/migrations/`, `scripts/migrate_check.py`.

## The files

Migrations are plain SQL files in `db/migrations/`, named `NNNN_description.sql`: four digits, an underscore, then lowercase letters, digits and
underscores. The numbers must run `0001, 0002, 0003, ...` **without gaps or repeats**; the runner and the checks refuse anything else.

| File | What it does |
|---|---|
| `0001_init_hippocampus_schema.sql` | Creates the `vector` extension, the `hippocampus` schema, the `collections`, `sources` and `document_chunks` tables, the HNSW index and the helper indexes |
| `0002_access_control.sql` | Adds `collections.visibility` (default `shared`, with a check constraint) and creates `api_clients` |
| `0003_sync_sources.sql` | Creates `sync_sources`, the saved folders that are kept in step with a collection |

## The runner

`python -m hippocampus.storage.migrate` (reads `HIPPO_DSN`; the folder is `HIPPO_MIGRATIONS_DIR`, or `<repo>/db/migrations` in a checkout):

1. Waits for the database, retrying the connection up to 30 times, 2 seconds apart.
2. Creates `public.schema_migrations` if it does not exist.
3. Reads the names already applied, then applies each pending file **in order, each in its own transaction**: the file's SQL runs and
   `INSERT INTO public.schema_migrations(name) VALUES (...)` follows in the same transaction. If any statement fails, that migration rolls
   back completely and the runner stops with an error; earlier migrations stay applied.
4. Prints `migrations applied: <names>` or `migrations applied: none pending`.

It is **idempotent**: running it again does nothing. The same code runs in these places:

| Where | When |
|---|---|
| The `migrate` service of `compose.yaml` | At every `./hippocampus up`, before the API starts |
| `scripts/dev-db.sh migrate` | Applies the same files to the development database (with `psql`, tracked in the same table) |
| `scripts/migrate_check.py apply` | In the CI checks, to prepare the test database |
| Any other tool that applies the files in order and records them in `public.schema_migrations` | For example a deployment script using `psql` |

Because the bookkeeping table is the same, the tools can be mixed on one database.

## Rules for writing a migration

1. **Append only.** Never edit a migration that has been applied anywhere. Add a new file with the next number.
2. **Additive.** Prefer adding tables, columns with defaults, and indexes. Do not drop or rename columns that the previous release of the application
   reads: an older image must keep working against the newer schema so that a rollback of the code is safe. Where a destructive change is truly needed,
   do it in two releases (stop using it, then drop it).
3. **New `NOT NULL` columns need a default**, so existing rows stay valid.
4. **One purpose per file**, with a comment at the top saying why.
5. **The vector width is fixed** (`vector(384)`). A different embedding model is a new column and a full re-embed, not a tweak.
6. **No data you cannot regenerate** inside a migration without a back-out plan; take a backup first for risky ones.
7. **Keep it plain SQL.** The runner executes the whole file in one transaction; do not use `CREATE INDEX CONCURRENTLY` or anything else that
   cannot run inside a transaction.

## Adding one: the checklist

1. Create `db/migrations/000N_what_it_does.sql` (next number).
2. If the application now reads the new tables or columns, add them to `EXPECTED` in `scripts/migrate_check.py` so the check fails if a
   migration forgets them.
3. Update the store (`store.py`) and the code that uses it, and the tests ([../Testing/README.md](../Testing/README.md)).
4. Update [schema.md](schema.md) and this page's table of files.
5. Run `scripts/check.sh`. It applies **all migrations to a new, empty database from zero** and checks the schema, which proves the whole chain
   works on a fresh server and not only on a database migrated step by step.

## The checks

`scripts/migrate_check.py` has two modes (it needs `HIPPO_TEST_DSN`):

| Mode | What it does |
|---|---|
| `apply` | Applies pending migrations to the database in the DSN (used to prepare the CI test database) |
| `scratch` | Creates a throwaway database on the same server, applies every migration from zero, verifies that the expected tables and columns exist, that the HNSW index exists, and that `collections.visibility` defaults to `shared`, then drops the database |

`scratch` also validates the file names and numbering (the same rules as the runner). It is part of every run of `scripts/check.sh` that has a
database, locally and in CI.

## Rolling back

There are no down-migrations. To undo a schema change, restore a backup taken before the upgrade, or write a new forward migration. Because
migrations are additive, rolling back the **application** (an older image) against the newer schema is normally safe; see
[../User_Guide/operations.md](../User_Guide/operations.md#upgrading).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `migration numbers must run 0001..N without gaps or repeats` | A file is missing or two share a number. Renumber the newest one |
| `migration name '...' must look like 0001_some_name.sql` | Use four digits, an underscore, then lowercase letters, digits and underscores |
| `permission denied to create extension "vector"` | The role must be allowed to create extensions (a superuser, or pre-create the extension as one) |
| `cannot reach the database` | The database is not up or the DSN is wrong; the runner retried for about a minute |
| A migration fails halfway | It was rolled back; fix the SQL (in a new file if the old one ever ran elsewhere) and run again |
