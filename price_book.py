# -*- coding: utf-8 -*-
"""价目表门面（2026-11-17：实现搬进 domains/finance/price_book.py）。

本文件只做 re-export，旧导入路径与输出完全不变：
- amo_food_cost / amo_trip_cost 仍然 from . import price_book as _price_book；
- 全部测试仍然 import price_book as pb 后调 pb.price / pb.quote / pb.multiplier / pb.clear_cache。

关键约束（测试依赖）：TABLE_PATH 与 FORCE_VERSION 必须继续是**本模块的模块级变量**，
因为测试用 mock.patch.object(pb, "TABLE_PATH", ...) 换表、直接 pb.FORCE_VERSION = "v2" 强制版本。
实现里不缓存这两个值：这里用 lambda 把「读门面模块全局」的 provider 绑给实现，
所以 patch/赋值门面变量后，下一次 pb.price(...) 立刻按新值走（见 test_price_book）。
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

try:  # 包模式
    from .domains.finance import price_book as _impl
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.finance import price_book as _impl

#: 价目表 JSON 仍留在插件根目录（数据不搬进 domains）。
TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "price_book.json")
#: 测试/演示钩子：强制版本（空串 = 按 effective_from 自动选版）。
FORCE_VERSION = os.environ.get("AMO_PRICE_VERSION", "").strip()

#: 把「读门面模块全局」的钩子绑给实现：lambda 在本模块命名空间求值，
#: 于是 mock.patch.object(pb, "TABLE_PATH", ...) 与 pb.FORCE_VERSION = ... 立即生效。
_impl.bind_path_provider(lambda: TABLE_PATH)
_impl.bind_version_provider(lambda: FORCE_VERSION)

PriceBookError = _impl.PriceBookError
clear_cache = _impl.clear_cache
load = _impl.load
quote = _impl.quote
price = _impl.price
multiplier = _impl.multiplier
