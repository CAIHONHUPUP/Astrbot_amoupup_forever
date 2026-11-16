# -*- coding: utf-8 -*-
"""微型日常事件（2026-11-14）：让她的世界每 1-2 小时有点小事发生。

设计约束：
- 时间法则：凌晨只有失眠/噩梦/夜宵，早上只有通勤/早饭，下午才有下午茶/摸鱼；
- 场景法则：群聊只发公共事件，私聊才发私人琐事；
- 财务闭环：有消费的事件先真扣钱，钱不够就不抽它；
- 情绪闭环：职场负向、生活正向；当天第一次负向事件会把情绪基线压低，后续正向减半；
- 记忆闭环：事件写短期记忆，聊天 3 小时后还能接上；
- 防呆：24h 同事件不重复；40% 概率“无事发生”；连续 3 次没事 → 好无聊；
- 社交保底：连续 3 天没有社交事件 → 强制父母/闺蜜互动；
- 分享欲 >50 → 主动发消息（走 amo 的主动发送通道）。
"""
from __future__ import annotations

import datetime as _dt
import random
import re

try:
    from .amo_shared import logger
    from .clock import now as _now
except ImportError:  # 直接按文件加载时
    import logging

    logger = logging.getLogger(__name__)
    from clock import now as _now

try:
    from .amo_state_snapshot import build_snapshot, snapshot_text
except ImportError:  # 直接按文件加载时
    from amo_state_snapshot import build_snapshot, snapshot_text

try:
    from .amo_state_context import (StateConflict, build_state_context,
                                    event_conflict, format_event_memory)
except ImportError:  # 直接按文件加载时
    from amo_state_context import (StateConflict, build_state_context,
                                   event_conflict, format_event_memory)

try:  # 事实绑定（2026-11-15）：日程说做饭，就别再抽"点外卖"
    from .amo_fact_binder import COOK_WORDS as _COOK_WORDS
    from .amo_fact_binder import facts_lines as _facts_lines
    from .amo_fact_binder import guard as _fact_guard
    from .amo_fact_binder import ledger_today as _ledger_today
    from .amo_fact_binder import meal_facts as _meal_facts
    from .amo_fact_binder import schedule_items as _schedule_items
except ImportError:  # 直接按文件加载时
    from amo_fact_binder import facts_lines as _facts_lines
    from amo_fact_binder import guard as _fact_guard
    from amo_fact_binder import ledger_today as _ledger_today
    from amo_fact_binder import meal_facts as _meal_facts
    from amo_fact_binder import schedule_items as _schedule_items

BANDS = ("dawn", "morning", "forenoon", "noon", "afternoon", "evening", "night")

#: 事件标签（2026-11-15）：每个事件必须带 zone: / time:，可选 health:。
#: health:>60 表示要健康才发生，health:<=60 表示只在她虚弱时发生。
#: 新加事件漏打标签，validate_pool() 会在导入时直接报错。
POOL = (
    # 凌晨（0-6 点）：只能失眠/噩梦/起夜/喝水；夜宵/刷手机只到 24:00
    {"key": "insomnia", "band": ("dawn",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:00:00-06:00"),
     "text": "凌晨还睡不着，翻来覆去刷手机", "mood": {"疲惫": 6, "焦虑": 4}},
    {"key": "nightmare", "band": ("dawn",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:00:00-06:00"),
     "text": "做了个噩梦，醒来有点心慌", "mood": {"害怕": 8, "委屈": 4}},
    {"key": "late_snack", "band": ("night",), "scene": "private", "cost": 1500,
     "tags": ("zone:home", "time:22:00-24:00"),
     "text": "肚子饿了，点了份夜宵", "mood": {"开心": 5, "满足": 4}},
    {"key": "phone_night", "band": ("night",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:22:00-24:00"),
     "text": "躺床上刷手机，刷到眼睛发涩", "mood": {"疲惫": 3, "满足": 2}},
    {"key": "drink_water", "band": ("night", "dawn"), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:22:00-06:00"),
     "text": "起来倒了杯水，嗓子有点干", "mood": {"放松": 2}},
    {"key": "night_toilet", "band": ("dawn",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:00:00-06:00"),
     "text": "起夜上了个厕所，回来接着睡", "mood": {"放松": 1}},
    # 早上（6-9 点）：洗漱/早饭/通勤
    {"key": "wash_up", "band": ("morning",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:06:00-09:00"),
     "text": "洗漱完对着镜子发了会儿呆", "mood": {"放松": 3}},
    {"key": "make_breakfast", "band": ("morning",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:06:00-09:00", "health:>60"),
     "text": "煎了个鸡蛋当早饭", "mood": {"满足": 4}},
    {"key": "subway", "band": ("morning",), "scene": "both", "cost": 500,
     "tags": ("zone:road", "time:06:30-09:30", "health:>60"),
     "text": "早上挤地铁，差点被门夹到", "mood": {"疲惫": 3, "郁闷": 3}},
    {"key": "breakfast", "band": ("morning",), "scene": "both", "cost": 800,
     "tags": ("zone:home", "time:06:00-09:00"),
     "text": "买了豆浆和包子当早饭", "mood": {"开心": 3}},
    # 上午（9-12 点）：职场
    {"key": "paid_poop", "band": ("forenoon", "afternoon"), "scene": "private", "cost": 0,
     "tags": ("zone:office", "time:09:00-18:00", "health:>60"),
     "text": "带薪去洗手间摸鱼刷手机", "mood": {"放松": 4, "开心": 2}},
    {"key": "weird_client", "band": ("forenoon",), "scene": "private", "cost": 0,
     "tags": ("zone:office", "time:09:00-12:00", "health:>60"),
     "text": "遇到一个特别难缠的客户，气得半死", "mood": {"生气": 8, "疲惫": 4}},
    {"key": "boss_scold", "band": ("forenoon", "afternoon"), "scene": "private", "cost": 0,
     "tags": ("zone:office", "time:09:00-18:00", "health:>60"),
     "text": "被老板当众说了一顿，情绪很低落", "mood": {"委屈": 10, "郁闷": 8, "开心": -10}},
    {"key": "desk_slack", "band": ("forenoon", "afternoon"), "scene": "both", "cost": 0,
     "tags": ("zone:office", "time:09:00-18:00", "health:>60"),
     "text": "在工位上摸鱼刷了会儿手机", "mood": {"开心": 2}},
    # 中午（12-14 点）
    {"key": "colleague_lunch", "band": ("noon",), "scene": "private", "cost": 0,
     "tags": ("zone:office", "time:11:30-14:00", "health:>60"),
     "text": "同事拉着我一起去吃午饭", "mood": {"开心": 4}, "social": True},
    {"key": "tea_time", "band": ("noon", "afternoon"), "scene": "both", "cost": 1500,
     "tags": ("zone:office", "time:12:00-18:00", "health:>60"),
     "text": "公司下午茶发了杯奶茶", "mood": {"开心": 6, "满足": 4}},
    {"key": "cook_feast", "band": ("noon",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:11:00-13:00", "health:>60"),
     "text": "做了顿丰盛的午饭，厨房忙了半天", "mood": {"满足": 6, "疲惫": 3}},
    {"key": "takeout", "band": ("noon", "evening"), "scene": "private", "cost": 2500,
     "tags": ("zone:home", "time:11:00-14:00", "time:17:00-21:00"),
     "text": "今天不想做饭，点了个外卖", "mood": {"开心": 3, "懒": 3}},
    # 下午（14-18 点）
    {"key": "fat_cat", "band": ("afternoon", "evening"), "scene": "both", "cost": 0,
     "tags": ("zone:road", "time:15:00-21:00", "health:>60"),
     "text": "下班路上看到一只很胖的橘猫", "mood": {"开心": 8, "兴奋": 5}},
    {"key": "fruit_tea", "band": ("afternoon",), "scene": "both", "cost": 1500,
     "tags": ("zone:mall,road", "time:14:00-18:00", "health:>60"),
     "text": "买了一杯 15 块钱的果茶", "mood": {"开心": 4}},
    {"key": "egg_price", "band": ("afternoon", "evening"), "scene": "private", "cost": 0,
     "tags": ("zone:mall", "time:16:00-21:00", "health:>60"),
     "text": "去超市发现鸡蛋又涨价了", "mood": {"郁闷": 5, "委屈": 3}},
    {"key": "sofa_show", "band": ("forenoon", "afternoon"), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:09:00-18:00"),
     "text": "窝在沙发上看了会儿综艺", "mood": {"开心": 4, "放松": 3}},
    {"key": "sun_window", "band": ("forenoon",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:09:00-12:00"),
     "text": "站在窗边晒了会儿太阳", "mood": {"放松": 4, "温暖": 2}},
    {"key": "clean_up", "band": ("forenoon", "afternoon"), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:10:00-18:00", "health:>60"),
     "text": "把家里大扫除了一遍，累得直不起腰", "mood": {"满足": 5, "疲惫": 4}},
    {"key": "yoga", "band": ("morning",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:07:00-09:00", "health:>60"),
     "text": "跟着视频做了会儿瑜伽", "mood": {"放松": 5, "满足": 2}},
    {"key": "mall_shopping", "band": ("afternoon", "evening"), "scene": "both", "cost": 0,
     "tags": ("zone:mall", "time:10:00-21:00", "health:>60"),
     "text": "在商场逛了一会儿，试了两件衣服", "mood": {"开心": 5, "兴奋": 3}},
    {"key": "rest_after_shopping", "band": ("afternoon", "evening", "night"), "scene": "private",
     "cost": 0, "after": ("mall_shopping",),
     "tags": ("zone:mall", "time:12:00-22:00", "health:>60"),
     "text": "逛累了，坐在商场长椅上歇脚，脚有点酸", "mood": {"疲惫": 5, "满足": 4}},
    # 傍晚/晚上（18-24 点）
    {"key": "sunset", "band": ("evening",), "scene": "both", "cost": 0,
     "tags": ("zone:road", "time:17:30-19:30", "health:>60"),
     "text": "下班路上看到很好看的晚霞", "mood": {"开心": 7, "温柔": 3}},
    {"key": "cook_fail", "band": ("evening",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:17:00-21:00", "health:>60"),
     "text": "想自己做饭，结果把菜炒糊了", "mood": {"郁闷": 4, "委屈": 2}},
    {"key": "drama", "band": ("evening", "night"), "scene": "both", "cost": 0,
     "tags": ("zone:home", "time:19:00-23:00"),
     "text": "追的剧更新了，看到关键处卡住", "mood": {"开心": 5, "焦虑": 2}},
    {"key": "funny_video", "band": ("evening", "night"), "scene": "both", "cost": 0,
     "tags": ("zone:home", "time:19:00-23:00"),
     "text": "刷到一个很搞笑的视频，笑出声", "mood": {"开心": 6}},
    {"key": "bff_vent", "band": ("afternoon", "evening"), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:14:00-22:00"),
     "text": "闺蜜发微信跟我吐槽她男朋友", "mood": {"开心": 3, "无奈": 2}, "social": True},
    {"key": "mom_call", "band": ("evening",), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:18:00-22:00"),
     "text": "妈妈打电话来问周末回不回家吃饭", "mood": {"温暖": 5, "依恋": 3}, "social": True},
    {"key": "shower", "band": ("evening", "night"), "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:20:00-23:30"),
     "text": "洗了个热水澡，浑身放松", "mood": {"放松": 6, "舒服": 4}},
    {"key": "rain_umbrella", "band": ("afternoon", "evening"), "scene": "both", "cost": 0,
     "tags": ("zone:road", "time:14:00-20:00", "health:>60"),
     "text": "突然下雨，躲在便利店门口等雨停", "mood": {"郁闷": 4}},
    # 生病虚弱（只有 health<=60 才发生）
    {"key": "sick_water", "band": ("dawn", "morning", "forenoon", "noon", "afternoon", "evening", "night"),
     "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:00:00-23:59", "health:<=60"),
     "text": "撑着起来倒了杯热水，把药吃了", "mood": {"委屈": 3, "疲惫": 4}},
    {"key": "sick_blanket", "band": ("dawn", "morning", "forenoon", "noon", "afternoon", "evening", "night"),
     "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:00:00-23:59", "health:<=60"),
     "text": "裹紧被子缩在床上，鼻子还是堵", "mood": {"委屈": 5, "疲惫": 6}},
    {"key": "sick_nap", "band": ("dawn", "morning", "forenoon", "noon", "afternoon", "evening", "night"),
     "scene": "private", "cost": 0,
     "tags": ("zone:home", "time:00:00-23:59", "health:<=60"),
     "text": "睡睡醒醒，下午又昏睡了一觉", "mood": {"疲惫": 6}},
    {"key": "sick_takeout", "band": ("noon", "evening"), "scene": "private", "cost": 2000,
     "tags": ("zone:home", "time:11:00-13:00", "time:17:00-20:00", "health:<=60"),
     "text": "没力气做饭，点了份清淡的外卖", "mood": {"委屈": 2, "懒": 3}},
    # 日常/上下文连贯
    {"key": "broke_water", "band": ("dawn", "morning", "forenoon", "noon", "afternoon", "evening", "night"),
     "scene": "private", "cost": 0, "requires_broke": True,
     "tags": ("zone:home", "time:00:00-23:59"),
     "text": "口渴了，但连一瓶矿泉水都舍不得买", "mood": {"委屈": 5}},
)

PUBLIC_KEYS = {"subway", "breakfast", "desk_slack", "tea_time", "fat_cat",
               "fruit_tea", "sunset", "drama", "nightmare", "mall_shopping",
               "funny_video", "rain_umbrella"}
FOOD_KEYS = {"late_snack", "takeout", "breakfast", "tea_time", "fruit_tea",
             "colleague_lunch", "broke_water"}
#: 这一餐已经在家吃过了，就别再抽"做饭/外卖"这类重复的吃饭事件（2026-11-15）
MEAL_KEYS = {"takeout", "sick_takeout", "cook_feast", "colleague_lunch"}
FOOD_COOLDOWN_SECONDS = 2 * 3600
QUIET_HOURS = (23, 30, 7, 30)

TAG_ZONE = "zone:"
TAG_TIME = "time:"
TAG_HEALTH = "health:"

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$")
_HEALTH_RE = re.compile(r"^(>=|<=|>|<|=)\s*(\d+)$")


def _tag_values(tags, prefix):
    return [str(tag)[len(prefix):].strip() for tag in (tags or ())
            if str(tag).startswith(prefix)]


def event_zones(event):
    zones = set()
    for value in _tag_values(event.get("tags"), TAG_ZONE):
        zones.update(part.strip() for part in value.split(",") if part.strip())
    return zones


def _hhmm_minutes(text):
    """'12:30' -> 750；解析不了返回 -1（2026-11-15 判定这一餐吃没吃）。"""
    try:
        hour, minute = [int(x) for x in str(text or "").split(":")[:2]]
        return hour * 60 + minute
    except Exception:
        return -1


def in_time_window(now, window):
    """HH:MM-HH:MM；22:00-06:00 这种跨午夜也算。"""
    text = str(window or "")
    if "-" not in text:
        return True
    start, end = text.split("-", 1)
    try:
        sh, sm = [int(x) for x in start.split(":")]
        eh, em = [int(x) for x in end.split(":")]
    except Exception:
        return True
    cur = int(now.hour) * 60 + int(now.minute)
    begin, finish = sh * 60 + sm, eh * 60 + em
    if begin == finish:
        return True
    if begin < finish:
        return begin <= cur < finish
    return cur >= begin or cur < finish


def health_allowed(tags, score):
    rules = _tag_values(tags, TAG_HEALTH)
    if not rules:
        return True
    for rule in rules:
        matched = _HEALTH_RE.match(rule)
        if not matched:
            continue
        op, value = matched.group(1), int(matched.group(2))
        ok = ((op == ">" and score > value) or (op == ">=" and score >= value)
              or (op == "<" and score < value) or (op == "<=" and score <= value)
              or (op == "=" and score == value))
        if ok:
            return True
    return False


def event_allowed(event, now, snapshot=None):
    """按快照过滤：地点、时间窗、健康。没有快照时只走老规则（兼容旧调用）。"""
    # 时间窗先判、且不依赖快照：启动初期 / 热重载后快照为空的那几秒，
    # 夜里的事件（夜宵、失眠）照样不能在白天被抽中（2026-11-15 修）。
    windows = _tag_values(event.get("tags"), TAG_TIME)
    if windows and not any(in_time_window(now, window) for window in windows):
        return False
    if not snapshot:
        return True
    zone = str(snapshot.get("zone") or "")
    zones = event_zones(event)
    if zones and "any" not in zones and zone not in ("", "unknown") and zone not in zones:
        return False
    health = snapshot.get("health") or {}
    score = health.get("score")
    if score is None:
        score = 40 if health.get("sick") else 100
    return health_allowed(event.get("tags"), int(score))


def _window_bounds(window):
    """“HH:MM-HH:MM” → (start_min, end_min)；格式/取值不合法返回 None。"""
    text = str(window or "").strip()
    if not _TIME_RE.match(text):
        return None
    start_text, end_text = text.split("-", 1)
    try:
        sh, sm = start_text.split(":")
        eh, em = end_text.split(":")
        start = int(sh) * 60 + int(sm)
        end = int(eh) * 60 + int(em)
    except (TypeError, ValueError):
        return None
    if not (0 <= int(sh) < 24 and 0 <= int(sm) < 60):
        return None
    if not (0 <= int(eh) <= 24 and 0 <= int(em) < 60):
        return None
    return start, end


def validate_pool(pool=None):
    """每个事件必须打 zone/time 标签；新加事件漏标签在导入时就报错。

    2026-11-15 加严：除了“有没有标签”，还查四件以前会静默出错的事——
    key 重复（抽中两个同名事件）、文案为空（记忆里出现空条目）、
    time 窗口越界/零长度（99:99、10:00-10:00 这类永远不命中的窗口）、
    zone 里有空 token（“zone:home, ”会解析出空字符串）。
    """
    problems = []
    seen_keys = set()
    for event in (pool or POOL):
        tags = event.get("tags")
        key = event.get("key")
        if not key:
            problems.append("有事件缺少 key")
        elif key in seen_keys:
            problems.append("%s 的 key 重复" % key)
        else:
            seen_keys.add(key)
        if not str(event.get("text") or "").strip():
            problems.append("%s 没有文案" % key)
        if not tags:
            problems.append("%s 没有 tags" % key)
            continue
        zones = event_zones(event)
        if not zones:
            problems.append("%s 缺少 zone: 标签" % key)
        for value in _tag_values(tags, TAG_ZONE):
            tokens = [part.strip() for part in str(value).split(",")]
            if any(not token for token in tokens):
                problems.append("%s 的 zone 标签有空项：%s" % (key, value))
        windows = _tag_values(tags, TAG_TIME)
        if not windows:
            problems.append("%s 缺少 time: 标签" % key)
        for window in windows:
            bounds = _window_bounds(window)
            if bounds is None:
                problems.append("%s 的 time 标签格式/取值不对：%s" % (key, window))
                continue
            start, end = bounds
            if start == end:
                problems.append("%s 的 time 窗口长度为零：%s" % (key, window))
        for rule in _tag_values(tags, TAG_HEALTH):
            if not _HEALTH_RE.match(rule):
                problems.append("%s 的 health 标签格式不对：%s" % (key, rule))
    if problems:
        raise ValueError("事件池标签不合格：\n" + "\n".join(problems))
    return True


validate_pool()


def band_of(hour):
    hour = int(hour) % 24
    if hour < 6:
        return "dawn"
    if hour < 9:
        return "morning"
    if hour < 12:
        return "forenoon"
    if hour < 14:
        return "noon"
    if hour < 18:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "night"


def is_quiet(hour, minute):
    hour, minute = int(hour), int(minute)
    return (hour, minute) >= QUIET_HOURS[:2] or (hour, minute) < QUIET_HOURS[2:]


def pick_event(now, cash_cents, recent_keys=(), scene="private", rng=None,
               force_social=False, last_key="", food_locked=False, snapshot=None,
               state=None, meal_done=False):
    """按时间/场景/余额/24h 去重抽一个事件；返回 None 表示这次无事发生。"""
    rng = rng or random
    band = band_of(now.hour)
    recent = set(recent_keys or ())
    candidates = []
    for event in POOL:
        if band not in event.get("band", ()):
            continue
        if state and event_conflict(event, state):
            continue
        if snapshot and not event_allowed(event, now, snapshot):
            continue
        if scene == "group" and event["key"] not in PUBLIC_KEYS:
            continue
        if int(event.get("cost") or 0) > int(cash_cents or 0):
            continue
        if event["key"] in recent:
            continue
        if food_locked and event["key"] in FOOD_KEYS:
            continue
        if meal_done and event["key"] in MEAL_KEYS:
            # 日程已经写了这一餐在家做饭/吃过了，再来一次"点外卖"就是自相矛盾
            continue
        if event.get("requires_broke") and int(cash_cents or 0) >= 1500:
            continue
        if force_social and not event.get("social"):
            continue
        candidates.append(event)
    if not candidates:
        return None
    if not force_social and rng.random() < 0.40:
        return None
    if last_key:
        follow = [event for event in candidates if last_key in (event.get("after") or ())]
        if follow:
            candidates = follow
    return rng.choice(candidates)


def merge_mood(bias, delta):
    """当天情绪基线：负向事件后，正向收益减半，负向照常。"""
    out = {}
    for key, value in (delta or {}).items():
        if bias == "low" and value > 0:
            value = max(1, int(value * 0.5))
        out[key] = value
    return out


class MicroEventEngine:
    def __init__(self, plugin):
        self.p = plugin

    # ---------- 状态 ----------
    def _kv_get(self, key, default=None):
        try:
            return self.p.db.kv_get(key, default)
        except Exception:
            return default

    def _kv_set(self, key, value):
        try:
            self.p.db.kv_set(key, value)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)

    def _today(self, now=None):
        return (now or _now()).strftime("%Y-%m-%d")

    def _meal_done(self, now):
        """今天这一餐是不是已经在家做/吃过了（2026-11-15）。

        线上事故：日程 12:00「在家做饭」，13:32 微型事件又抽到 takeout 并扣了 25 元，
        结果聊天里她说「懒得做饭，点了个外卖」——同一天两套饭。
        """
        # 日程 + 账本一起看：日程写了做饭，或者账上有一笔"自己做饭"，都算这一餐吃过了
        try:
            items = _schedule_items(self.p, self._today(now))
            ledger = _ledger_today(self.p, now)
            facts = _meal_facts(items, ledger, now)
        except Exception:
            return False
        return bool(facts.get("cooked"))

    def _recent_keys(self):
        rows = self._kv_get("micro_event:recent", {}) or {}
        if not isinstance(rows, dict):
            return []
        cutoff = (_now().timestamp() - 24 * 3600)
        return [key for key, ts in rows.items() if float(ts or 0) >= cutoff]

    def _remember_key(self, key, now):
        rows = self._kv_get("micro_event:recent", {}) or {}
        if not isinstance(rows, dict):
            rows = {}
        rows[key] = now.timestamp()
        cutoff = now.timestamp() - 48 * 3600
        rows = {k: v for k, v in rows.items() if float(v or 0) >= cutoff}
        self._kv_set("micro_event:recent", rows)

    def _empty_ticks(self):
        try:
            return int(self._kv_get("micro_event:empty_ticks", 0) or 0)
        except Exception:
            return 0

    def _set_empty_ticks(self, value):
        self._kv_set("micro_event:empty_ticks", int(value))

    def _bias(self):
        return str(self._kv_get("micro_event:bias:" + self._today(), "") or "")

    def _set_bias(self, bias):
        self._kv_set("micro_event:bias:" + self._today(), str(bias))

    def _social_last_day(self):
        return str(self._kv_get("micro_event:social_day", "") or "")

    def share_drive(self, snap=None):
        """分享欲：心情好/兴奋/想念越高越想说。"""
        mood = {}
        try:
            mood = (snap or self.p.state.snapshot()).get("mood") or {}
        except Exception:
            mood = {}
        score = 45
        for key, weight in (("开心", 0.6), ("兴奋", 0.5), ("依恋", 0.4), ("想念", 0.4)):
            try:
                score += int(float(mood.get(key) or 0) * weight)
            except Exception as exc:
                logger.debug("[amo] optional step failed: %r", exc)
        # 分享欲是累积的：事件里每遇上一件高兴事 +5，攒在 micro_event:share_drive。
        # 以前这里读的是另一个从没人写过的键（share_boost），累积等于没有；
        # 但也不能把存值直接加进 45 的基准（会滚雪球），所以取「心情分」和「存值」里大的那个。
        try:
            stored = int(self._kv_get("micro_event:share_drive", 0) or 0)
        except (TypeError, ValueError):
            stored = 0
        return max(0, min(100, max(score, stored)))

    # ---------- 应用事件 ----------
    async def _pay(self, event, now):
        cost = int(event.get("cost") or 0)
        if cost <= 0:
            return True
        try:
            if int(self.p.wallet.balance()) < cost:
                return False
            self.p.wallet.debit(cost, str(event.get("text") or "生活小事"),
                                "micro:%s:%s" % (self._today(now), event["key"]))
            return True
        except Exception:
            return False

    def _mood(self, event, now):
        bias = self._bias()
        delta = merge_mood(bias, event.get("mood") or {})
        try:
            self.p.state.nudge_once("micro:%s:%s" % (self._today(now), event["key"]), **delta)
        except Exception:
            try:
                self.p.state.nudge(**delta)
            except Exception as exc:
                logger.debug("[amo] optional step failed: %r", exc)
        if not bias:
            if any(value < 0 for value in (event.get("mood") or {}).values()):
                self._set_bias("low")
            elif any(value > 0 for value in (event.get("mood") or {}).values()):
                self._set_bias("high")
        return delta

    async def _write_memory(self, event, ctx, umo, uid):
        """统一记忆管道：先硬校验状态，再交给 generate_memory_text 生成事实句。"""
        if not uid:
            return False
        writer = getattr(self.p, "_write_memory_checked", None)
        if writer is None:
            return False
        try:
            maker = getattr(self.p, "generate_memory_text", None)
            if callable(maker):
                text = await maker(event, ctx)
            else:
                text = format_event_memory(event, ctx)
            if not text:
                return False
            return bool(await writer(uid, text, kind="event", tier=0, subject="bot"))
        except StateConflict as exc:
            logger.warning("[amo] 记忆写入拦截（状态冲突）：%s" % exc)
            return False
        except Exception:
            return False

    # ---------- 突发事件动作生命周期 ----------
    def _set_action(self, action, until, key, stage="doing", extra=None):
        data = {"action": str(action or ""), "until": float(until or 0),
                "key": str(key or ""), "stage": str(stage or "doing"),
                "ts": _now().timestamp()}
        if isinstance(extra, dict):
            data.update(extra)
        self._kv_set("micro_event:action", data)
        try:
            self.p.set_current_action(str(action or ""), float(until or 0),
                                      place="家", zone="home", category="life")
        except Exception:
            try:
                self.p.db.kv_set("current_action", {"action": str(action or ""),
                                                      "until": float(until or 0),
                                                      "place": "家", "zone": "home",
                                                      "category": "life"})
            except Exception as exc:
                logger.debug("[amo] optional step failed: %r", exc)

    def _action(self, now=None):
        data = self._kv_get("micro_event:action", {}) or {}
        if not isinstance(data, dict):
            return None
        try:
            if float(data.get("until") or 0) <= (now or _now()).timestamp():
                return None
        except Exception:
            return None
        return data

    def lifecycle_tick(self, now=None):
        """推进外卖/吃夜宵/刚吃完/洗漱缓冲，不靠下一条消息。"""
        now = now or _now()
        data = self._kv_get("micro_event:action", {}) or {}
        if not isinstance(data, dict) or not data.get("key"):
            return
        stage = str(data.get("stage") or "")
        until = float(data.get("until") or 0)
        if now.timestamp() >= until:
            if stage == "waiting":
                food = str(data.get("food") or "夜宵")
                self._set_action("正在吃%s，刚拆开外卖" % food, now.timestamp() + 1200,
                                 data.get("key"), "eating", {"food": food})
                self._feed(food)
            elif stage == "eating":
                self._set_action("刚吃完夜宵，还没收拾", now.timestamp() + 600,
                                 data.get("key"), "done", {"food": data.get("food")})
            elif stage == "buffer":
                self._kv_set("micro_event:action", {})
                try:
                    self.p.set_current_action("", 0)
                except Exception as exc:
                    logger.debug("[amo] optional step failed: %r", exc)
            else:
                self._kv_set("micro_event:action", {})

    def _feed(self, food):
        try:
            self.p.add_hunger("amo", 45)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)
        try:
            hunger = getattr(self.p, "hunger", None)
            if hunger is not None:
                hunger.mark_meal(food, cooldown_hours=2)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)

    def _mount_action(self, event, now, cash):
        key = str(event.get("key") or "")
        action = ""
        until = 0
        stage = "doing"
        extra = {"food": event.get("food") or ""}
        if key == "late_snack":
            food = str(event.get("food") or "夜宵")
            if event.get("delivery"):
                action = "刚点了%s外卖，正躺在沙发上等，外卖还要 30 分钟" % food
                until = now.timestamp() + 1800
                stage = "waiting"
                extra["deliver_at"] = until
            else:
                action = "正在吃%s" % food
                until = now.timestamp() + 1800
                stage = "eating"
                self._feed(food)
            self._kv_set("micro_event:food_cooldown_until", now.timestamp() + FOOD_COOLDOWN_SECONDS)
            self._kv_set("late_snack_last", {"day": self._today(now), "food": food,
                                             "ts": now.timestamp()})
        elif key == "mall_shopping":
            action, until = "正在商场逛街", now.timestamp() + 5400
        elif key == "drama":
            action, until = "正在追剧", now.timestamp() + 3600
        elif key == "shower":
            action, until = "正在洗澡", now.timestamp() + 1800
        elif key == "cook_fail":
            action, until = "正在收拾厨房", now.timestamp() + 1800
        if action:
            self._set_action(action, until, key, stage, extra)

    def note_user_text(self, text, now=None):
        now = now or _now()
        action = self._action(now)
        if not action:
            return
        body = str(text or "")
        if any(word in body for word in ("别吃", "不要吃", "太晚", "长胖", "伤胃")):
            self._kv_set("micro_event:interference", {"until": now.timestamp() + 600,
                                                      "text": body[:60]})
        if any(word in body for word in ("吃完睡", "早点睡", "去睡", "睡觉", "睡了")):
            if action.get("stage") in ("eating", "done", "waiting"):
                self._set_action("刚洗漱完，躺在床上刷手机", now.timestamp() + 1200,
                                 action.get("key"), "buffer", {"food": action.get("food")})

    def _morning_penalty(self, now):
        data = self._kv_get("late_snack_last", {}) or {}
        if not isinstance(data, dict) or not data.get("day"):
            return ""
        try:
            day = _dt.date.fromisoformat(str(data["day"]))
        except Exception:
            return ""
        if (now.date() - day).days != 1 or int(now.hour) >= 12:
            return ""
        mark = "late_snack_penalty:%s" % now.date().isoformat()
        if self._kv_get(mark, False):
            return ""
        self._kv_set(mark, True)
        food = str(data.get("food") or "夜宵")
        try:
            self.p.state.nudge_once("late_snack:penalty:%s" % now.date().isoformat(),
                                    懊悔=8, 郁闷=4)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)
        return ("【昨晚的代价】昨晚你吃了%s，今天早上有点懊悔，觉得脸圆了一圈/肠胃不太舒服；"
                "可以自然提一句，但别演成大病。" % food)

    async def _share(self, event, umo, uid, profile, snapshot=None):
        """分享欲 >50：主动发一条关于这件小事的消息（带当前状态快照）。"""
        text = str(event.get("text") or "")
        if not text or not umo:
            return False
        try:
            snapshot = snapshot or build_snapshot(self.p)
        except Exception:
            snapshot = {}
        state_block = ""
        try:
            state_block = snapshot_text(snapshot)
        except Exception:
            state_block = ""
        persona = ""
        try:
            persona = await self.p.llm.persona(umo)
        except Exception:
            persona = ""
        facts_block = ""
        try:
            facts_block = "\n".join(_facts_lines(self.p))
        except Exception:
            facts_block = ""
        prompt = ((state_block + "\n") if state_block else "") + (
            (facts_block + "\n") if facts_block else ""
        ) + (
            "刚刚发生了一件小事：%s。你现在想主动跟老公分享，"
            "1-2 句，像发微信，带点你的情绪。"
            "这件事必须发生在【当前状态】里的地点和身体状态里；"
            "如果这件小事和此刻对不上，就按此刻改写（比如把“工位上”改成“书桌前”），"
            "如果这件小事和【绝对事实】或【今天的账单】对不上（比如日程写的是在家做饭，"
            "这件小事却是点外卖），一律按【绝对事实】说，绝不许说自己没做过的事、没花过的钱；"
            "不许说自己不在的地方，也不许把以前的事说成刚刚。只输出要说的话。"
            % text
        )
        try:
            line = await self.p.llm.chat(umo, persona or "你就是这个角色本人。", prompt, limit=80)
        except Exception:
            line = ""
        line = str(line or "").strip()
        if not line:
            return False
        guard = getattr(self.p, "_guard_zone_reply", None)
        if callable(guard):
            try:
                line = str(await guard(line, umo, snapshot=snapshot) or "").strip()
            except Exception as exc:
                logger.debug("[amo] optional step failed: %r", exc)
        if not line:
            return False
        try:
            line, _claim_changed, _claim_why = _fact_guard(line, self.p)
            if _claim_changed:
                logger.info("[CLAIM_GUARD] 分享消息里编出来的消费已改掉（%s）" % _claim_why)
        except Exception as exc:
            logger.debug("[amo] 分享消息消费校验失败: %s" % exc)
        if not str(line or "").strip():
            return False
        try:
            sent = await self.p._send_proactive_lines(profile or {}, umo, line)
            if sent:
                self.p._mark_bot_initiated(sent)
                return True
        except Exception:
            return False
        return False

    # ---------- 主流程 ----------
    async def tick(self, umo="", uid="", rng=None, now=None):
        now = now or _now()
        if not self.p.cfg_bool("micro_event_enabled", True):
            return {"ok": False, "reason": "disabled"}
        self.lifecycle_tick(now)
        action = self._action(now)
        if action:
            return {"ok": False, "reason": "busy", "action": action}
        scene = "group" if ":GroupMessage:" in str(umo or "") else "private"
        try:
            snapshot = build_snapshot(self.p, now)
            ctx = build_state_context(self.p, now)
        except Exception:
            snapshot, ctx = {}, {}
        try:
            cash = int(self.p.wallet.balance())
        except Exception:
            cash = 0
        today = self._today(now)
        social_last = self._social_last_day()
        if not social_last:
            # 第一次跑：从今天开始算，连续 3 天没社交才强制保底
            self._kv_set("micro_event:social_day", today)
            social_last = today
        force_social = False
        try:
            if (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(social_last)).days >= 3:
                force_social = True
        except Exception:
            force_social = False
        last_record = self._kv_get("micro_event:last", {}) or {}
        last_key = str(last_record.get("key") or "") if isinstance(last_record, dict) else ""
        food_locked = now.timestamp() < float(self._kv_get("micro_event:food_cooldown_until", 0) or 0)
        meal_done = self._meal_done(now)
        event = pick_event(now, cash, self._recent_keys(), scene=scene, rng=rng,
                           force_social=force_social, last_key=last_key,
                           food_locked=food_locked, snapshot=snapshot, state=ctx,
                           meal_done=meal_done)
        if event is not None and event.get("key") == "late_snack":
            event = dict(event)
            if cash > 100000:
                event.update(cost=5000, text="点了份烧烤当夜宵", food="烧烤", delivery=True)
            elif cash < 10000:
                event.update(cost=600, text="在便利店买了桶泡面当夜宵", food="泡面",
                             mood={"委屈": 5, "满足": 3})
            else:
                event.update(cost=1500, food="夜宵", delivery=True)
        if event is None:
            empty = self._empty_ticks() + 1
            self._set_empty_ticks(empty)
            if empty >= 3 and scene == "private":
                event = {"key": "bored:%s" % today, "text": "一整天都没什么新鲜事，有点无聊",
                         "mood": {"郁闷": 3}, "cost": 0, "scene": "private"}
                self._set_empty_ticks(0)
            else:
                return {"ok": False, "reason": "nothing", "empty": empty}
        if not await self._pay(event, now):
            return {"ok": False, "reason": "insufficient"}
        delta = self._mood(event, now)
        self._remember_key(event["key"], now)
        if event.get("social"):
            self._kv_set("micro_event:social_day", today)
        await self._write_memory(event, ctx, umo, uid)
        self._mount_action(event, now, cash)
        record = {"text": str(event.get("text") or ""), "ts": now.timestamp(),
                  "day": today, "key": event["key"], "mood": delta,
                  "cost": int(event.get("cost") or 0), "scene": scene}
        if last_record:
            self._kv_set("micro_event:prev", last_record)
        self._kv_set("micro_event:last", record)
        logger.info("[amo] 微型事件：%s（cost=%d, bias=%s）" % (
            record["text"], record["cost"], self._bias() or "-"))
        drive = min(100, self.share_drive() + (5 if any(value > 0 for value in delta.values()) else 0))
        self._kv_set("micro_event:share_drive", drive)
        shared = False
        if drive > 50 and scene == "private" and not is_quiet(now.hour, now.minute):
            profile = {}
            try:
                profile = self.p.db.get_profile(uid) if uid else {}
            except Exception:
                profile = {}
            shared = await self._share(event, umo, uid, profile, snapshot=snapshot)
            if shared:
                # 分享出去就把攒的那份清回基准，下一轮重新攒；
                # 不重置的话每件小事都 +5，几天后永远 >50、每件小事都要发消息。
                self._kv_set("micro_event:share_drive", 45)
        return {"ok": True, "event": record, "share_drive": drive, "shared": shared}

    def injection(self, now=None):
        """聊天时注入当前动作（最高优先级）+ 最近小事 + 当天情绪基线。"""
        now = now or _now()
        lines = []
        action = self._action(now)
        if action:
            stage = str(action.get("stage") or "")
            text = str(action.get("action") or "")
            if stage == "waiting":
                line = ("【当前动作·最高优先级】你刚点完外卖：%s。外卖还没到，你正躺在沙发上等；"
                        "严禁跳过这个动作直接说吃完了/去睡觉。" % text)
            elif stage == "eating":
                line = ("【当前动作·最高优先级】%s。你必须围绕吃东西回复，严禁快进到睡觉或下一个状态。"
                        % text)
            elif stage == "buffer":
                line = "【当前动作】%s，还有点困，别突然精神满满。" % text
            else:
                line = "【当前动作】%s。" % text
            lines.append(line)
        penalty = self._morning_penalty(now)
        if penalty:
            lines.append(penalty)
        interference = self._kv_get("micro_event:interference", {}) or {}
        if isinstance(interference, dict) and float(interference.get("until") or 0) > now.timestamp():
            lines.append("【他的干涉】他刚说“%s”。结合你的分享欲和听话度选："
                         "听话就说“好吧，那我不吃了，听老公的”，嘴馋就说“可是我真的好饿，就吃一小口行不行”。"
                         % str(interference.get("text") or "别吃了"))
        record = self._kv_get("micro_event:last", {}) or {}
        try:
            ts = float(record.get("ts") or 0) if isinstance(record, dict) else 0
        except Exception:
            ts = 0
        if isinstance(record, dict) and record.get("text") and now.timestamp() - ts <= 6 * 3600:
            line = "【刚发生的小事】" + str(record.get("text") or "")
            prev = self._kv_get("micro_event:prev", {}) or {}
            try:
                prev_ts = float((prev or {}).get("ts") or 0)
            except Exception:
                prev_ts = 0
            if (isinstance(prev, dict) and prev.get("text")
                    and prev.get("key") != record.get("key")
                    and now.timestamp() - prev_ts <= 12 * 3600):
                line += "（上一件小事：%s；如果这次是新事件，要接着上一件写，不要突然跳场景）" % str(prev.get("text"))
            if self._bias() == "low":
                line += "（今天情绪基线偏低，别突然元气满满）"
            lines.append(line)
        return "\n".join(lines)
