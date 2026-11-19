# -*- coding: utf-8 -*-
"""生图场景规则表（2026-11-12 照片严格跟随日程）。

这里只放**纯数据 + 纯函数**，不 import 插件引擎，方便被
``photo_scene`` / ``photo_engine`` / ``draw_engine`` / ``schedule_change`` 共用：

- ``zone_for_place()``：地点词 → 区域（日程写 zone 时也用它同步）；
- ``SCENE_RULES``：一个场景一条结构化规则——正向场景、允许/默认姿势、
  允许陈设、禁止项、守卫句、是否允许用姿势库；
- ``photo_plan``（2026-11-16 Phase 9 拆出去的）：``PhotoPlan`` /
  build_plan() / resolve_plan() / pick_pose_for_plan() / locks_for_plan() 都住
  那边；本模块只在文件末尾用 ``__getattr__`` 惰性 re-export 旧名字，既有
  import 不断。

老代码里的 ``SCENE_POLICY`` / ``SCENE_ALLOWED`` / ``OUTDOOR_POSES`` 仍保留在
``photo_engine`` 作为兼容视图，新链路全部走这张表。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：地点规则/纯函数唯一实现在 domains/schedule/place.py（经 amo_place 门面转出）
    from .amo_place import PLACE_ZONE_RULES, clean_place, zone_for_place  # noqa: F401  re-export
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_place import PLACE_ZONE_RULES, clean_place, zone_for_place  # noqa: F401  re-export

try:  # 配置化：英文场景/姿势锁在 infra/config/photo_prompt_en.json
    from .infra.config import load_config
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from infra.config import load_config

from typing import Any


#: 只认“整个地点就是这个词”的强改写（聊天改日程只改 place 不改 zone 时，用它纠偏）
EXPLICIT_PLACE_ZONES = {
    "家": "home",
    "家里": "home",
    "医院": "hospital",
    "路上": "road",
    "外面": "road",
    "公司": "office",
}

#: 日程类别的冲突优先级（同一分钟命中多条时，数字大的优先）
CATEGORY_PRIORITY = {
    "work": 5,
    "study": 5,
    "commute": 4,
    "meal": 3,
    "play": 3,
    "social": 3,
    "life": 2,
    "rest": 1,
    "sleep": 0,
}

TRAVEL_WORDS = ("旅游", "旅行", "景区", "景点", "古镇", "海边", "爬山", "公园", "游乐园")
SWIM_WORDS = ("游泳", "泳池", "泳馆", "海边", "沙滩", "水上乐园", "水世界", "海里", "玩水")
SWIM_STRONG_WORDS = ("游泳", "泳池", "游泳馆", "泳馆", "水上乐园", "水世界",
                     "海里", "玩水", "踩浪", "戏水", "冲浪", "滑道")
SWIM_NEGATIVE_WORDS = ("散步", "拍照", "看海", "路过", "买泳装")
SHOPPING_WORDS = ("逛街", "商场", "专柜", "步行街")

#: 交通工具词（road 之下再分地铁/公交/私家车/打车/走路）
RIDE_WORDS = {
    "subway": ("地铁", "subway", "metro", "车厢"),
    "bus": ("公交", "大巴", "巴士"),
    "drive": ("开车", "驾驶", "自驾", "车里", "车上"),
    "taxi": ("打车", "出租车", "网约", "taxi", "副驾"),
}

#: 禁止项 token → 中文（守卫句负向兜底用）
PROP_WORDS = {
    "bedding": "床铺被褥",
    "instant_noodles": "泡面",
    "thermos": "保温杯",
    "tableware": "餐具",
    "food": "食物",
    "office_desk": "办公桌和电脑",
    "transport": "地铁/公交/车厢",
    "mall_shelf": "商场橱窗",
}
POSTURE_WORDS = {
    "kneel": "跪坐",
    "sit_on_seat": "坐在座位上",
    "sit_on_floor": "坐在地上",
    "cross_leg": "盘腿坐",
    "lie": "躺下",
    "stand": "站起来",
    "drive_seat": "坐在驾驶位",
}

#: 家里再细分（按 category / activity），姿势和场景都跟着变
HOME_SUB_RULES = (
    ("home_bed", ("sleep",), ("睡", "被窝", "赖床", "上床")),
    ("home_bath", (), ("洗", "刷牙", "护肤", "化妆", "洗脸", "吹头")),
    ("home_desk", ("work",), ("加班", "改方案", "文档", "电脑", "写")),
    ("home_meal", ("meal",), ("吃", "早饭", "午饭", "晚饭", "面", "饭")),
    ("home_sofa", ("rest",), ("沙发", "追剧", "刷手机", "瘫", "躺")),
)


#: 场景规则表（配置化：infra/config/photo_scene_rules.json）。
#: 每条：scene（中文场景描述）、poses（允许姿势 key）、default_pose、pose_lines（姿势→自拍文本）、
#: props（画面里该有的东西）、banned（禁止出现的元素 key）、guard（正向守卫句）、
#: allow_library_pose（是否允许居家姿势库）、seat（坐姿类别）。
_SCENE_RULES_DEFAULT: dict[str, dict] = {}


def _is_scene_rules(value) -> bool:
    return (isinstance(value, dict) and bool(value)
            and all(isinstance(item, dict) and item.get("scene") for item in value.values()))


def _rule_from_config(raw: Any) -> dict:
    """JSON 没有元组：读回来按运行期形状归一（poses/props/banned 用元组，与旧实现一致）。"""
    rule = dict(raw)
    rule["poses"] = tuple(rule.get("poses") or ())
    rule["props"] = tuple(rule.get("props") or ())
    rule["banned"] = tuple(rule.get("banned") or ())
    rule["pose_lines"] = dict(rule.get("pose_lines") or {})
    return rule


#: 一个场景一条规则；photo_engine / draw_engine 的 prompt 都从这里取字段
SCENE_RULES: dict[str, dict] = {
    str(key): _rule_from_config(value)
    for key, value in load_config("photo_scene_rules", _SCENE_RULES_DEFAULT, _is_scene_rules).items()
    if isinstance(value, dict)
}

#: 英文场景/姿势锁（配置化：infra/config/photo_prompt_en.json）。
#: scene_en: 场景 key → 场景锁文本；pose_en: "场景key|pose_rule" → 姿势锁文本；
#: 查不到就返回空串 = 不锁（photo_plan.locks_for_plan 的既有语义）。
_PROMPT_EN_DEFAULT: dict[str, dict] = {"scene_en": {}, "pose_en": {}}


def _is_prompt_en(value) -> bool:
    return (isinstance(value, dict)
            and isinstance(value.get("scene_en"), dict)
            and isinstance(value.get("pose_en"), dict))


_PROMPT_EN = load_config("photo_prompt_en", _PROMPT_EN_DEFAULT, _is_prompt_en)
#: 英文场景锁：传给我能永远定格你副脑，解析完 JSON 后直接覆盖 environment_and_scene
SCENE_EN: dict[str, str] = {
    str(key): str(value)
    for key, value in (_PROMPT_EN.get("scene_en") or {}).items()
}
#: 英文姿势锁：(规则 key, pose_rule) → 覆盖 pose_and_action；没有条目的（居家姿势库）不锁
POSE_EN: dict[tuple[str, ...], str] = {
    tuple(str(key).split("|", 1)): str(value)
    for key, value in (_PROMPT_EN.get("pose_en") or {}).items()
    if "|" in str(key)
}

#: 兼容 photo_engine 旧视图：zone → 规则 key
LEGACY_ZONE_ALIAS = {    "road": "road_walk",
    "default": "unknown",
    "home": "home_generic",
    "office": "office",
    "restaurant": "restaurant",
    "mall": "mall",
    "scenic": "scenic",
    "hotel": "hotel",
    "station": "station",
    "street": "street",
    "hospital": "hospital",
}


# clean_place / zone_for_place / PLACE_ZONE_RULES 已迁到 domains/schedule/place.py（经 amo_place 门面转出，顶部 import）。


# zone_for_place 的实现已搬到 domains/schedule/place.py；这里从 amo_place 转出（顶部 import）。


def blob_of(activity: Any) -> str:
    data = activity if isinstance(activity, dict) else {}
    return "%s%s%s" % (
        str(data.get("activity") or ""),
        str(data.get("place") or ""),
        str(data.get("spot") or ""),
    )


def is_swim_activity(activity: Any) -> bool:
    data = activity if isinstance(activity, dict) else {}
    if data.get("swim") is True:
        return True
    text = blob_of(activity)
    if any(word in text for word in SWIM_STRONG_WORDS):
        return True
    if any(word in text for word in SWIM_NEGATIVE_WORDS):
        return False
    return False


def road_ride(activity: Any) -> str:
    """这一小时在路上是坐什么：subway / bus / drive / taxi / ''。"""
    data = activity if isinstance(activity, dict) else {}
    text = "%s%s%s" % (
        str(data.get("activity") or ""),
        str(data.get("transit") or ""),
        str(data.get("vehicle") or ""),
    )
    for kind, words in RIDE_WORDS.items():
        if any(word in text for word in words):
            return kind
    return ""


def _home_key(activity: dict) -> str:
    category = str(activity.get("category") or "")
    text = "%s%s" % (activity.get("activity") or "", activity.get("detail") or "")
    for key, categories, words in HOME_SUB_RULES:
        if categories and category in categories:
            return key
        if any(word in text for word in words):
            return key
    return "home_generic"


def _scene_key(zone: str, activity: dict) -> str:
    if is_swim_activity(activity) and zone in ("scenic", "", "street"):
        return "swim"
    if zone == "home":
        return _home_key(activity)
    if zone == "road":
        ride = road_ride(activity)
        if ride == "subway":
            return "road_subway"
        if ride == "bus":
            return "road_bus"
        if ride == "drive":
            return "road_car_drive"
        if ride == "taxi":
            return "road_car_taxi"
        return "road_walk"
    if zone in SCENE_RULES:
        return zone
    return "unknown"


#: 手脚还原度守卫：全身/局部照里最常崩的是脚趾（六趾、粘连、指甲糊）。
#: 正向句钉死「五个脚趾」，不依赖模型自己数；负向词放在 negative_prompt 里。
_BODY_GUARD = ("比例自然，手和脚结构正常：一只手五个手指、一只脚五个脚趾，"
               "大脚趾明显最粗最大、其余脚趾依次变短，脚趾分开、指甲清楚，"
               "不要手部或脚部的局部特写")


def _guard_line(rule: dict) -> str:
    positive = str(rule.get("guard") or "").strip()
    positive = (positive + "；" + _BODY_GUARD) if positive else _BODY_GUARD
    negative = []
    for token in rule.get("banned") or ():
        label = POSTURE_WORDS.get(token) or PROP_WORDS.get(token, token)
        # 正向句里已经写了「没有泡面/没有床铺」的，不再重复一条「不要泡面」
        if label and (label in positive or label[:2] in positive):
            continue
        negative.append("不要" + (label or token))
    if not negative:
        return positive
    return positive + "；" + "；".join(negative)


def pose_text(pose: Any) -> str:
    """姿势 dict → 提示词片段（和 pose_library.pose_line 同格式，避免互相 import）。"""
    pose = pose if isinstance(pose, dict) else {}
    name = str(pose.get("name") or "").strip()
    note = str(pose.get("note") or "").strip()
    if not name:
        return ""
    return "姿势：" + name + (("（" + note + "）") if note else "")


# ---- 兼容层：2026-11-16 Phase 9 把"计划"搬去 photo_plan，旧名字继续可用 ----
# 用模块级 ``__getattr__``（PEP 562）惰性转发：photo_plan 顶部要 import 本模块的
# 规则表，如果这里在导入期就 from photo_plan import ...，先 import photo_plan 的
# 场景会撞上"半初始化的模块"。
_PLAN_EXPORTS = (
    "PhotoPlan",
    "build_plan",
    "locks_for_plan",
    "normalize_activity",
    "pick_pose_for_plan",
    "resolve_plan",
)


def __getattr__(name):
    if name in _PLAN_EXPORTS:
        try:  # 包内导入（插件运行态）
            from . import photo_plan as module
        except ImportError:  # 直接按文件加载时（单测 / work 下的工具脚本）
            import photo_plan as module
        return getattr(module, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
