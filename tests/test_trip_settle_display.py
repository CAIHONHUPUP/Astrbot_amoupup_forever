# -*- coding: utf-8 -*-
"""B1b：旅游结算「新扣 N / 补记 M」在 /status 与面板只读展示（0/单/双）。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_commands  # noqa: E402
import amo_trip_cost  # noqa: E402
from amo_route_table import ROUTE_COUNT_EXPECTED, ROUTE_SPECS  # noqa: E402

DASH = os.path.join(ROOT, "views", "dashboard")


def read_text(*parts):
    with open(os.path.join(DASH, *parts), encoding="utf-8") as fh:
        return fh.read()


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.writes = 0

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.writes += 1
        self.kv[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()
        self.config = {}


def summary(recovered=0, new=0, day="2026-11-17"):
    settled = [{"idem": "new-%d" % index, "kind": "daily", "amount": 100} for index in range(new)]
    settled += [{"idem": "rec-%d" % index, "kind": "daily", "amount": 100, "duplicate": True}
                for index in range(recovered)]
    return {"ok": True, "reason": "", "day": day, "settled": settled,
            "recovered": ["rec-%d" % index for index in range(recovered)],
            "duplicate": [], "no_money": [], "future": [], "failed": [], "trip": None}


def status_data(settle=None):
    return {
        "time": "2026-11-17 15:00",
        "mood": {"calm": 50},
        "dominant": "平静",
        "period": {"phase": "卵泡期", "day": 3, "tone": "轻快"},
        "health": {"status": "健康"},
        "weather_text": "晴 20℃",
        "activity": {"activity": "看剧", "place": "家"},
        "threshold": {"value": 50, "base": 50},
        "activity_rate": 10,
        "counts": {"profiles": 1, "memories": 0, "messages": 2},
        "data_plugin": {"name": "Astrbot_amoupup_data", "connected": True, "synced": True},
        "trip_settle": settle or {},
    }


class ZeroOneTwoTests(unittest.TestCase):
    def test_zero_settled(self):
        plugin = FakePlugin()
        report = amo_trip_cost.record_settle_report(plugin, summary(new=0, recovered=0))
        self.assertEqual((report["new"], report["recovered"]), (0, 0))
        self.assertEqual(report["line"], "新扣 0 / 补记 0")

    def test_single_new_charge(self):
        plugin = FakePlugin()
        report = amo_trip_cost.record_settle_report(plugin, summary(new=1))
        self.assertEqual((report["new"], report["recovered"]), (1, 0))
        self.assertEqual(report["line"], "新扣 1 / 补记 0")

    def test_single_recovered(self):
        plugin = FakePlugin()
        report = amo_trip_cost.record_settle_report(plugin, summary(recovered=1))
        self.assertEqual((report["new"], report["recovered"]), (0, 1))
        self.assertEqual(report["line"], "新扣 0 / 补记 1（崩溃恢复，未重复扣款）")

    def test_double_recovered(self):
        plugin = FakePlugin()
        report = amo_trip_cost.record_settle_report(plugin, summary(recovered=2))
        self.assertEqual((report["new"], report["recovered"]), (0, 2))
        self.assertEqual(report["line"], "新扣 0 / 补记 2（崩溃恢复，未重复扣款）")

    def test_mixed_new_and_recovered(self):
        plugin = FakePlugin()
        report = amo_trip_cost.record_settle_report(plugin, summary(new=1, recovered=2))
        self.assertEqual((report["new"], report["recovered"]), (1, 2))
        self.assertIn("新扣 1 / 补记 2", report["line"])
        self.assertIn("未重复扣款", report["line"])


class ReadOnlyTests(unittest.TestCase):
    def test_settle_report_read_does_not_write_wallet(self):
        plugin = FakePlugin()
        amo_trip_cost.record_settle_report(plugin, summary(new=1, recovered=1))
        writes = plugin.db.writes
        for _ in range(3):
            report = amo_trip_cost.settle_report(plugin)
            self.assertEqual(report["line"], "新扣 1 / 补记 1（崩溃恢复，未重复扣款）")
        self.assertEqual(plugin.db.writes, writes, "读取不能写库/写钱包")

    def test_report_is_idempotent(self):
        plugin = FakePlugin()
        first = amo_trip_cost.record_settle_report(plugin, summary(new=1, recovered=2))
        second = amo_trip_cost.record_settle_report(plugin, summary(new=1, recovered=2))
        self.assertEqual((first["new"], first["recovered"]), (second["new"], second["recovered"]))
        self.assertEqual(amo_trip_cost.settle_report(plugin)["line"], second["line"])

    def test_no_report_returns_empty(self):
        self.assertEqual(amo_trip_cost.settle_report(FakePlugin()), {})


class StatusDisplayTests(unittest.TestCase):
    def test_status_text_shows_settle_line(self):
        data = status_data({"day": "2026-11-17", "new": 1, "recovered": 2, "settled": 3,
                            "line": "新扣 1 / 补记 2（崩溃恢复，未重复扣款）"})
        text = amo_commands.CommandMixin._format_status(object(), data)
        self.assertIn("🧳 旅游结算：新扣 1 / 补记 2（崩溃恢复，未重复扣款）", text)

    def test_status_text_without_record_keeps_clean(self):
        text = amo_commands.CommandMixin._format_status(object(), status_data())
        self.assertNotIn("旅游结算", text)

    def test_panel_has_settle_card_and_render(self):
        index = read_text("index.html")
        panel = read_text("assets", "panel.js")
        self.assertEqual(index.count('id="tripSettle"'), 1)
        self.assertIn("tripSettle", panel)
        self.assertIn("data.trip_settle", panel)
        self.assertIn("settle.line", panel)

    def test_no_new_route_for_settle(self):
        paths = [path for path, _handler, _methods in ROUTE_SPECS]
        self.assertEqual(len(paths), ROUTE_COUNT_EXPECTED)
        self.assertEqual(len(paths), 97)
        self.assertEqual([path for path in paths if "settle" in path], [])

    def test_settle_due_records_report_after_settling(self):
        source = amo_trip_cost.settle_due.__doc__ or ""
        self.assertIn("recovered", source)
        import inspect
        code = inspect.getsource(amo_trip_cost.settle_due)
        self.assertIn("record_settle_report(plugin, summary)", code)


if __name__ == "__main__":
    unittest.main()
