#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "========================================"
echo " Penny Stock Radar Live API Setup"
echo "========================================"
echo ""

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  echo "Creating .env from .env.example..."
  cp .env.example .env
fi

if [ ! -f ".env" ]; then
  echo "Unable to create .env."
  read -r "?Press Enter to close..."
  exit 1
fi

echo "Fill in one of the following blocks in .env."
echo ""
echo "1) Polygon"
echo "   PENNY_STOCK_LIVE_MARKET_PROVIDER=polygon"
echo "   PENNY_STOCK_POLYGON_API_KEY=your_key"
echo ""
echo "2) Alpaca"
echo "   PENNY_STOCK_LIVE_MARKET_PROVIDER=alpaca"
echo "   PENNY_STOCK_ALPACA_API_KEY=your_key"
echo "   PENNY_STOCK_ALPACA_SECRET_KEY=your_secret"
echo ""
echo "You can also leave provider as auto and just add keys."
echo ""
echo "Opening .env in TextEdit..."
echo ""

open -a TextEdit "$ROOT_DIR/.env"

echo "After saving, run:"
echo "  ./launch_dashboard.command"
echo ""

read -r "?Press Enter to close..."
