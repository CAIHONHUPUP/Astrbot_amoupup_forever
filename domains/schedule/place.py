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

from typing import Any

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


def zone_for_place(place: Any) -> str:
    """地点词 → zone；顺序从最具体到最泛，街/路兜底。"""
    text = clean_place(place)
    if not text:
        return ""
    for words, zone in PLACE_ZONE_RULES:
        if any(word in text for word in words):
            return zone
    return ""
