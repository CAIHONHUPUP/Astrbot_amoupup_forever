# -*- coding: utf-8 -*-
import asyncio, datetime as dt, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imm_harness as H
import amo_reply_pipeline as pipeline

NOW = dt.datetime(2026, 11, 17, 10, 30)
WORLD = {
    "day": "2026-11-17",
    "blocks": [{"start": "09:00", "end": "12:00", "zone": "home", "place": "家",
                "activity": "窝在沙发上", "category": "rest"}],
    "health": {"status": "健康", "sick": False},
    "mood": {"开心": 60, "平静": 50},
    "relation": "恋人",
    "kv": {},
}
plugin = H.build_plugin(WORLD)
ctx = pipeline.fact_guard_context(plugin, None, NOW)
print("CTX_KEYS:", sorted(ctx.keys()))
print("CTX_ACT:", ctx.get("activity"))
print("CTX_HEALTH:", ctx.get("health"))
print("CTX_NEARBY:", ctx.get("nearby"))

UMO = "大肥鱼:FriendMessage:302578963"
turns = [
    ("你在干嘛呀", "我在公司加班呢，有点想你了"),
    ("吃了吗", "刚吃完面，好香"),
    ("他刚才抱我没", "他刚抱我了，我有点难过"),
]
for b_mode in (False, True):
    p = H.build_plugin(WORLD)
    llm = H.MockLLM(mode="rotate", delay=0.0)
    p.llm = llm
    p.tone_llm_pick = b_mode
    print("=== B", b_mode)
    for user, cand in turns:
        out = asyncio.run(H.run_turn(p, user, cand, NOW, UMO, b_mode=b_mode))
        print(json.dumps({k: out[k] for k in ("sent", "violations", "mechanical", "fixed_used", "ms")}, ensure_ascii=False)[:600])
    print("STATS:", pipeline.tone_llm_pick_stats(), "llm_calls", llm.calls)
