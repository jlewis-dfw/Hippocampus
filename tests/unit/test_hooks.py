"""The Claude Code hooks in scripts/hooks: each is fed the JSON Claude Code sends and must allow, ask or deny as documented.

The allowed commands are the ones the slash commands in .claude/commands really run, so a change to a command that the hook would block
shows up here.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "scripts" / "hooks" / "hooks.py"


STATE = Path(tempfile.gettempdir()) / "hippocampus-hooks-pytesthooks.json"  # the hooks' per-session state for the session id used here


@pytest.fixture(autouse=True)
def _fresh_state():
    STATE.unlink(missing_ok=True)
    yield
    STATE.unlink(missing_ok=True)


def run(hook: str, tool: str, env: dict[str, str] | None = None, **tool_input: str) -> subprocess.CompletedProcess[str]:
    """Run one hook the way Claude Code does. The recursion guard is removed from the environment: the stop hook sets it for the pytest run it
    starts, and these tests are part of that run."""
    payload = json.dumps({"session_id": "pytest-hooks", "tool_name": tool, "tool_input": tool_input})
    full_env = {**{k: v for k, v in os.environ.items() if k != "HIPPO_HOOK_STOP"}, **(env or {})}
    return subprocess.run([sys.executable, str(HOOKS), hook], input=payload, capture_output=True, text=True, timeout=30, cwd=ROOT, env=full_env,
                          check=False)


def decision(script: str, tool: str, **tool_input: str) -> str:
    proc = run(script, tool, **tool_input)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] if proc.stdout.strip() else "allow"


DESTRUCTIVE_DENIED = [
    "./hippocampus reset",
    "cd /x && ./hippocampus reset",
    "docker compose --env-file hippocampus.env down -v",
    "podman compose down --volumes",
    "docker volume rm hippocampus_pgdata",
    "podman system prune --volumes",
    "docker compose exec postgres psql -U hippocampus -c 'DROP DATABASE hippocampus'",
    "psql -c 'truncate chunks'",
]
DESTRUCTIVE_ALLOWED = [
    "./hippocampus up 2>&1 | scripts/token_env.py capture HIPPO_TOKEN_ADMIN",
    "./hippocampus status",
    "./hippocampus down",
    "docker compose --env-file hippocampus.env exec postgres psql -U hippocampus hippocampus",
    'git commit -m "never run ./hippocampus reset or compose down -v"',
    "echo 'docker volume rm x'",
    "scripts/check.sh --all",
]


@pytest.mark.parametrize("cmd", DESTRUCTIVE_DENIED)
def test_destructive_commands_are_denied(cmd: str) -> None:
    assert decision("destructive", "Bash", command=cmd) == "deny"


@pytest.mark.parametrize("cmd", DESTRUCTIVE_ALLOWED)
def test_everyday_commands_are_not_blocked(cmd: str) -> None:
    assert decision("destructive", "Bash", command=cmd) == "allow"


SECRETS_DENIED_BASH = [
    "cat token.env",
    "grep HIPPO_TOKEN token.env",
    "source token.env",
    ". ./token.env",
    "echo hip_x >> token.env",
    "cp hippocampus.env token.env",
    "git add token.env",
    "git add -f hippocampus.env",
    "git add -f token.env",
    "echo $HIPPO_TOKEN",
    "printenv",
    "env | sort",
]
SECRETS_ASK_BASH = ["sed -i 's/8090/8091/' hippocampus.env", "echo HIPPO_PORT=9000 >> hippocampus.env"]
# lifted from .claude/commands/*.md
SECRETS_ALLOWED_BASH = [
    "scripts/token_env.py list",
    "scripts/token_env.py run HIPPO_TOKEN_ADMIN -- scripts/index_docs.py --plan",
    "./hippocampus admin create-client --name bot --read docs | scripts/token_env.py capture HIPPO_TOKEN_READER",
    "scripts/verify_setup.py --token-var HIPPO_TOKEN_READER --collection hippocampus-docs --query x --mcp",
    "scripts/register_mcp.py HIPPO_TOKEN_READER --dry-run",
    "docker compose --env-file hippocampus.env exec postgres psql -U hippocampus hippocampus",
    "podman compose --env-file hippocampus.env ps",
    "cat hippocampus.env.example",
    "ls -l token.env",
    "git check-ignore token.env",
    "git add -A",
    "env FOO=1 scripts/check.sh",
]


@pytest.mark.parametrize("cmd", SECRETS_DENIED_BASH)
def test_secret_access_in_bash_is_denied(cmd: str) -> None:
    assert decision("secrets", "Bash", command=cmd) == "deny"


@pytest.mark.parametrize("cmd", SECRETS_ASK_BASH)
def test_changing_hippocampus_env_asks(cmd: str) -> None:
    assert decision("secrets", "Bash", command=cmd) == "ask"


@pytest.mark.parametrize("cmd", SECRETS_ALLOWED_BASH)
def test_the_commands_own_invocations_are_allowed(cmd: str) -> None:
    assert decision("secrets", "Bash", command=cmd) == "allow"


@pytest.mark.parametrize(
    ("tool", "path", "expected"),
    [
        ("Read", "/repo/token.env", "deny"),
        ("Read", "/repo/token.txt", "deny"),
        ("Write", "/repo/token.env", "deny"),
        ("Edit", "/repo/other.env", "deny"),
        ("Write", "/repo/hippocampus.env", "ask"),
        ("Edit", "/repo/hippocampus.env", "ask"),
        ("Read", "/repo/hippocampus.env", "allow"),
        ("Write", "/repo/hippocampus.env.example", "allow"),
        ("Edit", "/repo/src/hippocampus/core/chunker.py", "allow"),
    ],
)
def test_file_tools(tool: str, path: str, expected: str) -> None:
    assert decision("secrets", tool, file_path=path) == expected


def test_lint_edit_reports_ruff_findings_and_stays_quiet_when_clean(tmp_path: Path) -> None:
    ruff = ROOT / ".venv" / "bin" / "ruff"
    if not ruff.exists():
        pytest.skip("ruff is not installed in .venv")
    bad = ROOT / "scripts" / "_hook_probe_tmp.py"
    try:
        bad.write_text("import os\n")
        proc = run("lint", "Edit", file_path=str(bad))
        assert proc.returncode == 2 and "F401" in proc.stderr
        bad.write_text("x = 1\n")
        assert run("lint", "Edit", file_path=str(bad)).returncode == 0
    finally:
        bad.unlink(missing_ok=True)
    assert run("lint", "Edit", file_path=str(tmp_path / "missing.py")).returncode == 0
    assert run("lint", "Edit", file_path=str(ROOT / "README.md")).returncode == 0


def test_stop_does_nothing_when_no_python_file_was_edited() -> None:
    proc = run("stop", "")
    assert proc.returncode == 0 and not proc.stdout and not proc.stderr


def test_stop_reports_lint_once_and_rechecks_after_a_further_edit() -> None:
    if not (ROOT / ".venv" / "bin" / "ruff").exists() and not (ROOT / ".venv" / "Scripts" / "ruff.exe").exists():
        pytest.skip("ruff is not installed in .venv")
    bad = ROOT / "scripts" / "_hook_probe_stop_tmp.py"
    try:
        bad.write_text("import os\n")
        run("lint", "Edit", file_path=str(bad))  # the lint hook records the edit (it also exits 2 here, which is fine)
        first = run("stop", "")
        assert first.returncode == 2 and "F401" in first.stderr and "ruff failed" in first.stderr
        assert run("stop", "").returncode == 0  # same files, already reported: no loop
        bad.write_text("import os\nimport sys\n")  # a further edit is checked again
        assert run("stop", "").returncode == 2
    finally:
        bad.unlink(missing_ok=True)


def test_stop_can_skip_the_tests_for_red_first_work(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("hippo_hooks", HOOKS)
    assert spec and spec.loader
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    files = [ROOT / "scripts" / "hooks" / "hooks.py"]
    monkeypatch.delenv("HIPPO_HOOK_STOP_TESTS", raising=False)
    labels = [label for label, _ in hooks.stop_steps(files)]
    if not (ROOT / ".venv").exists():
        pytest.skip("the project virtualenv is not installed")
    assert labels == ["ruff", "unit tests"]
    monkeypatch.setenv("HIPPO_HOOK_STOP_TESTS", "0")
    assert [label for label, _ in hooks.stop_steps(files)] == ["ruff"]  # ruff still runs; only the tests are skipped


def test_stop_runs_pytest_without_writing_a_report() -> None:
    spec = importlib.util.spec_from_file_location("hippo_hooks_env", HOOKS)
    assert spec and spec.loader
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    assert hooks.STOP_ENV["HIPPO_REPORT"] == "0" and hooks.STOP_ENV["HIPPO_HOOK_STOP"] == "1"


def test_stop_does_not_recurse_into_its_own_test_run() -> None:
    bad = ROOT / "scripts" / "_hook_probe_recurse_tmp.py"
    try:
        bad.write_text("import os\n")
        run("lint", "Edit", file_path=str(bad))
        assert run("stop", "", env={"HIPPO_HOOK_STOP": "1"}).returncode == 0  # would be 2 without the guard
    finally:
        bad.unlink(missing_ok=True)
