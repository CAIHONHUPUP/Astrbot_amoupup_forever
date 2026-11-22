# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever")
from types import SimpleNamespace
import amo_state_consistency as SC
import inspect

print(inspect.getsource(SC._attach_last_line))
plugin = SimpleNamespace(state=SimpleNamespace(current_activity=lambda: {"place": "家", "zone": "home"}))
NL = chr(10)
ctx = "哈哈哈" + NL + "老公，楼下奶茶店排队排到马路牙子上，笑死我了。"
body = "我在家呢。"
last_user = SC._last_user_line(ctx)
demand = SC._reply_demand(last_user)
anchor = SC._anchor_share(ctx, last_user)
info = SC._share_info(anchor, plugin)
label = SC._info_label(anchor) if anchor else SC._info_label(info)
text = str(body or "").strip()
print("last_user", repr(last_user), "demand", demand)
print("anchor", repr(anchor))
print("info", repr(info))
print("label", label)
print("covers", SC._covers(demand, text, info))
catch = SC._catch_sentence(demand, info, label, anchor, plugin, text)
print("catch", repr(catch))
print("clash", SC._append_clashes_scene(catch, plugin))
print("FOOD", SC._FOOD_ASK.search(last_user))
print("asks", SC._asks_share_followup(last_user))
print("attach", repr(SC._attach_last_line(body, ctx, plugin)))
