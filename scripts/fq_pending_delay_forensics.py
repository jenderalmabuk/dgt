"""Audit clean pending orders for causal placement-delay missed fills."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import pandas as pd


def fetch_1m(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
 q=urlencode({'category':'linear','symbol':symbol,'interval':'1','start':int(start.timestamp()*1000),'end':int(end.timestamp()*1000),'limit':200})
 with urlopen('https://api.bybit.com/v5/market/kline?'+q,timeout=20) as r:
  payload=json.load(r)
 rows=payload['result']['list']
 return pd.DataFrame([{'open_time':pd.to_datetime(int(x[0]),unit='ms',utc=True),'open':float(x[1]),'high':float(x[2]),'low':float(x[3]),'close':float(x[4])} for x in rows]).sort_values('open_time')
ROOT=Path(__file__).resolve().parents[1]
CUT=pd.Timestamp('2026-07-28T13:29:40Z')

def main():
 rows=[]
 for line in open(ROOT/'runtime/fusion_quantum/paper_gateway_audit.jsonl'):
  try:d=json.loads(line)
  except json.JSONDecodeError:continue
  if d.get('event')!='pending' or pd.Timestamp(d['at'])<CUT:continue
  s=d.get('setup') or {}; confirmed=pd.Timestamp(s['confirmed_at'],tz='UTC'); close=confirmed+pd.Timedelta(minutes=15); placed=pd.Timestamp(d['at']); entry=float(s['entry_price']); side=s['side']; symbol=s['symbol']
  m=fetch_1m(symbol,close,placed)
  # Only fully/partly elapsed 1m bars after signal became knowable and before placement.
  gap=m[(m.open_time>=close)&(m.open_time<placed.floor('min'))]
  touched=bool(((gap.low<=entry) if side=='LONG' else (gap.high>=entry)).any()) if len(gap) else False
  first=None
  if touched:
   hit=gap[(gap.low<=entry) if side=='LONG' else (gap.high>=entry)]
   first=str(hit.iloc[0].open_time)
  rows.append({'symbol':symbol,'side':side,'confirmed_at':str(confirmed),'signal_close':str(close),'placed_at':str(placed),'delay_sec':round((placed-close).total_seconds(),1),'entry':entry,'gap_bars':len(gap),'touched_before_placement':touched,'first_touch':first,'gap_low':float(gap.low.min()) if len(gap) else None,'gap_high':float(gap.high.max()) if len(gap) else None})
 print(json.dumps(rows,indent=2))
 summary={'n':len(rows),'missed':sum(x['touched_before_placement'] for x in rows),'median_delay_sec':float(pd.Series([x['delay_sec'] for x in rows]).median()) if rows else None,'max_delay_sec':max((x['delay_sec'] for x in rows),default=None)}
 print('SUMMARY',json.dumps(summary))
 (ROOT/'fusion_quantum/results/pending_delay_forensics.json').write_text(json.dumps({'summary':summary,'rows':rows},indent=2))
if __name__=='__main__':main()
