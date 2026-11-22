# -*- coding: utf-8 -*-
import sys, os, io, asyncio
HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout = io.TextIOWrapper(open(os.path.join(HERE, "task40_debug2.log"), "wb"), encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "work", "sim")); sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
os.chdir(ROOT)
import sim_env as E
import amo_rewrite_safety as S
import amo_persona_guard as P
import holdout_replay_prod as R
import amo_reply_pipeline as PIPE

WANT = {2006, 2061}
STATE = {"id": None}

orig_damage = S.damage_reasons
def spy_damage(cand, cur, allow_shrink=True):
    reasons = orig_damage(cand, cur, allow_shrink)
    if STATE["id"] in WANT:
        print("  DAMAGE cand=", repr(cand)[:100], "cur=", repr(cur)[:100], "->", reasons)
    return reasons
S.damage_reasons = spy_damage

async def main():
    blocks_by_day, health = R.load_schedule()
    messages = R.load_messages()
    recent = []; last_user = {}
    for msg in messages:
        when = R._ts(msg["created_at"]); umo = str(msg.get("umo") or ""); content = str(msg.get("content") or "")
        if msg["role"] == "user":
            last_user[umo] = content; recent.append({"role":"user","content":content,"when":when}); recent[:] = recent[-14:]; continue
        if R.MEDIA_RE.match(content):
            recent.append({"role":"bot","content":content,"when":when}); recent[:] = recent[-14:]; continue
        user_text = last_user.get(umo, "")
        blocks = blocks_by_day.get(when.strftime("%Y-%m-%d"), [])
        plugin = E.build_plugin(blocks, kv={}, health=dict(health))
        plugin._recent_rows = [{"role": row["role"], "content": row["content"]} for row in recent[-8:]]
        STATE["id"] = msg.get("id")
        if STATE["id"] in WANT:
            print("="*30, STATE["id"], "user=", repr(user_text)[:50])
        out = await R.run_one(plugin, umo, content, user_text, when)
        if STATE["id"] in WANT:
            print("  cand_peek=", repr(S.peek_candidate(plugin, umo))[:100])
            print("  FINAL:", repr(out["final"])[:120])
        recent.append({"role":"bot","content":content,"when":when}); recent[:] = recent[-14:]

asyncio.run(main())
print("DONE")
