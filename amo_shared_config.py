# -*- coding: utf-8 -*-
"""共享底座·配置数据：插件常量、日程/关系/健康配置。从 amo_shared.py 按域拆出，旧名仍在门面。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING
try:  # 可选依赖兜底（从 amo_shared 拆出）
    from .amo_shared_deps import (
    _note_swallowed,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared_deps import (
        _note_swallowed,
        )

PLUGIN_NAME = "Astrbot_amoupup_forever"


PLUGIN_VERSION = "8.0.2"


DEFAULT_ADMIN = ""


LEGACY_DEFAULT_ADMIN = "3261748099"


RELATIONS = ("仇恨", "抵触", "普通", "熟识", "相恋")








def city_or_empty(plugin: Any) -> str:
    """当前城市；哪儿都没配就返回空串。

    取值顺序：日程城市 → 天气城市（`weather_city` 可能是数字 LocationID，
    这时用 `weather_city_name`）→ 世界观城市 → `""`。

    以前这里兜底写死「贵阳」，新用户/没配城市的人会莫名其妙排出一整天贵阳的
    行程（观山湖区上班之类）。现在空就是空，由日程骨架走「不写具体城市」的分支。
    """
    # 10B2：旅游中（outbound/traveling/returning）以 trip.city 为准；
    # 没有 trip / travel_enabled=false 时 active_city 返回空串，行为完全不变。
    try:
        try:
            from . import amo_trip as _trip
        except ImportError:
            if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
                import amo_trip as _trip
        trip_city = str(_trip.active_city(plugin) or "").strip()
        if trip_city:
            return trip_city
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.city_or_empty.trip", _amo_exc)

    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def _cfg(key: str) -> str:
        try:
            return _clean(plugin.cfg_str(key, ""))
        except Exception:
            return ""

    city = _cfg("schedule_city")
    if city:
        return city
    weather_city = _cfg("weather_city")
    if weather_city and not weather_city.isdigit() and "," not in weather_city:
        return weather_city
    city = _cfg("weather_city_name")
    if city:
        return city
    state = getattr(plugin, "state", None)
    world = getattr(state, "worldview", None) if state is not None else None
    if isinstance(world, dict):
        city = _clean(world.get("city"))
        if city and city not in ("城市", "未知", "某市"):
            return city
    return ""






SCHEDULE_VERSION = "backbone-v6"


NETWORK_RELATIONS = ("家人", "恋人", "朋友", "同事", "同学", "熟人", "陌生人", "仇人")


#: 九维心情（唯一真实维度；旧十维只做迁移别名，不再存储）
MOOD_KEYS = ("开心", "好奇", "反思", "责任", "社交", "疲惫", "亲密", "压力", "依恋")


#: 真正算「生病」的状态；「疲惫」只是累，不该按病号排日程。
SICK_STATUSES = ("轻微不适", "感冒", "低烧", "发烧", "重感冒", "不舒服", "生病", "拉肚子", "肠胃炎", "咳嗽")


#: 每种病默认持续多久（小时）；生病不是一律 24 小时
SICK_DURATIONS = {
    "轻微不适": 12,
    "感冒": 60,
    "低烧": 36,
    "发烧": 84,
    "重感冒": 84,
    "肠胃炎": 36,
    "咳嗽": 60,
    "拉肚子": 36,
    "不舒服": 24,
    "生病": 48,
}


MOOD_BASE = {
    "开心": 50, "好奇": 55, "反思": 50, "责任": 50, "社交": 45,
    "疲惫": 20, "亲密": 40, "压力": 15, "依恋": 45,
}


def normalize_mood(mood):
    """只读兼容：旧十维快照 -> 九维；新键优先，不写回。"""
    if not isinstance(mood, dict):
        return {}
    out = {}
    for key in MOOD_KEYS:
        if key in mood:
            try:
                out[key] = max(0.0, min(100.0, float(mood[key])))
            except (TypeError, ValueError):
                # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
                pass
    def _v(key, default=50.0):
        try:
            return max(0.0, min(100.0, float(mood.get(key, default))))
        except (TypeError, ValueError):
            return float(default)
    if "好奇" not in out and "兴奋" in mood:
        out["好奇"] = _v("兴奋")
    if "压力" not in out:
        stress = max(_v("焦虑", MOOD_BASE["压力"]), _v("生气", 0.0),
                     _v("暴躁", 0.0), _v("委屈", 0.0))
        if "平静" in mood:
            stress = max(stress, 100.0 - _v("平静", 60.0))
        out["压力"] = max(0.0, min(100.0, stress))
    if "开心" not in out and "郁闷" in mood:
        out["开心"] = max(0.0, min(100.0, 100.0 - _v("郁闷", 50.0)))
    for key in ("依恋", "亲密", "疲惫"):
        if key not in out and key in mood:
            out[key] = _v(key, MOOD_BASE[key])
    return out


STYLE_HINTS = {
    "soft": "整体语气温柔、软一点。",
    "cool": "整体语气清淡、克制一点。",
    "lively": "整体语气活泼、有精神：多用语气词（呀/啦/嘛/诶/哼哼）、短句、反问和玩笑，偶尔撒个小娇或吐槽；不要平铺直叙，也不要每句都完整规矩。",
    "mature": "整体语气成熟、稳一点。",
}


# 关系网里的内部节点：bot/self 代表"我自己"，不是真实人物。
# 这种边只存在本地（_push_relation_to_data 不推给记忆终端），比较远端/本地时也要排掉，
# 否则远端永远比本地少一条，每 10 分钟误报一次"关系网不同步"。
INTERNAL_RELATION_NODES = ("bot", "self")


_CATEGORY_MOOD_CACHE = None


def schedule_category_mood() -> dict:
    """日程类别 → 每小时心情增量（复用 schedule_engine.CATEGORY_MOOD）。"""
    global _CATEGORY_MOOD_CACHE
    if _CATEGORY_MOOD_CACHE is not None:
        return _CATEGORY_MOOD_CACHE
    table: dict = {}
    try:
        from .schedule_engine import CATEGORY_MOOD as _category_mood
        table = dict(_category_mood or {})
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.schedule_category_mood", _amo_exc)
        try:
            from schedule_engine import CATEGORY_MOOD as _category_mood_abs
            table = dict(_category_mood_abs or {})
        except Exception as _amo_exc:
            _note_swallowed("amo_shared.schedule_category_mood", _amo_exc)
            table = {}
    _CATEGORY_MOOD_CACHE = dict(table)
    return _CATEGORY_MOOD_CACHE


