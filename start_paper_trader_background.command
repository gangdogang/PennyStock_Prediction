#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

LABEL="com.penny_stock_radar.paper_trader"
PLIST_SOURCE="$ROOT_DIR/automation/launchd/${LABEL}.plist"
PLIST_TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"

echo "========================================"
echo " Penny Stock Radar Paper Trader"
echo " Background Start"
echo "========================================"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed."
  echo "Please install Python 3 on macOS and try again."
  read -r "?Press Enter to close..."
  exit 1
fi

if [ ! -f "$PLIST_SOURCE" ]; then
  echo "Missing launchd plist:"
  echo "  $PLIST_SOURCE"
  read -r "?Press Enter to close..."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[1/5] Creating virtual environment (.venv)..."
  python3 -m venv .venv
fi

VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
VENV_PIP="$ROOT_DIR/.venv/bin/pip"

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
else
  echo "[3/5] Environment file is ready."
fi

echo "[4/5] Installing launchd agent..."
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$ROOT_DIR/automation/logs"
cp "$PLIST_SOURCE" "$PLIST_TARGET"

echo "[5/5] Starting background paper trader..."
launchctl bootout "gui/$(id -u)" "$PLIST_TARGET" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_TARGET"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo ""
echo "Background paper trader is running."
echo ""
echo "Status:"
launchctl print "gui/$(id -u)/$LABEL" | sed -n '1,40p'
echo ""
echo "Logs:"
echo "  stdout -> $ROOT_DIR/automation/logs/paper_trader_stdout.log"
echo "  stderr -> $ROOT_DIR/automation/logs/paper_trader_stderr.log"
echo "  csv    -> $ROOT_DIR/sample_outputs/paper_trading"
echo ""
read -r "?Press Enter to close..."
