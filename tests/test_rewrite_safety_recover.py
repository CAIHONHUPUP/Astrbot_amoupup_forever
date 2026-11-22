# -*- coding: utf-8 -*-
"""候选优先还原的门限（task-40/42）：只在现行文本是固定兜底/多池拼装时还原候选。

回归两件事：
1) 崩溃检查点里 recover() 把 guard_mod 用在赋值之前，任何「有候选且与现行不同」
   的调用都会 NameError，被调用方 except Exception 吞掉 → 候选还原层整层失效；
2) 第一版不做门限、把所有被改坏的现行文本都还原，daily holdout 从 50/50 掉到 27-44。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_rewrite_safety as safety  # noqa: E402
from domains.reply import repetition_guard as registry  # noqa: E402

CANDIDATE = "我在公司呢，睡觉。你呢？"
NORMAL_CURRENT = "我在家呢，睡觉。你呢？"
FALLBACK_CURRENT = "还是老样子。"
MULTI_POOL_CURRENT = "哈哈哈，挺喜欢的，还没买，笑死我了。"


def _sanitize(text):
    return (text, "ok")


class RecoverGateTests(unittest.TestCase):
    def _recover(self, current, candidate=CANDIDATE, source="chat"):
        original = safety.peek_candidate
        safety.peek_candidate = lambda plugin, umo: candidate
        try:
            return safety.recover(None, "umo", current, _sanitize, guard=None,
                                  source=source, user_text="在干嘛")
        finally:
            safety.peek_candidate = original

    def test_registry_classifies_fallback_and_normal(self):
        self.assertTrue(safety._current_is_fallback(FALLBACK_CURRENT, registry))
        self.assertFalse(safety._current_is_fallback(NORMAL_CURRENT, registry))
        self.assertTrue(registry.is_multi_pool(MULTI_POOL_CURRENT))

    def test_restores_when_current_is_fixed_fallback(self):
        self.assertEqual(self._recover(FALLBACK_CURRENT), CANDIDATE)

    def test_keeps_complete_candidate_instead_of_restoring(self):
        # 完整候选即使与场景冲突也交给场景守卫，不许靠候选优先整条顶回去。
        self.assertEqual(self._recover(NORMAL_CURRENT), NORMAL_CURRENT)

    def test_non_chat_source_never_restores(self):
        self.assertEqual(self._recover(FALLBACK_CURRENT, source="proactive"),
                         FALLBACK_CURRENT)

    def test_no_candidate_keeps_current(self):
        self.assertEqual(self._recover(FALLBACK_CURRENT, candidate=""), FALLBACK_CURRENT)


if __name__ == "__main__":
    unittest.main()
