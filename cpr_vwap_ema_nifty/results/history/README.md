# Run history

Each folder is a complete earlier run (trades.csv, summary.json/md, equity.png),
archived automatically when the next run starts.

Development runs from 2026-10-16 (kept for the record; superseded):
- 2026-10-16T085012: first run. Layer B missing for 2020-2023 (options_eod has no
  underlying price for those years; fixed in the next run).
- 2026-10-16T085225: layer B fixed (own spot close as underlying); no per-variant
  IS/OOS yet.
- 2026-10-16T085414: per-variant IS/OOS, by-year and bootstrap added.
- 2026-10-16T085751: same numbers as 085414; first run of the committed code.
  The committed results/latest at c9776dd6 (08:59:58) differs only in equity.png
  (one curve per variant).
