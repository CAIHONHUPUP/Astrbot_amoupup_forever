# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever")
import amo_state_consistency as SC, inspect
src = inspect.getsource(SC)
for marker in ("G33/t8", "_ASK_WHERE", "我在外面呢", "reaction_only", "_asks_share_followup",
               "complete = ", "dedupe_reply_segments"):
    print(marker, "->", src.count(marker))
print("---- attach ----")
print(inspect.getsource(SC._attach_last_line))
print("---- dedupe ----")
print(inspect.getsource(SC.dedupe_reply_segments))
