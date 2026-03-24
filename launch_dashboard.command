#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "========================================"
echo " Penny Stock Radar One-Click Launcher"
echo "========================================"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed."
  echo "Please install Python 3 on macOS and try again."
  read -r "?Press Enter to close..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[1/6] Creating virtual environment (.venv)..."
  python3 -m venv .venv
fi

VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
VENV_PIP="$ROOT_DIR/.venv/bin/pip"
DB_PATH="$ROOT_DIR/data/penny_stock_radar.sqlite3"

echo "[2/6] Checking dependencies..."
if "$VENV_PYTHON" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = [
    "streamlit",
    "typer",
    "pandas",
    "pydantic",
    "httpx",
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
  PIP_DISABLE_PIP_VERSION_CHECK=1 "$VENV_PIP" install -e '.[dev,ui]'
fi

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  echo "[3/6] Creating .env from .env.example..."
  cp .env.example .env
fi

echo "[4/6] Checking whether a full refresh is needed..."
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
  echo "Use the sidebar button '전체 최신화 실행' when you want a full recalculation."
else
  echo "Running the full pipeline. This can take a little while."
  ./scripts/run_full_pipeline.sh
fi

echo "[5/6] Launching dashboard..."
echo ""
echo "If the browser does not open automatically, visit:"
echo "http://localhost:8501"
echo ""
echo "Press Ctrl+C in this terminal window to stop the server."
echo ""

echo "[6/6] Starting Streamlit"
exec "$VENV_PYTHON" -m streamlit run "$ROOT_DIR/src/penny_stock_radar/ui/app.py" \
  --server.address localhost \
  --server.port 8501
