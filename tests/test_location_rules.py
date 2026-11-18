# -*- coding: utf-8 -*-
"""地点物理法则：规则表 / 事实池时段过滤 / 日记提示词 / 事后校验降级。"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_fact_pool as fp  # noqa: E402
import amo_location_rules as loc  # noqa: E402
import diary_engine  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402

DAY = dt.date(2026, 11, 16)
DAY_TEXT = "2026-11-16"


def _ts(hh, mm):
    return dt.datetime(2026, 11, 16, hh, mm).timestamp()


class RuleTableTests(unittest.TestCase):
    def test_categories_cover_required_places(self):
        for category in ("home", "hospital", "office", "mall", "scenic", "hotel",
                         "road", "restaurant", "station", "school", "unknown"):
            self.assertIn(category, loc.LOCATION_RULES)
            rule = loc.LOCATION_RULES[category]
            for key in ("allowed", "forbidden", "prompt_clause", "fallback_line"):
                self.assertIn(key, rule)

    def test_hospital_blocks_home_props(self):
        text = "我倒在床上，茶几上还放着外卖，翻个身没人拉被角。"
        hits = loc.check(text, "医院", "hospital")
        for word in ("床", "茶几", "被角"):
            self.assertIn(word, hits)

    def test_hospital_allows_queue_and_iv(self):
        text = "在走廊排队等叫号，护士让我躺到病床上输液，消毒水味很重。"
        self.assertEqual(loc.check(text, "医院·走廊", "hospital"), [])

    def test_office_blocks_bed_and_bath(self):
        self.assertIn("被窝", loc.check("躲被窝里", "公司", "office"))
        self.assertIn("洗澡", loc.check("在公司洗澡", "公司", "office"))

    def test_mall_blocks_workstation(self):
        self.assertIn("工位", loc.check("在工位改周报", "商场", "mall"))

    def test_home_allows_home_props(self):
        self.assertEqual(loc.check("窝在沙发上，茶几放着外卖，钻回被窝。", "家", "home"), [])

    def test_home_blocks_hospital_and_office_words(self):
        hits = loc.check("听见叫号，闻到消毒水，还去了试衣间", "家", "home")
        for word in ("叫号", "消毒水", "试衣间"):
            self.assertIn(word, hits)

    def test_unknown_is_loose_but_blocks_exclusive_words(self):
        self.assertEqual(loc.check("在门口坐着发呆，看手机", "某个没写清楚的地方", ""), [])
        self.assertIn("工位", loc.check("在工位坐着", "某个没写清楚的地方", ""))


class ResolveTests(unittest.TestCase):
    def test_hospital_variants(self):
        self.assertEqual(loc.resolve("医院"), "hospital")
        self.assertEqual(loc.resolve("医院·走廊"), "hospital")
        self.assertEqual(loc.resolve("医院病房", "home"), "hospital")
        self.assertEqual(loc.resolve("市一医院门诊"), "hospital")

    def test_other_places(self):
        cases = {
            "家": "home", "卧室": "home", "公司": "office", "写字楼": "office",
            "商场": "mall", "购物中心": "mall", "景区": "scenic", "公园": "scenic",
            "酒店": "hotel", "民宿": "hotel", "餐厅": "restaurant", "食堂": "restaurant",
            "地铁站": "station", "火车站": "station", "学校": "school",
            "教室": "school", "路上": "road", "地铁": "road",
        }
        for place, expected in cases.items():
            self.assertEqual(loc.resolve(place), expected, place)

    def test_zone_fallback_and_unknown(self):
        self.assertEqual(loc.resolve("", "office"), "office")
        self.assertEqual(loc.resolve("", "health"), "hospital")
        self.assertEqual(loc.resolve("火星基地"), "unknown")
        self.assertEqual(loc.resolve("", ""), "unknown")

    def test_prompt_clause_and_fallback_line(self):
        clause = loc.prompt_clause("医院·走廊", "hospital")
        self.assertIn("你当前所在的地点是【医院·走廊】", clause)
        self.assertIn("排队", clause)
        self.assertIn("被窝", clause)
        self.assertIn("医院", loc.fallback_line("医院"))

    def test_check_respects_config_switch(self):
        text = "倒在床上，茶几上外卖"
        self.assertTrue(loc.check(text, "医院"))
        self.assertEqual(loc.check(text, "医院", enabled_flag=False), [])
        self.assertEqual(loc.prompt_clause("医院", enabled_flag=False), "")


class FakeDB:
    def __init__(self, kv=None):
        self.kv = kv or {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def get_schedule(self, day):
        return []


class FakeState:
    def __init__(self, items):
        self.schedule = {DAY_TEXT: items}
        self.db = FakeDB()

    def health_info(self):
        return {"status": "健康", "sick": False}


class FakeWallet:
    def __init__(self, rows):
        self.rows = rows

    def ledger(self, limit=50):
        return list(self.rows)


class FakeWardrobe:
    def current(self, day=None):
        return {"text": "米色外套"}


class FakePlugin:
    def __init__(self, items=None, ledger=None, chat_rows=None, cfg=True):
        self.state = FakeState(items or [])
        self.wallet = FakeWallet(ledger or [])
        self.wardrobe = FakeWardrobe()
        self.db = FakeDB()
        self.chat_rows = list(chat_rows or [])
        self.queries = []
        self.config = {"location_rules_enabled": cfg}

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    async def _data_call(self, *args, **kwargs):
        start, end = args[2], args[3]
        self.queries.append((start, end))
        return [row for row in self.chat_rows if start <= row["created_at"] < end]

    def _data_bot_id(self):
        return "bot"


def hospital_items():
    return [
        {"start": "08:00", "end": "09:00", "activity": "在家赖床", "place": "家",
         "zone": "home", "category": "rest"},
        {"start": "12:00", "end": "13:00", "activity": "在家点外卖", "place": "家",
         "zone": "home", "category": "meal"},
        {"start": "13:41", "end": "15:41", "activity": "在医院排队看诊", "place": "医院",
         "zone": "hospital", "category": "health"},
    ]


def chat_rows():
    return [
        {"role": "user", "umo": "lark:FriendMessage:1",
         "content": "我点了外卖，在被窝里吃", "created_at": _ts(12, 30)},
        {"role": "user", "umo": "lark:FriendMessage:1",
         "content": "我在医院排队，消毒水味好重，多喝热水", "created_at": _ts(14, 10)},
    ]


def ledger_rows():
    return [
        {"ts": _ts(12, 10), "delta_cents": -2000, "reason": "外卖"},
        {"ts": _ts(14, 10), "delta_cents": -5000, "reason": "挂号"},
    ]


class FactPoolWindowTests(unittest.TestCase):
    def test_hospital_slot_drops_morning_home_facts(self):
        plugin = FakePlugin(hospital_items(), ledger_rows(), chat_rows())
        pool = asyncio.run(fp.build_fact_pool(
            plugin, DAY, now=dt.datetime(2026, 11, 16, 16, 0),
            slot_start="13:41", slot_end="15:41"))
        details = " ".join(f["detail"] for f in pool["facts"])
        self.assertIn("医院排队看诊", details)
        self.assertIn("挂号", details)
        self.assertIn("消毒水", details)
        self.assertNotIn("赖床", details)
        self.assertNotIn("外卖", details)
        self.assertNotIn("被窝", details)
        self.assertEqual(pool["meta"]["location"], "医院")
        self.assertEqual(pool["meta"]["zone"], "hospital")

    def test_chat_query_and_chat_facts_are_windowed(self):
        plugin = FakePlugin(hospital_items(), ledger_rows(), chat_rows())
        pool = asyncio.run(fp.build_fact_pool(
            plugin, DAY, now=dt.datetime(2026, 11, 16, 16, 0),
            hour_slot=13, slot_start="13:41", slot_end="15:41"))
        start = int(dt.datetime(2026, 11, 16, 13, 41).timestamp())
        end = int(dt.datetime(2026, 11, 16, 15, 41).timestamp())
        self.assertEqual(plugin.queries[0], (start, end))
        details = " ".join(f["detail"] for f in pool["facts"])
        self.assertNotIn("在被窝里吃", details)
        self.assertIn("消毒水", details)

    def test_whole_day_pool_keeps_home_history_for_long_diary(self):
        plugin = FakePlugin(hospital_items(), ledger_rows(), chat_rows())
        pool = asyncio.run(fp.build_fact_pool(
            plugin, DAY, now=dt.datetime(2026, 11, 16, 16, 0)))
        details = " ".join(f["detail"] for f in pool["facts"])
        self.assertIn("赖床", details)
        self.assertIn("外卖", details)
        self.assertEqual(pool["meta"]["location_scope"], "day")
        self.assertEqual(pool["meta"]["location"], "")
        self.assertIn("医院", pool["meta"]["places"])
        self.assertIn("家", pool["meta"]["places"])

    def test_whole_day_prompt_uses_place_list_not_single_place(self):
        items = [
            {"start": "08:00", "end": "09:00", "activity": "在医院排队看诊",
             "place": "医院", "zone": "hospital", "category": "health"},
            {"start": "18:00", "end": "19:00", "activity": "回家窝在沙发上",
             "place": "家", "zone": "home", "category": "rest"},
        ]
        plugin = FakePlugin(items, [], [])
        pool = asyncio.run(fp.build_fact_pool(
            plugin, DAY, now=dt.datetime(2026, 11, 16, 22, 0)))
        prompt = fp.render_prompt(pool)
        self.assertIn("今天去过", prompt)
        self.assertIn("医院", prompt)
        self.assertIn("家", prompt)
        self.assertNotIn("你当前所在的地点是【", prompt)

    def test_wake_late_and_spending_hard_facts_stay_in_slot(self):
        items = [
            {"start": "00:00", "end": "09:30", "activity": "睡觉", "place": "家",
             "zone": "home", "category": "sleep"},
            {"start": "13:41", "end": "15:41", "activity": "在医院排队看诊",
             "place": "医院", "zone": "hospital", "category": "health"},
        ]
        plugin = FakePlugin(items, ledger_rows(), [])
        pool = asyncio.run(fp.build_fact_pool(
            plugin, DAY, now=dt.datetime(2026, 11, 16, 16, 0),
            slot_start="13:41", slot_end="15:41"))
        details = " ".join(f["detail"] for f in pool["facts"])
        self.assertIn("睡到 09:30", details)
        self.assertIn("挂号", details)
        self.assertFalse(pool["meta"]["has_chat"])

    def test_fact_with_wrong_place_is_dropped(self):
        facts = [{"time": "14:00", "type": "活动", "detail": "在家躺床", "place": "家"}]
        kept = fp._filter_window_facts(facts, (13 * 60 + 41, 15 * 60 + 41), "hospital")
        self.assertEqual(kept, [])

    def test_disabled_config_keeps_prompt_neutral(self):
        plugin = FakePlugin(hospital_items(), ledger_rows(), chat_rows(), cfg=False)
        pool = asyncio.run(fp.build_fact_pool(
            plugin, DAY, now=dt.datetime(2026, 11, 16, 16, 0)))
        self.assertFalse(pool["meta"]["location_rules_enabled"])
        self.assertNotIn("绝对不能提及", fp.render_prompt(pool))


def _ctx():
    return {"day": DAY_TEXT, "meta": {}, "weather": {}, "health": {}, "period": {},
            "mood": {}, "yesterday": {}, "event": {}, "scenic": {}, "assets": "",
            "worn": "", "gift_log": "", "outfit": "", "location": "贵阳",
            "world": {}}


def _hospital_item():
    return {"start": "13:41", "end": "15:41", "activity": "在医院排队看诊",
            "place": "医院·走廊", "zone": "hospital", "category": "health"}


def _home_item():
    return {"start": "12:00", "end": "13:41", "activity": "在家躺着", "place": "家",
            "zone": "home", "category": "rest"}


class PromptInjectionTests(unittest.TestCase):
    def test_small_diary_prompt_has_location_and_transition(self):
        engine = ScheduleEngine.__new__(ScheduleEngine)
        prompt = engine._diary_prompt(
            _ctx(), _hospital_item(), _home_item(), None,
            prev_diary="我倒在床上，茶几上还有外卖，翻个身没人拉被角。")
        self.assertIn("你当前所在的地点是【医院·走廊】", prompt)
        self.assertIn("排队", prompt)
        self.assertIn("被窝", prompt)
        self.assertIn("地点切换", prompt)
        self.assertNotIn("茶几上还有外卖", prompt)

    def test_small_diary_same_place_keeps_prev_diary(self):
        engine = ScheduleEngine.__new__(ScheduleEngine)
        prompt = engine._diary_prompt(
            _ctx(), _hospital_item(), dict(_hospital_item(), start="12:00", end="13:41"),
            None, prev_diary="在走廊排队时睡着了。")
        self.assertIn("在走廊排队时睡着了", prompt)
        self.assertNotIn("地点切换", prompt)

    def test_long_diary_prompt_has_day_place_list(self):
        prompt = diary_engine.build_prompt(
            DAY, ["他：好点没"],
            ["08:00 在医院排队看诊", "18:00 回家窝在沙发上"],
            place="医院", zone="hospital")
        self.assertIn("今天去过", prompt)
        self.assertIn("医院", prompt)
        self.assertIn("家", prompt)
        self.assertNotIn("你当前所在的地点是【", prompt)

    def test_schedule_ctx_home_residue_does_not_override_current_place(self):
        engine = ScheduleEngine.__new__(ScheduleEngine)
        ctx = _ctx()
        ctx["home"] = "旧家"
        ctx["world"] = {"home": "旧家"}
        prompt = engine._diary_prompt(
            ctx, _hospital_item(), _home_item(), None,
            prev_diary="我倒在床上，茶几上还有外卖。")
        self.assertIn("医院·走廊", prompt)
        self.assertNotIn("旧家", prompt)
        self.assertNotIn("茶几上还有外卖", prompt)

    def test_config_schema_has_location_switch(self):
        import json as _json
        with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as fh:
            schema = _json.load(fh)
        self.assertIs(schema["location_rules_enabled"]["default"], True)

    def test_long_diary_render_prompt_has_day_place_list(self):
        pool = {"facts": [{"time": "13:41", "type": "日程", "detail": "在医院排队看诊（医院）"}],
                "meta": {"location_scope": "day", "location": "", "zone": "",
                         "places": ["医院", "家"]}}
        prompt = fp.render_prompt(pool)
        self.assertIn("今天去过", prompt)
        self.assertIn("医院", prompt)
        self.assertIn("家", prompt)
        self.assertNotIn("你当前所在的地点是【", prompt)


class GuardTests(unittest.TestCase):
    def _engine(self):
        engine = ScheduleEngine.__new__(ScheduleEngine)
        engine.p = FakePlugin()
        return engine

    def test_hospital_retry_then_pass(self):
        class FakeLLM:
            def __init__(self):
                self.calls = 0

            async def chat_raw(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return '{"diary":"我倒在床上，茶几上还有外卖，翻个身没人拉被角。"}'
                return '{"diary":"在走廊塑料椅上排队等叫号，消毒水味很重，烧得晕乎乎的。"}'

        llm = FakeLLM()
        engine = self._engine()
        text, fallback = asyncio.run(engine._guard_diary_location(
            "我倒在床上，茶几上还有外卖。", _hospital_item(), llm, "u", "sys", "prompt",
            lambda raw: json.loads(raw)["diary"], False, []))
        self.assertFalse(fallback)
        self.assertNotIn("床", text)
        self.assertIn("走廊", text)
        self.assertEqual(llm.calls, 2)

    def test_generate_diary_wires_location_fallback(self):
        class FakeLLM:
            async def chat_raw(self, *args, **kwargs):
                return '{"diary":"我倒在床上，茶几上还有外卖，翻个身没人拉被角。"}'

        engine = self._engine()
        result = asyncio.run(engine.generate_diary(
            "u", {"fact_pool": []}, _hospital_item(), _home_item(), None, FakeLLM()))
        self.assertTrue(result.get("fallback"))
        self.assertEqual(result.get("fallback_reason"), "location")
        self.assertIn("医院", result.get("diary") or "")

    def test_two_conflicts_fall_back_without_raise(self):
        class FakeLLM:
            def __init__(self):
                self.calls = 0

            async def chat_raw(self, *args, **kwargs):
                self.calls += 1
                return '{"diary":"我倒在床上，茶几上还有外卖，翻个身没人拉被角。"}'

        llm = FakeLLM()
        engine = self._engine()
        text, fallback = asyncio.run(engine._guard_diary_location(
            "我倒在床上，茶几上还有外卖。", _hospital_item(), llm, "u", "sys", "prompt",
            lambda raw: json.loads(raw)["diary"], False, []))
        self.assertTrue(fallback)
        self.assertIn("医院", text)
        self.assertGreater(len(text), 8)
        self.assertEqual(llm.calls, 2)

    def test_long_diary_validate_falls_back(self):
        pool = {"facts": [], "meta": {"location": "医院", "zone": "hospital"}}
        text = fp.validate_text("我倒在床上，茶几上还有外卖，翻个身没人拉被角。", pool)
        self.assertIn("医院", text)
        self.assertTrue(pool["meta"].get("location_fallback"))

    def _day_pool(self, places=None):
        return {"facts": [], "meta": {
            "location_scope": "day", "location": "", "zone": "",
            "places": list(places or ["医院", "家"]),
            "location_rules_enabled": True}}

    def test_long_diary_day_allows_hospital_and_sofa(self):
        pool = self._day_pool()
        text = fp.validate_text("上午在医院排队，晚上回家窝在沙发上。", pool)
        self.assertIn("医院", text)
        self.assertIn("沙发", text)
        self.assertFalse(pool["meta"].get("location_fallback"))

    def test_long_diary_day_blocks_unvisited_office(self):
        pool = self._day_pool()
        text = fp.validate_text("上午在医院排队。下午在公司工位敲键盘。", pool)
        self.assertIn("医院", text)
        self.assertNotIn("工位", text)

    def test_long_diary_day_falls_back_when_only_unvisited(self):
        pool = self._day_pool()
        text = fp.validate_text("在公司工位敲了一天键盘。", pool)
        self.assertIn("今天想记的太多", text)
        self.assertTrue(pool["meta"].get("location_fallback"))

    def test_long_diary_clean_text_passes(self):
        pool = {"facts": [], "meta": {"location": "医院", "zone": "hospital"}}
        text = fp.validate_text("在走廊排队等叫号，护士让我躺到病床上输液。", pool)
        self.assertIn("病床", text)
        self.assertNotIn("fallback", text)

    def test_long_diary_one_bad_sentence_is_stripped(self):
        pool = {"facts": [], "meta": {"location": "医院", "zone": "hospital"}}
        text = fp.validate_text("在走廊排队等叫号。回家倒在床上。", pool)
        self.assertIn("走廊", text)
        self.assertNotIn("倒在床上", text)


if __name__ == "__main__":
    unittest.main()
