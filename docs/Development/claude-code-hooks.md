# Claude Code hooks

The repository ships [Claude Code](https://claude.com/claude-code) hooks. A hook is a command that Claude Code runs around its own tool calls; these
four turn rules from [CLAUDE.md](../../CLAUDE.md) into checks that cannot be forgotten: keep the local data, keep the tokens out of the transcript, keep
the code lint-clean and the unit tests green. They apply to everyone who opens the project in Claude Code and to nobody else; the service itself does not
use them.

Everything lives in two places:

| Path | Contents |
|---|---|
| `.claude/settings.json` | Which hook runs for which tool |
| `scripts/hooks/hooks.py` | The code of all four hooks, plain Python (no bash, no `jq`), so it runs the same on macOS, Linux and Windows |
| `tests/unit/test_hooks.py` | One case per rule, in both directions (what is blocked and what must stay allowed) |

Each entry in `settings.json` runs `python3 scripts/hooks/hooks.py <name>` (`python` when `python3` does not exist). The tool call arrives as JSON on
standard input. A guard answers with a permission decision; the other two report through their exit status. A hook that crashes or is missing lets the
call through (the settings do not set `onFailure: block`), so a bug in a hook cannot lock a session.

## The four hooks

| Hook | When it runs | What it does |
|---|---|---|
| `destructive` | Before every Bash command | **Denies** `./hippocampus reset`, `docker`/`podman compose down -v` or `--volumes`, `volume rm`/`volume prune`, `system prune`, and `DROP DATABASE`, `DROP SCHEMA`, `DROP TABLE` or `TRUNCATE` sent through `psql` or `exec`. Looks inside `bash -c "..."` and `sh -c "..."`. Ignores commit and pull-request messages |
| `secrets` | Before Bash, Read, Write, Edit | **Denies** reading or writing `token.env`, `token.txt` and any other `*.env` file directly, and `git add` of them. **Asks** before writing `hippocampus.env` (`/onboard` may set `HIPPO_PORT`). Denies commands that would print a token (`echo $HIPPO_TOKEN`, `env`, `printenv`) |
| `lint` | After every Write or Edit | Runs ruff (`.py`), shellcheck (`.sh` and `hippocampus`) or yamllint (`.yml`) on that one file and sends the findings back to Claude. Report only: it never fixes, because ruff `--fix` would delete an import that an edit adds before the code that uses it. Remembers which Python files were edited, for the Stop hook |
| `stop` | When Claude finishes a turn | If Python files were edited in this session: runs ruff on them, then `pytest tests/unit -q -x`. A failure blocks the end of the turn and sends the output back (the last 40 lines). Costs nothing when no Python was edited |

### What the secrets guard allows

The token file is never read or written by hand; tokens move through `scripts/token_env.py` (`capture`, `list`, `run NAME -- command`) or a script's
`--token-var`, and the guard lets all of those through, because the file is opened by the script and not by Claude. Also allowed: `--env-file
hippocampus.env` (how `docker compose` and `podman compose` are called), any `*.example` or `*.env.sample` file, reading `hippocampus.env` with the Read
tool, and any mention of these names inside a commit or pull-request message or a here-document.

The commands that `/onboard`, `/crawl` and `/search` run are test cases in `tests/unit/test_hooks.py`, so a change to a command that the guard would
block shows up as a test failure.

### How the Stop hook decides

- Only Python files that the `lint` hook saw being edited in this session count, so a question-and-answer turn costs nothing and a working tree that was
  already dirty does not trigger it.
- It remembers a fingerprint (path, modification time, size) of what it last checked. The same files are not checked twice, so a failure Claude cannot
  fix is reported once and does not trap the session; any further edit is checked again.
- Ruff runs first. If it finds anything, the tests are skipped until that is fixed.
- The pytest run sets `HIPPO_REPORT=0` (it must not write a report or move `tests/reports/latest`) and `HIPPO_HOOK_STOP=1` (the unit tests include the
  hook tests, which must not start the hook again).
- It gives up silently after 100 seconds. `scripts/check.py` is the full gate (integration, end-to-end, linters).

## Switching a hook off

| To | Do |
|---|---|
| Skip the tests but keep ruff at Stop (red-first work) | Set `HIPPO_HOOK_STOP_TESTS=0`, for example `{ "env": { "HIPPO_HOOK_STOP_TESTS": "0" } }` in `.claude/settings.local.json` |
| Drop one hook for yourself | Override it in `.claude/settings.local.json`, which is git-ignored on developer machines, or disable it in the `/hooks` menu |
| Drop one hook for everyone | Remove its entry from `.claude/settings.json` in a pull request |

Run `/hooks` in Claude Code to see the active hooks. After editing `settings.json`, open `/hooks` once or restart the session if a change does not
take effect.

## Known limits

- A guard sees one command line. It cannot know why you want something: if you really mean to run `./hippocampus reset`, run it yourself at the prompt
  with a leading `!`.
- The destructive patterns are anchored to the start of a command (after `;`, `&`, `|`, `(` and `sudo`), and `bash -c`/`sh -c` payloads are checked. A command
  built in a variable or a script file is not seen.
- `secrets` blocks a command that names a secret file next to a program that reads or changes it (for example `cat`, `grep`, `sed`, `cp`, `>`). A file
  reached by a glob or an indirect path is not seen. It is a guard against mistakes, not a security boundary.
- Only edits made with the Write and Edit tools are tracked for the Stop hook. A `sed -i` or a file written by a script is not.
- The Stop hook runs unit tests only. Integration and end-to-end tests need `/test` or `scripts/check.py --all`.
- The lint hook can report problems in the middle of a change that needs several edits (for example an import added before its use). They disappear
  when the change is complete.
- Written and tested on macOS and Linux. The Windows code paths (`.venv\Scripts`, PowerShell verbs) are written but have not been run on Windows.
  The launcher in `settings.json` runs through `sh`, which Claude Code provides on Windows through Git Bash.

## Changing a hook

Edit `scripts/hooks/hooks.py` and add a case to `tests/unit/test_hooks.py` in **both** directions: the command that must be blocked and the nearby
command that must still pass. Keep it to the standard library, and keep path handling portable (see the multi-OS rule in the
[Development guide](README.md#conventions)).
