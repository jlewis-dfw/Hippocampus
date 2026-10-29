---
description: Run the full checks before a commit (lint, migrations, tests, docs links), summarise, and propose a commit message without committing
argument-hint: [--all to include end-to-end]
---

# Role

You are the gatekeeper before a commit on Hippocampus. You run the same checks the project runs, say plainly whether the change is ready, and prepare a
commit message. You do not commit.

# Task

1. **See what changed:** `git status --short` and `git diff --stat`. Flag anything that must not be committed: `hippocampus.env`, `token.env`, tokens, `backups/`
   dumps, `system-specs.md`, crawled content, MCP configuration containing a token. Say if a refreshed `tests/reports/latest` is in the diff (it is committed on purpose, not with every change).
2. **Run the checks:** `scripts/check.sh` (add `--all` if `$ARGUMENTS` has it; on Windows `python scripts/check.py`). It runs lint, migrations from zero, unit and
   integration tests and the JS check. Then `scripts/docs_check.py` on the changed Markdown files. If dependencies are missing, point to `/onboard` or `scripts/prereqs.sh --dev --setup`.
3. **Read the result** the way `/test` does: the gate decision (PASS, CONCERNS, FAIL) from `tests/reports/latest/results.json`, counts per tier, and for every
   failure the test id and first message line. Report lint findings with file and line.
4. **Check the project rules** against the diff: a new `store.py` method has the same method on `MemStore`; a new access rule has a test for the denied case and for the hidden case (404);
   behaviour changes have their `docs/` page updated; new tooling is Python, not bash.
5. **Propose a commit message:** a short imperative subject (under 72 characters), then a body of two to five lines saying what and why, in the style of `git log --oneline -10`.
   Say which files you would stage by name; never `git add -A`.

# Context

- `scripts/check.sh` only starts `scripts/check.py`; both pick the first container engine whose daemon answers. Integration tests start their own throwaway database; never point them at a real one.
- The Claude Code hooks already lint each edited file and run ruff plus the unit tests when a turn that edited Python ends; this command is the fuller gate.
- Commits and pushes happen only when the user asks.

# Constraints

- Do not commit, push, stage, amend or change git state. Do not fix failures unasked; list them and offer.
- Never print secrets from the diff; name the file instead.
- Report honestly: skipped tiers and CONCERNS are not a pass, and say so.
