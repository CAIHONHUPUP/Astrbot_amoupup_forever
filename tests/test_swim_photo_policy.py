# -*- coding: utf-8 -*-
"""泳装/比基尼照片口径：在家且没病才换；病着可拒绝；在外面换不了。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_llm import restore_swim_later, swim_photo_policy, swim_photo_rule  # noqa: E402

SWIM = "想看你穿比基尼拍张照"


class PolicyTests(unittest.TestCase):
    def test_home_healthy_allows(self):
        text = swim_photo_policy(SWIM, "home", False)
        self.assertIn("别拒绝", text)
        self.assertIn("泳装", text)
        self.assertIn("就一张", text)

    def test_home_sick_may_refuse(self):
        text = swim_photo_policy(SWIM, "home", True)
        self.assertIn("可以拒绝", text)
        self.assertNotIn("别拒绝", text)
        self.assertIn("别客服腔", text)

    def test_outside_cannot_change(self):
        text = swim_photo_policy(SWIM, "work", False)
        self.assertIn("在外面", text)
        self.assertIn("回家再说", text)

    def test_not_a_swim_request(self):
        self.assertEqual(swim_photo_policy("发张自拍", "home", False), "")


class FakeState:
    def __init__(self, place, status, sick=False):
        self._place = place
        self._health = {"status": status, "sick": sick}

    def snapshot(self):
        return {"activity": {"activity": self._place, "place": self._place},
                "health": self._health}


class FakeWardrobe:
    def __init__(self):
        self.synced = []
        self.restored = 0

    def swim_outfit(self):
        return {"text": "黑色比基尼两件套", "pending": False}

    def _sync_sidecar(self, outfit):
        self.synced.append(outfit)

    def current(self):
        self.restored += 1


class FakePlugin:
    def __init__(self, place, status, sick=False):
        self.state = FakeState(place, status, sick)
        self.wardrobe = FakeWardrobe()
        self._swim_photo_until = 0


class RuleTests(unittest.TestCase):
    def test_home_healthy_locks_swim_outfit(self):
        plugin = FakePlugin("家", "健康")
        text = swim_photo_rule(plugin, SWIM)
        self.assertIn("黑色比基尼两件套", text)
        self.assertEqual(len(plugin.wardrobe.synced), 1)
        self.assertGreater(plugin._swim_photo_until, 0)
        self.assertIn("拍完就换回", text)

    def test_outside_does_not_lock(self):
        plugin = FakePlugin("公司", "健康")
        text = swim_photo_rule(plugin, SWIM)
        self.assertIn("在外面", text)
        self.assertEqual(plugin.wardrobe.synced, [])

    def test_sick_does_not_lock(self):
        plugin = FakePlugin("家", "感冒", sick=True)
        text = swim_photo_rule(plugin, SWIM)
        self.assertIn("可以拒绝", text)
        self.assertEqual(plugin.wardrobe.synced, [])

    def test_non_swim_request_restores_after_expiry(self):
        plugin = FakePlugin("家", "健康")
        plugin._swim_photo_until = 1  # 早就过期
        self.assertEqual(swim_photo_rule(plugin, "发张自拍"), "")
        self.assertEqual(plugin.wardrobe.restored, 1)

    def test_non_swim_request_restores_even_before_expiry(self):
        plugin = FakePlugin("家", "健康")
        plugin._swim_photo_until = 9999999999  # 还在“泳装临时”窗口里
        self.assertEqual(swim_photo_rule(plugin, "发张自拍"), "")
        self.assertEqual(plugin.wardrobe.restored, 1)
        self.assertEqual(plugin._swim_photo_until, 0)

    def test_delayed_restore_after_swim_photo(self):
        import asyncio
        plugin = FakePlugin("家", "健康")
        plugin._swim_photo_until = 1
        asyncio.run(restore_swim_later(plugin, 0))
        self.assertEqual(plugin.wardrobe.restored, 1)
        self.assertEqual(plugin._swim_photo_until, 0)


if __name__ == "__main__":
    unittest.main()
