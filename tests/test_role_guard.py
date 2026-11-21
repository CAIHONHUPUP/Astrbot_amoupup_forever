# -*- coding: utf-8 -*-
"""情绪归属守卫。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_role_guard import rewrite  # noqa: E402


class RoleGuardTests(unittest.TestCase):
    def test_user_angry_not_self_denial(self):
        text = "（抱着枕头在沙发上，笑了一声）我哪生气了，别给我扣帽子。剧刚看到关键处卡住了，气的是这个。"
        out = rewrite(text, "我刚才气到差点把遥控器扔了")
        self.assertNotIn("我哪生气", out)
        self.assertNotIn("扣帽子", out)
        self.assertIn("关键处卡住", out)

    def test_user_angry_no_laugh_opener(self):
        out = rewrite("哈哈哈，别生气嘛。", "这剧卡得我气死了")
        self.assertFalse(out.startswith("哈哈"))

    def test_normal_kept(self):
        self.assertEqual("好呀。", rewrite("好呀。", "今天吃什么"))
