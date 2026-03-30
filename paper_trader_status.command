#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

LABEL="com.penny_stock_radar.paper_trader"
PLIST_TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
STDOUT_LOG="$ROOT_DIR/automation/logs/paper_trader_stdout.log"
STDERR_LOG="$ROOT_DIR/automation/logs/paper_trader_stderr.log"

echo "========================================"
echo " Penny Stock Radar Paper Trader"
echo " Background Status"
echo "========================================"
echo ""

if [ ! -f "$PLIST_TARGET" ]; then
  echo "LaunchAgent is not installed yet."
  echo "Run start_paper_trader_background.command first."
  echo ""
  read -r "?Press Enter to close..."
  exit 0
fi

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "LaunchAgent status:"
  launchctl print "gui/$(id -u)/$LABEL" | sed -n '1,40p'
else
  echo "LaunchAgent is installed but not currently loaded."
fi

echo ""
if [ -f "$STDOUT_LOG" ]; then
  echo "Recent stdout:"
  tail -n 10 "$STDOUT_LOG"
else
  echo "No stdout log yet."
fi

echo ""
if [ -f "$STDERR_LOG" ]; then
  echo "Recent stderr:"
  tail -n 10 "$STDERR_LOG"
else
  echo "No stderr log yet."
fi

echo ""
echo "CSV outputs:"
echo "  $ROOT_DIR/sample_outputs/paper_trading"
echo ""
read -r "?Press Enter to close..."
