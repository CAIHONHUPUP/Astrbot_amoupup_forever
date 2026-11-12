# -*- coding: utf-8 -*-
"""merge_uid_alias_data：把「同一个人的两个号」合进主 uid。

跨平台归一之后（QQ 302578963 / 飞书 ou_xxx），历史数据要并到主 uid 上；
并错了会丢消息、丢关系、甚至把两个人的档案混成一个，所以按场景逐条验证。
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_db import AmoDB  # noqa: E402
from amo_shared import merge_uid_alias_data  # noqa: E402

ALIAS = "ou_cec66ea605ad17f62632fb044bc5f6ac"
CANON = "302578963"


class MergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="amo-merge-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.db = AmoDB(self.tmp)
        self.db.init()
        self.conn = self.db.conn

    def tearDown(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_empty_map_is_noop(self) -> None:
        self.assertEqual(merge_uid_alias_data(self.conn, {})["profiles"], 0)
        self.assertEqual(merge_uid_alias_data(self.conn, None)["profiles"], 0)

    def test_alias_only_profile_is_renamed(self) -> None:
        self.db.touch_profile(ALIAS, "飞书侧", "lark", "lark:FriendMessage:" + ALIAS, "", True)
        report = merge_uid_alias_data(self.conn, {ALIAS: CANON})
        self.assertEqual(report["profiles"], 1)
        self.assertEqual(self.db.get_profile(CANON)["name"], "飞书侧")
        self.assertEqual(self.db.get_profile(ALIAS), {})

    def test_both_profiles_are_merged(self) -> None:
        self.db.touch_profile(CANON, "QQ侧", "qq", "qq:1", "", True)
        self.db.touch_profile(ALIAS, "飞书侧", "lark", "lark:1", "", True)
        self.db.update_profile(CANON, relation="相恋")
        self.db.update_profile(ALIAS, notes="备注")
        self.conn.execute("UPDATE profiles SET msg_count=5 WHERE uid=?", (CANON,))
        self.conn.execute("UPDATE profiles SET msg_count=9 WHERE uid=?", (ALIAS,))
        self.conn.commit()
        merge_uid_alias_data(self.conn, {ALIAS: CANON})
        merged = self.db.get_profile(CANON)
        self.assertEqual(merged["name"], "QQ侧")
        self.assertEqual(merged["relation"], "相恋")
        self.assertEqual(merged["msg_count"], 9)
        self.assertIn("备注", merged["notes"])
        self.assertEqual(self.db.get_profile(ALIAS), {})

    def test_messages_move_and_dedupe(self) -> None:
        # 同一秒的同一条消息（迁移/双写会留下这种重复）才去重；
        # 不同时间的同文是两条真实消息，不能删。
        self.db.add_message("qq:1", ALIAS, "他", "user", "同一条")
        self.conn.execute("UPDATE messages SET created_at=100 WHERE uid=?", (ALIAS,))
        self.db.add_message("qq:1", CANON, "他", "user", "同一条")
        self.conn.execute("UPDATE messages SET created_at=100 WHERE uid=?", (CANON,))
        self.db.add_message("qq:1", ALIAS, "他", "user", "只有别名有")
        report = merge_uid_alias_data(self.conn, {ALIAS: CANON})
        texts = [row["content"] for row in self.db.recent_messages("qq:1")]
        self.assertEqual(sorted(texts), sorted(["同一条", "只有别名有"]))
        self.assertGreaterEqual(report["messages"], 2)

    def test_proactive_log_moves(self) -> None:
        self.db.log_proactive(ALIAS, "meal", "吃饭没")
        merge_uid_alias_data(self.conn, {ALIAS: CANON})
        row = self.conn.execute("SELECT * FROM proactive_log").fetchone()
        self.assertEqual(row["uid"], CANON)

    def test_relation_rows_normalized(self) -> None:
        self.db.upsert_relation(CANON, "friend", "朋友")
        self.db.upsert_relation(ALIAS, "friend", "朋友")
        merge_uid_alias_data(self.conn, {ALIAS: CANON})
        rows = self.db.list_relations()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["a_uid"], rows[0]["b_uid"]), (CANON, "friend"))
        self.assertEqual(self.db.list_relations(), self.db.list_relations())

    def test_relation_self_loop_removed(self) -> None:
        self.db.upsert_relation(ALIAS, CANON, "朋友")
        merge_uid_alias_data(self.conn, {ALIAS: CANON})
        self.assertEqual(self.db.list_relations(), [])

    def test_channels_kv_merged(self) -> None:
        self.db.kv_set("channels:%s" % ALIAS, {"lark": {"ts": 200, "umo": "lark:1"}})
        self.db.kv_set("channels:%s" % CANON, {"qq": {"ts": 100, "umo": "qq:1"}})
        merge_uid_alias_data(self.conn, {ALIAS: CANON})
        merged = self.db.kv_get("channels:%s" % CANON, {})
        self.assertEqual(set(merged), {"qq", "lark"})
        self.assertIsNone(self.db.kv_get("channels:%s" % ALIAS))

    def test_other_kv_keys_renamed(self) -> None:
        self.db.kv_set("greeting_last:" + ALIAS, "2026-01-01")
        merge_uid_alias_data(self.conn, {ALIAS: CANON})
        self.assertEqual(self.db.kv_get("greeting_last:" + CANON), "2026-01-01")
        self.assertIsNone(self.db.kv_get("greeting_last:" + ALIAS))

    def test_same_uid_is_skipped(self) -> None:
        report = merge_uid_alias_data(self.conn, {CANON: CANON})
        self.assertEqual(report["profiles"], 0)

    def test_missing_tables_report_zero(self) -> None:
        import sqlite3

        conn = sqlite3.connect(":memory:")
        self.assertEqual(merge_uid_alias_data(conn, {ALIAS: CANON})["profiles"], 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
