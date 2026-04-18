#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

LABEL="com.penny_stock_radar.paper_trader"
PLIST_TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"

echo "========================================"
echo " Penny Stock Radar Paper Trader"
echo " Background Stop"
echo "========================================"
echo ""

if [ ! -f "$PLIST_TARGET" ]; then
  echo "LaunchAgent is not installed."
  echo "Nothing to stop."
  read -r "?Press Enter to close..."
  exit 0
fi

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "Stopping background paper trader..."
  launchctl bootout "gui/$(id -u)" "$PLIST_TARGET"
  echo "Stopped."
else
  echo "Background paper trader is already stopped."
fi

echo ""
echo "Installed plist remains here:"
echo "  $PLIST_TARGET"
echo "Run start_paper_trader_background.command when you want to turn it back on."
echo ""
read -r "?Press Enter to close..."
