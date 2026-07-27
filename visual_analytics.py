"""Reusable non-interactive market and option visual dashboards."""
from __future__ import annotations
import tempfile
from pathlib import Path
from typing import Optional

import chart_theme as ct

def technical_dashboard(df, symbol:str="NIFTY", output_dir:Optional[str]=None)->str:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    d=df.copy(); d.columns=[str(c).lower() for c in d.columns]; c=d["close"].astype(float); h=d["high"].astype(float); l=d["low"].astype(float); v=d.get("volume",c*0).astype(float)
    ema9=c.ewm(span=9,adjust=False).mean(); ema21=c.ewm(span=21,adjust=False).mean(); ema50=c.ewm(span=50,adjust=False).mean()
    delta=c.diff(); gain=delta.clip(lower=0).rolling(14).mean(); loss=(-delta.clip(upper=0)).rolling(14).mean(); rsi=100-100/(1+gain/loss.replace(0,np.nan)); macd=c.ewm(span=12,adjust=False).mean()-c.ewm(span=26,adjust=False).mean(); sig=macd.ewm(span=9,adjust=False).mean()
    fig,axes=plt.subplots(4,1,figsize=(12,10),dpi=120,sharex=True,gridspec_kw={"height_ratios":[3,1,1,1]}); ct.apply_theme(fig,axes)
    x=np.arange(len(d)); colors=np.where(c>=d["open"].astype(float),ct.BULLISH,ct.BEARISH); axes[0].vlines(x,l,h,color=colors,lw=.7); axes[0].bar(x,c-d["open"].astype(float),bottom=d["open"].astype(float),color=colors,width=.65); axes[0].plot(x,ema9,label="EMA9",color=ct.WARNING); axes[0].plot(x,ema21,label="EMA21",color=ct.INFO); axes[0].plot(x,ema50,label="EMA50",color=ct.CATEGORICAL[3]); axes[0].legend(); axes[0].set_title(f"{symbol} CANDLESTICK + RAINBOW MOMENTUM",color=ct.TEXT_PRIMARY,weight="bold")
    axes[1].bar(x,v,color=colors); axes[1].set_title("VOLUME / CVD PROXY",color=ct.TEXT_PRIMARY)
    axes[2].plot(x,rsi,color=ct.WARNING); axes[2].axhline(70,color=ct.BEARISH); axes[2].axhline(30,color=ct.BULLISH); axes[2].set_title("RSI",color=ct.TEXT_PRIMARY)
    axes[3].plot(x,macd,color=ct.INFO); axes[3].plot(x,sig,color=ct.CATEGORICAL[4]); axes[3].fill_between(x,macd-sig,0,color=np.where((macd-sig)>=0,ct.BULLISH,ct.BEARISH),alpha=.5); axes[3].set_title("MACD",color=ct.TEXT_PRIMARY)
    out=Path(output_dir or tempfile.gettempdir()); out.mkdir(parents=True,exist_ok=True); path=out/f"technical_dashboard_{symbol}.png"; plt.tight_layout(); fig.savefig(path,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); return str(path)

def option_dashboard(df, spot:float, symbol:str="NIFTY", output_dir:Optional[str]=None)->str:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from option_metrics_cache import compute_max_pain
    d=df.copy(); strikes=d["strikePrice"].astype(float); ce=d["CE_openInterest"].astype(float); pe=d["PE_openInterest"].astype(float); civ=d.get("CE_impliedVolatility",ce*0).astype(float); piv=d.get("PE_impliedVolatility",pe*0).astype(float); pain=compute_max_pain(d)
    fig,axes=plt.subplots(2,2,figsize=(13,9),dpi=120); ct.apply_theme(fig,axes)
    axes[0,0].bar(strikes-.15,ce,width=.3,color=ct.BEARISH,label="CE OI"); axes[0,0].bar(strikes+.15,pe,width=.3,color=ct.BULLISH,label="PE OI"); axes[0,0].axvline(spot,color=ct.WARNING,label="Spot"); axes[0,0].axvline(pain,color=ct.TEXT_PRIMARY,ls="--",label="Max Pain"); axes[0,0].legend(); axes[0,0].set_title("OI DISTRIBUTION / MAX PAIN",color=ct.TEXT_PRIMARY,weight="bold")
    heat=np.vstack([ce/max(ce.max(),1),pe/max(pe.max(),1)]); axes[0,1].imshow(heat,aspect="auto",cmap="magma"); axes[0,1].set_yticks([0,1],labels=["CE","PE"]); axes[0,1].set_title("OI HEATMAP",color=ct.TEXT_PRIMARY,weight="bold")
    axes[1,0].plot(strikes,civ,color=ct.BEARISH,label="CE IV"); axes[1,0].plot(strikes,piv,color=ct.BULLISH,label="PE IV"); axes[1,0].legend(); axes[1,0].set_title("VOLATILITY SMILE",color=ct.TEXT_PRIMARY,weight="bold")
    # Payoff colors deliberately NOT mapped to BULLISH/BEARISH: here green/red
    # mean "this leg's own payoff shape" (call profits as price rises, put as
    # it falls), not a resistance/support or market-direction call like the
    # OI panel above -- a different semantic that happens to reuse the same
    # two hues. See chart_theme.py's docstring on BULLISH/BEARISH being named
    # for meaning, not hue, precisely so this distinction stays legible.
    atm=float(strikes.iloc[(strikes-spot).abs().argsort().iloc[0]]); expiry=np.linspace(strikes.min(),strikes.max(),100); call=np.maximum(expiry-atm,0); put=np.maximum(atm-expiry,0); axes[1,1].plot(expiry,call,color="#51cf66",label=f"Long {atm:.0f} CE"); axes[1,1].plot(expiry,put,color="#ff6b6b",label=f"Long {atm:.0f} PE"); axes[1,1].legend(); axes[1,1].set_title("OPTIONS PAYOFF",color=ct.TEXT_PRIMARY,weight="bold")
    fig.suptitle(f"{symbol} OPTION ANALYTICS",color=ct.TEXT_PRIMARY,fontsize=16,weight="bold"); out=Path(output_dir or tempfile.gettempdir()); out.mkdir(parents=True,exist_ok=True); path=out/f"option_dashboard_{symbol}.png"; plt.tight_layout(rect=(0,0,1,.95)); fig.savefig(path,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); return str(path)

def sector_treemap(sectors:dict, output_dir:Optional[str]=None)->str:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    names=list(sectors); vals=[float(sectors[x] or 0) for x in names]; fig,ax=plt.subplots(figsize=(12,6),dpi=120); ct.apply_theme(fig,ax)
    colors=[ct.BULLISH if v>=0 else ct.BEARISH for v in vals]; bars=ax.barh(names,vals,color=colors); ax.axvline(0,color=ct.TEXT_PRIMARY,lw=.8); ax.tick_params(colors=ct.TEXT_PRIMARY); ax.set_title("SECTOR ROTATION TREEMAP",color=ct.TEXT_PRIMARY,weight="bold")
    for b,v in zip(bars,vals): ax.text(v,b.get_y()+b.get_height()/2,f" {v:+.2f}%",va="center",color=ct.TEXT_PRIMARY)
    out=Path(output_dir or tempfile.gettempdir()); out.mkdir(parents=True,exist_ok=True); path=out/"sector_treemap.png"; plt.tight_layout(); fig.savefig(path,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); return str(path)

def fundamental_radar(symbol:str, metrics:dict, output_dir:Optional[str]=None)->str:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    labels=list(metrics); values=[max(0,min(100,float(metrics[k] or 0))) for k in labels]; values+=values[:1]; angles=np.linspace(0,2*np.pi,len(labels),endpoint=False).tolist(); angles+=angles[:1]
    fig,ax=plt.subplots(figsize=(7,7),dpi=120,subplot_kw={"polar":True}); ct.apply_theme(fig,ax); ax.plot(angles,values,color=ct.INFO,lw=2); ax.fill(angles,values,color=ct.INFO,alpha=.25); ax.set_xticks(angles[:-1],labels,color=ct.TEXT_PRIMARY); ax.set_ylim(0,100); ax.set_title(f"{symbol} FUNDAMENTAL RADAR",color=ct.TEXT_PRIMARY,weight="bold")
    out=Path(output_dir or tempfile.gettempdir()); out.mkdir(parents=True,exist_ok=True); path=out/f"fundamental_radar_{symbol}.png"; fig.savefig(path,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); return str(path)
