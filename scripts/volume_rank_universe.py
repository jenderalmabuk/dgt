#!/usr/bin/env python3
"""
Fetch 24h volume from Binance, rank universe pairs by volume.
Top 300 → Binance collector, Rest → Bybit collector (both collect all for redundancy).
"""
import json
import urllib.request

def main():
    print("=== 📊 VOLUME RANKING FOR UNIVERSE ===\n")
    
    # Load universe
    with open('/home/fusion_omega/fusion_omega_nexus/data/universe_fresh.json') as f:
        universe = json.load(f)
    
    pairs = universe['pairs']
    print(f"Universe pairs: {len(pairs)}")
    
    # Fetch Binance 24h ticker
    print("\n1️⃣ Fetching Binance 24h ticker...")
    url = 'https://fapi.binance.com/fapi/v1/ticker/24hr'
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            tickers = json.loads(response.read())
        
        # Create volume map
        binance_volume = {}
        for t in tickers:
            symbol = t['symbol']
            volume_usdt = float(t['quoteVolume'])
            binance_volume[symbol] = volume_usdt
        
        print(f"   Binance tickers: {len(binance_volume)}")
        
        # Get volumes for our universe pairs
        universe_volumes = {}
        no_volume = []
        for pair in pairs:
            if pair in binance_volume:
                universe_volumes[pair] = binance_volume[pair]
            else:
                no_volume.append(pair)
        
        print(f"   Pairs with Binance volume: {len(universe_volumes)}")
        if no_volume:
            print(f"   Pairs NO Binance volume: {len(no_volume)}")
            print(f"   Examples: {no_volume[:5]}")
        
        # Sort by volume descending
        sorted_pairs = sorted(universe_volumes.items(), key=lambda x: x[1], reverse=True)
        
        print(f"\n📊 TOP 20 by Binance volume:")
        for i, (pair, vol) in enumerate(sorted_pairs[:20], 1):
            print(f"   {i:2d}. {pair}: ${vol/1e9:.2f}B")
        
        # Top 300 for Binance
        top_300 = [p for p, v in sorted_pairs[:300]]
        rest = [p for p in pairs if p not in top_300]
        
        # Add no-volume pairs to rest (they'll get Bybit data)
        rest = rest + no_volume
        
        print(f"\n✅ Proposed split:")
        print(f"   Binance (top 300 by volume): {len(top_300)} pairs")
        print(f"   Bybit (rest): {len(rest)} pairs")
        
        # Show threshold
        threshold = sorted_pairs[299][1] if len(sorted_pairs) > 299 else 0
        print(f"   Volume threshold: ${threshold/1e6:.1f}M")
        
        # Save lists
        with open('/home/fusion_omega/fusion_omega_nexus/data/binance_top300.txt', 'w') as f:
            for p in top_300:
                f.write(f"{p}\n")
        
        with open('/home/fusion_omega/fusion_omega_nexus/data/bybit_rest.txt', 'w') as f:
            for p in rest:
                f.write(f"{p}\n")
        
        # Full Bybit list (all 691 for redundancy)
        with open('/home/fusion_omega/fusion_omega_nexus/data/bybit_all_691.txt', 'w') as f:
            for p in pairs:
                f.write(f"{p}\n")
        
        print(f"\n✅ Files created:")
        print(f"   data/binance_top300.txt ({len(top_300)} pairs)")
        print(f"   data/bybit_rest.txt ({len(rest)} pairs)")
        print(f"   data/bybit_all_691.txt ({len(pairs)} pairs)")
        
        # Verify key pairs
        print(f"\n🔍 Key pairs assignment:")
        for p in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'QTUMUSDT', 'BLESSUSDT']:
            if p in top_300:
                vol = universe_volumes.get(p, 0)
                print(f"   {p}: BINANCE ✅ (${vol/1e6:.1f}M)")
            elif p in rest:
                print(f"   {p}: BYBIT (rest)")
            else:
                print(f"   {p}: NOT IN UNIVERSE ❌")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)