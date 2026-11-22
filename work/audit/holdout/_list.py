# -*- coding: utf-8 -*-
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(HERE, "replay_prod.json"), encoding="utf-8"))
rows = {}
for bucket in ("fixed", "persistent", "clean_other_rewrite", "clean_dropped"):
    for r in d["samples"].get(bucket) or []:
        rows[r["id"]] = r
# also all rows? samples doesn't include unchanged. Re-derive from txt is limited; use clean_other + fixed
want = [1976,2018,2020,2028,2106,2113,2134,2147,2155,2163,2175,2215,2220,2231]
for i in want:
    r = rows.get(i)
    if r: print(i, r["diff_kind"], "|", r["raw"][:50], "=>", r["final"][:60])
