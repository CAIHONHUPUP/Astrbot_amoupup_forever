# -*- coding: utf-8 -*-
"""飞书 2026-11-23 23:00 事故回归（第一刀）。

覆盖：
- resolve_zone：楼下小馆/粉店/面馆/馆子 + meal → restaurant；具体地点绝不 road；
- guard_road_home：旧脏行 place=楼下小馆 zone=road 不再说“还在车上”，真地铁仍拦；
- _strip_line：未闭合「（」保留后面的台词；闭合括号行为不变；
- segment_engine 兜底：晚安→抱抱，睡吧。；在吗/干嘛→我在呢。；其它不硬发。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_place as place  # noqa: E402
import amo_persona_guard as guard  # noqa: E402
import amo_transition as tr  # noqa: E402
import segment_engine as seg  # noqa: E402


class ResolveZoneTests(unittest.TestCase):
    def test_restaurant_place_never_road(self):
        for text in ("楼下小馆", "楼下粉店", "老张面馆", "街角馆子"):
            self.assertEqual(place.resolve_zone(text, "meal", "road"), "restaurant", text)

    def test_transit_place_is_road(self):
        for text in ("地铁", "公交", "车上", "路上"):
            self.assertEqual(place.resolve_zone(text, "commute", ""), "road", text)

    def test_category_commute_is_road_only_for_unknown_place(self):
        self.assertEqual(place.resolve_zone("某地", "commute", ""), "road")
        # 具体目的地仍按具体地点算，不会被通勤类别盖成 road
        self.assertEqual(place.resolve_zone("公司", "commute", ""), "office")
        self.assertEqual(place.resolve_zone("医院", "commute", ""), "hospital")

    def test_concrete_place_never_falls_back_to_road(self):
        self.assertEqual(place.resolve_zone("幸福路", "life", "road"), "street")
        self.assertEqual(place.resolve_zone("某展览馆", "life", "road"), "street")
        self.assertNotEqual(place.resolve_zone("某展览馆", "life", "road"), "road")

    def test_repair_zone_rows_only_today_and_future(self):
        rows = [{"start": "23:00", "end": "23:05", "place": "楼下小馆",
                 "zone": "road", "category": "meal", "activity": "到了楼下小馆"}]
        fixed, changes = place.repair_zone_rows(rows, "2026-11-23", today="2026-11-23")
        self.assertEqual(fixed[0]["zone"], "restaurant")
        self.assertEqual(len(changes), 1)
        history, history_changes = place.repair_zone_rows(
            rows, "2026-11-22", today="2026-11-23")
        self.assertEqual(history[0]["zone"], "road")
        self.assertEqual(history_changes, [])


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, activity):
        self.activity = activity

    def current_activity(self):
        return dict(self.activity)


class FakePlugin:
    def __init__(self, activity):
        self.db = FakeDB()
        self.state = FakeState(activity)


class RoadGuardTests(unittest.TestCase):
    def test_dirty_restaurant_row_does_not_trigger_road_guard(self):
        plugin = FakePlugin({"activity": "到了楼下小馆", "place": "楼下小馆",
                             "zone": "road", "category": "meal"})
        self.assertEqual(tr.guard_road_home(plugin, "我到家了"), "我到家了")

    def test_real_road_still_triggers(self):
        plugin = FakePlugin({"activity": "在地铁上", "place": "地铁",
                             "zone": "road", "category": "commute"})
        self.assertIn("还在车上", tr.guard_road_home(plugin, "我到家了"))

    def test_road_place_but_restaurant_category_is_not_road(self):
        plugin = FakePlugin({"activity": "在小馆", "place": "楼下小馆",
                             "zone": "restaurant", "category": "meal"})
        self.assertEqual(tr.guard_road_home(plugin, "我到家了"), "我到家了")


class UnclosedParenTests(unittest.TestCase):
    def test_unclosed_paren_keeps_night_line(self):
        raw = "（刚把盒子推远一点，好饱。晚安，明天还得早起呢。"
        self.assertEqual(guard.strip_action_narration(raw), "晚安，明天还得早起呢。")

    def test_unclosed_paren_pure_action_still_dropped(self):
        self.assertEqual(guard.strip_action_narration("（听到这句话"), "")

    def test_closed_paren_behavior_unchanged(self):
        self.assertEqual(guard.strip_action_narration("（笑）晚安"), "晚安")
        self.assertEqual(guard.strip_action_narration("（把盒子推远）晚安"), "晚安")

    def test_action_only_contract_unchanged(self):
        self.assertTrue(guard.action_only("（靠在椅背上，把手机举得离眼睛近了点）"))
        self.assertFalse(guard.action_only("晚安"))


class ContextualFallbackTests(unittest.TestCase):
    def test_goodnight(self):
        self.assertEqual(seg.contextual_fallback("晚安，老婆"), "抱抱，睡吧。")
        self.assertNotIn("晚安", seg.contextual_fallback("晚安，老婆"))

    def test_asking_if_there(self):
        self.assertEqual(seg.contextual_fallback("在吗"), "我在呢。")
        self.assertEqual(seg.contextual_fallback("你干嘛呢"), "我在呢。")

    def test_other_does_not_force_a_line(self):
        self.assertEqual(seg.contextual_fallback("今天吃什么"), "")


class TransitionFakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class TransitionFakeState:
    def __init__(self):
        self.schedule = {}
        self.activity = {"activity": "在被子里翻身", "place": "家", "zone": "home",
                         "category": "rest"}
        self.mood = {"郁闷": 10, "委屈": 10, "开心": 50, "平静": 50}
        self.commits = []

    def current_activity(self):
        return dict(self.activity)

    def commit_schedule_change(self, day, items, old_items=None, source="", reason=""):
        self.schedule[day] = [dict(row) for row in items]
        self.commits.append([dict(row) for row in items])
        return {"changed": True, "saved": True}


class TransitionFakePlugin:
    def __init__(self):
        self.db = TransitionFakeDB()
        self.state = TransitionFakeState()

    def cfg_int(self, key, default=0):
        return default

    def _data_bot_id(self):
        return "amo"

    async def _data_call(self, *args, **kwargs):
        return None

    def _spawn(self, coro):
        try:
            coro.close()
        except Exception:
            pass
        return None


class ZoneUnificationTests(unittest.TestCase):
    def test_zone_for_place_uses_resolve_zone(self):
        import photo_rules
        self.assertEqual(place.zone_for_place("楼下小馆"), "restaurant")
        self.assertEqual(photo_rules.zone_for_place("楼下小馆"), "restaurant")
        self.assertEqual(place.zone_for_place("地铁"), "road")

    def test_photo_scene_reads_dirty_row_as_restaurant(self):
        import photo_scene
        dirty = {"place": "楼下小馆", "zone": "road", "category": "meal"}
        self.assertEqual(photo_scene.zone_of(dirty), "restaurant")
        self.assertEqual(photo_scene.zone_of({"place": "地铁", "zone": "road",
                                              "category": "commute"}), "road")

    def test_normalize_activity_repairs_road_for_concrete_place(self):
        import photo_plan
        data, fixes = photo_plan.normalize_activity(
            {"place": "楼下小馆", "zone": "road", "category": "meal", "activity": "吃饭"})
        self.assertEqual(data["zone"], "restaurant")
        self.assertEqual(fixes, [("road", "restaurant")])
        nearby, nearby_fixes = photo_plan.normalize_activity(
            {"place": "公司附近", "zone": "restaurant", "category": "meal",
             "activity": "吃午饭"})
        self.assertEqual(nearby["zone"], "restaurant")
        self.assertEqual(nearby_fixes, [])

    def test_schedule_change_syncs_restaurant_zone(self):
        import schedule_change
        item = {"place": "楼下小馆", "zone": "road", "category": "meal",
                "activity": "到了楼下小馆"}
        fixed = schedule_change._sync_zone(item, "楼下小馆", "meal")
        self.assertEqual(fixed["zone"], "restaurant")
        self.assertEqual(fixed["transit"], "")

    def test_finish_arrival_at_restaurant_is_not_road(self):
        plugin = TransitionFakePlugin()
        now = __import__("datetime").datetime(2026, 11, 23, 23, 0, 0)
        start = now - __import__("datetime").timedelta(minutes=20)
        tr.start(plugin, "commute", "楼下小馆", category="meal", minutes=20, now=start)
        tr.finish(plugin, now=now)
        rows = plugin.state.schedule[now.strftime("%Y-%m-%d")]
        arrival = [row for row in rows if str(row.get("step") or "") == "arrive"]
        self.assertEqual(len(arrival), 1, rows)
        self.assertEqual(arrival[0]["zone"], "restaurant")
        self.assertEqual(arrival[0]["place"], "楼下小馆")


class TransitionDedupeTests(unittest.TestCase):
    def _plugin(self):
        return TransitionFakePlugin()

    def test_same_to_kind_in_progress_is_ignored(self):
        plugin = self._plugin()
        now = __import__("datetime").datetime(2026, 11, 23, 18, 0, 0)
        self.assertIsNotNone(tr.start(plugin, "commute", "楼下小馆", category="meal",
                                      minutes=20, now=now))
        self.assertIsNone(tr.start(plugin, "commute", "楼下小馆", category="meal",
                                   minutes=20, now=now + __import__("datetime").timedelta(minutes=5)))
        self.assertEqual([], (plugin.db.kv_get("transition", {}) or {}).get("queued") or [])

    def test_recent_finished_same_to_kind_is_ignored(self):
        plugin = self._plugin()
        now = __import__("datetime").datetime(2026, 11, 23, 18, 0, 0)
        start = now - __import__("datetime").timedelta(minutes=20)
        tr.start(plugin, "commute", "楼下小馆", category="meal", minutes=20, now=start)
        tr.finish(plugin, now=now)
        self.assertIsNone(tr.start(plugin, "commute", "楼下小馆", category="meal",
                                   minutes=20, now=now + __import__("datetime").timedelta(minutes=5)))

    def test_finish_skips_arrival_after_leaving_step(self):
        plugin = self._plugin()
        now = __import__("datetime").datetime(2026, 11, 23, 23, 0, 0)
        start = now - __import__("datetime").timedelta(minutes=57)
        data = {
            "kind": "meal", "to": "楼下小馆", "category": "meal",
            "started_at": start.timestamp(),
            "steps": tr.steps_for("meal", "楼下小馆"),
            "from": "家", "activity": "去吃饭",
        }
        stage = tr.stage_at(data, now)
        self.assertTrue(stage.get("arrived"))
        fact = tr.finish(plugin, data, stage, now)
        self.assertIn("出来", fact)
        self.assertEqual([], plugin.state.commits)
        self.assertEqual({}, plugin.db.kv_get("transition", {}))
        self.assertEqual("meal", (plugin.db.kv_get("transit:last", {}) or {}).get("kind"))


class FoodContextTests(unittest.TestCase):
    def test_pending_line_never_says_restaurant(self):
        import amo_food_delivery as fd
        line = fd.pending_context_line("炒米粉")
        self.assertIn("外卖", line)
        self.assertIn("还没到", line)
        for word in ("小馆", "餐厅", "饭馆", "餐馆"):
            self.assertNotIn(word, line)

    def test_pending_lines_filter_restaurant_rows(self):
        import amo_food_delivery as fd
        lines = ["【绝对事实】19:00 到了楼下小馆（楼下小馆）", "【今天的账单】20 元"]
        out = fd.pending_context_lines(lines, "炒米粉")
        self.assertFalse(any("小馆" in line for line in out))
        self.assertIn("炒米粉", out[0])
        self.assertIn("20 元", "".join(out))


class PropGroundingTests(unittest.TestCase):
    def _plugin(self):
        return TransitionFakePlugin()

    def test_unsupported_box_and_road_are_dropped(self):
        plugin = self._plugin()
        with mock.patch(
                "amo_current_action.get_current_action",
                return_value={"action": "到了楼下小馆", "place": "楼下小馆",
                              "zone": "restaurant", "category": "meal"}):
            self.assertEqual(guard.ground_props(plugin, "我还在车上，快到了"), "快到了")
            self.assertEqual(guard.ground_props(plugin, "我把盒子推远一点，靠着看手机"),
                             "靠着看手机")

    def test_supported_props_are_kept(self):
        plugin = self._plugin()
        with mock.patch(
                "amo_current_action.get_current_action",
                return_value={"action": "在地铁上", "place": "地铁",
                              "zone": "road", "category": "commute"}), \
             mock.patch(
                "amo_fact_binder.meal_facts",
                return_value={"takeout": True, "takeout_rows": [{"food": "炒米粉"}]}):
            self.assertEqual(guard.ground_props(plugin, "我还在车上，快到了"),
                             "我还在车上，快到了")
            self.assertEqual(guard.ground_props(plugin, "我把盒子推远一点，靠着看手机"),
                             "我把盒子推远一点，靠着看手机")


if __name__ == "__main__":
    unittest.main()
