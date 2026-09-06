#!/usr/bin/env python3
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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

    inspect = run("docker inspect nexus_signal_copy nexus_gateway --format '{{.Name}}|{{.State.Status}}|{{.RestartCount}}|{{.State.OOMKilled}}'")
    if inspect.returncode:
        errors.append(f"docker inspect gagal: {inspect.stderr.strip()}")
    else:
        rows = inspect.stdout.strip().splitlines()
        if len(rows) != 2:
            errors.append(f"container count salah: {rows}")
        for row in rows:
            parts = row.split("|")
            if len(parts) != 4 or parts[1] != "running" or parts[2] != "0" or parts[3].lower() != "false":
                errors.append(f"container tidak sehat: {row}")

    for path, maximum in [
        (ROOT / "journal/open_positions.json", 180),
        (ROOT / "runtime/state/pending_lifecycle.jsonl", 21600),
    ]:
        if not path.exists():
            errors.append(f"state hilang: {path.relative_to(ROOT)}")
        elif age_seconds(path) > maximum:
            errors.append(f"state stale: {path.relative_to(ROOT)} age={age_seconds(path):.0f}s")

    disk = run("df -P / | tail -1")
    if disk.returncode == 0:
        try:
            used = int(disk.stdout.split()[4].rstrip("%"))
            if used >= 90:
                errors.append(f"disk kritis: {used}%")
        except (IndexError, ValueError):
            errors.append(f"disk parse gagal: {disk.stdout.strip()}")

    logs = run("docker logs --since 20m nexus_gateway 2>&1")
    bad = [line for line in logs.stdout.splitlines() if any(token in line.lower() for token in (
        "tradejournalwriter error", "not json serializable", "traceback", "no space left", "management loop error"
    ))]
    if bad:
        errors.append("gateway errors:\n" + "\n".join(bad[-12:]))

    listener = run("docker logs --since 30m nexus_signal_copy 2>&1")
    activity = any(token in listener.stdout for token in ("[SIGNAL_COPY]", "[READ ", "[OUTCOME]", "[PENDING]"))
    if not activity:
        errors.append("Telegram intake tidak punya activity proof selama 30 menit")

    if errors:
        print("ALERT SHADOW INTEGRITY\n" + "\n\n".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
