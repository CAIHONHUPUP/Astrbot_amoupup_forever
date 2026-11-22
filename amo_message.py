# -*- coding: utf-8 -*-
"""MessageMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

from typing import Any

import asyncio
import json
import random
import re
import time

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        Comp, MessageChain, logger, now_ts, sanitize_caption, strip_tech_terms,
        to_float, typing_delay_ms,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            Comp, MessageChain, logger, now_ts, sanitize_caption,
            strip_tech_terms, to_float, typing_delay_ms,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                Comp, MessageChain, logger, now_ts, sanitize_caption,
                strip_tech_terms, to_float, typing_delay_ms,
            )


try:  # 回复守卫流水线（Phase 6）：发送前的顺序与去重统一在 amo_reply_pipeline
    from . import amo_reply_pipeline as _reply_pipeline
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_reply_pipeline as _reply_pipeline

try:  # 唯一发送出口（2026-11-22 task-16）：去重/频控/批互斥/outbound_log
    from . import amo_outbound as _gateway
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_outbound as _gateway


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


def _strip_action_narration(text):
    """发送出口用的薄封装：函数内条件导入会被 mypy 推断成 object。"""
    try:
        from .amo_persona_guard import strip_action_narration as _fn
    except ImportError:  # 直接按文件加载时
        from amo_persona_guard import strip_action_narration as _fn
    return _fn(text)


def _has_orphan_paren(text):
    """出站括号不变量：剥掉成对括号后还残留全角括号 → 残片。"""
    try:
        from .amo_persona_guard import has_orphan_paren as _fn
    except ImportError:  # 直接按文件加载时
        from amo_persona_guard import has_orphan_paren as _fn
    return bool(_fn(text))

try:  # 地点跟日程对齐：她嘴里说的地方和日程对不上时改回来
    from .place_guard import rewrite_reply as _rewrite_reply
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from place_guard import rewrite_reply as _rewrite_reply
        except Exception:  # pragma: no cover
            def _rewrite_reply(text, activity):
                return str(text or "")


_FOOD_WORDS = ("汤", "面", "饭", "菜", "粉", "粥", "火锅", "汉堡", "披萨",
               "外卖", "饺子", "包子", "便当", "寿司", "烧烤", "麻辣烫", "米线")


_SLEEP_WORDS = ("睡", "午睡", "打盹", "眯", "躺", "休息", "补觉", "赖床")


def topic_lock_block():
    """讨论第三方时的代词锁定：他给的建议是对那件事说的，不是对她说的。

    2026-11-15 线上：她在聊闺蜜的渣男男友，他顺着说「趁早分了」，她回
    「分啥呀，我这儿正睡午觉呢」——把对第三方的建议当成了对自己的，还砸进后台状态。
    """
    return (
        "【话题锁定】他这一段很可能在说别人的事（闺蜜/同事/朋友/网上的人）："
        "他说“分了 / 骂他 / 别理他 / 这种人 / 太渣了”这类话，是对那件事、那个人说的，不是对你说的。"
        "你要顺着这个话题继续聊（“对，赶紧分了”“这种人不值得”），"
        "绝不把话拉回自己身上，也不要回“分啥呀 / 分谁”，更不要顺带报自己的状态。"
    )


def rest_state_block(activity="", category="", asked=False):
    """她在睡觉/休息、但人正跟你聊天：别把“正在睡”当话题，顺滑衔接。

    返回空串表示此刻不是休息状态，不用注入。
    """
    body = str(activity or "").strip()
    cat = str(category or "").strip()
    if cat not in ("sleep", "rest") and not any(word in body for word in _SLEEP_WORDS):
        return ""
    if asked:
        how = "他问你在干嘛，就一句“在床上眯着呢”带过，"
    else:
        how = "别主动说“我正睡午觉 / 我在睡觉”这种把话题拉走的话，"
    return (
        "【状态衔接】你此刻在休息（在床上刷手机、半梦半醒地回消息），不是被谁叫醒的。"
        + how
        + "想提就带个小动作（翻个身、手机差点砸脸上），说完马上接着聊他那件事。"
    )


def continuity_block():
    """连续聊天：不要演出“被突然打断/吓一跳”。"""
    return (
        "【连续对话】你们正在连续聊天，上一句就在几分钟内。"
        "不要表现成突然被打断、被吵醒或者吓一跳，也不要说“吓我一跳”“你干嘛突然说话”。"
        "只有半夜（0-6 点）突然来消息才可能有被打扰的感觉，普通对话里必须连贯接着聊。"
    )


_ASK_ACTIVITY_RE = re.compile(
    r"(你在干嘛|在干嘛|干嘛呢|干什么呢|你在哪|在哪呢|吃了吗|吃没吃|吃了没|忙吗|忙不忙"
    r"|在忙什么|上班吗|在上班|下班了吗|今天干嘛|做什么呢|干啥呢)"
)


def asks_about_activity(text):
    """他这句是不是在问「你在干嘛/在哪/吃了吗」。"""
    return bool(_ASK_ACTIVITY_RE.search(str(text or "")))


def dialogue_guard_blocks(snap, asked=False):
    """回复组装用：话题锁定 + 连续对话 +（在休息时）状态衔接。"""
    parts = [topic_lock_block(), continuity_block()]
    activity = (snap or {}).get("activity") or {}
    rest = rest_state_block(
        str(activity.get("activity") or ""),
        str(activity.get("category") or ""),
        asked,
    )
    if rest:
        parts.append(rest)
    return parts


_PHOTO_RESEND_HINTS = ("再发", "重发", "重新发", "刚才那张", "上一张", "之前那张",
                       "刚那张", "那张图", "刚拍的那张")
_PHOTO_RESEND_SOFT = ("发给我看看", "发我看看", "发过来看看", "发来看看", "发给我看")
_PHOTO_NEW_HINTS = ("拍一张", "拍张", "来一张", "换一张", "新的", "新拍", "重新拍")


def looks_like_photo_resend_request(text):
    """他是不是要「把刚才那张再发一次」，而不是让她重新拍一张。"""
    value = str(text or "")
    if not value:
        return False
    if any(word in value for word in _PHOTO_RESEND_HINTS):
        return True
    if any(word in value for word in _PHOTO_RESEND_SOFT):
        return not any(word in value for word in _PHOTO_NEW_HINTS)
    return False


def strip_repeated_food_question(reply, user_text, bot_text):
    """他刚回答「中午吃什么」，就别再问「你点了吗 / 吃了吗」。

    2026-11-12 线上：她问「今天中午吃啥呀」→ 他答「番茄蘑菇汤」→ 她回
    「番茄蘑菇汤挺好的…我就吃常吃的那家，你点了吗？」。答都答了，再问就是没接住。
    """
    reply = str(reply or "")
    user_text = str(user_text or "")
    bot_text = str(bot_text or "")
    if not reply or not user_text or not bot_text:
        return reply
    if not re.search(r"(吃啥|吃什么|吃点什么|中午吃|午饭吃|晚饭吃)", bot_text):
        return reply
    if len(user_text) > 20 or not any(word in user_text for word in _FOOD_WORDS):
        return reply
    question = re.compile(r"(点了吗|点没|点了没|吃了吗|吃了没|点了没呀)")
    parts = re.split(r"(?<=[。！？!?~～\n])", reply)
    kept = []
    for part in parts:
        if question.search(part):
            # 只删问句那半句，别把她刚说的「我就吃常吃的那家」也删掉
            clauses = re.split(r"(?<=[，,])", part)
            part = "".join(clause for clause in clauses if not question.search(clause))
        if part.strip():
            kept.append(part)
    out = "".join(kept).strip()
    out = re.sub(r"[，,]+$", "。", out)
    return out or "听着就香。"




_URGE_WORDS = ("催", "快点", "快去", "赶紧", "别磨蹭", "别待太久", "别在里面", "水放了没",
               "洗快点", "别拖", "去了没", "别看手机")
_ACK_WORDS = ("好", "嗯", "行", "知道了", "收到", "马上", "我去了")
_LEAVE_WORDS = ("洗澡", "洗个澡", "去睡", "睡了", "晚安", "拜拜", "先去忙", "去忙", "出门", "我先去")


def strip_repeated_urge(reply, user_text, bot_text):
    """他已经答应/要去做了，就别再催：去掉鼓吹句，只留一句软收尾。"""
    reply = str(reply or "")
    user_text = str(user_text or "")
    bot_text = str(bot_text or "")
    if not reply or not user_text:
        return reply
    short = len(user_text) <= 16
    ack = short and any(word in user_text for word in _ACK_WORDS)
    leaving = any(word in user_text for word in _LEAVE_WORDS)
    if not ack and not leaving:
        return reply
    prev_urge = any(word in bot_text for word in _URGE_WORDS)
    if not prev_urge and not any(word in reply for word in _URGE_WORDS):
        return reply
    parts = re.split(r"(?<=[。！？!?~～\n])", reply)
    kept = [part for part in parts if not any(word in part for word in _URGE_WORDS)]
    out = "".join(kept).strip()
    out_parts = re.split(r"(?<=[。！？!?~～\n])", out) if out else []
    out = "".join(out_parts[:1]).strip() if out_parts else ""
    out = re.sub(r"[，,]+$", "。", out)
    if is_action_only(out):
        out = (out + "\n嗯，去吧。").strip()
    return out or "嗯，去吧。"

_ACTION_SEGMENT = re.compile(r"^[（(][^（）()]*[）)]\s*[。！？!?~～]?$")


def is_action_only(text):
    """整条回复只有括号动作、没有一句真正说出口的话。"""
    parts = [part.strip() for part in str(text or "").splitlines() if part.strip()]
    return bool(parts) and all(_ACTION_SEGMENT.match(part) for part in parts)


#: 3a：动作-only 兜底统一走 domains/reply/repetition_guard.FALLBACKS（数据只有一份）。
_ACTION_INTENTS = (("action.shy", ("耳朵", "脸红", "烫", "害羞", "扣", "低头")),
                   ("action.laugh", ("笑",)),
                   ("action.default", ()))


def action_only_fallback(text, user_text=""):
    """动作-only 时补一句人话；保留原动作，不整段丢。"""
    body = str(text or "").strip()
    intent = "action.default"
    for name, words in _ACTION_INTENTS:
        if words and any(word in body for word in words):
            intent = name
            break
    try:
        try:
            from .domains.reply.repetition_guard import fallback_pool as _pool
        except ImportError:
            from domains.reply.repetition_guard import fallback_pool as _pool
        pool = _pool(intent) or ("……嗯。",)
    except Exception:
        pool = ("……嗯。",)
    line = random.choice(pool)
    return (body + "\n" + line).strip() if body else line


_SHORT_CONTINUE = ("慢慢看", "慢慢吃", "慢慢来", "慢慢走", "慢慢说", "去吧", "睡吧",
                   "好", "嗯", "行", "知道了", "好的", "收到", "晚安")
_CONFUSED_WORDS = ("啥呀", "什么呀", "啥意思", "什么意思", "说啥", "看啥")


def strip_confused_short_reply(reply, user_text, bot_text=""):
    """他只回一个很短的动作词（慢慢看/去吧/好）时，别反问「啥呀」；把空问句接回去。"""
    reply = str(reply or "")
    body = str(user_text or "").strip()
    if not reply or not body or len(body) > 10:
        return reply
    if not any(word in body for word in _SHORT_CONTINUE):
        return reply
    if not any(word in reply for word in _CONFUSED_WORDS):
        return reply
    out = reply
    for verb in ("慢慢看", "慢慢吃", "慢慢来", "慢慢走", "慢慢说", "看", "吃"):
        out = out.replace(verb + "啥呀", verb + "呀")
        out = out.replace(verb + "什么呀", verb + "呀")
    parts = re.split("(?<=[。！？!?~～" + chr(10) + "])", out)
    kept = [part for part in parts if not any(word in part for word in ("啥意思", "什么意思", "说啥"))]
    out = "".join(kept).strip()
    return out or reply


# ---------------------------------------------------------------------------
# 媒体记录标记（2026-11-22 task-15，10:32 真实 P0）
#
# 「[我发的照片] 自拍」「[我发的语音] …」是给审计/记忆用的内部记录，不是人话。
# 线上模型在最近对话里看到它，就把它当成一句可以复读的话抄进正文；她此刻在家，
# 连照片记录里的旧地点也一起复读。这里只做两件事：
# - 读侧（_recent / 记忆注入）整行剔除，绝不进 LLM 可复读文本；
# - 写侧出站前 strip，人话里不出现占位符（真正的图仍由图片组件发）。
# ---------------------------------------------------------------------------
_MEDIA_MARKER_RE = re.compile(r"[\[【]\s*我发的(?:照片|语音)[^\]】]*[\]】]\s*(?:自拍|随手拍|一段语音)?")
_MEDIA_RECORD_RE = re.compile(r"^\s*[\[【]\s*我发的(?:照片|语音)")


def is_media_record(text):
    """这行是不是机器人自己发的照片/语音占位记录（不是一句人话）。"""
    return bool(_MEDIA_RECORD_RE.match(str(text or "")))


def strip_media_markers(text):
    """去掉正文里的媒体占位符（连同紧跟的「自拍/随手拍/一段语音」说明）。"""
    return _MEDIA_MARKER_RE.sub("", str(text or "")).strip()


def sanitize_context_rows(plugin, rows):
    """喂给模型的近聊行：媒体占位整行剔除；她自己旧场景断言降级成过去时。"""
    clean_rows = []
    demote, scene = None, None
    try:
        try:
            from .amo_state_consistency import current_scene as _scene_of
            from .amo_state_consistency import demote_past_claims as _demote
        except ImportError:
            from amo_state_consistency import current_scene as _scene_of
            from amo_state_consistency import demote_past_claims as _demote
        demote = _demote
        scene = _scene_of(plugin)
    except Exception:
        demote, scene = None, None
    for row in list(rows or []):
        if not isinstance(row, dict):
            clean_rows.append(row)
            continue
        content = str(row.get("content") or "")
        if not content.strip():
            clean_rows.append(row)
            continue
        if is_media_record(content):
            continue
        cleaned = strip_media_markers(content)
        role = str(row.get("role") or "")
        if demote is not None and role in ("bot", "assistant") and cleaned:
            try:
                cleaned = demote(cleaned, plugin, scene)
            except Exception:  # cleanup: 兜底/降级，保持原行为
                pass
        if not cleaned.strip():
            continue
        if cleaned != content:
            row = dict(row)
            row["content"] = cleaned
        clean_rows.append(row)
    return clean_rows


class MessageMixin(_MixinBase):

    def _strip_repeated_urge(self, reply, user_text, bot_text):
        """他已经答应/要去做了，就别再催（确定性兜底，提示词之外）。"""
        return strip_repeated_urge(reply, user_text, bot_text)
    def _strip_confused_short_reply(self, reply, user_text, bot_text=""):
        """他只回「慢慢看/去吧」这类短句时，别反问「啥呀」（确定性兜底）。"""
        return strip_confused_short_reply(reply, user_text, bot_text)
    def _strip_repeated_food_question(self, reply, user_text, bot_text):
        """他刚回答「中午吃什么」，就别再问「你点了吗 / 吃了吗」。"""
        reply = str(reply or "")
        user_text = str(user_text or "")
        bot_text = str(bot_text or "")
        if not reply or not user_text or not bot_text:
            return reply
        if not re.search(r"(吃啥|吃什么|吃点什么|中午吃|午饭吃|晚饭吃)", bot_text):
            return reply
        if len(user_text) > 20 or not any(word in user_text for word in _FOOD_WORDS):
            return reply
        parts = re.split(r"(?<=[。！？!?~～\n])", reply)
        kept = [part for part in parts
                if not re.search(r"(点了吗|点没|点了没|吃了吗|吃了没|点了没呀)", part)]
        out = "".join(kept).strip()
        return out or "听着就香。"
    def _recent(self, umo, limit=10):
        """近聊上下文钩子：能力层缓冲优先，没有再读本地缓存表。"""
        size = max(1, int(limit or 10))
        rows = []
        hub = self._memory_hub()
        if hub is not None:
            try:
                rows = hub.buffer.recent(umo, size)
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._recent", _amo_exc)
                rows = []
        if not rows:
            try:
                rows = list((getattr(self, "_ctx_buffer", {}) or {}).get(str(umo), []))
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._recent", _amo_exc)
                rows = []
        if len(rows) >= size:
            return sanitize_context_rows(self, rows[-size:])
        try:
            local = self.db.recent_messages(umo, size)
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._recent", _amo_exc)
            local = []
        if not rows:
            return sanitize_context_rows(self, local)
        # 缓冲只有一小段时，用本地补前面
        known = {(row.get("role"), row.get("content")) for row in rows}
        merged = [row for row in local if (row.get("role"), row.get("content")) not in known]
        merged.extend(rows)
        return sanitize_context_rows(self, merged[-size:])

    async def _recent_by_uid(self, uid, limit=20, umo=""):
        """取近聊：优先按会话，其次按 uid；都没有再退回缓冲/本地缓存。"""
        uid = str(uid or "")
        size = max(1, int(limit or 20))
        mem = self._data_mem()
        lister = getattr(mem, "list_messages", None) if mem is not None else None
        if callable(lister) and umo:
            # 按会话取：bot 行的 uid 是字面量 "bot"，按 uid 过滤会把她说的话全滤掉
            try:
                rows = await lister(self._data_bot_id(), str(umo), size) or []
            except TypeError:
                rows = []
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._recent_by_uid", _amo_exc)
                rows = []
            if rows:
                return sanitize_context_rows(
                    self, sorted(rows, key=lambda item: to_float(item.get("created_at"), 0))[-size:])
        if callable(lister):
            try:
                rows = await lister(self._data_bot_id(), "", size, uid=uid) or []
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._recent_by_uid", _amo_exc)
                rows = []
            if rows:
                return sanitize_context_rows(
                    self, sorted(rows, key=lambda item: to_float(item.get("created_at"), 0))[-size:])
        merged = []
        hub = self._memory_hub()
        if hub is not None:
            try:
                merged = hub.buffer.by_uid(uid, size)
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._recent_by_uid", _amo_exc)
                merged = []
        if not merged:
            for rows in list((getattr(self, "_ctx_buffer", {}) or {}).values()):
                merged.extend([row for row in rows if str(row.get("uid") or "") == uid])
        if merged:
            return sanitize_context_rows(
                self, sorted(merged, key=lambda item: to_float(item.get("created_at"), 0))[-size:])
        try:
            return sanitize_context_rows(self, self.db.messages_by_uid(uid, size))
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._recent_by_uid", _amo_exc)
            return []

    async def _hydrate_recent(self, umo, limit=30):
        """从记忆终端把这个会话最近的话捞回内存，重启后也有上下文。"""
        umo = str(umo or "")
        if not umo:
            return
        if str(umo) in getattr(self, "_ctx_hydrated", set()):
            return
        rows: list[Any] = []
        hub = self._memory_hub()
        if hub is not None:
            try:
                rows = await hub.recent(umo, "", max(1, int(limit))) or []
            except Exception as exc:
                logger.debug("[amo] 能力层取上下文失败: %s" % exc)
                rows = []
        if not rows:
            mem = self._data_mem()
            lister = getattr(mem, "list_messages", None) if mem is not None else None
            if callable(lister):
                try:
                    rows = await lister(self._data_bot_id(), umo, max(1, int(limit))) or []
                except Exception as exc:
                    logger.debug("[amo] 从记忆终端取上下文失败: %s" % exc)
                    rows = []
        if not rows:
            try:
                rows = self.db.recent_messages(umo, max(1, int(limit)))
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._hydrate_recent", _amo_exc)
                rows = []
        if not rows:
            return
        ordered = sorted(rows, key=lambda item: to_float(item.get("created_at"), 0))
        buf = getattr(self, "_ctx_buffer", None)
        if buf is None:
            buf = {}
            self._ctx_buffer = buf
        buf[umo] = [
            {
                "umo": umo,
                "uid": str(item.get("uid") or ""),
                "name": str(item.get("name") or ""),
                "role": "bot" if str(item.get("role")) == "bot" else "user",
                "content": str(item.get("content") or ""),
                "created_at": to_float(item.get("created_at"), 0),
            }
            for item in ordered[-self.CONTEXT_BUFFER_LIMIT:]
        ]
        self._ctx_hydrated: Any = getattr(self, "_ctx_hydrated", set())
        self._ctx_hydrated.add(str(umo))

    def _is_other_bot(self, event) -> bool:
        """发言人是不是群里的其他机器人（只在她开“不跟机器人聊”时生效）。"""
        engine = getattr(self, "heartflow", None)
        if engine is None or not getattr(engine, "ignore_bots", True):
            return False
        try:
            return bool(engine.is_other_bot_sender(event))
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._is_other_bot", _amo_exc)
            return False

    def _is_talking_to_me(self, event):
        """这句是不是冲她来的：私聊、被 @ / 叫名字、或心流判定对象是 bot。"""
        try:
            if event.is_private_chat():
                return True
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._is_talking_to_me", _amo_exc)
            return True
        target = str(getattr(event, "heartflow_target", "") or "")
        if target == "bot":
            return True
        if str(getattr(event, "heartflow_mode_hint", "") or "") == "interject":
            return False
        try:
            if getattr(event, "is_at_or_wake_command", False):
                return True
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._is_talking_to_me", _amo_exc)
        try:
            if self.heartflow is not None and self.heartflow._addresses_bot(event.message_str or ""):
                return True
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._is_talking_to_me", _amo_exc)
        return False

    _SOOTHE_WORDS = ("快去睡", "快睡", "去睡", "早点睡", "别熬夜", "不要熬夜",
                     "睡觉吧", "睡吧", "晚安")

    def _try_soothe(self, uid, text):
        """他说“快去睡/早点睡/别熬夜”时，让熬夜系统记一笔「被哄睡」。

        2026-11-15：这里原来调用了一个从未定义过的 _try_soothe，私聊消息
        100% 抛 AttributeError，被 runtime 的兜底 except 吞掉 → 私聊完全不回复。
        """
        if not any(word in (text or "") for word in self._SOOTHE_WORDS):
            return
        late = getattr(getattr(self, "state", None), "late", None)
        if late is None:
            return
        try:
            if late.is_staying_up():
                late.soothe("老公哄睡")
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._try_soothe", _amo_exc)

    async def _handle_message(self, event):
        if not self._ready or self.state is None:
            logger.warning("[amo] 还没初始化完成，这条消息先不处理（ready=%s）" % self._ready)
            return
        self._reload_config_if_changed()
        await self._sync_persona_name(event)
        try:
            self.state.apply_schedule_events()
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._handle_message", _amo_exc)
        uid = self._canonical_uid(event)
        if not uid or uid == str(event.get_self_id()):
            logger.debug("[GATE] chat=unknown skip reason=no_uid")
            return
        text = (event.message_str or "").strip()
        if not text:
            logger.debug("[GATE] chat=unknown skip reason=empty_text")
            return
        if self._looks_like_command(text):
            logger.debug("[GATE] chat=command skip reason=command")
            return

        umo = event.unified_msg_origin
        private = event.is_private_chat()
        if not private and self._is_other_bot(event):
            # 群里别的机器人：@她也不接，避免两个机器人互相聊
            logger.info("[GATE] chat=group skip reason=other_bot sender=%s", event.get_sender_name())
            try:
                if self.heartflow is not None:
                    self.heartflow.note_other_bot(event)
                event.stop_event()
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._handle_message", _amo_exc)
            return
        if private:
            self._clear_proactive_pending(uid)
            self._try_soothe(uid, text)
        if private and not self.cfg_bool("enable_private_reply", True):
            logger.debug("[GATE] chat=private skip reason=private_reply_off")
            return
        if not private and not self.cfg_bool("enable_group_reply", True):
            logged = getattr(self, "_group_off_logged", None)
            if logged is None:
                logged = set()
                self._group_off_logged = logged
            if umo not in logged:
                logged.add(umo)
                logger.info("[GATE] chat=group skip reason=group_reply_off umo=%s", umo)
            return
        if private and self.cfg_bool("private_debounce_enabled", True):
            # 同一个人连着发的几条先合起来，只让最后一条走完整回复流程
            ok_debounce = await self._debounce_private(event, text)
            if not ok_debounce:
                logger.debug("[GATE] chat=private skip reason=debounce_buffer")
                return
            text = (event.message_str or "").strip()
            if not text:
                logger.debug("[GATE] chat=private skip reason=empty_after_debounce")
                return

        if not private:
            try:
                self._detect_impersonation(event)
            except Exception as exc:
                logger.debug("[amo] 冒充检测失败: %s" % exc)
        await self._learn(event, text)
        self._restore_lover_relation(uid)
        profile = self.db.get_profile(uid)

        if private:
            await self._handle_private(event, profile)
            return

        # 群聊只走完整心流引擎；旧的五维门控/防抖/直发逻辑不再参与。
        if self.heartflow is None or not self.cfg_bool("hf_enable_heartflow", True):
            logger.debug("[GATE] chat=group skip reason=heartflow_off")
            return
        try:  # task-30：此刻地点/动作唯一读场景账本（含请假/出差硬约束）
            from .amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        except ImportError:
            from amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        snap = _snap_with_scene(self)
        if self._should_disturb(snap, umo, uid, text, group=True):
            self._apply_disturbance(umo)
            setattr(event, "amo_force_reply", True)
        try:
            await self.heartflow.on_group_message(event)
        except Exception as exc:
            logger.error("[amo] 心流处理失败: %s" % exc)

    async def _handle_private(self, event, profile):
        try:  # task-30：此刻地点/动作唯一读场景账本（含请假/出差硬约束）
            from .amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        except ImportError:
            from amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        snap = _snap_with_scene(self)
        umo = event.unified_msg_origin
        uid = self._canonical_uid(event)
        if self._recent_bot_initiated(umo):
            self._disturb_until[str(umo)] = 0
            logger.info("[amo] 主动消息后的回复，按正常聊天处理")
            return
        if self._should_disturb(snap, umo, uid, event.message_str or "", group=False):
            self._apply_disturbance(umo)
            # 不 stop_event，让 AstrBot 正常生成；<amo_state> 会带上被打扰的语气。
            logger.debug("[GATE] chat=private skip reason=disturb_mode")
            return
        probability = self.gate.private_probability(snap, profile)
        roll = random.random()
        logger.info(
            "[amo] 私聊判定 | 关系=%s | 概率=%.2f | roll=%.2f | %s"
            % (profile.get("relation", "普通"), probability, roll,
               "回复" if roll <= probability else "不回")
        )
        if roll <= probability:
            return
        event.stop_event()
        if (
            self.cfg_bool("delay_reply", True)
            and probability >= 0.20
            and profile.get("relation", "普通") not in ("仇恨", "抵触")
            and random.random() < 0.45
        ):
            self._schedule_delayed_reply(event, profile)

    async def _handle_group_directed(self, event, text, profile):
        umo = event.unified_msg_origin
        group_id = event.get_group_id()
        try:  # task-30：此刻地点/动作唯一读场景账本（含请假/出差硬约束）
            from .amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        except ImportError:
            from amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        snap = _snap_with_scene(self)
        recent = self._recent(umo, 8 if self.cfg_str("context_style", "lean") == "lean" else 12)
        result = await self.gate.should_reply_group(
            umo, group_id, text, recent, True, profile, snap
        )
        if self.cfg_bool("debug_log", False):
            logger.debug("[amo] 群聊定向判定: %s" % json.dumps(result, ensure_ascii=False))
        if not result["reply"]:
            event.stop_event()

    async def _debounce_private(self, event, text):
        """私聊防抖：同一会话连发的几条合并成一句，只让最后一条继续回复。

        返回 True = 这条是这一批的最后一条（合并后的文本已写回 event.message_str）；
        返回 False = 已被合并，事件保持 stop 状态，调用方直接 return。
        """
        umo = event.unified_msg_origin
        delay = max(0.3, self.cfg_float("private_debounce_seconds", 6.0))
        max_wait = max(delay + 0.1, self.cfg_float("private_debounce_max_wait", 14.0))
        now = time.monotonic()
        buffer = self._private_buffers.get(umo)
        if buffer is None:
            buffer = {"seq": 0, "texts": [], "started": now, "updated": now}
            self._private_buffers[umo] = buffer
        buffer["seq"] += 1
        my_seq = buffer["seq"]
        buffer["texts"].append(str(text or ""))
        buffer["updated"] = now
        event.stop_event()
        deadline = buffer["started"] + max_wait
        while True:
            current = time.monotonic()
            if self._private_buffers.get(umo) is not buffer:
                return False  # 这一批已经被前面的某条发出去了
            if my_seq != buffer["seq"]:
                return False  # 后面又来了新的，交给最新的那条
            quiet_at = buffer["updated"] + delay
            if current >= quiet_at or current >= deadline:
                break
            try:
                await asyncio.sleep(min(quiet_at, deadline) - current)
            except asyncio.CancelledError:
                return False
        if self._private_buffers.get(umo) is buffer:
            self._private_buffers.pop(umo, None)
        merged = "｜".join(item for item in buffer["texts"] if item).strip()[:300]
        if len(buffer["texts"]) > 1:
            logger.info("[amo] 私聊合并 %d 条: %s" % (len(buffer["texts"]), merged[:200]))
            try:
                event.set_extra("amo_debounce_count", len(buffer["texts"]))
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._debounce_private", _amo_exc)
        event.message_str = merged or (event.message_str or "")
        try:
            event.continue_event()
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._debounce_private", _amo_exc)
        return True

    def _strip_place_marks(self, text):
        """兜底：把「东莞·商场·餐厅」这类内部地点串缩成口语的「餐厅」。"""
        value = str(text or "")
        marks = set()
        try:
            city = str((self.state.worldview or {}).get("city") or "").strip()
            if city:
                marks.add(city)
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._strip_place_marks", _amo_exc)
        try:
            marks.add(str((self.state.current_activity() or {}).get("place") or ""))
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._strip_place_marks", _amo_exc)
        for mark in marks:
            parts = [item.strip() for item in mark.split("·") if item.strip()]
            if len(parts) < 2:
                continue
            for index in range(len(parts) - 1, 0, -1):
                value = value.replace("·".join(parts[:index]) + "·", "")
        return value

    def _segment(self, text):
        if not text:
            return []
        if not self.cfg_bool("seg_enable", True) or self.segment_engine is None:
            return [text]
        try:
            segments = self.segment_engine.segment_text_by_rules(text)
            return segments or [text]
        except Exception as exc:
            logger.debug("[amo] 分段引擎异常: %s" % exc)
            return [text]

    def typing_seconds(self, text):
        if not self.cfg_bool("typing_simulate", True):
            return 0.0
        return typing_delay_ms(
            text,
            self.cfg_int("typing_base_ms", 600),
            self.cfg_int("typing_per_char_ms", 55),
            self.cfg_int("typing_max_ms", 4500),
        ) / 1000.0

    async def _typing(self, event, text):
        seconds = self.typing_seconds(text)
        if seconds <= 0:
            return
        try:
            if event is not None and hasattr(event, "send_typing"):
                await event.send_typing()
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._typing", _amo_exc)
        await asyncio.sleep(seconds)

    async def _segment_wait(self, event, segment, prev_segment, last_sent_at):
        """按当前分段字数补足打字时间；已过去的时间可以抵扣，但不会低于分段间隔。"""
        required = self.typing_seconds(segment)
        gap = await self._gap(prev_segment, segment)
        target = max(required, gap)
        elapsed = max(0.0, time.monotonic() - last_sent_at) if last_sent_at else 0.0
        wait = max(0.0, target - elapsed)
        logger.info(
            "[amo] 分段延迟（从上一段发送后计时） | 字数=%d | 需要=%.2fs | 已过=%.2fs | 补足=%.2fs"
            % (len(re.sub(r"\s+", "", str(segment or ""))), target, elapsed, wait)
        )
        if wait > 0.01:
            try:
                if event is not None and hasattr(event, "send_typing"):
                    await event.send_typing()
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._segment_wait", _amo_exc)
            await asyncio.sleep(wait)
        return wait

    async def _gap(self, text, next_text=""):
        if self.segment_engine is not None and self.cfg_bool("seg_enable", True):
            try:
                return max(0.0, float(self.segment_engine._calculate_delay(text, next_text)))
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._gap", _amo_exc)
        return 0.3

    def _platform_for_umo(self, umo):
        """umo 对应的平台名。QQ 的 umo 前缀是机器人名字，不能直接拿第一段当平台。"""
        umo = str(umo or "")
        cached = (getattr(self, "_umo_platforms", None) or {}).get(umo)
        if cached:
            return cached
        head = umo.split(":", 1)[0].strip().lower()
        if head in ("qq", "onebot", "onebotv11"):
            return "aiocqhttp"
        if head in ("aiocqhttp", "lark", "webchat", "telegram", "discord"):
            return head
        bn = str(getattr(self, "bot_name", "") or "").strip().lower()
        if bn and head == bn:
            return "aiocqhttp"
        if ":FriendMessage:" in umo or ":GroupMessage:" in umo:
            return "aiocqhttp"
        return head

    def _remember_bot_message(self, umo, text):
        """把机器人自己发出去的话也写进 messages 表。

        这张表既是主动消息的上下文，也是「这句话回过没有」的唯一依据。
        正常回复走的是分段引擎，以前只写 AstrBot 的会话历史，表里没有，
        主动消息就会把已经答过的话当成没答过，再答一遍。
        """
        umo = str(umo or "").strip()
        text = str(text or "").strip()
        if not umo or not text:
            return
        last_user = ""
        try:
            saved = self._last_bot_saved.get(umo)
            try:
                for row in reversed(self._recent(umo, 4) or []):
                    if str(row.get("role") or "") == "user":
                        last_user = str(row.get("content") or "").strip()
                        break
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._remember_bot_message", _amo_exc)
                last_user = ""
            # 同一句在 3 分钟内又发一次很常见（「嗯嗯」「好呀」）；只有「对应的
            # 他最后那句话也没变」才算重复。以前只看文字，第二次「好呀」会被丢掉，
            # 表里就变成「他还没被回过」，主动消息再把同一句答一遍。
            if saved and saved[0] == text and _amo_clock.now_ts() - saved[1] < 180:
                saved_user = str(saved[2] if len(saved) > 2 else "")
                if saved_user == last_user:
                    return
            self._ctx_remember(umo, "bot", self.bot_name, "bot", text)
            # 本地 messages 表不再缓存原始聊天；上下文只在内存，长期记录写记忆终端。
            # 直接同步写进记忆终端：fire-and-forget 在 reload/退出时会丢这句
            saved = False
            store = self._data_store()
            if store is not None and callable(getattr(store, "add_message", None)):
                try:
                    store.add_message(
                        self._data_bot_id(), umo, "bot", self.bot_name, "bot", text,
                        platform=self._platform_for_umo(umo),
                    )
                    saved = True
                except TypeError:
                    try:
                        store.add_message(self._data_bot_id(), umo, "bot", self.bot_name, "bot", text)
                        saved = True
                    except Exception as exc:
                        logger.warning("[amo] 自己的回复写入终端失败: %s" % exc)
                except Exception as exc:
                    logger.warning("[amo] 自己的回复写入终端失败: %s" % exc)
            if not saved:
                try:
                    loop = asyncio.get_running_loop()
                    task = loop.create_task(self._data_call(
                        "add_message", self._data_bot_id(), umo, "bot", self.bot_name, "bot", text,
                        platform=self._platform_for_umo(umo),
                    ))
                    def _log_failed_task(task):
                        if task.cancelled():
                            return
                        exc = task.exception()
                        if exc is not None:
                            logger.warning("[amo] 自己的回复异步落库失败: %s" % exc)

                    task.add_done_callback(_log_failed_task)
                except Exception as _amo_exc:
                    _note_swallowed("amo_message.HumanCorePlugin._remember_bot_message", _amo_exc)
            self._last_bot_saved[umo] = (text, _amo_clock.now_ts(), last_user)
        except Exception as exc:
            logger.debug("[amo] 记录自己的回复失败: %s" % exc)

    async def _remember_bot_media(self, umo, text):
        """记下她自己发过的照片/语音：聊天记录里留一条，私聊再写进长期记忆。"""
        marker = str(text or "").strip()
        if not marker:
            return
        if "测试图" in marker:
            # 面板/命令的测试图不是日常经历：聊天记录和长期记忆都不写，主动消息才不会拿它当话题
            logger.debug("[amo] 测试图不写入媒体记忆: %s", marker[:40])
            return
        try:
            self._remember_bot_message(umo, marker)
        except Exception as exc:
            logger.debug("[amo] 记录媒体消息失败: %s" % exc)
        if ":FriendMessage:" not in str(umo):
            return
        uid = str(umo).rsplit(":", 1)[-1].strip()
        if not uid or uid == "bot":
            return
        # task-30：照片/语音也是长期记忆写者，统一过场景守卫（只改「现在/此刻」的错地点，
        # 空串=这条不写）。标记句本身不含地点，守卫防的是以后 caption 混进媒体记忆。
        memory_text = marker
        try:
            try:
                from .amo_scene_ledger import guard_memory_text as _guard_memory
            except ImportError:
                from amo_scene_ledger import guard_memory_text as _guard_memory
            memory_text = str(_guard_memory(self, marker) or "")
        except Exception:  # cleanup: 记忆守卫异常时保留原标记
            memory_text = marker
        if not memory_text:
            logger.debug("[amo] 媒体记忆被场景守卫丢弃: %s", marker[:40])
            return
        try:
            await self._data_memory_write(
                uid, memory_text, kind="bot_media", tier=0, scope="private", subject="bot",
            )
        except Exception as exc:
            logger.debug("[amo] 媒体记忆写入失败: %s" % exc)

    async def _send_now(self, event, text, quote_id=None):
        """跳过分段引擎直接发一条（seg 关闭时，用来绕开 AstrBot 核心的引用/@）。"""
        if Comp is None or MessageChain is None:
            return False
        # 媒体占位符不是人话：直发路径同样剥掉（真正需要发图时走图片组件）。
        text = strip_media_markers(text)
        # task-26：出站技术词黑名单（聊天直发路径）；全命中时换成角色化短句。
        text = strip_tech_terms(text, source="chat")
        if not str(text or "").strip():
            return False
        # task-28：直发路径也过一遍人设/报账数字/姓名守卫（动作-only 不发）。
        try:
            try:
                from .amo_persona_guard import prepare_outbound as _prepare_outbound
            except ImportError:
                from amo_persona_guard import prepare_outbound as _prepare_outbound
            _umo = str(getattr(event, "unified_msg_origin", "") or "")
            text, _guard_reason = _prepare_outbound(self, _umo, text, "chat")
            if not text:
                logger.info("[amo] 直发被语义守卫拦截：%s", _guard_reason)
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._send_now.persona", _amo_exc)
        chain = []
        if quote_id is not None:
            try:
                chain.append(Comp.Reply(id=quote_id))
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._send_now", _amo_exc)
        chain.append(Comp.Plain(text))
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        # task-16：这是 chat 直发路径，登记 chat 批，期间非 chat 推送排队不插队。
        _batch = _gateway.begin_chat_batch(self, umo, "chat-now:%s" % time.monotonic())
        try:
            await event.send(MessageChain(chain))
            _gateway.note_chat(self, umo, text, _batch)
        except Exception as exc:
            logger.error("[amo] 直接发送失败: %s" % exc)
            return False
        finally:
            _gateway.end_chat_batch(self, umo, _batch)
        self._remember_sent_reply(event, text)
        try:
            self._last_reply_at[event.unified_msg_origin] = now_ts()
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._send_now", _amo_exc)
        logger.info("[amo] 直接发送 -> %s: %s" % (event.unified_msg_origin, text[:300]))
        return True

    async def _rewrite_without_foreign(self, umo, text, dirty):
        """回复里出现白名单外的名字：先让模型重写一遍；重写不干净就返回空。"""
        try:
            llm = getattr(self, "llm", None)
            if llm is None or not hasattr(llm, "chat_raw"):
                return ""
            try:
                from .amo_names import ROSTER_LINE, foreign_names
            except ImportError:
                from amo_names import ROSTER_LINE, foreign_names
            system = ("你是她本人，只输出改写后的一句话，不要解释。\n" + ROSTER_LINE)
            prompt = ("这句话里出现了不存在的名字（%s），保持原意重写，删掉这些名字、不要换新名字。\n原句：%s"
                      % ("、".join(dirty), text))
            out = str(await llm.chat_raw(umo, system, prompt) or "").strip()
            if out and not foreign_names(out):
                return out
        except Exception as exc:
            logger.debug("[amo] 人名重写失败: %s", exc)
        return ""

    def _outbound_allowed(self, umo, text, source=None):
        """统一出口去重闸：同人 + 同来源，90 秒内近似重复只发一次。

        来源走 contextvar（proactive/push/photo），时间走插件时钟 now_ts()。
        以前把来源字符串塞进 allow() 的 now 参数，float("proactive") 抛异常被吞，
        去重实际恒放行（07:30 4 秒 7 条的根因之一）。
        """
        try:
            try:
                from .amo_outbound_guard import allow as _outbound_allow
            except ImportError:
                from amo_outbound_guard import allow as _outbound_allow
            src = str(source or getattr(self, "_outbound_source", "chat"))
            return bool(_outbound_allow(self, umo, text, source=src, now=now_ts()))
        except Exception as _amo_exc:
            _note_swallowed("amo_message.MessageMixin._outbound_allowed", _amo_exc)
            return True

    async def _send_to_umo(self, umo, text, quote_id=None, group=False,
                            source=None, kind=""):
        """主动/推送文本的唯一发送出口（task-16）。

        source 必须显式或由调用方设置 _outbound_source；缺省 fail-closed 当 push，
        不再默认 chat。去重、频控、chat 批互斥、outbound_log 都在 amo_outbound。
        """
        # Phase 6：发送前的守卫不再手写在这里，统一走 amo_reply_pipeline 的 send 阶段
        context = _reply_pipeline.context_for_send(self, umo, group=bool(group))
        text = (await _reply_pipeline.run(text, context))["text"]
        # 媒体占位符只用于内部记录，任何出站文本都剥掉（图仍由图片组件发）。
        text = strip_media_markers(text)
        # task-32：收件人/角色反转唯一守卫（主动/push/延迟补话/外部插件推送都从这里出）。
        # 11:31 真实样例的链路是 watch request_push -> push_to_owner -> 这里，source=push；
        # 判定只在 amo_addressee_guard 一处，日志和 outbound_log 都带 source，便于追外部推送。
        _addr_src = str(source or getattr(self, "_outbound_source", "") or "push")
        _addr_raw = str(text or "")
        try:
            try:
                from .amo_addressee_guard import enforce as _addressee_enforce
            except ImportError:
                from amo_addressee_guard import enforce as _addressee_enforce
            text, _addr_reason = _addressee_enforce(self, umo, text, source=_addr_src)
            if not text:
                logger.info("[amo] 收件人守卫丢弃（source=%s, reason=%s）：%s",
                            _addr_src, _addr_reason, _addr_raw[:120])
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_message._send_to_umo.addressee", _amo_exc)
            text = _addr_raw
        _user_text = ""
        try:
            try:
                from .amo_anti_nag import rewrite as _anti_nag
            except ImportError:
                from amo_anti_nag import rewrite as _anti_nag
            _user_text = ""
            try:
                for _row in reversed(list(self._recent(umo, 3) or [])):
                    if str(_row.get("role") or "") == "user":
                        _user_text = str(_row.get("content") or "")
                        break
            except Exception:
                _user_text = ""
            text = _anti_nag(text, _user_text)
            try:
                try:
                    from .amo_purchase_guard import rewrite as _purchase_guard
                except ImportError:
                    from amo_purchase_guard import rewrite as _purchase_guard
                text = _purchase_guard(text, self)
            except Exception as _amo_exc:
                _note_swallowed("amo_message.purchase_guard", _amo_exc)
        except Exception as _amo_exc:
            _note_swallowed("amo_message.anti_nag", _amo_exc)
        # task-15 拼装前统一校验（聊天/主动共用）：媒体标记 -> 自我复读分句 -> 当前场景冲突。
        try:
            try:
                from .amo_state_consistency import validate_reply_assembly as _validate_assembly
            except ImportError:
                from amo_state_consistency import validate_reply_assembly as _validate_assembly
            text = _validate_assembly(text, self, umo=umo,
                                      user_text=str(_user_text or ""))
        except Exception as _amo_exc:
            _note_swallowed("amo_message._send_to_umo.validate_assembly", _amo_exc)
        # task-26：出站统一技术词黑名单。主动/push/系统通知/后台补话都走这里；
        # 整条只剩技术话时按来源处理（system/push 直接不发，chat 换成角色化短句）。
        try:
            _tech_src = str(source or getattr(self, "_outbound_source", "") or "push")
            _tech_kind = str(kind or getattr(self, "_outbound_kind", "") or "")
            text = strip_tech_terms(text, source=_tech_src, kind=_tech_kind)
        except Exception as _amo_exc:
            _note_swallowed("amo_message._send_to_umo.tech_terms", _amo_exc)
        if not text or not umo:
            return False
        # 归一化前移：旁白剥离是语义操作，分段是结构操作。必须先剥再分段，
        # 否则模型写成跨行的括号动作会被分段器劈成两半（2026-11-22 线上
        # 「嘴角一下翘起来）我也想你了…」）。
        try:
            text = _strip_action_narration(text)
        except Exception as _amo_exc:
            _note_swallowed("amo_message._send_to_umo.action_narration", _amo_exc)
        if not text:
            return False
        segments = self._segment(text)
        if not segments:
            return False
        # 出口不变量：任何一段都不许带不成对的全角括号。能剥则剥，剥不干净就
        # 丢弃这一段（宁可少发一句，也不把残句发到飞书），并留下可检索日志。
        try:
            _safe_segments = []
            for _seg in segments:
                if _has_orphan_paren(_seg):
                    _fixed = _strip_action_narration(_seg)
                    if not _fixed or _has_orphan_paren(_fixed):
                        logger.info("[amo] 出站残括号丢弃：%s", str(_seg)[:80])
                        continue
                    _seg = _fixed
                _safe_segments.append(_seg)
            segments = _safe_segments
        except Exception as _amo_exc:
            _note_swallowed("amo_message._send_to_umo.orphan_invariant", _amo_exc)
        if not segments:
            return False
        # task-15 分段去重：后段和最近说过的话/本轮前面的段重复就整段不发。
        try:
            try:
                from .amo_state_consistency import dedupe_reply_segments as _dedupe_segments
            except ImportError:
                from amo_state_consistency import dedupe_reply_segments as _dedupe_segments
            segments = _dedupe_segments(segments, plugin=self, umo=umo) or [text]
        except Exception as _amo_exc:
            _note_swallowed("amo_message._send_to_umo.dedupe_segments", _amo_exc)
        src = str(source or getattr(self, "_outbound_source", "") or "push")
        _kind = str(kind or getattr(self, "_outbound_kind", "") or "")
        _batch = str(getattr(self, "_outbound_batch", "") or "")
        last_sent_at = 0.0
        for index, segment in enumerate(segments):
            # 含分段：每一段发出前和最近 3-5 条 bot 消息 / 本轮前面的段比对，
            # 整句复读就换一句短的（绝不发空段）。
            try:
                segment = _reply_pipeline.apply_segment_repeat_guard(segment, self, umo)
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._send_to_umo.segment_dedup", _amo_exc)
            if index > 0:
                await self._segment_wait(None, segment, segments[index - 1], last_sent_at)
            elif last_sent_at == 0.0:
                await self._segment_wait(None, segment, "", 0.0)
            chain = []
            if (
                index == 0 and quote_id is not None
                and self._quote_allowed(umo, group)
            ):
                try:
                    chain.append(Comp.Reply(id=quote_id))
                except Exception as _amo_exc:
                    _note_swallowed("amo_message.HumanCorePlugin._send_to_umo", _amo_exc)
            chain.append(Comp.Plain(segment))

            async def _transport(chain=chain):
                await self.context.send_message(umo, MessageChain(chain))

            sent_ok = await _gateway.send(self, umo, segment, source=src, kind=_kind,
                                          batch_id=_batch, transport=_transport)
            if not sent_ok:
                if index > 0:
                    # 前面的分段已经发出去了，返回 True，别让换边逻辑整段重发
                    partial = "\n".join(segments[:index])
                    self._remember_bot_message(umo, partial)
                    self._last_reply_at[umo] = now_ts()
                    return True
                return False
            last_sent_at = time.monotonic()
        self._last_reply_at[umo] = now_ts()
        logger.info("[amo] 主动 -> %s: %s" % (umo, text[:300]))
        # task-28：落库/近聊里绝不能留动作旁白（否则下一轮又被模型复读回去）。
        _memory_text = text
        try:
            try:
                from .amo_persona_guard import strip_action_narration as _strip_actions
            except ImportError:
                from amo_persona_guard import strip_action_narration as _strip_actions
            _memory_text = _strip_actions(text) or text
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._send_to_umo.memory_text", _amo_exc)
        self._remember_bot_message(umo, _memory_text)
        return True
    async def _send_media_to_umo(self, umo, components, event_id="", source="photo",
                                  kind="photo"):
        """图片统一出口（task-16）：同一 event_id 90 秒内最多一张 + 频控 + 日志。

        同一 event_id 最多一张图由 amo_presence.claim_photo_event 在调用方先占位；
        这里再走 amo_outbound.send_media 做出口级去重/频控/outbound_log。
        """
        if not umo or MessageChain is None or not components:
            return False
        # task-26：照片 caption 也走技术词黑名单；命中就只发图（不切半句）。
        _plain_cls = getattr(Comp, "Plain", None)
        payload = []
        for comp in components:
            if _plain_cls is not None and isinstance(comp, _plain_cls):
                caption = sanitize_caption(getattr(comp, "text", ""))
                if not caption:
                    continue
                try:
                    comp.text = caption
                except Exception:  # pragma: no cover - 组件字段不可写时重建
                    comp = _plain_cls(caption)
            payload.append(comp)
        if not payload:
            # 组件全被剥掉（理论上不会，图片组件不是 Plain）：宁可不发也不发半句。
            return False

        async def _transport():
            await self.context.send_message(umo, MessageChain(payload))

        ok = await _gateway.send_media(self, umo, payload, event_id=event_id,
                                       source=str(source or "photo"), kind=kind,
                                       transport=_transport)
        if not ok:
            return False
        try:
            self._last_reply_at[umo] = now_ts()
        except Exception as _amo_exc:
            _note_swallowed("amo_message.MessageMixin._send_media_to_umo.stamp", _amo_exc)
        logger.info("[amo] 图片 -> %s（event=%s）", umo, str(event_id or "-")[:48])
        return True
    async def _guard_zone_reply(self, text, umo="", system="", snapshot=None, user_text=""):
        """地点黑名单：和当前状态冲突就先重生成，退一步再确定性改写。

        Home 时出现「工位/公司/地铁/客户」这类词，说的就不是此刻的事；
        聊天、主动消息、微型事件分享共用这一道闸。
        """
        value = str(text or "")
        if not value:
            return value
        try:
            from .amo_state_snapshot import (blacklist_hit, build_snapshot,
                                             force_rewrite, snapshot_text)
        except ImportError:
            from amo_state_snapshot import (blacklist_hit, build_snapshot,
                                            force_rewrite, snapshot_text)
        try:
            snapshot = snapshot or build_snapshot(self)
        except Exception:
            snapshot = {}
        zone = str((snapshot or {}).get("zone") or "")
        hits = blacklist_hit(value, zone)
        if not hits:
            return value
        logger.warning("[amo] 地点黑名单拦下「%s」: %s", "、".join(hits), value[:80])
        try:
            block = snapshot_text(snapshot)
        except Exception:
            block = ""
        llm = getattr(self, "llm", None)
        if llm is None:
            return force_rewrite(value, zone)
        prompt = (
            (block + "\n" if block else "")
            + "你刚才想说的是：「" + value + "」\n"
            "这句话和当前状态冲突：你现在不在" + "、".join(hits) + "。"
            "请按当前状态重写这句话：保留原来的情绪和想表达的意思，"
            "但不能出现" + "、".join(hits) + "，也不能把它们说成刚刚/今天发生。"
            "只输出重写后的话。"
        )
        retry = ""
        try:
            if not system:
                system = await llm.persona(umo)
            limit = 220
            try:
                limit = self.cfg_int("max_reply_chars", 220)
            except Exception:
                limit = 220
            retry = await llm.chat(umo, (block + "\n" + str(system or "")).strip(),
                                   prompt, limit=limit)
        except Exception as exc:
            logger.debug("[amo] 地点黑名单重生成失败: %s", exc)
            retry = ""
        retry = str(retry or "").strip()
        if retry and not blacklist_hit(retry, zone):
            return retry
        fixed = force_rewrite(value, zone)
        logger.info("[amo] 地点黑名单兜底改写: %s -> %s", value[:60], fixed[:60])
        return fixed

    @staticmethod
    def _short_place(place):
        """把「东莞·商场·餐厅」这种内部写法缩成口语里的「餐厅」。"""
        value = str(place or "").strip()
        if "·" in value:
            value = value.split("·")[-1].strip() or value
        return value or "家"

    def _guard_self_location(self, text, place):
        """把她嘴里和日程对不上的地点改回来（日程说在上班，就不能说在逛街）。

        ``place`` 既接受日程里那条活动（dict，带 zone/category），也接受老签名里的
        地点字符串——只给地点时由 ``zone_of()`` 从地点名反推。
        """
        value = str(text or "")
        if not value:
            return value
        activity = place if isinstance(place, dict) else {"place": place}
        return _rewrite_reply(value, activity)

    def _guard_consumption(self, text):
        """消费防口嗨（2026-11-15）：嘴里说的每一笔消费，账本里必须查得到。

        查不到就删掉那句/改写成事实；开了 chat_claim_charge 则先真扣钱再让她说。
        """
        value = str(text or "")
        if not value:
            return value
        try:
            try:
                from .amo_fact_binder import guard as _claim_guard
            except ImportError:
                from amo_fact_binder import guard as _claim_guard
            fixed, changed, reason = _claim_guard(value, self)
        except Exception as exc:
            _note_swallowed("amo_message._guard_consumption", exc)
            return value
        if changed:
            logger.info("[CLAIM_GUARD] 编出来的消费已改掉（%s）：%s -> %s"
                        % (reason, value[:60], (fixed or "")[:60]))
        return fixed or value
