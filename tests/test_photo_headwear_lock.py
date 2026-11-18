# -*- coding: utf-8 -*-
"""P3-1：自拍头饰只按三视图蓝蝴蝶结；普通空镜不被误伤。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import photo_view  # noqa: E402
import test_photo_plan as tp  # noqa: E402
import test_ref_order_scope as tr  # noqa: E402

PhotoEngine = tp.PhotoEngine
LOCK = "头饰只按三视图：蓝蝴蝶结"
BANNED = ("女仆头饰", "发箍", "白边头饰", "额外发饰")


class SelfieHeadwearLockTests(unittest.TestCase):
    def _activity(self):
        return {"zone": "home_sofa", "place": "家", "activity": "窝在沙发上",
                "category": "rest"}

    def test_selfie_prompt_has_exact_headwear_lock(self):
        prompt = PhotoEngine._selfie_prompt_for(
            tp.FakeEngine(), "daily", "", self._activity(), pose_text="__POSE__")
        self.assertIn(photo_view.HEADWEAR_LOCK, prompt)
        self.assertIn(LOCK, prompt)
        self.assertIn("蓝蝴蝶结", prompt)
        for word in BANNED:
            self.assertIn(word, prompt)

    def test_safe_selfie_prompt_keeps_headwear_lock(self):
        prompt = PhotoEngine._selfie_prompt_for(
            tp.FakeEngine(), "daily", "", self._activity(), pose_text="__POSE__", safe=True)
        self.assertIn(photo_view.HEADWEAR_LOCK, prompt)

    def test_empty_shot_prompt_is_not_touched(self):
        prompt = PhotoEngine._prompt_for(
            tp.FakeEngine(), "daily", "", self._activity())
        self.assertNotIn(LOCK, prompt)
        self.assertNotIn("女仆头饰", prompt)

    def test_selfie_negative_prompt_has_headwear_words(self):
        for word in BANNED:
            self.assertIn(word, photo_view.SELFIE_NEGATIVE)

    def test_selfie_reference_hint_carries_headwear_lock(self):
        engine = tr.FakeEngine(tr._root(), {"coat": "卡其色厚呢大衣"})
        tr._item(engine.plugin, "day", "上衣（外套）", "卡其色厚呢大衣")
        plan = tr._plan_room(home=True)
        pose = {"name": "托腮"}
        omni = tr.FakeOmni()
        asyncio.run(PhotoEngine._generate(engine, omni, "P", True, pose=pose, plan=plan))
        call = omni.calls[-1]
        self.assertIn("接下来是她的三视图", call["prompt"])
        self.assertIn(photo_view.HEADWEAR_LOCK, call["prompt"])

    def test_empty_reference_hint_has_no_headwear_lock(self):
        engine = tr.FakeEngine(tr._root(), {"top": "米色雪纺衬衫"})
        plan = tr._plan_room(home=True)
        omni = tr.FakeOmni()
        asyncio.run(PhotoEngine._generate(engine, omni, "P", False, pose=None, plan=plan))
        call = omni.calls[-1]
        self.assertNotIn(photo_view.HEADWEAR_LOCK, call["prompt"])
        self.assertNotIn("negative_prompt", call)

    def test_draw_selfie_prompt_has_headwear_lock(self):
        import draw_engine

        prompt = draw_engine.build_selfie_prompt(
            "家里的客厅", "米色针织衫配牛仔裤", {}, {}, light="自然光")
        self.assertIn(photo_view.HEADWEAR_LOCK, prompt)


if __name__ == "__main__":
    unittest.main()
