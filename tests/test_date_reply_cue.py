# -*- coding: utf-8 -*-
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixtures import FakeDB, FakePlugin  # Phase 11 共享测试桩
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import amo_date_context as dc  # noqa: E402






class ReplyCueTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin()
        self.now = dt.datetime(2026, 11, 14, 21, 0)
        self.p.db.kv_set("date_now", {"line": "约会中", "until": self.now.timestamp() + 3600})

    def test_her_proposal_marks_pending_ending(self):
        self.assertTrue(dc.mark_reply_date_end(self.p, "时间不早了，我们回家吧", self.now))
        ending = self.p.db.kv_get("date_ending")
        self.assertTrue(ending.get("sent"))
        self.assertIn("回家", ending.get("fact") or "")

    def test_negation_does_not_mark(self):
        self.assertFalse(dc.mark_reply_date_end(self.p, "我还不想回家", self.now))
        self.assertEqual(self.p.db.kv_get("date_ending", {}), {})

    def test_no_date_does_not_mark(self):
        self.p.db.kv_set("date_now", {})
        self.assertFalse(dc.mark_reply_date_end(self.p, "该回家了", self.now))


if __name__ == "__main__":
    unittest.main()
