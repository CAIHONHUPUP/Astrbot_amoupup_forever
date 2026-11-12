# -*- coding: utf-8 -*-
"""拍照/画画的视角约束单测（纯标准库，不连 AstrBot、不花钱）。

规则（2026-11-11 定）：
- 她本人出镜 → 自拍视角（前置摄像头、手臂伸出、人物占主体）
- 风景/环境   → 她的拍摄视角（第一人称、不出现她本人）、空镜
两种后缀绝不能一起出现，否则模型会画成「第三人在看风景」。
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from photo_engine import PhotoEngine  # noqa: E402
from photo_view import (  # noqa: E402
    SCENERY_ONLY,
    VIEW_POV,
    VIEW_SELFIE,
    looks_like_her,
    view_suffix,
)


class ViewConstantsTests(unittest.TestCase):
    def test_suffix_switches_by_who_is_in_frame(self):
        self.assertEqual(view_suffix(True), VIEW_SELFIE)
        self.assertEqual(view_suffix(False), VIEW_POV)
        self.assertIn("前置摄像头", VIEW_SELFIE)
        self.assertIn("不出现她本人", VIEW_POV)

    def test_two_suffixes_never_share_wording(self):
        # 两种视角互相矛盾，绝不能同时出现在一句提示词里
        self.assertNotIn("不出现她本人", VIEW_SELFIE)
        self.assertNotIn("前置摄像头", VIEW_POV)


class LooksLikeHerTests(unittest.TestCase):
    def test_her_in_frame(self):
        for text in ("发张自拍", "拍张你在工位的照片", "让我看看你", "你在干嘛",
                     "你的照片给我一张"):
            self.assertTrue(looks_like_her(text), text)

    def test_her_view_of_something(self):
        # 「你看到的/你拍的」= 她的视角，人不出镜
        for text in ("画一张你看到的海", "你眼里的窗外", "来一张你拍的街角", "第一人称的自习室"):
            self.assertFalse(looks_like_her(text), text)

    def test_plain_scenery_is_not_her(self):
        for text in ("画一只小狗", "窗外的雨", "", "画一碗牛肉面"):
            self.assertFalse(looks_like_her(text), text)


class _FakePhotoEngine:
    """只喂提示词构造需要的几个依赖，不碰数据库/网络。"""

    def _cfg_str(self, key, default=""):
        return {"photo_prompt_prefix": "手机随手拍，真实自然，生活感，"}.get(key, default)

    def _clean_place(self, place):
        return str(place or "")

    def outfit_today(self):
        return "米色针织衫"


class PhotoPromptViewTests(unittest.TestCase):
    def setUp(self):
        self.engine = _FakePhotoEngine()
        self.activity = {"place": "工位", "activity": "趴桌上眯二十分钟", "zone": "office"}

    def test_selfie_prompt_has_front_camera_view(self):
        prompt = PhotoEngine._selfie_prompt_for(self.engine, "outfit", "家", self.activity)
        self.assertIn("自拍", prompt)
        self.assertIn(VIEW_SELFIE, prompt)
        self.assertNotIn("不出现她本人", prompt)

    def test_scenery_prompts_are_empty_shots_from_her_pov(self):
        for kind in ("travel", "shopping", "weekend", "daily"):
            prompt = PhotoEngine._prompt_for(self.engine, kind, "莞城区的写字楼", self.activity)
            self.assertIn(SCENERY_ONLY, prompt, kind)
            self.assertIn(VIEW_POV, prompt, kind)
            self.assertNotIn("自拍", prompt, kind)
            self.assertNotIn(VIEW_SELFIE, prompt, kind)

    def test_daily_drops_her_action_so_empty_shot_stays_consistent(self):
        # 旧行为把「趴桌上眯二十分钟」原样拼进去，那句要求出现人物，和空镜打架
        prompt = PhotoEngine._prompt_for(self.engine, "daily", "", self.activity)
        self.assertNotIn("眯二十分钟", prompt)
        self.assertIn("工位", prompt)

    def test_scenery_keeps_today_outfit_on_sleeve(self):
        prompt = PhotoEngine._prompt_for(self.engine, "daily", "", self.activity)
        self.assertIn("今天穿的是", prompt)

    def test_scenery_allows_passersby_but_not_her(self):
        prompt = PhotoEngine._prompt_for(self.engine, "daily", "", self.activity)
        self.assertIn("路人", prompt)
        self.assertIn("不出现她本人", prompt)

    def test_outfit_fallback_has_no_face(self):
        # 自拍失败时的兜底：没有参考图就别硬画脸
        prompt = PhotoEngine._prompt_for(self.engine, "outfit", "", self.activity)
        self.assertIn(SCENERY_ONLY, prompt)
        self.assertIn("米色针织衫", prompt)


if __name__ == "__main__":
    unittest.main()
