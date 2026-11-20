# -*- coding: utf-8 -*-
"""弱项收口：日程审计、单一落库入口、行为不变量。"""
from __future__ import annotations

import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for path in (ROOT, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_human_state import HumanState  # noqa: E402
from amo_state_schedule import ScheduleStateMixin, health_problems  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class AuditTests(unittest.TestCase):
    def test_audit_records_before_after_and_reason(self):
        holder = ScheduleStateMixin.__new__(ScheduleStateMixin)
        holder.db = FakeDB()
        old = [{"start": "21:23", "end": "23:00", "activity": "在工位上忙工作",
                "place": "公司", "zone": "office", "source": "transit"}]
        new = [{"start": "21:23", "end": "23:00", "activity": "在家休息",
                "place": "家", "zone": "home", "source": "transit"}]
        holder._audit_schedule("2026-11-18", "transit", "过渡分块", 7, [21], old, new)
        rows = holder.db.kv_get("schedule_audit:2026-11-18")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "transit")
        self.assertEqual(row["reason"], "过渡分块")
        self.assertEqual(row["changed_hours"], [21])
        self.assertEqual(row["before"][0]["activity"], "在工位上忙工作")
        self.assertEqual(row["after"][0]["activity"], "在家休息")

    def test_audit_keeps_last_200(self):
        holder = ScheduleStateMixin.__new__(ScheduleStateMixin)
        holder.db = FakeDB()
        for index in range(205):
            holder._audit_schedule("2026-11-18", "jit", "tick %d" % index, index, [], [], [])
        rows = holder.db.kv_get("schedule_audit:2026-11-18")
        self.assertEqual(len(rows), 200)
        self.assertIn("tick 204", rows[-1]["reason"])


class SingleWriteEntryTests(unittest.TestCase):
    ALLOWED = {"amo_db.py", "amo_state_schedule.py"}

    def test_only_save_day_touches_db_save_schedule(self):
        callers = []
        for base, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", ".git", "work", "tools", "plugin_data")]
            for name in names:
                if not name.endswith(".py") or name in self.ALLOWED:
                    continue
                path = os.path.join(base, name)
                try:
                    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
                except (OSError, SyntaxError):
                    continue
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "save_schedule"):
                        callers.append("%s:%d" % (os.path.basename(path), node.lineno))
        self.assertEqual(callers, [], "日程必须只经 save_day 落库，发现直调 db.save_schedule: %s" % callers)

    def test_commit_is_the_single_public_entry(self):
        text = open(os.path.join(ROOT, "amo_state_schedule.py"), encoding="utf-8").read()
        # 允许 commit 内部调用一次 locked 实现；外部模块不允许直接调 _commit_schedule_change_locked。
        offenders = []
        for base, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", ".git", "work", "tools", "plugin_data")]
            for name in names:
                if not name.endswith(".py") or name == "amo_state_schedule.py":
                    continue
                path = os.path.join(base, name)
                body = open(path, encoding="utf-8").read()
                if "_commit_schedule_change_locked" in body:
                    offenders.append(os.path.basename(path))
        self.assertEqual(offenders, [], offenders)


class BehaviorSpecTests(unittest.TestCase):
    def _day(self):
        return [
            {"start": "00:00", "end": "07:00", "activity": "睡觉", "place": "家",
             "zone": "home", "category": "sleep"},
            {"start": "07:00", "end": "09:00", "activity": "洗漱吃早饭", "place": "家",
             "zone": "home", "category": "life"},
            {"start": "09:00", "end": "18:00", "activity": "在工位上忙工作", "place": "公司",
             "zone": "office", "category": "work"},
            {"start": "18:00", "end": "21:30", "activity": "窝在家里的晚上", "place": "家",
             "zone": "home", "category": "life"},
            {"start": "21:30", "end": "23:59", "activity": "睡觉", "place": "家",
             "zone": "home", "category": "sleep"},
        ]

    def test_normal_workday_satisfies_invariants(self):
        rows = HumanState._enforce_zone_activity([dict(r) for r in self._day()], "2026-11-19", "jit")
        self.assertEqual(health_problems(rows), [])

    def test_night_office_injection_is_folded_and_invariants_hold(self):
        # 用「替换 21:30 睡觉段」的方式注入，不人为造重叠（去重叠是 commit 层的职责）。
        rows = [dict(r) for r in self._day() if r["start"] != "21:30"]
        rows.append({"start": "21:30", "end": "23:59", "activity": "在工位上忙工作",
                     "place": "公司", "zone": "office", "category": "work"})
        rows = HumanState._enforce_zone_activity(rows, "2026-11-19", "transit")
        self.assertEqual(health_problems(rows), [])
        self.assertFalse([r for r in rows if r.get("zone") == "office" and str(r.get("start")) >= "18:00"])

    def test_sick_day_has_no_office(self):
        rows = [dict(r, category="work", zone="office", place="公司",
                     activity="在工位上忙工作") for r in self._day()]
        rows = HumanState._enforce_zone_activity(rows, "2026-11-19", "jit", sick=True)
        self.assertFalse([r for r in rows if "公司" in str(r.get("place"))])


if __name__ == "__main__":
    unittest.main()
