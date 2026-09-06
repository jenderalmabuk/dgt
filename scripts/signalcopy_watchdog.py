#!/usr/bin/env python3
"""SignalCopy watchdog. Silent when healthy; deduplicated Telegram alerts."""
import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path("/home/fusion_omega/fusion_omega_nexus")
STATE = ROOT / "runtime/state/signalcopy_watchdog.json"
CONTAINERS = ("nexus_signal_copy", "nexus_gateway", "nexus_binance_collector", "nexus_fastapi")


def run(*args):
    return subprocess.run(args, text=True, capture_output=True).stdout


def send(text):
    payload = json.dumps({"text": "SIGNALCOPY WATCHDOG\n" + text})
    code = """import json,os,urllib.request
p=json.loads(%r); token=os.getenv('TRADES_TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN'); chat=os.getenv('TRADES_TELEGRAM_CHAT_ID') or os.getenv('TELEGRAM_CHAT_ID')
if not token or not chat: raise SystemExit('telegram env missing')
data=json.dumps({'chat_id':chat,'text':p['text']}).encode(); urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',data=data,headers={'Content-Type':'application/json'}),timeout=10).read()
""" % payload
    subprocess.run(["docker", "exec", "nexus_signal_copy", "python", "-c", code], timeout=20)


def main():
    alerts = []
    states = run("docker", "inspect", "-f", "{{.Name}}|{{.State.Running}}", *CONTAINERS)
    for name in CONTAINERS:
        if f"/{name}|true" not in states:
            alerts.append(f"CONTAINER_DOWN {name}")

    collector = run("docker", "logs", "nexus_binance_collector", "--since", "6m")
    rate_errors = sum(x in line for line in collector.splitlines() for x in ("429", "418"))
    if rate_errors:
        alerts.append(f"BINANCE_RATE_LIMIT count={rate_errors}/6m")

    gateway = run("docker", "logs", "nexus_gateway", "--since", "6m")
    if "reject stale fill" in gateway:
        lines = [line[-300:] for line in gateway.splitlines() if "reject stale fill" in line]
        alerts.append("STALE_GATEWAY_MARK\n" + "\n".join(lines[-3:]))
    generic = sum("trader rejected open: no position created" in line for line in gateway.splitlines())
    if generic:
        alerts.append(f"GENERIC_TRADER_REJECTION count={generic}/6m")

    try:
        positions = json.loads((ROOT / "journal/open_positions.json").read_text())
        pending_path = ROOT / "runtime/state/pending_lifecycle.jsonl"
        latest = {}
        if pending_path.exists():
            for line in pending_path.read_text().splitlines():
                row = json.loads(line)
                if row.get("signal_id"):
                    latest[row["signal_id"]] = row
        pending = sum(row.get("event") in {"PENDING", "WAITING_FOR_SLOT"} for row in latest.values())
        if len(positions) + pending > 20:
            alerts.append(f"CAP_BREACH open={len(positions)} pending={pending} total={len(positions)+pending}")
    except Exception as exc:
        alerts.append(f"STATE_READ_ERROR {exc}")

    try:
        ledger = ROOT / "runtime/state/signal_lifecycle.jsonl"
        if ledger.exists():
            for line in ledger.read_text().splitlines()[-1000:]:
                row = json.loads(line)
                if row.get("position_confirmed") and not row.get("gateway_accepted"):
                    alerts.append(f"LIFECYCLE_INVARIANT signal_id={row.get('signal_id')}")
                    break
    except Exception as exc:
        alerts.append(f"LEDGER_READ_ERROR {exc}")

    digest = hashlib.sha256("\n".join(alerts).encode()).hexdigest() if alerts else ""
    old = json.loads(STATE.read_text()) if STATE.exists() else {}
    if alerts and (digest != old.get("digest") or time.time() - old.get("sent_at", 0) > 3600):
        send("\n\n".join(alerts))
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"digest": digest, "sent_at": time.time()}))
    elif not alerts and old.get("digest"):
        STATE.write_text(json.dumps({"digest": "", "sent_at": time.time()}))


if __name__ == "__main__":
    main()
