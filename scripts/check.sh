#!/usr/bin/env bash
# Runs every check for Hippocampus in one command. This is what CI runs too (.github/workflows/ci.yml), so a green run here
# means a green run there. Nothing here touches a live system.
#
#   scripts/check.sh            full run: starts a throwaway test database in a container (docker or podman) and removes it at the end, then
#                               1. migrations apply to a throwaway database from zero and the schema is as the code expects
#                               2. the whole test suite, including the database tests
#                               3. the dashboard JavaScript parses (if node is installed)
#   --skip-js                   leave out the JavaScript check (used by the CI image, which has no node)
#   --skip-md                   leave out the Markdown linter (same reason; CI lints Markdown in its own step)
#   --no-lint                   leave out the linters (ruff, shellcheck, yamllint, markdownlint); see scripts/lint.sh
#   --all                       run unit, integration and end-to-end tests in ONE pytest run, so they share one report
#                               (tests/reports/<timestamp>/). The end-to-end part needs Docker or Podman compose, like --e2e below;
#                               without it that part is SKIPPED, or a FAILURE with --ci, and the rest still runs
#   --e2e                       also run tests/e2e/ as a second, separate run after the test suite (its own report): it starts its own throwaway stack with Docker or Podman
#                               compose (never your running instance) and removes it afterwards. Without a container engine
#                               it is SKIPPED, or a FAILURE with --ci. HIPPO_E2E_BUILD=1 rebuilds the image first
#   scripts/check.sh --no-db    unit tests only (no database): quick, but the database tests and migration check are SKIPPED
#                               and the summary says so
#   scripts/check.sh --ci       for CI: HIPPO_TEST_DSN must point at an empty pgvector database, and node must exist; the database,
#                               migration and JavaScript checks may not be skipped (two optional tests, the browser crawl and the
#                               model download, still skip unless their tools are installed)
#
# Env: PY (python to use; default .venv/bin/python, else python3), HIPPO_TEST_DSN (use your own disposable database instead of the throwaway one;
# its name must contain 'test' or 'dev').
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

MODE=full
SKIPJS=; SKIPMD=; NOLINT=; E2E=; ALL=
for a in "$@"; do
  case "$a" in
    --no-db) MODE=nodb ;;
    --ci) MODE=ci ;;
    --skip-js) SKIPJS=1 ;;
    --skip-md) SKIPMD=1 ;;
    --no-lint) NOLINT=1 ;;
    --e2e) E2E=1 ;;
    --all) ALL=1 ;;
    -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option $a (see --help)" >&2; exit 2 ;;
  esac
done

PY=${PY:-}
if [[ -z $PY ]]; then if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY=python3; fi; fi

PASS=(); FAIL=(); SKIP=()
step() { # step "name" command...
  local name=$1; shift
  echo; echo "==> $name"
  if "$@"; then PASS+=("$name"); else FAIL+=("$name"); fi
}

case $MODE in
  ci)
    [[ -n ${HIPPO_TEST_DSN:-} ]] || { echo "--ci needs HIPPO_TEST_DSN (an empty pgvector database)" >&2; exit 2; }
    step "apply migrations to the test database" "$PY" scripts/migrate_check.py apply ;;
  full)
    if [[ -z ${HIPPO_TEST_DSN:-} ]]; then
      echo "==> starting a throwaway test database (docker or podman)"
      if out=$("$PY" scripts/test_db.py up); then
        read -r TESTDB_NAME HIPPO_TEST_DSN <<<"$out"; export HIPPO_TEST_DSN
        trap '"$PY" scripts/test_db.py down "$TESTDB_NAME"' EXIT
      else
        echo "no throwaway database: install docker or podman, set HIPPO_TEST_DSN to a disposable pgvector database, or use --no-db" >&2
        FAIL+=("database available")
      fi
    fi ;;
  nodb) unset HIPPO_TEST_DSN ;;
esac

if [[ -n ${HIPPO_TEST_DSN:-} ]]; then
  step "migrations apply from zero and the schema is as expected" "$PY" scripts/migrate_check.py scratch
else
  SKIP+=("migrations from zero (no database)" "database tests (no database)")
fi

if [[ -n $NOLINT ]]; then
  SKIP+=("linters (--no-lint)")
else
  LINT_ARGS=(); [[ $MODE == ci ]] && LINT_ARGS+=(--ci); [[ -n $SKIPMD ]] && LINT_ARGS+=(--skip-md)
  PY="$PY" step "lint (ruff, shellcheck, yamllint, markdownlint)" scripts/lint.sh "${LINT_ARGS[@]}"
fi

ENGINE=
if [[ -n $E2E || -n $ALL ]]; then
  for e in ${CONTAINER_ENGINE:-docker podman}; do
    if command -v "$e" >/dev/null 2>&1 && "$e" compose version >/dev/null 2>&1; then ENGINE=$e; break; fi
  done
fi
NOENGINE="end-to-end tests: no 'docker compose' or 'podman compose' found"

if [[ -n $ALL && -n $ENGINE ]]; then
  step "test suite: unit, integration and end-to-end ($ENGINE compose), one report" "$PY" -m pytest -q tests/unit tests/integration tests/e2e
else
  step "test suite" "$PY" -m pytest -q
  if [[ -n $ALL ]]; then
    [[ $MODE == ci ]] && FAIL+=("$NOENGINE") || SKIP+=("$NOENGINE")
  elif [[ -n $E2E ]]; then
    if [[ -n $ENGINE ]]; then
      step "end-to-end tests ($ENGINE compose)" "$PY" -m pytest tests/e2e -q
    elif [[ $MODE == ci ]]; then
      FAIL+=("$NOENGINE")
    else
      SKIP+=("$NOENGINE")
    fi
  fi
fi
if [[ -n $SKIPJS ]]; then
  SKIP+=("dashboard JavaScript (checked in its own CI step)")
elif command -v node >/dev/null 2>&1; then
  step "dashboard JavaScript parses" node --check src/hippocampus/ui/app.js
else
  [[ $MODE == ci ]] && FAIL+=("node is required in CI for the JavaScript check") || SKIP+=("dashboard JavaScript (node not installed)")
fi

echo; echo "================ summary ================"
for s in "${PASS[@]}"; do echo "  PASS  $s"; done
for s in "${SKIP[@]}"; do echo "  SKIP  $s"; done
for s in "${FAIL[@]}"; do echo "  FAIL  $s"; done
if (( ${#FAIL[@]} )); then echo "RESULT: FAILED"; exit 1; fi
if (( ${#SKIP[@]} )); then echo "RESULT: passed (some checks were skipped, listed above)"; exit 0; fi
echo "RESULT: all checks passed"
