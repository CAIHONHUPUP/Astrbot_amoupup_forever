# -*- coding: utf-8 -*-
"""单一兜底注册表（task-18）：意图/场景 -> 唯一一句，调用方不再各存一份池。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_question_guard  # noqa: E402
import amo_repetition_guard  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
import amo_state_consistency as consistency  # noqa: E402
from domains.reply import repetition_guard as registry  # noqa: E402

#: 问句模板仍在注册表里（数据未动，B2 才收缩）。B1 已删「调用方取模板造句」，
#: 这里保留数据期望，用来证明删的是产出、不是数据/识别集。
QUESTION_CASES = (
    ("where", {"place": "公司"}, "你在哪？", {"place": "公司"}, "我在公司呢。"),
    ("doing", {"place": "公司", "activity": "改方案"}, "你在干嘛？", {"doing": "改方案"},
     "我在改方案。"),
    ("who", {"place": "公司", "companion": "同事"}, "跟谁在一起？", {"who": "同事"},
     "和同事在一起呢。"),
    ("when_home", {"place": "家", "zone": "home"}, "几点回？", {}, "我已经在家啦。"),
    ("when_route", {"place": "路上", "zone": "road", "end": "七点"}, "几点回？",
     {"place": "路上", "end": "七点 "}, "我还在路上，七点 回去。"),
    ("when_late", {"place": "", "zone": "outside"}, "几点回？", {}, "我晚点回去。"),
    ("eaten", {"place": "公司", "category": "meal"}, "吃了吗？", {}, "吃过了，你呢？"),
    ("not_eaten", {"place": "公司", "zone": "office"}, "吃了吗？", {}, "还没呢，你呢？"),
)


def _plugin(activity):
    return SimpleNamespace(state=SimpleNamespace(current_activity=lambda: dict(activity)))


class RegistryShapeTests(unittest.TestCase):
    def test_every_intent_returns_one_sentence(self):
        self.assertTrue(registry.FALLBACKS)
        for intent, items in registry.FALLBACKS.items():
            with self.subTest(intent=intent):
                self.assertTrue(intent and isinstance(intent, str))
                self.assertTrue(items, intent)
                self.assertTrue(all(isinstance(item, str) and item.strip()
                                    for item in items), intent)
                line = registry.sentence(intent)
                self.assertEqual(line, items[0])  # 默认只返回第一句
                self.assertNotIn("\n", line)      # 一句，不是一段
                if "{" not in items[0]:            # 模板槽由调用方填，不算残留
                    self.assertNotIn("{", line)

    def test_old_pool_names_are_aliases_not_copies(self):
        self.assertIs(registry.SHORT_FALLBACKS, registry.FALLBACKS["short"])
        self.assertIs(registry.MODEL_LEAK_DEFLECTIONS, registry.FALLBACKS["leak"])
        self.assertEqual(
            tuple((theme, registry.FALLBACKS["emotion." + theme])
                  for theme, _items in registry.EMOTION_FALLBACKS),
            registry.EMOTION_FALLBACKS)
        # 门面 re-export 的也是同一份数据（不是又抄一份）
        self.assertIs(amo_repetition_guard.SHORT_FALLBACKS, registry.FALLBACKS["short"])

    def test_scene_question_and_action_data_are_registered(self):
        self.assertIn("scene_generic", registry.FALLBACKS)
        self.assertIn("question.where", registry.FALLBACKS)
        self.assertIn("action.shy", registry.FALLBACKS)
        self.assertIn("action.laugh", registry.FALLBACKS)
        self.assertIn("action.default", registry.FALLBACKS)
        self.assertIn("align.arrival_road", registry.FALLBACKS)
        self.assertIn("align.arrival_outside", registry.FALLBACKS)
        self.assertIn("align.swim_outside", registry.FALLBACKS)

    def test_registry_is_shared_by_callers(self):
        self.assertIs(pipeline._fallbacks.FALLBACKS, registry.FALLBACKS)
        # B1：问句守卫不再取模板造句（判据移出注册表依赖），只剩判据函数。
        self.assertFalse(hasattr(amo_question_guard, "_fallbacks"))
        self.assertEqual(tuple(consistency._GENERIC_REPLIES),
                         tuple(registry.FALLBACKS["scene_generic"]))

    def test_no_private_pools_left_in_callers(self):
        """私有池字面量必须删掉：池名不能再以赋值元组/字典/列表的形式出现。"""
        for name in ("amo_reply_pipeline.py", "amo_question_guard.py",
                     "domains/reply/repetition_guard.py", "amo_state_consistency.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                source = fh.read()
            for pool in ("_GENERIC_FALLBACKS", "SHORT_FALLBACKS", "_GENERIC_REPLIES",
                         "_ACTION_FALLBACK", "EMOTION_FALLBACKS",
                         "MODEL_LEAK_DEFLECTIONS"):
                with self.subTest(file=name, pool=pool):
                    self.assertNotIn(pool + " = (", source)
                    self.assertNotIn(pool + " = {", source)
                    self.assertNotIn(pool + " = [", source)


class ActionFallbackTests(unittest.TestCase):
    def test_action_fallback_no_longer_renders_registry_lines(self):
        # B1：动作-only 不再补台词（空串哨兵）；action.* 数据留给识别集（B2 才动）。
        import amo_message
        cases = (("（耳朵有点热）", "action.shy"), ("（笑）", "action.laugh"),
                 ("（把手机放下）", "action.default"))
        for text, key in cases:
            with self.subTest(key=key):
                self.assertTrue(registry.FALLBACKS[key])   # 数据仍在
                self.assertEqual(amo_message.action_only_fallback(text), "")
                self.assertTrue(amo_message.is_action_only(text))  # 判据仍在


class CallerParityTests(unittest.TestCase):
    def test_question_answer_no_longer_renders_registry_sentence(self):
        # B1：答非所问 → 空串（交出口 repair）；注册表里的问句模板数据原样保留。
        for key, act, question, render, expected in QUESTION_CASES:
            with self.subTest(key=key):
                activity = {"place": act.get("place"),
                            "zone": act.get("zone", "home"),
                            "activity": act.get("activity", ""),
                            "category": act.get("category", ""),
                            "companion": act.get("companion", ""),
                            "end": act.get("end", "")}
                self.assertEqual(amo_question_guard.answer(question, _plugin(activity), "跑题了。"),
                                 "")
                self.assertEqual(registry.sentence("question." + key, **render), expected)

    def test_pipeline_generic_detection_comes_from_registry(self):
        for text in ("还是老样子。", "我还在外面呢，晚点就回去。", "嗯", "等会儿再说！"):
            self.assertTrue(pipeline._is_generic_fallback(text), text)
        self.assertFalse(pipeline._is_generic_fallback("刚练完器械，还在健身房呢"))
        self.assertEqual(pipeline._GENERIC_BODIES, registry.generic_bodies())

    def test_status_repeat_sentence_comes_from_registry(self):
        self.assertEqual(pipeline._fallbacks.sentence("status_repeat"), "还是老样子。")

    def test_rotation_still_avoids_recent_lines(self):
        registry.reset()
        first = registry.short_fallback("reg:1", [])
        second = registry.short_fallback("reg:1", [first])
        self.assertTrue(first.strip() and second.strip())
        self.assertNotEqual(first, second)
        fixed, _dropped = registry.dedup_or_fallback("我没事。", "reg:2", [])
        self.assertIn("我没事", fixed)


if __name__ == "__main__":
    unittest.main()
