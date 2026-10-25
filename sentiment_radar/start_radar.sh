#!/bin/bash
# Start Sentiment Radar Daemon in background
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

export PYTHONPATH="$DIR:$PYTHONPATH"

# Load environment variables if .env exists
if [ -f "$DIR/.env" ]; then
    set -a
    source "$DIR/.env"
    set +a
fi

LOG_FILE="$DIR/runtime/sentiment/radar.log"
PID_FILE="$DIR/runtime/sentiment/radar.pid"

if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
    echo "Sentiment Radar is already running (PID: $(cat "$PID_FILE"))"
    exit 0
fi

nohup python3 -u sentiment_radar/radar.py --daemon --interval 180 >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Sentiment Radar started with PID: $(cat "$PID_FILE")"
echo "Logs: $LOG_FILE"
