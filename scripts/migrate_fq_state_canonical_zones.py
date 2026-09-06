#!/usr/bin/env python3
"""Migrate Fusion Quantum state: canonicalize zone keys, deduplicate lifecycle."""
import json
import sys
from pathlib import Path
import pandas as pd

def canonical_zone_key(key):
    if not key or len(key) != 4:
        return None
    try:
        stamp = pd.Timestamp(key[1])
        if pd.isna(stamp):
            return None
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        return [str(key[0]), stamp.isoformat(), float(key[2]), float(key[3])]
    except Exception:
        return None

def lifecycle_key(setup):
    key = canonical_zone_key(setup.get("zone_key"))
    return "|".join(str(x) for x in key) if key else None

def main():
    state_path = Path("runtime/fusion_quantum/state.json")
    if not state_path.exists():
        print(f"ERROR: {state_path} not found")
        sys.exit(1)

    data = json.loads(state_path.read_text())
    setups = data.get("setups", {})
    
    print(f"Before: {len(setups)} rows")
    
    # Canonicalize all zone_key timestamps
    for sid, row in setups.items():
        setup = row.get("setup")
        if setup and "zone_key" in setup:
            canonical = canonical_zone_key(setup["zone_key"])
            if canonical:
                setup["zone_key"] = canonical
    
    # Deduplicate by lifecycle key
    seen_lifecycle = {}
    terminal_states = {"confirmed", "failed_confirmation", "paper_opened", "paper_closed", 
                      "expired", "rejected", "blocked_running", "superseded", "hard_sl", "tp_hit"}
    to_remove = []
    
    for sid, row in setups.items():
        setup = row.get("setup")
        if not setup:
            continue
        
        lkey = lifecycle_key(setup)
        if not lkey:
            continue
        
        status = row.get("status")
        
        if lkey in seen_lifecycle:
            prior_sid, prior_status = seen_lifecycle[lkey]
            
            # Keep terminal over non-terminal
            if prior_status in terminal_states and status not in terminal_states:
                to_remove.append(sid)
                print(f"Remove duplicate {sid} ({status}) - terminal {prior_sid} ({prior_status}) exists")
                continue
            elif status in terminal_states and prior_status not in terminal_states:
                to_remove.append(prior_sid)
                seen_lifecycle[lkey] = (sid, status)
                print(f"Remove duplicate {prior_sid} ({prior_status}) - keeping terminal {sid} ({status})")
                continue
            
            # Both terminal or both non-terminal: keep older by updated_at
            prior_updated = setups[prior_sid].get("updated_at", "")
            curr_updated = row.get("updated_at", "")
            
            if curr_updated < prior_updated:
                to_remove.append(prior_sid)
                seen_lifecycle[lkey] = (sid, status)
                print(f"Remove newer duplicate {prior_sid} - keeping {sid}")
            else:
                to_remove.append(sid)
                print(f"Remove newer duplicate {sid} - keeping {prior_sid}")
        else:
            seen_lifecycle[lkey] = (sid, status)
    
    # Terminate pending from zones that are already terminal
    pending_terminate = []
    for sid, row in setups.items():
        if sid in to_remove:
            continue
        
        setup = row.get("setup")
        status = row.get("status")
        
        if status == "pending" and setup:
            lkey = lifecycle_key(setup)
            if lkey and lkey in seen_lifecycle:
                other_sid, other_status = seen_lifecycle[lkey]
                if other_sid != sid and other_status in terminal_states:
                    pending_terminate.append(sid)
                    print(f"Terminate pending {sid} - zone already terminal in {other_sid} ({other_status})")
    
    for sid in pending_terminate:
        setups[sid]["status"] = "superseded"
        setups[sid]["lifecycle_reason"] = "zone_already_consumed_migration"
    
    for sid in to_remove:
        del setups[sid]
    
    print(f"After: {len(setups)} rows (removed {len(to_remove)}, terminated {len(pending_terminate)} pending)")
    print(f"Unique lifecycle keys: {len(seen_lifecycle)}")
    
    # Write atomically
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(state_path)
    print(f"Migrated state written to {state_path}")

if __name__ == "__main__":
    main()
