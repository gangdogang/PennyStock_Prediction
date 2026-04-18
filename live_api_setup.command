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
echo "1) KIS (recommended)"
echo "   PENNY_STOCK_LIVE_MARKET_PROVIDER=kis"
echo "   PENNY_STOCK_KIS_APP_KEY=your_key"
echo "   PENNY_STOCK_KIS_APP_SECRET=your_secret"
echo "   PENNY_STOCK_KIS_NASDAQ_MASTER_PATH=./data/kis_master/NASMST.COD"
echo "   PENNY_STOCK_KIS_NYSE_MASTER_PATH=./data/kis_master/NYSMST.COD"
echo "   PENNY_STOCK_KIS_AMEX_MASTER_PATH=./data/kis_master/AMSMST.COD"
echo ""
echo "2) Alpaca fallback"
echo "   PENNY_STOCK_LIVE_MARKET_PROVIDER=alpaca"
echo "   PENNY_STOCK_ALPACA_API_KEY=your_key"
echo "   PENNY_STOCK_ALPACA_SECRET_KEY=your_secret"
echo ""
echo "Optional Gemini review"
echo "   PENNY_STOCK_GEMINI_API_KEY=your_key"
echo "   PENNY_STOCK_GEMINI_MODEL=gemini-3-flash-preview"
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
