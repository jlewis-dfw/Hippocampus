# Hippocampus API

Technical reference for the HTTP API in `src/hippocampus/service/api.py` and `src/hippocampus/service/routes/`: a FastAPI application served by uvicorn. It stores and
searches embedded documents in PostgreSQL (see [../Database/README.md](../Database/README.md)) and serves the dashboard.

This is a living document. It describes the code as of migration `0002_access_control`; update it alongside the source.

## Contents

- [endpoints.md](endpoints.md) — every route: who may call it, the request, the response and the status codes.
- [authorization.md](authorization.md) — principals, tokens, grants, the network gate and the exact rules for each operation.

## Conventions

- **Base path:** all data endpoints live under `/v1/`. The dashboard is at `/ui/` and `/` redirects there. FastAPI's generated
  pages are at `/docs` (interactive) and `/redoc`, and the machine-readable description at `/openapi.json`. The dashboard's **API**
  tab shows the same reference offline, and its "Try it out" uses the token from the Access tab.
- **API reference groups:** every operation carries one tag (Status, Search, Collections, Access, Ingest, Folder uploads, Saved
  sources, Docs sync, Jobs), one per module in `service/routes/`; the groups and their descriptions are in `service/routes/tags.py`. A test fails
  when an operation has no tag or description, so a new route must add both (a docstring is its description).
- **Format:** requests and responses are JSON (`Content-Type: application/json`), except the live job log, which is
  `text/event-stream`. Timestamps are ISO 8601 strings, except a job's `created_at`, which is a Unix time in seconds.
- **Authentication:** optional bearer token, sent as `Authorization: Bearer hip_...` or `X-Hippo-Token: hip_...`
  ([authorization.md](authorization.md#how-a-request-is-authenticated)). No header means anonymous.
- **Errors:** `{"detail": "<message>"}` with the HTTP status. Request validation failures are `422` with FastAPI's standard
  `detail` list (field location, message, type).

| Status | Used for |
|---|---|
| 200 / 201 / 204 | Success (201 for creates, 204 for deletes and revocations) |
| 202 | A background job was started; the body holds its `job_id` |
| 401 | A `hip_` token that is unknown or revoked, or a bad webhook signature |
| 403 | Authenticated or anonymous but not allowed (missing write grant or admin rights, a blocked source, a client network outside the gate) |
| 404 | The thing does not exist **or you may not know it exists** (restricted collections, other people's jobs) |
| 409 | A name is taken, or the last admin would be removed |
| 422 | A value is invalid |
| 501 | A feature that is not configured (crawler, docs sync) |

- **No pagination** except `limit` on a collection's sources, and `top_k` on search (at most 50 results).
- **No CORS headers** are sent: browsers can call the API only from the same origin (the dashboard). Server-side callers are unaffected.
- **No rate limiting** and no request-size limit beyond uvicorn's defaults. Put a reverse proxy in front if you expose it.
- **Versioning:** there is a single version, `/v1`. Additive changes (new fields, new endpoints) are made in place.

## Architecture

`create_app(store_factory, embedder, jobs, crawler, allowed_networks, docs_sync, require_auth, prober)` builds the application. It takes
its collaborators as arguments, so the tests run the whole API against in-memory fakes ([../Testing/README.md](../Testing/README.md)).
`app_from_env()` is the production factory (`uvicorn --factory hippocampus.service.api:app_from_env`): it reads the environment
([../User_Guide/configuration.md](../User_Guide/configuration.md)), connects to the database, creates the embedder and the crawler,
and optionally the docs sync.

| Piece | What it is |
|---|---|
| `store_factory` | A context manager yielding a store (`PgStore` in production). Each `with store_factory()` block opens a **short-lived autocommit connection** and closes it; a request with a token uses one to authenticate and another to do its work |
| `embedder` | `FastEmbedder`: the embedding model on CPU, loaded lazily on first use. Shared by all requests |
| `JobRegistry` | In-memory registry of background jobs (crawls, imports, syncs) with a replayable log; see [../Architecture/jobs.md](../Architecture/jobs.md) |
| `crawler` | The callable that runs a crawl job ([../Architecture/crawler.md](../Architecture/crawler.md)); `None` makes `/v1/crawl` answer 501 |
| `prober` | The callable behind `POST /v1/crawl/probe` (`make_prober`); `None` makes it answer 501 |
| `allowed_networks` | CIDR list for the network gate; `None` turns it off |
| `docs_sync` | The optional docs-repository sync configuration; `None` makes its routes answer 501 |
| `require_auth` | Whether writes to shared collections also need a write grant |

### Request flow

1. **Network gate** (middleware): for paths under `/v1/`, the client address must be inside `allowed_networks`, else `403`.
2. **Principal**: each route takes `current_principal` as a dependency. With no credential header it is anonymous without touching
   the database; with a `hip_` token it looks up the token hash.
3. **Route**: validates the body (pydantic), checks the slug format, applies the access rules, does the work through the store,
   and returns JSON, or starts a background job and returns `202`.
4. **UI headers** (middleware): responses under `/ui` get a strict `Content-Security-Policy`, `Cache-Control: no-cache`,
   `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.

The routes are plain synchronous functions, so uvicorn runs them in its thread pool. Long work never blocks a request: crawls,
repository imports and syncs run in daemon threads owned by the job registry.

### Project layout

```
src/hippocampus/
  service/          — the HTTP service
    api.py          — wiring only: middleware, the shared dependencies, the routers, app_from_env
    routes/         — one module per area (health, search, collections, access, ingest, uploads, sources, docs, jobs),
                      the request bodies (schemas.py) and the shared access checks (deps.py)
    auth.py         — Principal, token hashing, grant matching, authenticate()
    jobs.py         — the in-memory job registry
    ui/             — the dashboard (index.html, app.js, app.css)
  storage/
    store.py        — PgStore: every SQL statement; connect_store()
    migrate.py      — the migration runner
  ingest/
    crawler.py      — HTML to Markdown, the crawl loop, the static and browser fetchers, the public-address check
    crawl_probe.py  — the pre-crawl probe behind POST /v1/crawl/probe (robots, size, suggested settings)
    repo_ingest.py  — Repomix pack parsing and the secret pre-flight
    docs_sync.py    — Forgejo push-webhook sync of a docs repository
    sync_core.py    — the file filter and scan rules; files_ingest.py — folder uploads
  core/
    diff.py         — content-hash sync of documents into a collection (the shared indexing step)
    chunker.py      — header-aware Markdown chunking
    embedder.py     — the embedding model wrapper and the Embedder protocol
  cli/
    admin.py        — the admin command-line tool
    mcp_server.py   — the stdio MCP server
    healthcheck.py  — container health probe
```

## Known gaps

- **Jobs are in memory.** A restart loses running jobs and every log; the data already stored is safe.
- **No per-source delete**, and crawls and pushes never prune ([../User_Guide/ingesting.md](../User_Guide/ingesting.md#removing-things)).
- **Static tokens only.** Tokens do not expire and there is no audit log of who read what. An external identity provider (JWT
  validation) would replace `authenticate()` without changing the grant model ([authorization.md](authorization.md#extending-it)).
- **The network gate cannot identify clients behind a proxy** or a container port mapping.
- **No automatic retry or resume** for a failed embedding batch: a failed job reports an error and can be run again, because
  unchanged sources are skipped.
