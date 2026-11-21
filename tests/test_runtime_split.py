# -*- coding: utf-8 -*-
"""amo_runtime.py 结构拆分回归（2026-11-17 structure-b）。

拆分前 amo_runtime.py 2117 行（含 warm_up）；现在是薄门面 + 三个 mixin 模块：

    amo_runtime_context.py  RuntimeContextMixin  取时/身份/动态上下文（含 on_message）
    amo_runtime_reply.py    RuntimeReplyMixin    发送后/装饰改写/语音
    amo_runtime_tick.py     RuntimeTickMixin     后台循环/整点维护/里程碑/每日总结 + warm_up

RuntimeMixin 仍由三者组合，旧 import 路径与 patch 点（amo_runtime._now 等）保持；
方法体是逐行搬的，3.12 下用 co_code + 常量表哈希锁住"一字未改"（常量值改一个字也会红）。
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_runtime  # noqa: E402
import amo_runtime_context  # noqa: E402
import amo_runtime_ports  # noqa: E402
import amo_runtime_reply  # noqa: E402
import amo_runtime_tick  # noqa: E402

NEW_MODULES = ("amo_runtime_context.py", "amo_runtime_reply.py", "amo_runtime_tick.py",
               "amo_runtime_ports.py")
EXPECTED_METHODS = [
    "_align_arrival", "_align_footwear", "_align_swim_where", "_align_transit_word",
    "_apply_retention", "_background_loop", "_bot_initiated_line", "_ctx_agenda", "_ctx_facts", "_ctx_finalize",
    "_ctx_group", "_ctx_identity", "_ctx_memory", "_ctx_memory_tail_blocks", "_ctx_outfit",
    "_ctx_outfit_change", "_ctx_place_fix", "_ctx_query_blocks", "_ctx_state",
    "_ctx_state_blocks", "_ctx_tail_append", "_ctx_tail_runtime", "_ctx_tail_world",
    "_date_fact_line", "_deco_polish_native", "_deco_rewrite_reply", "_deco_soft_guards",
    "_deco_text_cleanup", "_diary_audit_tick", "_digest_profile_lines", "_digest_profile_write",
    "_dynamic_context", "_ensure_home_images", "_fill_pose_refs", "_first_private_umo", "_lean_parts",
    "_maybe_daily_digest", "_maybe_milestone_archive", "_maybe_send_voice_reply",
    "_milestone_archive_once", "_rain_fact_line", "_recent_micro_line", "_schedule_proposal_line",
    "_schedule_tool_line", "_self_identity", "_tick", "_tick_housekeeping", "_tick_loop_tasks",
    "_tick_schedule_media", "_tick_social", "_trip_sync", "on_after_message_sent",
    "on_decorating_result", "on_message", "sync_current_state",
]
CODE_MD5 = {
    "on_message": "acc047b7fd28796c805811abd9c7df05",
    "_dynamic_context": "cb95d4b83caf9c5efe0e919ceb10de19",
    "on_decorating_result": "aca2f274e927283da2e7909840d14a57",
    "_tick": "ac96fb9f0472533cfc38feb32d6a078e",
    "_maybe_daily_digest": "4acb3ce8497d6574eaed72b676066486",
    "warm_up": "511847b73896b66a6b03cb204c5832da",
}
#: 常量表指纹：递归展开 code 对象（按结构，不掺内存地址），改了提示词/数值就红。
FINGERPRINTS = {
    "on_message": "6da8d75162f656678fc8120b2a2dbee0",
    "_dynamic_context": "80205d5e7e2942927e3d71ac2c17db71",
    "on_decorating_result": "05f628a66ab90f94bb81b103981de42c",
    "_tick": "ea8ccbb018f28d852b20e7ef4c6f1644",
    "_maybe_daily_digest": "ee8626b0b24ac01c304112f7456abe22",
    "warm_up": "2e1a8372ae7b979ccade341cacff8811",
}


def _stable_const(value):
    if isinstance(value, types.CodeType):
        return ("code", hashlib.md5(value.co_code).hexdigest(),
                [_stable_const(item) for item in value.co_consts], value.co_names)
    return value


def _fingerprint(code):
    payload = (code.co_code, [_stable_const(item) for item in code.co_consts], code.co_names)
    return hashlib.md5(repr(payload).encode()).hexdigest()


def _method_names():
    return sorted(name for name in dir(amo_runtime.RuntimeMixin)
                  if callable(getattr(amo_runtime.RuntimeMixin, name, None))
                  and hasattr(getattr(amo_runtime.RuntimeMixin, name), "__code__"))


class RuntimeSplitTests(unittest.TestCase):
    def test_old_import_paths_still_work(self):
        self.assertTrue(callable(amo_runtime.RuntimeMixin))
        self.assertTrue(inspect.iscoroutinefunction(amo_runtime.warm_up))
        # 历史 patch 点（tests/fixtures.py 的 _CLOCK_BINDINGS）必须仍然存在
        for name in ("_now", "now_ts", "today_str"):
            self.assertTrue(hasattr(amo_runtime, name), "门面缺少 %s" % name)

    def test_mixins_composed_in_order(self):
        names = [cls.__name__ for cls in amo_runtime.RuntimeMixin.__mro__]
        self.assertEqual(names, ["RuntimeMixin", "RuntimeContextMixin", "RuntimeReplyMixin",
                                 "RuntimeTickMixin", "RuntimeMixinBase", "object"])
        self.assertTrue(issubclass(amo_runtime_context.RuntimeContextMixin,
                                   amo_runtime_ports.RuntimeMixinBase))

    def test_method_homes(self):
        context = ("on_message", "sync_current_state", "_dynamic_context", "_ctx_outfit")
        reply = ("on_decorating_result", "_deco_soft_guards", "_maybe_send_voice_reply")
        tick = ("_background_loop", "_tick", "_maybe_daily_digest", "_first_private_umo")
        for name in context:
            self.assertTrue(hasattr(amo_runtime_context.RuntimeContextMixin, name), name)
            self.assertFalse(hasattr(amo_runtime_reply.RuntimeReplyMixin, name), name)
            self.assertFalse(hasattr(amo_runtime_tick.RuntimeTickMixin, name), name)
        for name in reply:
            self.assertTrue(hasattr(amo_runtime_reply.RuntimeReplyMixin, name), name)
            self.assertFalse(hasattr(amo_runtime_context.RuntimeContextMixin, name), name)
        for name in tick:
            self.assertTrue(hasattr(amo_runtime_tick.RuntimeTickMixin, name), name)
            self.assertFalse(hasattr(amo_runtime_context.RuntimeContextMixin, name), name)

    def test_method_set_unchanged(self):
        self.assertEqual(_method_names(), sorted(EXPECTED_METHODS))
        self.assertEqual(len(_method_names()), 55)

    def test_facade_methods_are_same_objects(self):
        for mixin in (amo_runtime_context.RuntimeContextMixin,
                      amo_runtime_reply.RuntimeReplyMixin,
                      amo_runtime_tick.RuntimeTickMixin):
            for name, func in vars(mixin).items():
                if callable(func) and hasattr(func, "__code__"):
                    self.assertIs(getattr(amo_runtime.RuntimeMixin, name), func, name)

    def test_warm_up_reexported_from_tick(self):
        self.assertIs(amo_runtime.warm_up, amo_runtime_tick.warm_up)
        source = inspect.getsource(amo_runtime.warm_up)
        self.assertIn("await asyncio.sleep(3)", source)
        self.assertIn("ensure_schedule", source)

    @unittest.skipUnless(sys.version_info[:2] == (3, 12), "co_code 哈希只对 Python 3.12 稳定")
    def test_method_bytecode_identical_to_pre_split(self):
        for name, want in CODE_MD5.items():
            func = amo_runtime.warm_up if name == "warm_up" else getattr(amo_runtime.RuntimeMixin, name)
            got = hashlib.md5(func.__code__.co_code).hexdigest()
            self.assertEqual(got, want, "%s 的方法体和拆分前不一致" % name)
            self.assertEqual(_fingerprint(func.__code__), FINGERPRINTS[name],
                             "%s 的常量表（提示词/数值）和拆分前不一致" % name)

    def test_no_duplicate_method_definitions(self):
        seen = set()
        for mixin in (amo_runtime_context.RuntimeContextMixin,
                      amo_runtime_reply.RuntimeReplyMixin,
                      amo_runtime_tick.RuntimeTickMixin):
            for name in vars(mixin):
                if name.startswith("__"):
                    continue
                self.assertNotIn(name, seen, "%s 被定义了两次" % name)
                seen.add(name)

    def test_new_modules_do_not_import_facade(self):
        for name in NEW_MODULES:
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    self.assertNotEqual(mod.split(".")[-1], "amo_runtime",
                                        "%s 不许 import 门面" % name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name.split(".")[-1], "amo_runtime",
                                            "%s 不许 import 门面" % name)

    def test_new_modules_have_no_silent_except(self):
        for name in NEW_MODULES:
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                body = [n for n in node.body
                        if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                and isinstance(n.value.value, str))]
                self.assertFalse(len(body) == 1 and isinstance(body[0], ast.Pass),
                                 "%s:%d 有裸 except: pass" % (name, node.lineno))

    def test_each_new_module_under_1500_lines(self):
        for name in NEW_MODULES:
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                lines = len(fh.read().splitlines())
            self.assertLess(lines, 1500, "%s 又长回去了：%d 行" % (name, lines))


if __name__ == "__main__":
    unittest.main()
