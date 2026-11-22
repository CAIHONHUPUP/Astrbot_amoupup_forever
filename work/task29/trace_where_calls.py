# -*- coding: utf-8 -*-
"""定位 G08/t6 等非 where 轮为什么被补了地点：记录 _ensure_where_answer 调用。"""
import asyncio
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SIM = os.path.join(HERE, "work", "sim")
sys.path.insert(0, SIM)

import sim_env as E  # noqa: E402
import sim_worlds as W  # noqa: E402
import amo_state_consistency as SC  # noqa: E402

ROWS = {(r["gid"], r["turn"]): r for r in json.load(
    io.open(os.path.join(SIM, "real_llm_layer.json"), encoding="utf-8"))["rows"]}
GROUPS = {g["gid"]: g for g in W.build_groups(50)}
ORIG = SC._ensure_where_answer
CALLS = []


def traced(text, plugin, user_text):
    out = ORIG(text, plugin, user_text)
    if out != text:
        CALLS.append({"text": text, "user_text": user_text, "out": out})
    return out


SC._ensure_where_answer = traced


async def main():
    gid = "G08"
    group = GROUPS[gid]
    plugin = E.build_plugin(group["blocks"], kv={}, health=group["health"])
    for turn in group["turns"]:
        if turn["index"] > 8:
            break
        block = W.block_at(group["blocks"], turn["time"])
        plugin.db.kv["date_session"] = (
            {"phase": "active", "together": True, "place": block.get("place")}
            if block.get("together") else {"phase": "ended"})
        plugin.db.kv.pop("trip", None)
        CALLS.clear()
        row = ROWS.get((gid, turn["index"])) or {}
        out = await E.run_turn(plugin, turn["user"], row.get("candidate") or "", turn["time"])
        print("turn", turn["index"], "user:", turn["user"])
        print("   sent:", out["sent"])
        for call in CALLS:
            print("   ensure_where:", repr(call["text"])[:40], "| user_text:",
                  repr(call["user_text"])[:60], "->", call["out"][-20:])


asyncio.run(main())
