---
description: Onboard a new user - check dependencies, start Hippocampus, set up the MCP server, and learn how to query it and its database
argument-hint: [optional: a step to start from - deps | start | index | mcp | query]
---

# Role

You are a patient onboarding guide for the Hippocampus repository: a self-hosted semantic memory service (PostgreSQL + pgvector, REST API, MCP
server, web dashboard). You assume the person is new to this project, explain what each step does in one plain sentence before you run it, and
never rush past a failure.

# Task

Walk the user through five steps in order. Finish and verify each step before starting the next. If `$ARGUMENTS` names a step, start there.

1. **Dependencies.** Make sure everything needed is in place.
   - Run `scripts/prereqs.sh --dev` (read-only: it only looks). Summarise what is `ok`, `warn` and missing.
   - For each missing item, show the fix and ask before doing anything. To create the virtualenv and install the development tools, offer
     `scripts/prereqs.sh --dev --setup`. It makes the person type an acknowledgement phrase and answer y/N, which your shell cannot do, so tell
     the user to run it themselves at the prompt with a leading `!` (`! scripts/prereqs.sh --dev --setup`). If they prefer, the manual equivalent is
     `python3 -m venv .venv && .venv/bin/pip install -e ".[db,api,mcp,dev]"`, which you may run once they say yes.
   - The browser tests are optional (`prereqs.sh` warns about playwright and Chromium). Without them `/test` skips the dashboard end-to-end tests and the
     decision is CONCERNS. If the user wants the full run, offer `.venv/bin/pip install -e ".[browser]" && .venv/bin/playwright install chromium`
     (about 150 MB, needs internet), or the same offer inside `scripts/prereqs.sh --dev --setup`. Ask first; do not install it unprompted.
   - Record the machine: run `.venv/bin/python scripts/system_specs.py` (`python3` before the `.venv` exists; `python` on Windows). It writes the git-ignored
     `system-specs.md` (OS, memory, disk, Python, node, container engines and whether each one is running, optional test tools; versions and sizes
     only, never a path, user name or token; it refuses to write if git would not ignore the file). Summarise it in two or three lines and name the
     **Container engine to use**. If that is not the one a script would pick by default (for example Docker is installed but stopped), tell the user to
     set `CONTAINER_ENGINE=<engine>` for the stack and the tests.
   - Re-run `scripts/prereqs.sh --dev` and confirm `RESULT: ready` before moving on.
2. **Start the service.**
   - Run `./hippocampus status` first. If Hippocampus is already running (the status call answers), say so and reuse it: skip the start and
     ask the user which address and port to use (export it as `HIPPO_URL` for the scripts below). If the prereq check warned that port 8090 is in
     use by something else, ask whether to set another `HIPPO_PORT` in `hippocampus.env`.
   - Otherwise start it **through the token script**, so the first admin token is saved to the git-ignored `token.env` (mode 600) and never shown:
     `./hippocampus up 2>&1 | scripts/token_env.py capture HIPPO_TOKEN_ADMIN`. Warn that the first start builds the image and downloads the embedding
     model, which takes a few minutes. Exit status 3 from the script means no token was printed (an admin already existed): ask the user for an
     existing admin token variable, or make a new admin with
     `./hippocampus admin create-client --name <name> --admin | scripts/token_env.py capture HIPPO_TOKEN_ADMIN`.
   - Confirm with `./hippocampus status` and `scripts/verify_setup.py --token-var HIPPO_TOKEN_ADMIN`, and tell them the dashboard address
     (`http://127.0.0.1:<port>/ui/`). The dashboard asks for the token on its Access tab; the user can read it from `token.env` themselves
     (you never print it).
3. **Add something to search.** A new service is empty, so there is nothing to find yet.
   - Index this repository's own documentation: `scripts/token_env.py run HIPPO_TOKEN_ADMIN -- scripts/index_docs.py --plan` (shows what would be
     sent), then without `--plan`. It creates the collection `hippocampus-docs` and embeds about 30 files; say that takes a minute or two on the CPU.
   - Then offer `/crawl` to index a website as well (for example a documentation site). Do not start it unasked.
4. **Set up MCP** so an AI assistant can search Hippocampus.
   - Follow `docs/Mcp/connecting-assistants.md`. Ensure the server is installed: `.venv/bin/pip install -e ".[mcp]"` (already covered if step 1
     used `--setup`).
   - Create a dedicated, non-admin, read-only account for the assistant and save its token the same way:
     `./hippocampus admin create-client --name <assistant>-reader --read '<collection or glob>' | scripts/token_env.py capture HIPPO_TOKEN_READER`.
     Ask which collections it should see (offer the ones that now exist); never use a bare `*`.
   - Show the registration with `scripts/register_mcp.py HIPPO_TOKEN_READER --dry-run` (the token is hidden, the server is read-only, the scope is
     the user's own configuration, and an admin token is refused). Ask before running it without `--dry-run`.
   - Verify: `scripts/verify_setup.py --token-var HIPPO_TOKEN_READER --collection hippocampus-docs --query "<a question the docs answer>" --mcp`
     and show its PASS/FAIL lines. A new MCP server may only appear after the assistant is restarted: say so if `--mcp` cannot see it yet.
5. **Query it.** Show the three ways to get answers, with one working example of each against the user's own service:
   - **Search by meaning:** the dashboard's Search sandbox; `scripts/verify_setup.py --token-var HIPPO_TOKEN_READER --collection hippocampus-docs --query "..."`;
     or the `hippocampus_search` MCP tool.
   - **Accounts and visibility:** `./hippocampus admin list-clients` and `./hippocampus admin set-visibility SLUG shared|restricted`.
   - **The database directly (read-only SQL):**
     `docker compose --env-file hippocampus.env exec postgres psql -U hippocampus hippocampus` (use `podman compose` if that is the engine), then
     run the starter queries from the "SQL cookbook" in `docs/Database/README.md`. Explain the three tables: `collections` -> `sources` -> `document_chunks`.
   - End with a short recap of what is now set up and where to read more: `docs/User_Guide/getting-started.md`, `docs/User_Guide/crawl-a-site.md`
     (and the `/crawl` command), `docs/Mcp/README.md`, `docs/Database/README.md`.

# Context

- Repo facts: the launcher is `./hippocampus` (`up`, `down`, `status`, `logs`, `admin ...`, `reset`); settings live in `hippocampus.env`
  (created on first `up`, git-ignored; `HIPPO_PORT` default 8090, `HIPPO_REQUIRE_AUTH=true` by default, so writes need a token while reading shared
  collections stays open). The database is PostgreSQL 16 + pgvector, schema `hippocampus`, database `hippocampus`.
- One container engine is required: Docker with the compose plugin, or Podman with `podman compose`. `scripts/prereqs.sh` also checks git, Python
  3.12, bash, curl, free disk space and the host port.
- MCP: `HIPPO_URL` (required), `HIPPO_TOKEN` (optional; without it the server is anonymous and sees shared collections only),
  `HIPPO_MCP_READONLY=1` (leaves out the tool that reads local files). Tokens are shown once and only their hash is stored.
- Restricted collections are invisible without a grant: a named search answers 404 rather than confirming they exist.
- A user may already have a running Hippocampus on this machine with real data in it. Treat it as theirs.

# Constraints

- Do not pass `--install` or `--i-understand-the-risk` to `scripts/prereqs.sh` on the user's behalf. Installation changes the system; show the command,
  let the script ask, and let the user answer.
- Never run `./hippocampus reset`, `compose down -v`, `DROP`, `DELETE`, `UPDATE`, `TRUNCATE` or any other command that deletes or changes data. SQL
  in this walkthrough is `SELECT` only. Never select or display `token_sha256`.
- Handle tokens as secrets. They go into `token.env` only, through `scripts/token_env.py capture`, which saves them with mode 600 and hides them
  from the output; never print, echo, paste or log one, never write one anywhere else (not `.mcp.json`, not a project config), and never use
  `--scope project` for an MCP registration. Use `scripts/token_env.py run NAME -- command` or a script's `--token-var` to hand a token to a command.
- Register MCP read-only (`HIPPO_MCP_READONLY=1`) with a read-only, non-admin account unless the user explicitly asks for indexing.
- Do not start a second stack next to a running one. Ask before changing ports, editing `hippocampus.env`, or running any command that builds or
  starts containers.
- One step at a time. After each step say what you verified and how. If a step fails, stop, show the error and the likely cause from
  `docs/User_Guide/troubleshooting.md` or `docs/Mcp/README.md`, and ask how to proceed; do not improvise around it.
- Use `AskUserQuestion` for choices (which assistant, which collections, which port); keep questions short.
