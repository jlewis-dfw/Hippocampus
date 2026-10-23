# Testing and checks

What is tested, how to run it, and how CI runs the same thing. Nothing here touches a live system unless stated.

This is a living document. It describes the code as of migration `0002_access_control`; update it alongside the source.

## The one command

```bash
scripts/check.sh
```

The full run starts the development database if it is needed (see [../Local/README.md](../Local/README.md#the-development-database)) and then:

1. Runs the linters (`scripts/lint.sh`).
2. Applies the migrations to a throwaway database from zero and checks the schema is what the code expects (`scripts/migrate_check.py`).
3. Runs the whole test suite, including the database tests.
4. Checks that the dashboard JavaScript parses (needs node).

It prints a summary of what passed, failed and was **skipped**; a skipped step is never reported as a pass.

| Option | Effect |
|---|---|
| `--no-db` | Unit tests only. Quick, but the database tests and the migration check are skipped, and the summary says so |
| `--ci` | For CI: `HIPPO_TEST_DSN` must point at an empty pgvector database and node must exist; the database, migration and JavaScript checks may not be skipped |
| `--skip-js`, `--skip-md` | Leave out the JavaScript check or the Markdown linter (the CI image has neither node nor npm) |
| `--no-lint` | Leave out all linters |

Environment: `PY` (the Python to use; default `.venv/bin/python`, else `python3`) and `HIPPO_TEST_DSN` (use your own database instead of the development one).

## The test suite

124 tests are collected (122 pass, 2 skip unless their tools are installed). The files:

| File | Tests | Covers |
|---|---|---|
| `test_access.py` | 24 | Collections visibility, grants and globs, the admin flag, hidden-is-404, the reserved prefix, rotation, the last-admin rule, `HIPPO_REQUIRE_AUTH` |
| `test_admin_cli.py` | 1 | The admin tool |
| `test_api.py` | 11 | Routes, the network gate, the dashboard headers, job endpoints |
| `test_chunker.py` | 8 | Header-aware chunking, overlap, fenced blocks, size limits |
| `test_crawler.py` | 21 | URL rules, the public-address guard, `robots.txt`, the page limits, link following, the static engine (the browser test skips without Chromium) |
| `test_diff.py` | 3 | Which chunks are new, unchanged or removed |
| `test_docs_sync.py` | 20 | Webhook signature, path rules, the guarded fragments, sync of changed and removed files |
| `test_embedder_model.py` | 1 | The real embedding model (skips unless `HIPPO_RUN_MODEL_TESTS=1`) |
| `test_mcp.py` | 7 | The MCP tools, read-only mode, error handling |
| `test_prereqs.py` | 10 | The prerequisite checker (Linux only): risk gate, dry run, per-item confirmation |
| `test_repo_ingest.py` | 14 | Repomix parsing, secret pre-flight, whole-file rejection |
| `test_store_integration.py` | 4 | The real database: schema, atomic replace, search ordering. Skipped without a database |

### Fixtures

Most tests need neither a database nor the model:

- **`MemStore`** (in `tests/test_api.py`, imported by the other files) is an in-memory store that implements the same interface as the database store, so the API, the access rules and the jobs are tested quickly.
- **`KeywordEmbedder`** produces small deterministic vectors from keywords, so search ordering is testable without downloading the model.

Only `test_store_integration.py` talks to PostgreSQL, and only `test_embedder_model.py` uses the real model. Both are the slow ones, and both are what
`--no-db` leaves out.

### Running part of it

```bash
.venv/bin/python -m pytest tests/test_access.py -q
.venv/bin/python -m pytest -k rotate -q
HIPPO_RUN_MODEL_TESTS=1 .venv/bin/python -m pytest tests/test_embedder_model.py   # downloads the model once
```

## Linting

```bash
scripts/lint.sh              # everything
scripts/lint.sh --fix        # apply automatic fixes (ruff, markdownlint)
scripts/lint.sh --only py    # py | sh | yaml | md
scripts/lint.sh --ci         # a missing tool is a failure instead of a skip
scripts/lint.sh --changed    # only files that differ from origin/main (or --changed REF), including uncommitted ones
```

| Language | Tool | Configuration |
|---|---|---|
| Python | ruff (rules E, F, W, I, B, UP, RUF100; line length is not enforced) | `[tool.ruff]` in `pyproject.toml` |
| Shell | shellcheck (the `shellcheck-py` package) | defaults |
| YAML | yamllint | `.yamllint.yml` |
| Markdown | markdownlint-cli2 (pinned version, run through `npx`) | `.markdownlint-cli2.jsonc` |

The Python tools install with `pip install -e ".[dev]"` (or `scripts/prereqs.sh --dev --setup`). A missing tool is reported as **skipped**, and the run says
`LINT: nothing ran` if everything was skipped; with `--ci` a missing tool fails the run.

`--changed` finds the merge base with the reference, so it matches what a pull request changes, and lints just those files; a linter with no changed file of its type
is reported as skipped, not failed. Both pipelines run it first on pull requests as a fast gate (it needs the full git history, so the checkout fetches it), and
then still lint everything, so a change that breaks an untouched file is caught too.

PowerShell (`scripts/prereqs.ps1`) is not part of the lint step. To check it without Windows, parse it and run PSScriptAnalyzer inside a PowerShell container:

```bash
docker run --rm -v "$PWD/scripts:/s:ro" mcr.microsoft.com/powershell:latest pwsh -NoProfile -Command \
  "Install-Module PSScriptAnalyzer -Force -Scope CurrentUser; Invoke-ScriptAnalyzer -Path /s/prereqs.ps1"
```

The script has not been run on a real Windows machine; treat that as a known gap.

## Other scripts

| Script | Purpose |
|---|---|
| `scripts/migrate_check.py` | `apply` runs pending migrations against `HIPPO_TEST_DSN`; `scratch` creates a throwaway database on the same server, applies every migration from zero, checks the schema is what the code reads, and drops it |
| `scripts/smoke_live.py` | End-to-end check of a **running** instance: status and embedder, existing data undisturbed, a round trip through a restricted throwaway collection, isolation (anonymous and read-only callers), token rotation. Cleans up after itself. `--url` or `HIPPO_URL`; an admin token (`HIPPO_ADMIN_TOKEN` or typed) enables the later checks; `--no-admin` skips them. A revoked throwaway account row remains |
| `scripts/dev-db.sh` | The development database (Podman) |

## Continuous integration

The pipeline in `.github/workflows/ci.yml` builds `deploy/ci.Dockerfile`, which contains the same checks, and runs it next to a pgvector container. Its entry point is
`scripts/check.sh --ci --skip-js --skip-md`; the Markdown linter has its own step (`scripts/lint.sh --ci --only md`) because the CI image has no node.
A pull request is merged only when the pipeline is green. Details of where it runs belong to the deployment and are not part of this repository.

The public copy of the project has an equivalent workflow that runs `scripts/check.sh --ci` on every push and pull request.

## What is not tested

- The launcher script (`hippocampus`) and the container build: verified by running `./hippocampus up` and `scripts/smoke_live.py`.
- The dashboard in a real browser: the headers and the absence of external addresses are tested; behaviour is checked by hand.
- The Windows prerequisite script on real Windows.
- Large crawls and performance.

## Adding tests

- Put API and access tests in `tests/` using `MemStore` and `KeywordEmbedder`; add a database test only when the behaviour depends on SQL.
- A new access rule needs a test that shows the **denied** case as well as the allowed one, and that a hidden collection answers `404`.
- Never add a test that contacts the internet or a real site; the crawler tests use a `FakeFetcher` with canned pages.
