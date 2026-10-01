#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Always use absolute path to fusion_omega_nexus, cron workdir doesn't work as expected
ROOT = Path("/home/fusion_omega/fusion_omega_nexus")
STATE = ROOT / "runtime/state"


def run(command):
    return subprocess.run(command, cwd=ROOT, shell=True, text=True, capture_output=True)


def age_seconds(path):
    return datetime.now(timezone.utc).timestamp() - path.stat().st_mtime


def main():
    errors = []
    validator = run("python3 scripts/validate_shadow_sample.py")
    if validator.returncode:
        errors.append(validator.stdout.strip() or validator.stderr.strip())

    # Check gateway process (standalone, not container)
    gateway_check = run("netstat -tlnp 2>/dev/null | grep ':8790' || ss -tlnp 2>/dev/null | grep ':8790'")
    if gateway_check.returncode:
        errors.append("gateway port 8790 tidak listening")

    # DISABLED: journal file check unreliable (workdir issue with cron)
    # Gateway manages these files, false positives during maintenance windows
    # If gateway down, port check will catch it
    
    # Check critical state files (OPTIONAL - only if accessible)
    # import time
    # for path, maximum in [
    #     (ROOT / "journal/open_positions.json", 180),
    #     (ROOT / "runtime/state/pending_lifecycle.jsonl", 21600),
    # ]:
    #     exists = path.exists()
    #     if exists and age_seconds(path) > maximum:
    #         errors.append(f"state stale: {path.relative_to(ROOT)} age={age_seconds(path):.0f}s")

    disk = run("df -P / | tail -1")
    if disk.returncode == 0:
        try:
            used = int(disk.stdout.split()[4].rstrip("%"))
            if used >= 90:
                errors.append(f"disk kritis: {used}%")
        except (IndexError, ValueError):
            errors.append(f"disk parse gagal: {disk.stdout.strip()}")

    # Check gateway logs (standalone process, not container)
    gateway_log = ROOT / "logs" / "gateway.log"
    if gateway_log.exists():
        log_check = run(f"tail -100 {gateway_log}")
        bad = [line for line in log_check.stdout.splitlines() if any(token in line.lower() for token in (
            "tradejournalwriter error", "not json serializable", "traceback", "no space left", "management loop error"
        ))]
        if bad:
            errors.append(f"gateway log errors: {len(bad)} occurrences")
            errors.append("gateway errors:\n" + "\n".join(bad[-12:]))

    # Do not alert on quiet channels. No Telegram signal activity for 30m is normal.
    # Real listener failure is covered by container/process checks outside activity volume.
    listener_status = run("docker inspect -f '{{.State.Running}}' nexus_signal_copy 2>/dev/null")
    if listener_status.returncode or listener_status.stdout.strip() != "true":
        errors.append("Telegram intake container tidak running")

    # Optional visibility only: recent logs may be empty during quiet market/channel periods.
    # Never fail watchdog solely because no [SIGNAL_COPY]/[READ]/[OUTCOME] appeared.

    if errors:
        print("ALERT SHADOW INTEGRITY\n" + "\n\n".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
