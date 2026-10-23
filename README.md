# Hippocampus

A small self-hosted memory service: it stores documents (web pages you crawl, Markdown you push, repositories) as
searchable chunks in PostgreSQL with pgvector and answers **semantic searches** ("how do we handle database
backups?") instead of keyword matches. It has a REST API, an MCP server for AI assistants, and a web dashboard.
Everything runs on your own machine; nothing is sent to a cloud service.

- **Local embeddings:** `BAAI/bge-small-en-v1.5` (384 dimensions) runs on your CPU, baked into the image.
- **Collections with access control:** a collection is *shared* (readable by every caller) or *restricted* (only by
  access tokens you grant). Tokens are named accounts with read/write grants; only a hash is stored.
- **Dashboard:** crawl a site, browse sources, try searches, manage collections and accounts.
- **MCP:** a `hippocampus_search` tool (and an optional indexing tool) for MCP-capable assistants.

## Quick start

You need Docker (with the compose plugin) or Podman (with `podman compose`) and `bash`.

```bash
git clone <this repository> hippocampus && cd hippocampus
./hippocampus up
```

The first run creates `hippocampus.env` with a random database password, builds the image (a few minutes: it
downloads the embedding model), starts PostgreSQL and the API, applies the database migrations, and creates the
first admin account. **Its token is printed once, so copy it now.** Then open <http://127.0.0.1:8090/ui/>, click
`anonymous` in the header, and paste the token.

Other commands: `./hippocampus status`, `logs`, `down` (keeps your data), `reset` (deletes everything, asks first),
and `./hippocampus admin ...` for the account tool (`list-clients`, `create-client`, `rotate-client`, `revoke-client`).

## Using it

1. In the dashboard, **crawl** a documentation site into a new collection (the *static* engine is the default).
2. Use the **search sandbox**, or call the API:

   ```bash
   curl -s http://127.0.0.1:8090/v1/search -H 'Content-Type: application/json' \
     -d '{"query": "how do I configure the proxy?", "top_k": 5}'
   ```

3. Index your own Markdown: `POST /v1/ingest/file` with `{"collection": "...", "uri": "notes/a.md", "text": "..."}`.
4. Give a tool its own limited account: `./hippocampus admin create-client --name notes-reader --read my-notes`.

With `HIPPO_REQUIRE_AUTH=true` (the default) every write needs a token; reading shared collections stays open.
**Restricted** collections are invisible without a grant: an unscoped search never returns them and a named search
answers 404. Create one in the dashboard (admin token) or with `POST /v1/collections` and `"visibility": "restricted"`.

### MCP (for an AI assistant)

```bash
pip install -e ".[mcp]"   # in a virtualenv
HIPPO_URL=http://127.0.0.1:8090 HIPPO_TOKEN=<a token with read access> HIPPO_MCP_READONLY=1 python -m hippocampus.mcp_server
```

`HIPPO_MCP_READONLY=1` leaves out the indexing tool, which reads local files; use it wherever a model chooses the tools.

## Configuration

Everything is in `hippocampus.env` (see `hippocampus.env.example`): the port and bind address (default
`127.0.0.1`, this computer only), `HIPPO_REQUIRE_AUTH`, the allowed client networks, crawler limits, and
`HIPPO_INSTALL_BROWSER=1` to build with Chromium so the crawler's *browser* engine can render JavaScript-only sites.
After editing, run `./hippocampus up` again.

## Safety notes

- The service is meant for your own machine or a trusted network. Keep `HIPPO_BIND=127.0.0.1` unless you know why not;
  if you expose it, put a reverse proxy with TLS and your own login in front of it.
- Crawled web content is untrusted data. The crawler refuses private and local addresses, and the dashboard shows
  everything as plain text, never HTML.
- Tokens are shown once when created and cannot be recovered; rotate or replace an account if one is lost.
- Back up the database with, for example,
  `docker compose --env-file hippocampus.env exec postgres pg_dump -U hippocampus hippocampus > backup.sql`
  (use `podman compose` if that is your engine).

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[db,api,mcp,dev]"
scripts/check.sh            # migrations from zero, the test suite, the dashboard JavaScript (starts a dev database via podman)
scripts/check.sh --no-db    # unit tests only
scripts/smoke_live.py       # end-to-end check of a running instance (asks for an admin token, leaves nothing behind)
```

`.github/workflows/ci.yml` runs the same checks on every push and pull request.

## Contributing

Issues and pull requests are welcome: see `CONTRIBUTING.md` (set-up, the checks to run, how review works). This repository is
generated from the maintainer's private upstream; a merged pull request is imported there with your authorship kept, so the
next sync carries it forward.

## License

MIT, see `LICENSE`.
