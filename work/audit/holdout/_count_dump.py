# -*- coding: utf-8 -*-
import json, collections
d = json.load(open(r"work/audit/_scratch/mem_persona/dump_amo_data.json", encoding="utf-8"))
msgs = d["messages"]
print("total", len(msgs), "roles", collections.Counter(m["role"] for m in msgs))
print("first", msgs[0]["id"], "last", msgs[-1]["id"])
bot = [m for m in msgs if m["role"] == "bot"]
print("bot", len(bot), "first/last", bot[0]["id"], bot[-1]["id"])
import datetime
print("range", datetime.datetime.fromtimestamp(min(m["created_at"] for m in msgs)), datetime.datetime.fromtimestamp(max(m["created_at"] for m in msgs)))
