#!/usr/bin/env sh
# Starts scripts/lint.py (the implementation, plain Python, so it also runs on Windows: python scripts/lint.py). Kept so that
# `scripts/lint.sh`, CI and the docs keep working. All options are described in scripts/lint.py --help.
cd "$(dirname "$0")/.." || exit 1
py=${PY:-}
if [ -z "$py" ]; then if [ -x .venv/bin/python ]; then py=.venv/bin/python; else py=python3; fi; fi
exec "$py" scripts/lint.py "$@"
