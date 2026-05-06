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
  echo "Paper trader running: pid=$PID"
else
  echo "Paper trader pid file exists, but process is not running: pid=$PID"
fi
