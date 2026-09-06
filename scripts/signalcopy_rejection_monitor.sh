#!/bin/bash
# SignalCopy Bad Signal Rejection Monitor
# Track rejected signals to verify blocking gate is working

cd /home/fusion_omega/fusion_omega_nexus

# Check gateway logs for bad signal rejections in last 24h
docker logs nexus_gateway --since 24h 2>&1 | grep -E "REJECTED.*bad signal" > /tmp/bad_signal_rejects.log

if [ -s /tmp/bad_signal_rejects.log ]; then
    echo "🚫 BAD SIGNAL REJECTIONS (Last 24h)"
    echo
    
    # Count rejections
    count=$(wc -l < /tmp/bad_signal_rejects.log)
    echo "Total rejected: $count"
    echo
    
    # Show recent rejections
    echo "Recent rejections:"
    tail -10 /tmp/bad_signal_rejects.log | while IFS= read -r line; do
        # Extract symbol and categories
        symbol=$(echo "$line" | grep -oP '(LONG|SHORT) \K[A-Z]+' | head -1)
        cats=$(echo "$line" | grep -oP 'blocking: \K.*' | head -1)
        echo "  $symbol: $cats"
    done
    echo
    
    # Count by category
    echo "By category:"
    grep -oP 'blocking: \K[^,]+' /tmp/bad_signal_rejects.log | sort | uniq -c | sort -rn | while read count cat; do
        echo "  $cat: $count"
    done
else
    # Silent if no rejections (healthy)
    exit 0
fi

rm -f /tmp/bad_signal_rejects.log
