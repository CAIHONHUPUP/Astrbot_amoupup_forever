# -*- coding: utf-8 -*-
"""弱点1「架构耦合」收口：日程单一写入口 + 体检拒绝审计 + 存储单一权威。

覆盖施工单 A：
- apply_schedule 网关：锁内读当前块 -> mutator -> 唯一提交入口；
- 旁路断言：生产代码里 db.save_schedule 只出现在 save_day，state.save_day 只在
  amo_state_schedule.py，且调用它的只有 _commit_schedule_change_locked；
- 体检拒绝写 kv schedule_audit:<day>（reason 以「体检拒绝:」开头，带 rejected）；
- kv['schedule'] 只是镜像：读侧先读表、表空才回落老 kv。
"""
from __future__ import annotations

import ast
import copy
import os
import sys
import threading
import unittest
from datetime import datetime
from unittest import mock
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_state_schedule  # noqa: E402
import amo_transition  # noqa: E402
from fixtures import FixedClock  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_state_common import today_str  # noqa: E402
from amo_state_schedule import ScheduleStateMixin, health_problems  # noqa: E402

SKIP_DIRS = {"tests", "work", "__pycache__", "docs", "data", "tmp", ".git"}


def production_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(base, name)


def enclosing_functions(tree):
    """[(函数名, [Call...])]：只统计 self.xxx(...) / x.save_day(...) 这类调用。"""
    output = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls = [child for child in ast.walk(node) if isinstance(child, ast.Call)]
            output.append((node.name, calls))
    return output


def call_name(call):
    func = call.func
    if isinstance(func, ast.Attribute):
        base = func.value
        if isinstance(base, ast.Name):
            return "%s.%s" % (base.id, func.attr)
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
            return "%s.%s.%s" % (base.value.id, base.attr, func.attr)
    return ""


class FakeDB:
    def __init__(self, rows=None):
        self.kv = {}
        self.schedules = {}
        self.versions = {}
        self.saves = 0
        if rows:
            self.schedules.update({day: copy.deepcopy(items) for day, items in rows.items()})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return copy.deepcopy(self.schedules.get(day) or [])

    def save_schedule(self, day, items):
        self.saves += 1
        self.schedules[day] = copy.deepcopy(list(items or []))

    def schedule_version(self, day):
        return self.versions.get(day, 0)

    def bump_schedule_version(self, day):
        self.versions[day] = self.versions.get(day, 0) + 1
        return self.versions[day]

    def mark_diaries_stale(self, day, from_hour, version):
        return []

    def all_schedules(self):
        return copy.deepcopy(self.schedules)


class FakePlugin:
    def __init__(self, db):
        self.db = db

    def cfg_int(self, key, default=0):
        return default

    def cfg_bool(self, key, default=False):
        return default

    def _data_bot_id(self):
        return "amo"

    def _spawn(self, coro):
        try:
            coro.close()
        except Exception:
            pass


class Harness(ScheduleStateMixin):
    """真实网关/提交/审计实现 + 假 db；文案归一换成浅拷贝。"""

    def __init__(self, db):
        self.p = FakePlugin(db)
        self.db = db
        self.schedule = {}
        self.schedule_ctx = {}
        self._schedule_commit_lock = threading.RLock()

    def _jit_enabled(self):
        return False

    def _normalize_schedule_items(self, items, source, day=""):
        return [dict(row) if isinstance(row, dict) else row for row in items]


def row(start, end, activity, **extra):
    data = {"start": start, "end": end, "activity": activity}
    data.update(extra)
    return data


class SingleWriterBypassTests(unittest.TestCase):
    def test_gateway_is_the_only_read_modify_write_entry(self):
        text = open(os.path.join(ROOT, "amo_state_schedule.py"), encoding="utf-8").read()
        self.assertIn("def apply_schedule(self, day, mutator", text)
        self.assertIn("def _schedule_current_items(self, day)", text)
        self.assertIn("with self._schedule_commit_lock:", text)
        # 网关自己不允许落库：只能交回 commit_schedule_change。
        gateway = text.split("def apply_schedule(self, day, mutator", 1)[1]
        gateway = gateway.split("\n    def commit_schedule_change", 1)[0]
        self.assertIn("with self._schedule_commit_lock:", gateway)
        self.assertIn("self._schedule_current_items(day)", gateway)
        self.assertIn("self.commit_schedule_change(", gateway)
        self.assertNotIn("save_day(", gateway)

    def test_save_schedule_only_inside_save_day(self):
        violations = []
        for path in production_files():
            tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            for func_name, calls in enclosing_functions(tree):
                for call in calls:
                    if call_name(call) == "self.db.save_schedule" and func_name != "save_day":
                        violations.append("%s:%s" % (os.path.basename(path), func_name))
        self.assertEqual(violations, [], violations)

    def test_state_save_day_only_in_schedule_module(self):
        for path in production_files():
            name = os.path.basename(path)
            if name == "amo_state_schedule.py":
                continue
            text = open(path, encoding="utf-8").read()
            self.assertNotIn(".save_day(", text, name)

    def test_save_day_called_only_by_locked_commit(self):
        path = os.path.join(ROOT, "amo_state_schedule.py")
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        owners = set()
        for func_name, calls in enclosing_functions(tree):
            for call in calls:
                if call_name(call) == "self.save_day":
                    owners.add(func_name)
        self.assertEqual(owners, {"_commit_schedule_change_locked"}, owners)

    def test_runtime_tick_does_not_bypass_commit(self):
        text = open(os.path.join(ROOT, "amo_runtime_tick.py"), encoding="utf-8").read()
        self.assertNotIn("state.save_day(", text)
        self.assertIn('source="snapshot"', text)


class GatewayAuditTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.state = Harness(self.db)
        self.day = today_str()
        self.state.schedule = {self.day: []}

    def _entries(self):
        return list(self.db.kv_get("schedule_audit:%s" % self.day, []) or [])

    def test_chat_gateway_is_audited(self):
        result = self.state.apply_schedule(
            self.day,
            lambda rows: list(rows) + [row("08:00", "09:00", "看书", place="家", zone="home")],
            source="chat", reason="聊天改日程")
        self.assertTrue(result.get("changed"), result)
        entry = self._entries()[-1]
        self.assertEqual(entry["source"], "chat")
        self.assertEqual(entry["reason"], "聊天改日程")
        self.assertGreaterEqual(entry["version"], 1)
        self.assertTrue(self.db.schedules[self.day], "表里没有落库")

    def test_panel_api_goes_through_gateway_and_is_audited(self):
        from amo_api_schedule import ScheduleApiMixin

        before = row("10:00", "11:00", "写周报", place="工位", zone="office")
        self.db.schedules[self.day] = [copy.deepcopy(before)]
        self.state.schedule = {self.day: [copy.deepcopy(before)]}
        after = row("10:30", "11:30", "开会", place="会议室", zone="office")

        class Api(ScheduleApiMixin):
            def __init__(self, db, state):
                self.db = db
                self.state = state

        api = Api(self.db, self.state)
        payload = api._schedule_commit_edit(
            self.day, [after], [before], "update", before, after,
            reason="面板改块", operator="面板")
        self.assertEqual(payload["code"], 0, payload)
        sources = [entry["source"] for entry in self._entries()]
        self.assertIn("manual_edit_api", sources, sources)

    def test_transit_gateway_is_audited(self):
        before = [row("15:30", "16:00", "在家", place="家", zone="home")]
        self.db.schedules[self.day] = copy.deepcopy(before)
        self.state.schedule = {self.day: copy.deepcopy(before)}
        items = before + [row("16:00", "17:00", "在路上", place="路上", zone="road")]
        plugin = SimpleNamespace(state=self.state)
        result = amo_transition._commit(plugin, self.day, items, before)
        self.assertIsInstance(result, dict, result)
        sources = [entry["source"] for entry in self._entries()]
        self.assertIn("transit", sources, sources)


class HealthRejectAuditTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.state = Harness(self.db)
        self.day = today_str()

    def test_health_reject_writes_audit_and_keeps_schedule(self):
        old = [row("18:30", "19:30", "在家休息", place="家", zone="home", category="rest")]
        self.db.schedules[self.day] = copy.deepcopy(old)
        self.state.schedule = {self.day: copy.deepcopy(old)}
        bad = [row("19:00", "20:00", "在公司加班", place="公司·work", zone="office",
                   category="work")]
        with FixedClock(datetime(2026, 11, 18, 19, 0, 0)):
            day = today_str()
            self.day = day
            self.state.schedule = {day: copy.deepcopy(old)}
            self.db.schedules[day] = copy.deepcopy(old)
            with mock.patch.object(amo_state_schedule, "logger") as fake_logger:
                result = self.state.commit_schedule_change(
                    day, copy.deepcopy(bad), old_items=copy.deepcopy(old),
                    source="chat", reason="用户要求加班")
        self.assertFalse(result.get("changed"), result)
        self.assertTrue(result.get("rejected"), result)
        self.assertEqual(health_problems(copy.deepcopy(old)), [])
        self.assertTrue(any("office_out_of_window" in item
                            for item in result["rejected"]), result["rejected"])
        self.assertEqual(self.state.schedule[day], old, "被拒绝的写入不该改动日程")
        self.assertEqual(self.db.get_schedule(day), old)
        entries = self.db.kv_get("schedule_audit:%s" % day, []) or []
        self.assertTrue(entries, "体检拒绝没有写审计")
        entry = entries[-1]
        self.assertTrue(str(entry.get("reason") or "").startswith("体检拒绝:"), entry)
        self.assertTrue(entry.get("rejected"), entry)
        self.assertEqual(entry.get("changed_hours"), [])
        self.assertTrue(fake_logger.warning.called, "体检拒绝必须 logger.warning")
        warnings = " ".join(str(call) for call in fake_logger.warning.call_args_list)
        self.assertIn("日程体检不通过", warnings)


class MirrorAuthorityTests(unittest.TestCase):
    def _reader(self, db):
        obj = SimpleNamespace(db=db)
        return HumanState._load_schedule_cache(obj)

    def test_reader_prefers_table_over_kv(self):
        day = today_str()
        db = FakeDB()
        db.schedules[day] = [row("08:00", "09:00", "表里的块")]
        db.kv["schedule"] = {day: [row("08:00", "09:00", "kv 里的旧块")]}
        loaded = self._reader(db)
        self.assertEqual(loaded[day][0]["activity"], "表里的块")

    def test_reader_falls_back_to_kv_when_table_empty(self):
        day = today_str()
        db = FakeDB()
        db.kv["schedule"] = {day: [row("08:00", "09:00", "老库的块")]}
        loaded = self._reader(db)
        self.assertEqual(loaded[day][0]["activity"], "老库的块")

    def test_kv_mirror_is_rebuilt_from_table(self):
        day = today_str()
        db = FakeDB()
        db.schedules[day] = [row("08:00", "09:00", "表里的块")]
        state = Harness(db)
        state.save_day(day, [row("09:00", "10:00", "新写的块")])
        mirror = db.kv.get("schedule") or {}
        self.assertEqual(mirror[day][0]["activity"], "新写的块")
        self.assertEqual(db.schedules[day][0]["activity"], "新写的块")


if __name__ == "__main__":
    unittest.main()
