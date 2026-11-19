# -*- coding: utf-8 -*-
"""房屋系统：默认家 / 多套房 / 搬家 / 家具 / 破产回流 / 参考图。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from housing import Housing, classify_furniture  # noqa: E402


class FakeDB:
    def __init__(self, path=None):
        self.data = {}
        self.path = path or os.path.join(tempfile.mkdtemp(prefix="amo-house-"), "amo_forever.db")

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()

    def cfg_str(self, key, default=""):
        return default


class HousingSeedTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin()
        self.h = Housing(self.p)

    def test_default_is_rented_home(self):
        home = self.h.current()
        self.assertEqual(home["kind"], "rent")
        self.assertEqual(home["name"], "温馨出租屋")
        self.assertIn("1.2米单人床", home["furniture"])
        self.assertEqual(self.h.data["residence_id"], home["id"])

    def test_overview_card(self):
        text = self.h.card_text()
        self.assertIn("【当前居住】", text)
        self.assertIn("租房", text)
        self.assertIn("单人床", text)
        self.assertIn("/房屋 房产", text)

    def test_prompt_binds_furniture(self):
        line = self.h.prompt_line()
        self.assertIn("【家】", line)
        self.assertIn("单人床", line)
        self.assertIn("以这份清单为准", line)

    def test_own_page_empty(self):
        self.assertIn("暂无房产", self.h.card_text("房产"))

    def test_rent_page(self):
        text = self.h.card_text("租房")
        self.assertIn("当前租住", text)
        self.assertIn("月租 1500 元", text)

    def test_persists_between_instances(self):
        again = Housing(self.p)
        self.assertEqual(again.current()["id"], self.h.current()["id"])


class HousingMoveTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin()
        self.h = Housing(self.p)
        self.own = self.h.add_home("原木风两居室", kind="own", style="原木风", colors="米白色",
                                   furniture=["双人床", "布艺沙发", "木质茶几"])

    def test_add_and_move_in(self):
        ok, msg, home = self.h.move_in("原木风两居室")
        self.assertTrue(ok)
        self.assertIn("不用交房租", msg)
        self.assertEqual(self.h.current()["id"], self.own["id"])
        self.assertEqual(self.h.current()["status"], "living")

    def test_own_page_lists_current(self):
        self.h.move_in("原木风两居室")
        text = self.h.card_text("房产")
        self.assertIn("自住中", text)
        self.assertIn("原木风两居室", text)

    def test_move_unknown_lists_names(self):
        ok, msg, home = self.h.move_in("不存在的家")
        self.assertFalse(ok)
        self.assertIn("温馨出租屋", msg)
        self.assertIsNone(home)


class FurnitureTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin()
        self.h = Housing(self.p)

    def test_small_item_also_needs_new_image(self):
        self.h.current()["images_needed"] = False
        out = self.h.add_furniture("台灯")
        self.assertTrue(out["ok"])
        self.assertFalse(out["big"])
        self.assertTrue(self.h.current()["images_needed"], "小家具也会出现在画面里")

    def test_big_item_triggers_new_image(self):
        self.h.current()["images_needed"] = False
        out = self.h.add_furniture("双人床")
        self.assertTrue(out["ok"])
        self.assertTrue(out["big"])
        self.assertTrue(self.h.current()["images_needed"])
        self.assertIn("双人床", self.h.prompt_line())

    def test_duplicate_rejected(self):
        out = self.h.add_furniture("1.2米单人床")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "exists")

    def test_classify(self):
        self.assertEqual(classify_furniture("布艺沙发"), "big")
        self.assertEqual(classify_furniture("杯子"), "small")


class BankruptcyTests(unittest.TestCase):
    def test_bankruptcy_returns_to_cheapest_rent(self):
        p = FakePlugin()
        h = Housing(p)
        h.add_home("豪宅", kind="own", furniture=["双人床"])
        h.move_in("豪宅")
        home = h.on_bankruptcy()
        self.assertIsNotNone(home)
        self.assertEqual(h.current()["kind"], "rent")
        self.assertEqual(h.current()["rent_cents"], 100000)

    def test_bankruptcy_creates_rent_if_none(self):
        p = FakePlugin()
        h = Housing(p)
        h.add_home("自住房", kind="own")
        h.data["homes"] = [home for home in h.homes() if home["kind"] != "rent"]
        h.data["residence_id"] = h.data["homes"][0]["id"]
        home = h.on_bankruptcy()
        self.assertIsNotNone(home)
        self.assertEqual(h.current()["kind"], "rent")


class MoveCleanupTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin()
        self.h = Housing(self.p)
        self.old = self.h.current()
        os.makedirs(self.h.image_dir(), exist_ok=True)
        self.living = os.path.join(self.h.image_dir(), "old-living.png")
        self.bedroom = os.path.join(self.h.image_dir(), "old-bedroom.png")
        for path in (self.living, self.bedroom):
            with open(path, "wb") as fh:
                fh.write(b"x")
        self.h.set_room_image("living", self.living)
        self.h.set_room_image("bedroom", self.bedroom)

    def test_move_deletes_old_files_and_clears_paths(self):
        self.h.add_home("新房", kind="own")
        ok, _msg, _home = self.h.move_in("新房")
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(self.living))
        self.assertFalse(os.path.exists(self.bedroom))
        self.assertEqual(self.old["images"],
                         {"living": "", "bedroom": "", "bathroom": "", "kitchen": ""})
        self.assertTrue(self.old["images_needed"])

    def test_rent_to_own_ends_contract(self):
        self.h.add_home("新房", kind="own")
        self.h.move_in("新房")
        self.assertEqual(self.old["status"], "expired")
        self.assertTrue(self.old.get("contract_ended"))
        self.assertIn("目前未产生租房支出。", self.h.card_text("租房"))
        self.assertNotIn("月租", self.h.card_text("租房"))

    def test_new_home_keeps_its_own_images(self):
        own = self.h.add_home("新房", kind="own")
        keep = os.path.join(self.h.image_dir(), "keep.png")
        with open(keep, "wb") as fh:
            fh.write(b"x")
        own["images"] = {"living": keep, "bedroom": ""}
        self.h.move_in("新房")
        self.assertTrue(os.path.exists(keep))
        self.assertEqual(self.h.current()["images"]["living"], keep)

    def test_pending_message_while_generating(self):
        self.h.add_home("新房", kind="own")
        self.h.move_in("新房")
        self.assertIn("图片生成中", self.h.card_text())

    def test_bankruptcy_deletes_old_own_images(self):
        own = self.h.add_home("豪宅", kind="own")
        self.h.move_in("豪宅")
        path = os.path.join(self.h.image_dir(), "own-living.png")
        with open(path, "wb") as fh:
            fh.write(b"x")
        own["images"] = {"living": path, "bedroom": ""}
        self.h.on_bankruptcy()
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.h.current()["kind"], "rent")


class RentSellTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin()
        self.h = Housing(self.p)
        self.own = self.h.add_home("奶油风小公寓", kind="own")
        self.h.move_in(self.own["id"])
        self.rent = self.h.add_home("温馨出租屋", kind="rent", rent_cents=150000)
        self.h.move_in(self.rent["id"])

    def test_rent_out_idle_own_home(self):
        ok, msg = self.h.rent_out("奶油风小公寓", 300000)
        self.assertTrue(ok)
        self.assertIn("收租", msg)
        self.assertEqual(self.own["status"], "rented_out")
        self.assertEqual(self.own["rent_cents"], 300000)

    def test_cannot_rent_current_home(self):
        ok, msg = self.h.rent_out("温馨出租屋")
        self.assertFalse(ok)

    def test_sell_current_house_moves_to_rent(self):
        ok, msg = self.h.sell_home("温馨出租屋")
        self.assertTrue(ok)
        self.assertEqual(self.h.current()["kind"], "rent")

    def test_sell_own_home_removes_it(self):
        ok, _msg = self.h.sell_home("奶油风小公寓")
        self.assertTrue(ok)
        self.assertIsNone(self.h.by_ref("奶油风小公寓"))

    def test_renovate_marks_image_needed(self):
        self.h.current()["images_needed"] = False
        home = self.h.set_style("原木风", "浅木色")
        self.assertEqual(home["style"], "原木风")
        self.assertTrue(home["images_needed"])


class ImageTests(unittest.TestCase):
    def test_image_dir(self):
        p = FakePlugin()
        h = Housing(p)
        self.assertTrue(h.image_dir().replace("\\", "/").endswith("outfit_refs/家"))

    def test_set_room_images_clears_needed(self):
        p = FakePlugin()
        h = Housing(p)
        for room in ("living", "bedroom", "bathroom"):
            h.set_room_image(room, room + ".png")
            self.assertTrue(h.current()["images_needed"], room)
        h.set_room_image("kitchen", "kitchen.png")
        self.assertFalse(h.current()["images_needed"])
        self.assertEqual(h.room_image("living"), "living.png")


if __name__ == "__main__":
    unittest.main()
