# -*- coding: utf-8 -*-
import asyncio, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "work", "sim"))
import sim_env as E
import sim_worlds as W
import amo_reply_pipeline as pipeline
import amo_persona_guard as persona

UMO = "大肥鱼:FriendMessage:302578963"
async def probe(user, cand, plan="office_early", hhmm="08:03"):
    blocks = [W._block_tuple(row) for row in W.PLANS[plan]["blocks"]]
    plugin = E.build_plugin(blocks, kv={}, health={"status": "健康", "sick": False})
    event = E.FakeEvent(user, UMO); result = E.FakeResult()
    stages = {}
    with E.frozen_clock(hhmm):
        text = cand
        stages["raw"] = text
        text = plugin._deco_soft_guards(event, text); stages["soft"] = text
        text = plugin._deco_rewrite_reply(event, result, text); stages["rewrite"] = text
        text = await plugin._deco_text_cleanup(event, text); stages["cleanup"] = text
        text = pipeline.apply_chat_safety(text, plugin, UMO, user, history_text="")
        stages["chat_safety"] = text
        ok = await plugin._send_to_umo(UMO, text, source="chat")
        final = "".join(plugin.context.sent).strip()
        pbody, preason = persona.prepare_outbound(plugin, UMO, text, "chat")
    return {"user": user, "cand": cand, "final": final, "send_ok": ok,
            "persona_after": pbody, "persona_reason": preason, "stages": stages}

async def main():
    rows = [
        await probe("你在干嘛呢？", "在写代码呀。"),
        await probe("你在干嘛呢？", "写代码呢，你呢？"),
        await probe("在吗", "我刚打了两行代码，等下说。"),
    ]
    open(os.path.join(HERE, "probe_code_current.json"), "w", encoding="utf-8").write(
        json.dumps(rows, ensure_ascii=False, indent=1))
    print("ok")
asyncio.run(main())
