# -*- coding: utf-8 -*-
"""cov2: amo_schedule 的改日程/自己下班/天气提醒/大日记取料/跨零点补写。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_schedule as sched  # noqa: E402
from amo_schedule import ScheduleMixin  # noqa: E402


class FakeLogger:
    def __init__(self):
        self.records = []

    def _add(self, level, message):
        self.records.append((level, str(message)))

    def warning(self, m, *a, **k):
        self._add("warning", m)

    def info(self, m, *a, **k):
        self._add("info", m)

    def debug(self, m, *a, **k):
        self._add("debug", m)

    def error(self, m, *a, **k):
        self._add("error", m)


class Config(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = 0
        self.fail_save = False

    def save_config(self):
        self.saved += 1
        if self.fail_save:
            raise RuntimeError("保存失败")


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}
        self.versions = {}
        self.profiles = []
        self.conn = None

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return [dict(row) for row in (self.schedules.get(day) or [])]

    def schedule_version(self, day):
        return self.versions.get(day, 0)

    def proactive_profiles(self, limit=20):
        return list(self.profiles)[:limit]


class FakeConn:
    def __init__(self, block_rows=None, hour_rows=None, fail_block=False):
        self.block_rows = block_rows or []
        self.hour_rows = hour_rows or []
        self.fail_block = fail_block
        self.queries = []

    def execute(self, sql, params):
        self.queries.append((sql, params))
        if "block_diary" in sql:
            if self.fail_block:
                raise RuntimeError("没有 block_diary 表")
            rows = self.block_rows
        else:
            rows = self.hour_rows
        return types.SimpleNamespace(fetchall=lambda: list(rows))


class FakeState:
    def __init__(self, plugin):
        self.p = plugin
        self.schedule = {}
        self.commits = []
        self.ensure_calls = []
        self.current = {"activity": "上班", "place": "公司", "category": "work",
                        "start": "09:00"}
        self.current_raises = False
        self.health = {"status": "健康", "sick": False}
        self.set_health_calls = []
        self.set_treatment_calls = []
        self.refresh_calls = []
        self.daily_items = []

    def current_activity(self):
        if self.current_raises:
            raise RuntimeError("没有当前活动")
        return dict(self.current)

    async def ensure_schedule(self, umo):
        self.ensure_calls.append(umo)

    def commit_schedule_change(self, day, items, old_items=None, source="", reason=""):
        self.commits.append((day, [dict(row) for row in items], source, reason))
        self.schedule[day] = [dict(row) for row in items]
        return {"changed": True}

    def health_info(self):
        return dict(self.health)

    def set_health(self, status, **kwargs):
        self.set_health_calls.append((status, kwargs))
        self.health = {"status": status, "sick": True}

    def set_treatment(self, treatment):
        self.set_treatment_calls.append(treatment)

    def mood_words(self):
        return "还算轻快"

    def snapshot(self):
        return {"activity": {"activity": "刷手机", "category": "life"}}

    def prompt_text(self, snap=None):
        return "状态提示"

    async def refresh_weather(self, force=False):
        self.refresh_calls.append(force)
        return {"text": "晴", "temp": 20}

    async def _fetch_qweather_daily(self, key, city, days=3, fresh=False):
        return list(self.daily_items)


class FakeLLM:
    def __init__(self):
        self.reply = "{}"
        self.chat_reply = ""
        self.raise_chat = False
        self.chat_calls = []

    async def chat_raw(self, umo, system, prompt):
        self.chat_calls.append((umo, prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    async def persona(self, umo):
        return "人设"

    async def chat(self, umo, system, prompt, limit=60):
        self.chat_calls.append((umo, prompt))
        if self.raise_chat:
            raise RuntimeError("模型挂了")
        return self.chat_reply


class FakeEvent:
    def __init__(self, private=True, sender="u1", umo="qq:private:u1"):
        self.private = private
        self.sender = sender
        self.unified_msg_origin = umo

    def is_private_chat(self):
        return self.private

    def get_sender_id(self):
        return self.sender


class Host(ScheduleMixin):
    def __init__(self):
        self.db = FakeDB()
        self.config = Config({"schedule_change_enabled": True,
                              "weather_alert_enabled": True,
                              "hefeng_key": "K",
                              "weather_city": "101260101",
                              "weather_city_name": "贵阳"})
        self.state = FakeState(self)
        self.llm = FakeLLM()
        self.gift = None
        self.wallet = types.SimpleNamespace(balance=lambda: 12345)
        self._last_umo = "qq:private:u9"
        self._diary_tasks = {}
        self.spawned = []
        self.sent = []
        self.send_ok = True
        self.recent_rows = []
        self._first_private = ""
        self._city_hit = None

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def _canonical_uid(self, event):
        return getattr(event, "sender", "")

    def _first_private_umo(self):
        return self._first_private

    def _spawn_diary_task(self, day, item, items, version, reason="slot_ended"):
        self.spawned.append((day, str(item.get("start") or ""), reason))

    def _recent(self, umo, limit):
        return list(self.recent_rows)

    async def _send_to_umo(self, umo, text):
        self.sent.append((umo, text))
        return self.send_ok

    def _data_mem(self):
        return getattr(self, "mem", None)

    async def _data_call(self, name, *args, **kwargs):
        return await getattr(self.mem, name)(*args)

    def _data_bot_id(self):
        return "bot"

    def _city_lookup(self, raw):
        return self._city_hit


def row(start, end, activity="休息", **extra):
    item = {"start": start, "end": end, "activity": activity}
    item.update(extra)
    return item


class Base(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.logger = FakeLogger()
        self._saved = sched.logger
        sched.logger = self.logger

    def tearDown(self):
        sched.logger = self._saved

    def run_async(self, coro):
        return asyncio.run(coro)


class ChangeAllowedTests(Base):
    def test_disabled_and_group_and_event_error(self):
        self.host.config["schedule_change_enabled"] = False
        self.assertFalse(self.host._schedule_change_allowed(FakeEvent()))
        self.host.config["schedule_change_enabled"] = True
        self.assertFalse(self.host._schedule_change_allowed(FakeEvent(private=False)))
        class Boom:
            def is_private_chat(self):
                raise RuntimeError("x")
        self.assertFalse(self.host._schedule_change_allowed(Boom()))

    def test_owner_ids_match_uid_or_sender(self):
        self.host.config["schedule_change_owner_ids"] = "u1，u2"
        self.assertTrue(self.host._schedule_change_allowed(FakeEvent(sender="u1")))
        self.assertTrue(self.host._schedule_change_allowed(FakeEvent(sender="u2")))
        self.assertFalse(self.host._schedule_change_allowed(FakeEvent(sender="u3")))

    def test_owner_ids_uid_error_falls_back_to_sender(self):
        self.host.config["schedule_change_owner_ids"] = "u1"
        class BoomUid(Host):
            def _canonical_uid(self, event):
                raise RuntimeError("x")
        host = BoomUid()
        host.config["schedule_change_owner_ids"] = "u1"
        self.assertTrue(host._schedule_change_allowed(FakeEvent(sender="u1")))

    def test_gift_owner_and_gift_error(self):
        self.host.gift = types.SimpleNamespace(is_owner=lambda event: True)
        self.assertTrue(self.host._schedule_change_allowed(FakeEvent()))
        self.host.gift = types.SimpleNamespace(
            is_owner=lambda event: (_ for _ in ()).throw(RuntimeError("x")))
        self.host._first_private = ""
        self.assertTrue(self.host._schedule_change_allowed(FakeEvent()))

    def test_home_private_umo_gate(self):
        self.host._first_private = "qq:private:home"
        self.assertTrue(self.host._schedule_change_allowed(
            FakeEvent(umo="qq:private:home")))
        self.assertFalse(self.host._schedule_change_allowed(
            FakeEvent(umo="qq:private:other")))

    def test_no_home_umo_allows(self):
        self.host._first_private = ""
        self.assertTrue(self.host._schedule_change_allowed(FakeEvent()))


class JudgeTests(Base):
    def test_no_llm_fallback(self):
        self.host.llm = None
        out = self.run_async(self.host._judge_schedule_proposal(FakeEvent(), {}, []))
        self.assertFalse(out["agree"])
        self.assertTrue(out["fallback"])

    def test_state_errors_defaults(self):
        self.host.state.current_raises = True
        self.host.wallet = types.SimpleNamespace(
            balance=lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.host.state.mood_words = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        self.host.llm.reply = '{"tool": "update_schedule", "reason": "想去"}'
        out = self.run_async(self.host._judge_schedule_proposal(
            FakeEvent(), {"activity": "逛街"}, []))
        self.assertTrue(out["agree"])
        self.assertEqual(out["reason"], "想去")
        prompt = self.host.llm.chat_calls[-1][1]
        self.assertIn("余额：0.00 元", prompt)
        self.assertIn("逛街", prompt)

    def test_keep_schedule_and_garbage(self):
        self.host.llm.reply = '{"tool": "keep_schedule", "reason": "太累"}'
        out = self.run_async(self.host._judge_schedule_proposal(
            FakeEvent(), {"activity": "爬山"}, []))
        self.assertFalse(out["agree"])
        self.assertFalse(out["fallback"])
        self.host.llm.reply = "不是 JSON"
        out = self.run_async(self.host._judge_schedule_proposal(
            FakeEvent(), {"activity": "爬山"}, []))
        self.assertFalse(out["agree"])
        self.assertEqual(out["reason"], "")

    def test_llm_error_fallback(self):
        self.host.llm.reply = RuntimeError("模型挂了")
        out = self.run_async(self.host._judge_schedule_proposal(FakeEvent(), {}, []))
        self.assertFalse(out["agree"])
        self.assertTrue(out["fallback"])
        self.assertEqual(out["reason"], "没想好，先不改")
        self.assertTrue(any("决策失败" in m for _, m in self.logger.records))


class ApplyChangeTests(Base):
    def setUp(self):
        super().setUp()
        self._saved_detect = sched.detect_schedule_change
        self._saved_apply = sched.apply_sched_change
        self._saved_pajama = sched.should_switch_to_pajamas
        self._saved_travel = sched.TRAVEL_WORDS
        sched.TRAVEL_WORDS = ("旅游", "出差")
        sched.detect_schedule_change = lambda text: {"activity": "回家", "place": "家",
                                                     "category": "rest"}
        sched.apply_sched_change = lambda items, change, now: [dict(row, activity="回家",
                                                                     place="家")
                                                               for row in items]
        sched.should_switch_to_pajamas = lambda change, text: True

    def tearDown(self):
        sched.detect_schedule_change = self._saved_detect
        sched.apply_sched_change = self._saved_apply
        sched.should_switch_to_pajamas = self._saved_pajama
        sched.TRAVEL_WORDS = self._saved_travel
        super().tearDown()

    def test_helper_missing_returns(self):
        sched.detect_schedule_change = None
        self.run_async(self.host._apply_user_schedule_change(FakeEvent(), "回家"))
        self.assertEqual(self.host.state.commits, [])

    def test_no_change_travel_clears_home_early(self):
        sched.detect_schedule_change = lambda text: None
        self.host.db.kv_set("home_early:2026-11-18", True)
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"):
            self.run_async(self.host._apply_user_schedule_change(FakeEvent(), "我要去旅游"))
        self.assertFalse(self.host.db.kv_get("home_early:2026-11-18"))

    def test_not_allowed_skips(self):
        self.host.config["schedule_change_owner_ids"] = "someone"
        self.run_async(self.host._apply_user_schedule_change(FakeEvent(sender="u9"), "回家"))
        self.assertEqual(self.host.state.ensure_calls, [])

    def test_no_schedule_skips(self):
        self.run_async(self.host._apply_user_schedule_change(FakeEvent(), "回家"))
        self.assertEqual(self.host.state.commits, [])
        self.assertEqual(len(self.host.state.ensure_calls), 1)

    def test_no_change_from_helper_skips(self):
        sched.apply_sched_change = lambda items, change, now: items
        self.host.state.schedule["2026-11-18"] = [row("09:00", "18:00", "上班")]
        self.host.llm.reply = '{"tool": "update_schedule"}'
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"):
            self.run_async(self.host._apply_user_schedule_change(FakeEvent(), "回家"))
        self.assertEqual(self.host.state.commits, [])

    def test_rejected_records_proposal(self):
        self.host.state.schedule["2026-11-18"] = [row("09:00", "18:00", "上班")]
        self.host.llm.reply = '{"tool": "keep_schedule", "reason": "还在忙"}'
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 10, 0)):
            self.run_async(self.host._apply_user_schedule_change(FakeEvent(), "回家"))
        proposal = self.host.db.kv_get("schedule_proposal")
        self.assertEqual(proposal["agree"], False)
        self.assertEqual(proposal["reason"], "还在忙")
        self.assertEqual(self.host.state.commits, [])

    def test_agreed_commits_and_sets_pajamas(self):
        self.host.state.schedule["2026-11-18"] = [row("09:00", "18:00", "上班")]
        self.host.llm.reply = '{"tool": "update_schedule", "reason": "好"}'
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 10, 0)):
            self.run_async(self.host._apply_user_schedule_change(FakeEvent(), "回家"))
        self.assertEqual(len(self.host.state.commits), 1)
        self.assertEqual(self.host.state.commits[0][2], "chat")
        self.assertEqual(self.host.db.kv_get("current_action"), {})
        self.assertTrue(self.host.db.kv_get("home_early:2026-11-18"))
        self.assertTrue(any("改了日程" in m for _, m in self.logger.records))

    def test_travel_clears_pajamas_after_agree(self):
        sched.should_switch_to_pajamas = lambda change, text: False
        self.host.state.schedule["2026-11-18"] = [row("09:00", "18:00", "上班")]
        self.host.llm.reply = '{"tool": "update_schedule"}'
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 10, 0)):
            self.run_async(self.host._apply_user_schedule_change(FakeEvent(), "去旅游"))
        self.assertFalse(self.host.db.kv_get("home_early:2026-11-18"))

    def test_health_branch_sets_health_and_treatment(self):
        sched.detect_schedule_change = lambda text: {"activity": "回家休息", "place": "家",
                                                     "category": "health"}
        self.host.state.schedule["2026-11-18"] = [row("09:00", "18:00", "上班")]
        self.host.llm.reply = '{"tool": "update_schedule"}'
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 10, 0)):
            self.run_async(self.host._apply_user_schedule_change(
                FakeEvent(), "我发烧了，要去看医生"))
        self.assertEqual(self.host.state.set_health_calls[0][0], "发烧")
        self.assertEqual(self.host.state.set_health_calls[0][1]["treatment"], "看医生")

        sched.detect_schedule_change = lambda text: {"activity": "回家", "place": "家",
                                                     "category": "health"}
        self.host.state.health = {"status": "发烧", "sick": True}
        self.host.state.schedule["2026-11-18"] = [row("09:00", "18:00", "上班")]
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 10, 0)):
            self.run_async(self.host._apply_user_schedule_change(FakeEvent(), "去打吊水"))
        self.assertEqual(self.host.state.set_treatment_calls, ["打吊水"])

    def test_health_status_variants(self):
        self.host.state.schedule["2026-11-18"] = [row("09:00", "18:00", "上班")]
        self.host.llm.reply = '{"tool": "update_schedule"}'
        for text, expected in (("我肠胃炎了", "肠胃炎"), ("感冒了", "感冒"),
                               ("咳嗽得厉害", "咳嗽"), ("不太舒服", "生病")):
            sched.detect_schedule_change = lambda value: {"activity": "休息", "place": "家",
                                                          "category": "health"}
            self.host.state.schedule["2026-11-18"] = [row("09:00", "18:00", "上班")]
            self.host.state.health = {"status": "健康", "sick": False}
            self.host.state.set_health_calls = []
            with mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                    mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 10, 0)):
                self.run_async(self.host._apply_user_schedule_change(FakeEvent(), text))
            self.assertEqual(
                (text, [call[0] for call in self.host.state.set_health_calls],
                 self.host.state.commits, self.host.state.set_treatment_calls),
                (text, [expected], self.host.state.commits, []))


class OffworkTests(Base):
    def test_regex_gates(self):
        cases = ("今天真开心", "我还没下班", "他下班了", "你下班了吗")
        for text in cases:
            self.host.state.commits = []
            self.host._maybe_own_offwork_sync(text)
            self.assertEqual(self.host.state.commits, [], text)

    def test_before_five_pm(self):
        with mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 16, 0)):
            self.host._maybe_own_offwork_sync("我下班了")
        self.assertEqual(self.host.state.commits, [])

    def test_guard_already_set(self):
        self.host.db.kv_set("own_offwork:2026-11-18", True)
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 18, 0)):
            self.host._maybe_own_offwork_sync("我下班了")
        self.assertEqual(self.host.state.commits, [])

    def test_not_work_category(self):
        self.host.state.current = {"category": "rest", "start": "09:00"}
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 18, 0)):
            self.host._maybe_own_offwork_sync("我下班了")
        self.assertEqual(self.host.state.commits, [])

    def test_no_matching_start(self):
        self.host.state.schedule["2026-11-18"] = [row("08:00", "09:00", "上班")]
        self.host.state.current = {"category": "work", "start": "09:00"}
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 18, 0)):
            self.host._maybe_own_offwork_sync("我下班了")
        self.assertEqual(self.host.state.commits, [])

    def test_success_rewrites_current_block(self):
        item = row("09:00", "18:00", "上班", place="公司", zone="office", category="work")
        self.host.state.schedule["2026-11-18"] = [item]
        self.host.state.current = {"category": "work", "start": "09:00"}
        with mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 18, 0)):
            self.host._maybe_own_offwork_sync("我下班了")
        committed = self.host.state.commits[0][1][0]
        self.assertEqual(committed["activity"], "下班回家，路上")
        self.assertEqual(committed["category"], "commute")
        self.assertEqual(committed["zone"], "road")
        self.assertTrue(self.host.db.kv_get("own_offwork:2026-11-18"))
        self.assertTrue(any("她说下班了" in m for _, m in self.logger.records))


class WeatherAlertTests(Base):
    def test_umos_from_channels_and_private(self):
        self.host.db.profiles = [
            {"uid": "u1", "private_umo": "qq:private:u1"},
            {"uid": "u2", "private_umo": ""},
        ]
        self.host.db.kv["channels:u1"] = {"lark": {"umo": "lark:private:u1", "ts": 99},
                                          "bad": "x"}
        self.host.db.kv["channels:u2"] = {"qq": {"umo": "qq:private:u2", "ts": 50}}
        self.assertEqual(self.host._weather_alert_umos(),
                         ["lark:private:u1", "qq:private:u2", "qq:private:u1"])
        self.assertEqual(self.host._pick_weather_alert_umo(), "lark:private:u1")

    def test_umos_fallback_and_error(self):
        class BoomDB(FakeDB):
            def proactive_profiles(self, limit=20):
                raise RuntimeError("x")
        self.host.db = BoomDB()
        self.host._last_umo = "qq:private:last"
        self.assertEqual(self.host._weather_alert_umos(), ["qq:private:last"])
        self.host._last_umo = ""
        self.host._first_private = "qq:private:first"
        self.assertEqual(self.host._weather_alert_umos(), ["qq:private:first"])
        self.host._first_private = ""
        self.assertEqual(self.host._weather_alert_umos(), [])
        self.assertEqual(self.host._pick_weather_alert_umo(), "")

    def test_clean_alert(self):
        clean = self.host._clean_weather_alert
        self.assertEqual(clean("“天气提醒：明天有雨”"), "明天有雨")
        self.assertEqual(clean("请注意保暖"), "")
        self.assertEqual(clean("提醒您带伞"), "")
        self.assertEqual(clean("x" * 81), "")
        self.assertEqual(clean(None), "")

    def test_alert_text_template_and_model(self):
        self.host.recent_rows = [{"role": "user", "content": "今天好累"},
                                 {"role": "bot", "content": "抱抱"},
                                 {"role": "user", "content": ""}]
        self.host.llm.chat_reply = "明天要降温了，多穿点"
        text = self.run_async(self.host._weather_alert_text(
            "u", {"textDay": "小雨", "textNight": "阴", "tempMax": 12}, "明天有雨"))
        self.assertEqual(text, "明天要降温了，多穿点")
        prompt = self.host.llm.chat_calls[-1][1]
        self.assertIn("明天天气：小雨 阴", prompt)
        self.assertIn("明天最高 12℃", prompt)
        self.assertIn("他：今天好累", prompt)
        self.assertIn("参考说法（不要照抄）：明天有雨", prompt)

    def test_alert_text_falls_back_on_bad_model_or_state(self):
        self.host.state.prompt_text = lambda snap=None: (_ for _ in ()).throw(RuntimeError("x"))
        self.host.llm.chat_reply = "请注意保暖"
        text = self.run_async(self.host._weather_alert_text("u", {}, "模板句"))
        self.assertEqual(text, "模板句")
        self.host.llm.raise_chat = True
        text = self.run_async(self.host._weather_alert_text("u", {}, "模板句"))
        self.assertEqual(text, "模板句")

    def test_alert_text_recent_error(self):
        self.host._recent = lambda umo, limit: (_ for _ in ()).throw(RuntimeError("x"))
        text = self.run_async(self.host._weather_alert_text("u", {}, "模板"))
        self.assertEqual(text, "模板")


class WeatherQueryTests(Base):
    def test_no_city(self):
        with mock.patch.object(sched, "city_or_empty", lambda plugin: ""):
            text = self.run_async(self.host._weather_query_context("天气"))
        self.assertIn("还没设置城市", text)

    def test_refresh_error_uses_state_weather(self):
        self.host.state.refresh_weather = lambda **kw: (_ for _ in ()).throw(RuntimeError("x"))
        self.host.state.weather = {"text": "晴", "temp": 18}
        self.host.state.daily_items = [{"date": "d1"}, {"date": "d2"}]
        with mock.patch.object(sched, "city_or_empty", lambda plugin: "贵阳"), \
                mock.patch.object(sched, "forecast_brief",
                                  lambda now, today, tomorrow: "晴 18℃"):
            text = self.run_async(self.host._weather_query_context("天气"))
        self.assertEqual(text, "【天气查询】晴 18℃")

    def test_daily_error_and_empty_brief(self):
        self.host.state._fetch_qweather_daily = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("x"))
        with mock.patch.object(sched, "city_or_empty", lambda plugin: "贵阳"), \
                mock.patch.object(sched, "forecast_brief", lambda *a: ""):
            text = self.run_async(self.host._weather_query_context("天气"))
        self.assertIn("取不到天气数据", text)

    def test_no_key_skips_daily(self):
        self.host.config["hefeng_key"] = ""
        self.host.state.daily_items = [{"date": "d1"}]
        with mock.patch.object(sched, "city_or_empty", lambda plugin: "贵阳"), \
                mock.patch.object(sched, "forecast_brief", lambda *a: "简报"):
            text = self.run_async(self.host._weather_query_context("天气"))
        self.assertEqual(text, "【天气查询】简报")


class MaybeAlertTests(Base):
    def setUp(self):
        super().setUp()
        self.host.state.daily_items = [
            {"textDay": "小雨", "tempMax": 12, "tempMin": 5},
            {"textDay": "中雨", "tempMax": 9, "tempMin": 3},
        ]
        self.host.state.snapshot = lambda: {"activity": {"category": "work"}}
        self._patches = [
            mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 20, 0)),
            mock.patch.object(sched, "today_str", lambda: "2026-11-18"),
            mock.patch.object(sched, "city_or_empty", lambda plugin: "贵阳"),
            mock.patch.object(sched, "parse_clock",
                              lambda text, h, m: 20 * 60 if text == "20:00" else 8 * 60),
            mock.patch.object(sched, "next_check_slot",
                              lambda cur, morning, evening, checked, today: "evening"),
            mock.patch.object(sched, "slot_key", lambda day, slot: day + "|" + slot),
            mock.patch.object(sched, "evaluate_weather_alert",
                              lambda today, tomorrow, drop: "明天要大幅降温"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        super().tearDown()

    def test_disabled_or_no_key(self):
        self.host.config["weather_alert_enabled"] = False
        self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.sent, [])
        self.host.config["weather_alert_enabled"] = True
        self.host.config["hefeng_key"] = ""
        self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.sent, [])

    def test_already_sent_and_no_slot(self):
        self.host.db.kv_set("weather_alert_sent", "2026-11-18")
        self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.sent, [])
        self.host.db.kv["weather_alert_sent"] = ""
        with mock.patch.object(sched, "next_check_slot", lambda *a: ""):
            self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.sent, [])

    def test_no_city_marks_checked(self):
        with mock.patch.object(sched, "city_or_empty", lambda plugin: ""):
            self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.db.kv_get("weather_alert_checked"), "2026-11-18|evening")
        self.assertEqual(self.host.sent, [])

    def test_short_forecast_marks_checked(self):
        self.host.state.daily_items = [{"textDay": "小雨"}]
        self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.db.kv_get("weather_alert_checked"), "2026-11-18|evening")
        self.assertEqual(self.host.sent, [])

    def test_normal_weather_marks_checked_without_sending(self):
        with mock.patch.object(sched, "evaluate_weather_alert", lambda *a: ""):
            self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.db.kv_get("weather_alert_checked"), "2026-11-18|evening")
        self.assertEqual(self.host.sent, [])
        self.assertTrue(any("明天天气正常" in m for _, m in self.logger.records))

    def test_morning_only_logs(self):
        with mock.patch.object(sched, "next_check_slot", lambda *a: "morning"):
            self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.sent, [])
        self.assertTrue(any("早上" in m for _, m in self.logger.records))

    def test_evening_sends_to_first_working_umo(self):
        self.host.db.profiles = [{"uid": "u1", "private_umo": "qq:private:u1"},
                                 {"uid": "u2", "private_umo": "qq:private:u2"}]
        self.host.llm.chat_reply = "明天要降温啦"
        self.host._send_to_umo = self._flaky_send
        self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.db.kv_get("weather_alert_sent"), "2026-11-18")
        self.assertEqual(self.host.db.kv_get("weather_alert_open"), "2026-11-18")
        self.assertEqual([item[0] for item in self.host.sent],
                         ["qq:private:u1", "qq:private:u2"])
        self.assertTrue(any("换下一个" in m for _, m in self.logger.records))

    async def _flaky_send(self, umo, text):
        self.host.sent.append((umo, text))
        return umo != "qq:private:u1"

    def test_dry_run_does_not_send(self):
        self.host.config["weather_alert_dry_run"] = True
        self.host.llm.chat_reply = "明天要降温啦"
        self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.sent, [])
        self.assertEqual(self.host.db.kv_get("weather_alert_sent"), "2026-11-18")

    def test_all_platforms_fail_resets_checked(self):
        self.host.send_ok = False
        self.host.llm.chat_reply = "明天要降温啦"
        self.run_async(self.host._maybe_weather_alert())
        self.assertEqual(self.host.db.kv_get("weather_alert_checked"), "")
        self.assertEqual(self.host.db.kv_get("weather_alert_sent", ""), "")


class CitySwitchTests(Base):
    def test_switch_guard_and_no_change(self):
        self.run_async(self.host._switch_weather_city({}))
        self.run_async(self.host._switch_weather_city({"id": "101260101", "name": "贵阳"}))
        self.assertEqual(self.host.state.refresh_calls, [])

    def test_switch_updates_config_and_refreshes(self):
        self.run_async(self.host._switch_weather_city({"id": "101010100", "name": "北京"}))
        self.assertEqual(self.host.config["weather_city"], "101010100")
        self.assertEqual(self.host.config["weather_city_name"], "北京")
        self.assertEqual(self.host.config.saved, 1)
        self.assertEqual(self.host.state.refresh_calls, [True])

    def test_switch_save_error_still_refreshes(self):
        self.host.config.fail_save = True
        self.run_async(self.host._switch_weather_city({"id": "101010100", "name": "北京"}))
        self.assertEqual(self.host.state.refresh_calls, [True])
        self.assertTrue(any("保存天气城市失败" in m for _, m in self.logger.records))

    def test_sync_city_skips_non_names(self):
        for raw in ("", "101260101", "106.7,26.6"):
            self.host.config["weather_city"] = raw
            self.assertFalse(self.host._sync_weather_city_config())

    def test_sync_city_unknown_warns_once(self):
        self.host.config["weather_city"] = "不存在城"
        self.host._city_hit = None
        now = [10000.0]
        with mock.patch.object(sched._amo_clock, "now_ts", lambda: now[0]):
            self.assertFalse(self.host._sync_weather_city_config())
            self.assertFalse(self.host._sync_weather_city_config())
            self.assertEqual(len([m for _, m in self.logger.records if "离线城市表" in m]), 1)
            now[0] += 4000
            self.assertFalse(self.host._sync_weather_city_config())
            self.assertEqual(len([m for _, m in self.logger.records if "离线城市表" in m]), 2)

    def test_sync_city_ambiguous_and_backfill(self):
        self.host.config["weather_city"] = "苏州"
        self.host.config["weather_city_name"] = ""
        self.host._city_hit = {"id": "101", "name": "苏州", "ambiguous": True,
                               "note": "有多个同名城市"}
        now = [10000.0]
        with mock.patch.object(sched._amo_clock, "now_ts", lambda: now[0]):
            self.assertTrue(self.host._sync_weather_city_config())
            self.assertEqual(self.host.config["weather_city"], "101")
            self.assertEqual(self.host.config["weather_city_name"], "苏州")
            self.assertEqual(self.host.config.saved, 1)
            self.host.config["weather_city"] = "苏州"
            self.host.config["weather_city_name"] = "苏州"
            self.assertTrue(self.host._sync_weather_city_config())
            self.assertEqual(len([m for _, m in self.logger.records if "有多个同名城市" in m]), 1)

    def test_sync_city_save_error(self):
        self.host.config["weather_city"] = "苏州"
        self.host.config["weather_city_name"] = ""
        self.host._city_hit = {"id": "101", "name": "苏州"}
        self.host.config.fail_save = True
        self.assertTrue(self.host._sync_weather_city_config())
        self.assertTrue(any("回填城市配置失败" in m for _, m in self.logger.records))


class DiaryLinesTests(Base):
    def test_chat_lines_no_mem_and_ok(self):
        self.assertEqual(self.run_async(self.host._diary_chat_lines(dt.date(2026, 11, 18))), [])
        rows = [{"content": "你好\n呀", "role": "user", "name": "阿远"},
                {"content": "在呢", "role": "bot"},
                {"content": "", "role": "user"},
                {"content": "系统", "role": "system"},
                {"content": "群里的", "role": "user", "platform": "group", "scope": "group"}]
        self.host.mem = types.SimpleNamespace(
            messages_between=lambda *args: self._async_rows(rows))
        with mock.patch.object(sched, "_private_rows", lambda items: [
                item for item in items if item.get("platform") != "group"]):
            lines = self.run_async(self.host._diary_chat_lines(dt.date(2026, 11, 18)))
        self.assertEqual(lines, ["阿远：你好 呀", "我：在呢"])

    async def _async_rows(self, rows):
        return rows

    def test_chat_lines_error(self):
        async def boom(*args):
            raise RuntimeError("x")
        self.host.mem = types.SimpleNamespace(messages_between=boom)
        self.assertEqual(self.run_async(self.host._diary_chat_lines(dt.date(2026, 11, 18))), [])

    def test_hourly_lines_block_and_travel(self):
        self.host.db.conn = FakeConn(block_rows=[("09:00", "开了会"), ("10:00", "")],
                                     hour_rows=[("09:00", "旧")])
        self.host.db.kv["travel_log"] = [
            {"day": "2026-11-18", "city": "苏州", "hour": "11:00", "solo": True},
            {"day": "2026-11-18", "city": "苏州", "hour": "12:00", "solo": True, "back": True},
            {"day": "2026-11-18", "city": "北京", "hour": "13:00", "journey": True},
            {"day": "2026-11-18", "city": "上海", "hour": "14:00"},
            {"day": "2026-11-18", "city": "上海", "hour": "15:00", "back": True},
            {"day": "2026-11-18", "city": ""},
            "bad",
        ]
        self.host.db.kv["follow_reject_log"] = [
            {"day": "2026-11-18", "city": "苏州", "hour": "16:00"},
            {"day": "2026-11-17", "city": "苏州"}, "bad", {"day": "2026-11-18", "city": ""},
        ]
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True):
            lines = self.host._diary_hourly_lines(dt.date(2026, 11, 18))
        self.assertEqual(lines[0], "09:00 开了会")
        self.assertIn("11:00 她自己出门去苏州旅游，晚上住酒店", lines)
        self.assertIn("12:00 她自己从苏州旅游回来了", lines)
        self.assertIn("13:00 她坐车去北京陪他，路上约 2 小时", lines)
        self.assertIn("14:00 跟他出门到了上海，晚上住酒店", lines)
        self.assertIn("15:00 跟他从上海回来了", lines)
        self.assertIn("16:00 她说想陪他去苏州，他说让她好好上班，她闷闷不乐", lines)

    def test_hourly_lines_fallback_to_old_table(self):
        self.host.db.conn = FakeConn(block_rows=[], hour_rows=[("08:00", "旧表内容")])
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True):
            lines = self.host._diary_hourly_lines(dt.date(2026, 11, 18))
        self.assertEqual(lines, ["08:00 旧表内容"])

    def test_hourly_lines_errors(self):
        self.host.db.conn = types.SimpleNamespace(
            execute=lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertEqual(self.host._diary_hourly_lines(dt.date(2026, 11, 18)), [])


class BackfillTests(Base):
    def _plugin(self):
        return self.host

    def test_backfill_blocks(self):
        from amo_timeline import Timeline
        items = [row("08:00", "09:00", "早饭"), row("09:00", "10:00", "上班")]
        generated = sched._backfill_blocks(
            self.host, "2026-11-18", items, {}, 1, 2, lambda day, start, end: True)
        self.assertEqual(generated, 2)
        self.assertEqual([item[1] for item in self.host.spawned], ["09:00", "08:00"])
        self.assertEqual(sched._backfill_blocks(self.host, "2026-11-18", [], {}, 1, 2,
                                                lambda *a: True), 0)
        self.assertEqual(sched._backfill_blocks(self.host, "2026-11-18", items, {}, 1, 0,
                                                lambda *a: True), 0)

    def test_backfill_blocks_skips_valid_existing_and_tasks(self):
        items = [row("08:00", "09:00", "早饭"), row("09:00", "10:00", "上班")]
        self.host.db.block_diary_is_valid = (
            lambda day, bid, version: bid == "09:00")
        with mock.patch.object(sched, "block_id", lambda day, item: item.get("start")):
            generated = sched._backfill_blocks(
                self.host, "2026-11-18", items, {("2026-11-18", "08:00"): object()}, 1, 5,
                lambda *a: True)
        self.assertEqual((generated, self.host.spawned), (0, []))
        self.host.db.block_diary_is_valid = lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
        with mock.patch.object(sched, "block_id", lambda day, item: item.get("start")):
            self.assertEqual(sched._backfill_blocks(
                self.host, "2026-11-18", items, {}, 1, 5, lambda *a: True), 0)

    def test_backfill_hours(self):
        self.host.state._hourly_schedule = lambda items: [
            {"start": "09:00", "end": "10:00", "activity": "上班"},
            {"start": "10:00", "end": "11:00", "activity": "上班"},
        ]
        generated = sched._backfill_hours(
            self.host, "2026-11-18", [], {}, 1, 2, lambda *a: True)
        self.assertEqual(generated, 2)
        self.host.db.diary_is_valid = lambda day, start, version: start == "10:00"
        self.host.spawned = []
        generated = sched._backfill_hours(
            self.host, "2026-11-18", [], {("2026-11-18", "09:00"): object()}, 1, 2,
            lambda *a: True)
        self.assertEqual(generated, 0)

    def test_backfill_rollover_block_mode(self):
        yesterday = dt.date(2026, 11, 17)
        self.host.state.schedule[yesterday.isoformat()] = [row("23:00", "24:00", "睡觉")]
        class FakeTimeline:
            def __init__(self, plugin):
                pass

            def blocks(self, day):
                return [row("22:00", "23:00", "刷手机"), row("23:00", "24:00", "睡觉")]

            def last_ended(self, day, minute):
                return None

        with mock.patch.object(sched, "Timeline", FakeTimeline), \
                mock.patch.object(sched, "block_diary_enabled", lambda plugin: True):
            sched._backfill_rollover(self.host, dt.datetime(2026, 11, 18, 0, 30),
                                     {}, lambda *a: True)
        self.assertEqual(self.host.spawned[-1][1], "23:00")
        self.assertEqual(self.host.spawned[-1][2], "day_rollover")

    def test_backfill_rollover_guards(self):
        self.host.spawned = []
        sched._backfill_rollover(self.host, dt.datetime(2026, 11, 18, 0, 30),
                                 {}, lambda *a: True)
        self.assertEqual(self.host.spawned, [])
        self.host.state.schedule["2026-11-17"] = [row("23:00", "24:00", "睡觉")]
        class EmptyTimeline:
            def __init__(self, plugin):
                pass

            def blocks(self, day):
                return []

        with mock.patch.object(sched, "Timeline", EmptyTimeline), \
                mock.patch.object(sched, "block_diary_enabled", lambda plugin: True):
            sched._backfill_rollover(self.host, dt.datetime(2026, 11, 18, 0, 30),
                                     {}, lambda *a: True)
        self.assertEqual(self.host.spawned, [])

    def test_backfill_rollover_hour_mode(self):
        self.host.state.schedule["2026-11-17"] = [row("23:00", "24:00", "睡觉")]
        self.host.state._hourly_schedule = lambda items: [{"start": "23:00", "end": "24:00"}]
        self.host.spawned = []
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: False):
            sched._backfill_rollover(self.host, dt.datetime(2026, 11, 18, 0, 30),
                                     {}, lambda *a: True)
        self.assertEqual(self.host.spawned[-1][1], "23:00")
        self.host.db.diary_is_valid = lambda *a: True
        self.host.spawned = []
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: False):
            sched._backfill_rollover(self.host, dt.datetime(2026, 11, 18, 0, 30),
                                     {}, lambda *a: True)
        self.assertEqual(self.host.spawned, [])


class TimeRuleTests(Base):
    def test_clean_diary_text(self):
        text = "第一段\n[系统提示：日程又变了]\n第二段\n<正在重写>"
        cleaned = self.host._clean_diary_text(text)
        self.assertEqual(cleaned, "第一段\n\n第二段")
        self.assertEqual(self.host._clean_diary_text(None), "")

    def test_time_rules_finished_day(self):
        with mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 9, 0)):
            text = sched._diary_time_rules(self.host, dt.date(2026, 11, 17))
        self.assertIn("已经过完的 2026-11-17", text)
        self.assertNotIn("她还没睡", text)

    def test_time_rules_today_late_and_pending(self):
        self.host.db.kv["pending_delivery"] = "快递明天到"
        with mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 23, 0)):
            text = sched._diary_time_rules(self.host, None)
        self.assertIn("今天的事实只到 23:00", text)
        self.assertIn("她还没睡", text)
        self.assertIn("快递明天到", text)
        self.assertIn("写期待", text)

    def test_time_rules_sleeping_and_errors(self):
        self.host.state.snapshot = lambda: {"activity": {"category": "sleep"}}
        self.host.db.kv["pending_delivery"] = None
        with mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 23, 0)):
            text = sched._diary_time_rules(self.host, None)
        self.assertNotIn("她还没睡", text)
        self.host.state.snapshot = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        self.assertEqual(sched._diary_time_rules(self.host, None).count("今天的事实只到"), 1)


class DiaryRuleHelpers(Base):
    def test_end_day_and_window(self):
        now = dt.datetime(2026, 11, 18, 7, 30)
        self.assertEqual(sched.diary_end_day(now, 8, 0), dt.date(2026, 11, 17))
        self.assertIsNone(sched.diary_end_day(dt.datetime(2026, 11, 18, 21, 0), 22, 0))
        self.assertEqual(sched.diary_end_day(dt.datetime(2026, 11, 18, 22, 5), 22, 0),
                         dt.date(2026, 11, 18))
        self.assertTrue(sched.diary_in_window(dt.datetime(2026, 11, 18, 8, 10), 8, 0))
        self.assertFalse(sched.diary_in_window(dt.datetime(2026, 11, 18, 7, 50), 8, 0))
        self.assertTrue(sched.diary_in_window(dt.datetime(2026, 11, 18, 23, 50), 23, 45))
        self.assertFalse(sched.diary_in_window(dt.datetime(2026, 11, 18, 12, 0), None, 0))

    def test_manual_target(self):
        now = dt.datetime(2026, 11, 18, 10, 0)
        self.assertEqual(sched.diary_manual_target(now, None), (dt.date(2026, 11, 17), ""))
        self.assertEqual(sched.diary_manual_target(now, dt.date(2026, 11, 10)),
                         (dt.date(2026, 11, 10), ""))
        day, error = sched.diary_manual_target(now, dt.date(2026, 11, 18))
        self.assertIsNone(day)
        self.assertIn("第二天会自动写", error)


if __name__ == "__main__":
    unittest.main()
