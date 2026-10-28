#!/usr/bin/env sh
# Starts scripts/check.py (the implementation, plain Python, so it also runs on Windows: python scripts/check.py). Kept so that
# `scripts/check.sh`, CI and the docs keep working. All options are described in scripts/check.py --help.
cd "$(dirname "$0")/.." || exit 1
py=${PY:-}
if [ -z "$py" ]; then if [ -x .venv/bin/python ]; then py=.venv/bin/python; else py=python3; fi; fi
exec "$py" scripts/check.py "$@"
