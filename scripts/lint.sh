#!/usr/bin/env bash
# Lints everything in the repository: Python (ruff), Python types (mypy), shell scripts (shellcheck), YAML (yamllint) and Markdown (markdownlint-cli2).
# scripts/check.sh runs this too, and so do both pipelines.
#
#   scripts/lint.sh            run every linter; a linter that is not installed is SKIPPED with an install hint
#   scripts/lint.sh --fix      let ruff fix what it safely can (import order, unused imports, ...), then lint
#   scripts/lint.sh --ci       strict: a missing linter is a failure, nothing may be skipped
#   scripts/lint.sh --skip-md  leave out the Markdown linter (it needs node; the CI test image has none, so CI lints Markdown in its own step)
#   scripts/lint.sh --only md  run one linter only: py, types, sh, yaml or md
#   scripts/lint.sh --changed [REF]  lint only the files that differ from REF (default origin/main; the merge base, so it matches a pull request),
#                              including uncommitted ones: a fast early gate. A linter with no changed file of its type is skipped, not failed
#
# Tools: ruff, mypy, yamllint and shellcheck come with `pip install -e ".[dev]"` (they live in the virtualenv's bin folder);
# markdownlint-cli2 is fetched on demand by npx (needs node and network access once). Configuration: pyproject.toml ([tool.ruff]),
# [tool.mypy] (the strict module list lives there), .yamllint.yml, .markdownlint-cli2.jsonc. Exit status 0 = clean.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

FIX=0; STRICT=0; SKIPMD=0; ONLY=; CHANGED=0; BASE=origin/main
while (( $# )); do
  a=$1; shift
  case "$a" in
    --fix) FIX=1 ;; --ci) STRICT=1 ;; --skip-md) SKIPMD=1 ;;
    --only) ONLY=${1:-}; shift || true ;;
    --changed) CHANGED=1; if [[ ${1:-} && $1 != -* ]]; then BASE=$1; shift; fi ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option $a (see --help)" >&2; exit 2 ;;
  esac
done

MARKDOWNLINT_VERSION=0.24.0   # pinned so CI and your machine agree; bump deliberately
PY=${PY:-}
if [[ -z $PY ]]; then if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY=python3; fi; fi
BIN=$(dirname "$(command -v "$PY" 2>/dev/null || echo "$PY")")

# tool NAME: prefer the virtualenv's copy, else whatever is on the PATH
tool() { if [[ -x $BIN/$1 ]]; then echo "$BIN/$1"; else command -v "$1" 2>/dev/null || true; fi; }

# --changed: the files that differ from the merge base with BASE (added, copied, modified, renamed), plus untracked ones
FILES=()
if (( CHANGED )); then
  mb=$(git merge-base "$BASE" HEAD 2>/dev/null) || { echo "cannot find a merge base with '$BASE' (is the history fetched?)" >&2; exit 2; }
  mapfile -t FILES < <({ git diff --name-only --diff-filter=ACMR "$mb"; git ls-files --others --exclude-standard; } | sort -u)
  echo "lint: ${#FILES[@]} changed file(s) since $BASE"
fi
# sel REGEX...: the changed files matching the pattern, excluding the folders the linters never cover
sel() { local f; for f in "${FILES[@]}"; do [[ -f $f && $f =~ $1 && ! $f =~ ^(ideation|notes|backlog|\.venv|node_modules)/ ]] && echo "$f"; done; return 0; }

PASS=(); FAIL=(); SKIP=()
run() { # run "label" "tool name" "install hint" command...   (the command gets the resolved tool as $1)
  local label=$1 name=$2 hint=$3; shift 3
  local exe; exe=$(tool "$name")
  echo; echo "==> $label"
  if [[ -z $exe ]]; then
    if (( STRICT )); then echo "   $name is not installed ($hint)" >&2; FAIL+=("$label (tool missing)"); else echo "   $name is not installed: skipped ($hint)"; SKIP+=("$label"); fi
    return
  fi
  if "$@" "$exe"; then PASS+=("$label"); else FAIL+=("$label"); fi
}

py_lint()    {
  local exe=$1 t=(src tests scripts)
  (( CHANGED )) && mapfile -t t < <(sel '\.py$')
  if (( FIX )); then "$exe" check --fix "${t[@]}"; else "$exe" check "${t[@]}"; fi
}
type_lint()  { "$1"; }   # mypy reads its files and strictness from pyproject.toml and always checks the whole package
sh_lint()    {
  local exe=$1 t=(hippocampus scripts/*.sh)
  (( CHANGED )) && mapfile -t t < <(sel '(^hippocampus$|\.sh$)')
  "$exe" -x "${t[@]}"
}
yaml_lint()  {
  local exe=$1 files=()
  if (( CHANGED )); then mapfile -t files < <(sel '\.ya?ml$'); "$exe" -c .yamllint.yml "${files[@]}"; return; fi
  for f in compose.yaml deploy/podman-compose.dev.yml export/github-ci.yml; do [[ -f $f ]] && files+=("$f"); done
  for d in .forgejo .github; do [[ -d $d ]] && files+=("$d"); done
  (( ${#files[@]} )) || { echo "   no YAML files"; return 0; }
  "$exe" -c .yamllint.yml "${files[@]}"
}
md_lint()    {
  local exe=$1 t=("**/*.md")
  (( CHANGED )) && mapfile -t t < <(sel '\.md$')
  "$exe" -y "markdownlint-cli2@$MARKDOWNLINT_VERSION" "${t[@]}"
}

want() { [[ -z $ONLY || $ONLY == "$1" ]]; }
# lint KEY REGEX "label" tool hint fn: runs one linter, or notes that --changed left it nothing to do
lint() {
  local key=$1 re=$2; shift 2
  want "$key" || return 0
  if (( CHANGED )) && [[ -z $(sel "$re") ]]; then SKIP+=("$1 (no changed files)"); return 0; fi
  run "$@"
}
lint py   '\.py$'               "Python (ruff)"              ruff       'pip install -e ".[dev]"'  py_lint
lint types '\.py$'             "Python types (mypy)"        mypy       'pip install -e ".[dev]"'  type_lint
lint sh   '(^hippocampus$|\.sh$)' "Shell scripts (shellcheck)" shellcheck 'pip install -e ".[dev]"'  sh_lint
lint yaml '\.ya?ml$'            "YAML (yamllint)"            yamllint   'pip install -e ".[dev]"'  yaml_lint
if want md; then
  if (( SKIPMD )); then SKIP+=("Markdown (skipped by --skip-md)"); else lint md '\.md$' "Markdown (markdownlint-cli2)" npx 'install node (https://nodejs.org)' md_lint; fi
fi

echo; echo "================ lint summary ================"
for s in "${PASS[@]}"; do echo "  PASS  $s"; done
for s in "${SKIP[@]}"; do echo "  SKIP  $s"; done
for s in "${FAIL[@]}"; do echo "  FAIL  $s"; done
if (( ${#FAIL[@]} )); then echo "LINT: FAILED"; exit 1; fi
if (( ${#PASS[@]} == 0 )); then echo "LINT: nothing ran (every linter was skipped)"; exit 0; fi
echo "LINT: clean"
