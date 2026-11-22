# -*- coding: utf-8 -*-
"""定位发送阶段把干净回复清空/丢弃的具体守卫：包装 helper 记录入参出参。"""
import asyncio, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "work", "sim"))
import sim_env as E
import sim_worlds as W
import amo_reply_pipeline as pipeline
import amo_message as M
import amo_addressee_guard as addr
import amo_anti_nag as anti
import amo_purchase_guard as purchase
import amo_state_consistency as sc


def wrap(module, name, label, log):
    original = getattr(module, name)
    if asyncio.iscoroutinefunction(original):
        async def wrapper(*args, **kwargs):
            out = await original(*args, **kwargs)
            log.append({"step": label, "in": str(args[2] if name == "send" else (args[0] if args else ""))[:200],
                        "out": str(out)[:200]})
            return out
    else:
        def wrapper(*args, **kwargs):
            out = original(*args, **kwargs)
            log.append({"step": label,
                        "in": str(args[0] if args else "")[:200],
                        "out": str(out)[:200]})
            return out
    setattr(module, name, wrapper)


async def run_case(plan, hhmm, user, candidate):
    blocks = [W._block_tuple(row) for row in W.PLANS[plan]["blocks"]]
    plugin = E.build_plugin(blocks, kv={}, health=dict(W.PLANS[plan].get("health") or {"status": "健康", "sick": False}))
    block = W.block_at(blocks, hhmm)
    plugin.db.kv["date_session"] = ({"phase": "active", "together": True, "place": block.get("place")}
                                    if block.get("together") else {"phase": "ended"})
    log = []
    wrap(pipeline, "run", "pipeline.run", log)
    wrap(M, "strip_media_markers", "strip_media_markers", log)
    wrap(M, "strip_tech_terms", "strip_tech_terms", log)
    wrap(addr, "enforce", "addressee.enforce", log)
    wrap(anti, "rewrite", "anti_nag.rewrite", log)
    wrap(purchase, "rewrite", "purchase_guard.rewrite", log)
    wrap(sc, "validate_reply_assembly", "validate_assembly", log)
    wrap(sc, "dedupe_reply_segments", "dedupe_segments", log)
    wrap(pipeline, "apply_segment_repeat_guard", "segment_repeat_guard", log)
    # gateway.send
    gw = getattr(M, "_gateway", None)
    if gw is not None:
        try:
            wrap(gw, "send", "gateway.send", log)
        except Exception as exc:
            log.append({"step": "gateway-warp-error", "in": repr(exc), "out": ""})
    with E.frozen_clock(hhmm):
        text = str(candidate or "")
        text = pipeline.apply_chat_safety(text, plugin, "大肥鱼:FriendMessage:302578963", user, history_text="")
        log.append({"step": "chat_safety_out", "in": str(candidate), "out": text})
        ok = await plugin._send_to_umo("大肥鱼:FriendMessage:302578963", text)
        log.append({"step": "send_ok", "in": "", "out": str(ok)})
    return {"plan": plan, "time": hhmm, "user": user, "candidate": candidate,
            "sent": "".join(plugin.context.sent), "log": log}


async def main():
    rows = [
        await run_case("office_early", "08:03", "你在干嘛呢？", "写代码呢，你呢？"),
        await run_case("office_early", "08:03", "你在干嘛呢？", "在写代码呀。"),
        await run_case("scenic_trip", "11:00", "你在干嘛呢？", "爬山呢，你呢？"),
    ]
    path = os.path.join(HERE, "trace_send_stage.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=1)
    print("wrote", path)


asyncio.run(main())
