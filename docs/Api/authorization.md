# Authorization

How the API decides who may do what. Two layers apply to a request: the **network gate** (by client address) and the
**principal** (who the caller is, from an optional bearer token). Source: `src/hippocampus/api.py` and `src/hippocampus/auth.py`.

## The network gate

A middleware checks every request whose path starts with `/v1/`. The client address (as the service sees it) must be inside
one of the CIDR ranges in `HIPPO_ALLOWED_CIDRS`; otherwise the response is `403 {"detail": "client network not allowed"}`. An
unparseable address is refused. Paths outside `/v1/` (the dashboard files) are not gated.

- Built-in default: `10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, ::1/128`. The local stack sets
  `127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16`, because a container port mapping shows the container network's address.
- This is **defence in depth**. Behind a reverse proxy or a port mapping every client looks the same, so the gate cannot tell
  clients apart. The real boundaries are the published address (`HIPPO_BIND`) and the tokens.
- The webhook route is gated like the rest; its sender must be inside the allowed networks.

## Principals

Every request resolves to a `Principal` (a frozen dataclass in `auth.py`):

| Field | Meaning |
|---|---|
| `name` | `anonymous`, or the account name |
| `admin` | May do everything |
| `read` | Grants (slugs or `*` patterns) allowing read |
| `write` | Grants allowing write; **write implies read** |

There are three kinds in practice:

- **anonymous**: no credential. `name == "anonymous"`, no grants.
- **client**: an account found by its token hash.
- **admin**: a client with `admin = true`.

(The webhook is not a principal: its HMAC signature is its credential, and its jobs are owned by the pseudo-owner `system`.)

### How a request is authenticated

`authenticate(authorization, store, hippo_token)` in `auth.py`:

1. The credential is `X-Hippo-Token` if present, otherwise the value of `Authorization: Bearer <value>`. (`X-Hippo-Token` wins because a
   reverse proxy in front of the dashboard may put its own `Authorization` on every request.)
2. No credential, or one that does **not start with `hip_`** (a proxy's JWT, a Basic header): the caller is **anonymous**. Such a
   value carries no privilege, so ignoring it is safe.
3. A `hip_` token: its SHA-256 is looked up among the **active** (not revoked) accounts. Found: the caller is that account. Not
   found: `401 unknown or revoked token`. A broken credential is never silently downgraded to anonymous.

Without a credential header the route does not touch the database to authenticate.

### Tokens

- Format: `hip_` plus `secrets.token_urlsafe(32)`, 43 URL-safe characters (256 bits of randomness).
- Stored: only `sha256(token)` as a hex string, in `hippocampus.api_clients.token_sha256` (unique). A token is shown once, when
  created or rotated.
- Lookup is by hash equality in the database, so no secret comparison happens in application code. Because tokens are random,
  guessing is infeasible; there is nevertheless **no rate limiting** on bad tokens.
- `last_used_at` is refreshed at most once a minute per account (a single guarded `UPDATE`), to avoid a write on every request.
- Tokens do not expire. Rotate or revoke to retire one. Rotation replaces the hash in place and resets `last_used_at`.

### Grants

A grant is a slug or a pattern using `*`. It must match `^[a-z0-9*-]{1,64}$`, and a grant made only of `*` is refused (it would
cover restricted collections; use the admin flag). Matching uses `fnmatch.fnmatchcase` against the **whole slug**:

| Grant | Slug | Match |
|---|---|---|
| `notes-*` | `notes-2026` | yes |
| `notes-*` | `notes` | no |
| `notes-*` | `my-notes-1` | no |
| `notes` | `notes` | yes |
| `notes` | `notes2` | no |

Account names match `^[a-z0-9][a-z0-9_-]{0,63}$`.

## The rules

`Principal.can_read(slug, visibility)` is true when the caller is an admin, **or** the collection is `shared`, **or** a read or write
grant matches the slug.

`Principal.can_write(slug, visibility, require_auth)` is true when the caller is an admin, **or** a write grant matches, **or** the
collection is `shared` and `require_auth` is off.

The route helpers wrap these:

- `need_read`: the collection must exist and be readable, else `404 collection '<slug>' not found`. **A hidden collection and a missing
  one give the same answer.**
- `need_write`: first `need_read` (404), then `can_write`, else `403 writing to collection '<slug>' needs a token with a write grant`.
- `need_admin`: `403 admin token required`.

| Operation | Check |
|---|---|
| Search with named collections | `need_read` on each, then search exactly those |
| Search without names | The readable set (`readable()`): all collections for which `can_read` is true |
| List collections, status counts | The readable set |
| List sources | `need_read` |
| Create collection, shared | `can_write(slug, shared)`: admin, a matching write grant, or anyone when `require_auth` is off; else 403 |
| Create collection, restricted | `need_admin` |
| Update collection | `need_read` (404), then `need_admin` (403) |
| Delete collection | restricted: `need_read` then `need_admin`; shared: `need_write` |
| Ingest file, crawl, ingest repo, resync docs | `need_write` |
| Job status and log | admin, or `job.owner == principal.name`, or `job.owner == "system"` |
| Clients (list, create, update, rotate, revoke) | `need_admin` |
| Webhook | HMAC signature only |

Two rules protect the admin account set: `PATCH` with `admin: false` and `DELETE` on the **last active admin** answer `409`.
(The admin command-line tool talks to the database directly and has no such guard, which is how you recover from a lost admin
token.)

### Why "search without names" is safe

`PgStore.search` takes the **exact list of slugs to search** and has no "all collections" mode: an empty list searches nothing.
The API computes the readable list and passes it, and the `WHERE k.slug = ANY(...)` filter runs **before** the `ORDER BY ... LIMIT`.
So a restricted chunk that would be the best match can neither appear in the results nor push a visible result out of
`top_k`. A test inserts restricted chunks that out-rank every visible one to prove it.

### `HIPPO_REQUIRE_AUTH`

Passed to `create_app(require_auth=...)`; it only changes `can_write` for **shared** collections. With it on, anonymous callers can
read shared collections but cannot create, crawl into or index them. It never loosens anything: restricted collections always need
a grant.

## Reserved private names

Two constants in `auth.py` protect material that must never end up in a collection everyone can read:

| Constant | Value | Effect |
|---|---|---|
| `PRIVATE_SLUG_PREFIXES` | `("private",)` | A collection whose slug starts with the prefix is **always restricted**: creating it as shared is `422`, and changing it to shared is `422` |
| `PRIVATE_URI_PREFIXES` | `("private://",)` | A source URI with this scheme can only be indexed into a restricted collection (`403` otherwise) |

An ordinary installation never meets these. They exist for a companion application that keeps private story summaries in
`private-*` collections. To protect another family of names, add its prefix to the tuples (and a test).

Separately, `HIPPO_EXCLUDE_PATH_PARTS` (checked in `routes/ingest.py`, `crawler.py`, `repo_ingest.py`, `docs_sync.py`,
`files_ingest.py` and `mcp_server.py`) is a list of path fragments that are **refused as sources** (`403 this source is excluded from ingestion`): a guard so that
infrastructure documents can never be indexed into a store other tools can read. Change it only together with its tests.

## Data returned and data hidden

- Job logs and results name the paths a job touched, so they follow the owner rule above (`404` for anyone else).
- `GET /v1/clients` never returns tokens or hashes. A token appears in exactly two responses: creation and rotation.
- The dashboard sends the token as `X-Hippo-Token`; it keeps it in the tab's session storage and never in a cookie.
- The database and its backups are **not** protected by these rules: anyone with direct database access reads everything, including
  restricted collections.

## Extending it

The grant model (names, read and write lists, an admin flag, visibility per collection) is independent of how a credential is
verified. To use an external identity provider, replace `authenticate()` so it validates a signed token and returns a `Principal`
built from its claims (for example the allowed collections); no route changes. Keep the rule that a malformed or unverifiable
credential is an error, never anonymous.
