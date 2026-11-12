# -*- coding: utf-8 -*-
"""泳装 + 玩水日程（2026-11-12 用户要求）。

- 衣柜新增 swim 范围的单品/搭配，只有上衣（泳衣）和裤子（泳裤），没有鞋；
- 日程休息日偶尔去游泳馆/海边/水上乐园，一周最多一次，冬天不去，旅游例外；
- 在玩水地点时 current() 自动换泳装；照片背景是泳池/海边/水上乐园；
- 参考图落在 outfit_refs/泳装/上衣（外套）|裤子/ 下。
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from schedule_engine import ScheduleEngine  # noqa: E402
from wardrobe import SWIM_PLACE_WORDS, Wardrobe  # noqa: E402
import outfit_refs  # noqa: E402
import photo_scene  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "plugin_data", "Astrbot_Amo_forever", "amo_forever.db")
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, temp=20, place="家", activity="在家待着"):
        self.weather = {"temp": temp}
        self.activity = {"place": place, "activity": activity}

    def current_activity(self):
        return dict(self.activity)


class FakePlugin:
    def __init__(self, root, temp=20, place="家", activity="在家待着"):
        self.db = FakeDB(root)
        self.state = FakeState(temp, place, activity)
        self.plugin_dir = os.path.join(root, "plugin")


def make_wardrobe(place="家", activity="在家待着"):
    root = tempfile.mkdtemp(prefix="amo-swim-")
    os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
    return Wardrobe(FakePlugin(root, 30, place, activity)), root


class SwimSeedTests(unittest.TestCase):
    def test_at_least_five_swim_outfits(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe.snapshot()
        self.assertGreaterEqual(data["swim_outfit_count"], 5)

    def test_swim_items_only_in_swim_categories(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe.snapshot()
        swim_names = set()
        for cat in data["swim_categories"]:
            for item in cat["items"]:
                swim_names.add(item["name"])
        self.assertIn("黑色比基尼泳衣上衣", swim_names)
        day_names = set()
        for cat in data["categories"]:
            for item in cat["items"]:
                day_names.add(item["name"])
        self.assertFalse(day_names & swim_names, "泳装不能混进白天衣柜")
        sleep_names = set()
        for cat in data["sleep_categories"]:
            for item in cat["items"]:
                sleep_names.add(item["name"])
        self.assertFalse(sleep_names & swim_names)

    def test_swim_categories_have_no_shoes(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe.snapshot()
        keys = [cat["key"] for cat in data["swim_categories"]]
        self.assertEqual(keys, ["coat", "top", "bottom", "skirt", "shoes"])
        for cat in data["swim_categories"]:
            if cat["key"] in ("top", "bottom"):
                self.assertTrue(cat["items"])
            else:
                self.assertEqual(cat["items"], [])

    def test_fresh_seed_keeps_swim_outfits(self):
        # 回归：裙子迁移曾经按「必须有鞋」筛搭配，把没鞋的泳装整套删光
        wardrobe, _ = make_wardrobe()
        data = wardrobe._load()
        self.assertEqual(len([o for o in data["outfits"] if o.get("swim")]), 7)


class SwimWearTests(unittest.TestCase):
    def test_at_swim_detects_places(self):
        for place in ("市游泳馆", "海边", "水上乐园", "沙滩"):
            wardrobe, _ = make_wardrobe(place=place, activity="在玩水")
            self.assertTrue(wardrobe.at_swim(), place)
        wardrobe, _ = make_wardrobe(place="家", activity="在家待着")
        self.assertFalse(wardrobe.at_swim())

    def test_swim_place_words_cover_three_places(self):
        for word in ("游泳", "海边", "水上乐园"):
            self.assertTrue(any(word in text for text in SWIM_PLACE_WORDS))

    def test_current_switches_to_swimwear(self):
        wardrobe, _ = make_wardrobe(place="市游泳馆", activity="在泳池里游了几圈")
        current = wardrobe.current(hour=10)
        self.assertTrue(current.get("swim"))
        self.assertTrue(current.get("top"))
        self.assertTrue(current.get("bottom"))
        self.assertEqual(current.get("shoes"), "")
        self.assertNotIn("鞋", current.get("text") or "")

    def test_current_keeps_day_outfit_at_home(self):
        wardrobe, _ = make_wardrobe()
        current = wardrobe.current(hour=10)
        self.assertFalse(current.get("swim"))
        self.assertTrue(current.get("shoes"))

    def test_swim_outfit_stable_inside_a_day(self):
        wardrobe, _ = make_wardrobe()
        first = wardrobe.swim_outfit("2026-11-12")
        second = wardrobe.swim_outfit("2026-11-12")
        self.assertEqual(first["outfit"], second["outfit"])

    def test_set_swim_rejects_day_items(self):
        wardrobe, _ = make_wardrobe()
        day_top = wardrobe.snapshot()["categories"][1]["items"][0]
        result = wardrobe.set_swim(top=day_top["id"], bottom="")
        self.assertFalse(result["ok"])

    def test_set_swim_accepts_matching_set(self):
        wardrobe, _ = make_wardrobe()
        entry = wardrobe.snapshot()["swim_sets"][0]
        result = wardrobe.set_swim("2026-11-12", top=entry["top_id"], bottom=entry["bottom_id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["top"], entry["top"])
        self.assertEqual(result["bottom"], entry["bottom"])
        self.assertEqual(result["shoes"], "")
        self.assertEqual(result["set"], entry["set"])

    def test_set_swim_rejects_cross_set_pairing(self):
        wardrobe, _ = make_wardrobe()
        sets = wardrobe.snapshot()["swim_sets"]
        result = wardrobe.set_swim("2026-11-12", top=sets[0]["top_id"], bottom=sets[1]["bottom_id"])
        self.assertFalse(result["ok"])
        self.assertIn("整套", result["msg"])

    def test_every_swim_set_is_complete(self):
        wardrobe, _ = make_wardrobe()
        entries = wardrobe.snapshot()["swim_sets"]
        self.assertEqual(len(entries), 7)
        for entry in entries:
            self.assertTrue(entry["top"])
            self.assertTrue(entry["bottom"])
            self.assertEqual(entry["set"], entry["set"])
        # 上衣和泳裤名字必须成对（同一套的名字里带同样的款式词）
        pairs = {(entry["top"], entry["bottom"]) for entry in entries}
        self.assertIn(("黑色比基尼泳衣上衣", "黑色比基尼泳裤"), pairs)
        self.assertIn(("白色荷叶边泳衣上衣", "白色荷叶边泳裤"), pairs)

    def test_add_single_swim_item_is_rejected(self):
        wardrobe, _ = make_wardrobe()
        result = wardrobe.add_item("top", "橙色波点泳衣上衣", swim=True)
        self.assertFalse(result["ok"])
        self.assertIn("整套", result["msg"])
        data = wardrobe.snapshot()
        names = [item["name"] for cat in data["swim_categories"] for item in cat["items"]]
        self.assertNotIn("橙色波点泳衣上衣", names)

    def test_delete_swim_item_removes_ref_image(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe.snapshot()
        top = data["swim_categories"][1]["items"][0]
        item = {"category": "top", "name": top["name"], "swim": True}
        ref = outfit_refs.item_ref_path(wardrobe.plugin, item)
        os.makedirs(os.path.dirname(ref), exist_ok=True)
        with open(ref, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        self.assertTrue(wardrobe.delete_item(top["id"])["ok"])
        self.assertFalse(os.path.isfile(ref))


class SwimRefTests(unittest.TestCase):
    def test_scope_folder_is_swim(self):
        item = {"category": "top", "name": "黑色比基尼上衣", "swim": True}
        self.assertEqual(outfit_refs.scope_of(item), "swim")

    def test_ref_paths_under_swim_folder(self):
        wardrobe, _ = make_wardrobe()
        item = {"category": "top", "name": "黑色比基尼上衣", "swim": True}
        path = outfit_refs.item_ref_path(wardrobe.plugin, item)
        self.assertIn("泳装", path)
        self.assertIn("上衣（外套）", path)
        self.assertTrue(path.endswith("黑色比基尼上衣.png"))
        bottom = {"category": "bottom", "name": "黑色高腰泳裤", "swim": True}
        bottom_path = outfit_refs.item_ref_path(wardrobe.plugin, bottom)
        self.assertIn("泳装", bottom_path)
        self.assertIn("裤子", bottom_path)

    def test_pending_includes_swim_items(self):
        wardrobe, _ = make_wardrobe()
        items = outfit_refs.batch_items(wardrobe)
        swim = [item for item in items if item.get("swim")]
        self.assertGreaterEqual(len(swim), 10)
        self.assertTrue(all(item.get("category") in ("top", "bottom") for item in swim))


class SwimSceneTests(unittest.TestCase):
    def test_scene_is_pool_beach_or_park(self):
        cases = {
            ("市游泳馆", "在泳池里游了几圈"): "泳池",
            ("海边", "在海边踩浪"): "沙滩",
            ("水上乐园", "玩滑道"): "滑道",
        }
        for (place, act), expect in cases.items():
            activity = {"place": place, "activity": act}
            self.assertIn(expect, photo_scene.scene_for(activity))

    def test_is_swim(self):
        self.assertTrue(photo_scene.is_swim({"place": "海边"}))
        self.assertTrue(photo_scene.is_swim({"activity": "在水上乐园玩"}))
        self.assertFalse(photo_scene.is_swim({"place": "家", "activity": "看电视"}))


class FakeDBKV:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeConfig:
    def __init__(self, min_temp=22.0, prob=1.0):
        self.db = FakeDBKV()
        self.min_temp = min_temp
        self.prob = prob

    def cfg_float(self, key, default=0.0):
        if key == "schedule_swim_min_temp":
            return self.min_temp
        if key == "schedule_swim_prob":
            return self.prob
        return default


class FakeEngineSelf:
    def __init__(self, min_temp=22.0, prob=1.0):
        self.p = FakeConfig(min_temp, prob)


def pick(engine, day, rest=True, temp=30, holiday_days=0):
    return ScheduleEngine._pick_swim(
        engine, day, {"rest": rest, "holiday_days": holiday_days},
        {"temp": temp, "text": "晴"}, {}, {"pain": 0}, {"kind": "none"}, True, 1, "东莞",
    )


class PickSwimTests(unittest.TestCase):
    WEEK = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12",
            "2026-11-13", "2026-11-14", "2026-11-15"]

    def test_not_on_non_rest_days(self):
        engine = FakeEngineSelf()
        for day in self.WEEK:
            self.assertIsNone(pick(engine, day, rest=False))

    def test_winter_no_swim(self):
        engine = FakeEngineSelf()
        for day in self.WEEK:
            self.assertIsNone(pick(engine, day, temp=8))

    def test_only_one_outing_per_week(self):
        engine = FakeEngineSelf()
        results = [pick(engine, day) for day in self.WEEK]
        chosen = [item for item in results if item]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["zone"], "scenic")
        self.assertIn(chosen[0]["spot"], ("游泳馆", "海边", "水上乐园"))

    def test_marks_week_in_kv(self):
        engine = FakeEngineSelf()
        for day in self.WEEK:
            item = pick(engine, day)
            if item:
                key = [k for k in engine.p.db.kv if k.startswith("swim_week:")][0]
                value = engine.p.db.kv[key]
                self.assertTrue(value.startswith(item["spot"] + "|"))
                break

    def test_travel_can_go_again(self):
        engine = FakeEngineSelf()
        # 先手动占掉这周（前一天）
        engine.p.db.kv["2026-11-09"] = ""
        week = datetime.date(2026, 11, 12).isocalendar()
        engine.p.db.kv["swim_week:%04d-%02d" % (week[0], week[1])] = "游泳|2026-11-11"
        item = pick(engine, "2026-11-12", holiday_days=5)
        self.assertIsNotNone(item)

    def test_sick_skips(self):
        engine = FakeEngineSelf()
        for day in self.WEEK:
            result = ScheduleEngine._pick_swim(
                engine, day, {"rest": True}, {"temp": 30}, {"sick": True}, {"pain": 0},
                {"kind": "none"}, True, 1, "东莞")
            self.assertIsNone(result)


class SwimSegmentsTests(unittest.TestCase):
    def test_segments_cover_day_and_include_swim(self):
        engine = object.__new__(ScheduleEngine)
        segments = engine._swim_segments({
            "home": "家",
            "swim": {"place": "东莞·海边", "spot": "海边",
                     "activity": "在海边踩浪、玩水", "detail": "浪打过来"},
        })
        hours = sum(int(seg["end"].split(":")[0]) - int(seg["start"].split(":")[0])
                    for seg in segments)
        self.assertEqual(hours, 24)
        swim_segs = [seg for seg in segments if seg["zone"] == "scenic"]
        self.assertGreaterEqual(len(swim_segs), 3)
        self.assertTrue(all("海边" in str(seg.get("place") or "") for seg in swim_segs))
        self.assertTrue(any("玩水" in str(seg.get("activity") or "") for seg in swim_segs))

    def test_swim_card_pending_when_not_swimming(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe.snapshot()
        self.assertTrue(data["swim"].get("pending"))
        self.assertIn("游泳", data["swim"].get("text") or "")
        self.assertFalse((data.get("current") or {}).get("wearing_swim"))

    def test_swim_card_ready_and_day_card_marked_when_swimming(self):
        wardrobe, _ = make_wardrobe(place="游泳馆", activity="游泳")
        data = wardrobe.snapshot()
        self.assertFalse(data["swim"].get("pending"))
        self.assertTrue(data["swim"].get("swim"))
        self.assertTrue((data.get("current") or {}).get("wearing_swim"))
        self.assertIn("泳衣", (data["current"] or {}).get("text") or "")

    def test_after_swim_goes_home_day_then_night(self):
        # 玩水结束、回家：17 点还是白天那套；20 点后在家换睡衣
        wardrobe, _ = make_wardrobe(place="家", activity="回家洗澡")
        day = wardrobe.current(hour=17)
        self.assertFalse(day.get("swim"))
        self.assertFalse(day.get("sleep"))
        night = wardrobe.current(hour=20)
        self.assertTrue(night.get("sleep"))

    def test_after_swim_on_the_road_keeps_day_outfit(self):
        # 20 点还在路上（游玩回家途中）先别换睡衣，到家再换
        wardrobe, _ = make_wardrobe(place="路上", activity="回家")
        on_road = wardrobe.current(hour=20)
        self.assertTrue(on_road.get("sleep") is not True)

    def test_photo_outfit_uses_swim_outfit(self):
        class FakeWardrobe:
            def current(self, day=""):
                return {"text": "粉色蝴蝶结泳衣上衣，粉色蝴蝶结泳裤", "swim": True}

        plugin = type("P", (), {})()
        plugin.wardrobe = FakeWardrobe()
        engine = object.__new__(PhotoEngine)
        engine.plugin = plugin
        engine._outfit_day = ""
        engine._outfit_text = ""
        self.assertEqual(engine.outfit_today(), "粉色蝴蝶结泳衣上衣，粉色蝴蝶结泳裤")


if __name__ == "__main__":
    unittest.main()
