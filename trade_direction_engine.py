"""Consolidated, evidence-aware index option direction view and image card."""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

SUPPORTED = {"NIFTY", "BANKNIFTY", "FINNIFTY"}


def _safe(value: Any, default: float = 0.0) -> float:
    try: return float(value)
    except Exception: return default


def _technical(symbol: str) -> Dict[str, Any]:
    try:
        from data_fetcher import DataFetcher
        angel = None
        try:
            from angel import AngelOne
            angel = AngelOne(api_key=os.getenv("API_KEY", ""), client_id=os.getenv("CLIENT_ID", ""),
                             password=os.getenv("PASSWORD", ""), totp_secret=os.getenv("TOTP_SECRET", ""))
        except Exception: pass
        df = DataFetcher(angel=angel, paper_trade=False).get_market_data(symbol, "15m", 10)
        if df is None or len(df) < 30: return {"available":False,"score":0.0}
        cols={str(c).lower():c for c in df.columns}; close=df[cols["close"]].astype(float)
        high=df[cols["high"]].astype(float); low=df[cols["low"]].astype(float)
        ema20=close.ewm(span=20,adjust=False).mean(); ema50=close.ewm(span=50,adjust=False).mean()
        delta=close.diff(); gain=delta.clip(lower=0).rolling(14).mean(); loss=(-delta.clip(upper=0)).rolling(14).mean()
        rsi=float((100-100/(1+gain/loss.replace(0,1e-9))).iloc[-1])
        macd=close.ewm(span=12,adjust=False).mean()-close.ewm(span=26,adjust=False).mean()
        atr=float((high-low).rolling(14).mean().iloc[-1]); spot=float(close.iloc[-1])
        score=(1 if spot>ema20.iloc[-1] else -1)+(1 if ema20.iloc[-1]>ema50.iloc[-1] else -1)
        score += 1 if macd.iloc[-1]>0 else -1
        score += 1 if rsi>=55 else -1 if rsi<=45 else 0
        return {"available":True,"score":score,"spot":spot,"rsi":rsi,"macd":float(macd.iloc[-1]),
                "ema20":float(ema20.iloc[-1]),"ema50":float(ema50.iloc[-1]),"atr":atr,
                "closes":[float(x) for x in close.tail(60)],
                "times":[str(x)[11:16] for x in df.index[-60:]]}
    except Exception as exc:
        return {"available":False,"score":0.0,"error":str(exc)[:120]}


def _external_context(symbol: str) -> Dict[str, Any]:
    result={"score":0.0,"fii_5d":0.0,"vix":0.0,"global_bias":"NEUTRAL","sector_bias":"NEUTRAL"}
    try:
        import pandas as pd
        f=pd.read_csv("fii_history.csv"); col=next((c for c in f.columns if "fii" in c.lower() and "net" in c.lower()),None)
        if col: result["fii_5d"]=float(f[col].tail(5).sum()); result["score"]+=1 if result["fii_5d"]>2000 else -1 if result["fii_5d"]<-2000 else 0
    except Exception: pass
    try:
        import pandas as pd
        v=pd.read_csv("vix_history.csv"); num=[c for c in v.columns if c.lower() not in {"date","time","timestamp"}][0]; result["vix"]=float(v[num].iloc[-1]); result["score"]+=-0.5 if result["vix"]>22 else 0.25 if 0<result["vix"]<15 else 0
    except Exception: pass
    try:
        raw=json.loads(Path("macro_global_sentiment_cache.json").read_text()); text=json.dumps(raw).upper()
        result["global_bias"]="BULLISH" if text.count("BULLISH")>text.count("BEARISH") else "BEARISH" if text.count("BEARISH")>text.count("BULLISH") else "NEUTRAL"; result["score"]+=1 if result["global_bias"]=="BULLISH" else -1 if result["global_bias"]=="BEARISH" else 0
    except Exception: pass
    try:
        raw=json.loads(Path("sector_rotation_cache.json").read_text()); text=json.dumps(raw).upper(); key="BANK" if symbol=="BANKNIFTY" else "FIN" if symbol=="FINNIFTY" else "NIFTY"; result["sector_bias"]="BULLISH" if key in text and "BULLISH" in text else "NEUTRAL"; result["score"]+=.5 if result["sector_bias"]=="BULLISH" else 0
    except Exception: pass
    return result


def build_direction(symbol: str = "NIFTY") -> Dict[str, Any]:
    symbol=str(symbol or "NIFTY").upper()
    if symbol not in SUPPORTED: symbol="NIFTY"
    tech=_technical(symbol); context=_external_context(symbol); oc=None; error=""
    try:
        from option_chain_fetcher import NSEOptionChainFetcher
        oc=NSEOptionChainFetcher(symbol).fetch_and_analyze()
    except Exception as exc: error=str(exc)[:160]
    summary=(oc.summary if oc else {}) or {}; spot=_safe(summary.get("spot"),_safe(tech.get("spot")))
    pcr=_safe(summary.get("pcr_oi"),1.0); bias=str(summary.get("net_bias","NEUTRAL")).upper()
    oi_score=2 if bias=="BULLISH" else -2 if bias=="BEARISH" else (1 if pcr>1.05 else -1 if pcr<0.9 else 0)
    tech_score=_safe(tech.get("score")); combined=tech_score+oi_score+_safe(context.get("score"))
    source=str(getattr(getattr(oc,"raw_json",{}),"get",lambda *_:"")("_provider_source","") if oc else "")
    cache=Path(f"option_chain_cache_{symbol.lower()}.json")
    freshness=max(0,time.time()-cache.stat().st_mtime) if cache.exists() else 999999
    stale=freshness>900
    confidence=min(92.0, 45+abs(combined)*7+(10 if oc else 0))
    if stale: confidence=min(confidence,45.0)
    action="WAIT"
    if combined>=3 and confidence>=55: action="BUY CALL"
    elif combined<=-3 and confidence>=55: action="BUY PUT"
    elif abs(combined)<=1: action="NO TRADE"
    option_type="CE" if action=="BUY CALL" else "PE" if action=="BUY PUT" else ""
    strike=int(_safe(summary.get("atm_strike"),0)); premium=0.0
    if oc is not None and strike:
        try:
            row=oc.dataframe.iloc[(oc.dataframe["strikePrice"]-strike).abs().argsort()[:1]].iloc[0]
            premium=_safe(row.get(f"{option_type}_lastPrice")) if option_type else 0.0
        except Exception: pass
    atr=_safe(tech.get("atr")); sl=round(premium*.80,2) if premium else 0.0; target=round(premium*1.35,2) if premium else 0.0
    reasons=[f"Technical score {tech_score:+.0f}",f"OI bias {bias}",f"PCR {pcr:.2f}",
             f"FII 5d ₹{context['fii_5d']:+,.0f}Cr",f"Global {context['global_bias']}",
             f"VIX {context['vix']:.1f}" if context['vix'] else "VIX unavailable"]
    if stale: reasons.append(f"STALE option data ({freshness/60:.0f}m); confidence capped")
    if not oc: reasons.append("Option chain unavailable; no trade levels")
    return {"symbol":symbol,"generated_at":datetime.now().isoformat(),"action":action,
            "confidence":round(confidence,1),"spot":spot,"technical":tech,"pcr":pcr,
            "oi_bias":bias,"max_pain":summary.get("max_pain"),"call_wall":summary.get("call_wall"),
            "put_wall":summary.get("put_wall"),"expiry":getattr(oc,"expiry","") if oc else "",
            "suggested_strike":strike,"option_type":option_type,"entry":premium,"sl":sl,"target":target,
            "freshness_sec":round(freshness,1),"stale":stale,"data_source":source or ("cache" if cache.exists() else "none"),
            "quality_score":round(max(0,100-freshness/18) if oc else 0,1),"reasons":reasons,"error":error}


def format_direction(result: Dict[str, Any]) -> str:
    emoji={"BUY CALL":"🟢","BUY PUT":"🔴","WAIT":"🟡","NO TRADE":"⚪"}.get(result["action"],"⚪")
    levels=(f"\nStrike: <b>{result['suggested_strike']}{result['option_type']}</b> ({result['expiry']})"
            f"\nEntry ₹{result['entry']:.1f} | SL ₹{result['sl']:.1f} | Target ₹{result['target']:.1f}"
            if result.get("entry") else "")
    return (f"{emoji} <b>{result['symbol']} — {result['action']}</b>\n"
            f"Confidence: {result['confidence']:.0f}% | Spot {result['spot']:,.1f}\n"
            f"PCR {result['pcr']:.2f} | OI {result['oi_bias']} | Quality {result['quality_score']:.0f}%"
            f"{levels}\n\n"+"\n".join(f"• {x}" for x in result["reasons"]))


def generate_direction_card(result: Dict[str, Any], output_dir: Optional[str]=None) -> str:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig,(ax1,ax2)=plt.subplots(2,1,figsize=(10,8),dpi=130,gridspec_kw={"height_ratios":[2,1]})
    fig.patch.set_facecolor("#08111f")
    for ax in (ax1,ax2): ax.set_facecolor("#101c2c"); ax.tick_params(colors="#aebdca"); ax.grid(color="#2b3b50",alpha=.4)
    closes=result.get("technical",{}).get("closes",[])
    if closes: ax1.plot(closes,color="#4dabf7",lw=2); ax1.text(.02,.92,f"Spot {result['spot']:,.1f}",transform=ax1.transAxes,color="white")
    else: ax1.text(.5,.5,"Price series unavailable",ha="center",color="#aebdca",transform=ax1.transAxes)
    ax1.set_title(f"{result['symbol']} PRICE / MOMENTUM",color="white",weight="bold")
    ax2.axis("off"); color="#51cf66" if result["action"]=="BUY CALL" else "#ff6b6b" if result["action"]=="BUY PUT" else "#ffd43b"
    ax2.text(.03,.78,result["action"],color=color,fontsize=25,weight="bold",transform=ax2.transAxes)
    ax2.text(.03,.52,f"Confidence {result['confidence']:.0f}%   PCR {result['pcr']:.2f}   OI {result['oi_bias']}",color="white",fontsize=12,transform=ax2.transAxes)
    ax2.text(.03,.27," | ".join(result["reasons"][:3]),color="#b8c7d9",fontsize=10,transform=ax2.transAxes)
    if result.get("entry"): ax2.text(.03,.05,f"{result['suggested_strike']}{result['option_type']}  Entry ₹{result['entry']:.1f}  SL ₹{result['sl']:.1f}  Target ₹{result['target']:.1f}",color="white",fontsize=11,transform=ax2.transAxes)
    fig.suptitle(f"TRADE DIRECTION • {result['generated_at'][:16].replace('T',' ')}",color="white",weight="bold")
    out=Path(output_dir or tempfile.gettempdir()); out.mkdir(parents=True,exist_ok=True)
    path=out/f"trade_direction_{result['symbol']}.png"; plt.tight_layout(rect=(0,0,1,.95)); fig.savefig(path,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); return str(path)
