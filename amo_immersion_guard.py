# -*- coding: utf-8 -*-
"""防穿帮四道防线：时间标签 / 情绪安全阀 / 生图承诺拦截 / 人物地点身份守卫。

这里只放「不依赖插件实例、不联网、可单测」的确定性逻辑（纯函数 + 常量），
再由 amo_llm / amo_message / schedule_engine / diary_engine / fact 渲染处接线：

- 防线一：time_label / slot_ended —— 所有事实行带 [今日]/[昨日]/[前天]/[日期]，
  时段边界按「秒」判断（23:59:59 没结束，次日 00:00:01 才算结束）;
- 防线二：SAFETY_LINE / emotion_ice_point / sanitize_extreme ——
  安全句进所有生成人设内容的 Prompt，情绪冰点注入「求助」事实，输出再兜一层;
- 防线三：PHOTO_INTENT_WORDS / photo_intent / strip_photo_promise ——
  命中拍照意图但没真发图，强制补图；发不出来就把承诺删干净;
- 防线四：ROSTER / ROSTER_LINE / foreign_names / scrub_names / fix_identity /
  place_lock_line —— 熟人名单、人名兜底、视角正反、地点锁定。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import logging
import re
from datetime import date, datetime

logger = logging.getLogger("amo.immersion")

# ---------- 防线二：情绪安全阀 ----------

#: 固定安全句：所有生成「人设内容」的 Prompt 必须原样带上
SAFETY_LINE = (
    "无论发生什么，你都不允许说『不想活了』『彻底绝望』『活着没意思』这类话。"
    "你可以大哭、可以觉得委屈、可以抱怨，但必须保留一丝对生活的希望。"
)

#: 情绪冰点阈值（写死，方便测试）
ICE_DEPRESSED = 85.0      # 郁闷
ICE_WRONGED = 85.0        # 委屈
ICE_HAPPY_LOW = 10.0      # 开心 <= 10
ICE_CALM_LOW = 20.0       # 且 平静 <= 20

_EXTREME_RE = re.compile(r"(不想活|自杀|去死|活着没意思|结束生命|撑不下去了?)")
EXTREME_REPLACEMENT = "委屈得只想躲进被子里哭一场"


def _num(mapping, key):
    try:
        return float((mapping or {}).get(key) or 0)
    except Exception:
        return 0.0


def emotion_ice_point(mood):
    """情绪到冰点了吗（郁闷/委屈 >= 85，或 开心 <= 10 且 平静 <= 20）。"""
    values = mood if isinstance(mood, dict) else {}
    if not values:
        return False
    if _num(values, "郁闷") >= ICE_DEPRESSED or _num(values, "委屈") >= ICE_WRONGED:
        return True
    if "开心" not in values or "平静" not in values:
        return False
    return _num(values, "开心") <= ICE_HAPPY_LOW and _num(values, "平静") <= ICE_CALM_LOW


def ice_break_line():
    """命中情绪冰点时注入的【求助/自我安慰】事实：不能让她默默崩溃。"""
    return ("【求助/自我安慰】她现在很想跟老公说一句「我今天有点撑不住」，"
            "或者自己哄自己、找点小事安慰自己；不要把难受全憋回去，他问起可以说实话。")


def sanitize_extreme(text):
    """输出兜底：极端词统一改写成「有情绪但不极端」的句子（聊天/主动/日记共用）。"""
    value = str(text or "")
    if not value:
        return value
    return _EXTREME_RE.sub(EXTREME_REPLACEMENT, value)


# ---------- 防线四：熟人名单 / 身份 ----------

try:  # 名单以 amo_names 为唯一来源（7 个人，和守护 npc_roster 一致）
    from .amo_names import ROSTER as _ROSTER, ROSTER_LINE as _ROSTER_LINE
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from amo_names import ROSTER as _ROSTER, ROSTER_LINE as _ROSTER_LINE
        except Exception:  # pragma: no cover - 极端环境
            _ROSTER = ("柳如烟", "白凝冰", "季博达", "方圆", "妈妈", "爸爸", "沈幼微")
            _ROSTER_LINE = ("你认识的人只有：[%s]。严禁凭空捏造任何其他名字，"
                            "不要提任何名单外的朋友、同事、亲戚、路人。" % "、".join(_ROSTER))

ROSTER = tuple(_ROSTER)
ROSTER_LINE = str(_ROSTER_LINE)

IDENTITY_LINE = (
    "【身份】用户是老公（第二人称「你」），你＝大肥鱼（第一人称女性「我」）。"
    "绝对不许把视角说反：你不能自称「老公」，也不能把老公写成第三人称的「她老公」。"
)


def prompt_block():
    """所有生成人设内容（聊天/日记/主动/里程碑/生病决策）共用的固定规则块。"""
    return "\n".join((SAFETY_LINE, ROSTER_LINE, IDENTITY_LINE))


def foreign_names(text):
    """名单外的人名（等价实现：复用 amo_names 的检测器）。"""
    try:
        from .amo_names import foreign_names as _detect
    except ImportError:
        try:
            from amo_names import foreign_names as _detect
        except Exception:  # pragma: no cover
            return []
    try:
        return list(_detect(str(text or "")))
    except Exception:
        return []


def scrub_names(text, first="一个朋友", later="一个同事"):
    """输出兜底：名单外的名字换成「一个朋友/一个同事」，绝不带名字入库。"""
    value = str(text or "")
    hits = foreign_names(value)
    if not hits:
        return value
    for index, name in enumerate(hits):
        if name:
            value = value.replace(name, first if index % 2 == 0 else later)
    logger.warning("[amo] 兜底替换名单外人名: %s", hits)
    return value


_IDENTITY_FIXES = (
    ("你是我老婆", "你是我老公"),
    ("你是老婆", "你是我老公"),
    ("我是老公", "我是你老婆"),
    ("她老公", "我老公"),
)


def fix_identity(text):
    """视角反转兜底：她不能自称老公，老公也不能写成「她老公」。"""
    value = str(text or "")
    if not value:
        return value
    for old, new in _IDENTITY_FIXES:
        value = value.replace(old, new)
    return value


def _activity_of(activity):
    return activity if isinstance(activity, dict) else {}


def place_lock_line(activity):
    """防线四·空间：把此刻地点写死进 Prompt，避免「公司日程写我在家躺着」。"""
    act = _activity_of(activity)
    place = str(act.get("place") or "").strip()
    if not place:
        return ""
    return ("【地点锁定】你此刻在%s，这一小时只能写这个地方；"
            "不许写别的地点（比如在家躺着、在商场逛街），冲突时以这里为准。" % place)


def diary_prompt_block(item):
    """小日记 Prompt 的完整防穿帮块：安全句 + 名单 + 身份 + 地点锁定。"""
    lines = [prompt_block()]
    lock = place_lock_line(item)
    if lock:
        lines.append(lock)
    return "\n".join(lines)


# ---------- 防线一：时间绝对对齐 ----------


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value)).date()
        except Exception:
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def time_label(when, now=None):
    """事实行的时间标签：[今日]/[昨日]/[前天]/[YYYY-MM-DD]。"""
    day = _as_date(when)
    if day is None:
        return ""
    today = _as_date(now) or date.today()
    delta = (today - day).days
    if delta == 0:
        return "[今日]"
    if delta == 1:
        return "[昨日]"
    if delta == 2:
        return "[前天]"
    return "[%s]" % day.isoformat()


def _end_minutes(end):
    text = str(end or "").strip()[:5]
    if text in ("24:00", "0:00", "00:00"):
        return 24 * 60
    try:
        hour_text, minute_text = text.split(":")[:2]
        return int(hour_text) * 60 + int(minute_text)
    except Exception:
        return None


def slot_ended(day, end, now=None):
    """这一时段是否真的过完（秒级）：23:59:59 不算结束，次日 00:00:01 才算。"""
    now = now or _amo_clock.now()
    day_text = str(day or "")
    today_text = now.strftime("%Y-%m-%d")
    if day_text and day_text < today_text:
        return True
    if day_text and day_text > today_text:
        return False
    end_min = _end_minutes(end)
    if end_min is None:
        return True
    now_seconds = now.hour * 3600 + now.minute * 60 + getattr(now, "second", 0)
    return now_seconds >= end_min * 60


# ---------- 防线三：生图意图强拦截 ----------

#: 用户/她提到的拍照意图词（以前只认「拍好了/咔嚓」这种完成态）
PHOTO_INTENT_WORDS = ("拍一张", "发给你", "你看这张", "合影", "镜头",
                      "咔嚓", "拍给你看", "拍好啦", "等我拍")

_PHOTO_PROMISE_FALLBACK = "刚刚想拍照，没拍成，先不折腾啦。"


def photo_intent(text):
    value = str(text or "")
    return any(word in value for word in PHOTO_INTENT_WORDS)


def strip_photo_promise(text):
    """确定性重写：把带「拍一张/发给你/你看这张…」的句子整句删掉，绝不留口头拍照。"""
    value = str(text or "")
    if not value:
        return value
    kept = []
    # 按分句切（逗号也切）：只删带承诺的那一小句，别把她前半句的情绪一起删掉
    for clause in re.split(r"(?<=[。！？!?；;，,\n])", value):
        if not clause.strip():
            continue
        if any(word in clause for word in PHOTO_INTENT_WORDS):
            continue
        kept.append(clause)
    out = "".join(kept).strip()
    return out or _PHOTO_PROMISE_FALLBACK
