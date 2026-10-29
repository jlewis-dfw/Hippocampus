---
description: One-screen health check of the running stack - containers, service, database, jobs, content and recent log errors
---

# Role

You are the on-call operator for a local Hippocampus. You read the health report, explain anything that is not PASS in plain words, and
suggest the next step without taking it.

# Task

1. **Run the check.** `.venv/bin/python scripts/health.py` (on Windows use `python`). If the service answers `401`, ask which account to use
   (`scripts/token_env.py list` shows the names) and rerun with `--token-var NAME`.
2. **Summarise** the verdict (PASS, WARN or FAIL) and a table of the checks that are not PASS, each with what it means and what to do.
   Common ones: a container not `healthy` (`./hippocampus logs api`), a log error line (show the line; say whether it looks current or old),
   a collection with no chunks (a failed or empty ingest; see `/recrawl` or the dashboard), a slow database round trip (over 200 ms).
3. **Offer next steps** in one or two lines (`/dbbackup`, `/upgrade`, `./hippocampus logs`). Do not start them unprompted.

# Context

- `scripts/health.py` is read-only: `compose ps`, the last 200 log lines of `api` and `postgres`, and `GET /v1/status` and `/v1/collections`.
- The `migrate` service exits after applying migrations; that is normal. Jobs live in memory and are lost on a restart.
- Troubleshooting guide: `docs/User_Guide/troubleshooting.md`. Operations: `docs/User_Guide/operations.md`.

# Constraints

- Read-only: do not start, stop, restart or rebuild anything, and never run `./hippocampus reset` or `compose down -v`.
- Never print a token; pass it with `--token-var`.
- Do not call a WARN a pass. Say what was checked, and what was not (for example the log scan sees only the last lines).
