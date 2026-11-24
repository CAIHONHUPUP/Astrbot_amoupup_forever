# -*- coding: utf-8 -*-
"""动作描写开关 persona_action_narration（2026-11-24 用户要求可恢复）。

约定：
- 默认关：出站把括号动作全剥掉（2026-11-22 收口后的行为，逐字不变）；
- 打开：只留「一句十几字以内、跟台词同一行」的括号动作；
- 两种模式都仍然不发：整条只有动作、跨行括号、未闭合残括号、超长动作；
- 语音侧（amo_tts_guard / amo_voice）照旧只念台词，动作永不进 TTS。
"""
from __future__ import annotations

import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_message  # noqa: E402
import amo_persona_guard as guard  # noqa: E402
import amo_prompt_registry as registry  # noqa: E402


class FakePlugin:
    def __init__(self, allow=False, broken=False):
        self._allow = allow
        self._broken = broken

    def cfg_bool(self, key, default=False):
        if self._broken:
            raise RuntimeError("配置坏了")
        return self._allow if key == "persona_action_narration" else default


class SwitchTests(unittest.TestCase):
    def test_default_is_off(self):
        self.assertFalse(guard.action_narration_enabled(FakePlugin(allow=False)))
        self.assertFalse(guard.action_narration_enabled(None))
        self.assertFalse(guard.action_narration_enabled(object()))

    def test_broken_config_falls_back_to_off(self):
        self.assertFalse(guard.action_narration_enabled(FakePlugin(broken=True)))

    def test_on_reads_config(self):
        self.assertTrue(guard.action_narration_enabled(FakePlugin(allow=True)))


class StripBehaviourTests(unittest.TestCase):
    ACCIDENT = "（嘴巴还塞着饭，赶紧咽下去）道什么歉啊你。我吃着呢，食堂的菜一般般。"

    def test_off_strips_everything(self):
        self.assertEqual("道什么歉啊你。我吃着呢，食堂的菜一般般。",
                         guard.strip_action_narration(self.ACCIDENT))
        self.assertEqual("我吃上了", guard.strip_action_narration("（笑）我吃上了"))

    def test_on_keeps_one_short_inline_action(self):
        self.assertEqual("（笑）我吃上了", guard.strip_action_narration("（笑）我吃上了", keep_action=True))

    def test_on_keeps_accident_action(self):
        self.assertEqual(self.ACCIDENT, guard.strip_action_narration(self.ACCIDENT, keep_action=True))

    def test_on_still_drops_action_only(self):
        self.assertEqual("", guard.strip_action_narration("（笑了一下）", keep_action=True))
        self.assertEqual("", guard.strip_action_narration("（歪头）\n（笑）", keep_action=True))

    def test_on_still_drops_long_action(self):
        long_action = "（把桌上的文件一张一张码整齐，又顺手把水杯挪远一点）"
        self.assertEqual("我吃上了", guard.strip_action_narration(long_action + "我吃上了", keep_action=True))

    def test_on_still_drops_unclosed_paren(self):
        # 分段器劈开后只剩闭括号的残片：仍然丢到括回为止
        self.assertEqual("我也想你了",
                         guard.strip_action_narration("嘴角一下翘起来）我也想你了", keep_action=True))
        # 只有开括号、没有闭括号的尾巴整段丢
        self.assertEqual("", guard.strip_action_narration("（听到这句话，", keep_action=True))

    def test_on_keeps_action_and_line_on_separate_lines(self):
        text = "（把盒饭推过去）\n你先吃，我这边还有半个。"
        self.assertEqual(text, guard.strip_action_narration(text, keep_action=True))

    def test_numeric_paren_kept_in_both_modes(self):
        for keep in (False, True):
            with self.subTest(keep=keep):
                self.assertIn("（11月24日）", guard.strip_action_narration("（11月24日）我吃上了", keep_action=keep))

    def test_numeric_only_message_still_sent(self):
        # 改前「只有数字括号」是照发的（日期/价格），开关打开也不能把它当成动作删掉
        for keep in (False, True):
            with self.subTest(keep=keep):
                self.assertEqual("（11月24日）", guard.strip_action_narration("（11月24日）", keep_action=keep))


class StageTests(unittest.TestCase):
    class Ctx:
        def __init__(self, text, plugin):
            self.text = text
            self.plugin = plugin
            self.umo = ""
            self.source = "chat"
            self.user_text = ""

    def test_stage_respects_switch(self):
        ctx_off = self.Ctx("（笑）我吃上了", FakePlugin(allow=False))
        self.assertIsNone(guard._stage_narration(ctx_off))
        self.assertEqual("我吃上了", ctx_off.text)

        ctx_on = self.Ctx("（笑）我吃上了", FakePlugin(allow=True))
        self.assertIsNone(guard._stage_narration(ctx_on))
        self.assertEqual("（笑）我吃上了", ctx_on.text)

    def test_stage_drops_action_only_in_both_modes(self):
        for allow in (False, True):
            with self.subTest(allow=allow):
                ctx = self.Ctx("（笑了一下）", FakePlugin(allow=allow))
                self.assertEqual("action_only", guard._stage_narration(ctx))

    def test_send_to_umo_wires_the_switch(self):
        src = inspect.getsource(amo_message.MessageMixin._send_to_umo)
        self.assertIn("keep_action=_keep_action", src)
        self.assertIn("drop_conflicting_actions", src)
        # 动作仍然不进记忆/近聊（task-28 的落库口径不变）
        self.assertIn("_memory_text = _strip_actions(text) or text", src)


class PromptTests(unittest.TestCase):
    def test_default_prompt_still_forbids(self):
        text = registry.render("llm.rule.core")
        self.assertIn("不写动作旁白", text)
        self.assertIn("不写括号动作", text)
        self.assertNotIn("可以带一句很短的括号动作", text)

    def test_enabled_prompt_allows_one_inline_action(self):
        text = registry.render("llm.rule.core", allow_action=True)
        self.assertIn("可以带一句很短的括号动作", text)
        self.assertNotIn("不写括号动作", text)
        self.assertNotIn("不写动作旁白", text)

    def test_style_carries_turn_word_rule(self):
        for allow in (False, True):
            with self.subTest(allow=allow):
                text = registry.render("llm.rule.style", allow_action=allow)
                self.assertIn("倒是", text)
                self.assertIn("也算是", text)

    def test_style_tail_follows_switch(self):
        self.assertIn("不写动作旁白", registry.render("llm.rule.style"))
        self.assertIn("动作最多一句", registry.render("llm.rule.style", allow_action=True))

    def test_schema_has_switch_default_off(self):
        import json
        with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as fh:
            schema = json.load(fh)
        item = schema["persona_action_narration"]
        self.assertEqual("bool", item["type"])
        self.assertFalse(item["default"])


class SingleSourceTests(unittest.TestCase):
    """单一真源：任一模式下，所有注入点说的是同一句话，没有一处还在唱反调。

    2026-11-22 的教训是「基础人格说『不写』、后置规则说『允许』」——两套真源打架。
    这里锁住：文案只在 amo_narration_policy 出现一次，五个注入点一起翻。
    """

    SITES = ("amo_persona_pack.py", "amo_prompt_registry.py", "short_reply.py",
             "amo_date_context.py")
    FORBIDS = ("不写动作旁白", "不写括号动作", "不用括号动作")
    ALLOWS = ("可以带一句很短的括号动作", "动作只当点缀", "动作最多一句")

    def test_wording_lives_only_in_policy_module(self):
        for name in self.SITES:
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                src = fh.read()
            for phrase in self.FORBIDS + self.ALLOWS:
                self.assertNotIn(phrase, src, "%s 里不该再写死动作文案（应走 amo_narration_policy）" % name)

    def _blocks(self, allow):
        import amo_persona_pack as pack
        import short_reply
        return [
            registry.render("llm.rule.core", allow_action=allow),
            registry.render("llm.rule.style", allow_action=allow),
            registry.render("short.rules", allow_action=allow),
            registry.render("date.active", allow_action=allow, line="", scene="", meet=False,
                            meet_title="", outfit="", state_line=""),
            pack.card_text({"card": {}}, allow_action=allow),
            short_reply.prompt_block({}, "", allow_action=allow),
        ]

    def test_all_blocks_flip_together_when_on(self):
        for text in self._blocks(True):
            with self.subTest(text=text[:40]):
                for phrase in self.FORBIDS:
                    self.assertNotIn(phrase, text)
                self.assertTrue(any(phrase in text for phrase in self.ALLOWS), text)

    def test_all_blocks_flip_together_when_off(self):
        for text in self._blocks(False):
            with self.subTest(text=text[:40]):
                for phrase in self.ALLOWS:
                    self.assertNotIn(phrase, text)
                self.assertTrue(any(phrase in text for phrase in self.FORBIDS), text)


class ActionFactGuardTests(unittest.TestCase):
    """动作不是法外之地：和日程/状态冲突时，宁可不发动作。"""

    class GuardPlugin:
        def __init__(self, activity):
            self.state = self
            self.db = self
            self._activity = dict(activity)

        def current_activity(self):
            return dict(self._activity)

        def kv_get(self, key, default=None):
            return default

        def cfg_bool(self, key, default=False):
            return True

    def _drop(self, text):
        import amo_reply_pipeline as pipeline
        plugin = self.GuardPlugin({"activity": "吃午饭", "place": "公司附近",
                                   "zone": "office", "category": "meal"})
        return pipeline.drop_conflicting_actions(text, plugin)

    def test_wrong_place_inside_action_is_dropped(self):
        self.assertEqual("我在公司呢。", self._drop("（在商场逛着）我在公司呢。"))

    def test_harmless_action_kept(self):
        text = "（把盒饭推过去）你先吃，我这边还有半个。"
        self.assertEqual(text, self._drop(text))

    def test_no_action_untouched(self):
        self.assertEqual("我在公司呢。", self._drop("我在公司呢。"))

    def test_send_to_umo_checks_actions(self):
        src = inspect.getsource(amo_message.MessageMixin._send_to_umo)
        self.assertIn("drop_conflicting_actions", src)


if __name__ == "__main__":
    unittest.main()
