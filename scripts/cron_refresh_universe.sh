#!/bin/bash
# Daily Binance universe refresh cron
# Add to system crontab: @daily /home/fusion_omega/fusion_omega_nexus/scripts/cron_refresh_universe.sh >> /home/fusion_omega/fusion_omega_nexus/runtime/logs/universe_refresh.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/../runtime/logs/universe_refresh.log"

mkdir -p "$(dirname "$LOG_FILE")"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting Binance universe refresh" | tee -a "$LOG_FILE"

if "$SCRIPT_DIR/refresh_binance_universe.sh" >> "$LOG_FILE" 2>&1; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Universe refresh SUCCESS" | tee -a "$LOG_FILE"
  exit 0
else
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Universe refresh FAILED" | tee -a "$LOG_FILE"
  exit 1
fi
