# -*- coding: utf-8 -*-
"""日记 digest 端到端：正文带 digest、落库后清 notes、失败保留。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_schedule import ScheduleMixin  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def schedule_version(self, day):
        return 0


class FakeState:
    def mood_score(self):
        return 0


class Host(ScheduleMixin):
    def __init__(self, root, text="今天日记正文"):
        self.db = FakeDB()
        self.state = FakeState()
        self._root = root
        self._text = text
        self.generated = 0

    def _diary_dir(self):
        return self._root

    def _diary_existing_text(self, day):
        return ""

    def diary_fact_mismatch(self, day, text):
        return True

    def _daily_diary_rewrites(self, day):
        return 0

    def _bump_daily_diary_rewrite(self, day):
        return None

    async def _generate_daily_diary(self, day):
        self.generated += 1
        return self._text

    def _finalize_live_diary(self, day_text, now=None):
        return None


def _fake_write(root, day, text):
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "diary.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class DiaryDigestE2ETests(unittest.TestCase):
    def setUp(self):
        import amo_schedule
        self._old_write = amo_schedule._diary_write
        amo_schedule._diary_write = _fake_write

    def tearDown(self):
        import amo_schedule
        amo_schedule._diary_write = self._old_write

    def test_digest_written_and_notes_cleared(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            day = dt.date(2026, 11, 21)
            host.db.kv_set("episodic_memory", [
                {"key": "shop", "text": "试了开衫 380", "until": 9999999999,
                 "day": "2026-11-21"}])
            host.db.kv_set("diary_notes:2026-11-21", ["守护事件片段"])
            asyncio.run(host._write_one_daily_diary(day, dt.datetime(2026, 11, 22, 8, 0)))
            files = [name for name in os.listdir(root)
                     if os.path.isfile(os.path.join(root, name))]
            self.assertTrue(files, os.listdir(root))
            body = open(os.path.join(root, files[0]), encoding="utf-8").read()
            self.assertIn("试了开衫 380", body)
            self.assertEqual([], host.db.kv_get("diary_notes:2026-11-21"))
            self.assertEqual([], host.db.kv_get("episodic_memory"))

    def test_failure_keeps_notes_and_buffer(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root, text="")
            day = dt.date(2026, 11, 21)
            host.db.kv_set("episodic_memory", [
                {"key": "shop", "text": "试了开衫 380", "until": 9999999999,
                 "day": "2026-11-21"}])
            host.db.kv_set("diary_notes:2026-11-21", ["守护事件片段"])
            asyncio.run(host._write_one_daily_diary(day, dt.datetime(2026, 11, 22, 8, 0)))
            self.assertEqual(["守护事件片段"],
                             host.db.kv_get("diary_notes:2026-11-21"))
            self.assertTrue(host.db.kv_get("episodic_memory"))


class _SeedDB:
    def __init__(self):
        self.kv = {}
        self.schedule = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return list(self.schedule.get(day) or [])

    def schedule_version(self, day):
        return 0


class _SeedState:
    def __init__(self, day, rows):
        self.schedule = {day: list(rows or [])}
        self.weather = {}
        self.health = {"status": "健康", "sick": False}

    def health_info(self):
        return dict(self.health)


class _SeedWallet:
    def ledger(self, limit=200):
        return []


class SeedHost(ScheduleMixin):
    def __init__(self, day, rows):
        self.db = _SeedDB()
        self.db.schedule[day] = list(rows or [])
        self.state = _SeedState(day, rows)
        self.wallet = _SeedWallet()
        self.wardrobe = None
        self._last_umo = "test:private:1"

    def cfg_str(self, key, default=""):
        return default

    def cfg_int(self, key, default=0):
        return default

    def cfg_bool(self, key, default=False):
        return bool(default)


class DayDigestSeedTests(unittest.TestCase):
    """P0（2026-11-22）：大日记定稿后要把当天事实折成 day_digest，并且真的调用
    生产写入方 note_recent_topic（不能只从 episodic_memory 读、没人写）。"""

    DAY = "2026-11-17"
    ROWS = [
        {"start": "09:00", "end": "12:00", "activity": "上班", "place": "公司",
         "category": "work", "zone": "work"},
        {"start": "12:00", "end": "13:00", "activity": "在公司食堂吃午饭", "place": "公司",
         "category": "meal", "zone": "work"},
        {"start": "20:00", "end": "22:00", "activity": "看剧", "place": "家",
         "category": "rest", "zone": "home"},
    ]

    def _host(self):
        import amo_feature_api
        import types
        host = SeedHost(self.DAY, self.ROWS)
        host.note_recent_topic = types.MethodType(
            amo_feature_api.FeatureApiMixin.note_recent_topic, host)
        return host

    def test_seed_day_digest_writes_digest_with_day_attribution(self):
        import amo_schedule
        host = self._host()
        asyncio.run(amo_schedule._seed_day_digest(host, dt.date.fromisoformat(self.DAY)))
        digest = host.db.kv_get("day_digest:" + self.DAY, "")
        self.assertTrue(digest, host.db.kv)
        self.assertIn("公司", digest)
        # 折完后当天条目从缓冲里清掉（fold_day 消费），不无限堆
        self.assertEqual([], host.db.kv_get("episodic_memory", []))

    def test_seed_day_digest_merges_without_writer(self):
        # 没有 note_recent_topic（旧宿主/测试替身）也要能写，口径一致
        import amo_schedule
        host = SeedHost(self.DAY, self.ROWS)
        asyncio.run(amo_schedule._seed_day_digest(host, dt.date.fromisoformat(self.DAY)))
        self.assertTrue(host.db.kv_get("day_digest:" + self.DAY, ""))

    def test_seed_day_digest_is_idempotent(self):
        import amo_schedule
        host = self._host()
        host.db.kv_set("day_digest:" + self.DAY, "已有的 digest")
        asyncio.run(amo_schedule._seed_day_digest(host, dt.date.fromisoformat(self.DAY)))
        self.assertEqual("已有的 digest", host.db.kv_get("day_digest:" + self.DAY))
        self.assertEqual([], host.db.kv_get("episodic_memory", []))


if __name__ == "__main__":
    unittest.main()
