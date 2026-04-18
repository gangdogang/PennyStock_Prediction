#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
STRICT_COVERAGE_GATE=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --strict-coverage-gate)
      STRICT_COVERAGE_GATE=1
      shift
      ;;
    *)
      echo "Usage: ./scripts/check_quality.sh [--strict-coverage-gate]" >&2
      exit 1
      ;;
  esac
done

cd "$ROOT_DIR"

if [ -x "$VENV_PYTHON" ]; then
  PYTHON_BIN="$VENV_PYTHON"
else
  PYTHON_BIN="python3"
fi

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

"$PYTHON_BIN" -m pytest tests/test_regression_golden.py -q
"$PYTHON_BIN" -m pytest tests/

if [ "$STRICT_COVERAGE_GATE" -eq 1 ]; then
  "$PYTHON_BIN" -m penny_stock_radar.quality_gates --strict-coverage-gate
else
  "$PYTHON_BIN" -m penny_stock_radar.quality_gates
fi
