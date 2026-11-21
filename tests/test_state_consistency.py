# -*- coding: utf-8 -*-
"""回复必须符合当前地点（截图回归）。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_state_consistency import rewrite  # noqa: E402


def _plugin(place="路上", zone="road"):
    return SimpleNamespace(state=SimpleNamespace(
        current_activity=lambda: {"place": place, "zone": zone}))


class StateConsistencyTests(unittest.TestCase):
    def test_road_reply_strips_home_actions(self):
        text = ("老公你快看这个视频\n这狗子傻得跟我有得一拼\n"
                "我等会儿弄点吃的自己煮的面，差点笑喷出来\n"
                "（刚到家把外套脱了，笑着回你）\n笑什么呢你，也在刷视频。")
        user = "我刚刷到一个视频，一只猫跟着节奏摇头，笑死我了"
        out = rewrite(text, _plugin(), user_text=user)
        self.assertNotIn("到家", out)
        self.assertNotIn("煮", out)
        self.assertNotIn("外套", out)
        self.assertNotIn("狗", out)
        self.assertNotIn("笑什么", out)
        self.assertNotIn("你快看", out)

    def test_repeat_share_is_continuation(self):
        history = "哈哈我刚刷到一个视频，笑死我了 一只猫跟着节奏摇头，越摇越嗨给你看看"
        text = ("老公你快看这个视频。这狗子傻得跟我有得一拼。"
                "我等会儿弄点吃的自己煮的面，差点笑喷出来。")
        out = rewrite(text, _plugin(place="家", zone="home"), user_text=history)
        self.assertNotIn("你快看这个视频", out)
        self.assertNotIn("弄点吃的", out)
        self.assertIn("煮点面", out)
        self.assertIn("猫", out)
        self.assertNotIn("狗", out)

    def test_home_reply_strips_road_claim(self):
        out = rewrite("我还在路上呢，快到了。", _plugin(place="家", zone="home"))
        self.assertNotIn("路上", out)
        self.assertIn("在家", out)

    def test_normal_reply_kept(self):
        text = "我在家呢，刚把汤煮上。"
        self.assertEqual(text, rewrite(text, _plugin(place="家", zone="home")))


if __name__ == "__main__":
    unittest.main()
