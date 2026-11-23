# -*- coding: utf-8 -*-
"""ProactiveMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

import threading
from typing import Any, TYPE_CHECKING

try:  # 配置化骨架：阈值在 infra/config/amo_thresholds.json
    from .infra.config import load_config
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from infra.config import load_config


def _burst_guard_seconds() -> int:
    """连续爆发保护的窗口秒数；坏值回退 90。"""
    data = load_config("amo_thresholds", {"proactive_burst_guard_seconds": 90},
                       lambda value: isinstance(value, dict) and bool(value))
    try:
        value = int(data.get("proactive_burst_guard_seconds", 90))
    except (TypeError, ValueError):
        return 90
    return value if value > 0 else 90


PROACTIVE_BURST_GUARD_SECONDS = _burst_guard_seconds()
_PROACTIVE_SLOT_LOCK = threading.Lock()
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .observability import log_event
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .observability import log_event
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from observability import log_event

import asyncio
import difflib
import random
import re
import unicodedata
from datetime import timedelta
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        _greeting_targets, _now, _within_minutes, in_minute_range, logger, minutes_now, now_ts, parse_hhmm, quiet_now, to_float, to_int, today_str,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            _greeting_targets, _now, _within_minutes, in_minute_range, logger, minutes_now, now_ts, parse_hhmm, quiet_now, to_float, to_int, today_str,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                _greeting_targets, _now, _within_minutes, in_minute_range, logger, minutes_now, now_ts, parse_hhmm, quiet_now, to_float, to_int, today_str,
            )


try:  # 回复守卫流水线（Phase 6）：主动消息分段前的守卫也走同一张阶段表
    from . import amo_reply_pipeline as _reply_pipeline
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_reply_pipeline as _reply_pipeline

try:  # 唯一在场/沉默策略（2026-11-22 架构收口）
    from . import amo_presence as _presence
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_presence as _presence


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


#: 一轮主动决策最长允许拖多久：超过就丢弃。LLM 慢时（2026-11-19 实测 11 分钟）
#: 文案还锚在生成那一刻，发出去就是「迟到的早安」，比沉默更糟。
PROACTIVE_STALE_SECONDS = 120
#: 追问（「喂喂，人呢？」）之后先静默 2 分钟，别紧接着又开新话题。
PROACTIVE_AFTER_FOLLOWUP_SECONDS = 120
#: 主动消息主题去重：最近 6 小时的主动文本相似度 ≥ 阈值就当作复读。
PROACTIVE_THEME_DEDUP_SECONDS = 6 * 3600
PROACTIVE_THEME_THRESHOLD = 0.55  # 实测：复读 0.571，正常不同话题 ≤0.47
#: 短句按字面相似度容易误判（「好，等你忙完」 vs 「你忙完了没」），要求更高才拦。
PROACTIVE_SHORT_CHARS = 6
PROACTIVE_SHORT_THRESHOLD = 0.8
#: 日程提醒的提前量：太早提醒没意义，太晚又来不及准备。
SCHEDULE_REMIND_MIN_MINUTES = 10
SCHEDULE_REMIND_MAX_MINUTES = 60
#: 迟到窗口（分钟）：日程已经开始超过这么久就不要再发“快到了”的提醒。
SCHEDULE_REMIND_LATE_MINUTES = 5
#: 单飞占位 TTL：防止两个主动决策同时抢到同一条日程。
SCHEDULE_REMIND_CLAIM_TTL = 180.0



def _proactive_plain(text):
    """只留中文字符：标点/空格/表情不参与相似度。"""
    return re.sub(r"[^\u4e00-\u9fff]", "", str(text or ""))


#: 同一件小事换个说法也算重复：按主题词兜底（比例相似度抓不住「柳如烟讲笑话」->「同事讲笑话」）。
PROACTIVE_TOPIC_WORDS = (
    "笑话", "咖啡", "奶茶", "午饭", "晚饭", "早餐", "天气", "下雨", "加班", "下班",
    "电影", "综艺", "蛋糕", "冰淇淋", "睡衣", "晚霞", "地铁", "公交", "火锅",
    "外卖", "快递", "感冒", "头痛", "猫", "狗", "桂花",
)


def _schedule_minute(value):
    """把 "HH:MM" 转成当日分钟；格式不对返回 -1。"""
    body = str(value or "").strip()
    if not re.match(r"^\d{1,2}[:：]\d{2}$", body):
        return -1
    hour, minute = re.split(r"[:：]", body)
    return int(hour) * 60 + int(minute)


_SCHEDULE_OTHER_PLACES = ("机场", "医院", "餐厅", "公司", "商场", "学校", "地铁")
_EXTRA_EVENT_WORDS = ("表姐", "拿药", "复查", "开会", "面试", "取快递")


def _ground_schedule_text(text, fact):
    """提醒必须带这条日程的时间/地点/事件；带错时间或别的日程直接不发。"""
    body = str(text or "")
    if not isinstance(fact, dict) or not body.strip():
        return body
    want_time = str(fact.get("time") or "").replace("：", ":")
    want_place = str(fact.get("place") or "")
    want_event = str(fact.get("event") or "")
    times = [item.replace("：", ":") for item in re.findall(r"\d{1,2}[:：]\d{2}", body)]
    if want_time and times and want_time not in times:
        return ""
    if want_place:
        for place in _SCHEDULE_OTHER_PLACES:
            # 目的地写在事件里（如「路上/挤地铁去公司」）不算别的地点
            if place in body and place not in want_place and place not in want_event:
                return ""
    if want_event:
        for word in _EXTRA_EVENT_WORDS:
            if word in body and word not in want_event:
                return ""
    return body


def _schedule_event_key(row):
    """日程提醒幂等键：活动｜地点｜开始时间。

    2026-11-22 P0：旧键只有「活动｜地点」，同一天同活动同地点出现两次，
    第二次会被误判成“已经提醒过”而漏提醒；改期后也会撞旧键。开始时间必须进键。
    """
    return "%s|%s|%s" % (str(row.get("activity") or ""),
                         str(row.get("place") or ""),
                         str(row.get("start") or "").replace("：", ":"))


def _schedule_rows(db, day):
    """读某天日程；接口不存在或读取失败时返回 None（调用方按「不误杀」处理）。"""
    getter = getattr(db, "get_schedule", None)
    if not callable(getter):
        return None
    try:
        rows = getter(str(day or "")) or []
    except Exception:
        return None
    return [dict(row) for row in rows if isinstance(row, dict)]


def _schedule_version(db, day):
    getter = getattr(db, "schedule_version", None)
    if not callable(getter):
        return 0
    try:
        return int(getter(str(day or "")) or 0)
    except Exception:
        return 0


def _reminder_claim_key(day, key):
    return "schedule_reminder_claim:%s:%s" % (str(day), str(key))


def _claim_schedule_reminder(db, day, key):
    """发送前占位：同一条日程只能有一轮主动决策在生成提醒。"""
    claim_key = _reminder_claim_key(day, key)
    token = "%s|%s|%.3f" % (str(day), str(key), now_ts())
    try:
        current = db.kv_get(claim_key, {}) or {}
        if (isinstance(current, dict) and to_float(current.get("at"), 0)
                and now_ts() - to_float(current.get("at"), 0) < SCHEDULE_REMIND_CLAIM_TTL):
            return ""
        db.kv_set(claim_key, {"token": token, "at": now_ts()})
        mine = db.kv_get(claim_key, {}) or {}
        if isinstance(mine, dict) and str(mine.get("token") or "") == token:
            return token
    except Exception:  # cleanup: 占位失败按“没抢到”，宁可不提醒也不重复提醒
        return ""
    return ""


def release_schedule_reminder(db, day, key, token=""):
    """发送失败/作废时释放占位，让下一轮还能提醒。"""
    claim_key = _reminder_claim_key(day, key)
    try:
        current = db.kv_get(claim_key, {}) or {}
        if not token or (isinstance(current, dict)
                         and str(current.get("token") or "") == str(token)):
            db.kv_set(claim_key, {})
    except Exception:  # cleanup: 释放失败最多等 TTL 过期
        pass


def schedule_reminder_fact(db, now=None, day="", min_minutes=SCHEDULE_REMIND_MIN_MINUTES,
                           max_minutes=SCHEDULE_REMIND_MAX_MINUTES, claim=False):
    """挑一条 10~60 分钟后开始的日程做提醒；没有就返回 {}。

    只认还没提醒过、不是睡觉的那一条；返回的 version 用来在发送前复核
    「取消/改期」，避免拿旧快照提醒。claim=True 时在返回前原子占位（单飞）。
    """
    now = now or _now()
    day = str(day or today_str())
    minute = int(now.hour) * 60 + int(now.minute)
    candidates = []
    for row in _schedule_rows(db, day) or []:
        activity = str(row.get("activity") or "").strip()
        start = _schedule_minute(row.get("start"))
        if not activity or start < 0 or "睡" in activity:
            continue
        ahead = start - minute
        if ahead < min_minutes or ahead > max_minutes:
            continue
        key = _schedule_event_key(row)
        try:
            if db.kv_get("schedule_reminded:%s:%s" % (day, key), False):
                continue
        except Exception:  # cleanup: 读不到已提醒标记按未提醒
            pass
        candidates.append((start, {
            "day": day, "key": key, "version": _schedule_version(db, day),
            "time": str(row.get("start") or ""), "place": str(row.get("place") or ""),
            "event": activity, "zone": str(row.get("zone") or ""),
        }))
    candidates.sort(key=lambda pair: pair[0])
    for _start, fact in candidates:
        if not claim:
            return fact
        token = _claim_schedule_reminder(db, day, fact["key"])
        if token:
            fact["claim_token"] = token
            return fact
    return {}


def schedule_fact_current(db, fact, now=None, late_window=True):
    """发送/追问前复核：日程还在、开始时间没改、还没迟到；读不到就放行（不误杀）。

    late_window=False 用于「追问」：追问一定发生在提醒之后，日程本身可能已经开始，
    这时只要这条日程还在、没改期就该允许追问；迟到窗口只约束「快到了」的提醒本身。
    """
    if not isinstance(fact, dict) or not str(fact.get("key") or ""):
        return True
    rows = _schedule_rows(db, str(fact.get("day") or ""))
    if rows is None:
        return True
    same = [row for row in rows
            if _schedule_event_key(row) == str(fact.get("key"))]
    if not same:
        # 跨版本兼容：旧版 pending 的键只有「活动｜地点」（没有开始时间），
        # 升级后不该把还在的日程当成取消了；开始时间仍要对得上。
        parts = str(fact.get("key") or "").split("|")
        if len(parts) == 2:
            same = [row for row in rows
                    if str(row.get("activity") or "") == parts[0]
                    and str(row.get("place") or "") == parts[1]]
    if not same:
        return False
    # 同一天同一活动可能有两段（10:00/17:00）：任一段对得上就放行
    if not any(str(row.get("start") or "") == str(fact.get("time") or "")
               for row in same):
        return False
    if not late_window:
        return True
    # 迟到窗口：生成/发送之间拖过了开始时间，就别再提醒“快到了”。
    moment = now or _now()
    try:
        start = _schedule_minute(fact.get("time"))
        if (start >= 0
                and str(fact.get("day") or today_str()) == moment.strftime("%Y-%m-%d")):
            now_minute = int(moment.hour) * 60 + int(moment.minute)
            if now_minute > start + SCHEDULE_REMIND_LATE_MINUTES:
                logger.info("[amo] 日程提醒已迟到 %d 分钟，放弃发送：%s",
                            now_minute - start, str(fact.get("event") or "")[:30])
                return False
    except (TypeError, ValueError):  # cleanup: 兜底/降级，保持原行为
        pass
    return True


#: 同一条消息里两个问句核心相似到这个程度，就按「同一个追问」合并成一句。
SYNONYM_QUESTION_THRESHOLD = 0.6
#: 按句末标点切句，保留标点。
_QUESTION_CLAUSE_RE = re.compile(r"(?<=[。！？!?~～\n])")
#: 问句末尾的语气词/否定词，剥掉后比较「问的是什么」。
_QUESTION_TAIL_RE = re.compile(r"(?:吗|呢|嘛|么|吧|呀|啊|哈|啦|哦|没|没有|了|的|不)+$")
#: 带可核对答案（时间/地点/明确回答）的句子：去重整条删空时不能被静默吞掉。
_ANSWER_CUE_RE = re.compile(
    r"\d{1,2}[:：]\d{2}|在家|在公司|在路|到了|到家|不在|没有|吃饭|吃完饭|好的|是的"
    r"|医院|商场|餐厅|学校|机场|地铁|公交"
)


def _question_core(text):
    """问句核心：去掉标点、语气词和开头的人称，只留下「问的那件事」。"""
    body = unicodedata.normalize("NFKC", str(text or "")).lower()
    body = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", body)
    for _ in range(4):
        trimmed = _QUESTION_TAIL_RE.sub("", body)
        if trimmed == body:
            break
        body = trimmed
    return re.sub(r"^[你您我他她它]+", "", body)


def _is_question_clause(part):
    """一句是不是在问：有问号，或以「吗/呢/嘛/么/没」收尾。"""
    body = str(part or "").strip()
    if not body:
        return False
    if re.search(r"[?？]", body):
        return True
    return bool(re.search(r"(?:吗|呢|嘛|么|没|没有)[。！!~～\s]*$", body))


def _same_question(left, right):
    """两个问句核心是不是同一个意思：相等、包含或足够相似。"""
    if len(left) < 2 or len(right) < 2:
        return False
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) >= 2 and shorter in longer and len(shorter) * 2 >= len(longer):
        return True
    return difflib.SequenceMatcher(None, left, right).ratio() >= SYNONYM_QUESTION_THRESHOLD


def merge_synonym_questions(text):
    """同一条消息里连问两遍同一个意思，只留第一句。

    「你忙完了没？忙完了吗？」→「你忙完了没？」
    两句问的是不同的事（「到家了吗？下班了吗？」）时不动。
    """
    body = str(text or "")
    if not body.strip():
        return body
    parts = _QUESTION_CLAUSE_RE.split(body)
    if len(parts) < 2:
        return body
    kept: list = []
    cores: list = []
    changed = False
    for part in parts:
        if not part:
            continue
        if not _is_question_clause(part):
            kept.append(part)
            continue
        core = _question_core(part)
        if len(core) < 2:
            kept.append(part)
            continue
        if any(_same_question(core, old) for old in cores):
            changed = True
            continue
        cores.append(core)
        kept.append(part)
    if not changed:
        return body
    return "".join(kept).strip() or body


def _question_cores(text):
    """一句话里所有问句的「核心」：剥掉标点/语气词，只留长度 ≥2 的。"""
    cores = []
    for part in _QUESTION_CLAUSE_RE.split(str(text or "")):
        if not part or not _is_question_clause(part):
            continue
        core = _question_core(part)
        if len(core) >= 2:
            cores.append(core)
    return cores


def followup_repeated(text, previous=()):
    """这次追问是不是又把同一个问题问了一遍（后面多个「呀？」也算）。

    「你忙完了没？」→「你忙完了没？呀？」是同一问；「你吃饭了吗？」不是。
    只跟「她自己问过的话」比，返回被重复的旧句，没重复返回空串。
    """
    cores = _question_cores(text)
    if not cores:
        return ""
    for old in previous or ():
        for old_core in _question_cores(old):
            if any(_same_question(core, old_core) for core in cores):
                return str(old)
    return ""


def has_answer_cue(text):
    """这句话里有没有可核对的答案（时间/地点/吃饭/是否）。"""
    return bool(_ANSWER_CUE_RE.search(str(text or "")))


def keep_answer(original, fixed):
    """去重把整条删空时，带时间/地点/答案的原句要留下，不能被静默吞掉。"""
    if str(fixed or "").strip():
        return fixed
    text = str(original or "").strip()
    if text and has_answer_cue(text):
        return text
    return fixed


def proactive_same_topic(text, recent):
    """新文案和最近主动消息共享一个主题词，就算重复；返回旧文案。"""
    mine = _proactive_plain(text)
    if len(mine) < 4:
        return ""
    for old in recent or []:
        other = _proactive_plain(old)
        if len(other) < 4:
            continue
        for word in PROACTIVE_TOPIC_WORDS:
            if word in mine and word in other:
                return str(old)
    return ""


_CHECKIN_WORDS = ("你在干嘛", "在干嘛呢", "吃饭没", "吃了没", "在忙吗", "忙不忙",
                  "睡了没", "下班没", "到家没", "在不在")


def strip_mixed_checkin(text):
    """主动消息别「一句分享 + 一句查岗」：留分享，删掉不相干的查岗句。"""
    body = str(text or "")
    parts = [part for part in re.split(r"(?<=[。！？!?~～\n])", body) if part.strip()]
    if len(parts) < 2:
        return body
    share = [part for part in parts if not any(word in part for word in _CHECKIN_WORDS)]
    check = [part for part in parts if any(word in part for word in _CHECKIN_WORDS)]
    if not share or not check:
        return body
    # 分享句要有实际内容（不是纯问句），才删查岗；否则原样保留。
    if any(len(_proactive_plain(part)) >= 6 for part in share):
        return "".join(share).strip()
    return body


def proactive_repeated(text, recent, threshold=PROACTIVE_THEME_THRESHOLD):
    """新文案和最近主动消息是不是同一主题；是就返回那条旧文案。"""
    mine = _proactive_plain(text)
    if len(mine) < 4:
        return ""
    for old in recent or []:
        other = _proactive_plain(old)
        if len(other) < 4:
            continue
        ratio = difflib.SequenceMatcher(None, mine, other).ratio()
        limit = threshold
        if min(len(mine), len(other)) <= PROACTIVE_SHORT_CHARS:
            limit = max(limit, PROACTIVE_SHORT_THRESHOLD)
        if ratio >= limit:
            return str(old)
    return ""


#: 普通问候的每小时概率（2026-11-21）：40 基础；开心 60 起每 10 点 +10，封顶 60。
GREET_BASE_PROB = 40
GREET_MOOD_START = 60
GREET_MOOD_STEP = 10
GREET_PROB_CAP = 60


def greet_probability(happy) -> int:
    """普通问候的每小时触发概率（%）。开心只计到 70，所以最高 60%。"""
    try:
        value = float(happy or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value < GREET_MOOD_START:
        return GREET_BASE_PROB
    steps = int((value - GREET_MOOD_START) // GREET_MOOD_STEP) + 1
    return min(GREET_PROB_CAP, GREET_BASE_PROB + steps * GREET_MOOD_STEP)


def _roll_probability(prob) -> bool:
    """按百分比掷骰；抽成函数便于测试 patch。"""
    try:
        return random.random() * 100 < float(prob)
    except (TypeError, ValueError):
        return False
PROACTIVE_NIGHT_START = 30      # 00:30
PROACTIVE_NIGHT_END = 7 * 60    # 07:00

#: 刚发过照片（含面板测试/手动发）后，主动消息闭嘴的时长。
#: 2026-11-17 事故：15:11 面板测试图静默进聊天，15:12 主动消息立刻接话，看起来像同一条日常分享。
PROACTIVE_PHOTO_SILENCE_SECONDS = 10 * 60
#: 照片分享记录的 kv 键（photo_share.note_sent 写，这里只读）。
PHOTO_LAST_SHARE_KEY = "photo_last_share"
#: 过去时间词 → 相对现在的天数；「大前天」不算「前天」。
_PAST_CLAIM_RE = re.compile(r"(?<!大)前天|前晚|昨天|昨晚")
#: 明确指向「此刻」的词：和过去时间词同句出现＝时态混写。
CURRENT_DAY_WORDS = ("现在", "这会儿", "此刻", "目前", "如今", "刚刚", "刚才", "正在",
                     "今天", "今晚", "今儿")
#: 身体/状态反应词：接在过去句后面、又没有「现在」锚点，就会被读成把昨天说成此刻。
CURRENT_REACTION_WORDS = ("出了", "出汗", "感觉", "有点", "还是", "退了", "好多了", "烧退", "缓过来")
#: 分句只看标点，不跨句猜时态。
_CLAUSE_SPLIT_RE = re.compile(r"[，,。！？!?~～；;\n]+")

SHARE_EVENTS = (
    "看到了一只很胖的流浪猫，蹲在便利店门口。",
    "公司下午茶发了一块很好吃的蛋糕。",
    "下班路上看到了很好看的晚霞。",
    "便利店新出了一款冰淇淋，你尝了，很好吃。",
    "路过花店，门口摆了一排小雏菊。",
    "地铁上有人抱着一只很乖的柯基。",
    "楼下的桂花开了，风一吹都是甜的。",
    "今天买了一件新睡衣，软软的。",
)


def night_now(now=None):
    """00:30-07:00 不主动打扰（极端事件除外）。"""
    now = now or _now()
    minute = int(now.hour) * 60 + int(now.minute)
    return PROACTIVE_NIGHT_START <= minute < PROACTIVE_NIGHT_END or minute < 30


def share_drive(silence=0.0, sent_today=0, ignored_today=0):
    """分享欲只由「今天主动几次 / 被冷落几次」决定，不再随沉默时长上涨。

    旧实现越久没聊分越高，直接抵消「沉默 6 小时禁发」：越不理她越催。
    """
    score = 60 - int(sent_today) * 6 - int(ignored_today) * 12
    return int(max(0, min(100, score)))


def missing_score(silence=0.0):
    """想念值只是人设提示，不随沉默时长上升（沉默是禁发信号，不是加分信号）。"""
    return 40


def pick_life_event(day, uid=""):
    key = str(day) + "|" + str(uid)
    return SHARE_EVENTS[sum(ord(ch) for ch in key) % len(SHARE_EVENTS)]


# 严重度在插件里有两种写法：守护插件/老数据是 1~4 的数字，本体存档是 minor/serious 这种词。
# 以前这里直接 int()，遇到 "minor" 抛 ValueError，每次主动检查（5 分钟一次）都会把 tick 整条带崩。
SEVERITY_RANKS = {"none": 0, "healthy": 0, "健康": 0, "minor": 1, "mild": 1, "轻微": 1,
                  "moderate": 2, "medium": 2, "中度": 2, "serious": 3, "severe": 3,
                  "major": 3, "重度": 3, "critical": 4}


def severity_rank(value):
    """把严重度统一成整数档位；认不出来当 0（=不严重）。"""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    rank = SEVERITY_RANKS.get(text.lower())
    if rank is not None:
        return rank
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


#: 红线消息（情绪崩/大病/余额慌）也要避开「他刚说完话」的头几分钟：人在旁边，
#: 不急这几分钟，插话反而像没在听。普通消息用 proactive_user_idle_minutes。
PROACTIVE_FORCED_IDLE_MINUTES = 5


def forced_reason(snap, balance_ratio=None, silence=0.0):
    """必须主动的红线：情绪崩 / 大病 / 余额低于总资产 5%。

    2026-11-22：删掉「超过 8 小时没交流」分支——它会把「一整天没说话」变成
    必须主动找他，直接抵消沉默 6 小时禁发规则。沉默只影响能不能打扰，不是红线。
    2026-11-23：删掉「开心≥90 → 想分享」——开心不是红线；它曾让刚聊完天的一轮
    立刻以 forced 身份跳过「他刚说过话」检查，表现为「刚说完话马上又分享」。
    分享欲回到普通概率 + 30 分钟空闲口径。
    """
    mood = (snap or {}).get("mood") or {}
    if float(mood.get("委屈") or 0) >= 85 or float(mood.get("郁闷") or 0) >= 90:
        return "你现在情绪很崩，一个人很难受，特别想找老公。"
    health = (snap or {}).get("health") or {}
    if health.get("sick") and severity_rank(health.get("severity")) >= 3:
        return "你病得有点重，一个人在家很难受。"
    if balance_ratio is not None and balance_ratio < 0.05:
        return "你的余额只剩总资产的 5% 不到了，有点慌。"
    return ""


def proactive_period(hour):
    if 5 <= hour < 9:
        return "早上"
    if 9 <= hour < 11:
        return "上午"
    if 11 <= hour < 14:
        return "中午"
    if 14 <= hour < 18:
        return "下午"
    if 18 <= hour < 23:
        return "晚上"
    return "深夜"


MICRO_SHARE_TTL = 45 * 60


def _fresh_micro_text(record, now=None):
    """只把 45 分钟内发生的微型事件当「刚发生的小事」；旧事不再拿出来当话题。"""
    if not isinstance(record, dict):
        return ""
    try:
        ts = float(record.get("ts") or 0)
    except Exception:
        ts = 0
    if record.get("text") and now is not None and now - ts <= MICRO_SHARE_TTL:
        return str(record.get("text"))
    return ""


def _recent_said_line(texts):
    items = [str(item).strip() for item in (texts or ()) if str(item).strip()]
    if not items:
        return ""
    return ("你最近已经主动跟他说过：" + "；".join("「%s」" % item[:40] for item in items)
            + "。这次别再重复同一件事、同一个细节。")


def proactive_facts(snap, hunger, now=None, extra=""):
    """把此刻的事实摆给她：几点、天气、心情、饥饿、在做什么；发不发由她自己判断。"""
    now = now or _now()
    hour = int(now.hour)
    activity = snap.get("activity") or {}
    place = str(activity.get("activity") or "在家")
    mood = str(snap.get("dominant") or "").strip() or "平静"
    try:
        hunger_value = int(round(float(hunger)))
    except Exception:
        hunger_value = 80
    parts = [proactive_time_block(now),
             "现在是%s %s。" % (proactive_period(hour), now.strftime("%H:%M"))]
    if hour in (6, 7, 8, 9) and any(word in place for word in ("睡", "起床", "赖床")):
        parts.append("你刚起床。")
    weather_text = str(snap.get("weather_text") or "").strip()
    if weather_text:
        parts.append("外面" + weather_text + "。")
    parts.append("你的心情是%s，饥饿值是%d。" % (mood, hunger_value))
    if place:
        parts.append("你此刻%s。" % place)
    parts.append("你可以选择做自己的事，也可以选择找老公聊天；发不发、发什么，你自己决定。")
    if extra:
        parts.append(str(extra))
    return "".join(parts)


def proactive_skip(text):
    value = str(text or "").strip()
    if not value:
        return True
    if "[SKIP]" in value.upper():
        return True
    return value.strip("[] .。!！").upper() == "SKIP"

def proactive_now_stamp(now=None):
    """【现在：YYYY-MM-DD HH:MM】——主动消息的锚点，日期不能让模型自己猜。"""
    now = now or _now()
    return "【现在：%s】" % now.strftime("%Y-%m-%d %H:%M")


def proactive_tense_rules(now=None):
    """时态规则：过去的事必须写「昨天/前天」，现在的反应只能接当前事实。"""
    now = now or _now()
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    before = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    return (
        "【日期】昨天是%s，前天是%s。" % (yesterday, before)
        + "【时态规则】过去发生的事必须明确写「昨天」或「前天」，不能写成刚刚/现在；"
        "现在/这会儿只能接当前事实（此刻在做什么、心情、天气、地点），"
        "不许把昨天的身体感受（洗澡、出汗、退烧）说成此刻；"
        "同一条消息里不要把过去和现在混在一起说；记忆里没有的昨天/前天的事不要编。"
    )


def proactive_time_block(now=None):
    """主动消息 prompt 顶部的「现在」+ 时态规则。"""
    return proactive_now_stamp(now) + proactive_tense_rules(now)


def split_time_clauses(text):
    """按标点切句（空段丢掉）；时态只看句子，不跨句猜。"""
    return [item.strip() for item in _CLAUSE_SPLIT_RE.split(str(text or "")) if item.strip()]


def past_claim_labels(text):
    """这句话里出现过的过去时间词，归一成「昨天 / 前天」，按出现顺序去重。"""
    labels = []
    for match in _PAST_CLAIM_RE.finditer(str(text or "")):
        label = "前天" if match.group(0) in ("前天", "前晚") else "昨天"
        if label not in labels:
            labels.append(label)
    return labels


def past_day_label(text):
    """这一段说的是哪天（没有过去时间词返回空串）。"""
    labels = past_claim_labels(text)
    return labels[0] if labels else ""


def _has_any(text, words):
    return any(word in text for word in words)


def guard_proactive_time(text, evidence=None, now=None):
    """主动消息发送前的时态/日期校验。

    返回 (可以发的内容, 结论)：
    - ok：没提过去；或提了但 memory/diary 里有那一天、也没有把过去写成此刻；
    - degraded：有问题的句子被删掉，剩下的还能发；
    - blocked：整条都不该发（只剩有问题的句子）。

    evidence 是 {"昨天": bool, "前天": bool}：那一天在 memory/diary 里有没有记录。
    """
    raw = str(text or "").strip()
    if not raw or not past_claim_labels(raw):
        return raw, "ok"
    resolved = {"昨天": bool((evidence or {}).get("昨天")),
                "前天": bool((evidence or {}).get("前天"))}
    kept, dropped = [], []
    for clause in split_time_clauses(raw):
        label = past_day_label(clause)
        if label:
            if _has_any(clause, CURRENT_DAY_WORDS) or not resolved.get(label):
                dropped.append(clause)
                continue
            kept.append(clause)
            continue
        # 过去句之后、没有任何「现在/今天」锚点的身体或状态反应，会被读成昨天的事发生在此刻
        if _has_any(clause, CURRENT_REACTION_WORDS) and not _has_any(clause, CURRENT_DAY_WORDS):
            dropped.append(clause)
            continue
        kept.append(clause)
    if not kept:
        return "", "blocked"
    if dropped:
        return "，".join(kept) + "。", "degraded"
    return raw, "ok"


try:  # 约会会话单一源（Phase 2）
    from . import amo_date_session as _ds
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_date_session as _ds


class ProactiveMixin(_MixinBase):
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _last_proactive_activity: Any
        set_date_fact: Any
    def _migrate_proactive_timing(self):
        try:
            if self.db.kv_get("proactive_timing_migrated", False):
                return
            changes = {}
            if to_int(self.cfg("proactive_followup_minutes", 0), 0) == 30:
                changes["proactive_followup_minutes"] = 5
            if to_int(self.cfg("proactive_no_reply_minutes", 0), 0) == 120:
                changes["proactive_no_reply_minutes"] = 30
            for key, value in changes.items():
                self.config[key] = value
            if changes and hasattr(self.config, "save_config"):
                self.config.save_config()
            self.db.kv_set("proactive_timing_migrated", True)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._migrate_proactive_timing", _amo_exc)

    def _is_proactive_friend(self, profile):
        return str(profile.get("relation") or "普通") in ("熟识", "相恋")

    def _set_proactive_pending(self, uid, kind, text, umo="", schedule=None):
        pending = {
            "sent_at": now_ts(), "kind": kind, "text": str(text or ""),
            # 记下真正发出去的会话：追问要回同一个平台，否则会「飞书问完、QQ 追」。
            "umo": str(umo or ""),
            "followup": False, "mood": False,
        }
        # 日程提醒挂上日程身份：追问前复核，取消/改期后不再拿旧快照催。
        if isinstance(schedule, dict) and str(schedule.get("key") or ""):
            pending["schedule"] = {
                "day": str(schedule.get("day") or ""),
                "key": str(schedule.get("key") or ""),
                "version": to_int(schedule.get("version"), 0),
                "time": str(schedule.get("time") or ""),
                "place": str(schedule.get("place") or ""),
                "event": str(schedule.get("event") or ""),
            }
        self.db.kv_set(self._pending_key(uid), pending)

    def _clear_proactive_pending(self, uid):
        pending = self.db.kv_get(self._pending_key(uid), None)
        if pending:
            self.db.kv_set(self._pending_key(uid), {})
            self.db.kv_set("proactive_ignored_" + str(uid), 0)
            self.db.kv_set("proactive_held_" + str(uid), False)
            text = str(pending.get("text") or "")
            if text:
                try:
                    asyncio.get_running_loop().create_task(self._remember_shared(uid, text))
                except Exception as exc:
                    _note_swallowed("amo_proactive.py.189", exc)

    async def _remember_shared(self, uid, text):
        try:
            await self._data_call("remember", self._data_bot_id(), "amo",
                                  "今天主动跟老公分享：" + str(text)[:140],
                                  kind="event", importance=0.45)
        except Exception as exc:
            logger.debug("[amo] 分享记忆写入失败: %s" % exc)

            return
        words = ("抱抱", "亲亲", "哄", "对不起", "别难过", "喜欢你", "爱你", "怎么啦", "我在", "在呢", "乖")
        if any(word in str(text or "") for word in words):
            self.db.kv_set("proactive_depressed_until_" + str(uid), 0)
            self.db.kv_set("proactive_ignored_" + str(uid), 0)
            self.state.nudge(委屈=-8, 郁闷=-8, 开心=5, 依恋=3)
            log_event("debug", "proactive.comforted", uid=uid)

    @staticmethod
    def _followup_topics(text):
        """追问别再问同一件事：按话题打标，避开上一句已经问过的内容。"""
        text = str(text or "")
        groups = (
            ("food", ("吃", "饭", "饿", "外卖", "餐")),
            ("comfort", ("疼", "难受", "舒服", "药", "撑着")),
            ("work", ("下班", "加班", "上班", "项目")),
        )
        return {name for name, words in groups if any(word in text for word in words)}

    #: 语气样例：只让模型学「怎么说话」，不要求照抄，也不是固定输出。
    #: task-28 P0-4：删掉「你美丽的老婆/你老婆」这种第三人称自称，
    #: 基础人格唯一口径是「自称我、叫他老公」。
    FOLLOWUP_STYLE_EXAMPLES = (
        "老公你在干啥呀，忙完理我一下嘛。",
        "喂，你倒是看我一眼呀，我等你半天了。",
        "我这么好看你都不带搭理的，哼。",
    )

    async def _followup_text_live(self, umo, uid, kind, previous_text=""):
        """追问去模板化：让模型按人设和语气现写一句；生成不出来就不发。"""
        try:
            llm = getattr(self, "llm", None)
            if llm is None:
                raise RuntimeError("no llm")
            persona = await llm.persona(umo)
            try:
                activity = self.state.current_action_text()
            except Exception:
                activity = ""
            system = ((persona or "你是大肥鱼。") + "\n\n"
                      + "你现在要主动再找他一次。只输出你要发的那一句话，"
                        "不要解释、不要旁白、不要括号动作。")
            samples = "\n".join("- " + line for line in self.FOLLOWUP_STYLE_EXAMPLES)
            # task-28 P0-6：生成前把「素材原文 + 名单」一起给模型，
            # 名字/同伴只能从素材里来，不能自己填空（柳如烟→方圆的事故根因）。
            material = ""
            try:
                try:
                    from .amo_names import ROSTER_LINE as _roster_line
                except ImportError:
                    from amo_names import ROSTER_LINE as _roster_line
                _rows = self._recent(umo, 8) or []
                _recent_lines = [str(row.get("content") or "")[:120]
                                 for row in _rows if isinstance(row, dict)]
                _material = " / ".join([row for row in _recent_lines if row][-5:])
                material = ("\n你认识的人只有：" + str(_roster_line).replace("你认识的人只有：", "")
                            + "\n最近的素材原文（只能从这里取人名/同伴/物品，不能自己编）："
                            + (_material or "（没有新素材）") + "\n")
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive._followup_text_live.material", _amo_exc)
                material = ""
            # 上一句追问也进 prompt：重复追问是这轮要收口的毛病，生成阶段就避开。
            last_followup = ""
            try:
                last_followup = str(self.db.kv_get(
                    "proactive_followup_last_%s" % uid, "") or "")
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive._followup_text_live.last_followup", _amo_exc)
            prompt = (
                "他（老公）还没回你。你上一条主动说的是：“" + str(previous_text or "") + "”\n"
                + ("你上一句追问是：“" + last_followup + "”，这次别再问同一件事。\n"
                   if last_followup else "")
                + ("你现在在：" + str(activity) + "\n" if activity else "")
                + material
                + "学下面几句的语气（只学语气，不要照抄，也不要直接重复它们）：\n" + samples + "\n"
                + "写一句 20 字内的话：撒娇、小小抱怨他晾着你，自称「我」、叫他「老公」，"
                  "但别真的生气。\n"
                  "硬规则：不许编造任何具体事实——天气、新闻、你吃了什么、你看到什么、你在哪里、"
                  "他做了什么，这些你都不知道，一个都不许写；只能写感受、称呼、撒娇和抱怨。\n"
                  "不要用「人呢」「在吗」「醒醒」这类敷衍模板；不要重复上一句，也不要问已经问过的事。"
                  "只输出这句话。"
            )
            text = str(await llm.chat(umo, system, prompt, limit=60) or "").strip()
            text = text.strip('"“”').strip()
            # 素材里没出现的名单人名不算数（生成阶段就收口，不只靠回复侧）
            try:
                try:
                    from .amo_names import strip_unbacked_names as _strip_names
                except ImportError:
                    from amo_names import strip_unbacked_names as _strip_names
                text = _strip_names(text, str(previous_text or "") + " " + material)
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive._followup_text_live.names", _amo_exc)
            if not text or len(text) > 80 or any(
                    word in text for word in ("醒醒", "在等你", "你睡了没", "是不是不想理我",
                                              "人呢", "在吗")):
                raise ValueError("bad followup")
            # 编事实（天气/新闻/吃了什么）一律打回，避免后面和现实对不上
            if any(word in text for word in ("下雨", "天气", "新闻", "热搜", "我刚吃", "我刚看")):
                raise ValueError("fabricated fact")
            # 同一条里连问两遍同一个意思（「你忙完了没？忙完了吗？」）只留一句：
            # 和主动消息同一条规则，追问也不例外；先合并再跟上一条比重复。
            text = merge_synonym_questions(text)
            if not text:
                raise ValueError("bad followup")
            if previous_text and proactive_repeated(text, [previous_text], 0.45):
                raise ValueError("duplicate followup")
            # 上一句追问在发送成功后落到 kv（这里只读不写）：发送前统一闸门做去重，
            # 生成失败/没发出去的句子不算「她说过」。
            return text
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._followup_text_live.failed", _amo_exc)
            return ""   # 兜底池已删除：生成不出来就不发，绝不套模板

    def _followup_lock(self, uid):
        """每个用户一把追问锁：07:30 七连发的并发入口在这里串行化。"""
        locks = getattr(self, "_followup_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._followup_locks = locks
        lock = locks.get(str(uid))
        if lock is None:
            lock = asyncio.Lock()
            locks[str(uid)] = lock
        return lock

    async def _check_proactive_pending(self):
        _dating = getattr(self, "_dating_active", None)
        if callable(_dating) and _dating():
            return
        # 单飞：tick 被 spawn 多次时，同一时刻只允许一个扫描。
        if getattr(self, "_followup_scan_running", False):
            return
        self._followup_scan_running = True
        try:
            await ProactiveMixin._check_proactive_pending_once(self)
        finally:
            self._followup_scan_running = False

    async def _check_proactive_pending_once(self):
        try:
            profiles = self.db.proactive_profiles(limit=100)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._check_proactive_pending", _amo_exc)
            return
        now = now_ts()
        for profile in profiles:
            uid = str(profile.get("uid") or "")
            if not uid:
                continue
            pending = self.db.kv_get(self._pending_key(uid), None)
            if not isinstance(pending, dict) or not pending.get("sent_at"):
                continue
            # 追问要回真正发出这条主动消息的平台（飞书发的就在飞书追）；
            # 老数据没有 umo 时退回主页会话。
            umo = str(pending.get("umo") or "") or str(profile.get("private_umo") or "")
            if not umo:
                continue
            age = now - to_float(pending.get("sent_at"), 0)
            if age > 6 * 3600:
                self.db.kv_set(self._pending_key(uid), {})
                continue
            # 2026-11-21 夜间照顾（生病/难受/红线急）：他睡着了不是不理她。
            # 不追问、不算被忽略、不扣情绪；等他早上回，或 6 小时后自然清掉。
            if pending.get("night_care"):
                continue
            # 提醒发出后日程被取消/改期：旧快照上的追问作废，别再催他。
            _schedule = pending.get("schedule")
            # 追问发生在提醒之后，日程可能已经开始：只复核"还在/没改期"，不吃迟到窗口。
            if isinstance(_schedule, dict) and not schedule_fact_current(
                    self.db, _schedule, late_window=False):
                self.db.kv_set(self._pending_key(uid), {})
                try:
                    log_event("debug", "proactive.schedule.stale", uid=uid,
                              schedule=str(_schedule.get("event") or ""))
                except Exception as _amo_exc:
                    _note_swallowed("amo_proactive._check_proactive_pending.schedule_log", _amo_exc)
                logger.info("[amo] 追问取消：日程已取消或改期（%s）",
                            str(_schedule.get("event") or "")[:40])
                continue
            followup_minutes = self.cfg_int("proactive_followup_minutes", 30)
            # 0 / 负数 = 关掉追问，只保留「心情变差」这一档
            # 2026-11-20：5 分钟追问太催（12:03 主动、12:13 就「醒醒」）；
            # 下限 15 分钟，默认 20 分钟，给他吃饭/开会/通勤的时间。
            followup_after = 10 ** 9 if followup_minutes <= 0 else max(15, followup_minutes) * 60
            # 0 / 负数 = 关掉「没回复就心情变差」；默认跟 schema 一致（30 分钟）
            mood_minutes = max(0, self.cfg_int("proactive_no_reply_minutes", 240))
            mood_after = mood_minutes * 60 if mood_minutes else 10 ** 9
            # 唯一在场策略：用户沉默 6 小时后不再追问「怎么不理我」（2026-11-22）。
            try:
                _followup_allowed = _presence.can_proactive(self, profile, "followup", now=now)
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive._check_proactive_pending.presence", _amo_exc)
                _followup_allowed = True
            if not _followup_allowed:
                logger.info("[amo] 追问取消：用户已沉默超过 6 小时（%s）", uid)
                continue
            if not pending.get("followup") and age >= followup_after:
                if not quiet_now(self.cfg_str("quiet_hours", "23:30-07:30")):
                    # 生成前先原子占坑（per-umo 锁 + 立刻写 followup=True）：
                    # 07:30 七连发是 7 个并发扫描各自生成、发送后才写 followup。
                    async with ProactiveMixin._followup_lock(self, uid):
                        pending = self.db.kv_get(self._pending_key(uid), None)
                        if not isinstance(pending, dict) or pending.get("followup"):
                            continue
                        if now_ts() - to_float(pending.get("sent_at"), 0) < followup_after:
                            continue
                        kind = str(pending.get("kind") or "greet")
                        pending["followup"] = True
                        pending["followup_claim"] = now
                        self.db.kv_set(self._pending_key(uid), pending)
                        _live = getattr(self, "_followup_text_live", None)
                        followup = ""
                        try:
                            if callable(_live):
                                followup = await _live(umo, uid, kind, pending.get("text"))
                        finally:
                            if not followup:
                                # 生成失败回滚占坑：下一轮还能再试，但绝不重发旧句
                                latest = self.db.kv_get(self._pending_key(uid), {}) or {}
                                if isinstance(latest, dict):
                                    latest["followup"] = False
                                    latest.pop("followup_claim", None)
                                    self.db.kv_set(self._pending_key(uid), latest)
                        if not followup:
                            logger.info("[amo] 追问没生成出来，这轮先不发")
                            continue
                        # 追问闸门（发送前唯一一处）：同一句追问换个语气词再问一遍不发。
                        # 比的是「上一次真发出去的追问」+「上一条主动消息」，不再多看近聊窗口。
                        _last_followup = ""
                        try:
                            _last_followup = str(self.db.kv_get(
                                "proactive_followup_last_%s" % uid, "") or "")
                        except Exception as _amo_exc:
                            _note_swallowed(
                                "amo_proactive._check_proactive_pending.last_followup", _amo_exc)
                        if followup_repeated(followup, (_last_followup, pending.get("text"))):
                            logger.info("[amo] 追问：和上一句问的是同一件事，这条不发（%s）",
                                        followup[:40])
                            latest = self.db.kv_get(self._pending_key(uid), {}) or {}
                            if isinstance(latest, dict):
                                latest["followup"] = False
                                latest.pop("followup_claim", None)
                                self.db.kv_set(self._pending_key(uid), latest)
                            continue
                        _old_source = getattr(self, "_outbound_source", "")
                        _old_kind = getattr(self, "_outbound_kind", "")
                        self._outbound_source = "followup"
                        self._outbound_kind = "nudge"
                        try:
                            sent_ok = await self._send_to_umo(umo, followup)
                        finally:
                            self._outbound_source = _old_source
                            self._outbound_kind = _old_kind
                        if sent_ok:
                            latest = self.db.kv_get(self._pending_key(uid), {}) or {}
                            if isinstance(latest, dict):
                                latest["followup_at"] = now
                                latest["followup_text"] = followup
                                self.db.kv_set(self._pending_key(uid), latest)
                            self.db.kv_set("proactive_followup_at_" + uid, now)
                            # 追问本身也是一句她说过的话，不记下来会引出下一轮「你怎么不理我」；
                            # 同时进主动日志，下一轮主题去重才看得到（唯一事实源）。
                            try:
                                self.db.kv_set("proactive_followup_last_%s" % uid, followup)
                                self.db.log_proactive(uid, "followup", followup)
                            except Exception as _amo_exc:
                                _note_swallowed(
                                    "amo_proactive._check_proactive_pending.followup_mark", _amo_exc)
                            self._remember_bot_message(umo, followup)
                            self._mark_bot_initiated(umo, followup)
                            log_event("debug", "proactive.followup.sent", uid=uid)
                        else:
                            # 被统一出口丢弃（重复/频控/沉默）时保留占坑，避免下一轮继续生成；
                            # 只有纯发送失败（无 drop 记录）才回滚，允许下次重试。
                            _dropped = False
                            try:
                                try:
                                    from . import amo_outbound as _gateway_rows
                                except ImportError:
                                    import amo_outbound as _gateway_rows
                                _rows = _gateway_rows.rows(self, umo)
                                _dropped = bool(_rows and str(_rows[-1].get("decision")) == "drop")
                            except Exception as _amo_exc:
                                _note_swallowed("amo_proactive._check_proactive_pending.dropped", _amo_exc)
                            if not _dropped:
                                latest = self.db.kv_get(self._pending_key(uid), {}) or {}
                                if isinstance(latest, dict):
                                    latest["followup"] = False
                                    latest.pop("followup_claim", None)
                                    self.db.kv_set(self._pending_key(uid), latest)
            if not pending.get("mood") and age >= mood_after:
                pending["mood"] = True
                self.db.kv_set(self._pending_key(uid), pending)
                self.db.kv_set("proactive_held_" + uid, True)
                ignored = to_int(self.db.kv_get("proactive_ignored_" + uid, 0), 0) + 1
                self.db.kv_set("proactive_ignored_" + uid, ignored)
                self.state.nudge(委屈=5, 郁闷=5, 生气=2, 依恋=2)
                if ignored >= max(1, self.cfg_int("proactive_depressed_count", 3)):
                    self._set_depressed(uid)
                log_event("debug", "proactive.ignored", uid=uid, ignored=ignored)

    def _ritual_in_window(self, spec):
        try:
            start, end = str(spec).split("-", 1)
            return in_minute_range(minutes_now(), parse_hhmm(start), parse_hhmm(end))
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._ritual_in_window", _amo_exc)
            return False

    def _primary_channel_umo(self, profile):
        """主平台：配置 > 最近一次交互的平台 > private_umo；**绝不轮换**。

        2026-11-15 用户要求：主动消息只发主频道，不能 QQ 说完飞书再说一遍。
        """
        uid = str(profile.get("uid") or "")
        try:
            forced = str(self.cfg_str("proactive_primary_umo", "") or "").strip()
        except Exception:
            forced = ""
        if forced:
            return forced
        fallback = str(profile.get("private_umo") or "")
        if not uid:
            return fallback
        try:
            data = self.db.kv_get("channels:%s" % uid, {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._primary_channel_umo", _amo_exc)
            return fallback
        if isinstance(data, dict):
            items = [(to_float(v.get("ts"), 0), str(v.get("umo")))
                     for v in data.values() if isinstance(v, dict) and v.get("umo")]
            if items:
                items.sort(key=lambda pair: -pair[0])
                return items[0][1]
        return fallback

    def _ritual_umos(self, profile):
        """她能给你发问候的会话：只保留主平台（跨平台不重复问候）。"""
        primary = self._primary_channel_umo(profile)
        return [primary] if primary else []
        uid = str(profile.get("uid") or "")
        items = []
        try:
            data = self.db.kv_get("channels:%s" % uid, {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._ritual_umos", _amo_exc)
            data = {}
        if isinstance(data, dict):
            entries = [value for value in data.values() if isinstance(value, dict) and value.get("umo")]
            entries.sort(key=lambda value: -to_float(value.get("ts"), 0))
            for value in entries:
                umo = str(value.get("umo") or "")
                if umo and umo not in items:
                    items.append(umo)
        private_umo = str(profile.get("private_umo") or "")
        if private_umo and private_umo not in items:
            items.append(private_umo)
        return items

    def _recent_greeting(self, uid, minutes):
        """最近一次每日问候发在哪个平台、隔了多久（QQ/飞书算同一个人）。"""
        if not uid:
            return False
        try:
            data = self.db.kv_get("greeting_last:%s" % uid, {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._recent_greeting", _amo_exc)
            data = {}
        if not isinstance(data, dict):
            return False
        return _within_minutes(data.get("ts"), now_ts(), minutes)

    async def _user_spoke_today(self, uid, kind):
        """他今天已经主动说过话了吗（决定要不要发「醒了没 / 吃了吗 / 早点睡」）。

        早上问「醒了没」最容易露馅：他 08:04 说「我九点上班」，09:29 再问就成失忆了。
        所以早上只要他今天开过口，就当他已经醒了，不再发问候（正常回复里自然会带早安）。

        数据从记忆终端（能记）读：陪伴本地 `messages` 表不再写原始聊天，
        拿它判断会永远「今天没说话」。
        """
        uid = str(uid or "")
        if not uid:
            return False
        try:
            base = _now().replace(hour=0, minute=0, second=0, microsecond=0)
            if kind == "morning":
                # 凌晨聊天不算「今天已经醒了」：早安只认 06:00 之后开口
                base = base.replace(hour=max(0, min(23, self.cfg_int("ritual_morning_from_hour", 6))))
            start = base.timestamp()
        except Exception:
            start = now_ts() - 86400
        rows: list[Any] = []
        try:
            rows = await self._data_call(
                "messages_since", self._data_bot_id(), uid, int(start),
            ) or []
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._user_spoke_today.data", _amo_exc)
            rows = []
        if not rows:
            try:
                rows = self.db.messages_by_uid(uid, limit=60)
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive.ProactiveMixin._user_spoke_today.local", _amo_exc)
                rows = []
        for row in rows or []:
            if not isinstance(row, dict):  # 记忆终端偶发非 dict 行（旧格式/坏行），跳过而不是崩
                continue
            if str(row.get("role") or "") != "user":
                continue
            try:
                ts = float(row.get("created_at") or 0)
            except (TypeError, ValueError):
                continue
            if ts < start:
                continue
            text = str(row.get("content") or "")
            stripped = text.lstrip()
            if stripped.startswith("{") or '"event_id"' in text or '"level":"auto"' in text:
                # 金融/系统事件也以 role=user 落库，不能算「他说话了」
                continue
            if kind == "morning":
                return True
            if kind == "lunch" and any(word in text for word in
                                       ("吃", "饭", "饿", "外卖", "食堂", "早餐", "午饭", "汤", "面", "粉",
                 "粥", "菜", "火锅", "汉堡", "披萨", "便当", "寿司", "烧烤", "米线",
                 "饺子", "包子", "零食", "喝了")):
                return True
            if kind == "goodnight" and any(word in text for word in ("睡", "晚安", "困", "熬夜")):
                return True
        return False

    def _mark_greeting(self, uid, kind, umo):
        try:
            self.db.kv_set("greeting_last:%s" % str(uid), {
                "kind": str(kind),
                "umo": str(umo or ""),
                "ts": now_ts(),
            })
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._mark_greeting", _amo_exc)

    def _ritual_pending(self, profile):
        """每日早晚问候已废除（事实驱动第一阶段）：主动消息不再为问候让路。"""
        return False

    def _ritual_should_send(self, umos, kind):
        """今天这个问候还该不该发；QQ/飞书的近聊一起算，避免两边各问候一遍。"""
        umos = list(umos) if isinstance(umos, (list, tuple, set)) else [umos]
        rows = []
        for item in umos:
            try:
                rows.extend(self._recent(item, 10))
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive.ProactiveMixin._ritual_should_send", _amo_exc)
        rows.sort(key=lambda row: to_float(row.get("created_at"), 0))
        now = now_ts()
        recent_gap = max(5, self.cfg_int("ritual_skip_recent_minutes", 45)) * 60
        if rows:
            last_at = to_float(rows[-1].get("created_at"), 0)
            if last_at and now - last_at < recent_gap:
                return False
        if not self.cfg_bool("ritual_skip_if_greeted", True):
            return True
        # 只认「他」问候过：她自己那句「吃了没 / 晚安」多半是主动消息或追问，
        # 算进来会把当天的问候整段吃掉（午饭问候时有时无就是这么来的）。
        texts = [str(row.get("content") or "") for row in rows if str(row.get("role") or "") == "user"]
        joined = "\n".join(texts[-10:])
        if kind == "morning":
            return not bool(re.search(r"(?:^|\s)早(?:\s|$|[!！。~～])|早安|早上好|早上好呀|我醒了|起床了|还没起来吗|醒了没", joined))
        if kind == "goodnight":
            return not bool(re.search(r"晚安|睡了|睡觉|先睡|去睡|早点休息", joined))
        if kind == "lunch":
            return not bool(re.search(r"午饭|中午吃|吃啥|吃什么|吃饭|吃了没|吃了吗", joined))
        return True

    async def _maybe_date_wrap(self):
        """约会超时 / 静默收尾：只写客观事实，道别话由大模型现场生成（禁止模板）。"""
        now = now_ts()
        try:
            data = _ds.wrap_candidate(self, now)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._maybe_date_wrap.session", _amo_exc)
            return
        if not data or not str(data.get("line") or "").strip():
            return
        until = float(data.get("end_ts") or 0)
        try:
            profiles = self.db.proactive_profiles(limit=20)
        except Exception:
            return
        profile = next((p for p in profiles if str(p.get("relation") or "") == "相恋"), None) \
            or (profiles[0] if profiles else None)
        if not profile:
            return
        umo = self._primary_channel_umo(profile)
        if not umo:
            return
        reason = fact = ""
        pause_at = float(data.get("pause_at") or 0)
        if data.get("phase") == "paused" and pause_at and now - pause_at > 15 * 60:
            reason = "pause_timeout"
            fact = "他走开好一会儿还没回来，你有点坐不住了，想去找他，或者自己先回去。"
        elif until and now > until:
            reason = "timeout"
            fact = "时间不早了，吃饱喝足了，该回去了；他不在你身边，你自己先回家。"
        else:
            last_user: float = 0
            try:
                for row in self._recent(umo, 12):
                    if str(row.get("role") or "") == "user":
                        last_user = max(last_user, to_float(row.get("created_at"), 0))
            except Exception:
                last_user = 0
            if last_user and now - last_user > 30 * 60:
                reason = "silent"
                fact = "他好像去忙了，你在犹豫是继续等他，还是自己先溜达回去。"
        if not reason:
            return
        _ds.finish(self, now, reason=reason, title=str(data.get("title") or "约会"))
        _ds.request_end(self, fact, reason=reason, ttl=600, sent=True, now=now)
        _after_place = ""
        try:
            try:  # task-30：收尾事实的地点从账本取，不再硬说「现在你在家」
                from .amo_scene_ledger import scene_at as _scene_at
            except ImportError:
                from amo_scene_ledger import scene_at as _scene_at
            _after_place = str((_scene_at(self, _now()) or {}).get("place") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._maybe_date_wrap.scene", _amo_exc)
            _after_place = ""
        try:
            self.set_date_fact(
                "今天的约会已经结束，你们各自回到日常；%s他不在你身边，只能手机聊天。"
                % (("现在你在" + _after_place + "，") if _after_place else ""),
                now + 6 * 3600)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._maybe_date_wrap.fact", _amo_exc)
        try:
            try:  # task-30：此刻地点/动作唯一读场景账本（含请假/出差硬约束）
                from .amo_state_snapshot import snapshot_with_scene as _snap_with_scene
            except ImportError:
                from amo_state_snapshot import snapshot_with_scene as _snap_with_scene
            text = await self.llm.gen_proactive(
                umo, profile, _snap_with_scene(self), "date_end", facts=fact)
        except Exception as exc:
            logger.debug("[amo] 约会收尾生成失败: %s" % exc)
            text = ""
        text = str(text or "").strip()
        if not text:
            return
        sent = await self._send_proactive_lines(profile, umo, text)
        if sent:
            self._mark_bot_initiated(sent, text)
            self.db.log_proactive(str(profile.get("uid") or ""), "date_end", text)
            logger.info("[amo] 约会收尾 -> %s: %s" % (umo, text[:80]))

    async def _maybe_rituals(self):
        """只剩生日祝福：每天早/中/晚的固定问候已废除。"""
        now = now_ts()
        if now - to_float(getattr(self, "_last_ritual", 0), 0) < 60:
            return
        self._last_ritual = now
        try:
            profiles = self.db.proactive_profiles(limit=100)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._maybe_rituals", _amo_exc)
            return
        try:  # task-30：此刻地点/动作唯一读场景账本（含请假/出差硬约束）
            from .amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        except ImportError:
            from amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        snap = _snap_with_scene(self)
        for profile in profiles:
            if str(profile.get("relation") or "") != "相恋":
                continue
            uid = str(profile.get("uid") or "")
            umos = self._ritual_umos(profile)
            if not umos or not uid:
                continue
            home_umo = str(profile.get("private_umo") or "")
            name = str(profile.get("alias") or profile.get("name") or "宝").strip() or "宝"
            try:
                birthday = str(profile.get("birthday") or "").strip()[-5:]
                today_md = _now().strftime("%m-%d")
            except Exception:
                birthday, today_md = "", ""
            if birthday and birthday == today_md and self.cfg_bool("birthday_greet_enabled", True):
                year_key = "birthday_greet:" + uid + ":" + str(_now().year)
                if not self.db.kv_get(year_key, False):
                    targets = _greeting_targets(
                        umos, home_umo,
                        randomize=self.cfg_bool("ritual_random_platform", True),
                    )
                    text = ""
                    if targets:
                        try:
                            text = await self.llm.gen_proactive(targets[0], profile, snap, "birthday")
                        except Exception as exc:
                            logger.debug("[amo] 生日祝福生成失败: %s" % exc)
                    if not text:
                        text = name + "，生日快乐！"
                    sent_umo = ""
                    for candidate in targets:
                        if await self._send_to_umo(candidate, text, source="proactive",
                                                   kind="birthday"):
                            sent_umo = candidate
                            break
                    if sent_umo:
                        self._mark_bot_initiated(sent_umo, text)
                        self.db.kv_set(year_key, True)
                        self.db.log_proactive(uid, "birthday", text)
                        logger.info("[amo] 生日祝福 -> %s: %s" % (name, text[:120]))
                        continue
    #: 今晚已经发过天气提醒后，主动消息里不许再重复的天气话术（11-19 “记得带伞” 3 连）。
    _WEATHER_REPEAT_WORDS = ("天气", "带伞", "下雨", "下雪", "降温", "升温", "多穿",
                             "加衣服", "保暖", "雨伞")

    def _weather_repeat_blocked(self, text):
        """今晚天气提醒已经发过 → 后来的主动/晚安不许再把天气说一遍。

        weather_alert_open 只在当天有效（已由天气提醒侧按日清理），这里只做话题去重。
        """
        try:
            if str(self.db.kv_get("weather_alert_open", "") or "") != today_str():
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.weather_repeat.state", _amo_exc)
            return False
        return any(word in str(text or "") for word in self._WEATHER_REPEAT_WORDS)

    def _schedule_reminder_switch_enabled(self):
        """日程提醒开关状态唯一读取点：启动/切换时打一条明确日志。

        配置项 `proactive_schedule_reminder` 默认 False（线上一直是关的，所以
        “提醒没出来”不是坏了，是没开）。日志把最终生效值说清楚，面板也看得到。
        """
        enabled = bool(self.cfg_bool("proactive_schedule_reminder", False))
        if getattr(self, "_schedule_reminder_switch_logged", None) != enabled:
            self._schedule_reminder_switch_logged = enabled
            logger.info("[amo] 日程提醒开关 proactive_schedule_reminder=%s（默认关闭）",
                        "开启" if enabled else "未开启")
        return enabled

    def _choose_proactive_kind(self, profile, snap, category=""):
        relation = profile.get("relation", "普通")
        mood = snap["mood"]
        painful = (
            snap["health"]["sick"]
            or snap["period"]["pain"] >= 6
            or mood["委屈"] >= 60
            or mood["郁闷"] >= 65
        )
        health = snap.get("health") or {}
        if (relation == "相恋" and health.get("sick")
                and self.cfg_bool("proactive_comfort", True)
                and not self.db.kv_get("unwell_announced:" + today_str(), False)):
            return "unwell"
        if relation == "相恋" and painful and self.cfg_bool("proactive_comfort", True):
            return "comfort"
        # 日程提醒默认关；打开后把 10~60 分钟内开始的日程排成一次提醒。
        if self._schedule_reminder_switch_enabled():
            if schedule_reminder_fact(self.db, _now()):
                return "schedule_reminder"
        activity = str((snap.get("activity") or {}).get("activity") or "")
        if category == "play" and ("游戏" in activity or "打游戏" in activity):
            return "game"
        if category == "meal":
            return "meal"
        if category in ("rest", "play"):
            return "slack"
        if category == "commute":
            return "offwork" if _now().hour >= 16 else "commute"
        if relation in ("熟识", "相恋") and random.random() < self.cfg_float("proactive_share_prob", 0.35):
            return "share"
        return "greet"

    def _proactive_user_just_talked(self, profile, now, minutes=None):
        """对方刚说过话就别主动插话。0 表示关闭这项检查。

        minutes=None 用配置的 proactive_user_idle_minutes（默认 30）；
        红线消息传 PROACTIVE_FORCED_IDLE_MINUTES（5 分钟），避免「刚聊完马上分享」。
        """
        if minutes is None:
            minutes = max(0, self.cfg_int("proactive_user_idle_minutes", 30))
        minutes = max(0, int(minutes or 0))
        if not minutes:
            return False
        last_user_at = to_float(profile.get("last_seen"), 0)
        return bool(last_user_at) and (now - last_user_at) < minutes * 60

    def _proactive_forced_allowed(self, profile, now):
        """「日常关心」也要跟上次主动拉开间隔，避免重启后立刻又发。"""
        gap = max(1, self.cfg_int("proactive_forced_gap_minutes", 30)) * 60
        return (now - to_float(profile.get("last_proactive", 0))) >= gap

    def _proactive_candidates(self, profile, primary):
        """主动消息的候选平台：选中的优先，其次是其它通道，最后 private_umo。

        最近刚被主动联系过的平台会被跳过，避免兜底时连着骚扰同一处。
        """
        items = []

        def push(umo, force=False):
            umo = str(umo or "")
            if not umo or umo in items:
                return
            try:
                from .group_scene import proactive_allowed
            except ImportError:
                from group_scene import proactive_allowed
            if not proactive_allowed(self, umo):
                return
            if not force and self._recent_bot_initiated(umo, 3600):
                return
            items.append(umo)

        # 只推主频道：同一件事不能在 QQ 和飞书各说一遍
        push(primary, force=True)
        return items

    def _pick_proactive_umo(self, profile):
        """这轮主动消息发哪个平台：固定主频道，**不轮换**。"""
        return self._primary_channel_umo(profile)

    def _dating_active(self):
        """约会进行中：所有主动推演/追问都必须静默（2026-11-15 用户要求）。"""
        try:
            return bool(_ds.is_dating(self, now_ts()))
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._dating_active", _amo_exc)
        return False

    def _photo_proactive_blocked(self):
        """刚分享过照片（含测试/手动）就先别主动开口：10 分钟冷却。"""
        seconds = PROACTIVE_PHOTO_SILENCE_SECONDS
        checker = getattr(self, "photo_share_cooldown_active", None)
        if callable(checker):
            try:
                if checker(seconds):
                    return True
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive._photo_proactive_blocked.engine", _amo_exc)
        try:
            record = self.db.kv_get(PHOTO_LAST_SHARE_KEY, {}) or {}
            at = to_float(record.get("at"), 0) if isinstance(record, dict) else 0.0
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._photo_proactive_blocked.kv", _amo_exc)
            at = 0.0
        return bool(at) and (now_ts() - at) < max(0, int(seconds))

    @staticmethod
    def _day_has_diary(plugin, day):
        """那一天有没有留下日记（块日记优先，旧整点日记兜底）。"""
        db = getattr(plugin, "db", None)
        if db is None:
            return False
        lister = getattr(db, "list_block_diaries", None)
        if callable(lister):
            try:
                rows = lister(day) or []
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive._day_has_diary.block", _amo_exc)
                rows = []
            if any(isinstance(row, dict) and str(row.get("text") or "").strip() for row in rows):
                return True
        getter = getattr(db, "get_diary", None)
        if not callable(getter):
            return False
        for hour in range(24):
            try:
                if str(getter(day, hour) or "").strip():
                    return True
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive._day_has_diary.hour", _amo_exc)
                return False
        return False

    async def _day_has_memory(self, uid, day, label):
        """那一天的长期记忆在不在（记忆终端不可用时按「没有」处理）。"""
        recall = getattr(self, "_recall", None)
        if not callable(recall) or not uid:
            return False
        try:
            result = await recall(uid, label)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._day_has_memory", _amo_exc)
            return False
        text = ""
        if isinstance(result, (tuple, list)) and len(result) >= 2:
            text = str(result[1] or "")
        elif isinstance(result, str):
            text = result
        tag = "[昨日]" if label == "昨天" else "[前天]"
        return bool(text) and (label in text or day in text or tag in text)

    async def _proactive_time_evidence(self, uid, now):
        """昨天/前天在 memory 或 diary 里有没有记录：决定过去事实能不能说。"""
        evidence = {"昨天": False, "前天": False}
        for label, days in (("昨天", 1), ("前天", 2)):
            day = (now - timedelta(days=days)).strftime("%Y-%m-%d")
            if self._day_has_diary(self, day):
                evidence[label] = True
            elif await self._day_has_memory(uid, day, label):
                evidence[label] = True
        return evidence

    async def _proactive_time_guard_text(self, profile, text):
        """发送前校验：提到「昨天/前天」就必须和 memory/diary 的日期对得上。"""
        raw = str(text or "").strip()
        if not raw or not past_claim_labels(raw):
            return raw
        uid = str((profile or {}).get("uid") or "")
        now = _now()
        try:
            evidence = await self._proactive_time_evidence(uid, now)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._proactive_time_guard_text", _amo_exc)
            evidence = {"昨天": False, "前天": False}
        kept, decision = guard_proactive_time(raw, evidence, now)
        if decision != "ok":
            try:
                log_event("debug", "proactive.time_guard", uid=uid, decision=decision)
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive._proactive_time_guard_text.log", _amo_exc)
            logger.info("[amo] 主动消息时态校验 %s：%s", decision, raw[:60])
        return kept

    async def _maybe_proactive(self):
        """时段推演：把分享欲/想念/生活小事/红线摆给她，发不发由她自己判断。

        单飞：同一进程里只允许一轮在跑。LLM 慢的时候（2026-11-19 实测出现过
        11 分钟才回来的决策），下一轮 tick 直接返回，避免两轮各自发一条、
        用户看到的就是「怎么一直在说早」。
        """
        if getattr(self, "_proactive_running", False):
            logger.debug("[amo] 主动消息：上一轮还在生成，跳过本轮")
            return
        self._proactive_running = True
        try:
            await self._maybe_proactive_once()
        finally:
            self._proactive_running = False

    async def _maybe_proactive_once(self):
        """_maybe_proactive 的单轮实现（已被单飞包住）。"""
        _dating = getattr(self, "_dating_active", None)
        if callable(_dating) and _dating():
            logger.debug("[amo] 主动消息：约会中，本轮推演静默")
            return
        try:
            cur = self.state.current_activity() or {}
            if str(cur.get("category") or "") == "sleep" or "睡" in str(cur.get("activity") or ""):
                logger.debug("[amo] 主动消息：她正在睡觉，静默")
                return
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._maybe_proactive.sleep", _amo_exc)
        now = _now()
        # 2026-11-21：不再限制固定钟点；每个小时都可以单独掷骰（睡眠/免打扰仍拦）。
        quiet = quiet_now(self.cfg_str("quiet_hours", "23:30-07:30"))
        night = night_now(now)
        try:
            profiles = self.db.proactive_profiles(limit=100)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._maybe_proactive", _amo_exc)
            return
        if self.cfg_bool("proactive_require_friend", True):
            profiles = [profile for profile in profiles if self._is_proactive_friend(profile)]
        if not profiles:
            return
        try:  # task-30：此刻地点/动作唯一读场景账本（含请假/出差硬约束）
            from .amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        except ImportError:
            from amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        snap = _snap_with_scene(self)
        for profile in profiles:
            await _proactive_one(self, profile, snap, now, quiet, night)

    async def _send_proactive_lines(self, profile, target_umo, text, kind="greet"):
        """像真人一样连发 1-3 句，句间留一点打字延迟。"""
        raw = str(text or "").strip()
        if not raw:
            return ""
        photo_blocked = getattr(self, "_photo_proactive_blocked", None)
        if callable(photo_blocked) and photo_blocked():
            logger.info("[amo] 主动消息：刚分享过照片，本轮先不开口")
            return ""
        time_guard = getattr(self, "_proactive_time_guard_text", None)
        if callable(time_guard):
            raw = await time_guard(profile, raw)
            if not raw:
                return ""
        # 连续爆发保护：同一个会话 90 秒内只允许一轮主动消息（防机关枪）
        try:
            burst = getattr(self, "_proactive_burst", None)
            if not isinstance(burst, dict):
                burst = {}
                self._proactive_burst = burst
            key = str(target_umo or "")
            last = to_float(burst.get(key), 0)
            window = PROACTIVE_BURST_GUARD_SECONDS
            if str(kind or "") in ("greet", "share", "slack", "commute", "game"):
                try:
                    try:
                        from . import amo_proactive_gate as _gate
                    except ImportError:
                        import amo_proactive_gate as _gate
                    window = max(window, _gate.CROSS_WINDOW_SECONDS)
                except Exception:
                    window = max(window, 300)
            if last and now_ts() - last < window:
                logger.debug("[amo] 主动消息：%s 秒内刚发过，跳过这一轮", window)
                return ""
            burst[key] = now_ts()
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._send_proactive_lines.burst", _amo_exc)
        # 2026-11-15：主动消息也要过消费校验，不能张嘴就编外卖
        # Phase 6：这道守卫走 amo_reply_pipeline 的 proactive_pre 阶段（仍在切句之前）。
        context = _reply_pipeline.context_for_proactive(self, target_umo, profile)
        raw = str((await _reply_pipeline.run(raw, context))["text"] or "").strip()
        try:
            try:
                from .amo_purchase_guard import rewrite as _purchase_guard
            except ImportError:
                from amo_purchase_guard import rewrite as _purchase_guard
            raw = _purchase_guard(raw, self)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.purchase_guard", _amo_exc)
        try:
            try:
                from .amo_companion_guard import rewrite as _companion_guard
            except ImportError:
                from amo_companion_guard import rewrite as _companion_guard
            raw = _companion_guard(raw, self.state.current_activity() or {})
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.companion_guard", _amo_exc)
        try:
            try:
                from .amo_marriage_guard import rewrite as _marriage_guard
            except ImportError:
                from amo_marriage_guard import rewrite as _marriage_guard
            raw = _marriage_guard(raw)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.marriage_guard", _amo_exc)
        try:
            try:
                from .amo_voice_guard import clean as _voice_clean
                from .amo_persona_store import active_pack as _active_pack
            except ImportError:
                from amo_voice_guard import clean as _voice_clean
                from amo_persona_store import active_pack as _active_pack
            raw = _voice_clean(raw, _active_pack(self), public=False)
            if not raw:
                return ""
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.voice_guard", _amo_exc)
        try:
            try:
                from .amo_state_consistency import rewrite as _state_rewrite
            except ImportError:
                from amo_state_consistency import rewrite as _state_rewrite
            raw = _state_rewrite(raw, self, "")
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.state_consistency", _amo_exc)
        # 「不矛盾」守卫：主动消息不走 decorate，切句前补一次（消费校验顺序不变）
        # 事实只读唯一视图（F1）：apply_fact_guard → amo_diary_guard.narrative_check
        try:
            raw = await _reply_pipeline.apply_fact_guard_async(raw, self)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._send_proactive_lines.fact_guard", _amo_exc)
        # 同一状态 30 分钟内不重复主动发（跨段去重在 send 阶段）
        before_dedup = raw
        try:
            raw = _reply_pipeline.apply_proactive_dedup(raw, self, target_umo)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._send_proactive_lines.status_dedup", _amo_exc)
        # 去重整条删空时，带时间/地点/答案的句子不能被静默吞掉（提醒尤其如此）。
        raw = keep_answer(before_dedup, raw)
        if not raw:
            return ""
        # 口头禅去逐字复读：主动消息不走 decorate，这里紧跟事实守卫补一次
        try:
            raw = _reply_pipeline.apply_tone_guard(raw, self, target_umo)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._send_proactive_lines.tone_guard", _amo_exc)
        if not raw:
            return ""
        _is_goodnight = str(kind) == "goodnight" or any(
            word in raw for word in ("晚安", "梦里见", "早点睡"))
        if _is_goodnight:
            try:
                _last_goodnight = to_float(self.db.kv_get("goodnight_sent_at", 0), 0)
                if _last_goodnight and now_ts() - _last_goodnight < 6 * 3600:
                    return ""
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive.goodnight_lock", _amo_exc)
        try:
            try:
                from .amo_share_ledger import note as _share_note
            except ImportError:
                from amo_share_ledger import note as _share_note
            if any(word in raw for word in ("我刷到", "给你看", "快看", "分享")) and any(
                    word in raw for word in ("视频", "图片", "照片")):
                _share_note(self, raw, media=True)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.share_note", _amo_exc)
        parts = [item.strip() for item in re.split(r"(?<=[。！？!?~])", raw) if item.strip()]
        # 切句后的近似去重交给统一出口（amo_outbound_guard 的 90 秒同来源去重）；
        # amo_part_dedupe 的 nudge 意图表与它重叠，二选一，这里不再维护第二套规则。
        if len(parts) > 3:
            parts = ["".join(parts[:-2])] + parts[-2:]
        sent = ""
        _old_source = getattr(self, "_outbound_source", "")
        _old_kind = getattr(self, "_outbound_kind", "")
        _old_batch = getattr(self, "_outbound_batch", "")
        if str(kind) == "goodnight":
            self._outbound_source = "goodnight"
        elif str(kind) == "schedule_reminder":
            self._outbound_source = "reminder"
        else:
            self._outbound_source = "proactive"
        self._outbound_kind = str(kind or "")
        self._outbound_batch = "proactive:%s:%s" % (profile.get("uid") or "", now_ts())
        try:
            for index, part in enumerate(parts):
                if index:
                    await asyncio.sleep(random.uniform(0.8, 1.6))
                for candidate in self._proactive_candidates(profile, target_umo):
                    if await self._send_to_umo(candidate, part):
                        sent = candidate
                        target_umo = candidate
                        break
                else:
                    break
        finally:
            self._outbound_source = _old_source
            self._outbound_kind = _old_kind
            self._outbound_batch = _old_batch
        if sent and _is_goodnight:
            try:
                self.db.kv_set("goodnight_sent_at", now_ts())
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive.goodnight_lock.set", _amo_exc)
        return sent


    def _record_channel(self, uid, umo, platform):
        """记下这个人各平台的会话地址，供主动消息选边。"""
        if not uid or not umo:
            return
        try:
            key = "channels:%s" % uid
            data = self.db.kv_get(key, {}) or {}
            if not isinstance(data, dict):
                data = {}
            data[str(platform or "?")] = {"umo": str(umo), "ts": now_ts()}
            self.db.kv_set(key, data)
        except Exception as exc:
            logger.debug("[amo] 记录通道失败: %s" % exc)

    def _mark_forced_hour(self, uid):
        mark = today_str() + ":" + str(_now().hour)
        self._last_proactive_activity[str(uid)] = mark
        try:
            self.db.kv_set(self._forced_hour_key(uid), mark)
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._mark_forced_hour", _amo_exc)

    def _forced_sent_this_hour(self, uid):
        """「日常关心」每小时只强制一次；存数据库，热重载/重启都不清空。"""
        mark = today_str() + ":" + str(_now().hour)
        try:
            return str(self.db.kv_get(self._forced_hour_key(uid), "")) == mark
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._forced_sent_this_hour", _amo_exc)
            return self._last_proactive_activity.get(str(uid)) == mark

    def _forced_hour_key(self, uid):
        return "proactive_forced_hour_" + str(uid or "")

    def _set_depressed(self, uid):
        hours = max(1, self.cfg_int("proactive_depressed_hours", 24))
        self.db.kv_set("proactive_depressed_until_" + str(uid), now_ts() + hours * 3600)

    def _depressed_active(self, uid):
        return to_float(self.db.kv_get("proactive_depressed_until_" + str(uid), 0), 0) > now_ts()

    def _pending_key(self, uid):
        return "proactive_pending_" + str(uid)



    def _proactive_quiet(self):
        """主动消息免打扰（默认 23:00-07:30，和聊天的 quiet_hours 分开）。"""
        try:
            spec = self.cfg_str("proactive_quiet_hours", "23:00-07:30")
        except Exception:
            spec = "23:00-07:30"
        return quiet_now(spec)

    def _voluntary_count(self, uid, day):
        try:
            return to_int(self.db.kv_get("proactive_vol:%s:%s" % (uid, day), 0), 0)
        except Exception:  # cleanup: 读不到按 0
            return 0

    def _voluntary_hour(self, uid, day):
        try:
            value = self.db.kv_get("proactive_vol_hour:%s:%s" % (uid, day), None)
        except Exception:  # cleanup: 读不到按 None
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _mark_voluntary(self, uid, day, hour, kind):
        try:
            try:
                from . import amo_proactive_gate as _gate
            except ImportError:
                import amo_proactive_gate as _gate
            if _gate.counts_cap(kind):
                key = "proactive_vol:%s:%s" % (uid, day)
                self.db.kv_set(key, self._voluntary_count(uid, day) + 1)
            self.db.kv_set("proactive_vol_hour:%s:%s" % (uid, day), int(hour))
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._mark_voluntary", _amo_exc)

    def _goodnight_sent(self, uid, day):
        try:
            return bool(self.db.kv_get("proactive_goodnight:%s:%s" % (uid, day), False))
        except Exception:  # cleanup: 读不到按未发
            return False

    def _goodnight_due(self, profile, now):
        try:
            from . import amo_proactive_gate as _gate
        except ImportError:
            import amo_proactive_gate as _gate
        uid = str(profile.get("uid") or "")
        day = _gate.sleep_day(now, today_str())
        if self._goodnight_sent(uid, day):
            return False
        try:
            recent = self.db.recent_proactive_texts(uid, 3, 1) or []
        except Exception:  # cleanup: 读不到按空
            recent = []
        said = " ".join(str(item) for item in recent)
        if any(word in said for word in ("晚安", "睡了", "去睡", "先睡")):
            try:
                self.db.kv_set("proactive_goodnight:%s:%s" % (uid, day), True)
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive._goodnight_due.mark", _amo_exc)
            return False
        last_seen = to_float(profile.get("last_seen"), 0)
        return bool(last_seen) and (now_ts() - last_seen) >= 300

    async def _send_goodnight(self, profile, snap, target_umo, now):
        try:
            from . import amo_proactive_gate as _gate
        except ImportError:
            import amo_proactive_gate as _gate
        uid = str(profile.get("uid") or "")
        day = _gate.sleep_day(now, today_str())
        text = ""
        try:
            text = await self.llm.gen_proactive(
                target_umo, profile, snap, "goodnight",
                facts="现在该睡了。只写一句晚安，1 句，20 字内，不要旁白、不要括号动作。")
        except Exception as exc:
            logger.debug("[amo] 晚安生成失败: %s" % exc)
        text = str(text or "").strip()
        if not text:
            # 去模板化：模型不可用就今晚先不说，也不塞固定晚安
            logger.debug("[amo] 晚安生成失败，本轮跳过")
            return False
        if self._weather_repeat_blocked(text):
            logger.info("[amo] 晚安：今晚已经提醒过天气，不再重复提天气/带伞")
            return False
        sent = await self._send_proactive_lines(profile, target_umo, text, kind="goodnight")
        if not sent:
            return False
        try:
            self.db.kv_set("proactive_goodnight:%s:%s" % (uid, day), True)
            self.db.log_proactive(uid, "goodnight", text)
            self._mark_voluntary(uid, day, now.hour, "goodnight")
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._send_goodnight.mark", _amo_exc)
        self._mark_bot_initiated(sent, text)
        logger.info("[amo] 晚安 -> %s: %s", sent, text[:40])
        return True

_GREET_OPEN = ("在干嘛", "干嘛呢", "忙吗", "忙不忙", "睡了吗", "在吗", "人呢", "干嘛呀")
_MICRO_HINTS = ("我刚看到", "刚才路过", "刚看到", "路过看到", "刚才看到")


def _one_topic_guard(text, kind):
    """一条只做一件事：share 去掉开头问候；非 share 去掉“身边小事”句。"""
    import re as _re
    body = str(text or "").strip()
    if not body:
        return body
    if kind == "share":
        for word in _GREET_OPEN:
            if body.startswith(word):
                body = body[len(word):].lstrip("，,。！!？? ")
                break
        return merge_synonym_questions(body or str(text or "").strip())
    parts = _re.split(r"(?<=[。！？!?])", body)
    kept = [part for part in parts if not any(word in part for word in _MICRO_HINTS)]
    # 同一条消息里连问两遍同一个意思（「你忙完了没？忙完了吗？」）只留一句。
    return merge_synonym_questions("".join(kept).strip() or str(text or "").strip())


async def _proactive_one(self, profile, snap, now, quiet, night):
    """处理一个画像的主动消息判断（2026-11-21：概率/上限/跳整点/夜间白名单/晚安）。"""
    uid = str(profile.get("uid") or "")
    if not uid:
        return
    try:
        from . import amo_proactive_gate as gate
    except ImportError:
        import amo_proactive_gate as gate
    day = today_str()
    slot_key = "proactive_slot:%s:%s:%02d" % (uid, day, now.hour)
    started_at = now_ts()
    category = (snap.get("activity") or {}).get("category", "")
    silence = _presence.silence_hours(profile.get("last_seen"), now_ts())
    ratio = None
    try:
        total = int(self.wallet.total_assets())
        if total > 0:
            ratio = int(self.wallet.balance()) / float(total)
    except Exception:  # cleanup: 资产读不到按 None
        ratio = None
    forced = forced_reason(snap, ratio, silence)
    try:
        kind = str(self._choose_proactive_kind(profile, snap, category) or "greet")
    except Exception:  # cleanup: 选不到按问候
        kind = "greet"
    # 日程提醒：挑一条即将开始的日程；挑不到就退回普通问候，别硬编日程。
    schedule_fact = {}
    if kind == "schedule_reminder":
        schedule_fact = schedule_reminder_fact(self.db, now, day=day, claim=True)
        if not schedule_fact:
            kind = "greet"
    target_umo = self._pick_proactive_umo(profile)
    if not target_umo:
        return
    try:
        from .group_scene import proactive_allowed
    except ImportError:
        from group_scene import proactive_allowed
    if not proactive_allowed(self, target_umo):
        logger.info("[amo] 主动消息：目标群是工作群/陌生群，取消发送")
        self.db.kv_set(slot_key, True)
        return
    # 晚安保底：23:00 起、你安静 5 分钟后发；00:30 截止；说过就跳过
    if not forced and gate.goodnight_window(now) and self._goodnight_due(profile, now):
        if await self._send_goodnight(profile, snap, target_umo, now):
            return
    # 唯一在场策略：用户沉默 6 小时后，催问/逛街/自拍/未回复追问全部禁发，
    # 只保留早晚问候和明确时间点提醒（红线急/照顾类不算打扰，照发）。
    try:
        _allowed = _presence.can_proactive(self, profile, kind, now=now_ts(),
                                           forced=bool(forced))
    except Exception as _amo_exc:
        _note_swallowed("amo_proactive._proactive_one.presence", _amo_exc)
        _allowed = True
    if not _allowed:
        logger.info("[amo] 主动消息：用户沉默超过 6 小时，%s 类不打扰（只剩问候/时间点提醒）", kind)
        self.db.kv_set(slot_key, True)
        return
    if not forced:
        if self._photo_proactive_blocked():
            logger.info("[amo] 主动消息：%d 分钟内刚分享过照片，先不打扰",
                        PROACTIVE_PHOTO_SILENCE_SECONDS // 60)
            return
        if self._proactive_user_just_talked(profile, now_ts()):
            return
    elif self._proactive_user_just_talked(profile, now_ts(),
                                          minutes=PROACTIVE_FORCED_IDLE_MINUTES):
        # 红线也不在他刚说完话的头 5 分钟里插话（2026-11-23：刚聊完立刻分享的事故）。
        logger.info("[amo] 主动消息：他刚说完话，红线也等 %d 分钟",
                    PROACTIVE_FORCED_IDLE_MINUTES)
        return
    try:
        followup_at = to_float(self.db.kv_get("proactive_followup_at_" + uid, 0), 0)
    except Exception as _amo_exc:
        _note_swallowed("amo_proactive._proactive_one.followup_gap", _amo_exc)
        followup_at = 0.0
    if followup_at and now_ts() - followup_at < PROACTIVE_AFTER_FOLLOWUP_SECONDS:
        return
    pending = self.db.kv_get(self._pending_key(uid), None)
    if isinstance(pending, dict) and pending.get("sent_at"):
        if now_ts() - to_float(pending.get("sent_at"), 0) < 6 * 3600:
            return
        self.db.kv_set(self._pending_key(uid), {})
    if forced:
        if self._forced_sent_this_hour(uid) or not self._proactive_forced_allowed(profile, now_ts()):
            logger.info("[amo] 主动消息：红线想找他，但刚主动过，先压一压")
            return
    sent_today = to_int(profile.get("proactive_count"), 0) if profile.get("proactive_day") == day else 0
    ignored = to_int(self.db.kv_get("proactive_ignored_" + uid, 0), 0)
    held = bool(self.db.kv_get("proactive_held_" + uid, False))
    try:
        try:
            from .amo_rest import resting_now
        except ImportError:
            from amo_rest import resting_now
        if resting_now(self) and not forced:
            self.db.kv_set(slot_key, True)
            logger.info("[amo] 主动消息：他说要先睡，先不打扰")
            return
    except Exception as _amo_exc:
        _note_swallowed("amo_proactive.user_resting", _amo_exc)
    _night_ok = forced or gate.night_allowed(kind)
    if (quiet or night or category == "sleep") and not _night_ok:
        self.db.kv_set(slot_key, True)
        logger.info("[amo] 主动消息：夜间或免打扰，她把想说的话留到明天")
        return
    if not _night_ok and self._proactive_quiet():
        self.db.kv_set(slot_key, True)
        logger.info("[amo] 主动消息：主动免打扰时段，她把想说的话留到明天")
        return
    if (not forced and not gate.night_allowed(kind)
            and self._goodnight_sent(uid, gate.sleep_day(now, day)) and int(now.hour) < 7):
        logger.info("[amo] 主动消息：已经说过晚安，早上再聊")
        return
    if not forced and gate.counts_cap(kind):
        cap = max(1, self.cfg_int("proactive_daily_cap", gate.DAILY_CAP))
        if self._voluntary_count(uid, day) >= cap:
            self.db.kv_set(slot_key, True)
            logger.info("[amo] 主动消息：今天闲聊已达 %d 条上限", cap)
            return
        if gate.skip_next_hour(self._voluntary_hour(uid, day), now.hour):
            self.db.kv_set(slot_key, True)
            logger.info("[amo] 主动消息：上一小时刚发过，这一小时跳过")
            return
    if not forced and gate.chat_rolls(kind):
        roll_key = "proactive_roll:%s:%s:%02d" % (uid, day, now.hour)
        if self.db.kv_get(roll_key, False):
            return
        self.db.kv_set(roll_key, True)
        happy = float((snap.get("mood") or {}).get("开心") or 0)
        prob = greet_probability(happy)
        if not _roll_probability(prob):
            logger.info("[amo] 主动消息：掷骰没中（kind=%s 开心=%.0f 概率=%d%%）", kind, happy, prob)
            return
    if sent_today >= 5 and ignored >= 3 and not forced:
        self.db.kv_set(slot_key, True)
        logger.info("[amo] 主动消息：今天找过 %d 次都没回音，她决定先不打扰" % sent_today)
        return
    try:
        hunger = self.get_hunger()
    except Exception:  # cleanup: 饥饿值读不到按 80
        hunger = 80
    event = pick_life_event(day, uid)
    micro = ""
    try:
        record = self.db.kv_get("micro_event:last", {}) or {}
        micro = _fresh_micro_text(record, now_ts())
        if micro:
            shared = float(self.db.kv_get("micro_event:shared_ts", 0) or 0)
            stamp = float(record.get("ts") or 0)
            if stamp and abs(stamp - shared) < 1:
                micro = ""
            else:
                self.db.kv_set("micro_event:shared_ts", stamp)
    except Exception:  # cleanup: 微事件读不到按空
        micro = ""
    extra = []
    if forced:
        extra.append("【必须主动】" + forced)
    if held:
        extra.append("上次你主动找他，他很久没回你，你有点委屈；这次开口可以带点小脾气，但别质问他。")
    if kind == "share":
        if micro or event:
            extra.append("你身边的小事：" + (micro or event))
        extra.append("只讲这一件小事，一条消息只做一件事，不要先问他在干嘛。")
    else:
        extra.append("你现在有点想他（想念值 %d/100）。" % missing_score(silence))
        if kind != "greet" and share_drive(silence, sent_today, ignored) >= 40:
            extra.append("你今天分享欲挺强，遇到一点小事都想告诉他。")
    try:
        recent_said = self.db.recent_proactive_texts(uid, 2, 3)
    except Exception:  # cleanup: 近期主动话读不到按空
        recent_said = []
    said_line = _recent_said_line(recent_said)
    if said_line:
        extra.append(said_line)
    facts = proactive_facts(snap, hunger, now, extra="".join(extra))
    with _PROACTIVE_SLOT_LOCK:
        if self.db.kv_get(slot_key, False):
            return
        self.db.kv_set(slot_key, True)
    gen_kwargs = {"facts": facts}
    if schedule_fact:
        gen_kwargs["schedule"] = dict(schedule_fact)
    try:
        text = await self.llm.gen_proactive(target_umo, profile, snap, kind, **gen_kwargs)
    except Exception as exc:
        logger.debug("[amo] 主动消息决策失败: %s" % exc)
        self.db.kv_set(slot_key, False)
        return
    elapsed = now_ts() - started_at
    if elapsed > PROACTIVE_STALE_SECONDS:
        logger.info("[amo] 主动消息：生成耗时 %.0fs 超过 %ds，本轮丢弃",
                    elapsed, PROACTIVE_STALE_SECONDS)
        self.db.kv_set(slot_key, False)
        return
    if proactive_skip(text):
        logger.info("[amo] 主动消息：她自己决定这次不找你（%02d:00）" % int(now.hour))
        return
    try:
        recent_all = self.db.recent_proactive_texts(uid, 5, PROACTIVE_THEME_DEDUP_SECONDS // 3600)
    except Exception as _amo_exc:
        _note_swallowed("amo_proactive._proactive_one.theme_dedup", _amo_exc)
        recent_all = []
    text = strip_mixed_checkin(text)
    text = _one_topic_guard(text, kind)
    repeated = proactive_repeated(text, recent_all) or proactive_same_topic(text, recent_all)
    if repeated:
        logger.info("[amo] 主动消息：与最近一条主题重复，本轮不发（%s）", repeated[:40])
        return
    if self._weather_repeat_blocked(text):
        logger.info("[amo] 主动消息：今晚已经提醒过天气，这条不再重复提天气/带伞")
        return
    # 生成要时间：发送前再复核一次日程，取消/改期/早过开始时间后这条旧快照提醒必须作废。
    if schedule_fact and not schedule_fact_current(self.db, schedule_fact, now=now):
        logger.info("[amo] 主动消息：日程在生成期间被取消/改期或已迟到，这条提醒作废（%s）",
                    str(schedule_fact.get("event") or "")[:40])
        try:
            log_event("debug", "proactive.schedule.stale", uid=uid,
                      schedule=str(schedule_fact.get("event") or ""))
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._proactive_one.schedule_log", _amo_exc)
        release_schedule_reminder(self.db, day, schedule_fact["key"],
                                  schedule_fact.get("claim_token") or "")
        return
    _reminded_key = ""
    if schedule_fact:
        text = _ground_schedule_text(text, schedule_fact)
        if not text:
            logger.info("[amo] 主动消息：提醒与日程事实不符，本轮不发")
            release_schedule_reminder(self.db, day, schedule_fact["key"],
                                      schedule_fact.get("claim_token") or "")
            return
        # 2026-11-22：发送前先落幂等标记（含开始时间），并发/重试都不会把同一条
        # 日程提醒说两遍；发送失败再回滚标记，保证还能补发。
        _reminded_key = "schedule_reminded:%s:%s" % (day, schedule_fact["key"])
        self.db.kv_set(_reminded_key, True)
    sent_umo = await self._send_proactive_lines(profile, target_umo, text, kind=kind)
    if not sent_umo:
        if _reminded_key:
            self.db.kv_set(_reminded_key, False)
        if schedule_fact:
            release_schedule_reminder(self.db, day, schedule_fact["key"],
                                      schedule_fact.get("claim_token") or "")
        return
    if schedule_fact:
        # 标记在发送前已经写好（幂等）；这里只释放单飞占位。
        release_schedule_reminder(self.db, day, schedule_fact["key"],
                                  schedule_fact.get("claim_token") or "")
    self._mark_bot_initiated(sent_umo, text)
    self.db.kv_set("last_channel:%s" % uid, sent_umo)
    self.db.bump_proactive(uid)
    self.db.log_proactive(uid, kind, text)
    if schedule_fact:
        self._set_proactive_pending(uid, kind, text, umo=sent_umo, schedule=schedule_fact)
    else:
        self._set_proactive_pending(uid, kind, text, umo=sent_umo)
    if (int(now.hour) >= 23 or int(now.hour) < 7) and (forced or kind in ("comfort", "unwell")):
        try:
            _pending = self.db.kv_get(self._pending_key(uid), {}) or {}
            _pending["night_care"] = True
            self.db.kv_set(self._pending_key(uid), _pending)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._proactive_one.night_care", _amo_exc)
    self._mark_voluntary(uid, day, now.hour, "forced" if forced else kind)
    if forced:
        self._mark_forced_hour(uid)
    logger.info("[amo] 主动分享 -> %s (%s/%s)", profile.get("name", ""),
                "forced" if forced else "decide", kind)
