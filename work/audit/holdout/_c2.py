# -*- coding: utf-8 -*-
import sys, os, io, asyncio
HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout = io.TextIOWrapper(open(os.path.join(HERE, "task40_c2.log"), "wb"), encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE))); os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "work", "sim")); sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import holdout_adversarial as A
import amo_rewrite_safety as S
orig = S.recover
def spy(plugin, umo, current, sanitize, guard=None, source="chat", allow_shrink=True):
    cand = S.peek_candidate(plugin, umo)
    safe = sanitize(cand)[0] if cand else ""
    probe = guard(safe) if (cand and callable(guard)) else ""
    out = orig(plugin, umo, current, sanitize, guard=guard, source=source, allow_shrink=allow_shrink)
    print("RECOVER cand=", repr(cand)[:60], "safe=", repr(safe)[:60], "probe=", repr(probe)[:60])
    print("  cur=", repr(current)[:60], "->", repr(out)[:60], "unsafe=", S._candidate_unsafe(safe, probe), "cur_bad=", S._guard().is_fixed_rewrite(current), S._guard().is_multi_pool(current))
    return out
S.recover = spy
async def main():
    out = await A.run_chat("rest_home", "20:10", "你在干嘛呀？", "在商场试衣服，等我一下。")
    for k, v in out["stages"].items(): print("  ", k, "=", repr(v)[:100])
    print("FINAL", repr(out["final"]))
asyncio.run(main())
