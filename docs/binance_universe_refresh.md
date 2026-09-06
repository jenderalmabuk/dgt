# Binance Universe Auto-Refresh

## Overview

Daily automatic refresh of Binance USDT perpetual futures universe for SignalCopy OI/funding data.

## Files

- **`scripts/refresh_binance_universe.sh`** — Fetch current universe from Binance API
- **`scripts/cron_refresh_universe.sh`** — Cron wrapper with logging
- **`data/binance_usdt_perps.json`** — Output file (auto-generated)
- **`runtime/logs/universe_refresh.log`** — Refresh history

## Output Format

```json
{
  "updated_at": "2026-08-04T06:53:29.618Z",
  "source": "binance_fapi_exchangeInfo",
  "count": 387,
  "symbols": ["BTCUSDT", "ETHUSDT", ...]
}
```

## Setup System Cron

```bash
# Add to system crontab (runs daily at 3 AM UTC)
crontab -e
```

Add:
```
0 3 * * * /home/fusion_omega/fusion_omega_nexus/scripts/cron_refresh_universe.sh
```

Or use `@daily`:
```
@daily /home/fusion_omega/fusion_omega_nexus/scripts/cron_refresh_universe.sh
```

## Manual Run

```bash
cd /home/fusion_omega/fusion_omega_nexus
./scripts/refresh_binance_universe.sh
```

## Integration

`nexus/data_bridge.py` reads universe from:
1. `/app/data/binance_usdt_perps.json` (container)
2. `data/binance_usdt_perps.json` (host)
3. Fallback to legacy `runtime/revo/canonical_universe.json`

New pairs become available immediately after refresh—no gateway restart needed.

## Safety

- Rejects empty responses
- Rejects suspiciously low counts (<100)
- Atomic write via temp file
- Logs all refreshes
- Fails safe on errors

## Verification

```bash
# Check last refresh
cat data/binance_usdt_perps.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['updated_at'], d['count'])"

# Check cron logs
tail -20 runtime/logs/universe_refresh.log
```
