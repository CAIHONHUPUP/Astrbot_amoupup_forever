# -*- coding: utf-8 -*-
"""提示词装配：场景/姿势/守卫必须真的进 prompt；[PHOTO_DEBUG] 开关生效。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import photo_engine  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402
from photo_rules import resolve_plan  # noqa: E402


class FakeEngine:
    debug_prompt = False

    def _time_light(self):
        return "现在是10点，上午明亮自然光"

    def outfit_today(self):
        return "米色针织衫配牛仔裤"

    def _cfg_str(self, key, default=""):
        return default


class PromptCompositionTests(unittest.TestCase):
    def test_selfie_prompt_contains_plan_scene_pose_guard(self):
        plan = resolve_plan({"zone": "road", "place": "路上", "activity": "挤地铁", "transit": "subway"})
        prompt = PhotoEngine._selfie_prompt_for(
            FakeEngine(), "daily", "__SCENE__", plan.activity,
            pose_text="__POSE__", plan=plan,
        )
        self.assertIn("__SCENE__", prompt)
        self.assertIn("__POSE__", prompt)
        self.assertIn(plan.guard, prompt)

    def test_empty_shot_prompt_contains_guard(self):
        plan = resolve_plan({"zone": "office", "place": "写字楼", "activity": "上班"})
        with mock.patch.object(photo_engine, "resolve_plan", return_value=plan):
            prompt = PhotoEngine._prompt_for(FakeEngine(), "daily", "", plan.activity)
        self.assertIn(plan.guard, prompt)
        self.assertIn("空镜", prompt)

    def test_debug_log_prints_required_fields(self):
        plan = resolve_plan({"zone": "road", "place": "路上", "activity": "挤地铁", "transit": "subway"})
        engine = FakeEngine()
        engine.debug_prompt = True
        with mock.patch.object(photo_engine.logger, "info") as info:
            photo_engine._log_photo_debug(engine, plan.activity, plan, "FINAL", "POSE")
        text = "\n".join(str(call.args[0]) for call in info.call_args_list)
        for field in ("[PHOTO_DEBUG]", "zone=road_subway", "pose_rule=stand_hold_strap",
                      "guard=", "final_prompt=FINAL"):
            self.assertIn(field, text)

    def test_debug_log_silent_when_disabled(self):
        plan = resolve_plan({"zone": "home", "place": "家", "activity": "休息", "category": "rest"})
        with mock.patch.object(photo_engine.logger, "info") as info:
            photo_engine._log_photo_debug(FakeEngine(), plan.activity, plan, "FINAL")
        self.assertFalse(info.called)


if __name__ == "__main__":
    unittest.main()
