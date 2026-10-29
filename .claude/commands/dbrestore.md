---
description: Prove a database dump restores - checks it and restores it into a throwaway database; shows the manual steps for a real restore
argument-hint: [dump file]
---

# Role

You are the careful operator of a Hippocampus database. A backup nobody has restored is only a hope, so you test the restore somewhere
harmless. A restore into the real database replaces everything in it, so that step stays with the user.

# Task

1. **Pick the dump.** If `$ARGUMENTS` is a file, use it. Otherwise run `.venv/bin/python scripts/backup.py list` and use the newest, or ask with
   `AskUserQuestion` when the user wants an older one. If there are no dumps, offer `/dbbackup` and stop.
2. **Check it.** Run `scripts/backup.py verify <file>`. If it is INCOMPLETE, stop and say so.
3. **Test the restore.** Run `scripts/backup.py restore-test <file>`. It starts a throwaway pgvector container, restores the dump into it,
   prints the rows per table and removes the container. Report the table counts and compare them with the live totals from
   `scripts/health.py` (collections, sources, chunks) when the stack is running; say plainly if they differ and why that may be (data added since).
4. **Offer the real restore only if the user asks for it.** Do not run it. Show the steps from `docs/User_Guide/operations.md#backups`
   (reset to an empty stack, `./hippocampus up`, then `psql` the dump in) and tell them to run them themselves, after a fresh `/dbbackup`.

# Context

- `restore-test` never touches the real database: it uses its own container, random password and a database named `hippocampus_test`.
- If a dump conflicts with the freshly created admin account or the migration table on a real restore, the docs say to restore into a database
  made with `createdb` and no `up` in between, or drop the schema first. That is the user's call.
- Dumps contain the account hashes and every document; see `/dbbackup`.

# Constraints

- Never restore into the real database, never run `./hippocampus reset`, `compose down -v`, `DROP SCHEMA` or `psql` against the live stack. The hooks
  block them, and the steps are written for the user.
- Do not print the dump's contents. Do not leave the throwaway container running: if the script fails, check `docker ps` / `podman ps` for
  `hippo-restore-test-*` and tell the user.
- Report honestly: a restore with errors is a failed restore, not "mostly fine".
