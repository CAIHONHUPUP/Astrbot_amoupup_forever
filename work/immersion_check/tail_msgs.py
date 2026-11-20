# -*- coding: utf-8 -*-
import json, os, datetime
p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "real_seed.json")
d = json.load(open(p, encoding="utf-8"))
msgs = d["messages"]
for m in msgs[-70:]:
    t = datetime.datetime.fromtimestamp(m["ts"]).strftime("%m-%d %H:%M")
    print("%s %-4s %-70s" % (t, m["role"], str(m["content"]).replace("\n"," ")[:70]))
