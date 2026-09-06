#!/usr/bin/env bash
set -euo pipefail
cd /home/fusion_omega/fusion_omega_nexus
set -a
source .env
set +a
export TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
export TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
export AUTONOMOUS_BASKET_LIVE=YES
export AUTONOMOUS_BASKET_POLICY_SHA256="$('/usr/bin/python3' - <<'PY'
import sys, time
sys.path.insert(0, 'bots')
import autonomous_basket_trader as trader
print(trader.load_policy('bots/configs/autonomous_basket_policy.json', time.time()).digest)
PY
)"
exec /usr/bin/python3 bots/autonomous_basket_trader.py live --interval 15
