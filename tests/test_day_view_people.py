# -*- coding: utf-8 -*-
"""F2 事实视图细节：build_day_view 增加 people / companions / occasion / items。

覆盖家人聚餐、朋友聚餐、约会、外卖/堂食、只有金额、空数据、不编名字，
以及结构化 people/with/who、watch social 事件、消费方读取。
"""
from __future__ import annotations

import datetime as _dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_fact_binder as binder  # noqa: E402
import amo_fact_timeline as timeline  # noqa: E402
import amo_diary_guard as guard  # noqa: E402

DAY = "2026-11-16"
NOW = _dt.datetime(2026, 11, 16, 19, 0)


def _stamp(hh, mm=0):
    return _dt.datetime.combine(_dt.date.fromisoformat(DAY), _dt.time(hh, mm)).timestamp()


class _DB:
    def __init__(self, kv=None, diaries=None):
        self.kv = dict(kv or {})
        self.diaries = list(diaries or [])

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def get_schedule(self, day):
        return []

    def list_block_diaries(self, day):
        return list(self.diaries)


class _State:
    def __init__(self, items, health, db):
        self.schedule = {DAY: list(items or [])}
        self.health = dict(health or {"status": "健康"})
        self.db = db

    def health_info(self):
        return dict(self.health)

    def current_activity(self):
        return dict(self.schedule[DAY][0]) if self.schedule[DAY] else {}


class _Wallet:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def ledger(self, limit=200):
        return list(self.rows)


class _Plugin:
    def __init__(self, items=None, ledger=None, kv=None, diaries=None,
                 watch_social_events=None):
        self.db = _DB(kv, diaries)
        self.state = _State(items, {"status": "健康"}, self.db)
        self.wallet = _Wallet(ledger)
        if watch_social_events is not None:
            self.watch_social_events = watch_social_events


def _plugin(**kwargs):
    return _Plugin(**kwargs)


def _block(start, end, activity, place="家", category="social", zone="home", **extra):
    item = {"start": start, "end": end, "activity": activity, "place": place,
            "category": category, "zone": zone}
    item.update(extra)
    return item


def _view(plugin):
    return timeline.build_day_view(plugin, DAY, NOW)


def _event(view):
    assert view["events"], view
    return view["events"][0]


# ---------------------------------------------------------------------------
# 家人 / 朋友聚餐：有名字写名字，只有泛称写泛称，没有就不猜
# ---------------------------------------------------------------------------

def test_family_dinner_with_name_uses_name():
    plugin = _plugin(items=[_block("18:00", "20:00", "和妈妈吃饭", "家里")])
    view = _view(plugin)
    event = _event(view)
    assert event["people"] == ["妈妈"]
    assert "妈妈" in event["companions"]
    assert event["occasion"] == "家人聚餐"
    assert "妈妈" in view["people"]
    assert "家人聚餐" in view["occasions"]


def test_friends_dinner_without_name_uses_generic():
    plugin = _plugin(items=[_block("18:00", "20:00", "和朋友吃饭、聊天", "饭店")])
    event = _event(_view(plugin))
    assert event["people"] == []
    assert event["companions"] == ["朋友"]
    assert event["occasion"] == "朋友聚餐"


def test_named_friend_dinner_uses_actual_name_only():
    plugin = _plugin(items=[_block("18:00", "20:00", "和柳如烟吃饭", "饭店")])
    event = _event(_view(plugin))
    assert event["people"] == ["柳如烟"]
    assert event["companions"] == ["柳如烟"]
    assert event["occasion"] in ("吃饭", "堂食", "朋友聚餐")
    assert all("朋友" != name for name in event["people"])


def test_structured_people_with_who_are_read():
    plugin = _plugin(items=[_block("18:00", "20:00", "吃饭", "饭店",
                                   people=["柳如烟"], who="白凝冰",
                                   **{"with": "季博达"}, occasion="聚餐")])
    event = _event(_view(plugin))
    assert set(event["people"]) == {"柳如烟", "白凝冰", "季博达"}


# ---------------------------------------------------------------------------
# 约会
# ---------------------------------------------------------------------------

def test_date_with_session_marks_companion_and_occasion():
    session = {"phase": "active", "day": DAY, "title": "和老公看电影",
               "place": "商场", "together": True, "start_ts": _stamp(18, 30)}
    plugin = _plugin(items=[_block("18:00", "20:00", "和老公约会", "商场",
                                   category="social")],
                     kv={"date_session": session})
    event = _event(_view(plugin))
    assert event["occasion"] == "约会"
    assert "老公" in event["companions"]


def test_date_session_without_schedule_adds_date_fact():
    session = {"phase": "active", "day": DAY, "title": "和老公看电影",
               "place": "商场", "together": True, "start_ts": _stamp(18, 30)}
    plugin = _plugin(kv={"date_session": session})
    view = _view(plugin)
    dates = [event for event in view["events"] if event["type"] == "约会"]
    assert len(dates) == 1
    assert dates[0]["occasion"] == "约会"
    assert "老公" in dates[0]["companions"]
    assert "date_session" in dates[0]["sources"]


# ---------------------------------------------------------------------------
# 外卖 / 堂食 / 只有金额
# ---------------------------------------------------------------------------

def test_takeout_ledger_carries_item_name():
    plugin = _plugin(ledger=[{"ts": _stamp(19, 10), "delta_cents": -3200,
                              "reason": "美团外卖", "item_name": "烧烤外卖",
                              "idem": "m1"}])
    event = _event(_view(plugin))
    assert event["items"] == ["烧烤外卖"]
    assert event["occasion"] == "外卖"
    assert "烧烤外卖" in _view(plugin)["items"]


def test_dine_in_meal_is_marked_dine_in():
    plugin = _plugin(items=[_block("12:00", "13:00", "吃午饭", "公司附近饭店",
                                   category="meal", zone="restaurant")])
    event = _event(_view(plugin))
    assert event["occasion"] == "堂食"


def test_amount_only_ledger_does_not_invent_item():
    plugin = _plugin(ledger=[{"ts": _stamp(19, 10), "delta_cents": -1000,
                              "idem": "m2"}])
    event = _event(_view(plugin))
    assert event["items"] == []
    assert event["occasion"] == "消费"


def test_empty_view_has_empty_detail_fields():
    view = _view(_plugin())
    assert view["events"] == []
    assert view["people"] == []
    assert view["companions"] == []
    assert view["items"] == []
    assert view["occasions"] == []
    assert view["occasion"] == ""


def test_no_invented_names_when_only_generic_text():
    plugin = _plugin(items=[_block("18:00", "20:00", "和朋友吃饭", "饭店")],
                     ledger=[{"ts": _stamp(19, 10), "delta_cents": -2000,
                              "reason": "外卖", "item_name": "外卖", "idem": "m3"}])
    view = _view(plugin)
    assert view["people"] == []
    for event in view["events"]:
        assert event["people"] == []


# ---------------------------------------------------------------------------
# watch social 事件
# ---------------------------------------------------------------------------

def test_watch_social_hook_is_read():
    rows = [{"day": DAY, "kind": "social:accept",
             "payload": {"npc": "柳如烟", "text": "答应一起吃晚饭"}}]
    plugin = _plugin(watch_social_events=lambda day: rows)
    view = _view(plugin)
    assert "柳如烟" in view["people"]
    social = [event for event in view["events"] if event["type"] == "社交"]
    assert social and "柳如烟" in social[0]["companions"]
    assert any("watch_social" in event["sources"] for event in view["events"])


def test_watch_social_kv_is_read():
    rows = [{"day": DAY, "kind": "social:accept",
             "payload": {"npc": "白凝冰", "text": "约了一起逛街"}}]
    plugin = _plugin(kv={"watch:social_events": rows})
    view = _view(plugin)
    assert "白凝冰" in view["people"]
    assert "白凝冰" in view["companions"]


# ---------------------------------------------------------------------------
# 消费方（回复/日记）能读到
# ---------------------------------------------------------------------------

def test_binder_view_detail_exposes_people_and_items():
    plugin = _plugin(items=[_block("18:00", "20:00", "和柳如烟吃饭", "饭店")],
                     ledger=[{"ts": _stamp(19, 10), "delta_cents": -3200,
                              "reason": "外卖", "item_name": "烧烤外卖",
                              "idem": "m4"}])
    detail = binder.view_detail(plugin, NOW)
    assert "柳如烟" in detail["people"]
    assert "烧烤外卖" in detail["items"]
    lines = binder.view_detail_lines(plugin, NOW)
    assert any("今天的人" in line and "柳如烟" in line for line in lines)
    assert any("今天的东西" in line and "烧烤外卖" in line for line in lines)


def test_narrative_facts_exposes_people_and_items():
    plugin = _plugin(items=[_block("18:00", "20:00", "和柳如烟吃饭", "饭店")],
                     ledger=[{"ts": _stamp(19, 10), "delta_cents": -3200,
                              "reason": "外卖", "item_name": "烧烤外卖",
                              "idem": "m5"}])
    view = guard.narrative_view(plugin, NOW)
    facts = guard.narrative_facts(view, plugin=plugin, now=NOW)
    assert "柳如烟" in facts["people"]
    assert "烧烤外卖" in facts["items"]
    assert facts["occasion"]


def test_view_fields_contract_includes_detail_fields():
    for field in ("people", "companions", "occasion", "items"):
        assert field in timeline.VIEW_FIELDS
    event = _event(_view(_plugin(items=[_block("18:00", "20:00", "和朋友吃饭", "饭店")])))
    for field in ("people", "companions", "occasion", "items"):
        assert field in event