#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"

STRICT_COVERAGE_GATE=0
if [ "${1:-}" = "--strict-coverage-gate" ]; then
  STRICT_COVERAGE_GATE=1
  shift
fi
if [ "$#" -ne 0 ]; then
  echo "usage: ./scripts/check_quality.sh [--strict-coverage-gate]" >&2
  exit 2
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$ROOT_DIR/src:$PYTHONPATH"
else
  export PYTHONPATH="$ROOT_DIR/src"
fi

cd "$ROOT_DIR"

"$PYTHON_BIN" -m pytest tests/test_regression_golden.py -q
"$PYTHON_BIN" -m pytest tests/

if [ "$STRICT_COVERAGE_GATE" -eq 1 ]; then
  "$PYTHON_BIN" -m penny_stock_radar.quality_gates --strict-coverage-gate
else
  "$PYTHON_BIN" -m penny_stock_radar.quality_gates
fi
