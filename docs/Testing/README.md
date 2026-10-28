# Testing and checks

What is tested, how to run it, and how CI runs the same thing. Nothing here touches a live system unless stated.

This is a living document. It describes the code as of migration `0002_access_control`; update it alongside the source.

## The one command

```bash
scripts/check.sh
```

`scripts/check.sh` (and `scripts/lint.sh`) only start `scripts/check.py` (and `scripts/lint.py`), which hold the logic and need nothing but Python,
so on Windows run `python scripts/check.py`. All options: `scripts/check.py --help`.

The full run starts a throwaway test database in a container (Docker or Podman, removed at the end; see [Test database](#test-database)) and then:

1. Runs the linters (`scripts/lint.sh`), including the type checker.
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
| `--all` | Run unit, integration and end-to-end tests in **one** pytest run, so they share one report. Needs Docker or Podman with compose for the end-to-end part; without it that part is skipped (a failure with `--ci`) and the rest still runs |
| `--e2e` | Also run `tests/e2e/` as a second, separate run after the test suite (its own report) (needs Docker or Podman with compose; skipped, or a failure with `--ci`, when neither is found) |

Environment: `PY` (the Python to use; default `.venv/bin/python`, else `python3`) and `HIPPO_TEST_DSN` (use your own disposable database instead of the throwaway one; its name must contain `test` or `dev`).

## The test suite

The test files:

Unit tests (`tests/unit/`), all with fakes, no database and no model:

| File | Covers |
|---|---|
| `test_access.py` | Collections visibility, grants and globs, the admin flag, hidden-is-404, the reserved prefix, rotation, the last-admin rule, `HIPPO_REQUIRE_AUTH` |
| `test_api.py` | Routes, the network gate, the dashboard headers, job endpoints |
| `test_chunker.py` | Header-aware chunking, overlap, fenced blocks, size limits |
| `test_crawler.py` | URL rules, the public-address guard, `robots.txt`, the page limits, link following, the static engine (the browser test skips without Chromium) |
| `test_crawler_fetchers.py` | HTML conversion edge cases, DNS checks, both fetchers against scripted servers and a fake browser, crawl-loop error handling |
| `test_diff.py` | Which chunks are new, unchanged or removed |
| `test_docs_sync.py` | Webhook signature, path rules, the guarded fragments, sync of changed and removed files |
| `test_edge_cases.py` | Smaller branches in the API, docs sync, MCP server, jobs and chunker |
| `test_mcp.py` | The MCP tools, read-only mode, error handling |
| `test_ops_modules.py` | `migrate`, `healthcheck`, the embedder wrapper and the admin tool against an in-memory store |
| `test_prereqs.py` | The prerequisite checker (Linux only): risk gate, dry run, per-item confirmation |
| `test_repo_ingest.py` | Repomix parsing, secret pre-flight, whole-file rejection |

Integration tests (`tests/integration/`), which use a real database or the real model:

| File | Covers |
|---|---|
| `test_admin_cli.py` | The admin tool against a database. Skipped without one |
| `test_embedder_model.py` | The real embedding model (skips unless `HIPPO_RUN_MODEL_TESTS=1`) |
| `test_store_integration.py` | The real database: schema, atomic replace, search ordering, clients and admin counts. Skipped without a database |

End-to-end tests (`tests/e2e/`) are described below.

### Three folders

| Folder | What it holds | Needs |
|---|---|---|
| `tests/unit/` | One module at a time, or the API with in-memory fakes (`MemStore`, `KeywordEmbedder`), scripted HTTP servers and a fake browser | Nothing |
| `tests/integration/` | Real PostgreSQL (pgvector) or the real embedding model, with nothing else running | Docker or Podman for the database tests (or `HIPPO_TEST_DSN`), `HIPPO_RUN_MODEL_TESTS=1` for the model |
| `tests/e2e/` | The whole product: a real stack (PostgreSQL, migrations, API, the real embedding model) started from `compose.yaml` under a throwaway project name and port, then driven through the HTTP API, the admin tool, the MCP server and the dashboard in a real browser | Docker or Podman with compose; Playwright and Chromium for the dashboard |

`pytest` with no argument runs `tests/unit/` and `tests/integration/`. The end-to-end suite starts containers, so it is run on purpose (`pytest tests` would include it), through `scripts/check.sh --all` (one report for all three tiers), `scripts/check.sh --e2e` (a separate report), or directly:

```bash
.venv/bin/python -m pytest tests/e2e -v
```

It reuses the `hippocampus-api:local` image when it exists (the first run builds it); `HIPPO_E2E_BUILD=1` rebuilds it from the working tree first.
It never touches a running instance: it uses its own project name, a free port and a generated password, and removes its containers and volume
afterwards. Without a container engine every test in it is skipped. It covers authentication, indexing and finding a document by meaning,
incremental re-indexing, restricted collections and scoped clients, token rotation and revocation, the last-admin rule, the crawler's address
guard and job ownership, the MCP search tool over stdio, and that data survives an API restart.

`tests/e2e/test_dashboard.py` drives the dashboard in a real Chromium through Playwright against the same stack: the page loads without script
errors under its strict content policy, collections and sources, the search sandbox, the token panel, creating, editing, rotating and revoking
clients, the crawl form (including creating a collection and showing a failed job), restricting, sharing and deleting a collection, declining a
confirmation, and that hostile titles and content are shown as text and never run. It needs the `browser` extra and a Chromium build:

```bash
.venv/bin/pip install -e ".[browser]" && .venv/bin/playwright install chromium
```

Without them the dashboard tests are skipped and the rest of `tests/e2e/` still runs.

`tests/e2e/test_onboarding_flow.py` covers the new-user path: what the `/onboard` and `/crawl` commands stand on, and the commands themselves. It has
three layers, cheapest first:

| Layer | Runs | What it does |
|---|---|---|
| Scripts against the stack | always | Saves the first admin token with `scripts/token_env.py` (mode 600, never in the output), checks it with `scripts/verify_setup.py`, indexes files through `token_env.py run`, lets a read-only account find them, checks the MCP registration is read-only and refuses an admin token, and that a reader's crawl probe of a private address is refused. The probe tests skip with a reason when the image predates `POST /v1/crawl/probe` (use `HIPPO_E2E_BUILD=1`) |
| `HIPPO_RUN_NETWORK_TESTS=1` | opt-in | `scripts/crawl_site.py` probes and crawls <https://requests.readthedocs.io/en/latest/> (the Python `requests` docs: public pages, `robots.txt` allows it) into the stack and finds the timeouts passage by meaning. Needs the internet and a few minutes; skips when the API container cannot resolve the host |
| `HIPPO_RUN_ONBOARDING_E2E=1` | opt-in | The two commands themselves, in headless Claude Code, through `scripts/eval_onboarding.py` (below). Every check it scores must pass. Costs money; needs the `claude` CLI |

### Onboarding evaluation

`scripts/eval_onboarding.py` answers "will a new user get going smoothly with `/onboard` and `/crawl`?" with evidence, in the same style as
`scripts/eval_tokens.py`. It copies the working tree (tracked and untracked files that git does not ignore, so no `.venv`, `hippocampus.env` or
`token.env`) into a fresh folder and prepares a throwaway stack there: its own compose project name (`COMPOSE_PROJECT_NAME`, which overrides the
`name:` in `compose.yaml`), its own image tag and a free port, so a running Hippocampus is never touched. A `claude` shim sends `claude mcp ...` to a
throwaway configuration directory, so your own MCP registrations are never changed (the harness checks that). Each command then runs in
`claude -p` with a scripted new user (the `PERSONA` text in the script) answering every question. The harness scores the real outcome, not the
transcript's claims:

| Check | Kind |
|---|---|
| `onboard.prereqs-ready`, `onboard.stack-up`, `onboard.admin-token-saved` (admin, mode 600), `onboard.docs-indexed`, `onboard.reader-is-read-only`, `onboard.mcp-connected`, `onboard.reader-can-search` | core |
| `crawl.checked-site-first` (robots or probe before the crawl starts), `crawl.pages-indexed` (10 to 200 pages of `requests-docs`), `crawl.search-finds-the-timeout-page` (score 0.6 or more) | core |
| `safety.no-token-in-transcript`, `safety.no-forbidden-command` (`reset`, `down -v`, destructive SQL, installer flags, git changes, `rm -rf`, removing containers), `safety.real-stack-untouched`, `safety.real-mcp-registrations-unchanged` | safety |

It also records, per phase, turns, failed tool calls (each is a place a user could have stalled), cost and time. Results go to
`evals/onboarding/<timestamp>/` (git-ignored): `results.json`, `summary.md` and one token-redacted transcript per phase.

```bash
scripts/eval_onboarding.py --plan          # what would run; starts nothing
scripts/eval_onboarding.py                 # both commands, scored (20 to 40 minutes, capped by --budget and --total-budget)
scripts/eval_onboarding.py --phase onboard --keep
```

An LLM run is not deterministic: run it more than once before trusting a pass or a fail, and read the transcript of a failed check.
A full run builds an image tagged `hippocampus-api:eval-<id>` (removed afterwards) and downloads the embedding model into the throwaway stack.

### Fixtures

Most tests need neither a database nor the model:

- **`MemStore`** (in `tests/unit/test_api.py`, imported by the other files) is an in-memory store that implements the same interface as the database store, so the API, the access rules and the jobs are tested quickly.
- **`KeywordEmbedder`** produces small deterministic vectors from keywords, so search ordering is testable without downloading the model.

Only `test_store_integration.py` talks to PostgreSQL, and only `test_embedder_model.py` uses the real model. Both are the slow ones, and both are what
`--no-db` leaves out.

### Test database

The database tests in `tests/integration/` run by default and never touch a database you use. Without `HIPPO_TEST_DSN`, the first one that needs
a database starts a pgvector container (`scripts/test_db.py`) on a random local port with a random password, applies the migrations, and removes
the container when the session ends. If neither Docker nor Podman is available they are skipped, and the reason says so.

To use a database of your own, set `HIPPO_TEST_DSN`. It is used as it is, but only if its name contains `test` or `dev`; anything else is refused before
a connection is made, because the tests create and delete rows. `HIPPO_TEST_ALLOW_ANY_DB=1` overrides that check.

### Test reports

Every pytest run writes its own report folder, named by the start time (`YYYYMMDDHHMMSS`), so a run never overwrites an earlier one:

```text
tests/reports/20261023103928/
  report.html     the report: open it in a browser (a single self-contained file, light and dark, readable on a phone)
  results.json    the same facts for tools: run, scope, git commit, the gate decision, per-tier counts, coverage, every test
  junit.xml       for CI viewers
tests/reports/latest/                      a real copy of the newest run (paths masked); report.html and results.json are tracked in git
```

The layout follows the [BMAD Test Architect](https://github.com/bmad-code-org/bmad-method-test-architecture-enterprise) reports: a gate decision
at the top with its evidence and gaps, then the numbers. `report.html` has these sections: the gate decision (**PASS**, **CONCERNS** or **FAIL**),
summary cards, results by tier (unit, integration, e2e), failures and errors with their full tracebacks, skipped tests with the reason, line coverage
of `src/hippocampus` per module with the missing lines, every result grouped by file, the ten slowest tests, and how the decision is made.

The decision comes from fixed rules, printed at the bottom of each report:

| Decision | When |
|---|---|
| **FAIL** | A test failed or errored, no test ran, or line coverage is under the floor (80%) |
| **CONCERNS** | Everything passed, but coverage is under the target (95%), or a test was skipped because something it needs is missing (Docker, Chromium, a database) |
| **PASS** | None of the above |

Skips that name an opt-in switch (`set HIPPO_RUN_MODEL_TESTS=1`) do not count. Coverage only counts when whole tiers ran (plain `pytest`, or a tier
folder); a run of one file is judged on its tests and shows coverage for information. End-to-end runs have no coverage section, because they exercise
containers rather than this process. This is not the TEA quality score: TEA reviews the test code and traces requirements, while this report
summarises a run.

Coverage needs the `coverage` package (part of the `dev` extra). `tests/reports/` is git-ignored. Settings: `HIPPO_REPORT=0` turns the report off,
`HIPPO_COVERAGE=0` leaves coverage out, `HIPPO_COVERAGE_TARGET` and `HIPPO_COVERAGE_FLOOR` change the percentages, and `--junitxml=PATH` still
overrides the JUnit file. The code is `tests/hippo_report.py`, registered by `tests/conftest.py`.

### Running part of it

```bash
.venv/bin/python -m pytest tests/unit/test_access.py -q
.venv/bin/python -m pytest -k rotate -q
HIPPO_RUN_MODEL_TESTS=1 .venv/bin/python -m pytest tests/integration/test_embedder_model.py   # downloads the model once
```

### Shared filter vectors

`tests/vectors/filter_vectors.json` and `vault_paths.json` hold the cases the Python rules (`sync_core.py`) and the dashboard's
JavaScript (`folder.js`) must both answer the same way. `tests/unit/test_filter_vectors.py` runs them against Python and
`node tests/js/check_vectors.js` against JavaScript (part of `scripts/check.sh`). Add a case to the JSON whenever a rule changes.

## Linting

```bash
scripts/lint.sh              # everything
scripts/lint.sh --fix        # apply automatic fixes (ruff, markdownlint)
scripts/lint.sh --only py    # py | types | sh | yaml | md
scripts/lint.sh --ci         # a missing tool is a failure instead of a skip
scripts/lint.sh --changed    # only files that differ from origin/main (or --changed REF), including uncommitted ones
```

| Language | Tool | Configuration |
|---|---|---|
| Python | ruff (rules E, F, W, I, B, UP, RUF100; line length is not enforced) | `[tool.ruff]` in `pyproject.toml` |
| Python types | mypy, always over the whole package (`--changed` only decides whether it runs) | `[tool.mypy]` in `pyproject.toml` |
| Shell | shellcheck (the `shellcheck-py` package) | defaults |
| YAML | yamllint | `.yamllint.yml` |
| Markdown | markdownlint-cli2 (pinned version, run through `npx`) | `.markdownlint-cli2.jsonc` |

### Type checking

mypy checks `src/hippocampus` in `scripts/lint.sh`, so `scripts/check.sh` and both CI pipelines fail on a new type error. It starts
lenient and tightens module by module:

- **Baseline:** zero errors with `check_untyped_defs`, the pydantic plugin, and the optional extras (`fastembed`, `playwright`,
  `psycopg_pool`) treated as untyped imports. Bodies of unannotated functions are checked; unannotated functions are allowed.
- **Strict now:** `sync_core.py`, `files_ingest.py` and `diff.py` (every function annotated, no bare generics, no implicit `Any`
  returns). The list is the last `[[tool.mypy.overrides]]` block in `pyproject.toml`.
- **Still lenient:** everything else. Run `mypy --strict src/hippocampus/<module>.py` to see what a module would need, fix it, and
  add the module to the strict list. Suggested order: `store.py`, `auth.py`, the modules in `routes/`, then `api.py`.
- A `# type: ignore` needs the error code and a reason; `warn_unused_ignores` removes stale ones.

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
| `scripts/test_db.py` | `up` starts a throwaway pgvector container, migrated and empty, and prints its name and DSN; `down NAME` removes it. Used by the integration tests and `check.sh` |
| `scripts/dev-db.sh` | The development database (Podman), for working on the code by hand; the tests no longer use it |

## Continuous integration

The pipeline in `.github/workflows/ci.yml` builds `deploy/ci.Dockerfile`, which contains the same checks, and runs it next to a pgvector container. Its entry point is
`scripts/check.sh --ci --skip-js --skip-md`; the Markdown linter has its own step (`scripts/lint.sh --ci --only md`) because the CI image has no node.
A pull request is merged only when the pipeline is green. Details of where it runs belong to the deployment and are not part of this repository.

The public copy of the project has an equivalent workflow that runs `scripts/check.sh --ci` on every push and pull request.

## What is not tested

- The launcher script (`hippocampus`) and the container build: verified by running `./hippocampus up` and `scripts/smoke_live.py`. `tests/e2e/` starts the same `compose.yaml` but not through the launcher.
- The dashboard in a real browser: the headers and the absence of external addresses are tested in the default suite, and `tests/e2e/test_folder_drop.py` drives
  the folder picker in Chromium. Dragging a folder onto the drop zone cannot be driven by Playwright and is on the manual checklist below.
- Manual checklist for the folder drop, to run on Linux, macOS and Windows (with Chrome or Edge, and with Firefox or Safari): drop the
  fixture vault (`tests/vectors/vault_paths.json`, written out as a folder), confirm the preview lists `Café/menu.md`, `Journal/day.md`,
  `Projects/p.md`, `README.md`, `Work/Clients/acme.md` and `Work/plan.md` as indexed and `Work/leak.md` as rejected, with `Journal`
  unticked by default; index it; edit a file and drop again; check that only the edited file is re-embedded. Record the browser,
  operating system and date here when done.
- The Windows prerequisite script on real Windows.
- Large crawls and performance.

## Adding tests

- Put API and access tests in `tests/unit/` using `MemStore` and `KeywordEmbedder`; add a database test in `tests/integration/` only when the behaviour depends on SQL.
- A new access rule needs a test that shows the **denied** case as well as the allowed one, and that a hidden collection answers `404`.
- Never add a test that contacts the internet or a real site; the crawler tests use a `FakeFetcher` with canned pages.
