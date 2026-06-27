#!/usr/bin/env python3
"""Find correct NSE tokens using broader search."""
import sys, pandas as pd
sys.path.insert(0, '.')

df = pd.read_csv("MasterContract_ALL.csv", low_memory=False)
nse = df[df["exch_seg"].str.upper()=="NSE"]

searches = {
    "ZOMATO":    ["ZOMATO","ZOMA"],
    "MCDOWELLS": ["MCDOW","UNITED SPIRIT","UBL"],
    "MAHINDCIE": ["MAHINDCIE","MAHINDC","CIE AUTO"],
    "INDIAGRID": ["INDIGRID","INDIA GRID","INDIAGRID","POWERGRID"],
    "LTIM":      ["LTIMINDTREE","LTIM","MINDTREE"],
    "AMARA":     ["AMARA RAJA","AMARAJABAT","AMARARAJ"],
    "RINFRA":    ["RINFRA","RELINFRA","REL INFRA"],
}

print("Broad search results:\n")
for sym, terms in searches.items():
    print(f"── {sym} ──────────────────────")
    found = False
    for term in terms:
        mask = (nse["symbol"].str.upper().str.contains(term.upper(), na=False) |
                nse["name"].str.upper().str.contains(term.upper(), na=False))
        matches = nse[mask][["symbol","token","name"]].head(3)
        if len(matches):
            found = True
            for _,r in matches.iterrows():
                print(f"  [{term}] {r['symbol']:30} tok={r['token']:10} {r['name'][:30]}")
    if not found:
        print(f"  NOT FOUND in MasterContract NSE segment")
    print()
