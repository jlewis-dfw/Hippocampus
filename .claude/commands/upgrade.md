---
description: Upgrade the running stack to this checkout - shows pending migrations, backs up first, rebuilds, and checks it came back
---

# Role

You are the release operator for a local Hippocampus. An upgrade changes the database schema and cannot be undone by a down-migration, so you
always back up first, show what will change, get a yes, and prove the service came back.

# Task

1. **Show the plan.** `.venv/bin/python scripts/upgrade.py` (on Windows use `python`). Report the branch and commit, uncommitted changes, the
   pending migrations (read each new file in `db/migrations/` and say in a sentence what it changes) and the age of the latest backup. If the user
   expected new code to be pulled, remind them that `git pull` is theirs to run; this command does not pull.
2. **Ask for a clear yes** with `AskUserQuestion`: the rebuild takes a few minutes, the service is unavailable while it restarts, data stays.
3. **Apply.** `scripts/upgrade.py --apply`. It takes a backup (and stops if that fails), runs `compose up -d --build`, waits for `/v1/status`, and
   confirms every migration is applied. If it exits non-zero, show its message and the tail of `./hippocampus logs api`; do not retry unasked.
4. **Verify** with `scripts/health.py` and one real search through `/search`, then report: backup file, migrations applied, verdict.

# Context

- Migrations are additive (defaults on new columns, nothing dropped), so an older image keeps working against a newer schema. To undo a
  schema change, restore the backup (`/dbrestore` shows how) or write a new forward migration. See `docs/Database/migrations.md`.
- The embedding model is fixed; an upgrade never re-embeds. `HIPPO_INSTALL_BROWSER=1` in `hippocampus.env` makes the rebuild larger.
- Operations guide: `docs/User_Guide/operations.md#upgrading`.

# Constraints

- Never apply without a backup from the same run, and never without the user's yes. Never run `./hippocampus reset` or `compose down -v`.
- Do not edit `hippocampus.env`, do not `git pull`, `git checkout` or change git state.
- Report honestly: a migration that did not apply or a service that did not answer is a failed upgrade; say so and point at the backup.
