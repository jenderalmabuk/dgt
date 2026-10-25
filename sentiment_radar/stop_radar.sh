#!/bin/bash
# Stop Sentiment Radar Daemon
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$DIR/runtime/sentiment/radar.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "Sentiment Radar (PID: $PID) stopped."
    else
        echo "Process $PID not running."
    fi
    rm -f "$PID_FILE"
else
    echo "No PID file found. Radar is not running."
fi
