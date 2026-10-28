#!/usr/bin/env python3
"""Runs every check for Hippocampus in one command. This is what CI runs too (.github/workflows/ci.yml), so a green run here
means a green run there. Nothing here touches a live system. Plain Python, so it runs the same on macOS, Linux and Windows; scripts/check.sh
just starts it.

  scripts/check.py            full run: starts a throwaway test database in a container (docker or podman) and removes it at the end, then
                              1. migrations apply to a throwaway database from zero and the schema is as the code expects
                              2. the whole test suite, including the database tests
                              3. the dashboard JavaScript parses and its folder rules match the server's (if node is installed)
  --skip-js                   leave out the JavaScript check (used by the CI image, which has no node)
  --skip-md                   leave out the Markdown linter (same reason; CI lints Markdown in its own step)
  --no-lint                   leave out the linters (ruff, mypy, shellcheck, yamllint, markdownlint); see scripts/lint.py
  --all                       run unit, integration and end-to-end tests in ONE pytest run, so they share one report
                              (tests/reports/<timestamp>/). The end-to-end part needs Docker or Podman compose, like --e2e below;
                              without it that part is SKIPPED, or a FAILURE with --ci, and the rest still runs
  --e2e                       also run tests/e2e/ as a second, separate run after the test suite (its own report): it starts its own throwaway
                              stack with Docker or Podman compose (never your running instance) and removes it afterwards. Without a container
                              engine it is SKIPPED, or a FAILURE with --ci. HIPPO_E2E_BUILD=1 rebuilds the image first
  scripts/check.py --no-db    unit tests only (no database): quick, but the database tests and migration check are SKIPPED
                              and the summary says so
  scripts/check.py --ci       for CI: HIPPO_TEST_DSN must point at an empty pgvector database, and node must exist; the database,
                              migration and JavaScript checks may not be skipped (two optional tests, the browser crawl and the
                              model download, still skip unless their tools are installed)

Env: PY (python to use; default .venv, else the python running this script), HIPPO_TEST_DSN (use your own disposable database instead of the
throwaway one; its name must contain 'test' or 'dev'), CONTAINER_ENGINE (docker or podman; default: the first with a working compose).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def venv_python() -> str:
    """The python to use: $PY, else the project's .venv (bin on POSIX, Scripts on Windows), else the one running this script."""
    if os.environ.get("PY"):
        return os.environ["PY"]
    for sub, name in (("bin", "python"), ("Scripts", "python.exe")):
        candidate = ROOT / ".venv" / sub / name
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def compose_engine() -> str:
    """The first container engine (CONTAINER_ENGINE, else docker then podman) with a working compose and a daemon that answers, or ''.

    `info` is the liveness check: `docker compose version` succeeds with the plugin installed even when Docker is not running."""
    for engine in (os.environ.get("CONTAINER_ENGINE") or "docker podman").split():
        if shutil.which(engine) and all(subprocess.run([engine, *cmd], capture_output=True, check=False).returncode == 0
                                        for cmd in (["compose", "version"], ["info"])):
            return engine
    return ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--skip-js", action="store_true")
    parser.add_argument("--skip-md", action="store_true")
    parser.add_argument("--no-lint", action="store_true")
    parser.add_argument("--e2e", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    ci = args.ci
    mode = "ci" if ci else "nodb" if args.no_db else "full"  # --ci is the stricter, so it wins over --no-db

    py = venv_python()
    passed: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []

    def step(name: str, cmd: list[str], env: dict[str, str] | None = None) -> None:
        print(f"\n==> {name}", flush=True)
        done = subprocess.run(cmd, cwd=ROOT, env={**os.environ, **(env or {})}, check=False)
        (passed if done.returncode == 0 else failed).append(name)

    throwaway: str | None = None  # name of the throwaway database container this run started
    try:
        if mode == "ci":
            if not os.environ.get("HIPPO_TEST_DSN"):
                print("--ci needs HIPPO_TEST_DSN (an empty pgvector database)", file=sys.stderr)
                return 2
            step("apply migrations to the test database", [py, "scripts/migrate_check.py", "apply"])
        elif mode == "full" and not os.environ.get("HIPPO_TEST_DSN"):
            print("==> starting a throwaway test database (docker or podman)", flush=True)
            done = subprocess.run([py, "scripts/test_db.py", "up"], cwd=ROOT, stdout=subprocess.PIPE, text=True, check=False)
            parts = done.stdout.split(None, 1)
            if done.returncode == 0 and len(parts) == 2:
                throwaway = parts[0]
                os.environ["HIPPO_TEST_DSN"] = parts[1].strip()
            else:
                print("no throwaway database: install docker or podman, set HIPPO_TEST_DSN to a disposable pgvector database, or use --no-db",
                      file=sys.stderr)
                failed.append("database available")
        elif mode == "nodb":
            os.environ.pop("HIPPO_TEST_DSN", None)

        if os.environ.get("HIPPO_TEST_DSN"):
            step("migrations apply from zero and the schema is as expected", [py, "scripts/migrate_check.py", "scratch"])
        else:
            skipped += ["migrations from zero (no database)", "database tests (no database)"]

        if args.no_lint:
            skipped.append("linters (--no-lint)")
        else:
            lint_args = [*(["--ci"] if mode == "ci" else []), *(["--skip-md"] if args.skip_md else [])]
            step("lint (ruff, mypy, shellcheck, yamllint, markdownlint)", [py, "scripts/lint.py", *lint_args], env={"PY": py})

        engine = compose_engine() if (args.e2e or args.all) else ""
        if engine:
            os.environ["CONTAINER_ENGINE"] = engine  # the end-to-end tests then use the engine that was just found running
        no_engine = "end-to-end tests: no 'docker compose' or 'podman compose' found"
        if args.all and engine:
            step(f"test suite: unit, integration and end-to-end ({engine} compose), one report",
                 [py, "-m", "pytest", "-q", "tests/unit", "tests/integration", "tests/e2e"])
        else:
            step("test suite", [py, "-m", "pytest", "-q"])
            if args.all:
                (failed if mode == "ci" else skipped).append(no_engine)
            elif args.e2e:
                if engine:
                    step(f"end-to-end tests ({engine} compose)", [py, "-m", "pytest", "tests/e2e", "-q"])
                else:
                    (failed if mode == "ci" else skipped).append(no_engine)

        node = shutil.which("node")
        if args.skip_js:
            skipped.append("dashboard JavaScript (checked in its own CI step)")
        elif node:
            for js in sorted((ROOT / "src/hippocampus/service/ui").glob("*.js")):
                step(f"dashboard JavaScript parses ({js.name})", [node, "--check", str(js)])
            step("folder rules agree with the server (shared vectors)", [node, "tests/js/check_vectors.js"])
        else:
            (failed if mode == "ci" else skipped).append(
                "node is required in CI for the JavaScript check" if mode == "ci" else "dashboard JavaScript (node not installed)")
    finally:
        if throwaway:
            subprocess.run([py, "scripts/test_db.py", "down", throwaway], cwd=ROOT, check=False)

    print("\n================ summary ================")
    for name in passed:
        print(f"  PASS  {name}")
    for name in skipped:
        print(f"  SKIP  {name}")
    for name in failed:
        print(f"  FAIL  {name}")
    if failed:
        print("RESULT: FAILED")
        return 1
    if skipped:
        print("RESULT: passed (some checks were skipped, listed above)")
        return 0
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
