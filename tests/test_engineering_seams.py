# -*- coding: utf-8 -*-
"""工程缝：事件总线与外部能力端口的最小契约测试。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_events import EventBus, ScheduleChanged  # noqa: E402
from amo_ports import (  # noqa: E402
    MarketCalendarPort,
    MarketDataPort,
    MarketSession,
    Quote,
    SocialFeedPort,
    SocialItem,
)

class EventBusTests(unittest.TestCase):
    def test_typed_dispatch_and_unsubscribe(self):
        bus = EventBus()
        got = []
        unsubscribe = bus.subscribe(ScheduleChanged, got.append)
        bus.publish(ScheduleChanged(day="2026-11-13", version=1))
        bus.publish(object())
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].day, "2026-11-13")
        unsubscribe()
        bus.publish(ScheduleChanged(day="2026-11-14"))
        self.assertEqual(len(got), 1)

    def test_publish_collect_returns_subscriber_results(self):
        bus = EventBus()
        bus.subscribe(ScheduleChanged, lambda event: ["task-1"])
        bus.subscribe(ScheduleChanged, lambda event: None)
        self.assertEqual(bus.publish_collect(ScheduleChanged(day="x")), [["task-1"]])

    def test_subscriber_failure_does_not_break_publisher(self):
        bus = EventBus()
        got = []
        def broken(event):
            raise RuntimeError("boom")
        bus.subscribe(ScheduleChanged, broken)
        bus.subscribe(ScheduleChanged, got.append)
        self.assertEqual(bus.publish(ScheduleChanged(day="x")), 1)
        self.assertEqual(len(got), 1)

from plugin_fakes import EngineeringSeamsFakeMarket as FakeMarket, EngineeringSeamsFakeSocial as FakeSocial  # noqa: E402

class PortContractTests(unittest.TestCase):
    def test_fake_market_satisfies_ports(self):
        market = FakeMarket()
        self.assertIsInstance(market, MarketDataPort)
        self.assertIsInstance(market, MarketCalendarPort)

    def test_fake_social_satisfies_feed_port(self):
        self.assertIsInstance(FakeSocial(), SocialFeedPort)

if __name__ == "__main__":
    unittest.main()