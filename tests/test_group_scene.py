# -*- coding: utf-8 -*-
"""群聊场景：配置、Prompt、防社死拦截、主动消息渠道。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import group_scene as gs  # noqa: E402


class FakeEvent:
    def __init__(self, group_id="", group_name=""):
        self.group_id = group_id
        self.group_name = group_name

    def get_group_id(self):
        return self.group_id

    def get_group_name(self):
        return self.group_name


class FakePlugin:
    def __init__(self, raw="1055679275=friend_group\n888=work_group"):
        self.raw = raw

    def cfg_str(self, key, default=""):
        if key == "group_scenes":
            return self.raw
        if key == "owner_nickname":
            return "阿鱼"
        return default


class SceneTests(unittest.TestCase):
    def test_private(self):
        self.assertEqual(gs.scene_for_event(FakePlugin(), FakeEvent()), "private")

    def test_configured_group(self):
        self.assertEqual(gs.scene_for_event(FakePlugin(), FakeEvent("1055679275")), "friend_group")
        self.assertEqual(gs.scene_for_event(FakePlugin(), FakeEvent("888")), "work_group")

    def test_unknown_group_is_stranger(self):
        self.assertEqual(gs.scene_for_event(FakePlugin(), FakeEvent("999")), "stranger_group")

    def test_parse_json(self):
        self.assertEqual(gs.parse_scenes('{"1": "work_group"}'), {"1": "work_group"})

    def test_proactive_channels(self):
        p = FakePlugin()
        self.assertTrue(gs.proactive_allowed(p, "aiocqhttp:PrivateMessage:1"))
        self.assertTrue(gs.proactive_allowed(p, "aiocqhttp:GroupMessage:1055679275"))
        self.assertFalse(gs.proactive_allowed(p, "aiocqhttp:GroupMessage:888"))
        self.assertFalse(gs.proactive_allowed(p, "aiocqhttp:GroupMessage:999"))


class PromptTests(unittest.TestCase):
    def test_work_group_constraints(self):
        block = gs.prompt_block("work_group", "同事A", "阿鱼")
        self.assertIn("工作群", block)
        self.assertIn("15 字", block)
        self.assertIn("绝不撒娇", block)
        self.assertIn("不是你的老公", block)

    def test_friend_group_no_husband_call(self):
        block = gs.prompt_block("friend_group", "闺蜜", "阿鱼")
        self.assertIn("不要叫老公", block)

    def test_stranger_group_cold(self):
        block = gs.prompt_block("stranger_group", "陌生人", "阿鱼")
        self.assertIn("高冷", block)
        self.assertIn("只有被 @", block)

    def test_owner_mention_soft_but_limited(self):
        block = gs.prompt_block("work_group", "老公", "阿鱼", is_owner=True)
        self.assertIn("别闹，在忙呢", block)

    def test_private_is_open(self):
        block = gs.prompt_block("private")
        self.assertIn("可以撒娇", block)
        self.assertIn("老公", block)


class GuardTests(unittest.TestCase):
    def test_private_disclosure_is_rewritten(self):
        text, changed = gs.guard_group_reply("呜呜呜老公我没钱交房贷了", "friend_group", "阿鱼")
        self.assertTrue(changed)
        for word in ("老公", "房贷", "呜呜呜", "没钱"):
            self.assertNotIn(word, text)

    def test_work_reply_max_15(self):
        text, changed = gs.guard_group_reply("好的我知道了，这个方案我下午再仔细看一下", "work_group")
        self.assertTrue(changed)
        self.assertLessEqual(len(text), 15)

    def test_plain_friend_reply_kept(self):
        self.assertEqual(gs.guard_group_reply("哈哈哈笑死", "friend_group")[0], "哈哈哈笑死")

    def test_private_not_filtered(self):
        text, changed = gs.guard_group_reply("呜呜呜老公我肚子好饿", "private")
        self.assertFalse(changed)
        self.assertIn("老公", text)


class WiringTests(unittest.TestCase):
    def test_compose_uses_group_scene(self):
        source = open(os.path.join(ROOT, "amo_llm.py"), encoding="utf-8").read()
        self.assertIn("group_scene.prompt_block", source)
        self.assertIn("group_scene.guard_group_reply", source)
        self.assertIn("_maybe_group_private_tease", source)

    def test_proactive_gate(self):
        source = open(os.path.join(ROOT, "amo_proactive.py"), encoding="utf-8").read()
        self.assertIn("proactive_allowed", source)


if __name__ == "__main__":
    unittest.main()
