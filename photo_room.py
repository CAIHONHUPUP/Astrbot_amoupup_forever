# -*- coding: utf-8 -*-
"""P22 家里生图分房间：卧室 / 客厅 + 房间参考图（默认开启，photo_room_refs=false 可关）。

- 房间画像存 housing 配置（home["rooms"]），这里只读；没配置时用 housing 默认值；
- room_for_plan(plan) 固定两条规则：睡觉/躺床/换衣服 -> 卧室，其余在家场景 -> 客厅；
- 房间参考图是「同一间房」的锚点，不是像素模板：提示词只锁布局/光线，不锁像素。
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

import os

try:
    from astrbot.api import logger
except ImportError:  # 直接按文件加载时
    import logging

    logger = logging.getLogger(__name__)

ROOM_REF_SUBJECT_GUARD = "画面以她为主体，房间只作背景参考，不要变成房间空镜"
ROOM_REF_HINT = "最后一张是房间参考图，只参考家具、布局、光线和空间关系，不要照搬成空镜，不要复制图里的文字"
HOME_WORDS = ("家", "客厅", "卧室", "床", "沙发", "茶几", "餐桌", "书桌", "被窝", "衣柜")


def _default_profiles():
    try:
        from .housing import ROOM_PROFILES
    except ImportError:  # 直接按文件加载时
        from housing import ROOM_PROFILES
    return {room: dict(profile) for room, profile in ROOM_PROFILES.items()}


def _usable_profiles(rooms):
    """只收 dict 画像；坏配置（值是字符串/None/列表）直接丢掉，别让下游 .get 崩。"""
    if not isinstance(rooms, dict):
        return {}
    return {room: profile for room, profile in rooms.items() if isinstance(profile, dict)}


def room_profiles(housing=None):
    """当前家的房间画像；housing 不可用/配置坏掉时退回默认值。"""
    getter = getattr(housing, "room_profiles", None)
    if callable(getter):
        try:
            rooms = _usable_profiles(getter())
            if rooms:
                return rooms
        except Exception as exc:  # housing 是跨插件对象，任何异常都不能把生图带崩
            logger.debug("[amo] 房间画像读不出来，用默认值: %s", exc)
    return _default_profiles()


def room_label(room="living", housing=None):
    """房间的中文名；不认识时按客厅。"""
    profile = room_profiles(housing).get(str(room or "living"))
    label = str(profile.get("label") or "") if isinstance(profile, dict) else ""
    return label or ("卧室" if str(room or "") == "bedroom" else "客厅")


def _text_of(plan, activity):
    data = activity if isinstance(activity, dict) else {}
    return "%s%s%s%s" % (
        str(data.get("place") or ""),
        str(data.get("activity") or ""),
        str(getattr(plan, "scene", "") or ""),
        str(getattr(plan, "where", "") or ""),
    )


def room_for_plan(plan, housing=None, activity=None):
    """这条计划在家该拍哪间房：bedroom / living；不在家返回空串。"""
    data: Any = activity if isinstance(activity, dict) else {}
    if not data:
        # 没显式传活动时用计划里那份：build_plan 可能把 home_bed 归一成 home_generic，
        # 原始 zone 只在 plan.activity 里，房间图要按它选（否则卧室会被当成客厅）
        data = getattr(plan, "activity", None) if isinstance(getattr(plan, "activity", None), dict) else {}
    zones = []
    for item in (data.get("zone"), getattr(plan, "base_zone", ""), getattr(plan, "zone", "")):
        text = str(item or "").strip()
        if text and text not in zones:
            zones.append(text)
    zone = zones[0] if zones else ""
    text = _text_of(plan, data)
    profiles = room_profiles(housing)
    place = str(data.get("place") or "")
    at_home = zone.startswith("home") or (not zone and any(word in place for word in HOME_WORDS))
    if not at_home:
        return ""
    # 每间房先看 zone（活动原始 zone → 计划 base_zone → 归一后的 scene_key），
    # 再看地点词；顺序固定为 卧室→浴室→厨房→客厅，不认识的家内场景按客厅。
    for room in ("bedroom", "bathroom", "kitchen", "living"):
        profile = profiles.get(room)
        if not isinstance(profile, dict):
            profile = {}
        zones_of_room = tuple(str(item) for item in (profile.get("zones") or ()))
        if any(item in zones_of_room for item in zones):
            return room
        if any(str(word) and str(word) in text for word in (profile.get("places") or ())):
            return room
    return "living"


def room_ref_for_plan(plan, housing=None, activity=None):
    """room_ref：当前家那间房的参考图路径；没图/没 housing 返回空。"""
    room = room_for_plan(plan, housing, activity)
    if not room:
        return ""
    getter = getattr(housing, "room_image", None)
    path = ""
    if callable(getter):
        try:
            path = str(getter(room) or "")
        except Exception as exc:  # 跨插件对象：宁可空串也不要崩整张图
            logger.debug("[amo] 房间参考图路径读不出来: %s", exc)
            path = ""
    if not path:
        return ""
    if not os.path.isfile(path):
        logger.debug("[amo] 房间参考图不存在，本次跳过: %s", path)
        return ""
    return path


def room_lock_line(room="living", housing=None):
    """提示词里的房间锁：只锁同一间房，不锁像素。"""
    return ("同一间%s：家具、布局、墙面地面和光线方向保持一致；只锁房间结构，不照搬参考图的像素和构图"
            % room_label(room, housing))


def room_scene_line(housing, room="living"):
    """房间画像（该有什么家具）；housing 没有这个方法时返回空。"""
    getter = getattr(housing, "room_scene_line", None)
    if not callable(getter):
        return ""
    try:
        return str(getter(room) or "")
    except Exception as exc:  # 跨插件对象：读不出来就不加场景句
        logger.debug("[amo] 房间场景句读不出来: %s", exc)
        return ""
