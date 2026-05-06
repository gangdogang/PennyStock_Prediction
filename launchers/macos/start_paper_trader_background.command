#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$ROOT_DIR/automation/logs"
STATE_DIR="$ROOT_DIR/automation/state"
PID_FILE="$STATE_DIR/paper_trader.pid"

mkdir -p "$LOG_DIR" "$STATE_DIR"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" >/dev/null 2>&1; then
    echo "Paper trader already running: pid=$PID"
    exit 0
  fi
fi

nohup "$ROOT_DIR/scripts/psradar" paper-trader --check-interval-seconds 60 \
  >> "$LOG_DIR/paper_trader_stdout.log" \
  2>> "$LOG_DIR/paper_trader_stderr.log" &
echo "$!" > "$PID_FILE"
echo "Paper trader started: pid=$!"
