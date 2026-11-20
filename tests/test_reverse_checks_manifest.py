# -*- coding: utf-8 -*-
"""P1-7 反向验证清单自检：脚本存在、≥10 条核心、锚点/测试文件可解析。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
if WORK not in sys.path:
    sys.path.insert(0, WORK)

import reverse_checks  # noqa: E402

REQUIRED_AREAS = {"地点", "健康", "吃饭", "companion", "日程重叠", "文案地点",
                  "存储同步", "日记编造"}
REQUIRED_IDS = {"place_conflict", "health_rule", "meal_done", "companion",
                "schedule_overlap", "location_text", "storage_sync",
                "diary_fabrication", "trip_cost_idem"}


class ReverseChecksManifestTests(unittest.TestCase):
    def test_script_exists_and_has_at_least_ten_checks(self):
        self.assertTrue(os.path.isfile(os.path.join(WORK, "reverse_checks.py")))
        self.assertGreaterEqual(len(reverse_checks.CHECKS), 10)

    def test_core_areas_and_ids_are_covered(self):
        areas = {check["area"] for check in reverse_checks.CHECKS}
        ids = {check["id"] for check in reverse_checks.CHECKS}
        self.assertTrue(REQUIRED_AREAS <= areas, REQUIRED_AREAS - areas)
        self.assertTrue(REQUIRED_IDS <= ids, REQUIRED_IDS - ids)
        self.assertEqual(len(ids), len(reverse_checks.CHECKS), "检查 id 不能重复")

    def test_every_edit_anchor_is_unique_in_target(self):
        for check in reverse_checks.CHECKS:
            problems = reverse_checks.validate(check)
            self.assertEqual(problems, [], "%s: %s" % (check["id"], problems))

    def test_every_test_node_points_to_an_existing_file(self):
        for check in reverse_checks.CHECKS:
            self.assertTrue(check["tests"], check["id"])
            for node in check["tests"]:
                self.assertIn("::", node, node)
                test_file = node.split("::", 1)[0]
                self.assertTrue(os.path.isfile(os.path.join(ROOT, test_file)), node)

    def test_script_only_mutates_a_temp_copy(self):
        source = open(os.path.join(WORK, "reverse_checks.py"), encoding="utf-8").read()
        self.assertIn("tempfile.mkdtemp", source)
        self.assertIn("shutil.copytree", source)
        self.assertIn("shutil.rmtree", source)
        self.assertNotIn("open(os.path.join(ROOT, check[\"target\"]), \"wb\")", source)


if __name__ == "__main__":
    unittest.main()
