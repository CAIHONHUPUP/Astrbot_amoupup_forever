# -*- coding: utf-8 -*-
"""饭点占位的跨插件契约（2026-11-20）。

陪伴 amo_food_cost 与守护 services/date_rules 的 CLAIM_WINDOWS / CLAIM_WORDS
必须逐字一致，否则一边拦得住、另一边照吃。两个插件各自 CI 单独跑，
同目录下存在兄弟插件时才比对；结构断言（三个饭点）永远执行。
"""
from __future__ import annotations

import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_food_cost as food  # noqa: E402

WATCH = os.path.join(os.path.dirname(ROOT), "Astrbot_amoupup_watch",
                     "services", "date_rules.py")


def _value(node):
    """只认字面量、元组、字典和四则运算：常量写成 6 * 60 也能读。"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Tuple):
        return tuple(_value(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {_value(k): _value(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.BinOp):
        left, right = _value(node.left), _value(node.right)
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
    raise ValueError("不支持的常量表达式: %s" % ast.dump(node))


def _watch_const(name):
    with open(WATCH, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=WATCH)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return _value(node.value)
    return None


class StructureTests(unittest.TestCase):
    def test_slots_are_the_same_three(self):
        self.assertEqual({"breakfast", "lunch", "dinner"}, set(food.CLAIM_WINDOWS))

    def test_no_single_char_chi(self):
        # 单字「吃」会让 16:30 的「吃点东西」占掉晚饭
        self.assertNotIn("吃", food.CLAIM_WORDS)
        self.assertIn("饭", food.CLAIM_WORDS)


@unittest.skipUnless(os.path.isfile(WATCH), "兄弟插件不在同一目录")
class MealContractTests(unittest.TestCase):
    def test_windows_match(self):
        watch = _watch_const("CLAIM_WINDOWS")
        self.assertIsNotNone(watch, "watch 侧没有 CLAIM_WINDOWS")
        ours = {slot: (int(a), int(b)) for slot, (a, b) in food.CLAIM_WINDOWS.items()}
        self.assertEqual(ours, {slot: (int(a), int(b)) for slot, (a, b) in watch.items()},
                         "两个插件的饭点占位窗口漂移了")

    def test_words_match(self):
        watch = _watch_const("CLAIM_WORDS")
        self.assertIsNotNone(watch, "watch 侧没有 CLAIM_WORDS")
        self.assertEqual(tuple(food.CLAIM_WORDS), tuple(watch), "两侧餐饮词表漂移了")


if __name__ == "__main__":
    unittest.main()
