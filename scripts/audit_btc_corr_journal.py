#!/usr/bin/env python3
import json, math, random, statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "runtime/fusionnew/forward_trades_M30_both_fusionnew_m30_btc_corr.jsonl"
OUT = ROOT / "runtime/imbalance/audits/btc_corr_journal_attribution_20260831.json"

def key(x):
    return x.get("symbol"), x.get("side") or ({"BULL":"BUY","BEAR":"SELL"}.get(x.get("side"))), str(x.get("t_complete"))

def stats(rows):
    pnl=[float(x["pnl"]) for x in rows]; w=[x for x in pnl if x>0]; l=[x for x in pnl if x<0]
    return {"n":len(rows),"wins":len(w),"wr":len(w)/len(rows) if rows else None,"pf":sum(w)/-sum(l) if l else None,"net":sum(pnl),"expectancy":statistics.mean(pnl) if pnl else None}

def main():
    setups={}; fills={}; closes=[]; corr_by_symbol=defaultdict(list); pressure_by_symbol=defaultdict(list)
    for line in JOURNAL.open():
        x=json.loads(line); e=x.get("event")
        if e=="SETUP": setups[key(x)]=x
        elif e=="FILL": fills[key(x)]=x
        elif e=="CLOSE": closes.append(x)
        elif e=="btc_corr_check": corr_by_symbol[(x.get("symbol"),x.get("side"))].append(x)
        elif e=="oi_pressure_check": pressure_by_symbol[(x.get("symbol"),x.get("side"))].append(x)
    rows=[]
    for c in closes:
        k=key(c); tc=str(c.get("t_complete")); target=datetime.fromisoformat(tc.replace("Z","+00:00")).replace(tzinfo=timezone.utc) if "+" not in tc and "Z" not in tc else datetime.fromisoformat(tc.replace("Z","+00:00"))
        imb=c.get("imb_side"); candidates=corr_by_symbol[(c.get("symbol"),imb)]
        prior=[x for x in candidates if datetime.fromisoformat(x["ts"].replace("Z","+00:00")) <= target]
        corr=prior[-1] if prior else None
        pc=pressure_by_symbol[(c.get("symbol"),imb)]; pprior=[x for x in pc if datetime.fromisoformat(x["ts"].replace("Z","+00:00")) <= target]
        pressure=pprior[-1] if pprior else None
        r={"symbol":c.get("symbol"),"side":c.get("side"),"t_complete":tc,"pnl":float(c["pnl"]),"win":float(c["pnl"])>0,"oi_chg6":c.get("oi_chg6")}
        if corr:r.update({"btc_regime":corr.get("btc_regime"),"corr":corr.get("corr"),"strength":corr.get("strength"),"weight":corr.get("weight"),"decoupled":corr.get("decoupled"),"note":corr.get("note"),"quality_ok":corr.get("quality_ok")})
        if pressure:r.update({"price_chg":pressure.get("price_chg"),"cvd_delta":pressure.get("cvd_delta"),"pressure_oi_chg":pressure.get("oi_chg"),"pressure_reason":pressure.get("reason")})
        rows.append(r)
    groups={}
    for field in ("side","btc_regime","strength","note","decoupled"):
        vals=defaultdict(list)
        for r in rows: vals[str(r.get(field))].append(r)
        groups[field]={k:stats(v) for k,v in vals.items()}
    # Side x regime and correlation buckets; all descriptive.
    vals=defaultdict(list)
    for r in rows: vals[f"{r.get('side')}|{r.get('btc_regime')}"] .append(r)
    groups["side_regime"]={k:stats(v) for k,v in vals.items()}
    vals=defaultdict(list)
    for r in rows:
        z=r.get("corr"); b="missing" if z is None else "negative" if z<0 else "0-0.3" if z<.3 else "0.3-0.7" if z<.7 else ">=0.7"
        vals[f"{r.get('side')}|{b}"].append(r)
    groups["side_corr_bucket"]={k:stats(v) for k,v in vals.items()}
    result={"generated_utc":datetime.now(timezone.utc).isoformat(),"coverage":{"closes":len(closes),"corr_joined":sum(r.get('corr') is not None for r in rows),"pressure_joined":sum(r.get('price_chg') is not None for r in rows)},"baseline":stats(rows),"groups":groups,"rows":rows}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,indent=2,allow_nan=True)); print(OUT)
if __name__=="__main__": main()
