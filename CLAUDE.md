# Hippocampus

A small self-hosted memory service: it stores documents (crawled web pages, pushed Markdown, repositories) as searchable chunks in PostgreSQL with
pgvector and answers semantic searches. It has a REST API (FastAPI), an MCP server for AI assistants, and a web dashboard. Python 3.12. The
application is in beta. Start with [README.md](README.md) and [docs/Index.md](docs/Index.md).

Decisions and constraints that the code does not show (deployment scope, repository rules, structure) are in [MEMORY.md](MEMORY.md). Read it first.

## Slash commands

| Command | What it does |
|---|---|
| `/onboard` | Checks dependencies, starts the service, sets up the MCP server, and shows how to search and query the database |
| `/test` | Asks whether to run **unit**, **integration** or **all** tests, runs them and summarises the report |
| `/crawl` | Checks a site's `robots.txt`, creates or picks a collection, crawls and indexes the site, watches the job and proves it with a search |

The commands live in `.claude/commands/` and are written as Role, Task, Context, Constraints.

## Everyday commands

```bash
./hippocampus up | status | logs | down        # the local stack (Docker or Podman); `reset` deletes all data and asks first
./hippocampus admin list-clients               # access-control accounts; tokens are shown once
scripts/prereqs.sh --dev                       # read-only check of what the machine needs (add --setup to create .venv)
scripts/check.sh                               # lint + migrations from zero + unit and integration tests + JS check
scripts/check.sh --all                         # the same, plus end-to-end, in one run and one report
.venv/bin/python -m pytest tests/unit -q       # fast tests, no database
scripts/lint.sh --fix                          # ruff, shellcheck, yamllint, markdownlint
```

## Layout

- `src/hippocampus/` is grouped by layer; imports point downwards only (`cli` and `service` -> `ingest` and `storage` -> `core`):
  - `core/` - `chunker.py`, `diff.py` (incremental indexing), `embedder.py`.
  - `ingest/` - `sync_core.py` (file filter and scan rules), `crawler.py`, `repo_ingest.py`, `docs_sync.py`, `files_ingest.py` (folder uploads).
  - `storage/` - `store.py` (the only code that runs SQL), `migrate.py`.
  - `service/` - `api.py` (wiring), `routes/` (one module per area), `auth.py`, `jobs.py`, `observability.py`, `workers.py`, `ui/` (the dashboard).
  - `cli/` - `admin.py`, `mcp_server.py`, `healthcheck.py`.
- `db/migrations/` - numbered SQL files, applied in order. `compose.yaml` and `deploy/` - the stack and its image.
- `tests/unit`, `tests/integration`, `tests/e2e` - see below. `scripts/` - checks, the throwaway test database, prerequisites.
- `docs/` - living documentation. Update the relevant page when you change behaviour.

## Tests

- **unit**: fakes only (`MemStore` and `KeywordEmbedder` in `tests/unit/test_api.py`). **integration**: a real database or the real model.
  **e2e**: a real stack in containers, plus the dashboard in Chromium. A plain `pytest` runs unit and integration.
- Integration tests start a throwaway pgvector container themselves. Never point tests at a real database; `HIPPO_TEST_DSN` is refused unless the
  database name contains `test` or `dev`.
- Every run writes `tests/reports/<YYYYMMDDHHMMSS>/` (`report.html`, `results.json`, `junit.xml`) and updates `tests/reports/latest`. The report
  gives a PASS / CONCERNS / FAIL decision. Details: [docs/Testing/README.md](docs/Testing/README.md).
- A new `store.py` method needs the same method on `MemStore`. A new access rule needs a test for the denied case and for the hidden case (404).

## Conventions and safety

- Style: ruff with line length 120 (`scripts/lint.sh`). Match the surrounding code's naming and comment density.
- Secrets: never commit `hippocampus.env`, tokens or MCP configuration that contains one. Tokens are shown once and only their hash is stored.
- The crawler copies other people's content: read [docs/User_Guide/legal-and-ethics.md](docs/User_Guide/legal-and-ethics.md) before changing it.
- A user may have a real Hippocampus running on this machine. Do not run `./hippocampus reset` or `compose down -v`, and do not touch its database,
  unless explicitly asked.
