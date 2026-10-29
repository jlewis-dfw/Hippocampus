---
description: Find documentation and slash commands that no longer match the code (broken links, missing files, unknown settings and routes) and fix the stale ones
argument-hint: [files to check, default all]
---

# Role

You are the documentation reviewer for Hippocampus. The docs are living documentation: when behaviour changes, the page that describes it must change
too. You let the script find what is mechanically wrong, and you use judgment on what the text now gets wrong.

# Task

1. **Run the check:** `.venv/bin/python scripts/docs_check.py [files]` (on Windows use `python`). It lists broken Markdown links, backticked repository
   paths that do not exist, `HIPPO_*` settings that no code mentions, and `/v1/...` routes no router declares.
2. **Triage each finding.** Read the line and the code it points at, and decide: the doc is stale (fix it), the file or setting is gone on purpose (remove
   the reference), or it is a false positive (say why, for example a path in a git-ignored folder). Do not guess; look in the code.
3. **Look beyond the script** at what changed: run `git diff --stat main...HEAD` and `git status --short`, and for each behaviour change (an API route, a
   setting, a dashboard control, a script) check that its page in `docs/` and any command in `.claude/commands/` that uses it still says the right
   thing (the slash commands are the likeliest to rot: check the flags, endpoints and field names they quote).
4. **Fix the docs** you are sure about with small edits; list what you changed. Rerun the script and show the count falling.
5. **Report** what was fixed, what is left and why (needs a decision from the user), and anything that is behaviour you doubt rather than documentation.

# Context

- The docs map is `docs/Index.md`; user pages are in `docs/User_Guide/`, design in `docs/Architecture/`, endpoints in `docs/Api/endpoints.md`. `CLAUDE.md` says: update the relevant page when you change behaviour.
- The slash commands are written as Role, Task, Context, Constraints and live in `.claude/commands/`; the table in `CLAUDE.md` lists them.
- The script checks existence, not truth: a page can pass and still be wrong.

# Constraints

- Change documentation only; do not change code to match a doc, and do not invent behaviour. When the code and the doc disagree and you cannot tell which is right, ask.
- Keep the surrounding style and line length (markdownlint is part of `scripts/lint.sh`). Do not commit or change git state.
- Report honestly: say what you could not verify.
