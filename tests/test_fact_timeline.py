# -*- coding: utf-8 -*-
"""FactTimeline（Phase 3）：事实单一时间线。

验证三件事：
1. 条目是同构的（day/start/end/time/type/detail/together/category/source/place/weight）；
2. 裁剪 / 分类过滤 / 去重 / 稳定排序 / 渲染 / 校验这些能力是时间线自己的；
3. fact_pool 与 fact_binder 只读时间线——把时间线改坏，两边的输出必须同步变。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_fact_binder as binder  # noqa: E402
import amo_fact_pool as fp  # noqa: E402
import amo_fact_timeline as tl  # noqa: E402

DAY = "2026-11-16"
NOW = _dt.datetime(2026, 11, 16, 16, 0)
SLOT = ("13:41", "15:41")
SLOT_WINDOW = (13 * 60 + 41, 15 * 60 + 41)

HOSPITAL = {"start": "13:41", "end": "15:41", "activity": "在医院排队看诊",
            "place": "医院", "zone": "hospital", "category": "health"}
MORNING = {"start": "08:00", "end": "09:30", "activity": "在家赖床",
           "place": "家", "zone": "home", "category": "rest"}
COOK = {"start": "12:00", "end": "13:00", "activity": "在家做饭",
        "place": "家", "zone": "home", "category": "rest"}
WALK = {"start": "10:00", "end": "11:00", "activity": "和柳如烟逛街",
        "place": "商场", "source": "activity"}
DATE_BLOCK = {"start": "19:22", "end": "20:27", "activity": "和老公火锅",
              "place": "楼下小馆", "category": "social"}


def stamp(day, hh, mm=0):
    return _dt.datetime.combine(_dt.date.fromisoformat(day), _dt.time(hh, mm)).timestamp()


class FakeDB:
    def __init__(self, kv=None, schedule=None):
        self.kv = dict(kv or {})
        self.schedule = dict(schedule or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return list(self.schedule.get(day) or [])


class FakeState:
    def __init__(self, items=None, health=None):
        self.schedule = {DAY: list(items or [])}
        self.health = dict(health or {})

    def health_info(self):
        return dict(self.health)

    def current_activity_info(self):
        return {"activity": "在家做饭", "place": "家", "zone": "home"}, "schedule"


class FakeWallet:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def ledger(self, limit=200):
        return list(self.rows)


class FakeWardrobe:
    def __init__(self, text=""):
        self.text = text

    def current(self, day=None):
        return {"text": self.text}


class FakePlugin:
    def __init__(self, items=None, ledger=None, kv=None, chat=None, health=None,
                 outfit="", flags=None):
        self.db = FakeDB(kv=kv, schedule={DAY: list(items or [])})
        self.state = FakeState(items, health)
        self.state.db = self.db
        self.wallet = FakeWallet(ledger)
        self.wardrobe = FakeWardrobe(outfit)
        self.chat = list(chat or [])
        self.queries = []
        self._flags = dict(flags or {})

    def cfg_bool(self, key, default=False):
        return bool(self._flags.get(key, default))

    async def _data_call(self, name, bot, start, end, *args, **kwargs):
        self.queries.append((start, end))
        return [row for row in self.chat
                if start <= float(row.get("created_at") or 0) < end]

    def _data_bot_id(self):
        return "bot"


def ledger_rows():
    return [
        {"ts": stamp(DAY, 12, 10), "delta_cents": -2000, "reason": "外卖"},
        {"ts": stamp(DAY, 14, 10), "delta_cents": -5000, "reason": "挂号"},
    ]


def chat_rows():
    return [
        {"role": "user", "umo": "lark:FriendMessage:1", "content": "我点了外卖，在被窝里吃",
         "created_at": stamp(DAY, 12, 30)},
        {"role": "user", "umo": "lark:FriendMessage:1",
         "content": "我在医院排队，消毒水味好重，多喝热水", "created_at": stamp(DAY, 14, 10)},
        {"role": "bot", "umo": "lark:FriendMessage:1", "content": "我也想你",
         "created_at": stamp(DAY, 14, 15)},
        {"role": "user", "umo": "lark:GroupMessage:9", "content": "群里聊点想你的事",
         "created_at": stamp(DAY, 14, 20)},
    ]


def hospital_plugin(**kwargs):
    return FakePlugin(items=[MORNING, COOK, HOSPITAL, WALK], ledger=ledger_rows(),
                      chat=chat_rows(), health={"status": "健康"}, **kwargs)


def build(plugin, **kwargs):
    kwargs.setdefault("now", NOW)
    return asyncio.run(tl.build(plugin, DAY, **kwargs))


class EntryShapeTests(unittest.TestCase):
    def test_entries_are_canonical_dicts(self):
        entries = build(hospital_plugin())
        required = ("day", "start", "end", "time", "type", "detail", "together",
                    "category", "source", "place", "weight")
        for entry in entries:
            for key in required:
                self.assertIn(key, entry)
            self.assertIsInstance(entry["weight"], int)
            self.assertIsInstance(entry["detail"], str)
            self.assertIn(entry["source"], tl.WEIGHTS)
        schedule = [entry for entry in entries if entry["source"] == "schedule"]
        self.assertEqual(schedule[0]["day"], DAY)
        self.assertEqual(schedule[0]["start"], 8 * 60)
        self.assertEqual(schedule[0]["end"], 9 * 60 + 30)
        self.assertEqual(schedule[0]["time"], "08:00")
        self.assertEqual(schedule[0]["place"], "家")

    def test_schedule_entry_projects_to_legacy_fact(self):
        entries = build(hospital_plugin())
        fact = tl.to_fact(next(e for e in entries if e["source"] == "schedule"))
        self.assertEqual(fact["type"], "日程")
        self.assertEqual(fact["time"], "08:00")
        self.assertEqual(fact["detail"], "在家赖床（家）")
        self.assertEqual(fact["place"], "家")
        self.assertNotIn("start", fact)

    def test_activity_entry_has_span_and_companion(self):
        entries = build(hospital_plugin())
        act = next(e for e in entries if e["source"] == "activity")
        self.assertEqual((act["start"], act["end"]), (600, 660))
        self.assertIn("和柳如烟", act["detail"])
        self.assertIn("1小时", act["detail"])
        fact = tl.to_fact(act)
        self.assertEqual((fact["start"], fact["end"]), ("10:00", "11:00"))
        self.assertEqual(fact["place"], "商场")


class CapabilityTests(unittest.TestCase):
    def test_trim_keeps_overlapping_and_drops_other(self):
        entries = build(hospital_plugin())
        kept = tl.trim(entries, SLOT_WINDOW)
        details = " ".join(entry["detail"] for entry in kept)
        self.assertIn("医院排队看诊", details)
        self.assertNotIn("在家赖床", details)

    def test_trim_point_events_use_inclusive_bounds(self):
        edge = tl.make_entry(DAY, "ledger", "消费", "挂号 50.00 元",
                             start=SLOT_WINDOW[1], time="15:41", point=True)
        late = tl.make_entry(DAY, "ledger", "消费", "晚饭 50.00 元",
                             start=SLOT_WINDOW[1] + 1, time="15:42", point=True)
        kept = tl.trim([edge, late], SLOT_WINDOW)
        self.assertEqual([entry["detail"] for entry in kept], ["挂号 50.00 元"])

    def test_by_category_drops_wrong_place(self):
        home = tl.make_entry(DAY, "activity", "活动", "在家躺着", start=840,
                             time="14:00", place="家")
        hospital = tl.make_entry(DAY, "activity", "活动", "在医院排队", start=840,
                                 time="14:00", place="医院")
        ledger = tl.make_entry(DAY, "ledger", "消费", "挂号 50.00 元", start=840,
                               time="14:00", point=True)
        kept = tl.by_category([home, hospital, ledger], "hospital")
        self.assertNotIn(home, kept)
        self.assertIn(hospital, kept)
        self.assertIn(ledger, kept, "没有地点的事实（账单）不该被类别过滤掉")

    def test_by_category_empty_keeps_everything(self):
        entries = build(hospital_plugin())
        self.assertEqual(len(tl.by_category(entries, "")), len(entries))
        self.assertEqual(len(tl.by_category(entries, "unknown")), len(entries))

    def test_dedupe_removes_same_fact(self):
        item = tl.make_entry(DAY, "chat", "线上聊天", "[他对我说]：多喝热水",
                             start=600, time="10:00", point=True)
        self.assertEqual(len(tl.dedupe([item, dict(item)])), 1)
        other = tl.make_entry(DAY, "chat", "线上聊天", "[他对我说]：多喝热水",
                              start=601, time="10:01", point=True)
        self.assertEqual(len(tl.dedupe([item, other])), 2)

    def test_sort_stable_is_weight_then_start(self):
        chat = tl.make_entry(DAY, "chat", "线上聊天", "[他对我说]：想你", start=600,
                             time="10:00", point=True)
        late = tl.make_entry(DAY, "schedule", "日程", "晚饭（家）", start=1080, time="18:00")
        early = tl.make_entry(DAY, "schedule", "日程", "早饭（家）", start=480, time="08:00")
        sorted_entries = tl.sort_stable([chat, late, early])
        self.assertEqual([entry["detail"] for entry in sorted_entries],
                         ["早饭（家）", "晚饭（家）", "[他对我说]：想你"])


class WindowAndSourceTests(unittest.TestCase):
    def test_slot_window_matches_legacy_pool(self):
        plugin = hospital_plugin()
        entries = build(plugin, now=NOW, slot_start=SLOT[0], slot_end=SLOT[1])
        details = " ".join(entry["detail"] for entry in entries)
        self.assertIn("医院排队看诊", details)
        self.assertIn("挂号", details)
        self.assertNotIn("赖床", details)
        self.assertNotIn("外卖", details)
        self.assertNotIn("被窝", details)

    def test_chat_private_only_and_tagged(self):
        entries = build(hospital_plugin())
        chats = [entry for entry in entries if entry["source"] == "chat"]
        text = " ".join(entry["detail"] for entry in chats)
        self.assertIn("[他对我说]", text)
        self.assertIn("[我对他说]", text)
        self.assertNotIn("群里", text)
        # 13:41 之前/之后两段私聊会被合并成一条（时间取最后一句）
        self.assertEqual(len(chats), 2)

    def test_chat_is_windowed(self):
        entries = build(hospital_plugin(), now=NOW, slot_start=SLOT[0], slot_end=SLOT[1])
        text = " ".join(entry["detail"] for entry in entries if entry["source"] == "chat")
        self.assertNotIn("在被窝里吃", text)
        self.assertIn("消毒水", text)

    def test_meal_cook_and_ledger_entries(self):
        now = _dt.datetime(2026, 11, 16, 13, 32)
        plugin = FakePlugin(items=[COOK], ledger=[
            {"ts": stamp(DAY, 11, 30), "delta_cents": -1500, "reason": "其他：自己做饭 × 1"}])
        entries = tl.meal_entries([COOK], plugin.wallet.ledger(), now)
        view = tl.meal_view([COOK], plugin.wallet.ledger(), now)
        self.assertTrue(any(entry.get("category") == "做饭" for entry in entries))
        self.assertTrue(view["cooked"])
        self.assertFalse(view["takeout"])
        self.assertEqual(view["cooked_items"], [COOK])

    def test_meal_outside_band_is_empty(self):
        now = _dt.datetime(2026, 11, 16, 15, 30)  # 15:00-16:00 不是饭点
        self.assertEqual(tl.meal_entries([COOK], [], now), [])
        view = tl.meal_view([COOK], [], now)
        self.assertEqual(view["band"], "")
        self.assertFalse(view["cooked"])
        self.assertFalse(view["takeout"])

    def test_empty_plugin_pool_does_not_crash(self):
        plugin = FakePlugin(health={"status": "健康"})
        entries = build(plugin)
        self.assertEqual(entries, [])
        pool = asyncio.run(fp.build_fact_pool(plugin, DAY, now=NOW))
        self.assertEqual(pool["facts"], [])
        self.assertEqual(pool["meta"]["location_scope"], "day")
        self.assertEqual(pool["meta"]["places"], [])
        self.assertFalse(pool["meta"]["date_together"])
        self.assertIn("素材 JSON：", fp.render_prompt(pool))


class DateAndHistoryTests(unittest.TestCase):
    def test_date_scene_leads_and_together(self):
        plugin = FakePlugin(items=[DATE_BLOCK])
        entries = build(plugin)
        self.assertEqual(entries[0]["type"], "物理场景")
        self.assertTrue(entries[0]["together"])
        pool = asyncio.run(fp.build_fact_pool(plugin, DAY, now=NOW))
        self.assertTrue(pool["meta"]["date_together"])
        self.assertEqual(pool["facts"][0]["type"], "物理场景")

    def test_historical_day_does_not_leak_today(self):
        plugin = FakePlugin(items=[DATE_BLOCK], kv={
            "date_fact": {"day": DAY, "line": "和老公火锅"},
            "last_date_event": {"day": DAY, "activity": "和老公火锅"},
        })
        pool = asyncio.run(fp.build_fact_pool(plugin, "2026-11-14", now=NOW))
        self.assertFalse(pool["meta"]["date_together"])
        types = [fact["type"] for fact in pool["facts"]]
        self.assertNotIn("物理场景", types)
        self.assertNotIn("线下约会", types)
        self.assertEqual(pool["day"], "2026-11-14")

    def test_kv_date_is_attached_only_to_its_own_day(self):
        plugin = FakePlugin(items=[DATE_BLOCK])
        pool = asyncio.run(fp.build_fact_pool(plugin, DAY, now=NOW))
        types = [fact["type"] for fact in pool["facts"]]
        self.assertIn("物理场景", types)
        self.assertTrue(pool["meta"]["has_offline_meet"])


class RenderAndValidateTests(unittest.TestCase):
    def test_render_lines_have_time_labels(self):
        facts = [{"time": "12:00", "type": "日程", "detail": "在家做饭（家）"},
                 {"time": "", "type": "状态", "detail": "今天一直在家，没有出门"}]
        lines = tl.render_lines(facts, "[今日] ")
        self.assertEqual(lines[0], "- [今日] [日程] 12:00 在家做饭（家）")
        self.assertEqual(lines[1], "- [今日] [状态] 今天一直在家，没有出门")

    def test_render_json_keeps_chinese(self):
        text = tl.render_json([{"time": "12:00", "type": "日程", "detail": "在家做饭（家）"}])
        self.assertTrue(text.startswith("素材 JSON："))
        self.assertIn("在家做饭", text)

    def test_validate_rejects_fabrication(self):
        out = tl.validate("今天穿了新买的碎花裙，不想活了。", {"facts": [], "meta": {}})
        self.assertNotIn("新买的", out)
        self.assertNotIn("不想活", out)
        self.assertIn("委屈", out)


class SingleSourceTests(unittest.TestCase):
    def test_pool_facts_are_the_timeline_projection(self):
        plugin = hospital_plugin()
        entries = build(plugin)
        pool = asyncio.run(fp.build_fact_pool(plugin, DAY, now=NOW))
        self.assertEqual(pool["facts"], tl.to_facts(entries))

    def test_pool_facts_come_from_timeline(self):
        plugin = hospital_plugin()
        before = " ".join(fact["detail"] for fact in
                          asyncio.run(fp.build_fact_pool(plugin, DAY, now=NOW))["facts"])
        original = tl.schedule_entries
        tl.schedule_entries = lambda *args, **kwargs: []
        try:
            after = " ".join(fact["detail"] for fact in
                             asyncio.run(fp.build_fact_pool(plugin, DAY, now=NOW))["facts"])
        finally:
            tl.schedule_entries = original
        self.assertIn("在家赖床", before)
        self.assertNotIn("在家赖床", after)

    def test_binder_done_lines_come_from_timeline(self):
        plugin = FakePlugin(items=[COOK])
        now = _dt.datetime(2026, 11, 16, 13, 32)
        before = "\n".join(binder.facts_lines(plugin, now))
        original = tl.schedule_entries
        tl.schedule_entries = lambda *args, **kwargs: []
        try:
            after = "\n".join(binder.facts_lines(plugin, now))
        finally:
            tl.schedule_entries = original
        self.assertIn("【绝对事实】你今天真实发生的事", before)
        self.assertIn("在家做饭", before)
        self.assertNotIn("【绝对事实】", after)

    def test_binder_meal_lines_come_from_timeline(self):
        plugin = FakePlugin(items=[COOK])
        now = _dt.datetime(2026, 11, 16, 13, 32)
        before = "\n".join(binder.facts_lines(plugin, now))
        original = tl.meal_entries
        tl.meal_entries = lambda *args, **kwargs: []
        try:
            after = "\n".join(binder.facts_lines(plugin, now))
        finally:
            tl.meal_entries = original
        self.assertIn("【绝对事实·吃】", before)
        self.assertNotIn("【绝对事实·吃】", after)

    def test_binder_ledger_lines_come_from_timeline(self):
        plugin = FakePlugin(items=[COOK], ledger=[
            {"ts": stamp(DAY, 11, 30), "delta_cents": -1500,
             "reason": "一碗热汤面", "idem": "lunch-noodle"}])
        now = _dt.datetime(2026, 11, 16, 13, 32)
        before = "\n".join(binder.facts_lines(plugin, now))
        original = tl.ledger_entries
        tl.ledger_entries = lambda *args, **kwargs: []
        try:
            after = "\n".join(binder.facts_lines(plugin, now))
        finally:
            tl.ledger_entries = original
        self.assertIn("【今天的账单】[今日]", before)
        self.assertIn("一碗热汤面", before)
        self.assertNotIn("【今天的账单】[今日]", after)
        self.assertNotIn("一碗热汤面", after)

    def test_binder_exposes_legacy_helpers(self):
        from amo_fact_binder import meal_band, reversed_idems, spent_rows
        self.assertIs(meal_band, tl.meal_band)
        self.assertIs(reversed_idems, tl.reversed_idems)
        self.assertIs(spent_rows, tl.spent_rows)
        self.assertIs(fp._minutes, tl.minutes_of)

    def test_no_second_derivation_in_sources(self):
        # binder 不许再自己算「已经发生的块」和「这一餐」：源码里不能再出现旧的推导
        body = inspect.getsource(binder.facts_lines)
        self.assertIn("fact_timeline.collect", body)
        self.assertNotIn("_minutes(item", body)
        meal = inspect.getsource(binder.meal_facts)
        self.assertIn("meal_view", meal)
        pool = inspect.getsource(fp.build_fact_pool)
        self.assertIn("fact_timeline.build_pool", pool)


class DayScopedFactTests(unittest.TestCase):
    """P1（2026-11-22）：补写昨天时健康/天气必须读「那一天」，不是生成时刻。"""

    def test_backfill_health_uses_day_snapshot(self):
        plugin = FakePlugin(items=[MORNING, COOK], health={"status": "健康", "sick": False})
        sick_since = _dt.datetime(2026, 11, 15, 14, 0).timestamp()
        sick_until = _dt.datetime(2026, 11, 16, 7, 0).timestamp()
        plugin.state.health = {"status": "低烧", "sick": True, "note": "身上发烫",
                               "since": sick_since, "until": sick_until}
        # 生病当天 15:00 读一次：顺手存下当天快照（真实生产里回复/面板会这么读）
        today = tl.read_health(plugin, _dt.date(2026, 11, 15),
                               now=_dt.datetime(2026, 11, 15, 15, 0))
        self.assertTrue(today["sick"])
        # 第二天早上 08:10：health_info 已经自动康复、kv 被重置，但仍然要读到昨天生病
        plugin.state.health = {"status": "健康", "sick": False, "since": 0, "until": 0}
        backfill = tl.read_health(plugin, _dt.date(2026, 11, 15),
                                  now=_dt.datetime(2026, 11, 16, 8, 10))
        self.assertTrue(backfill["sick"], backfill)
        self.assertEqual("低烧", backfill["status"])

    def test_backfill_pool_has_health_and_weather_facts(self):
        day = _dt.date(2026, 11, 15)
        plugin = FakePlugin(items=[MORNING, COOK], health={"status": "低烧", "sick": True,
                                                          "note": "身上发烫",
                                                          "since": _dt.datetime(2026, 11, 15, 14, 0).timestamp(),
                                                          "until": _dt.datetime(2026, 11, 16, 7, 0).timestamp()})
        plugin.state.weather = {"text": "阴", "tag": "overcast", "temp": 16, "city": "东莞",
                                "ts": _dt.datetime(2026, 11, 15, 12, 0).timestamp()}
        pool = asyncio.run(fp.build_fact_pool(plugin, day.isoformat(),
                                              now=_dt.datetime(2026, 11, 16, 8, 10)))
        types = [fact["type"] for fact in pool["facts"]]
        self.assertIn("状态", types)
        self.assertIn("天气", types)
        detail = " ".join(fact["detail"] for fact in pool["facts"])
        self.assertIn("低烧", detail)
        self.assertIn("阴", detail)
        prompt = fp.render_prompt(pool)
        self.assertRegex(prompt, r"(?m)^- \[[^\]]+\] \[天气\]")
        # 日期口径统一：素材行用目标日做「今日」锚点，不再出现 prose=今天 / fact=[昨日] 打架
        self.assertIn("[今日]", prompt)
        self.assertNotIn("[昨日]", prompt)
        self.assertEqual("2026-11-15", str(pool["meta"]["day"]))
        self.assertTrue(pool["meta"].get("backfill"))


if __name__ == "__main__":
    unittest.main()
