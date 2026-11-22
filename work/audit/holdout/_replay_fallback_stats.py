# -*- coding: utf-8 -*-
import json, io, collections
j = json.load(open("work/audit/holdout/replay_prod.json", encoding="utf-8"))
rows = j["samples"]["clean_changed"] + j["samples"]["fixed"] + j["samples"]["persistent"]
FALLBACK_HEADS = ("还是老样子", "我在休息", "我在敷面膜", "唔…这个先不说了", "我试了试，挺喜欢的",
                  "哈哈哈", "我这儿一直在", "嗯，我在呢", "在听你说呢", "你说，我听着呢",
                  "嗯嗯，知道啦", "好，我记着呢", "我到了，等会儿说", "我还在路上呢", "我在家呢")
cnt = collections.Counter()
items = collections.defaultdict(list)
for r in rows:
    final = r["final"]
    if any(final.startswith(h) or h in final for h in FALLBACK_HEADS) and len(final) <= 40:
        cnt["fallback_replace"] += 1
        items["fallback_replace"].append((r["id"], r["raw"][:70], final[:70]))
    if len(final) and len(r["raw"]) >= 30 and len(final) <= 0.55 * len(r["raw"]):
        cnt["big_shrink"] += 1
        items["big_shrink"].append((r["id"], r["raw"][:70], final[:70]))
out = io.StringIO()
out.write(json.dumps(cnt, ensure_ascii=False) + "\n")
for key, vals in items.items():
    out.write("== %s ==\n" % key)
    for v in vals:
        out.write("  #%s raw=%r fin=%r\n" % v)
open("work/audit/holdout/replay_fallback_stats.txt", "w", encoding="utf-8").write(out.getvalue())
print("ok")
