# Development guide

How to set up a working copy, how the code is laid out, and the recipes for the common changes. Tests and linters are in
[../Testing/README.md](../Testing/README.md); the running stack is in [../Local/README.md](../Local/README.md).

This is a living document. It describes the code as of migration `0002_access_control`; update it alongside the source.

## Set up

```bash
scripts/prereqs.sh --dev --setup     # checks tools, offers to install what is missing (y/N each), creates .venv with the dev extras
# Windows: scripts\prereqs.ps1 -Dev -Setup
```

Read the script's warning first; it asks you to confirm you have. Without the script:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[db,embed,api,mcp,browser,dev]"
scripts/check.sh
```

Needs Python 3.12 or newer. Node 18 or newer is used only for the dashboard syntax check and the Markdown linter. A container engine (Docker or Podman) is needed
for the local stack and, with Podman, for the development database.

## Layout

| Path | Contents |
|---|---|
| `src/hippocampus/` | The package (below) |
| `src/hippocampus/ui/` | The dashboard: `index.html`, `app.js`, `app.css` ([../Dashboard/README.md](../Dashboard/README.md)) |
| `db/migrations/` | Numbered SQL files, applied in order ([../Database/migrations.md](../Database/migrations.md)) |
| `tests/` | The test suite, in `unit/`, `integration/` and `e2e/` ([../Testing/README.md](../Testing/README.md)) |
| `scripts/` | Checks, linters, the prerequisite scripts, the docs indexer, the smoke test, deploy and export helpers |
| `deploy/` | `api.Dockerfile`, `ci.Dockerfile`, the development database compose file |
| `compose.yaml`, `hippocampus`, `hippocampus.env.example` | The local stack ([../Local/README.md](../Local/README.md)) |
| `export/` | Files that exist only in the public copy of the project (README, license, contribution guide, CI workflow) |
| `docs/` | This documentation |

### The package

| Module | Role |
|---|---|
| `api.py` | The FastAPI application: routes, the network gate, dashboard headers, wiring |
| `auth.py` | Tokens, accounts, grants, the access decisions |
| `store.py` | All SQL: collections, sources, chunks, search, accounts |
| `chunker.py` | Markdown into chunks ([../Architecture/embedding-and-chunking.md](../Architecture/embedding-and-chunking.md)) |
| `embedder.py` | The embedding model |
| `diff.py` | Which chunks are new, kept or gone |
| `crawler.py` | The web crawler and its safety rules ([../Architecture/crawler.md](../Architecture/crawler.md)) |
| `repo_ingest.py` | Repomix pack parsing and the secret pre-flight |
| `docs_sync.py` | The Forgejo webhook sync |
| `jobs.py` | In-memory background jobs and their logs ([../Architecture/jobs.md](../Architecture/jobs.md)) |
| `mcp_server.py` | The MCP server ([../Mcp/README.md](../Mcp/README.md)) |
| `admin.py` | The admin command line ([../Admin/README.md](../Admin/README.md)) |
| `migrate.py`, `healthcheck.py` | The migration runner and the container health check |

Rule of thumb: SQL lives in `store.py`; access decisions in `auth.py`; routes stay thin.

## Conventions

- Python 3.12, type hints, `from __future__ import annotations`. Keep modules small and dependency-light; the standard library is preferred.
- FastAPI dependencies are written `p: Principal = Depends(current_principal)`. Do **not** use an `Annotated` alias together with `from __future__ import annotations`:
  every route then answers `422`.
- A comment explains a reason, not what the line does. Keep `# noqa` comments short (ruff's RUF100 removes the ones that are not needed, so put reasons in a separate comment).
- Never answer "forbidden" about something the caller may not know exists: a collection the caller cannot read is `404`.
- Everything from outside is untrusted: crawled text, API input, search results. Write to the dashboard with `textContent`.
- A migration is additive and never edited after it has been applied anywhere.
- Formatting is checked by `scripts/lint.sh`; run `scripts/lint.sh --fix` before committing.

## Recipes

### Add an endpoint

1. Add the route in `api.py`. Take `p: Principal = Depends(current_principal)`, then decide read or write access with the helpers in `auth.py`; do not write the rule inline.
2. Put new SQL in `store.py` and add the same method to `MemStore` in `tests/unit/test_api.py`, so the unit tests can exercise it.
3. Test the allowed case, the denied case and the hidden case (`404`) in `tests/unit/test_access.py` or `tests/unit/test_api.py`.
4. Add the route to [../Api/endpoints.md](../Api/endpoints.md), and to [../Api/authorization.md](../Api/authorization.md) if it changes who may do what.

### Add a migration

1. Create `db/migrations/NNNN_short_name.sql` with the next number. Make it additive (new columns with defaults, new tables, new indexes).
2. Add anything the code newly depends on to the expected-schema list in `scripts/migrate_check.py`.
3. Run `scripts/check.sh`: it applies every migration from zero in a throwaway database.
4. Update [../Database/schema.md](../Database/schema.md) and the "described as of migration" line on the pages that carry it.

### Add a setting

Read it in the module that uses it, with a default. Add it to `compose.yaml` and `hippocampus.env.example` if the local stack should expose it, and to
[../User_Guide/configuration.md](../User_Guide/configuration.md).

### Add a way to ingest

Follow `repo_ingest.py` or `docs_sync.py`: a function that returns documents, a call to the shared sync (hash skip, embed first, then atomic replace), a job wrapper
so the dashboard can show progress, and the guarded-fragment and reserved-prefix checks. Document it in [../Architecture/ingestion-pipeline.md](../Architecture/ingestion-pipeline.md)
and [../User_Guide/ingesting.md](../User_Guide/ingesting.md).

### Change the dashboard

See [../Dashboard/README.md](../Dashboard/README.md#changing-it). Keep the content security policy as it is.

## Documentation rules

- User pages say what to do; technical pages say how it works. Keep the two apart.
- Check a statement against the code before writing it, and re-check when the code changes. Say "not yet" rather than implying a feature exists.
- Keep this folder free of addresses, host names, credentials and anything specific to one deployment. Use `example` domains and placeholders.
- Markdown must pass `scripts/lint.sh --only md`. Tables have the same number of cells in every row; a pipe inside a cell is written `\|`.
- After changing the docs, index them again to try the search ([../User_Guide/ingesting.md](../User_Guide/ingesting.md) and `scripts/index_docs.py`).

## The public copy

This project is also published as a clean, generated copy. `scripts/export-public.sh` builds it from an allowlist of files, rewrites a few names, runs the tests and
linters inside the copy, and refuses to finish if a leak scan finds anything that should not be public. Files not on the allowlist, such as private notes,
never leave the private repository. When you add a file that should be public, add it to the allowlist in that script. Changes accepted on the public side come back
with `scripts/import-public.sh`; `scripts/publish-public.sh` sends a new export out. Run them only when you mean to publish.

## Commits and reviews

Work on a branch, run `scripts/check.sh`, open a pull request, and merge when the pipeline is green. Do not commit secrets, `hippocampus.env`, or database dumps.
