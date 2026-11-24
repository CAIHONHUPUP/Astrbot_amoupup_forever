# -*- coding: utf-8 -*-
"""房屋系统（2026-11-14）：她住哪儿、家是什么样、家里有哪些家具。

数据全部落在 amo 的 kv（键名 housing），结构：

{
  "version": 1,
  "residence_id": "home-rent",
  "homes": [
    {"id": "home-rent", "name": "温馨出租屋", "kind": "rent" | "own",
     "city": "", "style": "出租屋简易风", "colors": "米白色",
     "furniture": ["单人床", ...], "rent_cents": 150000,
     "status": "living" | "idle" | "rented_out",
     "images": {"living": "", "bedroom": ""}, "images_needed": true}
  ]
}

职责：
- current() 永远指向 residence_id 那套；多套房用 move_in() 切换；
- prompt_line() 把当前房间的装修/主色/家具注入聊天，家具清单是事实边界；
- /我的家、/房屋 房产|租房、/搬家 <名字> 由 amo_commands 调用；
- 破产时 on_bankruptcy() 强制搬回最便宜的出租屋；
- 买大件家具/装修会置 images_needed，客厅/卧室参考图由上层按需生成。

纯状态 + 文案，不直接生图，方便单测。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import os

try:
    from .amo_shared import city_or_empty, logger
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from amo_shared import city_or_empty, logger
        except Exception:  # pragma: no cover
            import logging

            logger = logging.getLogger(__name__)

            def city_or_empty(plugin):
                return ""

STYLE_DEFAULT = "出租屋简易风"
COLORS_DEFAULT = "米白色"
RENT_LIVING = ["二手布艺沙发", "简易木质茶几", "塑料收纳箱(当电视柜)", "落地晾衣架", "垃圾桶"]
RENT_BEDROOM = ["1.2米单人床", "简易布衣柜", "床头小台灯", "塑料盆"]
OWN_LIVING = ["三人位软包沙发", "实木电视柜", "智能电视", "毛绒地毯", "绿植盆栽", "落地灯"]
OWN_BEDROOM = ["1.8米双人床", "到顶大衣柜", "梳妆台", "床头柜", "暖光台灯"]
DEFAULT_ROOMS = {
    "rent": {"living": list(RENT_LIVING), "bedroom": list(RENT_BEDROOM)},
    "own": {"living": list(OWN_LIVING), "bedroom": list(OWN_BEDROOM)},
}
DEFAULT_FURNITURE = tuple(RENT_LIVING + RENT_BEDROOM)
SEASON_SUMMER = "夏季薄毯、电风扇"
SEASON_WINTER = "冬季厚地毯、暖气片"
IMAGE_COOLDOWN_SECONDS = 24 * 3600
BEDROOM_WORDS = ("床", "衣柜", "梳妆台", "床头柜", "台灯", "睡", "卧室")
BATHROOM_WORDS = ("洗手台", "洗手池", "镜子", "毛巾", "浴", "牙刷", "淋浴",
                  "马桶", "厕所", "洗手间", "浴室")
KITCHEN_WORDS = ("灶台", "橱柜", "冰箱", "锅", "微波炉", "厨房", "抽油烟机")
#: 房间画像（P22，2026-11-17 扩到四间）：睡觉/躺床/换衣→卧室，浴室→浴室，厨房→厨房，
#: 其余在家场景→客厅。写进 housing 配置，生图分房间读它；改默认值只影响新家，
#: 不动已经落进 kv 的户——老数据里的 home_bath 在 room_profiles() 里按默认值归位。
ROOM_PROFILES = {
    "bedroom": {"label": "卧室", "zones": ("home_bed",),
                "places": ("卧室", "床", "被窝", "睡衣", "衣柜", "换衣")},
    "living": {"label": "客厅",
               "zones": ("home", "home_sofa", "home_desk", "home_meal", "home_generic"),
               "places": ("客厅", "沙发", "茶几", "餐桌", "书桌")},
    "bathroom": {"label": "浴室", "zones": ("home_bath",),
                 "places": ("浴室", "洗手台", "洗手池", "镜子", "毛巾", "洗澡", "洗漱", "刷牙")},
    "kitchen": {"label": "厨房", "zones": ("home_kitchen",),
                "places": ("厨房", "灶台", "橱柜", "做饭", "煮饭", "下厨", "冰箱")},
}
#: 2026-11-17 新增的两间；老 kv 里的 zone 归属要按它们归位
NEW_ROOMS = ("bathroom", "kitchen")
ROOM_KEYS = ("living", "bedroom", "bathroom", "kitchen")
FURNITURE_CATEGORIES = (
    "沙发", "床", "茶几", "电视柜", "电视", "衣柜", "书桌", "餐桌", "椅子", "书架",
    "冰箱", "洗衣机", "地毯", "窗帘", "镜子", "洗手台", "浴缸", "马桶", "灶台",
    "油烟机", "橱柜", "空调", "落地灯", "床头柜",
)


FURNITURE_COLORS = {
    "sofa": "棕色", "bed": "米白色", "wardrobe": "灰色", "desk": "原木色",
    "dining": "原木色", "chair": "灰色", "tv": "黑色", "coffee": "原木色",
    "fridge": "银色", "stove": "黑色", "washer": "白色", "dryer": "白色",
    "mirror": "银色", "tub": "白色", "toilet": "白色", "sink": "白色",
    "curtain": "米白色", "rug": "米色", "lamp": "暖白色", "nightstand": "原木色",
    "plant": "绿色", "painting": "彩色", "shelf": "原木色", "cabinet": "白色",
    "hood": "银色", "ac": "白色",
}
FURNITURE_EXCLUDE = ("牙刷", "毛巾", "纸巾", "洗发", "沐浴", "护肤", "牙膏",
                     "洗衣液", "洗面奶", "锅", "碗", "垃圾桶", "拖鞋", "衣架",
                     "餐具", "杯子", "地毯", "窗帘", "挂画", "绿植", "抱枕", "靠垫",
                     "盆", "水桶", "晾衣架", "床单", "被套", "枕套", "枕头", "被子",
                     "毛毯")
ROOM_FURNITURE_LIMIT = 6
COLOR_WORDS = ("红", "橙", "黄", "绿", "青", "蓝", "紫", "粉", "黑", "白",
               "灰", "棕", "米", "银", "金", "原木")


def home_palette_color(home, slot):
    style = str((home or {}).get("style") or "")
    colors = str((home or {}).get("colors") or "")
    text = style + colors
    if any(word in text for word in ("原木", "日式", "muji", "MUJI")):
        return "原木色"
    if any(word in text for word in ("工业", "黑白", "现代")):
        return "灰色"
    if any(word in text for word in ("米", "暖", "奶油", "白")):
        return "米白色"
    if any(word in text for word in ("冷", "蓝", "灰")):
        return "灰色"
    return FURNITURE_COLORS.get(slot, "灰色")


def furniture_color(home, name):
    body = str(name or "")
    full = {"红": "红色", "橙": "橙色", "黄": "黄色", "绿": "绿色", "青": "青色",
            "蓝": "蓝色", "紫": "紫色", "粉": "粉色", "黑": "黑色", "白": "白色",
            "灰": "灰色", "棕": "棕色", "米": "米色", "银": "银色", "金": "金色",
            "原木": "原木色"}
    for word in COLOR_WORDS:
        if word in body:
            return full.get(word, word)
    return home_palette_color(home, furniture_slot(body))


FURNITURE_GENERIC = {
    "sofa": "沙发", "bed": "床", "wardrobe": "衣柜", "desk": "书桌",
    "dining": "餐桌", "chair": "椅子", "tv": "电视", "coffee": "茶几",
    "fridge": "冰箱", "stove": "灶台", "washer": "洗衣机", "dryer": "烘干机",
    "mirror": "镜子", "tub": "浴缸", "toilet": "马桶", "sink": "洗手台",
    "curtain": "窗帘", "rug": "地毯", "lamp": "台灯", "tv_cabinet": "电视柜",
    "nightstand": "床头柜", "plant": "绿植", "painting": "挂画",
    "shelf": "书架", "cabinet": "橱柜", "hood": "油烟机", "ac": "空调",
    "cushion": "抱枕",
}


FURNITURE_STYLES = {
    "sofa": "布艺", "bed": "实木", "wardrobe": "布艺", "desk": "木质", "dining": "木质",
    "chair": "木质", "tv": "平板", "tv_cabinet": "简约", "coffee": "木质", "fridge": "双门",
    "stove": "燃气",
    "washer": "滚筒", "dryer": "滚筒", "mirror": "简约", "tub": "陶瓷",
    "toilet": "陶瓷", "sink": "陶瓷", "curtain": "布艺", "rug": "短毛",
    "lamp": "陶瓷", "nightstand": "木质", "plant": "盆栽", "painting": "装饰",
    "shelf": "木质", "cabinet": "木质", "hood": "不锈钢", "ac": "壁挂",
    "cushion": "布艺",
}
FURNITURE_STYLE_WORDS = ("布艺", "木质", "实木", "皮质", "真皮", "玻璃", "陶瓷",
                         "金属", "不锈钢", "藤编", "塑料", "滚筒", "双门", "壁挂",
                         "简约", "简易", "折叠", "平板", "装饰", "盆栽", "短毛")


def furniture_style(name):
    body = str(name or "")
    for word in FURNITURE_STYLE_WORDS:
        if word in body:
            return word
    return FURNITURE_STYLES.get(furniture_slot(body), "简约")


def furniture_display(home, name):
    meta = (home or {}).get("furniture_meta") or {}
    color = str((meta.get(name) or {}).get("color") or furniture_color(home, name))
    generic = FURNITURE_GENERIC.get(furniture_slot(name)) or str(name)
    style = furniture_style(name)
    if style and style not in generic:
        return color + style + generic
    return color + generic


FURNITURE_SLOTS = (
    ("沙发", "sofa"), ("电视柜", "tv_cabinet"), ("电视", "tv"), ("茶几", "coffee"),
    ("台灯", "lamp"), ("床", "bed"),
    ("衣柜", "wardrobe"), ("书桌", "desk"), ("电脑桌", "desk"), ("餐桌", "dining"),
    ("餐椅", "chair"), ("椅子", "chair"), ("书柜", "shelf"), ("书架", "shelf"),
    ("冰箱", "fridge"), ("灶台", "stove"), ("油烟机", "hood"), ("橱柜", "cabinet"),
    ("洗手台", "sink"), ("浴缸", "tub"), ("马桶", "toilet"), ("镜子", "mirror"),
    ("窗帘", "curtain"), ("地毯", "rug"), ("空调", "ac"), ("落地灯", "lamp"),
    ("床头柜", "nightstand"), ("绿植", "plant"), ("挂画", "painting"),
    ("抱枕", "cushion"), ("靠垫", "cushion"),
)


def furniture_slot(name):
    """同功能位只留一件（买新沙发替换旧沙发）。"""
    body = str(name or "")
    for word, slot in FURNITURE_SLOTS:
        if word in body:
            return slot
    return ""


def furniture_category(name):
    """「红色三人位沙发」→「沙发」：同类家具买新的要替换旧的。"""
    body = str(name or "")
    for word in FURNITURE_CATEGORIES:
        if word in body:
            return word
    return ""
#: 浴室/厨房没有专属家具清单时的通用陈设（不许硬套客厅/卧室家具）
DEFAULT_ROOM_FURNITURE = {
    "bathroom": ["白色陶瓷洗手台", "方镜", "瓷砖墙面", "毛巾架和毛巾", "淋浴间玻璃隔断"],
    "kitchen": ["灶台", "油烟机", "橱柜", "冰箱", "小餐桌和两把椅子"],
}


def default_rooms_config():
    """新家/老数据都补一份房间画像，返回深拷贝，别让实例互相改到。"""
    return {room: {"label": str(profile.get("label") or ""),
                   "zones": tuple(profile.get("zones") or ()),
                   "places": tuple(profile.get("places") or ())}
            for room, profile in ROOM_PROFILES.items()}

def room_of_item(name):
    body = str(name or "")
    if "洗衣" in body or "烘干" in body:
        return "bathroom"
    if any(word in body for word in BEDROOM_WORDS):
        return "bedroom"
    if any(word in body for word in BATHROOM_WORDS):
        return "bathroom"
    if any(word in body for word in KITCHEN_WORDS):
        return "kitchen"
    return "living"


def room_key_of(value):
    """把 room id / 中文房名归一成 living|bedroom|bathroom|kitchen（不认识按客厅）。"""
    text = str(value or "").strip()
    if text in ("living", "bedroom", "bathroom", "kitchen"):
        return text
    if "卧" in text or "床" in text:
        return "bedroom"
    if "浴" in text or "厕" in text or "洗手" in text:
        return "bathroom"
    if "厨" in text or "灶" in text:
        return "kitchen"
    return "living"


def season_line(now=None):
    month = (now or _amo_clock.now()).month
    if 6 <= month <= 9:
        return SEASON_SUMMER
    if month in (12, 1, 2):
        return SEASON_WINTER
    return ""


def build_room_image_prompt(room_type, style, colors, furniture_list, now=None):
    """按房间动态拼生图 Prompt；返回 (正向词, 负向词)。"""
    room_type = str(room_type or "客厅")
    style = str(style or "简约风")
    colors = str(colors or "米白色")
    base = ("一张空无一人的%s室内图，第一人称视角，广角镜头，照片级写实，室内摄影，"
            "8k分辨率，温馨的生活气息，阳光透过窗户洒进来。" % room_type)
    room_key = room_key_of(room_type)
    items = [str(x) for x in (furniture_list or []) if str(x).strip()]
    if not items:
        items = list(DEFAULT_ROOM_FURNITURE.get(room_key) or ())
    furniture_str = "、".join(items) or "家具寥寥无几"
    if room_key == "bedroom":
        must_not = "沙发、电视柜、茶几、餐桌、马桶、厨房灶台、人物、宠物"
    elif room_key == "bathroom":
        must_not = "床、衣柜、梳妆台、沙发、电视柜、茶几、餐桌、厨房灶台、人物、宠物"
    elif room_key == "kitchen":
        must_not = "床、衣柜、梳妆台、沙发、电视柜、茶几、马桶、淋浴间、人物、宠物"
    else:
        must_not = "床、衣柜、梳妆台、床头柜、马桶、厨房灶台、人物、宠物"
    attr = ("装修风格为%s，主色调为%s。房间内真实摆放着：%s。"
            "房间里只能出现清单里的家具，不要添加清单外的家具、电器或杂物；"
            "家具要落地、有真实阴影、比例正确，空间不要拥挤。"
            "请将这些物品合理搭配进整体的装修风格中，保持画面的和谐与生活感。"
            "装修风格和主色调是硬约束（新家第一次生成也按这个来），不要随机换风格、"
            "不要给家具换颜色，家具颜色以清单为准。"
            % (style, colors, furniture_str))
    soft_visual = {"living": "地毯、窗帘、挂画、抱枕",
                   "bedroom": "窗帘、地毯",
                   "bathroom": "毛巾、浴帘",
                   "kitchen": "窗帘、桌布"}.get(room_key, "")
    if soft_visual:
        attr += "软装只影响画面、不算家具：可以有%s。" % soft_visual
    attr += "画面里不要出现：%s。" % must_not
    season = season_line(now)
    if season:
        attr += "季节感：%s。" % season
    negative = ("人物, 人类, 文字, 水印, 二次元, 插画, 拥挤, 杂乱无章, 多出来的腿, "
                + must_not)
    return base + attr, negative

BIG_WORDS = ("沙发", "床", "衣柜", "餐桌", "书桌", "书柜", "电视柜", "茶几", "冰箱",
             "洗衣机", "空调", "梳妆台", "地毯", "窗帘", "吊灯", "置物架", "鞋柜",
             "电脑桌", "床头柜")
SMALL_WORDS = ("台灯", "杯子", "抱枕", "床单", "被套", "花瓶", "挂画", "小摆件",
               "香薰", "收纳盒", "垃圾桶", "碗", "盘子", "拖鞋", "毛巾")

KIND_LABEL = {"rent": "租房", "own": "已购房"}
STATUS_LABEL = {"living": "自住中", "idle": "闲置", "rented_out": "已出租",
                "expired": "已退租"}


def classify_furniture(name):
    """大件才触发重新生图；杯子、抱枕这类小件不折腾。"""
    body = str(name or "").strip()
    if not body:
        return "small"
    if any(word in body for word in BIG_WORDS):
        return "big"
    if any(word in body for word in SMALL_WORDS):
        return "small"
    return "small"


class Housing:
    def __init__(self, plugin):
        self.p = plugin
        self.data = self._load()

    # ---------- 存取 ----------
    def _kv_get(self, key, default):
        try:
            return self.p.db.kv_get(key, default)
        except Exception:
            return default

    def _kv_set(self, key, value):
        try:
            self.p.db.kv_set(key, value)
        except Exception as exc:
            logger.debug("[amo] 房屋状态写不进去: %s" % exc)

    def _city(self):
        try:
            return str(city_or_empty(self.p) or "").strip()
        except Exception:
            return ""

    def _seed(self):
        city = self._city()
        home = {
            "id": "home-rent",
            "name": "温馨出租屋",
            "kind": "rent",
            "city": city,
            "style": STYLE_DEFAULT,
            "colors": COLORS_DEFAULT,
            "furniture": list(RENT_LIVING + RENT_BEDROOM),
            "room_furniture": {"living": list(RENT_LIVING), "bedroom": list(RENT_BEDROOM)},
            "rooms": default_rooms_config(),
            "rent_cents": 150000,
            "status": "living",
            "images": {room: "" for room in ROOM_KEYS},
            "images_needed": True,
            "image_status": "pending",
            "last_image_ts": 0,
            "rooms_needed": list(ROOM_KEYS),
            "image_fail_count": 0,
            "image_fail_until": 0,
        }
        return {"version": 1, "residence_id": home["id"], "homes": [home]}

    def _load(self):
        raw = self._kv_get("housing", None)
        if not isinstance(raw, dict) or not isinstance(raw.get("homes"), list) or not raw["homes"]:
            data = self._seed()
            self._kv_set("housing", data)
            return data
        homes = []
        for index, row in enumerate(raw["homes"]):
            if not isinstance(row, dict):
                continue
            home = dict(row)
            home.setdefault("id", "home-%d" % index)
            home.setdefault("name", "家")
            home.setdefault("kind", "rent")
            home.setdefault("city", "")
            home.setdefault("style", STYLE_DEFAULT)
            home.setdefault("colors", COLORS_DEFAULT)
            furniture = home.get("furniture")
            home["furniture"] = [str(x) for x in furniture] if isinstance(furniture, list) else list(DEFAULT_FURNITURE)
            rooms = home.get("room_furniture")
            if not isinstance(rooms, dict) or not (rooms.get("living") or rooms.get("bedroom")):
                legacy = {"单人床", "木质书桌", "布艺小沙发", "粉色台灯"}
                if set(home["furniture"]) == legacy:
                    preset = DEFAULT_ROOMS.get("own" if home.get("kind") == "own" else "rent", DEFAULT_ROOMS["rent"])
                    rooms = {"living": list(preset["living"]), "bedroom": list(preset["bedroom"])}
                    home["furniture"] = list(preset["living"] + preset["bedroom"])
                else:
                    rooms = {"living": [], "bedroom": []}
                    for item in home["furniture"]:
                        rooms[room_of_item(item)].append(item)
            room_furniture = {
                "living": [str(x) for x in (rooms.get("living") or [])],
                "bedroom": [str(x) for x in (rooms.get("bedroom") or [])],
            }
            for room_key, default_items in DEFAULT_ROOM_FURNITURE.items():
                room_furniture[room_key] = [str(x) for x in (rooms.get(room_key) or default_items)]
            washer_names = [item for item in home["furniture"]
                            if "洗衣" in str(item) or "烘干" in str(item)]
            if washer_names:
                for room_key, items in list(room_furniture.items()):
                    if room_key != "bathroom":
                        room_furniture[room_key] = [item for item in items
                                                    if item not in washer_names]
                bath = room_furniture.setdefault("bathroom", [])
                for item in washer_names:
                    if item not in bath:
                        bath.append(item)
            soft_words = ("地毯", "窗帘", "挂画", "绿植", "抱枕", "靠垫")
            home["furniture"] = [item for item in home.get("furniture", [])
                                 if not any(word in str(item) for word in soft_words)]
            for room_key, items in list(room_furniture.items()):
                room_furniture[room_key] = [item for item in items
                                            if not any(word in str(item) for word in soft_words)]
            dedup = {}
            for room_key, items in list(room_furniture.items()):
                kept = []
                seen_slots = set()
                for item in items:
                    slot = furniture_slot(item)
                    if slot and slot in seen_slots:
                        home["furniture"] = [x for x in home["furniture"] if x != item]
                        continue
                    if slot:
                        seen_slots.add(slot)
                    kept.append(item)
                dedup[room_key] = kept
            home["room_furniture"] = dedup
            rooms_cfg = home.get("rooms")
            rooms_cfg = dict(rooms_cfg) if isinstance(rooms_cfg, dict) else {}
            defaults = default_rooms_config()
            for room_key, profile in defaults.items():
                merged = dict(profile)
                override = rooms_cfg.get(room_key)
                if isinstance(override, dict):
                    if str(override.get("label") or "").strip():
                        merged["label"] = str(override["label"]).strip()
                    for field in ("zones", "places"):
                        values = override.get(field)
                        if isinstance(values, (list, tuple)) and values:
                            merged[field] = tuple(str(x) for x in values)
                rooms_cfg[room_key] = merged
            home["rooms"] = rooms_cfg
            home.setdefault("rent_cents", 0)
            home.setdefault("status", "living" if home.get("id") == raw.get("residence_id") else "idle")
            images = home.get("images")
            images = dict(images) if isinstance(images, dict) else {}
            for room_key in ROOM_KEYS:
                images.setdefault(room_key, "")
            home["images"] = images
            needed = [room_key for room_key in (home.get("rooms_needed") or []) if room_key in ROOM_KEYS]
            home["rooms_needed"] = needed
            home["images_needed"] = bool(needed) or not all(images.get(k) for k in ROOM_KEYS)
            home["image_status"] = "ready" if not home["images_needed"] else "pending"
            home.setdefault("last_image_ts", 0)
            home.setdefault("image_fail_count", 0)
            home.setdefault("image_fail_until", 0)
            homes.append(home)
        data = {"version": 1, "residence_id": str(raw.get("residence_id") or homes[0]["id"]), "homes": homes}
        self._kv_set("housing", data)
        return data

    def _save(self):
        self._kv_set("housing", self.data)

    # ---------- 查询 ----------
    def homes(self):
        return [home for home in (self.data.get("homes") or [])
                if not home.get("hidden")]

    def all_homes(self):
        return list(self.data.get("homes") or [])

    def current(self):
        rid = str(self.data.get("residence_id") or "")
        for home in self.homes():
            if home.get("id") == rid:
                return home
        homes = self.homes()
        return homes[0] if homes else self._seed()["homes"][0]

    def by_ref(self, ref):
        key = str(ref or "").strip()
        if not key:
            return None
        for home in self.homes():
            if home.get("id") == key or str(home.get("name") or "").strip() == key:
                return home
        return None

    def image_dir(self):
        """参考图目录：plugin_data/Astrbot_amoupup_forever/outfit_refs/家。"""
        try:
            root = os.path.dirname(str(self.p.db.path or ""))
        except Exception:
            root = ""
        return os.path.join(root, "outfit_refs", "家")

    # ---------- 变更 ----------
    def add_home(self, name, kind="own", city="", style="简约风", colors="米白色",
                 furniture=None, rent_cents=0):
        body = str(name or "").strip() or ("新家" if kind == "own" else "出租屋")
        used = {str(home.get("id") or "") for home in self.homes()}
        index = len(used) + 1
        while ("home-%d" % index) in used:
            index += 1
        home = {
            "id": "home-%d" % index,
            "name": body,
            "kind": "own" if kind == "own" else "rent",
            "city": str(city or self._city()),
            "style": str(style or "简约风"),
            "colors": str(colors or "米白色"),
            "furniture": [],
            "room_furniture": {"living": [], "bedroom": []},
            "rooms": default_rooms_config(),
            "rent_cents": int(rent_cents or 0),
            "status": "idle",
            "images": {room: "" for room in ROOM_KEYS},
            "images_needed": True,
            "image_status": "pending",
            "last_image_ts": 0,
            "rooms_needed": list(ROOM_KEYS),
            "image_fail_count": 0,
            "image_fail_until": 0,
        }
        if furniture:
            for item in furniture:
                home["furniture"].append(str(item))
                home["room_furniture"][room_of_item(item)].append(str(item))
        else:
            preset = DEFAULT_ROOMS.get("own" if home["kind"] == "own" else "rent", DEFAULT_ROOMS["rent"])
            home["room_furniture"] = {"living": list(preset["living"]),
                                      "bedroom": list(preset["bedroom"])}
            home["furniture"] = list(preset["living"] + preset["bedroom"])
        self.data.setdefault("homes", []).append(home)
        self._save()
        return home

    def _cleanup_home_images(self, home, rooms=None):
        """删掉旧家对应房间的物理图片；换房四间全删，换家具只删那一间。"""
        deleted = []
        rooms = tuple(rooms or ROOM_KEYS)
        images = dict((home or {}).get("images") or {})
        for room in rooms:
            path = str(images.get(room) or "").strip()
            if not path:
                continue
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    deleted.append(path)
            except Exception as exc:
                logger.debug("[amo] 旧家场景图删除失败（%s）: %s" % (path, exc))
        if isinstance(home, dict):
            images = dict(home.get("images") or {})
            for room in rooms:
                images[room] = ""
            home["images"] = images
            home["images_needed"] = not all(images.get(key) for key in ROOM_KEYS)
        return deleted

    def invalidate_images(self, home=None, reason="", rooms=None, delete=False):
        """房子/家具/装修变了：删掉旧场景图并标记要重画（2026-11-19）。

        只删这个家自己的图（images 里记录的路径 + image_dir/<id>-living|bedroom.png），
        不会误删别的家的图。
        """
        home = home if isinstance(home, dict) else self.current()
        rooms = tuple(rooms or ROOM_KEYS)
        home_id = str((home or {}).get("id") or "")
        deleted = []
        if delete:
            deleted = self._cleanup_home_images(home, rooms)
        if isinstance(home, dict):
            needed = set(home.get("rooms_needed") or [])
            needed.update(rooms)
            home["rooms_needed"] = [room for room in ROOM_KEYS if room in needed]
            home["images_needed"] = True
            home["image_status"] = "pending"
            home["last_image_ts"] = 0
            home["image_fail_count"] = 0
            home["image_fail_until"] = 0
        self.last_image_invalidate = {"home": home_id, "reason": str(reason or ""),
                                      "deleted": list(deleted)}
        self._save()
        return deleted

    def move_in(self, ref):
        """/搬家 <房产名|id>：清理旧图、切换 current、退租标记。"""
        home = self.by_ref(ref)
        if home is None:
            names = "、".join(str(item.get("name")) for item in self.homes()) or "（无）"
            return False, "找不到这个家。现有：%s" % names, None
        old = self.current()
        deleted = []
        if old.get("id") != home.get("id"):
            deleted = self._cleanup_home_images(old)
            if old.get("kind") == "rent" and home.get("kind") == "own":
                # 退租搬进自己买的房：合同标记过期，不再产生房租
                old["status"] = "expired"
                old["contract_ended"] = True
        if old.get("id") != home.get("id"):
            home["furniture"] = list(old.get("furniture") or [])
            home["room_furniture"] = {k: list(v) for k, v in
                                       (old.get("room_furniture") or {}).items()}
            home["furniture_meta"] = dict(old.get("furniture_meta") or {})
            for name, meta in home["furniture_meta"].items():
                if "color_source" not in (meta or {}):
                    meta["color_source"] = ("name" if any(w in name for w in COLOR_WORDS)
                                             else "default")
                if str((meta or {}).get("color_source") or "") == "default":
                    meta["color"] = home_palette_color(home, furniture_slot(name))
            old["furniture"] = []
            old["room_furniture"] = {k: [] for k in (old.get("room_furniture") or {})}
            old["furniture_meta"] = {}
            if old.get("kind") == "rent":
                old["status"] = "expired"
                old["contract_ended"] = True
                old["hidden"] = True
            self.invalidate_images(home, reason="move-in", rooms=ROOM_KEYS)
        self.data["residence_id"] = home["id"]
        for item in self.homes():
            if item.get("id") == home["id"]:
                item["status"] = "living"
            elif item.get("status") == "living":
                item["status"] = "idle"
        needed = list(home.get("rooms_needed") or [])
        home["images_needed"] = bool(needed) or not all(home["images"].get(key) for key in ROOM_KEYS)
        home["image_status"] = "ready" if not home["images_needed"] else "pending"
        self.last_move = {"old": old, "deleted": deleted}
        self._save()
        if home.get("kind") == "own":
            text = "已搬入「%s」，终于不用交房租啦！" % home.get("name")
        else:
            text = "已搬进「%s」，先把家里收拾一下。" % home.get("name")
        return True, text, home

    def set_style(self, style, colors=""):
        """装修：换风格/主色，客厅卧室要重新生成。"""
        home = self.current()
        if style:
            home["style"] = str(style)
        if colors:
            home["colors"] = str(colors)
        self.invalidate_images(home, reason="style", rooms=ROOM_KEYS)
        return home

    def rent_out(self, ref, rent_cents=0):
        """把闲置的自有房产出租，赚被动收入。"""
        home = self.by_ref(ref)
        if home is None:
            return False, "找不到这套房产"
        if home.get("kind") != "own":
            return False, "只能出租自己名下的房产"
        if home.get("id") == self.current().get("id"):
            return False, "自己正住着，先搬家再出租"
        home["status"] = "rented_out"
        home["rent_cents"] = int(rent_cents or home.get("rent_cents") or 0)
        self._save()
        return True, "已把「%s」出租，每月收租 %.0f 元" % (
            home.get("name"), int(home.get("rent_cents") or 0) / 100.0)

    def sell_home(self, ref):
        """卖掉一套房：清图、移除；如果是当前住处就搬回出租屋。"""
        home = self.by_ref(ref)
        if home is None:
            return False, "找不到这套房产"
        was_current = home.get("id") == self.current().get("id")
        self._cleanup_home_images(home)
        self.data["homes"] = [item for item in self.homes() if item.get("id") != home.get("id")]
        name = home.get("name") or "房子"
        if not self.data["homes"]:
            self.data["homes"] = self._seed()["homes"]
            self.data["residence_id"] = self.data["homes"][0]["id"]
        elif was_current:
            rents = [item for item in self.homes() if item.get("kind") == "rent"]
            if not rents:
                rents = [self.add_home("临时出租屋", kind="rent", rent_cents=100000)]
            cheapest = min(rents, key=lambda item: int(item.get("rent_cents") or 0))
            self.move_in(cheapest["id"])
        self._save()
        return True, "已卖掉「%s」，钱到账了。" % name

    def add_furniture(self, name, source="", color=""):
        """添一件家具；大件触发一次场景图更新。"""
        body = str(name or "").strip()
        home = self.current()
        if not body:
            return {"ok": False, "reason": "empty", "home": home}
        if any(word in body for word in FURNITURE_EXCLUDE):
            return {"ok": False, "reason": "not_furniture", "msg": "这是日用品，不算家具"}
        if body in home.get("furniture", []):
            return {"ok": False, "reason": "exists", "home": home, "big": False, "redraw": False}
        target_room = room_of_item(body)
        slot = furniture_slot(body)
        category = furniture_category(body)
        if slot or category:
            def same_kind(entry):
                if slot:
                    return furniture_slot(entry) == slot
                return furniture_category(entry) == category
            home["furniture"] = [item for item in home.get("furniture", [])
                                  if not same_kind(item)]
            rooms_cfg = home.setdefault("room_furniture", {})
            for room_key, items in list(rooms_cfg.items()):
                rooms_cfg[room_key] = [item for item in items if not same_kind(item)]
        defaults = set(DEFAULT_ROOM_FURNITURE.get(target_room) or ())
        room_items = [item for item in (home.get("room_furniture") or {}).get(target_room) or []
                      if item not in defaults
                      and not any(word in str(item) for word in FURNITURE_EXCLUDE)]
        same_slot = furniture_slot(body)
        replacing = any(furniture_slot(item) == same_slot for item in room_items) if same_slot else False
        if len(room_items) >= ROOM_FURNITURE_LIMIT and not replacing:
            return {"ok": False, "reason": "room_full",
                    "msg": "%s放不下了（每间最多 %d 件）" % (target_room, ROOM_FURNITURE_LIMIT)}
        home["furniture"].append(body)
        rooms = home.setdefault("room_furniture", {"living": [], "bedroom": []})
        rooms.setdefault(target_room, []).append(body)
        meta = home.setdefault("furniture_meta", {})
        has_name_color = any(word in body for word in COLOR_WORDS)
        meta[body] = {"color": color or furniture_color(home, body),
                      "color_source": "name" if (color or has_name_color) else "default",
                      "source": str(source or ""),
                      "at": _amo_clock.now_ts()}
        big = classify_furniture(body) == "big"
        redraw = False
        # 任何家具都重画它所在房间；旧图先留着，生成成功再替换
        self.invalidate_images(home, reason="furniture:" + body,
                               rooms=(target_room,))
        redraw = self.can_redraw()
        self._save()
        return {"ok": True, "home": home, "big": big, "redraw": redraw}

    def furniture_detail(self, home=None):
        """只列这个住处真实拥有的家具（浴室/厨房的默认陈设不算家具）。"""
        home = home if isinstance(home, dict) else self.current()
        waiting = set(home.get("rooms_needed") or [])
        grouped: dict[str, list[str]] = {room: [] for room in ROOM_KEYS}
        for name in home.get("furniture") or []:
            body = str(name or "")
            if any(word in body for word in FURNITURE_EXCLUDE):
                continue
            grouped.setdefault(room_of_item(body), []).append(body)
        out = []
        for room, label in (("living", "客厅"), ("bedroom", "卧室"),
                            ("bathroom", "浴室"), ("kitchen", "厨房")):
            items = []
            for name in grouped.get(room) or []:
                meta = (home.get("furniture_meta") or {}).get(name, {})
                items.append({"name": name,
                              "display": furniture_display(home, name),
                              "source": str(meta.get("source") or ""),
                              "at": meta.get("at") or 0})
            out.append({"room": room, "label": label, "items": items,
                        "pending": room in waiting})
        return out

    def remove_furniture(self, name):
        """退货/丢掉家具：从清单移除，所在房间标记重画（旧图删除）。"""
        body = str(name or "").strip()
        home = self.current()
        if not body:
            return None
        room = room_of_item(body)
        home["furniture"] = [item for item in home.get("furniture", []) if item != body]
        rooms_cfg = home.setdefault("room_furniture", {})
        for key, items in list(rooms_cfg.items()):
            rooms_cfg[key] = [item for item in items if item != body]
        (home.setdefault("furniture_meta", {})).pop(body, None)
        self.invalidate_images(home, reason="remove-furniture:" + body, rooms=(room,))
        return {"ok": True, "room": room}

    def on_bankruptcy(self):
        """破产/法拍：强制搬回最便宜的出租屋。"""
        rents = [home for home in self.homes() if home.get("kind") == "rent"]
        if not rents:
            home = self.add_home("临时出租屋", kind="rent", rent_cents=100000,
                                 style=STYLE_DEFAULT, furniture=list(DEFAULT_FURNITURE))
            rents = [home]
        cheapest = min(rents, key=lambda home: int(home.get("rent_cents") or 0))
        ok, _, _ = self.move_in(cheapest["id"])
        if ok:
            spawn = getattr(self.p, "_spawn", None)
            ensure = getattr(self.p, "_ensure_home_images", None)
            if callable(spawn) and callable(ensure):
                try:
                    spawn(ensure(force=True))
                except Exception as exc:
                    logger.debug("[amo] optional step failed: %r", exc)
        return cheapest if ok else None

    # ---------- 文案 ----------
    def kind_label(self, home):
        return KIND_LABEL.get(str((home or {}).get("kind") or ""), "租房")

    def _furniture_text(self, home, limit=None):
        items = [str(x) for x in (home or {}).get("furniture") or [] if str(x).strip()]
        if not items:
            return "（空）"
        if limit and len(items) > 8:
            return "、".join(items[:int(limit)]) + "…等 %d 件物品" % len(items)
        return "、".join(items)

    def _city_prefix(self, home):
        city = str((home or {}).get("city") or "") or self._city()
        return (city + "·") if city else ""

    def prompt_line(self):
        home = self.current()
        return ("【家】你住在%s%s（%s），装修是%s，主色%s；家里的陈设以这份清单为准：%s。"
                "说到房间、家具、在家做什么时，就按这份清单写。"
                % (self._city_prefix(home), home.get("name") or "家",
                   self.kind_label(home), home.get("style") or "简约风",
                   home.get("colors") or "米白色", self._furniture_text(home)))

    def card_text(self, page=""):
        target = str(page or "").strip()
        if target in ("房产", "房子"):
            return self._own_page()
        if target in ("租房", "房租"):
            return self._rent_page()
        home = self.current()
        status = str(home.get("image_status") or "")
        if (home.get("images") or {}).get("living"):
            if status == "pending":
                images = "旧图 · 新家具正在拆快递/摆放中，晚点给你看新图哦"
            else:
                images = "已生成"
        elif home.get("images_needed") or status == "pending":
            images = "正在给新家打扫卫生、布置家具，图片生成中..."
        else:
            images = "待生成"
        return ("【当前居住】%s · %s%s（%s）\n装修：%s；主色：%s\n家具：%s\n场景图：%s\n"
                "切换查看：发送 /房屋 房产 · /房屋 租房；搬家：/搬家 <名字>"
                % (self.kind_label(home), self._city_prefix(home), home.get("name") or "家",
                   STATUS_LABEL.get(str(home.get("status") or ""), "自住中"),
                   home.get("style") or "简约风", home.get("colors") or "米白色",
                   self._furniture_text(home, 6), images))

    def _own_page(self):
        rows = [home for home in self.homes() if home.get("kind") == "own"]
        if not rows:
            return "名下暂无房产，还在努力攒首付中~"
        current = self.current()
        lines = ["【房产】"]
        for home in rows:
            tag = STATUS_LABEL.get(str(home.get("status") or ""), "闲置")
            if home.get("id") == current.get("id"):
                tag = "自住中"
            extra = ""
            if home.get("status") == "rented_out" and int(home.get("rent_cents") or 0) > 0:
                extra = "，每月收租 %.0f 元" % (int(home["rent_cents"]) / 100.0)
            lines.append("· [%s] %s%s（%s）%s" % (
                tag, self._city_prefix(home), home.get("name") or "家",
                home.get("style") or "简约风", extra))
        return "\n".join(lines)

    def _rent_page(self):
        rows = [home for home in self.homes()
                if home.get("kind") == "rent" and home.get("status") != "expired"]
        current = self.current()
        if not rows:
            return "目前未产生租房支出。"
        if current.get("kind") == "own":
            return "目前未产生租房支出。"
        lines = ["【租房】"]
        for home in rows:
            tag = "当前租住" if home.get("id") == current.get("id") else "已退租"
            rent = int(home.get("rent_cents") or 0) / 100.0
            lines.append("· [%s] %s%s（月租 %.0f 元，下次交租：每月 1 号）" % (
                tag, self._city_prefix(home), home.get("name") or "出租屋", rent))
        return "\n".join(lines)

    # ---------- 参考图 ----------
    def set_room_image(self, room, path):
        home = self.current()
        images = home.setdefault("images", {})
        room_key = str(room or "living")
        images[room_key] = str(path or "")
        needed = [item for item in (home.get("rooms_needed") or []) if item != room_key]
        home["rooms_needed"] = needed
        home["images_needed"] = bool(needed) or not all(images.get(key) for key in ROOM_KEYS)
        self._save()
        return home

    def can_redraw(self, now=None):
        """大件/搬家/换风格才重绘；两次生图至少间隔 24 小时。"""
        import time as _time
        now = float(now if now is not None else _amo_clock.now_ts())
        try:
            home = self.current()
            last = float(home.get("last_image_ts") or 0)
            fail_until = float(home.get("image_fail_until") or 0)
        except Exception:
            last = 0
            fail_until = 0
        if fail_until and now < fail_until:
            return False
        return last <= 0 or (now - last) >= IMAGE_COOLDOWN_SECONDS

    def mark_image_ready(self, now=None):
        import time as _time
        home = self.current()
        home["rooms_needed"] = []
        home["image_fail_count"] = 0
        home["image_fail_until"] = 0
        home["last_image_ts"] = float(now if now is not None else _amo_clock.now_ts())
        home["image_status"] = "ready"
        home["images_needed"] = False
        self._save()
        return home

    def mark_image_failed(self, now=None):
        home = self.current()
        count = int(home.get("image_fail_count") or 0) + 1
        home["image_fail_count"] = count
        delay = min(1800 * (2 ** min(count - 1, 4)), 6 * 3600)
        home["image_fail_until"] = float(now if now is not None else _amo_clock.now_ts()) + delay
        home["image_status"] = "pending"
        home["images_needed"] = True
        self._save()
        return home

    def room_prompt(self, room_type="客厅", now=None):
        home = self.current()
        key = room_key_of(room_type)
        owned = list((home.get("room_furniture") or {}).get(key) or [])
        owned = [item for item in owned
                 if item not in (DEFAULT_ROOM_FURNITURE.get(key) or ())
                 and not any(word in str(item) for word in FURNITURE_EXCLUDE)]
        if not owned:
            owned = list(DEFAULT_ROOM_FURNITURE.get(key) or [])
        else:
            owned_slots = {furniture_slot(item) for item in owned}
            owned.extend(item for item in (DEFAULT_ROOM_FURNITURE.get(key) or ())
                         if furniture_slot(item) not in owned_slots)
        items = [furniture_display(home, item) for item in owned]
        return build_room_image_prompt(room_type, home.get("style"), home.get("colors"), items, now)

    def room_scene_line(self, room="living"):
        """自拍时给模型的一句话：这个房间该有什么、不该有什么。"""
        home = self.current()
        key = room_key_of(room)
        label = {"bedroom": "卧室", "bathroom": "浴室", "kitchen": "厨房"}.get(key, "客厅")
        items = (home.get("room_furniture") or {}).get(key) or DEFAULT_ROOM_FURNITURE.get(key) or []
        if not items:
            return ""
        if key == "bedroom":
            bad = "沙发、电视柜、茶几、餐桌、马桶、厨房灶台"
        elif key == "bathroom":
            bad = "床、衣柜、梳妆台、沙发、电视柜、茶几、餐桌、厨房灶台"
        elif key == "kitchen":
            bad = "床、衣柜、梳妆台、沙发、电视柜、茶几、马桶、淋浴间"
        else:
            bad = "床、衣柜、梳妆台、床头柜、马桶、厨房灶台"
        return ("背景是她的%s，房间里只能出现这些家具：%s；不要出现%s；"
                "不要出现人物、宠物、文字、水印，家具要落地有真实阴影。"
                % (label, "、".join(items), bad))

    def room_image(self, room="living"):
        """房间参考图路径；配置里那条丢了就按规范文件名回退，都没有返回空串。"""
        key = room_key_of(room)
        images = self.current().get("images") or {}
        stored = str(images.get(key) or "")
        if stored:
            return stored
        fallback = os.path.join(self.image_dir(), "%s-%s.png" % (
            self.current().get("id") or "home", key))
        if os.path.isfile(fallback):
            return fallback
        return ""

    def room_profiles(self):
        """当前家的房间画像：每间房认哪些 zone、哪些地点词。

        老数据（改名前的 kv）里 home_bath 写在客厅下面；这里按默认画像归位：
        zone 由默认值说了算（浴室认 home_bath、厨房认 home_kitchen），地点词以配置为准。
        """
        base = default_rooms_config()
        stored = self.current().get("rooms")
        stored = {str(key): dict(value) for key, value in stored.items()
                  if isinstance(value, dict)} if isinstance(stored, dict) else {}
        merged = {}
        for room, profile in base.items():
            data = dict(profile)
            own = stored.get(room) or {}
            zones = own.get("zones")
            if zones:
                data["zones"] = tuple(str(zone) for zone in zones)
            places = own.get("places")
            if places:
                data["places"] = tuple(places)
            label = str(own.get("label") or "").strip()
            if label:
                data["label"] = label
            merged[room] = data
        for room, data in stored.items():
            if room in merged:
                continue
            merged[room] = {"label": str(data.get("label") or room),
                            "zones": tuple(str(z) for z in (data.get("zones") or ())),
                            "places": tuple(data.get("places") or ())}
        # 只给「新增的两间」归位：老 kv 里 home_bath 写在客厅下面，浴室该认它；
        # 其它 zone 仍然听 housing 配置（用户改过的映射不能被默认值覆盖）。
        claimed = {}
        for room in NEW_ROOMS:
            for zone in base.get(room, {}).get("zones") or ():
                claimed[str(zone)] = room
        for room, data in merged.items():
            zones = [str(zone) for zone in (data.get("zones") or ())
                     if claimed.get(str(zone), room) == room]
            if room in NEW_ROOMS:
                for zone in base.get(room, {}).get("zones") or ():
                    if zone not in zones:
                        zones.append(str(zone))
            data["zones"] = tuple(zones)
        return merged

    def room_label(self, room="living"):
        """房间的中文名（提示词里用）；不认识的房间按客厅。"""
        profile = self.room_profiles().get(str(room or "living")) or {}
        return str(profile.get("label") or ("卧室" if str(room or "") == "bedroom" else "客厅"))
