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
from photo_view import (ROOM_NO_BODY, SELFIE_FRAMING, SELFIE_NEGATIVE,
                        SELFIE_STYLE)
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

    def __init__(self):
        class _State:
            def health_info(self):
                return {"status": "感冒", "sick": True}
        class _Plugin:
            state = _State()
        self.plugin = _Plugin()

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

    def test_scenery_never_shows_a_hand_or_body_part(self):
        # 2026-11-15：空镜里不再允许露手/袖口（那是"空房间+一只手"的来源）
        prompt = PhotoEngine._prompt_for(self.engine, "daily", "", self.activity)
        self.assertIn(ROOM_NO_BODY, prompt)
        self.assertNotIn("露出的手", prompt)
        self.assertNotIn("今天穿的是", prompt)

    def test_scenery_allows_passersby_but_not_her(self):
        prompt = PhotoEngine._prompt_for(self.engine, "daily", "", self.activity)
        self.assertIn("路人", prompt)
        self.assertIn("不出现她本人", prompt)

    def test_selfie_prompt_has_camera_style_and_framing(self):
        prompt = PhotoEngine._selfie_prompt_for(self.engine, "outfit", "家", self.activity)
        self.assertIn("第一人称自拍", prompt)
        self.assertIn(SELFIE_FRAMING, prompt)
        self.assertIn(SELFIE_STYLE, prompt)
        self.assertIn("前置摄像头", prompt)

    def test_selfie_prompt_uses_sick_state(self):
        bed = {"place": "卧室", "activity": "躺在床上休息", "zone": "home_bed"}
        prompt = PhotoEngine._selfie_prompt_for(self.engine, "outfit", "卧室", bed)
        self.assertIn("盖着被子", prompt)
        self.assertIn("素颜", prompt)
        self.assertIn("头发有点乱", prompt)


    def test_bed_selfie_has_no_shoes_fact(self):
        activity = {"place": "卧室", "activity": "躺在床上", "zone": "home_bed"}
        prompt = PhotoEngine._selfie_prompt_for(self.engine, "outfit", "卧室", activity)
        self.assertIn("盖着被子", prompt)
        self.assertIn("不要出现鞋", prompt)
        self.assertNotIn("光着脚", prompt)
        self.assertNotIn("脚踝", prompt)

    def test_bed_selfie_negative_blocks_shoes(self):
        self.assertIn("床上的鞋", SELFIE_NEGATIVE)
        self.assertIn("室内穿鞋", SELFIE_NEGATIVE)

    def test_selfie_negative_never_bans_people(self):
        self.assertIn("多只手", SELFIE_NEGATIVE)
        self.assertNotIn("人物", SELFIE_NEGATIVE)
        self.assertNotIn("人类", SELFIE_NEGATIVE)
        self.assertIn("空房间", SELFIE_NEGATIVE)


    def test_selfie_failure_never_falls_back_to_empty_room(self):
        import inspect
        source = inspect.getsource(PhotoEngine._share)
        self.assertIn("空镜兜底已禁用", source)
        self.assertNotIn('_prompt_for("scenery"', source)

    def test_draw_engine_uses_selfie_framing_and_negative(self):
        import inspect
        import draw_engine
        source = inspect.getsource(draw_engine.DrawEngine)
        self.assertIn("SELFIE_FRAMING", source)
        self.assertIn("SELFIE_NEGATIVE", source)
    def test_outfit_fallback_has_no_face(self):
        # 自拍失败时的兜底：没有参考图就别硬画脸
        prompt = PhotoEngine._prompt_for(self.engine, "outfit", "", self.activity)
        self.assertIn(SCENERY_ONLY, prompt)
        self.assertIn("米色针织衫", prompt)


class HomewearPromptTests(unittest.TestCase):
    """B2：居家便服自拍只保留具体单品的说法，不再注入泛化模板句。"""

    def test_homewear_selfie_has_exact_items_and_no_generic_template(self):
        class _Homewear:
            def current(self, *args, **kwargs):
                return {"homewear": True, "sleep": False,
                        "top": "纯棉长袖", "bottom": "米色居家长裤",
                        "shoes": "居家棉拖鞋"}
        engine = _FakePhotoEngine()
        engine.plugin.wardrobe = _Homewear()
        engine._selfie_outfit_text = lambda zone: "纯棉长袖、米色居家长裤"
        activity = {"place": "家", "activity": "坐在餐桌前吃饭",
                    "zone": "home", "category": "meal"}
        prompt = PhotoEngine._selfie_prompt_for(engine, "daily", "", activity)
        self.assertIn("纯棉长袖、米色居家长裤", prompt, "具体单品必须在提示词里")
        self.assertNotIn("穿着宽松居家便服", prompt,
                         "泛化模板句会被当成另一套衣服，导致穿搭被改写")
        self.assertNotIn("穿的就是这一套居家服", prompt,
                         "B2b：第二句泛化服装会和具体单品打架，必须删掉")
        self.assertIn("姿态松弛", prompt, "只补姿态，不再另起服装描述")


if __name__ == "__main__":
    unittest.main()
