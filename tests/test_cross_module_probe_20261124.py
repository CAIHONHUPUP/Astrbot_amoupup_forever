# -*- coding: utf-8 -*-
"""跨模块矛盾探针（2026-11-24）。

固定场景把“日程 → current_action → 照片 → 地点守卫 → 日程视图 → 食物 → 日记 → 过渡”
串起来，断言它们说的是同一个事实；任何一层再分叉，这里先红。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_current_action as ca  # noqa: E402
import amo_diary_guard as diary  # noqa: E402
import amo_fact_binder as binder  # noqa: E402
import amo_food_cost as food  # noqa: E402
import amo_food_delivery as delivery  # noqa: E402
import amo_transition as tr  # noqa: E402
import photo_plan  # noqa: E402
import photo_scene  # noqa: E402
import place_guard  # noqa: E402
import schedule_content  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, activity=None):
        self.activity = activity or {"activity": "在家休息", "place": "家",
                                     "zone": "home", "category": "rest"}
        self.schedule = {}
        self.mood = {}
        self.commits = []

    def current_activity(self):
        return dict(self.activity)

    def commit_schedule_change(self, day, items, old_items=None, source="", reason=""):
        self.schedule[day] = [dict(row) for row in items]
        self.commits.append(items)
        return {"changed": True, "saved": True}


class FakePlugin:
    def __init__(self, activity=None):
        self.db = FakeDB()
        self.state = FakeState(activity)
        self.meals = []
        self.actions = []

    def mark_meal(self, food, **kwargs):
        self.meals.append(food)

    def set_current_action(self, action, **kwargs):
        self.actions.append(action)

    def _spawn(self, coro):
        try:
            coro.close()
        except Exception:
            pass
        return None

    async def _data_call(self, *args, **kwargs):
        return None


def restaurant_row():
    return {"start": "23:00", "end": "23:05", "activity": "到了楼下小馆",
            "place": "楼下小馆", "zone": "road", "category": "meal"}


class RestaurantConsistencyTests(unittest.TestCase):
    def test_all_modules_say_restaurant(self):
        row = restaurant_row()
        now = dt.datetime(2026, 11, 23, 23, 0)
        info = dict(row, action=row["activity"], until=0)
        with mock.patch("amo_current_action._micro_action", return_value=None), \
             mock.patch("amo_current_action._jit_action", return_value=info), \
             mock.patch("amo_current_action._schedule_action", return_value={}):
            current = ca.get_current_action(FakePlugin(), now=now)
        plan = photo_plan.resolve_plan(row, mode="photo")
        zones = {
            "current_action": current["zone"],
            "photo_scene": photo_scene.zone_of(row),
            "photo_plan": plan.zone,
            "place_guard": "restaurant" if "restaurant" in (place_guard.allowed_groups(row) or ()) else "",
            "schedule_content": schedule_content._zone(row),
            "food_cost": food._place_zone_of(row)[1],
            "diary_guard": diary._zone_for_place(row["place"]),
        }
        for name, zone in zones.items():
            self.assertEqual("restaurant", zone, name)

    def test_place_guard_sees_conflict_with_mall(self):
        row = restaurant_row()
        self.assertIsNotNone(place_guard.conflicting_place("你在商场吧？", row))


class RealRoadConsistencyTests(unittest.TestCase):
    def test_all_modules_say_road(self):
        row = {"activity": "在地铁上", "place": "地铁", "zone": "road",
               "category": "commute"}
        now = dt.datetime(2026, 11, 23, 18, 0)
        info = dict(row, action=row["activity"], until=0)
        with mock.patch("amo_current_action._micro_action", return_value=None), \
             mock.patch("amo_current_action._jit_action", return_value=info), \
             mock.patch("amo_current_action._schedule_action", return_value={}):
            current = ca.get_current_action(FakePlugin(), now=now)
        plan = photo_plan.resolve_plan(row, mode="photo")
        self.assertEqual("road", current["zone"])
        self.assertEqual("road", photo_scene.zone_of(row))
        self.assertTrue(plan.zone.startswith("road"), plan.zone)
        self.assertIn("transit", place_guard.allowed_groups(row))


class PendingFoodConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin()
        self.now = dt.datetime(2026, 11, 23, 19, 30).timestamp()
        delivery.place_order(self.plugin, "umo-1", "炒米粉", "送炒米粉", now=self.now)

    def test_pending_never_writes_schedule(self):
        self.assertEqual({}, self.plugin.state.schedule)
        delivery.consume_arrival(self.plugin, "umo-1", "到了", now=self.now)
        self.assertEqual({}, self.plugin.state.schedule)

    def test_context_drops_restaurant_line(self):
        state = delivery.pending_state(self.plugin, now=self.now)
        self.assertTrue(state)
        lines = delivery.pending_context_lines(
            ["【绝对事实】19:00 到了楼下小馆（楼下小馆）"], state["food"])
        self.assertFalse(any("小馆" in line for line in lines))
        self.assertIn("外卖", lines[0])
        self.assertIn("还没到", lines[0])

    def test_props_stay_grounded_while_waiting(self):
        with mock.patch("amo_current_action.get_current_action",
                        return_value={"action": "到了楼下小馆", "place": "楼下小馆",
                                      "zone": "restaurant", "category": "meal"}), \
             mock.patch("amo_fact_binder.meal_facts", return_value={}):
            self.assertEqual("靠着看手机",
                             binder.ground_props(self.plugin, "我把盒子推远一点，靠着看手机"))


class TransitionLoopProbeTests(unittest.TestCase):
    def test_commute_arrival_at_restaurant_is_restaurant(self):
        plugin = FakePlugin()
        now = dt.datetime(2026, 11, 23, 23, 0)
        tr.start(plugin, "commute", "楼下小馆", category="meal",
                 minutes=20, now=now - dt.timedelta(minutes=20))
        tr.finish(plugin, now=now)
        rows = plugin.state.schedule[now.strftime("%Y-%m-%d")]
        arrival = [row for row in rows if row.get("step") == "arrive"]
        self.assertEqual(1, len(arrival))
        self.assertEqual("restaurant", arrival[0]["zone"])

    def test_meal_leaving_does_not_rearrive_same_place(self):
        plugin = FakePlugin()
        now = dt.datetime(2026, 11, 23, 23, 0)
        data = {"kind": "meal", "to": "楼下小馆", "category": "meal",
                "started_at": (now - dt.timedelta(minutes=57)).timestamp(),
                "steps": tr.steps_for("meal", "楼下小馆"), "from": "家"}
        stage = tr.stage_at(data, now)
        fact = tr.finish(plugin, data, stage, now)
        self.assertIn("出来", fact)
        self.assertEqual([], plugin.state.commits)
        self.assertEqual({}, plugin.db.kv_get("transition", {}))


class TrueChainProbeTests(unittest.TestCase):
    def test_day_view_current_event_is_restaurant(self):
        import amo_fact_timeline as timeline
        plugin = FakePlugin()
        plugin.state.schedule["2026-11-23"] = [restaurant_row()]
        now = dt.datetime(2026, 11, 23, 23, 0)
        view = timeline.build_day_view(plugin, "2026-11-23", now=now)
        current = timeline.current_view_event(view, now)
        self.assertIsNotNone(current)
        self.assertEqual("楼下小馆", current.get("place"))
        self.assertIn("堂食", str(current.get("occasion") or ""))
        self.assertEqual("restaurant", photo_scene.zone_of(current))

    def test_day_view_tomorrow_has_no_yesterday_restaurant(self):
        import amo_fact_timeline as timeline
        plugin = FakePlugin()
        plugin.state.schedule["2026-11-23"] = [restaurant_row()]
        view = timeline.build_day_view(plugin, "2026-11-24",
                                       now=dt.datetime(2026, 11, 24, 0, 30))
        text = "".join(str(event.get("place") or "") + str(event.get("activity") or "")
                       for event in view.get("events") or ())
        self.assertNotIn("楼下小馆", text)

    def test_photo_final_prompt_is_restaurant_not_road(self):
        from photo_prompt import PhotoPromptMixin

        class Stub:
            plugin = None

            def _cfg_str(self, *args, **kwargs):
                return "手机随手拍，真实自然，生活感，"

            def outfit_today(self):
                return "米色衬衫"

        prompt = PhotoPromptMixin._prompt_for(Stub(), "", "", restaurant_row())
        self.assertIn("楼下小馆", prompt)
        self.assertIn("餐桌", prompt)
        self.assertNotIn("地铁/公交/车厢", prompt.split("不要", 1)[0])

    def test_proactive_warning_line_uses_restaurant(self):
        import amo_fact_binder as binder
        plugin = FakePlugin()
        with mock.patch("amo_current_action.get_current_action",
                        return_value={"action": "到了楼下小馆", "place": "楼下小馆",
                                      "zone": "restaurant", "category": "meal"}):
            line = binder.warning_line(plugin, now=dt.datetime(2026, 11, 23, 23, 0))
        self.assertIn("餐厅", line)
        self.assertIn("到了楼下小馆", line)
        self.assertNotIn("【路上】", line)


    def test_diary_view_blocks_are_restaurant(self):
        import amo_diary_guard as diary
        plugin = FakePlugin()
        plugin.state.schedule["2026-11-23"] = [restaurant_row()]
        blocks = diary._view_blocks(plugin, "2026-11-23")
        self.assertEqual(1, len(blocks))
        self.assertEqual("restaurant", blocks[0]["zone"])
        self.assertEqual("楼下小馆", blocks[0]["place"])

    def test_share_scene_for_kind_is_restaurant(self):
        from photo_share import PhotoShareMixin
        scene = PhotoShareMixin._scene_for_kind("", "楼下小馆", restaurant_row())
        self.assertIn("餐桌", scene)

    def test_chat_final_text_drops_ungrounded_box(self):
        import amo_persona_guard as persona
        plugin = FakePlugin()
        with mock.patch("amo_current_action.get_current_action",
                        return_value={"action": "到了楼下小馆", "place": "楼下小馆",
                                      "zone": "restaurant", "category": "meal"}), \
             mock.patch("amo_fact_binder.meal_facts", return_value={}):
            text, _reason = persona.prepare_outbound(
                plugin, "umo", "（把盒子推远一点）晚安", "chat")
        self.assertNotIn("盒子", text)
        self.assertIn("晚安", text)


class SickLeaveProbeTests(unittest.TestCase):
    """病假日：日程还写着在公司，账本/居住/在场三层必须都落到「家」。

    2026-11-24 P4：scene_now 以前直接读 amo_current_action（照抄日程的「公司」），
    现在先读 amo_scene_ledger.scene_at（病假硬约束把地点钳回 home），三层才可能一致。
    """

    NOW = dt.datetime(2026, 11, 18, 20, 0)   # 星期三 20:00：这个点他不在上班

    def _plugin(self):
        plugin = FakePlugin(activity={"activity": "在工位上忙工作", "place": "公司",
                                      "zone": "office", "category": "work"})
        plugin.db.kv_set("household", {"stage": "together", "since": 0, "night_count": 0})
        plugin.state.on_sick_leave = lambda day=None: True
        return plugin

    def test_ledger_clamps_office_to_home(self):
        import amo_scene_ledger as ledger
        scene = ledger.scene_at(self._plugin(), self.NOW)
        self.assertEqual("家", scene["place"])
        self.assertEqual("home", scene["zone"])
        self.assertEqual(["sick_leave"], scene["constraints"])
        self.assertEqual("公司", (scene.get("clamped_from") or {}).get("place"))

    def test_scene_now_says_home(self):
        import amo_household as hh
        scene = hh.scene_now(self._plugin(), self.NOW)
        self.assertEqual("家", scene.place)
        self.assertEqual("home", scene.zone)
        self.assertTrue(scene.partner)
        self.assertEqual("同居·在家", scene.reason)

    def test_presence_line_says_together_home(self):
        import amo_household as hh
        line = hh.presence_line(self._plugin(), self.NOW)
        self.assertIn("你们住在一起", line)
        self.assertIn("在家", line)


if __name__ == "__main__":
    unittest.main()
