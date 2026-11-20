# -*- coding: utf-8 -*-
"""日记补记 / 隐私措辞 / 情绪余波 / 修正命令。"""

import datetime as dt
import inspect
import os
import shutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402
astrbot_stub.install()

import amo_fact_pool as fp  # noqa: E402
from amo_diary_guard import sanitize_privacy  # noqa: E402
from diary_engine import append_note, read_diary, write_diary  # noqa: E402


class NoteTests(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(ROOT, "tests", "_tmp_diary_note")
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(self.root, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_append_note_keeps_body(self):
        day = dt.date(2026, 11, 15)
        write_diary(self.root, day, "今天很普通。")
        path = append_note(self.root, day, "回想起来，他其实不容易。")
        text = read_diary(self.root, day)
        self.assertTrue(path)
        self.assertIn("今天很普通", text)
        self.assertIn("【补记】回想起来，他其实不容易", text)

    def test_append_requires_existing(self):
        self.assertEqual(append_note(self.root, dt.date(2026, 11, 16), "补记"), "")


class PrivacyTests(unittest.TestCase):
    def test_no_cold_tech_words(self):
        out = sanitize_privacy("这是系统加密的，上锁了。")
        self.assertNotIn("加密", out)
        self.assertNotIn("上锁", out)
        self.assertIn("才不给你看", out)


class MoodAndCommandTests(unittest.TestCase):
    def test_yesterday_low_is_carried(self):
        text = fp.render_prompt({"facts": [], "meta": {"yesterday_low": True}})
        self.assertIn("【情绪余波】", text)

    def test_fix_diary_command_exists(self):
        from amo_commands import CommandMixin
        self.assertTrue(hasattr(CommandMixin, "cmd_fix_diary"))
        source = inspect.getsource(CommandMixin.cmd_fix_diary)
        self.assertIn("_diary_write", source)


if __name__ == "__main__":
    unittest.main()
