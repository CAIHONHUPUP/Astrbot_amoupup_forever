# -*- coding: utf-8 -*-
"""amo_runtime.py 结构拆分回归（2026-11-17 structure-b）。

拆分前 amo_runtime.py 2117 行（含 warm_up）；现在是薄门面 + 三个 mixin 模块：

    amo_runtime_context.py  RuntimeContextMixin  取时/身份/动态上下文（含 on_message）
    amo_runtime_reply.py    RuntimeReplyMixin    发送后/装饰改写/语音
    amo_runtime_tick.py     RuntimeTickMixin     后台循环/整点维护/里程碑/每日总结 + warm_up

RuntimeMixin 仍由三者组合，旧 import 路径与 patch 点（amo_runtime._now 等）保持；
方法体是逐行搬的；回归以行为/关键入口为准，不再锁 co_code 哈希。
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
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

NEW_MODULES = ("amo_runtime_context.py", "amo_runtime_context_agenda.py", "amo_runtime_context_extra.py", "amo_runtime_reply.py", "amo_runtime_tick.py",
               "amo_runtime_ports.py")
REQUIRED_METHODS = (
    "on_message", "sync_current_state", "_dynamic_context",
    "on_decorating_result", "_tick", "_background_loop",
    "_maybe_daily_digest", "_first_private_umo",
    "_ctx_outfit", "_ctx_outfit_change", "_ctx_agenda",
    "_ctx_meal_missing", "_ctx_tail_world", "_ctx_memory",
    "_deco_soft_guards", "_maybe_send_voice_reply",
)


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

    def test_required_methods_still_exist(self):
        names = set(_method_names())
        missing = [name for name in REQUIRED_METHODS if name not in names]
        self.assertEqual([], missing, "关键入口方法丢了：%s" % missing)

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

    def test_each_new_module_under_1200_lines(self):
        for name in NEW_MODULES:
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                lines = len(fh.read().splitlines())
            self.assertLess(lines, 1200, "%s 又长回去了：%d 行" % (name, lines))


if __name__ == "__main__":
    unittest.main()
