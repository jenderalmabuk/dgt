"""Fresh H4+M15 expiry sweep. Entry/SL/TP/RR/lifecycle fixed."""
from __future__ import annotations
import json,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import pandas as pd
from bots.nexus_data import fetch_recent
import fusion_quantum.backtest_quantum as bt
from scripts.fq_delay_ablation import simulate_delay,split
ROOT=Path(__file__).resolve().parents[1]
EXPIRIES=(3,6,9,12,18,24,36,48)
DELAYS=(0,2)

def normalized(df):
 if df.empty:return df
 x=df.copy();x['open_time']=pd.to_datetime(x.open_time,utc=True)
 return x.sort_values('open_time').drop_duplicates('open_time',keep='last').reset_index(drop=True)

def main():
 raw=pd.read_parquet('/tmp/fq_klines.parquet')
 old={}
 for (s,tf),g in raw.groupby(['symbol','timeframe'],sort=False):old[(s,tf)]=normalized(g)
 syms=[x.strip() for x in open(ROOT/'runtime/revo/canonical_universe.txt') if x.strip()]
 data={};errors=[]
 def load(s):
  fresh4=normalized(fetch_recent(s,'4h',1000));fresh15=normalized(fetch_recent(s,'15m',1000))
  old4=bt.resample_ohlcv(old.get((s,'1h'),pd.DataFrame()),'4h')
  old15=bt.resample_ohlcv(old.get((s,'5m'),pd.DataFrame()),'15min')
  return s,normalized(pd.concat([old4,fresh4])),normalized(pd.concat([old15,fresh15]))
 with ThreadPoolExecutor(max_workers=12) as ex:
  fs={ex.submit(load,s):s for s in syms}
  for n,f in enumerate(as_completed(fs),1):
   try:
    s,h,l=f.result();data[(s,'4h')]=h;data[(s,'15m')]=l
   except Exception as e:errors.append({'symbol':fs[f],'error':repr(e)})
   if n%100==0:print('fetch',n,flush=True)
 bt.DATA=data
 out={'generated_at':str(pd.Timestamp.now(tz='UTC')),'symbols':len(syms),'fetch_errors':errors,'coverage':{},'scenarios':{}}
 starts=[d.open_time.min() for (s,tf),d in data.items() if tf=='15m' and not d.empty];ends=[d.open_time.max() for (s,tf),d in data.items() if tf=='15m' and not d.empty]
 out['coverage']={'start':str(max(starts)) if starts else None,'end':str(min(ends)) if ends else None}
 for delay in DELAYS:
  for expiry in EXPIRIES:
   label=f'd{delay}_e{expiry}';t=time.time();rows=[];errs=[]
   with ThreadPoolExecutor(max_workers=10) as ex:
    fs={ex.submit(simulate_delay,s,delay,expiry,False):s for s in syms}
    for f in as_completed(fs):
     try:rows.append(f.result())
     except Exception as e:errs.append({'symbol':fs[f],'error':repr(e)})
   trades=[x for r in rows for x in r.get('trades',[])]
   funnel={k:sum(r.get('funnel',{}).get(k,0) for r in rows) for k in bt.ZoneLifecycle().funnel}
   res=split(trades);res.update({'delay_bars':delay,'expiry_bars':expiry,'errors':errs,'funnel':funnel,'fill_rate_pct':round(100*funnel['fills']/funnel['pending_orders'],1) if funnel['pending_orders'] else None,'elapsed_sec':round(time.time()-t,1)})
   out['scenarios'][label]=res;a=res['all'];print(label,'n',a['n'],'wr',a['wr'],'pf',a['pf'],'net',a['net_unit'],'dd',a['maxdd_unit'],'oos_pf',res['oos']['pf'],'fill',res['fill_rate_pct'],'err',len(errs),flush=True)
 dest=ROOT/'fusion_quantum/results/h4_m15_expiry_sweep_fresh.json';dest.write_text(json.dumps(out,indent=2));print('written',dest)
if __name__=='__main__':main()
