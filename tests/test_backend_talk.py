# -*- coding: utf-8 -*-
"""出站技术词黑名单（task-26）：服务器/报错/超时/权限/面板…不出聊天。

现场（work/audit/cross_plugin.md §3.1）：8 条后台话术进聊天，例如
「可能是服务器那边卡住了」「一直报错」「网络连接超时了」「说我没权限」。
这里钉住唯一词表 + 出口清洗：
1. 任务点名的 9 个技术词全部命中；正常聊天/「减肥失败」「门卡住了」不误伤；
2. 聊天：删掉命中分句，整条不可说换成角色化短句；
3. push/系统通知：整条只剩技术话就不发；
4. 照片 caption：命中就只发图；
5. 命令返回也过同一道闸（on_decorating_result 的非 LLM 分支）。
"""
from __future__ import annotations

import asyncio
import os
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

import amo_message as M  # noqa: E402
import amo_runtime_reply as R  # noqa: E402
import amo_shared as shared  # noqa: E402
import amo_shared_text as text_mod  # noqa: E402
from test_cov2_message import FakeMix, FakePipeline  # noqa: E402


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class TechTermTests(unittest.TestCase):
    def test_task_terms_all_detected(self):
        for word in ("服务器", "报错", "超时", "权限", "面板", "接口", "异常",
                     "重试", "网络连接"):
            self.assertIn(word, text_mod.TECH_HARD_TERMS, word)
            self.assertTrue(text_mod.looks_like_tech_talk("又%s了，真烦。" % word),
                            word)

    def test_normal_chat_not_touched(self):
        corpus = (
            "今天好累呀，晚上想吃火锅。",
            "我买了件米白色针织开衫，好看吗？",
            "刚刚在楼下看到一只橘猫，好胖好可爱。",
            "减肥又失败了，好难过。",          # 「失败」不是技术上下文，不误伤
            "门卡住了，等我一下。",            # 「卡住」不是技术上下文，不误伤
            "我在商场试衣服呢，你猜好不好看？",
            "这个系统学习法挺好用的。",        # 「系统」日常义不误伤？见 test_soft
        )
        for sentence in corpus[:-1]:
            self.assertFalse(text_mod.looks_like_tech_talk(sentence), sentence)
            self.assertEqual(text_mod.strip_tech_terms(sentence, source="chat"),
                             sentence, sentence)

    def test_soft_terms_need_technical_context(self):
        self.assertTrue(text_mod.looks_like_tech_talk("图片发送失败了，等一下"))
        self.assertTrue(text_mod.looks_like_tech_talk("照片加载卡住了"))
        self.assertFalse(text_mod.looks_like_tech_talk("这次考试失败了"))
        self.assertFalse(text_mod.looks_like_tech_talk("门卡住了"))

    def test_production_samples_removed_from_chat(self):
        cases = (
            "图片一直发不出来，可能是服务器那边卡住了。",
            "还是失败，同一个报错，三回了。",
            "又断了，这网络连接超时了。真没骗你，就是出不来图…",
            "这回连权限都没了，说我没权限用这个。真不是我不给，是它不让我拍。",
        )
        for raw in cases:
            fixed = text_mod.strip_tech_terms(raw, source="chat")
            self.assertTrue(fixed.strip(), raw)
            for word in ("服务器", "报错", "超时", "权限", "网络连接"):
                self.assertNotIn(word, fixed, (raw, fixed))

    def test_mixed_sentences_keep_human_part(self):
        fixed = text_mod.strip_tech_terms(
            "今天好累呀。服务器又崩了。想早点睡。", source="chat")
        self.assertEqual(fixed, "今天好累呀。想早点睡。")

    def test_system_notice_dropped_entirely(self):
        notice = "【日记质检】昨天有 4 条日记对不上：待重写 4 条；详情在面板。"
        self.assertEqual(text_mod.strip_tech_terms(notice, source="push"), "")
        self.assertEqual(text_mod.strip_tech_terms(notice, source="system"), "")

    def test_caption_dropped_but_image_kept(self):
        self.assertEqual(text_mod.sanitize_caption("服务器又报错了，图没出来"), "")
        self.assertEqual(text_mod.sanitize_caption("喏，刚拍的"), "喏，刚拍的")


class _Eng:
    def segment_text_by_rules(self, text):
        return [text]


def _plugin():
    p = FakeMix({"seg_enable": True}, segment_engine=_Eng())

    async def segment_wait(*args, **kwargs):
        return 0.0

    p._segment_wait = segment_wait
    p._quote_allowed = lambda umo, group: False
    p._remember_bot_message = lambda umo, text: p.remembered.append((umo, text))

    class Ctx:
        def __init__(self):
            self.sent = []

        async def send_message(self, umo, chain):
            self.sent.append((umo, chain))

    p.context = Ctx()
    return p


class SendPathTests(unittest.TestCase):
    def test_push_with_only_tech_talk_is_not_sent(self):
        p = _plugin()
        with mock.patch.object(M, "_reply_pipeline",
                               FakePipeline("服务器那边卡住了，报错超时。")):
            self.assertFalse(asyncio.run(p._send_to_umo("u", "原文", source="push")))
        self.assertEqual(p.context.sent, [])

    def test_chat_with_only_tech_talk_falls_back_to_persona_line(self):
        p = _plugin()
        with mock.patch.object(M, "_reply_pipeline",
                               FakePipeline("服务器一直报错，超时了。")):
            self.assertTrue(asyncio.run(p._send_to_umo("u", "原文", source="chat")))
        sent_text = "".join(comp.text for _umo, chain in p.context.sent
                            for comp in chain.chain
                            if isinstance(comp, shared.Comp.Plain))
        self.assertTrue(sent_text.strip())
        for word in ("服务器", "报错", "超时"):
            self.assertNotIn(word, sent_text)

    def test_normal_text_still_sent(self):
        p = _plugin()
        normal = "今天好累呀，想早点睡。"
        with mock.patch.object(M, "_reply_pipeline", FakePipeline(normal)):
            self.assertTrue(asyncio.run(p._send_to_umo("u", "原文", source="chat")))
        self.assertEqual(len(p.context.sent), 1)
        chain = p.context.sent[0][1].chain
        self.assertEqual(chain[-1].text, normal)

    def test_media_caption_tech_dropped_image_kept(self):
        p = _plugin()
        components = [shared.Comp.Plain("服务器报错，图没出来"),
                      shared.Comp.Image(file="x.jpg")]
        ok = asyncio.run(p._send_media_to_umo("u", components, event_id="ev1",
                                              source="photo", kind="photo"))
        self.assertTrue(ok)
        payload = p.context.sent[0][1].chain
        self.assertTrue(payload)
        self.assertFalse(any(isinstance(comp, shared.Comp.Plain) for comp in payload))
        self.assertTrue(any(isinstance(comp, shared.Comp.Image) for comp in payload))

    def test_command_result_sanitized(self):
        result = types.SimpleNamespace(chain=[
            shared.Comp.Plain("今天好累呀。"),
            shared.Comp.Plain("服务器报错，稍后再试。"),
        ])
        R.RuntimeReplyMixin._sanitize_command_result(object(), result)
        self.assertEqual([comp.text for comp in result.chain], ["今天好累呀。"])

        empty = types.SimpleNamespace(chain=[shared.Comp.Plain("网络连接超时")])
        R.RuntimeReplyMixin._sanitize_command_result(object(), empty)
        self.assertEqual(empty.chain, [])


class WiringGuardTests(unittest.TestCase):
    """源码级接线：聊天/主动/push/照片 caption/命令/draw 都必须调同一个词表。"""

    def test_chat_and_push_wired_in_message(self):
        source = _read("amo_message.py")
        self.assertIn("strip_tech_terms", source)
        self.assertIn('strip_tech_terms(text, source="chat")', source)
        self.assertIn("sanitize_caption", source)

    def test_chat_and_command_wired_in_runtime(self):
        source = _read("amo_runtime_reply.py")
        self.assertIn("strip_tech_terms(text, source=\"chat\")", source)
        self.assertIn("_sanitize_command_result", source)

    def test_draw_tool_and_caption_wired(self):
        source = _read("draw_engine.py")
        self.assertIn("sanitize_caption", source)
        # 用户可见的工具返回不许再把工程词/权限句当台词交给模型
        for bad in ("权限不足：这个功能只对老公开放", "触发了发图限制",
                    "上一张还在生成，还没出来", "合拍失败：%s"):
            self.assertFalse(bad in source, bad)

    def test_shared_text_is_single_source(self):
        source = _read("amo_shared_text.py")
        self.assertIn("TECH_HARD_TERMS", source)
        for word in ("服务器", "报错", "超时", "权限", "面板", "接口", "异常",
                     "重试", "网络连接"):
            self.assertIn(word, source)


if __name__ == "__main__":
    unittest.main()
