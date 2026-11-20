# -*- coding: utf-8 -*-
"""每日日记质检（amo_diary_audit）：HARD/WARN 判定、幂等、补审、通知、面板/命令/CLI。

每条用例都钉住一个真实回退：去掉 mark_block_diary_stale 空文本用例就红；去掉幂等
重复审计用例就红；去掉 lookback 补审最旧优先用例就红；通知关掉 quiet/幂等也各有用例。
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import importlib.util
import inspect
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

import amo_diary_audit as audit  # noqa: E402
from amo_api_diary import DiaryApiMixin  # noqa: E402
from amo_commands import CommandMixin  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_runtime import RuntimeMixin  # noqa: E402
from amo_state_common import block_id  # noqa: E402

NOW = datetime.datetime(2026, 11, 16, 10, 0)
DAY = "2026-11-15"


def _block(start, end, category="rest", place="家", zone="home", activity="休息"):
    return {"start": start, "end": end, "activity": activity, "place": place,
            "zone": zone, "category": category}


class FakePlugin:
    """真 AmoDB + 最小插件壳：审计只需要配置、db、空 state 和 push_to_owner。"""

    def __init__(self, tmp, config=None):
        self.config = dict(config or {})
        self.db = AmoDB(tmp)
        self.db.init()
        self.state = types.SimpleNamespace(schedule={})
        self.owner_messages = []

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    async def push_to_owner(self, text):
        self.owner_messages.append(str(text))
        return True


class AuditBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-audit-")
        self.plugin = FakePlugin(self.tmp)
        self.day = DAY
        self.now = NOW

    def tearDown(self):
        with contextlib.suppress(Exception):
            self.plugin.db.close()

    def seed(self, blocks, day=None):
        day = day or self.day
        self.plugin.state.schedule[day] = [dict(block) for block in blocks]
        return self.plugin.state.schedule[day]

    def save(self, block, text, day=None, status="valid", key_activity=None):
        day = day or self.day
        target = dict(block)
        if key_activity is not None:
            target["activity"] = key_activity
        key = block_id(day, target)
        self.plugin.db.save_block_diary(
            day, key, text, start=str(block.get("start") or ""),
            end=str(block.get("end") or ""), place=str(block.get("place") or ""),
            version=0, status=status)
        return key

    def run_day(self, apply=True, day=None, now=None):
        return audit.run(self.plugin, day or self.day, apply=apply,
                         now=now or self.now)

    def kinds(self, report, bucket):
        return [entry.get("kind") for entry in report.get(bucket) or []]


class HardTests(AuditBase):
    def test_missing_diary_is_hard(self):
        self.seed([_block("09:00", "10:00")])
        report = self.run_day()
        self.assertEqual(report["counts"]["hard"], 1)
        self.assertEqual(self.kinds(report, "hard"), ["missing"])

    def test_empty_diary_is_hard_and_marked_stale(self):
        # 反向验证：去掉 run() 里的 mark_block_diary_stale 调用，status 会停在 valid。
        block = _block("09:00", "10:00")
        self.seed([block])
        key = self.save(block, "")
        report = self.run_day()
        self.assertEqual(self.kinds(report, "hard"), ["empty"])
        self.assertEqual(report["stale"], 1)
        record = self.plugin.db.get_block_diary_record(self.day, key)
        self.assertEqual(record["status"], "stale")

    def test_place_forbidden_is_hard(self):
        block = _block("09:00", "10:00", category="health", place="医院",
                       zone="hospital", activity="在医院排队")
        self.seed([block])
        self.save(block, "在医院排队，冷得缩进被窝里等叫号。" * 2)
        report = self.run_day()
        self.assertIn("place_forbidden", self.kinds(report, "hard"))

    def test_block_id_mismatch_is_hard(self):
        block = _block("09:00", "10:00", activity="休息")
        self.seed([block])
        self.save(block, "旧块身份写下的正文，这一天其实看过电视。", key_activity="看电视")
        report = self.run_day()
        self.assertEqual(self.kinds(report, "hard"), ["block_mismatch"])

    def test_block_id_mismatch_with_empty_row_end_is_hard(self):
        block = _block("09:00", "10:00", activity="休息")
        self.seed([block])
        old_key = block_id(self.day, _block("09:00", "10:00", activity="看电视"))
        self.plugin.db.save_block_diary(
            self.day, old_key, "旧迁移行留下的正文，那会儿还在看电视。",
            start="09:00", end="", place="家", version=0, status="stale")
        report = self.run_day()
        self.assertIn("block_mismatch", self.kinds(report, "hard"))

    def test_only_ended_blocks_are_audited(self):
        self.day = "2026-11-16"
        ended = _block("08:00", "09:00")
        future = _block("20:00", "21:00")
        self.seed([ended, future])
        self.save(ended, "早上在家慢慢收拾，出门前还晒了会儿太阳。")
        report = self.run_day()
        self.assertEqual(report["counts"]["blocks"], 1)
        self.assertEqual(report["counts"]["hard"], 0)

    def test_duplicate_same_block_counted_once(self):
        block = _block("09:00", "10:00")
        self.seed([dict(block), dict(block)])
        report = self.run_day()
        self.assertEqual(report["counts"]["blocks"], 1)
        self.assertEqual(report["counts"]["hard"], 1)

    def test_sleep_placeholder_not_hard(self):
        sleep = _block("23:00", "24:00", category="sleep", activity="睡觉")
        self.seed([sleep])
        report = self.run_day()
        self.assertEqual(report["counts"]["hard"], 0)


class WarnTests(AuditBase):
    def test_duplicate_prefix_is_warn(self):
        first = _block("09:00", "10:00")
        second = _block("10:00", "11:00")
        self.seed([first, second])
        text = "今天忙了一整天，下午连开两个会，回到家脑子还在嗡嗡响，只想躺着。"
        self.save(first, text)
        self.save(second, text)
        report = self.run_day()
        self.assertIn("dup_prefix", self.kinds(report, "warn"))
        self.assertEqual(report["counts"]["hard"], 0)

    def test_time_word_mismatch_is_warn(self):
        block = _block("09:00", "10:00")
        self.seed([block])
        self.save(block, "上午一直在家里改东西，晚上八点半还在忙，等回过神来已经十一点，赶紧洗漱准备睡觉。")
        report = self.run_day()
        self.assertIn("time_word", self.kinds(report, "warn"))

    def test_common_quantifier_yidian_is_not_a_time(self):
        block = _block("16:00", "17:00")
        self.seed([block])
        self.save(block, "屋里有点热，天色一点点暗下去，肚子有点饿，这身衣服一点不勒，"
                        "就这么耗到饭点吧，懒得再动了。")
        self.assertNotIn("time_word", self.kinds(self.run_day(), "warn"))

    def test_ambiguous_hour_accepted_when_other_reading_fits(self):
        # 「快四点」在 15:00-16:00 的块里是下午四点；不能按 04:00 误报时间可疑。
        block = _block("15:00", "16:00")
        self.seed([block])
        self.save(block, "一觉睡到快四点，醒过来的时候天还是灰蒙蒙的，鼻子好像通了点，"
                        "人清醒了，就是还有点懵。")
        self.assertNotIn("time_word", self.kinds(self.run_day(), "warn"))

    def test_long_or_short_text_is_warn(self):
        short = _block("09:00", "10:00")
        long_block = _block("10:00", "11:00")
        self.seed([short, long_block])
        self.save(short, "嗯。")
        self.save(long_block, "啊" * 420)
        kinds = self.kinds(self.run_day(), "warn")
        self.assertIn("too_short", kinds)
        self.assertIn("too_long", kinds)


class ApplyTests(AuditBase):
    def test_apply_false_does_not_mark_stale(self):
        block = _block("09:00", "10:00")
        self.seed([block])
        key = self.save(block, "")
        report = self.run_day(apply=False)
        self.assertEqual(report["counts"]["hard"], 1)
        self.assertFalse(report["applied"])
        self.assertIsNone(self.plugin.db.kv_get(audit.KEY_DONE + self.day, None))
        record = self.plugin.db.get_block_diary_record(self.day, key)
        self.assertEqual(record["status"], "valid")

    def test_idempotent_run_does_not_remark(self):
        # 反向验证：去掉 diary_audit_done 幂等，第二次会重新判定并再标一次 stale。
        block = _block("09:00", "10:00")
        self.seed([block])
        self.save(block, "")
        calls = []
        original = self.plugin.db.mark_block_diary_stale

        def spy(day, bid):
            calls.append((day, bid))
            return original(day, bid)

        self.plugin.db.mark_block_diary_stale = spy
        first = self.run_day()
        second = self.run_day()
        self.assertEqual(len(calls), 1)
        self.assertTrue(second.get("cached"))
        self.assertEqual(first["counts"]["hard"], 1)
        self.assertTrue(self.plugin.db.kv_get(audit.KEY_DONE + self.day, False))

    def test_report_kv_shape(self):
        self.seed([_block("09:00", "10:00")])
        report = self.run_day()
        stored = self.plugin.db.kv_get(audit.KEY_REPORT + self.day, {})
        self.assertEqual(stored["counts"]["hard"], report["counts"]["hard"])
        self.assertTrue(stored["hard"])
        self.assertEqual(set(stored["hard"][0]), {"start", "end", "kind"})
        self.assertIn("at", stored)
        self.assertIsInstance(stored["warn"], list)


class TriggerTests(AuditBase):
    def test_lookback_audits_oldest_one_per_tick(self):
        self.plugin.config["diary_audit_lookback_days"] = 3
        self.assertEqual(audit.next_day(self.plugin, self.now), "2026-11-13")
        first = asyncio.run(audit.tick(self.plugin, self.now))
        self.assertEqual(first["day"], "2026-11-13")
        second = asyncio.run(audit.tick(self.plugin, self.now))
        self.assertEqual(second["day"], "2026-11-14")
        third = asyncio.run(audit.tick(self.plugin, self.now))
        self.assertEqual(third["day"], "2026-11-15")
        self.assertEqual(audit.next_day(self.plugin, self.now), "")

    def test_lookback_skips_audited_days(self):
        self.plugin.config["diary_audit_lookback_days"] = 3
        self.plugin.db.kv_set(audit.KEY_DONE + "2026-11-13", True)
        self.plugin.db.kv_set(audit.KEY_DONE + "2026-11-14", True)
        self.assertEqual(audit.next_day(self.plugin, self.now), DAY)

    def test_disabled_does_not_run(self):
        self.plugin.config["diary_audit_enabled"] = False
        result = asyncio.run(audit.tick(self.plugin, self.now))
        self.assertEqual(result["day"], "")
        for offset in (1, 2, 3):
            day = (self.now.date() - datetime.timedelta(days=offset)).isoformat()
            self.assertIsNone(self.plugin.db.kv_get(audit.KEY_DONE + day, None))
            self.assertIsNone(self.plugin.db.kv_get(audit.KEY_REPORT + day, None))

    def test_housekeeping_trigger_calls_audit_tick(self):
        source = inspect.getsource(RuntimeMixin._tick_housekeeping)
        self.assertIn("_diary_audit_tick", source)
        self.assertIn("_last_diary_audit", source)

    def test_audit_tick_method_delegates(self):
        plugin = self.plugin
        calls = []

        async def fake_tick(target, now=None):
            calls.append(target)
            return {"day": "x", "report": {}, "notified": False}

        with mock.patch.object(audit, "tick", fake_tick):
            result = asyncio.run(RuntimeMixin._diary_audit_tick(plugin))
        self.assertEqual(calls, [plugin])
        self.assertEqual(result["day"], "x")


class NotifyTests(AuditBase):
    def _seed_report(self, hard=1, warn=0):
        report = {
            "day": DAY,
            "counts": {"blocks": 3, "hard": hard, "warn": warn},
            "hard": [{"start": "09:00", "end": "10:00", "kind": "missing"}] * hard,
            "warn": [{"start": "10:00", "end": "11:00", "kind": "time_word"}] * warn,
            "at": 1,
        }
        self.plugin.db.kv_set(audit.KEY_REPORT + DAY, report)
        return report

    def test_notify_sends_once_after_hour(self):
        self._seed_report(hard=2, warn=1)
        sent = asyncio.run(audit.notify(self.plugin, self.now))
        self.assertTrue(sent)
        self.assertEqual(len(self.plugin.owner_messages), 1)
        self.assertIn("日记质检", self.plugin.owner_messages[0])
        again = asyncio.run(audit.notify(self.plugin, self.now))
        self.assertFalse(again)
        self.assertEqual(len(self.plugin.owner_messages), 1)

    def test_notify_skips_quiet_hours(self):
        self._seed_report()
        late = datetime.datetime(2026, 11, 16, 23, 30)
        self.assertFalse(asyncio.run(audit.notify(self.plugin, late)))
        self.assertEqual(self.plugin.owner_messages, [])

    def test_notify_skips_clean_report(self):
        self._seed_report(hard=0, warn=2)
        self.assertFalse(asyncio.run(audit.notify(self.plugin, self.now)))
        self.assertEqual(self.plugin.owner_messages, [])

    def test_notify_skips_before_hour(self):
        self._seed_report()
        early = datetime.datetime(2026, 11, 16, 8, 0)
        self.assertFalse(asyncio.run(audit.notify(self.plugin, early)))
        self.assertEqual(self.plugin.owner_messages, [])

    def test_notify_disabled_by_config(self):
        self.plugin.config["diary_audit_notify"] = False
        self._seed_report()
        self.assertFalse(asyncio.run(audit.notify(self.plugin, self.now)))
        self.assertEqual(self.plugin.owner_messages, [])


class _Panel(DiaryApiMixin):
    def __init__(self, plugin):
        self.plugin = plugin
        self.db = plugin.db
        self.payload = {}
        self.query = {}

    async def _ensure_ready(self):
        return None

    async def _body(self):
        return dict(self.payload)

    def _q(self, key, default=None):
        return self.query.get(key, default)


class _Command(CommandMixin):
    def __init__(self, plugin, admin=True):
        self.plugin = plugin
        self.db = plugin.db
        self.config = plugin.config
        self._admin = admin

    def _is_admin(self, event):
        return self._admin

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))


class PanelCommandTests(AuditBase):
    def test_panel_route_returns_report_by_day(self):
        self.seed([_block("09:00", "10:00")])
        self.run_day()
        panel = _Panel(self.plugin)
        panel.query = {"day": self.day}
        result = asyncio.run(panel.api_diary_audit())
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["day"], self.day)
        self.assertEqual(result["data"]["report"]["counts"]["hard"], 1)
        panel.query = {"day": "2026-13-40"}
        result = asyncio.run(panel.api_diary_audit())
        self.assertEqual(result["code"], 1)

    def test_latest_report_day_uses_report(self):
        self.seed([_block("09:00", "10:00")])
        self.run_day()
        self.assertEqual(audit.latest_report_day(self.plugin, self.now), self.day)

    def test_command_summary_and_admin_gate(self):
        self.seed([_block("09:00", "10:00")])
        self.run_day()
        cmd = _Command(self.plugin, admin=True)
        event = types.SimpleNamespace()
        text = asyncio.run(cmd._cmd_audit(event, ["audit", self.day], "1"))
        self.assertIn("日记质检", text)
        self.assertIn(self.day, text)
        denied = asyncio.run(_Command(self.plugin, admin=False)._cmd_audit(
            event, ["audit", self.day], "1"))
        self.assertIn("管理员", denied)
        bad = asyncio.run(cmd._cmd_audit(event, ["audit", "13/40"], "1"))
        self.assertIn("YYYY-MM-DD", bad)


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(ROOT, "work", "diary_audit.py")
        spec = importlib.util.spec_from_file_location("amo_work_diary_audit", path)
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def test_target_days_and_date_parse(self):
        args = argparse.Namespace(date="", days=2)
        self.assertEqual(self.cli._target_days(args, NOW), ["2026-11-14", "2026-11-15"])
        self.assertEqual(self.cli._parse_day("2026-11-15"), "2026-11-15")
        self.assertEqual(self.cli._parse_day("15/11"), "")
        with self.assertRaises(SystemExit):
            self.cli._target_days(argparse.Namespace(date="bad", days=3), NOW)


if __name__ == "__main__":
    unittest.main()
