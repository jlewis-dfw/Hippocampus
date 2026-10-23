# Operations

Day-to-day running: health, logs, backups, upgrades and a security checklist.

## What runs

The local stack has three compose services (`compose.yaml`, project name `hippocampus`):

| Service | What it is | Lifetime |
|---|---|---|
| `postgres` | PostgreSQL 16 with the pgvector extension; data in the named volume `pgdata` | long-running; no host port |
| `migrate` | Runs `python -m hippocampus.migrate` once and exits | one-shot at every `up` |
| `api` | The service and dashboard (uvicorn, port 8090 in the container) | long-running; waits for `migrate` |

The images are `hippocampus-api:local` (built from `deploy/api.Dockerfile`) and `pgvector/pgvector:pg16`.

## Health and status

- `./hippocampus status` shows the containers and the status JSON.
- `GET /v1/status` returns the database round-trip time, the number of running jobs, the embedding model and the totals for
  what the caller may read. It is the best single check.
- The `api` container has a built-in health check (`python -m hippocampus.healthcheck`, every 15 seconds) that requests the
  interactive API page (`/docs`) from inside the container. `docker compose ps` shows `healthy` when it passes.
- `GET /openapi.json` is the machine-readable API description; `/docs` and `/redoc` are the interactive pages. (The interactive
  pages load their scripts from a public CDN, so they need internet access in the browser; `/openapi.json` does not.)
- `scripts/smoke_live.py --url <address>` is an end-to-end check that leaves nothing behind ([../Testing/README.md](../Testing/README.md)).

## Logs

```bash
./hippocampus logs api         # follow the service log
./hippocampus logs postgres
./hippocampus logs migrate     # what the last migration run did
```

The service logs requests and errors to standard output. Job logs (what a crawl fetched, what a sync rejected) are available
through the API and the dashboard, not in the container log, and are lost on restart.

## Backups

Everything lives in the PostgreSQL database (the `pgdata` volume). The embedding model is in the image, so it is not part of
the data. Back up with a logical dump:

```bash
docker compose --env-file hippocampus.env exec -T postgres \
  pg_dump -U hippocampus hippocampus > hippocampus-$(date +%F).sql
# Podman: use "podman compose" instead of "docker compose"
```

The dump contains every collection, source, chunk (including the vectors), the **account hashes** and the revoked accounts.
Treat it as sensitive: it holds your documents, and restricted collections are only restricted from the API, not in the dump.

Restore into an empty database:

```bash
./hippocampus reset                      # type DELETE: starts from nothing
./hippocampus up                         # recreates the volume and applies the migrations
docker compose --env-file hippocampus.env exec -T postgres \
  psql -U hippocampus hippocampus < hippocampus-2026-10-22.sql
```

If the dump conflicts with the freshly created admin account or the migration table, restore into a database made with
`createdb` and no `up` in between, or drop the schema first: `DROP SCHEMA hippocampus CASCADE;`. Test a restore before you
need it.

Because content is re-creatable from its sources, an alternative is not to back up the vectors at all and to re-run your crawls
and pushes. Accounts would then have to be recreated.

## Upgrading

```bash
git pull
./hippocampus up
```

The rebuilt image carries the new migrations; the `migrate` service applies any pending ones before the API starts. Migrations
are written to be **additive** (new columns have defaults, nothing is dropped), so an older image keeps working against a newer
schema if you need to roll back the code. Check the release notes or the `db/migrations` folder first, and take a backup before a
big upgrade.

The embedding model is fixed. Changing it would change the vector size and require re-embedding everything; it is not a
setting.

## Resource use

- **CPU:** embedding runs on the CPU and is the heavy part: indexing a long page takes seconds, searching one short query is fast.
  There is no GPU use.
- **RAM:** the API holds the embedding model (a few hundred megabytes) plus the job log of recent jobs. Give the engine at least
  2 GB.
- **Disk:** each chunk stores its text and a 384-dimension vector (about 1.5 KB of vector, plus the HNSW index). The numbers are
  small for documentation-sized collections: thousands of pages fit in a few hundred megabytes.
- **Concurrency:** requests are served by a thread pool; crawls and imports run in background threads. Searches during an import
  work, but an import slows them because both use the CPU.

## Resetting

`./hippocampus reset` removes the containers, the network and the `pgdata` volume after you type `DELETE`. There is no undo.

## Security checklist

- [ ] Leave `HIPPO_BIND=127.0.0.1` unless you need remote access. If you expose it, put a reverse proxy with **TLS and
      authentication** in front of it and publish only the proxy.
- [ ] Keep `HIPPO_REQUIRE_AUTH=true` so writes need a token.
- [ ] Narrow `HIPPO_ALLOWED_CIDRS` if you publish the port beyond this computer.
- [ ] Treat tokens like passwords: one account per tool, read-only unless writing is needed, rotate when someone leaves, never
      paste them into chat, tickets or commits.
- [ ] Keep restricted collections restricted, and remember the database and its backups are not protected by the API rules.
- [ ] Do not index secrets: the repository ingester rejects common secret shapes, but a crawl or a pushed file is stored as
      you send it.
- [ ] Remember crawled pages are untrusted text; do not let an assistant treat search results as instructions.
- [ ] Keep the host and the images updated: `./hippocampus up` reuses cached layers, so to pick up newer base images run `docker compose --env-file hippocampus.env build --pull --no-cache` (or `podman compose ...`) and then `./hippocampus up`.
