# -*- coding: utf-8 -*-
"""事实池：聊天过滤、素材 Prompt、事后拦截、穿搭校验。"""

import asyncio
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402
astrbot_stub.install()

import amo_fact_pool as fp  # noqa: E402


class ChatFactTests(unittest.TestCase):
    def _rows(self):
        return [
            {"role": "user", "umo": "lark:FriendMessage:1", "content": "多喝热水，早点休息", "created_at": 1},
            {"role": "user", "umo": "lark:FriendMessage:1", "content": "/转账 50", "created_at": 2},
            {"role": "user", "umo": "lark:FriendMessage:1", "content": "[图片]", "created_at": 3},
            {"role": "user", "umo": "lark:FriendMessage:1", "content": "<thought>内部</thought> 想你", "created_at": 4},
            {"role": "bot", "umo": "lark:FriendMessage:1", "content": "我也想你", "created_at": 5},
            {"role": "user", "umo": "lark:GroupMessage:9", "content": "哈哈哈", "created_at": 6},
        ]

    def test_private_keywords_only(self):
        facts = fp.chat_facts(self._rows())
        texts = [f["detail"] for f in facts]
        self.assertTrue(any("多喝热水" in t for t in texts))
        self.assertFalse(any("/转账" in t or "[图片]" in t or "thought" in t for t in texts))
        self.assertTrue(any("[我对他说]" in t for t in texts))
        self.assertFalse(any("群里" in t for t in texts))

    def test_no_emotion_labels_during_extract(self):
        facts = fp.chat_facts(self._rows())
        for fact in facts:
            self.assertNotIn("生气", fact["detail"])
            self.assertNotIn("开心", fact["detail"])


class FakeDB:
    def __init__(self, kv=None):
        self.kv = kv or {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def get_schedule(self, day):
        return []


class FakeState:
    def __init__(self):
        self.schedule = {"2026-11-15": [
            {"start": "08:00", "activity": "在家休息", "place": "家"},
            {"start": "20:00", "activity": "未来安排", "place": "外面"},
        ]}
        self.db = FakeDB()

    def health_info(self):
        return {"status": "感冒", "sick": True, "note": "低烧"}


class FakeWallet:
    def ledger(self, limit=50):
        return [{"ts": dt.datetime(2026, 11, 15, 12, 0).timestamp(),
                 "delta_cents": -2000, "reason": "外卖"}]


class FakeWardrobe:
    def current(self, day=None):
        return {"text": "黑色高领毛衣，在家穿的睡裤"}


class FakePlugin:
    def __init__(self):
        self.state = FakeState()
        self.wallet = FakeWallet()
        self.wardrobe = FakeWardrobe()
        self.db = FakeDB()

    async def _data_call(self, *args, **kwargs):
        return [{"role": "user", "umo": "lark:FriendMessage:1",
                 "content": "感冒好点没，多喝水", "created_at": 1}]

    def _data_bot_id(self):
        return "bot"


class PoolTests(unittest.TestCase):
    def test_pool_has_sources_and_no_future(self):
        pool = asyncio.run(fp.build_fact_pool(FakePlugin(), "2026-11-15",
                                              dt.datetime(2026, 11, 15, 19, 0)))
        details = " ".join(f["detail"] for f in pool["facts"])
        self.assertIn("在家休息", details)
        self.assertIn("外卖", details)
        self.assertIn("感冒", details)
        self.assertIn("多喝水", details)
        self.assertNotIn("未来安排", details)
        self.assertIn("睡裤", pool["meta"]["outfit"])

    def test_prompt_has_tiers_and_sources(self):
        pool = {"facts": [{"time": "08:00", "type": "日程", "detail": "在家休息"}],
                "meta": {"outfit": "睡裤"}}
        text = fp.render_prompt(pool)
        for token in ("【物理事实】", "【线上事实】", "【心理事实】", "【严禁虚构】",
                      "【间接引语】", "【穿搭】", "【消费】", "【作息】",
                      "【今天没怎么聊天】", "素材（JSON）"):
            self.assertIn(token, text)

    def test_validate_blocks_phone_and_system_log(self):
        pool = {"facts": [{"type": "日程", "detail": "在家休息"}], "meta": {}}
        text = fp.validate_text("[系统提示] 刚和老公通电话了。今天好多了。", pool)
        self.assertNotIn("[系统提示]", text)
        self.assertNotIn("通电话", text)

    def test_validate_extreme_and_new_clothes(self):
        pool = {"facts": [{"type": "日程", "detail": "在家休息"}], "meta": {}}
        text = fp.validate_text("今天穿了新买的碎花裙，不想活了。", pool)
        self.assertNotIn("新买的", text)
        self.assertNotIn("不想活", text)
        self.assertIn("委屈", text)

    def test_validate_keeps_bought_clothes(self):
        pool = {"facts": [{"type": "消费", "detail": "购物 碎花裙 199 元"}],
                "meta": {"bought_clothes": [{"detail": "购物 碎花裙"}]}}
        text = fp.validate_text("今天穿了新买的碎花裙。", pool)
        self.assertIn("新买的碎花裙", text)



    def test_subject_reversal_fixed(self):
        pool = {"facts": [{"type": "线上聊天", "detail": "[他对我说]：多喝热水"}],
                "meta": {"has_chat": True}}
        out = fp.validate_text("我让他多喝热水。", pool)
        self.assertIn("他提醒我多喝热水", out)

    def test_empty_chat_blocks_anxiety(self):
        pool = {"facts": [{"type": "日程", "detail": "在家休息"}], "meta": {"has_chat": False}}
        out = fp.validate_text("他是不是不爱我了，他是不是去找别人了。", pool)
        self.assertNotIn("不爱我", out)
        self.assertIn("很忙", out)

    def test_spending_without_item(self):
        pool = {"facts": [{"type": "消费", "detail": "外卖 15.00 元"}], "meta": {}}
        out = fp.validate_text("今天买了一个超可爱的毛绒玩具。", pool)
        self.assertNotIn("毛绒玩具", out)
        self.assertIn("买了点东西", out)

    def test_routine_ignores_prepare_sleep_at_midnight(self):
        facts = fp._routine_facts([
            {"start": "00:00", "end": "06:00", "activity": "睡觉", "place": "家", "category": "sleep"},
            {"start": "23:00", "end": "24:00", "activity": "准备睡觉", "place": "家", "category": "sleep"},
        ])
        details = " ".join(f["detail"] for f in facts)
        self.assertNotIn("24:00", details)
        self.assertNotIn("睡到", details)

    def test_past_day_pool_does_not_cut_at_wall_clock(self):
        class PastState(FakeState):
            def __init__(self):
                self.schedule = {"2026-11-14": [
                    {"start": "08:00", "activity": "早班", "place": "公司"},
                    {"start": "20:00", "activity": "下班回家", "place": "家"},
                ]}
                self.db = FakeDB()

        plugin = FakePlugin()
        plugin.state = PastState()
        pool = asyncio.run(fp.build_fact_pool(plugin, "2026-11-14",
                                              dt.datetime(2026, 11, 15, 9, 0)))
        details = " ".join(f["detail"] for f in pool["facts"])
        self.assertIn("下班回家", details)

    def test_routine_blocks_early_rise(self):
        pool = {"facts": [{"type": "作息", "detail": "今天睡到 10:00 才起"}],
                "meta": {"wake_late": True}}
        out = fp.validate_text("今天早起去晨跑。", pool)
        self.assertNotIn("早起", out)
        self.assertNotIn("晨跑", out)

    def test_schedule_detail_has_no_love_tail(self):
        from amo_diary_guard import clean_schedule_text
        out = clean_schedule_text("08:00 楼下小馆吃早餐，突然好想老公")
        self.assertNotIn("好想老公", out)
        self.assertIn("早餐", out)

    def test_historical_diary_read_only(self):
        import inspect
        from amo_schedule import ScheduleMixin
        # 2026-11-15 拆函数后，「历史日记只读」这段落到 _write_one_daily_diary
        source = inspect.getsource(ScheduleMixin._write_one_daily_diary)
        self.assertIn("历史日记只读", source)


if __name__ == "__main__":
    unittest.main()
