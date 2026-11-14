# -*- coding: utf-8 -*-
"""日程主语校验：纯函数 + 写库护栏（2026-11-14）。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from astrbot.api.star import Context  # noqa: E402
import main  # noqa: E402
from schedule_subject import (  # noqa: E402
    OTHER,
    SELF,
    UNKNOWN,
    WITH_OTHER,
    needs_date,
    rewrite_activity,
    subject_of,
    validate_activity,
)


class SubjectPureTests(unittest.TestCase):
    def test_self_activities_are_allowed(self):
        for text in (
            "在办公室敲周报",
            "和闺蜜逛街",
            "我和同事去吃饭",
            "自己在家躺着",
            "去楼下买点水果",
            "在电影院看电影",
        ):
            self.assertEqual(subject_of(text), SELF, text)
            self.assertFalse(needs_date(text), text)
            self.assertTrue(validate_activity(text)["ok"], text)

    def test_companion_activities_need_a_date(self):
        for text in (
            "和老公去逛街",
            "跟老公一起吃饭",
            "老公陪我看电影",
            "他带我去医院",
            "让老公开车来接我",
            "与我男朋友逛街",
        ):
            self.assertEqual(subject_of(text), WITH_OTHER, text)
            self.assertTrue(needs_date(text), text)
            self.assertFalse(validate_activity(text)["ok"], text)

    def test_other_subject_is_not_her_schedule(self):
        for text in ("他在加班", "老公出差了", "他今天很忙", "他去医院了"):
            self.assertEqual(subject_of(text), OTHER, text)
            self.assertTrue(needs_date(text), text)

    def test_empty_and_unknown_are_safe(self):
        for text in ("", None, "   "):
            self.assertEqual(subject_of(text), UNKNOWN)
            self.assertFalse(needs_date(text))
            self.assertTrue(validate_activity(text)["ok"])

    def test_date_approval_keeps_companion_text(self):
        for text in ("和老公去逛街", "老公陪我看电影"):
            result = validate_activity(text, approved=True)
            self.assertTrue(result["ok"], text)
            self.assertEqual(result["activity"], text)
            self.assertFalse(result["changed"])

    def test_rewrite_swaps_companion_to_friend(self):
        self.assertEqual(rewrite_activity("和老公去逛街"), "和闺蜜去逛街")
        self.assertEqual(rewrite_activity("跟老公一起吃饭"), "和闺蜜一起吃饭")
        self.assertEqual(rewrite_activity("老公陪我看电影"), "和闺蜜看电影")
        self.assertEqual(rewrite_activity("他带我去医院"), "和闺蜜去医院")

    def test_other_subject_rewrite_is_neutral(self):
        self.assertEqual(rewrite_activity("他在加班"), "自己待着")

    def test_rewrite_keeps_self_activity_untouched(self):
        for text in ("和闺蜜逛街", "在办公室敲周报"):
            self.assertEqual(rewrite_activity(text), text)

    def test_validate_reports_change_and_is_idempotent(self):
        result = validate_activity("和老公去逛街")
        self.assertFalse(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["activity"], "和闺蜜去逛街")
        again = validate_activity(result["activity"])
        self.assertTrue(again["ok"])
        self.assertFalse(again["changed"])

    def test_custom_companion_and_fallback(self):
        self.assertEqual(rewrite_activity("和老公逛街", companion="同事"), "和同事逛街")
        self.assertEqual(
            validate_activity("他在加班", companion="同事")["activity"],
            "自己待着",
        )


class CommitGuardTests(unittest.TestCase):
    """生成来源写库时改主语；用户明确设的日程不动；约过会就不改。"""

    DAY = "2026-11-14"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-subj-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.plugin = self._build()
        asyncio.run(self.plugin._ensure_ready())

    def _build(self):
        plugin = main.HumanCorePlugin(Context(), {})

        async def noop():
            return None

        def spawn(coro):
            coro.close()
            return None

        plugin._spawn = spawn
        plugin._sync_with_data_plugin = noop
        plugin._warm_up = noop
        plugin._background_loop = noop
        return plugin

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

    def _commit(self, activity, source="social"):
        item = {"start": "09:00", "end": "10:00", "activity": activity,
                "place": "商场", "zone": "mall", "category": "play"}
        self.plugin.state.commit_schedule_change(
            self.DAY, [item], old_items=[], source=source, reason="test")
        return self.plugin.state.schedule[self.DAY][0]["activity"]

    def _approve_date(self):
        self.plugin.db.kv_set("date_now", {"line": "正和老公逛街", "until": time.time() + 3600})

    def test_generated_activity_with_companion_is_rewritten(self):
        self.assertEqual(self._commit("和老公去逛街"), "和闺蜜去逛街")

    def test_generated_other_subject_is_rewritten(self):
        self.assertEqual(self._commit("他在加班"), "自己待着")

    def test_generated_self_activity_is_untouched(self):
        self.assertEqual(self._commit("和闺蜜去逛街"), "和闺蜜去逛街")

    def test_user_schedule_change_is_untouched(self):
        self.assertEqual(self._commit("和老公去逛街", source="chat"), "和老公去逛街")

    def test_approved_date_keeps_companion(self):
        self._approve_date()
        self.assertEqual(self._commit("和老公去逛街"), "和老公去逛街")

    def test_guard_runs_on_jit_source(self):
        self.assertEqual(self._commit("和老公看电影", source="jit"), "和闺蜜看电影")


if __name__ == "__main__":
    unittest.main()
