# Project memory

Decisions and constraints that the code does not show. Read this with [CLAUDE.md](CLAUDE.md) before changing structure or
scope. Keep it short; when an entry stops being true, change or delete it.

## Scope

- **Deployment.** Every user runs the stack locally through Podman (Docker also works), bound to the local machine only
  (`127.0.0.1`), with no proxy or tunnel in front. One user, one machine, one API worker. Treat per-address login rate limiting
  and multi-worker persistence as low priority; keep the connection pool small; keep everything working under Podman.
- **Revisit** these only if the service is ever published beyond localhost.

## Rules for this repository

- It is public. It must hold no private project names, no infrastructure path fragments and no secrets. Path exclusions are
  configuration only (`HIPPO_EXCLUDE_PATH_PARTS`, none by default). Local files (`hippocampus.env`, `token.env`, `wip/`,
  `stage4plan/`) are ignored.

## Structure decisions

- `src/hippocampus/` is grouped by layer: `core/` (chunker, diff, embedder), `ingest/`, `storage/` (`store.py` is the only SQL),
  `service/` and `cli/`. Imports point downwards only. `service/api.py` stays one file and only wires the application; the routes live
  in `service/routes/`, one `APIRouter` module per area, with shared checks in `deps.py`, request bodies in `schemas.py` and API
  reference groups in `tags.py`. `auth.py`, `jobs.py`, `observability.py` and `workers.py` sit beside `api.py` inside `service/`.
  Entry points: `hippocampus.service.api:app_from_env`, `hippocampus.storage.migrate`, `hippocampus.cli.healthcheck`, `.admin`, `.mcp_server`.
- A refactor changes no behaviour: the generated OpenAPI document is compared before and after, and only tags and descriptions
  may differ.
- Every route has a tag and a one-line docstring (a test requires both).
- The dashboard API tab frames `ui/api.html` with the vendored Swagger UI (`ui/vendor/swagger-ui/`, pinned version). Only that page
  has a relaxed policy (inline styles); every other dashboard page keeps the strict one.

## Hardening plan

The plan and status are local: `wip/hardening/goals.md` and `roadmap.md`. Done: repository hygiene, the design decisions page
([docs/Architecture/design-decisions.md](docs/Architecture/design-decisions.md)), the single-worker guard, logging, the router
split, the API tab and tagged API reference. Next: connection pool (small defaults, `503` when exhausted), then type checking.

## Working notes

- Run the unit suite and `scripts/lint.sh` before each commit, and check their exit status, not the tail of the output.
- Browser checks: Chromium is only used by the e2e tests; Firefox headless can take a screenshot for a quick look.
