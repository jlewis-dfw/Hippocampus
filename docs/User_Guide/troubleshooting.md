# Troubleshooting

Symptoms, causes and fixes. API errors are JSON of the form `{"detail": "..."}`; the dashboard shows them in a banner as
`<status>: <detail>`.

## Starting and building

| Symptom | Cause and fix |
|---|---|
| `no running 'docker compose' or 'podman compose' was found` | Install Docker with the compose plugin or Podman with compose, **and start it** (Docker Desktop, or `podman machine start`): an engine whose daemon does not answer is skipped. Check with `docker compose version` and `docker info`, or set `CONTAINER_ENGINE`. |
| The build fails while downloading the model or packages | The first build needs internet access once (Python packages and the embedding model). Retry; check proxy settings; make sure there is disk space (`df -h`). |
| `the service did not become healthy in time` | The first start loads the embedding model, which can take a few minutes on a slow disk. See `./hippocampus logs api`. A crash loop usually shows a database or configuration error there. |
| `variable is not set: run ./hippocampus up, which creates hippocampus.env` | You ran `docker compose` by hand without the env file. Use the launcher, or pass `--env-file hippocampus.env`. |
| `password authentication failed for user "hippocampus"` | You changed `POSTGRES_PASSWORD` after the database volume was created; the old password is still inside it. Restore the old value, or `./hippocampus reset` (deletes data) and start again. |
| `port is already allocated` / `address already in use` | Another program uses `HIPPO_PORT`. Change the port in `hippocampus.env` and run `./hippocampus up`. |
| Nothing listens from another machine | The default `HIPPO_BIND=127.0.0.1` is this computer only. See [getting-started.md](getting-started.md#network-exposure). |
| The first admin token scrolled away or was lost | `./hippocampus admin create-client --name admin-2 --admin` makes a new one; revoke the lost one with `revoke-client`. |
| `./hippocampus up` says an admin already exists but you have no token | Same fix: create another admin with the admin tool. |

## Access errors

| Status and message | Meaning and fix |
|---|---|
| `403 client network not allowed` | Your address is outside `HIPPO_ALLOWED_CIDRS`. Behind Docker the service sees the engine's private address, so the private ranges are allowed by default; if you narrowed the list, add your network. See [configuration.md](configuration.md#the-network-gate). |
| `401 unknown or revoked token` | The `hip_` token is wrong, was revoked, or was rotated. Check for a stray space or newline when pasting, and for an old token in a saved configuration. |
| `404 collection '<slug>' not found` | The collection does not exist **or you have no grant on it**. The two look identical by design. Check the slug and the account's grants. |
| `403 writing to collection '<slug>' needs a token with a write grant` | With `HIPPO_REQUIRE_AUTH=true`, or on a restricted collection, writing needs a write grant. Use an account that has one. |
| `403 creating a collection needs a token with a write grant` | Same for creating a shared collection when authentication is required. |
| `403 admin token required` | The action (restricted collections, visibility, accounts) is admin-only. |
| `403 this collection name cannot be used` | A restricted collection with that name exists that you cannot see. Pick another name. |
| `409 collection '<slug>' already exists` | Choose another slug, or use the existing collection. |
| `422 invalid collection slug` | Slugs are lowercase letters, digits and dashes, starting with a letter or digit, up to 64 characters. |
| `422 invalid client name` or `invalid grant` | Names: letters, digits, `-`, `_`. Grants: lowercase letters, digits, `-`, `*`; a bare `*` is refused. |
| `409 cannot remove the last admin` / `cannot revoke the last admin` | Create another admin first. |
| `404 client '<name>' not found` | No active account has that name (a revoked one counts as not found for edits and rotation). |

## Indexing

| Symptom | Cause and fix |
|---|---|
| `501 crawler not available` | The service was started without a crawler. Use the standard `api` service. |
| Crawl log: `refused <url>: '<host>' resolves to a non-public address` | By design: private, local and VPN addresses are never crawled. |
| Crawl log: `skip (robots.txt) <url>` | The site's `robots.txt` disallows the crawler for that path. |
| Crawl log: `skip (404 ...)`, `skip (200 application/pdf ...)`, `skip (empty)` | Not an HTML page, not found, or the page had no text after cleaning. Only HTML is indexed. |
| The crawl finishes with 1 page although you asked for depth 3 | Links are followed only on the start host and only if they match `match_pattern`; a JavaScript-only site shows few links to the static engine. Try the `browser` engine. |
| `503 the database is busy` on every request, and the API log says `failed to resolve host 'postgres'` | Podman's container DNS (`aardvark-dns`) has gone stale, so the API cannot find the database although it is healthy. Run `./hippocampus down`, stop the stale `aardvark-dns` process (Podman starts a fresh one), then `./hippocampus up`. Your data is kept: only `reset` deletes it. Note that the helper is shared by every Podman network of your user. |
| Job error `engine 'browser' needs the 'browser' extra (playwright) and Chromium` | Rebuild with `HIPPO_INSTALL_BROWSER=1` in `hippocampus.env` and `./hippocampus up`. |
| `403 this source is excluded from ingestion` | The address or path contains a guarded fragment (a deliberate block on indexing internal documents). |
| `422 unrecognised pack format (use repomix --style xml or --style markdown)` | The pack is not a Repomix xml or markdown file. |
| A file is listed under `rejected` in a repository job | It matched a secret pattern, had a credential-like name, or was over 200,000 bytes; the `reasons` name the rule. |
| `501 docs sync not configured` | The Forgejo variables are not all set ([configuration.md](configuration.md#docs-repository-sync-optional)). |
| `404 collection '<name>' not found` from a webhook | The sync's target collection must exist before the first push. Create it. |
| `401 bad signature` from a webhook | The webhook secret differs from `HIPPO_WEBHOOK_SECRET`, or a proxy altered the body. |
| Indexing is slow | Embedding runs on the CPU. Long pages take seconds; a big crawl takes minutes. Watch the job log. |
| A deleted page is still returned by search | Crawls and pushes never prune. See [ingesting.md](ingesting.md#removing-things). |

## Searching

| Symptom | Cause and fix |
|---|---|
| No results | The collection is empty, you have no read grant on it (it then looks missing), or `min_score` is too high. Check **Sources** and the account's grants. |
| Results are irrelevant, with low scores | Nothing relevant is indexed, or the question is too vague. See [searching.md](searching.md#asking-good-questions). A search always returns the nearest chunks, however poor. |
| An exact identifier or error code is not found | Search is by meaning, not keywords. Quote the surrounding sentence instead. |
| The first search after a restart is slow | The embedding model loads on first use. |
| `422` on a search | The query is empty or longer than 4000 characters, `top_k` is outside 1 to 50, or `min_score` is outside -1 to 1. |

## Dashboard

| Symptom | Cause and fix |
|---|---|
| The status pill says `API unreachable` | The service is down or the browser cannot reach it. `./hippocampus status`. |
| The identity button stays `anonymous` after pasting a token | The token was rejected (a banner shows `401`), or storage is blocked in a private window. Try again; check the token. |
| A restricted collection is missing from the list | You are not signed in with a token that can read it. |
| `Session expired. Reload the page to sign in again.` | A login proxy in front of the dashboard ended your session. Reload. |
| The job log stops early | A proxy may buffer the live stream. The final status and result still appear when the job ends. |

## Looking deeper

```bash
./hippocampus logs api                      # the service log (requests, errors)
./hippocampus logs migrate                  # the last migration run
docker compose --env-file hippocampus.env exec postgres psql -U hippocampus hippocampus   # a SQL prompt (podman compose is the same)
scripts/smoke_live.py --url http://127.0.0.1:8090                                        # end-to-end check
```

If you find a bug, a minimal reproduction (the request you sent, the collection settings, the relevant log lines with tokens removed)
makes it quick to fix.
