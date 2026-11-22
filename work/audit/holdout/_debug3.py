# -*- coding: utf-8 -*-
import sys, os, io, asyncio
HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout = io.TextIOWrapper(open(os.path.join(HERE, "task40_debug3.log"), "wb"), encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "work", "sim")); sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
os.chdir(ROOT)
import sim_env as E
import amo_rewrite_safety as S
import holdout_replay_prod as R
WANT = {2006, 2061}
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
        out = await R.run_one(plugin, umo, content, user_text, when)
        if msg.get("id") in WANT:
            print("==", msg.get("id"))
            for k, v in out.get("stage_text", {}).items():
                print("  ", k, "=", repr(v)[:140])
        recent.append({"role":"bot","content":content,"when":when}); recent[:] = recent[-14:]
asyncio.run(main())
print("DONE")
