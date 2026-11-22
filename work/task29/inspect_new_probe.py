# -*- coding: utf-8 -*-
"""查看 lead 最新 real_llm_layer 报告里 G13/G36 turn10 的现场。"""
import io
import json

d = json.load(io.open("work/sim/real_llm_layer.json", encoding="utf-8"))
print("keys:", list(d))
for key in ("status", "provider", "groups", "turns", "failed_turns",
            "turn_pass_rate", "elapsed_sec"):
    print(key, ":", d.get(key))
print("codes:", json.dumps(d.get("codes"), ensure_ascii=False))
print("note:", str(d.get("note"))[:500])
rows = d.get("rows") or []
print("rows:", len(rows))
if rows:
    print("row keys:", sorted(rows[0]))
for row in rows:
    if str(row.get("gid")) in ("G13", "G36") and int(row.get("turn") or 0) == 10:
        print("=" * 70)
        print(json.dumps(row, ensure_ascii=False, indent=2))
