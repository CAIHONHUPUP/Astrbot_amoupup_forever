# -*- coding: utf-8 -*-
"""人设单一真源 + 出站语义守卫（task-28 P0-3/P0-4/P0-5/P0-6）。

对应 work/audit/memory_persona.md：
- 动作旁白 56.5%、45.4% 分段只有动作 → 出站必须 ≤5%（整条动作不发、行内旁白剥掉）；
- 「你美丽的老婆」「你们男人」「收到你后来」→ 禁语；
- 「什么视频…没别的视频」44 秒否认自己 → self-recent 硬清单 + denial 判定；
- 「别生气」→「我哪生气了」→ 先接情绪判定；
- 柳如烟→方圆、米白色米白色 → 姓名/物品 grounding。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_persona_guard as guard  # noqa: E402


class ActionNarrationTests(unittest.TestCase):
    def test_inline_action_stripped(self):
        self.assertEqual("什么视频", guard.strip_action_narration(
            "（把脸从靠垫上偏过来，抱枕还抱着）什么视频"))

    def test_action_only_detected(self):
        text = "（靠在椅背上，把手机举得离眼睛近了点）"
        self.assertTrue(guard.action_only(text))
        self.assertEqual("", guard.strip_action_narration(text))

    def test_action_with_latin_letters_stripped(self):
        self.assertEqual("我去啦", guard.strip_action_narration("（套回大T恤，坐回沙发）我去啦"))

    def test_date_parenthesis_kept(self):
        self.assertIn("（11月22日）", guard.strip_action_narration("（11月22日）我在家"))

    def test_normal_speech_kept(self):
        self.assertEqual("你回来啦", guard.strip_action_narration("你回来啦"))
        self.assertFalse(guard.has_action_narration("你回来啦"))

    def test_ratio_under_five_percent_after_strip(self):
        raw = [
            "（歪头）我在呢",
            "（笑了一下）你回来啦",
            "今天好累呀",
            "（揉了揉眼睛）饭吃了没",
            "我在看电视呢",
            "（抱着枕头）嗯嗯",
            "你猜我在干嘛",
        ]
        cleaned = [guard.strip_action_narration(row) for row in raw]
        self.assertLessEqual(guard.action_ratio(cleaned), 0.05)

    def test_ratio_measures_before(self):
        self.assertGreater(guard.action_ratio(["（歪头）在", "（笑）好"]), 0.9)


class ForbiddenPhraseTests(unittest.TestCase):
    def test_third_person_self_becomes_first_person(self):
        out = guard.neutralize_forbidden("老公你美丽的老婆晾这儿半天了。")
        self.assertNotIn("你美丽的老婆", out)
        self.assertIn("我", out)

    def test_you_men_replaced(self):
        out = guard.neutralize_forbidden("你们男人笑点真低。")
        self.assertNotIn("你们男人", out)
        self.assertIn("你", out)

    def test_empty_phrase_sentence_dropped(self):
        out = guard.neutralize_forbidden("收到你后来说的那句了。图卡住了。")
        self.assertNotIn("收到你后来", out)

    def test_issues_reports_forbidden(self):
        found = guard.issues("你回来了", "你美丽的老婆在这儿呢")
        self.assertTrue(any("禁语" in item for item in found))


class SelfRecentTests(unittest.TestCase):
    def test_extract_own_lines(self):
        recent = [
            {"role": "user", "content": "在吗"},
            {"role": "bot", "content": "刚刷到一个视频太离谱了"},
            {"role": "bot", "content": "[我发的照片] 自拍"},
            {"role": "bot", "content": "你回来啦"},
        ]
        self.assertEqual(["刚刷到一个视频太离谱了", "你回来啦"],
                         guard.self_recent_texts(recent))

    def test_block_marks_unavoidable(self):
        block = guard.self_recent_block(["刚刷到一个视频太离谱了"])
        self.assertIn("不可否认", block)
        self.assertIn("什么视频", block)

    def test_denial_of_own_share_flagged(self):
        found = guard.denial_of_self_issue(
            "视频啥内容啊", "什么视频，我这边就一个人看综艺，没别的视频",
            ["刚刷到一个视频太离谱了"])
        self.assertTrue(found)
        self.assertIn("否认", found[0])

    def test_unrelated_question_not_flagged(self):
        self.assertEqual([], guard.denial_of_self_issue(
            "晚饭吃了什么", "我吃了面，你呢", ["刚刷到一个视频"]))


class EmotionFirstTests(unittest.TestCase):
    def test_emotion_without_empathy_flagged(self):
        found = guard.emotion_first_issue("我今天好累啊", "我今天跑了三公里。")
        self.assertTrue(found)

    def test_emotion_with_empathy_passes(self):
        self.assertEqual([], guard.emotion_first_issue("我今天好累啊", "抱抱，早点休息。"))

    def test_denying_own_emotion_flagged(self):
        found = guard.emotion_first_issue("别生气嘛，电视剧而已", "我哪生气了，别给我扣帽子。")
        self.assertTrue(any("否认" in item for item in found))

    def test_plain_statement_ignored(self):
        self.assertEqual([], guard.emotion_first_issue("今天上班。", "我也刚下班。"))


class PromptSingleSourceTests(unittest.TestCase):
    """人设单一真源：PromptRegistry 里不能再有「允许括号动作」的后置规则。"""

    def test_rule_core_forbids_action_narration(self):
        import amo_prompt_registry as registry

        text = registry.render("llm.rule.core")
        self.assertIn("不写动作旁白", text)
        self.assertIn("不写括号动作", text)

    def test_no_static_block_allows_bracket_action(self):
        import amo_prompt_registry as registry

        offenders = []
        for block in registry.REGISTRY.blocks():
            for line in str(block.text or "").splitlines():
                if "允许" in line and ("括号动作" in line or "括号写" in line):
                    offenders.append(block.id)
        self.assertEqual([], offenders)

    def test_self_recent_block_renders_hard_list(self):
        import amo_prompt_registry as registry

        rendered = registry.render(
            "llm.self_recent", self_texts=["刚刷到一个视频太离谱了"])
        self.assertIn("不可否认", rendered)
        self.assertIn("刚刷到一个视频太离谱了", rendered)
        self.assertEqual("", registry.render("llm.self_recent", self_texts=[]))

    def test_no_backend_block_covers_ops_words(self):
        import amo_prompt_registry as registry

        text = registry.render("llm.no_backend")
        for word in ("服务器", "面板", "报错", "超时", "权限", "质检"):
            self.assertIn(word, text)


class OutboundPrepareTests(unittest.TestCase):
    class FakePlugin:
        def __init__(self):
            self.db = self

        def _recent(self, umo, limit=8):
            return []

        def recent_messages(self, umo, limit=8):
            return []

    def setUp(self):
        self.plugin = self.FakePlugin()

    def prepare(self, text, source="chat"):
        return guard.prepare_outbound(self.plugin, "umo", text, source)

    def test_action_only_message_not_sent(self):
        body, reason = self.prepare("（靠在椅背上，把手机举得离眼睛近了点）")
        self.assertEqual("", body)
        self.assertEqual("action_only", reason)

    def test_inline_action_stripped_but_sent(self):
        body, reason = self.prepare("（揉了揉眼睛）你回来啦")
        self.assertEqual("你回来啦", body)
        self.assertEqual("", reason)

    def test_system_phrase_dropped_for_push(self):
        body, reason = self.prepare("【日记质检】详情在面板", "push")
        self.assertEqual("", body)
        self.assertEqual("system_phrase", reason)

    def test_system_phrase_humanized_for_chat(self):
        body, reason = self.prepare("服务器那边的锅，我这边报错三次了", "chat")
        self.assertEqual("", reason)
        self.assertEqual("刚才走神了一下，你再说一遍？", body)

    def test_exact_numbers_approximated(self):
        body, reason = self.prepare("就是985，我卡里还有4072，等了十五分钟", "chat")
        self.assertEqual("", reason)
        for exact in ("985", "4072", "十五分钟"):
            self.assertNotIn(exact, body)

    def test_duplicate_prefix_collapsed(self):
        body, _reason = self.prepare("今天买了米白色米白色针织开衫", "chat")
        self.assertNotIn("米白色米白色", body)

    def test_forbidden_neutralized(self):
        body, reason = self.prepare("老公你美丽的老婆晾这儿半天了。", "chat")
        self.assertEqual("", reason)
        self.assertNotIn("你美丽的老婆", body)

    def test_clean_message_untouched(self):
        body, reason = self.prepare("嗯，我在呢，你慢慢说。", "chat")
        self.assertEqual("嗯，我在呢，你慢慢说。", body)
        self.assertEqual("", reason)


class GroundingTests(unittest.TestCase):
    class FakePlugin:
        def __init__(self, recent):
            self.recent = recent
            self.db = self

        def _recent(self, umo, limit=8):
            return self.recent

        def recent_messages(self, umo, limit=8):
            return self.recent

    def test_name_not_in_material_replaced(self):
        plugin = self.FakePlugin(
            [{"role": "user", "content": "今天公司食堂的饭好咸"}])
        out = guard.ground_names(plugin, "umo", "柳如烟刚讲了个冷笑话")
        self.assertNotIn("柳如烟", out)

    def test_name_in_material_kept(self):
        plugin = self.FakePlugin(
            [{"role": "user", "content": "我刚跟柳如烟逛完商场"}])
        out = guard.ground_names(plugin, "umo", "柳如烟刚讲了个冷笑话")
        self.assertIn("柳如烟", out)


if __name__ == "__main__":
    unittest.main()
