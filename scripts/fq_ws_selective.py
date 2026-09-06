"""Selective closed-M15 shadow scanner. No state mutation, orders, or Telegram."""
from __future__ import annotations
import asyncio,json,os,time
from pathlib import Path
from typing import Any,Awaitable,Callable
import pandas as pd
from bots.nexus_data import fetch_recent
from fusion_quantum.paper_runner import lifecycle_scan,setup_id
from scripts.bybit_ws_observer import observe
ROOT=Path(__file__).resolve().parents[1]
AUDIT=Path(os.getenv('FQ_WS_SELECTIVE_AUDIT','runtime/fusion_quantum/ws_selective.jsonl'))

def expected_close_ms(start:int)->int:return start+15*60*1000

class CandleCache:
 def __init__(self,h4:dict[str,pd.DataFrame],m15:dict[str,pd.DataFrame]):
  self.frames={'4h':h4,'15m':m15}
  for frames in self.frames.values():
   for df in frames.values():df['open_time']=pd.to_datetime(df.open_time,utc=True)
 def upsert(self,symbol:str,timeframe:str,row:dict[str,Any])->None:
  row=dict(row)
  if isinstance(row.get('open_time'),int):row['open_time']=pd.to_datetime(row['open_time'],unit='ms',utc=True)
  df=pd.concat([self.frames[timeframe][symbol],pd.DataFrame([row])],ignore_index=True)
  df['open_time']=pd.to_datetime(df.open_time,utc=True)
  self.frames[timeframe][symbol]=df.drop_duplicates('open_time',keep='last').sort_values('open_time').tail(1000).reset_index(drop=True)
 def snapshot(self,symbol:str,timeframe:str,cutoff:pd.Timestamp)->pd.DataFrame:
  df=self.frames[timeframe][symbol]
  return df[pd.to_datetime(df.open_time,utc=True)<=cutoff].copy()

class SerialWriter:
 def __init__(self):self.queue:asyncio.Queue[Any]=asyncio.Queue()
 async def submit(self,fn:Callable[...,Awaitable[Any]],*args:Any)->Any:
  future=asyncio.get_running_loop().create_future()
  await self.queue.put((fn,args,future))
  return await future
 async def run(self)->None:
  while True:
   item=await self.queue.get()
   if item is None:self.queue.task_done();return
   fn,args,future=item
   try:future.set_result(await fn(*args))
   except Exception as exc:future.set_exception(exc)
   finally:self.queue.task_done()
 async def close(self)->None:await self.queue.put(None)

class SelectiveDispatcher:
 def __init__(self,worker:Callable[[dict[str,Any]],Awaitable[None]],concurrency:int=8):
  self.worker=worker;self.sem=asyncio.Semaphore(concurrency);self.seen:set[tuple[str,int,str]]=set()
 async def dispatch(self,event:dict[str,Any])->None:
  key=(event['symbol'],event['start'],event.get('timeframe','15m'))
  if key in self.seen:return
  self.seen.add(key)
  if len(self.seen)>5000:self.seen=set(sorted(self.seen,key=lambda x:x[1])[-2500:])
  async with self.sem:await self.worker(event)

def append(row:dict[str,Any])->None:
 AUDIT.parent.mkdir(parents=True,exist_ok=True)
 with AUDIT.open('a') as f:f.write(json.dumps(row,default=str)+'\n')

CACHE:CandleCache|None=None

async def preload(syms:list[str],concurrency:int=24)->CandleCache:
 sem=asyncio.Semaphore(concurrency);h4={};m15={}
 async def one(symbol:str)->None:
  async with sem:
   h,l=await asyncio.gather(asyncio.to_thread(fetch_recent,symbol,'4h',1000),asyncio.to_thread(fetch_recent,symbol,'15m',1000))
   h4[symbol]=h;m15[symbol]=l
 await asyncio.gather(*(one(s) for s in syms))
 return CandleCache(h4,m15)

async def scan(event:dict[str,Any])->None:
 t=time.time();symbol=event['symbol'];start=event['start'];expected=pd.to_datetime(start,unit='ms',utc=True)
 try:
  if CACHE is None or event.get('candle') is None:raise RuntimeError('warm cache/event candle unavailable')
  timeframe=event.get('timeframe','15m')
  CACHE.upsert(symbol,timeframe,event['candle'])
  if timeframe=='4h':
   append({'at':pd.Timestamp.now(tz='UTC'),'event':'h4_cache_update','symbol':symbol,'candle_start':start,'event_to_result_sec':round(time.time()-event['received_at'],3),'observer_only':True})
   return
  h=CACHE.snapshot(symbol,'4h',expected);snap=CACHE.snapshot(symbol,'15m',expected)
  latest=pd.to_datetime(snap.open_time.iloc[-1],utc=True) if len(snap) else None
  if latest != expected:raise RuntimeError(f'exact candle unavailable expected={expected} latest={latest}')
  attempt=0
  result=lifecycle_scan(symbol,h,snap);setups=[];seen=set()
  for s in result.setups:
   sid=setup_id(s)
   if sid not in seen:seen.add(sid);setups.append({'setup_id':sid,'side':s['side'],'confirmed_at':s['confirmed_at'],'entry_price':s['entry_price']})
  append({'at':pd.Timestamp.now(tz='UTC'),'event':'result','symbol':symbol,'candle_start':start,'event_to_result_sec':round(time.time()-event['received_at'],3),'attempts':attempt+1,'latest':latest,'setups':setups,'errors':0,'observer_only':True})
 except Exception as exc:
  append({'at':pd.Timestamp.now(tz='UTC'),'event':'error','symbol':symbol,'candle_start':start,'event_to_result_sec':round(time.time()-event['received_at'],3),'error':repr(exc),'observer_only':True})

async def main()->None:
 global CACHE
 syms=sorted(set(x.strip() for x in open(os.getenv('FQ_UNIVERSE_FILE',ROOT/'runtime/revo/canonical_universe.txt')) if x.strip()))
 started=time.time();CACHE=await preload(syms,int(os.getenv('FQ_CACHE_PRELOAD_CONCURRENCY','24')))
 append({'at':pd.Timestamp.now(tz='UTC'),'event':'cache_ready','symbols':len(CACHE.frames['15m']),'preload_sec':round(time.time()-started,3),'observer_only':True})
 dispatcher=SelectiveDispatcher(scan,int(os.getenv('FQ_WS_CONCURRENCY','12')))
 await observe(syms,int(os.getenv('BYBIT_WS_DURATION','1800')),dispatcher.dispatch,include_h4=True)

if __name__=='__main__':asyncio.run(main())
