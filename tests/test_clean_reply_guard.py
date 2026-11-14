# -*- coding: utf-8 -*-
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from amo_shared import clean_reply

class CleanReplyGuardTests(unittest.TestCase):
    def test_thought_and_dsml_removed(self):
        self.assertEqual(clean_reply("<thought>想一下</thought>老公在吗"), "老公在吗")
        self.assertEqual(clean_reply("<DSML>x</DSML>在的"), "在的")

if __name__ == "__main__":
    unittest.main()
