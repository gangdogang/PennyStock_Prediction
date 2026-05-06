#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
PID_FILE="$ROOT_DIR/automation/state/paper_trader.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "Paper trader is not running."
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" >/dev/null 2>&1; then
  kill "$PID"
  echo "Paper trader stop requested: pid=$PID"
else
  echo "Paper trader process not running: pid=$PID"
fi
rm -f "$PID_FILE"
