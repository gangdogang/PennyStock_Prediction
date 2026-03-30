#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "========================================"
echo " Penny Stock Radar Paper Trader"
echo "========================================"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed."
  echo "Please install Python 3 on macOS and try again."
  read -r "?Press Enter to close..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[1/4] Creating virtual environment (.venv)..."
  python3 -m venv .venv
fi

VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
VENV_PIP="$ROOT_DIR/.venv/bin/pip"

echo "[2/4] Checking dependencies..."
if "$VENV_PYTHON" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = [
    "typer",
    "pandas",
    "pydantic",
    "pydantic_settings",
    "httpx",
    "rich",
    "yfinance",
]

missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(1 if missing else 0)
PY
then
  echo "Dependencies are already installed. Skipping package install."
else
  echo "Installing required packages. This can take 1-3 minutes on the first run."
  PIP_DISABLE_PIP_VERSION_CHECK=1 "$VENV_PIP" install -U pip
  PIP_DISABLE_PIP_VERSION_CHECK=1 "$VENV_PIP" install -e '.[dev]'
fi

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  echo "[3/4] Creating .env from .env.example..."
  cp .env.example .env
fi

echo "[4/4] Starting automated paper trader..."
echo ""
echo "Outputs:"
echo "  csv logs -> $ROOT_DIR/sample_outputs/paper_trading"
echo ""
echo "Default loop: 60-second polling"
echo "Press Ctrl+C in this terminal window to stop the paper trader."
echo ""

PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
  exec "$VENV_PYTHON" -m penny_stock_radar paper-trader --check-interval-seconds 60
