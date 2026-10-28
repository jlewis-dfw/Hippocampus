# Getting started

How to install, start and stop Hippocampus on one machine, and how to sign in to the dashboard for the first time.

## What you need

- **Docker** with the compose plugin (`docker compose version` must work) **or Podman** with `podman compose` (and a compose
  provider such as `podman-compose`). Either one is enough.
- **bash** and either `curl` or `python3` (the launcher uses one of them to wait for the service). On Windows, bash means Git Bash
  (Git for Windows) or a WSL terminal.
- About **1.2 GB of free disk** for the two images (measured: the API image is about 660 MB with the embedding model
  baked in, PostgreSQL with pgvector about 450 MB; the API image grows to about 1.7 GB if you build it with
  `HIPPO_INSTALL_BROWSER=1`), plus room for your data. The first build downloads the model, so it needs internet access once.
- Give your container engine at least **2 GB of RAM**: the API process holds the embedding model in memory.

### Check your machine first

A script checks all of this and tells you plainly what is missing. It only looks and changes nothing unless you ask it to:

```bash
scripts/prereqs.sh              # macOS and Linux
.\scripts\prereqs.ps1           # Windows PowerShell
```

It reports Docker and Podman separately (one working engine is enough), then lists **exactly what needs to be installed and why**,
with the command for each item. To have it install things for you, run it again with `--install` (`-Install` on Windows). It first
makes you type a sentence confirming that you have read the script and understand that it can change your system, and then asks
**y/N before every single item**, showing the exact command each time. `--dry-run` (`-DryRun`) shows the commands without running
anything. `--dev` (`-Dev`) also checks what you need to work on the code (git, Python 3.12, node).

The Windows script has been checked for syntax and run in a simulated environment, but not on a real Windows machine: read what it
prints before you answer y.

## First run

From the repository root:

```bash
./hippocampus up
```

The launcher (`hippocampus`, a bash script) does the following, in order:

1. Picks an engine: `docker compose` if it is available **and running**, otherwise `podman compose` (an engine that is installed but
   stopped is skipped). Set `CONTAINER_ENGINE=docker|podman` to force one.
2. Creates `hippocampus.env` from `hippocampus.env.example` if it does not exist, with a random 32-character database
   password. The file is created with mode 600 and is git-ignored.
3. Builds the image (`deploy/api.Dockerfile`) and starts three services: `postgres` (PostgreSQL 16 with pgvector),
   `migrate` (applies the database migrations once, then exits) and `api` (the service and dashboard).
4. Waits until `http://127.0.0.1:<port>/v1/status` answers. The first start loads the embedding model, which can take a
   minute or two.
5. If no active admin account exists, creates one named `admin` and **prints its token once**.

Copy that token now. (If you use the `/onboard` command, it starts the service through `scripts/token_env.py capture`, which saves the token to the
git-ignored `token.env` with mode 600 and keeps it out of the output; then `scripts/token_env.py run HIPPO_TOKEN_ADMIN -- <command>` hands it to a
command, and `scripts/token_env.py list` shows the names saved.) A line such as this is printed:

```
token (shown once, store it in the vault): hip_...
```

If you lose it, see [collections-and-access.md](collections-and-access.md#lost-or-leaked-tokens).

When it finishes, open <http://127.0.0.1:8090/ui/> (the port is `HIPPO_PORT`, default 8090).

## Signing in to the dashboard

The dashboard has no username or password. Click the button in the top right (it says `anonymous`), paste the admin
token into **Bearer token**, and click **Use token**. The button changes to `admin (admin)` and the admin-only parts
appear: restricting collections and the clients (accounts) table. The token is kept in the browser tab's session storage
only, so closing the tab signs you out. See [dashboard.md](dashboard.md).

## Check that it works

```bash
./hippocampus status                 # containers, and the status JSON from the API
curl -s http://127.0.0.1:8090/v1/status
```

A healthy answer looks like `{"db_latency_ms":9.3,"active_jobs":0,"embedder":{"model":"BAAI/bge-small-en-v1.5","dimensions":384},"collections":0,"sources":0,"chunks":0}`.

For a full end-to-end check, run `scripts/smoke_live.py --url http://127.0.0.1:8090`. It asks for the admin token, creates
a temporary restricted collection, indexes a page, searches it, checks the access rules, and deletes everything it made.

## Your first documents

1. In the dashboard, under **Crawl a site**, enter a documentation URL, choose **New collection...**, give it a slug
   such as `docs`, and click **Start crawl**. A live log appears; depth 1 is the start page only, depth 2 also follows
   the links on it.
2. When the job says `done`, click **Search** on the collection's row and ask a question.
3. Or push your own text over the API: see [ingesting.md](ingesting.md#pushing-markdown).

## Everyday commands

| Command | What it does |
|---|---|
| `./hippocampus up` | Start, or apply changes: it rebuilds the image first (cached layers make a repeat fast), restarts what changed, and does not create a second admin. Extra arguments go to `compose up` (for example `--force-recreate`). |
| `./hippocampus status` | Show the containers and the service status. |
| `./hippocampus logs [api\|postgres\|migrate]` | Follow the logs. |
| `./hippocampus admin ARGS` | Run the account tool (create, rotate and revoke accounts). See [../Admin/README.md](../Admin/README.md). |
| `./hippocampus down` | Stop everything. Your data stays in the database volume. |
| `./hippocampus reset` | Stop and **delete all data**. You must type `DELETE` to confirm. |

## Network exposure

By default the service listens on `127.0.0.1` only, so only this computer can reach it. To reach it from other machines,
set `HIPPO_BIND=0.0.0.0` in `hippocampus.env` and run `./hippocampus up` again. Only do that on a network you trust, and
read the security notes in [operations.md](operations.md#security-checklist) first: put a reverse proxy with TLS and
authentication in front of it, and keep `HIPPO_REQUIRE_AUTH=true`.

## Uninstalling

```bash
./hippocampus reset      # removes the containers, the network and the data volume
```

Then delete the directory. The image (`hippocampus-api:local`) can be removed with `docker rmi` or `podman rmi`.
