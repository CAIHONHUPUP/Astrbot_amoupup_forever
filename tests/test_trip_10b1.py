# -*- coding: utf-8 -*-
"""第十轮 B1：拦截 / 旅游待办 / JIT 旅行日池 / 跨地点回程通勤块。

每条都对「改回去就红」：去掉 commit 里的目的地锁、删掉 write_todos、
把旅行日池换回普通 jit_plan、或不再插 transit 块，这里都会失败。
"""
from __future__ import annotations

import copy
import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_transition as tr  # noqa: E402
import amo_trip as trip  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_state_common import minutes_now, today_str  # noqa: E402
from fixtures import FixedClock, today_at  # noqa: E402

DAY = today_str()


def _day(offset):
    import datetime as _dt
    return (_dt.date.fromisoformat(DAY) + _dt.timedelta(days=offset)).isoformat()


def _minute(text):
    body = str(text or "00:00").strip()[:5]
    hour, _sep, minute = body.partition(":")
    return int(hour or 0) * 60 + int(minute or 0)


def _no_overlap(rows):
    ordered = sorted([row for row in rows if isinstance(row, dict)],
                     key=lambda row: _minute(row.get("start")))
    for prev, nxt in zip(ordered, ordered[1:]):
        if _minute(prev.get("end")) > _minute(nxt.get("start")):
            return False
    return True


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}
        self.todos_map = {}
        self.versions = {}
        self.saves = 0
        self.deleted = []
        self.added = []
        self.cleared = []

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return [dict(row) for row in (self.schedules.get(day) or [])]

    def save_schedule(self, day, items):
        self.saves += 1
        self.schedules[day] = [dict(row) for row in items]

    def delete_schedule(self, day):
        self.deleted.append(day)
        self.schedules.pop(day, None)
        return True

    def schedule_version(self, day):
        return self.versions.get(day, 0)

    def bump_schedule_version(self, day):
        self.versions[day] = self.versions.get(day, 0) + 1
        return self.versions[day]

    def mark_diaries_stale(self, day, from_hour, version):
        return []

    def todos(self, day, include_done=False):
        return [dict(row) for row in self.todos_map.get(day, [])]

    def clear_todos(self, day):
        self.cleared.append(day)
        self.todos_map[day] = []
        return True

    def add_todo(self, day, item, source=""):
        row = dict(item)
        row.setdefault("id", len(self.added) + 1)
        row["source"] = str(item.get("source") or source or "")
        self.added.append((day, row, source))
        self.todos_map.setdefault(day, []).append(row)
        return int(row["id"])

    def close_todo(self, todo_id):
        return True


class FakeWallet:
    def __init__(self, balance=8000):
        self._balance = balance

    def balance(self):
        return self._balance

    def debit(self, cost, reason="", idem="", **kwargs):
        return True

    def credit(self, cost, reason="", idem="", **kwargs):
        return True


class FakeState:
    def __init__(self):
        self.moods = []
        self.spots = [{"city": "杭州", "name": "西湖"}, {"city": "厦门", "name": "鼓浪屿"},
                      {"city": "大理", "name": "洱海"}, {"city": "成都", "name": "宽窄巷子"}]

    def scenic_spots(self):
        return list(self.spots)

    def add_mood(self, delta):
        self.moods.append(delta)


class FakePlugin:
    def __init__(self, config=None, balance=8000):
        self.db = FakeDB()
        self.wallet = FakeWallet(balance)
        self.state = FakeState()
        self.events = None
        self.conf = {"travel_enabled": True}
        if config:
            self.conf.update(config)

    def cfg_int(self, name, default=0):
        return int(self.conf.get(name, default))

    def cfg_float(self, name, default=0.0):
        return float(self.conf.get(name, default))

    def cfg_bool(self, name, default=False):
        return bool(self.conf.get(name, default))

    def cfg_str(self, name, default=""):
        return str(self.conf.get(name, default))

    def today(self):
        return DAY


class FixedRng:
    def __init__(self, value=0.0, pick=0):
        self.value = value
        self.pick = pick

    def random(self):
        return self.value

    def choice(self, pool):
        return pool[self.pick % len(pool)]

    def randint(self, low, high):
        return high


def _state(plugin):
    state = HumanState.__new__(HumanState)
    state.p = plugin
    state.db = plugin.db
    state._schedule_commit_lock = threading.RLock()
    state._schedule_gen_locks = {}
    state.schedule = {}
    state.schedule_ctx = {}
    state.schedule_profile = {}
    state.mood = {}
    plugin.state = state
    return state


def _seed(state, rows):
    state.schedule[DAY] = copy.deepcopy(rows)
    state.db.schedules[DAY] = copy.deepcopy(rows)
    state.db.saves = 0


def _trip_plugin(phase="traveling", start_day=None, days=3):
    plugin = FakePlugin()
    state = _state(plugin)
    start_day = start_day or DAY
    info = trip.new_trip("杭州", start_day, days)
    info["phase"] = phase
    trip.save(plugin, info)
    return plugin, state, info


# ---------- 1. commit 同批追加重叠 ----------


class CommitGuardTests(unittest.TestCase):
    def test_after_departure_city_change_is_zero_change(self):
        plugin, state, info = _trip_plugin("traveling")
        rows = [{"start": "14:00", "end": "15:00", "activity": "在家休息",
                 "place": "家", "category": "rest"}]
        _seed(state, rows)
        result = state.commit_schedule_change(
            DAY, [{"start": "14:00", "end": "15:00", "activity": "去大理古城",
                   "place": "家", "category": "rest"}],
            old_items=rows, source="chat", reason="改去大理古城")
        self.assertFalse(result.get("changed"))
        self.assertIn("票都买好了", result.get("context") or "")
        self.assertEqual(state.schedule[DAY], rows, "日程必须零改动")
        self.assertEqual(plugin.db.get_schedule(DAY), rows)
        self.assertEqual(plugin.db.saves, 0, "不能落库")
        self.assertEqual(trip.status(plugin)["phase"], "traveling")
        self.assertEqual(trip.status(plugin)["city"], "杭州")

    def test_before_departure_city_change_takes_effect(self):
        plugin, state, _info = _trip_plugin("approved")
        rows = [{"start": "14:00", "end": "15:00", "activity": "在家休息",
                 "place": "家", "category": "rest"}]
        _seed(state, rows)
        result = state.commit_schedule_change(
            DAY, [{"start": "14:00", "end": "15:00", "activity": "去大理古城",
                   "place": "家", "category": "rest"}],
            old_items=rows, source="chat", reason="改去大理古城")
        self.assertTrue(result.get("changed"))
        self.assertEqual(state.schedule[DAY][0]["activity"], "去大理古城")

    def test_call_back_switches_to_returning(self):
        plugin, state, _info = _trip_plugin("traveling")
        rows = [{"start": "14:00", "end": "15:00", "activity": "在景区排队",
                 "place": "景区", "category": "play"}]
        _seed(state, rows)
        result = state.commit_schedule_change(
            DAY, [{"start": "14:00", "end": "15:00", "activity": "往回走了",
                   "place": "景区", "category": "play"}],
            old_items=rows, source="chat", reason="回来吧")
        self.assertFalse(result.get("changed"))
        self.assertIn("往回走", result.get("context") or "")
        # 10B2：叫回要立刻写返程块，所以这里会落库一次（聊天改日程本身仍被拦下）
        self.assertGreaterEqual(plugin.db.saves, 1)
        self.assertEqual(trip.status(plugin)["phase"], "returning")
        persisted = plugin.db.get_schedule(DAY)
        self.assertTrue(any(row.get("leg") == "return" for row in persisted), persisted)
        self.assertEqual(state.schedule[DAY], persisted, "返程块必须真的落库")

    def test_intent_entry_does_not_start_new_transition(self):
        # 「改去商场吧」既是改地点，又能被 detect_intent 认成去商场：没有锁就会起过渡。
        plugin, _state_obj, _info = _trip_plugin("traveling")
        self.assertIsNone(tr.maybe_start_from_text(plugin, "改去商场吧"))
        self.assertFalse(plugin.db.kv.get(tr.STATE_KEY))
        self.assertEqual(trip.status(plugin)["city"], "杭州")

    def test_intent_entry_call_back_switches_to_returning(self):
        plugin, _state_obj, _info = _trip_plugin("traveling")
        self.assertIsNone(tr.maybe_start_from_text(plugin, "回来吧"))
        self.assertEqual(trip.status(plugin)["phase"], "returning")


# ---------- 3. approved / 手动出发写旅游待办 ----------


class TripTodoTests(unittest.TestCase):
    def test_write_todos_fills_future_days_without_schedule(self):
        plugin, state, info = _trip_plugin("approved")
        result = trip.write_todos(plugin, info)
        tomorrow, day_after = _day(1), _day(2)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(sorted(result.get("days", {})), [tomorrow, day_after])
        for day in (tomorrow, day_after):
            rows = plugin.db.todos(day)
            self.assertEqual(len(rows), len(trip.TRIP_PLAN_BLOCKS))
            for row in rows:
                self.assertEqual(row.get("category"), "travel")
                self.assertTrue(str(row.get("source") or "").startswith("trip:"))
            self.assertEqual(plugin.db.get_schedule(day), [], "未来日期不能铺日程")
            self.assertIn(day, plugin.db.deleted)
        self.assertIsNone(plugin.db.todos_map.get(_day(3)))

    def test_manual_start_writes_todos(self):
        plugin, state, _info = _trip_plugin("done")
        state.scenic_spots = lambda: [{"city": "杭州", "name": "西湖"}]
        plugin.db.kv.pop(trip.KEY, None)
        result = trip.trip_manual_start(plugin, city="杭州", days=3,
                                        force=True, now_day=DAY)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(plugin.db.todos(_day(1))), len(trip.TRIP_PLAN_BLOCKS))
        self.assertTrue(all(str(row.get("source") or "").startswith("trip:")
                            for row in plugin.db.todos(_day(1))))


# ---------- 4. JIT 旅行日池 ----------


class TravelDayPoolTests(unittest.TestCase):
    BAD_WORDS = ("上班", "工位", "公司", "洗衣", "做饭", "下厨")

    def test_traveling_day_uses_only_travel_pool(self):
        plugin, state, _info = _trip_plugin("traveling")
        self.assertEqual(trip.day_kind(plugin, DAY), "traveling")
        rows = state._trip_day_items(DAY, "traveling")
        self.assertTrue(3 <= len(rows) <= 5, rows)
        self.assertTrue(_no_overlap(rows), rows)
        for row in rows:
            self.assertGreaterEqual(_minute(row.get("start")), 7 * 60, row)
            for word in self.BAD_WORDS:
                self.assertNotIn(word, str(row.get("activity") or ""), row)
        self.assertIsNone(plugin.db.todos_map.get(DAY))

    def test_traveling_day_is_stable_after_reload(self):
        plugin, state, _info = _trip_plugin("traveling")
        first = state._trip_day_items(DAY, "traveling")
        saves = plugin.db.saves
        again = state._trip_day_items(DAY, "traveling")
        self.assertEqual(again, first, "同一天不能每次刷新换一份")
        self.assertEqual(plugin.db.saves, saves, "不能重复写库")
        reload_plugin = FakePlugin()
        reload_plugin.db = plugin.db
        reload_state = _state(reload_plugin)
        reloaded = reload_state._trip_day_items(DAY, "traveling")
        self.assertEqual(reloaded, first, "重载后必须还是同一份")

    def test_traveling_jit_plan_never_uses_work_bank(self):
        plugin, state, _info = _trip_plugin("traveling")
        plan, cost, paid = state._jit_plan_for_hour(None, 10, DAY)
        self.assertEqual((cost, paid), (0, False))
        for word in self.BAD_WORDS:
            self.assertNotIn(word, str(plan.get("action") or ""), plan)
        self.assertIn(plan.get("action"), [event[0] for event in trip.TRAVEL_EVENT_POOL])

    def test_rest_day_jit_plan_is_home_rest(self):
        plugin, state, _info = _trip_plugin(
            "returning", start_day=_day(-3), days=3)
        plan, cost, paid = state._jit_plan_for_hour(None, 10, DAY)
        self.assertEqual(plan.get("action"), trip.REST_DAY_ACTIVITY)
        self.assertEqual(plan.get("category"), "rest")
        self.assertEqual((cost, paid), (0, False))

    def test_rest_day_is_single_block_and_truncates_old(self):
        plugin, state, _info = _trip_plugin(
            "returning", start_day=_day(-3), days=3)
        old = [{"start": "09:00", "end": "18:00", "activity": "上班",
                "place": "公司", "category": "work"}]
        state.schedule[DAY] = copy.deepcopy(old)
        plugin.db.schedules[DAY] = copy.deepcopy(old)
        self.assertEqual(trip.day_kind(plugin, DAY), "rest")
        rows = state._trip_day_items(DAY, "rest")
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["activity"], trip.REST_DAY_ACTIVITY)
        self.assertEqual((rows[0]["start"], rows[0]["end"]), ("08:00", "20:00"))
        self.assertTrue(_no_overlap(rows))

    def test_rest_day_helper_keeps_outside_pieces(self):
        plugin, _state_obj, info = _trip_plugin("returning")
        rows, _changed = trip.rest_day_rows(
            plugin, DAY,
            [{"start": "06:00", "end": "21:00", "activity": "上班",
              "place": "公司", "category": "work"}])
        self.assertTrue(_no_overlap(rows), rows)
        rest = [row for row in rows if row.get("activity") == trip.REST_DAY_ACTIVITY]
        self.assertEqual(len(rest), 1)
        self.assertIn("06:00", [row.get("start") for row in rows])
        self.assertIn("20:00", [row.get("start") for row in rows])


# ---------- 5. 配置接线 ----------


class ConfigWiringTests(unittest.TestCase):
    def test_days_min_and_max_are_read(self):
        plugin = FakePlugin({"travel_days_min": 1, "travel_days_max": 1})
        result = trip.decide(plugin, DAY, None, 8000, long_holiday=True,
                             rng=FixedRng(0.0))
        self.assertTrue(result.get("go"), result)
        self.assertEqual(result["days"], 1)
        default_plugin = FakePlugin()
        default = trip.decide(default_plugin, DAY, None, 8000, long_holiday=True,
                              rng=FixedRng(0.0))
        self.assertEqual(default["days"], 3, "默认还是 2-3 天上限 3")

    def test_budget_min_is_read(self):
        plugin = FakePlugin({"travel_budget_min": 5000})
        result = trip.budget_precheck(plugin, 3000, 3)
        self.assertFalse(result["ok"])
        self.assertEqual(result["need"], 5000)
        default_plugin = FakePlugin()
        default = trip.budget_precheck(default_plugin, 2000, 3)
        self.assertTrue(default["ok"])
        self.assertEqual(default["need"], 2000, "默认预检 2000 不能变")

    def test_assassin_prob_is_read(self):
        plugin = FakePlugin({"travel_assassin_prob": 0.0})
        info = trip.new_trip("杭州", DAY, 2)
        self.assertFalse(trip.assassin(plugin, info, rng=FixedRng(0.0),
                                       balance=5000)["hit"])
        default_plugin = FakePlugin()
        self.assertTrue(trip.assassin(default_plugin, info, rng=FixedRng(0.0),
                                      balance=5000)["hit"])

    def test_silence_ok_is_read(self):
        self.assertTrue(trip.silence_ok(FakePlugin()))
        self.assertFalse(trip.silence_ok(FakePlugin({"travel_silence_ok": False})))


# ---------- 补充需求：跨地点切换必须补通勤块 ----------


class TransitBlockTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin()
        self.state = _state(self.plugin)

    def _commit(self, old, new, source="jit", reason="到点即时决策"):
        self.state.schedule[DAY] = copy.deepcopy(old)
        self.plugin.db.schedules[DAY] = copy.deepcopy(old)
        self.plugin.db.saves = 0
        return self.state.commit_schedule_change(
            DAY, copy.deepcopy(new), old_items=copy.deepcopy(old),
            source=source, reason=reason)

    def _assert_return_transit(self, old_place):
        old = [{"start": "13:41", "end": "15:43", "activity": "在%s排队" % old_place,
                "place": old_place, "category": "health", "happened": True}]
        new = old + [{"start": "15:43", "end": "16:02",
                      "activity": "从%s回来，在沙发上瘫着" % old_place,
                      "place": "家", "category": "rest"}]
        self._commit(old, new)
        rows = self.state.schedule[DAY]
        self.assertTrue(_no_overlap(rows), rows)
        transit = [row for row in rows if row.get("source") == "transit_block"]
        self.assertEqual(len(transit), 1, rows)
        block = transit[0]
        self.assertEqual(block["start"], "15:43")
        self.assertEqual(block["place"], "路上")
        self.assertEqual(block["zone"], "road")
        duration = _minute(block["end"]) - _minute(block["start"])
        self.assertTrue(15 <= duration <= 40, duration)
        home = [row for row in rows if row.get("place") == "家"][0]
        self.assertEqual(home["start"], block["end"], "下一块必须从通勤块结束开始")
        self.assertTrue(str(block.get("activity") or "").endswith("（在路上）"))

    def test_hospital_to_home_inserts_transit(self):
        self._assert_return_transit("医院")

    def test_company_to_home_inserts_transit(self):
        self._assert_return_transit("公司")

    def test_mall_to_home_inserts_transit(self):
        self._assert_return_transit("商场")

    def test_home_internal_move_needs_no_transit(self):
        rows = [{"start": "13:00", "end": "14:00", "activity": "在客厅追剧",
                 "place": "客厅", "category": "rest"},
                {"start": "14:00", "end": "15:00", "activity": "回卧室躺着",
                 "place": "卧室", "category": "rest"}]
        self._commit([], rows)
        persisted = self.state.schedule[DAY]
        self.assertEqual(len(persisted), 2, persisted)
        self.assertFalse(any(row.get("source") == "transit_block" for row in persisted))

    def test_transit_duration_uses_stable_seed(self):
        first = tr.transit_minutes(DAY, 15 * 60 + 43, "医院", "家")
        second = tr.transit_minutes(DAY, 15 * 60 + 43, "医院", "家")
        self.assertEqual(first, second)
        self.assertTrue(15 <= first <= 40)

    def test_transit_insert_is_idempotent(self):
        self._assert_return_transit("医院")
        persisted = copy.deepcopy(self.state.schedule[DAY])
        count = len(persisted)
        result = self.state.commit_schedule_change(
            DAY, copy.deepcopy(persisted), old_items=copy.deepcopy(persisted),
            source="jit", reason="刷新一次")
        self.assertFalse(result.get("changed"))
        rows = self.plugin.db.get_schedule(DAY)
        self.assertEqual(len(rows), count)
        self.assertEqual(len([row for row in rows
                              if row.get("source") == "transit_block"]), 1)

    def test_current_activity_reads_transit_step(self):
        # 用例按「此刻 ± 分钟」造窗口：0 点刚过时 max(0, now-70) 会塌成同一分钟，
        # 通勤块插不进来。冻结到当天 13:30，窗口才是用例本来的形状。
        frozen = FixedClock(today_at(13, 30))
        frozen.start()
        self.addCleanup(frozen.stop)
        now_min = minutes_now()
        start = tr._minute_text(max(0, now_min - 70))
        prev_end = tr._minute_text(max(0, now_min - 10))
        next_end = tr._minute_text(min(24 * 60, now_min + 40))
        # 上一小时的 JIT current_action 还没过期（医院），没有通勤块就会继续显示医院
        self.plugin.db.kv["current_action"] = {
            "action": "在医院排队看诊", "place": "医院", "zone": "hospital",
            "category": "health", "at": 1, "until": 10 ** 12, "hour": 13,
        }
        old = [{"start": start, "end": prev_end, "activity": "在医院排队看诊",
                "place": "医院", "category": "health", "happened": True}]
        new = old + [{"start": prev_end, "end": next_end,
                      "activity": "从医院回来，在沙发上瘫着",
                      "place": "家", "category": "rest"}]
        self._commit(old, new)
        current = self.state.current_activity()
        self.assertEqual(current.get("place"), "路上")
        self.assertEqual(current.get("zone"), "road")
        self.assertIn("在路上", str(current.get("activity") or ""))

    def test_road_terminal_words_are_rewritten(self):
        plugin = FakePlugin()
        from types import SimpleNamespace
        plugin.state = SimpleNamespace(
            current_activity=lambda: {"activity": "在路上", "place": "路上",
                                      "zone": "road"})
        text = tr.guard_terminal(plugin, "我到家了，瘫在沙发上，好舒服")
        self.assertNotIn("到家", text)
        self.assertNotIn("沙发上", text)
        self.assertIn("车上", text)

    def test_reply_cleanup_wires_road_guard(self):
        import inspect
        import amo_runtime
        body = inspect.getsource(amo_runtime.RuntimeMixin._deco_text_cleanup)
        self.assertIn("rewrite_sent_text", body,
                      "普通聊天回复必须过通勤终态守卫")

    def test_after_arrival_terminal_words_pass(self):
        plugin = FakePlugin()
        from types import SimpleNamespace
        plugin.state = SimpleNamespace(
            current_activity=lambda: {"activity": "休息", "place": "家",
                                      "zone": "home"})
        text = "我到家了，瘫在沙发上，好舒服"
        self.assertEqual(tr.guard_terminal(plugin, text), text)


# ---------- 收尾：读/加载时修复既有跨地点瞬移 ----------


class RepairTransitTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin()
        self.state = _state(self.plugin)

    def _bad_pair(self):
        return [{"start": "13:41", "end": "15:41", "activity": "在医院排队看诊",
                 "place": "医院", "category": "health", "happened": True},
                {"start": "15:41", "end": "16:00",
                 "activity": "从医院回来，在沙发上瘫着",
                 "place": "家", "category": "rest"}]

    def test_repair_existing_hospital_pair(self):
        rows = self._bad_pair()
        _seed(self.state, rows)
        inserted = self.state._repair_transit_blocks(DAY)
        self.assertEqual(len(inserted), 1, inserted)
        persisted = self.state.schedule[DAY]
        self.assertEqual(len(persisted), 3, persisted)
        transit = [row for row in persisted
                   if row.get("source") == "transit_block"][0]
        self.assertEqual(transit["start"], "15:41")
        duration = _minute(transit["end"]) - _minute(transit["start"])
        self.assertTrue(15 <= duration <= 40, duration)
        home = [row for row in persisted if row.get("place") == "家"][0]
        self.assertEqual(home["start"], transit["end"], "家块必须顺延到路上块之后")
        self.assertEqual(_minute(home["end"]) - _minute(home["start"]), 19,
                         "顺延要保留原时长")
        self.assertTrue(_no_overlap(persisted), persisted)

    def test_repair_is_idempotent(self):
        _seed(self.state, self._bad_pair())
        self.state._repair_transit_blocks(DAY)
        saves = self.plugin.db.saves
        count = len(self.state.schedule[DAY])
        again = self.state._repair_transit_blocks(DAY)
        self.assertEqual(again, [])
        self.assertEqual(self.plugin.db.saves, saves, "第二次不能重复写库")
        self.assertEqual(len(self.state.schedule[DAY]), count)

    def test_repair_skips_home_internal(self):
        rows = [{"start": "13:00", "end": "14:00", "activity": "在客厅追剧",
                 "place": "客厅", "category": "rest"},
                {"start": "14:00", "end": "15:00", "activity": "回卧室躺着",
                 "place": "卧室", "category": "rest"}]
        _seed(self.state, rows)
        inserted = self.state._repair_transit_blocks(DAY)
        self.assertEqual(inserted, [])
        self.assertEqual(self.plugin.db.saves, 0)
        self.assertEqual(self.state.schedule[DAY], rows)

    def test_repair_shifts_following_blocks(self):
        rows = self._bad_pair() + [
            {"start": "16:00", "end": "17:00", "activity": "在家休息",
             "place": "家", "category": "rest"}]
        _seed(self.state, rows)
        self.state._repair_transit_blocks(DAY)
        persisted = self.state.schedule[DAY]
        self.assertEqual(len(persisted), 4, persisted)
        self.assertTrue(_no_overlap(persisted), persisted)
        ordered = sorted(persisted, key=lambda row: _minute(row.get("start")))
        self.assertEqual([row["start"] for row in ordered],
                         [row["start"] for row in ordered])
        durations = [_minute(row["end"]) - _minute(row["start"]) for row in ordered]
        self.assertIn(19, durations)
        self.assertIn(60, durations)

    def test_read_path_is_pure_and_maintenance_repairs(self):
        """P2-6：读接口纯读（不写库）；修复拆到显式维护任务 maintain_schedule_day。"""
        _seed(self.state, self._bad_pair())
        self.state.schedule_tree(DAY)
        self.assertEqual(self.plugin.db.saves, 0)
        self.assertEqual(len(self.plugin.db.get_schedule(DAY)), 2)
        self.state.maintain_schedule_day(DAY)
        persisted = self.plugin.db.get_schedule(DAY)
        self.assertEqual(len(persisted), 3, persisted)
        self.assertEqual(
            len([row for row in persisted if row.get("source") == "transit_block"]), 1)

    def test_repair_keeps_future_day_untouched(self):
        tomorrow = _day(1)
        rows = self._bad_pair()
        self.state.schedule[tomorrow] = copy.deepcopy(rows)
        self.plugin.db.schedules[tomorrow] = copy.deepcopy(rows)
        self.plugin.db.saves = 0
        self.assertEqual(self.state._repair_transit_blocks(tomorrow), [])
        self.assertEqual(self.plugin.db.schedules[tomorrow], rows)
        self.assertEqual(self.plugin.db.saves, 0)

    def test_repaired_transit_drives_current_activity_and_guard(self):
        frozen = FixedClock(today_at(13, 30))
        frozen.start()
        self.addCleanup(frozen.stop)
        now_min = minutes_now()
        start = tr._minute_text(max(0, now_min - 70))
        prev_end = tr._minute_text(max(0, now_min - 10))
        next_end = tr._minute_text(min(24 * 60, now_min + 10))
        self.plugin.db.kv["current_action"] = {
            "action": "在医院排队看诊", "place": "医院", "zone": "hospital",
            "category": "health", "at": 1, "until": 10 ** 12, "hour": 13,
        }
        _seed(self.state, [
            {"start": start, "end": prev_end, "activity": "在医院排队看诊",
             "place": "医院", "category": "health", "happened": True},
            {"start": prev_end, "end": next_end,
             "activity": "从医院回来，在沙发上瘫着",
             "place": "家", "category": "rest"}])
        self.state._repair_transit_blocks(DAY)
        current = self.state.current_activity()
        self.assertEqual(current.get("place"), "路上")
        self.assertEqual(current.get("zone"), "road")
        text = tr.guard_terminal(self.plugin, "我到家了，瘫在沙发上，好舒服")
        self.assertNotIn("到家", text)
        self.assertNotIn("沙发上", text)


# ---------- 收尾：LLM 工具入口也要过目的地锁 ----------


class ToolGuardTests(unittest.TestCase):
    def _tool_plugin(self):
        plugin, state, _info = _trip_plugin("traveling")
        now_min = minutes_now()
        start = tr._minute_text(max(0, now_min - 30))
        end = tr._minute_text(min(24 * 60, now_min + 30))
        row = {"start": start, "end": end, "activity": "在景区排队",
               "place": "景区", "category": "play", "happened": True}
        state.schedule[DAY] = [dict(row)]
        plugin.db.schedules[DAY] = [dict(row)]
        plugin.db.saves = 0
        state.current_activity = lambda: {"start": start, "activity": "在景区排队",
                                          "place": "景区", "zone": "scenic",
                                          "category": "play"}
        return plugin

    def test_update_schedule_new_event_blocked_after_departure(self):
        import asyncio
        import json
        from amo_schedule_tools import make_update_schedule_tool
        plugin = self._tool_plugin()
        result = json.loads(asyncio.run(make_update_schedule_tool(plugin)(
            new_event="改去大理", reason="改计划")))
        self.assertFalse(result.get("ok"), result)
        self.assertTrue(result.get("blocked"), result)
        self.assertIn("票都买好了", str(result.get("context") or ""))
        self.assertEqual(plugin.db.saves, 0, "被拦就不能落库")
        self.assertEqual(trip.status(plugin)["city"], "杭州")

    def test_continue_schedule_blocked_after_departure(self):
        import asyncio
        import json
        from amo_schedule_tools import make_continue_schedule_tool
        plugin = self._tool_plugin()
        result = json.loads(asyncio.run(make_continue_schedule_tool(plugin)(
            reason="改去大理")))
        self.assertFalse(result.get("ok"), result)
        self.assertTrue(result.get("blocked"), result)
        self.assertEqual(trip.status(plugin)["phase"], "traveling")


if __name__ == "__main__":
    unittest.main()
