# -*- coding: utf-8 -*-
"""ProactiveMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

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
import random
import re
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


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


PROACTIVE_SLOTS = (8, 12, 15, 19, 23)
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


def silence_hours(last_seen, now=None):
    stamp = float(now if now is not None else now_ts())
    last = to_float(last_seen, 0)
    if not last:
        return 24.0
    return max(0.0, (stamp - last) / 3600.0)


def share_drive(silence, sent_today=0, ignored_today=0):
    """分享欲：越久没聊越想说；今天说太多又被冷落，就冷下来。"""
    score = float(silence) * 8 - int(sent_today) * 6 - int(ignored_today) * 12
    return int(max(0, min(100, score)))


def missing_score(silence):
    return int(max(0, min(100, float(silence) * 10)))


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


def forced_reason(snap, balance_ratio=None, silence=0.0):
    """必须主动的红线：情绪极值 / 大病 / 余额低于总资产 5% / 超过 8 小时没交流。"""
    mood = (snap or {}).get("mood") or {}
    if float(mood.get("开心") or 0) >= 90:
        return "你现在开心到极点，特别想找人分享。"
    if float(mood.get("委屈") or 0) >= 85 or float(mood.get("郁闷") or 0) >= 90:
        return "你现在情绪很崩，一个人很难受，特别想找老公。"
    health = (snap or {}).get("health") or {}
    if health.get("sick") and severity_rank(health.get("severity")) >= 3:
        return "你病得有点重，一个人在家很难受。"
    if balance_ratio is not None and balance_ratio < 0.05:
        return "你的余额只剩总资产的 5% 不到了，有点慌。"
    if float(silence) >= 8:
        return "你已经一整天没跟老公说上话了，有点孤单，想问问他今天过得怎么样。"
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

    def _set_proactive_pending(self, uid, kind, text, umo=""):
        self.db.kv_set(self._pending_key(uid), {
            "sent_at": now_ts(), "kind": kind, "text": str(text or ""),
            # 记下真正发出去的会话：追问要回同一个平台，否则会「飞书问完、QQ 追」。
            "umo": str(umo or ""),
            "followup": False, "mood": False,
        })

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

    def _followup_text(self, uid, kind, previous_text=""):
        """追问换着说法：同一句不连着出现，也别把刚问的问题再问一遍。"""
        templates = [t for t in (self.FOLLOWUP_TEMPLATES.get(str(kind)) or self.FOLLOWUP_TEMPLATES["greet"]) if t]
        if not templates:
            return "？怎么不理我了？"
        key = "proactive_followup_last_%s" % uid
        try:
            last = str(self.db.kv_get(key, "") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._followup_text", _amo_exc)
            last = ""
        pool = [t for t in templates if t != last]
        topics = self._followup_topics(previous_text)
        if topics:
            fresh = [t for t in pool if not (self._followup_topics(t) & topics)]
            pool = fresh
        if not pool:
            # 同一类话题的问句都问过了，就换成「怎么不理我」的催回，别再问一遍。
            pool = [t for t in self.FOLLOWUP_TEMPLATES["greet"] if t != last] or list(self.FOLLOWUP_TEMPLATES["greet"])
        text = random.choice(pool)
        try:
            self.db.kv_set(key, text)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._followup_text", _amo_exc)
        return text

    async def _check_proactive_pending(self):
        _dating = getattr(self, "_dating_active", None)
        if callable(_dating) and _dating():
            return
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
            followup_minutes = self.cfg_int("proactive_followup_minutes", 30)
            # 0 / 负数 = 关掉追问，只保留「心情变差」这一档
            followup_after = 10 ** 9 if followup_minutes <= 0 else max(5, followup_minutes) * 60
            # 0 / 负数 = 关掉「没回复就心情变差」；默认跟 schema 一致（30 分钟）
            mood_minutes = max(0, self.cfg_int("proactive_no_reply_minutes", 240))
            mood_after = mood_minutes * 60 if mood_minutes else 10 ** 9
            if not pending.get("followup") and age >= followup_after:
                if not quiet_now(self.cfg_str("quiet_hours", "23:30-07:30")):
                    kind = str(pending.get("kind") or "greet")
                    followup = self._followup_text(uid, kind, pending.get("text"))
                    if await self._send_to_umo(umo, followup):
                        pending["followup"] = True
                        pending["followup_at"] = now
                        self.db.kv_set(self._pending_key(uid), pending)
                        # 追问本身也是一句她说过的话，不记下来会引出下一轮「你怎么不理我」
                        self._remember_bot_message(umo, followup)
                        self._mark_bot_initiated(umo, followup)
                        log_event("debug", "proactive.followup.sent", uid=uid)
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
            last_user = 0
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
        try:
            self.set_date_fact("今天的约会已经结束，你们各自回到日常；现在你在家，"
                               "他不在你身边，只能手机聊天。", now + 6 * 3600)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._maybe_date_wrap.fact", _amo_exc)
        try:
            text = await self.llm.gen_proactive(
                umo, profile, self.state.snapshot(), "date_end", facts=fact)
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
        snap = self.state.snapshot()
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
                        if await self._send_to_umo(candidate, text):
                            sent_umo = candidate
                            break
                    if sent_umo:
                        self._mark_bot_initiated(sent_umo, text)
                        self.db.kv_set(year_key, True)
                        self.db.log_proactive(uid, "birthday", text)
                        logger.info("[amo] 生日祝福 -> %s: %s" % (name, text[:120]))
                        continue
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

    def _proactive_user_just_talked(self, profile, now):
        """对方刚说过话就别主动插话。0 表示关闭这项检查。"""
        minutes = max(0, self.cfg_int("proactive_user_idle_minutes", 10))
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
        """时段推演：把分享欲/想念/生活小事/红线摆给她，发不发由她自己判断。"""
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
        if int(now.hour) not in PROACTIVE_SLOTS:
            return
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
        snap = self.state.snapshot()
        for profile in profiles:
            await _proactive_one(self, profile, snap, now, quiet, night)

    async def _send_proactive_lines(self, profile, target_umo, text):
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
            if last and now_ts() - last < PROACTIVE_BURST_GUARD_SECONDS:
                logger.debug("[amo] 主动消息：%s 秒内刚发过，跳过这一轮",
                             PROACTIVE_BURST_GUARD_SECONDS)
                return ""
            burst[key] = now_ts()
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._send_proactive_lines.burst", _amo_exc)
        # 2026-11-15：主动消息也要过消费校验，不能张嘴就编外卖
        # Phase 6：这道守卫走 amo_reply_pipeline 的 proactive_pre 阶段（仍在切句之前）。
        context = _reply_pipeline.context_for_proactive(self, target_umo, profile)
        raw = str((await _reply_pipeline.run(raw, context))["text"] or "").strip()
        # 「不矛盾」守卫：主动消息不走 decorate，切句前补一次（消费校验顺序不变）
        # 事实只读唯一视图（F1）：apply_fact_guard → amo_diary_guard.narrative_check
        try:
            raw = await _reply_pipeline.apply_fact_guard_async(raw, self)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._send_proactive_lines.fact_guard", _amo_exc)
        # 同一状态 30 分钟内不重复主动发（跨段去重在 send 阶段）
        try:
            raw = _reply_pipeline.apply_proactive_dedup(raw, self, target_umo)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._send_proactive_lines.status_dedup", _amo_exc)
        if not raw:
            return ""
        # 口头禅去逐字复读：主动消息不走 decorate，这里紧跟事实守卫补一次
        try:
            raw = _reply_pipeline.apply_tone_guard(raw, self, target_umo)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive._send_proactive_lines.tone_guard", _amo_exc)
        if not raw:
            return ""
        parts = [item.strip() for item in re.split(r"(?<=[。！？!?~])", raw) if item.strip()]
        if len(parts) > 3:
            parts = ["".join(parts[:-2])] + parts[-2:]
        sent = ""
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


async def _proactive_one(self, profile, snap, now, quiet, night):
    """处理一个画像的主动消息判断（原 _maybe_proactive 的循环体）。"""
    uid = str(profile.get("uid") or "")
    if not uid:
        return
    slot_key = "proactive_slot:%s:%s:%02d" % (uid, today_str(), now.hour)
    if self.db.kv_get(slot_key, False):
        return
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
    # 刚发过照片（尤其面板测试图）先闭嘴：15:11 测试图 + 15:12 主动消息会像同一条日常分享
    if self._photo_proactive_blocked():
        logger.info("[amo] 主动消息：%d 分钟内刚分享过照片，先不打扰",
                    PROACTIVE_PHOTO_SILENCE_SECONDS // 60)
        return
    if self._proactive_user_just_talked(profile, now_ts()):
        return
    pending = self.db.kv_get(self._pending_key(uid), None)
    if isinstance(pending, dict) and pending.get("sent_at"):
        if now_ts() - to_float(pending.get("sent_at"), 0) < 6 * 3600:
            return
        self.db.kv_set(self._pending_key(uid), {})
    category = (snap.get("activity") or {}).get("category", "")
    silence = silence_hours(profile.get("last_seen"), now_ts())
    sent_today = to_int(profile.get("proactive_count"), 0) if profile.get("proactive_day") == today_str() else 0
    ignored = to_int(self.db.kv_get("proactive_ignored_" + uid, 0), 0)
    held = bool(self.db.kv_get("proactive_held_" + uid, False))
    ratio = None
    try:
        total = int(self.wallet.total_assets())
        if total > 0:
            ratio = int(self.wallet.balance()) / float(total)
    except Exception:
        ratio = None
    forced = forced_reason(snap, ratio, silence)
    # 她睡了 / 夜间 / 免打扰：只有红线才允许打扰
    if (quiet or night or category == "sleep") and not forced:
        self.db.kv_set(slot_key, True)
        logger.info("[amo] 主动消息：夜间或免打扰，她把想说的话留到明天")
        return
    # 软阈值：今天主动很多次都没回音，她也高冷起来
    if sent_today >= 5 and ignored >= 3 and not forced:
        self.db.kv_set(slot_key, True)
        logger.info("[amo] 主动消息：今天找过 %d 次都没回音，她决定先不打扰" % sent_today)
        return
    try:
        hunger = self.get_hunger()
    except Exception:
        hunger = 80
    event = pick_life_event(today_str(), uid)
    micro = ""
    try:
        record = self.db.kv_get("micro_event:last", {}) or {}
        micro = _fresh_micro_text(record, now_ts())
    except Exception:
        micro = ""
    extra = []
    if forced:
        extra.append("【必须主动】" + forced)
    if held:
        extra.append("上次你主动找他，他很久没回你，你有点委屈；这次开口可以带点小脾气，但别质问他。")
    extra.append("你现在有点想他（想念值 %d/100）。" % missing_score(silence))
    if share_drive(silence, sent_today, ignored) >= 40:
        extra.append("你今天分享欲挺强，遇到一点小事都想告诉他。")
    if micro or event:
        extra.append("你身边的小事：" + (micro or event))
    try:
        recent_said = self.db.recent_proactive_texts(uid, 2, 3)
    except Exception:
        recent_said = []
    said_line = _recent_said_line(recent_said)
    if said_line:
        extra.append(said_line)
    facts = proactive_facts(snap, hunger, now, extra="".join(extra))
    try:
        text = await self.llm.gen_proactive(target_umo, profile, snap, "decide", facts=facts)
    except Exception as exc:
        logger.debug("[amo] 主动消息决策失败: %s" % exc)
        text = ""
    self.db.kv_set(slot_key, True)
    if proactive_skip(text):
        logger.info("[amo] 主动消息：她自己决定这次不找你（%02d:00）" % int(now.hour))
        return
    sent_umo = await self._send_proactive_lines(profile, target_umo, text)
    if not sent_umo:
        return
    self._mark_bot_initiated(sent_umo, text)
    self.db.kv_set("last_channel:%s" % uid, sent_umo)
    self.db.bump_proactive(uid)
    self.db.log_proactive(uid, "decide", text)
    self._set_proactive_pending(uid, "decide", text, umo=sent_umo)
    logger.info("[amo] 主动分享 -> %s (%s)" % (profile.get("name", ""), "forced" if forced else "decide"))


