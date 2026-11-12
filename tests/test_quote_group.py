# -*- coding: utf-8 -*-
"""群聊引用概率：人数到阈值后不该句句引用。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402
from main import HumanCorePlugin  # noqa: E402


class FakePlugin:
    def __init__(self, speakers, prob):
        self.speakers = speakers
        self.prob = prob

    def cfg_int(self, key, default=0):
        return 3 if key == "quote_group_speaker_threshold" else default

    def cfg_float(self, key, default=0.0):
        return self.prob if key == "quote_group_probability" else default

    def _group_speaker_count(self, umo):
        return self.speakers


def _should(fake):
    return HumanCorePlugin._should_quote_group(fake, "aiocqhttp:GroupMessage:1")


class QuoteGroupTests(unittest.TestCase):
    def test_below_threshold_never_quotes(self):
        self.assertFalse(_should(FakePlugin(2, 1.0)))

    def test_probability_one_keeps_old_behaviour(self):
        for _ in range(20):
            self.assertTrue(_should(FakePlugin(4, 1.0)))

    def test_probability_zero_never_quotes(self):
        for _ in range(20):
            self.assertFalse(_should(FakePlugin(4, 0.0)))

    def test_probability_is_respected(self):
        original = main.random.random
        try:
            main.random.random = lambda: 0.39
            self.assertTrue(_should(FakePlugin(4, 0.4)))
            main.random.random = lambda: 0.41
            self.assertFalse(_should(FakePlugin(4, 0.4)))
        finally:
            main.random.random = original

    def test_errors_do_not_raise(self):
        class Boom(FakePlugin):
            def _group_speaker_count(self, umo):
                raise RuntimeError("db gone")

        self.assertFalse(_should(Boom(0, 1.0)))


if __name__ == "__main__":
    unittest.main()
