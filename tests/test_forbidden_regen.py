# -*- coding: utf-8 -*-
"""3c 阶段 2：禁语命中时的一次模型重生成（默认关闭，接在 apply_chat_safety 之后）。

锁五件事：
1) 开关关（模块默认 FORBIDDEN_REGEN_ENABLED=False）→ **零模型调用**、原样返回，
   真实 on_decorating_result 路径也一样（spy 断言 calls == []）；
2) 开启 + 假成功 → 采用新句、过 neutralize_forbidden、ok+1；
3) 开启 + 异常 / 超时 → 返回原文（fail+1，绝不抛）；
4) 无命中 → 不调模型（不为正常消息付一次调用）；
5) 模型又把禁语说回来 / 返回空 → 返回原文（fail+1）。
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

import amo_persona_guard as guard  # noqa: E402
import amo_reply_pipeline as pipeline  # noqa: E402
from amo_shared import Comp  # noqa: E402
from amo_runtime_reply import RuntimeReplyMixin  # noqa: E402

UMO = "aiocqhttp:FriendMessage:regen"
DIRTY = "你美丽的老婆在呢。"
CLEAN = "我在呢。"


class _SpyLLM:
    """只有 chat_raw 的假 LLM：记录每次调用，可按脚本返回 / 抛错 / 慢返回。"""

    def __init__(self, reply="", error=None, delay=0.0, replies=None):
        self.reply = reply
        self.replies = None if replies is None else list(replies)
        self.error = error
        self.delay = delay
        self.calls = []

    async def chat_raw(self, umo, system, prompt):
        self.calls.append({"umo": umo, "system": system, "prompt": prompt})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if self.replies is not None:
            return self.replies.pop(0) if self.replies else ""
        return self.reply


def _plugin(llm):
    return types.SimpleNamespace(llm=llm)


def _regen(text, llm, user_text=""):
    return asyncio.run(pipeline.regenerate_forbidden(text, _plugin(llm), UMO, user_text))


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
    """最小运行时插件：装饰链只留 apply_chat_safety → 新钩子 → 发送记录。

    其余装饰步骤打桩，本文件只验证「新钩子接在哪个位置、开关关时是否动模型」。
    """

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


def _identity_safety(value, *args, **kwargs):
    return value


def _decorate(text, llm, message_str="在吗"):
    plugin = _RuntimePlugin(llm)
    event = _FakeEvent(message_str, text)
    with mock.patch.object(pipeline, "apply_chat_safety", _identity_safety):
        asyncio.run(plugin.on_decorating_result(event))
    return plugin


class DefaultOffTests(unittest.TestCase):
    def setUp(self):
        pipeline.reset_forbidden_regen_stats()

    def tearDown(self):
        pipeline.reset_forbidden_regen_stats()

    def test_module_default_is_off(self):
        self.assertIs(False, pipeline.FORBIDDEN_REGEN_ENABLED)

    def test_switch_off_never_calls_model(self):
        llm = _SpyLLM(reply=CLEAN)
        with mock.patch.object(pipeline, "FORBIDDEN_REGEN_ENABLED", False):
            out = _regen(DIRTY, llm)
        # 开关关：原样返回（逐字相等），一次模型调用都没有。
        self.assertEqual(out, DIRTY)
        self.assertEqual(llm.calls, [])
        stats = pipeline.forbidden_regen_stats()
        self.assertEqual(stats.get("skip"), 1)
        self.assertIsNone(stats.get("ok"))
        self.assertIsNone(stats.get("fail"))

    def test_switch_off_runtime_path_zero_model_calls(self):
        llm = _SpyLLM(reply=CLEAN)
        with mock.patch.object(pipeline, "FORBIDDEN_REGEN_ENABLED", False):
            plugin = _decorate(DIRTY, llm, message_str="老婆在干嘛")
        self.assertEqual(llm.calls, [])
        self.assertEqual(plugin.sent, [DIRTY])

    def test_no_hit_never_calls_model(self):
        llm = _SpyLLM(reply=CLEAN)
        with mock.patch.object(pipeline, "FORBIDDEN_REGEN_ENABLED", True):
            out = _regen("我饿了，快回来。", llm)
        self.assertEqual(out, "我饿了，快回来。")
        self.assertEqual(llm.calls, [])
        self.assertEqual(pipeline.forbidden_regen_stats(), {})

    def test_empty_text_never_calls_model(self):
        llm = _SpyLLM(reply=CLEAN)
        with mock.patch.object(pipeline, "FORBIDDEN_REGEN_ENABLED", True):
            self.assertEqual(_regen("   ", llm), "   ")
        self.assertEqual(llm.calls, [])
        self.assertEqual(pipeline.forbidden_regen_stats(), {})

    def test_no_llm_counts_skip_and_keeps_text(self):
        with mock.patch.object(pipeline, "FORBIDDEN_REGEN_ENABLED", True):
            out = asyncio.run(pipeline.regenerate_forbidden(
                DIRTY, types.SimpleNamespace(llm=None), UMO))
        self.assertEqual(out, DIRTY)
        self.assertEqual(pipeline.forbidden_regen_stats().get("skip"), 1)


class EnabledTests(unittest.TestCase):
    def setUp(self):
        pipeline.reset_forbidden_regen_stats()
        self._flag = mock.patch.object(pipeline, "FORBIDDEN_REGEN_ENABLED", True)
        self._flag.start()

    def tearDown(self):
        self._flag.stop()
        pipeline.reset_forbidden_regen_stats()

    def test_success_rewrites_cleans_and_counts_ok(self):
        llm = _SpyLLM(reply=CLEAN)
        out = _regen(DIRTY, llm, user_text="老婆在干嘛")
        self.assertEqual(out, CLEAN)
        self.assertEqual(out, guard.neutralize_forbidden(CLEAN))
        self.assertEqual(len(llm.calls), 1)
        call = llm.calls[0]
        self.assertEqual(call["umo"], UMO)
        self.assertIn("你美丽的老婆", call["prompt"])
        self.assertIn("不要第三人称自称", call["prompt"])
        self.assertIn("老婆在干嘛", call["prompt"])
        self.assertEqual(pipeline.forbidden_regen_stats(), {"ok": 1})

    def test_model_exception_returns_original(self):
        llm = _SpyLLM(error=RuntimeError("模型挂了"))
        out = _regen(DIRTY, llm)
        self.assertEqual(out, DIRTY)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(pipeline.forbidden_regen_stats(), {"fail": 1})

    def test_timeout_returns_original(self):
        llm = _SpyLLM(reply=CLEAN, delay=0.2)
        with mock.patch.object(pipeline, "FORBIDDEN_REGEN_TIMEOUT", 0.02):
            out = _regen(DIRTY, llm)
        self.assertEqual(out, DIRTY)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(pipeline.forbidden_regen_stats(), {"fail": 1})

    def test_still_forbidden_returns_original(self):
        llm = _SpyLLM(reply="你美丽的老婆又来了。")
        out = _regen(DIRTY, llm)
        self.assertEqual(out, DIRTY)
        self.assertEqual(pipeline.forbidden_regen_stats(), {"fail": 1})

    def test_empty_reply_returns_original(self):
        llm = _SpyLLM(reply="")
        out = _regen(DIRTY, llm)
        self.assertEqual(out, DIRTY)
        self.assertEqual(pipeline.forbidden_regen_stats(), {"fail": 1})

    def test_runtime_path_uses_regenerated_line(self):
        llm = _SpyLLM(reply=CLEAN)
        plugin = _decorate(DIRTY, llm, message_str="老婆在干嘛")
        self.assertEqual(plugin.sent, [CLEAN])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(pipeline.forbidden_regen_stats(), {"ok": 1})


class WiringTests(unittest.TestCase):
    """源码级接线：新钩子必须在 apply_chat_safety 之后、strip_tech_terms 之前。"""

    @staticmethod
    def _source():
        path = os.path.join(ROOT, "amo_runtime_reply.py")
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_hook_runs_after_chat_safety(self):
        source = self._source()
        call = "await _reply_pipeline.regenerate_forbidden("
        self.assertIn(call, source)
        self.assertLess(source.index("text = apply_chat_safety("), source.index(call))

    def test_hook_runs_before_tech_terms_and_is_guarded(self):
        source = self._source()
        call = "await _reply_pipeline.regenerate_forbidden("
        self.assertLess(source.index(call),
                        source.index('strip_tech_terms(text, source="chat")'))
        self.assertIn('_note_swallowed("amo_runtime_reply.forbidden_regen", _amo_exc)',
                      source)


if __name__ == "__main__":
    unittest.main()
