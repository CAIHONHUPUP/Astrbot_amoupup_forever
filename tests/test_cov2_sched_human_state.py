# -*- coding: utf-8 -*-
"""cov2: amo_human_state 天气/生病决策/健康/心情/世界观/语气这些分支。"""
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_human_state as hs  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_shared import MOOD_BASE, MOOD_KEYS, now_ts, today_str  # noqa: E402


class FakeLogger:
    def __init__(self):
        self.records = []

    def warning(self, message, *args, **kwargs):
        self.records.append(("warning", str(message)))

    def info(self, message, *args, **kwargs):
        self.records.append(("info", str(message)))

    def debug(self, message, *args, **kwargs):
        self.records.append(("debug", str(message)))

    def error(self, message, *args, **kwargs):
        self.records.append(("error", str(message)))


class FakeWallet:
    def balance(self):
        return 12345


class FakeLLM:
    def __init__(self, reply="", persona="人设", worldview=None):
        self.reply = reply
        self.persona_text = persona
        self.worldview = worldview
        self.calls = []

    async def chat_raw(self, umo, system, prompt):
        self.calls.append(("chat_raw", umo, prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    async def persona(self, umo):
        return self.persona_text

    async def gen_worldview(self, umo, persona):
        return self.worldview


class FakePlugin:
    def __init__(self, tmp, **cfg):
        self.config = {
            "weather_city": "", "weather_city_name": "", "weather_host": "",
            "weather_geo_host": "", "hefeng_key": "",
            "enable_period_simulate": True, "period_cycle_days": 28,
            "period_days": 5, "enable_sick_random": True, "sick_prob": 0.0,
        }
        self.config.update(cfg)
        self.persona_name = "大肥鱼"
        self.bot_name = "大肥鱼"
        self.db = AmoDB(tmp)
        self.db.init()
        self.wallet = FakeWallet()
        self.llm = FakeLLM()
        self.shared_calls = []
        self._last_umo = "test:private:1"

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

    async def _persona_name_for(self, umo):
        return getattr(self, "persona_for", "")

    def shared_activity_on_message(self, text):
        self.shared_calls.append(text)


class Resp:
    def __init__(self, text="", status=200, error=None):
        self._text = text
        self.status = status
        self.error = error

    async def text(self):
        if self.error:
            raise self.error
        return self._text


class HumanBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-cov2-hs-")
        self.plugin = FakePlugin(self.tmp)
        self.state = HumanState(self.plugin)
        self.logger = FakeLogger()
        self._saved_logger = hs.logger
        hs.logger = self.logger

    def tearDown(self):
        hs.logger = self._saved_logger
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass

    def run_async(self, coro):
        return asyncio.run(coro)

    def reset_mood(self):
        self.state.mood = {key: MOOD_BASE[key] for key in MOOD_KEYS}


class WeatherBaseTests(HumanBase):
    def test_base_url_variants(self):
        self.assertEqual(HumanState._weather_base_url("api.example.com/"), "https://api.example.com")
        self.assertEqual(HumanState._weather_base_url("https://x.y/"), "https://x.y")
        self.assertEqual(HumanState._weather_base_url("", "http://d"), "http://d")
        self.assertEqual(HumanState._weather_base_url("  "), "")

    def test_weather_tag_mapping(self):
        cases = {"雷阵雨": "storm", "暴雨": "storm", "小雪": "snow", "下雨": "rain",
                 "晴": "clear", "阴": "overcast", "有雾": "fog", "霾": "fog",
                 "多云": "cloud"}
        for text, tag in cases.items():
            self.assertEqual(HumanState._weather_tag(text), tag, text)

    def test_simulate_weather_summer(self):
        fixed = dt.datetime(2026, 7, 15, 10, 30)
        with mock.patch.object(hs, "_now", lambda: fixed):
            data = self.plugin and self.state._simulate_weather()
        self.assertEqual(data["source"], "simulate")
        self.assertTrue(data["ok"])
        self.assertTrue(25 <= data["temp"] <= 33)
        self.assertIn(data["tag"], ("clear", "cloud", "storm", "rain", "overcast"))
        self.assertTrue(40 <= data["humidity"] <= 90)
        self.assertIn("级", data["wind"])
        self.assertFalse(data["key_set"])

    def test_payload_ok(self):
        payload = self.run_async(self.state._qweather_payload(
            Resp(json.dumps({"code": "200", "now": {"temp": "20"}})), "now"))
        self.assertEqual(payload["code"], "200")

    def test_payload_read_error_empty_nonjson_list(self):
        self.assertEqual(self.run_async(self.state._qweather_payload(
            Resp(error=RuntimeError("boom")), "now")), {})
        self.assertEqual(self.run_async(self.state._qweather_payload(Resp("  "), "now")), {})
        self.assertEqual(self.run_async(self.state._qweather_payload(Resp("not json"), "now")), {})
        self.assertEqual(self.run_async(self.state._qweather_payload(Resp("[1, 2]"), "now")), {})
        self.assertEqual(len([r for r in self.logger.records if r[0] == "warning"]), 4)

    def test_payload_http_error_still_returns(self):
        payload = self.run_async(self.state._qweather_payload(
            Resp(json.dumps({"code": "500", "msg": "bad"}), status=500), "now"))
        self.assertEqual(payload["code"], "500")
        self.assertTrue(any("HTTP 500" in msg for _, msg in self.logger.records))

    def test_qweather_get_builds_query_and_parses(self):
        seen = {}

        class Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"code": "200"}'

        def fake_urlopen(req, timeout=0):
            seen["url"] = req.full_url
            seen["timeout"] = timeout
            seen["headers"] = dict(req.header_items())
            return Ctx()

        self.plugin.config["hefeng_key"] = "KEY"
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            data = self.run_async(self.state._qweather_get("https://api.example.com/x?a=1",
                                                           {"location": "101"}))
        self.assertEqual(data, {"code": "200"})
        self.assertIn("key=KEY", seen["url"])
        self.assertIn("location=101", seen["url"])
        self.assertIn("a=1", seen["url"])
        self.assertEqual(seen["timeout"], 12)

    def test_qweather_get_decompresses_gzip(self):
        raw = gzip.compress(b'{"code": "200", "ok": true}')

        class Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return raw

        with mock.patch("urllib.request.urlopen", lambda req, timeout=0: Ctx()):
            data = self.run_async(self.state._qweather_get("https://api.example.com/x", {}))
        self.assertTrue(data["ok"])

    def test_location_of_numeric_name_and_garbage(self):
        loc = self.state._location_of("贵阳")
        self.assertTrue(str(loc).isdigit())
        self.assertEqual(self.state._location_of("101"), "101")
        self.assertEqual(self.state._location_of("106.7,26.6"), "106.7,26.6")
        self.assertEqual(self.state._location_of("不存在的城市名"), "不存在的城市名")


class FetchWeatherTests(HumanBase):
    def setUp(self):
        super().setUp()
        self.plugin.config.update({"weather_host": "https://api.example.com",
                                   "weather_city_name": "贵阳", "hefeng_key": "K"})

    def test_no_host_returns_none(self):
        self.plugin.config["weather_host"] = ""
        self.assertIsNone(self.run_async(self.state._fetch_qweather("k", "贵阳")))

    def test_geocode_then_now(self):
        urls = []

        async def fake_get(url, params):
            urls.append((url, params))
            if "city/lookup" in url:
                return {"code": "200", "location": [{"id": "101260101"}]}
            return {"code": "200", "now": {"text": "小雨", "temp": "18", "humidity": "80",
                                           "windDir": "北风", "windScale": "2"}}

        self.state._qweather_get = fake_get
        data = self.run_async(self.state._fetch_qweather("K", "测试城"))
        self.assertEqual(data["city"], "贵阳")
        self.assertEqual(data["tag"], "rain")
        self.assertEqual(data["temp"], 18)
        self.assertEqual(data["wind"], "北风2级")
        self.assertEqual(data["source"], "qweather")
        self.assertEqual(len(urls), 2)
        self.assertIn("/geo/v2/city/lookup", urls[0][0])
        self.assertIn("/v7/weather/now", urls[1][0])

    def test_geo_host_with_lookup_suffix_is_trimmed(self):
        urls = []

        async def fake_get(url, params):
            urls.append(url)
            if "city/lookup" in url:
                return {"code": "200", "location": [{"id": "101"}]}
            return {"code": "200", "now": {"text": "晴"}}

        self.plugin.config["weather_geo_host"] = "https://geo.example.com/geo/v2/city/lookup"
        self.state._qweather_get = fake_get
        self.run_async(self.state._fetch_qweather("K", "测试城"))
        self.assertEqual(urls[0], "https://geo.example.com/geo/v2/city/lookup")

    def test_legacy_host_warns(self):
        self.plugin.config["weather_host"] = "https://devapi.qweather.com"

        async def fake_get(url, params):
            if "city/lookup" in url:
                return {"code": "200", "location": [{"id": "101"}]}
            return {"code": "200", "now": {"text": "晴"}}

        self.state._qweather_get = fake_get
        self.assertIsNotNone(self.run_async(self.state._fetch_qweather("K", "贵阳")))
        self.assertTrue(any("停服" in msg for _, msg in self.logger.records))

    def test_digit_location_skips_geocode(self):
        urls = []

        async def fake_get(url, params):
            urls.append(url)
            return {"code": "200", "now": {"text": "晴"}}

        self.state._qweather_get = fake_get
        self.run_async(self.state._fetch_qweather("K", "101260101"))
        self.assertEqual(len(urls), 1)
        self.assertNotIn("lookup", urls[0])

    def test_geo_and_now_failures(self):
        async def bad_geo(url, params):
            return {"code": "404"}

        self.state._qweather_get = bad_geo
        self.assertIsNone(self.run_async(self.state._fetch_qweather("K", "贵阳")))

        async def bad_now(url, params):
            if "lookup" in url:
                return {"code": "200", "location": []}
            return {"code": "500"}

        self.state._qweather_get = bad_now
        self.assertIsNone(self.run_async(self.state._fetch_qweather("K", "贵阳")))

        async def no_now(url, params):
            if "lookup" in url:
                return {"code": "200", "location": [{"id": "101"}]}
            return {"code": "200", "now": []}

        self.state._qweather_get = no_now
        self.assertIsNone(self.run_async(self.state._fetch_qweather("K", "贵阳")))

    def test_exception_returns_none(self):
        async def boom(url, params):
            raise RuntimeError("网络挂了")

        self.state._qweather_get = boom
        self.assertIsNone(self.run_async(self.state._fetch_qweather("K", "贵阳")))
        self.assertTrue(any("接口失败" in msg for _, msg in self.logger.records))

    def test_daily_cache_hit_and_fresh(self):
        store = types.SimpleNamespace()
        cached = [{"date": "2026-11-19", "text": "晴"}]
        calls = []

        class Store:
            def get_fresh(self, key):
                return {"ts": now_ts(), "city": "贵阳", "days": 3, "items": cached}

            def set_ttl(self, key, value, ttl):
                calls.append((key, ttl))

        async def fake_get(url, params):
            if "lookup" in url:
                return {"code": "200", "location": [{"id": "101"}]}
            return {"code": "200", "daily": []}

        with mock.patch("amo_state_store.for_plugin", lambda plugin, now=None: Store()):
            self.assertEqual(self.run_async(self.state._fetch_qweather_daily("K", "贵阳")), cached)
            self.state._qweather_get = fake_get
            with mock.patch.object(hs, "parse_weather_daily", lambda data: []):
                self.assertEqual(self.run_async(self.state._fetch_qweather_daily(
                    "K", "贵阳", fresh=True)), [])
        self.assertTrue(calls)

    def test_daily_fetch_success_and_failures(self):
        class Store:
            def __init__(self):
                self.items = []

            def get_fresh(self, key):
                return {}

            def set_ttl(self, key, value, ttl):
                self.items.append((key, value, ttl))

        store = Store()

        async def fake_get(url, params):
            if "lookup" in url:
                return {"code": "200", "location": [{"id": "101"}]}
            return {"code": "200", "daily": []}

        self.state._qweather_get = fake_get
        with mock.patch("amo_state_store.for_plugin", lambda plugin, now=None: store), \
                mock.patch.object(hs, "parse_weather_daily",
                                  lambda data: [{"date": "2026-11-19", "text": "晴"}]):
            items = self.run_async(self.state._fetch_qweather_daily("K", "贵阳"))
        self.assertEqual(items, [{"date": "2026-11-19", "text": "晴"}])
        self.assertEqual(store.items[0][0], "weather_daily")
        self.assertEqual(store.items[0][2], 3 * 3600)

        self.plugin.config["weather_host"] = ""
        self.assertEqual(self.run_async(self.state._fetch_qweather_daily("K", "贵阳")), [])

        self.plugin.config["weather_host"] = "https://api.example.com"

        async def bad_geo(url, params):
            return {"code": "500"}

        self.state._qweather_get = bad_geo
        with mock.patch("amo_state_store.for_plugin", lambda plugin, now=None: Store()):
            self.assertEqual(self.run_async(self.state._fetch_qweather_daily("K", "测试城")), [])

        async def boom(url, params):
            raise RuntimeError("x")

        self.state._qweather_get = boom
        with mock.patch("amo_state_store.for_plugin", lambda plugin, now=None: Store()):
            self.assertEqual(self.run_async(self.state._fetch_qweather_daily("K", "贵阳")), [])


class RefreshWeatherTests(HumanBase):
    def setUp(self):
        super().setUp()
        self.plugin.config.update({"weather_city": "贵阳", "weather_host": "https://api.example.com",
                                   "hefeng_key": "K"})

    def test_recent_cache_returns_without_fetch(self):
        current = {"city": "贵阳", "key_set": True, "host": "https://api.example.com",
                   "ts": now_ts(), "tag": "clear"}
        self.state.weather = current
        out = self.run_async(self.state.refresh_weather())
        self.assertEqual(out, current)
        self.assertIsNone(self.plugin.db.kv_get("weather"))

    def test_fetch_and_mood_nudge_on_rain(self):
        async def fake_fetch(key, city):
            return {"city": city, "tag": "rain", "text": "小雨", "temp": 12,
                    "humidity": 80, "wind": "北风2级", "ts": now_ts(),
                    "source": "qweather", "key_set": True,
                    "host": "https://api.example.com"}

        self.state._fetch_qweather = fake_fetch
        self.state.weather = {}
        before = dict(self.state.mood)
        out = self.run_async(self.state.refresh_weather(force=True))
        self.assertEqual(out["tag"], "rain")
        self.assertEqual(self.plugin.db.kv_get("weather")["tag"], "rain")
        self.assertGreater(self.state.mood["郁闷"], before["郁闷"])
        self.assertEqual(self.state.mood["开心"], before["开心"])

    def test_clear_weather_nudges_happy(self):
        async def fake_fetch(key, city):
            return {"city": city, "tag": "clear", "text": "晴", "ts": now_ts(),
                    "source": "qweather", "key_set": True,
                    "host": "https://api.example.com"}

        self.state._fetch_qweather = fake_fetch
        before = self.state.mood["开心"]
        self.run_async(self.state.refresh_weather(force=True))
        self.assertGreater(self.state.mood["开心"], before)

    def test_no_key_simulates(self):
        self.plugin.config["hefeng_key"] = ""
        self.state.weather = {}
        out = self.run_async(self.state.refresh_weather(force=True))
        self.assertEqual(out["source"], "simulate")

    def test_force_within_three_seconds_returns_cached(self):
        current = {"city": "", "key_set": False, "host": "",
                   "ts": now_ts(), "tag": "cloud"}
        self.state.weather = current
        self.assertEqual(self.run_async(self.state.refresh_weather(force=True)), current)


class HealthTests(HumanBase):
    def test_set_health_downgrades_and_resets(self):
        self.state.set_health("肺炎", hours=24)
        self.assertEqual(self.state.health["status"], "重感冒")
        self.assertEqual(self.state.health["base_hours"], 24)
        self.assertFalse(self.state.health["doctor"])
        self.assertGreaterEqual(self.state.health["until"], self.state.health["since"] + 48 * 3600)
        self.assertEqual(self.plugin.db.kv_get("health")["status"], "重感冒")

        self.state.set_health("外星病", hours=5)
        self.assertEqual(self.state.health["status"], "不舒服")

        self.state.set_health("健康")
        self.assertEqual(self.state.health["status"], "健康")
        self.assertEqual(self.state.health["until"], 0)

    def test_set_health_keeps_earliest_since_when_ongoing(self):
        self.state.set_health("发烧", hours=24)
        since = self.state.health["since"]
        until = self.state.health["until"]
        self.state.set_health("发烧", hours=10)
        self.assertEqual(self.state.health["since"], since)
        self.assertEqual(self.state.health["until"], until)
        self.state.set_health("发烧", hours=72)
        self.assertEqual(self.state.health["since"], since)
        self.assertGreater(self.state.health["until"], until)

    def test_set_health_doctor_doubles_recovery(self):
        self.state.set_health("感冒", hours=24, treatment="看医生")
        self.assertTrue(self.state.health["doctor"])
        span = self.state.health["until"] - self.state.health["since"]
        self.assertGreaterEqual(span, 24 * 3600)

    def test_health_info_fatal_and_expired(self):
        self.state.health = {"status": "癌症"}
        info = self.state.health_info()
        self.assertEqual(info["status"], "不舒服")
        self.assertEqual(self.plugin.db.kv_get("health")["status"], "不舒服")

        self.state.health = {"status": "发烧", "until": now_ts() - 10, "since": 1}
        info = self.state.health_info()
        self.assertEqual(info["status"], "健康")
        self.assertEqual(self.plugin.db.kv_get("health")["status"], "健康")
        self.assertEqual(self.plugin.db.kv_get("health")["status"], "健康")

    def test_health_info_sick_flag_and_fields(self):
        self.state.set_health("发烧", hours=12, note="头昏", treatment="看医生", kind="发烧")
        info = self.state.health_info()
        self.assertTrue(info["sick"])
        self.assertEqual(info["note"], "头昏")
        self.assertEqual(info["treatment"], "看医生")
        self.assertEqual(info["kind"], "发烧")

    def test_roll_health_disabled_and_same_day(self):
        self.plugin.config["enable_sick_random"] = False
        self.assertIsNone(self.state.roll_health())
        self.plugin.config["enable_sick_random"] = True
        self.state.last_health_roll = today_str()
        self.assertIsNone(self.state.roll_health())

    def test_roll_health_recovers_and_sets_cooldown(self):
        self.state.health = {"status": "发烧", "since": 1, "until": now_ts() - 5}
        self.state.last_health_roll = ""
        self.state.roll_health()
        self.assertEqual(self.state.health["status"], "健康")
        self.assertGreater(self.plugin.db.kv_get("sick_cooldown_until", 0), now_ts())
        self.assertEqual(self.plugin.db.kv_get("last_health_roll"), today_str())

    def test_roll_health_rolls_illness(self):
        self.state.health = {"status": "健康", "since": now_ts(), "until": 0}
        self.state.last_health_roll = ""
        self.plugin.config["sick_prob"] = 1.0
        self.plugin.db.kv_set("sick_cooldown_until", 0)
        with mock.patch("services.health_engine.sick_probability", lambda *a: 1.0), \
                mock.patch("services.health_engine.pick_illness", lambda rng: "感冒"), \
                mock.patch.object(hs.random, "random", lambda: 0.0):
            self.state.roll_health()
        self.assertEqual(self.state.health["status"], "感冒")
        self.assertEqual(self.state.health["note"], "鼻子堵堵的，头有点沉")
        self.assertEqual(self.state.health["treatment"], "看医生")

    def test_roll_health_tired_branch(self):
        self.state.health = {"status": "健康", "since": now_ts(), "until": 0}
        self.state.last_health_roll = ""
        self.plugin.config["sick_prob"] = 1.0
        with mock.patch("services.health_engine.sick_probability", lambda *a: 1.0), \
                mock.patch("services.health_engine.pick_illness", lambda rng: "疲惫"), \
                mock.patch.object(hs.random, "random", lambda: 0.0):
            self.state.roll_health()
        self.assertEqual(self.state.health["status"], "疲惫")
        self.assertEqual(self.state.health["base_hours"], 10)

    def test_roll_health_no_roll_when_random_above_prob(self):
        self.state.health = {"status": "健康", "since": now_ts(), "until": 0}
        self.state.last_health_roll = ""
        with mock.patch("services.health_engine.sick_probability", lambda *a: 0.0), \
                mock.patch.object(hs.random, "random", lambda: 0.99):
            self.state.roll_health()
        self.assertEqual(self.state.health["status"], "健康")

    def test_set_treatment_noop_when_healthy_and_shortens(self):
        self.state.health = {"status": "健康"}
        self.assertIsNone(self.state.set_treatment("看医生"))
        self.state.set_health("感冒", hours=24)
        before = self.state.health["until"]
        self.state.set_treatment("看医生")
        self.assertTrue(self.state.health["doctor"])
        self.assertLessEqual(self.state.health["until"], before)
        self.assertEqual(self.plugin.db.kv_get("health")["treatment"], "看医生")


class SickDecisionTests(HumanBase):
    def _sick(self):
        self.state.health = {"status": "发烧", "kind": "发烧", "since": now_ts(),
                             "until": now_ts() + 86400, "note": "烧得头昏"}

    def test_not_sick_returns_cached_none(self):
        now = dt.datetime(2026, 11, 16, 8, 0)
        self.assertIsNone(self.run_async(self.state.decide_sick_action(now=now)))

    def test_weekend_and_late_hour_skip(self):
        self._sick()
        self.assertIsNone(self.run_async(self.state.decide_sick_action(
            now=dt.datetime(2026, 11, 21, 8, 0))))
        self.assertIsNone(self.run_async(self.state.decide_sick_action(
            now=dt.datetime(2026, 11, 16, 12, 0))))

    def test_cached_decision_short_circuits(self):
        self._sick()
        self.plugin.db.kv_set("sick_decision:2026-11-16",
                              {"action": "take_sick_leave", "reason": "早定了"})
        out = self.run_async(self.state.decide_sick_action(
            now=dt.datetime(2026, 11, 16, 8, 0)))
        self.assertEqual(out["reason"], "早定了")

    def test_llm_take_leave_records_deduction(self):
        self._sick()
        self.plugin.llm.reply = '{"tool": "take_sick_leave", "reason": "太难受了"}'
        before = self.state.mood["平静"]
        out = self.run_async(self.state.decide_sick_action(
            umo="test:private:1", now=dt.datetime(2026, 11, 16, 8, 0)))
        self.assertEqual(out["action"], "take_sick_leave")
        self.assertEqual(out["reason"], "太难受了")
        self.assertEqual(out["mood_word"], "虚弱但安心")
        deductions = self.plugin.db.kv_get("salary_deductions")
        self.assertEqual(len(deductions), 1)
        self.assertEqual(deductions[0]["wage_cents"], 30000)
        self.assertEqual(deductions[0]["attendance_cents"], 20000)
        self.assertEqual(deductions[0]["reason"], "sick_leave")
        self.assertGreater(self.state.mood["平静"], before)
        self.assertEqual(self.plugin.db.kv_get("sick_decision:2026-11-16")["action"],
                         "take_sick_leave")

    def test_llm_go_to_work_extends_health(self):
        self._sick()
        self.plugin.llm.reply = '{"action": "go_to_work_sick", "reason": "项目要上线"}'
        before = self.state.health["until"]
        out = self.run_async(self.state.decide_sick_action(
            now=dt.datetime(2026, 11, 16, 8, 0)))
        self.assertEqual(out["action"], "go_to_work_sick")
        self.assertEqual(out["mood_word"], "委屈又疲惫")
        self.assertIn("带病上班", self.state.health["note"])
        self.assertGreaterEqual(self.state.health["until"], before)

    def test_llm_failure_and_garbage_fall_back(self):
        self._sick()
        self.plugin.llm.reply = RuntimeError("模型挂了")
        out = self.run_async(self.state.decide_sick_action(
            now=dt.datetime(2026, 11, 16, 8, 0)))
        self.assertEqual(out["action"], "go_to_work_sick")
        self.assertTrue(out["fallback"])
        self.plugin.db.kv_set("sick_decision:2026-11-16", {})
        self.plugin.llm.reply = "不是 JSON"
        out = self.run_async(self.state.decide_sick_action(
            now=dt.datetime(2026, 11, 16, 8, 0)))
        self.assertTrue(out["fallback"])


class MoodObserveTests(HumanBase):
    def test_nudge_once_is_idempotent(self):
        before = self.state.mood["开心"]
        self.assertTrue(self.state.nudge_once("evt-1", 开心=10))
        self.assertFalse(self.state.nudge_once("evt-1", 开心=10))
        self.assertAlmostEqual(self.state.mood["开心"], before + 10, places=1)
        self.assertTrue(self.plugin.db.kv_get("mood_applied:evt-1"))
        self.assertTrue(self.state.nudge_once("", 开心=1))

    def test_mood_words_bands(self):
        cases = [(93.75, "狂喜"), (75.0, "很开心"), (60.0, "还算轻快"),
                 (43.75, "平静"), (31.25, "有点低落"), (12.5, "难过"), (-10.0, "崩溃")]
        for positive, expected in cases:
            self.reset_mood()
            for key in ("开心", "兴奋", "平静", "依恋"):
                self.state.mood[key] = positive
            for key in ("郁闷", "疲惫", "生气", "暴躁", "委屈", "焦虑"):
                self.state.mood[key] = 50.0
            self.assertIn(expected, self.state.mood_words())

    def test_observe_keyword_branches(self):
        self.reset_mood()
        self.state.observe("对不起，是我不好")
        self.assertLess(self.state.mood["生气"], MOOD_BASE["生气"])
        self.reset_mood()
        self.state.observe("你就是个废物")
        self.assertGreater(self.state.mood["生气"], MOOD_BASE["生气"])
        self.reset_mood()
        self.state.observe("我今天好难过")
        self.assertGreater(self.state.mood["郁闷"], MOOD_BASE["郁闷"])
        self.reset_mood()
        self.state.observe("我喜欢你")
        self.assertGreater(self.state.mood["依恋"], MOOD_BASE["依恋"])
        self.reset_mood()
        self.state.observe("今天真开心")
        self.assertGreater(self.state.mood["开心"], MOOD_BASE["开心"])
        self.reset_mood()
        self.state.observe("我好累好难受")
        self.assertGreater(self.state.mood["疲惫"], MOOD_BASE["疲惫"])
        self.reset_mood()
        self.state.observe("嗯")
        self.assertGreater(self.state.mood["平静"], MOOD_BASE["平静"])
        self.assertEqual(len(self.plugin.shared_calls), 7)
        self.assertEqual(self.plugin.shared_calls[-1], "嗯")

    def test_observe_night_hour_branch(self):
        self.reset_mood()
        with mock.patch.object(hs, "_now", lambda: dt.datetime(2026, 11, 16, 23, 0)):
            self.state.observe("嗯")
        self.assertGreater(self.state.mood["依恋"], MOOD_BASE["依恋"])

    def test_period_info_enabled_disabled_and_bad_start(self):
        info = self.state.period_info()
        self.assertIn(info["phase"], ("月经期", "卵泡期", "排卵期", "黄体期"))
        self.assertTrue(1 <= info["day"] <= 28)
        self.plugin.config["enable_period_simulate"] = False
        info = self.state.period_info()
        self.assertEqual(info["phase"], "非生理期")
        self.plugin.config["enable_period_simulate"] = True
        self.state.period_start = "不是日期"
        self.assertIn(self.state.period_info()["phase"], ("月经期", "卵泡期", "排卵期", "黄体期"))
        self.assertNotEqual(self.state.period_start, "不是日期")


class WorldviewToneTests(HumanBase):
    def test_ensure_worldview_cached(self):
        self.state.worldview = {"name": "旧", "_ts": now_ts()}
        out = self.run_async(self.state.ensure_worldview("u"))
        self.assertEqual(out["name"], "旧")

    def test_ensure_worldview_generates_and_renames(self):
        self.plugin.llm.worldview = {"name": "临时名"}
        self.plugin.persona_for = "新名字"
        out = self.run_async(self.state.ensure_worldview("u", force=True))
        self.assertEqual(out["name"], "新名字")
        self.assertEqual(self.plugin.persona_name, "新名字")
        self.assertEqual(self.plugin.bot_name, "新名字")
        self.assertEqual(self.plugin.db.kv_get("worldview")["name"], "新名字")

    def test_ensure_worldview_fallback(self):
        self.plugin.llm.worldview = None
        self.plugin.persona_for = ""
        out = self.run_async(self.state.ensure_worldview("u", force=True))
        self.assertEqual(out["name"], "大肥鱼")
        self.assertEqual(out["occupation"], "普通上班族")
        self.assertEqual(self.plugin.bot_name, "大肥鱼")

    def test_ensure_worldview_without_bot_name_config(self):
        self.plugin.llm.worldview = {"name": "世界观名"}
        self.plugin.persona_for = ""
        out = self.run_async(self.state.ensure_worldview("u", force=True))
        self.assertEqual(out["name"], "世界观名")

    def test_weather_and_worldview_texts(self):
        self.state.weather = {"city": "贵阳", "text": "小雨", "temp": 12,
                              "humidity": 80, "wind": "北风2级", "tag": "rain",
                              "source": "qweather", "ts": 5}
        self.assertIn("贵阳 小雨 12℃", self.state.weather_text())
        brief = self.state.weather_brief()
        self.assertEqual(brief["tag"], "rain")
        self.assertEqual(brief["ts"], 5)
        self.state.weather = {}
        self.assertEqual(self.state.weather_text(), "还不知道天气")
        self.assertEqual(self.state.weather_brief(), {})
        self.assertEqual(self.state.worldview_text(), "还没有生成世界观。")
        self.state.worldview = {"name": "小鱼", "hobbies": ["听歌", "散步"]}
        text = self.state.worldview_text()
        self.assertIn("听歌、散步", text)

    def test_tone_text_branches(self):
        def snap(mood=None, sick=False, phase="卵泡期"):
            values = {key: 50.0 for key in MOOD_KEYS}
            values.update(mood or {})
            return {"mood": values, "health": {"sick": sick}, "period": {"phase": phase}}

        self.assertIn("恋人", self.state.tone_text(snap({"暴躁": 70}), relation="相恋"))
        self.assertIn("很暴躁", self.state.tone_text(snap({"暴躁": 61})))
        self.assertIn("烦躁", self.state.tone_text(snap({"生气": 63})))
        self.assertIn("委屈", self.state.tone_text(snap({"委屈": 61})))
        self.assertIn("心情很好", self.state.tone_text(snap({"开心": 66})))
        self.assertIn("低落", self.state.tone_text(snap({"郁闷": 61})))
        self.assertIn("很累", self.state.tone_text(snap({"疲惫": 66})))
        self.assertIn("焦虑", self.state.tone_text(snap({"焦虑": 61})))
        self.assertIn("黏人", self.state.tone_text(snap({"依恋": 66})))
        self.assertIn("自然", self.state.tone_text(snap()))
        self.assertIn("身体不舒服", self.state.tone_text(snap(sick=True)))
        self.assertIn("生理期", self.state.tone_text(snap(phase="月经期")))
        self.assertIn("恋人", self.state.tone_text(snap(), relation="相恋"))
        self.assertIn("讨厌", self.state.tone_text(snap(), relation="仇恨"))
        self.assertIn("防备", self.state.tone_text(snap(), relation="抵触"))


class InitBranchTests(HumanBase):
    def _new_state(self, config_start="", seen="", stored="", ctx=None):
        tmp = tempfile.mkdtemp(prefix="amo-cov2-hs2-")
        plugin = FakePlugin(tmp, period_start=config_start)
        plugin.db.kv_set("period_config_seen", seen)
        plugin.db.kv_set("period_start", stored)
        if ctx is not None:
            plugin.db.kv_set("schedule_ctx", ctx)
            plugin.db.kv_set("schedule_profile", ctx)
        state = HumanState(plugin)
        return plugin, state

    def test_valid_config_start_applied(self):
        plugin, state = self._new_state("2026-11-01", stored="2026-10-01")
        self.assertEqual(state.period_start, "2026-11-01")
        self.assertEqual(plugin.db.kv_get("period_config_seen"), "2026-11-01")

    def test_invalid_config_start_keeps_stored(self):
        plugin, state = self._new_state("bad-date", stored="2026-10-01")
        self.assertEqual(state.period_start, "2026-10-01")

    def test_non_dict_schedule_context_falls_back(self):
        plugin, state = self._new_state(ctx=["x"])
        self.assertEqual(state.schedule_ctx, {})
        self.assertEqual(state.schedule_profile, {})


if __name__ == "__main__":
    unittest.main()
