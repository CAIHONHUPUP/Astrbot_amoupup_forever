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


if __name__ == "__main__":
    unittest.main()
