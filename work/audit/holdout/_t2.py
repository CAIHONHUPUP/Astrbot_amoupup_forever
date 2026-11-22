# -*- coding: utf-8 -*-
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE))); os.chdir(ROOT); sys.path.insert(0, ROOT)
import amo_state_consistency as SC
texts = [
 "你发的那个视频我还没点开，你再发我一遍？",
 "你把刚拍的视频再发我一次。",
 "我在商场逛街呢，刚试了件衣服。",
 "在商场试衣服，等我一下。",
]
for t in texts:
    print(repr(t), "->", repr("".join(SC._strip_role_reversal(s) for s in SC._sentences(t))))
print("SHARE_ROLE", SC._SHARE_ROLE.pattern)
print("ROLE?")
for t in texts:
    print(repr(t), [bool(p.search(t)) for p in (SC._SHARE_ROLE,)])
