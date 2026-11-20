# -*- coding: utf-8 -*-
"""历史块的穿搭：按块的时间/地点取，夜里不许把睡衣写进外景（2026-11-20）。

现场：19:09 楼下小馆的火锅日记写成「穿着白睡衣就下来了」——日记在 20:00 之后
生成，wardrobe.current() 没收到块时间，按「现在」取到了睡衣。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_fact_timeline as ftl  # noqa: E402
from wardrobe_daily import WardrobeDailyMixin  # noqa: E402

DATA = {
    "items": [{"id": "t1", "name": "米色雪纺衬衫"},
              {"id": "b1", "name": "黑色短裤"},
              {"id": "s1", "name": "淡蓝色运动鞋"}],
    "outfits": [{"id": "o1", "top": "t1", "bottom": "b1", "shoes": "s1"}],
    "daily": {"2026-11-20": {"outfit": "o1"}},
}


class _Wardrobe(WardrobeDailyMixin):
    def __init__(self, data=None):
        self.data = data if data is not None else DATA

    def _load(self, *_a, **_k):
        return self.data


class AtHomeTests(unittest.TestCase):
    def setUp(self):
        self.w = _Wardrobe()

    def test_zone_outside_is_not_home(self):
        self.assertFalse(self.w._at_home({"place": "楼下小馆", "zone": "restaurant"}))

    def test_zone_home_variants(self):
        self.assertTrue(self.w._at_home({"place": "家", "zone": "home"}))
        self.assertTrue(self.w._at_home({"place": "", "zone": "home_bed"}))

    def test_unknown_place_still_unknown(self):
        self.assertIsNone(self.w._at_home({"place": "楼下小馆"}))
        self.assertIsNone(self.w._at_home({"place": "", "zone": "unknown"}))


class DaytimeOutfitTests(unittest.TestCase):
    def test_day_outfit_text(self):
        text = _Wardrobe().daytime_text("2026-11-20")
        self.assertIn("雪纺", text)
        self.assertNotIn("睡衣", text)

    def test_missing_day_returns_empty(self):
        self.assertEqual("", _Wardrobe().daytime_text("2026-11-19"))


class _FakeWardrobe:
    def __init__(self, day_text="", sleep_outfit=None):
        self._day = day_text
        self._sleep = sleep_outfit

    def daytime_text(self, day):
        return self._day

    def current(self, day=""):
        return self._sleep or {}


class _Plugin:
    def __init__(self, wardrobe):
        self.wardrobe = wardrobe


class ReadOutfitTests(unittest.TestCase):
    def test_uses_daytime_text(self):
        plugin = _Plugin(_FakeWardrobe("米色雪纺衬衫、黑色短裤"))
        self.assertIn("雪纺", ftl.read_outfit(plugin, __import__("datetime").date(2026, 11, 20)))

    def test_falls_back_without_sleepwear(self):
        class _NoDay(_FakeWardrobe):
            daytime_text = None
        plugin = _Plugin(_NoDay(sleep_outfit={"text": "白色棉质睡衣上衣", "sleep": True}))
        self.assertEqual("", ftl.read_outfit(plugin, __import__("datetime").date(2026, 11, 20)))


if __name__ == "__main__":
    unittest.main()
