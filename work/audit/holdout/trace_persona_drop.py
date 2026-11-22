# -*- coding: utf-8 -*-
import asyncio, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "work", "sim"))
import sim_env as E
import sim_worlds as W
import amo_outbound as OB
import amo_persona_guard as PG

async def probe(text, plan="office_early", hhmm="08:03"):
    blocks = [W._block_tuple(row) for row in W.PLANS[plan]["blocks"]]
    plugin = E.build_plugin(blocks, kv={}, health={"status": "健康", "sick": False})
    with E.frozen_clock(hhmm):
        stamp = OB._now_ts(None)
        body, reason = PG.prepare_outbound(plugin, "大肥鱼:FriendMessage:302578963", text, "push")
        decision = None
        if body:
            decision = OB._decide(plugin, "大肥鱼:FriendMessage:302578963", body, "push", "", stamp, "")
        return {"text": text, "after_persona": body, "persona_reason": reason,
                "decide": decision}

async def main():
    rows = [await probe(t) for t in ("在写代码呀。", "写代码呢，你呢？", "我在景区爬山。", "没干嘛，写代码。", "我在写方案。", "在改方案呢。", "在工位上班。")]
    path = os.path.join(HERE, "trace_persona_drop.json")
    json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote", path)

asyncio.run(main())
