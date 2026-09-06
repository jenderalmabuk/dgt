"""Closed-candle WebSocket and warm cache for Fusion Quantum paper runtime."""
from __future__ import annotations
import asyncio,inspect,json,os,time
from typing import Any,Callable
import aiohttp
import pandas as pd
from bots.nexus_data import fetch_recent

WS_URL=os.getenv('BYBIT_WS_URL','wss://stream.bybit.com/v5/public/linear')
BATCH=int(os.getenv('BYBIT_WS_BATCH','200'))

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

async def preload(symbols:list[str],concurrency:int=24)->CandleCache:
 sem=asyncio.Semaphore(concurrency);h4={};m15={}
 async def one(symbol:str)->None:
  async with sem:
   h,l=await asyncio.gather(asyncio.to_thread(fetch_recent,symbol,'4h',1000),asyncio.to_thread(fetch_recent,symbol,'15m',1000))
   h4[symbol]=h;m15[symbol]=l
 await asyncio.gather(*(one(s) for s in symbols))
 return CandleCache(h4,m15)

class Dispatcher:
 def __init__(self,worker:Callable[[dict[str,Any]],Any],concurrency:int=12):
  self.worker=worker;self.sem=asyncio.Semaphore(concurrency);self.seen:set[tuple[str,int,str]]=set()
 async def dispatch(self,event:dict[str,Any])->None:
  key=(event['symbol'],event['start'],event['timeframe'])
  if key in self.seen:return
  self.seen.add(key)
  if len(self.seen)>5000:self.seen=set(sorted(self.seen,key=lambda x:x[1])[-2500:])
  async with self.sem:
   result=self.worker(event)
   if inspect.isawaitable(result):await result

def parse_kline(msg:dict[str,Any])->dict[str,Any]|None:
 data=msg.get('data') or []
 if not data or data[0].get('confirm') is not True:return None
 parts=str(msg.get('topic','')).split('.');row=data[0]
 if len(parts)<3 or parts[-2] not in {'15','240'}:return None
 candle=None
 if all(k in row for k in ('open','high','low','close','volume')):
  candle={'open_time':int(row['start']),'open':float(row['open']),'high':float(row['high']),'low':float(row['low']),'close':float(row['close']),'volume':float(row['volume'])}
 return {'symbol':parts[-1],'start':int(row['start']),'timeframe':'4h' if parts[-2]=='240' else '15m','closed':True,'candle':candle}

async def observe(symbols:list[str],callback:Callable[[dict[str,Any]],Any],duration:int=315360000)->None:
 seen:set[tuple[str,int,str]]=set();deadline=time.monotonic()+duration;attempt=0
 async with aiohttp.ClientSession() as session:
  while time.monotonic()<deadline:
   try:
    async with session.ws_connect(WS_URL,heartbeat=20,autoping=True) as ws:
     topics=[f'kline.15.{s}' for s in symbols]+[f'kline.240.{s}' for s in symbols]
     for i in range(0,len(topics),BATCH):await ws.send_json({'op':'subscribe','args':topics[i:i+BATCH]})
     attempt=0
     while time.monotonic()<deadline:
      msg=await ws.receive(timeout=30)
      if msg.type!=aiohttp.WSMsgType.TEXT:
       if msg.type in {aiohttp.WSMsgType.CLOSED,aiohttp.WSMsgType.ERROR}:raise ConnectionError(msg.type.name)
       continue
      payload=json.loads(msg.data)
      if payload.get('success') is False:raise ConnectionError(f"subscribe rejected: {payload}")
      event=parse_kline(payload)
      if not event:continue
      key=(event['symbol'],event['start'],event['timeframe'])
      if key in seen:continue
      seen.add(key);event['received_at']=time.time();await callback(event)
   except (aiohttp.ClientError,asyncio.TimeoutError,ConnectionError) as exc:
    delay=min(30,2**attempt);attempt+=1
    print(json.dumps({'warm_ws_reconnect_in':delay,'error':repr(exc)}),flush=True)
    await asyncio.sleep(min(delay,max(0,deadline-time.monotonic())))
