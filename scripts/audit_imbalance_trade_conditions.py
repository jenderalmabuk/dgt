#!/usr/bin/env python3
"""Causal winner/loss audit from archived CLOSE events + local TimescaleDB candles."""
from __future__ import annotations
import csv, io, json, math, random, statistics, subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINES = {
    "legacy_h1": ROOT / "runtime/state/forward_trades_H1_both_h1_adversarial.jsonl",
    "fusionnew_m30": ROOT / "runtime/fusionnew/forward_trades_M30_both_fusionnew_m30.jsonl",
}
OUT = ROOT / "runtime/imbalance/audits/deep_trade_conditions_20260811.json"

def parse_ts(v):
    if not v: return None
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)

def load_closes(path):
    rows=[]
    for line in path.read_text().splitlines():
        try: rows.append(json.loads(line))
        except json.JSONDecodeError: continue
    fills={}
    for r in rows:
        if r.get("event") == "FILL":
            key=(r.get("symbol"),r.get("side"),str(r.get("t_complete")),float(r.get("entry",0)))
            fills.setdefault(key,r.get("ts"))
    out=[]
    for r in rows:
        if r.get("event") == "CLOSE" and isinstance(r.get("pnl"), (int,float)):
            key=(r.get("symbol"),r.get("side"),str(r.get("t_complete")),float(r.get("entry",0)))
            r=dict(r); r["fill_ts"]=fills.get(key)
            out.append(r)
    return out

def pg(sql):
    cmd=["docker","exec","-i","nexus_timescaledb","psql","-U","nexus","-d","nexus","-At","-F","\t","-c",sql]
    p=subprocess.run(cmd,text=True,capture_output=True,timeout=180,check=True)
    return list(csv.reader(io.StringIO(p.stdout),delimiter="\t"))

def f(v):
    try:return float(v)
    except:return math.nan

def mean(xs):
    z=[x for x in xs if math.isfinite(x)]
    return statistics.mean(z) if z else math.nan

def med(xs):
    z=[x for x in xs if math.isfinite(x)]
    return statistics.median(z) if z else math.nan

def pct(a,b): return a/b-1 if b and math.isfinite(a) and math.isfinite(b) else math.nan

def candles(symbol, tf, start, end):
    q=lambda x:x.replace("'","''")
    sql=("SELECT extract(epoch from open_time),open,high,low,close,volume,quote_vol "
         f"FROM klines WHERE exchange='binance' AND symbol='{q(symbol)}' AND timeframe='{q(tf)}' "
         f"AND open_time >= '{start.isoformat()}' AND open_time <= '{end.isoformat()}' ORDER BY open_time")
    return [[f(x) for x in row] for row in pg(sql)]

def ema(vals,n):
    if not vals:return math.nan
    a=2/(n+1); e=vals[0]
    for x in vals[1:]:e=a*x+(1-a)*e
    return e

def atr(rows,n=14):
    if len(rows)<2:return math.nan
    tr=[]
    for i in range(1,len(rows)):
        _,o,h,l,c,*_=rows[i]; pc=rows[i-1][4]
        tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    return mean(tr[-n:])

def features(trade, zone_tf, btc_cache):
    tc=parse_ts(trade.get("t_complete")) or parse_ts(trade["ts"])
    close_ts=parse_ts(trade["ts"])
    fill_ts=parse_ts(trade.get("fill_ts"))
    pre=candles(trade["symbol"],zone_tf,tc-timedelta(hours=240),tc)
    post=candles(trade["symbol"],"5m",fill_ts,close_ts) if fill_ts else []
    entry,sl,tp=map(float,(trade["entry"],trade["sl"],trade["tp"]))
    side=trade["side"]
    out={"symbol":trade["symbol"],"side":side,"pnl":trade["pnl"],"win":trade["pnl"]>0,
         "t_complete":tc.isoformat(),"fill_ts":fill_ts.isoformat() if fill_ts else None,"close_ts":close_ts.isoformat(),
         "sl_pct":abs(entry-sl)/entry*100,"rr":abs(tp-entry)/abs(entry-sl),"oi_chg6":trade.get("oi_chg6")}
    if len(pre)>=20:
        closes=[r[4] for r in pre]; vols=[r[5] for r in pre]; last=pre[-1]; A=atr(pre)
        out.update({
          "pair_ret_1":pct(closes[-1],closes[-2]),
          "pair_ret_4":pct(closes[-1],closes[-5]) if len(closes)>=5 else math.nan,
          "pair_ret_12":pct(closes[-1],closes[-13]) if len(closes)>=13 else math.nan,
          "ema50_dist":(closes[-1]/ema(closes[-50:],50)-1) if len(closes)>=50 else math.nan,
          "ema200_dist":(closes[-1]/ema(closes[-200:],200)-1) if len(closes)>=200 else math.nan,
          "atr_pct":A/closes[-1]*100 if A else math.nan,
          "body_atr":abs(last[4]-last[1])/A if A else math.nan,
          "volume_ratio":last[5]/med(vols[-21:-1]) if med(vols[-21:-1]) else math.nan,
        })
    bkey=(int(tc.timestamp()),zone_tf)
    if bkey not in btc_cache:
        btc_cache[bkey]=candles("BTCUSDT",zone_tf,tc-timedelta(hours=240),tc)
    br=btc_cache[bkey]
    if len(br)>=20:
        bc=[r[4] for r in br]
        out.update({"btc_ret_1":pct(bc[-1],bc[-2]),"btc_ret_4":pct(bc[-1],bc[-5]) if len(bc)>=5 else math.nan,
                    "btc_ret_12":pct(bc[-1],bc[-13]) if len(bc)>=13 else math.nan,
                    "btc_ema50_dist":bc[-1]/ema(bc[-50:],50)-1 if len(bc)>=50 else math.nan})
    if post:
        highs=[r[2] for r in post]; lows=[r[3] for r in post]
        if side=="BUY": mfe=(max(highs)-entry)/abs(entry-sl); mae=(entry-min(lows))/abs(entry-sl)
        else: mfe=(entry-min(lows))/abs(entry-sl); mae=(max(highs)-entry)/abs(entry-sl)
        out.update({"mfe_r":mfe,"mae_r":mae,"post_bars_5m":len(post),"reached_1r":mfe>=1,"reached_2r":mfe>=2})
    return out

def summary(rows):
    pnl=[r["pnl"] for r in rows]; wins=[x for x in pnl if x>0]; loss=[x for x in pnl if x<0]
    return {"n":len(rows),"wins":len(wins),"wr":len(wins)/len(rows) if rows else math.nan,
            "net":sum(pnl),"pf":sum(wins)/-sum(loss) if loss else math.inf,"expectancy":mean(pnl)}

def profile(rows):
    keys=["sl_pct","pair_ret_1","pair_ret_4","pair_ret_12","ema50_dist","ema200_dist","atr_pct","body_atr","volume_ratio",
          "btc_ret_1","btc_ret_4","btc_ret_12","btc_ema50_dist","oi_chg6","mfe_r","mae_r"]
    return {k:{"win_median":med([r.get(k,math.nan) for r in rows if r["win"]]),
               "loss_median":med([r.get(k,math.nan) for r in rows if not r["win"]]),
               "win_mean":mean([r.get(k,math.nan) for r in rows if r["win"]]),
               "loss_mean":mean([r.get(k,math.nan) for r in rows if not r["win"]])} for k in keys}

def split_test(rows,key,threshold,op):
    yes=[r for r in rows if math.isfinite(f(r.get(key))) and (f(r[key])>=threshold if op==">=" else f(r[key])<threshold)]
    no=[r for r in rows if r not in yes]
    if not yes or not no:return None
    obs=sum(r["pnl"] for r in yes); allp=[r["pnl"] for r in rows]; k=len(yes); random.seed(20260811); ge=0
    for _ in range(10000):
        s=random.sample(allp,k); ge+=sum(s)>=obs
    return {"rule":f"{key}{op}{threshold}","yes":summary(yes),"no":summary(no),"permutation_p":(ge+1)/10001}

def main():
    result={"generated_utc":datetime.now(timezone.utc).isoformat(),"source":"local TimescaleDB closed candles","engines":{}}
    btc={}
    for name,path in ENGINES.items():
        tf="1h" if name=="legacy_h1" else "30m"
        rows=[]; errors=[]
        for i,t in enumerate(load_closes(path),1):
            try: rows.append(features(t,tf,btc)); print(name,i,len(load_closes(path)),t['symbol'],flush=True)
            except Exception as e: errors.append({"symbol":t.get("symbol"),"error":str(e)})
        tests=[]
        # Predeclared mechanism cuts; descriptive only until fresh OOS.
        for spec in [("sl_pct",1.5,">="),("volume_ratio",1.0,">="),("body_atr",0.5,">="),("btc_ret_4",0.0,"<"),("pair_ret_4",0.0,"<")]:
            z=split_test(rows,*spec)
            if z:tests.append(z)
        result["engines"][name]={"baseline":summary(rows),"by_side":{s:summary([r for r in rows if r['side']==s]) for s in ('BUY','SELL')},
          "winner_loss_profile":profile(rows),"candidate_splits":tests,"trades":rows,"errors":errors}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(result,indent=2,allow_nan=True))
    print(OUT)
if __name__=="__main__":main()
