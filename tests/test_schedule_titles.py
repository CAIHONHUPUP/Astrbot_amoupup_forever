# -*- coding: utf-8 -*-
"""日程组标题：GET 只贴缓存，缺标题时后台补（2026-11-17 修复标题丢失）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import json  # noqa: E402

import amo_human_state as hs  # noqa: E402


class FakeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


def make_state(cache):
    holder = type("Holder", (), {})()
    holder.db = FakeDB({"schedule_block_summaries": cache})
    return holder


def block(start, facts, summary=None):
    row = {"start": start, "facts": facts}
    if summary:
        row["summary"] = summary
    return row


FACTS = {"part": "上午", "start": "06:00", "end": "12:00",
         "events": [{"time": "09:00-10:00", "activity": "上班",
                      "place": "公司", "category": "work"}]}


class ApplySummaryTests(unittest.TestCase):
    def test_cache_hit_fills_summary(self):
        tree = [block("06:00", FACTS)]
        cache = {"2026-11-17|06:00": {
                 "facts": json.dumps(FACTS, ensure_ascii=False, sort_keys=True),
                 "summary": "埋头苦干"}}
        hs.HumanState.apply_schedule_summaries(make_state(cache), "2026-11-17", tree)
        self.assertEqual("埋头苦干", tree[0]["summary"])

    def test_facts_mismatch_is_ignored(self):
        tree = [block("06:00", FACTS)]
        cache = {"2026-11-17|06:00": {"facts": "{}" , "summary": "过期标题"}}
        hs.HumanState.apply_schedule_summaries(make_state(cache), "2026-11-17", tree)
        self.assertNotIn("summary", tree[0])

    def test_missing_facts_or_cache_is_safe(self):
        tree = [{"start": "06:00"}, block("12:00", FACTS)]
        hs.HumanState.apply_schedule_summaries(make_state({}), "2026-11-17", tree)
        self.assertNotIn("summary", tree[0])
        self.assertNotIn("summary", tree[1])


class PanelTitleTests(unittest.TestCase):
    def test_panel_prefers_generated_summary(self):
        path = os.path.join(ROOT, "views", "dashboard", "assets", "panel.js")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("groupSummary || block.title", text)


if __name__ == "__main__":
    unittest.main()
