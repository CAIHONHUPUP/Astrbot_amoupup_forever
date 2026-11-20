# -*- coding: utf-8 -*-
"""约会收尾截断 + 未来已定行程展示。"""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class FakeDB:
    def __init__(self):
        self.data = {"date_now": {"line": "和老公火锅", "until": 9999999999}}

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakeState:
    def __init__(self):
        self.schedule = {"2026-11-15": [
            {"start": "19:22", "end": "21:22", "activity": "和老公火锅",
             "place": "楼下小馆", "category": "social", "zone": "restaurant"},
        ]}
        self.commits = []

    def commit_schedule_change(self, day, items, old_items=None, source="", reason=""):
        self.schedule[day] = list(items)
        self.commits.append(reason)
        return {"changed": True}


class Plugin:
    def __init__(self):
        self.db = FakeDB()
        self.state = FakeState()
        self.homes = []

    def set_date_home(self, title, at=0):
        self.db.kv_set("date_home", {"title": title, "at": at, "until": at + 3600})
        self.homes.append(title)


import amo_date_context as dc  # noqa: E402
import amo_human_state as hs  # noqa: E402

NOW = dt.datetime(2026, 11, 15, 20, 27)


def test_end_words_truncate_date_schedule():
    plugin = Plugin()
    assert dc.maybe_finish_date(plugin, "天气冷，老婆我们回家吧", NOW) == "user_done"
    rows = plugin.state.schedule["2026-11-15"]
    assert rows[0]["end"] == "20:27"
    assert plugin.db.kv_get("date_now") == {}
    assert plugin.homes, "必须切到刚到家"


def test_non_end_message_does_nothing():
    plugin = Plugin()
    assert dc.maybe_finish_date(plugin, "再吃点吧", NOW) == ""
    assert plugin.state.schedule["2026-11-15"][0]["end"] == "21:22"


def test_booked_tree_shows_future_social():
    class State(hs.HumanState):
        pass

    state = State.__new__(State)
    state.schedule = {"2026-11-16": [
        {"start": "14:00", "end": "17:00", "activity": "和柳如烟逛街",
         "place": "商场", "category": "social", "source": "social", "zone": "mall"},
    ]}
    state.db = type("D", (), {"get_schedule": lambda self, day: []})()

    class Stub(hs.HumanState):
        def __init__(self):
            self.schedule = state.schedule
            self.db = state.db

        def _jit_visible_slots(self, raw, now_min):
            return hs.HumanState._jit_visible_slots(self, raw, now_min)

        def _merge_quiet_slots(self, slots):
            return hs.HumanState._merge_quiet_slots(self, slots)

    stub = Stub()
    tree = hs.HumanState._booked_tree(stub, "2026-11-16")
    texts = [c["activity"] for block in tree for c in block["children"]]
    assert any("柳如烟" in t for t in texts), texts
