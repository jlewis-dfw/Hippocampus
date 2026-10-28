# The local stack

How `./hippocampus up` runs the whole service on one machine with Docker or Podman. For how to *use* it, see
[../User_Guide/getting-started.md](../User_Guide/getting-started.md); this page is how it works.

Files: `compose.yaml`, `hippocampus.env.example`, the launcher `hippocampus` (a bash script), and `deploy/api.Dockerfile`.

This is a living document. It describes the code as of migration `0002_access_control`; update it alongside the source.

## The compose project

`compose.yaml` defines a project named `hippocampus` with three services, one volume and the default network. Resource names follow compose's
convention (`hippocampus-api-1`, `hippocampus_pgdata`, `hippocampus_default`).

| Service | Image | Role | Lifetime |
|---|---|---|---|
| `postgres` | `docker.io/pgvector/pgvector:pg16` | The database. Data in the named volume `pgdata`. **No published port.** Health check: `pg_isready` every 3 s, 20 retries | long-running, `restart: unless-stopped` |
| `migrate` | built from `deploy/api.Dockerfile`, tagged `hippocampus-api:local` | Runs `python -m hippocampus.storage.migrate`, then exits | one-shot, `restart: "no"`; starts when `postgres` is healthy |
| `api` | `hippocampus-api:local` (`pull_policy: never`: it is built by `migrate`'s build section) | The service and dashboard on port 8090 inside the container | long-running, `restart: unless-stopped`; starts when `postgres` is healthy **and** `migrate` completed successfully |

The `api` health check is `python -m hippocampus.cli.healthcheck` (it requests the interactive API page from inside the container) every 15 s with a 30 s start
period and 5 retries.

### Wiring

```
host 127.0.0.1:${HIPPO_PORT} ──▶ api:8090 ──▶ postgres:5432          (internal network only)
                                  ▲
                      migrate ────┘ (runs first, then exits)
```

- **Published port:** `${HIPPO_BIND:-127.0.0.1}:${HIPPO_PORT:-8090}:8090`.
- **Database connection:** `postgresql://hippocampus:${POSTGRES_PASSWORD}@postgres:5432/hippocampus` for both `api` and `migrate` (the container hostname
  `postgres` is the service name).
- **Required variable:** `POSTGRES_PASSWORD`; compose stops with a message pointing at `./hippocampus up` if it is unset.
- **Passed through with defaults:** `HIPPO_ALLOWED_CIDRS` (`127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`), `HIPPO_REQUIRE_AUTH` (`true`),
  `HIPPO_CRAWL_MAX_PAGES` (200), `HIPPO_CRAWL_DELAY` (0.5).
- **Build argument:** `INSTALL_BROWSER` from `HIPPO_INSTALL_BROWSER` (default `0`): whether Chromium is installed in the image.

The default CIDR list includes the private ranges because, with a published port, the application sees the container network's address, not your real
address; the real boundary is the published address ([../Api/authorization.md](../Api/authorization.md#the-network-gate)).

## The image (`deploy/api.Dockerfile`)

Based on `python:3.12-slim`:

1. Installs the package with the `db`, `embed`, `api` and `browser` extras.
2. If `INSTALL_BROWSER=1`, installs Chromium with its system libraries (about 400 MB more).
3. Downloads the embedding model into `/opt/model-cache` **at build time**, so the container needs no internet when it runs.
4. Copies `db/` into `/app/db` and sets `HIPPO_MIGRATIONS_DIR` so the migration runner finds the files.
5. Creates a non-root user (`hippo`, uid 10001) and runs as it. Default command: uvicorn with the app factory on `0.0.0.0:8090`.

Sizes (measured): about 660 MB without Chromium, about 1.7 GB with it; the database image is about 450 MB.

## The launcher

`hippocampus` is a bash script that wraps compose. It runs from any directory (it changes into its own folder first).

| Piece | What it does |
|---|---|
| `detect_engine` | `CONTAINER_ENGINE` if set; else `docker` when `docker compose version` works; else `podman` when `podman compose version` works; else exits with advice |
| `compose` | `<engine> compose --env-file hippocampus.env ...` |
| `ensure_env` | If `hippocampus.env` is missing, copies the example with a random 32-character password (letters and digits only, so it is safe inside a connection URL), created with mode 600 |
| `port` | Reads `HIPPO_PORT` from the env file |
| `wait_healthy` | Polls `/v1/status` on `127.0.0.1:<port>` every 3 s for up to 6 minutes (with `curl`, else `python3`); on timeout prints the last 40 log lines and fails |
| `admin` | `compose run --rm --no-deps -T api python -m hippocampus.cli.admin ...`: runs the admin tool in a throwaway API container |
| `has_admin` | Runs `list-clients` and looks for an active admin account |

### Commands

| Command | Does |
|---|---|
| `up [args]` | `ensure_env`; `compose up -d --build [args]`; `wait_healthy`; creates the first admin (named `admin`) if there is no active admin, printing its token once; prints the dashboard address |
| `down` | `compose down` (containers and network; the data volume stays) |
| `status` | `compose ps` plus `GET /v1/status` |
| `logs [service]` | `compose logs -f` |
| `admin ARGS` | The admin tool |
| `reset` | After typing `DELETE`: `compose down -v` (removes the volume: all data) |
| `help` | The header of the script |

`up` always rebuilds the image first: with cached layers a repeat is quick, and changes to the code or to `HIPPO_INSTALL_BROWSER` take effect.

### Podman notes

`podman compose` is a thin wrapper that delegates to an external compose provider (`docker-compose` or `podman-compose`); install one. Image names appear with a
registry prefix (`docker.io/library/hippocampus-api:local`). The launcher and compose file are written to work with both engines; the behaviours above were checked with
Podman and its `docker-compose` provider.

## Data and persistence

- **The volume `hippocampus_pgdata`** holds all data. `down` keeps it; `reset` deletes it; `docker volume ls` shows it.
- The embedding model is in the image, not the volume.
- Configuration lives in `hippocampus.env` (git-ignored, mode 600). Deleting it does **not** delete data, but a new generated password will not match the old volume:
  keep the file, or restore the old password.

## The development database

A separate, simpler database for working on the code by hand: `scripts/dev-db.sh` (`up`, `down`, `status`, `logs`, `psql`, `migrate`, `reset`) starts one pgvector container from
`deploy/podman-compose.dev.yml` on `127.0.0.1:5442` (database `hippocampus_dev`, user `hippocampus`, a fixed development password) with its own volume. It does not
touch the local stack. **The script uses Podman.** The tests no longer use it: `scripts/check.sh` and the integration tests start their own throwaway database
(see [../Testing/README.md](../Testing/README.md#test-database)). To point the tests at a database of your own instead, for example this one:

```bash
docker run -d --name hippo-test-db -p 127.0.0.1:5442:5432 \
  -e POSTGRES_USER=hippocampus -e POSTGRES_PASSWORD=hippocampus_dev_password -e POSTGRES_DB=hippocampus_dev \
  pgvector/pgvector:pg16
export HIPPO_TEST_DSN=postgresql://hippocampus:hippocampus_dev_password@127.0.0.1:5442/hippocampus_dev
.venv/bin/python scripts/migrate_check.py apply     # prepare it
scripts/check.sh                                    # uses HIPPO_TEST_DSN instead of starting a throwaway database
```

## Troubleshooting

See [../User_Guide/troubleshooting.md](../User_Guide/troubleshooting.md#starting-and-building). Useful commands:

```bash
./hippocampus status
./hippocampus logs migrate         # did the migrations run?
docker compose --env-file hippocampus.env ps -a
docker compose --env-file hippocampus.env config      # the resolved compose file, with your settings substituted
```

## Tests

The local stack is verified by running it: `./hippocampus up`, then `scripts/smoke_live.py --url http://127.0.0.1:8090` (the same end-to-end check used on any instance).
`tests/unit/test_prereqs.py` covers the prerequisite checker. There is no automated test of the launcher itself.
