"""scripts/prereqs.sh: the safety behaviour (the acknowledgement gate, y/N per item, nothing runs unasked).

The script is run for real, in a minimal PATH made of symlinks plus a stub ``sudo`` and ``apt-get`` that only log what they are asked
to do, so no test can install anything.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prereqs.sh"
PHRASE = "I HAVE READ THIS SCRIPT AND UNDERSTAND THE RISK"

pytestmark = pytest.mark.skipif(sys.platform != "linux" or shutil.which("bash") is None, reason="runs the Linux (apt) branch of the script")

TOOLS = ("bash", "sh", "uname", "grep", "sed", "awk", "sort", "head", "tr", "df", "printf", "cat", "dirname", "basename",
         "wc", "mkdir", "date", "env", "test", "cut", "ls", "id", "tail", "rm")


@pytest.fixture
def bare(tmp_path):
    """A PATH with the basic tools and a logging stub for sudo/apt-get: a machine with no container engine, curl, git or python."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in TOOLS:
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    log = tmp_path / "sudo.log"
    for name in ("sudo", "apt-get"):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 0\n')
        stub.chmod(0o755)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path), "USER": "tester"}
    return env, log


def run(args, env, stdin=""):
    return subprocess.run(["bash", str(SCRIPT), *args], input=stdin, capture_output=True, text=True, env=env, timeout=60, check=False)


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def test_help_starts_with_the_warning():
    r = run(["--help"], dict(os.environ))
    assert r.returncode == 0
    assert "READ THIS FIRST" in r.stdout and "understand the risk" in r.stdout


def test_unknown_option_is_refused():
    assert run(["--nope"], dict(os.environ)).returncode == 2


def test_checking_changes_nothing_and_needs_no_acknowledgement(bare):
    env, log = bare
    r = run([], env)
    assert r.returncode == 1                                   # a bare machine is not ready
    assert "what needs to be installed" in r.stdout
    assert "Option A: Docker" in r.stdout and "Option B: Podman" in r.stdout   # either engine is offered
    assert "Nothing was installed" in r.stdout
    assert calls(log) == []


@pytest.mark.parametrize("typed", ["", "yes", "i have read this script and understand the risk", PHRASE + " please"])
def test_install_needs_the_exact_phrase(bare, typed):
    env, log = bare
    r = run(["--install"], env, stdin=typed + "\n")
    assert r.returncode == 1
    assert "Not confirmed. Nothing was changed." in r.stdout
    assert calls(log) == []


def test_dry_run_needs_no_acknowledgement_and_runs_nothing(bare):
    env, log = bare
    r = run(["--install", "--dry-run", "--yes"], env)
    assert "Not confirmed" not in r.stdout
    assert "(dry run: not executed)" in r.stdout
    assert calls(log) == []


def test_every_item_is_a_separate_question_and_only_yes_runs(bare):
    env, log = bare
    # phrase; engine: docker? n; podman? y; podman y; podman-compose n; then curl y
    answers = "\n".join([PHRASE, "n", "y", "y", "n", "y"]) + "\n"
    r = run(["--install"], env, stdin=answers)
    assert "Do you want to install docker as your container engine? [y/N]" in r.stdout
    assert "Do you want to install podman as your container engine? [y/N]" in r.stdout
    done = calls(log)
    assert "sudo apt-get install -y podman" in done[0] or "apt-get install -y podman" in done[0]
    assert any(c.endswith("install -y curl") for c in done)
    assert not any("podman-compose" in c for c in done)      # answered n
    assert not any("docker" in c for c in done)              # declined Docker
    assert len(done) == 2                                    # nothing else ran


def test_the_flag_gives_the_acknowledgement_without_typing_it(bare):
    env, log = bare
    r = run(["--install", "--i-understand-the-risk"], env, stdin="n\nn\nn\n")   # decline everything that is asked
    assert "IMPORTANT: READ BEFORE CONTINUING" not in r.stdout
    assert calls(log) == []
