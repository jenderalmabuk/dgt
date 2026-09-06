#!/usr/bin/env python3
"""
Fetch fresh universe from Binance + Bybit via REST API.
Binance primary, Bybit supplement for pairs not on Binance.
"""
import json
import urllib.request
from datetime import datetime
from pathlib import Path

def fetch_binance_perps():
    """Fetch Binance USDT perpetuals."""
    print("1️⃣ Binance USDⓈ-M Futures...")
    url = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read())
        
        perps = [
            s['symbol']
            for s in data['symbols']
            if s['contractType'] == 'PERPETUAL'
            and s['quoteAsset'] == 'USDT'
            and s['status'] == 'TRADING'
        ]
        perps.sort()
        print(f"   Found: {len(perps)} active USDT perpetuals")
        print(f"   Sample: {perps[:5]}")
        return perps
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return []

def fetch_bybit_perps():
    """Fetch Bybit USDT linear perpetuals."""
    print("\n2️⃣ Bybit Linear Perpetuals...")
    url = 'https://api.bybit.com/v5/market/instruments-info?category=linear'
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read())
        
        perps = [
            s['symbol']
            for s in data['result']['list']
            if s['quoteCoin'] == 'USDT'
            and s['status'] == 'Trading'
            and s['settleCoin'] == 'USDT'
        ]
        perps.sort()
        print(f"   Found: {len(perps)} active USDT perpetuals")
        print(f"   Sample: {perps[:5]}")
        return perps
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return []

def main():
    print("=== 📡 FETCHING FRESH UNIVERSE ===\n")
    
    # 1. Fetch from both exchanges
    binance_pairs = fetch_binance_perps()
    bybit_pairs = fetch_bybit_perps()
    
    if not binance_pairs or not bybit_pairs:
        print("\n❌ Failed to fetch from one or both exchanges")
        return False
    
    # 2. Combine: Binance primary, Bybit supplement
    print("\n3️⃣ Merging Universe (Binance primary + Bybit unique)...")
    binance_set = set(binance_pairs)
    bybit_set = set(bybit_pairs)
    
    binance_only = binance_set - bybit_set
    bybit_only = bybit_set - binance_set
    common = binance_set & bybit_set
    
    print(f"   Binance-only pairs: {len(binance_only)}")
    if binance_only:
        print(f"   Examples: {sorted(binance_only)[:5]}")
    
    print(f"   Bybit-only pairs: {len(bybit_only)}")
    if bybit_only:
        print(f"   Examples: {sorted(bybit_only)[:5]}")
    
    print(f"   Common pairs: {len(common)}")
    
    universe = sorted(binance_set | bybit_set)
    print(f"\n✅ Total Universe: {len(universe)} pairs")
    
    # 3. Map to primary exchange
    exchange_map = {}
    for pair in universe:
        if pair in binance_set and pair in bybit_set:
            exchange_map[pair] = 'both'
        elif pair in binance_set:
            exchange_map[pair] = 'binance'
        else:
            exchange_map[pair] = 'bybit'
    
    from collections import Counter
    source_counts = Counter(exchange_map.values())
    print(f"\n📊 Source Distribution:")
    for source, count in sorted(source_counts.items()):
        print(f"   {source}: {count}")
    
    # 4. Save to files
    script_dir = Path(__file__).parent
    output_dir = script_dir.parent / 'data'
    output_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.utcnow().isoformat() + 'Z'
    
    # Universe list
    universe_file = output_dir / 'universe_fresh.json'
    with open(universe_file, 'w') as f:
        json.dump({
            'updated': timestamp,
            'total': len(universe),
            'binance': len(binance_set),
            'bybit': len(bybit_set),
            'common': len(common),
            'binance_only': len(binance_only),
            'bybit_only': len(bybit_only),
            'pairs': universe
        }, f, indent=2)
    print(f"\n✅ Saved: {universe_file}")
    
    # Exchange mapping
    mapping_file = output_dir / 'exchange_mapping.json'
    with open(mapping_file, 'w') as f:
        json.dump({
            'updated': timestamp,
            'pairs': exchange_map
        }, f, indent=2)
    print(f"✅ Saved: {mapping_file}")
    
    # Check specific pairs
    print(f"\n🔍 Checking specific pairs:")
    test_pairs = ['QTUMUSDT', 'SOLUSDT', 'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'BLESSUSDT']
    for pair in test_pairs:
        source = exchange_map.get(pair, 'NOT FOUND')
        status = '✅' if source != 'NOT FOUND' else '❌'
        print(f"   {status} {pair}: {source}")
    
    print(f"\n{'='*60}")
    print("✅ PHASE 1 COMPLETE: UNIVERSE FETCHED")
    print(f"  Total pairs: {len(universe)}")
    print(f"  Binance: {len(binance_set)} ({len(binance_only)} exclusive)")
    print(f"  Bybit: {len(bybit_set)} ({len(bybit_only)} exclusive)")
    print(f"  Both exchanges: {len(common)}")
    print(f"  Files saved:")
    print(f"    - data/universe_fresh.json")
    print(f"    - data/exchange_mapping.json")
    
    return True

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
