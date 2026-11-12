#!/usr/bin/env bash
# Local quality gate: format, lint, types, tests with coverage.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-.venv/bin}
$PY/ruff format --check . >/dev/null || { $PY/ruff format --check .; exit 1; }
$PY/ruff check . --output-format concise
$PY/mypy app
$PY/pytest -q -x "$@"
