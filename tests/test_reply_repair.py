# -*- coding: utf-8 -*-
"""3C：统一「生成式修复」hook（守卫把回复变成空/罐头句时，调一次模型重生成）。

锁六件事：
1) 默认开启（REPAIR_ENABLED=True），但开关关 = 零模型调用、原样返回（行为零变化）；
2) 罐头识别覆盖面：空/纯空白、固定兜底句、safe_reply 池、问句模板、动作台词、
   short_reply 旧句；正常句**不命中**（不为正常消息付一次模型调用）；
3) 模型成功 → 采用新句（ok+1），提示词带上他的原话/上一版/最近上下文；
4) 模型异常/超时/仍罐头/仍禁语/空 → fail-open 到「guarded or raw」，绝不吞回复；
5) 非 chat 来源（主动/推送）不修；
6) 一次出站最多一次模型调用：命中修复时 runtime 跳过 regenerate_forbidden。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_reply_pipeline as pipeline  # noqa: E402
import amo_state_consistency as SC  # noqa: E402
from amo_rewrite_safety import remember_candidate  # noqa: E402
from amo_shared import Comp  # noqa: E402
from amo_runtime_reply import RuntimeReplyMixin  # noqa: E402

UMO = "aiocqhttp:FriendMessage:repair"
CANNED = "嗯，我在呢。"
NORMAL = "我饿了，快回来。"
FRESH = "我等你回来，路上慢点。"
#: 既是罐头句、又含禁语：修复与禁语重生成都会命中（用来验「最多一次调用」）。
BOTH = "我在呢，先别急，慢慢跟我说。你美丽的老婆在这儿。"


class _SpyLLM:
    """只有 chat_raw 的假 LLM：记录每次调用，可按脚本返回 / 抛错 / 慢返回。"""

    def __init__(self, reply="", error=None, delay=0.0):
        self.reply = reply
        self.error = error
        self.delay = delay
        self.calls = []

    async def chat_raw(self, umo, system, prompt):
        self.calls.append({"umo": umo, "system": system, "prompt": prompt})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.reply


def _plugin(llm):
    return types.SimpleNamespace(llm=llm)


def _rich_plugin(llm):
    """带账本/场景的最小插件：purchase/fact 守卫探针要用。"""

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute(
                "create table wallet_ledger (ts real, delta_cents int, item_name text)")
            self.conn.commit()

    plugin = types.SimpleNamespace(llm=llm)
    plugin.db = _DB()
    plugin.state = types.SimpleNamespace(
        current_activity=lambda: {"place": "家", "zone": "home"})
    plugin._recent = lambda umo, n=8: []
    return plugin


def _repair(raw, guarded, llm, **kwargs):
    return asyncio.run(
        pipeline.repair_reply(raw, guarded, _plugin(llm), UMO, **kwargs))


class _FakeResult:
    def __init__(self, text):
        self.chain = [Comp.Plain(text)]

    def is_llm_result(self):
        return True


class _FakeEvent:
    def __init__(self, message_str, reply):
        self.unified_msg_origin = UMO
        self.message_str = message_str
        self.message_obj = types.SimpleNamespace(message_id="m1")
        self._result = _FakeResult(reply)

    def get_result(self):
        return self._result

    def is_private_chat(self):
        return True


async def _async_noop(*args, **kwargs):
    return None


async def _passthrough_three(event, result, text):
    return text


async def _passthrough_two(event, text):
    return text


async def _async_false(*args, **kwargs):
    return False


class _RuntimePlugin(RuntimeReplyMixin):
    """最小运行时插件：装饰链只留 apply_chat_safety（打桩）→ 修复 hook → 发送记录。"""

    def __init__(self, llm):
        self.llm = llm
        self.heartflow = None
        self.gift = None
        self.draw = None
        self.segment_engine = None
        self._last_reply_at = {}
        self.sent = []
        self._ensure_ready = _async_noop
        self._deco_soft_guards = lambda event, text: text
        self._deco_rewrite_reply = lambda event, result, text: text
        self._deco_polish_native = _passthrough_three
        self._deco_text_cleanup = _passthrough_two
        self._maybe_send_voice_reply = _async_false
        self._remember_sent_reply = self._record_sent
        self._recent = lambda umo, n=6: []
        self._quote_allowed = lambda umo, group: False
        self._core_decor_flags = lambda umo: {}
        self.cfg_bool = lambda key, default=False: default
        self.cfg_int = lambda key, default=0: default
        self.cfg_str = lambda key, default="": default

    def _record_sent(self, event, text):
        self.sent.append(text)


def _decorate(raw, guarded, llm, message_str="在吗"):
    """跑真实 on_decorating_result，把确定性守卫固定成 guarded。"""
    plugin = _RuntimePlugin(llm)
    event = _FakeEvent(message_str, raw)
    with mock.patch.object(pipeline, "apply_chat_safety",
                           lambda value, *args, **kwargs: guarded):
        asyncio.run(plugin.on_decorating_result(event))
    return plugin


class CannedDetectionTests(unittest.TestCase):
    def setUp(self):
        pipeline._CANNED_BODIES = None

    def test_empty_or_blank_is_canned(self):
        for text in ("", "   ", "\n\t", None):
            self.assertTrue(pipeline.is_canned_reply(text), repr(text))

    def test_fixed_rewrite_pool_is_canned(self):
        for text in ("还是老样子。", CANNED, "我在家呢。", "一个人呢。"):
            self.assertTrue(pipeline.is_canned_reply(text), text)

    def test_safe_reply_pool_is_canned(self):
        # 来自 amo_persona_guard.safe_reply / amo_grounding_critic.safe_reply
        for text in ("我在呢，先别急，慢慢跟我说。",
                     "我记混了，你再说一遍？",
                     "换谁都得气，我在听着。",
                     "我在听，你接着说。"):
            self.assertTrue(pipeline.is_canned_reply(text), text)

    def test_question_templates_are_canned(self):
        for text in ("吃过了，你呢？", "还没呢，你呢？", "我晚点回去。",
                     "我在忙点事。", "和朋友在一起呢。"):
            self.assertTrue(pipeline.is_canned_reply(text), text)

    def test_action_only_fallback_is_canned(self):
        # 动作旁白 + 固定台词：判「说出口的部分」
        self.assertTrue(pipeline.is_canned_reply("（耳朵有点热）\n……你少来。"))

    def test_short_reply_old_lines_are_canned(self):
        for text in ("好呀~", "哇！谢谢老公！爱你！", "没问题啦！",
                     "你敷衍我是吧？那别聊了。"):
            self.assertTrue(pipeline.is_canned_reply(text), text)

    def test_normal_replies_are_not_canned(self):
        for text in (NORMAL, "刚练完器械，还在健身房呢，等会儿给你发张照片。",
                     "哈哈，你这么一说我也想吃了。", FRESH):
            self.assertFalse(pipeline.is_canned_reply(text), text)

    def test_repair_failure_reasons(self):
        self.assertEqual("empty", pipeline.repair_failure("  "))
        self.assertEqual("canned", pipeline.repair_failure(CANNED))
        self.assertEqual("", pipeline.repair_failure(NORMAL))
        self.assertTrue(pipeline.needs_repair(CANNED))
        self.assertFalse(pipeline.needs_repair(NORMAL))


class DefaultsTests(unittest.TestCase):
    """默认开（用户要求）+ 超时口径；这里不 patch，直接读模块默认值。"""

    def test_module_default_is_on(self):
        self.assertIs(True, pipeline.REPAIR_ENABLED)
        self.assertEqual(8.0, pipeline.REPAIR_TIMEOUT)

    def test_repair_stats_are_per_process_and_resettable(self):
        pipeline.reset_repair_stats()
        self.assertEqual({}, pipeline.repair_stats())


class RepairTests(unittest.TestCase):
    def setUp(self):
        pipeline.reset_repair_stats()
        self._flag = mock.patch.object(pipeline, "REPAIR_ENABLED", True)
        self._flag.start()

    def tearDown(self):
        self._flag.stop()
        pipeline.reset_repair_stats()

    def test_empty_reply_is_regenerated(self):
        llm = _SpyLLM(reply=FRESH)
        out = _repair(NORMAL, "", llm, user_text="你什么时候回来")
        self.assertEqual(out, FRESH)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(pipeline.repair_stats(), {"ok": 1})

    def test_canned_reply_is_regenerated(self):
        llm = _SpyLLM(reply=FRESH)
        out = _repair(NORMAL, CANNED, llm)
        self.assertEqual(out, FRESH)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(pipeline.repair_stats(), {"ok": 1})

    def test_prompt_carries_context_and_demands(self):
        llm = _SpyLLM(reply=FRESH)
        _repair(NORMAL, CANNED, llm, user_text="你什么时候回来",
                history="他说今天要加班")
        prompt = llm.calls[0]["prompt"]
        self.assertIn("你什么时候回来", prompt)
        self.assertIn("他说今天要加班", prompt)
        self.assertIn(NORMAL, prompt)     # 守卫改之前的那一版
        self.assertIn(CANNED, prompt)     # 现在准备发的那一版
        self.assertIn("第一人称", prompt)
        self.assertIn("只输出一句话", prompt)
        self.assertEqual(llm.calls[0]["umo"], UMO)

    def test_normal_reply_never_calls_model(self):
        llm = _SpyLLM(reply=FRESH)
        out = _repair(NORMAL, NORMAL, llm)
        self.assertEqual(out, NORMAL)
        self.assertEqual(llm.calls, [])
        self.assertEqual(pipeline.repair_stats(), {})

    def test_switch_off_keeps_guarded_without_calling(self):
        llm = _SpyLLM(reply=FRESH)
        with mock.patch.object(pipeline, "REPAIR_ENABLED", False):
            out = _repair(NORMAL, CANNED, llm)
        self.assertEqual(out, CANNED)
        self.assertEqual(llm.calls, [])
        self.assertEqual(pipeline.repair_stats(), {"skip": 1})

    def test_non_chat_source_is_not_repaired(self):
        llm = _SpyLLM(reply=FRESH)
        out = _repair(NORMAL, CANNED, llm, source="proactive")
        self.assertEqual(out, CANNED)
        self.assertEqual(llm.calls, [])
        self.assertEqual(pipeline.repair_stats(), {"skip": 1})

    def test_no_llm_keeps_guarded_without_calling(self):
        out = asyncio.run(pipeline.repair_reply(
            NORMAL, CANNED, types.SimpleNamespace(llm=None), UMO))
        self.assertEqual(out, CANNED)
        self.assertEqual(pipeline.repair_stats(), {"skip": 1})

    def test_model_exception_falls_back_to_guarded(self):
        llm = _SpyLLM(error=RuntimeError("模型挂了"))
        out = _repair(NORMAL, CANNED, llm)
        self.assertEqual(out, CANNED)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(pipeline.repair_stats(), {"fail": 1})

    def test_model_exception_on_empty_guarded_falls_back_to_raw(self):
        llm = _SpyLLM(error=RuntimeError("模型挂了"))
        out = _repair(NORMAL, "   ", llm)
        self.assertEqual(out, NORMAL)          # 绝不吞回复
        self.assertEqual(pipeline.repair_stats(), {"fail": 1})

    def test_timeout_falls_back_to_guarded(self):
        llm = _SpyLLM(reply=FRESH, delay=0.2)
        with mock.patch.object(pipeline, "REPAIR_TIMEOUT", 0.02):
            out = _repair(NORMAL, CANNED, llm)
        self.assertEqual(out, CANNED)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(pipeline.repair_stats(), {"fail": 1})

    def test_model_returning_canned_is_rejected(self):
        llm = _SpyLLM(reply=CANNED)
        out = _repair(NORMAL, CANNED, llm)
        self.assertEqual(out, CANNED)
        self.assertEqual(pipeline.repair_stats(), {"fail": 1})

    def test_model_returning_forbidden_is_rejected(self):
        # prepare_generated_line 会把禁语整句重写/换成中性句 → 仍罐头 → 不采用
        llm = _SpyLLM(reply="你美丽的老婆在呢。")
        out = _repair(NORMAL, CANNED, llm)
        self.assertEqual(out, CANNED)
        self.assertEqual(pipeline.repair_stats(), {"fail": 1})

    def test_model_returning_blank_is_rejected(self):
        llm = _SpyLLM(reply="   ")
        out = _repair(NORMAL, CANNED, llm)
        self.assertEqual(out, CANNED)
        self.assertEqual(pipeline.repair_stats(), {"fail": 1})

    def test_model_returning_paragraph_is_rejected(self):
        llm = _SpyLLM(reply="我等你回来。" * 30)
        out = _repair(NORMAL, CANNED, llm)
        self.assertEqual(out, CANNED)
        self.assertEqual(pipeline.repair_stats(), {"fail": 1})

    def test_at_most_one_model_call_per_repair(self):
        llm = _SpyLLM(reply=FRESH)
        _repair(BOTH, BOTH, llm)
        self.assertEqual(len(llm.calls), 1)


class RuntimeWiringTests(unittest.TestCase):
    def setUp(self):
        pipeline.reset_repair_stats()
        pipeline.reset_forbidden_regen_stats()

    def tearDown(self):
        pipeline.reset_repair_stats()
        pipeline.reset_forbidden_regen_stats()

    def test_runtime_replaces_canned_reply(self):
        llm = _SpyLLM(reply=FRESH)
        plugin = _decorate(NORMAL, CANNED, llm, message_str="你什么时候回来")
        self.assertEqual(plugin.sent, [FRESH])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(pipeline.repair_stats(), {"ok": 1})

    def test_runtime_regenerates_empty_reply(self):
        llm = _SpyLLM(reply=FRESH)
        plugin = _decorate(NORMAL, "", llm, message_str="你什么时候回来")
        self.assertEqual(plugin.sent, [FRESH])
        self.assertEqual(len(llm.calls), 1)

    def test_runtime_normal_reply_zero_model_calls(self):
        llm = _SpyLLM(reply=FRESH)
        plugin = _decorate(NORMAL, NORMAL, llm)
        self.assertEqual(plugin.sent, [NORMAL])
        self.assertEqual(llm.calls, [])

    def test_runtime_repair_and_forbidden_regen_never_both_call(self):
        """既是罐头又含禁语：修复调过一次模型后，禁语重生成必须跳过。"""
        llm = _SpyLLM(reply=CANNED)   # 修复也失败 → fail-open 到 guarded
        with mock.patch.object(pipeline, "FORBIDDEN_REGEN_ENABLED", True):
            plugin = _decorate(BOTH, BOTH, llm, message_str="你老婆在吗")
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(pipeline.repair_stats(), {"fail": 1})
        self.assertEqual(pipeline.forbidden_regen_stats(), {})
        self.assertTrue(plugin.sent and "我在呢，先别急" in plugin.sent[-1])

    def test_hook_runs_between_chat_safety_and_forbidden_regen(self):
        path = os.path.join(ROOT, "amo_runtime_reply.py")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        hook = "await _reply_pipeline.repair_reply("
        self.assertIn(hook, source)
        self.assertLess(source.index("text = apply_chat_safety("), source.index(hook))
        self.assertIn('_note_swallowed("amo_runtime_reply.reply_repair", _amo_exc)',
                      source)
        self.assertLess(source.index(hook),
                        source.index("await _reply_pipeline.regenerate_forbidden("))


class QuestionGuardEmptyExitTests(unittest.TestCase):
    """B1 集成：问句答非所问 → 空串 → chat 出口 repair → 最终非模板非空。"""

    def setUp(self):
        pipeline.reset_repair_stats()
        pipeline.reset_forbidden_regen_stats()
        pipeline.reset_repetition_memory()

    def tearDown(self):
        pipeline.reset_repair_stats()
        pipeline.reset_forbidden_regen_stats()
        pipeline.reset_repetition_memory()

    def test_chat_safety_returns_empty_for_unanswered_question(self):
        # 问句守卫不再套模板：答非所问 → 空串（不 or 原文），交出口修复。
        # 「在哪」由场景守卫（state_consistency._ensure_where_answer）补地点——那是
        # 宪法层，不属 B1；这里用同样可判定的 doing 问句验证问句守卫本身。
        plugin = _RuntimePlugin(_SpyLLM(reply=FRESH))
        self.assertEqual(pipeline.apply_chat_safety(
            "今天天气不错。", plugin, UMO, "你在干嘛呀？"), "")
        self.assertEqual(pipeline.apply_chat_safety(
            "今天天气不错。", plugin, UMO, "吃了吗？"), "")

    def test_unanswered_question_regenerated_at_chat_exit(self):
        llm = _SpyLLM(reply=FRESH)          # 假 LLM 会接话
        plugin = _RuntimePlugin(llm)
        event = _FakeEvent("你在干嘛呀？", "今天天气不错。")
        asyncio.run(plugin.on_decorating_result(event))
        self.assertEqual(1, len(llm.calls), "空串必须真的送到出口 repair")
        self.assertEqual(pipeline.repair_stats(), {"ok": 1})
        self.assertTrue(plugin.sent, "最终必须发出消息")
        final = plugin.sent[-1]
        self.assertTrue(final.strip(), "最终不能是空")
        self.assertNotEqual(final, "今天天气不错。")
        # 不是问句模板（is_canned_reply 覆盖 question.* 模板样例）。
        self.assertFalse(pipeline.is_canned_reply(final), final)
        self.assertEqual(final, FRESH)


class RepairSafetyProbeTests(unittest.TestCase):
    """3b-C：修复句采用前的 purchase/fact 宪法探针。"""

    def setUp(self):
        pipeline.reset_repair_stats()
        pipeline.reset_repetition_memory()

    def tearDown(self):
        pipeline.reset_repair_stats()
        pipeline.reset_repetition_memory()

    def test_generated_purchase_claim_is_not_adopted(self):
        # 假 LLM 补出「买了…649元」：账本没这笔 → 不采用，回退 raw，绝不带金额/已购。
        llm = _SpyLLM(reply="我买了米白色针织开衫，花了649元。")
        plugin = _decorate("今天天气不错。", "", llm,
                           message_str="你买那件开衫了吗？")
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(plugin.sent)
        final = plugin.sent[-1]
        self.assertEqual(final, "今天天气不错。")
        self.assertNotIn("买了", final)
        self.assertNotIn("649", final)
        self.assertEqual(pipeline.repair_stats(), {"fail_guard": 1})

    def test_generated_fact_conflict_is_not_adopted(self):
        # 此刻在家，修复句却说在商场 → fact 守卫要改口 → 整句不采用。
        plugin = _rich_plugin(_SpyLLM(reply="我在商场逛街呢，刚试了件衣服。"))
        out = asyncio.run(pipeline.repair_reply(
            "今天天气不错。", "", plugin, UMO, user_text="你在哪？"))
        self.assertEqual(out, "今天天气不错。")
        self.assertEqual(pipeline.repair_stats(), {"fail_guard": 1})


class RepairedCandidateSwapTests(unittest.TestCase):
    """3b-C：repair 成功后候选换成修复句，send 段复检/去重不许用旧候选覆盖。"""

    def setUp(self):
        pipeline.reset_repair_stats()
        pipeline.reset_repetition_memory()

    def tearDown(self):
        pipeline.reset_repair_stats()
        pipeline.reset_repetition_memory()

    def test_repaired_sentence_survives_validate_and_dedupe(self):
        fresh = "我刚到家，正换鞋呢，今天有点累。"
        old = "我买了米白色针织开衫，花了649元。"
        question = "你买那件开衫了吗？"
        plugin = _rich_plugin(_SpyLLM(reply=fresh))
        pipeline.apply_narrative_repetition(old, plugin, question, UMO)
        remember_candidate(plugin, UMO, old)        # 装饰链入口登记旧候选
        out = asyncio.run(pipeline.repair_reply(
            old, "嗯，我在呢。", plugin, UMO, user_text=question))
        self.assertEqual(out, fresh)
        self.assertEqual(pipeline._turn_candidate(UMO), fresh)  # 候选已换位
        # send 段：旧候选答到他会把修复句换回「我还没买…」；换位后必须原样保住。
        self.assertEqual(
            SC.validate_reply_assembly(out, plugin, umo=UMO, user_text=question), fresh)
        self.assertEqual(
            "".join(SC.dedupe_reply_segments([out], plugin=plugin, umo=UMO)), fresh)


class RepairProtectedSkipTests(unittest.TestCase):
    """3b-C：安全产物（还没买 / leak 挡话）不进 repair：不调模型、不二次改写。"""

    def setUp(self):
        pipeline.reset_repair_stats()
        pipeline.reset_repetition_memory()

    def tearDown(self):
        pipeline.reset_repair_stats()
        pipeline.reset_repetition_memory()

    def test_protected_guarded_is_skipped_without_model_call(self):
        for guarded in ("我试了试，挺喜欢的，还没买。", "我还没买那件针织开衫。"):
            with self.subTest(guarded=guarded):
                pipeline.reset_repair_stats()
                llm = _SpyLLM(reply=FRESH)
                out = _repair("今天天气不错。", guarded, llm)
                self.assertEqual(out, guarded)
                self.assertEqual(llm.calls, [])          # 一次模型都没调
                self.assertEqual(pipeline.repair_stats(), {"skip_protected": 1})


if __name__ == "__main__":
    unittest.main()
