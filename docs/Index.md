# Hippocampus Docs

Living documentation for Hippocampus, a self-hosted semantic search service: it indexes documentation, web pages and code into PostgreSQL with pgvector, and answers
questions by meaning, from a REST API, a web dashboard or an MCP server for AI assistants. Docs are organized by audience and project area. They are kept up to date as
development continues: update the relevant page(s) alongside code changes.

New here? Start with [Getting started](User_Guide/getting-started.md).

## Index

### [User Guide](User_Guide/README.md)

For people who run and use Hippocampus.

| Doc | Covers |
|---|---|
| [README](User_Guide/README.md) | What Hippocampus is, what it is for, and a map of the guide |
| [getting-started.md](User_Guide/getting-started.md) | Check your machine, start the stack, first admin token, first collection and search |
| [dashboard.md](User_Guide/dashboard.md) | Every part of the web dashboard |
| [collections-and-access.md](User_Guide/collections-and-access.md) | Shared and restricted collections, accounts, tokens, grants, the authentication switch |
| [ingesting.md](User_Guide/ingesting.md) | Crawling websites, importing repositories, keeping a docs repository in sync, removing things |
| [searching.md](User_Guide/searching.md) | Asking good questions, scores, filters |
| [configuration.md](User_Guide/configuration.md) | Every environment variable and the network gate |
| [operations.md](User_Guide/operations.md) | Health, logs, backups, upgrades, resource use, security checklist |
| [troubleshooting.md](User_Guide/troubleshooting.md) | Symptoms, causes and fixes |
| [walkthrough.md](User_Guide/walkthrough.md) | A worked example with screenshots: index the docs, crawl a site, search both |
| [legal-and-ethics.md](User_Guide/legal-and-ethics.md) | Responsible web crawling: what the crawler does and does not honour, a checklist, removal requests |

### [Api](Api/README.md)

The REST service, `src/hippocampus/api.py`.

| Doc | Covers |
|---|---|
| [README](Api/README.md) | Overview, conventions, error format |
| [endpoints.md](Api/endpoints.md) | Every route: access required, request, response, status codes |
| [authorization.md](Api/authorization.md) | Tokens, grants and globs, hidden-is-404, the reserved prefix, the network gate |

### [Architecture](Architecture/README.md)

How it works inside.

| Doc | Covers |
|---|---|
| [README](Architecture/README.md) | The components and how a request moves through them |
| [embedding-and-chunking.md](Architecture/embedding-and-chunking.md) | The fixed embedding model and the header-aware chunker |
| [ingestion-pipeline.md](Architecture/ingestion-pipeline.md) | Hash skip, embed-before-write, atomic replace |
| [crawler.md](Architecture/crawler.md) | The static and browser engines, the public-address guard, robots.txt, limits |
| [search.md](Architecture/search.md) | The query, scores, filters, the vector index |
| [jobs.md](Architecture/jobs.md) | Background jobs, logs and the live stream |
| [security.md](Architecture/security.md) | Threat model and defences |

### [Database](Database/README.md)

PostgreSQL 16 with pgvector.

| Doc | Covers |
|---|---|
| [README](Database/README.md) | Overview, SQL cookbook, known limits |
| [schema.md](Database/schema.md) | Every table and column, indexes, constraints |
| [migrations.md](Database/migrations.md) | How migrations are written, applied and checked |

### [Mcp](Mcp/README.md)

The stdio server that gives AI assistants `hippocampus_search` and `hippocampus_index_doc` (with a read-only mode).

| Doc | Covers |
|---|---|
| [README](Mcp/README.md) | The tools, configuration, read-only mode, safety notes, troubleshooting |
| [connecting-assistants.md](Mcp/connecting-assistants.md) | Registering the server with Claude Code, OpenCode, Antigravity (`agy`) and Devin; accounts, tokens and checking it works |

### [Dashboard](Dashboard/README.md)

How the dependency-free web dashboard is built: serving, the content security policy, the code rules, the live job log.

### [Admin](Admin/README.md)

The command-line tool for accounts and collection visibility.

### [Local](Local/README.md)

The Docker or Podman stack, the launcher, the image, persistence and the development database.

### [Testing](Testing/README.md)

The test suite, the checks and linters, the smoke test and CI.

### [Development](Development/README.md)

Setup, code layout, conventions, recipes for common changes, documentation rules and the public copy.

## Search these docs with Hippocampus

You can index this folder into your own instance and ask questions of it:

```bash
.venv/bin/python scripts/index_docs.py --plan     # show what would be indexed
.venv/bin/python scripts/index_docs.py            # index it (asks for an admin or write token)
```

It fills a collection named `hippocampus-docs` (created if missing) and never deletes anything. Details and options are in the project README.
