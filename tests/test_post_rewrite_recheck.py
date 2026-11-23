# -*- coding: utf-8 -*-
"""task-29：破坏性改写后的唯一后置语义复检（契约测试）。

锁三件事：
1) 改写顺序：所有会整句删除/替换的守卫跑完（_align_arrival/_align_swim_where →
   state_consistency.rewrite → question_guard.answer → validate_reply_assembly）
   之后，必须过同一道 final_semantic_recheck；
2) 候选恢复：原候选答得上他这一句、改写后答不到（或只剩通用兜底句）→ 保留候选；
3) 不覆盖：候选没答到、非问句轮、模型泄漏/假消费等安全优先场景，不还原候选。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_grounding_critic as GC  # noqa: E402
import amo_question_guard as Q  # noqa: E402
import amo_reply_pipeline as P  # noqa: E402
import amo_state_consistency as SC  # noqa: E402

UMO = "lark:FriendMessage:recheck"


def _plugin(place="家", zone="home", rows=None):
    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute(
                "create table wallet_ledger (ts real, delta_cents int, item_name text)")
            self.conn.commit()

    p = SimpleNamespace()
    p.db = _DB()
    p.state = SimpleNamespace(current_activity=lambda: {"place": place, "zone": zone})
    p._recent = lambda umo, n=8: list(rows or [])[-int(n):]
    return p


def _answers(reply, question):
    """按审计口径：严格答案判据或 grounding critic 任一认为答到了。"""
    if not str(reply or "").strip():
        return False
    return bool(P._answered_intent(reply, question) or not GC.issues(question, reply))


def _source(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def _body(source, start, end):
    """取 start 之后、end 之前的源码段（start/end 都是源码里的唯一锚）。"""
    return source[source.index(start):source.index(end)]


#: (名称, 原候选, 他这句, real_llm_layer 里真实发出的错文本)
REAL_LLM_HARMS = (
    ("G13 when", "不回啦，我就在家歇着呢，刚到家没多久，今天真不想动了",
     "今晚回不回来？", "嗯，我在呢。"),
    ("G23 recall", "记得呀，那天你穿白衬衫，在奶茶店门口等我，我还迟到了",
     "你还记得那天我穿什么去接你的吗？", "我还在路上呢，快到了。"),
    ("G43 when", "17:27啦，我刚到家歇着呢，今晚不回了，明早还要早起",
     "今晚回不回来？", "我这儿一直在。"),
    ("G09 where", "没呢，刚下班走在回家的路上，今天累死了",
     "到公司了吗？", "还是老样子。"),
    ("G29 when", "刚练完器械，还在健身房呢，估计得七点多才能到家吧，今天练得有点久。",
     "几点到家呀？", "我还在外面呢，晚点就回去。"),
    ("G13-2 eating", "中午随便吃了碗云吞面，现在正走在回家路上，饿得不行了",
     "你中午吃的什么？", "还是老样子。"),
)


class RecheckCoreTests(unittest.TestCase):
    """纯判定核心：amo_grounding_critic.post_rewrite_recheck。"""

    def test_rewrite_loses_answer_restores_candidate(self):
        cand = "记得呀，那天你穿白衬衫，在奶茶店门口等我，我还迟到了"
        out = GC.post_rewrite_recheck(cand, "我在家呢。", "你还记得那天我穿什么去接你的吗？")
        self.assertEqual(out, cand)

    def test_unanswered_candidate_keeps_rewrite(self):
        # 生产调用方总会传严格判据；短问句（≤5 字）critic 判不了答案，以严格判据为准。
        out = GC.post_rewrite_recheck(
            "今天天气不错。", "嗯，我在呢。", "你在哪？",
            answered=lambda body: P._answered_intent(body, "你在哪？"))
        self.assertEqual(out, "嗯，我在呢。")

    def test_non_question_turn_is_not_rechecked(self):
        # 普通陈述/情绪轮没有「答案」可保：去重/场景改写不许被翻回去。
        out = GC.post_rewrite_recheck("嗯嗯，我在家呢，想你", "我也想你呀。", "想你")
        self.assertEqual(out, "我也想你呀。")

    def test_short_question_uses_strict_answer_predicate(self):
        cand = "刚吃完饭，好撑。"
        out = GC.post_rewrite_recheck(
            cand, "嗯，我在呢。", "吃了吗",
            answered=lambda body: P._answered_intent(body, "吃了吗"))
        self.assertEqual(out, cand)

    def test_generic_rewrite_does_not_cover_candidate(self):
        for fallback in ("还是老样子。", "嗯，我在呢。", "我这儿一直在。"):
            with self.subTest(fallback=fallback):
                out = GC.post_rewrite_recheck(
                    "刚练完器械，七点多才能到家。", fallback, "几点到家呀？")
                self.assertEqual(out, "刚练完器械，七点多才能到家。")

    def test_fallback_body_set_covers_generic_and_short_but_not_scene_or_leak(self):
        self.assertTrue(GC.is_fallback("还是老样子。"))
        self.assertTrue(GC.is_fallback("嗯，我在呢。"))        # SHORT_FALLBACKS
        self.assertFalse(GC.is_fallback("我在家呢。"))          # scene_generic：事实状态句
        self.assertFalse(GC.is_fallback("秘密，不告诉你。"))    # leak：安全挡话
        self.assertTrue(GC.is_blocking_fallback("秘密，不告诉你。"))


class GuardOrderContractTests(unittest.TestCase):
    """改写顺序契约：复检必须排在所有整句删除/替换之后。"""

    def test_chat_safety_recheck_is_last(self):
        source = _source("amo_reply_pipeline.py")
        body = _body(source, "def apply_chat_safety(", "def apply_narrative_repetition(")
        self.assertIn("final_semantic_recheck(value, plugin, umo, user_text)", body)
        # 1b-1：三层候选优先已收敛；旧的裸还原层不得再出现（否则会和 recover 打架）。
        self.assertNotIn("_prefer_answered_candidate", body)
        for earlier in ("value = fix_identity(sanitize_extreme(value))",
                        "_purchase_guard(value, plugin)",
                        "_state_rewrite(value, plugin, _context",
                        "_question_answer("):
            with self.subTest(earlier=earlier):
                self.assertIn(earlier, body)
                self.assertLess(body.index(earlier), body.index("final_semantic_recheck("))

    def test_narrative_dedupe_ends_with_recheck(self):
        source = _source("amo_reply_pipeline.py")
        body = _body(source, "def apply_narrative_repetition(", "def apply_proactive_dedup(")
        # B1：删空即空串（不许再用「fixed or value」把被删的复读捞回来）。
        self.assertIn("final_semantic_recheck(fixed, plugin, umo, user_text",
                      body)
        self.assertNotIn("fixed or value", body)
        self.assertNotIn('_fallbacks.sentence("status_repeat")', body)

    def test_state_rewrite_and_validate_end_with_recheck(self):
        source = _source("amo_state_consistency.py")
        rewrite_body = _body(source, "def rewrite(text, plugin", "def current_scene(")
        self.assertIn("_post_recheck(body, plugin, umo, _last_user_line(user))",
                      rewrite_body)
        # M1：validate 收敛成 _CHAINS["assembly"] 薄壳；顺序由阶段表锁，复检仍是 judge。
        self.assertEqual(SC._CHAINS["assembly"], (
            "media", "clause_dedupe", "scene", "empty_scene", "addressee",
            "judge", "complete_answer", "recover", "ensure_where"))
        validate_body = _body(source, "def validate_reply_assembly(", "def _turn_registered(")
        self.assertIn('_run_assembly([body], ctx, "assembly")', validate_body)
        judge_body = _body(source, "def _stage_judge(", "def _stage_complete_answer(")
        self.assertIn("_post_recheck(ctx.text, ctx.plugin, ctx.umo, ctx.user_text)",
                      judge_body)
        self.assertIn("def _post_recheck(", source)

    def test_align_guards_run_before_the_gate(self):
        source = _source("amo_runtime_reply.py")
        # 装饰链：_deco_soft_guards（含整句去重）→ _deco_text_cleanup（含 _align_*）
        # → apply_chat_safety（含唯一后置复检）。
        self.assertLess(source.index("text = self._deco_soft_guards(event, text)"),
                        source.index("text = await self._deco_text_cleanup(event, text)"))
        self.assertLess(source.index("text = await self._deco_text_cleanup(event, text)"),
                        source.index("text = apply_chat_safety("))
        cleanup = _body(source, "async def _deco_text_cleanup(",
                        "async def _maybe_send_voice_reply(")
        self.assertLess(cleanup.index("text = self._align_arrival(text)"),
                        cleanup.index("text = self._align_swim_where(text, event.message_str or \"\")"))


class CandidateRecoveryTests(unittest.TestCase):
    """真实链：候选答到、被守卫换成通用句/删坏后必须还原。"""

    def setUp(self):
        P.reset_repetition_memory()

    def _record(self, candidate, question, plugin=None):
        P.apply_narrative_repetition(candidate, plugin or _plugin(), question, UMO)

    def test_real_llm_known_harms_are_restored(self):
        for name, cand, q, sent in REAL_LLM_HARMS:
            with self.subTest(name=name):
                P.reset_repetition_memory()
                self._record(cand, q)
                out = P.apply_chat_safety(sent, _plugin(), UMO, q)
                self.assertEqual(out, cand)

    def test_real_chain_always_answers_question(self):
        cases = REAL_LLM_HARMS + (
            ("G36 when", "在地铁上呢，晚点回去", "今晚回不回来？", "还是老样子。"),
            ("G47 doing", "刚洗完澡躺床上刷手机呢，酒店空调开得挺舒服的",
             "忙什么呢", "我在在酒店休息。"),
        )
        for name, cand, q, _sent in cases:
            with self.subTest(name=name):
                P.reset_repetition_memory()
                self._record(cand, q)
                out = P.apply_chat_safety(cand, _plugin(), UMO, q)
                self.assertTrue(_answers(out, q), (name, out))

    def test_align_arrival_output_restores_recall_candidate(self):
        # 审计 3.2.1 G23/t7：候选「记得呀，那天…」被 _align_arrival 整句换成
        # 「我还在路上呢，快到了」——复检必须还原候选。
        cand = "记得呀，那天你穿白衬衫，在奶茶店门口等我，我还迟到了"
        q = "你还记得那天我穿什么去接你的吗？"
        self._record(cand, q)
        out = P.apply_chat_safety("我还在路上呢，快到了。", _plugin(), UMO, q)
        self.assertEqual(out, cand)

    def test_narrative_dedupe_keeps_answered_candidate(self):
        # 整句去重会先压成短句兜底；候选答到了他这句（问句守卫词表认不出），
        # 出口复检必须把候选要回来。
        cand = "记得呀，那天你穿白衬衫，在奶茶店门口等我，我还迟到了"
        q = "你还记得那天我穿什么去接你的吗？"
        plugin = _plugin(rows=[{"role": "bot", "content": cand}])
        out = P.apply_narrative_repetition(cand, plugin, q, UMO)
        self.assertEqual(out, cand)

    def test_state_rewrite_keeps_answered_candidate(self):
        self._record("刚吃完，在沙发上瘫着呢", "吃了吗？")
        out = SC.rewrite("还是老样子。", _plugin(), "吃了吗？", umo=UMO)
        self.assertEqual(out, "刚吃完，在沙发上瘫着呢")

    def test_validate_reply_assembly_keeps_answered_candidate(self):
        self._record("刚吃完，在沙发上瘫着呢", "吃了吗？")
        out = SC.validate_reply_assembly(
            "还是老样子。", _plugin(), umo=UMO, user_text="吃了吗？")
        self.assertEqual(out, "刚吃完，在沙发上瘫着呢")

    def test_where_answer_added_after_segment_dedupe(self):
        # 真实模型层 G33/t8：「在呢，刚到家歇会儿，今天上班累死我了」被整句去重删成
        # 「在呢，今天上班累死我了」——地点答案没了，必须用 scene_at 当前 place 补回。
        plugin = _plugin("家", "home",
                         rows=[{"role": "bot", "content": "不回啦，刚到家歇会儿，今天有点累。"}])
        P.apply_narrative_repetition(
            "在呢，刚到家歇会儿，今天上班累死我了", plugin, "你在家吗？", UMO)
        segments = SC.dedupe_reply_segments(
            ["在呢，刚到家歇会儿，今天上班累死我了"], plugin=plugin, umo=UMO)
        joined = "".join(segments)
        self.assertNotIn("刚到家", joined)      # 复读句确实被删
        self.assertIn("在呢", joined)
        self.assertIn("家", joined)             # 地点答案补回来了
        self.assertTrue(_answers(joined, "你在家吗？"), joined)

    def test_where_answer_added_after_validate_dedupe(self):
        plugin = _plugin("家", "home",
                         rows=[{"role": "bot", "content": "窝沙发上刷手机呢，刚吃完饭不想动"}])
        P.apply_narrative_repetition(
            "在啊，窝沙发上刷手机呢，今天下班回来就不想动了", plugin, "你在家吗？", UMO)
        out = SC.validate_reply_assembly(
            "在啊，窝沙发上刷手机呢，今天下班回来就不想动了",
            plugin, umo=UMO, user_text="你在家吗？")
        self.assertNotIn("沙发", out)
        self.assertIn("家", out)
        self.assertTrue(_answers(out, "你在家吗？"), out)

    def test_segment_dedupe_where_question_never_returns_empty(self):
        # 整段都被去重删光时，where 问句不能交回空串。
        plugin = _plugin("家", "home",
                         rows=[{"role": "bot", "content": "在呢，刚到家歇会儿，今天有点累。"}])
        P.apply_narrative_repetition(
            "在呢，刚到家歇会儿，今天有点累。", plugin, "你在家吗？", UMO)
        segments = SC.dedupe_reply_segments(
            ["在呢，刚到家歇会儿，今天有点累。"], plugin=plugin, umo=UMO)
        joined = "".join(segments)
        self.assertTrue(joined.strip())
        self.assertTrue(_answers(joined, "你在家吗？"), joined)

    def test_segment_dedupe_non_where_turn_does_not_add_place(self):
        plugin = _plugin("家", "home",
                         rows=[{"role": "bot", "content": "在呢，刚到家歇会儿，今天有点累。"}])
        P.apply_narrative_repetition(
            "在呢，刚到家歇会儿，今天上班累死我了", plugin, "忙什么呢？", UMO)
        segments = SC.dedupe_reply_segments(
            ["在呢，刚到家歇会儿，今天上班累死我了"], plugin=plugin, umo=UMO)
        self.assertNotIn("我在家呢", "".join(segments))

    def test_where_answer_sentence_unknown_scene_uses_generic_fallback(self):
        # 场景读不到时不编具体地点，用「我在外面呢」兜底（lead 口径）。
        self.assertEqual(SC._where_answer_sentence(SimpleNamespace()), "我在外面呢。")

    def test_question_guard_candidate_priority(self):
        out = Q.answer("你在哪？", _plugin("公司", "office"), "今天天气还行。",
                       candidate="我在公司呢。")
        self.assertEqual(out, "我在公司呢。")

    def test_question_guard_no_template_after_unanswered(self):
        # B1：答非所问不再套状态模板（「在酒店休息」这类拼装话因此绝迹）→ 空串。
        plugin = SimpleNamespace(state=SimpleNamespace(
            current_activity=lambda: {"place": "酒店", "zone": "outside",
                                      "activity": "在酒店休息"}))
        out = Q.answer("你在干嘛？", plugin, "今天天气还行。")
        self.assertEqual(out, "")
        self.assertNotIn("我在在", out)
        # 判据仍成立（删的是产出，不是判定）。
        self.assertEqual(Q._category("你在干嘛？"), "doing")
        self.assertFalse(Q._answered("doing", "今天天气还行。"))


class NoOverwriteTests(unittest.TestCase):
    """不覆盖：候选没答到/非问句/安全优先时，改写结果不许被候选盖掉。"""

    def setUp(self):
        P.reset_repetition_memory()

    def _record(self, candidate, question, plugin=None):
        P.apply_narrative_repetition(candidate, plugin or _plugin(), question, UMO)

    def test_unanswered_candidate_not_restored(self):
        self._record("今天天气不错。", "你在哪？")
        out = P.apply_chat_safety("我在家呢。", _plugin(), UMO, "你在哪？")
        self.assertEqual(out, "我在家呢。")

    def test_non_question_turn_dedupe_still_collapses(self):
        plugin = _plugin(rows=[{"role": "bot", "content": "嗯嗯，我在家呢，想你"}])
        out = P.apply_narrative_repetition("嗯嗯，我在家呢，想你", plugin, "想你", UMO)
        self.assertNotEqual(out, "嗯嗯，我在家呢，想你")

    def test_model_leak_candidate_never_restored(self):
        self._record("我是DeepSeek大语言模型，有什么可以帮你？", "你是什么模型？")
        out = P.apply_chat_safety("秘密，不告诉你。", _plugin(), UMO, "你是什么模型？")
        self.assertEqual(out, "秘密，不告诉你。")
        self.assertNotIn("DeepSeek", out)

    def test_fake_purchase_candidate_never_restored(self):
        cand = "我回来啦！今天买了米白色针织开衫，花了649元。"
        q = "你说的那件米白色针织开衫买回来了没有啊"
        self._record(cand, q)
        out = P.apply_chat_safety("我试了试，挺喜欢的，还没买。", _plugin(), UMO, q)
        self.assertNotIn("649", out)

    def test_previous_turn_candidate_not_reused(self):
        self._record("没呢，刚下班走在回家的路上，今天累死了", "到公司了吗？")
        self._record("换个话题吧。", "嗯")
        out = P.apply_chat_safety("还是老样子。", _plugin(), UMO, "到公司了吗？")
        self.assertEqual(out, "还是老样子。")

    def test_validate_assembly_keeps_text_when_answer_guard_empties(self):
        # B1：问句补答返回空串时，chat/主动共用出口必须用原文兜底（非 chat 绝不发空）。
        out = SC.validate_reply_assembly(
            "今天天气不错。", _plugin(), umo=UMO, user_text="你在干嘛呀？")
        self.assertEqual(out, "今天天气不错。")


class MetricsTests(unittest.TestCase):
    """埋点：restored/blocked/calls，验收口径「改写后语义复检失败 = 0」。"""

    def setUp(self):
        P.reset_repetition_memory()

    def test_stats_reset_and_count_restore(self):
        self.assertEqual(P.semantic_recheck_stats(),
                         {"calls": 0, "restored": 0, "blocked": 0})
        # G23：候选答到（靠 grounding 判据，_prefer 的严格判据认不出），
        # 被换成 align 兜底句后由出口复检还原 —— restored 埋点必须动。
        cand = "记得呀，那天你穿白衬衫，在奶茶店门口等我，我还迟到了"
        q = "你还记得那天我穿什么去接你的吗？"
        P.apply_narrative_repetition(cand, _plugin(), q, UMO)
        out = P.apply_chat_safety("我还在路上呢，快到了。", _plugin(), UMO, q)
        self.assertEqual(out, cand)
        stats = P.semantic_recheck_stats()
        self.assertGreaterEqual(stats["restored"], 1)
        self.assertGreaterEqual(stats["calls"], 1)
        P.reset_repetition_memory()
        self.assertEqual(P.semantic_recheck_stats()["restored"], 0)

    def test_non_question_turn_does_not_touch_stats(self):
        P.apply_narrative_repetition("嗯嗯，我在家呢，想你", _plugin(), "想你", UMO)
        P.apply_chat_safety("我也想你呀。", _plugin(), UMO, "想你")
        self.assertEqual(P.semantic_recheck_stats(),
                         {"calls": 0, "restored": 0, "blocked": 0})

    def test_recheck_never_returns_empty(self):
        # 守护契约：复检只会「保持改写结果」或「还原候选」，绝不返回空串。
        for _name, cand, q, sent in REAL_LLM_HARMS:
            with self.subTest(q=q):
                P.reset_repetition_memory()
                P.apply_narrative_repetition(cand, _plugin(), q, UMO)
                self.assertTrue(P.apply_chat_safety(sent, _plugin(), UMO, q).strip())


if __name__ == "__main__":
    unittest.main()
