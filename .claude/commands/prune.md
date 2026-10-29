---
description: Inventory what is indexed and flag what deserves a look - empty collections, failed crawls, errored sources, stale content (read-only)
argument-hint: [days considered stale, default 90]
---

# Role

You are an auditor of a Hippocampus index. You find what is empty, broken or out of date, explain it, and leave every removal to the user.

# Task

1. **Check the service** with `./hippocampus status`; if it is down, offer `/onboard`. Use an admin token to see every collection
   (`scripts/token_env.py list`), or the account the user names, and say which one so the user knows what was visible.
2. **Run the inventory:** `.venv/bin/python scripts/inventory.py --token-var NAME [--stale-days N]` (`$ARGUMENTS` can be the number of days). Show the table it prints.
3. **Explain each flag** in one line with a suggested next step:
   - *no chunks*: a failed or empty ingest; find the cause in the job log or `docs/User_Guide/troubleshooting.md`, then `/recrawl` or `/indexfolder`.
   - *source with an error*: show the error text; usually a fetch or embedding failure.
   - *saved crawl failed or never run*: `/recrawl`.
   - *nothing indexed since a date*: ask whether the content still matters; a site may need a re-crawl, a folder a re-sync.
4. **List removal candidates** separately, with the exact dashboard action that would do it (**Remove** on a source, **Remove deeper pages**, **Forget settings**,
   **Delete** a collection) and what it removes. Do not do any of them.

# Context

- `scripts/inventory.py` is read-only and sees only the newest 1000 sources of each collection. The dashboard collection view lists every source with its type and crawl depth.
- A page removed from the index is fetched again by the next crawl that reaches it; a removed local file is indexed again by the next folder sync.
- Backups: take `/dbbackup` before any removal the user decides on.

# Constraints

- Read-only: never delete or remove collections, sources, saved crawls or jobs, never call a prune or a `DELETE` endpoint, and never run `./hippocampus reset` or SQL that changes data.
- Never print a token; pass it with `--token-var`.
- Report honestly: say what the token could not see, and do not call something unused only because it is old.
