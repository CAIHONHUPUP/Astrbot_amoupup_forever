# -*- coding: utf-8 -*-
"""复现 lead 最新真实模型层两个 guard-introduced，并逐阶段定位。"""
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

UMO = "大肥鱼:FriendMessage:302578963"
REPORT = os.path.join(SIM, "real_llm_layer.json")
GROUPS = {g["gid"]: g for g in W.build_groups(50)}
ROWS = {(r["gid"], r["turn"]): r for r in json.load(io.open(REPORT, encoding="utf-8"))["rows"]}


def _set_date_context(plugin, group, turn):
    block = W.block_at(group["blocks"], turn["time"])
    plugin.db.kv["date_session"] = (
        {"phase": "active", "together": True, "place": block.get("place")}
        if block.get("together") else {"phase": "ended"})
    if group["trip"]:
        plugin.db.kv["trip"] = dict(group["trip"])
    else:
        plugin.db.kv.pop("trip", None)


async def replay_to(gid, target_turn):
    group = GROUPS[gid]
    plugin = E.build_plugin(group["blocks"], kv={}, health=group["health"])
    for turn in group["turns"]:
        if turn["index"] >= target_turn:
            break
        _set_date_context(plugin, group, turn)
        row = ROWS.get((gid, turn["index"])) or {}
        await E.run_turn(plugin, turn["user"], row.get("candidate") or "", turn["time"])
    return plugin, group


async def trace(gid, target_turn):
    import amo_reply_pipeline as pipeline
    import amo_state_consistency as SC
    from amo_diary_guard import invalidate_narrative_view

    plugin, group = await replay_to(gid, target_turn)
    turn = next(t for t in group["turns"] if t["index"] == target_turn)
    row = ROWS.get((gid, target_turn)) or {}
    candidate = row.get("candidate") or ""
    _set_date_context(plugin, group, turn)
    invalidate_narrative_view(plugin)
    plugin._recent_rows.append({"role": "user", "content": turn["user"]})

    print("=" * 78)
    print(gid, "turn", target_turn, "time", turn["time"], "probe", turn.get("probe"),
          "| user:", turn["user"])
    print("  snap:", row.get("snap"))
    print("  candidate:", candidate)

    event = E.FakeEvent(turn["user"])
    result = E.FakeResult()
    with E.frozen_clock(turn["time"]) as now:
        text = candidate
        text = plugin._deco_soft_guards(event, text)
        print("  after soft_guards :", text)
        text = plugin._deco_rewrite_reply(event, result, text)
        print("  after rewrite_reply:", text)
        text = await plugin._deco_text_cleanup(event, text)
        print("  after text_cleanup:", text)
        history = " ".join(str(r.get("content") or "") for r in plugin._recent(UMO, 6)
                           if isinstance(r, dict))
        text = pipeline.apply_chat_safety(text, plugin, UMO, turn["user"], history_text=history)
        print("  after chat_safety :", text)
        print("  rewrite() only    :", SC.rewrite(candidate, plugin, turn["user"], umo=UMO))
        print("  validate() only   :", SC.validate_reply_assembly(
            candidate, plugin, umo=UMO, user_text=turn["user"]))
        print("  chat_safety+validate:", SC.validate_reply_assembly(
            text, plugin, umo=UMO, user_text=turn["user"]))
    print("  report sent       :", row.get("sent"))
    print("  sent_codes        :", row.get("sent_codes"))
    # 最近 bot 文本（去重参考）
    bots = [str(r.get("content") or "") for r in plugin._recent(UMO, 8)
            if isinstance(r, dict) and r.get("role") == "bot"]
    print("  recent bot:", bots[-3:])


async def main():
    await trace("G13", 10)
    await trace("G36", 10)


asyncio.run(main())
