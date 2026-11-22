# -*- coding: utf-8 -*-
import asyncio, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SIM = os.path.join(ROOT, "work", "sim")
sys.path.insert(0, SIM)
import run_daily as R
import sim_worlds as W
import sim_env as E
import random, zlib
import amo_reply_pipeline as pipeline
import reply_fact_guard

def rng_for(gid, index):
    return random.Random(zlib.crc32(str(gid).encode("utf-8")) * 1000003 + index * 7919)

async def main(gid):
    R.install_db_guard()
    groups = W.build_groups(50, 20261121)
    group = next(g for g in groups if g["gid"] == gid)
    plugin = E.build_plugin(group["blocks"], kv={}, health=group["health"])
    prev_snap = None
    for turn in group["turns"]:
        block = W.block_at(group["blocks"], turn["time"])
        plugin.db.kv["date_session"] = ({"phase": "active", "together": True, "place": block.get("place")} if block.get("together") else {"phase": "ended"})
        if group["trip"]:
            plugin.db.kv["trip"] = dict(group["trip"])
        else:
            plugin.db.kv.pop("trip", None)
        with E.frozen_clock(turn["time"]):
            snap = dict(plugin.state.current_activity() or {})
        meal_now = W.meal_ok(group["blocks"], turn["time"])
        together = bool(snap.get("together"))
        from mock_provider import respond
        candidate, drift_meta = respond(turn, snap, prev_snap, meal_now, together, rng_for(gid, turn["index"]))
        event = E.FakeEvent(turn["user"])
        result = E.FakeResult()
        try:
            from amo_diary_guard import invalidate_narrative_view
            invalidate_narrative_view(plugin)
        except Exception:  # cleanup: 视图失效不影响回放
            pass
        plugin._recent_rows.append({"role": "user", "content": str(turn["user"] or "")})
        with E.frozen_clock(turn["time"]) as now:
            activity = dict(plugin.state.current_activity() or {})
            ctx = pipeline.fact_guard_context(plugin, activity, now)
            fixed, before = reply_fact_guard.guard(str(candidate or ""), ctx)
            steps = [("candidate", candidate), ("guard(pre)", fixed)]
            text = str(candidate or "")
            if not drift_meta:
                prev_snap = snap
                continue
            text = plugin._deco_soft_guards(event, text); steps.append(("soft_guards", text))
            text = plugin._deco_rewrite_reply(event, result, text); steps.append(("rewrite_reply", text))
            text = await plugin._deco_text_cleanup(event, text); steps.append(("text_cleanup", text))
            history = " ".join(str(r.get("content") or "") for r in plugin._recent("大肥鱼:FriendMessage:302578963", 6) if isinstance(r, dict))
            text = pipeline.apply_chat_safety(text, plugin, "大肥鱼:FriendMessage:302578963", turn["user"], history_text=history)
            steps.append(("chat_safety", text))
            plugin.context.sent = []
            await plugin._send_to_umo("大肥鱼:FriendMessage:302578963", text, source="chat")
            steps.append(("sent", "".join(plugin.context.sent)))
        print("=== %s t%s %s user=%s snap=%s/%s cat=%s drift=%s" % (gid, turn["index"], turn["time"], turn["user"], snap.get("place"), snap.get("zone"), snap.get("category"), drift_meta))
        for name, value in steps:
            print("  %-14s %r" % (name, value))
        prev_snap = snap

asyncio.run(main(sys.argv[1]))
