# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever")
import amo_state_consistency as SC
for text in ("家里人说想我了。", "最近是不是胖了？", "晚安。", "你跟谁在一起呢？",
             "你在家吗？", "晚上吃啥呀？", "吃了吗？"):
    m = SC._ASK_WHERE.search(text)
    print(repr(text), "->", bool(m), m.group(0) if m else "",
          "| where_words:", [w for w in SC._WHERE_WORDS if w in text],
          "| last_line:", SC._last_user_line(text))
