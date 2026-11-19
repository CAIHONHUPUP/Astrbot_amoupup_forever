# -*- coding: utf-8 -*-
"""她说「走回工位」时，日程从吃饭改回在工位忙（2026-11-19 上下文错乱的兜底）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_schedule import ScheduleMixin, today_str  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=False):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return []


class FakeState:
    def __init__(self, items):
        self.schedule = {today_str(): items}
        self.patched = None

    def current_activity(self):
        return dict(self.schedule[today_str()][0])

    def apply_schedule(self, day, fn, source="", reason=""):
        self.patched = fn(self.schedule[day])
        self.schedule[day] = self.patched
        return {"ok": True}


class FakeHost(ScheduleMixin):
    def __init__(self, items):
        self.db = FakeDB()
        self.state = FakeState(items)


def meal_item():
    return {"start": "12:49", "end": "13:00", "activity": "在公司食堂吃午饭",
            "place": "公司", "zone": "office", "category": "meal"}


class OwnDeskSyncTests(unittest.TestCase):
    def test_back_to_desk_rewrites_meal(self):
        host = FakeHost([meal_item()])
        host._maybe_own_desk_sync("（把餐盘放回去，走回工位坐下）")
        item = host.state.schedule[today_str()][0]
        self.assertEqual(item["activity"], "在工位上忙工作")
        self.assertEqual(item["category"], "work")
        self.assertEqual(item["zone"], "office")

    def test_other_person_going_back_is_ignored(self):
        host = FakeHost([meal_item()])
        host._maybe_own_desk_sync("他回工位了")
        self.assertEqual(host.state.current_activity()["category"], "meal")

    def test_not_company_place_is_ignored(self):
        item = meal_item()
        item["place"] = "家"
        host = FakeHost([item])
        host._maybe_own_desk_sync("走回工位坐下")
        self.assertIsNone(host.state.patched)

    def test_already_working_is_ignored(self):
        item = meal_item()
        item["category"] = "work"
        item["activity"] = "在工位上忙工作"
        host = FakeHost([item])
        host._maybe_own_desk_sync("走回工位坐下")
        self.assertIsNone(host.state.patched)

    def test_meeting_room(self):
        host = FakeHost([meal_item()])
        host._maybe_own_desk_sync("（抓起本子，去了会议室）")
        item = host.state.schedule[today_str()][0]
        self.assertEqual(item["activity"], "在会议室开会")
        self.assertEqual(item["category"], "work")

    def test_pantry(self):
        host = FakeHost([meal_item()])
        host._maybe_own_desk_sync("（去茶水间接了杯水）")
        self.assertEqual(host.state.schedule[today_str()][0]["activity"], "在茶水间歇一会儿")

    def test_canteen_after_meeting(self):
        item = meal_item()
        item["activity"] = "在会议室开会"
        item["category"] = "work"
        host = FakeHost([item])
        host._maybe_own_desk_sync("（散会了，去食堂打饭）")
        self.assertEqual(host.state.schedule[today_str()][0]["activity"], "在公司食堂吃午饭")

    def test_other_person_goes_to_meeting(self):
        host = FakeHost([meal_item()])
        host._maybe_own_desk_sync("他去会议室了")
        self.assertIsNone(host.state.patched)


if __name__ == "__main__":
    unittest.main()
