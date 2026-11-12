# -*- coding: utf-8 -*-
"""HumanState：心情/生理期/健康/天气缓存/日程整理/状态快照。

这些是"她"的内在状态，错了会直接体现在回复里（比如痛经时排满病号日程、
天气永远走模拟、心情不衰减），所以在不联网的前提下把纯逻辑全跑一遍。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_shared import MOOD_BASE, MOOD_KEYS, now_ts, today_str  # noqa: E402


class FakePlugin:
    def __init__(self, tmp, **cfg):
        self.config = {
            "weather_city": "",
            "weather_city_name": "",
            "enable_period_simulate": True,
            "period_cycle_days": 28,
            "period_days": 5,
            "enable_sick_random": True,
            "sick_prob": 0.0,
        }
        self.config.update(cfg)
        self.persona_name = "大肥鱼"
        self.bot_name = "大肥鱼"
        self.db = AmoDB(tmp)
        self.db.init()

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def _reload_config_if_changed(self):
        return None

    def _sync_weather_city_config(self):
        return None


class StateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="amo-state-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.plugin = FakePlugin(self.tmp)
        self.state = HumanState(self.plugin)

    def tearDown(self) -> None:
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def set_start(self, days_ago: int) -> None:
        self.state.period_start = (date.today() - timedelta(days=days_ago)).isoformat()


class MoodTests(StateTestCase):
    def test_initial_mood_is_base(self) -> None:
        for key in MOOD_KEYS:
            self.assertAlmostEqual(self.state.mood[key], MOOD_BASE[key], places=3)

    def test_nudge_clamps_and_persists(self) -> None:
        self.state.nudge(开心=1000, 郁闷=-1000)
        self.assertEqual(self.state.mood["开心"], 100.0)
        self.assertEqual(self.state.mood["郁闷"], 0.0)
        self.assertEqual(self.plugin.db.kv_get("mood")["开心"], 100.0)

    def test_decay_moves_toward_base(self) -> None:
        self.state.mood["开心"] = 90.0
        self.state.mood_ts = now_ts() - 3 * 3600
        self.state.decay_mood()
        self.assertLess(self.state.mood["开心"], 90.0)
        self.assertGreater(self.state.mood["开心"], MOOD_BASE["开心"])

    def test_decay_noop_when_clock_not_moved(self) -> None:
        self.state.mood["开心"] = 90.0
        # 时间戳在未来 → hours <= 0，直接 early return（不靠浮点误差判等）
        self.state.mood_ts = now_ts() + 60
        before = dict(self.state.mood)
        self.state.decay_mood()
        self.assertEqual(before, self.state.mood)

    def test_dominant_and_score(self) -> None:
        self.state.mood = {key: 0.0 for key in MOOD_KEYS}
        self.state.mood["兴奋"] = 99.0
        self.assertEqual(self.state.dominant_mood(), "兴奋")
        self.assertGreater(self.state.mood_score(), 50)
        self.assertIn("兴奋99", self.state.mood_text())

    def test_save_persists_period_and_health(self) -> None:
        self.state.last_daily = "2026-01-01"
        self.state.save()
        self.assertEqual(self.plugin.db.kv_get("last_daily"), "2026-01-01")

    def test_observe_apology_cools_down(self) -> None:
        self.state.mood["生气"] = 70.0
        self.state.observe("对不起，我错了")
        self.assertLess(self.state.mood["生气"], 70.0)

    def test_observe_insult_raises_anger(self) -> None:
        self.state.mood["生气"] = 10.0
        self.state.observe("你这个废物")
        self.assertGreater(self.state.mood["生气"], 10.0)

    def test_observe_sad_and_affection(self) -> None:
        self.state.mood["委屈"] = 10.0
        self.state.observe("我好难过")
        self.assertGreater(self.state.mood["委屈"], 10.0)
        self.state.mood["依恋"] = 10.0
        self.state.observe("抱抱")
        self.assertGreater(self.state.mood["依恋"], 10.0)

    def test_observe_neutral(self) -> None:
        self.state.mood["平静"] = 50.0
        self.state.observe("今天周二")
        self.assertGreater(self.state.mood["平静"], 50.0)


class PeriodTests(StateTestCase):
    def test_disabled(self) -> None:
        self.plugin.config["enable_period_simulate"] = False
        info = self.state.period_info()
        self.assertEqual(info["phase"], "非生理期")
        self.assertEqual(info["day"], 0)

    def test_phases(self) -> None:
        self.set_start(0)
        self.assertEqual(self.state.period_info()["phase"], "月经期")
        self.set_start(8)
        self.assertEqual(self.state.period_info()["phase"], "卵泡期")
        self.set_start(13)
        self.assertEqual(self.state.period_info()["phase"], "排卵期")
        self.set_start(20)
        self.assertEqual(self.state.period_info()["phase"], "黄体期")

    def test_period_info_fields(self) -> None:
        self.set_start(0)
        info = self.state.period_info()
        for key in ("day", "phase", "phase_day", "tone", "pain", "reply_mult",
                    "days_to_next", "cycle_days", "bleed_days", "next_start"):
            self.assertIn(key, info)
        self.assertGreaterEqual(info["pain"], 0)
        self.assertLessEqual(info["pain"], 10)

    def test_set_period_start(self) -> None:
        self.assertTrue(self.state.set_period_start("2026-01-01"))
        self.assertEqual(self.state.period_start, "2026-01-01")
        self.assertFalse(self.state.set_period_start("不是日期"))


class HealthTests(StateTestCase):
    def test_default_healthy(self) -> None:
        info = self.state.health_info()
        self.assertEqual(info["status"], "健康")
        self.assertFalse(info["sick"])

    def test_sick_flag_only_for_real_sickness(self) -> None:
        self.state.set_health("疲惫")
        self.assertFalse(self.state.health_info()["sick"])
        self.state.set_health("感冒")
        self.assertTrue(self.state.health_info()["sick"])

    def test_expired_health_resets(self) -> None:
        self.state.health = {"status": "感冒", "since": 0, "until": now_ts() - 10, "note": ""}
        self.assertEqual(self.state.health_info()["status"], "健康")

    def test_set_health_zero_hours_still_has_until(self) -> None:
        self.state.set_health("感冒", hours=0)
        self.assertGreater(self.state.health["until"], now_ts())

    def test_roll_health_disabled(self) -> None:
        self.plugin.config["enable_sick_random"] = False
        self.state.roll_health()
        self.assertEqual(self.plugin.db.kv_get("health", {}) or {}, {})

    def test_roll_health_skips_when_rolled_today(self) -> None:
        self.plugin.config["sick_prob"] = 1.0
        self.state.last_health_roll = today_str()
        self.state.roll_health()
        self.assertEqual(self.state.health["status"], "健康")

    def test_roll_health_can_make_her_sick(self) -> None:
        self.plugin.config["sick_prob"] = 1.0
        self.state.last_health_roll = ""
        self.state.roll_health()
        self.assertNotEqual(self.state.health["status"], "健康")
        self.assertEqual(self.state.last_health_roll, today_str())


class WeatherTests(StateTestCase):
    def test_base_url(self) -> None:
        self.assertEqual(HumanState._weather_base_url("m56hf38tfd.re.qweatherapi.com"),
                         "https://m56hf38tfd.re.qweatherapi.com")
        self.assertEqual(HumanState._weather_base_url("https://x.com/"), "https://x.com")
        self.assertEqual(HumanState._weather_base_url("", "https://d.com"), "https://d.com")

    def test_tags(self) -> None:
        self.assertEqual(HumanState._weather_tag("雷阵雨"), "storm")
        self.assertEqual(HumanState._weather_tag("小雪"), "snow")
        self.assertEqual(HumanState._weather_tag("小雨"), "rain")
        self.assertEqual(HumanState._weather_tag("晴"), "clear")
        self.assertEqual(HumanState._weather_tag("阴"), "overcast")
        self.assertEqual(HumanState._weather_tag("雾"), "fog")
        self.assertEqual(HumanState._weather_tag("多云"), "cloud")

    def test_simulate_weather_shape(self) -> None:
        data = self.state._simulate_weather()
        for key in ("city", "text", "tag", "temp", "humidity", "wind", "ts", "source", "ok"):
            self.assertIn(key, data)
        self.assertEqual(data["source"], "simulate")
        self.assertEqual(data["city"], "贵阳")

    def test_simulate_uses_display_name(self) -> None:
        self.plugin.config["weather_city"] = "101281601"
        self.plugin.config["weather_city_name"] = "东莞"
        self.assertEqual(self.state._simulate_weather()["city"], "东莞")

    def test_refresh_weather_without_key_simulates(self) -> None:
        data = asyncio.run(self.state.refresh_weather(force=True))
        self.assertEqual(data["source"], "simulate")
        self.assertEqual(self.plugin.db.kv_get("weather")["source"], "simulate")

    def test_refresh_weather_reuses_cache(self) -> None:
        first = asyncio.run(self.state.refresh_weather(force=True))
        second = asyncio.run(self.state.refresh_weather())
        self.assertEqual(first["ts"], second["ts"])

    def test_refresh_weather_debounces_force(self) -> None:
        first = asyncio.run(self.state.refresh_weather(force=True))
        second = asyncio.run(self.state.refresh_weather(force=True))
        self.assertEqual(first["ts"], second["ts"])

    def test_weather_text_and_brief(self) -> None:
        self.state._simulate_weather()
        self.state.weather = self.state._simulate_weather()
        text = self.state.weather_text()
        self.assertIn("℃", text)
        brief = self.state.weather_brief()
        self.assertEqual(brief["source"], "simulate")

    def test_empty_weather_text(self) -> None:
        self.state.weather = {}
        self.assertEqual(self.state.weather_text(), "还不知道天气")
        self.assertEqual(self.state.weather_brief(), {})


class ScheduleTests(StateTestCase):
    def test_hourly_schedule_is_24h(self) -> None:
        rows = HumanState._hourly_schedule([
            {"start": "09:00", "end": "12:00", "activity": "上班", "place": "公司",
             "category": "work", "detail": "改需求"},
            {"start": "坏数据"},
        ])
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[9]["activity"], "上班")
        self.assertEqual(rows[9]["detail"], "改需求")
        self.assertEqual(rows[23]["end"], "24:00")
        self.assertEqual(rows[0]["category"], "sleep")

    def test_fmt_minutes(self) -> None:
        self.assertEqual(HumanState._fmt_minutes(510), "08:30")
        self.assertEqual(HumanState._fmt_minutes(-5), "00:00")
        self.assertEqual(HumanState._fmt_minutes(24 * 60 + 30), "24:00")

    def test_cover_schedule_adds_sleep(self) -> None:
        rows = HumanState._cover_schedule([{"start": "09:00", "activity": "上班"}])
        self.assertEqual(rows[0]["start"], "00:00")
        self.assertEqual(rows[0]["category"], "sleep")
        self.assertEqual(HumanState._cover_schedule([]), [])
        self.assertEqual(HumanState._cover_schedule(None), None)

    def test_emergency_schedule_is_closed_loop(self) -> None:
        rows = self.state._emergency_schedule()
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[0]["start"], "00:00")
        self.assertEqual(rows[-1]["end"], "24:00")
        for index in range(23):
            self.assertEqual(rows[index]["end"], rows[index + 1]["start"])

    def test_schedule_tree(self) -> None:
        day = today_str()
        self.state.schedule = {day: [{"start": "09:00", "end": "18:00", "activity": "上班",
                                      "place": "公司", "category": "work"}]}
        tree = self.state.schedule_tree(day)
        self.assertEqual(len(tree), 4)
        self.assertEqual(tree[0]["title"], "深夜")
        self.assertEqual(sum(len(block["children"]) for block in tree), 24)

    def test_schedule_context_default(self) -> None:
        self.assertEqual(self.state.schedule_context(), {})

    def test_minimal_context(self) -> None:
        context = self.state._minimal_context(today_str())
        for key in ("day", "meta", "weather", "world", "city", "home", "workplace",
                    "mood", "period", "health", "profile"):
            self.assertIn(key, context)

    def test_current_activity_from_schedule(self) -> None:
        import amo_shared

        now_min = amo_shared.minutes_now()
        start = "%02d:00" % max(0, now_min // 60)
        end = "%02d:00" % min(24, (now_min // 60) + 1)
        self.state.schedule = {today_str(): [
            {"start": start, "end": end, "activity": "测试活动", "place": "家", "category": "rest"}
        ]}
        self.assertEqual(self.state.current_activity()["activity"], "测试活动")

    def test_current_activity_fallback(self) -> None:
        self.state.schedule = {}
        self.assertIn("category", self.state.current_activity())

    def test_busy_level(self) -> None:
        self.assertEqual(HumanState.busy_level({"category": "sleep"}), 1.0)
        self.assertEqual(HumanState.busy_level({"category": "work"}), 0.75)
        self.assertEqual(HumanState.busy_level({"category": "commute"}), 0.6)
        self.assertEqual(HumanState.busy_level({"category": "meal"}), 0.3)
        self.assertEqual(HumanState.busy_level({}), 0.1)


class SnapshotTests(StateTestCase):
    def test_snapshot_keys(self) -> None:
        snap = self.state.snapshot()
        for key in ("date", "weekday", "time", "datetime_text", "mood", "dominant",
                    "mood_score", "period", "health", "weather", "weather_text",
                    "activity", "busy", "schedule"):
            self.assertIn(key, snap)

    def test_prompt_text_hides_activity_by_default(self) -> None:
        text = self.state.prompt_text()
        self.assertIn("心情：", text)
        self.assertNotIn("你此刻在", text)
        with_activity = self.state.prompt_text(with_activity=True)
        self.assertIn("你此刻在", with_activity)

    def test_tone_text_branches(self) -> None:
        snap = self.state.snapshot()
        snap["mood"] = {key: 50.0 for key in MOOD_KEYS}
        self.assertIn("自然平静", self.state.tone_text(snap))
        snap["mood"]["暴躁"] = 80.0
        self.assertIn("暴躁", self.state.tone_text(snap))
        self.assertIn("恋人", self.state.tone_text(snap, relation="相恋"))
        snap["mood"] = {key: 50.0 for key in MOOD_KEYS}
        snap["health"] = {"sick": True, "status": "感冒", "note": ""}
        self.assertIn("身体不舒服", self.state.tone_text(snap))

    def test_interesting_event(self) -> None:
        self.state.weather = {"tag": "rain"}
        seen = {self.state.interesting_event() for _ in range(4)}
        self.assertTrue(all(isinstance(item, str) and item for item in seen))


class DailyAndWorldTests(StateTestCase):
    def test_daily_tick_menstrual(self) -> None:
        self.set_start(0)
        before = self.state.mood["疲惫"]
        self.state.daily_tick()
        self.assertGreater(self.state.mood["疲惫"], before)
        self.assertEqual(self.state.last_daily, today_str())

    def test_daily_tick_luteal(self) -> None:
        self.set_start(20)
        before = self.state.mood["焦虑"]
        self.state.daily_tick()
        self.assertGreater(self.state.mood["焦虑"], before)

    def test_daily_tick_ovulation(self) -> None:
        self.set_start(13)
        before = self.state.mood["兴奋"]
        self.state.daily_tick()
        self.assertGreater(self.state.mood["兴奋"], before)

    def test_daily_tick_follicular(self) -> None:
        self.set_start(8)
        before = self.state.mood["开心"]
        self.state.daily_tick()
        self.assertGreater(self.state.mood["开心"], before)

    def test_worldview_text_empty(self) -> None:
        self.state.worldview = {}
        self.assertEqual(self.state.worldview_text(), "还没有生成世界观。")

    def test_worldview_text_fields(self) -> None:
        self.state.worldview = {
            "name": "阿珍", "age": 24, "city": "东莞", "occupation": "会计",
            "workplace": "写字楼", "personality": "温柔", "hobbies": ["看书", "爬山"],
            "daily_life": "上班", "world_rules": "规则",
        }
        text = self.state.worldview_text()
        self.assertIn("阿珍", text)
        self.assertIn("看书、爬山", text)

    def test_fallback_world(self) -> None:
        world = self.state._fallback_world()
        self.assertEqual(world["name"], "大肥鱼")
        self.assertIn("city", world)
        self.assertGreater(world["age"], 0)

    def test_follow_day_log_deduped(self) -> None:
        day = today_str()
        self.state._log_follow_day("杭州", day)
        self.state._log_follow_day("杭州", day)
        log = self.plugin.db.kv_get("travel_log", [])
        self.assertEqual(len([row for row in log if row.get("city") == "杭州"]), 1)


if __name__ == "__main__":
    unittest.main()
