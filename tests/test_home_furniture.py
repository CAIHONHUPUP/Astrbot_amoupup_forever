# -*- coding: utf-8 -*-
"""房屋家具库：租房/买房预设、房间 Prompt、重绘阈值、UI 截断、图片状态。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from housing import (  # noqa: E402
    Housing,
    OWN_BEDROOM,
    OWN_LIVING,
    RENT_BEDROOM,
    RENT_LIVING,
    build_room_image_prompt,
    room_of_item,
    season_line,
)


class FakeDB:
    def __init__(self):
        self.data = {}
        self.path = os.path.join(tempfile.mkdtemp(prefix="amo-furn-"), "amo_forever.db")

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()

    def cfg_str(self, key, default=""):
        return default


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin()
        self.h = Housing(self.p)

    def test_rent_seed_has_basic_furniture(self):
        home = self.h.current()
        self.assertEqual(home["room_furniture"]["living"], RENT_LIVING)
        self.assertEqual(home["room_furniture"]["bedroom"], RENT_BEDROOM)
        self.assertIn("二手布艺沙发", home["furniture"])
        self.assertIn("1.2米单人床", home["furniture"])

    def test_own_home_gets_upgraded_furniture(self):
        home = self.h.add_home("原木风两居室", kind="own")
        self.assertEqual(home["room_furniture"]["living"], OWN_LIVING)
        self.assertEqual(home["room_furniture"]["bedroom"], OWN_BEDROOM)
        self.assertIn("三人位软包沙发", home["furniture"])

    def test_bedroom_classification(self):
        self.assertEqual(room_of_item("1.8米双人床"), "bedroom")
        self.assertEqual(room_of_item("落地灯"), "living")


class PromptTests(unittest.TestCase):
    def test_room_prompt_matches_spec(self):
        prompt, negative = build_room_image_prompt("客厅", "原木风", "米白色", ["布艺沙发", "木质茶几"])
        self.assertIn("一张空无一人的客厅室内图", prompt)
        self.assertIn("第一人称视角", prompt)
        self.assertIn("8k分辨率", prompt)
        self.assertIn("装修风格为原木风", prompt)
        self.assertIn("布艺沙发、木质茶几", prompt)
        self.assertIn("合理搭配进整体的装修风格", prompt)
        self.assertIn("人物", negative)
        self.assertIn("多出来的腿", negative)

    def test_season_words(self):
        self.assertIn("电风扇", season_line(dt.datetime(2026, 7, 1)))
        self.assertIn("暖气片", season_line(dt.datetime(2026, 1, 1)))
        self.assertEqual(season_line(dt.datetime(2026, 4, 1)), "")

    def test_housing_room_prompt_uses_room_lists(self):
        p = FakePlugin()
        h = Housing(p)
        living, _ = h.room_prompt("客厅")
        bedroom, _ = h.room_prompt("卧室")
        self.assertIn("沙发", living)
        self.assertNotIn("1.2米单人床", living)
        self.assertIn("床", bedroom)


class RedrawTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin()
        self.h = Housing(self.p)
        self.home = self.h.current()

    def test_small_item_also_triggers_redraw(self):
        self.home["images_needed"] = False
        out = self.h.add_furniture("台灯")
        self.assertTrue(out["ok"])
        self.assertFalse(out["big"])
        self.assertTrue(out["redraw"])
        self.assertTrue(self.home["images_needed"])

    def test_big_item_triggers_redraw_when_cool(self):
        self.home["last_image_ts"] = 0
        out = self.h.add_furniture("三人位沙发")
        self.assertTrue(out["big"])
        self.assertTrue(out["redraw"])
        self.assertTrue(self.home["images_needed"])

    def test_furniture_change_clears_cooldown(self):
        import time
        self.home["last_image_ts"] = time.time()
        out = self.h.add_furniture("双人床")
        self.assertTrue(out["big"])
        self.assertTrue(out["redraw"], "换家具要立刻允许重画")
        self.assertEqual(self.home["image_status"], "pending")

    def test_image_status_helpers(self):
        self.h.mark_image_failed()
        self.assertEqual(self.h.current()["image_status"], "pending")
        self.h.mark_image_ready(now=123)
        self.assertEqual(self.h.current()["image_status"], "ready")
        self.assertFalse(self.h.current()["images_needed"])
        self.assertEqual(self.h.current()["last_image_ts"], 123)


class CardTests(unittest.TestCase):
    def test_furniture_list_is_truncated(self):
        p = FakePlugin()
        h = Housing(p)
        for name in ("椅子", "书柜", "绿植", "挂画", "落地灯", "边几", "花瓶"):
            h.add_furniture(name)
        text = h.card_text()
        self.assertIn("…等 %d 件物品" % len(h.current()["furniture"]), text)
        self.assertNotIn("、".join(h.current()["furniture"][6:]), text)

    def test_pending_with_old_image_shows_reassurance(self):
        p = FakePlugin()
        h = Housing(p)
        h.current()["images"]["living"] = "old.png"
        h.mark_image_failed()
        text = h.card_text()
        self.assertIn("旧图", text)
        self.assertIn("拆快递", text)


if __name__ == "__main__":
    unittest.main()
