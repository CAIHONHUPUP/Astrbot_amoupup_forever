# -*- coding: utf-8 -*-
"""wardrobe_data：衣柜常量与分类纯函数（品类/颜色/材质/搭配判定、种子模板）。

方法体逐字搬运自拆分前的 wardrobe.py，未改任何文本/数值。"""

from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import json
import os
import random
import re
import threading
import time
import uuid
from datetime import date, timedelta

from astrbot.api import logger

try:  # 原子写 JSON（tmp + fsync + os.replace），三个模块共用一份
    from .amo_fileio import atomic_write_json
except ImportError:  # pragma: no cover - 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_fileio import atomic_write_json

try:  # 删衣服时把它那张参考图也删掉
    from .outfit_refs import item_ref_path as _item_ref_path
except Exception:  # pragma: no cover - 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from outfit_refs import item_ref_path as _item_ref_path
        except Exception:
            _item_ref_path = None  # type: ignore[assignment]


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

try:
    from .amo_shared import today_str
except Exception:  # pragma: no cover - 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from amo_shared import today_str
        except Exception:
            def today_str():  # type: ignore[misc]
                import datetime
                return datetime.date.today().isoformat()

try:  # 用插件统一时区判断「几点、该不该换睡衣」
    from .clock import now as _clock_now
except Exception:  # pragma: no cover
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from clock import now as _clock_now
        except Exception:
            from datetime import datetime as _dt

            def _clock_now(*args, **kwargs):  # type: ignore[misc]
                return _dt.now()

CATEGORY_LABELS = (
    ("coat", "外套"),
    ("top", "上衣（衬衫）"),
    ("bottom", "裤子"),
    ("skirt", "裙子"),
    ("shoes", "鞋子"),
    ("socks", "袜子"),
)
CATEGORY_KEYS = tuple(key for key, _ in CATEGORY_LABELS)
CATEGORY_NAMES = dict(CATEGORY_LABELS)
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
WARM_WORDS = ("外套", "大衣", "羽绒服", "毛衣", "针织", "卫衣", "风衣", "西装", "马甲", "灯芯绒")
COOL_BOTTOM_WORDS = ("灯芯绒", "针织长裙")
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
HOME_PLACE_WORDS = (
    "家", "客厅", "卧室", "宿舍", "房间", "阳台", "沙发", "床", "厨房", "浴室",
    "卫生间", "洗手间", "餐桌", "书房", "玄关", "被窝", "屋里", "室内", "地毯",
)
OUTSIDE_PLACE_WORDS = (
    "公司", "写字楼", "办公室", "工位", "商场", "街", "地铁", "公交", "车站", "餐厅",
    "咖啡", "学校", "教室", "公园", "医院", "超市", "店", "路", "广场", "图书馆",
    "健身房", "景区", "酒店", "机场", "火车", "车", "摊",
)
SLEEP_HOUR_FROM = 20
SLEEP_HOUR_TO = 8
DAY_TARGET_OUTFITS = 120
DAY_SEED_VERSION = 4
SLEEP_TARGET_OUTFITS = 120
SLEEP_EXTRA_ITEMS = (
    ("shoes", "兔子毛绒拖鞋"),
    ("shoes", "厚底棉拖鞋"),
)
SLEEP_SEED_VERSION = 4
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
SWIM_PLACE_WORDS = ("游泳", "泳池", "泳馆", "海边", "沙滩", "水上乐园", "水世界",
                    "海里", "玩水", "泳装")
SWIM_STRONG_WORDS = ("游泳", "泳池", "游泳馆", "泳馆", "水上乐园", "水世界",
                     "海里", "玩水", "踩浪", "戏水", "冲浪", "滑道")
SWIM_NEGATIVE_WORDS = ("散步", "拍照", "看海", "路过", "买泳装")
SWIM_SPOTS = ("游泳馆", "泳池", "海边", "沙滩", "水上乐园", "水世界")
SHOE_WORDS = ("鞋", "靴", "拖鞋", "凉拖", "玛丽珍")
#: 袜子单独一类：只有出门穿，在家/晚上不穿（2026-11-19 用户要求）
SOCKS_WORDS = ("丝袜", "连裤袜", "长筒袜", "短袜", "棉袜", "船袜", "打底袜",
               "渔网袜", "过膝袜", "堆堆袜", "袜子")
SOCKS_SEED_VERSION = 1
SEED_SOCKS = (
    "黑色连裤袜", "肉色连裤袜", "黑色丝袜", "肉色丝袜", "黑色长筒袜",
    "白色长筒袜", "灰色过膝袜", "黑色渔网袜", "棉质短袜", "船袜",
)
def _ensure_socks_items(data, new_id):
    """补齐 10 款袜子（只加缺的，不复活用户删掉的同名以外单品）。"""
    items = data.setdefault("items", [])
    have = {(item.get("category"), str(item.get("name") or "")) for item in items}
    for name in SEED_SOCKS:
        if ("socks", name) in have:
            continue
        items.append({"id": new_id("i-"), "category": "socks", "name": name,
                      "source": "seed", "gift_id": "",
                      "created_at": _amo_clock.now_ts()})
    return items

def is_long_socks(name):
    """长袜/连裤袜：穿长裙时不能配，只能配短袜；长袜只能配短裤。"""
    text = str(name or "")
    return any(word in text for word in ("连裤袜", "打底袜", "丝袜", "长筒袜",
                                         "过膝袜", "渔网袜", "堆堆袜"))

def is_home_socks(name):
    """棉袜/船袜这类可以在家穿的袜子（长袜/连裤袜只出门穿）。"""
    text = str(name or "")
    if not text or "袜" not in text or is_long_socks(text):
        return False
    return any(word in text for word in ("棉", "船", "短袜"))

def is_short_socks(name):
    text = str(name or "")
    return bool(text) and ("袜" in text) and not is_long_socks(text)

def is_tights(name):
    """连裤袜/丝袜/打底袜：穿了可以不再穿裤子。"""
    text = str(name or "")
    return any(word in text for word in ("连裤袜", "打底袜", "丝袜"))
BOTTOM_WORDS = ("裤", "裙")
TOP_WORDS = (
    "衬衫", "上衣", "T恤", "t恤", "卫衣", "毛衣", "针织衫", "开衫", "外套", "大衣",
    "风衣", "羽绒服", "西装", "夹克", "马甲", "背心", "吊带", "雪纺", "Polo", "polo",
    "打底衫", "罩衫",
)
COAT_WORDS = ("羽绒服", "棉服", "大衣", "风衣", "夹克", "羊羔毛", "毛呢", "外套")
SUMMER_BAD_WORDS = ("羽绒服", "棉服", "大衣", "毛呢", "羊羔毛", "加厚", "夹棉",
                    "毛衣", "卫衣", "针织", "开衫", "西装", "外套", "风衣", "夹克",
                    "法兰绒", "珊瑚绒", "加绒", "羊毛", "马海毛")
SUMMER_DRESS_WORDS = ("短袖", "T恤", "t恤", "短裤", "短裙", "凉鞋", "吊带", "背心")
WORK_SCENE_WORDS = ("公司", "写字楼", "办公室", "工位", "上班", "开会", "通勤",
                    "地铁", "客户", "学校", "教室")
SWIM_WORDS = ("泳衣", "泳装", "比基尼", "吊带", "热裤", "超短", "沙滩", "露肩")
SLEEP_SHOE_WORDS = ("拖鞋", "棉拖", "室内拖")
SLEEP_BOTTOM_WORDS = ("睡裤",)
SLEEP_TOP_WORDS = ("睡衣", "睡裙", "睡袍", "家居服")
QUANTIFIER_RE = re.compile(r"^(一条|一件|一双|一顶|一个|一套|只|条|件|双)\s*")
MAX_OUTFITS = 400
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
DRESS_WORDS = ("连衣裙", "长裙", "连身裙")
def is_dress(name):
    return any(word in str(name or "") for word in DRESS_WORDS)
def is_swim_item(item):
    return bool((item or {}).get("swim"))
def is_swim_outfit(outfit):
    return bool((outfit or {}).get("swim"))
def is_day_item(item):
    """白天衣柜只收普通衣服：睡衣、泳装、居家便服都不参与白天随机。"""
    item = item or {}
    return (not item.get("sleep") and not item.get("swim")
            and not item.get("homewear"))
def is_day_outfit(outfit):
    outfit = outfit or {}
    return not outfit.get("sleep") and not outfit.get("swim")
HOME_SEED_VERSION = 3
HOME_TOPS = ("洗得发白的宽松大T恤", "纯棉长袖")
HOME_BOTTOMS = ("灰色运动短裤", "米色居家长裤")
HOME_SHOES = ("居家棉拖鞋", "软底室内拖鞋")
HOME_EXTRA = ("白色纯棉短袖", "浅灰宽松T恤", "米色宽松针织开衫", "粉色珊瑚绒家居上衣",
              "浅灰棉质家居裤", "黑色运动短裤", "米色针织家居裤", "深灰宽松卫裤",
              "软底拖鞋", "棉质软底室内拖鞋")
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
    if any(word in text for word in SOCKS_WORDS):
        return "socks"
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
def _combo_candidates(item, by_cat, swim):
    """按新加单品的类别铺出所有候选组合；同色系/同材质过滤和打乱交给调用方。"""
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

    return combos
def _outfit_slots(hour, swim_now, at_home, at_hotel):
    """算今天/今晚的档位：ready / pending8 / outing / pending20 / swim，外加日志用的 reason。"""
    day_ready = hour >= 8
    night_ready = hour >= SLEEP_HOUR_FROM
    # 酒店不算“临时外出”：住酒店仍用酒店睡衣；家里/外面才分。
    night_out = bool(night_ready and at_home is False and not at_hotel)
    if swim_now:
        day_state = night_state = "swim"
    else:
        day_state = "ready" if day_ready else "pending8"
        night_state = ("outing" if night_out else
                       ("ready" if night_ready else "pending20"))
    reason = ("swim_scene" if swim_now else
              "before_8" if hour < 8 else
              "out_after_20" if night_out else
              "home_after_20" if night_ready else "daytime")
    return day_state, night_state, night_out, reason
def _wardrobe_category_list(data, sleep, hotel=False, swim=False):
    """按 sleep/hotel/swim 三态过滤出「上衣/裤子/裙子/鞋子」四栏（面板用）。"""
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
def _day_item_pools(items):
    """白天可用单品按类别分池；用固定种子的 rng 按原顺序打乱，保证每天组合稳定。"""
    def pick(category):
        return [item for item in items if is_day_item(item) and item.get("category") == category]

    skirts = pick("skirt")
    pools = {
        "coat": pick("coat"),
        "top": pick("top"),
        "bottom": pick("bottom"),
        "short_skirt": [item for item in skirts if not is_dress(item.get("name"))],
        "dress_skirt": [item for item in skirts if is_dress(item.get("name"))],
        "shoes": pick("shoes"),
    }
    rng = random.Random("amo-wardrobe-day-extra")
    # 顺序必须和旧代码一致：top → bottom → short_skirt → dress_skirt → shoes → coat
    for key in ("top", "bottom", "short_skirt", "dress_skirt", "shoes", "coat"):
        rng.shuffle(pools[key])
    return pools

__all__ = [name for name in (
    'BOTTOM_WORDS',
    'CATEGORY_KEYS',
    'CATEGORY_LABELS',
    'CATEGORY_NAMES',
    'COAT_SEED_VERSION',
    'COAT_WORDS',
    'COLOR_FAMILIES',
    'COOL_BOTTOM_WORDS',
    'DAY_SEED_VERSION',
    'DAY_TARGET_OUTFITS',
    'DRESS_SEED_VERSION',
    'DRESS_WORDS',
    'HOME_BOTTOMS',
    'HOME_EXTRA',
    'HOME_PLACE_WORDS',
    'HOME_SEED_VERSION',
    'HOME_SHOES',
    'HOME_TOPS',
    'HOTEL_PLACE_WORDS',
    'HOTEL_SEED',
    'HOTEL_SEED_VERSION',
    'MATERIAL_WORDS',
    'MAX_OUTFITS',
    'NEUTRAL_SHOE_WORDS',
    'OUTFIT_CHANGE_WORDS',
    'OUTFIT_CONTEXT_WORDS',
    'OUTSIDE_PLACE_WORDS',
    'QUANTIFIER_RE',
    'SEED_COATS',
    'SEED_OUTFITS',
    'SEED_SLEEP',
    'SEED_SWIM_SETS',
    'SHOE_WORDS',
    'SLEEP_BOTTOM_WORDS',
    'SLEEP_EXTRA_ITEMS',
    'SLEEP_HOUR_FROM',
    'SLEEP_HOUR_TO',
    'SLEEP_SEED_VERSION',
    'SLEEP_SHOE_WORDS',
    'SLEEP_TARGET_OUTFITS',
    'SLEEP_TOP_WORDS',
    'SUMMER_BAD_WORDS',
    'SUMMER_DRESS_WORDS',
    'SWIM_NEGATIVE_WORDS',
    'SWIM_PLACE_WORDS',
    'SWIM_SEED_VERSION',
    'SWIM_SPOTS',
    'SWIM_STRONG_WORDS',
    'SWIM_WORDS',
    'TOP_WORDS',
    'TYPE_CHECKING',
    'WARM_WORDS',
    'WORK_SCENE_WORDS',
    '_amo_clock',
    '_clock_now',
    '_combo_candidates',
    '_day_item_pools',
    '_dt',
    '_item_ref_path',
    '_new_id',
    '_note_swallowed',
    '_outfit_slots',
    '_wardrobe_category_list',
    'annotations',
    'atomic_write_json',
    'classify_item',
    'classify_sleep_item',
    'clean_name',
    'color_family',
    'combo_compatible',
    'date',
    'fabric_match',
    'is_day_item',
    'is_day_outfit',
    'is_dress',
    'is_outfit_change_request',
    'is_pajama_change_request',
    'is_swim_item',
    'is_swim_outfit',
    'json',
    'logger',
    'material_set',
    'os',
    'random',
    're',
    'shoe_matches',
    'threading',
    'time',
    'timedelta',
    'today_str',
    'uuid',
) if name in globals()]
