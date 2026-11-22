# -*- coding: utf-8 -*-
"""复现 G33/t8：where 问句地点句被删后没有补地点。"""
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
import amo_reply_pipeline as RP  # noqa: E402
from amo_scene_ledger import scene_at  # noqa: E402

ROWS = {(r["gid"], r["turn"]): r for r in json.load(
    io.open(os.path.join(SIM, "real_llm_layer.json"), encoding="utf-8"))["rows"]}
GROUPS = {g["gid"]: g for g in W.build_groups(50)}

ORIG_ENSURE = SC._ensure_where_answer
CALLS = []


def traced_ensure(text, plugin, user_text, umo=""):
    out = ORIG_ENSURE(text, plugin, user_text, umo)
    CALLS.append({"in": text, "user_text": user_text, "umo": umo, "out": out,
                  "ask": SC._turn_ask(umo, user_text),
                  "answers_where": SC._answers_where(text)})
    return out


SC._ensure_where_answer = traced_ensure

ORIG_DEDUPE = SC.dedupe_reply_segments


def traced_dedupe(segments, refs=None, plugin=None, umo=""):
    out = ORIG_DEDUPE(segments, refs=refs, plugin=plugin, umo=umo)
    print("   dedupe_reply_segments:", list(segments), "->", out)
    return out


SC.dedupe_reply_segments = traced_dedupe

ORIG_SEG = RP.apply_segment_repeat_guard


def traced_seg(text, plugin=None, umo=""):
    out = ORIG_SEG(text, plugin, umo)
    print("   apply_segment_repeat_guard:", repr(text), "->", repr(out))
    return out


RP.apply_segment_repeat_guard = traced_seg


async def main():
    gid, target = "G33", 8
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
    row = ROWS.get((gid, target)) or {}
    candidate = row.get("candidate") or ""
    CALLS.clear()
    print("=" * 70)
    print("user:", turn["user"], "| probe:", turn.get("probe"), "| snap:", row.get("snap"))
    print("candidate:", candidate)
    with E.frozen_clock(turn["time"]):
        print("scene_at:", dict(scene_at(plugin) or {}).get("place"),
              dict(scene_at(plugin) or {}).get("zone"))
        print("refs(bot):", SC.history_bot_texts(plugin, "大肥鱼:FriendMessage:302578963"))
        print("rewrite()  ->", SC.rewrite(candidate, plugin, turn["user"], umo="大肥鱼:FriendMessage:302578963"))
        print("validate() ->", SC.validate_reply_assembly(
            candidate, plugin, umo="大肥鱼:FriendMessage:302578963", user_text=turn["user"]))
    out = await E.run_turn(plugin, turn["user"], candidate, turn["time"])
    print("run_turn sent:", out["sent"])
    for call in CALLS:
        print("ensure call: in=", repr(call["in"])[:50], "| ask=", repr(call["ask"]),
              "answers_where=", call["answers_where"], "-> out=", repr(call["out"])[:70])


asyncio.run(main())
