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
from unittest import mock

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

    def test_has_orphan_paren_invariant(self):
        self.assertTrue(guard.has_orphan_paren("嘴角一下翘起来）我也想你了"))
        self.assertTrue(guard.has_orphan_paren("（听到这句话"))
        self.assertFalse(guard.has_orphan_paren("（听到这句话，嘴角一下翘起来）我也想你了"))
        self.assertFalse(guard.has_orphan_paren("（11月22日）我在家"))
        self.assertFalse(guard.has_orphan_paren("你回来啦"))

    def test_orphan_close_parenthesis_dropped(self):
        # 分段器把「（听到这句话，\n嘴角一下翘起来）」劈开后，第二段只剩闭括号；
        # 残片不许原样发出去（线上事故：飞书收到「嘴角一下翘起来）我也想你了」）。
        self.assertEqual("我也想你了", guard.strip_action_narration("嘴角一下翘起来）我也想你了"))
        self.assertEqual("", guard.strip_action_narration("（听到这句话"))

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
    """3c：整句重写 → 残片丢句 → 中性兜底（非空输入永不返回空）。"""

    #: 施工图 §6.2 对抗句：整串相等 + 不变量一起钉，不许退回「含某个字」。
    REWRITTEN = (
        ("你们男人就是不懂。", "你就是不懂。"),
        ("你老婆饿了，快回来。", "我饿了，快回来。"),
        ("你美丽的老婆晾这儿半天了，理一下嘛。", "我晾这儿半天了，理一下嘛。"),
        ("你老婆我饿了。", "我饿了。"),
        ("你别惹你美丽的老婆生气。", "你别惹我生气。"),
        ("你们男人都这样，你妈也这么说。", "你都这样，你妈也这么说。"),
        ("这有啥好笑的，你们男人笑点真低。 中午我吃的面，你吃了吗？",
         "中午我吃的面，你吃了吗？"),
        ("今天下班早，你美丽的老婆在门口等你呢。老公快回来。",
         "今天下班早，我在门口等你呢。老公快回来。"),
        ("你美丽的老婆做的饭你不吃？", "我做的饭你不吃？"),
        ("他刚才还念叨你美丽的老婆呢。", "他刚才还念叨我呢。"),
    )
    #: 重写不出来就只能丢句、走中性句池的输入。
    ALL_DROPPED = (
        "我想你美丽的老婆了。",          # 宾语位 + 前面已有「我」：换了是「我想我」
        "我哪生气了，别给我扣帽子。",      # 两条整句丢弃
        "我最受不了你们男人。",            # 群体称呼在宾语位
    )
    #: 残片对抗：只剩代词/定语、称呼后接话、命中断在半句。
    FRAGMENT_RISK = (
        "你美丽的老婆", "你老婆我", "你美丽的老婆，早点睡。",
        "你老婆的包呢？", "你美丽的老婆晾这儿半天了。",
    )

    @staticmethod
    def short_pool():
        from domains.reply import repetition_guard
        return {repetition_guard.compare_body(item)
                for item in repetition_guard.FALLBACKS["short"]}

    def assertCleanAndIdempotent(self, out):
        self.assertTrue(out.strip(), "非空输入不许返回空串")
        self.assertFalse(guard._looks_fragment(out), out)
        self.assertNotIn("我我", out)
        self.assertNotIn("我想我", out)
        for word in guard._THIRD_REPLACE:
            self.assertNotIn(word, out)
        self.assertEqual(out, guard.neutralize_forbidden(out))

    def test_adversarial_rewritten_whole(self):
        for text, expected in self.REWRITTEN:
            with self.subTest(text=text):
                out = guard.neutralize_forbidden(text)
                self.assertEqual(expected, out)
                self.assertCleanAndIdempotent(out)

    def test_all_dropped_falls_back_to_registry_short_pool(self):
        from domains.reply import repetition_guard
        pool = self.short_pool()
        for text in self.ALL_DROPPED:
            with self.subTest(text=text):
                out = guard.neutralize_forbidden(text)
                self.assertCleanAndIdempotent(out)
                self.assertIn(repetition_guard.compare_body(out), pool)

    def test_neutral_fallback_rotates_not_repeats(self):
        first = guard.neutralize_forbidden("我哪生气了，别给我扣帽子。")
        second = guard.neutralize_forbidden("我哪生气了，别给我扣帽子。")
        self.assertNotEqual(first, second)

    def test_rewrite_never_leaves_fragment(self):
        for text in self.FRAGMENT_RISK:
            with self.subTest(text=text):
                self.assertCleanAndIdempotent(guard.neutralize_forbidden(text))

    def test_drop_beats_replace(self):
        out = guard.neutralize_forbidden("这有啥好笑的，你们男人笑点真低。")
        from domains.reply import repetition_guard
        self.assertIn(repetition_guard.compare_body(out), self.short_pool())
        self.assertEqual("", guard.rewrite_forbidden(
            "这有啥好笑的，你们男人笑点真低。")[0])

    def test_empty_input_stays_empty(self):
        # prepare_outbound 的 "empty" 分支依赖这一点：空白不算禁语
        for text in ("", "   ", None):
            self.assertEqual("", guard.neutralize_forbidden(text))

    def test_third_person_self_becomes_first_person(self):
        out = guard.neutralize_forbidden("老公你美丽的老婆晾这儿半天了。")
        self.assertEqual("老公我晾这儿半天了。", out)
        self.assertNotIn("你美丽的老婆", out)
        self.assertIn("我", out)
        self.assertNotIn("我我", out)
        self.assertFalse(guard._looks_fragment(out))
        self.assertEqual("。", out[-1])

    def test_you_men_replaced(self):
        out = guard.neutralize_forbidden("你们男人笑点真低。")
        self.assertEqual("你笑点真低。", out)
        self.assertNotIn("你们", out)
        self.assertIn("你", out)
        self.assertEqual("你啊，永远看不懂。",
                         guard.neutralize_forbidden("你们男人啊，永远看不懂。"))

    def test_empty_phrase_sentence_dropped(self):
        out = guard.neutralize_forbidden("收到你后来说的那句了。图卡住了。")
        self.assertEqual("图卡住了。", out)
        self.assertNotIn("收到你后来", out)

    def test_newline_between_sentences_kept(self):
        self.assertEqual("我在呢。\n图卡住了。",
                         guard.neutralize_forbidden("你美丽的老婆在呢。\n图卡住了。"))

    def test_stats_and_reset(self):
        guard.reset_forbidden_stats()
        guard.neutralize_forbidden("你美丽的老婆饿了。")
        self.assertEqual(1, guard.forbidden_stats()["rewrite"])
        guard.neutralize_forbidden("我哪生气了，别给我扣帽子。")
        stats = guard.forbidden_stats()
        self.assertEqual(1, stats["drop_sentence"])
        self.assertEqual(1, stats["fallback"])
        guard.reset_forbidden_stats()
        self.assertEqual({}, guard.forbidden_stats())

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
        self.assertEqual("老公我晾这儿半天了。", body)
        self.assertNotIn("你美丽的老婆", body)

    def test_forbidden_all_dropped_still_sent(self):
        # 3c：全句禁语不再丢消息，出站给中性句（reason 必须为空）
        from domains.reply import repetition_guard
        body, reason = self.prepare("我哪生气了，别给我扣帽子。", "chat")
        self.assertEqual("", reason)
        self.assertTrue(body)
        self.assertIn(repetition_guard.compare_body(body),
                      {repetition_guard.compare_body(item)
                       for item in repetition_guard.FALLBACKS["short"]})

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


class MergedChainTests(unittest.TestCase):
    """3a-3：一个 runner + 三张 profile 表 + 三个薄壳的冻结契约。"""

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

    def sanitize(self, text, user_text=""):
        return guard.sanitize_candidate(self.plugin, "umo", text, user_text)

    def test_chain_tables_are_frozen(self):
        self.assertEqual((
            "media", "collapse", "narration", "system", "forbidden",
            "amounts", "names", "collapse_tail", "addressee",
        ), guard._CHAIN["outbound"])
        self.assertEqual((
            "media", "narration", "tech", "system", "forbidden",
            "amounts", "names", "anti_nag", "purchase", "addressee",
        ), guard._CHAIN["candidate"])
        self.assertEqual(("fact_probe",), guard._CHAIN["probe"])
        self.assertEqual(13, len(guard._STAGES))
        for name in guard._CHAIN["outbound"] + guard._CHAIN["candidate"]:
            self.assertIn(name, guard._STAGES)

    def test_media_only_reason_differs_by_profile(self):
        # 出站剥空即 empty；候选留给旁白阶段按 action_only 归因（不得统一）
        self.assertEqual(("", "empty"), self.prepare("[我发的照片]"))
        self.assertEqual(("", "action_only"), self.sanitize("[我发的照片]"))
        self.assertEqual(("", "empty"), self.prepare("  [我发的语音]  "))

    def test_empty_and_whitespace_are_empty_for_both(self):
        for text in ("", "   ", None):
            self.assertEqual(("", "empty"), self.prepare(text))
            self.assertEqual(("", "empty"), self.sanitize(text))
        self.assertEqual("", guard.guard_candidate(self.plugin, "umo", "   "))

    def test_system_phrase_gate_differs_by_profile(self):
        push = self.prepare("【日记质检】详情在面板", "push")
        self.assertEqual(("", "system_phrase"), push)
        self.assertEqual("system_phrase", self.prepare("【日记质检】详情在面板", None)[1])
        # candidate 的系统词只 humanize，永不因此丢弃
        body, reason = self.sanitize("【日记质检】详情在面板")
        self.assertEqual("", reason)
        self.assertTrue(body)

    def test_public_return_shapes_are_frozen(self):
        prep = self.prepare("嗯，我在呢。")
        self.assertIs(tuple, type(prep))
        self.assertEqual(2, len(prep))
        self.assertTrue(all(type(item) is str for item in prep))
        san = self.sanitize("嗯，我在呢。")
        self.assertIs(tuple, type(san))
        self.assertEqual(2, len(san))
        self.assertIs(str, type(guard.guard_candidate(self.plugin, "umo", "嗯，我在呢。")))

    def test_guard_is_fact_probe_only(self):
        import amo_reply_pipeline
        seen = []

        def fake_fact_guard(text, plugin):
            seen.append(text)
            return "【FG】" + text

        raw = "[我发的照片]（歪头）你美丽的老婆在呢"
        with mock.patch.object(amo_reply_pipeline, "apply_fact_guard", fake_fact_guard):
            out = guard.guard_candidate(self.plugin, "umo", raw)
        # 事实守卫拿到的是 trim 后的原文：媒体占位符/旁白/禁语一个都没被清洗
        self.assertEqual([raw], seen)
        self.assertEqual("【FG】" + raw, out)
        self.assertEqual(("fact_probe",), guard._CHAIN["probe"])

    def test_only_outbound_can_return_empty_after_guard(self):
        import amo_memory_ledger
        calls = []

        def second_call_empties(body):
            calls.append(body)
            return body if len(calls) == 1 else ""

        with mock.patch.object(amo_memory_ledger, "collapse_repeats", second_call_empties):
            self.assertEqual(("", "empty_after_guard"), self.prepare("嗯，我在呢，你慢慢说。"))
            body, reason = self.sanitize("嗯，我在呢，你慢慢说。")
        self.assertEqual("", reason)
        self.assertTrue(body)

    def test_only_candidate_can_return_forbidden_phrase(self):
        # 出站：禁语全丢也发中性句（reason 为空）；候选：仍返回空，交给 recover 保持现行
        text = "我哪生气了，别给我扣帽子。"
        body, reason = self.prepare(text)
        self.assertEqual("", reason)
        self.assertTrue(body)
        self.assertEqual(("", "forbidden_phrase"), self.sanitize(text))
        body, reason = self.sanitize("你美丽的老婆晾这儿半天了，理一下嘛。")
        self.assertEqual(("我晾这儿半天了，理一下嘛。", ""), (body, reason))

    def test_only_candidate_can_return_tech_terms(self):
        import amo_shared_text
        with mock.patch.object(amo_shared_text, "strip_tech_terms",
                               lambda text, source="chat": ""):
            self.assertEqual(("", "tech_terms"), self.sanitize("嗯，我在呢，你慢慢说。"))
            body, reason = self.prepare("嗯，我在呢，你慢慢说。")
        self.assertEqual("", reason)
        self.assertTrue(body)

    def test_prepare_is_idempotent(self):
        for text in ("（揉了揉眼睛）你回来啦", "老公你美丽的老婆晾这儿半天了。",
                     "就是985，我卡里还有4072，等了十五分钟", "（歪头）"):
            first, reason = self.prepare(text)
            again, again_reason = self.prepare(first)
            if first:
                self.assertEqual("", reason)
                self.assertEqual(first, again)
                self.assertEqual("", again_reason)
            else:
                self.assertEqual(("", "empty"), (again, again_reason))


if __name__ == "__main__":
    unittest.main()
