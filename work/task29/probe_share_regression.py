# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever")
from types import SimpleNamespace
import amo_state_consistency as SC
import amo_share_ledger as LED

NL = chr(10)
ctx = "哈哈哈" + NL + "老公，楼下奶茶店排队排到马路牙子上，笑死我了。"
last_user = SC._last_user_line(ctx)
demand = SC._reply_demand(last_user)
anchor = SC._anchor_share(ctx, last_user)
plugin = SimpleNamespace(state=SimpleNamespace(current_activity=lambda: {"place": "家", "zone": "home"}))
info = SC._share_info(anchor, plugin)
label = SC._info_label(anchor) if anchor else SC._info_label(info)
print("last_user:", repr(last_user), "demand:", demand)
print("anchor:", repr(anchor))
print("info:", repr(info))
print("label:", label)
print("body:", repr(SC._attach_last_line("我在家呢。", ctx, plugin)))
print("rewrite:", repr(SC.rewrite("我在家呢。", plugin, ctx)))
print("last_info:", repr(LED.last_info("老公，楼下奶茶店排队排到马路牙子上，笑死我了。")))
print("share_info empty?", not info)
