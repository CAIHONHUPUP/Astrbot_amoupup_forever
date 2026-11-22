# -*- coding: utf-8 -*-
"""P0 日记孤儿/版本/跨源冲突（2026-11-22）。

真实库证据（work/audit/diary_facts.md）：
- 11-18 有 25 条孤儿块日记仍 status=valid（公司加班/地铁 vs 请假在家），11-19 5、
  11-20 2、11-17 3；同 start 多条 valid；自审只审当前块，报 blocks=12 hard=0；
- 09:00 日程在家，块日记/hourly 写逛街；
- 健康/天气按天快照缺失；weather_city_name=清远 vs schedule_city=东莞；
- 块日记正文会被当事实回流给下一块/大日记。

这组用例钉住修复：删块/改块后旧身份行不能继续 valid；失效按 event_id 精确到块
（不再从 changed_hour 粗扫整表，快照补字段不制造 stale 风暴）；自审覆盖孤儿/版本/
地点/同伴/吃饭/金额/健康/天气；块日记不再进块日记事实池、也不再进大日记素材。
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import inspect
import os
import shutil
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import clock  # noqa: E402
import amo_diary_audit as audit  # noqa: E402
import amo_schedule  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_schedule import ScheduleMixin  # noqa: E402
from amo_state_common import block_id  # noqa: E402
from amo_state_diary import DiaryStateMixin  # noqa: E402
from amo_timeline import Timeline, mint_event_id  # noqa: E402

DAY = "2026-11-18"


def _block(start, end, activity, place="家", zone="home", category="rest", **extra):
    row = {"start": start, "end": end, "activity": activity, "place": place,
           "zone": zone, "category": category}
    row.update(extra)
    return row


def _make_db(tmp):
    db = AmoDB(tmp)
    db.init()
    return db


def _tmp_path(test):
    path = tempfile.mkdtemp(prefix="amo-diary-p0-")
    test.addCleanup(shutil.rmtree, path, True)
    return path


class BlockDiaryValidityTests(unittest.TestCase):
    """is_valid：status + 正文 + 块还在 + 身份/窗口对得上（+ version 不超前）。"""

    def setUp(self):
        self.db = _make_db(_tmp_path(self))
        self.addCleanup(self.db.close)
        self.today = _block("13:00", "14:00", "在家休息")
        self.other = _block("15:00", "16:00", "在家看书")
        self.db.save_schedule(DAY, [self.today, self.other])
        self.bid = block_id(DAY, self.today)
        self.db.save_block_diary(DAY, self.bid, "在家躺着休息了一会儿。",
                                 start="13:00", end="14:00", place="家",
                                 version=3, status="valid")

    def test_valid_row_for_existing_block(self):
        self.assertTrue(self.db.block_diary_is_valid(DAY, self.bid, 3))
        self.assertTrue(self.db.block_diary_is_valid(DAY, self.bid))

    def test_version_newer_than_schedule_is_invalid(self):
        # 日记写于更晚的版本（回滚/时间倒流）不能当当前缓存。
        self.assertFalse(self.db.block_diary_is_valid(DAY, self.bid, 2))

    def test_stale_or_empty_row_is_invalid(self):
        self.db.save_block_diary(DAY, self.bid, "旧正文", start="13:00", end="14:00",
                                 version=3, status="stale")
        self.assertFalse(self.db.block_diary_is_valid(DAY, self.bid, 3))
        self.db.save_block_diary(DAY, self.bid, "", start="13:00", end="14:00",
                                 version=3, status="valid")
        self.assertFalse(self.db.block_diary_is_valid(DAY, self.bid, 3))

    def test_orphan_row_for_deleted_block_is_invalid(self):
        # 11-18 现场：日程里已经没有这个块，行还是 valid。
        orphan = "ev_deadbeefdeadbeef"
        self.db.save_block_diary(DAY, orphan, "在公司加班到很晚。",
                                 start="21:18", end="21:23", place="公司",
                                 version=3, status="valid")
        self.assertFalse(self.db.block_diary_is_valid(DAY, orphan, 3))

    def test_row_window_must_match_current_block(self):
        # 身份相同但窗口被改过（旧正文按旧窗口写）→ 不是有效缓存。
        self.db.save_block_diary(DAY, self.bid, "按旧窗口写的正文。",
                                 start="13:30", end="14:00", place="家",
                                 version=3, status="valid")
        self.assertFalse(self.db.block_diary_is_valid(DAY, self.bid, 3))

    def test_sync_marks_orphans_and_precise_ids_keeps_others(self):
        orphan = "ev_0000000000000001"
        frozen = "ev_frozenfrozen01"
        self.db.save_block_diary(DAY, orphan, "孤儿正文：公司在加班。",
                                 start="18:00", end="19:00", place="公司")
        self.db.save_block_diary(DAY, frozen, "人工冻结正文。",
                                 start="19:00", end="20:00", place="家",
                                 status="frozen")
        stats = self.db.sync_block_diaries(DAY, [self.today, self.other],
                                           stale_ids=[self.bid])
        self.assertTrue(stats["stale"] >= 2)
        self.assertIn(orphan, stats["orphans"])
        self.assertEqual(self.db.get_block_diary_record(DAY, orphan)["status"], "stale")
        self.assertEqual(self.db.get_block_diary_record(DAY, self.bid)["status"], "stale")
        # 冻结是人工内容，不动
        self.assertEqual(self.db.get_block_diary_record(DAY, frozen)["status"], "frozen")
        # 没被点名的块保持 valid
        self.db.save_block_diary(DAY, block_id(DAY, self.other), "在家看书。",
                                 start="15:00", end="16:00", place="家", status="valid")
        self.db.sync_block_diaries(DAY, [self.today, self.other], stale_ids=[])
        self.assertEqual(
            self.db.get_block_diary_record(DAY, block_id(DAY, self.other))["status"],
            "valid")


class FakeEngine:
    def __init__(self):
        self.calls = 0

    async def generate_diary(self, umo, context, item, prev_item, next_item, llm,
                             prev_diary=""):
        self.calls += 1
        return {"diary": "块 %s-%s 写着 %s" % (
            item.get("start"), item.get("end"), item.get("activity")), "mood": "平静"}

    def fallback_diary(self, context, item, prev_item=None):
        return "兜底日记 %s" % (item.get("start") or "")


class CommitPlugin(ScheduleMixin):
    def __init__(self, tmp):
        self.config = {}
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

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def _diary_lock(self, day, start):
        key = (str(day), str(start))
        lock = self._diary_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._diary_locks[key] = lock
        return lock


class PreciseInvalidationTests(unittest.TestCase):
    """日程落库点按 event_id diff 精确失效，不按 changed_hour 粗扫整表。"""

    def setUp(self):
        self.tmp = _tmp_path(self)
        self.plugin = CommitPlugin(self.tmp)
        self.addCleanup(self.plugin.db.close)
        self.state = HumanState(self.plugin)
        self.plugin.state = self.state
        self.state.engine = FakeEngine()
        clock.freeze(datetime.datetime(2026, 11, 18, 20, 0))
        self.addCleanup(clock.unfreeze)
        self.blocks = self.install([
            _block("09:00", "10:00", "在家收拾屋子"),
            _block("10:00", "11:00", "在家看剧"),
            _block("11:00", "12:00", "在家看书"),
        ])

    def install(self, items):
        # 线上日程块在落库时就有 event_id（stamp_event_ids）；这里按同一口径补上，
        # 否则块日记会落在旧内容哈希身份上，测不出「身份不变就不失效」。
        stamped = [dict(item, event_id=item.get("event_id") or mint_event_id(DAY, item))
                   for item in items]
        self.state.schedule[DAY] = [dict(item) for item in stamped]
        self.state.schedule_ctx[DAY] = {"version": "p0", "day": DAY}
        self.plugin.db.save_schedule(DAY, stamped)
        return stamped

    def warm(self):
        for item in list(self.state.schedule[DAY]):
            asyncio.run(self.state.expand_block(DAY, dict(item), self.state.schedule[DAY]))
        return [self.status(item) for item in self.blocks]

    def status(self, item):
        record = self.plugin.db.get_block_diary_record(DAY, block_id(DAY, item))
        return (record or {}).get("status")

    def commit(self, rows, source="test"):
        result = Timeline(self.state).save(DAY, list(rows), source=source,
                                           reason="p0_precise")
        self.assertTrue(result.get("changed"), result)
        return result

    def test_changed_block_only_invalidates_itself(self):
        self.assertEqual(self.warm(), ["valid", "valid", "valid"])
        changed = dict(self.state.schedule[DAY][1], activity="在家写代码")
        rows = [self.state.schedule[DAY][0], changed, self.state.schedule[DAY][2]]
        self.commit(rows)
        statuses = [self.status(self.blocks[0]), self.status(changed),
                    self.status(self.blocks[2])]
        self.assertEqual(statuses, ["valid", "stale", "valid"],
                         "只失效被改的块；旧行为从 changed_hour 粗扫会把 11 点也打 stale")

    def test_deleted_block_diary_is_orphan_cleaned(self):
        self.assertEqual(self.warm(), ["valid", "valid", "valid"])
        rows = [self.state.schedule[DAY][0], self.state.schedule[DAY][2]]
        self.commit(rows)
        self.assertEqual(self.status(self.blocks[1]), "stale",
                         "块被删了，它的日记不能继续 valid")
        self.assertEqual(self.status(self.blocks[0]), "valid")
        self.assertEqual(self.status(self.blocks[2]), "valid")

    def test_snapshot_padding_field_does_not_stale_diaries(self):
        # 23:59 快照会在块上补 cost_settled/source 这类附加字段：日程版本会 bump，
        # 但日记正文相关字段没变，不能因此把块日记打 stale（11-21 stale 风暴）。
        self.assertEqual(self.warm(), ["valid", "valid", "valid"])
        rows = [dict(row) for row in self.state.schedule[DAY]]
        rows[2]["cost_settled"] = True
        rows[2]["locked"] = True
        self.commit(rows)
        self.assertEqual([self.status(item) for item in self.blocks],
                         ["valid", "valid", "valid"])


class AuditPlugin:
    def __init__(self, tmp, config=None):
        self.config = dict(config or {})
        self.db = AmoDB(tmp)
        self.db.init()
        self.state = types.SimpleNamespace(schedule={})

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return 0

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    async def push_to_owner(self, text):
        return False


class _AuditBase(unittest.TestCase):
    def setUp(self):
        self.plugin = AuditPlugin(_tmp_path(self))
        self.addCleanup(self.plugin.db.close)
        self.now = datetime.datetime(2026, 11, 19, 10, 0)

    def seed(self, blocks, day=DAY):
        self.plugin.state.schedule[day] = [dict(block) for block in blocks]
        self.plugin.db.save_schedule(day, blocks)

    def save(self, block, text, day=DAY, version=0, key_activity=None, status="valid"):
        target = dict(block)
        if key_activity is not None:
            target["activity"] = key_activity
        bid = block_id(day, target)
        self.plugin.db.save_block_diary(
            day, bid, text, start=str(block.get("start") or ""),
            end=str(block.get("end") or ""), place=str(block.get("place") or ""),
            version=version, status=status)
        return bid

    def run_day(self, day=DAY):
        return audit.run(self.plugin, day, apply=True, now=self.now)

    def kinds(self, report, bucket):
        return [entry.get("kind") for entry in report.get(bucket) or []]


class AuditOrphanVersionTests(_AuditBase):
    def test_orphan_row_is_hard_and_marked_stale(self):
        block = _block("13:00", "14:00", "在家休息")
        self.seed([block])
        self.save(block, "在家躺着看电视。")
        orphan = self.save(block, "这个点其实我在公司加班，还坐地铁回来。",
                           key_activity="在公司加班")
        report = self.run_day()
        self.assertIn("orphan", self.kinds(report, "hard"))
        self.assertEqual(report["counts"]["orphans"], 1)
        self.assertEqual(
            self.plugin.db.get_block_diary_record(DAY, orphan)["status"], "stale")

    def test_same_start_old_identity_row_is_orphan(self):
        # 同 start 多条 valid：当前块身份 + 旧活动身份各一条，旧的那条是孤儿。
        block = _block("13:00", "14:00", "在家休息")
        self.seed([block])
        self.save(block, "在家躺着看电视。")
        old = self.save(block, "旧版正文：整理一个朋友、回邮件。", key_activity="整理房间")
        report = self.run_day()
        self.assertEqual(report["counts"]["orphans"], 1)
        self.assertEqual(
            self.plugin.db.get_block_diary_record(DAY, old)["status"], "stale")

    def test_diary_newer_than_schedule_is_version_mismatch(self):
        block = _block("13:00", "14:00", "在家休息")
        self.seed([block])
        self.save(block, "在家躺着看电视。", version=9)
        self.plugin.db.kv_set("schedule_ver:" + DAY, 3)
        report = self.run_day()
        self.assertIn("version_mismatch", self.kinds(report, "hard"))
        self.assertEqual(report["stale"], 1)

    def test_home_block_writing_outside_is_place_mismatch(self):
        # 09:00 现场：日程在家歇着，块日记写逛街/交通/奶茶。
        block = _block("09:00", "10:00", "在家歇着")
        self.seed([block])
        self.save(block, "九点多出门，我坐地铁去了公司，忙到中午才回来。")
        report = self.run_day()
        self.assertIn("place_mismatch", self.kinds(report, "hard"))


class AuditFactDimensionTests(_AuditBase):
    def test_companion_claim_without_people_is_warn(self):
        block = _block("12:00", "13:00", "在家吃饭", category="meal")
        self.seed([block])
        self.save(block, "中午跟闺蜜一起吃了饭，我们聊了好久，心情挺好的。")
        report = self.run_day()
        self.assertIn("companion", self.kinds(report, "warn"))

    def test_meal_block_saying_no_food_is_warn(self):
        block = _block("12:00", "13:00", "吃午饭", category="meal")
        self.seed([block])
        self.save(block, "中午一直忙，没吃饭，也不饿。")
        report = self.run_day()
        self.assertIn("meal", self.kinds(report, "warn"))

    def test_money_mismatch_with_ledger_is_warn(self):
        block = _block("12:00", "13:00", "吃火锅", category="meal")
        self.seed([block])
        self.save(block, "中午跟柳如烟吃了火锅，花了160块钱。")
        ts = datetime.datetime(2026, 11, 18, 12, 30).timestamp()
        self.plugin.wallet = types.SimpleNamespace(
            ledger=lambda limit=200: [{"ts": ts, "delta_cents": -20000,
                                       "reason": "火锅"}])
        report = self.run_day()
        self.assertIn("money", self.kinds(report, "warn"))

    def test_health_snapshot_contradiction_is_warn(self):
        block = _block("09:00", "10:00", "在家休息")
        self.seed([block])
        self.save(block, "感冒已经好了，身体很好。")
        self.plugin.db.kv_set("health_day:" + DAY,
                              {"status": "感冒", "sick": True})
        report = self.run_day()
        self.assertIn("health", self.kinds(report, "warn"))

    def test_weather_temp_and_city_conflict_is_warn(self):
        block = _block("09:00", "10:00", "在家里待着")
        self.seed([block])
        self.save(block, "外面东莞 28℃，热得不想出门。")
        self.plugin.config["schedule_city"] = "东莞"
        self.plugin.db.kv_set("weather_day:" + DAY,
                              {"text": "阴", "temp": 16, "city": "清远"})
        report = self.run_day()
        kinds = self.kinds(report, "warn")
        self.assertIn("weather", kinds)
        self.assertIn("weather_city", kinds)

    def test_missing_day_snapshots_are_warn(self):
        block = _block("09:00", "10:00", "在家休息")
        self.seed([block])
        self.save(block, "在家慢慢收拾了一上午。")
        report = self.run_day()
        kinds = self.kinds(report, "warn")
        self.assertIn("weather_missing", kinds)
        self.assertIn("health_missing", kinds)


class BackflowTests(unittest.TestCase):
    def test_diary_facts_does_not_read_block_diary(self):
        source = inspect.getsource(DiaryStateMixin._diary_facts)
        self.assertNotIn("from .amo_fact_timeline import day_view_facts", source)
        self.assertNotIn("day_view_facts(", source)
        self.assertNotIn("block_diary", source)
        self.assertIn("build_fact_pool", source)

    def test_hourly_lines_only_read_current_blocks(self):
        plugin = CommitPlugin(_tmp_path(self))
        self.addCleanup(plugin.db.close)
        plugin.state = types.SimpleNamespace(schedule={})
        day = datetime.date(2026, 11, 18)
        block = _block("09:00", "10:00", "在家歇着")
        plugin.state.schedule[DAY] = [dict(block)]
        plugin.db.save_schedule(DAY, [block])
        plugin.db.save_block_diary(DAY, block_id(DAY, block), "当前块正文：在家热了剩饭。",
                                   start="09:00", end="10:00", place="家")
        plugin.db.save_block_diary(DAY, "ev_orphan_company", "孤儿正文：在公司加班到很晚。",
                                   start="13:40", end="14:40", place="公司")
        lines = plugin._diary_hourly_lines(day)
        self.assertTrue(any("当前块正文" in line for line in lines), lines)
        self.assertFalse(any("孤儿正文" in line for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
