import asyncio
from datetime import timedelta

from api import main
from bots import nexus_scanner
from collector import oi_rollup


def test_flow_all_uses_fresh_db_universe(monkeypatch):
    calls = []

    async def fake_query(sql, *args):
        calls.append((sql, args))
        return [{"symbol": "HUSDT"}, {"symbol": "AVAAIUSDT"}]

    async def fake_flow(symbol, exchange):
        return {"pair": symbol, "data_ready": True, "flow_direction": "BOTH_ALLOWED"}

    monkeypatch.setattr(main, "query", fake_query)
    monkeypatch.setattr(main, "_compute_flow", fake_flow)
    result = asyncio.run(main.get_all_flow(exchange="bybit", max_age_sec=900))
    assert set(result["pairs"]) == {"HUSDT", "AVAAIUSDT"}
    sql, args = calls[0]
    assert "MAX(open_time)" in sql and "NOW() -" in sql
    assert "LIMIT" not in sql
    assert args == ("bybit", "1m", 900)


def test_scanner_requests_all_fresh_bybit_flow():
    class Client:
        def get(self, url, params, timeout):
            class Response:
                def raise_for_status(self): pass
                def json(self): return {}
            assert url.endswith("/flow/all")
            assert params == {"exchange": "bybit", "max_age_sec": nexus_scanner.FRESH_KLINE_MAX_AGE_SEC}
            return Response()

    nexus_scanner.fetch_json(Client(), "/flow/all", exchange="bybit",
                             max_age_sec=nexus_scanner.FRESH_KLINE_MAX_AGE_SEC)


def test_rollup_passes_timedelta_to_asyncpg_interval():
    assert all(isinstance(interval, timedelta) for _, _, interval in oi_rollup.ROLLUPS)
