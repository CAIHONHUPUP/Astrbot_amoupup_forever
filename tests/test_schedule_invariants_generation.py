# -*- coding: utf-8 -*-
"""日程不变量常驻回归（生成层）：无重叠 / 地点文案自洽 / 财务不落睡眠 / 7 天结构 HARD=0。

事故背景（2026-11-17）：日程里 sleep 06:00-07:00 与 finance 06:12-06:22 重叠；
小日记里「楼下吃碗热粉…」却挂在 place=家。这里把两条都钉成不依赖真实库的回归。

确定性口径：
- 固定 seed 7 / 42 / 123 / 2026；
- 假插件 + 内存 FakeDB（work/sim_lib.make_state）+ 逐日冻结时钟（sim_lib.set_clock）；
- 7 天日程全部由生产 build_day_skeleton 生成，finance 流水走生产 commit_schedule_change 落库；
- 不读真实 plugin_data，随时钟退出恢复真实时钟。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import os
import random
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "work")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import schedule_plan  # noqa: E402
import sim_lib  # noqa: E402
from amo_shared import MOOD_KEYS  # noqa: E402
import work.preview_day as preview  # noqa: E402
import work.simulate_weeks as sw  # noqa: E402

START_DAY = dt.date(2026, 11, 16)
SEEDS = (7, 42, 123, 2026)
#: 第一条就是事故时刻 06:12-06:22（落在 sleep 06:00-07:00 内部），其余分布在全天。
FINANCE_TEMPLATES = (
    {"start": "06:12", "end": "06:22", "activity": "财务·支出",
     "detail": "买了个小蛋糕（花销 ¥41.95）", "category": "finance", "place": "家", "zone": "home"},
    {"start": "09:12", "end": "09:22", "activity": "财务·支出",
     "detail": "买了个小蛋糕（花销 ¥41.95）", "category": "finance", "place": "家", "zone": "home"},
    {"start": "13:05", "end": "13:15", "activity": "财务·支出",
     "detail": "买了奶茶（花销 ¥12）", "category": "finance", "place": "家", "zone": "home"},
    {"start": "21:03", "end": "21:13", "activity": "财务·支出",
     "detail": "买了本书（花销 ¥58）", "category": "finance", "place": "家", "zone": "home"},
)
ACCIDENT_TEXT = "楼下吃碗热粉，加了个蛋，汤都喝完了"


def _minute(text):
    return sim_lib.minute_of(text)


def _overlaps(first, second):
    """半开区间相交：start < other.end and other.start < end（含包含关系）。"""
    return (_minute(first["start"]) < _minute(second["end"])
            and _minute(second["start"]) < _minute(first["end"]))


def _overlap_pairs(rows):
    for index, first in enumerate(rows):
        for second in rows[index + 1:]:
            if _overlaps(first, second):
                yield first, second


@contextlib.contextmanager
def seeded_week(seed):
    """固定 seed 的一周：假插件 + 定种子 RNG；退出时恢复真实时钟。"""
    plugin, state = sim_lib.make_state()
    try:
        yield plugin, state, random.Random(seed)
    finally:
        sim_lib.restore_clock()


def open_day(plugin, state, index, rng):
    """把假插件与冻结时钟推进到第 index 天，并按种子给心情撒点变化。"""
    day = (START_DAY + dt.timedelta(days=index)).isoformat()
    plugin.day = day
    sim_lib.set_clock(sim_lib.at(day, 8, 0))
    state.mood = {key: rng.randint(20, 90) for key in MOOD_KEYS}
    return day


def build_day(plugin, state, day):
    """生产骨架生成 7 天里的一天，返回落库后的块。"""
    sw.build_day_skeleton(plugin, state, day, sw.day_kind_of(day))
    return sw.rows_of(plugin, state, day)


def finance_of(seed, index):
    """按 seed/日序挑一条流水（确定性）。"""
    return dict(FINANCE_TEMPLATES[(SEEDS.index(seed) + index) % len(FINANCE_TEMPLATES)])


def splice_finance(rows, item):
    """把流水块按分钟切进时间轴：原块让位，不制造重叠。"""
    begin, finish = _minute(item["start"]), _minute(item["end"])
    out = []
    for row in rows:
        row_begin, row_finish = _minute(row["start"]), _minute(row["end"])
        if not (row_begin <= begin and row_finish >= finish):
            out.append(dict(row))
            continue
        if row_begin < begin:
            left = dict(row)
            left["end"] = item["start"]
            out.append(left)
        out.append(dict(item))
        if row_finish > finish:
            right = dict(row)
            right["start"] = item["end"]
            out.append(right)
    return out


class NoOverlapTests(unittest.TestCase):
    """1) 同一天任意两项不得相交（覆盖包含关系与 finance 块）。"""

    def test_generated_skeleton_has_no_overlap(self):
        for seed in SEEDS:
            with self.subTest(seed=seed), seeded_week(seed) as (plugin, state, rng):
                for index in range(7):
                    day = open_day(plugin, state, index, rng)
                    rows = build_day(plugin, state, day)
                    self.assertTrue(rows, "%s 生成日程为空" % day)
                    for first, second in _overlap_pairs(rows):
                        self.fail("%s 生成日程重叠: %r / %r" % (day, first, second))

    def test_finance_overlay_keeps_day_disjoint(self):
        for seed in SEEDS:
            with self.subTest(seed=seed), seeded_week(seed) as (plugin, state, rng):
                for index in range(7):
                    day = open_day(plugin, state, index, rng)
                    rows = build_day(plugin, state, day)
                    item = finance_of(seed, index)
                    state.commit_schedule_change(
                        day, splice_finance(rows, item), old_items=rows,
                        source="finance", reason="invariant")
                    stored = sw.rows_of(plugin, state, day)
                    finance = [row for row in stored
                               if str(row.get("category") or "") == "finance"]
                    self.assertEqual(len(finance), 1,
                                     "%s seed=%s 流水块落库后应恰好一条" % (day, seed))
                    self.assertEqual((finance[0]["start"], finance[0]["end"]),
                                     (item["start"], item["end"]))
                    for first, second in _overlap_pairs(stored):
                        self.fail("%s seed=%s 含流水日程重叠: %r / %r"
                                  % (day, seed, first, second))

    def test_overlap_predicate_catches_containment_and_finance(self):
        """阳性对照：检测函数本身不能是常假（包含关系、finance 重叠都要抓到）。"""
        sleep = {"start": "06:00", "end": "07:00"}
        finance = {"start": "06:12", "end": "06:22"}
        work = {"start": "09:00", "end": "10:00"}
        partial = {"start": "09:30", "end": "10:30"}
        adjacent = {"start": "07:00", "end": "08:00"}
        self.assertTrue(_overlaps(sleep, finance), "被包住的 finance 必须判重叠")
        self.assertTrue(_overlaps(finance, sleep), "包含关系两个方向都要判重叠")
        self.assertTrue(_overlaps(work, partial), "部分重叠必须判重叠")
        self.assertFalse(_overlaps(sleep, adjacent), "首尾相接不算重叠")


class PlaceConsistencyTests(unittest.TestCase):
    """2) 地点/文案自洽：activity 与 place/zone 不能互相矛盾。"""

    def test_generated_blocks_are_place_consistent(self):
        for seed in SEEDS:
            with self.subTest(seed=seed), seeded_week(seed) as (plugin, state, rng):
                for index in range(7):
                    day = open_day(plugin, state, index, rng)
                    rows = build_day(plugin, state, day)
                    for row in rows:
                        self.assertFalse(
                            schedule_plan.activity_place_conflict(
                                row.get("activity"), row.get("place"), row.get("zone")),
                            "%s 文案与地点矛盾: %r" % (day, row))
                    for template in FINANCE_TEMPLATES:
                        self.assertFalse(
                            schedule_plan.activity_place_conflict(
                                template["activity"], template["place"], template["zone"]),
                            "流水块文案与地点矛盾: %r" % (template,))

    def test_accident_sentence_with_home_place_is_conflict(self):
        """事故原句：place=家 却写「楼下吃碗热粉…」必须判冲突。"""
        self.assertTrue(schedule_plan.activity_place_conflict(ACCIDENT_TEXT, "家", "home"))
        self.assertTrue(schedule_plan.activity_place_conflict(ACCIDENT_TEXT, "家", ""))
        self.assertFalse(schedule_plan.activity_place_conflict(ACCIDENT_TEXT, "公司", "office"))


class FinanceSleepTests(unittest.TestCase):
    """3) finance 块不得落在 sleep 块内部（至少不得与 sleep 重叠）。"""

    def test_finance_never_overlaps_sleep_in_week(self):
        for seed in SEEDS:
            with self.subTest(seed=seed), seeded_week(seed) as (plugin, state, rng):
                for index in range(7):
                    day = open_day(plugin, state, index, rng)
                    rows = build_day(plugin, state, day)
                    item = finance_of(seed, index)
                    state.commit_schedule_change(
                        day, splice_finance(rows, item), old_items=rows,
                        source="finance", reason="invariant")
                    stored = sw.rows_of(plugin, state, day)
                    sleeps = [row for row in stored
                              if str(row.get("category") or "") == "sleep"]
                    finance = [row for row in stored
                               if str(row.get("category") or "") == "finance"]
                    self.assertTrue(finance, "%s seed=%s 流水块丢了" % (day, seed))
                    for finance_row in finance:
                        for sleep_row in sleeps:
                            self.assertFalse(
                                _overlaps(finance_row, sleep_row),
                                "%s seed=%s 流水压到睡眠: %r / %r"
                                % (day, seed, finance_row, sleep_row))

    def test_commit_refuses_finance_inside_sleep(self):
        """事故原始数据直接交给生产落库：06:12-06:22 的流水不能留在 06:00-07:00 睡眠内部。"""
        plugin, state = sim_lib.make_state()
        day = START_DAY.isoformat()
        plugin.day = day
        sleep = {"start": "06:00", "end": "07:00", "activity": "睡觉",
                 "place": "家", "zone": "home", "category": "sleep"}
        finance = dict(FINANCE_TEMPLATES[0])
        try:
            sim_lib.set_clock(sim_lib.at(day, 8, 0))
            state.commit_schedule_change(day, [sleep], old_items=[], source="ensure")
            state.commit_schedule_change(
                day, [dict(sleep), dict(finance)],
                old_items=[dict(sleep)], source="finance")
            rows = sw.rows_of(plugin, state, day)
        finally:
            sim_lib.restore_clock()
        sleeps = [row for row in rows if str(row.get("category") or "") == "sleep"]
        for row in rows:
            if str(row.get("category") or "") != "finance":
                continue
            for sleep_row in sleeps:
                self.assertFalse(_overlaps(row, sleep_row),
                                 "落库后流水仍压在睡眠内部: %r / %r" % (row, sleep_row))
        self.assertFalse([row for row in rows if row.get("start") == "06:12"
                          and str(row.get("category") or "") == "finance"],
                         "06:12-06:22 的流水不能原样落在 06:00-07:00 睡眠块内部")


class SevenDayHardZeroTests(unittest.TestCase):
    """6) 7 天全量：生产骨架 + stabilize 后，结构审查 HARD=0。"""

    def test_seven_days_structure_hard_zero(self):
        plugin, state = sim_lib.make_state()
        started = time.time()
        try:
            for index in range(7):
                day = (START_DAY + dt.timedelta(days=index)).isoformat()
                plugin.day = day
                sim_lib.set_clock(sim_lib.at(day, 8, 0))
                sw.build_day_skeleton(plugin, state, day, sw.day_kind_of(day))
                sw.stabilize_day(plugin, state, day)
                rows = sw.rows_of(plugin, state, day)
                self.assertGreaterEqual(len(rows), 20, "%s 块数过少: %d" % (day, len(rows)))
                self.assertEqual(sw.timeline(rows), [], "%s 时间轴有问题" % day)
                hard, _warn = preview.review(rows, day, now=sim_lib.at(day, 0, 0))
                self.assertEqual(hard, [], "%s 结构 HARD 非 0: %r" % (day, hard))
        finally:
            sim_lib.restore_clock()
        self.assertLess(time.time() - started, 30.0, "7 天结构审查必须 <30s")


if __name__ == "__main__":
    unittest.main()
