#!/usr/bin/env python3
import csv, io, json, subprocess
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
J=ROOT/'runtime/fusionnew/forward_trades_M30_both_fusionnew_m30_btc_corr.jsonl'
rows=[json.loads(x) for x in J.open()]
fills={(x['symbol'],x['side'],str(x['t_complete']),float(x['entry'])):x for x in rows if x.get('event')=='FILL'}
closes=[x for x in rows if x.get('event')=='CLOSE']

def candles(sym,start,end):
 q=f"SELECT extract(epoch from open_time),high,low,close FROM klines WHERE exchange='binance' AND symbol='{sym}' AND timeframe='5m' AND open_time >= '{start}' AND open_time <= '{end}' ORDER BY open_time"
 p=subprocess.run(['docker','exec','-i','nexus_timescaledb','psql','-U','nexus','-d','nexus','-At','-F','\t','-c',q],text=True,capture_output=True,check=True)
 return [[float(v) for v in r] for r in csv.reader(io.StringIO(p.stdout),delimiter='\t')]

def replay(t):
 if t['side']!='BUY': return None
 k=(t['symbol'],t['side'],str(t['t_complete']),float(t['entry'])); f=fills[k]
 bars=candles(t['symbol'],f['ts'],t['ts']); entry,sl,tp=map(float,(t['entry'],t['sl'],t['tp'])); risk=entry-sl; floor=None;peak=0
 for _,hi,lo,close in bars:
  if lo<=sl:return {'reason':'SL','r':-1}
  if hi>=tp:return {'reason':'TP','r':3}
  if floor is not None and close<=entry+floor*risk:return {'reason':'SMART','r':floor}
  peak=max(peak,(close-entry)/risk)
  if peak>=2:floor=1
  elif peak>=1:floor=.25
 return {'reason':'OPEN','r':(float(t['exit'])-entry)/risk}
out=[]
for t in closes:
 z=replay(t)
 if z:out.append({'symbol':t['symbol'],'actual_pnl':t['pnl'],**z})
print(json.dumps({'n_buy':len(out),'actual_net':sum(x['actual_pnl'] for x in out),'counterfactual_r':sum(x['r'] for x in out),'outcomes':{k:sum(x['reason']==k for x in out) for k in ('SL','TP','SMART','OPEN')},'rows':out},indent=2))
