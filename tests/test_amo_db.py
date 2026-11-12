# -*- coding: utf-8 -*-
"""AmoDB 存储层：kv / 画像 / 关系网 / 消息 / 日程 / 日记 / 统计。

以前这些方法只在别的行为测试里顺带跑过一部分，SQL 写错了也不容易发现
（比如 purge 条件、fade 只留骨架、关系对归一）。这里用临时库逐条跑。
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_db import AmoDB  # noqa: E402
from amo_shared import _now, now_ts, today_str  # noqa: E402

QQ = "大肥鱼:FriendMessage:302578963"
LARK = "lark:FriendMessage:ou_x"


class AmoDBTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="amo-db-")
        self._saved = {}
        # 装了 AstrBot 的环境里 AmoDB 会写真实 plugin_data；测试必须挡住
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

    def tearDown(self) -> None:
        try:
            self.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_db_lives_under_tmp(self) -> None:
        self.assertTrue(os.path.abspath(self.db.path).startswith(os.path.abspath(self.tmp)))

    def test_kv_roundtrip_and_default(self) -> None:
        self.assertEqual(self.db.kv_get("missing", "默认"), "默认")
        self.db.kv_set("mood", {"开心": 66})
        self.assertEqual(self.db.kv_get("mood", {}), {"开心": 66})
        self.db.kv_set("mood", {"开心": 70})
        self.assertEqual(self.db.kv_get("mood")["开心"], 70)

    def test_touch_profile_insert_and_update(self) -> None:
        created = self.db.touch_profile("1", "阿明", "qq", QQ, "", True)
        self.assertEqual(created["name"], "阿明")
        self.assertEqual(created["private_umo"], QQ)
        updated = self.db.touch_profile("1", "", "qq", LARK, "", True)
        self.assertEqual(updated["msg_count"], 2)
        self.assertEqual(updated["name"], "阿明", "没名字时不该把已有名字清掉")
        self.assertEqual(updated["private_umo"], LARK)

    def test_touch_profile_group_keeps_private_umo(self) -> None:
        self.db.touch_profile("1", "阿明", "qq", QQ, "", True)
        row = self.db.touch_profile("1", "阿明", "qq", "群消息", "g1", False)
        self.assertEqual(row["private_umo"], QQ)
        self.assertEqual(row["last_group"], "群消息")

    def test_update_profile_fields(self) -> None:
        self.db.touch_profile("1", "阿明", "qq", QQ, "", True)
        self.assertTrue(self.db.update_profile("1", relation="相恋", alias="明明", unknown=1))
        row = self.db.get_profile("1")
        self.assertEqual(row["relation"], "相恋")
        self.assertEqual(row["alias"], "明明")
        self.assertGreater(row["relation_at"], 0)
        self.assertFalse(self.db.update_profile("1", nope="x"))

    def test_update_profile_without_local_mark(self) -> None:
        self.db.touch_profile("1", "阿明", "qq", QQ, "", True)
        self.db.update_profile("1", mark_local=False, notes="远端拉的")
        self.assertEqual(self.db.get_profile("1")["profile_updated_at"], 0)

    def test_delete_and_list_profiles(self) -> None:
        self.db.touch_profile("1", "阿明", "qq", QQ, "", True)
        self.db.touch_profile("2", "小红", "lark", LARK, "", True)
        self.assertEqual(len(self.db.list_profiles()), 2)
        self.assertEqual([row["uid"] for row in self.db.list_profiles("小红")], ["2"])
        self.db.delete_profile("1")
        self.assertEqual(self.db.get_profile("1"), {})

    def test_relations_are_normalized(self) -> None:
        self.assertTrue(self.db.upsert_relation("b", "a", "朋友", "备注"))
        rows = self.db.relations_for("a")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["a_uid"], rows[0]["b_uid"]), ("a", "b"))
        self.assertTrue(self.db.upsert_relation("a", "b", "相恋"))
        self.assertEqual(self.db.relations_for("b")[0]["relation"], "相恋")
        self.assertFalse(self.db.upsert_relation("a", "a", "朋友"))
        self.assertFalse(self.db.upsert_relation("", "b", "朋友"))
        self.assertTrue(self.db.delete_relation("a", "b"))
        self.assertEqual(self.db.list_relations(), [])

    def test_proactive_profiles_filters_hostile(self) -> None:
        self.db.touch_profile("1", "阿明", "qq", QQ, "", True)
        self.db.touch_profile("2", "仇人", "qq", "qq:2", "", True)
        self.db.update_profile("2", relation="仇恨")
        self.db.touch_profile("3", "群友", "qq", "群", "g", False)
        uids = [row["uid"] for row in self.db.proactive_profiles()]
        self.assertEqual(uids, ["1"])

    def test_bump_proactive_resets_on_new_day(self) -> None:
        self.db.touch_profile("1", "阿明", "qq", QQ, "", True)
        self.db.bump_proactive("1")
        self.db.bump_proactive("1")
        self.assertEqual(self.db.get_profile("1")["proactive_count"], 2)
        self.db.conn.execute(
            "UPDATE profiles SET proactive_day=? WHERE uid='1'", ("2000-01-01",)
        )
        self.db.conn.commit()
        self.db.bump_proactive("1")
        self.assertEqual(self.db.get_profile("1")["proactive_count"], 1)
        self.db.bump_proactive("缺人")  # 不存在也不该报错

    def test_log_proactive(self) -> None:
        self.db.log_proactive("1", "meal", "吃饭没")
        row = self.db.conn.execute("SELECT * FROM proactive_log").fetchone()
        self.assertEqual(row["kind"], "meal")

    def test_messages_roundtrip(self) -> None:
        self.db.add_message(QQ, "1", "阿明", "user", "在吗")
        self.db.add_message(QQ, "bot", "", "bot", "在呀")
        self.db.add_message(QQ, "1", "阿明", "user", "")
        rows = self.db.recent_messages(QQ)
        self.assertEqual([row["content"] for row in rows], ["在吗", "在呀"])
        self.assertEqual(self.db.recent_messages(QQ, limit=1)[0]["content"], "在呀")
        self.assertEqual(len(self.db.messages_by_uid("1")), 1)

    def test_chat_history_merges_conversations(self) -> None:
        self.db.add_message(QQ, "1", "阿明", "user", "QQ 说的")
        self.db.add_message(LARK, "1", "阿明", "user", "飞书说的")
        self.db.add_message(LARK, "bot", "", "bot", "回一句")
        texts = [row["content"] for row in self.db.chat_history("1")]
        self.assertIn("QQ 说的", texts)
        self.assertIn("飞书说的", texts)
        self.assertIn("回一句", texts)

    def test_chat_history_without_umo(self) -> None:
        self.db.add_message("", "9", "", "user", "没会话的记录")
        self.assertEqual([row["content"] for row in self.db.chat_history("9")], ["没会话的记录"])

    def test_interaction_stats(self) -> None:
        self.db.add_message(QQ, "1", "阿明", "user", "一")
        self.db.add_message(QQ, "1", "阿明", "user", "二")
        self.db.add_message(QQ, "bot", "", "bot", "机器人")
        rows = self.db.interaction_stats()
        self.assertEqual(rows[0]["uid"], "1")
        self.assertEqual(rows[0]["c"], 2)
        self.assertEqual(self.db.interaction_stats(days=0)[0]["c"], 2)

    def test_active_users(self) -> None:
        self.db.add_message(QQ, "1", "阿明", "user", "在吗")
        rows = self.db.active_users(0)
        self.assertEqual(rows[0]["uid"], "1")

    def test_purge_messages(self) -> None:
        self.db.add_message(QQ, "1", "阿明", "user", "老的")
        self.db.conn.execute("UPDATE messages SET created_at=?", (now_ts() - 40 * 86400,))
        self.db.conn.commit()
        self.db.purge_messages(days=14)
        self.assertEqual(self.db.recent_messages(QQ), [])

    def test_schedule_roundtrip_and_fade(self) -> None:
        old_day = (_now().date() - timedelta(days=10)).isoformat()
        self.db.save_schedule(old_day, [{"hour": 9, "detail": "开会", "activity": "开会"}])
        self.db.save_schedule(today_str(), [{"hour": 9, "activity": "上班"}])
        self.db.fade_schedules(days=7)
        faded = self.db.get_schedule(old_day)
        self.assertNotIn("detail", faded[0])
        self.assertEqual(faded[0]["activity"], "开会")
        self.db.purge_schedules(days=5)
        self.assertIsNone(self.db.get_schedule(old_day))
        self.assertIsNotNone(self.db.get_schedule(today_str()))

    def test_diaries(self) -> None:
        day = today_str()
        self.db.save_diary(day, 9, "上午")
        self.db.save_diary(day, 10, "十点")
        self.assertEqual(self.db.get_diary(day, 9), "上午")
        self.db.clear_diaries_from(day, 10)
        self.assertEqual(self.db.get_diary(day, 10), "")
        self.assertEqual(self.db.get_diary(day, 9), "上午")
        self.db.clear_diaries(day)
        self.assertEqual(self.db.get_diary(day, 9), "")
        old = (date.today() - timedelta(days=60)).isoformat()
        self.db.save_diary(old, 9, "很旧")
        self.db.purge_diaries(days=30)
        self.assertEqual(self.db.get_diary(old, 9), "")

    def test_counts(self) -> None:
        self.db.touch_profile("1", "阿明", "qq", QQ, "", True)
        self.db.add_message(QQ, "1", "阿明", "user", "在吗")
        self.assertEqual(self.db.counts(), {"profiles": 1, "memories": 0, "messages": 1})

    def test_conn_reopens_after_close(self) -> None:
        self.db.conn.close()
        self.db._conn = None
        self.assertEqual(self.db.kv_get("missing", 1), 1)


if __name__ == "__main__":
    unittest.main()
