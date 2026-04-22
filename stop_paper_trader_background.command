#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
exec "$ROOT_DIR/scripts/stop_paper_trader_background.command"
