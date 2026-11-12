# -*- coding: utf-8 -*-
"""面板「删除画像」要连数据插件一起删，否则刷新后还在（amo 侧）。"""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


class ProfileDeleteSyncTests(unittest.TestCase):
    def test_handler_deletes_data_side(self):
        src = open(os.path.join(_ROOT, "amo_api.py"), encoding="utf-8").read()
        body = src[src.index("async def api_profile_delete"):src.index("async def api_relation_save")]
        self.assertIn('_data_call("delete_profile"', body)
        self.assertIn("data_counts", body)


if __name__ == "__main__":
    unittest.main()
