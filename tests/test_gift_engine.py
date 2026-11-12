# -*- coding: utf-8 -*-
"""礼物引擎的单测。

logger 按上架规范只能从 astrbot.api 导入，所以这里先用 astrbot 桩。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from gift_engine import (  # noqa: E402
    GiftEngine,
    clean_item,
    extract_wish_regex,
    has_wish_cue,
    is_gift_intent,
    parse_gift_object,
)


class FakeDB:
    def __init__(self, path):
        self.path = path
        self.profiles = {}
        self.memories = []
        self.messages = []

    def get_profile(self, uid):
        return self.profiles.get(str(uid), {})

    def recent_messages(self, umo, limit=10):
        return self.messages[-max(1, int(limit)):]

    def add_memory(self, uid, content, kind="fact", tier=0, **kwargs):
        # 真实插件的记忆已经交给外挂插件，这里只为兼容旧断言保留
        self.memories.append({"uid": str(uid), "content": content, "kind": kind, "tier": tier})
        return True


class FakeState:
    def __init__(self):
        self.activity = {"activity": "休息", "place": "家", "zone": "home"}
        self.weather = {"temp": 10, "text": "晴"}
        self.nudged = None

    def current_activity(self):
        return self.activity

    def nudge(self, **deltas):
        self.nudged = deltas


class FakePlugin:
    def __init__(self, tmp):
        self.plugin_dir = tmp
        self.db = FakeDB(os.path.join(tmp, "amo_forever.db"))
        self.state = FakeState()
        self.admin_uids = {"999"}
        self.context = None
        self.config = {
            "gift_enable": True,
            "gift_owner_ids": "302578963",
            "gift_allow_group": False,
            "gift_capture_wishes": True,
            "gift_capture_private_only": True,
            "gift_wish_ttl_hours": 72,
            "gift_extract_with_llm": False,
            "gift_inject_wall": True,
            "gift_max_wall_items": 8,
            "gift_allow_big": False,
            "gift_only_shopping": True,
        }

    def cfg_bool(self, key, default=False):
        value = self.config.get(key, default)
        return bool(value) if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")

    def cfg_int(self, key, default=0):
        try:
            return int(self.config.get(key, default))
        except Exception:
            return default

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default) or "")

    def cfg_float(self, key, default=0.0):
        try:
            return float(self.config.get(key, default))
        except Exception:
            return float(default)

    def _external_memory_write(self, uid, content, kind="fact", tier=1):
        """真实插件把记忆写进外挂记忆模块；测试里记到 FakeDB 上方便断言。"""
        return self.db.add_memory(uid, content, kind, tier)

    @staticmethod
    def _canonical_uid(event):
        return event.uid

    @staticmethod
    def _short_place(place):
        value = str(place or "")
        return value.split("·")[-1] if "·" in value else value


class FakeEvent:
    def __init__(self, text, uid="302578963", mid="m1", private=True, admin=False):
        self.message_str = text
        self.uid = uid
        self.unified_msg_origin = "test:FriendMessage:" + uid
        self.message_obj = types.SimpleNamespace(message_id=mid)
        self._private = private
        self._admin = admin

    def get_sender_id(self):
        return self.uid

    def is_private_chat(self):
        return self._private

    def is_admin(self):
        return self._admin


OWNER = "302578963"


class TextTests(unittest.TestCase):
    def test_gift_intent(self):
        self.assertTrue(is_gift_intent("送给你"))
        self.assertTrue(is_gift_intent("送围巾"))
        self.assertTrue(is_gift_intent("送她围巾"))
        self.assertTrue(is_gift_intent("给你买了个包"))
        self.assertTrue(is_gift_intent("我请你喝奶茶"))
        self.assertFalse(is_gift_intent("我送你回家"))
        self.assertFalse(is_gift_intent("送她回家"))
        self.assertFalse(is_gift_intent("我送你的围巾好看吗"))
        self.assertFalse(is_gift_intent("改天送给你"))
        self.assertFalse(is_gift_intent("开玩笑的"))

    def test_gift_object(self):
        self.assertEqual(parse_gift_object("送你一条羊毛围巾"), "羊毛围巾")
        self.assertEqual(parse_gift_object("送围巾"), "围巾")
        self.assertEqual(parse_gift_object("送她围巾"), "围巾")
        self.assertEqual(parse_gift_object("我送围巾"), "围巾")
        self.assertEqual(parse_gift_object("送你点零食"), "零食")
        self.assertEqual(parse_gift_object("送些水果"), "水果")
        self.assertEqual(parse_gift_object("那条围巾送给你"), "围巾")
        self.assertEqual(parse_gift_object("给你买了个包"), "包")
        self.assertEqual(parse_gift_object("送给你"), "")

    def test_any_item_after_give(self):
        for text, item in [
            ("送手表", "手表"), ("送口红", "口红"), ("送个包", "包"),
            ("送束花", "花"), ("送杯奶茶", "奶茶"), ("我送耳机", "耳机"),
        ]:
            self.assertTrue(is_gift_intent(text), text)
            self.assertEqual(parse_gift_object(text), item, text)

    def test_gift_object_with_provenance(self):
        self.assertEqual(parse_gift_object("今天逛街看到的那个挂件送给你"), "挂件")
        self.assertEqual(parse_gift_object("把今天逛街看到的那个挂件送给你"), "挂件")
        self.assertEqual(parse_gift_object("刚才看中的包送你"), "包")

    def test_wish_cue(self):
        self.assertTrue(has_wish_cue("今天在商场看到一条羊毛围巾，好想要啊"))
        self.assertFalse(has_wish_cue("我好喜欢你"))
        self.assertEqual(extract_wish_regex("今天在商场看到一条羊毛围巾，好想要啊"), "羊毛围巾")
        self.assertEqual(extract_wish_regex("好想要那个包"), "包")

    def test_vague_liking_is_a_wish(self):
        # 真机：她没直说「想要」，只说「那个小挂件配色特别对我胃口」
        text = "记得啊，那个小挂件配色特别对我胃口。怎么，做出来了？"
        self.assertTrue(has_wish_cue(text))
        self.assertEqual(extract_wish_regex(text), "小挂件")
        self.assertFalse(has_wish_cue("今天心情不错"))

    def test_clean_item(self):
        self.assertEqual(clean_item("一条羊毛围巾"), "羊毛围巾")
        self.assertEqual(clean_item("那个包"), "包")
        self.assertEqual(clean_item("蓝色鲸鱼的小挂件"), "蓝色鲸鱼小挂件")
        self.assertEqual(parse_gift_object("送蓝色鲸鱼的小挂件"), "蓝色鲸鱼小挂件")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.plugin = FakePlugin(self.tmp)
        self.engine = GiftEngine(self.plugin)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_accessory_is_small_item(self):
        # 手机壳/电脑包 是配件，不是手机/电脑本身；当大件会把心愿静默吞掉
        for item in ("手机壳", "电脑包", "相机包", "表带"):
            self.assertEqual(self.engine.classify(item), "small", item)
        for item in ("手机", "电脑", "相机"):
            self.assertNotEqual(self.engine.classify(item), "small", item)

    def test_gift_without_wish_asks(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送给你")))
        self.assertIn("没说是什么", hint)
        self.assertEqual(self.engine.stats()["gifts"], 0)

    def test_wish_then_gift(self):
        self.engine.add_wish("羊毛围巾", "看到一条羊毛围巾，好想要", umo="test:FriendMessage:" + OWNER, uid=OWNER)
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送给你")))
        self.assertIn("羊毛围巾", hint)
        self.assertEqual(self.engine.stats()["gifts"], 1)
        self.assertEqual(self.engine.find_wish(item="羊毛围巾"), {})
        self.assertEqual(self.plugin.state.nudged, {"开心": 6, "兴奋": 4, "依恋": 3})
        self.assertEqual(len(self.plugin.db.memories), 1)
        self.assertIn("羊毛围巾", self.plugin.db.memories[0]["content"])

    def test_named_gift(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("给你买了个包")))
        self.assertIn("包", hint)
        self.assertEqual(self.engine.recent_gifts(1)[0]["item"], "包")

    def test_duplicate_message_not_recorded_twice(self):
        self.engine.add_wish("口红", "好喜欢那支口红", umo="test:FriendMessage:" + OWNER, uid=OWNER)
        event = FakeEvent("口红送给你", mid="same")
        asyncio.run(self.engine.build_hint(event))
        asyncio.run(self.engine.build_hint(event))
        self.assertEqual(self.engine.stats()["gifts"], 1)

    def test_non_owner_cannot_gift(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送给你", uid="123")))
        self.assertEqual(hint, "")
        self.assertEqual(self.engine.stats()["gifts"], 0)

    def test_relation_lover_can_gift(self):
        self.plugin.db.profiles["456"] = {"relation": "相恋"}
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送给你", uid="456")))
        self.assertIn("没说是什么", hint)

    def test_group_off_by_default(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送给你", private=False)))
        self.assertEqual(hint, "")

    def test_observe_reply_stores_wish(self):
        self.plugin.state.activity = {"activity": "逛街", "place": "东莞·商场", "zone": "mall"}
        event = FakeEvent("", mid="m2")

        async def go():
            self.engine.observe_reply(event, "今天在商场看到一条羊绒围巾，好想要啊")
            await asyncio.sleep(0.05)

        asyncio.run(go())
        self.assertEqual(self.engine.recent_wishes(1)[0]["item"], "羊绒围巾")

    def test_wall_only_on_topic(self):
        self.engine.add_gift("羊毛围巾", "送给你", uid=OWNER)
        hint = asyncio.run(self.engine.build_hint(FakeEvent("今天天气不错")))
        self.assertEqual(hint, "")
        hint = asyncio.run(self.engine.build_hint(FakeEvent("围巾好看吗")))
        self.assertIn("他送过你的东西", hint)

    def test_gift_resolves_from_user_context(self):
        # 真机：他说「还记得你刚才喜欢的那个小挂件吗」，下一句只说「送给你」
        self.plugin.db.messages = [{
            "role": "user",
            "content": "还记得你刚才喜欢的那个小挂件吗",
            "created_at": time.time() - 60,
        }]
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送给你")))
        self.assertIn("小挂件", hint)
        self.assertEqual(self.engine.recent_gifts(1)[0]["item"], "小挂件")

    def test_vague_wish_then_gift(self):
        self.plugin.state.activity = {"activity": "逛街", "place": "东莞·商场", "zone": "mall"}

        async def go():
            self.engine.observe_reply(
                FakeEvent("", mid="m20"),
                "记得啊，那个小挂件配色特别对我胃口。怎么，做出来了？",
            )
            await asyncio.sleep(0.05)
            return await self.engine.build_hint(FakeEvent("送给你", mid="m21"))

        hint = asyncio.run(go())
        self.assertIn("小挂件", hint)
        self.assertEqual(self.engine.recent_gifts(1)[0]["item"], "小挂件")

    def test_big_items_never_become_wishes(self):
        self.assertEqual(self.engine.add_wish("保时捷", "好想要辆车", umo="test:FriendMessage:" + OWNER, uid=OWNER), {})
        self.assertEqual(self.engine.add_wish("别墅", "好喜欢那套别墅", umo="test:FriendMessage:" + OWNER, uid=OWNER), {})
        self.assertEqual(self.engine.stats()["wishes"], 0)

    def test_expensive_gift_refused_by_default(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送你一辆保时捷", mid="e1")))
        self.assertIn("太贵", hint)
        self.assertEqual(self.engine.stats()["gifts"], 0)
        self.assertEqual(self.engine.asset_text(), "")

    def test_big_gift_kind_and_asset(self):
        self.plugin.config["gift_allow_big"] = True
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送你一辆保时捷", mid="b1")))
        self.assertIn("大东西", hint)
        gift = self.engine.recent_gifts(1)[0]
        self.assertEqual(gift["item"], "保时捷")
        self.assertEqual(gift["kind"], "vehicle")
        self.assertEqual(self.engine.asset_override("vehicle"), "保时捷")
        self.assertEqual(self.engine.asset_text(), "保时捷")

    def test_small_gift_has_no_asset(self):
        asyncio.run(self.engine.build_hint(FakeEvent("送你一条围巾", mid="b2")))
        self.assertEqual(self.engine.asset_text(), "")

    def test_big_item_boundary_hint(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("我想买辆车", mid="b3")))
        self.assertIn("不会让他买", hint)

    def test_watch_affordance_on_time_question(self):
        asyncio.run(self.engine.build_hint(FakeEvent("送你一块手表", mid="w1")))
        hint = asyncio.run(self.engine.build_hint(FakeEvent("现在几点了", mid="w2")))
        self.assertIn("看一眼", hint)
        self.assertIn("手表", hint)

    def test_scarf_affordance_when_cold(self):
        asyncio.run(self.engine.build_hint(FakeEvent("送你一条围巾", mid="w3")))
        hint = asyncio.run(self.engine.build_hint(FakeEvent("今天好冷", mid="w4")))
        self.assertIn("围巾", hint)

    def test_scarf_gift_in_hot_weather_waits(self):
        self.plugin.state.weather = {"temp": 28, "text": "多云"}
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送围巾", mid="s-hot")))
        self.assertIn("等天冷", hint)
        self.assertEqual(self.engine.recent_gifts(1)[0]["item"], "围巾")

    def test_scarf_not_worn_when_hot(self):
        asyncio.run(self.engine.build_hint(FakeEvent("送你一条围巾", mid="h1")))
        self.plugin.state.weather = {"temp": 30, "text": "晴"}
        hint = asyncio.run(self.engine.build_hint(FakeEvent("今天好热", mid="h2")))
        self.assertIn("这么热", hint)
        self.assertNotIn("围上他送的围巾", hint)

    def test_worn_text(self):
        asyncio.run(self.engine.build_hint(FakeEvent("送你一块手表", mid="w5")))
        asyncio.run(self.engine.build_hint(FakeEvent("送你一条围巾", mid="w6")))
        self.assertIn("手表", self.engine.worn_text())
        self.assertIn("围巾", self.engine.worn_text())

    def test_watch_is_valuable_but_not_dramatic(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送你一块手表", mid="w7")))
        self.assertNotIn("大东西", hint)
        self.assertEqual(self.engine.recent_gifts(1)[0]["kind"], "valuable")

    def test_wish_capture_only_when_shopping(self):
        event = FakeEvent("", mid="m30")

        async def go_at_home():
            self.engine.observe_reply(event, "今天看到一条围巾，好想要啊")
            await asyncio.sleep(0.05)

        asyncio.run(go_at_home())
        self.assertEqual(self.engine.stats()["wishes"], 0)
        self.plugin.state.activity = {"activity": "逛街", "place": "东莞·商场", "zone": "mall"}

        async def go_shopping():
            self.engine.observe_reply(event, "今天看到一条围巾，好想要啊")
            await asyncio.sleep(0.05)

        asyncio.run(go_shopping())
        self.assertEqual(self.engine.recent_wishes(1)[0]["item"], "围巾")

    def test_similar_gift_reminds_already_received(self):
        asyncio.run(self.engine.build_hint(FakeEvent("送你一个小挂件", mid="s1")))
        hint = asyncio.run(self.engine.build_hint(FakeEvent("今天逛街看到的那个挂件送给你", mid="s2")))
        self.assertIn("送过", hint)
        self.assertEqual(self.engine.stats()["gifts"], 1)

    def test_antique_refused_with_caring_tone(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送你一幅字画", mid="a1")))
        self.assertIn("太贵", hint)
        self.assertIn("乱花", hint)
        self.assertEqual(self.engine.stats()["gifts"], 0)

    def test_food_gift_does_not_pretend_eating(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送你一杯奶茶", mid="f1")))
        self.assertIn("吃不到", hint)
        self.assertIn("数据", hint)
        self.assertEqual(self.engine.stats()["gifts"], 0)

    def test_impossible_gift_not_received(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送你一个月亮", mid="i1")))
        self.assertIn("拿不到", hint)
        self.assertEqual(self.engine.stats()["gifts"], 0)

    def test_gift_log_has_date_and_item(self):
        asyncio.run(self.engine.build_hint(FakeEvent("送你一块手表", mid="log1")))
        log = self.engine.gift_log_text()
        self.assertIn("手表", log)
        self.assertRegex(log, r"\d{2}-\d{2}")

    def test_wish_keeps_specific_detail(self):
        wish = self.engine.add_wish("小挂件", "看到一条蓝色鲸鱼的小挂件，好想要", umo="test:FriendMessage:" + OWNER, uid=OWNER)
        self.assertEqual(wish["item"], "蓝色鲸鱼小挂件")
        wish = self.engine.add_wish("围巾", "看到一条格纹羊绒围巾，好喜欢", umo="test:FriendMessage:" + OWNER, uid=OWNER)
        self.assertEqual(wish["item"], "格纹羊绒围巾")

    def test_generic_gift_asks_for_detail(self):
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送围巾", mid="g1")))
        self.assertIn("什么颜色", hint)

    def test_generic_gift_uses_detailed_wish(self):
        self.engine.add_wish("小挂件", "看到一条蓝色鲸鱼的小挂件，好想要", umo="test:FriendMessage:" + OWNER, uid=OWNER)
        hint = asyncio.run(self.engine.build_hint(FakeEvent("送挂件", mid="gd1")))
        self.assertIn("蓝色鲸鱼小挂件", hint)
        self.assertEqual(self.engine.recent_gifts(1)[0]["item"], "蓝色鲸鱼小挂件")

    def test_panel_data(self):
        self.engine.add_wish("羊绒围巾", "看到一条羊绒围巾，好想要", umo="t", uid=OWNER)
        asyncio.run(self.engine.build_hint(FakeEvent("送羊绒围巾", mid="p1")))
        data = self.engine.panel_data()
        self.assertTrue(data["enabled"])
        self.assertTrue(any(item["item"] == "羊绒围巾" for item in data["gifts"]))
        self.assertIn("ts", data["gifts"][0])
        self.assertIn("worn", data)

    def test_food_and_impossible_never_become_wishes(self):
        self.assertEqual(self.engine.add_wish("奶茶", "好想喝奶茶", umo="t", uid=OWNER), {})
        self.assertEqual(self.engine.add_wish("月亮", "好想要月亮", umo="t", uid=OWNER), {})
        self.assertEqual(self.engine.stats()["wishes"], 0)

    def test_wear_labels_temperature(self):
        asyncio.run(self.engine.build_hint(FakeEvent("送你一条围巾", mid="wt1")))
        self.assertIn("围巾", self.engine.wear_labels(10))
        self.assertNotIn("围巾", self.engine.wear_labels(28))

    def test_commands(self):
        self.engine.add_wish("口红", "好喜欢", uid=OWNER)
        self.assertIn("口红", self.engine.command_text())
        self.assertIn("记下了", self.engine.manual_gift(FakeEvent(""), "包"))


class ManualGiftTests(unittest.TestCase):
    """命令送礼（/送礼、面板）要和聊天送礼一样：大件拒收、吃的说吃不到、送过要提。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.plugin = FakePlugin(self.tmp)
        self.engine = GiftEngine(self.plugin)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_manual_gift_records_nudges_and_memory(self):
        text = self.engine.manual_gift(FakeEvent(""), "羊毛围巾")
        self.assertIn("记下了", text)
        self.assertEqual(self.engine.stats()["gifts"], 1)
        self.assertEqual(self.plugin.state.nudged, {"开心": 6, "兴奋": 4, "依恋": 3})
        self.assertTrue(any("羊毛围巾" in m["content"] for m in self.plugin.db.memories))

    def test_manual_gift_refuses_big_item(self):
        text = self.engine.manual_gift(FakeEvent(""), "车")
        self.assertIn("太贵", text)
        self.assertEqual(self.engine.stats()["gifts"], 0)

    def test_manual_gift_food_says_cannot_eat(self):
        text = self.engine.manual_gift(FakeEvent(""), "奶茶")
        self.assertIn("吃不到", text)
        self.assertEqual(self.engine.stats()["gifts"], 0)

    def test_manual_gift_reminds_already_received(self):
        self.engine.manual_gift(FakeEvent(""), "挂件")
        text = self.engine.manual_gift(FakeEvent(""), "小挂件")
        self.assertIn("送过", text)
        self.assertEqual(self.engine.stats()["gifts"], 1)

    def test_manual_gift_disabled_does_nothing(self):
        self.plugin.config["gift_enable"] = False
        self.assertEqual(self.engine.manual_gift(FakeEvent(""), "围巾"), "")
        self.assertEqual(self.engine.stats()["gifts"], 0)

    def test_manual_gift_bad_usage(self):
        self.assertIn("用法", self.engine.manual_gift(FakeEvent(""), ""))


class ShopHintTests(unittest.TestCase):
    """逛街提示：不是每次逛街都提；有概率、有间隔、有每日上限，重载也不重置。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.plugin = FakePlugin(self.tmp)
        self.plugin.state.activity = {"activity": "逛街", "place": "东莞·商场", "zone": "mall"}
        self.engine = GiftEngine(self.plugin)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _hint(self, text="今天好热", mid="shop"):
        return asyncio.run(self.engine.build_hint(FakeEvent(text, mid=mid)))

    def test_chance_zero_never_hints(self):
        self.plugin.config["gift_shop_hint_chance"] = 0.0
        for i in range(5):
            self.assertNotIn("【逛街】", self._hint(mid="s%d" % i))

    def test_chance_one_hints_once_per_gap(self):
        self.plugin.config["gift_shop_hint_chance"] = 1.0
        self.plugin.config["gift_shop_hint_gap_hours"] = 6
        self.assertEqual(self._hint(mid="a").count("【逛街】"), 1)
        self.assertNotIn("【逛街】", self._hint(mid="b"))

    def test_daily_cap(self):
        self.plugin.config["gift_shop_hint_chance"] = 1.0
        self.plugin.config["gift_shop_hint_max_per_day"] = 1
        self.assertEqual(self._hint(mid="c").count("【逛街】"), 1)
        meta = self.engine._load()["meta"]["shop_hint"]
        meta["ts"] = time.time() - 10 * 3600
        self.engine._save()
        self.assertNotIn("【逛街】", self._hint(mid="d"))

    def test_new_day_allows_again(self):
        self.plugin.config["gift_shop_hint_chance"] = 1.0
        self.assertEqual(self._hint(mid="e").count("【逛街】"), 1)
        meta = self.engine._load()["meta"]["shop_hint"]
        meta["day"] = "2020-01-01"
        meta["ts"] = time.time() - 10 * 3600
        self.engine._save()
        self.assertEqual(self._hint(mid="f").count("【逛街】"), 1)

    def test_reload_does_not_reset(self):
        self.plugin.config["gift_shop_hint_chance"] = 1.0
        self.assertEqual(self._hint(mid="g").count("【逛街】"), 1)
        again = GiftEngine(self.plugin)
        self.assertNotIn("【逛街】", asyncio.run(again.build_hint(FakeEvent("今天好热", mid="h"))))

    def test_purge_user_clears_gifts(self):
        self.engine.add_gift(item="挂件", raw="送你", umo="u1", uid=OWNER, source="chat")
        self.assertEqual(self.engine.purge_user(OWNER), 1)
        self.assertEqual(self.engine.stats()["gifts"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
