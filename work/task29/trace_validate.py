# -*- coding: utf-8 -*-
"""定位 G36 validate_reply_assembly 里是哪一步删掉地点答案。"""
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

UMO = "大肥鱼:FriendMessage:302578963"
GROUPS = {g["gid"]: g for g in W.build_groups(50)}
ROWS = {(r["gid"], r["turn"]): r for r in json.load(
    io.open(os.path.join(SIM, "real_llm_layer.json"), encoding="utf-8"))["rows"]}


async def main():
    gid, target = "G36", 10
    group = GROUPS[gid]
    plugin = E.build_plugin(group["blocks"], kv={}, health=group["health"])
    for turn in group["turns"]:
        if turn["index"] >= target:
            break
        block = W.block_at(group["blocks"], turn["time"])
        plugin.db.kv["date_session"] = (
            {"phase": "active", "together": True, "place": block.get("place")}
            if block.get("together") else {"phase": "ended"})
        plugin.db.kv.pop("trip", None)
        row = ROWS.get((gid, turn["index"])) or {}
        await E.run_turn(plugin, turn["user"], row.get("candidate") or "", turn["time"])
    turn = next(t for t in group["turns"] if t["index"] == target)
    block = W.block_at(group["blocks"], turn["time"])
    plugin.db.kv["date_session"] = (
        {"phase": "active", "together": True, "place": block.get("place")}
        if block.get("together") else {"phase": "ended"})
    plugin._recent_rows.append({"role": "user", "content": turn["user"]})
    with E.frozen_clock(turn["time"]):
        candidate = (ROWS.get((gid, target)) or {})["candidate"]
        print("candidate:", candidate)
        refs = SC.history_bot_texts(plugin, UMO)
        print("refs:", refs)
        print("drop_repeated_clauses ->", SC.drop_repeated_clauses(candidate, refs))
        print("strip_scene ->", SC.strip_current_scene_conflicts(candidate, plugin))
        print("scene:", SC.current_scene(plugin))
        print("scene_at:", end=" ")
        try:
            from amo_scene_ledger import scene_at
            print(dict(scene_at(plugin) or {}))
        except Exception as exc:
            print("ERR", exc)


asyncio.run(main())
