# -*- coding: utf-8 -*-
"""跨插件契约快照：能陪 调 守护 / 能记 的方法必须在对方源码里真实存在。

两处调用面：
1. `watch.xxx(...)`：守护插件实例（社交桥握手后拿到）；
2. `self._data_call("xxx", ...)`：字符串形式的记忆插件调用——正是过去
   「能记没有 nightly_memory_candidates」这类问题最容易漏检的地方，这里按字面量逐个核对。

对方插件不在同一台机器时自动跳过。更新快照：
    python tests/test_cross_plugin_contract.py --update
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.dirname(ROOT)
CONTRACT_DIR = os.path.join(ROOT, "tests", "contracts")
SKIP_DIRS = {"__pycache__", ".git", "tests", "tools", "_test_tmp", ".test-tmp",
             "dist-publish", "data", "views"}

BUILTIN_METHODS = frozenset((
    "get", "setdefault", "items", "keys", "values", "update", "pop", "copy", "clear",
    "append", "extend", "insert", "remove", "index", "count", "sort", "reverse",
    "format", "strip", "split", "join", "lower", "upper", "replace", "encode", "decode",
))

PROVIDERS = {
    # `watch`：直接拿到的守护实例；`instance`：amo_social_bridge.get_watch 的返回值
    "Astrbot_amoupup_watch": {"vars": ("watch", "instance"), "cls": None},
}
DATA_PLUGIN = "Astrbot_amoupup_data"
DATA_API_CLASS = "MemoryAPI"


def _py_files(root):
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(base, name)


def _chain(node):
    attrs = []
    cur = node
    while isinstance(cur, ast.Attribute):
        attrs.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        attrs.reverse()
        return cur.id, attrs
    return None, []


def consumer_calls(root, vars_):
    found = []
    for path in _py_files(root):
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read(), filename=path)
        except (SyntaxError, OSError):
            continue
        rel = os.path.relpath(path, root).replace("\\", "/")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            root_name, attrs = _chain(node.func)
            if root_name is None or not attrs:
                continue
            if root_name == "self":
                if not attrs or attrs[0] not in vars_:
                    continue
                method, full = attrs[-1], "self." + ".".join(attrs)
            else:
                if root_name not in vars_:
                    continue
                method, full = attrs[-1], ".".join([root_name] + attrs)
            if method in BUILTIN_METHODS or method.startswith("_"):
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            found.append((method, kwargs, "%s:%d" % (rel, node.lineno), full))
    return found


def provider_surface(dir_path, cls_name=None):
    out = {}
    if not os.path.isdir(dir_path):
        return out
    for path in _py_files(dir_path):
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read(), filename=path)
        except (SyntaxError, OSError):
            continue
        if cls_name:
            targets = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == cls_name:
                    targets = [n for n in node.body
                               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    break
        else:
            targets = [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for fn in targets:
            params = {a.arg for a in list(fn.args.args) + list(fn.args.kwonlyargs)
                      if a.arg not in ("self", "cls")}
            entry = out.setdefault(fn.name, {"params": set(), "var_kw": False})
            entry["params"] |= params
            entry["var_kw"] = entry["var_kw"] or fn.args.kwarg is not None
    return out


def data_call_literals(root):
    """`self._data_call("method", ...)` 里的方法名。"""
    found = []
    for path in _py_files(root):
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read(), filename=path)
        except (SyntaxError, OSError):
            continue
        rel = os.path.relpath(path, root).replace("\\", "/")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            root_name, attrs = _chain(node.func)
            if root_name != "self" or not attrs or attrs[-1] != "_data_call":
                continue
            if node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                found.append((node.args[0].value, "%s:%d" % (rel, node.lineno)))
    return found


def available():
    return {name: cfg for name, cfg in PROVIDERS.items()
            if os.path.isdir(os.path.join(PLUGINS_DIR, name))}


def snapshot_path(provider):
    return os.path.join(CONTRACT_DIR, provider + "_surface.json")


def build_snapshot(provider, cfg):
    surface = provider_surface(os.path.join(PLUGINS_DIR, provider), cfg["cls"])
    used = sorted({call[0] for call in consumer_calls(ROOT, cfg["vars"]) if call[0] in surface})
    return {name: sorted(surface[name]["params"]) for name in used}


class CrossPluginContractTests(unittest.TestCase):
    def test_watch_methods_exist(self):
        providers = available()
        if not providers:
            self.skipTest("本机没有守护插件目录，契约测试跳过")
        for name, cfg in providers.items():
            surface = provider_surface(os.path.join(PLUGINS_DIR, name), cfg["cls"])
            missing = sorted({(call[0], call[2], call[3])
                              for call in consumer_calls(ROOT, cfg["vars"])
                              if call[0] not in surface})
            with self.subTest(provider=name):
                self.assertFalse(missing, "调用了 %s 里不存在的方法：%s" % (name, missing[:8]))

    def test_watch_kwargs_are_accepted(self):
        for name, cfg in available().items():
            surface = provider_surface(os.path.join(PLUGINS_DIR, name), cfg["cls"])
            bad = []
            for method, kwargs, where, full in consumer_calls(ROOT, cfg["vars"]):
                info = surface.get(method)
                if info is None or info["var_kw"] or not kwargs:
                    continue
                extra = sorted(kwargs - info["params"])
                if extra:
                    bad.append("%s 调 %s 不接受 %s" % (where, full, ",".join(extra)))
            with self.subTest(provider=name):
                self.assertFalse(bad, "关键字参数对不上：%s" % bad[:6])

    def test_watch_describe_exists(self):
        """握手只认 bound method `instance.describe`（不是 call 节点），单独钉住。"""
        watch_dir = os.path.join(PLUGINS_DIR, "Astrbot_amoupup_watch")
        if not os.path.isdir(watch_dir):
            self.skipTest("本机没有守护插件目录")
        surface = provider_surface(watch_dir, None)
        self.assertIn("describe", surface, "守护插件没有 describe()，握手会一直失败")

    def test_data_call_literals_exist(self):
        data_dir = os.path.join(PLUGINS_DIR, DATA_PLUGIN)
        if not os.path.isdir(data_dir):
            self.skipTest("本机没有能记插件目录")
        surface = provider_surface(data_dir, DATA_API_CLASS)
        self.assertTrue(surface, "能记 MemoryAPI 一个方法都没解析到")
        calls = data_call_literals(ROOT)
        self.assertTrue(calls, "_data_call 字面量一个都没扫到，检查扫描规则")
        missing = sorted({(name, where) for name, where in calls if name not in surface})
        self.assertFalse(missing, "能记 MemoryAPI 里没有这些方法：%s" % missing[:8])

    def test_surface_snapshot(self):
        for name, cfg in available().items():
            current = build_snapshot(name, cfg)
            path = snapshot_path(name)
            with self.subTest(provider=name):
                if not os.path.exists(path):
                    self.fail("缺少契约快照 %s，跑 python tests/test_cross_plugin_contract.py --update"
                              % os.path.basename(path))
                with io.open(path, encoding="utf-8") as fh:
                    saved = json.load(fh)
                if current != saved:
                    diff = {k: (saved.get(k), current.get(k))
                            for k in set(current) | set(saved) if saved.get(k) != current.get(k)}
                    self.fail("守护接口表面变了：%s；确认没影响后更新快照" % diff)


if __name__ == "__main__":
    if "--update" in sys.argv:
        os.makedirs(CONTRACT_DIR, exist_ok=True)
        for provider, cfg in available().items():
            payload = build_snapshot(provider, cfg)
            with io.open(snapshot_path(provider), "w", encoding="utf-8", newline="") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
            print("snapshot:", provider, len(payload), "methods")
    else:
        unittest.main()
