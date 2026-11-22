# -*- coding: utf-8 -*-
"""task-40 探针：逐子步骤归因（soft_guards / chat_safety / 出口）不改生产代码。"""
from __future__ import annotations
import asyncio, datetime, io, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "work", "sim"))

import sim_env as E  # noqa
import amo_reply_pipeline as pipeline  # noqa
import amo_message as M  # noqa
import holdout_replay_prod as R  # noqa

TRACE = []
CUR = {"id": None}

def wrap(name, fn, idx=None):
    def inner(*args, **kwargs):
        before = args[idx] if idx is not None else None
        out = fn(*args, **kwargs)
        after = out
        if isinstance(out, tuple):
            after = out[0]
        if str(before) != str(after):
            TRACE.append((name, str(before)[:300], str(after)[:300]))
        return out
    return inner

# soft_guards 内部
pipeline.apply_fact_guard = wrap("fact_guard", pipeline.apply_fact_guard, 0)
pipeline.apply_narrative_repetition = wrap("narrative_dedupe", pipeline.apply_narrative_repetition, 0)
pipeline.apply_tone_guard = wrap("tone_guard", pipeline.apply_tone_guard, 0)
# 3a-2b：_guard_self_location 已删除（地点守卫收敛到 reply_fact_guard），没有就不包
orig_guard_self = getattr(M.MessageMixin, "_guard_self_location", None)
if orig_guard_self is not None:
    M.MessageMixin._guard_self_location = wrap("self_location", orig_guard_self, 1)
try:
    orig_np = R.__dict__.get("_narrative_place")
except Exception:
    orig_np = None

# chat_safety 内部
import amo_state_consistency as SC
import amo_purchase_guard as PG
import amo_question_guard as QG
import amo_persona_guard as PERS
pipeline_safety_purchase = PG.rewrite
PG.rewrite = wrap("chat.purchase", pipeline_safety_purchase, 0)
SC.rewrite = wrap("chat.state_rewrite", SC.rewrite, 0)
QG.answer = wrap("chat.question_answer", QG.answer, 1 if False else 2)
PERS.prepare_outbound = wrap("persona.prepare", PERS.prepare_outbound, 2)
from amo_addressee_guard import enforce as _enforce  # noqa

async def main():
    blocks_by_day, health = R.load_schedule()
    messages = R.load_messages()
    recent = []
    last_user = {}
    rows = []
    for msg in messages:
        when = R._ts(msg["created_at"])
        umo = str(msg.get("umo") or "")
        content = str(msg.get("content") or "")
        if msg["role"] == "user":
            last_user[umo] = content
            recent.append({"role": "user", "content": content, "when": when})
            recent[:] = recent[-14:]
            continue
        if R.MEDIA_RE.match(content):
            recent.append({"role": "bot", "content": content, "when": when})
            recent[:] = recent[-14:]
            continue
        user_text = last_user.get(umo, "")
        history = list(recent)
        blocks = blocks_by_day.get(when.strftime("%Y-%m-%d"), [])
        plugin = E.build_plugin(blocks, kv={}, health=dict(health))
        plugin._recent_rows = [{"role": row["role"], "content": row["content"]}
                               for row in recent[-8:]]
        TRACE.clear()
        CUR["id"] = msg.get("id")
        try:
            out = await R.run_one(plugin, umo, content, user_text, when)
        except Exception as exc:
            out = {"final": "", "stages": [("error", repr(exc))], "stage_text": {}}
        before = R.base_problems(content) + R.repeat_problems(content, history, when)
        if not before and out["final"] != content:
            rows.append({"id": msg.get("id"), "time": when.strftime("%Y-%m-%d %H:%M"),
                         "raw": content, "final": out["final"],
                         "user": user_text,
                         "kind": R.intended_change(content, out["final"]),
                         "trace": list(TRACE)})
        recent.append({"role": "bot", "content": content, "when": when})
        recent[:] = recent[-14:]
    out = io.StringIO()
    out.write(json.dumps({"clean_changed": len(rows)}, ensure_ascii=False) + "\n")
    for row in rows:
        out.write("\n-- #%s %s [%s] user=%r\n  raw: %s\n  fin: %s\n" % (
            row["id"], row["time"], row["kind"], row["user"][:60], row["raw"][:240], row["final"][:240]))
        for name, b, a in row["trace"]:
            out.write("    [%s] %r -> %r\n" % (name, b[:120], a[:120]))
    open(os.path.join(HERE, "task40_probe.txt"), "w", encoding="utf-8").write(out.getvalue())
    print(out.getvalue())

asyncio.run(main())
