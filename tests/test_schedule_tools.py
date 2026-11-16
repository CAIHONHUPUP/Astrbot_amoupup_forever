# -*- coding: utf-8 -*-
"""日程消费工具、天气工具与幂等结算。"""
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from amo_human_state import HumanState  # noqa: E402
from amo_schedule_tools import (make_continue_schedule_tool, make_set_cost_tool,
                                make_update_schedule_tool)  # noqa: E402
from amo_shared import today_str  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeWallet:
    def __init__(self):
        self.calls = []

    def debit(self, amount, reason, idem, **kwargs):
        self.calls.append((int(amount), reason, idem))
        return True


class FakeState:
    def __init__(self, item):
        self.day = today_str()
        self.schedule = {self.day: [item]}
        self.commits = []

    def current_activity(self):
        return dict(self.schedule[self.day][0])

    def commit_schedule_change(self, day, items, **kwargs):
        self.schedule[day] = items
        self.commits.append((day, kwargs))


class FakePlugin:
    def __init__(self, item):
        self.state = FakeState(item)
        self.db = FakeDB()
        self.wallet = FakeWallet()


def _item(activity="吃午饭", place="公司附近", zone="restaurant", category="meal"):
    return {"start": "13:00", "end": "14:00", "activity": activity,
            "place": place, "zone": zone, "category": category,
            "cost_cents": 1500}


def test_update_schedule_changes_current_item_and_zone():
    plugin = FakePlugin(_item(activity="在去逛街的路上", place="路上", zone="road", category="commute"))
    tool = make_update_schedule_tool(plugin)
    import asyncio
    result = asyncio.run(tool(new_event="回家躲雨", reason="暴雨"))
    assert '"ok": true' in result
    item = plugin.state.schedule[plugin.state.day][0]
    assert item["place"] == "家"
    assert item["zone"] == "home"


def test_continue_does_not_change_schedule():
    plugin = FakePlugin(_item())
    before = dict(plugin.state.schedule[plugin.state.day][0])
    tool = make_continue_schedule_tool(plugin)
    import asyncio
    asyncio.run(tool(reason="买把伞继续"))
    assert plugin.state.schedule[plugin.state.day][0] == before
    assert plugin.db.kv_get("schedule_weather_continue")["reason"] == "买把伞继续"


def test_set_cost_is_persisted_for_settlement():
    plugin = FakePlugin(_item())
    tool = make_set_cost_tool(plugin)
    import asyncio
    result = asyncio.run(tool(cost_cents=800, reason="吃碗面"))
    assert '"cost_cents": 800' in result
    assert plugin.state.schedule[plugin.state.day][0]["cost_cents"] == 800


def test_settlement_is_idempotent():
    plugin = FakePlugin(_item())
    item = plugin.state.schedule[plugin.state.day][0]
    item["cost_cents"] = 1500
    fake_state = type("S", (), {})()
    fake_state.schedule = plugin.state.schedule
    fake_state.db = plugin.db
    fake_state.p = plugin
    now = datetime.datetime.combine(datetime.date.today(), datetime.time(14, 5))
    HumanState.settle_schedule_costs(fake_state, now)
    HumanState.settle_schedule_costs(fake_state, now)
    assert len(plugin.wallet.calls) == 1
    assert plugin.wallet.calls[0][0] == 1500
    assert plugin.wallet.calls[0][2].startswith("sch_")
