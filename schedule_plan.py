# -*- coding: utf-8 -*-
"""schedule_plan：日程常量与纯函数（城市/住址/职业/时段银行、JSON 提取、JIT 计划）。

方法体逐字搬运自拆分前的 schedule_engine.py，未改任何文本/数值。"""

from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

try:  # Phase 11：时间/数字纯函数唯一实现在 amo_numbers
    from .amo_numbers import clamp, md5_int, parse_hhmm
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_numbers import clamp, md5_int, parse_hhmm

from typing import Any

try:  # 纯内核：HH:MM / JSON 提取（域内只用标准库）
    from .domains.schedule import plan_text as _plan_text
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.schedule import plan_text as _plan_text

try:  # 纯内核：JIT 到点计划（域内只用标准库）
    from .domains.schedule.jit_plan import (
        _BAD_WEATHER_WORDS,
        _home_day_plan,
        _is_bad_weather,
        _outing_day_plan,
        _overtime_day_plan,
        _period_day_plan,
        _sleep_plan,
        _slack_day_plan,
        jit_plan,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from domains.schedule.jit_plan import (
            _BAD_WEATHER_WORDS,
            _home_day_plan,
            _is_bad_weather,
            _outing_day_plan,
            _overtime_day_plan,
            _period_day_plan,
            _sleep_plan,
            _slack_day_plan,
            jit_plan,
        )
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

import asyncio
import hashlib
import json
import random
import re
from datetime import date, datetime, timedelta

try:
    from .schedule_holiday import (
        is_long_holiday,
        pick_amusement_spot,
        pick_holiday_plan,
    )
    from .schedule_segments import (
        amusement_segments,
        homebody_segments,
        shopping_segments,
    )
    from .schedule_scenic import scenic_segments
    from .schedule_day_segments import (
        indoor_rest_segments,
        outing_segments,
        workday_segments,
    )
    from .schedule_rest_segments import (
        business_trip_segments,
        overtime_segments,
        period_rest_segments,
        sick_rest_segments,
    )
    from .schedule_social_segments import friend_dinner_segments
    from .schedule_swim import swim_segments
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from schedule_holiday import (
            is_long_holiday,
            pick_amusement_spot,
            pick_holiday_plan,
        )
        from schedule_segments import (
            amusement_segments,
            homebody_segments,
            shopping_segments,
        )
        from schedule_scenic import scenic_segments
        from schedule_day_segments import (
            indoor_rest_segments,
            outing_segments,
            workday_segments,
        )
        from schedule_rest_segments import (
            business_trip_segments,
            overtime_segments,
            period_rest_segments,
            sick_rest_segments,
        )
        from schedule_social_segments import friend_dinner_segments
        from schedule_swim import swim_segments

# 统一时区：日程的 generated_at / 跨天日期按配置时区算，不吃服务器本地时区。
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .clock import now as _clock_now, today as _clock_today
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .clock import now as _clock_now, today as _clock_today
    except Exception:
        try:
            from clock import now as _clock_now, today as _clock_today
        except Exception:
            def _clock_now(*args, **kwargs):
                return _amo_clock.now()

            def _clock_today(*args, **kwargs):
                return date.today()

try:  # 数据表已拆到 schedule_banks.py（2026-11-18）
    from .schedule_banks import (  # noqa: F401  re-export
        MOOD_KEYS,
        MOOD_BASE,
        PROVINCE_CITIES,
        POSITIVE_LABELS,
        CITY_POOL,
        DISTRICT_MAP,
        HOME_STYLES,
        COMPANY_POOL,
        CAR_POOL,
        CAR_BRANDS,
        NEGATIVE_LABELS,
        CATEGORY_MOOD,
        TECH_WORK_BANK,
        OFFICE_WORK_BANK,
        STUDENT_BANK,
        TEACHER_BANK,
        MED_BANK,
        SALES_BANK,
        CREATIVE_BANK,
        SERVICE_BANK,
        ADMIN_BANK,
        OCCUPATION_BANKS,
        TRAVEL_KEYWORDS,
        _FOOD_ACTIVITY_WORDS,
        _MEETING_DETAIL_WORDS,
        _SLEEP_ACTIVITY_WORDS,
        _WORK_DETAIL_WORDS,
        COMMUTE_BANK,
        HOME_PLACE_WORDS,
        OUT_ACTIVITY_WORDS,
        OUT_DEPARTURE_WORDS,
        OUTDOOR_VERB_WORDS,
        HOME_ACTIVITY_WORDS,
        OUT_PLACE_WORDS,
        HOME_SAFE_WORDS,
        LUNCH_HOME_BANK,
        LUNCH_OUT_BANK,
        BREAKFAST_HOME_BANK,
        BREAKFAST_OUT_BANK,
        LUNCH_BANK,
        BREAKFAST_BANK,
        REST_BANK,
        DINNER_HOME_BANK,
        DINNER_OUT_BANK,
        DINNER_BANK,
        _MEAL_BANKS,
        HOME_LIFE_BANK,
        NIGHT_LIFE_BANK,
        SCENIC_BANK,
        SWIM_PLACES,
        SWIM_DETAILS,
        ROAD_BANK,
        TAXI_BANK,
        STATION_BANK,
        HOTEL_BANK,
        INDOOR_BANK,
        SAFE_EVENT,
        JIT_HOURS,
        TRANSIT_CODE_TEXT,
        NON_DRIVE_VEHICLE_WORDS,
        NON_DRIVE_TRANSIT,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from schedule_banks import (  # noqa: F401  re-export
        MOOD_KEYS,
        MOOD_BASE,
        PROVINCE_CITIES,
        POSITIVE_LABELS,
        CITY_POOL,
        DISTRICT_MAP,
        HOME_STYLES,
        COMPANY_POOL,
        CAR_POOL,
        CAR_BRANDS,
        NEGATIVE_LABELS,
        CATEGORY_MOOD,
        TECH_WORK_BANK,
        OFFICE_WORK_BANK,
        STUDENT_BANK,
        TEACHER_BANK,
        MED_BANK,
        SALES_BANK,
        CREATIVE_BANK,
        SERVICE_BANK,
        ADMIN_BANK,
        OCCUPATION_BANKS,
        TRAVEL_KEYWORDS,
        _FOOD_ACTIVITY_WORDS,
        _MEETING_DETAIL_WORDS,
        _SLEEP_ACTIVITY_WORDS,
        _WORK_DETAIL_WORDS,
        COMMUTE_BANK,
        HOME_PLACE_WORDS,
        OUT_ACTIVITY_WORDS,
        OUT_DEPARTURE_WORDS,
        OUTDOOR_VERB_WORDS,
        HOME_ACTIVITY_WORDS,
        OUT_PLACE_WORDS,
        HOME_SAFE_WORDS,
        LUNCH_HOME_BANK,
        LUNCH_OUT_BANK,
        BREAKFAST_HOME_BANK,
        BREAKFAST_OUT_BANK,
        LUNCH_BANK,
        BREAKFAST_BANK,
        REST_BANK,
        DINNER_HOME_BANK,
        DINNER_OUT_BANK,
        DINNER_BANK,
        _MEAL_BANKS,
        HOME_LIFE_BANK,
        NIGHT_LIFE_BANK,
        SCENIC_BANK,
        SWIM_PLACES,
        SWIM_DETAILS,
        ROAD_BANK,
        TAXI_BANK,
        STATION_BANK,
        HOTEL_BANK,
        INDOOR_BANK,
        SAFE_EVENT,
        JIT_HOURS,
        TRANSIT_CODE_TEXT,
        NON_DRIVE_VEHICLE_WORDS,
        NON_DRIVE_TRANSIT,
        )

def _city_of(ctx):
    """当前城市；没配城市就是空串（不再兜底写死某个城市）。"""
    return str((ctx or {}).get("city") or "").strip()
def _with_city(ctx, text):
    """把词尾拼在城市后面；没有城市就只留这个词（北站/车站这类）。"""
    city = _city_of(ctx)
    text = str(text or "")
    return (city + text) if city else text
def _place_of(ctx, name):
    """「城市·地点」；没有城市就只写地点，避免出现「·饭店」这种半截字符串。"""
    city = _city_of(ctx)
    name = str(name or "").strip()
    if not name:
        return city
    return (city + "·" + name) if city else name
def work_bank_for(world):
    """按职业挑工作活动库；认不出来就用通用办公，不再默认「写代码/改 Bug」。"""
    world = world or {}
    text = (str(world.get("occupation") or "") + " " + str(world.get("workplace") or "")).lower()
    for keywords, bank in OCCUPATION_BANKS:
        for keyword in keywords:
            if keyword in text:
                return bank
    return OFFICE_WORK_BANK
def travels_for_work(world):
    text = (str((world or {}).get("occupation") or "") + " " + str((world or {}).get("workplace") or "")).lower()
    return any(keyword in text for keyword in TRAVEL_KEYWORDS)
def occupation_kind(world):
    """粗分类，用来决定突发事件文案。"""
    world = world or {}
    text = (str(world.get("occupation") or "") + " " + str(world.get("workplace") or "")).lower()
    for keywords, kind in (
        (("学生", "研究生", "大学生", "高中生", "初中生", "考研"), "student"),
        (("老师", "教师", "教授", "讲师", "教练", "园长"), "teacher"),
        (("医生", "护士", "医师", "药师", "医护", "牙医"), "med"),
        (("程序员", "开发", "架构", "运维", "测试", "技术", "科技", "算法", "前端", "后端", "软件"), "tech"),
    ):
        for keyword in keywords:
            if keyword in text:
                return kind
    return "office"
def detail_conflicts(activity, detail):
    """这一小时的「细节」和「活动」明显不是一回事时返回 True。

    真机案例（2026-11-10 11:00）：activity 被模型润色成「在便利店纠结买啥」，
    但 detail 还是按 zone=office/category=work 随机挑的「被拉进临时会议」；
    小时日记和面板于是同时出现「在便利店」和「被拉进临时会议」两套剧情。
    """
    activity = str(activity or "")
    detail = str(detail or "")
    if not activity or not detail:
        return False
    if any(word in activity for word in _FOOD_ACTIVITY_WORDS) and any(
        word in detail for word in _MEETING_DETAIL_WORDS
    ):
        return True
    if any(word in activity for word in _SLEEP_ACTIVITY_WORDS) and any(
        word in detail for word in _WORK_DETAIL_WORDS
    ):
        return True
    return False
# 2026-11-17：早饭/午饭/晚饭以前混在同一个池里，家的块会抽到「下楼吃碗粉」，
# 于是出现 place=家 + activity=楼下吃粉 的自相矛盾（bot 还把这句话原样说了出去）。
# 现在按「在哪吃」分两套：家的池只写在家弄的，外面的池只写楼下/街上吃的。
#: 这些 place 里的人是「在家」
#: 文案里出现这些词 = 人不在家（place=家 时不许出现）
#: 「出门/出去」单独出现不算矛盾（出门前收拾包 / 出门上班都在家）；
#: 只有和吃喝闲逛的动词连用（出门吃面、出去逛逛）才算人在外面。
#: 文案里出现这些词 = 这顿饭是在家弄的（place=楼下小馆 时不许出现）
#: 注意别把「煎饼/煮粉」这种买来的东西算成在家做饭：只认在家里动手的词。
#: 这些 place 是「在外面吃」
#: 「没出去/不想出门」这类否定句不算出门

# 老名字保留（导入/测试还在用）；选池请走 meal_bank_for()


def is_home_place(place):
    """place 是不是「在家」。"""
    text = str(place or "")
    if not text:
        return False
    if any(word in text for word in OUT_PLACE_WORDS):
        return False
    return any(word in text for word in HOME_PLACE_WORDS)


def meal_slot(hour):
    """这一小时属于早饭/午饭/晚饭。"""
    try:
        hour = int(hour or 0)
    except (TypeError, ValueError):
        hour = 0
    if hour < 10:
        return "breakfast"
    if 10 <= hour < 15:
        return "lunch"
    return "dinner"


def meal_bank_for(hour, zone="", place=""):
    """按「在哪吃」选文案池：家 -> 在家弄的；外面 -> 楼下/街上吃的。"""
    zone_text = str(zone or "")
    place_text = str(place or "")
    if zone_text == "home":
        home = True
    elif zone_text and zone_text != "home":
        home = False
    else:
        home = True if not place_text else is_home_place(place_text)
    return list(_MEAL_BANKS[(meal_slot(hour), home)])


def activity_place_conflict(activity, place="", zone=""):
    """activity 和 place/zone 自相矛盾时返回 True。

    place=家 却写「楼下/出去/外面」（2026-11-17 08:00 事故），
    或 place=楼下小馆 却写「在家煮面」。
    """
    act = str(activity or "")
    if not act:
        return False
    if any(word in act for word in HOME_SAFE_WORDS):
        return False
    zone_text = str(zone or "")
    place_text = str(place or "")
    if zone_text in ("road", "office", "scenic", "hotel", "station", "playground", "mall"):
        return False
    home = zone_text == "home" or (not zone_text and is_home_place(place_text))
    if home:
        if any(word in act for word in OUT_ACTIVITY_WORDS):
            return True
        return (any(word in act for word in OUT_DEPARTURE_WORDS)
                and any(word in act for word in OUTDOOR_VERB_WORDS))
    if zone_text == "restaurant" or (place_text and is_home_place(place_text) is False
                                     and any(word in place_text for word in OUT_PLACE_WORDS)):
        return any(word in act for word in HOME_ACTIVITY_WORDS)
    return False
def fmt_hhmm(minutes):
    """把分钟格式化成 HH:MM；1440 输出 24:00（用于 23:00-24:00 闭环）。"""
    return _plan_text.fmt_hhmm(minutes, _note_swallowed)


_strip_fences = _plan_text.strip_fences
_balanced = _plan_text.balanced
logger = __import__("logging").getLogger("astrbot_amoupup_forever.schedule_engine")


def json_extract(raw, fallback=None):
    """从模型输出里抠 JSON：去 Markdown、去掉前后废话、容忍尾逗号；失败返回 fallback。"""
    return _plan_text.json_extract(raw, fallback, logger)


def diary_presence_rules(together=False):
    """小日记的第 7/8 条：约会这一小时不许把老公写成“微信上的网友”。"""
    if together:
        return (
            "【物理场景事实·最高优先级】这一小时你们在一起：老公就在你对面，面对面坐在一起；"
            "这不是网恋、不是异地。聊天记录只是你们当面说的话，不能拿来判断“人在不在”。\n"
            "7. 只能写面对面的细节（他给你夹菜、你抬头看他、一起走出店门）；"
            "严禁写「他在微信上跟我说」「他不在我身边」「我一个人吃」。\n"
            "8. 面对面的互动（他抱我、他来看我、他给我夹菜）是事实，可以照写；素材里没有的动作不要编。\n"
        )
    return (
        "7. 素材包里没写他出现，就不能写他出现；严禁虚构打电话/拥抱/送药/来陪你；\n"
        "8. 可以写「想他/想被抱抱」，但不能写成「他抱了我/他打电话来」；"
        "线上聊天只能写「在微信/飞书上聊了几句」；\n"
    )
def _brief_world(world):
    world = world or {}
    hobbies = world.get("hobbies") or []
    if isinstance(hobbies, list):
        hobbies = "、".join([str(item) for item in hobbies])
    return "\n".join([
        "名字：%s；年龄：%s；城市：%s；职业：%s；任职：%s" % (
            world.get("name", ""), world.get("age", ""), world.get("city", ""),
            world.get("occupation", ""), world.get("workplace", ""),
        ),
        "性格：%s；爱好：%s；日常：%s" % (
            world.get("personality", ""), hobbies, world.get("daily_life", ""),
        ),
    ])
def _weather_text(weather):
    weather = weather or {}
    city = str(weather.get("city") or "").strip()
    prefix = (city + " ") if city else ""
    return "%s%s %s℃" % (prefix, weather.get("text", ""), weather.get("temp", ""))
def _mood_text(mood):
    mood = mood or {}
    return " ".join("%s%d" % (key, int(round(float(mood.get(key, 0) or 0)))) for key in MOOD_KEYS)
def _scenic_text(scenic):
    if not scenic:
        return "无"
    projects = scenic.get("projects") or []
    if isinstance(projects, list):
        projects = "、".join([str(item) for item in projects])
    return "%s（%s·%s，%s）：%s" % (
        scenic.get("name", ""), scenic.get("city", ""), scenic.get("area", ""),
        scenic.get("duration", ""), projects,
    )
#: 坏天气：下雨/下雪/打雷/大风/沙尘，或极端温度


# ---------------------------------------------------------------------------
# T1：路上块交通方式统一口径（vehicle 可能是车名，也可能是地铁/公交/打车）
# ---------------------------------------------------------------------------
#: transit 代码 -> 提示词/日记里的中文说法
#: vehicle 里出现这些词 = 公共交通/两轮，不能当私家车名写成「开着X」
#: transit 代码里明确不是自驾的


def vehicle_is_car(item):
    """vehicle 是不是「她自己的车名」；地铁/打车/公交一律不是。

    2026-11-17 事故：`vehicle=地铁` 被 plan/diary 当成车名，提示词里写成
    「这一小时是开地铁」、日记写成「开着地铁」。这里把车名和交通方式分开。
    """
    name = str((item or {}).get("vehicle") or "").strip()
    if not name:
        return ""
    if any(word in name for word in NON_DRIVE_VEHICLE_WORDS):
        return ""
    transit = str((item or {}).get("transit") or "").strip()
    if transit in NON_DRIVE_TRANSIT:
        return ""
    return name


def transit_phrase(item):
    """路上块的交通短句（日记正文用）：自驾=开着<车名>，其余=在地铁上/在公交车上…"""
    car = vehicle_is_car(item)
    if car:
        return "开着" + car
    transit = str((item or {}).get("transit") or "").strip()
    return {
        "subway": "在地铁上", "bus": "在公交车上", "taxi": "坐在出租车里",
        "train": "在高铁上", "plane": "在飞机上", "walk": "在走路",
        "bicycle": "在骑车", "bike": "在骑车", "drive": "开着车",
    }.get(transit, "")


def transit_instruction(item):
    """路上块给小日记提示词的素材句：按交通方式写，不能什么都写「开车」。"""
    zone = str((item or {}).get("zone") or "")
    source = str((item or {}).get("source") or "")
    if zone != "road" and source != "transit_block":
        return ""
    car = vehicle_is_car(item)
    if car:
        return "这一小时是开%s；必须写清堵车、找停车位或路上的细节。" % car
    transit = str((item or {}).get("transit") or "").strip()
    return {
        "subway": "这一小时是坐地铁；可以写车厢、换乘、挤不挤。",
        "bus": "这一小时是坐公交；可以写等车、车上人多。",
        "taxi": "这一小时是打车；可以写司机、路况。",
        "train": "这一小时是坐高铁；可以写车站、车厢、窗外。",
        "plane": "这一小时是坐飞机；可以写候机、起飞。",
        "walk": "这一小时是走路；可以写路上的风、脚下的路。",
        "bicycle": "这一小时是骑车；可以写风、车流。",
        "bike": "这一小时是骑车；可以写风、车流。",
        "drive": "这一小时是开车；必须写清堵车、找停车位或路上的细节。",
    }.get(transit, "")


def _diary_material_lines(self, ctx, item, prev_item, next_item, prev_diary="",
                          same_place=True):
    """小日记提示词里的素材行：上/下一小时、车、礼物、穿着。模块级函数，假 self 也能直接调。"""
    prev_text = "无"
    if prev_item:
        prev_text = "%s-%s 在%s，%s，心情%s" % (
            prev_item.get("start"), prev_item.get("end"), prev_item.get("place"),
            prev_item.get("activity"), prev_item.get("mood") or "平静",
        )
    next_text = "无"
    if next_item:
        next_text = "%s-%s 去%s，%s" % (
            next_item.get("start"), next_item.get("end"), next_item.get("place"),
            next_item.get("activity"),
        )
    if same_place:
        prev_diary_text = str(prev_diary or "").strip()[:500] or "（还没有生成上一小时日记）"
    else:
        prev_diary_text = "（上一小时在别的地点，那段场景已经结束，不能照搬；只承接情绪）"
    car_line = transit_instruction(item)
    if item.get("detail"):
        car_line = (car_line + " 可以用的细节：%s。" % item.get("detail")).strip()
    asset_line = ""
    assets = str(ctx.get("assets") or "").strip()
    if assets:
        asset_line = ("他送你的东西：%s。写这一小时时可以自然戴/用/开，"
                      "但不要每段都提，也不要写你还想要更大的。" % assets)
    allow_gift = False
    try:
        hour = int(str(item.get("start") or "")[:2])
        allow_gift = hour in self._gift_mention_hours(ctx)
    except (TypeError, ValueError):
        allow_gift = False
    worn_line = ""
    worn = str(ctx.get("worn") or "").strip()
    if worn:
        if allow_gift:
            worn_line = "你今天身上/包里有：%s（他送的）。这一小时可以自然带一句，只提一句，别写成交代行踪。" % worn
        else:
            worn_line = "这一小时不要主动提他送的东西（今天已经提过了）；除非上一小时写了、这一小时要承接。"
    outfit_line = ""
    outfit = str(ctx.get("outfit") or "").strip()
    if outfit:
        outfit_line = ("你今天穿的是：%s（今天锁定的这一套，日记里写到衣服只能写它，不能另编）。" % outfit)
    gift_line = ""
    gift_log = str(ctx.get("gift_log") or "").strip()
    if gift_log and allow_gift:
        gift_line = ("他送过你的东西（日期）：%s。这一小时可以自然带一句；"
                     "日期不是今天的，不要写成今天送的。" % gift_log)
    return prev_text, next_text, prev_diary_text, car_line, asset_line, worn_line, gift_line, outfit_line

__all__ = [name for name in (
    'ADMIN_BANK',
    'BREAKFAST_HOME_BANK',
    'BREAKFAST_OUT_BANK',
    'DINNER_HOME_BANK',
    'DINNER_OUT_BANK',
    'HOME_ACTIVITY_WORDS',
    'HOME_PLACE_WORDS',
    'HOME_SAFE_WORDS',
    'LUNCH_HOME_BANK',
    'LUNCH_OUT_BANK',
    'OUTDOOR_VERB_WORDS',
    'OUT_ACTIVITY_WORDS',
    'OUT_DEPARTURE_WORDS',
    'OUT_PLACE_WORDS',
    'activity_place_conflict',
    'is_home_place',
    'meal_bank_for',
    'meal_slot',
    'Any',
    'BREAKFAST_BANK',
    'CAR_BRANDS',
    'CAR_POOL',
    'CATEGORY_MOOD',
    'CITY_POOL',
    'COMMUTE_BANK',
    'COMPANY_POOL',
    'CREATIVE_BANK',
    'DINNER_BANK',
    'DISTRICT_MAP',
    'HOME_LIFE_BANK',
    'HOME_STYLES',
    'HOTEL_BANK',
    'INDOOR_BANK',
    'JIT_HOURS',
    'LUNCH_BANK',
    'MED_BANK',
    'MOOD_BASE',
    'MOOD_KEYS',
    'NEGATIVE_LABELS',
    'NIGHT_LIFE_BANK',
    'OCCUPATION_BANKS',
    'OFFICE_WORK_BANK',
    'POSITIVE_LABELS',
    'PROVINCE_CITIES',
    'REST_BANK',
    'ROAD_BANK',
    'SAFE_EVENT',
    'SALES_BANK',
    'SCENIC_BANK',
    'SERVICE_BANK',
    'STATION_BANK',
    'STUDENT_BANK',
    'SWIM_DETAILS',
    'SWIM_PLACES',
    'TAXI_BANK',
    'TEACHER_BANK',
    'TECH_WORK_BANK',
    'TRAVEL_KEYWORDS',
    'TYPE_CHECKING',
    '_FOOD_ACTIVITY_WORDS',
    '_MEETING_DETAIL_WORDS',
    '_SLEEP_ACTIVITY_WORDS',
    '_WORK_DETAIL_WORDS',
    '_amo_clock',
    '_balanced',
    '_brief_world',
    '_city_of',
    '_clock_now',
    '_clock_today',
    '_diary_material_lines',
    '_mood_text',
    '_note_swallowed',
    '_place_of',
    '_scenic_text',
    '_strip_fences',
    '_weather_text',
    '_with_city',
    'amusement_segments',
    'annotations',
    'asyncio',
    'business_trip_segments',
    'clamp',
    'date',
    'datetime',
    'detail_conflicts',
    'diary_presence_rules',
    'fmt_hhmm',
    'friend_dinner_segments',
    'hashlib',
    'homebody_segments',
    'indoor_rest_segments',
    'is_long_holiday',
    'jit_plan',
    'json',
    'json_extract',
    'logger',
    'md5_int',
    'occupation_kind',
    'outing_segments',
    'overtime_segments',
    'parse_hhmm',
    'period_rest_segments',
    'pick_amusement_spot',
    'pick_holiday_plan',
    'random',
    're',
    'scenic_segments',
    'shopping_segments',
    'sick_rest_segments',
    'swim_segments',
    'timedelta',
    'travels_for_work',
    'work_bank_for',
    'workday_segments',
) if name in globals()]
