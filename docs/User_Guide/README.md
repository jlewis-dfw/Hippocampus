# Hippocampus user guide

Hippocampus is a small self-hosted memory service. You give it documents (web pages you crawl, Markdown you push,
whole repositories) and it answers **semantic searches**: you ask "how do we handle database backups?" and it returns
the passages that mean that, even when they never use those words. Everything runs on your own machine, and nothing is
sent to a cloud service: the embedding model runs on your CPU inside the container.

This guide is for people who run and use it. For how it works inside, see [Architecture](../Architecture/README.md);
for the HTTP reference, see [Api](../Api/README.md).

This is a living document. It describes the code as of migration `0002_access_control`; update it alongside the source.

## Contents

- [getting-started.md](getting-started.md) — install, first run, the first admin account, stopping and resetting.
- [walkthrough.md](walkthrough.md) — a worked example with screenshots: index the docs, crawl a site, search both.
- [dashboard.md](dashboard.md) — every panel of the web dashboard and the common tasks.
- [collections-and-access.md](collections-and-access.md) — shared and restricted collections, accounts, grants and tokens.
- [ingesting.md](ingesting.md) — crawling sites, pushing Markdown, indexing repositories, keeping a docs repository in sync.
- [searching.md](searching.md) — the search API, how scores work, how to ask good questions.
- [legal-and-ethics.md](legal-and-ethics.md) — **read before crawling**: what the crawler does and does not do to behave well, and the legal and ethical considerations of copying other people's content.
- [configuration.md](configuration.md) — every setting and environment variable.
- [operations.md](operations.md) — backups, upgrades, logs, health checks, resource use.
- [troubleshooting.md](troubleshooting.md) — symptoms and fixes.

## The ideas in five minutes

| Term | Meaning |
|---|---|
| **Collection** | A named set of documents, for example `team-handbook`. Searches run over one collection, several, or everything you may read. Its name (the *slug*) uses lowercase letters, digits and dashes, up to 64 characters. |
| **Source** | One document inside a collection, identified by a URI such as `https://docs.example.com/guide` or `repo://myrepo/README.md`. Indexing the same URI again replaces it, but only when its content changed. |
| **Chunk** | A passage of a source, about 400 words and symbols long, cut at headings. A search returns chunks, each with the heading path it sits under (`Guide > Install > Linux`). |
| **Score** | How close a chunk's meaning is to your question, from -1 to 1 (cosine similarity). About 0.6 and above is usually a good match. |
| **Shared / restricted** | A *shared* collection can be read by everyone who can reach the service. A *restricted* collection is invisible without a token that grants it: it does not appear in lists, searches or counts, and asking for it by name answers 404. |
| **Account** (client) | A named identity with a secret **token** and a list of grants (which collections it may read or write, with optional wildcards such as `notes-*`). An **admin** account manages the others. The API calls accounts *clients*. |
| **Token** | The secret that starts with `hip_`. It is shown once when created. Only a hash is stored, so it cannot be recovered; rotate or replace it if lost. |

## What you can do with it

1. **Crawl documentation** into a collection from the dashboard, then search it.
2. **Push your own Markdown** (notes, runbooks, decisions) through the API, then let an assistant search it over MCP.
3. **Index a repository** from a Repomix pack, with secrets filtered out before anything is embedded.
4. **Keep a docs repository current** with a Forgejo push webhook, so only changed files are re-indexed.
5. **Keep private material private** in restricted collections that only specific accounts can see.

## What it is not

- It is not a chat bot or a text generator: there is no language model in it, only retrieval. You get back the
  passages; what you do with them is up to you or your assistant.
- It is not a multi-tenant service. It is meant for one person or a small trusted group on one machine or network.
- It does not read your files by itself. Content gets in only when you crawl, push or sync it.

## Where to go next

New: [getting-started.md](getting-started.md). Already running: [dashboard.md](dashboard.md) and
[ingesting.md](ingesting.md). Setting up access for other people or tools:
[collections-and-access.md](collections-and-access.md).
