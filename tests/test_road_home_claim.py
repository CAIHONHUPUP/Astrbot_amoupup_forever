# -*- coding: utf-8 -*-
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import amo_transition as tr  # noqa: E402


class FakeState:
    def current_activity(self):
        return {"place": "路上", "zone": "road", "activity": "打车回家"}


class FakePlugin:
    def __init__(self):
        self.state = FakeState()


class RoadHomeGuardTests(unittest.TestCase):
    def test_home_tv_claim_on_road_is_rewritten(self):
        plugin = FakePlugin()
        out = tr.guard_road_home(plugin, "我在家看电视，沙发上窝着")
        for bad in ("在家", "看电视", "沙发", "窝着"):
            self.assertNotIn(bad, out, out)
        self.assertIn("还在路上", out)

    def test_home_alone_on_road_is_rewritten(self):
        out = tr.guard_road_home(FakePlugin(), "我躺在床上刷手机")
        self.assertNotIn("在床上", out)
        self.assertIn("还在路上", out)


if __name__ == "__main__":
    unittest.main()
