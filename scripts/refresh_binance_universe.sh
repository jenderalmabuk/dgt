#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_FILE="$ROOT_DIR/data/binance_usdt_perps.json"
TEMP_FILE="$OUTPUT_FILE.tmp"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Fetching Binance USDT perpetual universe..."

curl -sf --max-time 30 'https://fapi.binance.com/fapi/v1/exchangeInfo' \
  | python3 -c "
import sys, json
from datetime import datetime, timezone

data = json.load(sys.stdin)
symbols = [
    s['symbol']
    for s in data['symbols']
    if s['status'] == 'TRADING'
    and s['quoteAsset'] == 'USDT'
    and s['contractType'] == 'PERPETUAL'
]

result = {
    'updated_at': datetime.now(timezone.utc).isoformat(),
    'source': 'binance_fapi_exchangeInfo',
    'count': len(symbols),
    'symbols': sorted(symbols)
}

print(json.dumps(result, indent=2))
" > "$TEMP_FILE"

if [ ! -s "$TEMP_FILE" ]; then
  echo "ERROR: Empty response from Binance API"
  rm -f "$TEMP_FILE"
  exit 1
fi

COUNT=$(python3 -c "import json; print(json.load(open('$TEMP_FILE'))['count'])")
if [ "$COUNT" -lt 100 ]; then
  echo "ERROR: Suspiciously low count ($COUNT), refusing update"
  rm -f "$TEMP_FILE"
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_FILE")"
mv "$TEMP_FILE" "$OUTPUT_FILE"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Universe updated: $COUNT symbols"
echo "$OUTPUT_FILE"
