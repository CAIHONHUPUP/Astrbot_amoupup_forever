# -*- coding: utf-8 -*-
"""P1 内容政策单测：户外长发呆、文案地点对齐、同地点碎块合并。"""
from __future__ import annotations

import random
import unittest

import schedule_content as content
import schedule_engine_v2 as engine
import schedule_observer as observer


def _row(start, end, activity, place, zone, **extra):
    row = {"start": start, "end": end, "activity": activity, "place": place, "zone": zone}
    row.update(extra)
    return row


class AlignTests(unittest.TestCase):
    def test_road_text_at_home_is_rewritten(self):
        row = _row("17:00", "18:00", "路上刷手机放空", "家", "home")
        out = content.align_text(row)
        self.assertNotIn("路上", out["activity"])
        self.assertIn("刷手机", out["activity"])

    def test_home_text_at_office_is_rewritten(self):
        row = _row("10:00", "11:00", "在家躺着", "公司", "office")
        out = content.align_text(row)
        self.assertNotIn("在家", out["activity"])
        self.assertIn("工位", out["activity"])

    def test_normal_text_untouched(self):
        row = _row("09:00", "10:00", "吃早饭", "家", "home")
        self.assertEqual(content.align_text(row)["activity"], "吃早饭")


class OutdoorIdleTests(unittest.TestCase):
    def test_long_outdoor_idle_becomes_concrete(self):
        row = _row("14:00", "15:00", "在商场原地发呆", "商场", "mall")
        out = content.constrain_idle(row)
        self.assertNotIn("发呆", out["activity"])
        self.assertEqual(out["place"], "商场")
        self.assertEqual((out["start"], out["end"]), ("14:00", "15:00"))

    def test_short_outdoor_idle_kept(self):
        row = _row("14:00", "14:10", "在商场原地发呆", "商场", "mall")
        self.assertIn("发呆", content.constrain_idle(row)["activity"])

    def test_home_long_idle_kept(self):
        row = _row("14:00", "16:00", "在沙发上发呆", "家", "home")
        self.assertIn("发呆", content.constrain_idle(row)["activity"])

    def test_station_idle_becomes_waiting(self):
        row = _row("14:00", "15:00", "发呆", "高铁站", "station")
        self.assertIn("候车", content.constrain_idle(row)["activity"])


class MergeTests(unittest.TestCase):
    def test_adjacent_home_idle_merged(self):
        rows = [
            _row("16:46", "17:21", "原地发呆", "家", "home"),
            _row("17:21", "17:41", "在家待着", "家", "home"),
            _row("17:41", "18:00", "日常发呆", "家", "home"),
        ]
        out = content.polish(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["start"], out[0]["end"]), ("16:46", "18:00"))

    def test_merge_cap_prevents_whole_evening(self):
        rows = [
            _row("16:00", "17:20", "发呆", "家", "home"),
            _row("17:20", "18:40", "发呆", "家", "home"),
        ]
        out = content.polish(rows)
        self.assertEqual(len(out), 2)

    def test_different_place_not_merged(self):
        rows = [
            _row("16:00", "17:00", "发呆", "家", "home"),
            _row("17:00", "18:00", "发呆", "公司", "office"),
        ]
        self.assertEqual(len(content.polish(rows)), 2)

    def test_polish_idempotent(self):
        rows = [
            _row("16:46", "17:21", "原地发呆", "家", "home"),
            _row("17:21", "17:41", "在家待着", "家", "home"),
        ]
        once = content.polish(rows)
        twice = content.polish(once)
        self.assertEqual(engine.timeline_text(once), engine.timeline_text(twice))


class ScreenshotCaseTests(unittest.TestCase):
    def test_1646_case_merged_and_no_fake_return(self):
        rows = [
            _row("13:25", "16:25", "和柳如烟逛街", "商场", "mall",
                 category="social", source="watch"),
            _row("16:25", "16:46", "刚出商场，打车回家（在路上）", "路上", "road",
                 source="transit_block", category="commute"),
            _row("16:46", "17:21", "在原地发呆", "家", "home"),
            _row("17:21", "17:41", "刚出家，坐地铁回家（在路上）", "路上", "road",
                 source="transit_block", category="commute"),
            _row("17:41", "18:00", "日常发呆", "家", "home"),
        ]
        view, _ = observer.view_rows(rows, "2026-11-22", now_min=17 * 60)
        text = " ".join(str(row.get("activity") or "") for row in view)
        self.assertNotIn("刚出家", text)
        home_idle = [row for row in view if row.get("zone") == "home"
                     and "16:46" <= str(row.get("start")) <= "17:41"]
        self.assertEqual(len(home_idle), 1, view)
        self.assertEqual(home_idle[0]["end"], "18:00")


class DiaryTierTests(unittest.TestCase):
    def test_idle_blocks_are_not_informative(self):
        self.assertFalse(content.is_informative(_row("16:46", "17:21", "在原地发呆", "家", "home", category="rest")))
        self.assertFalse(content.is_informative(_row("23:00", "24:00", "睡觉", "家", "home", category="sleep")))

    def test_concrete_blocks_are_informative(self):
        self.assertTrue(content.is_informative(_row("09:00", "12:00", "上班", "公司", "office", category="work")))
        self.assertTrue(content.is_informative(_row("12:00", "13:00", "吃午饭", "公司", "office", category="meal")))
        self.assertTrue(content.is_informative(_row("19:00", "21:00", "和朋友吃饭、聊天", "饭店", "restaurant", category="social")))

    def test_spawn_diary_skips_idle_block(self):
        import amo_schedule

        class _Fake:
            _diary_tasks = {}

            def _spawn(self, coro):
                raise AssertionError("空转块不应启动日记任务")

        task = amo_schedule.ScheduleMixin._spawn_diary_task(
            _Fake(), "2026-11-22", _row("16:46", "17:21", "在原地发呆", "家", "home"), [], 1)
        self.assertIsNone(task)


class PropertyTests(unittest.TestCase):
    PLACES = (("家", "home"), ("公司", "office"), ("商场", "mall"),
              ("路边", "road"), ("餐厅", "restaurant"), ("医院", "hospital"))

    def test_thousand_random_days_have_no_long_outdoor_idle(self):
        rng = random.Random(20261122)
        problems = []
        for index in range(1000):
            rows = []
            cursor = rng.randrange(0, 360)
            for _ in range(rng.randint(3, 10)):
                duration = rng.choice((5, 10, 20, 30, 45, 60, 90))
                place, zone = rng.choice(self.PLACES)
                activity = rng.choice(("发呆", "放空", "待着", "吃饭", "走路",
                                       "路上刷手机", "在家躺着", "逛街"))
                rows.append(_row(engine._hm(cursor), engine._hm(cursor + duration),
                                 activity, place, zone))
                cursor += duration + rng.choice((0, 0, 5, 15))
                if cursor >= 24 * 60:
                    break
            view = content.polish(rows)
            spans = [engine.span_of(row) for row in view]
            if any(span is None for span in spans):
                problems.append((index, "span"))
                continue
            clean = [span for span in spans if span]
            for left, right in zip(clean, clean[1:]):
                if left[1] > right[0]:
                    problems.append((index, "overlap"))
            for row in view:
                span = engine.span_of(row)
                if not span:
                    continue
                zone = content._zone(row)
                if (content.is_idle(row) and zone not in ("home", "office", "")
                        and span[1] - span[0] >= content.OUTDOOR_IDLE_LIMIT):
                    problems.append((index, "outdoor_idle", row))
                if (zone == "home"
                        and any(word in str(row.get("activity") or "")
                                for word in content.ROAD_WORDS)):
                    problems.append((index, "road_text_home", row))
        self.assertEqual(problems[:3], [])


if __name__ == "__main__":
    unittest.main()
