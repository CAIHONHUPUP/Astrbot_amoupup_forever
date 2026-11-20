# -*- coding: utf-8 -*-
"""关系抽取的单测：只认明确陈述，不瞎猜。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from relation_learn import normalize_relation, parse_relations  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(normalize_relation("男朋友"), "恋人")
        self.assertEqual(normalize_relation("哥哥"), "家人")
        self.assertEqual(normalize_relation("同事"), "同事")
        self.assertEqual(normalize_relation("闺蜜"), "朋友")
        self.assertEqual(normalize_relation("仇人"), "仇人")
        self.assertEqual(normalize_relation("路人"), "")


class ParseTests(unittest.TestCase):
    def test_simple(self):
        pairs = parse_relations("小明是我同事")
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0], "小明")
        self.assertEqual(pairs[0][1], "同事")

    def test_with_he(self):
        pairs = parse_relations("我和阿斌是同学")
        self.assertEqual([(item[0], item[1]) for item in pairs], [("阿斌", "同学")])

    def test_lover(self):
        pairs = parse_relations("小冉是我女朋友")
        self.assertEqual([(item[0], item[1]) for item in pairs], [("小冉", "恋人")])

    def test_skip_pronouns(self):
        self.assertEqual(parse_relations("她是我女朋友"), [])
        self.assertEqual(parse_relations("他是我朋友"), [])
        self.assertEqual(parse_relations("这个是我朋友"), [])

    def test_skip_self(self):
        self.assertEqual(parse_relations("我是你老公"), [])
        self.assertEqual(parse_relations("我哥是我朋友"), [])

    def test_plain_chat(self):
        self.assertEqual(parse_relations("今天天气不错"), [])
        self.assertEqual(parse_relations("我朋友小明今天来了"), [])

    def test_no_duplicate(self):
        pairs = parse_relations("小明是我同事，小明是我同事")
        self.assertEqual(len(pairs), 1)


if __name__ == "__main__":
    unittest.main()
