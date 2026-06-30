"""Executive EOD/cumulative report from all signals, trades, and runtime health."""
from __future__ import annotations
import json, sqlite3, tempfile, time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

WINDOWS=(1,5,20,60,400)

def _quality(n:int,days:int)->str:
    return "ROBUST" if n>=200 and days>=20 else "DEVELOPING" if n>=50 and days>=5 else "LOW_SAMPLE"

def build_report() -> Dict[str,Any]:
    out={"generated_at":datetime.now().isoformat(),"session_date":date.today().isoformat(),"windows":{},"strategies":{},"observations":[],"tomorrow":[]}
    con=sqlite3.connect("signal_log.db"); con.row_factory=sqlite3.Row
    all_rows=[dict(r) for r in con.execute("SELECT signal_date,strategy,tb_label,tb_r_multiple_net,tb_r_multiple,rejection_reason FROM signal_log WHERE tb_label IN (-1,0,1)")]; con.close()
    dates=sorted({str(r["signal_date"]) for r in all_rows if r.get("signal_date")})
    for w in WINDOWS:
        keep=set(dates[-w:]); rows=[r for r in all_rows if str(r.get("signal_date")) in keep]
        wins=sum(r["tb_label"]==1 for r in rows); losses=sum(r["tb_label"]==-1 for r in rows); timeouts=sum(r["tb_label"]==0 for r in rows)
        out["windows"][str(w)]={"signals":len(rows),"wins":wins,"losses":losses,"timeouts":timeouts,
          "win_rate":round(wins/max(wins+losses,1),4),"sample_quality":_quality(len(rows),len(keep))}
    by=defaultdict(list)
    for r in all_rows: by[str(r.get("strategy") or "unknown")].append(r)
    for strategy,rows in by.items():
        vals=[float(r.get("tb_r_multiple_net") or r.get("tb_r_multiple") or r.get("tb_label") or 0) for r in rows]
        pos=sum(v for v in vals if v>0); neg=abs(sum(v for v in vals if v<0)); n=len(vals); days=len({r.get("signal_date") for r in rows})
        median=sorted(vals)[n//2] if n else 0; avg=sum(vals)/max(n,1)
        out["strategies"][strategy]={"samples":n,"win_rate":round(sum(v>0 for v in vals)/max(n,1),4),
          "profit_factor":round(pos/max(neg,1e-9),3),"expectancy":round(sum(vals)/max(n,1),4),
          "median":round(median,4),"sample_quality":_quality(n,days),
          "outlier_skew_warning":bool(n<30 or abs(avg-median)>max(.5,abs(median)*2))}
    eligible=({"strategy":k,**v} for k,v in out["strategies"].items() if v["samples"]>=20)
    out["leaderboard"]=sorted(eligible,key=lambda x:(x["sample_quality"]=="ROBUST",not x["outlier_skew_warning"],x["expectancy"],x["samples"]),reverse=True)[:15]
    reasons=Counter(str(r.get("rejection_reason") or "").split(",")[0] for r in all_rows if r.get("rejection_reason")); out["rejections"]=dict(reasons.most_common(12))
    try:
        t=sqlite3.connect("trades.db"); tr=t.execute("SELECT realized_pnl,total_charges FROM trades WHERE status='CLOSED'").fetchall(); t.close()
    except Exception: tr=[]
    out["trades"]={"closed":len(tr),"net_pnl":round(sum(float(x[0] or 0) for x in tr),2),"charges":round(sum(float(x[1] or 0) for x in tr),2)}
    if out["trades"]["charges"]>abs(out["trades"]["net_pnl"]): out["observations"].append("Transaction costs dominate realized performance.")
    robust=[x for x in out["leaderboard"] if x["sample_quality"]=="ROBUST" and x["profit_factor"]>1.2]
    out["tomorrow"].append("Trade only cost-gate-qualified setups; keep reverse candidates shadow-only.")
    out["tomorrow"].append(f"Prioritize {', '.join(x['strategy'] for x in robust[:3])}." if robust else "No strategy has robust promotion evidence yet; remain paper-first.")
    return out

def save_report(report:Dict[str,Any],path:str="executive_eod_report.json")->None:
    Path(path).write_text(json.dumps(report,indent=2,default=str))
    from runtime_telemetry import _connect, ensure_schema
    ensure_schema(); con=_connect(); now=time.time()
    con.execute("INSERT OR REPLACE INTO daily_summary(session_date,generated_at,payload) VALUES (?,?,?)",(report["session_date"],now,json.dumps(report,default=str)))
    for w,data in report["windows"].items(): con.execute("INSERT INTO cumulative_summary(window_days,generated_at,payload) VALUES (?,?,?)",(int(w),now,json.dumps(data)))
    for s,d in report["strategies"].items(): con.execute("INSERT INTO strategy_statistics(strategy,window_days,generated_at,samples,win_rate,profit_factor,expectancy,median_pnl,avg_pnl,sample_quality,weight,confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(s,400,now,d["samples"],d["win_rate"],d["profit_factor"],d["expectancy"],d["median"],d["expectancy"],d["sample_quality"],1.0,min(1,d["samples"]/200)))
    con.commit(); con.close()

def generate_chart(report:Dict[str,Any],output_dir:Optional[str]=None)->str:
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(13,9),dpi=130); fig.patch.set_facecolor("#08111f")
    for ax in axes.flat: ax.set_facecolor("#101c2c"); ax.tick_params(colors="#b8c7d9"); ax.grid(color="#2b3b50",alpha=.4)
    wins=[report["windows"][str(w)]["wins"] for w in WINDOWS]; losses=[report["windows"][str(w)]["losses"] for w in WINDOWS]
    axes[0,0].bar([str(w) for w in WINDOWS],wins,color="#51cf66",label="Wins"); axes[0,0].bar([str(w) for w in WINDOWS],losses,bottom=wins,color="#ff6b6b",label="Losses"); axes[0,0].set_title("CUMULATIVE SIGNAL OUTCOMES",color="white",weight="bold"); axes[0,0].legend()
    leaders=report["leaderboard"][:8][::-1]; axes[0,1].barh([x["strategy"][:22] for x in leaders],[x["expectancy"] for x in leaders],color=["#51cf66" if x["expectancy"]>0 else "#ff6b6b" for x in leaders]); axes[0,1].set_title("STRATEGY EXPECTANCY",color="white",weight="bold")
    rej=list(report["rejections"].items())[:7][::-1]; axes[1,0].barh([x[0][:24] for x in rej],[x[1] for x in rej],color="#ffd43b"); axes[1,0].set_title("TOP REJECTIONS",color="white",weight="bold")
    axes[1,1].axis("off"); tr=report["trades"]; text=(f"Closed trades  {tr['closed']}\nNet P&L       ₹{tr['net_pnl']:+,.0f}\nCharges       ₹{tr['charges']:,.0f}\n\nAI OBSERVATIONS\n"+"\n".join("• "+x for x in report["observations"]+report["tomorrow"])); axes[1,1].text(.04,.92,text,va="top",color="white",fontsize=11,linespacing=1.5)
    fig.suptitle(f"EXECUTIVE EOD REPORT • {report['session_date']}",color="white",fontsize=17,weight="bold"); out=Path(output_dir or tempfile.gettempdir()); out.mkdir(parents=True,exist_ok=True); path=out/f"executive_eod_{report['session_date']}.png"; plt.tight_layout(rect=(0,0,1,.95)); fig.savefig(path,facecolor=fig.get_facecolor(),bbox_inches="tight"); plt.close(fig); return str(path)

def run(send_telegram:bool=False)->Dict[str,Any]:
    report=build_report(); save_report(report); path=generate_chart(report); report["chart_path"]=path
    if send_telegram:
        try:
            from alerts import AlertManager
            AlertManager().send_photo(path,f"Executive EOD • {report['session_date']}")
        except Exception: pass
    return report
