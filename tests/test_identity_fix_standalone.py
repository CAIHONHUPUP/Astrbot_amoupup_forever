# -*- coding: utf-8 -*-
"""7.5.3：人称反转后处理（单句复述）单点校验。

「就因为你是我老婆才总跟你要啊」这类句子是她在复述他上一句的句式，
发送前要按说话人视角改回「我是你老婆」，而不是改成「你是我老公」。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_immersion_guard import fix_identity  # noqa: E402


class StandaloneIdentityFixTests(unittest.TestCase):
    def test_single_inversion_flips_to_speaker(self):
        self.assertEqual(fix_identity("就因为你是我老婆才总跟你要啊。"),
                         "就因为我是你老婆才总跟你要啊。")

    def test_single_inversion_with_echo_prefix(self):
        self.assertEqual(fix_identity("想看就看，谁让你是我老婆。"),
                         "想看就看，谁让我是你老婆。")

    def test_wife_synonyms(self):
        self.assertEqual(fix_identity("你是我媳妇儿，当然向着我"),
                         "我是你媳妇儿，当然向着我")
        self.assertEqual(fix_identity("你是我太太，还能有假"),
                         "我是你太太，还能有假")

    def test_paired_inversion_still_swaps(self):
        self.assertEqual(fix_identity("我是老公，你是我老婆"), "我是你老婆，你是我老公")

    def test_correct_sentence_untouched(self):
        self.assertEqual(fix_identity("你是我老公，我在家等你"), "你是我老公，我在家等你")

    def test_third_person_husband(self):
        self.assertEqual(fix_identity("她老公今天加班"), "我老公今天加班")


if __name__ == "__main__":
    unittest.main()
