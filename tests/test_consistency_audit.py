# -*- coding: utf-8 -*-
"""上线前一致性审计：假时钟 24 小时仿真 + 跨模块不变量。

覆盖：时间轴、地点物理/回程修复、日记地点闸、聊天此刻、钱、旅游、社会逻辑。
工作脚本见 work/simulate_day.py；这里把同一套假时钟/假库/假 LLM 的检查
固化成可回归的测试。运行：python -m unittest tests.test_consistency_audit -v
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TESTS = os.path.dirname(os.path.abspath(__file__))
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_location_rules as loc  # noqa: E402
import amo_transition as tr  # noqa: E402
import amo_trip as trip  # noqa: E402
import work.preview_day as preview  # noqa: E402
import work.simulate_day as sim  # noqa: E402

DAY = sim.DEFAULT_DAY
DAY2 = (dt.date.fromisoformat(DAY) + dt.timedelta(days=1)).isoformat()


class AuditCase(unittest.TestCase):
    def setUp(self):
        sim.set_clock(sim.at(DAY, 9, 0))

    def tearDown(self):
        sim.restore_clock()

    def committed_day(self, **kwargs):
        plugin, state = sim.make_state(city="东莞", day=DAY)
        sim.set_clock(sim.at(DAY, 9, 0))
        ctx, rows = sim.build_day(plugin, state, DAY, **kwargs)
        return plugin, state, ctx, rows


class TimelineAuditTests(AuditCase):
    def test_committed_workday_timeline_is_continuous_and_has_no_fragments(self):
        _plugin, _state, _ctx, rows = self.committed_day()
        self.assertEqual(sim.timeline_violations(rows), [])
        ordered = sorted(rows, key=lambda row: sim.span(row)[0])
        self.assertEqual(ordered[0]["start"], "00:00")
        self.assertEqual(ordered[-1]["end"], "24:00")
        for first, second in zip(ordered, ordered[1:]):
            self.assertLessEqual(sim.span(first)[1], sim.span(second)[0],
                                 (first, second))

    def test_transit_blocks_are_ten_to_forty_minutes(self):
        _plugin, _state, _ctx, rows = self.committed_day()
        # A1：过渡不落库，读侧派生；断言用户可见的过渡时长仍是 10-40 分钟。
        view = _state._schedule_view_items(DAY, rows)
        transit = [row for row in view if row.get("transit_kind")]
        self.assertTrue(transit)
        for row in transit:
            length = sim.span(row)[1] - sim.span(row)[0]
            self.assertTrue(10 <= length <= 40, (row, length))

    def test_no_block_crosses_midnight(self):
        _plugin, _state, _ctx, rows = self.committed_day()
        for row in rows:
            begin, finish = sim.span(row)
            self.assertGreaterEqual(begin, 0, row)
            self.assertLessEqual(finish, 24 * 60, row)
            self.assertLess(begin, finish, row)


class PlacePhysicsTests(AuditCase):
    def test_committed_day_place_switches_go_through_road(self):
        _plugin, _state, _ctx, rows = self.committed_day()
        # 落库只有事件；跨地点切换的“路上”由读侧派生，物理检查看派生视图。
        view = _state._schedule_view_items(DAY, rows)
        self.assertEqual(sim.place_violations(view), [])

    def test_repair_normal_hospital_return_has_one_leg(self):
        rows = [
            {"start": "13:41", "end": "15:41", "place": "医院", "zone": "hospital",
             "activity": "看诊"},
            {"start": "15:41", "end": "16:30", "place": "家", "zone": "home",
             "activity": "瘫着"},
        ]
        repaired, inserted = tr.repair_cross_place_blocks(
            None, DAY, rows, now=sim.at(DAY, 23, 59))
        self.assertEqual(len(inserted), 1, repaired)
        self.assertEqual(inserted[0]["transit_kind"], "return")
        self.assertEqual(inserted[0]["start"], "15:41")
        self.assertEqual(sim.timeline_violations(repaired), [])
        self.assertEqual(sim.place_violations(repaired), [])
        _again, inserted_again = tr.repair_cross_place_blocks(
            None, DAY, repaired, now=sim.at(DAY, 23, 59))
        self.assertEqual(inserted_again, [])

    def test_repair_late_return_does_not_duplicate_or_overlap(self):
        rows = [
            {"start": "21:00", "end": "23:30", "place": "医院", "zone": "hospital",
             "activity": "看诊"},
            {"start": "23:30", "end": "24:00", "place": "家", "zone": "home",
             "activity": "瘫着"},
        ]
        repaired, inserted = tr.repair_cross_place_blocks(
            None, DAY, rows, now=sim.at(DAY, 23, 59))
        self.assertEqual(len(inserted), 1, repaired)
        self.assertEqual(sim.timeline_violations(repaired), [])
        self.assertEqual(sim.place_violations(repaired), [])
        _again, inserted_again = tr.repair_cross_place_blocks(
            None, DAY, repaired, now=sim.at(DAY, 23, 59))
        self.assertEqual(inserted_again, [], _again)

    def test_day_end_insert_transit_never_creates_one_minute_overlap(self):
        rows = [
            {"start": "23:50", "end": "23:57", "place": "公司", "zone": "office",
             "activity": "加班"},
            {"start": "23:57", "end": "24:00", "place": "家", "zone": "home",
             "activity": "瘫着"},
        ]
        result, inserted = tr.insert_transit_blocks(
            DAY, rows, old_items=[], now=sim.at(DAY, 23, 59))
        self.assertLessEqual(len(inserted), 1, result)
        self.assertEqual(sim.timeline_violations(result), [])

    def test_travel_pool_has_three_to_five_non_work_blocks(self):
        plugin, _state = sim.make_state(city="东莞", day=DAY)
        info = trip.new_trip("杭州", DAY, 3)
        info["phase"] = "traveling"
        trip.save(plugin, info)
        rows = trip.travel_day_blocks(plugin, DAY2)
        self.assertTrue(3 <= len(rows) <= 5, rows)
        for row in rows:
            self.assertNotIn(str(row.get("activity") or ""),
                             ("上班", "工位", "洗衣", "做饭"))
            self.assertGreaterEqual(sim.span(row)[0], 7 * 60, row)
            self.assertLessEqual(sim.span(row)[1], 21 * 60 + 30, row)
        self.assertEqual(sim.timeline_violations(rows), [])


class DiaryLocationTests(AuditCase):
    def test_wrong_place_llm_text_is_rewritten_or_falls_back_per_slot(self):
        _plugin, state, ctx, rows = self.committed_day()
        self.assertEqual(sim.check_small_diary(state, ctx, rows), [])

    def test_hospital_diary_never_keeps_home_words(self):
        plugin, state = sim.make_state(city="东莞", day=DAY)
        sim.set_clock(sim.at(DAY, 10, 0))
        ctx, rows = sim.build_day(
            plugin, state, DAY, health={"sick": True, "status": "发烧",
                                       "treatment": "打吊水"},
            sick_action="take_sick_leave")
        hospital = [row for row in state._hourly_schedule(rows)
                    if sim.place_category(row) == "hospital"]
        self.assertTrue(hospital, rows)
        item = hospital[0]
        llm = sim.WrongLLM(sim.WRONG_TEXT["hospital"])
        result = __import__("asyncio").run(state.engine.generate_diary(
            "", dict(ctx, fact_pool=[], chat_lines_hour=[], worn="", assets="",
                     outfit="", gift_log=""), item, None, None, llm))
        text = str(result["diary"])
        self.assertEqual(loc.check(text, item.get("place"), item.get("zone")), [])
        for word in ("被窝", "茶几", "沙发"):
            self.assertNotIn(word, text)

    def test_office_diary_never_keeps_bed_or_home_words(self):
        _plugin, state, ctx, rows = self.committed_day()
        office = [row for row in state._hourly_schedule(rows)
                  if sim.place_category(row) == "office"]
        self.assertTrue(office)
        item = office[0]
        llm = sim.WrongLLM(sim.WRONG_TEXT["office"])
        result = __import__("asyncio").run(state.engine.generate_diary(
            "", dict(ctx, fact_pool=[], chat_lines_hour=[], worn="", assets="",
                     outfit="", gift_log=""), item, None, None, llm))
        text = str(result["diary"])
        self.assertEqual(loc.check(text, item.get("place"), item.get("zone")), [])
        for word in ("床", "被窝", "沙发"):
            self.assertNotIn(word, text)

    def test_big_diary_rejects_places_not_visited_today(self):
        self.assertEqual(sim.check_daily_diary(), [])


class ChatNowAuditTests(AuditCase):
    def test_every_ten_minutes_place_matches_current_block(self):
        plugin, state, _ctx, rows = self.committed_day()
        self.assertEqual(sim.check_chat(plugin, state, rows, DAY), [])

    def test_road_guard_uses_active_transition_when_state_cache_stale(self):
        plugin, _state = sim.make_state(city="东莞", day=DAY)
        base = sim.at(DAY, 8, 50)
        sim.set_clock(base)
        self.assertTrue(tr.start(plugin, "commute", "家", minutes=40, now=base))

        class _StaleState:
            def current_activity(self):
                return {"place": "家", "zone": "home"}

        plugin.state = _StaleState()
        guarded = tr.guard_road_home(plugin, "我到家了，瘫在沙发上",
                                     base + dt.timedelta(minutes=39))
        self.assertNotIn("到家了", guarded)
        self.assertNotIn("沙发上", guarded)
        self.assertIn("还在车上", guarded)
        self.assertEqual(guarded.count("还在车上"), 1)

    def test_road_guard_rewrites_sofa_until_arrival(self):
        plugin, _state, _ctx, rows = self.committed_day()
        road = [row for row in rows if row.get("zone") == "road"]
        home = [row for row in rows if row.get("zone") == "home"
                and sim.span(row)[0] >= 18 * 60]
        self.assertTrue(road)
        self.assertTrue(home)
        sim.set_clock(sim.at(DAY, sim.span(road[0])[0] // 60,
                             sim.span(road[0])[0] % 60))
        guarded = tr.guard_road_home(plugin, "我到家了，瘫在沙发上",
                                     sim.at(DAY, 12, 0))
        self.assertNotIn("沙发", guarded)
        self.assertNotIn("到家了", guarded)
        sim.set_clock(sim.at(DAY, sim.span(home[0])[0] // 60,
                             sim.span(home[0])[0] % 60))
        self.assertEqual(tr.guard_road_home(plugin, "我到家了，瘫在沙发上",
                                            sim.at(DAY, 19, 0)),
                         "我到家了，瘫在沙发上")

    def test_transit_step_place_is_road_not_destination(self):
        plugin, _state = sim.make_state(city="东莞", day=DAY)
        sim.set_clock(sim.at(DAY, 9, 0))
        data = tr.start(plugin, "commute", "医院", now=sim.at(DAY, 9, 0))
        self.assertTrue(data)
        current = None
        for offset in range(1, 90):
            fixed = sim.at(DAY, 9, 0) + dt.timedelta(minutes=offset)
            sim.set_clock(fixed)
            current = tr.current_activity(plugin, fixed)
            if current and current.get("zone") == "road":
                break
        self.assertIsNotNone(current)
        self.assertIn(current.get("place"), ("路上", "地铁", "公交"))
        self.assertTrue(current.get("transit"))
        self.assertNotIn("医院", str(current.get("activity") or ""))


class DatePresenceAuditTests(AuditCase):
    """约会结束后的块不许写他还在身边；约会时段内允许面对面（B 方案）。"""

    def _rows(self):
        return [
            {"start": "19:22", "end": "21:22", "activity": "和老公火锅", "place": "楼下小馆",
             "zone": "restaurant", "category": "social",
             "_diary": "他坐在我对面给我夹菜，我抬头看他。"},
            {"start": "21:22", "end": "21:32", "activity": "一个人走回家（耳机里还连着）",
             "place": "路上", "zone": "road", "category": "life",
             "_diary": "一个人走回家，耳机里还连着语音。"},
            {"start": "21:32", "end": "23:00", "activity": "到家给他发消息/视频道晚安",
             "place": "家", "zone": "home", "category": "rest",
             "_diary": "到家给他发消息，视频道了晚安。"},
        ]

    def _flags(self, rows):
        hard, _warn = preview.review(rows, DAY, sim.at(DAY, 23, 0))
        return [item[0] for item in hard]

    def test_date_block_face_to_face_is_allowed(self):
        rows = self._rows()
        rows[2]["_diary"] = "到家给他发消息，视频道了晚安。"
        self.assertNotIn("date_presence_after_end", self._flags(rows))

    def test_after_date_block_with_shared_home_is_hard(self):
        rows = self._rows()
        rows[2]["_diary"] = "我们一起回家，他抱着我，屋里就我俩。"
        self.assertIn("date_presence_after_end", self._flags(rows))

    def test_after_date_walk_block_must_be_alone(self):
        rows = self._rows()
        rows[1]["_diary"] = "他就在身边，我们一起回家。"
        self.assertIn("date_presence_after_end", self._flags(rows))


class MoneyAuditTests(AuditCase):
    def test_day_spend_never_negative_and_same_idem_single_ledger(self):
        self.assertEqual(sim.check_money(), [])
        plugin = sim.FakePlugin()
        plugin.wallet.credit(100000, "初始化", "audit:init")
        info = trip.new_trip("杭州", DAY, 2)
        first = trip.travel_spend(plugin, info, 100, "晚饭", "audit:food",
                                  category="food", balance=None)
        second = trip.travel_spend(plugin, info, 100, "晚饭", "audit:food",
                                   category="food", balance=None)
        self.assertTrue(first["ok"])
        self.assertEqual(second["reason"], "duplicate")
        idems = [entry["idem"] for entry in plugin.wallet.ledger(20)]
        self.assertEqual(len(idems), len(set(idems)))
        self.assertGreaterEqual(plugin.wallet.balance(), 0)

    def test_travel_premium_only_food_transport_daily(self):
        plugin = sim.FakePlugin()
        for category in ("food", "transport", "daily"):
            self.assertEqual(trip.price(plugin, category, 100), 160)
        for category in ("hotel", "ticket", "salary", "bill", "mortgage"):
            self.assertEqual(trip.price(plugin, category, 100), 100)

    def test_finance_obligations_do_not_go_through_trip_premium(self):
        plugin = sim.FakePlugin()
        plugin.wallet.credit(100000, "初始化", "audit:init")
        info = trip.new_trip("杭州", DAY, 2)
        before = plugin.wallet.balance()
        for category in ("salary", "bill", "mortgage", "utility"):
            result = trip.travel_spend(plugin, info, 100, "义务",
                                       "audit:" + category, category=category,
                                       balance=None)
            self.assertEqual(result["reason"], "exempt", result)
        self.assertEqual(plugin.wallet.balance(), before)


class TripAuditTests(AuditCase):
    def test_manual_start_writes_three_to_five_hour_outbound(self):
        plugin, state = sim.make_state(city="东莞", day=DAY)
        plugin.wallet.credit(800000, "初始化", "audit:trip:init")
        sim.set_clock(sim.at(DAY, 9, 0))
        started = trip.trip_manual_start(plugin, city="杭州", days=3, force=True,
                                         now_day=DAY, now=sim.at(DAY, 9, 0),
                                         balance=8000)
        self.assertTrue(started.get("ok"), started)
        outbound = [row for row in state.schedule[DAY]
                    if row.get("leg") == "outbound"]
        self.assertTrue(outbound)
        total = sum(sim.span(row)[1] - sim.span(row)[0] for row in outbound)
        self.assertTrue(180 <= total <= 300, total)
        self.assertEqual(started["phase"], "outbound")

    def test_traveling_day_pool_and_jit_never_works(self):
        self.assertEqual(sim.check_trip(DAY), [])
        plugin, state = sim.make_state(city="东莞", day=DAY)
        plugin.wallet.credit(800000, "初始化", "audit:trip:init")
        info = trip.new_trip("杭州", DAY, 3)
        info["phase"] = "traveling"
        trip.save(plugin, info)
        sim.set_clock(sim.at(DAY2, 10, 0))
        pool = state._trip_day_items(DAY2, "traveling")
        self.assertTrue(3 <= len(pool) <= 5, pool)
        for row in pool:
            self.assertNotIn(str(row.get("activity") or ""),
                             ("上班", "工位", "洗衣", "做饭"))

    def test_after_departure_change_to_dali_is_zero_change(self):
        plugin, state = sim.make_state(city="东莞", day=DAY)
        plugin.wallet.credit(800000, "初始化", "audit:trip:init")
        sim.set_clock(sim.at(DAY, 9, 0))
        started = trip.trip_manual_start(plugin, city="杭州", days=3, force=True,
                                         now_day=DAY, now=sim.at(DAY, 9, 0),
                                         balance=8000)
        self.assertTrue(started.get("ok"), started)
        rows = copy.deepcopy(state.schedule[DAY])
        city = trip.status(plugin)["city"]
        self.assertEqual(trip.guard_change(plugin, "改去大理")["action"], "blocked")
        result = state.commit_schedule_change(
            DAY, copy.deepcopy(rows), old_items=copy.deepcopy(rows),
            source="chat", reason="改去大理")
        self.assertTrue(result.get("blocked"), result)
        self.assertEqual(state.schedule[DAY], rows)
        self.assertEqual(trip.status(plugin)["city"], city)

    def test_finish_rest_day_and_memory_are_idempotent(self):
        self.assertEqual(sim.check_trip_finish(DAY), [])

    def test_reload_twice_does_not_duplicate_blocks_or_memory(self):
        plugin, state = sim.make_state(city="东莞", day=DAY)
        plugin.wallet.credit(800000, "初始化", "audit:trip:init")
        sim.set_clock(sim.at(DAY, 9, 0))
        started = trip.trip_manual_start(plugin, city="杭州", days=2, force=True,
                                         now_day=DAY, now=sim.at(DAY, 9, 0),
                                         balance=8000)
        self.assertTrue(started.get("ok"), started)
        return_day = DAY2
        sim.set_clock(sim.at(return_day, 16, 0))
        trip.begin_return(plugin, started["trip"], now=sim.at(return_day, 16, 0))
        sim.set_clock(sim.at(return_day, 22, 0))
        finished = trip.sync(plugin, now=sim.at(return_day, 22, 0))
        self.assertEqual(finished["phase"], "rest")
        row_count = len(state.schedule.get(return_day, []))
        city = finished["city"]
        sim.set_clock(sim.at(return_day, 22, 30))
        first = trip.load(plugin)
        second = trip.load(plugin)
        self.assertEqual(first["phase"], second["phase"])
        self.assertEqual(first["city"], city)
        self.assertEqual(len(state.schedule.get(return_day, [])), row_count)
        import asyncio
        asyncio.run(trip.finalize_async(plugin, second))
        asyncio.run(trip.finalize_async(plugin, second))
        memory = [call for call in plugin.memory_calls if call["kind"] == "activity"]
        facts = [call for call in plugin.memory_calls if call["kind"] == "fact"]
        self.assertEqual(len(memory), 1)
        self.assertEqual(len(facts), 1)
        self.assertEqual(len(plugin.db.kv.get(trip.TRAVEL_LOG_KEY) or []), 1)


class SocialAuditTests(AuditCase):
    def test_workday_has_work_block_and_sick_leave_has_none(self):
        _plugin, _state, _ctx, rows = self.committed_day()
        work = [row for row in rows
                if row.get("category") == "work" and row.get("zone") == "office"]
        self.assertGreaterEqual(len(work), 3, rows)
        plugin, state = sim.make_state(city="东莞", day=DAY)
        sim.set_clock(sim.at(DAY, 9, 0))
        _ctx, sick_rows = sim.build_day(
            plugin, state, DAY,
            health={"sick": True, "status": "感冒"},
            day_type="workday", sick_action="take_sick_leave")
        self.assertFalse([row for row in sick_rows if row.get("zone") == "office"])
        self.assertFalse([row for row in sick_rows if row.get("category") == "work"])

    def test_late_night_has_no_mall_company_or_restaurant(self):
        _plugin, _state, _ctx, rows = self.committed_day()
        for row in rows:
            begin = sim.span(row)[0]
            if 22 * 60 <= begin or begin < 6 * 60:
                self.assertNotIn(sim.place_category(row),
                                 ("mall", "office", "restaurant"), row)
        plugin, _state = sim.make_state(city="东莞", day=DAY)
        for hour in (22, 23):
            plan = trip.travel_event_for_hour(plugin, DAY, hour)
            self.assertNotIn(plan["zone"], ("mall", "office", "restaurant"), plan)

    def test_late_night_jit_plan_stays_home(self):
        from schedule_engine import jit_plan
        for hour in (22, 23):
            plan = jit_plan(hour, 100, 8000, weekday=0)
            self.assertEqual(plan["zone"], "home", (hour, plan))
            self.assertNotIn(plan["zone"], ("mall", "office", "restaurant"))

    def test_bill_on_first_and_salary_on_fifteenth_continue_during_trip(self):
        plugin = sim.FakePlugin()
        plugin.wallet.credit(100000, "初始化", "audit:init")
        info = trip.new_trip("杭州", "2026-11-01", 10)
        info["phase"] = "traveling"
        trip.save(plugin, info)
        for day, category in (("2026-11-01", "bill"), ("2026-11-15", "salary")):
            self.assertEqual(sim.at(day, 9, 0).day, 1 if category == "bill" else 15)
            result = trip.travel_spend(plugin, info, 100, "义务", "audit:" + category,
                                       category=category, balance=None)
            self.assertEqual(result["reason"], "exempt", result)
            self.assertEqual(trip.price(plugin, category, 100), 100)


if __name__ == "__main__":
    unittest.main()
