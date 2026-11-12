# -*- coding: utf-8 -*-
"""离线「中文地名 → 和风天气 LocationID」映射。

数据在 city_ids.json（结构 {省: {市: {短名: LocationID}}}，由官方
LocationList 生成，见 tools/gen_city_ids.py）。纯标准库、无 astrbot 依赖，
方便单测和面板复用。

支持的写法：
    东莞 / 东莞市 / 广东东莞 / 广东省东莞市 / 东莞南城
    北京 / 北京朝阳 / 朝阳区
    101281601（数字原样返回）
"""

from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

import json
import os
import re
import threading

FILE_NAME = "city_ids.json"
_DIR = os.path.dirname(os.path.abspath(__file__))

# 行政区划后缀：先长后短，剥掉才能让「东莞市」和「东莞」等价
_SUFFIXES = (
    "特别行政区", "自治区", "自治州", "自治县", "自治旗",
    "新区", "地区", "省", "市", "区", "县", "盟", "旗", "镇", "乡",
)

_SPLIT = re.compile(r"[\s·•\-_,，、/\\|]+")

_lock = threading.Lock()
_table = None      # 省 -> 市 -> 名 -> ID
_rows: list[tuple] = []         # [(省, 市, 名, ID, 级别)]
_groups: dict[tuple, dict] = {}       # (省stem, 市stem) -> {名stem: (省, 市, 名, ID)}
_provinces: list[tuple] = []    # [(省stem, 省)]
_cities: dict[str, list] = {}       # 市stem -> [(省, 市, 名, ID)]（地级市那一行）
_names: dict[str, list] = {}        # 名stem -> [(省, 市, 名, ID, 级别)]


def _norm(text) -> str:
    return _SPLIT.sub("", str(text or "").strip())


def _stem(text) -> str:
    """去掉末尾的行政后缀（只剥一层就够，调用方可重复）。"""
    value = _norm(text)
    for suffix in _SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def _variants(text) -> tuple:
    """同一地名的两种写法：原样 + 去后缀，长的在前。"""
    value = _norm(text)
    stem = _stem(value)
    return (value,) if value == stem else (value, stem)


_LEAD = ("市", "区", "县", "省", "盟", "旗")


def _strip_lead(text) -> str:
    """去掉「东莞市莞城区」里多出来的那个「市」。"""
    value = _norm(text)
    while value and value[0] in _LEAD and len(value) > 1:
        value = value[1:]
    return value


def _level(prov: str, city: str, name: str) -> int:
    # 地级市（含直辖市本身）=1，区县=2；省级不单独成行，当成城市用
    if _stem(name) == _stem(city):
        return 1
    return 2


def _load():
    global _table, _rows, _groups, _provinces, _cities, _names
    if _table is not None:
        return _table
    with _lock:
        if _table is not None:
            return _table
        table = {}
        try:
            with open(os.path.join(_DIR, FILE_NAME), "r", encoding="utf-8") as fh:
                table = json.load(fh)
            if not isinstance(table, dict):
                table = {}
        except Exception as _amo_exc:
            _note_swallowed("amo_city_index._load", _amo_exc)
            table = {}
        rows, groups, provinces = [], {}, []
        cities, names = {}, {}
        for prov, city_map in table.items():
            if not isinstance(city_map, dict):
                continue
            prov_stem = _stem(prov)
            provinces.append((prov_stem, prov))
            for city, name_map in city_map.items():
                if not isinstance(name_map, dict):
                    continue
                city_stem = _stem(city)
                group = groups.setdefault((prov_stem, city_stem), {})
                for name, loc_id in name_map.items():
                    loc_id = str(loc_id or "").strip()
                    if not loc_id:
                        continue
                    row = (prov, city, name, loc_id)
                    level = _level(prov, city, name)
                    name_stem = _stem(name)
                    rows.append(row + (level,))
                    group[name_stem] = row
                    names.setdefault(name_stem, []).append(row + (level,))
                    if level == 1:
                        cities.setdefault(city_stem, []).append(row + (level,))
        _table = table
        _rows = rows
        _groups = groups
        _provinces = provinces
        _cities = cities
        _names = names
        return _table


def _hit(row, match: str = "") -> dict:
    prov, city, name, loc_id = row[0], row[1], row[2], row[3]
    display = _norm(name) or name
    return {
        "id": str(loc_id),
        "name": display,
        "province": _norm(prov) or prov,
        "city": _norm(city) or city,
        "label": "%s·%s" % (_norm(prov) or prov, _norm(city) or city),
        "level": _level(prov, city, name),
        "match": match or display,
        "ok": True,
    }


def _approx(row, reason: str = "该区/镇不在离线表里，已按所属市查询") -> dict:
    hit = _hit(row)
    hit["approx"] = True
    hit["note"] = reason
    return hit


def _best(rows):
    """同名多条时按级别挑：地级市 > 区县 > 省级；同级保持官方顺序。"""
    if not rows:
        return None
    return sorted(rows, key=lambda row: row[4])[0][:4]


def _split_prefix(text: str, head) -> str | None:
    """text 去掉 head（任一写法）前缀；不匹配返回 None，全等返回空串。"""
    for form in _variants(head):
        if text.startswith(form):
            return text[len(form):]
    return None


def _label(row) -> str:
    return "%s·%s" % (_norm(row[0]) or row[0], _norm(row[1]) or row[1])


def _mark_ambiguous(hit, rows, text) -> dict:
    """同名多条时在结果里带上候选清单，方便面板/日志提示（默认选择不变）。"""
    if not hit or not rows or len(rows) <= 1:
        return hit
    labels = []
    for row in rows[:6]:
        label = _label(row)
        if label not in labels:
            labels.append(label)
    chosen = (_norm(hit.get("province") or ""), _norm(hit.get("city") or ""))
    alt = next((row for row in rows if (_norm(row[0]), _norm(row[1])) != chosen), rows[0])
    alt_city = _stem(alt[1]) or _norm(alt[1])
    alt_name = _norm(alt[2]) or alt[2]
    if alt_city == alt_name or _level(alt[0], alt[1], alt[2]) == 1:
        example = (_stem(alt[0]) or _norm(alt[0])) + alt_city
    else:
        example = alt_city + alt_name
    hit["ambiguous"] = len(rows)
    hit["candidates"] = labels
    hit["note"] = "「%s」有 %d 个同名，默认用了 %s；要换成 %s 就写「%s」" % (
        hit.get("match") or str(text or ""), len(rows), hit.get("label", ""), _label(alt), example,
    )
    return hit


def resolve_city(text):
    """把中文地名解析成 LocationID；解析不了返回 None。数字原样返回。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return {
            "id": raw, "name": raw, "province": "", "city": "",
            "label": raw, "level": 9, "match": raw, "ok": True,
        }
    table = _load()
    if not table:
        return None

    norm = _norm(raw)

    # 1) 带省前缀：「广东省东莞市」「广东东莞」「北京朝阳」
    for prov_stem, prov in _provinces:
        rest = _split_prefix(norm, prov)
        if not rest:
            continue
        # 1a) 省 + 市（可能再跟区县）
        city_fallback = None
        for city_stem, city_rows in _cities.items():
            for prov_row, city_name, _n, _i, _lvl in city_rows:
                if _stem(prov_row) != prov_stem:
                    continue
                tail = _split_prefix(rest, city_name)
                if tail is None:
                    continue
                group = _groups.get((prov_stem, city_stem)) or {}
                if not tail:
                    return _hit((prov_row, city_name, _n, _i))
                row = group.get(_stem(_strip_lead(tail)))
                if row:
                    return _hit(row)
                if city_fallback is None:
                    city_fallback = (prov_row, city_name, _n, _i)
        if city_fallback is not None:
            return _approx(city_fallback)
        # 1b) 省 + 短名，如「北京市朝阳」
        for city_stem, _rows_ in _cities.items():
            row = (_groups.get((prov_stem, city_stem)) or {}).get(_stem(_strip_lead(rest)))
            if row:
                return _hit(row)
        return None

    # 2) 只写市名：「深圳」（写了「区/县」后缀就不当市名）
    stem = _stem(norm)
    if not norm.endswith(("区", "县", "镇", "乡")) and stem in _cities:
        rows = _cities[stem]
        return _mark_ambiguous(_hit(_best(rows)), rows, raw)

    # 3) 只写区县名：「朝阳」「朝阳区」（重名按级别挑，写清后缀则优先同名后缀）
    if stem in _names:
        rows = _names[stem]
        # 「长安镇」这类镇级写法，只认名字本身带「镇」的条目，
        # 否则会错配成河北石家庄的长安区。
        town = next((s for s in ("街道", "镇", "乡", "村") if norm.endswith(s)), "")
        if town:
            rows = [row for row in rows if _norm(row[2]).endswith(town)]
        if rows:
            # 写清后缀就按后缀选级别：朝阳区 -> 区，朝阳 -> 地级市
            if norm.endswith(("区", "县", "镇", "乡")):
                subset = [row for row in rows if row[4] == 2]
                if subset:
                    rows = subset
            elif norm.endswith("市"):
                subset = [row for row in rows if row[4] == 1]
                if subset:
                    rows = subset
            return _mark_ambiguous(_hit(_best(rows)), rows, raw)

    # 4) 市 + 区县，不带省：「深圳南山」「东莞市莞城区」
    fallback = None
    for city_stem in sorted(_cities, key=len, reverse=True):
        if not norm.startswith(city_stem) or len(norm) == len(city_stem):
            continue
        tail = _stem(_strip_lead(norm[len(city_stem):]))
        for prov, city, _n, _i, _lvl in _cities[city_stem]:
            row = (_groups.get((_stem(prov), city_stem)) or {}).get(tail)
            if row:
                return _hit(row)
            if fallback is None:
                fallback = (prov, city, _n, _i)
    if fallback is not None:
        return _approx(fallback)

    return None


_COORDS = re.compile(r"^-?\d{1,3}(?:\.\d+)?\s*,\s*-?\d{1,3}(?:\.\d+)?$")


def is_coordinates(value) -> bool:
    """「经度,纬度」这种坐标（和风 v7 直接支持，不用查表）。"""
    return bool(_COORDS.match(str(value or "").strip()))


def city_location(city) -> str:
    """把中文城市名换成和风能用的 LocationID；数字/空值原样返回。

    取天气前的统一入口：解析不出来就把原文交回去，由调用方决定
    （GeoAPI 兜底、退回模拟天气、打日志）。
    """
    text = str(city or "").strip()
    if not text or text.isdigit():
        return text
    if is_coordinates(text):
        # 和风要「经度,纬度」且不能有空格；顺手去掉逗号两边空白
        return re.sub(r"\s+", "", text)
    hit = resolve_city(text)
    return str(hit["id"]) if hit else text


def search_city(query, limit: int = 20) -> list:
    """面板候选：按短名/市名/省名前缀匹配，返回最多 limit 条。"""
    stem = _stem(query)
    if not stem:
        return []
    _load()
    hits, seen = [], set()
    for prov, city, name, loc_id, level in _rows:
        name_stem, city_stem, prov_stem = _stem(name), _stem(city), _stem(prov)
        if not (
            name_stem.startswith(stem)
            or city_stem.startswith(stem)
            or prov_stem.startswith(stem)
        ):
            continue
        dedup = (city_stem, name_stem)
        if dedup in seen:
            continue
        seen.add(dedup)
        item = _hit((prov, city, name, loc_id), match=name)
        if name_stem == stem:
            score = 0
        elif city_stem == stem:
            score = 1
        elif name_stem.startswith(stem):
            score = 2
        else:
            score = 3
        item["score"] = score
        hits.append(item)
    hits.sort(key=lambda item: (item["score"], item["level"], len(item["name"])))
    return hits[: max(1, int(limit or 20))]


def stats() -> dict:
    _load()
    return {
        "entries": len(_rows),
        "provinces": len(_table or {}),
        "cities": sum(len(v) for v in (_table or {}).values() if isinstance(v, dict)),
    }
