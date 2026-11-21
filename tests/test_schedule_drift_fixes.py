# -*- coding: utf-8 -*-
"""2026-11-22 日程漂移修复回归：R2/R4/R6/R7/R8/R9。

- R2：插通勤块不再把尾部睡眠挤出 24:00（整天骨架顶到 24:00 时从上一块借时间）；
- R4：白天在岗一直连到晚上的 office 块不折家，孤立夜间公司块仍折家；
- R6：通勤块细节不再被 category 词表抹成「在路上赶路」；
- R7：休息日事件（聚餐/加班）优先于 outing/homebody 日类型；
- R8：出差跨天延续——第二天从酒店醒来，最后一天傍晚回家；
- R9：地点归一化后通勤文案用最终地名刷新，且到达类通勤块目的地对齐下一块。
"""
from __future__ import annotations

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_transition as tr  # noqa: E402
from amo_state_schedule import ScheduleStateMixin, health_problems  # noqa: E402
from domains.schedule.rest_segments import business_trip_segments  # noqa: E402
from schedule_engine import ScheduleEngine, parse_hhmm  # noqa: E402

ZONES = {"home", "office", "road", "mall", "scenic", "restaurant", "hotel", "station",
         "playground"}


class _Base(unittest.TestCase):
    def assert_full_day(self, items):
        self.assertEqual(items[0].get("start"), "00:00")
        self.assertEqual(items[-1].get("end"), "24:00")
        for prev, cur in zip(items, items[1:]):
            self.assertEqual(prev.get("end"), cur.get("start"), (prev, cur))
        for item in items:
            self.assertGreater(parse_hhmm(item.get("end")), parse_hhmm(item.get("start")), item)
            self.assertIn(str(item.get("zone") or ""), ZONES)
            self.assertTrue(str(item.get("activity") or "").strip())


# ---------------------------------------------------------------- R4 夜间公司
class NightOfficeTests(_Base):
    def _office_rows(self):
        return [
            {"start": "09:00", "end": "12:00", "activity": "上班", "place": "公司",
             "zone": "office", "category": "work"},
            {"start": "13:00", "end": "18:00", "activity": "上班", "place": "公司",
             "zone": "office", "category": "work"},
            {"start": "18:00", "end": "19:00", "activity": "临时的事还没完，还在公司",
             "place": "公司", "zone": "office", "category": "work"},
            {"start": "19:00", "end": "20:00", "activity": "点个外卖，边吃边改",
             "place": "公司", "zone": "office", "category": "work"},
            {"start": "20:00", "end": "21:00", "activity": "总算收尾，收拾东西准备走",
             "place": "公司", "zone": "office", "category": "work"},
            {"start": "21:00", "end": "22:00", "activity": "下班回家", "place": "路上",
             "zone": "road", "category": "commute"},
            {"start": "22:00", "end": "24:00", "activity": "睡觉", "place": "家",
             "zone": "home", "category": "sleep"},
        ]

    def test_contiguous_office_shift_survives_night_fold(self):
        rows = ScheduleStateMixin._enforce_zone_activity(
            [dict(row) for row in self._office_rows()], "2026-10-26", "ensure")
        late = [row for row in rows
                if row["zone"] == "office" and int(str(row["start"])[:2]) >= 18]
        self.assertEqual(3, len(late), rows)
        self.assertEqual("临时的事还没完，还在公司", late[0]["activity"])

    def test_isolated_night_office_still_folds_home(self):
        rows = [{"start": "21:18", "end": "22:00", "activity": "在工位上忙工作",
                 "place": "公司", "zone": "office", "category": "work"}]
        out = ScheduleStateMixin._enforce_zone_activity(rows, "2026-10-26", "jit")
        self.assertEqual("home", out[0]["zone"])
        self.assertEqual("家", out[0]["place"])
        self.assertEqual("rest", out[0]["category"])

    def test_health_check_allows_contiguous_night_shift(self):
        problems = health_problems(self._office_rows())
        self.assertFalse([p for p in problems if p.startswith("office_out_of_window:1")],
                         problems)
        lonely = [{"start": "21:18", "end": "22:00", "activity": "在工位上忙工作",
                   "place": "公司", "zone": "office", "category": "work"}]
        self.assertIn("office_out_of_window:21:18", health_problems(lonely))


# ---------------------------------------------------------------- R6 通勤细节
class CommuteDetailTests(_Base):
    def test_transit_detail_is_not_flattened(self):
        rows = [
            {"start": "08:45", "end": "09:00", "activity": "到楼下便利店买了早餐，边吃边等电梯",
             "place": "路上", "zone": "road", "category": "commute"},
            {"start": "09:00", "end": "10:00", "activity": "回消息处理事情", "place": "公司",
             "zone": "office", "category": "work"},
        ]
        out = ScheduleStateMixin._enforce_zone_activity(rows, "2026-11-02", "ensure")
        self.assertNotEqual("在路上赶路", out[0]["activity"])
        self.assertIn("便利店", out[0]["activity"])
        self.assertIn("电梯", out[0]["activity"])

    def test_transit_city_name_containing_home_word_kept(self):
        rows = [{"start": "09:00", "end": "12:00", "activity": "坐高铁去石家庄",
                 "place": "路上", "zone": "road", "category": "commute",
                 "transit": "train"}]
        out = ScheduleStateMixin._enforce_zone_activity(rows, "2026-10-16", "ensure")
        self.assertEqual("坐高铁去石家庄", out[0]["activity"])


# ---------------------------------------------------------------- R2 尾部睡眠
class TailSleepTests(_Base):
    def _whole_day(self):
        return [
            {"start": "00:00", "end": "07:00", "activity": "睡觉", "place": "家",
             "zone": "home", "category": "sleep"},
            {"start": "07:00", "end": "08:00", "activity": "洗漱", "place": "家",
             "zone": "home", "category": "life"},
            {"start": "08:00", "end": "09:00", "activity": "挤地铁去公司", "place": "路上",
             "zone": "road", "category": "commute"},
            {"start": "09:00", "end": "20:00", "activity": "上班", "place": "公司",
             "zone": "office", "category": "work"},
            {"start": "20:00", "end": "24:00", "activity": "睡觉", "place": "家",
             "zone": "home", "category": "sleep"},
        ]

    def test_transit_insert_keeps_tail_sleep(self):
        rows = [dict(row) for row in self._whole_day()]
        out, inserted = tr.insert_transit_blocks("2026-11-02", rows, old_items=[])
        self.assertEqual(1, len(inserted), out)
        self.assert_full_day(out)
        last = out[-1]
        self.assertEqual("24:00", last["end"])
        self.assertEqual("20:00", last["start"], "尾部睡眠不能被通勤块挤走")
        self.assertFalse(last.get("truncated_by"))
        company = [row for row in out if row.get("zone") == "office"][0]
        self.assertTrue(company.get("transit_carved"), "应该从活动块结尾借时间")


# ---------------------------------------------------------------- R7 事件优先
BASE_CTX = {
    "day": "2026-10-25",
    "day_type": {"id": "outing", "label": "外出日"},
    "meta": {"rest": True},
    "home": "家里",
    "workplace": "莞城区的写字楼",
    "car": "小电驴",
    "has_gift_car": False,
    "city": "东莞",
    "scenic": {},
    "event": {"kind": "none"},
    "good_weather": True,
    "world": {"occupation": "普通上班族", "workplace": "莞城区的写字楼"},
    "health": {},
    "period": {},
    "yesterday": {},
    "mood": {},
    "weather": {"temp": 22, "text": "晴"},
    "profile": {},
    "seed": 20261025,
}


class _SkeletonState:
    def __init__(self):
        self.p = types.SimpleNamespace(gift=None)
        self.db = None


def _build(**overrides):
    ctx = dict(BASE_CTX)
    ctx.update(overrides)
    return ScheduleEngine(_SkeletonState()).build_skeleton(ctx)


class EventPrecedenceTests(_Base):
    def test_friend_dinner_beats_outing_day_type(self):
        items = _build(event={"kind": "friend_dinner", "effects": {}})
        self.assert_full_day(items)
        social = [row for row in items if row.get("category") == "social"]
        self.assertTrue(social, items)
        self.assertIn("朋友", social[0]["activity"])

    def test_overtime_event_beats_homebody_day_type(self):
        items = _build(day_type={"id": "homebody", "label": "宅家日"},
                       event={"kind": "overtime", "effects": {}, "task": "处理临时的事"})
        self.assert_full_day(items)
        self.assertTrue([row for row in items if row.get("zone") == "office"], items)


# ---------------------------------------------------------------- R8 出差延续
class TripContinuationTests(_Base):
    EVENT = {
        "kind": "business_trip", "title": "临时外派",
        "province": "河北省", "city": "石家庄",
        "where": "河北省·石家庄", "task": "处理外派的事",
        "effects": {"疲惫": 10, "焦虑": 8}, "soothe": False,
    }

    def _ctx(self, **extra):
        ctx = dict(BASE_CTX)
        ctx.update({"meta": {}, "day_type": {"id": "workday"},
                    "event": dict(self.EVENT, **extra)})
        return ctx

    @staticmethod
    def _seg(start, end, activity, place, zone, category, **extra):
        row = {"start": start, "end": end, "activity": activity, "place": place,
               "zone": zone, "category": category}
        row.update(extra)
        return row

    def _build(self, **extra):
        return business_trip_segments(self._ctx(**extra), self._seg, lambda _ctx, name: name)

    def test_first_day_stays_at_hotel(self):
        items = self._build()
        self.assert_full_day(items)
        self.assertEqual("hotel", items[-1]["zone"])
        self.assertEqual("河北省·石家庄·酒店", items[-1]["place"])

    def test_second_day_starts_at_hotel(self):
        items = self._build(continued=True, day_index=1, days=3, return_home=False)
        self.assert_full_day(items)
        self.assertEqual("hotel", items[0]["zone"], "第二天必须从酒店醒来，不能回家")
        self.assertIn("酒店", items[0]["place"])

    def test_final_day_returns_home(self):
        items = self._build(continued=True, day_index=1, days=2, return_home=True)
        self.assert_full_day(items)
        self.assertEqual("hotel", items[0]["zone"], "最后一天上午还在酒店")
        self.assertEqual("home", items[-1]["zone"], "最后一天晚上要睡回家")
        self.assertTrue(any(row.get("place") == self._ctx()["home"] for row in items))

    def test_foreign_hotel_is_not_home_place(self):
        hotel = {"start": "20:00", "end": "21:00", "activity": "回酒店整理资料",
                 "place": "河北省·石家庄·酒店", "zone": "hotel", "category": "work"}
        meal = {"start": "19:00", "end": "20:00", "activity": "酒店附近吃晚饭",
                "place": "河北省·石家庄·小吃街", "zone": "restaurant", "category": "meal"}
        self.assertFalse(tr._is_home_place(hotel["place"]))
        self.assertTrue(tr._needs_transit_block(meal, hotel))


class _TripPlugin:
    def __init__(self):
        self.gift = None

    def cfg_float(self, key, default=0.0):
        if "event_prob" in str(key):
            return 0.9
        return float(default)


class _TripDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return []


class _TripState:
    def __init__(self, schedule, ctx_map=None):
        self.p = _TripPlugin()
        self.db = _TripDB()
        self.schedule = schedule
        self._ctx_map = ctx_map or {}

    def schedule_context(self, day):
        return self._ctx_map.get(day) or {}


def _hotel_day(where="河北省·石家庄", office_place="河北省·石家庄·客户公司"):
    return [
        {"start": "14:00", "end": "18:00", "activity": "在客户公司处理紧急项目",
         "place": office_place, "zone": "office", "category": "work"},
        {"start": "21:00", "end": "23:00", "activity": "在酒店休息",
         "place": where + "·酒店", "zone": "hotel", "category": "rest"},
        {"start": "23:00", "end": "24:00", "activity": "睡觉",
         "place": where + "·酒店", "zone": "hotel", "category": "sleep"},
    ]


class TripStateContinuationTests(unittest.TestCase):
    def _engine(self, state):
        return ScheduleEngine(state)

    def test_event_continues_after_hotel_night(self):
        state = _TripState({"2026-10-08": _hotel_day()})
        event = self._engine(state)._pick_event(
            "2026-10-09", {"rest": False}, {}, {}, {}, 7,
            {"occupation": "项目经理", "workplace": "客户公司"})
        self.assertIsNotNone(event)
        self.assertEqual("business_trip", event["kind"])
        self.assertTrue(event.get("continued"))
        self.assertEqual("河北省·石家庄", event["where"])

    def test_plan_controls_hotel_days_and_return_day(self):
        state = _TripState({"2026-10-08": _hotel_day()})
        state.db.kv["business_trip_plan"] = {
            "start_day": "2026-10-08", "days": 3, "where": "河北省·石家庄",
            "city": "石家庄", "province": "河北省",
        }
        state.schedule["2026-10-09"] = _hotel_day()
        engine = self._engine(state)
        world = {"occupation": "项目经理", "workplace": "客户公司"}
        second = engine._pick_event("2026-10-09", {"rest": False}, {}, {}, {}, 7, world)
        third = engine._pick_event("2026-10-10", {"rest": True}, {}, {}, {}, 7, world)
        self.assertFalse(second["return_home"], "3 天出差第 2 天还要住酒店")
        self.assertTrue(third["return_home"], "最后一天返程")

    def test_fresh_trip_remembers_plan(self):
        state = _TripState({})
        engine = self._engine(state)
        world = {"occupation": "项目经理", "workplace": "客户公司"}
        events = [engine._pick_event("2026-10-08", {"rest": False}, {}, {}, {}, seed, world)
                  for seed in range(80)]
        trips = [event for event in events
                 if isinstance(event, dict) and event.get("kind") == "business_trip"]
        self.assertTrue(trips, "出勤职业应该能抽到出差事件")
        event = trips[0]
        plan = state.db.kv.get("business_trip_plan") or {}
        self.assertEqual("2026-10-08", plan.get("start_day"))
        self.assertEqual(2, plan.get("days"))


# ---------------------------------------------------------------- R9 通勤文案
class TransitTextRefreshTests(_Base):
    def _rows(self):
        return [
            {"start": "09:00", "end": "12:00", "activity": "开会", "place": "公司",
             "zone": "office", "category": "work"},
            {"start": "12:00", "end": "12:20", "activity": "从东莞·写字楼去东莞·写字楼附近，坐地铁（在路上）",
             "place": "路上", "zone": "road", "category": "commute",
             "source": "transit_block", "vehicle": "地铁", "transit": "subway",
             "transit_kind": "transfer"},
            {"start": "12:20", "end": "13:20", "activity": "在楼下小馆吃碗面",
             "place": "楼下小馆", "zone": "restaurant", "category": "meal"},
            {"start": "13:20", "end": "13:50", "activity": "刚出公司，坐地铁回家（在路上）",
             "place": "路上", "zone": "road", "category": "commute",
             "source": "transit_block", "vehicle": "地铁", "transit": "subway",
             "transit_kind": "return"},
            {"start": "13:50", "end": "24:00", "activity": "在家休息", "place": "家",
             "zone": "home", "category": "rest"},
        ]

    def test_refresh_uses_final_places_and_drops_pair_format(self):
        rows = tr.refresh_transit_texts([dict(row) for row in self._rows()])
        transfer = rows[1]
        self.assertIn("离开公司", transfer["activity"])
        self.assertIn("去楼下小馆", transfer["activity"])
        self.assertNotIn("东莞·写字楼", transfer["activity"])
        self.assertNotIn("从", transfer["activity"])
        self.assertEqual("transfer", transfer["transit_kind"])

    def test_return_transit_still_says_home(self):
        rows = tr.refresh_transit_texts([dict(row) for row in self._rows()])
        back = rows[3]
        self.assertIn("回家", back["activity"])
        self.assertEqual("return", back["transit_kind"])


if __name__ == "__main__":
    unittest.main()