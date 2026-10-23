#!/usr/bin/env bash
# Runs every check for Hippocampus in one command. This is what CI runs too (.github/workflows/ci.yml), so a green run here
# means a green run there. Nothing here touches a live system.
#
#   scripts/check.sh            full run: starts the local dev database if needed (podman), then
#                               1. migrations apply to a throwaway database from zero and the schema is as the code expects
#                               2. the whole test suite, including the database tests
#                               3. the dashboard JavaScript parses (if node is installed)
#   --skip-js                   leave out the JavaScript check (used by the CI image, which has no node)
#   scripts/check.sh --no-db    unit tests only (no database): quick, but the database tests and migration check are SKIPPED
#                               and the summary says so
#   scripts/check.sh --ci       for CI: HIPPO_TEST_DSN must point at an empty pgvector database, and node must exist; the database,
#                               migration and JavaScript checks may not be skipped (two optional tests, the browser crawl and the
#                               model download, still skip unless their tools are installed)
#
# Env: PY (python to use; default .venv/bin/python, else python3), HIPPO_TEST_DSN (use your own database instead of the dev one).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

MODE=full
SKIPJS=
for a in "$@"; do
  case "$a" in
    --no-db) MODE=nodb ;;
    --ci) MODE=ci ;;
    --skip-js) SKIPJS=1 ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option $a (see --help)" >&2; exit 2 ;;
  esac
done

PY=${PY:-}
if [[ -z $PY ]]; then if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY=python3; fi; fi
DEV_DSN=postgresql://hippocampus:hippocampus_dev_password@127.0.0.1:5442/hippocampus_dev

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
      if command -v podman >/dev/null 2>&1; then
        echo "==> starting the local dev database (podman)"
        if scripts/dev-db.sh up >/dev/null && scripts/dev-db.sh migrate >/dev/null; then export HIPPO_TEST_DSN=$DEV_DSN
        else FAIL+=("start the local dev database"); fi
      else
        echo "podman not found: database checks cannot run (set HIPPO_TEST_DSN to your own pgvector database, or use --no-db)" >&2
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

step "test suite" "$PY" -m pytest -q
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
