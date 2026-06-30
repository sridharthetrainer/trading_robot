"""Derived option-metrics cache with freshness and quality metadata."""
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Any, Dict

PATH=Path("option_metrics_cache.json")

def compute_max_pain(df) -> float:
    try:
        strikes=[float(x) for x in df["strikePrice"]]
        ce=[float(x or 0) for x in df["CE_openInterest"]]
        pe=[float(x or 0) for x in df["PE_openInterest"]]
        pain=[]
        for settle in strikes:
            total=sum(max(0,settle-k)*oi for k,oi in zip(strikes,ce))
            total+=sum(max(0,k-settle)*oi for k,oi in zip(strikes,pe))
            pain.append((total,settle))
        return min(pain)[1] if pain else 0.0
    except Exception: return 0.0

def load_all() -> Dict[str, Any]:
    try: return json.loads(PATH.read_text())
    except Exception: return {}

def update(symbol: str, summary: Dict[str, Any], *, source: str="", expiry: str="") -> Dict[str, Any]:
    data=load_all(); now=time.time(); rows=int(summary.get("rows",0) or 0)
    quality=min(100.0,35+(25 if source and "cache" not in source else 0)+(25 if summary.get("pcr_oi") else 0)+(15 if rows>=10 else 0))
    item={"symbol":symbol.upper(),"updated_at":now,"source":source,"expiry":expiry,
          "pcr":summary.get("pcr_oi",0),"pcr_change":summary.get("pcr_change_oi",0),
          "call_wall":summary.get("call_wall"),"put_wall":summary.get("put_wall"),
          "max_pain":summary.get("max_pain"),"spot":summary.get("spot",0),"quality_score":quality}
    data[symbol.upper()]=item; PATH.write_text(json.dumps(data,indent=2)); return item

def get(symbol: str, max_age_sec: int=86400) -> Dict[str, Any]:
    item=(load_all().get(symbol.upper()) or {}).copy()
    if not item: return {}
    age=max(0,time.time()-float(item.get("updated_at",0))); item["freshness_sec"]=round(age,1); item["stale"]=age>max_age_sec
    if item["stale"]: item["quality_score"]=min(float(item.get("quality_score",0)),35.0)
    return item
