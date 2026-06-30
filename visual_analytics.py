"""Reusable non-interactive market and option visual dashboards."""
from __future__ import annotations
import tempfile
from pathlib import Path
from typing import Optional

def technical_dashboard(df, symbol:str="NIFTY", output_dir:Optional[str]=None)->str:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    d=df.copy(); d.columns=[str(c).lower() for c in d.columns]; c=d["close"].astype(float); h=d["high"].astype(float); l=d["low"].astype(float); v=d.get("volume",c*0).astype(float)
    ema9=c.ewm(span=9,adjust=False).mean(); ema21=c.ewm(span=21,adjust=False).mean(); ema50=c.ewm(span=50,adjust=False).mean()
    delta=c.diff(); gain=delta.clip(lower=0).rolling(14).mean(); loss=(-delta.clip(upper=0)).rolling(14).mean(); rsi=100-100/(1+gain/loss.replace(0,np.nan)); macd=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean(); sig=macd.ewm(span=9,adjust=False).mean()
    fig,axes=plt.subplots(4,1,figsize=(12,10),dpi=120,sharex=True,gridspec_kw={"height_ratios":[3,1,1,1]}); fig.patch.set_facecolor("#08111f")
    for ax in axes: ax.set_facecolor("#101c2c"); ax.grid(color="#2b3b50",alpha=.4); ax.tick_params(colors="#b8c7d9")
    x=np.arange(len(d)); colors=np.where(c>=d["open"].astype(float),"#51cf66","#ff6b6b"); axes[0].vlines(x,l,h,color=colors,lw=.7); axes[0].bar(x,c-d["open"].astype(float),bottom=d["open"].astype(float),color=colors,width=.65); axes[0].plot(x,ema9,label="EMA9",color="#ffd43b"); axes[0].plot(x,ema21,label="EMA21",color="#4dabf7"); axes[0].plot(x,ema50,label="EMA50",color="#da77f2"); axes[0].legend(); axes[0].set_title(f"{symbol} CANDLESTICK + RAINBOW MOMENTUM",color="white",weight="bold")
    axes[1].bar(x,v,color=colors); axes[1].set_title("VOLUME / CVD PROXY",color="white")
    axes[2].plot(x,rsi,color="#ffd43b"); axes[2].axhline(70,color="#ff6b6b"); axes[2].axhline(30,color="#51cf66"); axes[2].set_title("RSI",color="white")
    axes[3].plot(x,macd,color="#4dabf7"); axes[3].plot(x,sig,color="#ff922b"); axes[3].fill_between(x,macd-sig,0,color=np.where((macd-sig)>=0,"#51cf66","#ff6b6b"),alpha=.5); axes[3].set_title("MACD",color="white")
    out=Path(output_dir or tempfile.gettempdir()); out.mkdir(parents=True,exist_ok=True); path=out/f"technical_dashboard_{symbol}.png"; plt.tight_layout(); fig.savefig(path,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); return str(path)

def option_dashboard(df, spot:float, symbol:str="NIFTY", output_dir:Optional[str]=None)->str:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from option_metrics_cache import compute_max_pain
    d=df.copy(); strikes=d["strikePrice"].astype(float); ce=d["CE_openInterest"].astype(float); pe=d["PE_openInterest"].astype(float); civ=d.get("CE_impliedVolatility",ce*0).astype(float); piv=d.get("PE_impliedVolatility",pe*0).astype(float); pain=compute_max_pain(d)
    fig,axes=plt.subplots(2,2,figsize=(13,9),dpi=120); fig.patch.set_facecolor("#08111f")
    for ax in axes.flat: ax.set_facecolor("#101c2c"); ax.grid(color="#2b3b50",alpha=.4); ax.tick_params(colors="#b8c7d9")
    axes[0,0].bar(strikes-.15,ce,width=.3,color="#ff6b6b",label="CE OI"); axes[0,0].bar(strikes+.15,pe,width=.3,color="#51cf66",label="PE OI"); axes[0,0].axvline(spot,color="#ffd43b",label="Spot"); axes[0,0].axvline(pain,color="white",ls="--",label="Max Pain"); axes[0,0].legend(); axes[0,0].set_title("OI DISTRIBUTION / MAX PAIN",color="white",weight="bold")
    heat=np.vstack([ce/max(ce.max(),1),pe/max(pe.max(),1)]); axes[0,1].imshow(heat,aspect="auto",cmap="magma"); axes[0,1].set_yticks([0,1],labels=["CE","PE"]); axes[0,1].set_title("OI HEATMAP",color="white",weight="bold")
    axes[1,0].plot(strikes,civ,color="#ff6b6b",label="CE IV"); axes[1,0].plot(strikes,piv,color="#51cf66",label="PE IV"); axes[1,0].legend(); axes[1,0].set_title("VOLATILITY SMILE",color="white",weight="bold")
    atm=float(strikes.iloc[(strikes-spot).abs().argsort().iloc[0]]); expiry=np.linspace(strikes.min(),strikes.max(),100); call=np.maximum(expiry-atm,0); put=np.maximum(atm-expiry,0); axes[1,1].plot(expiry,call,color="#51cf66",label=f"Long {atm:.0f} CE"); axes[1,1].plot(expiry,put,color="#ff6b6b",label=f"Long {atm:.0f} PE"); axes[1,1].legend(); axes[1,1].set_title("OPTIONS PAYOFF",color="white",weight="bold")
    fig.suptitle(f"{symbol} OPTION ANALYTICS",color="white",fontsize=16,weight="bold"); out=Path(output_dir or tempfile.gettempdir()); out.mkdir(parents=True,exist_ok=True); path=out/f"option_dashboard_{symbol}.png"; plt.tight_layout(rect=(0,0,1,.95)); fig.savefig(path,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); return str(path)

def sector_treemap(sectors:dict, output_dir:Optional[str]=None)->str:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    names=list(sectors); vals=[float(sectors[x] or 0) for x in names]; fig,ax=plt.subplots(figsize=(12,6),dpi=120); fig.patch.set_facecolor("#08111f"); ax.set_facecolor("#101c2c")
    colors=["#51cf66" if v>=0 else "#ff6b6b" for v in vals]; bars=ax.barh(names,vals,color=colors); ax.axvline(0,color="white",lw=.8); ax.tick_params(colors="white"); ax.set_title("SECTOR ROTATION TREEMAP",color="white",weight="bold")
    for b,v in zip(bars,vals): ax.text(v,b.get_y()+b.get_height()/2,f" {v:+.2f}%",va="center",color="white")
    out=Path(output_dir or tempfile.gettempdir()); out.mkdir(parents=True,exist_ok=True); path=out/"sector_treemap.png"; plt.tight_layout(); fig.savefig(path,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); return str(path)

def fundamental_radar(symbol:str, metrics:dict, output_dir:Optional[str]=None)->str:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    labels=list(metrics); values=[max(0,min(100,float(metrics[k] or 0))) for k in labels]; values+=values[:1]; angles=np.linspace(0,2*np.pi,len(labels),endpoint=False).tolist(); angles+=angles[:1]
    fig,ax=plt.subplots(figsize=(7,7),dpi=120,subplot_kw={"polar":True}); fig.patch.set_facecolor("#08111f"); ax.set_facecolor("#101c2c"); ax.plot(angles,values,color="#4dabf7",lw=2); ax.fill(angles,values,color="#4dabf7",alpha=.25); ax.set_xticks(angles[:-1],labels,color="white"); ax.tick_params(colors="#b8c7d9"); ax.set_ylim(0,100); ax.set_title(f"{symbol} FUNDAMENTAL RADAR",color="white",weight="bold")
    out=Path(output_dir or tempfile.gettempdir()); out.mkdir(parents=True,exist_ok=True); path=out/f"fundamental_radar_{symbol}.png"; fig.savefig(path,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); return str(path)
