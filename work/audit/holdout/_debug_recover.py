# -*- coding: utf-8 -*-
import sys, os, io, asyncio
HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout = io.TextIOWrapper(open(os.path.join(HERE, "task40_debug.log"), "wb"), encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "work", "sim")); sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
os.chdir(ROOT)
import sim_env as E
import amo_rewrite_safety as S
import holdout_replay_prod as R

orig = S.recover
def spy(plugin, umo, current, sanitize, source="chat", allow_shrink=True):
    cand = S.peek_candidate(plugin, umo)
    safe, reason = sanitize(cand) if cand else ("", "")
    reasons = S.damage_reasons(cand, current, allow_shrink) if cand and current else []
    out = orig(plugin, umo, current, sanitize, source=source, allow_shrink=allow_shrink)
    print("RECOVER umo=%r" % (umo,))
    print("  cand:", repr(cand)[:120])
    print("  safe:", repr(safe)[:120], "reason", reason)
    print("  cur :", repr(current)[:120])
    print("  reasons:", reasons, "out:", repr(out)[:100])
    return out
S.recover = spy

async def main():
    blocks_by_day, health = R.load_schedule()
    messages = R.load_messages()
    recent = []; last_user = {}
    want = {2089, 2006, 2207, 2147, 2134}
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
        if msg.get("id") in want:
            print("="*40, msg.get("id"), repr(content)[:80], "user=", repr(user_text)[:40])
            out = await R.run_one(plugin, umo, content, user_text, when)
            print("FINAL:", repr(out["final"])[:120])
        else:
            await R.run_one(plugin, umo, content, user_text, when)
        recent.append({"role":"bot","content":content,"when":when}); recent[:] = recent[-14:]

asyncio.run(main())
print("DONE", flush=True)
