# -*- coding: utf-8 -*-
"""心愿「待确认」审核队列（2026-11-19）。

规则：
- 模型 confidence >= 0.75 → 直接进心愿墙（wanted）；
- 模型拿不准（<0.75）或规则兜底 → 进待确认（pending），面板「收下」才转正；
- 面板「删除」→ 只删这一条，不做长期封禁（她说同一件可以再进待确认）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_routes  # noqa: E402
from amo_api import ApiMixin  # noqa: E402
from gift_engine import GiftEngine  # noqa: E402


def make_engine():
    engine = object.__new__(GiftEngine)
    engine._lock = threading.RLock()
    engine._data = {"wishes": [], "gifts": [], "meta": {}}
    engine.path = "gifts.json"
    engine._save = lambda: None
    engine.classify = lambda item: "small"
    engine._place = lambda: "商场"
    engine._short_place = lambda place: place
    engine.asset_text = lambda: ""
    engine.worn_text = lambda: ""
    return engine


def with_ttl(seconds=3600):
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(mock.patch.object(GiftEngine, "ttl", new_callable=mock.PropertyMock,
                                          return_value=seconds))
    stack.enter_context(mock.patch.object(GiftEngine, "enabled", new_callable=mock.PropertyMock,
                                          return_value=True))
    return stack


class WishStatusTests(unittest.TestCase):
    def test_confident_wish_goes_straight_to_wall(self):
        engine = make_engine()
        wish = engine.add_wish("蓝色鲸鱼小挂件", "好想要蓝色鲸鱼小挂件", confidence=0.9)
        self.assertEqual(wish["status"], "wanted")
        with with_ttl():
            self.assertEqual(engine.pending_wishes(), [])
            self.assertEqual([w["item"] for w in engine.panel_data()["wishes"]],
                             ["蓝色鲸鱼小挂件"])

    def test_uncertain_wish_waits_for_review(self):
        engine = make_engine()
        wish = engine.add_wish("格纹围巾", "好喜欢那个格纹围巾", confidence=0.5)
        self.assertEqual(wish["status"], "pending")
        with with_ttl():
            self.assertEqual([w["item"] for w in engine.pending_wishes()], ["格纹围巾"])
            data = engine.panel_data()
            self.assertEqual(data["wishes"], [])
            self.assertEqual([w["item"] for w in data["pending"]], ["格纹围巾"])

    def test_regex_fallback_wish_waits_for_review(self):
        # 规则兜底没有 confidence：宁可让面板点一下，也不直接进墙
        engine = make_engine()
        wish = engine.add_wish("奶白色毛衣", "好想要奶白色毛衣", confidence=None)
        self.assertEqual(wish["status"], "pending")

    def test_approve_moves_wish_to_wall(self):
        engine = make_engine()
        wish = engine.add_wish("格纹围巾", "好喜欢", confidence=0.4)
        approved = engine.approve_wish(wish["id"])
        self.assertEqual(approved["status"], "wanted")
        with with_ttl():
            self.assertEqual(engine.pending_wishes(), [])
            self.assertEqual([w["item"] for w in engine.panel_data()["wishes"]], ["格纹围巾"])

    def test_reject_deletes_just_this_wish(self):
        engine = make_engine()
        wish = engine.add_wish("豆沙色口红", "好想要豆沙色口红", confidence=0.5)
        self.assertEqual(wish["status"], "pending")
        gone = engine.reject_wish(wish["id"])
        self.assertEqual(gone["item"], "豆沙色口红")
        with with_ttl():
            self.assertEqual(engine.pending_wishes(), [])
            self.assertEqual(engine.panel_data()["pending"], [])
        self.assertEqual(engine.stats()["wishes"], 0)
        # 只删这一条，不做封禁：她再说同一件，照样进待确认
        again = engine.add_wish("豆沙色口红", "又提到豆沙色口红", confidence=0.9)
        self.assertEqual(again["status"], "wanted")

    def test_high_confidence_upgrades_existing_pending(self):
        engine = make_engine()
        first = engine.add_wish("豆沙色口红", "好想要口红", confidence=0.5)
        again = engine.add_wish("豆沙色口红", "好想要豆沙色口红啊", confidence=0.9)
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(again["status"], "wanted")

    def test_review_does_not_touch_received_gifts(self):
        engine = make_engine()
        now = 1770000000.0
        engine._data["gifts"] = [{"id": "g1", "item": "羊毛围巾", "ts": now,
                                  "kind": "small", "place": "商场"}]
        wish = engine.add_wish("豆沙色口红", "好想要豆沙色口红", confidence=0.4)
        with with_ttl():
            self.assertEqual(engine.stats()["gifts"], 1)
            engine.approve_wish(wish["id"])
            self.assertEqual(engine.stats()["gifts"], 1)
            self.assertEqual([g["item"] for g in engine.panel_data()["gifts"]], ["羊毛围巾"])
            engine.reject_wish(wish["id"])
            self.assertEqual(engine.stats()["gifts"], 1)
            self.assertEqual([g["item"] for g in engine.panel_data()["gifts"]], ["羊毛围巾"])

    def test_review_routes_are_post_only(self):
        self.assertIn("/gift/wish/approve", amo_routes.WRITE_ROUTES)
        self.assertIn("/gift/wish/reject", amo_routes.WRITE_ROUTES)

    def test_api_approve_and_reject(self):
        engine = make_engine()
        wish = engine.add_wish("小熊睡衣", "好想要小熊睡衣", confidence=0.4)

        class Fake:
            gift = engine

            async def _ensure_ready(self):
                return None

            async def _body(self):
                return {"id": wish["id"]}

        fake = Fake()
        res = asyncio.run(ApiMixin.api_gift_wish_approve(fake))
        self.assertEqual(res["code"], 0, res)
        self.assertEqual(res["data"]["status"], "wanted")
        self.assertEqual(asyncio.run(ApiMixin.api_gift_wish_approve(fake))["code"], 1)


if __name__ == "__main__":
    unittest.main()
