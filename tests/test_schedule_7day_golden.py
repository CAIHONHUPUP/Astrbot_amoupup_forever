# -*- coding: utf-8 -*-
"""seed=7 的 7 天日程 golden：固定 seed + 冻结时钟 + 临时目录。

测什么：
- 当前生成结果与 tests/golden/schedule_7day_seed7.json 完全一致（块清单 + sha256）；
- 生成两次逐块一致（确定性，防止随机源/真实时钟回流）；
- 每天 24 块、00:00→24:00 首尾相接、字段齐全（golden 自身也要体检）；
- golden 带「为什么允许更新」的说明（防止随手刷新快照）。

为什么允许更新 golden：只有日程主干生成规则被有意改动时才允许重新生成，见
golden 的 _update_policy 与 work/schedule_7day.py 顶部说明；更新方式
python work/schedule_7day.py --write，更新前人工看 diff 并在说明里写原因。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import work.schedule_7day as golden7  # noqa: E402

GOLDEN_PATH = os.path.join(TESTS, "golden", "schedule_7day_seed7.json")
FIELDS = ("start", "end", "activity", "place", "category", "zone")


def _load():
    with open(GOLDEN_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _first_diff(expected, actual):
    for index, (left, right) in enumerate(zip(expected, actual)):
        if left != right:
            return index, left, right
    if len(expected) != len(actual):
        return min(len(expected), len(actual)), "<len=%d>" % len(expected), "<len=%d>" % len(actual)
    return None


class Schedule7DayGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = _load()
        cls.days = golden7.build_days()

    @classmethod
    def tearDownClass(cls):
        golden7.restore_clock()

    def test_golden_meta_is_frozen_and_documented(self):
        golden = self.golden
        self.assertEqual(golden["schema"], "schedule_7day_golden_v1")
        self.assertEqual(golden["seed"], 7)
        self.assertEqual(golden["city"], golden7.CITY)
        self.assertEqual(golden["start_day"], golden7.weeks.START_DAY.isoformat())
        self.assertEqual(golden["clock"], "frozen_at_day_start_00:00")
        self.assertEqual(len(golden["days"]), 7)
        policy = " ".join(golden["_update_policy"])
        self.assertIn("规则", policy)
        self.assertIn("schedule_7day.py --write", policy)

    def test_current_generation_matches_golden_blocks(self):
        for day, expected in self.golden["days"].items():
            with self.subTest(day=day):
                actual = self.days.get(day)
                self.assertIsNotNone(actual, "没生成 %s" % day)
                self.assertEqual(actual["kind"], expected["kind"])
                diff = _first_diff(expected["blocks"], actual["blocks"])
                if diff is not None:
                    index, left, right = diff
                    self.fail(
                        "%s 第 %s 块不一致\n  期望: %s\n  实际: %s\n"
                        "如果这次规则改动是有意的，用 python work/schedule_7day.py --write "
                        "更新 golden 并写清原因；禁止随手刷新快照。"
                        % (day, index, left, right))

    def test_current_generation_matches_golden_hashes(self):
        for day, expected in self.golden["days"].items():
            with self.subTest(day=day):
                actual_hash = golden7.block_hash(self.days[day]["blocks"])
                self.assertEqual(actual_hash, expected["sha256"], "%s 块清单哈希变了" % day)
                self.assertEqual(expected["block_count"], len(expected["blocks"]))

    def test_generation_is_deterministic(self):
        again = golden7.build_days()
        self.assertEqual(again, self.days, "同一 seed 两次生成不一致，说明有非确定性来源")
        self.assertEqual(golden7.dumps(golden7.payload(again)),
                         golden7.dumps(golden7.payload(self.days)))

    def test_every_day_is_24_contiguous_blocks(self):
        for day, item in self.days.items():
            with self.subTest(day=day):
                blocks = item["blocks"]
                self.assertEqual(len(blocks), 24)
                self.assertEqual(blocks[0][0], "00:00")
                self.assertEqual(blocks[-1][1], "24:00")
                for index, block in enumerate(blocks):
                    self.assertEqual(len(block), len(FIELDS))
                    self.assertTrue(block[0] and block[1], "第 %d 块缺时间" % index)
                    self.assertTrue(block[4], "第 %d 块缺 category" % index)
                    if index:
                        self.assertEqual(blocks[index - 1][1], block[0],
                                         "%s 第 %d 块与上一块不相接" % (day, index))
