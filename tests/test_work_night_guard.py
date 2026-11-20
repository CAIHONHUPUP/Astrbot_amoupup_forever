# -*- coding: utf-8 -*-
"""病假/夜间/工作日下班后不许去公司（2026-11-18 用户要求）。

规则：
- 病假当天：全天不许去公司（哪条写路径都一样）；
- 只有正常上班时间 09:00-18:00 能去公司：周末加班也一样，早 9 晚 6 之外折回家；
- 工作日 18:00 之后（下班回家后）：当天不许再回公司；
- 周末：白天可以加班。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from amo_human_state import HumanState  # noqa: E402
from amo_transition import _no_office_allowed, start as transition_start  # noqa: E402

WED = "2026-11-18"   # 周三
SAT = "2026-11-21"   # 周六


def _row(start, activity, place="公司", zone="office", category="work"):
    return {"start": start, "end": "23:00", "activity": activity, "place": place,
            "zone": zone, "category": category}


class NightOfficeGuardTests(unittest.TestCase):
    def test_night_office_work_folds_to_home(self):
        rows = HumanState._enforce_zone_activity([_row("21:23", "在工位上忙工作")], WED, "transit")
        self.assertEqual(rows[0]["zone"], "home")
        self.assertEqual(rows[0]["place"], "家")
        self.assertEqual(rows[0]["activity"], "在家休息")
        self.assertEqual(rows[0]["category"], "rest")
        self.assertNotIn("transit", rows[0])

    def test_weekday_evening_office_work_folds_to_home(self):
        rows = HumanState._enforce_zone_activity([_row("18:30", "在工位上忙工作")], WED, "jit")
        self.assertEqual(rows[0]["zone"], "home")

    def test_weekend_daytime_overtime_allowed(self):
        rows = HumanState._enforce_zone_activity([_row("15:00", "在工位上忙工作")], SAT, "jit")
        self.assertEqual(rows[0]["zone"], "office")
        self.assertEqual(rows[0]["activity"], "在工位上忙工作")

    def test_weekend_evening_office_folds_to_home(self):
        rows = HumanState._enforce_zone_activity([_row("18:30", "在工位上忙工作")], SAT, "jit")
        self.assertEqual(rows[0]["zone"], "home")

    def test_weekend_late_night_office_folds_to_home(self):
        rows = HumanState._enforce_zone_activity([_row("23:30", "在工位上忙工作")], SAT, "jit")
        self.assertEqual(rows[0]["zone"], "home")

    def test_sick_day_office_work_folds_to_home(self):
        rows = HumanState._enforce_zone_activity([_row("10:00", "在工位上忙工作")],
                                                 WED, "jit", sick=True)
        self.assertEqual(rows[0]["zone"], "home")
        self.assertEqual(rows[0]["activity"], "在家养病休息")

    def test_sick_day_commute_to_office_folds_to_home(self):
        rows = HumanState._enforce_zone_activity([_row("12:00", "在路上赶路", zone="road", category="commute")],
                                                 WED, "jit", sick=True)
        self.assertEqual(rows[0]["zone"], "home")
        self.assertEqual(rows[0]["place"], "家")

    def test_weekend_early_morning_overtime_refused(self):
        rows = HumanState._enforce_zone_activity([_row("08:30", "在工位上忙工作")], SAT, "jit")
        self.assertEqual(rows[0]["zone"], "home")

    def test_normal_weekday_morning_office_work_untouched(self):
        rows = HumanState._enforce_zone_activity([_row("10:00", "在工位上忙工作")], "2026-11-19", "jit")
        self.assertEqual(rows[0]["zone"], "office")
        self.assertEqual(rows[0]["activity"], "在工位上忙工作")
        self.assertEqual(rows[0]["place"], "公司")


class FakeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, leave=False):
        self.leave = leave

    def on_sick_leave(self, day=None):
        return self.leave

    def current_activity(self):
        return {}


class FakePlugin:
    def __init__(self, leave=False, kv=None):
        self.db = FakeDB(kv)
        self.state = FakeState(leave)


class TransitionOfficeGuardTests(unittest.TestCase):
    def test_night_transition_to_office_refused(self):
        now = dt.datetime(2026, 11, 18, 21, 18)
        self.assertIsNone(transition_start(FakePlugin(), "commute", to_place="公司", now=now))

    def test_weekday_evening_transition_to_office_refused(self):
        self.assertTrue(_no_office_allowed(FakePlugin(), dt.datetime(2026, 11, 18, 18, 30)))

    def test_weekend_overtime_transition_allowed(self):
        self.assertFalse(_no_office_allowed(FakePlugin(), dt.datetime(2026, 11, 21, 15, 0)))

    def test_weekend_evening_transition_to_office_refused(self):
        self.assertTrue(_no_office_allowed(FakePlugin(), dt.datetime(2026, 11, 21, 18, 30)))

    def test_weekend_late_night_transition_refused(self):
        self.assertTrue(_no_office_allowed(FakePlugin(), dt.datetime(2026, 11, 21, 23, 30)))

    def test_sick_transition_to_office_refused(self):
        now = dt.datetime(2026, 11, 18, 10, 0)
        self.assertIsNone(transition_start(FakePlugin(leave=True), "commute", to_place="公司", now=now))

    def test_sick_until_kv_blocks_transition(self):
        import time
        plugin = FakePlugin(kv={"health:sick_until": time.time() + 3600})
        self.assertTrue(_no_office_allowed(plugin, dt.datetime(2026, 11, 18, 10, 0)))

    def test_daytime_transition_to_office_still_allowed(self):
        self.assertFalse(_no_office_allowed(FakePlugin(), dt.datetime(2026, 11, 18, 10, 0)))

    def test_early_morning_transition_to_office_refused(self):
        self.assertTrue(_no_office_allowed(FakePlugin(), dt.datetime(2026, 11, 18, 5, 30)))


if __name__ == "__main__":
    unittest.main()
