# -*- coding: utf-8 -*-
"""产品级行为规格 B：小日记文案必须跟最终日程块一致。

给定：某小时/某块已经生成过一条小日记；
当：同一时段的 activity 被改写；
必须：旧小日记被标记 stale（不再作为有效缓存对外供文），或重新生成后的文案
      与新块一致；任何「有效」记录里都不许残留旧 activity 的文案。

真实入口：Timeline.save → commit_schedule_change → _publish_schedule_change /
AmoDB.mark_diaries_stale（块表 block_diary + 旧整点表 schedule_diary 一起失效）。
"""
from __future__ import annotations

import asyncio
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
from amo_schedule import ScheduleMixin  # noqa: E402
from amo_state_common import block_id  # noqa: E402
from amo_timeline import Timeline  # noqa: E402

OLD_ACTIVITY = "在家休息"
NEW_ACTIVITY = "在家看剧"


def _block(start, end, activity, place="家", zone="home", category="rest"):
    return {"start": start, "end": end, "activity": activity, "place": place,
            "zone": zone, "category": category}


class FakeEngine:
    """正文里带块当时的 activity：块被改写后旧文案会一眼可见。"""

    def __init__(self):
        self.calls = 0
        self.seen = []

    async def generate_diary(self, umo, context, item, prev_item, next_item, llm, prev_diary=""):
        self.calls += 1
        self.seen.append(dict(item))
        return {"diary": "块 %s-%s 写着 %s" % (
            item.get("start"), item.get("end"), item.get("activity")), "mood": "平静"}

    def fallback_diary(self, context, item, prev_item=None):
        return "兜底日记 %s" % (item.get("start") or "")

    def yesterday_summary(self, day):
        return {}


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

    def _spawn(self, coro):
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
        key = (str(day), str(start))
        lock = self._diary_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._diary_locks[key] = lock
        return lock


class BlockDiaryConsistencyTests(unittest.TestCase):
    DAY = "2026-11-18"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-spec-b-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        # 15:00 冻结：13:00-14:00 这一块已经过完，可以生成小日记
        clock.freeze(datetime.datetime(2026, 11, 18, 15, 0))
        self.plugin = FakePlugin(self.tmp)
        self.state = HumanState(self.plugin)
        self.plugin.state = self.state
        self.engine = FakeEngine()
        self.state.engine = self.engine

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
        self.state.schedule[self.DAY] = [dict(x) for x in items]
        self.state.schedule_ctx[self.DAY] = {"version": "backbone-v5", "day": self.DAY}
        self.plugin.db.save_schedule(self.DAY, items)

    def rewrite(self, old_item, new_activity):
        new_item = dict(old_item, activity=new_activity)
        result = Timeline(self.state).save(
            self.DAY, [new_item], old_items=[dict(old_item)],
            source="test", reason="spec_block_rewrite",
        )
        self.assertTrue(result.get("changed"), "改写 activity 必须产生一次真实提交: %r" % (result,))
        return result

    # ---------- B1：块级小日记（真实路径） ----------
    def test_changed_block_invalidates_and_regenerates_diary(self):
        old = _block("13:00", "14:00", OLD_ACTIVITY)
        self.install([old])
        first = asyncio.run(self.state.expand_block(self.DAY, dict(old), [dict(old)]))
        self.assertIn(OLD_ACTIVITY, first["diary"])
        old_bid = block_id(self.DAY, old)
        record = self.plugin.db.get_block_diary_record(self.DAY, old_bid)
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "valid")
        self.assertIn(OLD_ACTIVITY, record["text"])

        self.rewrite(old, NEW_ACTIVITY)

        record = self.plugin.db.get_block_diary_record(self.DAY, old_bid)
        self.assertIsNotNone(record, "旧小日记不能物理消失（失败时要能兜底）")
        self.assertEqual(record["status"], "stale",
                         "同一时段被改写后旧小日记必须失效: %r" % (record,))
        self.assertFalse(self.plugin.db.block_diary_is_valid(self.DAY, old_bid),
                         "stale 记录不能再被当作有效缓存")
        # 任何仍然 valid 的记录都不许残留旧 activity
        for row in self.plugin.db.list_block_diaries(self.DAY):
            if str(row.get("status") or "valid") != "valid":
                continue
            self.assertNotIn(OLD_ACTIVITY, str(row.get("text") or ""),
                             "有效小日记里残留了旧 activity: %r" % (row,))

        # 重新生成：文案必须跟着最终块走
        current = self.state.schedule[self.DAY]
        new_item = next(x for x in current if str(x.get("start")) == "13:00")
        self.assertEqual(new_item["activity"], NEW_ACTIVITY)
        new_bid = block_id(self.DAY, new_item)
        second = asyncio.run(self.state.expand_block(self.DAY, dict(new_item), current))
        self.assertIn(NEW_ACTIVITY, second["diary"])
        self.assertNotIn(OLD_ACTIVITY, second["diary"])
        fresh = self.plugin.db.get_block_diary_record(self.DAY, new_bid)
        self.assertIsNotNone(fresh)
        self.assertEqual(fresh["status"], "valid")
        self.assertIn(NEW_ACTIVITY, fresh["text"])
        self.assertNotIn(OLD_ACTIVITY, fresh["text"])

    # ---------- B2：回滚开关下的旧整点表（schedule_diary） ----------
    def test_changed_hour_invalidates_legacy_schedule_diary(self):
        self.plugin.config["block_diary_enabled"] = False
        old = _block("13:00", "14:00", OLD_ACTIVITY)
        self.install([old])
        first = asyncio.run(self.state.expand_hour(self.DAY, dict(old), [dict(old)]))
        self.assertIn(OLD_ACTIVITY, first["diary"])
        record = self.plugin.db.get_diary_record(self.DAY, "13:00")
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "valid")

        self.rewrite(old, NEW_ACTIVITY)

        record = self.plugin.db.get_diary_record(self.DAY, "13:00")
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "stale",
                         "旧整点表也要跟着失效: %r" % (record,))
        self.assertFalse(self.plugin.db.diary_is_valid(self.DAY, "13:00"),
                         "stale 的整点小日记不能继续被当作有效缓存供文")
        # 旧文案保留是产品设计（生成失败时兜底），但状态必须已经是 stale；
        # 不能出现「status=valid 且写着旧 activity」的记录。
        self.assertEqual(record["text"], first["diary"],
                         "stale 只标记失效，不该物理清掉旧文案（失败兜底）")

        current = self.state.schedule[self.DAY]
        new_item = next(x for x in current if str(x.get("start")) == "13:00")
        second = asyncio.run(self.state.expand_hour(self.DAY, dict(new_item), current))
        self.assertIn(NEW_ACTIVITY, second["diary"])
        self.assertNotIn(OLD_ACTIVITY, second["diary"])
        fresh = self.plugin.db.get_diary_record(self.DAY, "13:00")
        self.assertEqual(fresh["status"], "valid")
        self.assertIn(NEW_ACTIVITY, fresh["text"])
        self.assertIn(NEW_ACTIVITY, self.plugin.db.get_diary(self.DAY, "13:00"))


if __name__ == "__main__":
    unittest.main()
