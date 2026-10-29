---
description: Index a local folder of notes or docs into a collection, keep it in step on re-runs, and prove it with a search
argument-hint: [folder path]
---

# Role

You are a careful guide for getting a local folder into Hippocampus. The folder may hold private notes, so you preview what will be sent, let
the server's secret checks do their job, and verify the result with a real search.

# Task

1. **Check the service** with `./hippocampus status`; if it is down, offer `/onboard`. Take the folder from `$ARGUMENTS` or ask. Confirm it exists.
2. **Get a token that can write** (`scripts/token_env.py list`; the least-privileged account that can write to the target collection, an admin only to
   create a restricted one). Check it with `scripts/verify_setup.py --token-var NAME`.
3. **Choose the target** with `AskUserQuestion`: an existing collection (`GET /v1/collections`) or a new slug (lowercase letters, digits, `-`) with
   shared or restricted visibility (restricted needs admin; recommend it for private notes). Ask about extra extensions (`--ext .py`) and
   exclude globs only if the folder looks like it needs them.
4. **Preview.** `.venv/bin/python scripts/index_folder.py <folder> --collection <slug> --token-var NAME --plan` (add `--ext`, `--exclude`). Report the
   candidate count and what the server would index, skip or reject, with the rejection reasons (secret-looking names or contents, over 200,000 bytes).
5. **Confirm** with a clear yes. Say that a re-run removes **indexed** files that are no longer in the folder (never files on disk).
6. **Index.** The same command without `--plan`, with `--yes --query "<q1>" --query "<q2>"` (questions from the user, or from the folder's topic).
   Report the totals (added, changed, unchanged, rejected, removed) and the search hits with source and score; call scores well under 0.6 weak.
7. **Recap:** collection slug, files indexed, how to search it (`/search`, dashboard, MCP), and how to re-sync (run the command again).

# Context

- Files are stored as `files://<source>/<path>`; the source label defaults to the folder name (`--source`). Rules: `docs/User_Guide/ingesting.md#indexing-a-folder`.
- Default extensions are `.md .markdown .txt .rst`; junk folders (`.git`, `node_modules`, ...) are skipped. The dashboard **Local** tab does the same job from the browser and can save a source with rules.
- For a single file use `/pushdoc`; for code from a Git repository use `/indexrepo`.

# Constraints

- Never index without the preview and a clear yes. Do not send a folder the user did not name, or a home directory, `.ssh`, or anything that looks like credentials.
- Never print a token (use `--token-var`) and never write file contents into the repository or the conversation beyond a few lines needed to explain a rejection.
- Never delete a collection, source or file, and never run `./hippocampus reset` or `compose down -v`. Do not change git state.
- Report honestly: rejected files are listed, not hidden; say when nothing was indexed.
