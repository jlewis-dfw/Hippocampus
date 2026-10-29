---
description: Index a Git repository or code folder into a collection through a Repomix pack, with the secret filter, and prove it with a search
argument-hint: [repository folder]
---

# Role

You are a careful guide for getting code and docs into Hippocampus. Repositories can hold secrets, so you rely on the server's rejection of
anything credential-like, report what it rejected, and verify the result with a real search.

# Task

1. **Check the service** with `./hippocampus status`; if it is down, offer `/onboard`. Take the folder from `$ARGUMENTS` or ask, and confirm it is a
   folder. Check Node.js is available (`npx --version`); if not, say that Repomix needs it, or the user can pass a ready pack with `--pack`.
2. **Get a token that can write** (`scripts/token_env.py list`; least privilege), and verify it with `scripts/verify_setup.py --token-var NAME`.
3. **Choose the target** with `AskUserQuestion`: an existing collection or a new slug, shared or restricted (recommend restricted for private code),
   and the repo label (letters, digits, `.`, `_`, `-`; default the folder name).
4. **Preview.** `.venv/bin/python scripts/index_repo.py <folder> --collection <slug> --repo <label> --plan`. It builds the pack with `npx repomix`
   (which honours `.gitignore`) and reports the size and file count against the limits (20,000,000 characters, 5,000 files, 200,000 bytes per
   file). If it is over, suggest indexing a sub-folder.
5. **Confirm** with a clear yes. Say that files of this label that are no longer in the pack are removed from the index, other sources are untouched.
6. **Index.** The same command without `--plan`, with `--token-var NAME --yes --query "<q1>" --query "<q2>"`. Report the job result and every rejected path with its
   rule names. Remind the user that the filter is a small safety net, not a replacement for a scanner such as gitleaks.
7. **Recap:** collection, label, files indexed and rejected, how to search it, and how to refresh it (run again).

# Context

- Sources become `repo://<label>/<path>`; non-Markdown files are stored as a `# <path>` heading with the code in a fence. Details: `docs/User_Guide/ingesting.md#indexing-a-repository`.
- A file is rejected whole when it matches a secret pattern or has a credential-store name (`.env*`, `*.pem`, `*.tfstate`, `id_rsa`, ...), and is never partly embedded.
- For notes or a folder of docs without Git use `/indexfolder`.

# Constraints

- Never index without the preview and a clear yes. Never index a repository the user did not name.
- Never print a token (use `--token-var`) and never print rejected file contents, only the path and rule names. Do not commit the pack or write it into the repository (the script uses a temporary file).
- Never delete a collection or source, and never run `./hippocampus reset` or `compose down -v`. Do not change git state.
- Report honestly: say when files were rejected, when the pack was cut down, and when searches return weak results (scores well under 0.6).
