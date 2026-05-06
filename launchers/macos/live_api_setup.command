#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp ".env.example" ".env"
fi

if [ ! -f ".env" ]; then
  echo ".env.example was not found, so .env could not be created." >&2
  exit 1
fi

cat <<'EOF'
Fill in one of these in .env:

PENNY_STOCK_LIVE_MARKET_PROVIDER=kis
PENNY_STOCK_KIS_APP_KEY=
PENNY_STOCK_KIS_APP_SECRET=
PENNY_STOCK_KIS_NASDAQ_MASTER_PATH=./data/kis_master/NASMST.COD
PENNY_STOCK_KIS_NYSE_MASTER_PATH=./data/kis_master/NYSMST.COD
PENNY_STOCK_KIS_AMEX_MASTER_PATH=./data/kis_master/AMSMST.COD

Optional Gemini review:
PENNY_STOCK_GEMINI_API_KEY=
PENNY_STOCK_GEMINI_MODEL=gemini-3-flash-preview
EOF

if command -v open >/dev/null 2>&1; then
  open -e "$ROOT_DIR/.env"
else
  echo "Edit $ROOT_DIR/.env"
fi
