# -*- coding: utf-8 -*-
"""关系网自动沉淀的单测：门槛、保护规则、两跳推断、幂等。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from relation_sediment import auto_relation_for, note_kind, plan_edges  # noqa: E402


def _stats(uid, count, days):
    return [{"uid": uid, "c": count, "f": 0, "l": days * 86400}]


class SedimentTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(auto_relation_for(0, 0), "陌生人")
        self.assertEqual(auto_relation_for(1, 0), "陌生人")
        self.assertEqual(auto_relation_for(14, 30), "陌生人")
        self.assertEqual(auto_relation_for(15, 1), "陌生人")
        self.assertEqual(auto_relation_for(15, 2), "熟人")
        self.assertEqual(auto_relation_for(60, 6), "熟人")
        self.assertEqual(auto_relation_for(60, 7), "朋友")

    def test_note_kind(self):
        self.assertEqual(note_kind(""), "")
        self.assertEqual(note_kind("自动：累计 70 条"), "auto")
        self.assertEqual(note_kind("推断：经小明认识"), "infer")
        self.assertEqual(note_kind("管理员手设"), "manual")

    def test_plans_bot_edge(self):
        plan = plan_edges(_stats("u1", 70, 10), {}, {}, infer_limit=5)
        self.assertEqual(plan, [("bot", "u1", "朋友", "自动：累计 70 条")])

    def test_small_talk_becomes_stranger(self):
        plan = plan_edges(_stats("u1", 2, 0), {}, {})
        self.assertEqual(plan, [("bot", "u1", "陌生人", "自动：累计 2 条")])

    def test_profile_only_becomes_stranger(self):
        plan = plan_edges([{"uid": "u9", "c": 0, "f": 0, "l": 0}], {}, {})
        self.assertEqual(plan, [("bot", "u9", "陌生人", "自动：在列表里")])

    def test_manual_edge_never_overwritten(self):
        existing = {("bot", "u1"): ("朋友", "管理员手设：老朋友")}
        self.assertEqual(plan_edges(_stats("u1", 70, 10), existing, {}), [])

    def test_family_lover_enemy_untouched(self):
        for relation in ("家人", "恋人", "仇人"):
            existing = {("bot", "u1"): (relation, "")}
            self.assertEqual(
                plan_edges(_stats("u1", 70, 10), existing, {}), [],
                relation,
            )

    def test_auto_edge_never_downgrades(self):
        existing = {("bot", "u1"): ("朋友", "自动：累计 70 条")}
        # 这阵子聊得少了，只够「陌生人」，保持「朋友」
        self.assertEqual(plan_edges(_stats("u1", 3, 0), existing, {}), [])

    def test_auto_edge_upgrades(self):
        existing = {("bot", "u1"): ("熟人", "自动：累计 20 条")}
        plan = plan_edges(_stats("u1", 70, 10), existing, {})
        self.assertIn(("bot", "u1", "朋友", "自动：累计 70 条"), plan)

    def test_two_hop_inference(self):
        existing = {("u1", "u2"): ("朋友", "")}
        plan = plan_edges(_stats("u1", 70, 10), existing, {"u1": "小明"})
        self.assertIn(("bot", "u1", "朋友", "自动：累计 70 条"), plan)
        self.assertIn(("bot", "u2", "朋友", "推断：经小明认识"), plan)

    def test_stranger_does_not_drive_inference(self):
        # 只聊过两句的人，不该把 TA 的朋友介绍给 bot
        existing = {("u1", "u2"): ("朋友", "")}
        plan = plan_edges(_stats("u1", 2, 0), existing, {"u1": "小明"})
        self.assertNotIn(("bot", "u2", "朋友", "推断：经小明认识"), plan)

    def test_inference_idempotent(self):
        existing = {
            ("u1", "u2"): ("朋友", ""),
            ("bot", "u1"): ("朋友", "自动：累计 70 条"),
            ("bot", "u2"): ("朋友", "推断：经小明认识"),
        }
        self.assertEqual(plan_edges(_stats("u1", 70, 10), existing, {"u1": "小明"}), [])

    def test_inference_limit_per_user(self):
        existing = {("u1", "u%d" % i): ("同事", "") for i in range(2, 8)}
        plan = plan_edges(_stats("u1", 70, 10), existing, {}, infer_limit=3)
        inferred = [item for item in plan if item[3].startswith("推断：")]
        self.assertEqual(len(inferred), 3)

    def test_enemy_human_edge_not_used(self):
        existing = {("u1", "u2"): ("仇人", "")}
        plan = plan_edges(_stats("u1", 70, 10), existing, {"u1": "小明"})
        self.assertNotIn(("bot", "u2", "朋友", "推断：经小明认识"), plan)


if __name__ == "__main__":
    unittest.main()
