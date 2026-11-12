# -*- coding: utf-8 -*-
"""穿搭衣柜：每日一套、面板增删、礼物自动入库。

数据落在 ``plugin_data/Astrbot_amoupup_forever/wardrobe.json``：

- ``items``   每件衣服，分三类：``top``（上衣（外套））/ ``bottom``（裤子）/ ``shoes``（鞋子）；
- ``outfits`` 搭配模板（至少 30 套），每套记 top / bottom / shoes 三个 item id；
- ``daily``   每天锁定的那一套，同一天所有自拍/照片都用它，换场景也不换。

另外会把当天这套写到万象画卷的 ``plugin_data/astrbot_plugin_omnidraw/amo_outfit.json``，
聊天里直接调 ``generate_selfie`` 时也能锁住衣服（万象画卷那边会把这份当「今日着装」）。
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import uuid

from astrbot.api import logger

try:  # 删衣服时把它那张参考图也删掉
    from .outfit_refs import item_ref_path as _item_ref_path
except Exception:  # pragma: no cover - 直接按文件加载时
    try:
        from outfit_refs import item_ref_path as _item_ref_path
    except Exception:
        _item_ref_path = None

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

try:
    from .amo_shared import today_str
except Exception:  # pragma: no cover - 直接按文件加载时
    try:
        from amo_shared import today_str
    except Exception:
        def today_str():
            import datetime
            return datetime.date.today().isoformat()

try:  # 用插件统一时区判断「几点、该不该换睡衣」
    from .clock import now as _clock_now
except Exception:  # pragma: no cover
    try:
        from clock import now as _clock_now
    except Exception:
        from datetime import datetime as _dt

        def _clock_now(*args, **kwargs):
            return _dt.now()


CATEGORY_LABELS = (
    ("coat", "外套"),
    ("top", "上衣（衬衫）"),
    ("bottom", "裤子"),
    ("skirt", "裙子"),
    ("shoes", "鞋子"),
)
CATEGORY_KEYS = tuple(key for key, _ in CATEGORY_LABELS)
CATEGORY_NAMES = dict(CATEGORY_LABELS)

#: 至少 30 套种子模板。用户给的例子排第一套。
SEED_OUTFITS = (
    ("米色雪纺衬衫", "黑色包臀裙", "淡蓝色运动鞋"),
    ("白色棉质衬衫", "深蓝直筒牛仔裤", "白色帆布鞋"),
    ("浅蓝条纹衬衫", "米白阔腿裤", "米色乐福鞋"),
    ("黑色高领毛衣", "卡其色半身裙", "棕色短靴"),
    ("燕麦色针织衫", "浅色牛仔裤", "白色老爹鞋"),
    ("灰色宽松卫衣", "灰色运动裤", "灰色跑鞋"),
    ("奶油色开衫", "白色棉麻长裤", "浅粉玛丽珍"),
    ("粉色短袖T恤", "浅色牛仔短裙", "白色帆布鞋"),
    ("藏青色西装外套", "黑色西装裤", "黑色小皮鞋"),
    ("卡其色风衣", "深蓝直筒牛仔裤", "棕色短靴"),
    ("牛仔外套", "黑色西装裤", "黑色马丁靴"),
    ("白色荷叶边上衣", "藏青百褶裙", "裸色高跟鞋"),
    ("墨绿色针织马甲", "米白阔腿裤", "米色乐福鞋"),
    ("浅紫雪纺衫", "黑色皮裙", "黑色小皮鞋"),
    ("白色棉质衬衫", "棕色灯芯绒裤", "棕色短靴"),
    ("浅蓝条纹衬衫", "藏青百褶裙", "淡蓝色运动鞋"),
    ("黑色高领毛衣", "深蓝直筒牛仔裤", "黑色马丁靴"),
    ("燕麦色针织衫", "卡其色半身裙", "棕色短靴"),
    ("奶油色开衫", "浅灰针织长裙", "浅粉玛丽珍"),
    ("灰色宽松卫衣", "浅色牛仔短裙", "白色老爹鞋"),
    ("藏青色西装外套", "米白阔腿裤", "黑色小皮鞋"),
    ("卡其色风衣", "黑色西装裤", "黑色马丁靴"),
    ("粉色短袖T恤", "白色棉麻长裤", "淡蓝色运动鞋"),
    ("牛仔外套", "黑色包臀裙", "黑色小皮鞋"),
    ("白色荷叶边上衣", "浅色牛仔裤", "裸色高跟鞋"),
    ("墨绿色针织马甲", "深蓝直筒牛仔裤", "白色帆布鞋"),
    ("浅紫雪纺衫", "米白阔腿裤", "浅粉玛丽珍"),
    ("米色雪纺衬衫", "黑色西装裤", "米色乐福鞋"),
    ("黑色高领毛衣", "浅灰针织长裙", "黑色马丁靴"),
    ("藏青色西装外套", "藏青百褶裙", "黑色小皮鞋"),
)

#: 热天别选厚重上衣；冷天优先这些
WARM_WORDS = ("外套", "大衣", "羽绒服", "毛衣", "针织", "卫衣", "风衣", "西装", "马甲", "灯芯绒")
COOL_BOTTOM_WORDS = ("灯芯绒", "针织长裙")

#: 下班回家/清早在家穿的睡衣（面板里不显示，也不参与白天随机），一共 20 套
SEED_COATS = (
    "黑色长款羽绒服",
    "米白色短款羽绒服",
    "卡其色厚呢大衣",
    "深灰色毛呢外套",
    "米色针织格子外套",
    "棕色羊羔毛外套",
)
SEED_SLEEP = (
    ("浅粉色棉质睡衣上衣", "同色系棉质睡裤", "毛绒拖鞋"),
    ("奶油色法兰绒睡衣上衣", "奶油色法兰绒睡裤", "棉拖鞋"),
    ("浅蓝色真丝睡衣上衣", "浅蓝色真丝睡裤", "软底室内拖鞋"),
    ("白色棉质睡衣上衣", "白色棉质睡裤", "毛绒拖鞋"),
    ("浅灰色珊瑚绒睡衣上衣", "浅灰色珊瑚绒睡裤", "棉拖鞋"),
    ("淡紫色棉质睡衣上衣", "淡紫色棉质睡裤", "软底室内拖鞋"),
    ("米黄色针织睡衣上衣", "米黄色针织睡裤", "毛绒拖鞋"),
    ("浅绿色棉麻睡衣上衣", "浅绿色棉麻睡裤", "棉拖鞋"),
    ("粉色草莓印花睡衣上衣", "粉色草莓印花睡裤", "毛绒拖鞋"),
    ("藏青色条纹睡衣上衣", "藏青色条纹睡裤", "软底室内拖鞋"),
    ("白色蕾丝边睡衣上衣", "白色蕾丝边睡裤", "毛绒拖鞋"),
    ("浅橙色珊瑚绒睡衣上衣", "浅橙色珊瑚绒睡裤", "棉拖鞋"),
    ("灰色格纹法兰绒睡衣上衣", "灰色格纹法兰绒睡裤", "软底室内拖鞋"),
    ("淡粉色丝绸睡衣上衣", "淡粉色丝绸睡裤", "毛绒拖鞋"),
    ("浅黄色棉质睡衣上衣", "浅黄色棉质睡裤", "棉拖鞋"),
    ("浅蓝色条纹睡衣上衣", "浅蓝色条纹睡裤", "毛绒拖鞋"),
    ("奶白色针织睡衣上衣", "奶白色针织睡裤", "软底室内拖鞋"),
    ("浅紫色珊瑚绒睡衣上衣", "浅紫色珊瑚绒睡裤", "棉拖鞋"),
    ("白色小碎花睡衣上衣", "白色小碎花睡裤", "毛绒拖鞋"),
    ("黑色丝质睡衣上衣", "黑色丝质睡裤", "软底室内拖鞋"),
    ("加厚珊瑚绒睡衣上衣", "加厚珊瑚绒睡裤", "厚底棉拖鞋"),
    ("夹棉加厚睡衣上衣", "夹棉加厚睡裤", "兔子毛绒拖鞋"),
    ("厚法兰绒睡衣上衣", "厚法兰绒睡裤", "棉拖鞋"),
    ("羊羔绒睡衣上衣", "羊羔绒睡裤", "厚底棉拖鞋"),
)
#: 在家/在外判定：家里这些词都算在家（阳台、沙发、床上、厨房…以前会被误判成「在外面」）
HOME_PLACE_WORDS = (
    "家", "客厅", "卧室", "宿舍", "房间", "阳台", "沙发", "床", "厨房", "浴室",
    "卫生间", "洗手间", "餐桌", "书房", "玄关", "被窝", "屋里", "室内", "地毯",
)
OUTSIDE_PLACE_WORDS = (
    "公司", "写字楼", "办公室", "工位", "商场", "街", "地铁", "公交", "车站", "餐厅",
    "咖啡", "学校", "教室", "公园", "医院", "超市", "店", "路", "广场", "图书馆",
    "健身房", "景区", "酒店", "机场", "火车", "车", "摊",
)
#: 晚上 8 点以后在家换睡衣（早上 8 点前在家也还算睡衣）
SLEEP_HOUR_FROM = 20
SLEEP_HOUR_TO = 8
#: 白天搭配目标套数：30 套手工配的之外，再用上衣×裤子×鞋自动配到这么多套
DAY_TARGET_OUTFITS = 120
DAY_SEED_VERSION = 4
#: 睡衣模板目标套数：20 套手工配的之外，再用上衣×睡裤×拖鞋自动配到这么多套，每晚换着穿
SLEEP_TARGET_OUTFITS = 120
#: 睡衣单品里额外补的拖鞋（不在 20 套手工搭配里，只参与自动组合）
SLEEP_EXTRA_ITEMS = (
    ("shoes", "兔子毛绒拖鞋"),
    ("shoes", "厚底棉拖鞋"),
)
#: 睡衣种子版本：升到新版本才会补一次，之后用户删掉的睡衣不会自己长回来
SLEEP_SEED_VERSION = 4
#: 酒店睡衣：只有出门旅游、晚上住酒店才穿；和家里睡衣完全分开
HOTEL_SEED = (
    ("白色酒店浴袍", "白色棉质睡裤", "一次性棉拖鞋"),
    ("浅灰色酒店睡袍", "浅灰色睡裤", "一次性棉拖鞋"),
    ("藏青色酒店睡衣上衣", "藏青色睡裤", "酒店防滑拖鞋"),
    ("米色酒店浴袍", "米色睡裤", "一次性棉拖鞋"),
    ("白色酒店睡衣上衣", "白色睡裤", "酒店防滑拖鞋"),
    ("浅蓝色酒店睡袍", "浅蓝色睡裤", "一次性棉拖鞋"),
    ("深灰色酒店睡袍", "深灰色睡裤", "酒店防滑拖鞋"),
    ("淡粉色酒店睡衣上衣", "淡粉色睡裤", "一次性棉拖鞋"),
)
HOTEL_SEED_VERSION = 2
DRESS_SEED_VERSION = 1
COAT_SEED_VERSION = 1
HOTEL_PLACE_WORDS = ("酒店", "宾馆", "民宿", "旅馆", "客栈", "度假村", "客房", "套房")

#: 泳装：只有去游泳馆 / 海边 / 水上乐园才穿；**整套穿，上衣和泳裤必须同一套**，不穿鞋。
#: (整套名, 上衣, 泳裤)
SEED_SWIM_SETS = (
    ("黑色比基尼", "黑色比基尼泳衣上衣", "黑色比基尼泳裤"),
    ("白色荷叶边泳衣", "白色荷叶边泳衣上衣", "白色荷叶边泳裤"),
    ("蓝色条纹泳衣", "蓝色条纹泳衣上衣", "蓝色条纹泳裤"),
    ("粉色蝴蝶结泳衣", "粉色蝴蝶结泳衣上衣", "粉色蝴蝶结泳裤"),
    ("绿色碎花泳衣", "绿色碎花泳衣上衣", "绿色碎花泳裤"),
    ("紫色运动泳衣", "紫色运动泳衣上衣", "紫色运动泳裤"),
    ("黄色波点泳衣", "黄色波点泳衣上衣", "黄色波点泳裤"),
)
SWIM_SEED_VERSION = 2
#: 日程地点里出现这些词就是「在水边/玩水」，自动换泳装
SWIM_PLACE_WORDS = ("游泳", "泳池", "泳馆", "海边", "沙滩", "水上乐园", "水世界",
                    "海里", "玩水", "泳装")

SHOE_WORDS = ("鞋", "靴", "拖鞋", "凉拖", "玛丽珍")
BOTTOM_WORDS = ("裤", "裙")
TOP_WORDS = (
    "衬衫", "上衣", "T恤", "t恤", "卫衣", "毛衣", "针织衫", "开衫", "外套", "大衣",
    "风衣", "羽绒服", "西装", "夹克", "马甲", "背心", "吊带", "雪纺", "Polo", "polo",
    "打底衫", "罩衫",
)
COAT_WORDS = ("羽绒服", "棉服", "大衣", "风衣", "夹克", "羊羔毛", "毛呢", "外套")
#: 夏天不穿（厚实/保暖）
SUMMER_BAD_WORDS = ("羽绒服", "棉服", "大衣", "毛呢", "羊羔毛", "加厚", "夹棉",
                    "毛衣", "卫衣", "针织", "开衫", "西装", "外套", "风衣", "夹克",
                    "法兰绒", "珊瑚绒", "加绒", "羊毛", "马海毛")
#: 冬天不穿（清凉单品）
SUMMER_DRESS_WORDS = ("短袖", "T恤", "t恤", "短裤", "短裙", "凉鞋", "吊带", "背心")
#: 上班/通勤场景不穿泳衣、吊带、热裤这类
WORK_SCENE_WORDS = ("公司", "写字楼", "办公室", "工位", "上班", "开会", "通勤",
                    "地铁", "客户", "学校", "教室")
SWIM_WORDS = ("泳衣", "泳装", "比基尼", "吊带", "热裤", "超短", "沙滩", "露肩")
#: 礼物里出现这些词就归到睡衣，不进白天衣柜
SLEEP_SHOE_WORDS = ("拖鞋", "棉拖", "室内拖")
SLEEP_BOTTOM_WORDS = ("睡裤",)
SLEEP_TOP_WORDS = ("睡衣", "睡裙", "睡袍", "家居服")
QUANTIFIER_RE = re.compile(r"^(一条|一件|一双|一顶|一个|一套|只|条|件|双)\s*")

MAX_OUTFITS = 400

#: 颜色系（自动组合只允许同色系/同材质，避免黑上衣配藏青裤这种混搭）
COLOR_FAMILIES = (
    ("白", ("白色", "米白", "奶白", "奶油色", "米色", "燕麦色", "杏色", "裸色", "香槟色", "银白")),
    ("黑", ("黑色",)),
    ("灰", ("灰色", "浅灰", "深灰", "银灰")),
    ("蓝", ("蓝色", "浅蓝", "淡蓝", "深蓝", "藏青", "牛仔")),
    ("粉", ("粉色", "浅粉", "淡粉", "藕粉")),
    ("紫", ("紫色", "浅紫", "淡紫")),
    ("绿", ("绿色", "墨绿", "浅绿")),
    ("棕", ("棕色", "咖啡", "驼色", "焦糖", "卡其")),
    ("黄", ("黄色", "浅黄", "鹅黄")),
    ("红", ("红色", "酒红", "砖红")),
)
MATERIAL_WORDS = (
    "棉质", "棉麻", "纯棉", "雪纺", "针织", "法兰绒", "珊瑚绒", "真丝", "丝绸",
    "丝质", "牛仔", "牛皮", "皮", "灯芯绒", "蕾丝", "西装", "运动", "毛呢", "羊毛",
)
NEUTRAL_SHOE_WORDS = ("白色", "黑色", "米色", "裸色", "香槟", "银色", "金色")
#: 这些名字是连衣裙（一件就够，不用配上衣）
DRESS_WORDS = ("连衣裙", "长裙", "连身裙")


def is_dress(name):
    return any(word in str(name or "") for word in DRESS_WORDS)


def is_swim_item(item):
    return bool((item or {}).get("swim"))


def is_swim_outfit(outfit):
    return bool((outfit or {}).get("swim"))


def is_day_item(item):
    """白天衣柜只收普通衣服：睡衣和泳装都不参与白天随机。"""
    item = item or {}
    return not item.get("sleep") and not item.get("swim")


def is_day_outfit(outfit):
    outfit = outfit or {}
    return not outfit.get("sleep") and not outfit.get("swim")


def color_family(name):
    """取名字里的颜色系（白/黑/灰/蓝…），取不到返回空串。"""
    text = str(name or "")
    best = ""
    for _, words in COLOR_FAMILIES:
        for word in words:
            if word in text and len(word) > len(best):
                best = word
    return best


def material_set(name):
    text = str(name or "")
    return {word for word in MATERIAL_WORDS if word in text}


def fabric_match(first, second):
    """同色系或同材质才算搭。"""
    first_color = color_family(first)
    second_color = color_family(second)
    if first_color and first_color == second_color:
        return True
    return bool(material_set(first) & material_set(second))


def shoe_matches(shoe, top, bottom):
    """鞋子同色/同材质，或者本身就是白黑米裸这种百搭色、拖鞋。"""
    text = str(shoe or "")
    if "拖鞋" in text or any(word in text for word in NEUTRAL_SHOE_WORDS):
        return True
    return fabric_match(text, top) or fabric_match(text, bottom)


def _new_id(prefix=""):
    return prefix + uuid.uuid4().hex[:12]


def classify_sleep_item(name):
    """礼物是不是睡衣/睡裤/拖鞋/睡裙；是的话返回对应分类，否则空串。"""
    text = str(name or "").strip()
    if not text or not any(word in text for word in SLEEP_SHOE_WORDS + SLEEP_BOTTOM_WORDS + SLEEP_TOP_WORDS):
        return ""
    if any(word in text for word in SLEEP_SHOE_WORDS):
        return "shoes"
    if "睡裙" in text:
        return "skirt"
    if any(word in text for word in SLEEP_BOTTOM_WORDS):
        return "bottom"
    if any(word in text for word in SLEEP_TOP_WORDS):
        return "top"
    return ""


def is_pajama_change_request(text):
    """他嫌睡衣不好看/让换一套时返回 True（只认和睡衣一起出现的说法）。"""
    text = str(text or "")
    if not any(word in text for word in ("睡衣", "睡裙", "睡裤", "睡袍", "居家服")):
        return False
    return any(word in text for word in ("不好看", "难看", "换", "不喜欢", "丑"))


OUTFIT_CHANGE_WORDS = ("换一套", "换套", "换装", "换衣服", "换件", "换一下",
                       "不好看", "难看", "不喜欢", "丑")
OUTFIT_CONTEXT_WORDS = ("衣服", "穿搭", "睡衣", "睡裙", "睡裤", "睡袍", "外套",
                        "裙子", "裤子", "上衣", "这件", "这套", "这身")


def is_outfit_change_request(text):
    """他是不是在说换一套/换装（不认换手机、换话题这种）。"""
    text = str(text or "")
    if not text:
        return False
    if "换装" in text:
        return True
    if not any(word in text for word in OUTFIT_CONTEXT_WORDS):
        return False
    return any(word in text for word in OUTFIT_CHANGE_WORDS)


def classify_item(name):
    """按名字判断是哪一类衣服；不是这些就返回空串。"""
    text = str(name or "").strip()
    if not text:
        return ""
    if any(word in text for word in SHOE_WORDS):
        return "shoes"
    if any(word in text for word in COAT_WORDS):
        return "coat"
    if "裙" in text:
        return "skirt"
    if any(word in text for word in BOTTOM_WORDS):
        return "bottom"
    if any(word in text for word in TOP_WORDS):
        return "top"
    if text in ("衣服", "外套", "上衣"):
        return "top"
    return ""


def clean_name(name):
    text = QUANTIFIER_RE.sub("", str(name or "").strip())
    return text[:40].strip()


def combo_compatible(coat, top, bottom, skirt, shoes):
    """自动组合是否协调：同色系/同材质才配，外套最多一件、衬衫在里。

    - 长裙：一件就够，外套可选；
    - 短裙：上衣和短裙同色系/同材质，裤子可选；
    - 外套：只跟里层同色系/同材质，或本身是黑/白/米/卡其/灰/棕这类百搭色。
    """
    coat = str(coat or "").strip()
    top = str(top or "").strip()
    bottom = str(bottom or "").strip()
    skirt = str(skirt or "").strip()
    shoes = str(shoes or "").strip()
    if skirt:
        if is_dress(skirt):
            if not shoe_matches(shoes, skirt, skirt):
                return False
        else:
            if not top or not fabric_match(top, skirt):
                return False
            if bottom and not fabric_match(bottom, top) and not fabric_match(bottom, skirt):
                return False
            if not shoe_matches(shoes, top, skirt):
                return False
    else:
        if not top or not bottom:
            return False
        if not fabric_match(top, bottom) or not shoe_matches(shoes, top, bottom):
            return False
    if coat:
        neutral = any(word in coat for word in ("黑", "白", "米", "卡其", "灰", "棕", "驼", "裸"))
        if not neutral:
            layers = [name for name in (top, bottom, skirt) if name]
            if not any(fabric_match(coat, name) for name in layers):
                return False
    return True


class Wardrobe:
    VERSION = 1

    def __init__(self, plugin):
        self.plugin = plugin
        self._lock = threading.RLock()
        self._data = None
        self._today_cache = None
        self._synced = ""
        base = ""
        try:
            base = os.path.dirname(str(getattr(plugin.db, "path", "") or ""))
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe.__init__", _amo_exc)
            base = ""
        if not base:
            base = str(getattr(plugin, "plugin_dir", "") or ".")
        self.path = os.path.join(base, "wardrobe.json")
        self.sidecar_path = os.path.join(
            os.path.dirname(base), "astrbot_plugin_omnidraw", "amo_outfit.json"
        )
        logger.info("[amo] 穿搭衣柜已加载（%s）" % self.path)

    # ---------- 存储 ----------
    def _empty(self):
        return {"version": self.VERSION, "items": [], "outfits": [], "daily": {}}

    def _load(self):
        with self._lock:
            if self._data is not None:
                return self._data
            data = None
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except FileNotFoundError:
                data = None
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe._load", _amo_exc)
                data = None
            if not isinstance(data, dict) or not data.get("items") or not data.get("outfits"):
                data = self._seed()
                self._data = data
                self._save(data)
                return data
            data.setdefault("version", self.VERSION)
            data.setdefault("daily", {})
            data.setdefault("outfits", [])
            data.setdefault("items", [])
            if data.get("day_seed") != DAY_SEED_VERSION:
                self._ensure_day(data)
                data["day_seed"] = DAY_SEED_VERSION
                self._save(data)
            if data.get("sleep_seed") != SLEEP_SEED_VERSION:
                self._ensure_sleep(data)
                data["sleep_seed"] = SLEEP_SEED_VERSION
                self._save(data)
            if data.get("hotel_seed") != HOTEL_SEED_VERSION:
                self._ensure_hotel(data)
                data["hotel_seed"] = HOTEL_SEED_VERSION
                self._save(data)
            if data.get("swim_seed") != SWIM_SEED_VERSION:
                self._ensure_swim(data)
                data["swim_seed"] = SWIM_SEED_VERSION
                self._save(data)
            if data.get("dress_seed") != DRESS_SEED_VERSION:
                self._migrate_dresses(data)
                data["dress_seed"] = DRESS_SEED_VERSION
                self._save(data)
            if data.get("coat_seed") != COAT_SEED_VERSION:
                self._ensure_coats(data)
                data["coat_seed"] = COAT_SEED_VERSION
                self._save(data)
            self._data = data
            return data

    def _save(self, data=None):
        data = data if data is not None else self._data
        if data is None:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._save", _amo_exc)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def _seed(self):
        data = self._empty()
        items = {}
        for top, bottom, shoes in SEED_OUTFITS:
            ids = {}
            for category, name in (("top", top), ("bottom", bottom), ("shoes", shoes)):
                key = (category, name)
                if key not in items:
                    items[key] = {
                        "id": _new_id("i-"),
                        "category": category,
                        "name": name,
                        "source": "seed",
                        "gift_id": "",
                        "created_at": time.time(),
                    }
                ids[category] = items[key]["id"]
            data["outfits"].append({
                "id": _new_id("o-"),
                "top": ids["top"],
                "bottom": ids["bottom"],
                "shoes": ids["shoes"],
                "source": "seed",
            })
        data["items"] = list(items.values())
        self._ensure_coats(data)
        data["coat_seed"] = COAT_SEED_VERSION
        self._ensure_day(data)
        data["day_seed"] = DAY_SEED_VERSION
        self._ensure_sleep(data)
        data["sleep_seed"] = SLEEP_SEED_VERSION
        self._ensure_hotel(data)
        data["hotel_seed"] = HOTEL_SEED_VERSION
        self._ensure_swim(data)
        data["swim_seed"] = SWIM_SEED_VERSION
        self._migrate_dresses(data)
        data["dress_seed"] = DRESS_SEED_VERSION
        return data

    def _ensure_day(self, data):
        """白天搭配：手工 + 同色系/同材质的自动组合（外套最多一件，长裙单穿）。"""
        items = data.setdefault("items", [])
        outfits = data.setdefault("outfits", [])
        by_id = {item["id"]: item for item in items}
        by_key = {(item.get("category"), item.get("name")): item
                  for item in items if is_day_item(item)}
        seed_names = {frozenset(name for name in (top, bottom, shoes) if name)
                      for top, bottom, shoes in SEED_OUTFITS}
        keys = ("coat", "top", "bottom", "skirt", "shoes")
        kept = []
        changed = False
        for outfit in outfits:
            if outfit.get("sleep") or outfit.get("swim"):
                kept.append(outfit)
                continue
            names = frozenset(str((by_id.get(outfit.get(key)) or {}).get("name") or "")
                              for key in keys) - {""}
            if outfit.get("source") == "seed" and names not in seed_names:
                parts = [str((by_id.get(outfit.get(key)) or {}).get("name") or "")
                         for key in keys]
                if not combo_compatible(*parts):
                    changed = True
                    continue
            kept.append(outfit)
        if changed:
            outfits[:] = kept
        existing = {tuple(outfit.get(key) or "" for key in keys)
                    for outfit in outfits if is_day_outfit(outfit)}
        day_count = len([outfit for outfit in outfits if is_day_outfit(outfit)])
        if day_count >= DAY_TARGET_OUTFITS:
            return changed
        coats = [item for item in items if is_day_item(item) and item.get("category") == "coat"]
        tops = [item for item in items if is_day_item(item) and item.get("category") == "top"]
        bottoms = [item for item in items if is_day_item(item) and item.get("category") == "bottom"]
        skirts = [item for item in items if is_day_item(item) and item.get("category") == "skirt"]
        shoes_pool = [item for item in items if is_day_item(item)
                      and item.get("category") == "shoes"]
        dress_skirts = [item for item in skirts if is_dress(item.get("name"))]
        short_skirts = [item for item in skirts if not is_dress(item.get("name"))]
        added = changed
        rng = random.Random("amo-wardrobe-day-extra")
        tops = list(tops)
        bottoms = list(bottoms)
        short_skirts = list(short_skirts)
        dress_skirts = list(dress_skirts)
        shoes_pool = list(shoes_pool)
        coats = list(coats)
        for pool in (tops, bottoms, short_skirts, dress_skirts, shoes_pool, coats):
            rng.shuffle(pool)

        def add_combo(coat, top, bottom, skirt, shoe):
            nonlocal day_count, added
            if day_count >= DAY_TARGET_OUTFITS:
                return
            key = (coat["id"] if coat else "", top["id"] if top else "",
                   bottom["id"] if bottom else "", skirt["id"] if skirt else "",
                   shoe["id"])
            if key in existing:
                return
            names = [str(part.get("name") or "") if part else ""
                     for part in (coat, top, bottom, skirt, shoe)]
            if not combo_compatible(*names):
                return
            existing.add(key)
            outfits.append({
                "id": _new_id("o-"),
                "coat": key[0],
                "top": key[1],
                "bottom": key[2],
                "skirt": key[3],
                "shoes": key[4],
                "source": "seed",
                "combo": True,
            })
            day_count += 1
            added = True

        # 先保证每件上衣至少进一套，避免随机只铺满几种上衣
        for top in tops:
            before = day_count
            for bottom in bottoms:
                for shoe in shoes_pool:
                    add_combo(None, top, bottom, "", shoe)
                    if day_count > before:
                        break
                if day_count > before:
                    break
        for bottom in bottoms:
            for top in tops:
                for shoe in shoes_pool:
                    if day_count >= DAY_TARGET_OUTFITS:
                        break
                    add_combo(None, top, bottom, "", shoe)
                if day_count >= DAY_TARGET_OUTFITS:
                    break
            if day_count >= DAY_TARGET_OUTFITS:
                break
        for top in tops:
            for skirt in short_skirts:
                for shoe in shoes_pool:
                    if day_count >= DAY_TARGET_OUTFITS:
                        break
                    add_combo(None, top, "", skirt, shoe)
                if day_count >= DAY_TARGET_OUTFITS:
                    break
            if day_count >= DAY_TARGET_OUTFITS:
                break
        for top in tops:
            for skirt in short_skirts:
                for bottom in bottoms:
                    for shoe in shoes_pool:
                        if day_count >= DAY_TARGET_OUTFITS:
                            break
                        add_combo(None, top, bottom, skirt, shoe)
                    if day_count >= DAY_TARGET_OUTFITS:
                        break
                if day_count >= DAY_TARGET_OUTFITS:
                    break
            if day_count >= DAY_TARGET_OUTFITS:
                break
        for dress in dress_skirts:
            for shoe in shoes_pool:
                if day_count >= DAY_TARGET_OUTFITS:
                    break
                add_combo(None, None, None, dress, shoe)
        for coat in coats:
            for bottom in bottoms:
                for top in tops:
                    for shoe in shoes_pool:
                        if day_count >= DAY_TARGET_OUTFITS:
                            break
                        add_combo(coat, top, bottom, "", shoe)
                    if day_count >= DAY_TARGET_OUTFITS:
                        break
                if day_count >= DAY_TARGET_OUTFITS:
                    break
            if day_count >= DAY_TARGET_OUTFITS:
                break
        return added

    def _ensure_sleep(self, data):
        """补上睡衣：20 套手工搭配 + 自动组合到 SLEEP_TARGET_OUTFITS 套，每晚换着穿。"""
        items = data.setdefault("items", [])
        outfits = data.setdefault("outfits", [])
        by_key = {(item.get("category"), item.get("name")): item
                  for item in items if not item.get("hotel")}
        by_id = {item["id"]: item for item in items}
        seed_triples = set()
        for top, bottom, shoes in SEED_SLEEP:
            triple_items = [by_key.get(("top", top)), by_key.get(("bottom", bottom)),
                            by_key.get(("shoes", shoes))]
            if all(triple_items):
                seed_triples.add(tuple(entry["id"] for entry in triple_items))
        kept = []
        repaired = False
        for outfit in outfits:
            if not outfit.get("sleep") or outfit.get("hotel"):
                kept.append(outfit)
                continue
            triple = (outfit.get("top"), outfit.get("bottom"), outfit.get("shoes"))
            if outfit.get("source") == "seed" and triple not in seed_triples:
                names = [str((by_id.get(outfit.get(key)) or {}).get("name") or "")
                         for key in ("coat", "top", "bottom", "skirt", "shoes")]
                if not combo_compatible(*names):
                    repaired = True
                    continue
            kept.append(outfit)
        if repaired:
            outfits[:] = kept
        existing = {
            (outfit.get("top"), outfit.get("bottom"), outfit.get("shoes"))
            for outfit in outfits if outfit.get("sleep") and not outfit.get("hotel")
        }
        added = repaired

        def ensure_item(category, name):
            nonlocal added
            key = (category, name)
            item = by_key.get(key)
            if item is None:
                item = {
                    "id": _new_id("i-"),
                    "category": category,
                    "name": name,
                    "source": "seed",
                    "gift_id": "",
                    "sleep": True,
                    "created_at": time.time(),
                }
                items.append(item)
                by_key[key] = item
                added = True
            return item

        for top, bottom, shoes in SEED_SLEEP:
            top_item = ensure_item("top", top)
            bottom_item = ensure_item("bottom", bottom)
            shoes_item = ensure_item("shoes", shoes)
            triple = (top_item["id"], bottom_item["id"], shoes_item["id"])
            if triple in existing:
                continue
            existing.add(triple)
            outfits.append({
                "id": _new_id("o-"),
                "top": triple[0],
                "bottom": triple[1],
                "shoes": triple[2],
                "source": "seed",
                "sleep": True,
            })
            added = True

        for category, name in SLEEP_EXTRA_ITEMS:
            ensure_item(category, name)

        added = self._refill_sleep_combos(data) or added
        return added

    def _refill_sleep_combos(self, data):
        """只用现有睡衣单品补组合（不重新种子，删掉的衣服不会被复活）。"""
        items = data.setdefault("items", [])
        outfits = data.setdefault("outfits", [])
        keys = ("coat", "top", "bottom", "skirt", "shoes")
        existing = {tuple(outfit.get(key) or "" for key in keys)
                    for outfit in outfits if outfit.get("sleep") and not outfit.get("hotel")}
        sleep_count = len([outfit for outfit in outfits
                           if outfit.get("sleep") and not outfit.get("hotel")])
        if sleep_count >= SLEEP_TARGET_OUTFITS:
            return False
        tops = [item for item in items if item.get("sleep") and not item.get("hotel")
                and item.get("category") == "top"]
        bottoms = [item for item in items if item.get("sleep") and not item.get("hotel")
                   and item.get("category") == "bottom"]
        skirts = [item for item in items if item.get("sleep") and not item.get("hotel")
                  and item.get("category") == "skirt"]
        shoes_pool = [item for item in items if item.get("sleep") and not item.get("hotel")
                      and item.get("category") == "shoes"]
        dress_skirts = [item for item in skirts if is_dress(item.get("name"))]
        combos = []
        for top in tops:
            for bottom in bottoms:
                for shoe in shoes_pool:
                    combos.append(("", top, bottom, "", shoe))
        for dress in dress_skirts:
            for shoe in shoes_pool:
                combos.append(("", "", "", dress, shoe))

        def combo_names(entry):
            return tuple(str(part.get("name") or "") if part else "" for part in entry)

        combos = [entry for entry in combos if combo_compatible(*combo_names(entry))]
        random.Random("amo-wardrobe-sleep-extra").shuffle(combos)
        added = False
        for coat, top, bottom, skirt, shoe in combos:
            if sleep_count >= SLEEP_TARGET_OUTFITS:
                break
            key = (coat, top["id"] if top else "", bottom["id"] if bottom else "",
                   skirt["id"] if skirt else "", shoe["id"])
            if key in existing:
                continue
            existing.add(key)
            outfits.append({
                "id": _new_id("o-"),
                "coat": key[0],
                "top": key[1],
                "bottom": key[2],
                "skirt": key[3],
                "shoes": key[4],
                "source": "seed",
                "sleep": True,
                "combo": True,
            })
            sleep_count += 1
            added = True
        return added

    def _ensure_coats(self, data):
        """补几件冬天外套（羽绒服 / 厚呢 / 针织格子 / 羊羔毛）。"""
        items = data.setdefault("items", [])
        existing = {item.get("name") for item in items if item.get("category") == "coat"}
        added = False
        for name in SEED_COATS:
            if name in existing:
                continue
            items.append({
                "id": _new_id("i-"),
                "category": "coat",
                "name": name,
                "source": "seed",
                "gift_id": "",
                "created_at": time.time(),
            })
            added = True
        return added

    def _migrate_dresses(self, data):
        """把裙子挪到「裙子」栏：长裙单穿，短裙配上衣（可再加裤子/睡裤）。"""
        items = data.setdefault("items", [])
        by_id = {item["id"]: item for item in items}
        for item in items:
            if "裙" in str(item.get("name") or "") and item.get("category") in ("top", "bottom"):
                item["category"] = "skirt"
        kept = []
        for outfit in data.setdefault("outfits", []):
            if outfit.get("swim"):
                # 泳装本来就没有鞋，别让下面的「必须有鞋」把它筛掉
                kept.append(outfit)
                continue
            top = by_id.get(outfit.get("top")) or {}
            bottom = by_id.get(outfit.get("bottom")) or {}
            skirt = by_id.get(outfit.get("skirt")) or {}
            if top.get("category") == "skirt":
                outfit["skirt"] = outfit.get("top")
                outfit["top"] = ""
                skirt = top
            if bottom.get("category") == "skirt":
                outfit["skirt"] = outfit.get("bottom")
                outfit["bottom"] = ""
                skirt = bottom
            if is_dress(skirt.get("name")):
                outfit["top"] = ""
                outfit["bottom"] = ""
            kept.append(outfit)
        data["outfits"] = [outfit for outfit in kept
                           if (outfit.get("swim") or outfit.get("shoes"))
                           and (outfit.get("top") or outfit.get("bottom") or outfit.get("skirt"))]

    def _ensure_hotel(self, data):
        """补上酒店睡衣：只有出门旅游、晚上住酒店才会穿，和家里睡衣分开。"""
        items = data.setdefault("items", [])
        outfits = data.setdefault("outfits", [])
        by_key = {(item.get("category"), item.get("name")): item
                  for item in items if item.get("hotel")}
        by_id = {item.get("id"): item for item in items}
        kept = []
        for outfit in outfits:
            if outfit.get("hotel") and any(
                    not by_id.get(outfit.get(key), {}).get("hotel")
                    for key in ("top", "bottom", "shoes")):
                continue
            kept.append(outfit)
        if len(kept) != len(outfits):
            outfits[:] = kept
            added_repaired = True
        else:
            added_repaired = False
        existing = {
            (outfit.get("top"), outfit.get("bottom"), outfit.get("shoes"))
            for outfit in outfits if outfit.get("hotel")
        }
        added = added_repaired
        for top, bottom, shoes in HOTEL_SEED:
            ids = {}
            for category, name in (("top", top), ("bottom", bottom), ("shoes", shoes)):
                key = (category, name)
                item = by_key.get(key)
                if item is None:
                    item = {
                        "id": _new_id("i-"),
                        "category": category,
                        "name": name,
                        "source": "seed",
                        "gift_id": "",
                        "sleep": True,
                        "hotel": True,
                        "created_at": time.time(),
                    }
                    items.append(item)
                    by_key[key] = item
                    added = True
                ids[category] = item["id"]
            triple = (ids["top"], ids["bottom"], ids["shoes"])
            if triple in existing:
                continue
            existing.add(triple)
            outfits.append({
                "id": _new_id("o-"),
                "top": triple[0],
                "bottom": triple[1],
                "shoes": triple[2],
                "source": "seed",
                "sleep": True,
                "hotel": True,
            })
            added = True
        return added

    def _ensure_swim(self, data):
        """重建泳装：**整套**（上衣 + 同套泳裤），没有鞋；旧泳装连同参考图一起作废。"""
        items = data.setdefault("items", [])
        outfits = data.setdefault("outfits", [])
        old_items = [item for item in items if item.get("swim")]
        if old_items:
            self._purge_swim_refs(old_items)
            items[:] = [item for item in items if not item.get("swim")]
            outfits[:] = [outfit for outfit in outfits if not outfit.get("swim")]
        added = bool(old_items)
        for set_name, top, bottom in SEED_SWIM_SETS:
            ids = {}
            for category, name in (("top", top), ("bottom", bottom)):
                item = {
                    "id": _new_id("i-"),
                    "category": category,
                    "name": name,
                    "source": "seed",
                    "gift_id": "",
                    "swim": True,
                    "set": set_name,
                    "created_at": time.time(),
                }
                items.append(item)
                ids[category] = item["id"]
            outfits.append({
                "id": _new_id("o-"),
                "top": ids["top"],
                "bottom": ids["bottom"],
                "shoes": "",
                "source": "seed",
                "swim": True,
                "set": set_name,
            })
            added = True
        return added

    def _purge_swim_refs(self, items):
        """删掉旧泳装的参考图（衣服按名字一张，整套换新后不再沿用）。"""
        if _item_ref_path is None:
            return
        for item in items:
            try:
                path = _item_ref_path(self.plugin, item)
                if path and os.path.isfile(path):
                    os.remove(path)
                    logger.info("[amo] 旧泳装参考图已删除：%s" % path)
            except Exception as _amo_exc:
                _note_swallowed("wardrobe.Wardrobe._purge_swim_refs", _amo_exc)
    def _trim_daily(self, data):
        days = sorted(data.get("daily", {}))
        for day in days[:-30]:
            data["daily"].pop(day, None)

    # ---------- 查询 ----------
    def _items_map(self, data):
        return {item["id"]: item for item in data.get("items", [])}

    def _outfit_by_id(self, data, outfit_id):
        for outfit in data.get("outfits", []):
            if outfit.get("id") == outfit_id:
                return outfit
        return None

    def _describe(self, data, outfit):
        items = self._items_map(data)
        coat = items.get(outfit.get("coat")) or {}
        top = items.get(outfit.get("top")) or {}
        bottom = items.get(outfit.get("bottom")) or {}
        skirt = items.get(outfit.get("skirt")) or {}
        shoes = items.get(outfit.get("shoes")) or {}
        names = [str(part.get("name") or "") for part in (coat, top, skirt, bottom, shoes)]
        names = [name for name in names if name]
        return {
            "outfit": outfit.get("id", ""),
            "coat": str(coat.get("name") or ""),
            "top": str(top.get("name") or ""),
            "bottom": str(bottom.get("name") or ""),
            "skirt": str(skirt.get("name") or ""),
            "shoes": str(shoes.get("name") or ""),
            "text": "，".join(names),
            "items": [coat.get("id", ""), top.get("id", ""), bottom.get("id", ""),
                      skirt.get("id", ""), shoes.get("id", "")],
            "swim": bool(outfit.get("swim")),
        }

    def _weather_temp(self):
        try:
            return float((getattr(self.plugin.state, "weather", {}) or {}).get("temp") or 0)
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._weather_temp", _amo_exc)
            return 0.0

    def _candidates(self, data):
        items = self._items_map(data)

        def usable(outfit):
            if outfit.get("sleep") or outfit.get("swim") or not outfit.get("shoes"):
                return False
            skirt = items.get(outfit.get("skirt")) or {}
            if is_dress(skirt.get("name")):
                return True
            if outfit.get("skirt"):
                return bool(outfit.get("top"))
            return bool(outfit.get("top")) and bool(outfit.get("bottom"))

        outfits = [outfit for outfit in data.get("outfits", []) if usable(outfit)]
        keys = ("coat", "top", "bottom", "skirt", "shoes")

        def names_of(outfit):
            return [str(items.get(outfit.get(key), {}).get("name") or "")
                    for key in keys]

        def has(outfit, words):
            return any(word in name for name in names_of(outfit) for word in words)

        # 上班/通勤/见客户：不穿泳衣、吊带、热裤这类
        scene = ""
        try:
            activity = self.plugin.state.current_activity() or {}
            scene = str(activity.get("place") or "") + " " + str(activity.get("activity") or "")
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._candidates", _amo_exc)
            scene = ""
        if any(word in scene for word in WORK_SCENE_WORDS):
            work_safe = [outfit for outfit in outfits if not has(outfit, SWIM_WORDS)]
            if work_safe:
                outfits = work_safe
        temp = self._weather_temp()
        if temp:
            if temp >= 26:
                cool = [outfit for outfit in outfits
                        if not has(outfit, SUMMER_BAD_WORDS)]
                if cool:
                    return cool
            elif temp <= 10:
                warm = [outfit for outfit in outfits
                        if not has(outfit, SUMMER_DRESS_WORDS)]
                if temp <= 5:
                    down = [outfit for outfit in warm if has(outfit, ("羽绒服",))]
                    if down:
                        return down
                if warm:
                    return warm
        return outfits

    def _is_warm(self, items, outfit):
        names = [str(items.get(outfit.get(key), {}).get("name") or "")
                 for key in ("coat", "top", "bottom", "skirt", "shoes")]
        return any(word in name for name in names for word in WARM_WORDS + COOL_BOTTOM_WORDS)

    def _pick(self, data, day, exclude=""):
        outfits = self._candidates(data)
        if exclude:
            outfits = [outfit for outfit in outfits if outfit.get("id") != exclude] or outfits
        if not outfits:
            # 衣服被删光了：补回种子（新 id），不然今日穿搭会指向不存在的单品
            seed = self._seed()
            data.setdefault("items", []).extend(seed["items"])
            data.setdefault("outfits", []).extend(seed["outfits"])
            outfits = seed["outfits"]
        chooser = random.Random("amo-wardrobe:" + day)
        return chooser.choice(outfits)

    # ---------- 每日穿搭 ----------
    def today(self, day=""):
        day = str(day or today_str())
        with self._lock:
            if self._today_cache and self._today_cache.get("day") == day:
                return dict(self._today_cache)
            data = self._load()
            entry = (data.get("daily") or {}).get(day) or {}
            outfit = self._outfit_by_id(data, entry.get("outfit"))
            if outfit is None:
                outfit = self._pick(data, day)
                data.setdefault("daily", {})[day] = {
                    "outfit": outfit.get("id", ""),
                    "ts": time.time(),
                }
                self._trim_daily(data)
                self._save(data)
            result = self._describe(data, outfit)
            result["day"] = day
            self._today_cache = result
        return dict(result)

    def _sleep_candidates(self, data, hotel=False):
        """选睡衣时也看天气：夏天不选厚的，冬天优先法兰绒/珊瑚绒/加厚。"""
        outfits = self._sleep_outfits(data, hotel)
        temp = self._weather_temp()
        if not temp:
            return outfits
        thick_words = ("法兰绒", "珊瑚绒", "加厚", "夹棉", "毛绒", "羊毛", "针织")
        if temp >= 26:
            cool = [outfit for outfit in outfits
                    if not any(word in str((self._items_map(data).get(outfit.get("top")) or {}).get("name") or "")
                               for word in thick_words)]
            return cool or outfits
        if temp <= 10:
            warm = [outfit for outfit in outfits
                    if any(word in "".join(str((self._items_map(data).get(outfit.get(key)) or {}).get("name") or "")
                                           for key in ("top", "bottom", "skirt"))
                           for word in thick_words)]
            return warm or outfits
        return outfits

    def _sleep_outfits(self, data, hotel=False):
        """家里只取家里睡衣、酒店只取酒店睡衣，两边绝不混搭。"""
        return [outfit for outfit in data.get("outfits", [])
                if outfit.get("sleep") and bool(outfit.get("hotel")) == bool(hotel)]

    def _night_entry(self, data, day):
        entry = (data.get("daily") or {}).get(day) or {}
        return self._outfit_by_id(data, str(entry.get("night") or ""))

    def _describe_sleep(self, data, outfit, day):
        result = self._describe(data, outfit)
        result["day"] = day
        result["sleep"] = True
        result["hotel"] = bool(outfit.get("hotel"))
        return result

    # ---------- 泳装（游泳馆 / 海边 / 水上乐园） ----------

    def _swim_outfits(self, data):
        """整套泳装：上衣和泳裤都得在、而且属于同一套（不留单件/混搭）。"""
        items = self._items_map(data)
        result = []
        for outfit in data.get("outfits", []):
            if not outfit.get("swim"):
                continue
            top = items.get(outfit.get("top")) or {}
            bottom = items.get(outfit.get("bottom")) or {}
            if not top or not bottom:
                continue
            if str(top.get("set") or "") != str(bottom.get("set") or ""):
                continue
            result.append(outfit)
        return result

    def swim_sets(self, data=None):
        """给面板用：整套泳装列表（一套一行，不能拆开挑）。"""
        data = data if isinstance(data, dict) else self._load()
        items = self._items_map(data)
        sets = []
        for outfit in self._swim_outfits(data):
            top = items.get(outfit.get("top")) or {}
            bottom = items.get(outfit.get("bottom")) or {}
            sets.append({
                "id": outfit.get("id") or "",
                "set": outfit.get("set") or top.get("set") or "",
                "top": str(top.get("name") or ""),
                "bottom": str(bottom.get("name") or ""),
                "top_id": str(top.get("id") or ""),
                "bottom_id": str(bottom.get("id") or ""),
            })
        sets.sort(key=lambda entry: entry["set"])
        return sets

    def _at_swim(self):
        """现在是不是在游泳/海边/水上乐园：是的话自动换泳装。"""
        try:
            activity = self.plugin.state.current_activity() or {}
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._at_swim", _amo_exc)
            return False
        text = str(activity.get("place") or "") + " " + str(activity.get("activity") or "")
        return any(word in text for word in SWIM_PLACE_WORDS)

    def at_swim(self):
        return self._at_swim()

    def _describe_swim(self, data, outfit, day):
        result = self._describe(data, outfit)
        result["day"] = day
        result["swim"] = True
        result["set"] = str(outfit.get("set") or "")
        return result

    def swim_outfit(self, day=""):
        """今天去游泳/海边时穿的那套泳装：按日期锁一套，面板可改。"""
        day = str(day or today_str())
        with self._lock:
            data = self._load()
            entry = (data.get("daily") or {}).get(day) or {}
            outfit = self._outfit_by_id(data, str(entry.get("swim") or ""))
            if outfit is None or not outfit.get("swim"):
                pool = self._swim_outfits(data)
                if not pool:
                    self._ensure_swim(data)
                    self._save(data)
                    pool = self._swim_outfits(data)
                if not pool:
                    return {"pending": True, "swim": True, "day": day, "text": ""}
                chosen = random.Random("amo-wardrobe-swim:" + day).choice(pool)
                entry = data.setdefault("daily", {}).setdefault(day, {})
                entry["swim"] = chosen["id"]
                entry.setdefault("ts", time.time())
                self._trim_daily(data)
                self._save(data)
                outfit = chosen
            return self._describe_swim(data, outfit, day)

    def set_swim(self, day="", top="", bottom=""):
        """面板「泳衣」保存：只选上衣（泳衣）+ 裤子（泳裤），没有鞋。"""
        day = str(day or today_str())
        with self._lock:
            data = self._load()
            items = self._items_map(data)
            top_item = items.get(str(top or ""))
            bottom_item = items.get(str(bottom or ""))
            if not (top_item and top_item.get("category") == "top" and top_item.get("swim")):
                return {"ok": False, "msg": "先选一件泳衣上衣"}
            if not (bottom_item and bottom_item.get("category") == "bottom"
                    and bottom_item.get("swim")):
                return {"ok": False, "msg": "先选一条泳裤"}
            # 泳装只能整套穿：上衣和泳裤必须是同一套，不允许混搭
            if str(top_item.get("set") or "") != str(bottom_item.get("set") or "") \
                    or not str(top_item.get("set") or ""):
                return {"ok": False, "msg": "泳装要穿整套：上衣和泳裤要选同一套"}
            outfit = None
            for entry in data.get("outfits", []):
                if (entry.get("swim") and entry.get("top") == top_item["id"]
                        and entry.get("bottom") == bottom_item["id"]):
                    outfit = entry
                    break
            if outfit is None:
                outfit = {
                    "id": _new_id("o-"),
                    "top": top_item["id"],
                    "bottom": bottom_item["id"],
                    "shoes": "",
                    "source": "manual",
                    "swim": True,
                    "set": str(top_item.get("set") or ""),
                }
                data.setdefault("outfits", []).append(outfit)
            entry = data.setdefault("daily", {}).setdefault(day, {})
            entry["swim"] = outfit["id"]
            entry.setdefault("ts", time.time())
            self._trim_daily(data)
            self._save(data)
            self._today_cache = None
        result = self.swim_outfit(day)
        result["ok"] = True
        return result

    def night(self, day="", hour=None, hotel=None):
        """今晚的睡衣：白天也直接生成并显示；住酒店时用酒店睡衣，否则用家里睡衣。"""
        day = str(day or today_str())
        if hotel is None:
            hotel = self._at_hotel()
        hotel = bool(hotel)
        with self._lock:
            data = self._load()
            entry = (data.get("daily") or {}).get(day) or {}
            outfit = self._outfit_by_id(data, str(entry.get("night") or ""))
            if (outfit is not None and outfit.get("sleep")
                    and bool(outfit.get("hotel")) == hotel):
                return self._describe_sleep(data, outfit, day)
        return self.set_night(day, hotel=hotel)

    def set_night(self, day="", outfit_id="", reroll=False, top="", bottom="", shoes="", hotel=None):
        """锁定今晚的睡衣：家里用家里睡衣，住酒店用酒店睡衣，可换一套/指定一套。"""
        day = str(day or today_str())
        if hotel is None:
            hotel = self._at_hotel()
        hotel = bool(hotel)
        with self._lock:
            data = self._load()
            outfits = self._sleep_candidates(data, hotel)
            if not outfits:
                if hotel:
                    self._ensure_hotel(data)
                else:
                    self._ensure_sleep(data)
                outfits = self._sleep_candidates(data, hotel)
            items = self._items_map(data)
            current = self._night_entry(data, day)
            if current is None or bool(current.get("hotel")) != hotel:
                current = None
            chosen = None
            if not reroll and top and bottom:
                top_item = items.get(str(top))
                bottom_item = items.get(str(bottom))
                shoes_item = items.get(str(shoes or ""))
                if (top_item and top_item.get("category") == "top" and top_item.get("sleep")
                        and bool(top_item.get("hotel")) == hotel
                        and bottom_item and bottom_item.get("category") == "bottom" and bottom_item.get("sleep")
                        and bool(bottom_item.get("hotel")) == hotel):
                    if not (shoes_item and shoes_item.get("category") == "shoes" and shoes_item.get("sleep")
                            and bool(shoes_item.get("hotel")) == hotel):
                        pool = [item for item in data["items"]
                                if item.get("category") == "shoes" and item.get("sleep")
                                and bool(item.get("hotel")) == hotel]
                        shoes_item = random.Random("amo-wardrobe-night-shoes:" + day).choice(pool) if pool else None
                    if shoes_item is not None:
                        for outfit in outfits:
                            if (outfit.get("top") == top_item["id"]
                                    and outfit.get("bottom") == bottom_item["id"]
                                    and outfit.get("shoes") == shoes_item["id"]):
                                chosen = outfit
                                break
                        if chosen is None:
                            chosen = {
                                "id": _new_id("o-"),
                                "top": top_item["id"],
                                "bottom": bottom_item["id"],
                                "shoes": shoes_item["id"],
                                "source": "manual",
                                "sleep": True,
                                "hotel": True if hotel else False,
                            }
                            data["outfits"].append(chosen)
            if chosen is None and not reroll and outfit_id:
                candidate = self._outfit_by_id(data, outfit_id)
                if (candidate is not None and candidate.get("sleep")
                        and bool(candidate.get("hotel")) == hotel):
                    chosen = candidate
            if chosen is None and current is not None and not reroll:
                chosen = current
            if chosen is None:
                pool = [outfit for outfit in outfits
                        if current is None or outfit.get("id") != current.get("id")] or outfits
                chosen = random.Random("amo-wardrobe-night:" + day).choice(pool)
            entry = data.setdefault("daily", {}).setdefault(day, {})
            entry["night"] = chosen["id"]
            entry["night_kind"] = "travel" if hotel else "home"
            entry.setdefault("ts", time.time())
            self._trim_daily(data)
            self._save(data)
        return self._describe_sleep(data, chosen, day)

    def current(self, day="", at_home=None, hour=None, at_hotel=None):
        """她现在实际穿的那套：白天穿的；晚上 20 点回到家/酒店自动换睡衣。"""
        day = str(day or today_str())
        if hour is None:
            try:
                hour = int(_clock_now().hour)
            except Exception:
                hour = 20
        if at_home is None:
            at_home = self._at_home()
        if at_hotel is None:
            at_hotel = self._at_hotel()
        home_early = False
        try:
            home_early = bool(self.plugin.db.kv_get("home_early:" + day, False))
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe.current", _amo_exc)
            home_early = False
        # 在游泳馆/海边/水上乐园：不管几点、在不在家，都换泳装
        if self._at_swim():
            result = self.swim_outfit(day)
            if result.get("pending"):
                result = self.today(day)
            self._sync_sidecar(result)
            return dict(result)
        night = hour >= SLEEP_HOUR_FROM or hour < SLEEP_HOUR_TO
        if home_early and at_home is True:
            night = True
        if night and (at_hotel or at_home is not False):
            result = self.night(day, hour=hour, hotel=at_hotel)
            if result.get("pending"):
                result = self.today(day)
        else:
            result = self.today(day)
        self._sync_sidecar(result)
        return dict(result)

    def _at_home(self):
        """看当前日程地点判断在不在家：True 在家 / False 在外面 / None 判断不了。"""
        try:
            activity = self.plugin.state.current_activity() or {}
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._at_home", _amo_exc)
            return None
        place = str(activity.get("place") or "")
        if not place:
            return None
        if any(word in place for word in HOME_PLACE_WORDS):
            return True
        if any(word in place for word in OUTSIDE_PLACE_WORDS):
            return False
        return None

    def at_home(self):
        """给聊天换装判断用：True 在家 / False 在外面 / None 不知道。"""
        return self._at_home()

    def _at_hotel(self):
        """现在是不是在酒店/民宿（出门旅游住店）。"""
        try:
            activity = self.plugin.state.current_activity() or {}
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._at_hotel", _amo_exc)
            return False
        place = str(activity.get("place") or "")
        return any(word in place for word in HOTEL_PLACE_WORDS)

    def at_hotel(self):
        return self._at_hotel()

    def reroll(self, day=""):
        day = str(day or today_str())
        with self._lock:
            data = self._load()
            entry = (data.get("daily") or {}).get(day) or {}
            outfit = self._pick(data, day, exclude=str(entry.get("outfit") or ""))
            data.setdefault("daily", {})[day] = {
                "outfit": outfit.get("id", ""),
                "ts": time.time(),
            }
            self._trim_daily(data)
            self._save(data)
            self._today_cache = None
        self.current(day)  # 同步 sidecar：白天换新一套、晚上保持睡衣
        return self.today(day)

    def set_today(self, day="", coat="", top="", bottom="", skirt="", shoes=""):
        """面板手动选好一套后保存：今天锁这一套。

        外套最多一件、可以套在衬衫外面；长裙单穿；短裙配上衣，裤子可选。
        """
        day = str(day or today_str())
        with self._lock:
            data = self._load()
            items = self._items_map(data)
            coat_item = items.get(str(coat or ""))
            top_item = items.get(str(top or ""))
            bottom_item = items.get(str(bottom or ""))
            skirt_item = items.get(str(skirt or ""))
            if coat_item and coat_item.get("category") != "coat":
                coat_item = None
            if top_item and top_item.get("category") != "top":
                top_item = None
            if bottom_item and bottom_item.get("category") != "bottom":
                bottom_item = None
            if skirt_item and skirt_item.get("category") != "skirt":
                skirt_item = None
            if skirt_item and is_dress(skirt_item.get("name")):
                top_item = None
                bottom_item = None
            elif skirt_item:
                if not top_item:
                    return {"ok": False, "msg": "短裙要配一件上衣"}
            elif not top_item:
                return {"ok": False, "msg": "先选一件上衣（衬衫）"}
            elif not bottom_item:
                return {"ok": False, "msg": "先选一条裤子/裙子"}
            shoes_item = items.get(str(shoes or ""))
            if not shoes_item or shoes_item.get("category") != "shoes":
                shoes_item = None
                entry = (data.get("daily") or {}).get(day) or {}
                old = self._outfit_by_id(data, entry.get("outfit"))
                old_shoes = items.get(old.get("shoes")) if old else None
                if old_shoes and old_shoes.get("category") == "shoes":
                    shoes_item = old_shoes
                else:
                    pool = [item for item in data["items"] if item.get("category") == "shoes"]
                    if pool:
                        shoes_item = random.Random("amo-wardrobe-shoes:" + day).choice(pool)
            if shoes_item is None:
                return {"ok": False, "msg": "还没有鞋子，先加一双"}
            ids = {
                "coat": coat_item["id"] if coat_item else "",
                "top": top_item["id"] if top_item else "",
                "bottom": bottom_item["id"] if bottom_item else "",
                "skirt": skirt_item["id"] if skirt_item else "",
                "shoes": shoes_item["id"],
            }
            outfit = None
            for entry in data.get("outfits", []):
                if all((entry.get(key) or "") == value for key, value in ids.items()):
                    outfit = entry
                    break
            if outfit is None:
                outfit = {
                    "id": _new_id("o-"),
                    **ids,
                    "source": "manual",
                }
                data.setdefault("outfits", []).append(outfit)
            data.setdefault("daily", {})[day] = {
                "outfit": outfit["id"],
                "ts": time.time(),
            }
            self._trim_daily(data)
            self._save(data)
            self._today_cache = None
        self.current(day)  # 同步 sidecar（晚上仍保持睡衣）
        result = self.today(day)
        result["ok"] = True
        return result

    # ---------- 增删 ----------
    def add_item(self, category, name, source="manual", gift_id="", sleep=False, hotel=False,
                 swim=False):
        category = str(category or "").strip()
        name = clean_name(name)
        hotel = bool(hotel)
        swim = bool(swim)
        sleep = (bool(sleep) or hotel) and not swim
        if category not in CATEGORY_KEYS:
            return {"ok": False, "msg": "分类不对，只能是 top / bottom / shoes"}
        if not name:
            return {"ok": False, "msg": "衣服名字不能为空"}
        if swim:
            # 泳装只能整套穿：面板里选整套，不接受单件添加，避免出现「有上衣没泳裤」
            return {"ok": False, "msg": "泳装只能整套穿，不能单件添加"}
        with self._lock:
            data = self._load()
            for item in data["items"]:
                if (item.get("category") == category and item.get("name") == name
                        and bool(item.get("sleep")) == sleep
                        and bool(item.get("hotel")) == hotel
                        and bool(item.get("swim")) == swim):
                    return {"ok": True, "item": dict(item), "dup": True, "combos": 0}
            item = {
                "id": _new_id("i-"),
                "category": category,
                "name": name,
                "source": source or "manual",
                "gift_id": gift_id or "",
                "created_at": time.time(),
            }
            if sleep:
                item["sleep"] = True
            if hotel:
                item["hotel"] = True
            if swim:
                item["swim"] = True
            data["items"].append(item)
            combos = self._combos_for(data, item)
            if len(data["outfits"]) > MAX_OUTFITS:
                data["outfits"] = data["outfits"][-MAX_OUTFITS:]
            self._save(data)
            self._today_cache = None
        return {"ok": True, "item": dict(item), "combos": combos}

    def delete_item(self, item_id):
        item_id = str(item_id or "").strip()
        if not item_id:
            return {"ok": False, "msg": "缺少衣服 id"}
        with self._lock:
            data = self._load()
            before = len(data["items"])
            deleted = [item for item in data["items"] if item.get("id") == item_id]
            data["items"] = [item for item in data["items"] if item.get("id") != item_id]
            if len(data["items"]) == before:
                return {"ok": False, "msg": "没找到这件衣服"}
            # 衣服删了，它的参考图也一起删掉（只删这件自己那张，不动同名不同分类的）
            if deleted and _item_ref_path is not None:
                try:
                    ref = _item_ref_path(self.plugin, deleted[0])
                    if ref and os.path.isfile(ref):
                        os.remove(ref)
                        logger.info("[amo] 服装参考图已删除：%s" % ref)
                except Exception as _amo_exc:
                    _note_swallowed("wardrobe.Wardrobe.delete_item.ref", _amo_exc)
            gone = [outfit["id"] for outfit in data["outfits"]
                    if item_id in (outfit.get("coat"), outfit.get("top"),
                                   outfit.get("bottom"), outfit.get("skirt"),
                                   outfit.get("shoes"))]
            data["outfits"] = [outfit for outfit in data["outfits"]
                               if outfit.get("id") not in gone]
            for day, entry in list((data.get("daily") or {}).items()):
                if entry.get("outfit") in gone:
                    data["daily"].pop(day, None)
                elif entry.get("swim") in gone:
                    data["daily"][day].pop("swim", None)
            self._ensure_day(data)
            self._refill_sleep_combos(data)
            self._save(data)
            self._today_cache = None
        return {"ok": True, "deleted": deleted[0] if deleted else {}, "outfits": len(gone)}

    def _combos_for(self, data, item):
        """新加一件衣服时，和现有互补单品组几套（同色系/同材质、外套最多一件）。"""
        sleep = bool(item.get("sleep"))
        hotel = bool(item.get("hotel"))
        swim = bool(item.get("swim"))
        by_cat = {key: [entry for entry in data["items"]
                        if entry.get("category") == key
                        and bool(entry.get("sleep")) == sleep
                        and bool(entry.get("hotel")) == hotel
                        and bool(entry.get("swim")) == swim]
                  for key in CATEGORY_KEYS}
        coats = by_cat.get("coat") or []
        tops = by_cat.get("top") or []
        bottoms = by_cat.get("bottom") or []
        skirts = by_cat.get("skirt") or []
        shoes_pool = by_cat.get("shoes") or []
        dress_skirts = [entry for entry in skirts if is_dress(entry.get("name"))]
        short_skirts = [entry for entry in skirts if not is_dress(entry.get("name"))]
        category = item.get("category")
        combos = []
        if swim:
            # 泳装只有「上衣 + 泳裤」两件，不配外套/裙子/鞋子；而且只跟自己这一套配
            my_set = str(item.get("set") or "")
            if category == "top":
                for bottom in bottoms:
                    if my_set and str(bottom.get("set") or "") == my_set:
                        combos.append(("", item, bottom, "", ""))
            elif category == "bottom":
                for top in tops:
                    if my_set and str(top.get("set") or "") == my_set:
                        combos.append(("", top, item, "", ""))
        elif category == "coat":
            for top in tops:
                for shoe in shoes_pool:
                    for bottom in bottoms:
                        combos.append((item, top, bottom, "", shoe))
                    for skirt in short_skirts:
                        combos.append((item, top, "", skirt, shoe))
            for dress in dress_skirts:
                for shoe in shoes_pool:
                    combos.append((item, "", "", dress, shoe))
        elif category == "top":
            for coat in coats:
                for shoe in shoes_pool:
                    for bottom in bottoms:
                        combos.append((coat, item, bottom, "", shoe))
                    for skirt in short_skirts:
                        combos.append((coat, item, "", skirt, shoe))
            for shoe in shoes_pool:
                for bottom in bottoms:
                    combos.append(("", item, bottom, "", shoe))
                for skirt in short_skirts:
                    combos.append(("", item, "", skirt, shoe))
        elif category == "bottom":
            for top in tops:
                for shoe in shoes_pool:
                    combos.append(("", top, item, "", shoe))
                    for coat in coats:
                        combos.append((coat, top, item, "", shoe))
        elif category == "skirt":
            if is_dress(item.get("name")):
                for shoe in shoes_pool:
                    combos.append(("", "", "", item, shoe))
                    for coat in coats:
                        combos.append((coat, "", "", item, shoe))
            else:
                for top in tops:
                    for shoe in shoes_pool:
                        combos.append(("", top, "", item, shoe))
                        for bottom in bottoms:
                            combos.append(("", top, bottom, item, shoe))
                            for coat in coats:
                                combos.append((coat, top, bottom, item, shoe))
        else:
            for top in tops:
                for bottom in bottoms:
                    combos.append(("", top, bottom, "", item))
                for skirt in short_skirts:
                    combos.append(("", top, "", skirt, item))
                    for bottom in bottoms:
                        combos.append(("", top, bottom, skirt, item))
            for coat in coats:
                for top in tops:
                    for bottom in bottoms:
                        combos.append((coat, top, bottom, "", item))
            for dress in dress_skirts:
                combos.append(("", "", "", dress, item))

        def combo_names(entry):
            return tuple(str(part.get("name") or "") if part else "" for part in entry)

        if not swim:
            # 泳装不需要同色系/同材质：一套泳衣本来就是配好的
            combos = [entry for entry in combos if combo_compatible(*combo_names(entry))]
        random.Random("amo-wardrobe-combo:" + str(item.get("id") or "")).shuffle(combos)
        combos = combos[:4]
        existing = {tuple(outfit.get(key) or "" for key in
                          ("coat", "top", "bottom", "skirt", "shoes"))
                    for outfit in data["outfits"]}
        added = 0
        for coat, top, bottom, skirt, shoe in combos:
            key = (coat["id"] if coat else "", top["id"] if top else "",
                   bottom["id"] if bottom else "", skirt["id"] if skirt else "",
                   shoe["id"] if shoe else "")
            if key in existing:
                continue
            existing.add(key)
            data["outfits"].append({
                "id": _new_id("o-"),
                "coat": key[0], "top": key[1], "bottom": key[2],
                "skirt": key[3], "shoes": key[4],
                "source": item.get("source") or "manual",
                "sleep": True if sleep else False,
                "hotel": True if hotel else False,
                "swim": True if swim else False,
                "combo": True,
            })
            added += 1
        return added

    # ---------- 礼物 ----------
    def add_from_gift(self, item, gift_id=""):
        """他送的衣服自动进衣柜；睡衣单独归到晚上衣柜，不是衣服就不动。"""
        name = clean_name(item)
        sleep_category = classify_sleep_item(name)
        if sleep_category:
            result = self.add_item(sleep_category, name, source="gift", gift_id=gift_id, sleep=True)
            if result.get("ok"):
                result["category"] = sleep_category
                result["sleep"] = True
            return result
        category = classify_item(name)
        if not category:
            return None
        result = self.add_item(category, name, source="gift", gift_id=gift_id)
        if result.get("ok"):
            result["category"] = category
        return result

    # ---------- 面板 / 万象画卷 ----------
    def snapshot(self):
        data = self._load()
        today = self.today()
        try:
            hour = int(_clock_now().hour)
        except Exception:
            hour = 20

        def category_list(sleep, hotel=False, swim=False):
            result = []
            for key, label in CATEGORY_LABELS:
                items = [item for item in data.get("items", [])
                         if item.get("category") == key
                         and bool(item.get("sleep")) == sleep
                         and bool(item.get("hotel")) == hotel
                         and bool(item.get("swim")) == swim]
                items.sort(key=lambda entry: float(entry.get("created_at") or 0))
                result.append({
                    "key": key,
                    "label": label,
                    "items": [{
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "source": item.get("source") or "manual",
                        "gift_id": item.get("gift_id") or "",
                    } for item in items],
                })
            return result

        # 晚上穿搭（睡衣）只在 20:00 之后生成：白天面板只提示「20:00 后自动生成」，
        # 免得早上就看到今晚穿哪套。真正换上同样在 20:00（current() 里判断）。
        night_ready = hour >= SLEEP_HOUR_FROM or hour < SLEEP_HOUR_TO
        return {
            "categories": category_list(False, False, False),
            "sleep_categories": category_list(True, False, False),
            "hotel_categories": category_list(True, True, False),
            # 泳装只有上衣（泳衣）和裤子（泳裤），鞋是横线
            "swim_categories": category_list(False, False, True),
            "swim_sets": self.swim_sets(data),
            "swim": self.swim_outfit(),
            "at_swim": self._at_swim(),
            "today": today,
            "current": self.current(),
            "night": self.night() if night_ready else {"pending": True},
            "hour": hour,
            "outfit_count": len([outfit for outfit in data.get("outfits", [])
                                 if is_day_outfit(outfit)]),
            "sleep_outfit_count": len(self._sleep_outfits(data)),
            "hotel_sleep_outfit_count": len([outfit for outfit in data.get("outfits", [])
                                             if outfit.get("hotel")]),
            "swim_outfit_count": len(self._swim_outfits(data)),
            "day_seed_count": len([outfit for outfit in data.get("outfits", [])
                                   if is_day_outfit(outfit) and not outfit.get("combo")]),
            "sleep_seed_count": len([outfit for outfit in data.get("outfits", [])
                                     if outfit.get("sleep") and not outfit.get("hotel")
                                     and not outfit.get("combo")]),
            "hotel_seed_count": len([outfit for outfit in data.get("outfits", [])
                                     if outfit.get("hotel") and not outfit.get("combo")]),
            "path": self.path,
        }

    def _sync_sidecar(self, result):
        """把今天的穿搭写给万象画卷，聊天里直接自拍时也锁这一套。"""
        day = str(result.get("day") or "")
        text = str(result.get("text") or "")
        if not day or not text:
            return
        key = day + "|" + str(result.get("outfit") or "")
        if key == self._synced:
            return
        payload = {
            "day": day,
            "outfit": result.get("outfit") or "",
            "coat": result.get("coat") or "",
            "top": result.get("top") or "",
            "bottom": result.get("bottom") or "",
            "skirt": result.get("skirt") or "",
            "shoes": result.get("shoes") or "",
            "text": text,
            "ts": time.time(),
        }
        try:
            folder = os.path.dirname(self.sidecar_path)
            if not os.path.isdir(folder):
                return
            raw = json.dumps(payload, ensure_ascii=False)
            try:
                with open(self.sidecar_path, encoding="utf-8") as fh:
                    if fh.read() == raw:
                        self._synced = key
                        return
            except FileNotFoundError:
                pass
            tmp = self.sidecar_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(raw)
            os.replace(tmp, self.sidecar_path)
            self._synced = key
        except Exception as _amo_exc:
            _note_swallowed("wardrobe.Wardrobe._sync_sidecar", _amo_exc)
