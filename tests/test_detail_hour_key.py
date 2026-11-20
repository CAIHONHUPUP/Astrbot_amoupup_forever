# -*- coding: utf-8 -*-
"""详情读取口径：块身份（block_id）唯一，旧整点回退链已删除。

第 1 阶段重构后，详情只按 block_diary 的块身份读：拿不到就交给块重新生成，
不再 fallback 到 legacy:<start> / schedule_diary 的旧整点行。
"""
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402

astrbot_stub.install()
import amo_api_schedule  # noqa: E402
from amo_api_schedule import _block_diary_text, _detail_block_ended  # noqa: E402
from panel_source import panel_source  # noqa: E402


class FakeDB:
    """最小 DB：块表 + 旧整点表，用来证明详情不读旧表。"""

    def __init__(self, record=None, old_text=""):
        self.record = record
        self.old_text = old_text
        self.read_keys = []

    def get_block_diary_record(self, day, block_key):
        self.read_keys.append(block_key)
        return dict(self.record) if isinstance(self.record, dict) else None

    def get_diary(self, day, key):
        return self.old_text


class FakePlugin:
    def __init__(self, db):
        self.db = db

    def cfg_bool(self, key, default=False):
        return True


def _block(start="16:07", end="16:56"):
    return {"start": start, "end": end, "activity": "在医院排队", "place": "医院",
            "category": "health", "zone": "hospital"}


class DetailPendingGateCase(unittest.TestCase):
    def test_ongoing_block_is_not_ended(self):
        now = dt.datetime(2026, 11, 16, 18, 10)
        self.assertFalse(_detail_block_ended("2026-11-16", "16:56", "18:26", now=now))

    def test_finished_block_is_ended(self):
        now = dt.datetime(2026, 11, 16, 18, 30)
        self.assertTrue(_detail_block_ended("2026-11-16", "16:56", "18:26", now=now))

    def test_past_day_is_ended(self):
        now = dt.datetime(2026, 11, 17, 0, 5)
        self.assertTrue(_detail_block_ended("2026-11-16", "16:56", "18:26", now=now))


class LegacyHourFallbackRemovedCase(unittest.TestCase):
    def test_legacy_hour_key_helpers_are_gone(self):
        self.assertFalse(hasattr(amo_api_schedule, "_diary_hour_key"))
        self.assertFalse(hasattr(amo_api_schedule, "_old_row_covers_block"))

    def test_old_hour_table_is_never_used_by_detail(self):
        db = FakeDB(record=None, old_text="旧整点日记")
        plugin = FakePlugin(db)
        self.assertEqual(_block_diary_text(plugin, "2026-11-16", _block()), "")
        self.assertEqual(len(db.read_keys), 1)
        self.assertEqual(len(db.read_keys[0]), 16, "详情只按 block_id 试探")

    def test_block_record_wins_over_old_hour_table(self):
        db = FakeDB(record={"text": "块日记", "status": "valid"}, old_text="旧整点日记")
        plugin = FakePlugin(db)
        self.assertEqual(_block_diary_text(plugin, "2026-11-16", _block()), "块日记")

    def test_stale_block_record_is_not_reused(self):
        db = FakeDB(record={"text": "时间口径歪掉的旧文", "status": "stale"})
        plugin = FakePlugin(db)
        self.assertEqual(_block_diary_text(plugin, "2026-11-16", _block()), "")


class AccordionWiringCase(unittest.TestCase):
    def test_panel_closes_other_open_hours(self):
        html = panel_source(ROOT)
        self.assertIn('querySelectorAll("#scheduleList .schedule-hour.open")', html)
        self.assertIn("本时段消费", html)


if __name__ == "__main__":
    unittest.main()
