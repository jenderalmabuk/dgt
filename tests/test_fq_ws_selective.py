import asyncio
import json
import pandas as pd
from scripts.bybit_ws_observer import parse_kline
from scripts.fq_ws_selective import CandleCache, SelectiveDispatcher, expected_close_ms


def test_closed_kline_carries_complete_ohlcv():
    msg={'topic':'kline.15.BTCUSDT','data':[{'start':1000,'confirm':True,'open':'1','high':'3','low':'0.5','close':'2','volume':'7'}]}
    event=parse_kline(msg)
    assert event['candle'] == {'open_time':1000,'open':1.0,'high':3.0,'low':0.5,'close':2.0,'volume':7.0}
    json.dumps(event)


def test_cache_normalizes_naive_preload_to_utc():
    base=pd.DataFrame([{'open_time':pd.Timestamp('2026-08-02 01:45'),'open':1.,'high':2.,'low':.5,'close':1.5,'volume':2.}])
    cache=CandleCache({'BTCUSDT':base.copy()},{'BTCUSDT':base.copy()})
    got=cache.snapshot('BTCUSDT','15m',pd.Timestamp('2026-08-02T02:00Z'))
    assert str(got.open_time.dt.tz)=='UTC'


def test_cache_upserts_and_deduplicates_closed_candle():
    base=pd.DataFrame([{'open_time':pd.Timestamp('2026-08-02T01:45Z'),'open':1.,'high':2.,'low':.5,'close':1.5,'volume':2.}])
    cache=CandleCache({'BTCUSDT':base},{'BTCUSDT':base})
    row={'open_time':pd.Timestamp('2026-08-02T02:00Z'),'open':1.5,'high':3.,'low':1.,'close':2.5,'volume':4.}
    cache.upsert('BTCUSDT','15m',row);cache.upsert('BTCUSDT','15m',{**row,'close':2.6})
    got=cache.snapshot('BTCUSDT','15m',row['open_time'])
    assert len(got)==2
    assert float(got.iloc[-1].close)==2.6


def test_positive_setup_parity_after_cache_upsert(monkeypatch):
    import numpy as np
    import fusion_quantum.paper_runner as runner
    t=pd.date_range('2026-01-01',periods=20,freq='15min',tz='UTC')
    l=pd.DataFrame({'open_time':t,'open':100.,'high':101.,'low':99.,'close':100.,'volume':1.})
    h=l.copy();zone={'t':1,'zlow':98.,'zhigh':102.}
    monkeypatch.setattr(runner,'_valid_obs',lambda df,side:[zone] if side=='BULL' else [])
    monkeypatch.setattr(runner,'_imbalances',lambda df,side:[{'ce':2,'t':3,'leg_low':99.,'leg_high':101.}] if side=='BULL' else [])
    monkeypatch.setattr(runner,'_atr',lambda df:np.ones(len(df)))
    monkeypatch.setattr(runner,'mss_confirm',lambda *a,**k:{'i':13,'sweep':98.,'disp':103.,'side':'BULL'})
    direct=[runner.setup_id(x) for x in runner.lifecycle_scan('TESTUSDT',h,l).setups]
    cache=CandleCache({'TESTUSDT':h.copy()},{'TESTUSDT':l.iloc[:-1].copy()})
    cache.upsert('TESTUSDT','15m',l.iloc[-1].to_dict())
    cached=[runner.setup_id(x) for x in runner.lifecycle_scan('TESTUSDT',cache.snapshot('TESTUSDT','4h',t[-1]),cache.snapshot('TESTUSDT','15m',t[-1])).setups]
    assert cached==direct and len(cached)==1


def test_expected_close_is_15_minutes_after_start():
    assert expected_close_ms(1_785_600_000_000) == 1_785_600_900_000


def test_delayed_confirmed_setup_remains_fresh_until_expiry():
    import fusion_quantum.paper_runner as runner
    setup={'confirmed_at':'2026-08-03T03:15:00Z','expires_at':'2026-08-03T04:45:00Z'}
    assert runner.setup_fresh_at(setup,pd.Timestamp('2026-08-03T03:30:00Z'))
    assert not runner.setup_fresh_at(setup,pd.Timestamp('2026-08-03T05:00:00Z'))


def test_dispatcher_deduplicates_inflight_symbol_candle():
    calls=[]
    async def work(event):
        calls.append((event['symbol'],event['start']))
    async def run():
        d=SelectiveDispatcher(work,concurrency=1)
        event={'symbol':'BTCUSDT','start':1000}
        await asyncio.gather(d.dispatch(event),d.dispatch(event))
    asyncio.run(run())
    assert calls == [('BTCUSDT',1000)]


def test_pending_manager_does_not_scan_full_universe(monkeypatch, tmp_path):
    import fusion_quantum.paper_runner as runner
    state=runner.State(tmp_path/'state.json')
    calls=[]
    monkeypatch.setattr(runner,'symbols',lambda:(_ for _ in ()).throw(AssertionError('full universe forbidden')))
    monkeypatch.setattr(runner,'fetch_recent',lambda symbol,tf,n: calls.append(symbol) or pd.DataFrame())
    class FakeGateway:
        async def portfolio(self): return {'open_symbols':[]}
    monkeypatch.setattr(runner,'GatewayClient',FakeGateway)
    asyncio.run(runner.manage_pending_once(state))
    assert calls==[]


def test_serial_writer_runs_mutations_one_at_a_time():
    from scripts.fq_ws_selective import SerialWriter
    active=peak=0
    async def mutate(value):
        nonlocal active,peak
        active+=1;peak=max(peak,active)
        await asyncio.sleep(0.01)
        active-=1
        return value
    async def run():
        writer=SerialWriter();task=asyncio.create_task(writer.run())
        got=await asyncio.gather(*(writer.submit(mutate,i) for i in range(6)))
        await writer.close();await task
        return got
    assert asyncio.run(run())==list(range(6))
    assert peak==1


def test_dispatcher_bounds_concurrency():
    active=peak=0
    async def work(event):
        nonlocal active,peak
        active+=1;peak=max(peak,active)
        await asyncio.sleep(0.01)
        active-=1
    async def run():
        d=SelectiveDispatcher(work,concurrency=2)
        await asyncio.gather(*(d.dispatch({'symbol':str(i),'start':i}) for i in range(6)))
    asyncio.run(run())
    assert peak == 2


def test_warm_scanner_h4_updates_cache_without_scan():
    import fusion_quantum.paper_runner as runner
    base=pd.DataFrame([{'open_time':pd.Timestamp('2026-08-02T00:00Z'),'open':1.,'high':2.,'low':.5,'close':1.5,'volume':2.}])
    cache=CandleCache({'BTCUSDT':base.copy()},{'BTCUSDT':base.copy()})
    calls=[]
    scanner=runner.WarmPaperScanner(object(),cache,asyncio.Lock(),lambda *args:calls.append(args))
    event={'symbol':'BTCUSDT','start':1785643200000,'timeframe':'4h','candle':{**base.iloc[0].to_dict(),'open_time':1785643200000}}
    asyncio.run(scanner.handle(event))
    assert calls == []
    assert len(cache.frames['4h']['BTCUSDT']) == 2


def test_warm_scanner_scans_memory_concurrently_but_serializes_mutation():
    import fusion_quantum.paper_runner as runner
    base=pd.DataFrame([{'open_time':pd.Timestamp('2026-08-02T00:00Z'),'open':1.,'high':2.,'low':.5,'close':1.5,'volume':2.}])
    cache=CandleCache({s:base.copy() for s in ('A','B')},{s:base.copy() for s in ('A','B')})
    scanning=scan_peak=mutating=mutation_peak=0
    async def scan(symbol,htf,ltf):
        nonlocal scanning,scan_peak
        scanning+=1;scan_peak=max(scan_peak,scanning);await asyncio.sleep(.01);scanning-=1
        return symbol
    async def mutate(state,symbol,ltf,result):
        nonlocal mutating,mutation_peak
        assert state is shared
        mutating+=1;mutation_peak=max(mutation_peak,mutating);await asyncio.sleep(.01);mutating-=1
    shared=object();scanner=runner.WarmPaperScanner(shared,cache,asyncio.Lock(),mutate,scan)
    async def run():
        await asyncio.gather(*(scanner.handle({'symbol':s,'start':1785629700000,'timeframe':'15m','candle':{**base.iloc[0].to_dict(),'open_time':1785629700000}}) for s in ('A','B')))
    asyncio.run(run())
    assert scan_peak == 2
    assert mutation_peak == 1
