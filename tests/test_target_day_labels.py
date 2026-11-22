# -*- coding: utf-8 -*-
"""task-22：target_day 单一来源（日期标签 / 补写窗口 / JIT 星期 / 素材隔离）。

钉住审计里的四个问题：
1) jit_plan 不再硬编码「周一早上」，星期由 target_day/weekday 决定；
2) 日期标签（星期/节气/节日）只由 amo_shared_time.day_label(target_day) 生成：
   11-21 是「星期六 周末」、没有小雪；11-22 才是「星期日 小雪」；
3) 自动补写只允许 T+1（目标日=昨天），更早的缺日只记 diary_skipped，不生成正文；
4) 日记素材按 target_day 隔离：聊天只取目标日 00:00-24:00，越界行丢弃；
   大日记 prompt / 正文的日期标签也按 target_day 归一。
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import unittest
from datetime import date, datetime as dt
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_schedule  # noqa: E402
import amo_shared_time  # noqa: E402
from amo_schedule import ScheduleMixin  # noqa: E402
from schedule_engine import jit_plan  # noqa: E402


class _KV:
    """最小 kv 桩：只给日期/补写窗口逻辑用。"""

    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class _SnapshotState:
    def __init__(self, activity=None):
        self._activity = dict(activity or {})

    def snapshot(self):
        return {"activity": dict(self._activity)}


class DayLabelTests(unittest.TestCase):
    """日期标签唯一来源：同一 target_day 的星期/节气/节日不会互相串。"""

    def test_1121_is_saturday_without_xiaoxue(self):
        label = amo_shared_time.day_label("2026-11-21")
        self.assertEqual(label["weekday_full"], "星期六")
        self.assertEqual(label["solar_term"], "")
        self.assertNotIn("小雪", label["text"])
        self.assertIn("2026年11月21日 星期六", label["text"])

    def test_1122_is_sunday_with_xiaoxue(self):
        label = amo_shared_time.day_label("2026-11-22")
        self.assertEqual(label["weekday_full"], "星期日")
        self.assertEqual(label["solar_term"], "小雪")
        self.assertIn("2026年11月22日 星期日", label["text"])
        self.assertIn("小雪", label["text"])

    def test_day_label_accepts_date_and_datetime(self):
        self.assertEqual(amo_shared_time.day_label(date(2026, 11, 21))["text"],
                         amo_shared_time.day_label("2026-11-21")["text"])
        self.assertEqual(amo_shared_time.day_label(dt(2026, 11, 21, 23, 59))["text"],
                         amo_shared_time.day_label("2026-11-21")["text"])

    def test_weekday_helpers_accept_target_day(self):
        self.assertEqual(amo_shared_time.weekday_cn("2026-11-21"), "六")
        self.assertEqual(amo_shared_time.weekday_name("2026-11-22"), "星期日")


class JitWeekdayTests(unittest.TestCase):
    """JIT 日记的星期来自 target_day/weekday，不再写死「周一」。"""

    def test_weekday_labels_are_dynamic(self):
        # 工作日 07:00 是唯一带星期文案的 JIT 分支：星期必须跟着 weekday 变。
        for index, expected in enumerate(("周一", "周二", "周三", "周四", "周五")):
            plan = jit_plan(7, 90, 5000, weekday=index)
            self.assertIn(expected, plan["diary"], index)
            self.assertIn("出门", plan["action"], index)
        for index in (5, 6):
            plan = jit_plan(7, 90, 5000, weekday=index)
            self.assertNotIn("周一", plan["diary"], index)

    def test_target_day_drives_weekday(self):
        # 2026-11-18 是周三：只给 target_day、不给 weekday 也要写「周三」，不能是「周一」。
        plan = jit_plan(7, 90, 5000, target_day="2026-11-18")
        self.assertIn("周三", plan["diary"])
        self.assertNotIn("周一", plan["diary"])
        plan = jit_plan(7, 90, 5000, target_day="2026-11-19")
        self.assertIn("周四", plan["diary"])

    def test_unknown_target_day_does_not_fake_a_weekday(self):
        plan = jit_plan(7, 90, 5000, rest_day=False)
        self.assertNotIn("周", plan["diary"])
        self.assertIn("出门", plan["diary"])


class DiaryDateLabelTests(unittest.TestCase):
    """大日记 prompt / 正文的日期标签只按 target_day 生成。"""

    def _plugin(self):
        class Plugin(ScheduleMixin):
            def __init__(self):
                self.db = _KV()
                self.state = _SnapshotState()

        return Plugin()

    def test_prompt_label_uses_target_day_not_generation_day(self):
        plugin = self._plugin()
        with mock.patch.object(amo_schedule, "_now",
                               return_value=dt(2026, 11, 22, 0, 0, 55)):
            text = amo_schedule._diary_time_rules(plugin, date(2026, 11, 21))
        self.assertIn("日期标签", text)
        self.assertIn("2026年11月21日 星期六", text)
        self.assertNotIn("小雪", text)
        with mock.patch.object(amo_schedule, "_now",
                               return_value=dt(2026, 11, 22, 0, 0, 55)):
            text22 = amo_schedule._diary_time_rules(plugin, date(2026, 11, 22))
        self.assertIn("2026年11月22日 星期日", text22)
        self.assertIn("小雪", text22)

    def test_generated_body_label_is_normalized_to_target_day(self):
        # 模型把第二天的小雪写到 11-21 的标题里：落库前必须改回 11-21 的唯一标签。
        bad = "2026年11月21日 星期六 小雪 睡到自然醒，拉开窗帘外面阴着。"
        fixed = amo_schedule._normalize_diary_date_label(bad, date(2026, 11, 21))
        self.assertTrue(fixed.startswith("2026年11月21日 星期六"))
        self.assertIn("睡到自然醒", fixed)
        self.assertNotIn("小雪", fixed)
        # 正文里没有日期头就不动（避免误删正文里的「小雪」天气词）。
        body = "外面下了点小雪，好冷。"
        self.assertEqual(amo_schedule._normalize_diary_date_label(body, date(2026, 11, 21)),
                         body)

    def test_normalize_keeps_body_and_handles_comma_separators(self):
        text = "2026年11月22日，星期日，小雪，今天好冷。"
        fixed = amo_schedule._normalize_diary_date_label(text, date(2026, 11, 22))
        self.assertIn("2026年11月22日 星期日", fixed)
        self.assertIn("今天好冷", fixed)


class BackfillWindowTests(unittest.TestCase):
    """自动补写只允许 T+1；更早缺日只记 skipped。"""

    def _plugin(self):
        class Plugin(ScheduleMixin):
            def __init__(self):
                self.db = _KV()

        return Plugin()

    def test_only_yesterday_is_written_and_older_days_are_skipped(self):
        plugin = self._plugin()
        plugin.db.kv_set("diary_last_day", "2026-11-09")
        now = dt(2026, 11, 18, 10, 0)
        days = plugin._diary_days_to_write(now, 8, 0)
        self.assertEqual([day.isoformat() for day in days], ["2026-11-17"])
        for skipped in ("2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
                        "2026-11-14", "2026-11-15", "2026-11-16"):
            record = plugin.db.kv_get("diary_skipped:" + skipped)
            self.assertEqual(record.get("reason"), "out_of_window", skipped)
            self.assertEqual(record.get("window_days"), 1, skipped)

    def test_skipped_is_recorded_only_once(self):
        plugin = self._plugin()
        plugin.db.kv_set("diary_last_day", "2026-11-14")
        now = dt(2026, 11, 18, 10, 0)
        plugin._diary_days_to_write(now, 8, 0)
        first = dict(plugin.db.kv_get("diary_skipped:2026-11-15"))
        plugin._diary_days_to_write(dt(2026, 11, 18, 11, 0), 8, 0)
        self.assertEqual(plugin.db.kv_get("diary_skipped:2026-11-15"), first)

    def test_stale_day_is_not_rewritten_outside_window(self):
        plugin = self._plugin()
        plugin.db.kv_set("diary_stale:2026-11-12", True)  # 在 _stale_daily_days 的 7 天回看内
        days = plugin._diary_days_to_write(dt(2026, 11, 18, 10, 0), 8, 0)
        # 11-12 超出 T+1：不重写、清掉 stale、留 skipped；窗口内仍只有 11-17。
        self.assertEqual([day.isoformat() for day in days], ["2026-11-17"])
        self.assertFalse(plugin.db.kv_get("diary_stale:2026-11-12", False))
        self.assertEqual(
            (plugin.db.kv_get("diary_skipped:2026-11-12") or {}).get("reason"),
            "stale_out_of_window")


class DiaryMaterialIsolationTests(unittest.TestCase):
    """日记素材按 target_day 隔离：只取目标日 00:00-24:00 的聊天。"""

    class _Mem:
        def __init__(self, rows):
            self.rows = list(rows)
            self.calls = []

        async def messages_between(self, bot_id, start_ts, end_ts, uid="", limit=2000):
            self.calls.append((int(start_ts), int(end_ts)))
            return list(self.rows)

    def _plugin(self, rows):
        mem = self._Mem(rows)

        class Plugin(ScheduleMixin):
            def __init__(self):
                self.mem = mem

            def _data_mem(self):
                return self.mem

            async def _data_call(self, name, *args, **kwargs):
                func = getattr(self.mem, name)
                return await func(*args, **kwargs)

            def _data_bot_id(self):
                return "bot"

        return Plugin(), mem

    def test_chat_lines_are_bounded_to_target_day(self):
        day = date(2026, 11, 21)
        day_start = int(datetime.datetime.combine(day, datetime.time.min).timestamp())
        rows = [
            {"role": "user", "content": "11-20 的旧对话", "name": "他",
             "created_at": day_start - 3600},
            {"role": "user", "content": "11-21 上午的对话", "name": "他",
             "created_at": day_start + 12 * 3600},
            {"role": "user", "content": "11-22 的越界对话", "name": "他",
             "created_at": day_start + 86400 + 60},
        ]
        plugin, mem = self._plugin(rows)
        lines = asyncio.run(plugin._diary_chat_lines(day))
        self.assertEqual(mem.calls, [(day_start, day_start + 86400)])
        self.assertTrue(any("11-21 上午的对话" in line for line in lines), lines)
        self.assertFalse(any("11-20 的旧对话" in line for line in lines), lines)
        self.assertFalse(any("11-22 的越界对话" in line for line in lines), lines)


class WeatherAlertTargetDayTests(unittest.TestCase):
    """提醒也只接受 target_day：日期标签同样由 day_label 生成。"""

    class _LLM:
        def __init__(self):
            self.prompts = []

        async def persona(self, umo):
            return ""

        async def chat(self, umo, system, prompt, limit=60):
            self.prompts.append(prompt)
            return "明天有雨，伞别忘了。"

    class _State:
        def prompt_text(self, snap=None):
            return ""

        def snapshot(self):
            return {}

    def _plugin(self):
        class Plugin:
            def __init__(self):
                self.llm = WeatherAlertTargetDayTests._LLM()
                self.state = WeatherAlertTargetDayTests._State()

            def _recent(self, umo, limit):
                return []

            def _clean_weather_alert(self, text):
                return ScheduleMixin._clean_weather_alert(text)

        return Plugin()

    def test_alert_prompt_label_uses_target_day(self):
        plugin = self._plugin()
        text = asyncio.run(ScheduleMixin._weather_alert_text(
            plugin, "u", {"textDay": "小雨", "textNight": "阴", "tempMax": 12},
            "明天有雨", target_day=date(2026, 11, 22)))
        self.assertEqual(text, "明天有雨，伞别忘了。")
        prompt = plugin.llm.prompts[-1]
        self.assertIn("2026年11月22日 星期日", prompt)
        self.assertIn("小雪", prompt)
        self.assertIn("小雨 阴", prompt)

    def test_alert_target_day_defaults_to_tomorrow_and_has_no_wrong_solar_term(self):
        plugin = self._plugin()
        with mock.patch.object(amo_schedule, "_now",
                               return_value=dt(2026, 11, 20, 20, 0)):
            asyncio.run(ScheduleMixin._weather_alert_text(
                plugin, "u", {"textDay": "晴", "tempMax": 20}, "模板句"))
        prompt = plugin.llm.prompts[-1]
        self.assertIn("2026年11月21日 星期六", prompt)
        self.assertNotIn("小雪", prompt)


if __name__ == "__main__":
    unittest.main()
