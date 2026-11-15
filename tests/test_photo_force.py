# -*- coding: utf-8 -*-
"""约会生图封板测试：意图强拦截 / 单人自拍硬约束 / 动态场景 / 直令快速通道。"""
import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if path not in sys.path:
        sys.path.insert(0, path)

from draw_engine import (  # noqa: E402
    PHOTO_CLAIM_WORDS, SELFIE_WORLDVIEW, build_selfie_prompt,
)
from test_draw_engine import FakeEvent, FakeOmni, FakeState, engine_for  # noqa: E402

CLAIM_SAMPLES = ("拍一张", "拍好啦", "发给你", "你看这张", "照片", "合影", "留念",
                 "镜头", "咔嚓", "笑一个", "好看吗", "拍个照", "三、二、一")


def _engine(place="火锅店", activity="吃火锅", zone="restaurant", category="social", cfg=None):
    engine, plugin = engine_for(dict(cfg or {}), FakeOmni())
    state = FakeState()
    state.current_activity = lambda: {"place": place, "activity": activity,
                                      "zone": zone, "category": category}
    state.weather_brief = lambda: ""
    plugin.state = state
    return engine, plugin


class ClaimWordTests(unittest.TestCase):
    def test_word_list_covers_every_phrase_user_gave(self):
        for sample in CLAIM_SAMPLES:
            self.assertTrue(any(word in sample for word in PHOTO_CLAIM_WORDS), sample)

    def test_countdown_claim_triggers_a_real_photo(self):
        engine, plugin = _engine()
        event = FakeEvent("拍照留念")
        body, added = engine.enforce_photo_claim(event, "三、二、一，笑一个——好啦！")
        self.assertTrue(added)
        self.assertEqual(len(plugin.tasks), 1)

    def test_plain_reply_does_not_trigger(self):
        engine, plugin = _engine()
        body, added = engine.enforce_photo_claim(FakeEvent("聊天"), "好呀，那你多吃点~")
        self.assertFalse(added)
        self.assertEqual(plugin.tasks, [])


class DirectCommandTests(unittest.TestCase):
    def test_explicit_photo_wins_over_topic_mode_and_zero_prob(self):
        engine, _ = _engine(cfg={"draw_trigger_mode": "topic", "draw_topic_prob": 0})
        self.assertIsNotNone(engine.decide(FakeEvent("拍张照"), "好呀"))
        self.assertIsNotNone(engine.decide(FakeEvent("发张自拍"), "好呀"))

    def test_force_photo_bypasses_hourly_limit(self):
        engine, plugin = _engine(cfg={"draw_max_per_hour": 0, "draw_max_per_day": 0})
        refused = engine.send_photo_now(FakeEvent("x"), "随手拍", kind="real")
        self.assertIn("先不发图", refused)
        forced = engine.send_photo_now(FakeEvent("x"), "拍一张自拍发他", kind="real", force=True)
        self.assertIn("拍一张", forced)
        self.assertEqual(len(plugin.tasks), 1)


class DynamicSceneTests(unittest.TestCase):
    def test_prompt_is_assembled_from_facts(self):
        prompt = build_selfie_prompt("繁华的商场步行街", "米色针织衫配牛仔裤",
                                     {"zone": "mall"}, None, extra="手上拎着购物袋")
        self.assertIn("第一人称手机前置镜头自拍", prompt)
        self.assertIn("繁华的商场步行街", prompt)
        self.assertIn("米色针织衫配牛仔裤", prompt)
        self.assertIn("看着镜头微笑", prompt)
        self.assertIn("真实的手机抓拍画质", prompt)
        self.assertIn("只有她一个人", prompt)
        self.assertNotIn("风筝", prompt)

    def test_worldview_is_stated_in_prompt(self):
        self.assertIn("他不在画面里", SELFIE_WORLDVIEW)
        self.assertIn("合影", SELFIE_WORLDVIEW)

    def test_real_photo_uses_current_scene_and_outfit(self):
        engine, plugin = _engine(place="火锅店", activity="吃火锅", zone="restaurant")
        engine.send_photo_now(FakeEvent("在火锅店约会"), "红油锅底咕嘟咕嘟", kind="real")
        asyncio.run(plugin.tasks[0])
        call = plugin.context.omni.calls[0]
        self.assertIn("背景是", call["prompt"])
        self.assertIn("红油锅底咕嘟咕嘟", call["prompt"])
        self.assertIn("米色针织衫配牛仔裤", call["prompt"])
        self.assertIn("只有她一个人", call["prompt"])

    def test_negative_bans_second_person_in_both_languages(self):
        engine, plugin = _engine()
        engine.send_photo_now(FakeEvent("x"), "自拍一张", kind="real")
        asyncio.run(plugin.tasks[0])
        negative = plugin.context.omni.calls[0]["negative_prompt"]
        for word in ("男人", "合影", "第二个人", "group photo", "second person", "extra arms"):
            self.assertIn(word, negative)


if __name__ == "__main__":
    unittest.main()
