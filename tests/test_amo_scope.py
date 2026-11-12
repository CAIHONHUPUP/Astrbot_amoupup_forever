# -*- coding: utf-8 -*-
"""amo_scope 单测：群聊 / 私聊归属 + 事件取 scope。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amo_scope  # noqa: E402


class Event:
    def __init__(self, private, group_id=""):
        self._private = private
        self._group = group_id

    def is_private_chat(self):
        return self._private

    def get_group_id(self):
        return self._group


class Broken:
    def is_private_chat(self):
        raise RuntimeError("平台适配器没实现")


class GroupTests(unittest.TestCase):
    def test_group_umos(self):
        self.assertTrue(amo_scope.is_group_umo("aiocqhttp:GroupMessage:123"))
        self.assertTrue(amo_scope.is_group_umo("lark:GroupMessage:oc_x"))
        self.assertFalse(amo_scope.is_group_umo("aiocqhttp:FriendMessage:1"))
        self.assertFalse(amo_scope.is_group_umo(""))
        self.assertFalse(amo_scope.is_group_umo(None))

    def test_private_rows(self):
        rows = [
            {"umo": "aiocqhttp:FriendMessage:1", "content": "私聊"},
            {"umo": "aiocqhttp:GroupMessage:2", "content": "群里"},
            {"umo": "lark:GroupMessage:3", "content": "飞书群里"},
            {"content": "老数据没有 umo"},
            None,
        ]
        kept = [row["content"] for row in amo_scope.private_rows(rows) if row]
        self.assertEqual(kept, ["私聊", "老数据没有 umo"])

    def test_private_rows_empty(self):
        self.assertEqual(amo_scope.private_rows(None), [])
        self.assertEqual(amo_scope.private_rows([]), [])


class ScopeTests(unittest.TestCase):
    def test_private_event(self):
        self.assertEqual(amo_scope.event_scope(Event(True)), ("private", ""))

    def test_group_event(self):
        self.assertEqual(amo_scope.event_scope(Event(False, "g1")), ("group", "g1"))

    def test_missing_event(self):
        self.assertEqual(amo_scope.event_scope(None), ("private", ""))

    def test_broken_event_falls_back(self):
        self.assertEqual(amo_scope.event_scope(Broken()), ("private", ""))

    def test_normalize(self):
        self.assertEqual(amo_scope.normalize("group", "g1"), ("group", "g1"))
        self.assertEqual(amo_scope.normalize("", "g1"), ("private", ""))
        self.assertEqual(amo_scope.normalize("GROUP", "g1"), ("private", ""))
        self.assertEqual(amo_scope.normalize(None, None), ("private", ""))


if __name__ == "__main__":
    unittest.main()
