# -*- coding: utf-8 -*-
"""产品级行为规格 C：面板 doing 只标「覆盖当前分钟」的那一块。

给定时间轴数据与当前分钟：
- 当前分钟落在某个块内 → 恰好一个块 doing=True，且该块区间包含当前分钟；
- 当前分钟落在空档 → 没有任何块 doing=True。

入口只用 state.schedule_tree（今天的 JIT 时间轴）与每个 child 的 doing 字段，
不依赖面板 JSON 的其它细节。
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

DAY = "2026-11-18"


def _row(start, end, activity, place="家", zone="home", category="rest"):
    return {"start": start, "end": end, "activity": activity, "place": place,
            "zone": zone, "category": category}


def _children(tree):
    return [child for block in tree or [] for child in (block.get("children") or [])]


def _doing(children):
    return [child for child in children if child.get("doing")]


def _now_min(hour, minute):
    return hour * 60 + minute


class FakePlugin(ScheduleMixin):
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


class PanelDoingSpecTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-spec-c-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.plugin = FakePlugin(self.tmp, {"schedule_jit": True})
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

    def install(self, items):
        self.state.schedule[DAY] = [dict(x) for x in items]
        self.state.schedule_ctx[DAY] = {"version": "backbone-v5", "day": DAY}
        self.plugin.db.save_schedule(DAY, items)

    def tree(self):
        return self.state.schedule_tree(DAY)

    # ---------- C1：当前分钟在块内 ----------
    def test_doing_marks_exactly_the_block_covering_now(self):
        clock.freeze(datetime.datetime(2026, 11, 18, 15, 30))
        self.install([_row("15:00", "16:00", "在家看书")])
        children = _children(self.tree())
        self.assertTrue(children, "当前分钟之前必须铺出时间轴")
        doing = _doing(children)
        self.assertEqual(len(doing), 1,
                         "覆盖当前分钟的块必须恰好一个 doing: %r" % (children,))
        block = doing[0]
        begin = minutes_of(block.get("start"))
        end = minutes_of(block.get("end"))
        self.assertIsNotNone(begin)
        self.assertIsNotNone(end)
        mine = _now_min(15, 30)
        self.assertTrue(begin <= mine < end,
                        "doing 块区间必须包含当前分钟: %r" % (block,))

    # ---------- C2：当前分钟在空档 ----------
    def test_doing_is_empty_when_now_falls_in_a_gap(self):
        clock.freeze(datetime.datetime(2026, 11, 18, 15, 30))
        # 13:00-14:00 已经结束，14:00-15:30 是补出来的空档
        self.install([_row("13:00", "14:00", "在家看书")])
        children = _children(self.tree())
        self.assertTrue(children, "空档也要铺出时间轴，不能整棵树为空")
        mine = _now_min(15, 30)
        # 空档会被「默认生活段」铺到当前分钟为止（半开区间 [14:00, 15:30)），
        # 当前分钟本身没有真实日程覆盖，所以不能有任何 doing。
        last = max(children, key=lambda child: minutes_of(child.get("end")) or 0)
        self.assertEqual(minutes_of(last.get("end")), mine,
                         "空档的默认段应正好铺到当前分钟: %r" % (children,))
        self.assertEqual(_doing(children), [],
                         "当前分钟落在空档时 doing 必须为 0: %r" % (children,))

    # ---------- C3：块开始时间正好等于当前分钟（边界） ----------
    def test_doing_marks_block_that_starts_at_this_minute(self):
        # 边界语义：15:00 整，15:00-16:00 这块「已经开始」，不能藏到 15:01 才出现。
        clock.freeze(datetime.datetime(2026, 11, 18, 15, 0))
        self.install([_row("15:00", "16:00", "在家看书")])
        children = _children(self.tree())
        self.assertTrue(children, "刚开始的块必须出现在时间轴里")
        doing = _doing(children)
        self.assertEqual(len(doing), 1,
                         "块开始时间 == 当前分钟时必须恰好一个 doing: %r" % (children,))
        block = doing[0]
        self.assertEqual(minutes_of(block.get("start")), _now_min(15, 0))
        self.assertGreater(minutes_of(block.get("end")), _now_min(15, 0))

    # ---------- C4：未来块仍然藏起来 ----------
    def test_future_block_is_still_hidden(self):
        clock.freeze(datetime.datetime(2026, 11, 18, 15, 0))
        self.install([_row("15:05", "16:00", "在家看书")])
        children = _children(self.tree())
        self.assertEqual(_doing(children), [],
                         "还没到点的块不能当 doing: %r" % (children,))
        self.assertFalse(any(minutes_of(child.get("start")) == _now_min(15, 5)
                             for child in children),
                         "还没到点的块不该铺进时间轴: %r" % (children,))



if __name__ == "__main__":
    unittest.main()
