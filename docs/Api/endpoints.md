# HTTP endpoint reference

Every route of the API. "Needs" names the access required; the exact rules are in [authorization.md](authorization.md).
Examples use `$T` for a token and `http://127.0.0.1:8090` for the service. All routes are under `/v1` unless stated.

| Method and path | Needs | Purpose |
|---|---|---|
| `GET /v1/whoami` | anyone | Who the caller is |
| `GET /v1/status` | anyone | Health and totals (counts only for what the caller may read) |
| `POST /v1/search` | read on each collection searched | Semantic search |
| `GET /v1/collections` | anyone (shows what you may read) | List collections |
| `POST /v1/collections` | write grant or anyone (shared); admin (restricted) | Create a collection |
| `PATCH /v1/collections/{slug}` | admin | Rename, describe, or change visibility |
| `DELETE /v1/collections/{slug}` | write (shared); admin (restricted) | Delete a collection and everything in it |
| `GET /v1/collections/{slug}/sources` | read | List a collection's sources |
| `POST /v1/ingest/file` | write | Index one Markdown document |
| `POST /v1/ingest/files` | write | Index a folder of files, in batches (or preview with `dry_run`) |
| `POST /v1/ingest/repo` | write | Index a Repomix pack (job) |
| `DELETE /v1/ingest/files/{session}` | the run's owner | Cancel a folder run; the index keeps what it had |
| `GET /v1/sources`, `GET /v1/sources/{name}` | read on the source's collection | Saved folder sources |
| `POST /v1/sources` | write on the collection and on each folder collection | Save a folder source |
| `PATCH /v1/sources/{name}`, `DELETE /v1/sources/{name}` | write | Edit the rules, or forget it (`?purge=true` also removes its files) |
| `POST /v1/sources/{name}/sync` | write | Re-sync now (`409` for an upload source: drop the folder again) |
| `POST /v1/crawl` | write | Crawl a site (job) |
| `POST /v1/sync/docs` | write on the docs collection | Full resync of the docs repository (job) |
| `POST /v1/webhooks/forgejo` | HMAC signature | Forgejo push webhook (job) |
| `GET /v1/jobs/{job_id}` | the job's owner, or admin | Job status and result |
| `GET /v1/jobs/{job_id}/events` | the job's owner, or admin | Live job log (server-sent events) |
| `GET /v1/clients` | admin | List accounts |
| `POST /v1/clients` | admin | Create an account |
| `PATCH /v1/clients/{name}` | admin | Change grants, admin flag or note |
| `POST /v1/clients/{name}/rotate` | admin | New token for an account |
| `DELETE /v1/clients/{name}` | admin | Revoke an account |

Outside `/v1`: `GET /` redirects to `/ui/`; `/ui/*` serves the dashboard files; `/docs`, `/redoc` and `/openapi.json` are
FastAPI's generated documentation.

Common failures for every route that takes a token: `401 unknown or revoked token`; and, when the client's address is outside the
allowed networks, `403 client network not allowed`. A malformed slug anywhere in a path or body is `422 invalid collection slug '<value>'`.

---

## `GET /v1/whoami`

Returns the caller's identity, so a client can check its token.

```json
{"name": "admin", "admin": true, "read": [], "write": [], "require_auth": true}
```

An anonymous caller gets `{"name": "anonymous", "admin": false, "read": [], "write": [], "require_auth": ...}`.

## `GET /v1/status`

```json
{"db_latency_ms": 9.3, "active_jobs": 0, "embedder": {"model": "BAAI/bge-small-en-v1.5", "dimensions": 384},
 "collections": 2, "sources": 114, "chunks": 1043}
```

`db_latency_ms` is the time to count the totals. The counts cover only the collections the caller may read.

## `POST /v1/search`

Request:

```json
{"query": "how do we handle database backups?", "collections": ["team-notes"], "top_k": 5, "min_score": 0.0}
```

| Field | Type | Default | Constraint |
|---|---|---|---|
| `query` | string | required | 1 to 4000 characters |
| `collections` | string[] | `[]` | slugs; empty = every collection the caller may read |
| `top_k` | integer | 5 | 1 to 50 |
| `min_score` | number | 0.0 | -1 to 1 |

Response `200`:

```json
{"query": "...", "results": [
  {"collection": "team-notes", "uri": "notes/backups.md", "title": "Backups", "header_path": "Backups > Nightly dump",
   "content": "...", "chunk_index": 1, "score": 0.712}]}
```

Errors: `404 collection '<slug>' not found` if any named collection is missing or not readable; `422` for invalid input.
The filter by readable collections runs **inside the SQL query, before the `top_k` limit** ([../Architecture/search.md](../Architecture/search.md)).

## `GET /v1/collections`

```json
{"collections": [{"slug": "docs", "name": "Docs", "description": null, "created_at": "2026-10-22T16:17:27+00:00",
                  "source_count": 114, "chunk_count": 1043, "visibility": "shared"}]}
```

Ordered by slug; only collections the caller may read.

## `POST /v1/collections`

Request:

```json
{"slug": "team-notes", "name": "Team notes", "description": "optional", "visibility": "restricted"}
```

`slug` follows `^[a-z0-9][a-z0-9-]{0,63}$`; `name` is 1 to 255 characters; `visibility` is `shared` (default) or `restricted`.
Response `201`: `{"slug", "name", "description", "visibility"}`.

| Status | When |
|---|---|
| 403 `creating a collection needs a token with a write grant` | A shared collection, and `HIPPO_REQUIRE_AUTH` is on, and the caller has no write grant matching the slug |
| 403 `admin token required` | `visibility: "restricted"` and the caller is not an admin |
| 403 `this collection name cannot be used` | The slug is taken by a restricted collection the caller cannot see |
| 409 `collection '<slug>' already exists` | The slug is taken by a collection the caller can see |
| 422 `collections named jstories* hold private data and must be restricted` | A reserved private prefix with `shared` ([authorization.md](authorization.md#reserved-private-names)) |

## `PATCH /v1/collections/{slug}`

Admin only. Any of `name`, `description`, `visibility` (all optional). Fields you omit are left as they are; there is no way to clear a
description back to empty. Returns the collection row as in the list. Errors: `404` if the caller cannot read it, `403 admin token
required`, `422` when a reserved private name would become shared.

## `DELETE /v1/collections/{slug}`

Deletes the collection with its sources and chunks (`204`). A restricted collection needs an admin; a shared one needs write access.
`404` if the caller cannot read it. **There is no undo.**

## `GET /v1/collections/{slug}/sources`

Query `limit` (1 to 1000, default 200). Newest-indexed first.

```json
{"collection": "docs", "sources": [{"uri": "https://docs.example.com/guide", "title": "Guide", "status": "indexed",
  "chunk_count": 6, "sha256": "9f2c4e1a0b7d", "error": null, "last_indexed_at": "2026-10-22T16:20:01+00:00"}]}
```

`sha256` is the first 12 characters of the content hash.

## `POST /v1/ingest/file`

```json
{"collection": "team-notes", "uri": "notes/backups.md", "title": "Backups", "text": "# Backups\n..."}
```

`uri` is 1 to 1024 characters, `title` at most 512. Synchronous: it chunks, embeds and stores, then returns
`{"added": ["notes/backups.md"], "changed": [], "unchanged": []}` (exactly one of the three lists holds the uri). An unchanged
text is skipped without embedding.

| Status | When |
|---|---|
| 403 `this source is excluded from ingestion` | The `uri` contains a guarded fragment (`FORBIDDEN_URI_PARTS`) |
| 403 `private story sources may only be indexed into a restricted collection` | A reserved private URI scheme into a shared collection |
| 403 / 404 | No write access / no such readable collection |

## `POST /v1/ingest/files`

Indexes a folder of files. Many requests with one `session` make one run, so a large folder stays under the request size limit.

```json
{"collection": "notes", "source": "vault", "files": [{"path": "Work/plan.md", "content": "# Plan\n..."}],
 "filter": {"include": ["Work/"], "exclude": ["Work/Clients/"], "extensions": ["md"], "default_excludes": true,
            "gitignore": null},
 "session": null, "final": false, "dry_run": false}
```

- `source` is the folder's label (`^[A-Za-z0-9._-]{1,100}$`). Sources are `files://<source>/<path>`.
- `files`: at most 500 per request and 20,000,000 characters of content. Paths use `/` or `\`; they are stored NFC-normalised
  with `/`. Text is hashed with LF line endings, so CRLF and LF checkouts give one hash.
- `filter` is used when the run starts and kept for the whole run. `include` and `exclude` are gitignore-style globs (a name with
  no `/` matches at any depth, a `/` anchors to the root, `dir/` is a folder, `**` crosses folders). `extensions` defaults to
  `md, markdown, txt, rst`; an empty list allows any. The server also applies the guarded path fragments
  (`HIPPO_EXCLUDE_PATH_PARTS`, and `private-infra`), which no client setting removes.
- The first request of a run omits `session`; the reply carries one. Send it back on each later request. `final: true` ends the
  run: this folder's sources that were not sent are removed, and the session is closed (`"session": null` in the reply).
  A session belongs to the account that started it and expires after an hour.
- A file that is rejected (a secret, a secret-looking name, over 200,000 bytes, an unsafe path) is reported by path and rule,
  never by content, and if it was indexed before it is removed at once.
- `dry_run: true` decides and writes nothing. `content` may be left out in a dry run (send `size`) to preview by path.

Reply: `{"session", "source", "files": [{"path", "outcome": "indexed|skipped|rejected", "reason", "action":
"added|changed|unchanged|removed", "warning"}], "removed": [paths], "totals": {added, changed, unchanged, removed, skipped,
rejected, files}}`. A dry run returns `{"dry_run": true, "counts", "files"}`.

| Status | When |
|---|---|
| 403 / 404 | No write access / no such readable collection (the same 404 for a hidden one) |
| 404 | The session is unknown, expired, or another account's |
| 413 | The batch is over 20,000,000 characters, or the run over 20,000 files |
| 422 | A rule is over 300 characters, a file has no `content` outside a dry run, or the source or slug is invalid |
| 429 | Too many uploads in progress |

When a saved source (below) has this `source` name, its rules replace the request's `filter`, and the request's `collection` must
be the source's collection or one of its folder collections (`409` otherwise). A run that ends is recorded on the source and as a
finished job of kind `sync-files` (`job_id` in the reply, with the counts in its `result`). If a batch fails (the embedder is
down, say), the open run is dropped, nothing is pruned, the index keeps what it had, and the source shows `last_status: "error"`
with the cause named by type.

## `DELETE /v1/ingest/files/{session}` — `204`

Cancels a run. Only the account that started it can (`404` for anyone else). Pruning only happens on the final batch, so the index
keeps what it had. `?reason=` (up to 100 characters) is shown on a saved source as the last error.

## Saved sources

A saved source keeps a folder's rules and target so a later drop uses the same ones.

```json
{"name": "vault", "collection": "notes",
 "settings": {"folders": ["Work", "Projects"], "exclude": ["Work/Clients/"], "extensions": ["md"]},
 "folder_collections": {"Work": "notes-work"}}
```

- `kind` is `upload` (the only one with a reader today); `mount`, `s3`, `drive` and `git` are reserved in the schema.
- `settings` are the `filter` fields plus `folders`: the top-level folders to index (`"."` is the files at the top). Empty means all.
  Unticking a folder and running again removes its files.
- `folder_collections` sends a folder to a collection of its own. The client then runs once per collection with the same `source`;
  the server only lets each folder's files into its own collection. The caller needs write access to every collection named.
- Listing and reading need read access to the source's collection; a source you cannot read is a `404`. Creating, editing,
  syncing and deleting need write access (`403` if you can read but not write, `404` if the collection is hidden). Deleting a source
  leaves its indexed files searchable unless `?purge=true`.
- A reply is `{name, kind, collection, settings, folder_collections, created_by, created_at, updated_at, last_run_at, last_status,
  last_summary}`.

## `POST /v1/ingest/repo` — `202`

```json
{"collection": "code", "repo": "my-project", "pack": "<output of repomix --style xml>"}
```

`repo` matches `^[A-Za-z0-9._-]{1,100}$`; `pack` is 1 to 20,000,000 characters. The pack is parsed up front (`422` if it is not a Repomix
xml or markdown pack), then a job runs the secret pre-flight and indexes the clean files. Returns `{"job_id": "...", "status":
"queued"}`. The job's `result` is `{"files": n, "added": n, "changed": n, "unchanged": n, "removed": n, "rejected": [{"path", "reasons"}]}`.
Details: [../User_Guide/ingesting.md](../User_Guide/ingesting.md#indexing-a-repository).

## `POST /v1/crawl` — `202`

```json
{"url": "https://docs.example.com/", "collection": "docs", "depth": 2, "match_pattern": "/guide/", "engine": "static"}
```

`url` 1 to 1024 characters; `depth` 1 to 5 (default 1); `match_pattern` an optional regex; `engine` `static` (default) or
`browser`. Returns `{"job_id", "status"}`. The job's `result` is `{"pages", "added", "changed", "unchanged"}`.

| Status | When |
|---|---|
| 501 `crawler not available` | The application was created without a crawler |
| 403 `this source is excluded from ingestion` | The URL contains a guarded fragment |

Address problems (non-public hosts, unsupported schemes) are reported **inside the job**, not as an HTTP error, because the
check runs when the crawl starts and again on every redirect.

## `POST /v1/sync/docs` — `202`

No body. Starts a full resync of the configured docs repository into the configured collection. Needs write access to that
collection. `501 docs sync not configured` if the feature is off. The result is `{"added", "changed", "unchanged", "removed",
"rejected": [...], "skipped"}`.

## `POST /v1/webhooks/forgejo` — `202`

A Forgejo push webhook. Authenticated by the HMAC-SHA256 of the raw body, hex-encoded (optionally prefixed `sha256=`), in
`X-Forgejo-Signature` or `X-Gitea-Signature`; no token is used. The network gate still applies.

| Outcome | Response |
|---|---|
| Not configured | `501 docs sync not configured` |
| Bad or missing signature | `401 bad signature` |
| Body is not JSON | `422 payload is not JSON` |
| A different repository or branch | `202 {"ignored": "<reason>"}` (the route answers 202 for every accepted request) |
| Target collection missing | `404 collection '<name>' not found` |
| Accepted | `202 {"job_id", "status", "mode"}` where `mode` is `incremental` or `full` |

The job is owned by `system` and visible to everyone.

## `GET /v1/jobs/{job_id}`

```json
{"id": "9f3c...", "kind": "crawl", "status": "done", "result": {"pages": 12, "added": 12, "changed": 0, "unchanged": 0},
 "error": null, "event_count": 18, "created_at": 1761150000.12}
```

`kind` is `crawl`, `ingest-repo`, `docs-sync` or `docs-resync`. `status` is `queued`, `running`, `done` or `error` (then `error` holds
`<ExceptionType>: <message>`). `404 job not found` for an unknown id **or a job you may not see**.

## `GET /v1/jobs/{job_id}/events`

`text/event-stream`. Each log line arrives as `data: <text>`; when the job has finished, the stream ends with

```
event: end
data: done
```

(`done` or `error`). The log is replayed from the start for every new connection, so a late client sees everything. Use
`curl -N` or a fetch reader; the browser `EventSource` cannot send an authorization header.

## `GET /v1/clients`

Admin. `{"clients": [{"name", "admin", "read": [], "write": [], "note", "created_at", "last_used_at", "revoked"}]}`, ordered by
name, revoked accounts included. Tokens and hashes are never returned.

## `POST /v1/clients` — `201`

```json
{"name": "assistant", "admin": false, "read": ["team-notes"], "write": [], "note": "read-only assistant"}
```

`name` matches `^[a-z0-9][a-z0-9_-]{0,63}$`; grants are slugs or `*` patterns (`^[a-z0-9*-]{1,64}$`, a bare `*` refused); `note` at
most 500 characters. Response: `{"name", "token", "admin", "read", "write"}` where `token` (`hip_` plus 43 characters) is
**returned only here**. Errors: `422 invalid client name` / `invalid grant`, `409 client '<name>' already exists` (revoked names count as taken).

## `PATCH /v1/clients/{name}`

Any of `admin`, `read`, `write`, `note`. Omitted fields are unchanged; the grant lists are replaced as a whole. Works only on
active accounts. `409 cannot remove the last admin` when `admin: false` would leave no admin; `404` if not found or revoked.
Returns the updated row.

## `POST /v1/clients/{name}/rotate`

Replaces the account's token. Returns `{"name", "token"}`. The previous token stops working at once; the name, grants and
admin flag are unchanged, and "last used" is reset. `404` for an unknown or revoked account.

## `DELETE /v1/clients/{name}` — `204`

Revokes the account for good (its name stays reserved). `409 cannot revoke the last admin`, `404` if not found or already revoked.
