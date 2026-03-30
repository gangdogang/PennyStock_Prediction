#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "========================================"
echo " Penny Stock Radar Snapshot Launcher"
echo "========================================"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed."
  echo "Please install Python 3 on macOS and try again."
  read -r "?Press Enter to close..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[1/5] Creating virtual environment (.venv)..."
  python3 -m venv .venv
fi

VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
VENV_PIP="$ROOT_DIR/.venv/bin/pip"
DB_PATH="$ROOT_DIR/data/penny_stock_radar.sqlite3"
OUTPUT_PATH="$ROOT_DIR/sample_outputs/radar_dashboard.html"

echo "[2/5] Checking dependencies..."
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
  echo "[3/5] Creating .env from .env.example..."
  cp .env.example .env
fi

echo "[4/5] Checking whether a full refresh is needed..."
if [ -f "$DB_PATH" ] && "$VENV_PYTHON" - "$DB_PATH" <<'PY' >/dev/null 2>&1
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import sys

db_path = sys.argv[1]
connection = sqlite3.connect(db_path)
row = connection.execute(
    "SELECT created_at FROM scan_runs ORDER BY created_at DESC LIMIT 1"
).fetchone()
connection.close()

if row is None or not row[0]:
    raise SystemExit(1)

created_at = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
age_minutes = (datetime.now(timezone.utc) - created_at).total_seconds() / 60
raise SystemExit(0 if age_minutes <= 15 else 1)
PY
then
  echo "Recent data found in the last 15 minutes. Skipping startup refresh."
else
  echo "Running the full pipeline. This can take a little while."
  ./scripts/run_full_pipeline.sh
fi

echo "[5/5] Exporting and opening snapshot dashboard..."
echo ""
echo "Snapshot file:"
echo "$OUTPUT_PATH"
echo ""

PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
  exec "$VENV_PYTHON" -m penny_stock_radar snapshot-dashboard --output-path "$OUTPUT_PATH"
