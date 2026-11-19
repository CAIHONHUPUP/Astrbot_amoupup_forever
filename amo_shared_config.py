# -*- coding: utf-8 -*-
"""共享底座·配置数据：插件常量、方言表、日程/关系/健康配置。从 amo_shared.py 按域拆出，旧名仍在门面。"""
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


PLUGIN_VERSION = "7.5.1"


DEFAULT_ADMIN = ""


LEGACY_DEFAULT_ADMIN = "3261748099"


RELATIONS = ("仇恨", "抵触", "普通", "熟识", "相恋")


DIALECT_SAMPLES = {
    "贵州话": "晓得、咋个、要得、安逸",
    "东北话": "咋整、唠嗑、得瑟、麻溜",
    "四川话": "啥子、莫得、巴适、搞快些",
    "广东话": "唔该、点解、得闲、搞掂",
    "台湾腔": "酱紫、蛮、超赞、拍谢",
    "天津话": "嘛呢、倍儿、哏儿、得嘞",
    "河南话": "弄啥嘞、中、得劲、木有",
    "上海话": "阿拉、侬、哪能、老灵",
}


CITY_DIALECT_RULES = (
    (("贵阳", "贵州", "遵义", "安顺", "六盘水", "凯里"), "贵州话"),
    (("成都", "四川", "重庆", "绵阳", "德阳", "宜宾"), "四川话"),
    (("广州", "深圳", "东莞", "佛山", "珠海", "中山", "惠州", "江门", "汕头", "广东"), "广东话"),
    (("上海", "苏州", "无锡", "常州", "昆山"), "上海话"),
    (("天津",), "天津话"),
    (("郑州", "河南", "洛阳", "开封"), "河南话"),
    (("哈尔滨", "沈阳", "长春", "大连", "东北", "辽宁", "吉林", "黑龙江"), "东北话"),
    (("台北", "台湾", "高雄", "台中"), "台湾腔"),
)


def dialect_for_city(city: Any) -> str:
    text = str(city or "")
    if not text:
        return ""
    for keys, style in CITY_DIALECT_RULES:
        if any(key in text for key in keys):
            return style
    return ""


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


DIALECT_FALLBACK_ROWS = {
    "东北话": [
        ("咋整", "怎么办", "咋整啊"), ("唠嗑", "聊天", "来唠嗑"), ("得瑟", "显摆", "别得瑟"),
        ("麻溜", "快点", "麻溜的"), ("贼拉", "非常", "贼拉冷"), ("嘎哈", "干什么", "你嘎哈呢"),
        ("老铁", "哥们", "老铁没毛病"),
    ],
    "四川话": [
        ("啥子", "什么", "你说啥子"), ("莫得", "没有", "莫得问题"), ("巴适", "舒服", "巴适得很"),
        ("搞快些", "快点", "搞快些"), ("瓜娃子", "傻孩子", "你个瓜娃子"), ("雄起", "加油", "雄起"),
        ("撇脱", "干脆", "撇脱点"),
    ],
    "广东话": [
        ("唔该", "谢谢、劳驾", "唔该晒"), ("点解", "为什么", "点解啊"), ("得闲", "有空", "得闲饮茶"),
        ("搞掂", "搞定", "搞掂啦"), ("靓仔", "帅哥", "靓仔"), ("靓女", "美女", "靓女"),
        ("冇", "没有", "冇问题"),
    ],
    "台湾腔": [
        ("酱紫", "这样", "酱紫啊"), ("蛮", "很", "蛮好的"), ("超赞", "非常棒", "超赞"),
        ("拍谢", "不好意思", "拍谢啦"), ("机车", "麻烦、烦人", "很机车"), ("阿嬷", "奶奶", "阿嬷"),
        ("龟毛", "挑剔", "别龟毛"),
    ],
    "天津话": [
        ("嘛呢", "干什么", "嘛呢"), ("倍儿", "非常", "倍儿好"), ("哏儿", "有趣", "真哏儿"),
        ("得嘞", "好的", "得嘞"), ("腻歪", "腻烦、黏人", "别腻歪"), ("幺蛾子", "麻烦", "出幺蛾子"),
        ("耐人", "招人喜欢", "真耐人"),
    ],
    "河南话": [
        ("弄啥嘞", "干什么", "你弄啥嘞"), ("中", "好", "中，就这么办"), ("得劲", "舒服", "真得劲"),
        ("木有", "没有", "木有事"), ("恁", "你", "恁吃饭没"), ("俺", "我", "俺知道了"),
        ("中不中", "行不行", "中不中"),
    ],
    "上海话": [
        ("阿拉", "我、我们", "阿拉晓得"), ("侬", "你", "侬好"), ("哪能", "怎么", "哪能办"),
        ("结棍", "厉害", "侬结棍"), ("老灵", "很好", "老灵额"), ("白相", "玩", "出去白相"),
        ("适意", "舒服", "适意"),
    ],
}


DIALECT_REGIONS = {
    "贵州话": "贵州",
    "东北话": "东北",
    "四川话": "四川",
    "广东话": "广东",
    "台湾腔": "台湾",
    "天津话": "天津",
    "河南话": "河南",
    "上海话": "上海",
}


SCHEDULE_VERSION = "backbone-v6"


NETWORK_RELATIONS = ("家人", "恋人", "朋友", "同事", "同学", "熟人", "陌生人", "仇人")


MOOD_KEYS = ("开心", "平静", "郁闷", "生气", "暴躁", "委屈", "疲惫", "焦虑", "兴奋", "依恋")


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
    "开心": 55, "平静": 60, "郁闷": 18, "生气": 8, "暴躁": 6, "委屈": 20,
    "疲惫": 28, "焦虑": 18, "兴奋": 30, "依恋": 45,
}


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


