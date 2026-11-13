# -*- coding: utf-8 -*-
"""大肥鱼日记的落盘、目录和读取。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diary_engine  # noqa: E402
import diary_lines  # noqa: E402


class DiaryEngineTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="amo-diary-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_path_layout(self):
        day = date(2026, 11, 8)
        path = diary_engine.diary_path(self.root, day)
        self.assertTrue(path.endswith(os.path.join("2026", "11", "08", "我的日记2026年11月08日.txt")))
        self.assertEqual(diary_engine.title_for(day), "我的日记2026年11月08日")

    def test_write_and_read(self):
        day = date(2026, 11, 8)
        body = "今天他一直在修代码。\n我在旁边看着，觉得他很认真。"
        path = diary_engine.write_diary(self.root, day, body)
        self.assertTrue(os.path.isfile(path))
        text = diary_engine.read_diary(self.root, day)
        self.assertTrue(text.startswith("我的日记2026年11月08日\n\n"))
        self.assertIn("今天他一直在修代码。", text)

    def test_missing_read_returns_empty(self):
        self.assertEqual(diary_engine.read_diary(self.root, date(2000, 1, 1)), "")

    def test_tree_groups_by_year_month_day(self):
        diary_engine.write_diary(self.root, date(2026, 11, 8), "a")
        diary_engine.write_diary(self.root, date(2026, 11, 9), "b")
        diary_engine.write_diary(self.root, date(2026, 10, 1), "c")
        diary_engine.write_diary(self.root, date(2025, 12, 31), "d")
        tree = diary_engine.list_tree(self.root)
        self.assertEqual(tree["total"], 4)
        self.assertEqual(tree["latest"], "2026-11-09")
        self.assertEqual([y["year"] for y in tree["years"]], [2026, 2025])
        self.assertEqual([m["month"] for m in tree["years"][0]["months"]], [11, 10])
        self.assertEqual([d["date"] for d in tree["years"][0]["months"][0]["days"]],
                         ["2026-11-09", "2026-11-08"])

    def test_parse_day_variants(self):
        for value in ("2026-11-08", "2026/11/8", "2026年11月8日", date(2026, 11, 8)):
            self.assertEqual(diary_engine.parse_day(value), date(2026, 11, 8))
        self.assertIsNone(diary_engine.parse_day("不是日期"))

    def test_copy_folder_to_new_root_keeps_dates(self):
        diary_engine.write_diary(self.root, date(2026, 11, 8), "a")
        diary_engine.write_diary(self.root, date(2025, 12, 31), "b")
        new_root = tempfile.mkdtemp(prefix="amo-diary-copy-")
        try:
            shutil.copytree(self.root, new_root, dirs_exist_ok=True)
            tree = diary_engine.list_tree(new_root)
            self.assertEqual(tree["total"], 2)
            self.assertEqual(tree["latest"], "2026-11-08")
            self.assertEqual([y["year"] for y in tree["years"]], [2026, 2025])
            self.assertIn("a", diary_engine.read_diary(new_root, date(2026, 11, 8)))
        finally:
            shutil.rmtree(new_root, ignore_errors=True)

    def test_renamed_file_still_readable_by_folder_date(self):
        path = diary_engine.write_diary(self.root, date(2026, 11, 8), "随手记的内容")
        os.rename(path, os.path.join(os.path.dirname(path), "随手记.txt"))
        tree = diary_engine.list_tree(self.root)
        self.assertEqual(tree["total"], 1)
        self.assertEqual(tree["years"][0]["months"][0]["days"][0]["date"], "2026-11-08")
        self.assertIn("随手记的内容", diary_engine.read_diary(self.root, date(2026, 11, 8)))

    def test_build_prompt_grounds_in_schedule(self):
        prompt = diary_engine.build_prompt(
            date(2026, 11, 8),
            ["他：我在修 bug", "我：好"],
            ["13:00 在工位写报告", "19:30 在家煮番茄蛋汤"],
        )
        self.assertIn("2026年11月08日", prompt)
        self.assertIn("13:00 在工位写报告", prompt)
        self.assertIn("他：我在修 bug", prompt)
        self.assertIn("不许编", prompt)
        self.assertIn("骨架", prompt)

    def test_build_prompt_handles_empty_material(self):
        prompt = diary_engine.build_prompt(date(2026, 11, 8), [], [])
        self.assertIn("这天没怎么聊", prompt)
        self.assertIn("没有小日记", prompt)

    def test_phrase_pools_are_96_unique(self):
        pools = [
            diary_lines.DIARY_ENDING_HINTS,
            diary_lines.DIARY_WRITTEN_REPLIES,
            diary_lines.DIARY_SHOW_REPLIES,
        ]
        for pool in pools:
            self.assertEqual(len(pool), 96)
            self.assertEqual(len(set(pool)), 96)

    def test_prompt_uses_a_sample_from_ending_pool(self):
        # 约三成日子才要求“不要看”，测试固定挑一个该有的日子。
        day = date(2026, 11, 8)
        for offset in range(10):
            candidate = day + timedelta(days=offset)
            if diary_engine.wants_private_ending(candidate):
                day = candidate
                break
        self.assertTrue(diary_engine.wants_private_ending(day))
        prompt = diary_engine.build_prompt(day, ["他：在吗"], ["13:00 在工位"])
        self.assertTrue(any(item in prompt for item in diary_engine.DIARY_ENDING_HINTS))
        self.assertNotIn("不许偷看", diary_engine.DIARY_ENDING_HINTS)

    def test_private_ending_is_occasional_and_guaranteed(self):
        days = [date(2026, 11, 1) + timedelta(days=i) for i in range(30)]
        wanted = [day for day in days if diary_engine.wants_private_ending(day)]
        self.assertTrue(0 < len(wanted) < len(days))
        body = "今天上班有点累，但晚饭吃得很香。"
        out = diary_engine.ensure_private_ending(wanted[0], body)
        self.assertNotEqual(out, body)
        self.assertTrue(any(word in out for word in diary_engine._PRIVATE_ENDING_WORDS))
        # 已经写过“不给看”的，不重复补。
        already = body + "\n\n不给看，收好啦。"
        self.assertEqual(diary_engine.ensure_private_ending(wanted[0], already), already)

    def test_import_folder_merges_by_date(self):
        source = tempfile.mkdtemp(prefix="amo-diary-src-")
        try:
            nested = os.path.join(source, "2025", "12", "31")
            os.makedirs(nested)
            with open(os.path.join(nested, "我的日记2025年12月31日.txt"), "w", encoding="utf-8") as fp:
                fp.write("旧电脑的日记")
            # 平铺文件按文件名解析
            with open(os.path.join(source, "我的日记2025年12月30日.txt"), "w", encoding="utf-8") as fp:
                fp.write("平铺的日记")
            # 目标里已存在的一天：不能被覆盖
            diary_engine.write_diary(self.root, date(2026, 11, 8), "现有的")
            nested2 = os.path.join(source, "2026", "11", "08")
            os.makedirs(nested2)
            with open(os.path.join(nested2, "我的日记2026年11月08日.txt"), "w", encoding="utf-8") as fp:
                fp.write("旧的同一天")
            result = diary_engine.import_folder(source, self.root)
            self.assertTrue(result["ok"])
            self.assertEqual(result["copied"], 2)
            self.assertEqual(result["skipped"], 1)
            self.assertIn("旧电脑的日记", diary_engine.read_diary(self.root, date(2025, 12, 31)))
            self.assertIn("平铺的日记", diary_engine.read_diary(self.root, date(2025, 12, 30)))
            self.assertIn("现有的", diary_engine.read_diary(self.root, date(2026, 11, 8)))
        finally:
            shutil.rmtree(source, ignore_errors=True)

    def test_import_missing_folder(self):
        result = diary_engine.import_folder(os.path.join(self.root, "nope"), self.root)
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["error"])


if __name__ == "__main__":
    unittest.main()
