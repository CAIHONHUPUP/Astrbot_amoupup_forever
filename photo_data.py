# -*- coding: utf-8 -*-
"""照片引擎共享数据：场景/姿势常量与纯函数（从 photo_engine 拆出，行为逐字节不变）。"""

from typing import TYPE_CHECKING
from astrbot.api import logger

def home_room_for_plan(plan, activity=None):
    """在家自拍该用哪张房间参考图：卧室 / 客厅；不在家返回空。

    P22 起真正的分房间规则在 photo_room.room_for_plan（读 housing 里的房间画像），
    这里保留老名字给旧调用点和老测试用。
    """
    try:
        from .photo_room import room_for_plan
    except ImportError:  # 直接按文件加载时
        from photo_room import room_for_plan
    return room_for_plan(plan, activity=activity)


def _light_text(engine, plan=None):
    """按场景取光线描述：室外夜景不能写“室内暖灯”（测试桩只收 self 时回退）。

    结果写回 plan.light：一次生图里的光线只算一次，调试和测试都从计划里取。
    """
    text = _light_value(engine, plan)
    if plan is not None:
        setattr(plan, "light", text)
    return text


def _light_value(engine, plan=None):
    """真正的光线取值：室内/室外由计划的 scene_key 决定。"""
    outdoor = False
    if plan is not None:
        outdoor = not str(getattr(plan, "scene_key", "") or getattr(plan, "zone", "") or "").startswith(
            ("home_", "office", "restaurant", "mall", "hotel", "station", "hospital", "unknown")
        )
    getter = getattr(engine, "_time_light", None)
    if not callable(getter):
        return "自然光"
    try:
        return getter(outdoor=outdoor)
    except TypeError:
        try:
            return getter()
        except Exception:
            return "自然光"
    except Exception:
        return "自然光"



def selfie_state_hint(engine, activity, plan=None):
    """自拍时按当前处境补细节：生病躺床 / 刚洗完澡 / 睡衣。"""
    parts = []
    try:
        health = engine.plugin.state.health_info() or {}
    except Exception:
        health = {}
    if health.get("sick"):
        parts.append("有点生病，素颜，头发有点乱，慵懒")
    act = str((activity or {}).get("activity") or "")
    zone = str(getattr(plan, "zone", "") or (activity or {}).get("zone") or "")
    if zone == "home_bed" or any(w in act for w in ("睡", "躺", "休息", "被窝")):
        parts.append("躺在床上，盖着被子，慵懒")
    if any(w in act for w in ("洗澡", "洗完", "沐浴")):
        parts.append("刚洗完澡，头发微湿")
    try:
        outfit = str(engine.outfit_today() or "")
    except Exception:
        outfit = ""
    if "睡" in outfit:
        parts.append("穿着睡衣")
    if "真丝" in outfit:
        parts.append("真丝睡衣的质感")
    seen, out = set(), []
    for item in parts:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return "，".join(out)


def _log_photo_debug(engine, activity, plan, final_prompt, pose_text=""):
    """生图链路调试日志（模块级：测试里的假引擎也能用）。"""
    try:
        enabled = bool(getattr(engine, "debug_prompt", False))
    except Exception:
        enabled = False
    if not enabled:
        return
    data = activity if isinstance(activity, dict) else {}
    act_text = " | ".join(filter(None, [
        str(data.get("activity") or ""),
        str(data.get("place") or ""),
        str(data.get("category") or ""),
        str(data.get("transit") or ""),
        str(data.get("vehicle") or ""),
    ]))
    # 字段一律从计划的 debug_dict() 取：plan 是唯一真值，日志不再自己重算
    info = plan.debug_dict() if plan is not None else {}
    logger.info(
        "[PHOTO_DEBUG] 日程=%s zone=%s pose_rule=%s guard=%s final_prompt=%s"
        % (act_text or "-", info.get("scene_key") or "-",
           info.get("pose_rule") or pose_text or "-",
           info.get("guard") or "", final_prompt)
    )
    if info.get("source") not in ("schedule",) or info.get("explicit_conflict") or info.get("repairs"):
        logger.info(
            "[PHOTO_DEBUG] source=%s reason=%s explicit_place=%s conflict=%s repairs=%s"
            % (info.get("source"), info.get("reason") or "-", info.get("explicit_place") or "-",
               info.get("explicit_conflict"), info.get("repairs") or [])
        )
    locks = info.get("locks") or {}
    scene_en = locks.get("scene_lock") or ""
    pose_en = locks.get("pose_lock") or ""
    if scene_en or pose_en:
        logger.info(
            "[PHOTO_DEBUG] locks scene_lock=%s pose_lock=%s"
            % (scene_en or "-", pose_en or "-")
        )


#: 户外/非居家场景的兜底姿势（姿势库那 9 个都是床上的，出门照搬会穿帮）
OUTDOOR_POSES = {
    "road": "站在车厢里，一只手扶着头顶的扶手或吊环，身体微微侧着看镜头",
    "street": "站在路边，身体侧一点，回头看向镜头",
    "mall": "站在橱窗前，一只手自然垂着，微微侧身",
    "office": "站在工位旁，一只手搭在椅背上，微微侧身",
    "scenic": "站在景点前，抬手挡了一下阳光，头发被风吹起",
    "restaurant": "坐在餐桌旁，双手放在桌上，正对镜头",
    "station": "站在候车厅里，一只手扶着行李箱拉杆",
    "hospital": "坐在候诊椅上，双手放在腿上，正对镜头",
    "hotel": "坐在床边，一只手撑在身侧，身体微微后仰看镜头",
}


def outdoor_pose_line(zone):
    """不在家时用的姿势句子。"""
    return OUTDOOR_POSES.get(str(zone or ""), "站着，身体微微侧向镜头")


#: 场景限制（结构化）：提示词和测试都从这里取，别去断言提示词字符串。
SCENE_POLICY = {
    "road": {
        "poses": ["standing", "hold_strap"],
        "banned_postures": ["kneel", "sit_on_seat", "cross_leg"],
        "banned_props": ["bedding", "instant_noodles", "thermos", "tableware", "food"],
        "seat": "hard_bench",
    },
    "default": {
        "poses": ["standing", "sit"],
        "banned_postures": [],
        "banned_props": ["bedding", "tableware"],
        "seat": "",
    },
}

_PROP_WORDS = {"bedding": "床铺被褥", "instant_noodles": "泡面", "thermos": "保温杯",
               "tableware": "餐具", "food": "食物"}
_POSTURE_WORDS = {"kneel": "跪坐", "sit_on_seat": "坐在座位上", "cross_leg": "盘腿坐"}



# ---- 2026-11-12：路上再细分（地铁/公交/私家车），姿势跟着交通工具走 ----
_RIDE_WORDS = {
    "car": ("开车", "打车", "taxi", "网约", "车里", "驾驶", "副驾", "车上"),
    "bus": ("公交", "大巴", "巴士"),
    "subway": ("地铁", "subway", "metro", "车厢"),
}


def road_ride(activity):
    """这一小时在路上是坐什么：car / bus / subway / ''。"""
    data = activity if isinstance(activity, dict) else {}
    act = str(data.get("activity") or "")
    transit = str(data.get("transit") or "")
    vehicle = str(data.get("vehicle") or "")
    text = act + transit + vehicle
    for kind, words in _RIDE_WORDS.items():
        if any(word in text for word in words):
            return kind
    return ""


def pose_line_for(zone, activity):
    """不靠姿势库时的姿势句：酒店坐床边、私家车系安全带、地铁扶杆、其余站立。"""
    zone = str(zone or "")
    if zone == "road":
        ride = road_ride(activity)
        if ride == "car":
            return "坐在座位上系好安全带，一只手举手机自拍，不要站起来"
        if ride == "bus":
            return "坐在公交车硬质座椅上，一只手扶着前座靠背，身体微微侧向镜头"
        return "站在车厢里，一只手扶着头顶的扶手或吊环，身体微微侧着看镜头"
    return OUTDOOR_POSES.get(zone, "站着，身体微微侧向镜头")

def scene_policy(zone):
    """这个场景允许/禁止什么（结构化；测试断言字段，不测提示词）。"""
    return SCENE_POLICY.get(str(zone or ""), SCENE_POLICY["default"])


def scene_guard(zone):
    """把 scene_policy 翻成提示词里的限制。

    2026-11-12：路上不再写“不要X”——我能永远定格你的副脑只收正向描述、会重写提示词，
    “不要泡面”容易被它理解成“有泡面”。改成把环境写死（只存在什么），让它没得发挥。
    """
    if str(zone or "") == "road":
        return (
            "画面里只有车厢或路面上本来就有的东西：座椅、扶手、吊环、车窗、街景、乘客；"
            "地面和座椅干净，只有这些东西，没有别的物件"
        )
    policy = scene_policy(zone)
    parts = []
    for key in policy.get("banned_postures") or []:
        parts.append("不要" + _POSTURE_WORDS.get(key, key))
    for key in policy.get("banned_props") or []:
        parts.append("不要出现" + _PROP_WORDS.get(key, key))
    if not parts:
        return "画面里只出现这个地点本来就有的东西"
    return "；".join(parts) + "；画面里只出现这个地点本来就有的东西"

# 生图可能卡住/返回超大图：都设上限，别把事件循环和数据库拖死
GENERATE_TIMEOUT_SECONDS = 180
_MAX_IMAGE_BYTES = 16 * 1024 * 1024


# 同一天所有自拍都用同一套穿搭；按日期稳定挑选，换场景也不换衣服
OUTFIT_POOL = (
    "米色针织衫配牛仔裤",
    "白色衬衫配深色半裙",
    "浅灰卫衣配运动裤",
    "奶油色开衫配直筒裤",
    "黑色小高领配卡其色半裙",
    "牛仔外套配白色T恤和长裤",
    "浅蓝衬衫配米白阔腿裤",
    "粉色卫衣配牛仔裙",
    "燕麦色毛衣配深色半裙",
    "条纹长袖配浅色牛仔裤",
)


def _int_in(value, low, high, default):
    try:
        number = int(float(value))
    except Exception:
        return default
    return max(low, min(high, number))


PHOTO_INTENT_WORDS = (
    "拍照", "拍张", "拍个", "拍给我", "拍来看", "照片", "自拍",
    "看看你", "看看你在", "你在哪", "在哪呢",
)
PHOTO_PLACE_WORDS = (
    "公园", "商场", "咖啡", "学校", "公司", "家里", "街上", "海边", "餐厅",
    "超市", "地铁", "床上", "办公室", "图书馆", "电影院", "游乐园", "动物园",
    "植物园", "江边", "河边", "山上", "健身房", "教室", "宿舍", "路边", "小区",
)


# ---- 2026-11-12 按场景细化：一个 default 会误伤家里/餐厅/酒店 ----
# 只在模块加载后覆盖对应 key；scene_policy()/scene_guard() 都读同一个 dict。
SCENE_POLICY.update({
    "home": {"poses": ["library"], "banned_postures": [], "banned_props": [], "seat": "soft"},
    "restaurant": {"poses": ["sit"], "banned_postures": [], "banned_props": [], "seat": "chair"},
    "hotel": {"poses": ["sit_edge", "stand"], "banned_postures": [], "banned_props": [], "seat": "soft"},
    "office": {"poses": ["stand", "sit"], "banned_postures": [], "banned_props": ["bedding"], "seat": "chair"},
    "mall": {"poses": ["stand", "walk"], "banned_postures": [],
             "banned_props": ["bedding", "instant_noodles", "tableware"], "seat": "bench"},
    "station": {"poses": ["stand"], "banned_postures": [],
                "banned_props": ["bedding", "instant_noodles", "tableware"], "seat": "bench"},
    "scenic": {"poses": ["stand", "walk"], "banned_postures": [],
               "banned_props": ["bedding", "instant_noodles", "tableware"], "seat": "bench"},
    "street": {"poses": ["stand", "walk"], "banned_postures": [],
               "banned_props": ["bedding", "instant_noodles", "tableware"], "seat": "bench"},
    "hospital": {"poses": ["sit"], "banned_postures": [], "banned_props": ["bedding"], "seat": "chair"},
})


# ---- 2026-11-12 场景“该有什么”：只靠禁词会让画面空、道具乱，给每个场景一句正向要素 ----
SCENE_ALLOWED = {
    "home": "家里该有的东西：沙发或床、抱枕、毯子、水杯、窗边的光",
    "office": "工位该有的东西：办公桌、显示器、键盘、马克杯、文件、办公椅",
    "mall": "商场该有的东西：店铺橱窗、扶梯、地砖、指示牌、来往的人",
    "scenic": "景区该有的东西：景点建筑或山水、栏杆、游客、路牌、自然光",
    "restaurant": "餐厅该有的东西：餐桌、碗筷餐盘、菜单、杯子、店里的灯",
    "hotel": "酒店房间该有的东西：床和枕头、床头灯、行李、窗帘、地毯",
    "station": "车站该有的东西：候车座椅、指示牌、行李箱、时刻表、行人",
    "hospital": "医院该有的东西：候诊椅、叫号屏、走廊、扶手、消毒水味的环境",
    "road": "路上该有的东西：扶手或安全带、车窗、座椅、窗外街景或隧道",
    "street": "街边该有的东西：路面、行道树、店面、路牌、走过的行人",
}


def scene_allowed_line(zone):
    """这个场景里应该出现什么（和禁词配合，避免画面空荡或乱加道具）。"""
    return SCENE_ALLOWED.get(str(zone or ""), "只出现这个地点本来就有的东西")
