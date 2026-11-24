# -*- coding: utf-8 -*-
"""语气温度回归（2026-11-24）：few-shot 挑暖句、说话方式有下限、元规则搬回 system、
出站不再先删称呼/情绪。

四步改动各一组；每组都配一条反向断言（把改动拿掉会红）。
"""
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_persona_pack as pack  # noqa: E402
import amo_prompt_registry as registry  # noqa: E402
import tone_guard  # noqa: E402
from domains.reply import status_text  # noqa: E402

STYLE_PATH = os.path.join(ROOT, "persona_packs", "style_examples.json")


def _read(path):
    return io.open(path, encoding="utf-8").read()


class StylePoolTests(unittest.TestCase):
    """第一步：示例池决定她学哪种句式。"""

    def test_warmth_score_orders_question_above_period(self):
        self.assertGreater(pack.warmth_score("你吃了没呀？"), pack.warmth_score("我吃了。"))

    def test_warm_first_is_deterministic(self):
        rows = ["平平淡淡。", "想你啦", "干嘛呢？"]
        first = pack._warm_first(rows)
        self.assertEqual(first, pack._warm_first(list(rows)))
        self.assertEqual(first[-1], "平平淡淡。")

    def test_pool_has_enough_warm_samples(self):
        data = json.load(io.open(STYLE_PATH, encoding="utf-8"))
        rows = [row["text"] for row in data["items"]]
        warm = [text for text in rows
                if any(ch in text for ch in pack.WARM_TONE) or "？" in text or "?" in text]
        asks = [text for text in rows if "？" in text or "?" in text]
        self.assertGreaterEqual(len(rows), 45, "示例池没补暖句")
        self.assertGreaterEqual(len(asks), 8, "带反问的示例太少，模型学不到反问")
        self.assertGreaterEqual(len(warm) / len(rows), 0.6,
                                "带语气词/反问的示例不足六成（改前 48.5%）")

    def test_samples_prefer_warm_when_intent_matches(self):
        # 「想你」落到 miss；挑出来的两条里至少一条带语气词/反问
        picked = pack.style_samples("我有点想你了", limit=2)
        self.assertEqual(len(picked), 2)
        self.assertTrue(any(pack.warmth_score(text) >= 2 for text in picked), picked)


class StyleRuleTests(unittest.TestCase):
    """第二步：天花板改地板 + 句式不再强制「接住+汇报自己」。"""

    def test_rule_style_has_floor_not_ceiling(self):
        text = registry._b_rule_style()
        self.assertIn("反问半句", text)
        self.assertIn("带一个语气词", text)
        self.assertNotIn("别每句都叠", text)
        self.assertNotIn("最多一个问句", text)

    def test_rule_style_does_not_force_status_report(self):
        text = registry._b_rule_style()
        self.assertIn("当下最想说的", text)
        self.assertNotIn("再说一句你自己的状态", text)

    def test_short_reply_head_no_forbidden_list(self):
        head = registry._SHORT_RULES_HEAD
        for phrase in ("祝您工作顺利", "我明白了", "很抱歉"):
            self.assertNotIn(phrase, head, "禁语清单又把客服词塞回注意力了")
        # 反向：客服还得有一个落点，不能一句都不提
        self.assertIn("客服", head)
        # 正向例子留着（教她怎么答应，而不是只教她别说什么）
        self.assertIn("好呀~", head)


class StateSplitTests(unittest.TestCase):
    """第三步：每轮不变的元规则搬回 system，事实块留在 <amo_state>。"""

    def _ctx_state_source(self):
        src = _read(os.path.join(ROOT, "amo_runtime_context.py"))
        start = src.index("def _ctx_state(")
        end = src.index("def _recent_micro_line(", start)
        return src[start:end]

    def test_state_has_no_duplicate_rules(self):
        body = self._ctx_state_source()
        for tag in ("【语气】", "【聊天格式】", "【长度】", "【人格】",
                    "【身份核验】", "【别翻旧账】", "【问题怎么答】"):
            self.assertNotIn(tag, body, "元规则又塞回 <amo_state> 了: " + tag)

    def test_fact_blocks_stay_in_state(self):
        body = self._ctx_state_source()
        for tag in ("【事实锁定】", "【此刻地点】", "【绝对事实·最高优先级】"):
            self.assertIn(tag, body, "事实块被搬走了: " + tag)

    def test_moved_rules_are_in_system(self):
        src = _read(os.path.join(ROOT, "amo_llm.py"))
        for tag in ("【身份核验】", "【别翻旧账】", "【问题怎么答】"):
            self.assertIn(tag, src, "元规则没接进 system: " + tag)

    def test_consumption_rule_is_short(self):
        src = _read(os.path.join(ROOT, "amo_fact_binder.py"))
        self.assertIn("只按【今天的账单】说", src)
        self.assertNotIn("绝对不许说", src, "消费红线又写回长否定句了")

    def test_diary_lines_have_own_header(self):
        src = _read(os.path.join(ROOT, "amo_fact_binder.py"))
        self.assertIn('"【今天的小日记】"', src)


class StateGateTests(unittest.TestCase):
    """状态按需：没问到的氛围事实不塞，规则块不动。"""

    def test_gate_drops_unasked_flavor(self):
        import amo_state_gate as gate
        parts = ["【穿搭】今天穿黑毛衣", "【心情】平静", "【正在】在工位上忙工作",
                 "【事实锁定】以日程为准"]
        out = gate.filter_parts(parts, "你在干嘛呀")
        self.assertIn("【正在】在工位上忙工作", out)
        self.assertIn("【事实锁定】以日程为准", out)
        self.assertNotIn("【穿搭】今天穿黑毛衣", out)
        self.assertNotIn("【心情】平静", out)

    def test_gate_keeps_asked_flavor(self):
        import amo_state_gate as gate
        parts = ["【穿搭】今天穿黑毛衣", "【天气】小雨 16℃", "【正在】在工位"]
        out = gate.filter_parts(parts, "今天穿什么了呀，外面下雨吗")
        self.assertIn("【穿搭】今天穿黑毛衣", out)
        self.assertIn("【天气】小雨 16℃", out)

    def test_gate_keeps_unknown_blocks(self):
        import amo_state_gate as gate
        parts = ["【最近聊到】午饭的事", "【绝对事实】10:00 在工位"]
        self.assertEqual(gate.filter_parts(parts, "想你了"), parts)


class ConsumptionOffTests(unittest.TestCase):
    """钱包退役后，消费守卫不再把话换成固定句。"""

    def _plugin(self):
        class Fake:
            wallet = None
            def cfg_bool(self, key, default=False):
                return default
        plugin = Fake()
        from amo_init import _RetiredWallet
        plugin.wallet = _RetiredWallet(None)
        return plugin

    def test_retired_wallet_guard_is_off(self):
        import amo_fact_binder as binder
        self.assertEqual(binder._mode(self._plugin()), "off")

    def test_retired_wallet_does_not_rewrite(self):
        import amo_fact_binder as binder
        text, changed, _ = binder.guard("我刚吃了碗热汤面，你呢", self._plugin())
        self.assertEqual(text, "我刚吃了碗热汤面，你呢")
        self.assertFalse(changed)


class OutboundWarmthTests(unittest.TestCase):
    """第四步：出站别先删称呼/情绪。"""

    def setUp(self):
        tone_guard.reset()

    def test_tone_guard_keeps_callname(self):
        tone_guard.remember("老公你终于回我啦", umo="u1")
        fixed, actions = tone_guard.guard("老公你终于理我了", umo="u1")
        self.assertTrue(actions, "这组本该触发去重")
        self.assertTrue(fixed.startswith("老公"), fixed)

    def test_tone_guard_still_strips_filler(self):
        tone_guard.remember("其实我就是想你了", umo="u2")
        fixed, _actions = tone_guard.guard("其实我就是有点累", umo="u2")
        self.assertFalse(fixed.startswith("其实我就是"), fixed)

    def test_status_keeps_warm_sentence(self):
        kept, dropped = status_text._drop_repeated_status("我到家啦，想你了。", {"home"})
        self.assertIn("想你了", kept)
        self.assertFalse(dropped)

    def test_status_still_drops_plain_repeat(self):
        kept, dropped = status_text._drop_repeated_status("我到家了。", {"home"})
        self.assertEqual(kept, "")
        self.assertTrue(dropped)


class DropInsteadOfReplaceTests(unittest.TestCase):
    """冲突时删句，不拿固定说法顶替（除非他正问「在哪」）。"""

    def _violations(self):
        return [{"rule": "place", "sentence": "我在商场逛呢。", "replacement": "我在公司呢。"}]

    def test_drops_conflict_sentence_when_answer_remains(self):
        import amo_reply_pipeline as pipeline
        out = pipeline._drop_canned_conflicts("我在商场逛呢。你吃饭了没？", self._violations())
        self.assertEqual(out, "你吃饭了没？")

    def test_keeps_replacement_when_question_is_where(self):
        import amo_reply_pipeline as pipeline
        old = pipeline._turn_question
        pipeline._turn_question = lambda umo: "你现在在哪呢？"
        try:
            out = pipeline._drop_canned_conflicts(
                "我在商场逛呢。你吃饭了没？", self._violations(), umo="u-where")
        finally:
            pipeline._turn_question = old
        self.assertEqual(out, "", "他问在哪时必须给地点，不能删")

    def test_keeps_replacement_when_question_is_doing(self):
        import amo_reply_pipeline as pipeline
        old = pipeline._turn_question
        pipeline._turn_question = lambda umo: "你在干嘛呀？"
        try:
            out = pipeline._drop_canned_conflicts(
                "我在商场逛呢。你吃饭了没？", self._violations(), umo="u-doing")
        finally:
            pipeline._turn_question = old
        self.assertEqual(out, "", "他问在干嘛时必须给活动，不能删")

    def test_does_not_empty_single_sentence(self):
        import amo_reply_pipeline as pipeline
        out = pipeline._drop_canned_conflicts("我在商场逛呢。", self._violations())
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
