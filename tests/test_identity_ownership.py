# -*- coding: utf-8 -*-
"""动作归属/主体归属回归：不只看文案，直接断言规则、主体和前缀。"""
from __future__ import annotations

import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_llm import ACTION_OWNERSHIP_RULES, PHOTO_REQUEST_RULE  # noqa: E402
from amo_memory_context import memory_subject  # noqa: E402
from diary_engine import ensure_private_ending, wants_private_ending  # noqa: E402
from memory_hooks import render_recent_block  # noqa: E402
from photo_view import SCENERY_ONLY, VIEW_POV, VIEW_SELFIE  # noqa: E402


class ActionOwnershipTests(unittest.TestCase):
    def test_static_rules_name_bot_as_executor(self):
        self.assertIn("执行者是你（bot）", ACTION_OWNERSHIP_RULES)
        self.assertIn("用户说", ACTION_OWNERSHIP_RULES)
        self.assertIn("不是你", ACTION_OWNERSHIP_RULES)
        self.assertIn("画面主体是你自己", ACTION_OWNERSHIP_RULES)

    def test_photo_hint_has_executor_and_forbids_user_action(self):
        self.assertIn("执行者是你（bot）", PHOTO_REQUEST_RULE)
        self.assertIn("不是他", PHOTO_REQUEST_RULE)
        self.assertIn("你快点拍", PHOTO_REQUEST_RULE)  # 作为禁止项出现
        self.assertIn("照片由你生成并发送", PHOTO_REQUEST_RULE)

    def test_selfie_view_names_bot_subject(self):
        for text in (VIEW_SELFIE, VIEW_POV, SCENERY_ONLY):
            self.assertTrue("bot" in text or "用户" in text)
        self.assertIn("画面主体是bot自己", VIEW_SELFIE)
        self.assertIn("不是用户", VIEW_SELFIE)
        self.assertIn("不是用户拍的", VIEW_POV)


class MemorySubjectTests(unittest.TestCase):
    def test_subject_prefers_explicit_field(self):
        self.assertEqual(memory_subject({"subject": "bot", "kind": "fact"}), "bot")
        self.assertEqual(memory_subject({"subject": "user", "kind": "bot_media"}), "user")

    def test_old_bot_media_kind_is_bot(self):
        self.assertEqual(memory_subject({"kind": "bot_media"}), "bot")
        self.assertEqual(memory_subject({"kind": "fact"}), "user")
        self.assertEqual(memory_subject({}), "user")

    def test_history_labels_bot_and_user(self):
        text = render_recent_block([
            {"role": "user", "content": "在吗", "name": "他"},
            {"role": "bot", "content": "在", "name": "大肥鱼"},
        ])
        self.assertIn("他：在吗", text)
        self.assertIn("你：在", text)


class DiaryEndingTests(unittest.TestCase):
    def test_private_ending_is_occasional_and_idempotent(self):
        days = [datetime.date(2026, 11, 1) + datetime.timedelta(days=i) for i in range(30)]
        wanted = [day for day in days if wants_private_ending(day)]
        self.assertTrue(0 < len(wanted) < len(days))
        body = "今天上班有点累，晚饭吃得很香。"
        out = ensure_private_ending(wanted[0], body)
        self.assertNotEqual(out, body)
        self.assertEqual(ensure_private_ending(wanted[0], out), out)

    def test_non_private_day_does_not_force_hint(self):
        days = [datetime.date(2026, 11, 1) + datetime.timedelta(days=i) for i in range(30)]
        quiet = [day for day in days if not wants_private_ending(day)]
        self.assertTrue(quiet)
        body = "今天上班有点累。"
        self.assertEqual(ensure_private_ending(quiet[0], body), body)


if __name__ == "__main__":
    unittest.main()
