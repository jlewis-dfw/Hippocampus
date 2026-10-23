# Security model

What Hippocampus protects, from whom, and how. The access rules themselves are in [../Api/authorization.md](../Api/authorization.md);
this page is the whole picture in one place.

## What is protected

| Asset | Why it matters |
|---|---|
| The documents in restricted collections | They may be private notes or internal material |
| Account tokens | They grant access, and an admin token grants everything |
| The host and network around the service | The crawler and the docs sync make outbound requests on request |
| The integrity of the index | Wrong or poisoned content misleads whoever searches it |

## Trust boundaries

```
 outside world ──(crawled pages, pushed files, repository packs, webhooks)──▶ service ──▶ database
 callers ────────(REST requests with optional tokens)────────────────────────▶ service
 an assistant ◀──(search results = untrusted text)───────────────────────────  service
```

Everything that arrives from outside, **including the text of documents**, is untrusted. Callers are trusted only as far as their
token grants.

## Threats and controls

| Threat | Control | Where |
|---|---|---|
| Reading restricted data without permission | Visibility per collection; the readable set is computed per request; a hidden collection answers 404 like a missing one; the vector query filters before its limit; the store has no "all collections" mode | [authorization.md](../Api/authorization.md), [search.md](search.md) |
| Guessing or stealing a token | 256-bit random tokens, only a hash stored, shown once, rotation and revocation take effect at once, no tokens in responses or logs | `auth.py`, `api.py` |
| A leaked token | Per-tool accounts with minimal grants, read-only accounts, rotate or revoke | [../User_Guide/collections-and-access.md](../User_Guide/collections-and-access.md) |
| Using the crawler to reach internal services (server-side request forgery) | Every URL, redirect hop and browser sub-request must resolve only to public addresses | [crawler.md](crawler.md) |
| Cross-site scripting through crawled or pushed content | The dashboard writes all API data with `textContent`, never HTML; a strict Content-Security-Policy; `X-Content-Type-Options: nosniff` | [../Dashboard/README.md](../Dashboard/README.md) |
| Secrets ending up in the index | A pre-flight rejects whole files with secret-shaped content or credential-like names before anything is embedded; reports never include the matched text | [ingestion-pipeline.md](ingestion-pipeline.md) |
| Documents that must never be shared being indexed | Guarded path fragments are refused on every route, in the crawler, the repository ingester, the docs sync and the MCP indexing tool | [ingestion-pipeline.md](ingestion-pipeline.md#where-the-guarded-fragments-live) |
| Private-by-name material landing in a shared collection | Reserved slug prefixes are always restricted; reserved URI schemes only go into restricted collections | [authorization.md](../Api/authorization.md#reserved-private-names) |
| Forged webhooks | HMAC-SHA256 of the raw body compared in constant time; a wrong signature is rejected before the body is parsed | `docs_sync.py` |
| Unauthenticated writes | `HIPPO_REQUIRE_AUTH` (on by default in the local stack) | [../User_Guide/configuration.md](../User_Guide/configuration.md) |
| Exposure to the network | The published port binds to `127.0.0.1` by default; a network gate by client address; no database port is published | `compose.yaml`, `api.py` |
| Job logs revealing paths | Job status and logs are visible only to the owner and admins | [jobs.md](jobs.md) |
| Overly large or hostile inputs | Request field limits; a 20 MB pack cap, 5,000 files and 200,000 bytes per file; a crawl page cap, per-page byte cap, redirect cap and politeness delay | `api.py`, `repo_ingest.py`, `crawler.py` |
| An assistant obeying text in a search result (prompt injection) | Results are data; the MCP server's instructions say so; read-only MCP mode; no tool executes anything found in a document | [../Mcp/README.md](../Mcp/README.md) |
| Tampering by an attacker with database access | Out of scope: the database holds everything and the access rules live in the application | below |

## Defaults that matter

- The service listens on `127.0.0.1` only.
- Writes need a token (`HIPPO_REQUIRE_AUTH=true`).
- There is no database port on the host; only the API container reaches the database.
- The API container runs as a non-root user (uid 10001).

## Known limits

- **Direct database access bypasses everything.** Anyone who can connect to PostgreSQL, or who holds a dump, reads restricted collections
  and the account hashes. Protect the database volume, its backups and its password.
- **No rate limiting and no lockout** for bad tokens; the token space makes guessing infeasible, but a flood of requests is not stopped.
  Use a reverse proxy for rate limits.
- **No token expiry and no audit log.** Tokens are valid until rotated or revoked, and the service does not record who searched what.
- **The network gate is not authentication.** Behind a proxy or a port mapping every client looks the same.
- **DNS rebinding** is not defended against in the crawler (the name is resolved separately from the connection), and WebSockets opened by
  a rendered page are not checked.
- **The secret scanner is small** and regex-based; it will miss secrets that do not match its patterns. A crawl or a pushed file is stored
  exactly as sent, without a scan.
- **TLS is not provided.** Terminate it in a reverse proxy.
- **Dependencies and images are not scanned or pinned by digest** by this repository; base images are referenced by tag.

## Reporting a problem

Report vulnerabilities privately, as described in the repository's security policy, not in a public issue.

## Tests

`tests/unit/test_access.py` is the access-control suite (visibility, 404 for hidden collections, write rules, token handling, last-admin
protection, rotation, job ownership, hostile result text). `tests/unit/test_crawler.py` covers the address guard. `tests/unit/test_repo_ingest.py`
and `tests/unit/test_docs_sync.py` cover the secret pre-flight and the path guards. `scripts/smoke_live.py` checks the isolation rules on a
running instance.
