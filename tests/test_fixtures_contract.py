# -*- coding: utf-8 -*-
"""Phase 11 共享测试桩契约（tests/fixtures.py + conftest fixture）。"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import clock  # noqa: E402
from fixtures import (  # noqa: E402
    DEFAULT_NOW, FakeDB, FakePlugin, FakeState, FakeWallet, FixedClock,
)


class FakeDBContractTests(unittest.TestCase):
    def test_kv_roundtrip(self):
        db = FakeDB({"a": 1})
        self.assertEqual(db.kv_get("a"), 1)
        self.assertEqual(db.kv_get("missing", 7), 7)
        db.kv_set("b", 2)
        self.assertEqual(db.kv_get("b"), 2)

    def test_kv_alias_is_the_same_dict(self):
        db = FakeDB()
        db.kv["x"] = 9
        self.assertEqual(db.data["x"], 9)
        db.data["y"] = 8
        self.assertEqual(db.kv["y"], 8)

    def test_writes_are_logged(self):
        db = FakeDB()
        db.kv_set("k", 1)
        db.kv_set("k", 2)
        self.assertEqual(db.writes, [("k", 1), ("k", 2)])

    def test_schedule_is_deep_copied(self):
        db = FakeDB()
        row = {"start": "09:00", "activity": "上班"}
        db.set_schedule("2026-11-16", [row])
        got = db.get_schedule("2026-11-16")
        got[0]["activity"] = "改了"
        self.assertEqual(db.get_schedule("2026-11-16")[0]["activity"], "上班")

    def test_kv_del(self):
        db = FakeDB({"a": 1})
        db.kv_del("a")
        self.assertIsNone(db.kv_get("a"))


class FakePluginContractTests(unittest.TestCase):
    def test_cfg_helpers(self):
        plugin = FakePlugin(config={"n": "3", "flag": True, "f": "1.5"})
        self.assertEqual(plugin.cfg_str("n"), "3")
        self.assertTrue(plugin.cfg_bool("flag"))
        self.assertEqual(plugin.cfg_int("n"), 3)
        self.assertEqual(plugin.cfg_float("f"), 1.5)
        self.assertEqual(plugin.cfg("none", "d"), "d")

    def test_keyword_args_become_config(self):
        plugin = FakePlugin(schedule_city="东莞")
        self.assertEqual(plugin.cfg_str("schedule_city"), "东莞")

    def test_db_state_wallet_attached(self):
        plugin = FakePlugin(kv={"k": 1})
        self.assertEqual(plugin.db.kv_get("k"), 1)
        self.assertIsInstance(plugin.state, FakeState)
        self.assertIsInstance(plugin.wallet, FakeWallet)

    def test_wallet_balance_and_debit(self):
        wallet = FakeWallet(500)
        self.assertEqual(wallet.balance(), 500)
        self.assertTrue(wallet.debit(100, "饭", "idem-1"))
        self.assertEqual(wallet.debits[0]["idem"], "idem-1")


class FakeStateContractTests(unittest.TestCase):
    def test_prompt_texts(self):
        state = FakeState()
        self.assertIn("在家", state.prompt_text())
        self.assertEqual(state.tone_text(), "语气温和")
        self.assertEqual(state.worldview_text(), "普通小城生活。")

    def test_period_start(self):
        state = FakeState()
        self.assertEqual(state.period_start, "2026-01-01")
        state.set_period_start("2026-02-02")
        self.assertEqual(state.period_start, "2026-02-02")


class FixedClockContractTests(unittest.TestCase):
    def test_freezes_clock_now_and_now_ts(self):
        with FixedClock(datetime(2026, 1, 2, 3, 4, 5)) as frozen:
            self.assertEqual(clock.now(), datetime(2026, 1, 2, 3, 4, 5))
            self.assertEqual(clock.now_ts(), datetime(2026, 1, 2, 3, 4, 5).timestamp())
            self.assertEqual(frozen.moment, datetime(2026, 1, 2, 3, 4, 5))

    def test_restores_original_clock(self):
        before = clock.now
        frozen = FixedClock()
        frozen.start()
        self.assertIsNot(clock.now, before)
        frozen.stop()
        self.assertIs(clock.now, before)
        self.assertLess(abs(clock.now_ts() - time.time()), 5.0)

    def test_advance_moves_moment(self):
        frozen = FixedClock(datetime(2026, 1, 1, 0, 0, 0))
        frozen.start()
        try:
            frozen.advance(90)
            self.assertEqual(clock.now(), datetime(2026, 1, 1, 0, 1, 30))
        finally:
            frozen.stop()

    def test_module_consumer_sees_frozen_clock(self):
        import amo_shared_activity
        with FixedClock(DEFAULT_NOW):
            self.assertEqual(amo_shared_activity._now(), DEFAULT_NOW)


def test_fixed_clock_fixture(fixed_clock):
    self = None  # noqa: F841 - pytest 函数式用例
    assert clock.now() == DEFAULT_NOW
    assert fixed_clock.moment == DEFAULT_NOW


def test_temp_db_fixture(temp_db):
    conn = sqlite3.connect(temp_db)
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        assert conn.execute("SELECT x FROM t").fetchone()[0] == 1
    finally:
        conn.close()
    assert os.path.isfile(temp_db)


def test_temp_dir_fixture(temp_dir):
    path = os.path.join(temp_dir, "x.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("ok")
    assert open(path, encoding="utf-8").read() == "ok"


if __name__ == "__main__":
    unittest.main()
