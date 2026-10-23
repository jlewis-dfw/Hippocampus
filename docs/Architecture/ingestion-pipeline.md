# The ingestion pipeline

Every way of indexing content (a crawl, a pushed file, a repository pack, the docs sync) ends in one function, `sync_collection`
in `diff.py`. This page describes it, and the two parsers that feed it besides the crawler: the repository ingester
(`repo_ingest.py`) and the docs sync (`docs_sync.py`). The crawler has its own page: [crawler.md](crawler.md).

## `sync_collection`

```python
sync_collection(collection, documents, store, embedder, *, prune=True) -> SyncResult
```

`documents` maps a **source URI** to a `Document(title, text)`. For each entry:

1. `digest = sha256(text)` (UTF-8). If the store already holds that URI with the same digest, the URI goes into `unchanged` and nothing
   else happens.
2. Otherwise the text is cut by `chunk_markdown` and **all of its chunks are embedded in one call**. The vector count must equal the
   chunk count, or `ValueError` is raised.
3. Only now is the store touched: `store.replace_source(collection, uri, title, digest, chunks)`. The URI goes into `added` if the
   store did not know it, or `changed` if it did.

With `prune=True`, sources that are in the store but **not** in `documents` are deleted (`deleted` list). The result is a `SyncResult`
with the four lists `added`, `changed`, `unchanged`, `deleted`.

**Order matters:** embedding happens before any write, so an embedding failure (model error, out of memory) leaves the previous
chunks of that source untouched. A failure in the middle of a batch leaves the earlier sources updated and the later ones as they
were; running the same sync again finishes the work, because the finished ones now count as unchanged.

### Who prunes

| Caller | `prune` | Meaning |
|---|---|---|
| Crawl | `False` | A page that disappears from the site stays in the collection |
| `POST /v1/ingest/file` | `False` | One document at a time; never removes anything |
| Repository pack | `False` here, then its own cleanup | Removes sources of that repository label that are not in the pack |
| Docs sync | `False` here, then its own cleanup | Removes sources of that repository that left the branch or became ineligible |

So `prune=True` is a capability of the function that production code does not use directly; each caller prunes only what it owns,
by URI prefix, so one source type can never delete another's content.

### The store contract

`diff.Store` is a `Protocol`:

| Method | Contract |
|---|---|
| `source_hashes(collection)` | `{uri: sha256}` for every source in the collection |
| `replace_source(collection, uri, title, sha256, chunks)` | **Atomically** upsert the source and replace all its chunks |
| `delete_source(collection, uri)` | Delete the source; its chunks go with it |

`PgStore.replace_source` runs in one transaction: it inserts or updates the `sources` row (title, hash, chunk count, status
`indexed`, clears any error, sets `last_indexed_at`), deletes the source's old chunks, and inserts the new ones with their vectors.
The unique constraint `(collection_id, uri)` makes the URI the identity, so two concurrent indexes of the same URI end with the last
writer's version, never a mixture. The tests run the same logic against an in-memory store.

### Source URIs

A URI is any string up to 1024 characters. Conventions: crawled pages use the normalised page address; pushed files use whatever the
caller sends; repository sources use `repo://<repo>/<path>`. The URI is shown in results and decides what "the same source" means.

## The repository ingester (`repo_ingest.py`)

Input: the text of `repomix --style xml` (`<file path="...">...</file>` blocks) or `--style markdown` (`## File: <path>` followed by a
fenced block). `parse_pack` extracts `(path, content)` pairs and raises `ValueError` for anything else.

For each file, `scan_pack` decides:

| Check | Result |
|---|---|
| Path contains a guarded fragment (`FORBIDDEN_PATH_PARTS`) | rejected: `excluded-source` |
| Path looks like a credential store (`SECRET_PATH_RE`) | rejected: `secret-file-name` |
| Content over `MAX_FILE_BYTES` (200,000) | rejected: `too-large` |
| Content matches a secret rule | rejected, with each rule's name |
| Empty after trimming | silently skipped |
| Otherwise | becomes `repo://<repo>/<path>` |

A pack may hold at most `MAX_FILES` (5,000) files, else `ValueError`.

**Secret rules** (`SECRET_RULES`, regular expressions): AWS access key ids, AWS secret keys, PEM private-key headers, `Bearer` tokens,
JWTs, GitHub tokens, Slack tokens, Azure storage keys, and quoted credential assignments (`password`, `passwd`, `secret`, `token`,
`api_key`, `client_secret` set to a quoted literal of eight or more characters). **Credential-store names** matched by `SECRET_PATH_RE`:
`.env`, `.env.*`, `.vault_pass`, `vault.yml`, `secrets.auto.tfvars`, `id_rsa`/`dsa`/`ecdsa`/`ed25519`, `*.pem`, `*.key`, `*.p12`, `*.pfx`,
`*.tfstate`, `credentials`, `credentials.json`.

A rejected file is **never partly redacted**: it is dropped whole, so no half-sanitised text is embedded. Reports contain the path and
rule names only, never the matched text. The rule set is deliberately small and weaker than a maintained scanner (gitleaks,
detect-secrets); it is a safety net.

**Markdown conversion** (`_to_markdown`): `.md` and `.markdown` files are used as they are. Any other file becomes
`# <path>` followed by the content in a fenced block (the fence is longer than any backtick run in the content, with a language tag
from the extension when known), so code is chunked as code and the file path is the page heading.

`ingest_pack` then calls `sync_collection(..., prune=False)` with the clean files, and removes the collection's sources that start with
`repo://<repo>/` and are not in this pack (this includes files that now carry a secret). It returns counts and the rejected list.

## The docs sync (`docs_sync.py`)

Keeps one collection in step with one branch of a Forgejo repository. It is built from:

- `DocsSyncConfig`: collection, owner, repo, branch, webhook secret and a `DocsSource` (an object that can list files and read a file).
  `ForgejoSource` is the real one: a read-only Forgejo API client that lists the tree
  (`/repos/{owner}/{repo}/git/trees/{ref}?recursive=true`, following pages while `truncated`) and fetches raw files
  (`/repos/{owner}/{repo}/raw/{path}?ref=...`). Redirects are not followed.
- `verify_signature(secret, body, headers)`: HMAC-SHA256 of the raw body compared in constant time with the hex value in
  `X-Forgejo-Signature` or `X-Gitea-Signature` (optionally prefixed `sha256=`).
- `plan_push(cfg, payload)`: returns a `PushPlan`.

### Planning a push

| Payload | Plan |
|---|---|
| `repository.full_name` is not the configured `owner/repo` | ignored |
| `ref` is not `refs/heads/<branch>` | ignored |
| No commits, or 20 or more (Forgejo truncates the list of a big push) | **full resync** |
| Otherwise | for each commit oldest first, paths in `added` or `modified` go to `upserts` and paths in `removed` to `removes`; the **last action on a path wins** |

### Applying changes

`apply_changes(cfg, store, embedder, ref, upserts, removes)`:

1. `is_indexable(path)`: a path must end in `.md`, must not be absolute or contain `..`, and must not contain a guarded fragment. Paths
   that fail are counted as `skipped` and **never fetched**.
2. Each remaining file is read at the pushed commit. A missing file is treated as removed. A file over 200,000 bytes is rejected as
   `too-large` and removed from the collection. A file matching a secret rule is rejected and **removed from the collection** (so a
   file that newly acquires a secret does not stay indexed).
3. The clean files go through `sync_collection(prune=False)`; then every URI to remove that is present is deleted.

The result is `{added, changed, unchanged, removed, rejected: [{path, reasons}], skipped}`.

### Full resync

`full_resync`: list every file on the branch, keep the eligible ones, find this repository's sources that are not eligible any more
(by URI prefix `repo://<repo>/`), apply all eligible files, and delete the stale URIs. Used for the first fill, for pushes of 20 or more
commits, and for the manual `POST /v1/sync/docs`.

## Where the guarded fragments live

A list of path fragments is checked in several places so that documents which must never enter a shared store are refused however they arrive:
`api.py` (`FORBIDDEN_URI_PARTS`, for file and crawl requests and for repository labels), `crawler.py` (`FORBIDDEN_URL_PARTS`, for
every URL), `repo_ingest.py` and `docs_sync.py` (`FORBIDDEN_PATH_PARTS`), and `mcp_server.py` (`FORBIDDEN`, for local file paths). They
are kept in sync by hand; the tests assert each one.

## Tests

`tests/unit/test_diff.py` (added, changed, unchanged, prune, embed-before-write), `tests/unit/test_repo_ingest.py` (formats, rejections, the endpoint),
`tests/unit/test_docs_sync.py` (signature, plans, apply, full resync, the guards on both routes).
