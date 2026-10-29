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
| `/search` | Searches the memory for a question (MCP tool, or the REST API as a fallback) and answers from the results with sources |
| `/indexfolder` | Previews, then indexes a local folder into a collection and keeps it in step (`scripts/index_folder.py`) |
| `/indexrepo` | Indexes a Git repository or code folder through a Repomix pack with the secret filter (`scripts/index_repo.py`) |
| `/pushdoc` | Pushes one Markdown file as a source and proves it with a search (`scripts/index_docs.py --file`) |
| `/recrawl` | Re-runs a saved crawl with its saved settings; previews what a lower depth would remove (`scripts/crawls.py`) |
| `/dbbackup` | Dumps the database to the git-ignored `backups/` folder and checks the dump (`scripts/backup.py`) |
| `/dbrestore` | Proves a dump restores, in a throwaway database; shows the manual steps for a real restore |
| `/health` | One-screen check of containers, service, database, content and recent log errors (`scripts/health.py`) |
| `/upgrade` | Shows pending migrations, backs up, rebuilds and checks the stack came back (`scripts/upgrade.py`) |
| `/accounts` | Lists, creates, rotates or revokes access accounts; tokens go to `token.env` and are never shown |
| `/prune` | Read-only inventory that flags empty, failed and stale collections (`scripts/inventory.py`) |
| `/migration` | Adds a numbered migration with its store, `MemStore`, schema-check, docs and test changes (`scripts/new_migration.py`) |
| `/docsync` | Finds docs and commands that no longer match the code and fixes them (`scripts/docs_check.py`) |
| `/precommit` | Runs the full checks, reviews the diff against the project rules and proposes a commit message; never commits |

The commands live in `.claude/commands/` and are written as Role, Task, Context, Constraints. Anything deterministic in them is a Python script in
`scripts/` (shared HTTP helpers in `scripts/hippo_client.py`); the command only decides, asks and explains.

## Everyday commands

```bash
./hippocampus up | status | logs | down        # the local stack (Docker or Podman); `reset` deletes all data and asks first
./hippocampus admin list-clients               # access-control accounts; tokens are shown once
scripts/prereqs.sh --dev                       # read-only check of what the machine needs (add --setup to create .venv)
scripts/check.sh                               # lint + migrations from zero + unit and integration tests + JS check
scripts/check.sh --all                         # the same, plus end-to-end, in one run and one report
.venv/bin/python -m pytest tests/unit -q       # fast tests, no database
scripts/lint.sh --fix                          # ruff, mypy, shellcheck, yamllint, markdownlint
```

`scripts/check.sh` and `scripts/lint.sh` only start `scripts/check.py` and `scripts/lint.py`, which hold the logic and run the same on every OS;
on Windows use `python scripts/check.py` and `python scripts/lint.py`. Both and the end-to-end tests pick the first container engine whose
daemon answers (`info`), so a stopped Docker next to a running Podman is handled; `CONTAINER_ENGINE` forces one. `scripts/system_specs.py` writes
the git-ignored `system-specs.md` (machine, engines and their state, optional test tools; no paths or user names) that `/onboard` creates.

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
- Every run writes `tests/reports/<YYYYMMDDHHMMSS>/` (`report.html`, `results.json`, `junit.xml`) and copies them into the real folder `tests/reports/latest` (paths masked; `report.html` and `results.json` are tracked in git, the run folders
  and `junit.xml` are not; commit a refresh on purpose, not with every change). The report
  gives a PASS / CONCERNS / FAIL decision. Details: [docs/Testing/README.md](docs/Testing/README.md).
- A new `store.py` method needs the same method on `MemStore`. A new access rule needs a test for the denied case and for the hidden case (404).

## Conventions and safety

- Style: ruff with line length 120 (`scripts/lint.sh`). Match the surrounding code's naming and comment density.
- Secrets: never commit `hippocampus.env`, tokens or MCP configuration that contains one. Tokens are shown once and only their hash is stored.
- The crawler copies other people's content: read [docs/User_Guide/legal-and-ethics.md](docs/User_Guide/legal-and-ethics.md) before changing it.
- Multi-OS: everything must work on macOS, Linux and Windows. New tooling, hooks and helper scripts are written in Python (already required),
  not bash or jq; use `pathlib` and `sys.executable`, not hard-coded `/` separators or `.venv/bin` paths. Shell scripts that already exist stay
  as they are (each has a PowerShell or Python counterpart where Windows needs one, e.g. `scripts/prereqs.ps1`).
- Claude Code hooks (`.claude/settings.json`, scripts in `scripts/hooks/`, tests in `tests/unit/test_hooks.py`) block destructive commands and direct
  access to `token.env` / `*.env`, lint each edited file, and ruff plus the unit tests when Claude finishes a turn that edited Python. Tokens go through `scripts/token_env.py`, which the hooks allow.
- A user may have a real Hippocampus running on this machine. Do not run `./hippocampus reset` or `compose down -v`, and do not touch its database,
  unless explicitly asked.
