# -*- coding: utf-8 -*-
"""Phase 8：旅游拆分（state/cost/schedule/report + 门面）回归测试。

钉住三件事：
1) 四个模块各自的纯函数边界：金额口径（分→元、区间、幂等键/流水文案）、天数、
   日程块覆盖、待办文案、记忆文本；
2) 门面 amo_trip 的旧 API 快照（名字 + 签名 + 同一个函数对象），保证
   from amo_trip import X 的所有调用方照常工作；
3) 依赖方向：state/cost/schedule/report 不 import 门面，且只允许单向依赖。

反向验证（改回去就红）：
- 门面漏 re-export 一个函数 → FacadeTests.test_old_api_roster_present 红；
- split 后金额口径改成一位小数（_yuan 的 "%.2f" 改 "%.1f"）→
  CostBoundaryTests.test_yuan_keeps_two_decimals_text 红；
- schedule 的旅行日时间窗少一块（24h 覆盖少一块）→
  ScheduleBoundaryTests.test_travel_day_windows_cover_24h 红。
"""
from __future__ import annotations

import ast
import inspect
import os
import re
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import clock  # noqa: E402

import amo_trip as trip  # noqa: E402
import amo_trip_cost as cost  # noqa: E402
import amo_trip_report as report  # noqa: E402
import amo_trip_schedule as schedule  # noqa: E402
import amo_trip_state as state  # noqa: E402

DAY = trip._now_dt().date().isoformat()

ROSTER = (
    "logger", "KEY", "PHASES", "LOCKED_PHASES", "MULTIPLIER_KINDS",
    "EXEMPT_CATEGORIES", "SPEND_RANGES", "ASSASSIN_PROB", "ASSASSIN_RANGE",
    "MEMORY_IMPORTANCE", "MEMORY_TAG", "LEGACY_KEYS", "TRAVEL_LOG_KEY",
    "TRIP_DECIDED_PREFIX", "COOLDOWN_KEY", "COUNT_KEY", "MIN_LONG_HOLIDAY",
    "RETURN_COMMAND_WORDS", "REST_DAY_START", "REST_DAY_END", "REST_DAY_ACTIVITY",
    "TRIP_PLAN_DAYS_MAX", "TRAVEL_EVENT_POOL", "TRAVEL_DAY_WINDOWS",
    "TRIP_PLAN_BLOCKS", "TRIP_COST_KINDS", "TRIP_COST_RANGES", "TRIP_COST_CATEGORY",
    "TRAVEL_DAY_START_MIN", "cfg", "enabled", "_load_trip", "load", "save",
    "_migrate_legacy", "_shift", "candidates", "new_trip", "return_estimate",
    "budget_precheck", "silence_ok", "decide", "_days_between", "CHANGE_WORDS",
    "RETURN_WORDS", "DEST_WORDS", "guard_change", "is_return_command",
    "early_return", "price", "spend", "range_amount", "assassin", "stranded",
    "_yuan", "memory_line", "memory_payload", "_minute_of_text", "_text_of_minute",
    "day_kind", "travel_day_blocks", "travel_event_for_hour", "rest_day_rows",
    "trip_todo_items", "write_todos", "approve", "trip_manual_start", "cancel",
    "status", "finish", "LEG_SOURCE", "LEG_PACK_MIN", "LEG_STATION_MIN",
    "LEG_RIDE_MIN", "LEG_TOTAL_MAX", "RETURN_START_MINUTE", "PENDING_PREFIX",
    "DONE_PREFIX", "ARCHIVED_PREFIX", "MEMORY_DONE_PREFIX", "FACT_DONE_PREFIX",
    "STRANDED_LINE", "_flags_set", "_now_dt", "_kv_get", "_kv_set", "_cfg_obj",
    "_cfg_get", "_cfg_set", "_save_config", "_wallet_balance", "_wallet_has_idem",
    "_trip_idem", "_refresh_weather", "active_city", "_resolve_weather_city",
    "switch_weather", "restore_weather", "_row_span", "_day_rows", "_commit_day",
    "_truncate_from", "_leg_rows", "travel_day_legs", "_start_outbound",
    "_should_return", "begin_return", "sync", "sync_async", "_weather_async",
    "_write_rest_day", "_archive_trip", "_memory_uid", "_write_trip_memory",
    "_write_trip_fact", "finalize_async", "stranded_line", "check_stranded",
    "shorten", "wait_for_funds", "travel_spend", "_wallet_debit", "_record_spent",
    "_refund", "_trip_cost", "_cost_category", "cost_idem", "cost_reason",
    "_settle_days", "settle_due",
)

SIGNATURES = {
    "cfg": "(plugin, name, default)",
    "enabled": "(plugin)",
    "_load_trip": "(plugin)",
    "load": "(plugin)",
    "save": "(plugin, trip)",
    "_migrate_legacy": "(plugin)",
    "_shift": "(day, delta)",
    "candidates": "(plugin, limit=4)",
    "new_trip": "(city, start_day, days, budget_cap=0, now_ts=0)",
    "return_estimate": "(plugin)",
    "budget_precheck": "(plugin, balance, nights)",
    "silence_ok": "(plugin)",
    "decide": "(plugin, today, now, balance, long_holiday=False, low_mood=False, rng=None)",
    "_days_between": "(a, b)",
    "guard_change": "(plugin, text)",
    "is_return_command": "(plugin, text)",
    "early_return": "(plugin, now_day=None, now=None)",
    "price": "(plugin, kind, base)",
    "spend": "(plugin, trip, amount, reason, idem, category='', balance=None)",
    "range_amount": "(kind, rng=None)",
    "assassin": "(plugin, trip, rng=None, balance=None)",
    "stranded": "(plugin, balance)",
    "_yuan": "(cents)",
    "memory_line": "(trip)",
    "memory_payload": "(trip)",
    "_minute_of_text": "(value)",
    "_text_of_minute": "(value)",
    "day_kind": "(plugin, day)",
    "travel_day_blocks": "(plugin, day)",
    "travel_event_for_hour": "(plugin, day, hour)",
    "rest_day_rows": "(plugin, day, existing=None)",
    "trip_todo_items": "(plugin, trip)",
    "write_todos": "(plugin, trip)",
    "approve": "(plugin, trip)",
    "trip_manual_start": "(plugin, city='', days=None, force=False, now_day=None, balance=None, now=None)",
    "cancel": "(plugin)",
    "status": "(plugin)",
    "finish": "(plugin, trip, now_day, mood='还不错')",
    "_flags_set": "(trip, **changes)",
    "_now_dt": "(now=None)",
    "_kv_get": "(plugin, key, default=None)",
    "_kv_set": "(plugin, key, value)",
    "_cfg_obj": "(plugin)",
    "_cfg_get": "(plugin, key, default='')",
    "_cfg_set": "(plugin, key, value)",
    "_save_config": "(plugin, obj=None)",
    "_wallet_balance": "(plugin)",
    "_wallet_has_idem": "(wallet, idem)",
    "_trip_idem": "(trip_id, idem)",
    "_refresh_weather": "(plugin)",
    "active_city": "(plugin)",
    "_resolve_weather_city": "(city)",
    "switch_weather": "(plugin, trip)",
    "restore_weather": "(plugin, trip)",
    "_row_span": "(row)",
    "_day_rows": "(plugin, day)",
    "_commit_day": "(plugin, day, rows, old_items, source, reason)",
    "_truncate_from": "(rows, start_min)",
    "_leg_rows": "(plugin, trip, day, start_min, kind)",
    "travel_day_legs": "(rows)",
    "_start_outbound": "(plugin, trip, now=None, day='')",
    "_should_return": "(plugin, trip, today, now_min)",
    "begin_return": "(plugin, trip, now=None, reason='', start_min=None)",
    "sync": "(plugin, now=None)",
    "sync_async": "(plugin, now=None)",
    "_weather_async": "(plugin, trip)",
    "_write_rest_day": "(plugin, trip, day)",
    "_archive_trip": "(plugin, trip, arrival, mood)",
    "_memory_uid": "(plugin)",
    "_write_trip_memory": "(plugin, trip)",
    "_write_trip_fact": "(plugin, trip)",
    "finalize_async": "(plugin, trip=None)",
    "stranded_line": "(plugin)",
    "check_stranded": "(plugin, balance=None, trip=None)",
    "shorten": "(plugin, now=None)",
    "wait_for_funds": "(plugin)",
    "travel_spend": "(plugin, trip, amount_cents, reason, idem, category='', sub_category='', item_name='', trigger_reason='', balance=None)",
    "_wallet_debit": "(wallet, value, reason, idem, category='', sub_category='', item_name='', trigger_reason='')",
    "_record_spent": "(plugin, trip, value, idem, duplicate=False)",
    "_refund": "(plugin, value, idem)",
    "_trip_cost": "(kind, trip_id, day, activity, start_text)",
    "_cost_category": "(kind, activity)",
    "cost_idem": "(kind, day, trip, row)",
    "cost_reason": "(trip, kind, day, row)",
    "_settle_days": "(trip, today)",
    "settle_due": "(plugin, now=None)",
}

class _DB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}
        self.saves = 0

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return [dict(row) for row in (self.schedules.get(str(day)) or [])]

    def save_schedule(self, day, items):
        self.saves += 1
        self.schedules[str(day)] = [dict(row) for row in items]


class _Wallet:
    def __init__(self, balance=500000):
        self.cents = int(balance)

    def balance(self):
        return self.cents


class _State:
    def __init__(self):
        self.schedule = {}

    def scenic_spots(self):
        return [{"city": "三亚", "name": "亚龙湾"}, {"city": "杭州", "name": "西湖"}]

    def add_mood(self, delta):
        return None


class _Plugin:
    def __init__(self, conf=None):
        self.db = _DB()
        self.wallet = _Wallet()
        self.state = _State()
        self.config = {"travel_enabled": True, "travel_price_multiplier": 1.6,
                       "travel_return_cost": 800}
        if conf:
            self.config.update(conf)

    def cfg_int(self, name, default=0):
        return int(self.config.get(name, default))

    def cfg_float(self, name, default=0.0):
        return float(self.config.get(name, default))

    def cfg_bool(self, name, default=False):
        return bool(self.config.get(name, default))

    def cfg_str(self, name, default=""):
        return str(self.config.get(name, default))

    def today(self):
        return DAY


def _traveling(plugin, days=3, start=None):
    info = trip.new_trip("三亚", start or DAY, days)
    info["phase"] = "traveling"
    trip.save(plugin, info)
    return info


class _FixedRandom:
    def __init__(self):
        self.bounds = None

    def randint(self, low, high):
        self.bounds = (low, high)
        return low


class StateBoundaryTests(unittest.TestCase):
    def test_shift_and_days_between(self):
        self.assertEqual(state._shift("2026-03-01", 2), "2026-03-03")
        self.assertEqual(state._shift("2026-03-01", -1), "2026-02-28")
        self.assertEqual(state._shift("", 2), "")
        self.assertEqual(state._days_between("2026-03-01", "2026-03-04"), 3)
        # 解析失败一律给大数：宁可当成「很久以前」，也不误判成刚去过。
        self.assertEqual(state._days_between("坏日期", "2026-03-04"), 10 ** 6)

    def test_minute_text_roundtrip(self):
        self.assertEqual(state._minute_of_text("07:30"), 450)
        self.assertEqual(state._minute_of_text("7:30"), 450)
        self.assertIsNone(state._minute_of_text(""))
        self.assertIsNone(state._minute_of_text(None))
        self.assertEqual(state._text_of_minute(450), "07:30")
        self.assertEqual(state._text_of_minute(0), "00:00")
        # 跨午夜切片用 24:00（不许出现 24:00 之后的 start）
        self.assertEqual(state._text_of_minute(24 * 60), "24:00")
        self.assertEqual(state._text_of_minute(24 * 60 + 30), "24:00")

    def test_new_trip_shape_and_return_day(self):
        info = trip.new_trip("三亚", "2026-03-01", 3, budget_cap=5000, now_ts=42)
        self.assertEqual(info["id"], "trip-20260301-三亚")
        self.assertEqual(info["phase"], "approved")
        self.assertEqual(info["start_day"], "2026-03-01")
        self.assertEqual(info["return_day"], "2026-03-03")
        self.assertEqual((info["days"], info["budget_cap"], info["spent"]), (3, 5000, 0))
        self.assertEqual(info["flags"], {})
        self.assertEqual(info["created_at"], 42)

    def test_settle_days_is_today_and_yesterday(self):
        # 天数口径：只结今天 + 昨天；昨天早于出发日就跳过。
        self.assertEqual(cost._settle_days({"start_day": "2026-03-01"}, "2026-03-03"),
                         ["2026-03-03", "2026-03-02"])
        self.assertEqual(cost._settle_days({"start_day": "2026-03-01"}, "2026-03-01"),
                         ["2026-03-01"])
        self.assertEqual(cost._settle_days({}, "2026-03-05"),
                         ["2026-03-05", "2026-03-04"])

    def test_load_tick_is_registered_and_advances_phase(self):
        plugin = _Plugin()
        info = trip.new_trip("三亚", DAY, 3)
        info["phase"] = "outbound"
        info["outbound_ts"] = int(clock.now_ts()) - 60
        trip.save(plugin, info)
        loaded = trip.load(plugin)
        self.assertEqual(loaded["phase"], "traveling", loaded)
        # 门面注册的推进钩子必须挂上（拆分前这两步直接写在 load 里）
        self.assertIsNotNone(state._TICK)
        self.assertIs(trip.load, state.load)


class CostBoundaryTests(unittest.TestCase):
    def test_price_only_multiplies_daily_kinds(self):
        plugin = _Plugin()
        self.assertEqual(trip.price(plugin, "food", 100), 160)
        self.assertEqual(trip.price(plugin, "transport", 50), 80)
        self.assertEqual(trip.price(plugin, "daily", 1000), 1600)
        # 住宿/大餐/门票按原价，不乘 1.6
        self.assertEqual(trip.price(plugin, "hotel", 30000), 30000)
        self.assertEqual(trip.price(plugin, "feast", 20000), 20000)

    def test_range_amount_uses_spend_ranges(self):
        rng = _FixedRandom()
        self.assertEqual(trip.range_amount("hotel", rng), 300)
        self.assertEqual(rng.bounds, (300, 1000))
        unknown = _FixedRandom()
        trip.range_amount("nothing", unknown)
        self.assertEqual(unknown.bounds, (100, 300))

    def test_yuan_keeps_two_decimals_text(self):
        # 分 → 元的显示口径：两位小数、去掉尾随 0、0 显示成 "0"
        self.assertEqual(cost._yuan(12345), "123.45")
        self.assertEqual(cost._yuan(2580), "25.8")
        self.assertEqual(cost._yuan(258000), "2580")
        self.assertEqual(cost._yuan(100), "1")
        self.assertEqual(cost._yuan(0), "0")
        self.assertEqual(cost._yuan(None), "0")

    def test_trip_cost_cents_in_range_and_seeded(self):
        args = ("hotel", "trip-x", DAY, "睡到自然醒", "08:00")
        first = trip._trip_cost(*args)
        self.assertEqual(first, trip._trip_cost(*args), "价签必须按稳定种子定死")
        low, high = trip.TRIP_COST_RANGES["hotel"]
        self.assertTrue(low <= first["base_cents"] <= high, first)
        self.assertEqual(first["category"], "hotel")
        self.assertEqual(
            trip._trip_cost("meal", "trip-x", DAY, "吃当地小吃", "12:00")["category"], "feast")
        self.assertEqual(
            trip._trip_cost("ticket", "trip-x", DAY, "打卡当地景点", "14:00")["category"],
            "ticket")
        self.assertEqual(trip._cost_category("daily", "打车被绕路"), "transport")
        self.assertEqual(trip._cost_category("daily", "吃当地小吃"), "food")
        self.assertEqual(trip._cost_category("daily", "回酒店躺平"), "daily")

    def test_cost_idem_and_reason_text(self):
        info = trip.new_trip("三亚", DAY, 3)
        row = {"start": "08:00", "activity": "睡到自然醒"}
        self.assertEqual(trip.cost_idem("hotel", DAY, info, row), "day1:hotel")
        self.assertEqual(trip.cost_idem("daily", DAY, info, row), "day1:daily:08")
        self.assertEqual(trip.cost_idem("hotel", state._shift(DAY, 2), info, row), "day3:hotel")
        self.assertEqual(trip.cost_reason(info, "hotel", DAY, row), "三亚住宿（第1晚）")
        self.assertEqual(trip.cost_reason(info, "meal", DAY, row), "海鲜大餐")
        self.assertEqual(trip.cost_reason(info, "ticket", DAY, {"activity": "买纪念品"}), "纪念品")
        self.assertEqual(trip.cost_reason(info, "ticket", DAY, {"activity": "打卡当地景点"}),
                         "景区门票")
        self.assertEqual(trip.cost_reason(info, "daily", DAY, {"activity": "打车被绕路"}), "打车")
        self.assertEqual(trip.cost_reason(info, "daily", DAY, {"activity": "吃当地小吃"}),
                         "当地小吃")
        self.assertEqual(trip.cost_reason(info, "daily", DAY, {"activity": "发呆"}), "旅游零花")


class ScheduleBoundaryTests(unittest.TestCase):
    def test_travel_day_windows_cover_24h(self):
        windows = schedule.TRAVEL_DAY_WINDOWS
        self.assertEqual(len(windows), 6, "旅行日池的时间窗是 6 块，少一块就是 24h 覆盖缺口")
        busy = 0
        prev_end = -1
        for start, end in windows:
            self.assertGreaterEqual(start, schedule.TRAVEL_DAY_START_MIN)
            self.assertGreater(end, start)
            self.assertLessEqual(end, 21 * 60 + 30)
            self.assertGreaterEqual(start, prev_end, "时间窗不许重叠")
            prev_end = end
            busy += end - start
        self.assertEqual(busy, 12 * 60)
        self.assertEqual(max(end for _start, end in windows), 21 * 60 + 30)

    def test_travel_day_blocks_stay_inside_windows(self):
        plugin = _Plugin()
        _traveling(plugin, days=4)
        for offset in range(0, 4):
            day = state._shift(DAY, offset)
            rows = trip.travel_day_blocks(plugin, day)
            self.assertTrue(3 <= len(rows) <= 5, rows)
            spans = [(state._minute_of_text(row["start"]), state._minute_of_text(row["end"]))
                     for row in rows]
            self.assertEqual(spans, sorted(spans), "旅行日块必须按时间有序且不重叠")
            for span in spans:
                self.assertIn(span, set(schedule.TRAVEL_DAY_WINDOWS), rows)
            kinds = [row["cost"]["kind"] for row in rows]
            self.assertEqual(kinds.count("hotel"), 1, rows)
            for row in rows:
                low, high = cost.TRIP_COST_RANGES[row["cost"]["kind"]]
                self.assertTrue(low <= row["cost"]["base_cents"] <= high, row)

    def test_travel_event_for_hour_night_is_hotel(self):
        plugin = _Plugin()
        _traveling(plugin)
        night = trip.travel_event_for_hour(plugin, DAY, 23)
        self.assertEqual(night["zone"], "hotel")
        self.assertEqual(night["cost_cents"], 0, "到点动作不许预扣钱")
        day = trip.travel_event_for_hour(plugin, DAY, 10)
        self.assertEqual(day["cost_cents"], 0)
        self.assertIn(day["cost"]["kind"], trip.TRIP_COST_KINDS)
        pool = {event[0] for event in trip.TRAVEL_EVENT_POOL}
        self.assertIn(day["action"], pool, "只许从 8 条事件池里取，不能出上班/洗衣/做饭")

    def test_rest_day_rows_cover_8_to_20(self):
        plugin = _Plugin()
        info = trip.new_trip("三亚", state._shift(DAY, -3), 3)
        info["phase"] = "rest"
        trip.save(plugin, info)
        existing = [
            {"start": "07:00", "end": "09:00", "activity": "早饭"},
            {"start": "12:00", "end": "13:00", "activity": "午饭"},
            {"start": "19:00", "end": "21:00", "activity": "看剧"},
        ]
        rows, changed = trip.rest_day_rows(plugin, DAY, existing)
        self.assertTrue(changed)
        spans = [(state._minute_of_text(row["start"]), state._minute_of_text(row["end"]))
                 for row in rows]
        self.assertIn((schedule.REST_DAY_START, schedule.REST_DAY_END), spans)
        self.assertIn((7 * 60, 8 * 60), spans, "跨边界的块要截断，不许被整块清掉")
        self.assertIn((20 * 60, 21 * 60), spans)
        self.assertNotIn((12 * 60, 13 * 60), spans, "休息时段里的旧活动块要清掉")
        self.assertEqual([start for start, _end in spans],
                         sorted(start for start, _end in spans))

    def test_trip_todo_items_text_and_anchor(self):
        plugin = _Plugin()
        info = trip.new_trip("三亚", DAY, 3)
        plan = trip.trip_todo_items(plugin, info)
        self.assertEqual(sorted(plan), [state._shift(DAY, 1), state._shift(DAY, 2)])
        anchor = "trip:%s" % info["id"]
        for day, items in plan.items():
            self.assertEqual(len(items), len(schedule.TRIP_PLAN_BLOCKS), day)
            for item, block in zip(items, schedule.TRIP_PLAN_BLOCKS):
                self.assertEqual((item["start"], item["end"], item["activity"]), block)
                self.assertEqual(item["source"], anchor)
                self.assertEqual(item["place"], "三亚")
                self.assertEqual(item["cost_cents"], 0)
        # 只有一天一夜的短途不写待办
        self.assertEqual(trip.trip_todo_items(plugin, trip.new_trip("三亚", DAY, 1)), {})


class ReportBoundaryTests(unittest.TestCase):
    def test_memory_line_text(self):
        self.assertEqual(report.memory_line({"city": "三亚", "days": 3, "spent": 123456,
                                             "mood": "开心"}),
                         "去三亚玩了3天，花了1234.56元，开心。")
        self.assertEqual(report.memory_line({"city": "三亚", "days": 2, "spent": 0}),
                         "去三亚玩了2天，花了0元，还不错。")
        self.assertEqual(report.memory_line({}), "")

    def test_memory_payload_shape(self):
        payload = report.memory_payload({"id": "trip-x", "city": "杭州", "days": 2,
                                         "spent": 258000, "mood": "还不错"})
        self.assertEqual(payload["content"], "去杭州玩了2天，花了2580元，还不错。")
        self.assertEqual(payload["importance"], 0.9)
        self.assertEqual(payload["tag"], "#旅游")
        self.assertEqual(payload["idem"], "trip:trip-x:memory")


class FacadeTests(unittest.TestCase):
    def test_old_api_roster_present(self):
        missing = [name for name in ROSTER if not hasattr(trip, name)]
        self.assertEqual(missing, [], "门面漏 re-export：%s" % missing)
        exported = set(trip.__all__)
        self.assertTrue(set(ROSTER) <= exported, sorted(set(ROSTER) - exported))

    def test_signatures_unchanged(self):
        changed = {}
        for name, expected in SIGNATURES.items():
            actual = str(inspect.signature(getattr(trip, name)))
            if actual != expected:
                changed[name] = (expected, actual)
        self.assertEqual(changed, {})

    def test_reexports_are_same_objects(self):
        owners = {}
        for module in (state, cost, schedule, report):
            for name, value in vars(module).items():
                if name.startswith("__"):
                    continue
                owners.setdefault(name, module)
        checked = 0
        for name in ROSTER:
            if name == "logger":
                continue
            owner = owners.get(name)
            if owner is None:
                continue
            self.assertIs(getattr(trip, name), getattr(owner, name), name)
            checked += 1
        self.assertGreaterEqual(checked, 100)

    def test_dependency_direction(self):
        allowed = {
            "amo_trip_state.py": set(),
            "amo_trip_cost.py": {"amo_trip_state"},
            "amo_trip_schedule.py": {"amo_trip_state", "amo_trip_cost"},
            "amo_trip_report.py": {"amo_trip_state", "amo_trip_cost", "amo_trip_schedule"},
            "amo_trip.py": {"amo_trip_state", "amo_trip_cost", "amo_trip_schedule",
                            "amo_trip_report"},
        }
        for filename, expected in allowed.items():
            with open(os.path.join(ROOT, filename), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=filename)
            deps = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = (node.module or "").lstrip(".")
                    if module.startswith("amo_trip"):
                        deps.add(module)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("amo_trip"):
                            deps.add(alias.name)
            self.assertEqual(deps - expected, set(), filename)

    def test_split_modules_never_import_the_facade(self):
        pattern = re.compile(r"^\s*(?:from|import)\s+\.?amo_trip(?![_\w])", re.M)
        for filename in ("amo_trip_state.py", "amo_trip_cost.py",
                         "amo_trip_schedule.py", "amo_trip_report.py"):
            with open(os.path.join(ROOT, filename), encoding="utf-8") as fh:
                source = fh.read()
            self.assertIsNone(pattern.search(source), "不得反向 import 门面：" + filename)

    def test_old_callers_import_ok(self):
        import amo_api_trip
        import amo_state_jit
        import schedule_engine
        for module in (amo_api_trip, amo_state_jit, schedule_engine):
            self.assertIsNotNone(module, "旧调用方模块必须还能 import")
        for name in ("status", "trip_manual_start", "cancel", "sync", "sync_async",
                     "settle_due", "day_kind", "travel_day_blocks", "travel_day_legs"):
            self.assertTrue(callable(getattr(trip, name)), name)


if __name__ == "__main__":
    unittest.main()
