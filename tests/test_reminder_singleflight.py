# -*- coding: utf-8 -*-
"""提醒单飞/幂等/迟到窗口 P0（2026-11-22）。

线上事实（work/audit/time_facts.md）：
- 11-21 20:00:03/05/06/07 同一句天气提醒判 4 次（4 秒 4 次）；
- 11-20 早间槽位 09:54 才补跑（配置 08:00）；11-22 08:00 接口失败 12 次静默；
- 11-19 的 weather_alert_open 留到 11-22；
- 日程提醒幂等键只有「活动|地点」，不含开始时间。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_proactive as proactive  # noqa: E402
import amo_schedule as sched  # noqa: E402

DAY = "2026-11-18"


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}
        self.versions = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return [dict(row) for row in (self.schedules.get(day) or [])]

    def schedule_version(self, day):
        return self.versions.get(day, 0)


class WeatherState:
    def __init__(self):
        self.daily_items = [
            {"textDay": "小雨", "tempMax": 12, "tempMin": 5},
            {"textDay": "中雨", "tempMax": 9, "tempMin": 3},
        ]
        self.fetch_calls = 0
        self.fail = False

    async def _fetch_qweather_daily(self, key, city, days=3, fresh=False):
        self.fetch_calls += 1
        await asyncio.sleep(0.01)
        if self.fail:
            raise TimeoutError("qweather timeout")
        return list(self.daily_items)

    def snapshot(self):
        return {"activity": {"category": "work"}}


class WeatherHost(sched.ScheduleMixin):
    def __init__(self):
        self.db = FakeDB()
        self.state = WeatherState()
        self.sent = []
        self.config = {"weather_alert_enabled": True, "hefeng_key": "K",
                       "weather_alert_time": "20:00",
                       "weather_alert_morning_time": "08:00"}

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def _weather_alert_umos(self):
        return ["qq:private:u1"]

    async def _send_to_umo(self, umo, text, source=None, kind=""):
        self.sent.append((umo, text))
        return True

    async def _weather_alert_text(self, umo, tomorrow, fallback):
        return fallback


class WeatherAlertTests(unittest.TestCase):
    def setUp(self):
        self.host = WeatherHost()
        self.patches = [
            mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 20, 0)),
            mock.patch.object(sched, "today_str", lambda: DAY),
            mock.patch.object(sched, "city_or_empty", lambda plugin: "贵阳"),
            mock.patch.object(sched, "parse_clock",
                              lambda text, h, m: 20 * 60 if text == "20:00" else 8 * 60),
            mock.patch.object(sched, "evaluate_weather_alert", lambda *a: ""),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()

    def test_concurrent_ticks_fetch_once_and_write_once(self):
        async def both():
            await asyncio.gather(self.host._maybe_weather_alert(),
                                 self.host._maybe_weather_alert())

        asyncio.run(both())
        self.assertEqual(self.host.state.fetch_calls, 1,
                         "4 秒 4 次 = 并发同时通过 check；必须单飞")
        self.assertEqual(self.host.db.kv_get("weather_alert_checked"),
                         DAY + "|evening")
        self.assertEqual(self.host.db.kv_get("weather_alert_status")["status"], "normal")

    def test_late_evening_slot_is_marked_missed_not_sent(self):
        with mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 20, 30)):
            asyncio.run(self.host._maybe_weather_alert())
        self.assertEqual(self.host.sent, [], "迟到 30 分钟不许补发")
        self.assertEqual(self.host.state.fetch_calls, 0, "迟到就不必再请求接口")
        status = self.host.db.kv_get("weather_alert_status")
        self.assertEqual(status["status"], "missed")
        self.assertEqual(status["reason"], "late")
        self.assertEqual(status["delay_minutes"], 30)

    def test_fetch_failure_is_visible(self):
        self.host.state.fail = True
        asyncio.run(self.host._maybe_weather_alert())
        status = self.host.db.kv_get("weather_alert_status")
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["reason"], "TimeoutError")
        self.assertEqual(self.host.db.kv_get("weather_alert_checked"),
                         DAY + "|evening", "失败也要记 checked，避免每 30 秒重试")

    def test_expired_weather_alert_open_is_cleared(self):
        self.host.db.kv_set("weather_alert_open", "2026-11-19")
        self.host.db.kv_set("weather_alert_sent", "2026-11-19")
        self.host._cleanup_weather_state("2026-11-22")
        self.assertEqual(self.host.db.kv_get("weather_alert_open"), "")
        self.assertEqual(self.host.db.kv_get("weather_alert_sent"), "")


def _rows():
    return [{"start": "15:10", "end": "15:50", "activity": "复诊拿药",
             "place": "医院", "zone": "hospital"}]


class ScheduleReminderTests(unittest.TestCase):
    def test_key_contains_start_time(self):
        row = _rows()[0]
        key = proactive._schedule_event_key(row)
        self.assertEqual(key, "复诊拿药|医院|15:10")

    def test_claim_is_single_flight(self):
        db = FakeDB()
        db.schedules[DAY] = _rows()
        now = dt.datetime(2026, 11, 18, 15, 0)
        first = proactive.schedule_reminder_fact(db, now, day=DAY, claim=True)
        second = proactive.schedule_reminder_fact(db, now, day=DAY, claim=True)
        self.assertEqual(first["event"], "复诊拿药")
        self.assertEqual(second, {}, "同一条日程不能被两轮主动决策同时抢到")
        proactive.release_schedule_reminder(db, DAY, first["key"],
                                            first.get("claim_token") or "")
        again = proactive.schedule_reminder_fact(db, now, day=DAY, claim=True)
        self.assertEqual(again["event"], "复诊拿药", "发送失败释放后还能补提醒")

    def test_late_fact_is_not_sent(self):
        db = FakeDB()
        db.schedules[DAY] = _rows()
        fact = proactive.schedule_reminder_fact(db, dt.datetime(2026, 11, 18, 15, 0),
                                                day=DAY)
        self.assertTrue(proactive.schedule_fact_current(
            db, fact, now=dt.datetime(2026, 11, 18, 15, 12)))
        self.assertFalse(proactive.schedule_fact_current(
            db, fact, now=dt.datetime(2026, 11, 18, 15, 30)),
            "日程已开始 20 分钟，不能再发“快到了”")

    def test_reminded_mark_blocks_second_reminder(self):
        db = FakeDB()
        db.schedules[DAY] = _rows()
        now = dt.datetime(2026, 11, 18, 15, 0)
        first = proactive.schedule_reminder_fact(db, now, day=DAY)
        db.kv["schedule_reminded:%s:%s" % (DAY, first["key"])] = True
        self.assertEqual(proactive.schedule_reminder_fact(db, now, day=DAY), {})


class WeatherRepeatGuardTests(unittest.TestCase):
    def _plugin(self, open_day=""):
        plugin = proactive.ProactiveMixin.__new__(proactive.ProactiveMixin)
        plugin.db = FakeDB()
        plugin.db.kv["weather_alert_open"] = open_day
        return plugin

    def test_no_repeat_after_weather_alert_sent_today(self):
        plugin = self._plugin(DAY)
        with mock.patch.object(proactive, "today_str", lambda: DAY):
            self.assertTrue(plugin._weather_repeat_blocked("明天记得带伞，别淋雨"))
            self.assertTrue(plugin._weather_repeat_blocked("今天降温了，多穿点"))
            self.assertFalse(plugin._weather_repeat_blocked("晚上想吃火锅"))

    def test_stale_open_does_not_block(self):
        plugin = self._plugin("2026-11-19")
        with mock.patch.object(proactive, "today_str", lambda: DAY):
            self.assertFalse(plugin._weather_repeat_blocked("明天记得带伞"))


class SwitchStateTests(unittest.TestCase):
    def test_switch_state_is_explicit(self):
        plugin = proactive.ProactiveMixin.__new__(proactive.ProactiveMixin)
        plugin.config = {"proactive_schedule_reminder": False}

        def cfg_bool(key, default=False):
            return bool(plugin.config.get(key, default))

        plugin.cfg_bool = cfg_bool
        self.assertFalse(plugin._schedule_reminder_switch_enabled())
        plugin.config["proactive_schedule_reminder"] = True
        self.assertTrue(plugin._schedule_reminder_switch_enabled())


if __name__ == "__main__":
    unittest.main()
