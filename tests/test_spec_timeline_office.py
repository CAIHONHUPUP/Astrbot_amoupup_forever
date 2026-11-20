# -*- coding: utf-8 -*-
"""产品级行为规格 A：整日时间轴不得出现「公司·上班」。

用户可感知的承诺（2026-11-18）：
- 工作日 18:00 之后不许再去公司；
- 病假当天全天都不许出现公司/上班；
- 周末加班也只允许 09:00-18:00，18:00 之后照样回家。

入口只用稳定接口：HumanState 的唯一写入口 Timeline.save（内部 commit_schedule_change）
+ Timeline.blocks 读回最终块；断言看 zone/activity/区间，不碰面板 JSON 字段。
"""
from __future__ import annotations

import datetime
import os
import shutil
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for path in (ROOT, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import clock  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_numbers import minutes_of  # noqa: E402
from amo_schedule import ScheduleMixin  # noqa: E402
from amo_timeline import Timeline  # noqa: E402

DAY_END = 24 * 60
#: 「公司」的三种可观察写法：地点/区域/文案任一出圈都算违约。
OFFICE_ZONE = "office"
OFFICE_WORDS = ("公司", "上班", "工位")


def _row(start, end, activity, place, zone, category, **extra):
    row = {"start": start, "end": end, "activity": activity, "place": place,
           "zone": zone, "category": category}
    row.update(extra)
    return row


def _span(row):
    begin = minutes_of(row.get("start"))
    end = minutes_of(row.get("end"))
    return begin, end


def _workday_plan(office_start="20:00", office_end="24:00"):
    """一份完整的 24 小时工作日计划，末尾故意带一段「公司·上班」。"""
    return [
        _row("00:00", "07:00", "睡觉", "家", "home", "sleep"),
        _row("07:00", "08:00", "起床洗漱", "家", "home", "life"),
        _row("08:00", "09:00", "通勤去公司", "路上", "road", "commute"),
        _row("09:00", "12:00", "在工位上忙工作", "公司", "office", "work"),
        _row("12:00", "13:00", "在公司食堂吃午饭", "公司食堂", "office", "meal"),
        _row("13:00", "18:00", "在工位上忙工作", "公司", "office", "work"),
        _row("18:00", "19:00", "下班回家", "路上", "road", "commute"),
        _row("19:00", office_start, "在家休息", "家", "home", "rest"),
        _row(office_start, office_end, "回公司加班", "公司", "office", "work"),
    ]


def _sick_day_plan():
    """病假当天：上班时段也排着「公司·上班」，总闸必须把它们全部折回家。"""
    return [
        _row("00:00", "07:00", "睡觉", "家", "home", "sleep"),
        _row("07:00", "08:00", "起床洗漱", "家", "home", "life"),
        _row("08:00", "09:00", "通勤去公司", "路上", "road", "commute"),
        _row("09:00", "12:00", "在工位上忙工作", "公司", "office", "work"),
        _row("12:00", "13:00", "在公司食堂吃午饭", "公司食堂", "office", "meal"),
        _row("13:00", "18:00", "在工位上忙工作", "公司", "office", "work"),
        _row("18:00", "19:00", "下班回家", "路上", "road", "commute"),
        _row("19:00", "24:00", "在家休息", "家", "home", "rest"),
    ]


def _weekend_plan():
    """周六：白天允许加班（09-18），18 点之后再回公司必须被折回家。"""
    return [
        _row("00:00", "08:00", "睡觉", "家", "home", "sleep"),
        _row("08:00", "09:00", "起床洗漱", "家", "home", "life"),
        _row("09:00", "12:00", "周末加班", "公司", "office", "work"),
        _row("12:00", "13:00", "在公司食堂吃午饭", "公司食堂", "office", "meal"),
        _row("13:00", "18:00", "周末加班", "公司", "office", "work"),
        _row("18:00", "19:00", "下班回家", "路上", "road", "commute"),
        _row("19:00", "24:00", "回公司加班", "公司", "office", "work"),
    ]


class FakePlugin(ScheduleMixin):
    """够 HumanState + 写入口用的轻量插件桩（不装事件总线，走兼容失效分支）。"""

    def __init__(self, tmp, config=None):
        self.config = dict(config or {})
        self.db = AmoDB(tmp)
        self.db.init()
        self.state = None
        self.llm = None
        self.wardrobe = None
        self.photo = None
        self.events = None
        self._last_umo = ""
        self._diary_locks = {}
        self._diary_tasks = {}
        self._spawned = []

    def _spawn(self, coro):
        import asyncio
        task = asyncio.ensure_future(coro)
        self._spawned.append(task)
        return task

    def cfg(self, key, default=None):
        return self.config.get(key, default)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def _diary_lock(self, day, start):
        import asyncio
        key = (str(day), str(start))
        lock = self._diary_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._diary_locks[key] = lock
        return lock


class TimelineOfficeSpecTests(unittest.TestCase):
    """A1/A2/A3 + 块不重叠/覆盖连续。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-spec-a-")
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
        self.plugin.state = self.state

    def tearDown(self):
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 工具 ----------
    def save_day(self, day, items):
        result = Timeline(self.state).save(
            day, [dict(x) for x in items], old_items=[],
            source="test", reason="spec_office_window",
        )
        self.assertTrue(result.get("changed"), "规格输入应产生一次真实写入: %r" % (result,))
        return result

    def blocks(self, day):
        return Timeline(self.state).blocks(day)

    def assert_no_office(self, blocks, begin, end, label):
        for row in blocks:
            row_begin, row_end = _span(row)
            if row_begin is None or row_end is None:
                continue
            if row_begin >= end or row_end <= begin:
                continue
            zone = str(row.get("zone") or "")
            activity = str(row.get("activity") or "")
            place = str(row.get("place") or "")
            offenders = [word for word in OFFICE_WORDS if word in activity]
            self.assertNotEqual(zone, OFFICE_ZONE,
                                "%s: %s-%s 仍是 office 区: %r" % (label, row.get("start"), row.get("end"), row))
            self.assertFalse(offenders,
                             "%s: %s-%s 文案仍含公司/上班/工位 %r: %r"
                             % (label, row.get("start"), row.get("end"), offenders, row))
            self.assertNotIn("公司", place,
                             "%s: %s-%s 地点仍是公司: %r" % (label, row.get("start"), row.get("end"), row))

    def assert_timeline_invariants(self, blocks, label):
        self.assertTrue(blocks, "%s: 时间轴不能为空" % label)
        ordered = sorted(blocks, key=lambda row: _span(row)[0])
        self.assertEqual(_span(ordered[0])[0], 0, "%s: 第一块必须从 00:00 开始" % label)
        self.assertEqual(_span(ordered[-1])[1], DAY_END, "%s: 最后一块必须到 24:00" % label)
        for prev, cur in zip(ordered, ordered[1:]):
            prev_begin, prev_end = _span(prev)
            cur_begin, cur_end = _span(cur)
            self.assertLessEqual(prev_begin, prev_end, "%s: 坏区间 %r" % (label, prev))
            self.assertLessEqual(cur_begin, cur_end, "%s: 坏区间 %r" % (label, cur))
            self.assertEqual(prev_end, cur_begin,
                             "%s: 相邻块必须首尾相接（%s-%s vs %s-%s）"
                             % (label, prev.get("start"), prev.get("end"),
                                cur.get("start"), cur.get("end")))

    # ---------- A1 工作日 18:00 之后 ----------
    def test_workday_after_18_has_no_office(self):
        day = "2026-11-18"  # 周三
        clock.freeze(datetime.datetime(2026, 11, 18, 20, 0))
        result = self.save_day(day, _workday_plan())
        self.assertEqual(result.get("version"), 1, result)
        blocks = self.blocks(day)
        self.assert_no_office(blocks, 18 * 60, DAY_END, "A1 工作日 18-24 点")
        self.assert_timeline_invariants(blocks, "A1")

    # ---------- A2 病假当天 ----------
    def test_sick_day_has_no_office_anywhere(self):
        day = "2026-11-18"
        clock.freeze(datetime.datetime(2026, 11, 18, 10, 0))
        self.plugin.db.kv_set("sick_decision:%s" % day, {"action": "take_sick_leave"})
        self.assertTrue(self.state.on_sick_leave(day))
        result = self.save_day(day, _sick_day_plan())
        self.assertEqual(result.get("version"), 1, result)
        blocks = self.blocks(day)
        self.assert_no_office(blocks, 0, DAY_END, "A2 病假全天")
        self.assert_timeline_invariants(blocks, "A2")

    # ---------- A3 周末 18:00 之后 ----------
    def test_weekend_after_18_has_no_office_but_daytime_overtime_allowed(self):
        day = "2026-11-21"  # 周六
        clock.freeze(datetime.datetime(2026, 11, 21, 20, 0))
        result = self.save_day(day, _weekend_plan())
        self.assertEqual(result.get("version"), 1, result)
        blocks = self.blocks(day)
        self.assert_no_office(blocks, 18 * 60, DAY_END, "A3 周末 18-24 点")
        # 周末白天加班是允许的：09-18 内必须还留得下 office 块，否则说明被一刀切了
        daytime_office = [
            row for row in blocks
            if str(row.get("zone") or "") == OFFICE_ZONE
            and 9 * 60 <= _span(row)[0] and _span(row)[1] <= 18 * 60
        ]
        self.assertTrue(daytime_office, "周末 09-18 的加班不该被折回家: %r" % (blocks,))
        self.assert_timeline_invariants(blocks, "A3")


if __name__ == "__main__":
    unittest.main()
