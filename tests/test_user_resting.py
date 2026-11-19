# -*- coding: utf-8 -*-
"""他说要先睡 → 主动消息别再打扰。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_rest import note_user_rest, resting_now  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()


class RestingTests(unittest.TestCase):
    def test_says_going_to_sleep_marks_resting(self):
        plugin = FakePlugin()
        note_user_rest(plugin, "（下巴蹭蹭）我先睡会，昨天折腾太晚了。")
        self.assertTrue(resting_now(plugin))

    def test_wake_clears(self):
        plugin = FakePlugin()
        note_user_rest(plugin, "我先睡会")
        note_user_rest(plugin, "我醒了")
        self.assertFalse(resting_now(plugin))

    def test_any_message_clears(self):
        plugin = FakePlugin()
        note_user_rest(plugin, "我睡了")
        note_user_rest(plugin, "中午吃啥")
        self.assertFalse(resting_now(plugin))

    def test_says_busy_marks_resting(self):
        plugin = FakePlugin()
        note_user_rest(plugin, "我先忙了，晚点聊")
        self.assertTrue(resting_now(plugin))

    def test_busy_finished_clears(self):
        plugin = FakePlugin()
        note_user_rest(plugin, "我在开会")
        note_user_rest(plugin, "忙完了")
        self.assertFalse(resting_now(plugin))

    def test_not_busy_does_not_mark(self):
        plugin = FakePlugin()
        note_user_rest(plugin, "今天不忙")
        self.assertFalse(resting_now(plugin))

    def test_plain_message_does_not_mark(self):
        plugin = FakePlugin()
        note_user_rest(plugin, "在干嘛")
        self.assertFalse(resting_now(plugin))


if __name__ == "__main__":
    unittest.main()
