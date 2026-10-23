# Ingesting documents

Four ways to get content into a collection. All of them end in the same pipeline (cut into chunks at headings, embed each
chunk, store it; see [../Architecture/ingestion-pipeline.md](../Architecture/ingestion-pipeline.md)), and all of them
need **write access** to the target collection ([collections-and-access.md](collections-and-access.md)).

| Way | Best for | Returns |
|---|---|---|
| [Crawl a site](#crawling-a-site) | Public documentation websites | A background job with a live log |
| [Push Markdown](#pushing-markdown) | Notes, decisions, runbooks you already have as text | The result immediately |
| [Index a repository](#indexing-a-repository) | Code and docs from a Git repository, via a Repomix pack | A background job |
| [Sync a docs repository](#keeping-a-docs-repository-in-sync) | A Forgejo repository of Markdown that changes often | A background job per push |

**The collection must exist first** (except in the dashboard's crawl form, which can create it). Create one with
`POST /v1/collections` or the dashboard.

## How re-indexing works

Every source has a **content hash** (SHA-256 of its text). When you index a source again:

- the same hash: the source is **skipped** and not re-embedded (so re-running a crawl or a sync is cheap);
- a different hash: the new text is chunked and embedded **first**, and only then replaces the old chunks, in one
  database transaction. If embedding fails, the old version stays, so a failure never empties a source.

Nothing is removed unless a pruning step is described below. The result of an indexing call lists the sources as `added`,
`changed` or `unchanged`.

## Crawling a site

> **Crawl responsibly.** You are copying other people's content. Read [legal-and-ethics.md](legal-and-ethics.md) first: crawl your own sites or sites that
> allow it, respect `robots.txt` and the terms of use, never get around a login or paywall, keep the page cap and depth low, and be careful with personal data.
> The crawler honours `robots.txt` but **not** `Crawl-delay`, `noindex` or `X-Robots-Tag`; that guide lists exactly what it does and does not do.

Dashboard: **Crawl a site** ([dashboard.md](dashboard.md#crawl-a-site)). API:

```bash
curl -s -X POST http://127.0.0.1:8090/v1/crawl \
  -H "X-Hippo-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"url": "https://docs.example.com/", "collection": "docs", "depth": 2, "match_pattern": "/guide/", "engine": "static"}'
# -> 202 {"job_id": "...", "status": "queued"}
```

| Field | Values |
|---|---|
| `url` | `http` or `https`, up to 1024 characters |
| `collection` | An existing collection slug you can write to |
| `depth` | 1 to 5 (default 1). 1 is the start page only, 2 adds the pages it links to, and so on |
| `match_pattern` | Optional regular expression searched in each link's address; non-matching links are not followed |
| `engine` | `static` (default) or `browser` |

What the crawler does:

- It walks **breadth first** and stays on the **start URL's host**. It never follows a link to another host.
- It obeys **`robots.txt`** for its own user agent. A missing `robots.txt` means allowed.
- It waits **0.5 seconds** between requests and stops after **200 pages** (`HIPPO_CRAWL_DELAY`, `HIPPO_CRAWL_MAX_PAGES`).
- It fetches only **HTML** (`text/html`, `application/xhtml+xml`), reads at most **2 MB** per page, and follows at most
  **5 redirects**. Links to images, archives, media, fonts, stylesheets, scripts, JSON and XML files are skipped.
- It converts HTML to Markdown, dropping `script`, `style`, `nav`, `footer`, `aside`, `form`, `svg`, `template` and `button`
  elements. Headings, lists, code blocks, links, bold and italic survive. A page that comes out empty is skipped.
- Each page's source URI is its normalised address (lowercase scheme and host, no `#fragment`).

**What it refuses.** The crawler refuses any address that resolves to a private, loopback, link-local, carrier-grade NAT or
otherwise non-public IP, including after redirects. This stops a crawl request from being aimed at internal services.
Refused addresses appear in the log as `refused <url>: ...`. Known limit: the name is resolved separately from the connection,
so DNS rebinding is not defended against.

**The `browser` engine** renders pages in headless Chromium for sites that build their content with JavaScript. It needs an
image built with `HIPPO_INSTALL_BROWSER=1` (about 400 MB larger); otherwise the job fails with `engine 'browser' needs the 'browser'
extra (playwright) and Chromium`. Images, media, fonts and stylesheets are not loaded, service workers are blocked, and every
sub-request the page makes is checked against the same public-address rule. WebSockets are not checked.

Re-crawling **does not remove** pages that have disappeared from the site; see [Removing things](#removing-things).

Watch a job: [Jobs](#jobs).

## Pushing Markdown

For text you already have:

```bash
curl -s -X POST http://127.0.0.1:8090/v1/ingest/file \
  -H "X-Hippo-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"collection": "team-notes", "uri": "notes/backups.md", "title": "Backups", "text": "# Backups\n\nWe back up nightly.\n"}'
# -> {"added": ["notes/backups.md"], "changed": [], "unchanged": []}
```

- `uri` identifies the source (1 to 1024 characters). Use anything stable: a path, a URL, `notes/2026-10-22.md`. Pushing the
  same `uri` again updates that source.
- `title` is optional (up to 512 characters) and is shown in search results.
- `text` is Markdown. Headings (`#`, `##`, `###`) become the heading path of each chunk, so structure your notes with headings.
- The call is synchronous: it returns after the text is embedded.

There is also the `hippocampus_index_doc` MCP tool, which reads a local file and pushes it ([../Mcp/README.md](../Mcp/README.md)).

## Indexing a repository

Hippocampus does not clone repositories. You produce a text pack of the repository with
[Repomix](https://github.com/yamadashy/repomix) and send it:

```bash
npx repomix --style xml -o pack.xml            # or --style markdown
python3 - <<'PY' > body.json
import json
print(json.dumps({"collection": "code", "repo": "my-project", "pack": open("pack.xml").read()}))
PY
curl -s -X POST http://127.0.0.1:8090/v1/ingest/repo \
  -H "X-Hippo-Token: $TOKEN" -H 'Content-Type: application/json' --data @body.json
# -> 202 {"job_id": "...", "status": "queued"}
```

- `repo` is a label (letters, digits, `.`, `_`, `-`, up to 100 characters) used in each source's URI:
  `repo://<repo>/<path>`. A file that is not Markdown is indexed as a Markdown page: a `# <path>` heading and the content in a
  fenced code block.
- Limits: the pack may be at most **20,000,000 characters** and **5,000 files**; a single file at most **200,000 bytes**.
- **Secrets are filtered before anything is embedded.** Every file passes a pattern check, and a file that matches is
  **rejected whole** (never partly redacted and embedded). A file is rejected when it contains an AWS key, a private-key
  block, a bearer token, a JWT, a GitHub or Slack token, an Azure storage key or a quoted credential assignment
  (`password = "..."`), or when its **name** looks like a credential store (`.env*`, `vault.yml`, `*.pem`, `*.key`,
  `*.tfstate`, `id_rsa`, `credentials`). It is also rejected if it is over the size limit. The job result lists each rejected
  path with the rule names (`reasons`), never the matched text.
- The pattern set is deliberately small. It is a safety net, not a substitute for a maintained scanner such as gitleaks:
  scan the repository yourself if it matters.
- After a re-index, files of that `repo` label that are no longer in the pack, **including files that now carry a secret**, are
  removed from the collection. Other sources in the collection are untouched.

## Keeping a docs repository in sync

An optional feature that keeps a collection in step with a **Forgejo** repository of Markdown files. It is off unless you
configure it.

**Configuration** (in `hippocampus.env` or the service environment; all three of the first group are needed, otherwise the
routes answer `501 docs sync not configured`):

| Variable | Meaning |
|---|---|
| `HIPPO_FORGEJO_URL` | Base URL of the Forgejo server |
| `HIPPO_FORGEJO_TOKEN` | A token with **read** access to that one repository |
| `HIPPO_WEBHOOK_SECRET` | The shared secret of the push webhook |
| `HIPPO_DOCS_OWNER`, `HIPPO_DOCS_REPO` | Owner and name of the repository to index (set them explicitly) |
| `HIPPO_DOCS_COLLECTION` | The collection to fill. It **must already exist** |
| `HIPPO_DOCS_BRANCH` | The branch to follow (default `main`) |

**Set up the webhook** in the repository's settings: target `http://<hippocampus>/v1/webhooks/forgejo`, method POST, content type
JSON, the same secret, trigger on push events. The webhook sender must be inside the allowed client networks
(`HIPPO_ALLOWED_CIDRS`).

**What happens on a push.** The request is authenticated by an HMAC-SHA256 signature of the body (header `X-Forgejo-Signature` or
`X-Gitea-Signature`); a wrong signature answers `401`. Pushes to other repositories or branches are ignored (`{"ignored": ...}`).
For a normal push only the files the push **added, changed or removed** are fetched and re-indexed; the content-hash check
skips anything unchanged. A push of 20 or more commits, or one with no commit list, triggers a full resync instead.

**Rules.** Only `.md` files are indexed. A path containing a guarded fragment, an absolute path, or one with `..` is skipped
before any file is fetched (`FORBIDDEN_PATH_PARTS` in `docs_sync.py`; it protects documents that must never enter a broadly
readable store). Files over 200,000 bytes, or matching the secret patterns above, are rejected and removed from the collection.
Sources are `repo://<repo>/<path>`.

**Full resync**: `POST /v1/sync/docs` (the dashboard's **Resync docs repo** button) lists every eligible file on the branch,
indexes them, and removes sources of that repository that are no longer eligible. Use it for the first fill and to repair
drift. It needs write access to the collection.

The sync jobs started by a webhook are visible to everyone; a manual resync belongs to the account that ran it.

## Removing things

There is **no API to delete a single source**. What exists:

- **Delete a collection**: `DELETE /v1/collections/{slug}` (dashboard **Delete**) removes it and every source and chunk in it.
- **Repository packs and the docs sync prune themselves**: sources that left the pack or the branch are removed on the next run.
- **Crawls do not prune.** A page that vanished from the site stays in the collection. To start clean, delete the collection and
  crawl again.
- **Push Markdown does not prune.** To drop one pushed source, delete the collection and re-push what you want to keep, or
  remove the row directly in the database (see [../Database/README.md](../Database/README.md)).

## Jobs

Crawls, repository imports and syncs run in the background and return a `job_id`.

```bash
curl -s -H "X-Hippo-Token: $TOKEN" http://127.0.0.1:8090/v1/jobs/<job_id>
# {"id": "...", "kind": "crawl", "status": "done", "result": {"pages": 12, "added": 12, ...}, "error": null, ...}

curl -N -H "X-Hippo-Token: $TOKEN" http://127.0.0.1:8090/v1/jobs/<job_id>/events    # live log (server-sent events)
```

Statuses are `queued`, `running`, `done` and `error` (the error text is in `error`). **Jobs live in memory**: they are lost
when the service restarts, and about the most recent 200 are kept (finished jobs are dropped oldest first). A job's log and result name the files it touched, so only the
account that started it, and admins, can read them. The dashboard shows the log for you.
