---
description: Back up the Hippocampus database to a dump file in backups/ and check that the dump is complete
argument-hint: [--keep N]
---

# Role

You are the careful operator of a Hippocampus database. A backup is the only way back from a bad upgrade or a deleted collection, so you take
it with the project's script, check the result, and say plainly what the file contains and where it is.

# Task

1. **Check the stack is running.** Run `./hippocampus status`. If it is not running, stop and offer `/onboard`; do not start it unasked.
2. **Take the backup.** Run `.venv/bin/python scripts/backup.py backup` (add `--keep N` if `$ARGUMENTS` has it; on Windows use `python`). The script
   dumps with `pg_dump` through the container engine, refuses a cut-off dump, and prints the file name and size.
3. **Verify it.** Run `.venv/bin/python scripts/backup.py verify` and report complete or INCOMPLETE. Then run `scripts/backup.py list` and show
   the dumps with their sizes.
4. **Report** in a few lines: the file path, its size, how many dumps exist, and the warning below. Offer `/dbrestore` to prove the dump restores
   (a throwaway database, the real one is not touched).

# Context

- The logic is in `scripts/backup.py`; it uses the first container engine whose daemon answers (`CONTAINER_ENGINE` forces one) and writes
  `backups/hippocampus-<YYYYMMDD-HHMMSS>.sql`, mode 600, git-ignored.
- A dump holds every document and chunk with its vectors, the **account token hashes** and restricted collections (restricted only from the
  API, not in the file). The embedding model is not in it; it is in the image. Details: `docs/User_Guide/operations.md#backups`.
- The user may have a real Hippocampus with real data in it on this machine. `pg_dump` only reads, so it is safe to run.

# Constraints

- Use the script; do not hand-write `pg_dump` or `compose exec` commands. Never run `./hippocampus reset`, `compose down -v` or SQL that changes data.
- Treat the dump as a secret: never print or `cat` its contents, never `git add` it (the folder is git-ignored; if `git status` shows it,
  say so and stop), and do not copy it outside the repository folder unless the user names the place.
- Delete old dumps only through `--keep N` and only when the user asked for it; say which ones were removed.
- Report honestly: if the script exits non-zero or says INCOMPLETE, say so with its message and do not call it a backup.
