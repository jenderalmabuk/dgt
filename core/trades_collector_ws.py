#!/usr/bin/env python3
"""
Bybit Trades Stream WebSocket Collector
Aggregates trades into candles and computes taker buy/sell volume.
Writes to a JSONL file for use by the Nexus API to enrich kline data.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
import websockets
import signal

# Bybit WebSocket endpoint for linear futures (USDT perpetual)
WS_URL = "wss://stream.bybit.com/v5/public/linear"

# Timeframes we want to aggregate (in seconds)
TIMEFRAMES = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
}

# Output file - use /tmp/revo by default, but can be overridden by TRADES_DATA_PATH
OUTPUT_DIR = os.getenv("TRADES_DATA_PATH", "/tmp/revo")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "trades_bybit.jsonl")

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

class TradesCollectorWS:
    def __init__(self, pairs, timeframes=None):
        """
        :param pairs: list of symbols (e.g., ["BTCUSDT", "ETHUSDT"])
        :param timeframes: dict of timeframe name to seconds (default: TIMEFRAMES)
        """
        self.pairs = [p.upper() for p in pairs]
        self.timeframes = timeframes or TIMEFRAMES
        # For each symbol and timeframe, we track the current candle
        self.current = {}  # (symbol, tf) -> {"start": int, "buy_base": float, "buy_quote": float}
        self._running = False
        self._tasks = []
        self._lock = asyncio.Lock()

    def _get_candle_start(self, timestamp_ms, tf_sec):
        """Compute the start time of the candle for a given timestamp (ms) and timeframe (sec)."""
        return (timestamp_ms // (tf_sec * 1000)) * (tf_sec * 1000)

    def _build_streams(self):
        """Build list of stream names for the WebSocket connection."""
        streams = []
        for symbol in self.pairs:
            # Bybit v5 uses publicTrade for trade data
            streams.append(f"publicTrade.{symbol}")
        return [streams]  # We'll use a single connection for all streams

    async def _ws_connection(self, stream_names, conn_id):
        """Single WebSocket connection handler with reconnection."""
        streams_param = "/".join(stream_names)
        url = f"{self.WS_URL}?streams={streams_param}"
        symbol_map = {s.split(".")[1].upper(): s.split(".")[1].upper() for s in stream_names if "." in s}

        print(f"[trades_ws:{conn_id}] Connecting to {len(stream_names)} symbols...")
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    print(f"[trades_ws:{conn_id}] Connected ({len(stream_names)} symbols)")
                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(msg)
                            topic = data.get("topic", "")
                            # topic format: "publicTrade.BTCUSDT"
                            if not topic.startswith("publicTrade."):
                                continue
                            symbol = topic.split(".")[1].upper()
                            if symbol not in self.pairs:
                                continue
                            trades = data.get("data", [])
                            for trade in trades:
                                await self._process_trade(symbol, trade)
                        except Exception as e:
                            print(f"[trades_ws:{conn_id}] Error processing message: {e}")
            except websockets.ConnectionClosed:
                print(f"[trades_ws:{conn_id}] Connection closed, reconnecting in 5s...")
            except Exception as e:
                print(f"[trades_ws:{conn_id}] Error: {e}, reconnecting in 5s...")
            if self._running:
                await asyncio.sleep(5)

    async def _process_trade(self, symbol, trade):
        """Process a single trade and update current candle aggregates."""
        try:
            # Trade fields: T (timestamp), S (symbol), s (side), v (volume), p (price)
            timestamp_ms = int(trade["T"])
            side = trade["s"]  # "B" for Buy, "S" for Sell
            volume = float(trade["v"])
            price = float(trade["p"])

            quote_volume = volume * price

            # Update each timeframe
            for tf_name, tf_sec in self.timeframes.items():
                start = self._get_candle_start(timestamp_ms, tf_sec)
                key = (symbol, tf_name)
                async with self._lock:
                    if key not in self.current:
                        self.current[key] = {
                            "start": start,
                            "buy_base": 0.0,
                            "buy_quote": 0.0,
                        }
                    candle = self.current[key]
                    if candle["start"] != start:
                        # Candle has changed, write the previous candle
                        await self._write_candle(symbol, tf_name, candle)
                        # Reset for new candle
                        candle["start"] = start
                        candle["buy_base"] = 0.0
                        candle["buy_quote"] = 0.0
                    # Add current trade to the candle
                    if side == "B":
                        candle["buy_base"] += volume
                        candle["buy_quote"] += quote_volume
        except Exception as e:
            print(f"[trades_ws] Error processing trade for {symbol}: {e}")

    async def _write_candle(self, symbol, tf_name, candle):
        """Write a completed candle to the output file."""
        try:
            record = {
                "exchange": "bybit",
                "symbol": symbol,
                "tf": tf_name,
                "timestamp": candle["start"] // 1000,  # convert to seconds
                "taker_buy_base": candle["buy_base"],
                "taker_buy_quote": candle["buy_quote"],
            }
            line = json.dumps(record)
            async with self._lock:
                with open(OUTPUT_FILE, "a") as f:
                    f.write(line + "\n")
        except Exception as e:
            print(f"[trades_ws] Error writing candle for {symbol} {tf_name}: {e}")

    async def start(self):
        """Start all WebSocket connections."""
        self._running = True
        stream_groups = self._build_streams()
        print(f"[trades_ws] Starting {len(stream_groups)} connection(s) for {len(self.pairs)} pairs")
        for i, group in enumerate(stream_groups):
            task = asyncio.create_task(self._ws_connection(group, i))
            self._tasks.append(task)
        # Wait for first data to arrive
        await asyncio.sleep(10)

    async def stop(self):
        """Stop all connections and flush any pending candles."""
        self._running = False
        if self._tasks:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        # Flush any remaining candles (optional, but we can try to write the last incomplete candle as well?)
        # For now, we leave it; the next run will overwrite.
        print("[trades_ws] Stopped.")

    def get_latest_cvd(self, symbol, tf_name, timestamp_sec):
        """
        Get the latest CVD data for a given symbol, timeframe, and timestamp (in seconds).
        Returns a dict with taker_buy_base and taker_buy_quote, or None if not found.
        This is a simple file read; in production we might want to cache.
        """
        try:
            with open(OUTPUT_FILE, "r") as f:
                lines = f.readlines()
            # We want the latest candle for the given symbol and tf that matches or is before timestamp_sec
            # Since the file is append-only, we can iterate from the end.
            for line in reversed(lines):
                record = json.loads(line.strip())
                if record["symbol"] == symbol.upper() and record["tf"] == tf_name:
                    # The record's timestamp is the start of the candle in seconds
                    if record["timestamp"] <= timestamp_sec:
                        return {
                            "taker_buy_base": record["taker_buy_base"],
                            "taker_buy_quote": record["taker_buy_quote"],
                        }
            return None
        except Exception as e:
            print(f"[trades_ws] Error reading CVD data: {e}")
            return None

def get_pairs_from_env():
    """Get list of pairs from environment variable TRADES_PAIRS, comma-separated."""
    pairs_str = os.getenv("TRADES_PAIRS", "")
    if pairs_str:
        return [p.strip().upper() for p in pairs_str.split(",") if p.strip()]
    # Default list of major pairs
    return [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT",
        "XRPUSDT", "ADAUSDT", "DOTUSDT", "MATICUSDT", "LTCUSDT"
    ]

async def _main():
    print("=== Bybit Trades WebSocket Collector ===")
    pairs = get_pairs_from_env()
    print(f"Collecting trades for {len(pairs)} pairs: {', '.join(pairs)}")
    collector = TradesCollectorWS(pairs=pairs)
    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        print("\nReceived shutdown signal...")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await collector.start()
        await stop_event.wait()
    finally:
        await collector.stop()

if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nCollector interrupted by user")