# -*- coding: utf-8 -*-
"""日摘补主语：以称呼开头的句子不能拼成「他老公说……」。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_shared_text import digest_subject  # noqa: E402


class DigestSubjectTests(unittest.TestCase):
    def test_address_becomes_he(self):
        # 线上真实病句：原文「老公说以后回来和我一起住」被写成「他老公说……」
        self.assertEqual(digest_subject("老公说以后回来和我一起住"),
                         "他说以后回来和我一起住")

    def test_address_with_comma(self):
        self.assertEqual(digest_subject("亲爱的，早点睡"), "他早点睡")

    def test_address_alone(self):
        self.assertEqual(digest_subject("老公"), "他说")

    def test_existing_subject_untouched(self):
        for text in ("他夸我好看", "我下班后去吃了炒米粉", "她笑了", "用户说好"):
            self.assertEqual(digest_subject(text), text)

    def test_bare_sentence_gets_he(self):
        self.assertEqual(digest_subject("早上吃了鸡蛋"), "他早上吃了鸡蛋")

    def test_empty(self):
        self.assertEqual(digest_subject(""), "")
        self.assertEqual(digest_subject(None), "")


if __name__ == "__main__":
    unittest.main()
