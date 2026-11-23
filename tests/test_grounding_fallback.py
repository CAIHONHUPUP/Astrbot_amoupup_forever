# -*- coding: utf-8 -*-
"""两次没接住时走一次 repair（B3）：返回原文或 repair 结果，绝不为空、绝不模板。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_reply_pipeline import repair_reply  # noqa: E402

UMO = "sim:FriendMessage:1"
#: 旧 safe_reply 的固定句（B3 后只留在识别集里，任何来源都不许再发出去）。
CANNED = "我在听，你接着说。"
FRESH = "刚把碗洗完，手上还湿着呢。"


class _StubLLM:
    """repair 桩：模拟真模型在修复提示词里重写一句（空串 = 模型不可用）。"""

    def __init__(self, answer=""):
        self.answer = str(answer or "")
        self.calls = 0

    async def chat_raw(self, umo="", system="", prompt="", *args, **kwargs):
        self.calls += 1
        return self.answer


def _repair(original, user="今天上班", answer=""):
    return asyncio.run(repair_reply(original, "", SimpleNamespace(llm=_StubLLM(answer)),
                                    UMO, user))


class GroundingRepairTests(unittest.TestCase):
    def test_model_unavailable_keeps_original(self):
        # 旧断言「按意图发安全句」→ 现在「保留原文 + 非空」
        for user in ("你晚上想吃什么？", "这剧卡得我气死了", "我刚刷到一个视频", "今天上班"):
            out = _repair("原话一句。", user=user)
            self.assertTrue(str(out or "").strip(), user)
            self.assertEqual("原话一句。", out, user)

    def test_fresh_sentence_is_used(self):
        out = _repair("原话一句。", user="你晚上想吃什么？", answer=FRESH)
        self.assertEqual(FRESH, out)

    def test_canned_sentence_is_rejected(self):
        # 模型还是写固定安全句 → 判罐头、不采用，回退原文
        out = _repair("原话一句。", user="今天上班", answer=CANNED)
        self.assertEqual("原话一句。", out)

    def test_never_empty_and_never_template(self):
        for answer in ("", CANNED, "嗯嗯，好呀。"):
            out = _repair("原话一句。", user="今天上班", answer=answer)
            self.assertTrue(str(out or "").strip(), repr(answer))
            self.assertNotEqual(CANNED, out, repr(answer))


if __name__ == "__main__":
    unittest.main()
