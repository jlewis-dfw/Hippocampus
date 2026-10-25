# Architecture

How Hippocampus is put together and why. This is the map; each topic has its own page.

This is a living document. It describes the code as of migration `0003_sync_sources`; update it alongside the source.

## Contents

- [embedding-and-chunking.md](embedding-and-chunking.md) — the chunker and the embedding model.
- [ingestion-pipeline.md](ingestion-pipeline.md) — the content-hash sync shared by every way of indexing, the repository ingester, the docs sync and folder uploads.
- [crawler.md](crawler.md) — HTML to Markdown, the crawl loop, the two engines and the public-address guard.
- [search.md](search.md) — the search query, scoring and why filtering happens before the limit.
- [jobs.md](jobs.md) — background jobs and the live log.
- [security.md](security.md) — the threat model and every control in one place.
- [design-decisions.md](design-decisions.md) — the unusual choices, why they were made and when to revisit them.

## The system at a glance

```
                          ┌───────────────────────────────────────────────┐
   browser ──────────────▶│  /ui/  dashboard (static files, CSP)          │
   curl / scripts ───────▶│  /v1/* REST API  (FastAPI, uvicorn)           │
   AI assistant ─▶ MCP ──▶│        │             │              │         │
   (stdio server, a       │   access rules   background      embedder     │
    thin REST client)     │   (auth.py)      jobs            (CPU, ONNX)  │
                          └────────┬─────────────┬──────────────┬─────────┘
                                   │             │              │
                            ┌──────▼──────┐  ┌───▼────┐   ┌─────▼──────┐
                            │ PostgreSQL  │  │ crawler│   │ bge-small  │
                            │ + pgvector  │  │ (HTTP, │   │ en-v1.5    │
                            │ collections │  │ Chrom- │   │ 384-dim    │
                            │ sources     │  │ ium)   │   │ (in image) │
                            │ chunks      │  └────────┘   └────────────┘
                            │ api_clients │
                            └─────────────┘
```

## Components

| Component | Where | Role |
|---|---|---|
| **API** | `api/app.py` | REST routes, access rules, background jobs, serves the dashboard |
| **Store** | `store.py` | Every SQL statement; the only code that touches the database |
| **Auth** | `auth.py` | Tokens, grants, the `Principal` |
| **Chunker** | `chunker.py` | Cuts Markdown into heading-aware passages |
| **Embedder** | `embedder.py` | Turns text into 384-number vectors on the CPU |
| **Diff/sync** | `diff.py` | The one indexing step all sources share: skip unchanged, embed, replace |
| **Crawler** | `crawler.py` | Fetches sites (static HTTP or headless Chromium) and converts them to Markdown |
| **Repository ingester** | `repo_ingest.py` | Parses Repomix packs and rejects files that look like secrets |
| **Docs sync** | `docs_sync.py` | Keeps a collection in step with a Forgejo repository via webhook |
| **Folder uploads** | `sync_core.py`, `files_ingest.py` | One set of file rules; batched uploads that build a sync run, with saved sources |
| **Jobs** | `jobs.py` | In-memory registry of background work with a replayable log |
| **Migrations** | `migrate.py`, `db/migrations/` | The schema, applied in order |
| **MCP server** | `mcp_server.py` | A stdio tool server that calls the REST API |
| **Admin tool** | `admin.py` | Manages accounts and visibility straight in the database |
| **Dashboard** | `ui/` | A single static page |

All four ways of getting content in (crawl, push, repository pack, docs sync) build a mapping `uri -> Document(title, text)` and
hand it to the **same** function, `sync_collection`, so they share the chunking, embedding and change detection.

## Data flow

### Indexing a document

```
source (crawl page / pushed text / pack file / repo file)
   └─▶ Document(title, text)
        └─▶ sha256(text)  ──equal to the stored hash?──▶ yes: skip (unchanged)
                 │ no
                 ▼
        chunk_markdown(text)         headings → sections → ~400-token chunks, header_path per chunk
                 ▼
        embedder.embed(chunks)       384-dim vectors, CPU          (before the store is touched)
                 ▼
        store.replace_source(...)    one transaction: upsert the source, delete its old chunks, insert the new ones
```

Embedding happens **before** any database write, so a failure leaves the previous version of a source intact.

### Searching

```
query text ─▶ embedder.embed([query]) ─▶ vector
          ─▶ the caller's readable collections (access rules)
          ─▶ SELECT ... WHERE slug = ANY(readable) ORDER BY embedding <=> vector LIMIT top_k     (cosine distance)
          ─▶ score = 1 - distance, drop below min_score ─▶ results
```

## Design decisions

| Decision | Why |
|---|---|
| **PostgreSQL with pgvector**, not a dedicated vector database | One dependable store for text, metadata, vectors and accounts; transactions make "replace a source" atomic; plain SQL for filtering. The scale (documents, not billions of vectors) does not need more |
| **A fixed embedding model**, `bge-small-en-v1.5` (384 dimensions) | Small enough to run on any CPU, good retrieval quality for its size, and the schema (`vector(384)`) is built around it. It is **not pluggable**: changing it means a new column size and re-embedding everything |
| **CPU only, model baked into the image** | No GPU dependency, no download at run time, the same behaviour everywhere |
| **Python and FastAPI** | The ecosystem for embedding, crawling and PDF/HTML handling is Python; the service is small |
| **Heading-aware chunks with a heading breadcrumb** | A passage is far more useful with its place in the document; results show `Guide > Install > Linux` |
| **Content-hash change detection** | Re-crawling and re-syncing become cheap, idempotent operations |
| **Jobs in memory** | Simple and enough for one process; the data they produce is durable, and a failed job can simply be re-run |
| **Retrieval only, no generation** | The service stays small and predictable; any assistant can use the results |
| **Access rules in the API, not in the database** | Visibility and grants are a few lines in one place, easy to test; the cost is that direct database access bypasses them |
| **One process** | The API, the jobs and the embedder share memory; scaling out would need the job registry and model cache rethought |

## Invariants worth knowing

1. A search never reads a collection the caller is not allowed to read, even by accident: the store has no "all collections" mode.
2. Indexing never loses the old version of a source if embedding fails.
3. A secret-shaped file is **rejected whole**, never redacted and embedded.
4. Content from outside (crawled pages, files) is data: it is stored and shown as text, never interpreted.
5. Every URL the crawler touches, including every redirect and every browser sub-request, passes the public-address check.
6. Migrations are append-only and additive.

## Scaling and limits

The design targets one person or a small group: thousands to tens of thousands of documents. Indexing speed is bound by the CPU,
searching by one embedding of the query plus a vector search. Nothing is cached between requests except the loaded model. See
[../User_Guide/operations.md](../User_Guide/operations.md#resource-use) for sizing.
