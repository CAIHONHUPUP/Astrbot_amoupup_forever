# -*- coding: utf-8 -*-
"""价目表唯一实现（2026-11-17 从根目录 price_book.py 搬进 domains/finance）。

只读静态价目表：版本化区间价（分），无随机、不写库。
查询优先级：city+item → city 通用(item="") → *+item → * 通用(item="")；城市空/未知回退 *。
版本按 effective_from 取不晚于 moment 的最近一版；v1 与 TRIP_COST_RANGES 逐项一致（A/B 测试锁）。

两个「可打补丁」的模块级变量（TABLE_PATH / FORCE_VERSION）留在根门面 price_book.py：
测试用 mock.patch.object(pb, "TABLE_PATH", ...) 换表、直接 pb.FORCE_VERSION = "v2" 强制版本。
门面启动时通过 bind_path_provider / bind_version_provider 把自己的模块全局读法绑进来，
本实现每次调用都经 provider 取值，所以 patch 门面变量立即生效（不缓存这两个值）。
"""
from __future__ import annotations

from typing import Any, Callable, Optional, TYPE_CHECKING

import datetime as dt
import json
import os

try:  # 统一时钟：默认 moment 取插件时区的“今天”
    from ... import clock as _clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _clock

#: 插件根目录（domains/finance/price_book.py 向上三层）。
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: 默认价目表路径：price_book.json 留在插件根目录（数据不随实现搬进 domains）。
DEFAULT_TABLE_PATH = os.path.join(PLUGIN_ROOT, "price_book.json")

#: 门面绑定的 provider：返回当前 TABLE_PATH / FORCE_VERSION，让测试 patch 门面即生效。
_path_provider: Optional[Callable[[], Any]] = None
_version_provider: Optional[Callable[[], Any]] = None

#: 进程内价目表缓存：{绝对路径: ((st_mtime_ns, st_size), 解析结果)}
#: 文件一变（mtime/size）立即失效；版本仍由 _pick 按 effective_from 选，换版本不改缓存语义。
_CACHE: dict[str, Any] = {}


def bind_path_provider(provider: Optional[Callable[[], Any]]) -> None:
    """绑定「价目表路径」读取钩子（门面用 lambda 读自己的模块全局）。"""
    global _path_provider
    _path_provider = provider


def bind_version_provider(provider: Optional[Callable[[], Any]]) -> None:
    """绑定「强制版本」读取钩子（门面用 lambda 读自己的模块全局）。"""
    global _version_provider
    _version_provider = provider


def table_path() -> str:
    """当前价目表路径：优先门面 provider，其次本文件默认（插件根 price_book.json）。"""
    provider = _path_provider
    value = provider() if provider is not None else None
    return os.path.abspath(str(value or DEFAULT_TABLE_PATH))


def forced_version() -> str:
    """当前强制版本：优先门面 provider，未绑定/空串时返回 ""。"""
    provider = _version_provider
    value = provider() if provider is not None else ""
    return str(value or "").strip()


def clear_cache():
    """清空进程内价目表缓存（测试/维护用；线上靠文件 mtime 自动失效）。"""
    _CACHE.clear()


def _signature(target):
    """价目表的失效标记：mtime_ns + size；读不到就抛 PriceBookError（与旧口径一致）。"""
    try:
        stat = os.stat(target)
    except OSError as exc:
        raise PriceBookError("价目表读不到: %s" % exc) from exc
    return (stat.st_mtime_ns, stat.st_size)


class PriceBookError(ValueError):
    """价目表缺失、JSON 非法、字段不合法或查不到价。"""


def _date(text):
    try:
        return dt.date.fromisoformat(str(text or "").strip())
    except ValueError as exc:
        raise PriceBookError("effective_from 不是 ISO 日期: %r" % (text,)) from exc


def _entry(raw):
    if not isinstance(raw, dict):
        raise PriceBookError("价目行不是对象")
    version, category, unit = (str(raw.get(key) or "").strip()
                               for key in ("version", "category", "unit"))
    if not version or not category or not unit:
        raise PriceBookError("价目行缺少 version/category/unit: %r" % (raw,))
    low, high = raw.get("min_cents"), raw.get("max_cents")
    if (isinstance(low, bool) or isinstance(high, bool)
            or not isinstance(low, int) or not isinstance(high, int)):
        raise PriceBookError("价目行金额必须是整数分: %r" % (raw,))
    if low < 0 or high < low:
        raise PriceBookError("价目行区间非法（0 <= min <= max）: %r" % (raw,))
    mult = raw.get("multiplier")
    try:
        mult = None if mult is None else float(mult)
    except (TypeError, ValueError) as exc:
        raise PriceBookError("价目行 multiplier 非法: %r" % (raw,)) from exc
    if mult is not None and mult <= 0:
        raise PriceBookError("价目行 multiplier 必须 > 0: %r" % (raw,))
    return {"version": version, "effective_from": _date(raw.get("effective_from")),
            "multiplier": mult, "city": str(raw.get("city") or "").strip() or "*",
            "category": category, "item": str(raw.get("item") or "").strip(),
            "min_cents": low, "max_cents": high, "unit": unit}


def load(path=None):
    """读并校验价目表；只读、不写文件，进程内按 (路径, mtime/size) 缓存。

    缓存只省「读盘 + 全表校验」，不改变选版口径：同一份静态表在旅行结算的
    20 条账目里只解析一次；价目表文件被改/替换后 mtime 或 size 变化立即重读，
    不会把旧价当成新价（无脏读）。
    """
    target = os.path.abspath(path or table_path())
    signature = _signature(target)
    cached = _CACHE.get(target)
    if cached is not None and cached[0] == signature:
        return cached[1]
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise PriceBookError("价目表读不到: %s" % exc) from exc
    except ValueError as exc:
        raise PriceBookError("价目表 JSON 非法: %s" % exc) from exc
    rows = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise PriceBookError("价目表缺少 entries 列表")
    tables: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = _entry(raw)
        table = tables.setdefault(row["version"], {
            "version": row["version"], "effective_from": row["effective_from"],
            "multiplier": row["multiplier"], "entries": []})
        if (table["effective_from"] != row["effective_from"]
                or table["multiplier"] != row["multiplier"]):
            raise PriceBookError("%s 的版本元数据不一致" % row["version"])
        table["entries"].append(row)
    result = {"tables": list(tables.values())}
    _CACHE[target] = (signature, result)
    return result


def _pick(tables, moment):
    version = forced_version()
    if version:
        found = [table for table in tables if table["version"] == version]
        if not found:
            raise PriceBookError("强制版本不存在: %s" % version)
        return found[0]
    if not tables:
        raise PriceBookError("价目表为空")
    moment = _clock.today() if moment is None else moment
    when = (moment.date() if isinstance(moment, dt.datetime) else
            moment if isinstance(moment, dt.date) else
            _date(moment) if isinstance(moment, str) else None)
    if when is None:
        raise PriceBookError("moment 不支持: %r" % (moment,))
    ready = [table for table in tables if table["effective_from"] <= when]
    if not ready:
        return min(tables, key=lambda table: table["effective_from"])
    return max(ready, key=lambda table: table["effective_from"])


def quote(plugin=None, category=None, item=None, city="", moment=None):
    """命中行的 {version,min_cents,max_cents,unit}；查不到抛 PriceBookError。"""
    table = _pick(load()["tables"], moment)
    category, city, item = (str(category or "").strip(), str(city or "").strip() or "*",
                            str(item or "").strip())
    pairs = dict.fromkeys(((city, item), (city, ""), ("*", item), ("*", "")))
    for want_city, want_item in pairs:
        for row in table["entries"]:
            if (row["category"] == category and row["city"] == want_city
                    and row["item"] == want_item):
                return {"version": table["version"], "min_cents": row["min_cents"],
                        "max_cents": row["max_cents"], "unit": row["unit"]}
    raise PriceBookError("价目表无此价格: category=%r item=%r city=%r"
                         % (category, item, city))


def price(plugin=None, category=None, item=None, city="", moment=None):
    """区间价 (min_cents, max_cents)；只读、无随机。"""
    hit = quote(plugin, category, item, city, moment)
    return (hit["min_cents"], hit["max_cents"])


def multiplier(plugin=None, moment=None):
    return _pick(load()["tables"], moment).get("multiplier")
