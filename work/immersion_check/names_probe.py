# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
import astrbot_stub; astrbot_stub.install()
import amo_names
for text in ["问了，我就笑笑不说话", "她挺好的，还是老样子", "可以啊，好好吃。吃完跟我说说吃了啥。"]:
    print(repr(text), "->", amo_names.foreign_names(text))
s = amo_names.sanitize
for text in ["问了，我就笑笑不说话", "可以啊，好好吃。吃完跟我说说吃了啥。"]:
    print("sanitize", repr(text), "->", repr(s(text)) if callable(s) else s)
