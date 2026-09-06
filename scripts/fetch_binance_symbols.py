#!/usr/bin/env python3
"""
Fetch full Binance USDT perpetual futures symbol list
"""
import json
import time
from datetime import datetime, timezone

def fetch_symbols():
    """Fetch from Binance exchangeInfo API"""
    import requests
    
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    
    print("Fetching Binance exchangeInfo...")
    
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        
        data = resp.json()
        
        # Filter USDT perpetual futures
        symbols = []
        for s in data.get('symbols', []):
            if (s.get('status') == 'TRADING' and 
                s.get('contractType') == 'PERPETUAL' and
                s.get('quoteAsset') == 'USDT'):
                symbols.append(s['symbol'])
        
        symbols.sort()
        
        print(f"✅ Found {len(symbols)} USDT perpetual symbols")
        
        return {
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'source': 'binance_fapi_exchangeInfo',
            'count': len(symbols),
            'symbols': symbols
        }
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 418:
            print("❌ IP rate limited. Using fallback cached list...")
            return use_fallback()
        raise
    except Exception as e:
        print(f"❌ Error: {e}")
        print("Using fallback cached list...")
        return use_fallback()

def use_fallback():
    """Use a known good fallback list"""
    # From a recent successful fetch (2026-08-04)
    print("Loading fallback symbol list...")
    
    # This is a minimal safe list - script will try API first
    fallback = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
        "MATICUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT", "ETCUSDT",
        "NEARUSDT", "APTUSDT", "STXUSDT", "FILUSDT", "ARBUSDT",
        "OPUSDT", "SUIUSDT", "INJUSDT", "SEIUSDT", "TIAUSDT",
        # Add more as needed
    ]
    
    return {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'source': 'fallback_minimal',
        'count': len(fallback),
        'symbols': sorted(fallback)
    }

def main():
    import sys
    import os
    
    # Change to script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(os.path.dirname(script_dir), 'data')
    output_file = os.path.join(data_dir, 'binance_usdt_perps.json')
    
    # Backup existing
    if os.path.exists(output_file):
        backup = output_file + '.bak'
        import shutil
        shutil.copy2(output_file, backup)
        print(f"📦 Backed up to {backup}")
    
    # Fetch
    result = fetch_symbols()
    
    # Validate
    if result['count'] < 50:
        print(f"⚠️ Only {result['count']} symbols - seems too low!")
        print("Check if API is working or fallback is incomplete")
        sys.exit(1)
    
    # Save
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Saved {result['count']} symbols to {output_file}")
    
    # Show sample
    print(f"\nSample symbols:")
    for s in result['symbols'][:10]:
        print(f"  {s}")
    
    # Check for TAKEUSDT
    if 'TAKEUSDT' in result['symbols']:
        print("\n✅ TAKEUSDT found in list!")
    else:
        print("\n⚠️ TAKEUSDT not found - may be new or delisted")

if __name__ == '__main__':
    main()
