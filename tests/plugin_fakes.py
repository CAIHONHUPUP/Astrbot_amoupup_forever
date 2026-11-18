# -*- coding: utf-8 -*-
"""Phase 18：本插件测试里重复定义的 Fake* 统一收口到这里（只搬位置，断言不变）。

三插件的 tests/support 保持逐字节一致，本文件放插件特有 / 历史测试专用桩。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import os
import random
import sqlite3
import sys
import threading
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class BalanceFakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

class CrossPlatformOrderFakeEvent:
    pass

class CrossPlatformOrderFakeSelf:
    def _data_mem(self):
        return object()

    def _data_bot_id(self):
        return "大肥鱼"

    def _platform_of(self, event):
        return "lark"

    async def _data_call(self, *args, **kwargs):
        rows = [{"platform": "lark", "uid": "1", "role": "summary",
                 "content": "摘要：这是摘要不是他原话", "created_at": 900}]
        for index in range(8, 0, -1):
            rows.append({
                "platform": "aiocqhttp", "uid": "1", "role": "user",
                "content": "第%d条（%s）" % (
                    index, "最新：摸鱼被领导骂了" if index == 8 else "旧消息"),
                "created_at": 100 * index,
            })
        return {"recent": rows}

class EngineeringSeamsFakeMarket:
    async def quote(self, symbol):
        return Quote(symbol=symbol, price=1.0)

    async def history(self, symbol, days=20):
        return [Quote(symbol=symbol, price=1.0)]

    async def session(self, day):
        return MarketSession(day=day, is_trading_day=True)

class EngineeringSeamsFakeSocial:
    async def timeline(self, limit=20):
        return [SocialItem(item_id="1", author="a", text="hello")]

    async def search(self, query, limit=20):
        return []

class ScheduleSkeletonsFakeState:
    def __init__(self):
        self.p = types.SimpleNamespace(gift=None)
        self.db = None

class WalletFakeDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
