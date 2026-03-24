#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
RUNNER="$ROOT_DIR/scripts/psradar"
DB_PATH="$ROOT_DIR/data/penny_stock_radar.sqlite3"

# Start the demo from a clean database so the replay path stays reproducible.
rm -f "$DB_PATH"

"$RUNNER" init-db
"$RUNNER" build-universe --max-symbols 40 --export-json "$ROOT_DIR/sample_outputs/universe_candidates.sample.json"
"$RUNNER" build-watchlist --limit 10 --lookback-hours 48
"$RUNNER" run-replay-pipeline \
  --output-csv "$ROOT_DIR/sample_outputs/mock_replay.sample.csv" \
  --export-json "$ROOT_DIR/sample_outputs/replay_report.sample.json"
"$RUNNER" analyze-social --mentions-csv "$ROOT_DIR/sample_outputs/social_mentions.sample.csv"
"$RUNNER" export-summary \
  --json-output "$ROOT_DIR/sample_outputs/radar_summary.json" \
  --markdown-output "$ROOT_DIR/sample_outputs/radar_summary.md"
