# -*- coding: utf-8 -*-
"""穿搭衣柜单测：30 套种子、每日锁定、增删、礼物归类、万象画卷 sidecar。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from wardrobe import Wardrobe, classify_item  # noqa: E402


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "plugin_data", "Astrbot_Amo_forever", "amo_forever.db")
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, temp=20, place="家"):
        self.weather = {"temp": temp}
        self.activity = {"place": place}

    def current_activity(self):
        return dict(self.activity)


class FakePlugin:
    def __init__(self, root, temp=20, place="家"):
        self.db = FakeDB(root)
        self.state = FakeState(temp, place)
        self.plugin_dir = os.path.join(root, "plugin")


def make_wardrobe(temp=20, place="家"):
    root = tempfile.mkdtemp(prefix="amo-wardrobe-")
    os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
    return Wardrobe(FakePlugin(root, temp, place)), root


class SeedTests(unittest.TestCase):
    def test_seed_has_30_outfits_and_three_categories(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe.snapshot()
        self.assertGreaterEqual(data["outfit_count"], 30)
        categories = {item["key"]: item for item in data["categories"]}
        self.assertEqual(set(categories), {"coat", "top", "bottom", "skirt", "shoes"})
        for key in ("coat", "top", "bottom", "skirt", "shoes"):
            self.assertTrue(categories[key]["items"], key + " 不该为空")

    def test_seed_survives_reload(self):
        wardrobe, root = make_wardrobe()
        first = wardrobe.snapshot()
        again = Wardrobe(FakePlugin(root)).snapshot()
        self.assertEqual(first["outfit_count"], again["outfit_count"])


class DayVarietyTests(unittest.TestCase):
    def test_day_outfits_reach_target(self):
        wardrobe, _ = make_wardrobe()
        day = [outfit for outfit in wardrobe._load()["outfits"] if not outfit.get("sleep")]
        self.assertGreaterEqual(len(day), 100)

    def test_auto_combos_are_same_color_or_material(self):
        from wardrobe import combo_compatible
        wardrobe, _ = make_wardrobe()
        data = wardrobe._load()
        items = {item["id"]: item for item in data["items"]}
        checked = 0
        for outfit in data["outfits"]:
            if not outfit.get("combo"):
                continue
            names = [str((items.get(outfit.get(key)) or {}).get("name") or "")
                     for key in ("coat", "top", "bottom", "skirt", "shoes")]
            self.assertTrue(combo_compatible(*names), names)
            checked += 1
        self.assertGreater(checked, 50)

    def test_day_outfits_never_use_night_items(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe._load()
        items = {item["id"]: item for item in data["items"]}
        for outfit in data["outfits"]:
            if outfit.get("sleep"):
                continue
            for key in ("coat", "top", "bottom", "skirt", "shoes"):
                item = items.get(outfit.get(key)) or {}
                self.assertFalse(item.get("sleep"), key)

    def test_panel_counts_day_seed_and_combos(self):
        wardrobe, _ = make_wardrobe()
        snap = wardrobe.snapshot()
        self.assertEqual(snap["day_seed_count"], 30)
        self.assertGreater(snap["outfit_count"], snap["day_seed_count"])


class TodayTests(unittest.TestCase):
    def test_today_is_stable_and_has_three_parts(self):
        wardrobe, _ = make_wardrobe()
        first = wardrobe.today("2026-11-11")
        second = wardrobe.today("2026-11-11")
        self.assertEqual(first["outfit"], second["outfit"])
        self.assertEqual(first["text"], second["text"])
        self.assertIn(len(first["text"].split("，")), (2, 3, 4, 5))
        self.assertTrue(first["shoes"])
        self.assertTrue((first["top"] and first["bottom"]) or first["skirt"])

    def test_reroll_changes_outfit(self):
        wardrobe, _ = make_wardrobe()
        first = wardrobe.today("2026-11-11")
        second = wardrobe.reroll("2026-11-11")
        self.assertNotEqual(first["outfit"], second["outfit"])

    def test_hot_weather_skips_warm_tops(self):
        wardrobe, _ = make_wardrobe(temp=32)
        warm = ("外套", "大衣", "毛衣", "针织", "卫衣", "风衣", "西装", "马甲")
        for day in ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"):
            today = wardrobe.today(day)
            self.assertFalse(any(word in today["top"] for word in warm), today["top"])


class ItemTests(unittest.TestCase):
    def test_add_item_creates_outfits(self):
        wardrobe, _ = make_wardrobe()
        before = wardrobe.snapshot()["outfit_count"]
        result = wardrobe.add_item("top", "白色雪纺衬衫")
        self.assertTrue(result["ok"])
        self.assertGreater(result["combos"], 0)
        self.assertGreater(wardrobe.snapshot()["outfit_count"], before)

    def test_add_duplicate_is_ignored(self):
        wardrobe, _ = make_wardrobe()
        first = wardrobe.add_item("shoes", "白色帆布鞋")
        self.assertTrue(first["ok"])
        self.assertTrue(first.get("dup"))

    def test_delete_item_removes_its_outfits(self):
        wardrobe, _ = make_wardrobe()
        added = wardrobe.add_item("top", "测试用上衣")
        item_id = added["item"]["id"]
        wardrobe.today("2026-11-11")
        result = wardrobe.delete_item(item_id)
        self.assertTrue(result["ok"])
        data = wardrobe.snapshot()
        names = [item["name"] for cat in data["categories"] for item in cat["items"]]
        self.assertNotIn("测试用上衣", names)

    def test_bad_category_is_rejected(self):
        wardrobe, _ = make_wardrobe()
        result = wardrobe.add_item("hat", "棒球帽")
        self.assertFalse(result["ok"])

    def test_delete_everything_reseeds_valid_outfit(self):
        wardrobe, _ = make_wardrobe()
        with wardrobe._lock:
            ids = [item["id"] for item in wardrobe._load()["items"]]
        for item_id in ids:
            wardrobe.delete_item(item_id)
        from wardrobe import is_dress
        today = wardrobe.today("2026-11-12")
        self.assertTrue(today["shoes"])
        self.assertTrue((today["top"] and today["bottom"]) or today["skirt"])
        if today["skirt"]:
            self.assertTrue(is_dress(today["skirt"]) or bool(today["top"]))
        self.assertIn(len(today["text"].split("，")), (2, 3, 4, 5))


class ManualTodayTests(unittest.TestCase):
    def _ids(self, wardrobe):
        data = wardrobe._load()
        by_cat = {}
        for item in data["items"]:
            by_cat.setdefault(item["category"], []).append(item["id"])
        return by_cat

    def test_set_today_uses_chosen_items(self):
        wardrobe, _ = make_wardrobe()
        ids = self._ids(wardrobe)
        result = wardrobe.set_today(
            "2026-11-11", top=ids["top"][0], bottom=ids["bottom"][1], shoes=ids["shoes"][2]
        )
        self.assertTrue(result["ok"])
        names = {item["id"]: item["name"] for cat in wardrobe.snapshot()["categories"]
                 for item in cat["items"]}
        self.assertEqual(result["top"], names[ids["top"][0]])
        self.assertEqual(result["bottom"], names[ids["bottom"][1]])
        self.assertEqual(result["shoes"], names[ids["shoes"][2]])
        self.assertEqual(wardrobe.today("2026-11-11")["outfit"], result["outfit"])

    def test_set_today_without_shoes_keeps_existing(self):
        wardrobe, _ = make_wardrobe()
        ids = self._ids(wardrobe)
        first = wardrobe.set_today(
            "2026-11-11", top=ids["top"][0], bottom=ids["bottom"][0], shoes=ids["shoes"][0]
        )
        second = wardrobe.set_today(
            "2026-11-11", top=ids["top"][1], bottom=ids["bottom"][1], shoes=""
        )
        self.assertTrue(second["ok"])
        self.assertEqual(second["shoes"], first["shoes"])

    def test_set_today_rejects_wrong_category(self):
        wardrobe, _ = make_wardrobe()
        ids = self._ids(wardrobe)
        result = wardrobe.set_today("2026-11-11", top=ids["bottom"][0], bottom=ids["bottom"][0])
        self.assertFalse(result["ok"])


class GiftTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify_item("白色衬衫"), "top")
        self.assertEqual(classify_item("黑色包臀裙"), "skirt")
        self.assertEqual(classify_item("黑色丝质长裙"), "skirt")
        self.assertEqual(classify_item("淡蓝色运动鞋"), "shoes")
        self.assertEqual(classify_item("围巾"), "")

    def test_gift_item_enters_wardrobe(self):
        wardrobe, _ = make_wardrobe()
        result = wardrobe.add_from_gift("一件米色开衫", "g1")
        self.assertTrue(result and result["ok"])
        self.assertEqual(result["category"], "top")
        names = [item["name"] for cat in wardrobe.snapshot()["categories"] for item in cat["items"]]
        self.assertIn("米色开衫", names)


class SidecarTests(unittest.TestCase):
    def test_sidecar_written_for_omnidraw(self):
        wardrobe, root = make_wardrobe()
        os.makedirs(os.path.join(root, "plugin_data", "astrbot_plugin_omnidraw"), exist_ok=True)
        current = wardrobe.current("2026-11-11", at_home=False, hour=14)
        path = os.path.join(root, "plugin_data", "astrbot_plugin_omnidraw", "amo_outfit.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["day"], "2026-11-11")
        self.assertEqual(data["text"], current["text"])
        self.assertEqual(data["outfit"], current["outfit"])

    def test_night_sidecar_is_pajamas(self):
        wardrobe, root = make_wardrobe()
        os.makedirs(os.path.join(root, "plugin_data", "astrbot_plugin_omnidraw"), exist_ok=True)
        current = wardrobe.current("2026-11-11", at_home=True, hour=21)
        self.assertTrue(current.get("sleep"))
        path = os.path.join(root, "plugin_data", "astrbot_plugin_omnidraw", "amo_outfit.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("睡衣", data["text"])

    def test_no_sidecar_without_omnidraw_dir(self):
        wardrobe, root = make_wardrobe()
        wardrobe.current("2026-11-11", at_home=False, hour=14)
        self.assertFalse(os.path.exists(
            os.path.join(root, "plugin_data", "astrbot_plugin_omnidraw", "amo_outfit.json")
        ))


class SleepTests(unittest.TestCase):
    def test_day_candidates_never_pick_sleepwear(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe._load()
        day_ids = {outfit["id"] for outfit in data["outfits"] if not outfit.get("sleep")}
        for day in ("2026-07-01", "2026-07-02", "2026-07-03", "2026-11-11"):
            self.assertIn(wardrobe.today(day)["outfit"], day_ids)

    def test_current_evening_at_home_is_sleepwear(self):
        wardrobe, _ = make_wardrobe()
        current = wardrobe.current("2026-11-11", at_home=True, hour=20)
        self.assertTrue(current.get("sleep"))
        self.assertIn("睡衣", current["text"])

    def test_current_evening_outside_keeps_day_outfit(self):
        wardrobe, _ = make_wardrobe()
        current = wardrobe.current("2026-11-11", at_home=False, hour=20)
        self.assertFalse(current.get("sleep"))

    def test_current_daytime_at_home_keeps_day_outfit(self):
        wardrobe, _ = make_wardrobe()
        current = wardrobe.current("2026-11-11", at_home=True, hour=14)
        self.assertFalse(current.get("sleep"))

    def test_sleepwear_hidden_from_panel_lists(self):
        wardrobe, _ = make_wardrobe()
        names = [item["name"] for cat in wardrobe.snapshot()["categories"] for item in cat["items"]]
        self.assertFalse(any("睡衣" in name or "拖鞋" in name for name in names))

    def test_old_wardrobe_file_gets_sleepwear_added(self):
        wardrobe, root = make_wardrobe()
        with wardrobe._lock:
            data = wardrobe._load()
            data["outfits"] = [outfit for outfit in data["outfits"] if not outfit.get("sleep")]
            data["items"] = [item for item in data["items"] if not item.get("sleep")]
            data.pop("sleep_seed", None)
            wardrobe._save(data)
            wardrobe._data = None
        again = Wardrobe(FakePlugin(root))
        self.assertTrue(any(outfit.get("sleep") for outfit in again._load()["outfits"]))

    def test_twenty_sleep_templates(self):
        wardrobe, _ = make_wardrobe()
        sleep = [outfit for outfit in wardrobe._load()["outfits"] if outfit.get("sleep")]
        self.assertGreaterEqual(len(sleep), 20)

    def test_sleep_templates_are_many(self):
        wardrobe, _ = make_wardrobe()
        sleep = [outfit for outfit in wardrobe._load()["outfits"] if outfit.get("sleep")]
        self.assertGreaterEqual(len(sleep), 100)

    def test_night_changes_across_days(self):
        wardrobe, _ = make_wardrobe()
        outfits = {wardrobe.night("2026-11-%02d" % day, hour=21)["outfit"] for day in range(1, 6)}
        self.assertGreater(len(outfits), 1)

    def test_deleted_sleep_item_stays_deleted(self):
        wardrobe, root = make_wardrobe()
        with wardrobe._lock:
            data = wardrobe._load()
            item = next(item for item in data["items"] if item.get("sleep"))
        wardrobe.delete_item(item["id"])
        again = Wardrobe(FakePlugin(root))
        ids = [item["id"] for item in again._load()["items"]]
        self.assertNotIn(item["id"], ids)

    def test_night_available_before_8pm(self):
        wardrobe, _ = make_wardrobe()
        night = wardrobe.night("2026-11-11", hour=19)
        self.assertFalse(night.get("pending"))
        self.assertTrue(night.get("sleep"))
        self.assertIn("睡衣", night["text"])

    def test_after_8pm_night_is_locked_and_stable(self):
        wardrobe, _ = make_wardrobe()
        first = wardrobe.night("2026-11-11", hour=20)
        self.assertTrue(first.get("sleep"))
        again = wardrobe.night("2026-11-11", hour=21)
        self.assertEqual(first["outfit"], again["outfit"])

    def test_reroll_night_changes_outfit(self):
        wardrobe, _ = make_wardrobe()
        first = wardrobe.set_night("2026-11-11")
        second = wardrobe.set_night("2026-11-11", reroll=True)
        self.assertNotEqual(first["outfit"], second["outfit"])

    def test_before_8pm_at_home_still_day_outfit(self):
        wardrobe, _ = make_wardrobe()
        current = wardrobe.current("2026-11-11", at_home=True, hour=19)
        self.assertFalse(current.get("sleep"))


class SleepItemTests(unittest.TestCase):
    def test_add_sleep_item_goes_to_night_list(self):
        wardrobe, _ = make_wardrobe()
        result = wardrobe.add_item("top", "白色丝质睡衣上衣", sleep=True)
        self.assertTrue(result["ok"])
        snap = wardrobe.snapshot()
        day_names = [item["name"] for cat in snap["categories"] for item in cat["items"]]
        night_names = [item["name"] for cat in snap["sleep_categories"] for item in cat["items"]]
        self.assertNotIn("白色丝质睡衣上衣", day_names)
        self.assertIn("白色丝质睡衣上衣", night_names)

    def test_gift_pajamas_classified_as_sleep(self):
        wardrobe, _ = make_wardrobe()
        result = wardrobe.add_from_gift("真丝睡衣", "g2")
        self.assertTrue(result and result.get("sleep"))
        self.assertEqual(result["category"], "top")
        night_names = [item["name"] for cat in wardrobe.snapshot()["sleep_categories"]
                       for item in cat["items"]]
        self.assertIn("真丝睡衣", night_names)

    def test_gift_slippers_go_to_sleep_shoes(self):
        wardrobe, _ = make_wardrobe()
        result = wardrobe.add_from_gift("毛绒拖鞋", "g3")
        self.assertEqual(result["category"], "shoes")
        self.assertTrue(result.get("sleep"))

    def test_set_night_with_chosen_items(self):
        wardrobe, _ = make_wardrobe()
        items = {item["name"]: item["id"] for item in wardrobe._load()["items"]}
        result = wardrobe.set_night(
            "2026-11-11",
            top=items["浅粉色棉质睡衣上衣"],
            bottom=items["同色系棉质睡裤"],
            shoes=items["毛绒拖鞋"],
        )
        self.assertEqual(result["top"], "浅粉色棉质睡衣上衣")
        self.assertEqual(result["bottom"], "同色系棉质睡裤")
        self.assertEqual(result["shoes"], "毛绒拖鞋")
        self.assertTrue(result.get("sleep"))


class PajamaChangeTests(unittest.TestCase):
    def test_detects_pajama_change_requests(self):
        from wardrobe import is_pajama_change_request
        for text in ("睡衣不好看", "这套睡衣难看，换一套", "换一套睡衣", "不喜欢这件睡衣", "睡衣太丑了"):
            self.assertTrue(is_pajama_change_request(text), text)

    def test_ignores_other_clothes_and_plain_questions(self):
        from wardrobe import is_pajama_change_request
        for text in ("这件衬衫不好看", "换一套衣服", "睡衣呢", "今天穿什么", "晚安"):
            self.assertFalse(is_pajama_change_request(text), text)


class HotelTests(unittest.TestCase):
    def test_hotel_detection(self):
        wardrobe, _ = make_wardrobe(place="酒店")
        self.assertTrue(wardrobe.at_hotel())
        self.assertFalse(wardrobe.at_home())

    def test_hotel_templates_created(self):
        wardrobe, _ = make_wardrobe()
        hotel = [outfit for outfit in wardrobe._load()["outfits"] if outfit.get("hotel")]
        self.assertGreaterEqual(len(hotel), 8)

    def test_night_at_hotel_is_hotel_pajamas(self):
        wardrobe, _ = make_wardrobe(place="酒店")
        night = wardrobe.night("2026-11-11", hour=22)
        self.assertTrue(night.get("hotel"))
        self.assertTrue(any(word in night["text"] for word in ("酒店", "浴袍", "睡袍")))

    def test_hotel_nights_are_always_hotel(self):
        wardrobe, _ = make_wardrobe(place="酒店")
        for day in range(1, 10):
            night = wardrobe.night("2026-11-%02d" % day, hour=22)
            self.assertTrue(night.get("hotel"), night["text"])

    def test_night_at_home_is_not_hotel(self):
        wardrobe, _ = make_wardrobe(place="家")
        night = wardrobe.night("2026-11-11", hour=22)
        self.assertFalse(night.get("hotel"))

    def test_home_nights_never_hotel(self):
        wardrobe, _ = make_wardrobe(place="家")
        for day in range(1, 10):
            night = wardrobe.night("2026-11-%02d" % day, hour=22)
            self.assertFalse(night.get("hotel"), night["text"])

    def test_home_pajamas_not_used_at_hotel(self):
        wardrobe, _ = make_wardrobe(place="酒店")
        home = next(outfit for outfit in wardrobe._load()["outfits"]
                    if outfit.get("sleep") and not outfit.get("hotel"))
        result = wardrobe.set_night("2026-11-11", outfit_id=home["id"])
        self.assertTrue(result.get("hotel"))
        self.assertNotEqual(result["outfit"], home["id"])

    def test_hotel_pajamas_never_used_at_home(self):
        wardrobe, _ = make_wardrobe(place="家")
        hotel = next(outfit for outfit in wardrobe._load()["outfits"] if outfit.get("hotel"))
        result = wardrobe.set_night("2026-11-11", outfit_id=hotel["id"])
        self.assertFalse(result.get("hotel"))

    def test_daytime_at_hotel_is_day_outfit(self):
        wardrobe, _ = make_wardrobe(place="酒店")
        current = wardrobe.current("2026-11-11", hour=14)
        self.assertFalse(current.get("sleep"))

    def test_hotel_outfits_use_only_hotel_items(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe._load()
        items = {item["id"]: item for item in data["items"]}
        for outfit in data["outfits"]:
            if not outfit.get("hotel"):
                continue
            for key in ("top", "bottom", "shoes"):
                self.assertTrue(items[outfit[key]].get("hotel"), key)

    def test_home_outfits_never_use_hotel_items(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe._load()
        items = {item["id"]: item for item in data["items"]}
        for outfit in data["outfits"]:
            if outfit.get("hotel") or not outfit.get("sleep"):
                continue
            for key in ("top", "bottom", "shoes"):
                self.assertFalse(items[outfit[key]].get("hotel"), key)

    def test_adding_home_sleep_item_does_not_create_hotel_outfits(self):
        wardrobe, _ = make_wardrobe()
        before = len([o for o in wardrobe._load()["outfits"] if o.get("hotel")])
        wardrobe.add_item("top", "测试居家睡衣上衣", sleep=True)
        after = len([o for o in wardrobe._load()["outfits"] if o.get("hotel")])
        self.assertEqual(before, after)

    def test_hotel_items_not_in_home_panel_lists(self):
        wardrobe, _ = make_wardrobe()
        names = [item["name"] for cat in wardrobe.snapshot()["sleep_categories"]
                 for item in cat["items"]]
        self.assertFalse(any("酒店" in name for name in names))

    def test_hotel_categories_visible_in_panel(self):
        wardrobe, _ = make_wardrobe()
        snap = wardrobe.snapshot()
        hotel_items = [item for cat in snap["hotel_categories"] for item in cat["items"]]
        self.assertTrue(hotel_items)
        self.assertEqual(snap["hotel_seed_count"], len(__import__("wardrobe").HOTEL_SEED))

    def test_add_hotel_item_shows_in_hotel_list_only(self):
        wardrobe, _ = make_wardrobe()
        result = wardrobe.add_item("top", "测试酒店睡袍", hotel=True)
        self.assertTrue(result["ok"])
        snap = wardrobe.snapshot()
        hotel_names = [item["name"] for cat in snap["hotel_categories"] for item in cat["items"]]
        sleep_names = [item["name"] for cat in snap["sleep_categories"] for item in cat["items"]]
        day_names = [item["name"] for cat in snap["categories"] for item in cat["items"]]
        self.assertIn("测试酒店睡袍", hotel_names)
        self.assertNotIn("测试酒店睡袍", sleep_names)
        self.assertNotIn("测试酒店睡袍", day_names)


class DressTests(unittest.TestCase):
    def test_dress_gets_single_piece_combos(self):
        wardrobe, _ = make_wardrobe()
        result = wardrobe.add_item("skirt", "黑色丝质长裙")
        self.assertTrue(result["ok"])
        self.assertGreater(result["combos"], 0)
        data = wardrobe._load()
        dress = next(item for item in data["items"] if item["name"] == "黑色丝质长裙")
        dress_outfits = [outfit for outfit in data["outfits"]
                         if outfit.get("skirt") == dress["id"]]
        self.assertTrue(any(not outfit.get("top") and not outfit.get("bottom")
                            for outfit in dress_outfits))

    def test_save_dress_only_outfit(self):
        wardrobe, _ = make_wardrobe()
        dress = wardrobe.add_item("skirt", "黑色丝质长裙")["item"]
        result = wardrobe.set_today("2026-11-11", top="", skirt=dress["id"])
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("skirt"), "黑色丝质长裙")
        self.assertEqual(result.get("top"), "")
        self.assertEqual(result.get("bottom"), "")

    def test_handmade_count_changes_after_delete(self):
        wardrobe, _ = make_wardrobe()
        before = wardrobe.snapshot()
        seed_top = "米色雪纺衬衫"
        item = next(entry for entry in wardrobe._load()["items"]
                    if entry["name"] == seed_top and entry["category"] == "top"
                    and not entry.get("sleep"))
        wardrobe.delete_item(item["id"])
        after = wardrobe.snapshot()
        self.assertLess(after["day_seed_count"], before["day_seed_count"])

    def test_delete_refills_combos(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe._load()
        combo = next(outfit for outfit in data["outfits"]
                     if outfit.get("combo") and not outfit.get("sleep"))
        item = wardrobe._load()["items"][0]
        before = wardrobe.snapshot()["outfit_count"]
        wardrobe.delete_item(item["id"])
        after = wardrobe.snapshot()["outfit_count"]
        self.assertGreater(after, 0)


class WorkOutfitTests(unittest.TestCase):
    def test_work_scene_excludes_swimwear(self):
        wardrobe, _ = make_wardrobe(place="写字楼")
        wardrobe.add_item("top", "测试泳衣")
        data = wardrobe._load()
        items = {item["id"]: item for item in data["items"]}
        names = []
        for outfit in wardrobe._candidates(data):
            names.extend(str(items.get(outfit.get(key), {}).get("name") or "")
                         for key in ("coat", "top", "bottom", "skirt", "shoes"))
        self.assertTrue(names)
        self.assertFalse(any("泳衣" in name for name in names))


class HomeEarlyTests(unittest.TestCase):
    def test_home_early_switches_to_pajamas(self):
        wardrobe, _ = make_wardrobe(place="家")
        wardrobe.plugin.db.kv_set("home_early:2026-11-11", True)
        current = wardrobe.current("2026-11-11", hour=15)
        self.assertTrue(current.get("sleep"))

    def test_without_home_early_keeps_day_outfit(self):
        wardrobe, _ = make_wardrobe(place="家")
        current = wardrobe.current("2026-11-11", hour=15)
        self.assertFalse(current.get("sleep"))


class WeatherTests(unittest.TestCase):
    def _names(self, wardrobe):
        data = wardrobe._load()
        items = {item["id"]: item for item in data["items"]}
        outfits = wardrobe._candidates(data)
        return ["".join(str(items.get(outfit.get(key), {}).get("name") or "")
                        for key in ("coat", "top", "bottom", "skirt", "shoes"))
                for outfit in outfits]

    def test_summer_excludes_winter_coats(self):
        wardrobe, _ = make_wardrobe(temp=30)
        names = self._names(wardrobe)
        self.assertTrue(names)
        self.assertFalse(any("羽绒服" in name or "大衣" in name or "毛呢" in name
                             for name in names))

    def test_winter_excludes_summer_items(self):
        wardrobe, _ = make_wardrobe(temp=5)
        names = self._names(wardrobe)
        self.assertTrue(names)
        self.assertFalse(any(word in name for name in names
                             for word in ("短袖", "短裤", "短裙")))

    def test_very_cold_prefers_down_jacket(self):
        wardrobe, _ = make_wardrobe(temp=0)
        names = self._names(wardrobe)
        self.assertTrue(names)
        self.assertTrue(any("羽绒服" in name for name in names))


class ChangeRequestTests(unittest.TestCase):
    def test_outfit_change_requests(self):
        from wardrobe import is_outfit_change_request
        for text in ("换一套衣服", "这件不好看，换一套", "换装", "这套穿搭不喜欢", "睡衣不好看"):
            self.assertTrue(is_outfit_change_request(text), text)

    def test_ignores_other_change_talks(self):
        from wardrobe import is_outfit_change_request
        for text in ("换手机", "换个话题", "晚安", "今天穿什么", "换乘地铁"):
            self.assertFalse(is_outfit_change_request(text), text)


class AtHomeTests(unittest.TestCase):
    def test_at_home(self):
        wardrobe, _ = make_wardrobe(place="家里")
        self.assertTrue(wardrobe.at_home())

    def test_outside(self):
        wardrobe, _ = make_wardrobe(place="写字楼")
        self.assertFalse(wardrobe.at_home())

    def test_balcony_counts_as_home(self):
        wardrobe, _ = make_wardrobe(place="阳台")
        self.assertTrue(wardrobe.at_home())

    def test_unknown_place(self):
        wardrobe, _ = make_wardrobe(place="某个地方")
        self.assertIsNone(wardrobe.at_home())

    def test_night_on_balcony_wears_pajamas(self):
        wardrobe, _ = make_wardrobe(place="阳台")
        current = wardrobe.current("2026-11-11", hour=22)
        self.assertTrue(current.get("sleep"))

    def test_daytime_at_home_keeps_day_outfit(self):
        wardrobe, _ = make_wardrobe(place="阳台")
        current = wardrobe.current("2026-11-11", hour=14)
        self.assertFalse(current.get("sleep"))


class ChatContextTests(unittest.TestCase):
    def test_context_line_is_short_but_knows_outfit(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "amo_runtime.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("【穿搭】", source)
        self.assertIn('_outfit.get("top")', source)
        self.assertIn('_outfit.get("bottom")', source)
        self.assertIn("被问起穿搭就照这个说", source)

    def test_chat_can_reroll_pajamas(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "amo_runtime.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("is_outfit_change_request", source)
        self.assertIn("is_pajama_change_request", source)
        self.assertIn("set_night(reroll=True)", source)
        self.assertIn("at_home is False", source)


if __name__ == "__main__":
    unittest.main()
