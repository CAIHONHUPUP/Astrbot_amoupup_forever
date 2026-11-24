# -*- coding: utf-8 -*-
"""地点归一唯一实现（2026-11-17 从 amo_place.py 搬进 domains/schedule）。

重构前地点纯归一逻辑散在两处：
- amo_state_common.normalize_place()：白名单/别名/zone 兜底；
- photo_rules.clean_place()：日程里的动作描述（「他桌面上」）洗成「家里」。

本模块是这两段的**唯一实现**；根目录 amo_place.py 只做 re-export 门面
（zone_for_place 仍在门面里转发 photo_rules 的规则表，因为规则表不动）。

2026-11-22：normalize_place 从「zone 折叠」改为「保留原始 place 语义」——restaurant
不再一律变「楼下小馆」，未枚举 zone 不再回落「家」（schedule_report R1/R14）。

为什么放在 domains：这是纯字典匹配 + 字符串清洗，没有任何插件依赖，
属于『地点/日程』域；域内代码只允许 import 标准库（由架构棘轮测试锁定）。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger("amo.place")

#: 桌面投影/旧数据一律洗成「家」；通勤/就医/酒店等真实场景保留。
PLACE_WHITELIST = frozenset({
    "家", "卧室", "客厅", "公司", "楼下小馆", "地铁", "商场",
})
_PLACE_ALIASES = (
    ("他桌面上", "家"), ("桌面", "家"), ("电脑里", "家"), ("家里", "家"),
    ("家中", "家"), ("出租屋", "家"), ("小窝", "家"), ("房间", "家"),
)
#: 这些 zone 的地点语义必须靠原始 place 保留（商场餐厅/景区餐馆/酒店/通勤），
#: 绝不折叠成「楼下小馆」；只有 place 为空时才用中性兜底。
_PLACE_ZONE_KEEP = frozenset({
    "road", "health", "hospital", "hotel", "scenic", "restaurant", "station", "school",
})
#: 已知 zone 的中性兜底（place 为空时用）；未知/缺省 zone 不查这张表，直接保留原文。
_PLACE_ZONE_FALLBACKS = {
    "home": "家",
    "office": "公司",
    "mall": "商场",
    "restaurant": "餐厅",
    "road": "路上",
    "transit": "路上",
    "commute": "路上",
    "health": "医院",
    "hospital": "医院",
    "hotel": "酒店房间",
    "scenic": "景区",
    "station": "车站",
    "school": "学校",
    "playground": "游乐园",
    "street": "街上",
}
#: 这些原始 place 已经是最小语义，不再受 zone 兜底改写。
_PLACE_KEEP_TEXT = ("路上", "医院", "酒店房间", "景区", "餐厅", "车站", "学校")


def normalize_place(value, zone=""):
    """地点归一：只洗「桌面投影/旧数据」，原始 place 的语义必须保留。

    2026-11-22 修正（schedule_report R1/R14）：
    - zone=restaurant 不再一律折叠「楼下小馆」：商场餐厅/景区餐馆/外地小吃街的原始
      place（如「东莞·商场·餐厅」）原样保留，不再变成家附近小馆；
    - 未枚举的 zone（playground/street/gym…）不再回落成「家」：有 place 就保留，
      没 place 就留空，交给调用方按 unknown 处理；
    - 只有 zone=home（或白名单/别名）才归一到「家」。
    """
    text = str(value or "").strip()
    if text in PLACE_WHITELIST:
        return text
    for alias, canonical in _PLACE_ALIASES:
        if alias in text:
            return canonical
    if text in _PLACE_KEEP_TEXT:
        return text
    zone = str(zone or "").strip().lower()
    if zone == "home":
        return "家"
    if zone == "office":
        # office 是通勤/在岗的锚点，旧口径把写字楼名折叠成「公司」，保持不动。
        return "公司"
    if zone == "road":
        if "地铁" in text or "公交" in text:
            return "地铁"
        return text or _PLACE_ZONE_FALLBACKS["road"]
    if zone in _PLACE_ZONE_KEEP:
        return text or _PLACE_ZONE_FALLBACKS.get(zone, text)
    if zone in _PLACE_ZONE_FALLBACKS:
        return text or _PLACE_ZONE_FALLBACKS[zone]
    # 未知/缺失 zone：保留原始 place，绝不回落「家」（R14）。
    return text


def clean_place(place: Any, limit: int = 16) -> str:
    """日程里的 place 有时是「他桌面上」这种动作描述，不适合当拍照地点。"""
    text = str(place or "").strip()
    if not text:
        return ""
    if "桌" in text or text.startswith("他") or text.startswith("她"):
        return "家里"
    return text[:limit]

#: 地点词 → zone：顺序 = 从最具体到最泛，「街」「路」最后兜底
PLACE_ZONE_RULES = (
    (("医院", "诊所", "卫生院", "急诊"), "hospital"),
    (("景区", "景点", "古镇", "海边", "山上"), "scenic"),
    (("游乐园", "乐园", "欢乐谷", "迪士尼", "方特", "环球影城"), "playground"),
    (("家", "客厅", "卧室", "沙发", "床", "厨房", "阳台", "被窝", "宿舍"), "home"),
    (("酒店", "宾馆", "民宿", "旅馆", "客栈"), "hotel"),
    (("写字楼", "公司", "办公室", "工位", "单位"), "office"),
    (("商场", "购物中心"), "mall"),
    (("餐厅", "饭馆", "饭店", "食堂"), "restaurant"),
    (("地铁站", "公交站", "车站", "火车站", "机场", "候车厅"), "station"),
    (("地铁", "公交", "大巴", "车厢", "车上", "出租车", "网约车"), "road"),
    (("街",), "street"),
    (("路", "路边"), "road"),
)


#: 交通工具词：只有 place 本身是交通工具时才是 road（「地铁站」仍归 station）。
_TRANSIT_VEHICLE_WORDS = (
    "地铁", "公交", "大巴", "巴士", "车厢", "车上", "车里",
    "出租车", "网约车", "打车",
)
#: 口语餐饮地点：PLACE_ZONE_RULES 里只有「餐厅/饭馆/饭店/食堂」，这里补小馆/粉店/面馆/馆子。
_RESTAURANT_PLACE_WORDS = (
    "小馆", "馆子", "粉店", "面馆", "小吃", "烧烤", "火锅", "咖啡",
)
#: 具体地点认不出时按 category 兜底；这些值都不是 road（road 只由交通工具/通勤触发）。
_CATEGORY_ZONE_FALLBACK = {
    "meal": "restaurant",
    "work": "office",
    "study": "office",
    "sleep": "home",
    "rest": "home",
    "play": "street",
    "social": "street",
    "life": "street",
}
#: 整词就是路上/路边的，才允许 road；「幸福路小区」这种带具体名字的不算。
_ROAD_EXACT_TEXTS = frozenset(("路", "路上", "路边", "外面"))


def resolve_zone(place, category="", fallback="") -> str:
    """地点 + 类别 → 唯一 zone；有具体地点时绝不返回 road（2026-11-23 23:00 事故）。

    规则顺序：
    1. 交通工具/路上词 → road（唯二允许 road 的地点来源）；
    2. 具体地点词 → PLACE_ZONE_RULES（餐厅/小馆等在规则表之后补充）；
    3. category=commute 且目的地认不出 → road（通勤途中）；
    4. 认不出的具体地点按 category 给非 road 兜底；
    5. 调用方 fallback 只在非 road 时采用。

    这样 resolve_zone("楼下小馆", "meal") 稳定返回 restaurant，不会因为
    旧 zone=road 或 stage 回落把餐厅写成路上。
    """
    text = clean_place(place)
    cat = str(category or "").strip().lower()
    fb = str(fallback or "").strip().lower()
    if fb in ("transit", "commute", "路上"):
        fb = "road"
    if not text:
        # 地点为空：只有通勤/调用方明确给 road 时才允许 road。
        if cat == "commute":
            return "road"
        return fb
    if text in _ROAD_EXACT_TEXTS:
        return "road"
    # 先认具体地点（医院/景区/家/酒店/公司/商场/餐厅/车站），
    # 再补口语餐饮词，最后才看「街/路/交通工具」这些泛地点。
    for words, zone in PLACE_ZONE_RULES:
        if zone in ("street", "road"):
            continue
        if any(word in text for word in words):
            return zone
    if any(word in text for word in _RESTAURANT_PLACE_WORDS):
        return "restaurant"
    if "街" in text:
        return "street"
    if any(word in text for word in _TRANSIT_VEHICLE_WORDS):
        return "road"
    if cat == "commute":
        return "road"
    if cat in _CATEGORY_ZONE_FALLBACK:
        return _CATEGORY_ZONE_FALLBACK[cat]
    if fb and fb != "road":
        return fb
    return ""


def repair_zone_rows(items, day, today="") -> tuple[list, list]:
    """写库前不变量：具体地点 + zone=road → 纠正成 resolve_zone 的结果。

    只修今天和未来（历史日记不回写）。返回 (rows, changes)，changes 供调用方打日志。
    """
    rows = list(items or [])
    day_text = str(day or "")[:10]
    if not day_text:
        return rows, []
    today_text = str(today or date.today().isoformat())[:10]
    if day_text < today_text:
        return rows, []
    fixed_rows: list = []
    changes: list = []
    for row in rows:
        if not isinstance(row, dict):
            fixed_rows.append(row)
            continue
        place = str(row.get("place") or "").strip()
        zone = str(row.get("zone") or "").strip().lower()
        if not place or zone != "road":
            fixed_rows.append(row)
            continue
        target = resolve_zone(place, row.get("category"), "")
        if not target or target == "road" or target == zone:
            fixed_rows.append(row)
            continue
        new_row = dict(row)
        new_row["zone"] = target
        fixed_rows.append(new_row)
        changes.append({
            "place": place,
            "category": str(row.get("category") or ""),
            "activity": str(row.get("activity") or ""),
            "old": zone,
            "new": target,
        })
    for change in changes:
        logger.warning(
            "[amo] 日程地点/zone 不一致已纠正 day=%s %s→%s place=%s category=%s activity=%s",
            day_text, change["old"], change["new"], change["place"],
            change["category"] or "-", change["activity"][:40] or "-")
    return fixed_rows, changes


def zone_for_place(place: Any) -> str:
    """地点词 → zone（兼容旧入口）：统一转发 resolve_zone，不再维护第二套规则。"""
    return resolve_zone(place, "", "")
