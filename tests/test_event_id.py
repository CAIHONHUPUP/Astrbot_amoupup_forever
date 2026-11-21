# -*- coding: utf-8 -*-
"""事实层统一（event-id）：一个事件 id 贯穿日程 / 账本 / 块日记 / 记忆。

钉住用户确认的语义：
- 块创建时分配 event_id 并持久化，不是临时 hash；
- 编辑保留父 id；切块时父块（最早那段）保留、新块新 id；
- wallet_ledger 带同一个 event_id，旧 ref 调用不受影响；
- block_diary 的 block_id 列就是 event_id（正文逐字不动）；
- 活动记忆 tags 带 event:<id>；想记不认 tags 时只记录、不硬造；
- 旧库迁移按 day+start+end 确定性补 id，可重入，金额/正文不动。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_db_schema as dbschema  # noqa: E402
import amo_food_cost  # noqa: E402
import amo_timeline  # noqa: E402
import amo_transition  # noqa: E402
from amo_db import SCHEMA_SQL, AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_schedule import ScheduleMixin  # noqa: E402
from amo_shared import PLUGIN_NAME, today_str  # noqa: E402
from amo_wallet import Wallet  # noqa: E402


def _block(start="09:00", end="10:00", activity="吃早饭", place="家",
           category="meal", **extra):
    row = {"start": start, "end": end, "activity": activity, "place": place,
           "zone": "home", "category": category}
    row.update(extra)
    return row


class FakePlugin(ScheduleMixin):
    """够 HumanState / 日记 / 钱包链路跑起来的最小插件假件。"""

    def __init__(self, tmp, config=None):
        self.config = dict(config or {})
        self.db = AmoDB(tmp)
        self.db.init()
        self.state = None
        self.llm = None
        self.wardrobe = None
        self.photo = None
        self._last_umo = ""
        self._diary_locks = {}
        self._diary_tasks = {}
        self._spawned = []
        self.spawned_coros = []
        self.wallet = Wallet(self.db)
        self.wallet.ensure_schema()
        self.owner_uid = "owner-1"

    def _spawn(self, coro):
        # 测试里不跑事件循环：把协程收起来，用例自己 asyncio.run。
        self.spawned_coros.append(coro)
        return None

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def _diary_lock(self, day, start):
        key = (str(day), str(start))
        lock = self._diary_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._diary_locks[key] = lock
        return lock


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-eventid-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.day = today_str()
        self.plugin = FakePlugin(self.tmp)
        self.state = HumanState(self.plugin)
        self.plugin.state = self.state
        self.plugin.wallet.credit(100000, "测试初始化", "eventid:test:init")

    def tearDown(self):
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, items, old_items=None, source="chat"):
        return self.state.commit_schedule_change(
            self.day, items, old_items=old_items, source=source, reason="测试")

    def now_at(self, hour, minute=0):
        return datetime.datetime.combine(
            datetime.date.fromisoformat(today_str()), datetime.time(hour, minute))

    def saved_rows(self):
        return self.plugin.db.get_schedule(self.day) or []


class EventIdChainTests(BaseCase):
    def test_create_persists_event_id_and_survives_reload(self):
        self.commit([_block()], old_items=[])
        saved = self.saved_rows()[0]
        self.assertTrue(saved["event_id"].startswith("ev_"), saved)
        fresh = AmoDB(self.tmp)
        try:
            fresh.init()
            self.assertEqual(fresh.get_schedule(self.day)[0]["event_id"],
                             saved["event_id"])
        finally:
            fresh.close()

    def test_full_chain_schedule_wallet_diary_memory_shares_one_id(self):
        block = _block(activity="和朋友逛街", place="商场", category="social",
                       zone="mall", cost_cents=1200)
        self.commit([block], old_items=[])
        saved = self.saved_rows()[0]
        eid = saved["event_id"]
        now = self.now_at(11, 0)

        items = self.state.settle_schedule_costs(now=now)
        self.assertTrue(items[0].get("cost_settled"))
        spend = self.plugin.wallet.ledger(5)[0]
        self.assertEqual(spend["event_id"], eid, "账本要带同一个事件 id")
        self.assertEqual(spend["delta_cents"], -1200)

        bid = amo_timeline.block_id(self.day, saved)
        self.assertEqual(bid, eid, "块日记的键就是事件 id")
        text = "和朋友在商场逛了一下午，买了两件衣服，还喝了杯奶茶，心情很好。"
        self.plugin.db.save_block_diary(self.day, bid, text,
                                        start=saved["start"], end=saved["end"])
        self.assertEqual(self.plugin.db.get_block_diary(self.day, eid), text)
        self.assertEqual(self.plugin.db.list_block_diaries(self.day)[0]["block_id"], eid)

        seen = {}

        async def writer(uid, content, kind="fact", tier=1, scope="private",
                         group_id="", subject="user", platform="", idem_key="",
                         importance=None, tags=""):
            seen.update(tags=tags, idem=idem_key, content=content)
            return True

        self.plugin._data_memory_write = writer
        written = amo_transition.write_activity_memory(
            self.plugin, [dict(saved, truncated=True)], now=now)
        self.assertTrue(written)
        for coro in self.plugin.spawned_coros:
            asyncio.run(coro)
        self.assertIn("event:%s" % eid, seen.get("tags", ""),
                      "长期记忆 tags 要带同一个事件 id")

    def test_activity_memory_without_tags_support_reports_instead_of_faking(self):
        block = _block(start="08:00", end="09:00", activity="和朋友逛街",
                       place="商场", category="social", zone="mall")
        self.commit([block], old_items=[])
        saved = self.saved_rows()[0]
        eid = saved["event_id"]

        async def old_writer(uid, content, kind="fact"):  # 旧想记：不认 tags/idem
            return True

        self.plugin._data_memory_write = old_writer
        amo_transition.write_activity_memory(
            self.plugin, [dict(saved, truncated=True)], now=self.now_at(11, 0))
        for coro in self.plugin.spawned_coros:
            asyncio.run(coro)
        lost = self.plugin.db.kv_get(amo_transition.ACTIVITY_MEM_TAG_LOST_KEY, {}) or {}
        self.assertIn(eid, lost.values(), "不认 tags 时要把事件 id 留档")
        self.assertGreaterEqual(
            int(self.plugin.db.kv_get(amo_transition.ACTIVITY_MEM_FALLBACK_KEY, 0) or 0), 1)


class EventIdEditSplitTests(BaseCase):
    def test_edit_keeps_parent_event_id(self):
        base = _block(activity="在医院排队", place="医院", category="health",
                      zone="hospital")
        self.commit([base], old_items=[])
        saved = self.saved_rows()[0]
        edited = dict(saved, activity="在商场逛街", place="商场", category="shopping",
                      zone="mall")
        self.commit([edited], old_items=[saved])
        rows = self.saved_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], saved["event_id"], "编辑必须保留父 id")
        self.assertEqual(rows[0]["activity"], "在商场逛街")

    def test_split_parent_keeps_id_and_new_piece_gets_new_id(self):
        parent = _block(start="14:00", end="16:00", activity="在商场逛街",
                        place="商场", category="shopping", zone="mall")
        self.commit([parent], old_items=[])
        saved = self.saved_rows()[0]
        first = dict(saved, start="14:00", end="15:00")
        second = dict(saved, start="15:00", end="16:00")
        self.commit([first, second], old_items=[saved])
        rows = self.saved_rows()
        self.assertEqual(len(rows), 2, rows)
        ids = [row["event_id"] for row in rows]
        self.assertEqual(ids[0], saved["event_id"], "父块（最早那段）保留原 id")
        self.assertNotEqual(ids[1], saved["event_id"])
        self.assertTrue(ids[1].startswith("ev_"))

    def test_new_block_gets_new_id_and_old_keeps_its_own(self):
        a = _block(start="09:00", end="10:00", activity="吃早饭", place="家")
        self.commit([a], old_items=[])
        saved = self.saved_rows()[0]
        b = _block(start="20:00", end="21:00", activity="看电影", place="影院",
                   category="play", zone="cinema")
        self.commit([dict(saved), b], old_items=[saved])
        rows = self.saved_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["event_id"], saved["event_id"])
        self.assertTrue(rows[1]["event_id"].startswith("ev_"))
        self.assertNotEqual(rows[1]["event_id"], saved["event_id"])

    def test_identical_recommit_does_not_bump_version_or_change_ids(self):
        self.commit([_block()], old_items=[])
        saved = self.saved_rows()[0]
        version = self.plugin.db.schedule_version(self.day)
        before = self.plugin.db.get_block_diary(self.day, saved["event_id"])
        result = self.commit([dict(saved)], old_items=[saved])
        self.assertFalse(result["changed"])
        self.assertEqual(self.plugin.db.schedule_version(self.day), version)
        self.assertEqual(self.saved_rows()[0]["event_id"], saved["event_id"])
        self.assertEqual(self.plugin.db.get_block_diary(self.day, saved["event_id"]), before)

    def test_food_split_keeps_parent_id_on_earliest_piece(self):
        parent = _block(start="22:00", end="23:00", activity="休息", place="家",
                        category="sleep")
        self.commit([parent], old_items=[])
        saved = self.saved_rows()[0]
        result = amo_food_cost.ensure_block_window(
            self.plugin, self.day, "22:10", "22:30", "吃烧烤（宵夜）")
        self.assertTrue(result.get("ok"), result)
        rows = self.saved_rows()
        self.assertEqual(3, len(rows), rows)
        self.assertEqual(saved["event_id"], rows[0]["event_id"],
                         "父 id 必须落在最早那段（显式 split）")
        self.assertEqual(result["block_id"], rows[1]["event_id"],
                         "返回的新块 id 就是它的 event_id（旧账 ref 可直接用）")
        self.assertEqual(3, len({row["event_id"] for row in rows}), rows)

    def test_food_split_follows_current_place(self):
        """R13：宵夜块跟随当前地点（出差酒店），不再写死「家」。"""
        parent = _block(start="21:00", end="24:00", activity="在酒店休息",
                        place="内蒙古自治区·呼和浩特·酒店", zone="hotel",
                        category="rest")
        self.commit([parent], old_items=[])
        result = amo_food_cost.ensure_block_window(
            self.plugin, self.day, "22:10", "22:30", "吃烧烤（宵夜）")
        self.assertTrue(result.get("ok"), result)
        rows = self.saved_rows()
        snack = [row for row in rows if "宵夜" in str(row.get("activity") or "")]
        self.assertEqual(1, len(snack), rows)
        self.assertEqual("内蒙古自治区·呼和浩特·酒店", snack[0]["place"])
        self.assertEqual("hotel", snack[0]["zone"])
        self.assertEqual("吃烧烤（宵夜）", snack[0]["activity"])

    def test_food_on_road_keeps_commute_in_one_piece(self):
        """R13：通勤途中吃宵夜，不把通勤块劈成两截，并进原块、地点仍是「路上」。"""
        parent = _block(start="21:40", end="22:30", activity="坐地铁回家", place="路上",
                        zone="road", category="commute", source="transit_block")
        self.commit([parent], old_items=[])
        result = amo_food_cost.ensure_block_window(
            self.plugin, self.day, "22:10", "22:30", "吃烧烤（宵夜）")
        self.assertTrue(result.get("ok"), result)
        rows = self.saved_rows()
        self.assertEqual(1, len(rows), rows)
        self.assertEqual("路上", rows[0]["place"])
        self.assertEqual("road", rows[0]["zone"])
        self.assertTrue(str(rows[0]["activity"]).strip())

    def test_commit_keeps_restaurant_and_playground_places(self):
        """R1/R14：落库归一不再把商场餐厅折叠成「楼下小馆」、游乐园折成「家」。"""
        rows = [
            _block(start="12:00", end="13:00", activity="在商场里吃午饭",
                   place="东莞·商场·餐厅", category="meal", zone="restaurant"),
            _block(start="14:00", end="16:00", activity="在园里玩过山车",
                   place="东莞·游乐园", category="play", zone="playground"),
        ]
        self.commit(rows, old_items=[])
        saved = {row["start"]: row for row in self.saved_rows()}
        self.assertEqual("东莞·商场·餐厅", saved["12:00"]["place"])
        self.assertEqual("东莞·游乐园", saved["14:00"]["place"])


class IdentityContractTests(unittest.TestCase):
    def test_block_id_prefers_event_id_else_legacy_hash(self):
        block = _block(start="16:07", end="16:56", activity="在医院排队", place="医院")
        legacy = amo_timeline.block_id("2026-11-16", block)
        self.assertEqual(legacy, amo_timeline.legacy_block_id("2026-11-16", block))
        self.assertEqual(len(legacy), 16)
        explicit = amo_timeline.block_id(
            "2026-11-16", dict(block, event_id="ev_0123456789abcdef"))
        self.assertEqual(explicit, "ev_0123456789abcdef")

    def test_mint_is_deterministic_from_day_start_end(self):
        item = {"start": "09:00", "end": "10:00"}
        first = amo_timeline.mint_event_id("2026-11-16", item)
        self.assertEqual(first, amo_timeline.mint_event_id(
            "2026-11-16", dict(item, activity="换文案", place="换地点")))
        self.assertNotEqual(first, amo_timeline.mint_event_id(
            "2026-11-16", {"start": "09:00", "end": "11:00"}))
        self.assertNotEqual(first, amo_timeline.mint_event_id("2026-11-17", item))
        self.assertTrue(first.startswith("ev_"))
        self.assertEqual(len(first), 19)
        self.assertEqual(len(amo_timeline.new_event_id()), 19)


class WalletEventIdTests(BaseCase):
    def test_duplicate_settlement_charges_once_same_event_id(self):
        block = _block(cost_cents=2500, activity="买菜", category="meal")
        self.commit([block], old_items=[])
        saved = self.saved_rows()[0]
        now = self.now_at(11, 0)
        self.state.settle_schedule_costs(now=now)
        self.state.settle_schedule_costs(now=now)
        spend = [row for row in self.plugin.wallet.ledger(10)
                 if int(row["delta_cents"]) < 0]
        self.assertEqual(len(spend), 1, spend)
        self.assertEqual(spend[0]["event_id"], saved["event_id"])

        # 模拟进程重启丢了 cost_settled 标记：钱包幂等键仍然兜住
        item = self.state.schedule[self.day][0]
        item["cost_settled"] = False
        item.pop("cost_result", None)
        self.plugin.db.kv_set(
            "cost_settled:" + ("sch_%s_%s_%s" % (self.day, "09:00", "meal")), False)
        self.state.settle_schedule_costs(now=now)
        spend = [row for row in self.plugin.wallet.ledger(10)
                 if int(row["delta_cents"]) < 0]
        self.assertEqual(len(spend), 1, "标记丢了也不能扣第二笔")

    def test_legacy_ref_and_event_id_api_coexist(self):
        self.assertTrue(self.plugin.wallet.debit(
            300, "旧调用", "legacy:debit:1", ref="legacy-ref", category="餐饮"))
        row = self.plugin.wallet.ledger(1)[0]
        self.assertEqual(row["ref"], "legacy-ref")
        self.assertEqual(row["event_id"], "")
        self.assertTrue(self.plugin.wallet.credit(
            100, "退款", "legacy:credit:1", event_id="ev_manual0000000000"))
        row = self.plugin.wallet.ledger(1)[0]
        self.assertEqual(row["event_id"], "ev_manual0000000000")


class EventIdMigrationTests(unittest.TestCase):
    DAY = "2026-11-10"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-eventid-mig-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        dbschema._LAST_ERROR = None
        probe = AmoDB(self.tmp)
        self.db_path = probe.path
        probe.close()
        try:
            os.remove(self.db_path)
        except OSError:
            pass
        self.block = _block(start="16:07", end="16:56",
                            activity="在医院排队", place="医院", category="health")
        self.legacy_key = amo_timeline.legacy_block_id(self.DAY, self.block)
        self._make_legacy_db()

    def tearDown(self):
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_legacy_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "CREATE TABLE wallet_ledger (idem TEXT PRIMARY KEY, account TEXT NOT NULL,"
                " delta_cents INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '',"
                " ref TEXT NOT NULL DEFAULT '', ts REAL NOT NULL)")
            conn.execute(
                "CREATE TABLE wallet_account (account TEXT PRIMARY KEY,"
                " balance_cents INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0)")
            conn.execute("INSERT INTO schedule(day, items, updated) VALUES(?,?,?)",
                         (self.DAY, json.dumps([self.block], ensure_ascii=False), 0))
            conn.execute(
                "INSERT INTO block_diary(day, block_id, start, end, place, text, updated,"
                " version, status, invalidated_at, source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (self.DAY, self.legacy_key, self.block["start"], self.block["end"],
                 self.block["place"], "旧块日记正文，迁移不许改写一个字。", 0, 0,
                 "valid", 0, "migration"))
            conn.execute(
                "INSERT INTO wallet_ledger(idem, account, delta_cents, reason, ref, ts)"
                " VALUES(?,?,?,?,?,?)",
                ("old:ledger:1", "cash", -1200, "旧消费", "旧ref", 0))
            conn.execute(
                "INSERT INTO kv(key, value, updated) VALUES('schedule', ?, 0)",
                (json.dumps({self.DAY: [self.block]}, ensure_ascii=False),))
            conn.execute("PRAGMA user_version=2")
            conn.commit()
        finally:
            conn.close()

    def _open(self):
        db = AmoDB(self.tmp)
        db.init()
        return db

    def test_legacy_db_backfills_deterministic_event_id_and_rewrites_diary_key(self):
        db = self._open()
        try:
            self.assertEqual(dbschema.LATEST_VERSION, dbschema.user_version(db))
            rows = db.get_schedule(self.DAY)
            eid = rows[0]["event_id"]
            self.assertEqual(eid, amo_timeline.mint_event_id(self.DAY, self.block))
            self.assertNotEqual(eid, self.legacy_key)
            snapshot = db.kv_get("schedule", {}) or {}
            self.assertEqual(snapshot[self.DAY][0]["event_id"], eid,
                             "kv 快照（运行时主缓存）必须一起补 id")
            record = db.get_block_diary_record(self.DAY, eid)
            self.assertIsNotNone(record, db.list_block_diaries(self.DAY))
            self.assertEqual(record["text"], "旧块日记正文，迁移不许改写一个字。")
            self.assertIsNone(db.get_block_diary_record(self.DAY, self.legacy_key))
            cols = {row[1] for row in db.conn.execute("PRAGMA table_info(wallet_ledger)")}
            self.assertIn("event_id", cols)
            ledger = db.conn.execute(
                "SELECT delta_cents, reason, ref, event_id FROM wallet_ledger"
                " WHERE idem='old:ledger:1'").fetchone()
            self.assertEqual(int(ledger[0]), -1200, "迁移不许改金额")
            self.assertEqual(ledger[2], "旧ref", "旧 ref 原样保留")
            self.assertEqual(ledger[3], "")
        finally:
            db.close()

    def test_migration_is_idempotent_and_survives_version_rollback(self):
        db = self._open()
        try:
            first = dbschema.run(db)
            self.assertEqual([], first["applied_now"])
            rows_before = db.get_schedule(self.DAY)
            diaries_before = db.list_block_diaries(self.DAY)
            # 模拟回滚到 v2 再启动：迁移重跑，但数据必须逐字不变、也不重复
            dbschema.set_user_version(db, 2)
            report = dbschema.run(db)
            expected = [item["id"] for item in dbschema.MIGRATIONS if item["version"] > 2]
            self.assertEqual(expected, report["applied_now"])
            self.assertEqual(rows_before, db.get_schedule(self.DAY))
            self.assertEqual(diaries_before, db.list_block_diaries(self.DAY))
            self.assertEqual(dbschema.LATEST_VERSION, dbschema.user_version(db))
            ledger = db.conn.execute(
                "SELECT COUNT(*), SUM(delta_cents) FROM wallet_ledger").fetchone()
            self.assertEqual(int(ledger[0]), 1)
            self.assertEqual(int(ledger[1]), -1200)
        finally:
            db.close()

    def test_migration_keeps_conflicting_diary_row_instead_of_losing_text(self):
        # 目标键已存在时：保留旧行（孤儿但可读），绝不合并/丢正文
        conn = sqlite3.connect(self.db_path)
        try:
            target = amo_timeline.mint_event_id(self.DAY, self.block)
            conn.execute(
                "INSERT INTO block_diary(day, block_id, start, end, place, text, updated,"
                " version, status, invalidated_at, source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (self.DAY, target, self.block["start"], self.block["end"],
                 self.block["place"], "新生成正文", 0, 0, "valid", 0, ""))
            conn.execute("PRAGMA user_version=2")
            conn.commit()
        finally:
            conn.close()
        db = self._open()
        try:
            texts = {row["text"] for row in db.list_block_diaries(self.DAY)}
            self.assertIn("旧块日记正文，迁移不许改写一个字。", texts)
            self.assertIn("新生成正文", texts)
            record = db.get_block_diary_record(self.DAY, amo_timeline.mint_event_id(
                self.DAY, self.block))
            self.assertEqual(record["text"], "新生成正文")
        finally:
            db.close()

    def test_corrupted_kv_is_rebuilt_from_table_and_aligned(self):
        # 人为改坏 kv（缺 event_id + 多出一天）：迁移以表为权威整体重建
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE kv SET value=? WHERE key='schedule'", (json.dumps(
                {self.DAY: [dict(self.block, event_id="")],
                 "2099-01-01": [{"start": "01:00", "end": "02:00",
                                 "activity": "假的", "place": "别处"}]},
                ensure_ascii=False),))
            conn.commit()
        finally:
            conn.close()
        db = self._open()
        try:
            table = db.get_schedule(self.DAY)
            eid = table[0]["event_id"]
            self.assertEqual(eid, amo_timeline.mint_event_id(self.DAY, self.block))
            snapshot = db.kv_get("schedule", {}) or {}
            self.assertEqual(sorted(snapshot), [self.DAY], "kv 只保留表里有的天")
            self.assertEqual(snapshot[self.DAY], table, "kv 必须逐字等于表")
            self.assertEqual({item.get("event_id") for item in table},
                             {item.get("event_id") for item in snapshot[self.DAY]})
        finally:
            db.close()

    def test_mid_transaction_failure_leaves_table_and_kv_unchanged(self):
        conn = sqlite3.connect(self.db_path)
        try:
            before_table = conn.execute(
                "SELECT items FROM schedule WHERE day=?", (self.DAY,)).fetchone()[0]
            before_kv = conn.execute(
                "SELECT value FROM kv WHERE key='schedule'").fetchone()[0]
        finally:
            conn.close()
        original = dbschema._rebuild_schedule_kv

        def boom(conn, days):
            raise RuntimeError("kv 重建炸了")

        dbschema._rebuild_schedule_kv = boom
        try:
            db = AmoDB(self.tmp)
            db.init()
            try:
                self.assertEqual(2, dbschema.user_version(db))
                self.assertIn("kv 重建炸了", dbschema.last_error() or "")
                after_table = db.conn.execute(
                    "SELECT items FROM schedule WHERE day=?", (self.DAY,)).fetchone()[0]
                after_kv = db.conn.execute(
                    "SELECT value FROM kv WHERE key='schedule'").fetchone()[0]
                self.assertEqual(before_table, after_table, "表必须回滚到迁移前")
                self.assertEqual(before_kv, after_kv, "kv 必须回滚到迁移前")
                cols = {row[1] for row in db.conn.execute(
                    "PRAGMA table_info(wallet_ledger)")}
                self.assertNotIn("event_id", cols, "账本补列也要回滚")
                self.assertIsNotNone(
                    db.get_block_diary_record(self.DAY, self.legacy_key),
                    "块日记键也要回滚")
            finally:
                db.close()
        finally:
            dbschema._rebuild_schedule_kv = original
        # 修好后重跑：同一个确定性 id，表/kv 对齐
        db = self._open()
        try:
            self.assertEqual(dbschema.LATEST_VERSION, dbschema.user_version(db))
            row = db.get_schedule(self.DAY)[0]
            self.assertEqual(row["event_id"],
                             amo_timeline.mint_event_id(self.DAY, self.block))
            self.assertEqual((db.kv_get("schedule") or {})[self.DAY][0]["event_id"],
                             row["event_id"])
        finally:
            db.close()

    def test_misaligned_kv_fails_migration_and_keeps_version(self):
        # 表补了 id、kv 没对齐：校验必须失败，user_version 不前进、事务整体回滚
        original = dbschema._rebuild_schedule_kv

        def broken(conn, days):
            conn.execute(
                "INSERT INTO kv(key, value, updated) VALUES('schedule', ?, 0) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps({self.DAY: [self.block]}, ensure_ascii=False),))
            return 0

        dbschema._rebuild_schedule_kv = broken
        try:
            db = AmoDB(self.tmp)
            db.init()
            try:
                self.assertEqual(2, dbschema.user_version(db))
                self.assertIn("event_id 不一致", dbschema.last_error() or "")
                self.assertNotIn("event_id", db.get_schedule(self.DAY)[0])
                self.assertNotIn(
                    "event_id", (db.kv_get("schedule") or {})[self.DAY][0])
            finally:
                db.close()
        finally:
            dbschema._rebuild_schedule_kv = original

    def _add_ledger_row(self, idem, delta, reason, ref):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO wallet_ledger(idem, account, delta_cents, reason, ref, ts)"
                " VALUES(?,?,?,?,?,?)", (idem, "cash", delta, reason, ref, 0))
            conn.commit()
        finally:
            conn.close()

    # ---------- H3：block_diary 双列（v4） ----------
    def test_block_diary_has_event_id_column_and_old_rows_backfilled(self):
        db = self._open()
        try:
            cols = {row[1] for row in db.conn.execute("PRAGMA table_info(block_diary)")}
            self.assertIn("event_id", cols)
            rows = db.conn.execute(
                "SELECT block_id, event_id FROM block_diary").fetchall()
            self.assertTrue(rows)
            self.assertEqual([(row[0], row[0]) for row in rows],
                             [(row[0], row[1]) for row in rows],
                             "旧行 event_id 必须从 block_id 回填")
            self.assertEqual([], db.block_diary_identity_mismatches())
        finally:
            db.close()

    def test_old_code_reads_block_diary_by_block_id(self):
        db = self._open()
        try:
            eid = db.get_schedule(self.DAY)[0]["event_id"]
            row = db.conn.execute(
                "SELECT text, event_id FROM block_diary WHERE day=? AND block_id=?",
                (self.DAY, eid)).fetchone()
            self.assertIsNotNone(row, "旧代码按 block_id 必须仍能读到")
            self.assertEqual("旧块日记正文，迁移不许改写一个字。", row[0])
            self.assertEqual(eid, row[1])
            self.assertEqual("旧块日记正文，迁移不许改写一个字。",
                             db.get_block_diary(self.DAY, eid))
        finally:
            db.close()

    def test_new_block_diary_write_fills_both_columns(self):
        db = self._open()
        try:
            db.save_block_diary(self.DAY, "ev_0123456789abcdef", "新正文",
                                start="09:00", end="10:00", place="家")
            row = db.conn.execute(
                "SELECT block_id, event_id FROM block_diary WHERE block_id=?",
                ("ev_0123456789abcdef",)).fetchone()
            self.assertEqual(("ev_0123456789abcdef", "ev_0123456789abcdef"),
                             tuple(row))
            self.assertEqual([], db.block_diary_identity_mismatches())
            # 只给 event_id：block_id 对齐成同一个身份（两列仍相等）
            db.save_block_diary(self.DAY, "", "再来", start="11:00", end="12:00",
                                place="家", event_id="ev_fedcba9876543210")
            row = db.conn.execute(
                "SELECT block_id, event_id FROM block_diary WHERE event_id=?",
                ("ev_fedcba9876543210",)).fetchone()
            self.assertEqual(("ev_fedcba9876543210", "ev_fedcba9876543210"),
                             tuple(row))
            self.assertEqual([], db.block_diary_identity_mismatches())
        finally:
            db.close()

    def test_v4_failure_rolls_back_column_but_keeps_v3(self):
        original = dbschema._backfill_block_diary_event_ids

        def boom(conn):
            raise RuntimeError("回填炸了")

        dbschema._backfill_block_diary_event_ids = boom
        try:
            db = self._open()
            try:
                self.assertEqual(3, dbschema.user_version(db))
                self.assertIn("回填炸了", dbschema.last_error() or "")
                cols = {row[1] for row in db.conn.execute(
                    "PRAGMA table_info(block_diary)")}
                self.assertNotIn("event_id", cols, "v4 失败必须回滚补列")
                eid = db.get_schedule(self.DAY)[0]["event_id"]
                self.assertEqual(eid, amo_timeline.mint_event_id(self.DAY, self.block),
                                 "v3 已提交的日程 event_id 不能被 v4 回滚")
                self.assertIsNotNone(db.get_block_diary_record(self.DAY, eid),
                                     "旧账行仍按 block_id 可读")
            finally:
                db.close()
        finally:
            dbschema._backfill_block_diary_event_ids = original
        db = self._open()
        try:
            self.assertEqual(dbschema.LATEST_VERSION, dbschema.user_version(db))
            cols = {row[1] for row in db.conn.execute("PRAGMA table_info(block_diary)")}
            self.assertIn("event_id", cols)
            self.assertEqual([], db.block_diary_identity_mismatches())
        finally:
            db.close()

    # ---------- H4：旧账 event_id 只从可解析 ref 回填 ----------
    def test_ledger_ref_block_prefix_backfilled_others_left_empty(self):
        self._add_ledger_row("old:ledger:2", -500, "夜宵", "block:ev_aaaaaaaaaaaaaaaa")
        self._add_ledger_row("old:ledger:3", -700, "打车", "sch_2026-11-10_09:00_meal")
        self._add_ledger_row("old:ledger:4", -900, "买书", "block:")
        self._add_ledger_row("old:ledger:5", -300, "杂项", "自由文本")
        self._add_ledger_row("old:ledger:6", -200, "夜宵", "block:ev_bbbbbbbbbbbbbbbb 22:10-22:30")
        self._add_ledger_row("old:ledger:7", -400, "旧块", "block:deadbeefdeadbeef")
        db = self._open()
        try:
            rows = {row["idem"]: row for row in db.conn.execute(
                "SELECT idem, ref, event_id, delta_cents, reason FROM wallet_ledger"
            ).fetchall()}
            self.assertEqual("ev_aaaaaaaaaaaaaaaa", rows["old:ledger:2"]["event_id"])
            self.assertEqual("ev_bbbbbbbbbbbbbbbb", rows["old:ledger:6"]["event_id"],
                             "block:ev_<id> 带时段后缀也要能解析")
            for idem in ("old:ledger:1", "old:ledger:3", "old:ledger:4",
                         "old:ledger:5", "old:ledger:7"):
                self.assertEqual("", rows[idem]["event_id"], "%s 不能猜" % idem)
            self.assertEqual("sch_2026-11-10_09:00_meal", rows["old:ledger:3"]["ref"])
            self.assertEqual(-500, int(rows["old:ledger:2"]["delta_cents"]),
                             "回填不许改金额")
            self.assertEqual("夜宵", rows["old:ledger:2"]["reason"])
        finally:
            db.close()

    def test_ledger_ref_backfill_is_idempotent_and_keeps_existing_ids(self):
        db = self._open()
        try:
            db.conn.execute(
                "UPDATE wallet_ledger SET event_id='ev_manual0000000000', "
                "ref='block:ev_cccccccccccccccc' WHERE idem='old:ledger:1'")
            db.conn.commit()
            dbschema.set_user_version(db, 4)
            report = dbschema.run(db)
            self.assertEqual(["ledger_event_id_backfill"], report["applied_now"])
            row = db.conn.execute(
                "SELECT event_id FROM wallet_ledger WHERE idem='old:ledger:1'"
            ).fetchone()
            self.assertEqual("ev_manual0000000000", row[0], "已有 id 不许覆盖")
            self.assertEqual(0, dbschema._migration_ledger_event_ids(db),
                             "再跑一次没有可填的行")
            left = db.conn.execute(
                "SELECT COUNT(*) FROM wallet_ledger WHERE event_id='' "
                "AND ref LIKE 'block:%'").fetchone()
            self.assertEqual(0, int(left[0]))
        finally:
            db.close()


class ExplicitSplitTests(unittest.TestCase):
    """H5：显式切块（父 id 由调用方指定哪段继承）。"""

    DAY = "2026-11-17"

    def _parent(self, event_id="ev_0123456789abcdef"):
        return {"start": "14:00", "end": "16:00", "activity": "逛街",
                "place": "商场", "event_id": event_id}

    def test_one_parent_splits_into_many_with_explicit_parent_id(self):
        parent = self._parent()
        parts = [dict(parent, start="14:00", end="15:00"),
                 dict(parent, start="15:00", end="16:00")]
        rows, info = amo_timeline.split_schedule_block(
            self.DAY, parent, parts, inherit_index=0)
        self.assertEqual("ev_0123456789abcdef", info["parent_id"])
        self.assertEqual("ev_0123456789abcdef", rows[0]["event_id"])
        self.assertNotEqual(rows[0]["event_id"], rows[1]["event_id"])
        self.assertTrue(rows[1]["event_id"].startswith("ev_"))
        self.assertEqual(len(rows), len({row["event_id"] for row in rows}))
        self.assertEqual(1, info["dropped_parent_id"])

    def test_many_parts_collapse_to_one_parent_id(self):
        parent = self._parent()
        parts = [dict(parent, start="14:00", end="14:30"),
                 dict(parent, start="14:30", end="15:30"),
                 dict(parent, start="15:30", end="16:00")]
        rows, info = amo_timeline.split_schedule_block(
            self.DAY, parent, parts, inherit_index=1)
        self.assertEqual([False, True, False],
                         [row["event_id"] == info["parent_id"] for row in rows])
        self.assertEqual(3, len({row["event_id"] for row in rows}))

    def test_low_overlap_still_inherits_explicitly(self):
        parent = self._parent()
        # 只重叠 10 分钟（<50%）：旧启发式不会继承，显式 API 必须继承。
        parts = [dict(parent, start="15:50", end="16:30")]
        rows, info = amo_timeline.split_schedule_block(self.DAY, parent, parts)
        self.assertEqual(info["parent_id"], rows[0]["event_id"])
        self.assertEqual("ev_0123456789abcdef", rows[0]["event_id"])

    def test_parent_id_can_be_passed_explicitly_and_minted_when_missing(self):
        rows, info = amo_timeline.split_schedule_block(
            self.DAY, "ev_explicit0000000000",
            [{"start": "14:00", "end": "16:00", "activity": "x"}])
        self.assertEqual("ev_explicit0000000000", info["parent_id"])
        self.assertEqual("ev_explicit0000000000", rows[0]["event_id"])
        old = {"start": "14:00", "end": "16:00", "activity": "x"}
        _rows, info2 = amo_timeline.split_schedule_block(
            self.DAY, old, [{"start": "14:00", "end": "16:00", "activity": "x"}])
        self.assertEqual(amo_timeline.mint_event_id(self.DAY, old),
                         info2["parent_id"])


if __name__ == "__main__":
    unittest.main()