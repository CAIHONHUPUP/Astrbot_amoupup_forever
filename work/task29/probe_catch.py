# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever")
from types import SimpleNamespace
import amo_state_consistency as SC

plugin = SimpleNamespace(state=SimpleNamespace(current_activity=lambda: {"place": "家", "zone": "home"}))
info = "楼下奶茶店排队排到马路牙子上，笑死我了。"
label = "奶茶店"
anchor = "老公，楼下奶茶店排队排到马路牙子上，笑死我了。"
body = "我在家呢。"
print("covers:", SC._covers("laugh", body, info))
print("catch:", repr(SC._catch_sentence("laugh", info, label, anchor, plugin, body)))
print("clean_info:", repr(SC._clean_info(info)))
print("clash:", SC._append_clashes_scene(SC._catch_sentence("laugh", info, label, anchor, plugin, body), plugin))
print("FOOD:", bool(SC._FOOD_ASK.search("哈哈哈")))
print("asks_followup:", SC._asks_share_followup("哈哈哈"))
