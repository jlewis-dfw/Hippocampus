#!/usr/bin/env python3
"""Claude Code hooks for this repository, in Python so they run the same on macOS, Linux and Windows (no bash, no jq).

  hooks.py destructive   PreToolUse (Bash): refuse commands that delete the local Hippocampus data
  hooks.py secrets       PreToolUse (Bash, Read, Write, Edit): keep tokens and local configuration out of the transcript and the repository
  hooks.py lint          PostToolUse (Write, Edit): lint the one file that was just changed and hand the findings back to Claude
  hooks.py stop          Stop: when Python files were edited, ruff them and run the unit tests before Claude finishes

Each reads the tool call as JSON on standard input. The two guards print a permission decision (deny or ask), or nothing to let the call
through; ``lint`` exits with status 2 and the findings on standard error. Wired up in .claude/settings.json; tests in tests/unit/test_hooks.py.

Tokens reach token.env only through scripts/token_env.py (capture, list, run), which a Bash call may use freely: ``secrets`` looks only at
commands that name a secret file together with a command that reads or changes it.
  token.env, token.txt, other *.env   deny reading or writing them directly (*.env.example is fine)
  hippocampus.env                     ask before writing it (/onboard may set HIPPO_PORT); ``--env-file hippocampus.env`` is always fine
  git add of any of them              deny
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def decide(decision: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}}))
    sys.exit(0)


# ---- destructive -----------------------------------------------------------------------------------------------------------------

# A command position: start of line, or after ; & | ( and an optional sudo prefix. Keeps quoted text (commit messages, echo) from matching.
AT = r"(?:^|[;&|(])\s*(?:sudo\s+)?"
ENGINE = r"(?:docker|podman)(?:-compose)?"
DESTRUCTIVE = [
    (re.compile(AT + r"(?:\./|[^\s]*[/\\])?hippocampus\s+reset"), "'hippocampus reset' deletes all stored data"),
    (re.compile(AT + ENGINE + r"\s.*\bdown\b(?:\s.*)?\s(?:-v|--volumes)(?:\s|$)"), "'compose down -v' deletes the database volume"),
    (re.compile(AT + ENGINE + r"\s+(?:volume\s+(?:rm|prune)|system\s+prune)"),
     "removing volumes or pruning the container engine can delete the database"),
]
SQL_CONTEXT = re.compile(r"psql|\b(?:exec|run)\s")
DESTRUCTIVE_SQL = re.compile(r"\b(?:DROP\s+(?:DATABASE|SCHEMA|TABLE)|TRUNCATE\s)", re.I)


def destructive(call: dict) -> None:
    cmd = call.get("tool_input", {}).get("command") or ""
    reason = next((why for pattern, why in DESTRUCTIVE if pattern.search(cmd)), None)
    if reason is None and SQL_CONTEXT.search(cmd) and DESTRUCTIVE_SQL.search(cmd):
        reason = "destructive SQL against a database"
    if reason:
        decide("deny", f"Blocked: {reason}. CLAUDE.md forbids touching a real Hippocampus database unless explicitly asked; "
                       "ask the user to run it themselves.")


# ---- secrets ---------------------------------------------------------------------------------------------------------------------

SANCTIONED = [re.compile(r"--env-file[= ]\S*hippocampus\.env"), re.compile(r"[\w./\\-]*\.env\.(?:example|sample)")]
SECRET_NAME = re.compile(r"token\.env|token\.txt|[\w-]+\.env")
# commands that read or change a file (POSIX and PowerShell), a redirection, or a dot-source
VERB = re.compile(
    r"(?<![\w-])(?:cat|less|more|head|tail|grep|egrep|rg|ag|sed|awk|cut|sort|uniq|tee|cp|mv|rm|ln|source|bat|xxd|od|strings|base64|vim?|nano"
    r"|code|open|python3?|py|node|perl|ruby|jq|diff|wc|dd|type|copy|move|del|gc|gci|Get-Content|Set-Content|Add-Content|Out-File"
    r"|Copy-Item|Move-Item|Remove-Item|Select-String)(?![\w-])|[<>]|(?:^|[;&|])\s*\.\s",
    re.I,
)
CHANGE = re.compile(r"sed\s+-\w*i|tee\s|>|(?<![\w-])(?:mv|cp|rm|del|move|copy|Set-Content|Add-Content|Out-File|Remove-Item)\s", re.I)
GIT_ADD = re.compile(r"(?:^|[;&|])\s*git\s+add(?:\s|$)")
PRINTS_TOKEN = re.compile(r"(?:echo|printf|cat|Write-Output|Write-Host)[^;&|]*(?:\$\{?HIPPO_TOKEN|\$env:HIPPO_TOKEN|%HIPPO_TOKEN%)", re.I)
DUMPS_ENV = re.compile(r"^\s*(?:env|printenv|set|Get-ChildItem\s+env:)\s*(?:\||$)", re.I)


def kind(path: str) -> str:
    """'token' for a file that holds tokens or other secrets, 'config' for hippocampus.env, '' for anything else."""
    base = re.split(r"[\\/]", path)[-1]
    if base.endswith((".example", ".env.sample")):
        return ""
    if base == "hippocampus.env":
        return "config"
    return "token" if base in ("token.env", "token.txt") or base.endswith(".env") else ""


def secrets(call: dict) -> None:
    tool, args = call.get("tool_name", ""), call.get("tool_input", {})
    if tool in ("Read", "Write", "Edit", "NotebookEdit"):
        k = kind(args.get("file_path") or args.get("notebook_path") or "")
        if k == "token":
            decide("deny", "This file holds tokens. Save them with scripts/token_env.py capture and hand them to commands with "
                           "token_env.py run or --token-var; never read or write the file directly.")
        if k == "config" and tool != "Read":
            decide("ask", "hippocampus.env is the user's local configuration. Confirm before changing it.")
        return
    if tool != "Bash":
        return

    clean = args.get("command") or ""
    for pattern in SANCTIONED:  # the sanctioned uses of the config file and the example files are not accesses
        clean = pattern.sub("", clean)
    ask = ""
    for name in sorted(set(SECRET_NAME.findall(clean))):
        k = kind(name)
        if k and GIT_ADD.search(clean):
            decide("deny", f"{name} must never be committed (the repository is public). Do not git add it.")
        if k == "token" and VERB.search(clean):
            decide("deny", f"{name} holds tokens. Use scripts/token_env.py (capture, list, run) or a script's --token-var "
                           "instead of reading or writing it.")
        if k == "config" and CHANGE.search(clean):
            ask = "hippocampus.env is the user's local configuration. Confirm before changing it."  # a deny for another name still wins
    if PRINTS_TOKEN.search(clean) or DUMPS_ENV.search(clean):
        decide("deny", "This would print a token. Tokens stay in the environment of the command that needs them "
                       "(scripts/token_env.py run).")
    if ask:
        decide("ask", ask)


# ---- lint ------------------------------------------------------------------------------------------------------------------------

def venv_tool(name: str) -> str | None:
    """A tool from the project's virtualenv (bin on POSIX, Scripts on Windows), or None when it is not installed."""
    return shutil.which(name, path=str(ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")))


def lint(call: dict) -> None:
    args = call.get("tool_input", {})
    path = Path(args.get("file_path") or call.get("tool_response", {}).get("filePath") or "")
    if not path.is_file():
        return
    try:
        rel = path.resolve().relative_to(ROOT)
    except ValueError:
        return  # outside the repository
    if rel.parts[0] in (".venv", "ideation", "notes", "backlog", "node_modules"):
        return
    if rel.suffix == ".py":
        state = load_state(call)
        state["touched"] = sorted({*state.get("touched", []), rel.as_posix()})
        save_state(call, state)
    # Report only: ruff --fix would delete an import that an edit adds before the code that uses it. Markdown (npx) and mypy (whole
    # package) are left to scripts/lint.sh.
    if rel.suffix == ".py":
        cmd = [venv_tool("ruff"), "check", "--no-fix", "--output-format", "concise", str(rel)]
    elif rel.suffix == ".sh" or rel.name == "hippocampus":
        cmd = [venv_tool("shellcheck"), "-x", str(rel)]
    elif rel.suffix in (".yml", ".yaml"):
        cmd = [venv_tool("yamllint"), "-c", ".yamllint.yml", str(rel)]
    else:
        return
    if cmd[0] is None:
        return  # linter not installed: stay silent, scripts/lint.sh reports it
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=25, check=False)
    if done.returncode:
        sys.stderr.write(f"Lint problems in {rel.as_posix()} (fix them before moving on):\n{done.stdout}{done.stderr}\n")
        sys.exit(2)


# ---- stop ------------------------------------------------------------------------------------------------------------------------

def state_path(call: dict) -> Path:
    session = re.sub(r"\W", "", str(call.get("session_id") or "default"))
    return Path(tempfile.gettempdir()) / f"hippocampus-hooks-{session}.json"


def load_state(call: dict) -> dict:
    try:
        return json.loads(state_path(call).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(call: dict, state: dict) -> None:
    try:
        state_path(call).write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass  # no state: the stop check simply has nothing to do


# HIPPO_HOOK_STOP: the unit tests run this hook's own tests, so do not recurse. HIPPO_REPORT=0: a hook run must not write a report folder or
# move tests/reports/latest, which has to keep pointing at the last run the user asked for (/test, scripts/check.py).
STOP_ENV = {"HIPPO_HOOK_STOP": "1", "HIPPO_REPORT": "0"}


def stop_steps(files: list[Path]) -> list[tuple[str, list[str]]]:
    """The checks the stop hook runs, in order: ruff on the edited files, then the unit tests unless HIPPO_HOOK_STOP_TESTS=0."""
    steps: list[tuple[str, list[str]]] = []
    ruff, python = venv_tool("ruff"), venv_tool("python")
    if ruff:
        steps.append(("ruff", [ruff, "check", "--no-fix", "--output-format", "concise", *[f.relative_to(ROOT).as_posix() for f in files]]))
    if python and os.environ.get("HIPPO_HOOK_STOP_TESTS") != "0":  # =0 for red-first TDD: ruff still runs, a red test may end the turn
        steps.append(("unit tests", [python, "-m", "pytest", "tests/unit", "-q", "-x", "--no-header"]))
    return steps


def stop(call: dict) -> None:
    """Ruff the Python files edited in this session, then run the unit tests; exit 2 with the findings so Claude fixes them first.

    Only files the lint hook saw being edited count, so a question-and-answer turn costs nothing and a tree that was already dirty does not
    trigger it. A fingerprint (path, mtime, size) of what was last checked stops a failure that Claude cannot fix from looping: the same
    files are not checked twice, and any further edit is. HIPPO_HOOK_STOP_TESTS=0 (for example under "env" in .claude/settings.local.json)
    skips the test run so a turn may end at red; ruff still runs.
    """
    if os.environ.get("HIPPO_HOOK_STOP"):
        return  # the unit tests run this hook's own tests: do not recurse
    state = load_state(call)
    files = [ROOT / p for p in state.get("touched", []) if (ROOT / p).is_file()]
    fingerprint = [[f.relative_to(ROOT).as_posix(), f.stat().st_mtime_ns, f.stat().st_size] for f in files]
    if not files or state.get("checked") == fingerprint:
        return
    state["checked"] = fingerprint
    save_state(call, state)

    env = {**os.environ, **STOP_ENV}
    for label, cmd in stop_steps(files):  # ruff first: fix the lint before spending time on the tests
        try:
            done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=100, env=env, check=False)
        except subprocess.TimeoutExpired:
            return  # too slow for a hook: scripts/check.sh is the full gate
        if done.returncode:
            tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-40:])
            sys.stderr.write(f"The {label} failed after your edits (fix this before finishing, or say why it cannot be fixed):\n{tail}\n")
            sys.exit(2)


HOOKS = {"destructive": destructive, "secrets": secrets, "lint": lint, "stop": stop}


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[1] not in HOOKS:
        sys.exit(f"usage: hooks.py {'|'.join(HOOKS)}  (tool call as JSON on standard input)")
    try:
        call = json.load(io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8"))
    except ValueError:
        return  # not a tool call we can read: let it through
    HOOKS[argv[1]](call)


if __name__ == "__main__":
    main(sys.argv)
