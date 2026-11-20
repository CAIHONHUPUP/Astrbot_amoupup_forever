# -*- coding: utf-8 -*-
"""催办降温：他已经答应/要去做了，就别再催（确定性兜底）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_message import strip_repeated_urge  # noqa: E402


class UrgeGuardTests(unittest.TestCase):
    def test_ack_with_urge_falls_back_to_short_line(self):
        out = strip_repeated_urge("（把毯子拉到下巴，声音软软的）去吧，洗快点。", "好", "洗快点")
        self.assertEqual("嗯，去吧。", out)

    def test_keeps_non_urge_sentence(self):
        out = strip_repeated_urge("我在这边等你。水放了没？", "好", "")
        self.assertEqual("我在这边等你。", out)

    def test_leaving_message_is_capped(self):
        out = strip_repeated_urge("洗快点啊，别又在里面磨。我在这边等你。", "我先去洗澡了", "")
        self.assertEqual("我在这边等你。", out)

    def test_normal_chat_untouched(self):
        text = "我在公司呢，刚开完会。"
        self.assertEqual(text, strip_repeated_urge(text, "今天忙吗", ""))

    def test_soft_reply_without_urge_untouched(self):
        text = "（把毯子往上拉了拉，回你）乖，去吧。"
        self.assertEqual(text, strip_repeated_urge(text, "好", ""))

    def test_empty_reply_untouched(self):
        self.assertEqual("", strip_repeated_urge("", "好", "洗快点"))


if __name__ == "__main__":
    unittest.main()
