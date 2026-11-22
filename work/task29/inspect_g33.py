# -*- coding: utf-8 -*-
import io, json, os, sys
HERE = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, os.path.join(HERE, "work", "sim"))
import sim_worlds as W
groups = {g["gid"]: g for g in W.build_groups(50)}
g = groups["G33"]
print("G33 blocks:", [(b.get("start"), b.get("end"), b.get("place"), b.get("zone")) for b in g["blocks"]])
for t in g["turns"]:
    print("turn", t["index"], t["time"], "probe", t.get("probe"), "| user:", t.get("user"))
rows = json.load(io.open(os.path.join(HERE, "work", "sim", "real_llm_layer.json"), encoding="utf-8"))["rows"]
for row in rows:
    if row["gid"] == "G33" and int(row["turn"]) == 8:
        print("ROW:", json.dumps(row, ensure_ascii=False, indent=2))
