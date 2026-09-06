"""Read-only ablation: require LTF OB -> BOS -> FVG inside HTF setup area."""
import sys, json
from collections import defaultdict
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/fusionnew")
import numpy as np, pandas as pd
from backtest.data import fetch_klines
from backtest.faithful_imbalance import (_valid_obs, _annotate_ob_invalidation, _trend,
    _trend_ok, _atr, _manage_exit, _filter_flow, _filter_ema_dist, _filter_liquidity,
    _filter_stochastic, compute_fibonacci_entry, RR, FIB_EXPIRY, MAX_HOLD, TIERS)

DAYS=int(sys.argv[1]) if len(sys.argv)>1 else 120
TIER=sys.argv[2] if len(sys.argv)>2 else "M30"
DIRECTION=sys.argv[3] if len(sys.argv)>3 else "short"
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT","LINKUSDT","AVAXUSDT","DOGEUSDT","ADAUSDT","BNBUSDT","LTCUSDT","FILUSDT"]
CFG={"H1":("1h","5m",12,1.0),"M30":("30m","3m",20,2.5)}

def ltf_structures(df, side):
    """Return LTF FVGs whose displacement has an immediately preceding OB and BOS."""
    o,h,l,c=[df[x].to_numpy() for x in ("open","high","low","close")]; t=df.open_time.to_numpy(); n=len(df); out=[]
    for i in range(10,n-3):
        # OB is last opposite candle before displacement; displacement is i+1..i+3.
        if side=="BEAR":
            if not (c[i]>o[i]): continue
            if not (l[i]>h[i+2] and c[i+1]<o[i+1]): continue
            if min(l[i+1:i+4]) >= min(l[i-10:i]): continue
            out.append({"ob_i":i,"ob_low":float(l[i]),"ob_high":float(h[i]),"ce":i+2,"t":t[i+2],"leg_low":float(l[i+2]),"leg_high":float(h[i])})
        else:
            if not (c[i]<o[i]): continue
            if not (h[i]<l[i+2] and c[i+1]>o[i+1]): continue
            if max(h[i+1:i+4]) <= max(h[i-10:i]): continue
            out.append({"ob_i":i,"ob_low":float(l[i]),"ob_high":float(h[i]),"ce":i+2,"t":t[i+2],"leg_low":float(l[i]),"leg_high":float(h[i+2])})
    return out

def setups(zone,ltf,side,rr=RR,floor=0):
    tr=_trend(zone); obs=_valid_obs(zone,side); _annotate_ob_invalidation(obs,zone,side); out=[]; atr=_atr(ltf)
    structs=ltf_structures(ltf,side)
    if not obs: return out
    for x in structs:
        if not _trend_ok(tr,x["t"],side): continue
        matches=[o for o in obs if o["t"]<x["t"] and (o["t_inv"] is None or o["t_inv"]>x["t"]) and x["leg_low"]<=o["zhigh"] and x["leg_high"]>=o["zlow"]]
        if not matches: continue
        ce=x["ce"]
        if ce < len(ltf)-1-FIB_EXPIRY: continue
        candle=pd.Series({"open":x["leg_high"],"high":x["leg_high"],"low":x["leg_low"],"close":x["leg_low"]})
        entry=compute_fibonacci_entry(candle,0.618)
        if side=="BEAR":
            sl=max(x["ob_high"],x["leg_high"])+0.5*atr[ce]; sl=max(sl,entry*(1+floor/100))
            if sl<=entry: continue
            risk=sl-entry; tp=entry-rr*risk
        else:
            sl=min(x["ob_low"],x["leg_low"])-0.5*atr[ce]; sl=min(sl,entry*(1-floor/100))
            if sl>=entry: continue
            risk=entry-sl; tp=entry+rr*risk
        out.append(dict(side=side,ce=ce,t_complete=x["t"],entry=float(entry),sl=float(sl),tp=float(tp),risk=float(risk),age=len(ltf)-1-ce))
    return out

def run(sym):
    ztf,ltf_tf,step,floor=CFG[TIER]; z=fetch_klines(sym,ztf,DAYS); l=fetch_klines(sym,ltf_tf,DAYS)
    if min(len(z),len(l))<260:return [],defaultdict(int)
    tr=_trend(z); ll,lh,lc=l.low.to_numpy(),l.high.to_numpy(),l.close.to_numpy(); atr=_atr(l); alltr=[]; f=defaultdict(int)
    for end in range(999,len(l)-FIB_EXPIRY-MAX_HOLD,step):
        zz=z[z.open_time<=l.open_time.iloc[end]].iloc[-300:].reset_index(drop=True); llw=l.iloc[max(0,end-999):end+1].reset_index(drop=True)
        if len(zz)<260:continue
        for side in (["BEAR"] if DIRECTION=="short" else ["BULL","BEAR"]):
            ss=setups(zz,llw,side,floor=floor); f["raw"]+=len(ss)
            ss=_filter_ema_dist(ss,zz,1.0); f["ema"]+=len(ss); ss=_filter_liquidity(ss,llw,1_000_000,at_idx=len(llw)-1); f["liq"]+=len(ss); ss=_filter_stochastic(ss,llw,side,70); f["stoch"]+=len(ss)
            for s in ss[-1:]:
                f["orders"]+=1; fill=None; start=end+1
                for q in range(start,min(start+FIB_EXPIRY,len(l))):
                    if (side=="BEAR" and lh[q]>=s["entry"]) or (side=="BULL" and ll[q]<=s["entry"]):fill=q;break
                if fill is None:f["expired"]+=1;continue
                f["fills"]+=1; pu,reason=_manage_exit(side,s["entry"],s["sl"],s["tp"],ll,lh,lc,atr,fill,"fixed"); alltr.append(dict(symbol=sym,side=side,time=l.open_time.iloc[fill],netR=pu/s["risk"],reason=reason))
    return alltr,f

def metric(ts):
    rs=[x["netR"] for x in sorted(ts,key=lambda x:x["time"])]
    gp=sum(x for x in rs if x>0); gl=abs(sum(x for x in rs if x<=0)); return dict(n=len(rs),wr=round(sum(x>0 for x in rs)/len(rs)*100,1) if rs else 0,pf=round(gp/gl,2) if gl else 999,netR=round(sum(rs),2),expR=round(sum(rs)/len(rs),3) if rs else 0)

alltr=[]; agg=defaultdict(int)
for s in SYMS:
    tr,f=run(s);alltr+=tr
    for k,v in f.items():agg[k]+=v
    print(s,len(tr),dict(f),flush=True)
print("RESULT",json.dumps({"tier":TIER,"days":DAYS,"direction":DIRECTION,"metric":metric(alltr),"funnel":dict(agg)},default=str))
json.dump({"tier":TIER,"days":DAYS,"direction":DIRECTION,"metric":metric(alltr),"funnel":dict(agg),"trades":alltr},open(f"/tmp/ab_ltf_ob_{TIER}_{DIRECTION}.json","w"),default=str,indent=2)
