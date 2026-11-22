# -*- coding: utf-8 -*-
"""定位「干净 candidate 被清空」发生在哪一层：逐层打印。"""
import asyncio, io, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "work", "sim"))
import sim_env as E
import sim_worlds as W
import amo_reply_pipeline as pipeline


async def trace(plan, hhmm, user, candidate, meta=""):
    blocks = [W._block_tuple(row) for row in W.PLANS[plan]["blocks"]]
    plugin = E.build_plugin(blocks, kv={}, health=dict(W.PLANS[plan].get("health") or {"status": "健康", "sick": False}))
    block = W.block_at(blocks, hhmm)
    plugin.db.kv["date_session"] = ({"phase": "active", "together": True, "place": block.get("place")}
                                    if block.get("together") else {"phase": "ended"})
    event = E.FakeEvent(user, "大肥鱼:FriendMessage:302578963")
    result = E.FakeResult()
    out = {"meta": meta, "plan": plan, "time": hhmm, "user": user, "candidate": candidate}
    with E.frozen_clock(hhmm):
        text = str(candidate or "")
        out["stage0"] = text
        try:
            text = plugin._deco_soft_guards(event, text); out["stage1_soft_guards"] = text
        except Exception as exc:
            out["stage1_err"] = repr(exc)
        try:
            text = plugin._deco_rewrite_reply(event, result, text); out["stage2_rewrite_reply"] = text
        except Exception as exc:
            out["stage2_err"] = repr(exc)
        try:
            text = await plugin._deco_text_cleanup(event, text); out["stage3_text_cleanup"] = text
        except Exception as exc:
            out["stage3_err"] = repr(exc)
        try:
            history = " ".join(str(row.get("content") or "") for row in plugin._recent("大肥鱼:FriendMessage:302578963", 6) if isinstance(row, dict))
            text = pipeline.apply_chat_safety(text, plugin, "大肥鱼:FriendMessage:302578963", user, history_text=history)
            out["stage4_chat_safety"] = text
        except Exception as exc:
            out["stage4_err"] = repr(exc)
        try:
            await plugin._send_to_umo("大肥鱼:FriendMessage:302578963", text)
        except Exception as exc:
            out["send_err"] = repr(exc)
        out["sent"] = "".join(plugin.context.sent)
    return out


async def main():
    cases = [
        ("office_early", "08:03", "你在干嘛呢？", "写代码呢，你呢？", "20261202 G40 ctrl t2 (sent 空)"),
        ("office_early", "08:03", "你在干嘛呢？", "在写代码呀。", "20261203 G10 ctrl t2 (sent 空)"),
        ("office_early", "08:20", "你在干嘛呢？", "没干嘛，写代码。", "20261121 G20 ctrl t2 (sent 空)"),
        ("office_early", "09:35", "你在干嘛呢？", "在写代码呀。", "20261201 G30 ctrl t3 (sent 空)"),
        ("rest_home", "18:42", "你什么时候回来？", "我就在家呀。", "20261201 G01 (换成 嗯，我在呢。)"),
        ("rest_home", "21:45", "你旁边有人吗？", "自己一个人呀。", "20261203 G11 (换成 我这儿一直在。)"),
    ]
    rows = [await trace(*case) for case in cases]
    path = os.path.join(HERE, "trace_empty_reply.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=1)
    print("wrote", path)
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, indent=1))


asyncio.run(main())
