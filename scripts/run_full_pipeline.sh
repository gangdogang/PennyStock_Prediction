#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
RUNNER="$ROOT_DIR/scripts/psradar"
DB_PATH="$ROOT_DIR/data/penny_stock_radar.sqlite3"

if [ "${PENNY_STOCK_RESET_DB:-0}" = "1" ]; then
  rm -f "$DB_PATH"
fi

"$RUNNER" init-db
"$RUNNER" build-universe --max-symbols "${PENNY_STOCK_UNIVERSE_MAX_SYMBOLS:-250}" --export-json "$ROOT_DIR/sample_outputs/universe_candidates.sample.json"
"$RUNNER" build-watchlist --limit 10 --lookback-hours 48
"$RUNNER" run-replay-pipeline \
  --output-csv "$ROOT_DIR/sample_outputs/mock_replay.sample.csv" \
  --export-json "$ROOT_DIR/sample_outputs/replay_report.sample.json"
echo "Skipping sample social analysis. Run \`./scripts/psradar analyze-social --mentions-csv <real_social_mentions.csv>\` when you have real mention data."
"$RUNNER" export-summary \
  --json-output "$ROOT_DIR/sample_outputs/radar_summary.json" \
  --markdown-output "$ROOT_DIR/sample_outputs/radar_summary.md"
