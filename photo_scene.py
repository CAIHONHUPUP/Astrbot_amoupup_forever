# -*- coding: utf-8 -*-
"""把日程里的「此刻在哪」翻成生图提示词里的具体场景（2026-11-12 加入）。

她按日程过日子，照片/画图的背景就该是日程里那个地点，而不是随手一张
「室内」或「街上」：

- 日程说在家 → 背景是家里的客厅 / 卧室 / 餐桌 / 卫生间；
- 日程说在写字楼 → 背景是办公室工位；
- 日程说逛街 → 背景是商场中庭、店铺橱窗；
- 日程说旅游（zone=scenic）→ 背景是景区，带上 ``spot`` 里的景点名；
- 在路上 → 地铁车厢 / 车里 / 街边；
- 住酒店 → 酒店房间；在车站 → 候车厅。

以前这里只用 ``place`` 拼提示词，而日程里 ``place`` 常常是「他桌面上」
「莞城区的写字楼」这种不适合当画面的词，模型只能自己编背景，于是「在家」
也会出咖啡馆、「逛街」也会出卧室。

两个引擎（``photo_engine`` 主动发照片、``draw_engine`` 他说了才画）共用这里的
``scene_for()``；``clean_place()`` 仍由 ``amo_image_common`` 提供，避免两份洗法漂移。
"""
from __future__ import annotations

try:  # 包模式优先，按文件直接加载时退回绝对导入
    from .amo_image_common import clean_place
except ImportError:  # 直接按文件加载时
    from amo_image_common import clean_place

#: 日程/活动里出现这些词就算「在旅游」
TRAVEL_WORDS = ("旅游", "旅行", "景区", "景点", "古镇", "海边", "爬山", "公园", "游乐园")
#: 日程/活动里出现这些词就算「在玩水」（衣柜会换泳装，照片背景是泳池/海边）
SWIM_WORDS = ("游泳", "泳池", "泳馆", "海边", "沙滩", "水上乐园", "水世界", "海里", "玩水")
#: 出现这些词就算「在逛街」
SHOPPING_WORDS = ("逛街", "商场", "专柜", "步行街")
#: 工作日早上要发「出门穿搭」的类别
OUTFIT_CATEGORY = ("commute", "work")

#: 只说了个大概、当不了画面锚点的地点（这些走 zone 的固定场景）
GENERIC_PLACE_WORDS = (
    "家里", "家中", "他家", "我家", "家",
    "公司", "写字楼", "办公室", "单位", "工位",
    "路上", "外面", "街上", "街边", "街头", "商场", "附近", "周边",
    "景区", "景点", "风景区", "酒店", "宾馆", "民宿", "旅馆", "客栈",
    "学校", "校园", "医院", "餐厅", "饭店", "食堂", "车站", "高铁站",
    "火车站", "机场", "城里", "市区",
)

#: 每种 zone 背景里的具体陈设（地点是具体名字时接在它后面）
_ZONE_PROPS = {
    "home": "生活里的日常陈设和暖光",
    "office": "电脑、办公桌和旁边的隔断",
    "restaurant": "餐桌、碗筷和头顶的暖色吊灯",
    "mall": "店铺橱窗、明亮的灯光和来往的人",
    "scenic": "远处的山和树木、石板路和零星的游客",
    "road": "路面、行道树、店面和走过的人",
    "hotel": "大床、落地窗和暖色的灯",
    "station": "指示牌、行李和来往的旅客",
}

#: 只说了 zone（没有具体地点）时的整句场景
_ZONE_SCENES = {
    "home": "家里，客厅的沙发和窗边",
    "office": "公司里的办公室，她的工位、电脑屏幕、办公桌和旁边的隔断",
    "restaurant": "餐厅里，餐桌、碗筷和头顶的暖色吊灯",
    "mall": "商场里，明亮的中庭、店铺橱窗和来往的人",
    "scenic": "景区里，远处的山和树木、石板路和零星的游客",
    "road": "街边，路面、行道树、店面和走过的人",
    "hotel": "酒店房间里，大床、落地窗和暖色的灯",
    "station": "车站候车厅，指示牌、行李和来往的旅客",
}

#: 没有 zone 时按生图类别兜底的场景（面板「发一张旅游照」这类手动请求）
_KIND_SCENES = {
    "travel": "户外风景区，远处有山和树木、开阔的天空和零星的游客",
    "shopping": "商场街边，有店铺招牌、明亮的橱窗、路人和街道",
    "weekend": "周末的街头公园，有树荫、长椅和散步的人",
}

#: 一句话里出现这些词，就当成「他已经说了在哪」，不用再补当前地点
PLACE_HINTS = (
    "家", "客厅", "卧室", "床", "厨房", "阳台", "卫生间", "洗手台", "沙发", "餐桌",
    "公司", "办公室", "工位", "写字楼", "单位", "会议室",
    "商场", "店铺", "柜台", "街", "路", "公园", "景区", "景点", "山", "海",
    "湖", "河", "江", "沙滩", "古镇", "游乐园", "动物园", "植物园",
    "餐厅", "饭馆", "店", "咖啡", "酒吧", "食堂", "超市", "书店", "图书馆",
    "地铁", "公交", "车厢", "车", "站台", "车站", "机场", "酒店", "宾馆", "民宿",
    "学校", "教室", "宿舍", "操场", "健身房", "医院", "小区",
)


def _activity_dict(activity):
    return activity if isinstance(activity, dict) else {}


def place_of(activity):
    """日程里洗过的地点（「他桌面上」→「家里」，太长的截断）。"""
    return clean_place(_activity_dict(activity).get("place"))


def zone_of(activity):
    """日程里的地图区域：home / office / mall / scenic / road / restaurant / hotel / station。"""
    zone = str(_activity_dict(activity).get("zone") or "").strip()
    if zone:
        return zone
    place = place_of(activity)
    if any(word in place for word in ("家", "客厅", "卧室", "沙发", "床", "厨房", "阳台")):
        return "home"
    if any(word in place for word in ("酒店", "宾馆", "民宿", "旅馆", "客栈")):
        return "hotel"
    if "路" in place or "街" in place:
        return "road"
    if "写字楼" in place or "公司" in place or "办公室" in place:
        return "office"
    if "商场" in place:
        return "mall"
    if "餐厅" in place or "饭馆" in place or "饭店" in place or "食堂" in place:
        return "restaurant"
    if "站" in place or "机场" in place:
        return "station"
    return ""


def scene_blob(activity):
    """zone / 地点 / 活动拼起来，给关键词判断用。"""
    activity = _activity_dict(activity)
    return "%s%s%s" % (
        str(activity.get("activity") or ""),
        str(activity.get("place") or ""),
        str(activity.get("spot") or ""),
    )


def is_travel(activity):
    return zone_of(activity) == "scenic" or any(word in scene_blob(activity) for word in TRAVEL_WORDS)


def is_shopping(activity):
    return zone_of(activity) == "mall" or any(word in scene_blob(activity) for word in SHOPPING_WORDS)


def is_swim(activity):
    """在游泳馆/海边/水上乐园玩水（这时候穿的是泳装，背景也得对）。"""
    return any(word in scene_blob(activity) for word in SWIM_WORDS)


def _swim_scene(activity):
    text = scene_blob(_activity_dict(activity))
    if any(word in text for word in ("海边", "沙滩", "海浪", "海里")):
        return "海边，沙滩、海浪和远处的海面，旁边放着毛巾和防晒"
    if any(word in text for word in ("水上乐园", "水世界")):
        return "水上乐园，彩色的滑道、水池和玩水的人"
    return "游泳馆的泳池边，蓝色水面、泳道和池边的躺椅"


def matches_kind(kind, activity):
    """当前日程配不配得上这个生图类别（不配时用手动类别自己的固定场景）。"""
    kind = str(kind or "")
    if kind == "travel":
        return is_travel(activity)
    if kind == "shopping":
        return is_shopping(activity)
    if kind == "weekend":
        # 周末在家不该硬塞一张公园照：只有人确实在外面才算对上
        return zone_of(activity) in ("mall", "scenic", "road", "restaurant", "hotel", "station") \
            or any(word in scene_blob(activity) for word in ("周末", "逛", "玩", "出门"))
    return True


def _is_specific(place):
    if not place:
        return False
    has_generic = any(word in place for word in GENERIC_PLACE_WORDS)
    return not has_generic


def _home_scene(activity):
    """在家也要分房间：睡觉在床上、吃饭在餐桌、洗澡在洗手台。"""
    activity = _activity_dict(activity)
    category = str(activity.get("category") or "")
    act = str(activity.get("activity") or "")
    if category == "sleep" or any(word in act for word in ("睡", "被窝", "赖床")):
        return "家里的卧室，床铺、被子和床头的小灯"
    if category == "meal" or any(word in act for word in ("吃", "早饭", "午饭", "晚饭", "面", "饭")):
        return "家里的餐桌旁，桌上的碗筷和热乎的饭菜"
    if any(word in act for word in ("洗", "刷牙", "护肤", "化妆", "洗脸", "吹头")):
        return "家里的卫生间，洗手台、镜子和挂着的毛巾"
    if any(word in act for word in ("换", "睡衣", "衣柜", "扎头", "梳头")):
        return "家里的卧室，衣柜、全身镜和叠好的睡衣"
    if category == "work" or any(word in act for word in ("加班", "改方案", "文档", "电脑")):
        return "家里的书桌前，笔记本电脑、台灯和摊开的资料"
    if category == "rest" or any(word in act for word in ("沙发", "追剧", "刷手机", "瘫")):
        return "家里的客厅，沙发、抱枕和茶几"
    return _ZONE_SCENES["home"]


def _road_scene(activity):
    activity = _activity_dict(activity)
    act = str(activity.get("activity") or "")
    transit = str(activity.get("transit") or "")
    vehicle = str(activity.get("vehicle") or "")
    text = act + transit + vehicle
    if any(word in text for word in ("地铁", "subway", "metro", "车厢里站着")):
        return "地铁车厢里，扶手、车窗和站着的乘客"
    if any(word in text for word in ("公交", "大巴")):
        return "公交车里，车窗、扶手和坐着的乘客"
    if any(word in text for word in ("打车", "taxi", "网约", "开车", "车里", "驾驶", "车上")):
        return "车里，车窗外是流动的街景"
    return _ZONE_SCENES["road"]


def scene_for(activity, kind=""):
    """日程 → 一句能直接进提示词的中文场景（不带「在」，调用方自己加）。

    什么都不知道（拿不到日程）时返回空串，让调用方自己决定兜底，
    不要在「她此刻在哪」完全未知时硬塞一句「街边」。
    """
    activity = _activity_dict(activity)
    kind = str(kind or "").strip()
    zone = zone_of(activity)
    place = place_of(activity)
    spot = str(activity.get("spot") or "").strip()

    if is_swim(activity):
        # 她去玩水：背景是泳池/海边/水上乐园，不能画成普通街景或客厅
        return _swim_scene(activity)

    if not zone:
        specific = spot or place
        if _is_specific(specific):
            return "%s，日常的生活场景" % specific
        if place and any(word in place for word in ("家", "床", "沙发")):
            zone = "home"
        elif kind in _KIND_SCENES:
            return _KIND_SCENES[kind]
        elif not place:
            return ""
        else:
            return place

    anchor = spot or place
    if zone in _ZONE_PROPS and _is_specific(anchor):
        # 地点本身够具体（西湖 / 咖啡馆 / 某个商场）：用它当地点，接这类的陈设
        if zone == "scenic":
            return "%s，风景区，%s" % (anchor, _ZONE_PROPS[zone])
        return "%s，%s" % (anchor, _ZONE_PROPS[zone])
    if zone == "home":
        scene = _home_scene(activity)
    elif zone == "road":
        scene = _road_scene(activity)
    else:
        scene = _ZONE_SCENES.get(zone, "")
    # 泛称地点（「公司」「商场」「路上」）不再重复拼在 scene 前面：
    # 固定场景本身已经带了这个词（「公司里的办公室」「商场里」），
    # 拼两遍会让模型把「写字楼」当外景画，室内场景反而没了。
    return scene or place or "日常"


def has_place_hint(text):
    """这句话里他自己说了地点吗（说了就不拿日程地点覆盖）。"""
    text = str(text or "")
    return any(word and word in text for word in PLACE_HINTS)
