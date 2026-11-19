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
from fixtures import FixedClock, today_at  # noqa: E402
import clock  # noqa: E402


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
        setattr(path_module, "get_astrbot_data_path", lambda: self.tmp)
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
        self.state.period_start = (clock.today() - timedelta(days=days_ago)).isoformat()


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


    def test_minor_illness_doctor_one_to_two_days(self):
        self.state.set_health("感冒", hours=36, treatment="看医生")
        info = self.state.health_info()
        self.assertTrue(info["doctor"])
        self.assertGreaterEqual(info["until"] - info["since"], 35 * 3600)
        self.assertLessEqual(info["until"] - info["since"], 37 * 3600)

    def test_no_doctor_doubles_recovery(self):
        self.state.set_health("感冒", hours=36, treatment="")
        info = self.state.health_info()
        self.assertFalse(info["doctor"])
        self.assertGreaterEqual(info["until"] - info["since"], 71 * 3600)

    def test_serious_illness_doctor_three_to_four_days(self):
        self.state.set_health("发烧", hours=84, treatment="看医生")
        info = self.state.health_info()
        self.assertEqual(info["severity"], "serious")
        self.assertGreaterEqual(info["until"] - info["since"], 83 * 3600)

    def test_fatal_illness_names_are_mapped_to_common_ones(self):
        self.state.set_health("肺炎")
        self.assertEqual(self.state.health_info()["status"], "重感冒")
        self.state.set_health("癌症")
        self.assertEqual(self.state.health_info()["status"], "不舒服")

    def test_seeing_doctor_shortens_without_resetting_start(self):
        self.state.set_health("感冒", hours=36, treatment="")
        since = self.state.health_info()["since"]
        self.state.set_treatment("看医生")
        info = self.state.health_info()
        self.assertEqual(info["since"], since)
        self.assertTrue(info["doctor"])
        self.assertLessEqual(info["until"] - since, 37 * 3600)


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
        self.assertEqual(data["city"], "")

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

    def _stub_weather_fetch(self):
        calls = []

        async def fake_fetch(key, city):
            calls.append(city)
            return {"city": city, "text": "晴", "tag": "clear", "temp": 20,
                    "humidity": 50, "wind": "东风1级", "ts": now_ts(),
                    "source": "qweather", "ok": True, "error": "",
                    "key_set": True, "host": "https://hf.example"}

        self.state._fetch_qweather = fake_fetch
        self.plugin.config["hefeng_key"] = "k"
        return calls

    def test_city_signature_does_not_force_every_tick(self) -> None:
        # 2026-11-19：日程城市（东莞）和天气城市显示名（清远）不同时，旧代码每次 tick 都
        # 判定「配置变了」强制刷新，和风一天被打 4 万次。修完后必须稳定命中 30 分钟缓存。
        calls = self._stub_weather_fetch()
        self.plugin.config["weather_city_name"] = "清远"
        self.plugin.config["schedule_city"] = "东莞"
        first = asyncio.run(self.state.refresh_weather(force=True))
        second = asyncio.run(self.state.refresh_weather())
        third = asyncio.run(self.state.refresh_weather())
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(first["ts"], second["ts"])
        self.assertEqual(second["ts"], third["ts"])

    def test_failed_fetch_falls_back_without_tick_loop(self) -> None:
        # 2026-11-19：401 时兜底模拟天气用了日程城市（东莞），和想取的天气城市（清远）对不上，
        # 签名永远不等 → 每个 tick 重试一次 401，和风被打到 4 万/天。
        self.plugin.config["hefeng_key"] = "k"
        self.plugin.config["weather_city_name"] = "清远"
        self.plugin.config["schedule_city"] = "东莞"
        calls = []

        async def fail_fetch(key, city):
            calls.append(city)
            return None

        self.state._fetch_qweather = fail_fetch
        first = asyncio.run(self.state.refresh_weather(force=True))
        second = asyncio.run(self.state.refresh_weather())
        third = asyncio.run(self.state.refresh_weather())
        self.assertEqual(first["source"], "simulate")
        self.assertEqual(first["city"], "清远")
        self.assertEqual(first["ts"], second["ts"])
        self.assertEqual(second["ts"], third["ts"])
        self.assertEqual(len(calls), 1, calls)

    def test_city_change_refreshes_once_then_caches(self) -> None:
        calls = self._stub_weather_fetch()
        self.plugin.config["weather_city_name"] = "清远"
        self.plugin.config["schedule_city"] = "东莞"
        asyncio.run(self.state.refresh_weather(force=True))
        self.state.weather["ts"] = float(self.state.weather["ts"]) - 5  # 越过 3 秒去抖
        self.plugin.db.kv_set("weather", self.state.weather)
        self.plugin.config["weather_city_name"] = "东莞"
        changed = asyncio.run(self.state.refresh_weather())
        self.assertEqual(changed["city"], "东莞")
        asyncio.run(self.state.refresh_weather())
        self.assertEqual(calls, ["清远", "东莞"])

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
        import amo_shared

        day = today_str()
        if amo_shared.minutes_now() == 0:
            self.skipTest("0 点整还没有已经过去的分钟")
        # 全天日程按自然跨度展开；当前这一小时标 doing，未来不预生成
        self.state.schedule = {day: [{"start": "00:00", "end": "24:00", "activity": "上班",
                                      "place": "公司", "category": "work"}]}
        tree = self.state.schedule_tree(day)
        children = [child for block in tree for child in block["children"]]
        self.assertTrue(children)
        self.assertEqual(children[0]["start"], "00:00")
        self.assertTrue(children[0]["doing"])
        self.assertFalse(any(block["start"] == "18:00" for block in tree))

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


class JitBlindBoxTests(StateTestCase):
    """盲盒日程：未来不预生成、当前小时 doing、睡眠事后合并、重启补空档。"""

    def test_future_hours_are_not_fabricated(self) -> None:
        import amo_human_state
        from unittest.mock import patch

        day = today_str()
        self.state.schedule = {day: [
            {"start": "13:00", "end": "14:00", "activity": "休息", "place": "家", "category": "rest"},
            {"start": "16:00", "end": "17:00", "activity": "休息", "place": "家", "category": "rest"},
        ]}
        with patch.object(amo_human_state, "minutes_now", return_value=14 * 60):
            tree = self.state.schedule_tree(day)
        children = [child for block in tree for child in block["children"]]
        starts = [child["start"] for child in children]
        self.assertIn("13:00", starts)
        self.assertNotIn("16:00", starts)
        self.assertFalse(any(block["start"] == "18:00" for block in tree))
        self.assertEqual(children[0]["start"], "00:00")
        self.assertEqual(children[-1]["end"], "14:00")
        for left, right in zip(children, children[1:]):
            self.assertEqual(left["end"], right["start"])

    def test_empty_hours_fill_continuous_past(self) -> None:
        import amo_human_state
        from unittest.mock import patch

        day = today_str()
        self.state.schedule = {day: [
            {"start": "08:00", "end": "09:00", "activity": "吃早饭", "place": "家", "category": "meal"},
        ]}
        with patch.object(amo_human_state, "minutes_now", return_value=12 * 60):
            tree = self.state.schedule_tree(day)
        children = [child for block in tree for child in block["children"]]
        self.assertGreater(len(children), 1)
        self.assertIn("吃早饭", [child["activity"] for child in children])
        self.assertEqual(children[0]["start"], "00:00")
        self.assertEqual(children[-1]["end"], "12:00")
        for left, right in zip(children, children[1:]):
            self.assertEqual(left["end"], right["start"])

    def test_current_hour_is_doing(self) -> None:
        import amo_human_state
        from unittest.mock import patch

        day = today_str()
        self.state.schedule = {day: [
            {"start": "14:00", "end": "15:00", "activity": "上班", "place": "公司", "category": "work"},
        ]}
        with patch.object(amo_human_state, "minutes_now", return_value=14 * 60 + 20):
            tree = self.state.schedule_tree(day)
        child = [c for block in tree for c in block["children"] if c["doing"]][0]
        self.assertTrue(child["current"])
        self.assertTrue(child["doing"])
        self.assertEqual(child["end"], "15:00")

    def test_consecutive_rest_is_merged(self) -> None:
        import amo_human_state
        from unittest.mock import patch

        day = today_str()
        self.state.schedule = {day: [
            {"start": "13:00", "end": "14:00", "activity": "休息", "place": "家", "category": "rest"},
            {"start": "14:00", "end": "15:00", "activity": "休息", "place": "家", "category": "rest"},
            {"start": "15:00", "end": "16:00", "activity": "休息", "place": "家", "category": "rest"},
        ]}
        with patch.object(amo_human_state, "minutes_now", return_value=16 * 60):
            tree = self.state.schedule_tree(day)
        children = [child for block in tree for child in block["children"]]
        rest = [child for child in children if child["activity"] == "休息"]
        self.assertEqual(len(rest), 1)
        self.assertEqual(rest[0]["start"], "13:00")
        self.assertEqual(rest[0]["end"], "16:00")

    def test_finance_activity_and_event_tag_are_humanized(self) -> None:
        import amo_human_state
        from unittest.mock import patch

        day = today_str()
        self.state.schedule = {day: [
            {"start": "09:18", "end": "09:28", "activity": "财务·支出", "place": "家",
             "category": "finance", "detail": "支出 ¥41.95，夜宵：小蛋糕",
             "event": "情绪由开心变为平静"},
        ]}
        with patch.object(amo_human_state, "minutes_now", return_value=12 * 60):
            tree = self.state.schedule_tree(day)
        children = [child for block in tree for child in block["children"]]
        hit = [child for child in children if child["start"] == "09:18"][0]
        self.assertEqual(hit["end"], "09:28")
        self.assertIn("小蛋糕", hit["activity"])
        self.assertIn("41.95", hit["activity"])
        self.assertEqual(hit["event"], "")

    def test_finance_activity_translates_salary_and_expense(self) -> None:
        self.assertEqual(
            HumanState._finance_activity("财务·工资到账", "工资到账 ¥7,212.64，工资"),
            "工资到账（¥7,212.64）",
        )
        self.assertEqual(
            HumanState._finance_activity("财务·支出", "支出 ¥41.95，夜宵：小蛋糕"),
            "买了小蛋糕（花销 ¥41.95）",
        )

    def test_block_carries_cold_facts_not_hardcoded_names(self) -> None:
        import amo_human_state
        from unittest.mock import patch

        day = today_str()
        self.state.schedule = {day: [
            {"start": "08:00", "end": "09:00", "activity": "吃早饭", "place": "家", "category": "meal"},
        ]}
        with patch.object(amo_human_state, "minutes_now", return_value=12 * 60):
            tree = self.state.schedule_tree(day)
        self.assertTrue(tree)
        for block in tree:
            self.assertEqual(block.get("activity"), "")
            self.assertTrue(block.get("facts", {}).get("events"))
            self.assertNotIn("summary", block)
            for child in block.get("children") or []:
                self.assertTrue(child.get("id"))

    def test_llm_generates_four_char_block_summary_and_caches(self) -> None:
        import amo_human_state
        from unittest.mock import patch

        class FakeLLM:
            def __init__(self):
                self.calls = 0

            async def chat_raw(self, umo, system, prompt):
                self.calls += 1
                return "迷糊休息"

        day = today_str()
        self.state.schedule = {day: [
            {"start": "08:00", "end": "09:00", "activity": "吃早饭", "place": "家", "category": "meal"},
        ]}
        with patch.object(amo_human_state, "minutes_now", return_value=12 * 60):
            tree = self.state.schedule_tree(day)
        llm = FakeLLM()
        self.state.p.llm = llm
        asyncio.run(self.state.enrich_schedule_tree(day, tree, ""))
        self.assertTrue(all(block.get("summary") == "迷糊休息" for block in tree))
        first_calls = llm.calls
        asyncio.run(self.state.enrich_schedule_tree(day, tree, ""))
        self.assertEqual(llm.calls, first_calls)

    def test_future_day_is_empty(self) -> None:
        tomorrow = (clock.today() + timedelta(days=1)).isoformat()
        self.state.schedule = {tomorrow: [
            {"start": "09:00", "end": "10:00", "activity": "上班", "place": "公司", "category": "work"},
        ]}
        self.assertEqual(self.state.schedule_tree(tomorrow), [])

    def test_past_day_keeps_full_history(self) -> None:
        yesterday = (clock.today() - timedelta(days=1)).isoformat()
        self.state.schedule = {yesterday: [
            {"start": "09:00", "end": "10:00", "activity": "上班", "place": "公司", "category": "work"},
        ]}
        tree = self.state.schedule_tree(yesterday)
        starts = [child["start"] for block in tree for child in block["children"]]
        self.assertEqual(len(tree), 4)
        self.assertIn("23:00", starts)

    def test_blind_hint(self) -> None:
        self.assertIn("还没过完", self.state.schedule_blind_hint(today_str()))
        self.assertEqual(self.state.schedule_blind_hint((clock.today() - timedelta(days=1)).isoformat()), "")
        self.assertTrue(self.state.schedule_blind_hint((clock.today() + timedelta(days=1)).isoformat()))

    def test_sleep_merges_after_waking(self) -> None:
        from datetime import datetime as dt

        previous = (clock.today() - timedelta(days=1)).isoformat()
        self.state.schedule = {previous: [
            {"start": "23:00", "end": "24:00", "activity": "入睡", "category": "sleep",
             "source": "jit", "open_sleep": True},
        ]}
        self.plugin.db.kv_set("sleep_open", {"day": previous, "start": "23:00"})
        result = self.state._finish_open_sleep(dt.combine(clock.today(), dt.min.time()).replace(hour=7, minute=10))
        self.assertEqual(result.get("wake"), "07:00")
        previous_rows = self.state.schedule[previous]
        self.assertEqual(previous_rows[0]["activity"], "睡觉")
        self.assertFalse(previous_rows[0].get("open_sleep"))
        today_rows = self.state.schedule[today_str()]
        sleep_rows = [row for row in today_rows if row.get("category") == "sleep"]
        self.assertEqual(len(sleep_rows), 1)
        self.assertEqual(sleep_rows[0]["start"], "00:00")
        self.assertEqual(sleep_rows[0]["end"], "07:00")
        self.assertEqual(sleep_rows[0]["sleep_from"], "23:00")
        self.assertEqual(self.plugin.db.kv_get("sleep_open"), {})

    def test_restart_backfills_gap(self) -> None:
        from datetime import datetime as dt

        day = today_str()
        self.state.schedule = {day: []}
        last = dt.combine(clock.today(), dt.min.time()).replace(hour=15, minute=5)
        now = dt.combine(clock.today(), dt.min.time()).replace(hour=15, minute=30)
        rows = self.state._jit_backfill_gap(now, last)
        self.assertEqual(len(rows), 1)
        # 期望按分钟算（跨分钟边界偶发红就是这个写死字符串造成的）
        expected_start = "%02d:00" % last.hour
        expected_end = "%02d:00" % min(24, now.hour + 1)

        def _minutes(text):
            hh, mm = str(text).split(":")[:2]
            return int(hh) * 60 + int(mm)

        self.assertEqual(_minutes(rows[0]["start"]), _minutes(expected_start))
        self.assertEqual(_minutes(rows[0]["end"]), _minutes(expected_end))
        # 空档补位必须是人话，不能再写“未记录”（2026-11-16 用户反馈）
        self.assertNotEqual(rows[0]["activity"], "未记录")
        self.assertTrue(str(rows[0]["activity"]).strip())
        self.assertEqual(self.state.schedule[day][0]["source"], "jit_recover")

    def test_normalize_place_whitelist(self) -> None:
        import amo_human_state

        self.assertEqual(amo_human_state.normalize_place("他桌面上"), "家")
        for place in ("家", "卧室", "客厅", "公司", "楼下小馆", "地铁", "商场"):
            self.assertEqual(amo_human_state.normalize_place(place), place)
        self.assertEqual(amo_human_state.normalize_place("某个奇怪的地方", "home"), "家")
        self.assertEqual(amo_human_state.normalize_place("路上", "road"), "路上")

    def test_cross_hour_event_keeps_one_span(self) -> None:
        import amo_human_state
        from unittest.mock import patch

        day = today_str()
        self.state.schedule = {day: [
            {"start": "14:50", "end": "15:20", "activity": "逛街", "place": "商场",
             "category": "play", "zone": "mall"},
        ]}
        with patch.object(amo_human_state, "minutes_now", return_value=16 * 60):
            tree = self.state.schedule_tree(day)
        rows = [child for block in tree for child in block["children"]]
        hit = [child for child in rows if child["start"] == "14:50"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["end"], "15:20")
        self.assertEqual(hit[0]["activity"], "逛街")

    def test_sleep_hour_detail_is_placeholder(self) -> None:
        # 睡眠占位的判断在「这一时段已过完」之后；0 点刚过时 03:00-04:00 还没到，
        # 会先返回 pending。冻结到当天 15:00，让这条用例和墙钟无关。
        frozen = FixedClock(today_at(15, 0))
        frozen.start()
        self.addCleanup(frozen.stop)
        day = today_str()
        item = {"start": "03:00", "end": "04:00", "activity": "又睡过去了",
                "place": "家", "zone": "home", "category": "sleep"}
        self.state.schedule = {day: [item]}
        result = asyncio.run(self.state.expand_hour(day, item, [item]))
        self.assertTrue(result.get("sleep"))
        self.assertIn("呼呼大睡", result.get("diary") or "")
        self.assertNotIn("他", result.get("diary") or "")

    def test_jit_tick_runs_every_hour(self) -> None:
        from datetime import datetime as dt

        day = today_str()
        self.state.schedule = {day: []}
        self.state.jit_tick(dt.combine(clock.today(), dt.min.time()).replace(hour=10, minute=5))
        rows = self.state.schedule[day]
        self.assertTrue(any(row.get("start") == "10:00" for row in rows))


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
        self.assertIn("自然、轻快", self.state.tone_text(snap))
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
