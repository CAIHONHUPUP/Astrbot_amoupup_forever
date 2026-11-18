# -*- coding: utf-8 -*-
"""PromptRegistry（Phase 4）：块登记 / 顺序 / lean 压缩 / 审计 / 黄金文本零漂移。

黄金快照 tests/golden/prompts_golden.json 由重构前代码生成（work/capture_prompt_golden.py），
本文件逐字节比对注册表与旧模块入口的输出。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import prompt_fixtures as fx  # noqa: E402
import amo_prompt_registry as pr  # noqa: E402
from amo_prompt_registry import PromptBlock, PromptNotFound, PromptRegistry  # noqa: E402
import amo_llm  # noqa: E402

with open(os.path.join(TESTS, "golden", "prompts_golden.json"), encoding="utf-8") as _fh:
    GOLDEN = json.load(_fh)


def diff_index(left, right):
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index
    return min(len(left), len(right))


class TextCase(unittest.TestCase):
    def assertText(self, actual, expected, label):
        if actual == expected:
            return
        index = diff_index(actual, expected)
        self.fail("%s 文本漂移 @%d\n got=%r\nwant=%r"
                  % (label, index, actual[index:index + 60], expected[index:index + 60]))


class RegistryApiTests(unittest.TestCase):
    def test_block_inventory_is_unique(self):
        ids = pr.REGISTRY.ids()
        self.assertGreaterEqual(len(ids), 60)
        self.assertEqual(len(ids), len(set(ids)))
        for block_id in ("llm.self_role", "group.direct", "date.active",
                         "short.rules", "guard.immersion"):
            self.assertTrue(pr.REGISTRY.has(block_id), block_id)

    def test_duplicate_id_is_rejected(self):
        registry = PromptRegistry()
        registry.register(PromptBlock("same", "【A】", 10, text="x"))
        with self.assertRaises(ValueError):
            registry.register(PromptBlock("same", "【B】", 20, text="y"))
        registry.register(PromptBlock("same", "【B】", 20, text="y"), replace=True)
        self.assertEqual("y", registry.render("same"))

    def test_unknown_block_raises(self):
        with self.assertRaises(PromptNotFound):
            pr.REGISTRY.render("nope.not.registered")

    def test_build_respects_requested_order_and_separator(self):
        registry = PromptRegistry()
        registry.register(PromptBlock("a", "", 20, text="A"))
        registry.register(PromptBlock("b", "", 10, text="B"))
        self.assertEqual("B|A", registry.build(["b", "a"], sep="|"))
        self.assertEqual(["B", "A"], registry.parts(["b", "a"]))
        self.assertEqual(["b", "a"], [block.id for block in
                                      sorted(registry.blocks(), key=lambda b: b.order)])

    def test_block_order_metadata(self):
        ordered = pr.REGISTRY.blocks()
        self.assertEqual(sorted(block.order for block in ordered),
                         [block.order for block in ordered])
        self.assertLess(pr.REGISTRY.get("llm.self_role").order,
                        pr.REGISTRY.get("llm.rule.core").order)


class LeanTests(TextCase):
    def test_lean_map_matches_golden(self):
        self.assertEqual(GOLDEN["lean_parts"], pr.REGISTRY.lean_parts_map())

    def test_compress_matches_legacy_behaviour(self):
        parts = ["【长度】很长的说明", "【昵称区分】别的", "【事实锁定】旧文案", "普通一行"]
        self.assertEqual([
            "【长度】默认 1-2 句，能一句说清就一句。",
            "【事实锁定】地点/在做什么/有没有出门以【正在】为准，不编和地点矛盾的行程；"
            "他记错或试探时按日程纠正，别顺着他改口。",
            "普通一行",
        ], pr.compress(parts))

    def test_compress_keeps_unknown_blocks(self):
        parts = ["【没登记】原样保留", "普通"]
        self.assertEqual(parts, pr.compress(parts))


class PrivateSystemTests(TextCase):
    def test_private_parts_match_golden(self):
        self.assertEqual(GOLDEN["private_parts"], fx.private_parts())

    def test_private_system_matches_golden(self):
        self.assertText(fx.system_private(""), GOLDEN["private_system"], "私聊 system")

    def test_private_system_with_note_matches_golden(self):
        self.assertText(fx.system_private("刚才在忙，补一句。"),
                        GOLDEN["private_system_note"], "私聊 system(note)")


class GroupSystemTests(TextCase):
    def test_group_parts_match_golden(self):
        self.assertEqual(GOLDEN["group_parts"], fx.group_parts("", ""))

    def test_group_system_variants_match_golden(self):
        self.assertText(fx.system_group("", ""), GOLDEN["group_system"], "群聊 system")
        self.assertText(fx.system_group("bot", ""), GOLDEN["group_system_direct"],
                        "群聊 system(direct)")
        self.assertText(fx.system_group("other", "想插一句"),
                        GOLDEN["group_system_interject"], "群聊 system(interject)")


class DateAndShortTests(TextCase):
    def test_date_blocks_match_golden(self):
        self.assertEqual(GOLDEN["date_blocks"], fx.date_blocks())

    def test_short_blocks_match_golden(self):
        self.assertEqual(GOLDEN["short_blocks"], fx.short_blocks())
        self.assertEqual(GOLDEN["short_blocks"]["rules"], pr.render("short.rules"))
        self.assertEqual(GOLDEN["short_blocks"]["prompt"], fx.short_blocks()["prompt"])

    def test_photo_and_ownership_blocks_match_golden(self):
        self.assertText(amo_llm.PHOTO_REQUEST_RULE,
                        GOLDEN["constants"]["photo_request_rule"], "拍照规则")
        self.assertText(amo_llm.ACTION_OWNERSHIP_RULES,
                        GOLDEN["constants"]["action_ownership_rules"], "动作归属")


class GroupSceneTests(TextCase):
    def test_group_scene_blocks_match_golden(self):
        self.assertEqual(GOLDEN["group_scene_blocks"], fx.group_scene_blocks())

    def test_session_prompt_blocks_are_short(self):
        self.assertLess(len(pr.render("short.state.happy")), 120)
        self.assertTrue(pr.render("group.scene.private").startswith("【私聊】"))
        self.assertIn("各回各家", pr.render("date.home"))


class AuditTests(unittest.TestCase):
    def test_audit_reports_duplicate_missing_and_oversized(self):
        registry = PromptRegistry()
        registry.register(PromptBlock("dup.a", "【重复】", 10, text="a"))
        registry.register(PromptBlock("dup.b", "【重复】", 20, text="b"))
        registry.register(PromptBlock("lean.missing", "【缺lean】", 30, text="c",
                                      lean_required=True))
        registry.register(PromptBlock("long", "", 40, text="长" * 50))
        report = registry.audit(max_chars=10)
        self.assertEqual([{"tag": "【重复】", "ids": ["dup.a", "dup.b"]}],
                         report["duplicate_tags"])
        self.assertEqual([{"id": "lean.missing", "tag": "【缺lean】"}],
                         report["missing_lean"])
        self.assertEqual([{"id": "long", "chars": 50, "limit": 10}], report["oversized"])

    def test_real_registry_audit_is_structured(self):
        report = pr.audit()
        self.assertEqual({"duplicate_tags", "missing_lean", "oversized"}, set(report))
        tags = {item["tag"] for item in report["duplicate_tags"]}
        self.assertIn("【群里的人】", tags)
        self.assertIn("【被打扰】", tags)


if __name__ == "__main__":
    unittest.main()
