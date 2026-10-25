# Configuration

All settings are environment variables. With the local stack you set them in **`hippocampus.env`** (created by
`./hippocampus up` from `hippocampus.env.example`); after editing, run `./hippocampus up` again. Other deployments set the same
variables in whatever starts the service. Variables with a default can be left out.

## Local stack settings (`hippocampus.env`)

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | generated | The database password. Only the containers use it. Changing it after the first run does **not** change the password inside an existing database volume; reset the volume or change it in PostgreSQL too. |
| `HIPPO_BIND` | `127.0.0.1` | The address the published port listens on. `127.0.0.1` = this computer only. `0.0.0.0` = your network. |
| `HIPPO_PORT` | `8090` | The port on the host. The container always listens on 8090. |
| `HIPPO_REQUIRE_AUTH` | `true` | `true`: writes need a token with a write grant; reading shared collections stays open. See [collections-and-access.md](collections-and-access.md#the-hippo_require_auth-switch). |
| `HIPPO_ALLOWED_CIDRS` | `127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` | Which client addresses may call `/v1/*`; others get `403 client network not allowed`. See [below](#the-network-gate). |
| `HIPPO_CRAWL_MAX_PAGES` | `200` | The most pages one crawl will fetch. |
| `HIPPO_CRAWL_DELAY` | `0.5` | Seconds to wait between requests in a crawl. |
| `HIPPO_INSTALL_BROWSER` | `0` | `1` builds the image with Chromium so the crawler's `browser` engine works (about 400 MB more). Takes effect when the image is next built, which `./hippocampus up` does every time. |

The launcher itself reads one more: `CONTAINER_ENGINE=docker|podman` forces the engine.

## Service settings (the API process)

| Variable | Default | Meaning |
|---|---|---|
| `HIPPO_DSN` | (required) | PostgreSQL connection string, for example `postgresql://hippocampus:PASSWORD@postgres:5432/hippocampus`. The password may instead come from the standard `PGPASSWORD` variable. |
| `HIPPO_MODEL_CACHE` | the library default | Where the embedding model is stored. The image sets `/opt/model-cache`, where the model is baked in, so the container needs no internet at run time. |
| `HIPPO_ALLOWED_CIDRS` | `10.0.0.0/8,100.64.0.0/10,127.0.0.0/8,::1/128` | As above. This is the service's built-in default; the local stack sets its own (above). |
| `HIPPO_REQUIRE_AUTH` | off | Accepts `1`, `true` or `yes` (case-insensitive). |
| `HIPPO_CRAWL_MAX_PAGES`, `HIPPO_CRAWL_DELAY` | `200`, `0.5` | As above. |
| `HIPPO_REQUIRE_SINGLE_WORKER` | `false` | Refuse to start when `WEB_CONCURRENCY` is above 1, instead of only logging a warning. |
| `HIPPO_EXCLUDE_PATH_PARTS` | none | Comma-separated, case-insensitive path fragments that are never indexed: repository and docs ingesters, file and folder uploads, crawled URLs, `index_doc`. No client setting removes them. |
| `HIPPO_MIGRATIONS_DIR` | `<repo>/db/migrations` | Where `python -m hippocampus.migrate` reads migrations. The image sets `/app/db/migrations`. |

### Docs repository sync (optional)

Off unless the first three are all set. See [ingesting.md](ingesting.md#keeping-a-docs-repository-in-sync).

| Variable | Meaning |
|---|---|
| `HIPPO_FORGEJO_URL` | Base URL of the Forgejo server, for example `https://forge.example.com` |
| `HIPPO_FORGEJO_TOKEN` | A token with read access to the one repository |
| `HIPPO_WEBHOOK_SECRET` | The webhook's shared secret (HMAC-SHA256 key) |
| `HIPPO_DOCS_OWNER`, `HIPPO_DOCS_REPO` | The repository to index. Set both explicitly |
| `HIPPO_DOCS_COLLECTION` | The collection to fill; it must already exist. Set it explicitly |
| `HIPPO_DOCS_BRANCH` | The branch to follow (default `main`) |

## Tool settings

| Variable | Used by | Meaning |
|---|---|---|
| `HIPPO_URL` | MCP server, `scripts/smoke_live.py` | Base URL of the service. The smoke test defaults to `http://127.0.0.1:8090`; the MCP server has no default and fails with a clear message if it is unset |
| `HIPPO_TOKEN` | MCP server | Token sent as `Authorization: Bearer ...`. Optional: without it the server acts as anonymous |
| `HIPPO_MCP_READONLY` | MCP server | `1`, `true` or `yes` leaves out the indexing tool |
| `HIPPO_ADMIN_TOKEN` | `scripts/smoke_live.py` | An admin token, instead of typing it |
| `HIPPO_DSN` | `python -m hippocampus.admin`, `python -m hippocampus.migrate` | Database to talk to |
| `HIPPO_TEST_DSN` | `scripts/check.sh`, the database tests | A pgvector database the tests may use. Use a throwaway one |
| `HIPPO_RUN_MODEL_TESTS` | the test suite | `1` runs the tests that download the real embedding model |
| `PY` | `scripts/check.sh` | The Python to use (default `.venv/bin/python`, else `python3`) |

## The network gate

Every request to a `/v1/*` path is checked against `HIPPO_ALLOWED_CIDRS` using the **client address the service sees**. A request
from outside gets `403 {"detail": "client network not allowed"}`. The dashboard page itself (`/ui/`) is not gated, because
it contains no data; its API calls are.

This is **defence in depth, not authentication**. It cannot tell two clients apart when they arrive through a proxy or a
container port mapping, because the service then sees the proxy's or the container network's address. The real controls are
binding the port to a private address (`HIPPO_BIND`) and the tokens. See [operations.md](operations.md#security-checklist).

## Where settings are read

`hippocampus.env` is passed to `docker compose` / `podman compose` by the launcher (`--env-file`); `compose.yaml` maps the
variables into the containers. The API reads its variables once at start, so a change needs a restart (`./hippocampus up`).
