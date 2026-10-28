#!/usr/bin/env python3
"""Lints everything in the repository: Python (ruff), Python types (mypy), shell scripts (shellcheck), YAML (yamllint) and Markdown (markdownlint-cli2).
scripts/check.py runs this too, and so do both pipelines. Plain Python, so it runs the same on macOS, Linux and Windows; scripts/lint.sh just starts it.

  scripts/lint.py            run every linter; a linter that is not installed is SKIPPED with an install hint
  scripts/lint.py --fix      let ruff fix what it safely can (import order, unused imports, ...), then lint
  scripts/lint.py --ci       strict: a missing linter is a failure, nothing may be skipped
  scripts/lint.py --skip-md  leave out the Markdown linter (it needs node; the CI test image has none, so CI lints Markdown in its own step)
  scripts/lint.py --only md  run one linter only: py, types, sh, yaml or md
  scripts/lint.py --changed [REF]  lint only the files that differ from REF (default origin/main; the merge base, so it matches a pull request),
                             including uncommitted ones: a fast early gate. A linter with no changed file of its type is skipped, not failed

Tools: ruff, mypy, yamllint and shellcheck come with `pip install -e ".[dev]"` (they live in the virtualenv's bin or Scripts folder);
markdownlint-cli2 is fetched on demand by npx (needs node and network access once). Configuration: pyproject.toml ([tool.ruff]),
[tool.mypy] (the strict module list lives there), .yamllint.yml, .markdownlint-cli2.jsonc. Exit status 0 = clean.
Env: PY (python whose folder holds the tools; default .venv, else the python running this script).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKDOWNLINT_VERSION = "0.24.0"  # pinned so CI and your machine agree; bump deliberately
SKIP_DIRS = ("ideation", "notes", "backlog", ".venv", "node_modules")  # folders the linters never cover


def venv_python() -> str:
    """The python to use: $PY, else the project's .venv (bin on POSIX, Scripts on Windows), else the one running this script."""
    if os.environ.get("PY"):
        return os.environ["PY"]
    for sub, name in (("bin", "python"), ("Scripts", "python.exe")):
        candidate = ROOT / ".venv" / sub / name
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def tool(name: str, python: str) -> str | None:
    """A tool from the same folder as the python (the virtualenv's), else whatever is on the PATH."""
    folder = Path(shutil.which(python) or python).parent
    found = shutil.which(name, path=str(folder))
    return found or shutil.which(name)


def git_lines(*args: str) -> list[str]:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError(done.stderr.strip())
    return done.stdout.splitlines()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--skip-md", action="store_true")
    parser.add_argument("--only", choices=["py", "types", "sh", "yaml", "md"])
    parser.add_argument("--changed", nargs="?", const="origin/main", default=None, metavar="REF")
    args = parser.parse_args(argv)

    python = venv_python()
    changed: list[str] = []
    if args.changed is not None:
        try:
            base = git_lines("merge-base", args.changed, "HEAD")[0]
            names = git_lines("diff", "--name-only", "--diff-filter=ACMR", base) + git_lines("ls-files", "--others", "--exclude-standard")
        except (RuntimeError, IndexError):
            print(f"cannot find a merge base with '{args.changed}' (is the history fetched?)", file=sys.stderr)
            return 2
        changed = sorted(set(names))
        print(f"lint: {len(changed)} changed file(s) since {args.changed}")

    def sel(pattern: str) -> list[str]:
        """The changed files matching the pattern, excluding the folders the linters never cover."""
        return [f for f in changed if (ROOT / f).is_file() and re.search(pattern, f) and f.split("/")[0] not in SKIP_DIRS]

    def run_cmd(cmd: list[str]) -> bool:
        return subprocess.run(cmd, cwd=ROOT, check=False).returncode == 0

    def py_lint(exe: str) -> bool:
        targets = sel(r"\.py$") if args.changed is not None else ["src", "tests", "scripts"]
        return run_cmd([exe, "check", *(["--fix"] if args.fix else []), *targets])

    def type_lint(exe: str) -> bool:
        return run_cmd([exe])  # mypy reads its files and strictness from pyproject.toml and always checks the whole package

    def sh_lint(exe: str) -> bool:
        if args.changed is not None:
            targets = sel(r"(^hippocampus$|\.sh$)")
        else:
            targets = ["hippocampus", *sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "scripts").glob("*.sh"))]
        return run_cmd([exe, "-x", *targets])

    def yaml_lint(exe: str) -> bool:
        if args.changed is not None:
            return run_cmd([exe, "-c", ".yamllint.yml", *sel(r"\.ya?ml$")])
        files = [f for f in ("compose.yaml", "deploy/podman-compose.dev.yml", "export/github-ci.yml") if (ROOT / f).is_file()]
        files += [d for d in (".forgejo", ".github") if (ROOT / d).is_dir()]
        if not files:
            print("   no YAML files")
            return True
        return run_cmd([exe, "-c", ".yamllint.yml", *files])

    def md_lint(exe: str) -> bool:
        targets = sel(r"\.md$") if args.changed is not None else ["**/*.md"]
        return run_cmd([exe, "-y", f"markdownlint-cli2@{MARKDOWNLINT_VERSION}", *targets])

    passed: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []

    def run(label: str, name: str, hint: str, fn: Callable[[str], bool]) -> None:
        """Run one linter (the command gets the resolved tool), or note that it is not installed."""
        exe = tool(name, python)
        print(f"\n==> {label}", flush=True)
        if exe is None:
            if args.ci:
                print(f"   {name} is not installed ({hint})", file=sys.stderr)
                failed.append(f"{label} (tool missing)")
            else:
                print(f"   {name} is not installed: skipped ({hint})")
                skipped.append(label)
            return
        (passed if fn(exe) else failed).append(label)

    def lint(key: str, pattern: str, label: str, name: str, hint: str, fn: Callable[[str], bool]) -> None:
        """Run one linter, or note that --changed left it nothing to do."""
        if args.only not in (None, key):
            return
        if args.changed is not None and not sel(pattern):
            skipped.append(f"{label} (no changed files)")
            return
        run(label, name, hint, fn)

    dev = 'pip install -e ".[dev]"'
    lint("py", r"\.py$", "Python (ruff)", "ruff", dev, py_lint)
    lint("types", r"\.py$", "Python types (mypy)", "mypy", dev, type_lint)
    lint("sh", r"(^hippocampus$|\.sh$)", "Shell scripts (shellcheck)", "shellcheck", dev, sh_lint)
    lint("yaml", r"\.ya?ml$", "YAML (yamllint)", "yamllint", dev, yaml_lint)
    if args.only in (None, "md"):
        if args.skip_md:
            skipped.append("Markdown (skipped by --skip-md)")
        else:
            lint("md", r"\.md$", "Markdown (markdownlint-cli2)", "npx", "install node (https://nodejs.org)", md_lint)

    print("\n================ lint summary ================")
    for label in passed:
        print(f"  PASS  {label}")
    for label in skipped:
        print(f"  SKIP  {label}")
    for label in failed:
        print(f"  FAIL  {label}")
    if failed:
        print("LINT: FAILED")
        return 1
    if not passed:
        print("LINT: nothing ran (every linter was skipped)")
        return 0
    print("LINT: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
