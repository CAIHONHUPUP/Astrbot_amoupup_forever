# -*- coding: utf-8 -*-
"""统一 OutboundGateway（2026-11-22 task-16）：所有对外发送的唯一出口。

背景（work/audit/outbound_repeat.md）：主动消息走 amo_message._send_to_umo，聊天回复
走 segment_engine.event.send，照片走 _send_media_to_umo，draw/commands 又各自直发。
双轨 + 缺 source + 非原子去重，导致 07:30 3.4 秒 7 条催问、23:33 双晚安、
09:00 同文日记质检双发、天气提醒插进聊天分段中间。

本模块是唯一事实源：
- send / send_media：唯一发送 API，per-umo asyncio.Lock 内做原子去重 + 频控 +
  落 outbound_log（decision/reason 可解释）；
- begin_chat_batch / end_chat_batch / note_chat：聊天分段批登记；
  chat 批进行中，非 chat 批一律进 deferred 队列，批结束 + 30s 冷却后按序补发，
  绝不插进聊天分段中间；
- stats / rows：只读审计入口。

频控参数（报告 §6.2）：20s<=4、60s<=6、300s<=10；同 source 90s、跨 source 300s；
nudge 20min<=1；沉默 >=6h 的非 chat 入口=0；晚安/系统/提醒同日同 kind<=1。
去重窗口 10 分钟：归一化文本相似度>=0.60 或同意图 -> drop（reason=dup_text/dup_intent）。
"""
from __future__ import annotations

import asyncio
import difflib
import re
import time
from typing import Any

#: 合法来源；缺省 fail-closed 当 push（受控），绝不默认 chat。
SOURCES = ("chat", "proactive", "followup", "goodnight", "reminder",
           "push", "photo", "system", "admin")
FAIL_CLOSED_SOURCE = "push"
#: 受沉默/频控约束的非 chat 来源
ENGAGE_SOURCES = frozenset({"proactive", "followup", "goodnight", "reminder",
                            "push", "photo", "system"})
#: 同日同 kind 只允许一次的来源
DAY_KINDS = frozenset({"goodnight", "system", "reminder"})
#: 频控窗口（秒）
BURST_WINDOW, BURST_MAX = 20.0, 4
MINUTE_WINDOW, MINUTE_MAX = 60.0, 6
FIVE_MIN_WINDOW, FIVE_MIN_MAX = 300.0, 10
SAME_SOURCE_WINDOW = 90.0
CROSS_SOURCE_WINDOW = 300.0
DEDUPE_WINDOW = 600.0
DEDUPE_TEXT_THRESHOLD = 0.60
NUDGE_WINDOW, NUDGE_MAX = 1200.0, 1
CHAT_COOLDOWN = 30.0
LOG_LIMIT = 60

_FILL = re.compile(r"[^\w\u4e00-\u9fff]+")
_KEY_PREFIX = ("你美丽的", "美丽的")

#: 意图词表（报告 §3.2）：同意图换措辞也要压。
_INTENT_WORDS = {
    "nudge": ("晾", "等你", "候着", "理一下", "忙完", "看我一眼", "瞄一眼", "人呢",
              "醒醒", "管管", "哄哄", "半天", "没看见", "不理", "回我", "在吗"),
    "goodnight": ("晚安", "梦里", "睡吧", "早点睡"),
    "morning": ("早安", "早上好", "起床", "醒了没"),
    "meal": ("吃饭", "吃啥", "午饭", "晚饭", "外卖", "饿了", "吃了吗"),
    "photo": ("照片", "自拍", "发张", "拍一张", "拍张"),
    "reminder": ("提醒", "别忘了", "到点", "记得", "要开始"),
    "system": ("日记质检", "系统", "天气"),
    "share": ("给你看", "刷到", "分享", "看到了"),
}

_LOCKS: dict[str, asyncio.Lock] = {}
_DEFERRED: dict[str, list[dict[str, Any]]] = {}
_CHAT: dict[str, dict[str, Any]] = {}
_FLUSH_TASKS: dict[str, asyncio.Task[Any]] = {}


def _strip_media(text):
    """出站文本统一剥掉 [我发的照片]/[我发的语音] 占位符（task-23 全出口 strip）。

    这是所有发送 API 的最后一道：任何调用方忘了剥，也不会把内部媒体标记
    当人话发出去；真正的图片/语音仍走组件。
    """
    body = str(text or "")
    if "我发的照片" not in body and "我发的语音" not in body:
        return body
    try:
        try:
            from .amo_message import strip_media_markers
        except ImportError:
            from amo_message import strip_media_markers
        return strip_media_markers(body)
    except Exception:
        return body


def _strip_component_markers(components):
    """把文本组件里的媒体占位符剥掉（图片/语音组件原样保留）。"""
    out = []
    for comp in list(components or []):
        text = getattr(comp, "text", None)
        if isinstance(text, str) and ("我发的照片" in text or "我发的语音" in text):
            cleaned = _strip_media(text)
            if not cleaned:
                continue
            try:
                comp.text = cleaned
            except Exception:  # cleanup: 兜底/降级，保持原行为
                pass
        out.append(comp)
    return out


def _now_ts(now=None):
    if now is not None:
        try:
            return float(now)
        except (TypeError, ValueError):  # cleanup: 兜底/降级，保持原行为
            pass
    try:
        try:
            from . import clock as _clock
        except ImportError:
            import clock as _clock
        return float(_clock.now_ts())
    except Exception:
        return 0.0


def _day_of(stamp):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(stamp)))
    except Exception:
        return ""


def _key(text):
    body = _FILL.sub("", str(text or ""))
    for prefix in _KEY_PREFIX:
        body = body.replace(prefix, "")
    return body


def _similar(left, right):
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def intent_of(text):
    """给一句话打意图标签（nudge/goodnight/morning/meal/photo/reminder/system/share）。"""
    body = str(text or "")
    for name, words in _INTENT_WORDS.items():
        if any(word in body for word in words):
            return name
    return ""


def normalize_source(source):
    value = str(source or "").strip().lower()
    return value if value in SOURCES else FAIL_CLOSED_SOURCE


def _lock(umo):
    lock = _LOCKS.get(str(umo))
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[str(umo)] = lock
    return lock


def _log_key(umo):
    return "outbound_log:%s" % str(umo or "")


def rows(plugin, umo):
    """读某个 umo 的 outbound_log（唯一事实源；坏数据按空）。"""
    try:
        data = plugin.db.kv_get(_log_key(umo), [])
    except Exception:
        return []
    return [dict(item) for item in data] if isinstance(data, list) else []


def _append_rows(plugin, umo, entry):
    try:
        data = rows(plugin, umo)
        data.append(dict(entry))
        plugin.db.kv_set(_log_key(umo), data[-LOG_LIMIT:])
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass


def _log(plugin, umo, *, source, kind="", text="", batch="", dedupe_key="",
         decision="sent", reason="", at=None):
    stamp = _now_ts(at)
    entry = {
        "at": stamp,
        "source": str(source or ""),
        "kind": str(kind or ""),
        "intent": intent_of(text),
        "key": _key(text)[:120],
        "text": str(text or "")[:80],
        "batch": str(batch or ""),
        "dedupe": str(dedupe_key or ""),
        "decision": str(decision or "sent"),
        "reason": str(reason or ""),
    }
    _append_rows(plugin, umo, entry)
    return entry


def _sent(plugin, umo, window=0.0, now=None):
    current = _now_ts(now)
    out = []
    for entry in rows(plugin, umo):
        if str(entry.get("decision") or "") != "sent":
            continue
        try:
            stamp = float(entry.get("at") or 0)
        except (TypeError, ValueError):
            continue
        if window and current - stamp > window:
            continue
        out.append((stamp, entry))
    out.sort(key=lambda pair: pair[0])
    return out


def _is_silent(plugin, now=None):
    try:
        try:
            from . import amo_presence as _presence
        except ImportError:
            import amo_presence as _presence
        return bool(_presence.is_silent(plugin, None, now=_now_ts(now)))
    except Exception:
        return False


def _decide(plugin, umo, text, source, kind, now=None, batch="", media=False):
    """返回 (decision, reason)；decision in {send, drop}。"""
    stamp = _now_ts(now)
    src = normalize_source(source)
    if src == "chat":
        return "send", ""
    # 1) 沉默 6 小时：参与类非 chat 入口一律 0；问候/时间点提醒允许（频控仍生效）。
    if src in ENGAGE_SOURCES and _is_silent(plugin, stamp) and src not in ("goodnight", "reminder", "system", "admin"):
        return "drop", "silence_6h"
    sent = _sent(plugin, umo, FIVE_MIN_WINDOW, stamp)
    same_batch = bool(batch) and any(
        str(entry.get("batch") or "") == str(batch) for _age, entry in sent)
    recent_20 = [item for item in sent if stamp - item[0] <= BURST_WINDOW]
    recent_60 = [item for item in sent if stamp - item[0] <= MINUTE_WINDOW]
    recent_300 = [item for item in sent if stamp - item[0] <= FIVE_MIN_WINDOW]
    if media:
        if len(recent_20) >= BURST_MAX:
            return "drop", "burst_20s"
        if len(recent_60) >= MINUTE_MAX:
            return "drop", "burst_60s"
        if len(recent_300) >= FIVE_MIN_MAX:
            return "drop", "burst_300s"
        return "send", ""
    if not same_batch:
        if len(recent_20) >= BURST_MAX:
            return "drop", "burst_20s"
        if len(recent_60) >= MINUTE_MAX:
            return "drop", "burst_60s"
        if len(recent_300) >= FIVE_MIN_MAX:
            return "drop", "burst_300s"
        # 2) 同 source 90 秒：同一入口不能连发（同义换措辞也不行）。
        for age, entry in reversed(sent):
            if stamp - age > SAME_SOURCE_WINDOW:
                break
            if str(entry.get("source") or "") == src:
                return "drop", "same_source_90s"
        # 3) 跨 source 300 秒：不同入口同一个 umo 也不能挤在一起。
        for age, entry in reversed(sent):
            if stamp - age > CROSS_SOURCE_WINDOW:
                break
            if str(entry.get("source") or "") != "chat":
                return "drop", "cross_source_300s"
    body = _key(text)
    # 4) 10 分钟文本/意图去重（跨 source，单一 key）。
    mine_intent = intent_of(text)
    for age, entry in reversed(sent):
        if stamp - age > DEDUPE_WINDOW:
            break
        other = str(entry.get("key") or "")
        if _similar(body, other) >= DEDUPE_TEXT_THRESHOLD:
            return "drop", "dup_text_10m"
        if mine_intent and str(entry.get("intent") or "") == mine_intent:
            return "drop", "dup_intent_10m"
    # 5) nudge 20 分钟 <=1（窗口比 300s 长，查全量日志）。
    if mine_intent == "nudge" or "nudge" in str(kind or ""):
        for age, entry in reversed(_sent(plugin, umo, 0.0, stamp)):
            if stamp - age > NUDGE_WINDOW:
                break
            if str(entry.get("intent") or "") == "nudge":
                return "drop", "nudge_20m"
    # 6) 晚安/系统/提醒同日同 kind <=1（查全量日志，不受 300s 窗口限制）。
    if src in DAY_KINDS:
        day = _day_of(stamp)
        for _age, entry in _sent(plugin, umo, 0.0, stamp):
            if (str(entry.get("source") or "") == src
                    and str(entry.get("kind") or "") == str(kind or "")
                    and _day_of(entry.get("at")) == day):
                return "drop", "same_day_kind"
    return "send", ""


def _chat_state(umo):
    state = _CHAT.get(str(umo))
    if not isinstance(state, dict):
        state = {"count": 0, "batch": "", "until": 0.0}
        _CHAT[str(umo)] = state
    return state


def chat_active(umo, now=None):
    state = _chat_state(umo)
    if int(state.get("count") or 0) > 0:
        return True
    return _now_ts(now) < float(state.get("until") or 0)


def begin_chat_batch(plugin, umo, batch_id="", now=None):
    """登记一个聊天分段批：期间所有非 chat 发送进入 deferred 队列。"""
    state = _chat_state(umo)
    state["count"] = int(state.get("count") or 0) + 1
    state["batch"] = str(batch_id or state.get("batch") or "")
    return state["batch"]


def end_chat_batch(plugin, umo, batch_id="", now=None):
    state = _chat_state(umo)
    state["count"] = max(0, int(state.get("count") or 0) - 1)
    state["until"] = _now_ts(now) + CHAT_COOLDOWN
    if state["count"] == 0:
        _schedule_flush(plugin, umo)


def note_chat(plugin, umo, text, batch_id=""):
    """聊天分段实际发出后登记（只记日志/频控，不再发送）。"""
    if not str(text or "").strip():
        return
    _log(plugin, umo, source="chat", text=text, batch=batch_id, decision="sent")


def _defer(plugin, umo, payload):
    queue = _DEFERRED.setdefault(str(umo), [])
    queue.append(dict(payload))
    if len(queue) > 20:
        _DEFERRED[str(umo)] = queue[-20:]


def _schedule_flush(plugin, umo):
    key = str(umo)
    if _FLUSH_TASKS.get(key):
        return
    try:
        spawn = getattr(plugin, "_spawn", None)
        coro = _flush_after(plugin, key)
        if callable(spawn):
            spawn(coro)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        _FLUSH_TASKS[key] = loop.create_task(coro)
    except Exception:
        _FLUSH_TASKS.pop(key, None)


async def _flush_after(plugin, umo):
    try:
        await asyncio.sleep(CHAT_COOLDOWN)
        queued = _DEFERRED.pop(str(umo), [])
        for payload in queued:
            if payload.get("media"):
                await send_media(plugin, umo, payload["media"],
                                 event_id="", source=payload.get("source"),
                                 kind=payload.get("kind", ""),
                                 batch_id=payload.get("batch", ""),
                                 now=payload.get("now"))
            else:
                await send(plugin, umo, payload.get("text") or "",
                           source=payload.get("source"),
                           kind=payload.get("kind", ""),
                           batch_id=payload.get("batch", ""),
                           now=payload.get("now"))
    finally:
        _FLUSH_TASKS.pop(str(umo), None)


async def send(plugin, umo, text, *, source=None, kind="", batch_id="",
               dedupe_key="", now=None, transport=None):
    """唯一文本发送 API；返回 True 表示确实发出或已进 deferred 队列。"""
    umo = str(umo or "")
    body = _strip_media(str(text or "")).strip()
    if not umo or not body:
        return False
    src = normalize_source(source)
    stamp = _now_ts(now)
    # task-32：收件人/角色反转唯一守卫（唯一定义在 amo_addressee_guard，persona_guard
    # 那条出口也会调用同一条规则）。11:31 真实样例就是从 watch 的 request_push 进来、
    # 在这里以 source=push 落 outbound_log 的，所以先在这里用 src 记原因。
    try:
        try:
            from .amo_addressee_guard import enforce as _addressee_enforce
        except ImportError:
            from amo_addressee_guard import enforce as _addressee_enforce
        body, addr_reason = _addressee_enforce(plugin, umo, body, source=src)
        if not body:
            _log(plugin, umo, source=src, kind=kind, text=str(text or ""),
                 batch=batch_id, dedupe_key=dedupe_key, decision="drop",
                 reason=addr_reason or "addressee", at=stamp)
            return False
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass
    # task-28：统一出口语义卫生（动作旁白/系统词/禁语/报账数字/姓名 grounding）。
    # 放在去重/频控之前：被守卫吃掉的文本不占频控窗口，也不会进 deferred。
    try:
        try:
            from . import amo_persona_guard as _persona
        except ImportError:
            import amo_persona_guard as _persona
        body, guard_reason = _persona.prepare_outbound(plugin, umo, body, src)
        if not body:
            _log(plugin, umo, source=src, kind=kind, text=str(text or ""),
                 batch=batch_id, dedupe_key=dedupe_key, decision="drop",
                 reason=guard_reason or "semantic_guard", at=stamp)
            return False
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass
    if src not in ("chat", "admin") and chat_active(umo, stamp):
        _log(plugin, umo, source=src, kind=kind, text=body, batch=batch_id,
             dedupe_key=dedupe_key, decision="defer", reason="chat_batch", at=stamp)
        _defer(plugin, umo, {"text": body, "source": src, "kind": kind,
                             "batch": batch_id, "now": stamp})
        return True
    async with _lock(umo):
        decision, reason = _decide(plugin, umo, body, src, kind, stamp, batch_id)
        if decision != "send":
            _log(plugin, umo, source=src, kind=kind, text=body, batch=batch_id,
                 dedupe_key=dedupe_key, decision="drop", reason=reason, at=stamp)
            return False
        try:
            if callable(transport):
                await transport()
            else:
                await plugin.context.send_message(umo, body)
        except Exception:
            _log(plugin, umo, source=src, kind=kind, text=body, batch=batch_id,
                 dedupe_key=dedupe_key, decision="send_failed", reason="transport",
                 at=stamp)
            return False
        _log(plugin, umo, source=src, kind=kind, text=body, batch=batch_id,
             dedupe_key=dedupe_key, decision="sent", at=stamp)
        return True


async def send_media(plugin, umo, components, *, event_id="", source="photo",
                     kind="photo", batch_id="", transport=None, now=None):
    """唯一图片发送 API：同一 event_id 90 秒内最多一张 + 同批互斥 + 频控 + 落日志。"""
    umo = str(umo or "")
    components = _strip_component_markers(components)
    if not umo or not components:
        return False
    src = normalize_source(source or "photo")
    stamp = _now_ts(now)
    # task-32：图片/语音的 caption 也是出站文本，同样过收件人守卫（只改/去文字，不丢媒体）。
    try:
        try:
            from .amo_addressee_guard import enforce as _addressee_enforce
        except ImportError:
            from amo_addressee_guard import enforce as _addressee_enforce
        for comp in components:
            caption = getattr(comp, "text", None)
            if not isinstance(caption, str) or not caption.strip():
                continue
            fixed, _cap_reason = _addressee_enforce(plugin, umo, caption, source=src)
            if _cap_reason:
                try:
                    comp.text = fixed
                except Exception:  # cleanup: 兜底/降级，保持原行为
                    pass
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass
    dedupe = "photo:%s" % (str(event_id or "").strip() or "shot")
    if src not in ("chat", "admin") and chat_active(umo, stamp):
        _log(plugin, umo, source=src, kind=kind, text="[图片]", batch=batch_id,
             dedupe_key=dedupe, decision="defer", reason="chat_batch", at=stamp)
        _defer(plugin, umo, {"media": list(components), "source": src, "kind": kind,
                             "batch": batch_id, "now": stamp})
        return True
    async with _lock(umo):
        for age, entry in reversed(_sent(plugin, umo, SAME_SOURCE_WINDOW, stamp)):
            if stamp - age > SAME_SOURCE_WINDOW:
                break
            if str(entry.get("dedupe") or "") == dedupe:
                _log(plugin, umo, source=src, kind=kind, text="[图片]", batch=batch_id,
                     dedupe_key=dedupe, decision="drop", reason="dup_photo_90s", at=stamp)
                return False
        decision, reason = _decide(plugin, umo, "[图片]", src, kind, stamp, batch_id, media=True)
        if decision != "send":
            _log(plugin, umo, source=src, kind=kind, text="[图片]", batch=batch_id,
                 dedupe_key=dedupe, decision="drop", reason=reason, at=stamp)
            return False
        try:
            if callable(transport):
                await transport()
            else:
                await plugin.context.send_message(umo, components)
        except Exception:
            _log(plugin, umo, source=src, kind=kind, text="[图片]", batch=batch_id,
                 dedupe_key=dedupe, decision="send_failed", reason="transport", at=stamp)
            return False
        _log(plugin, umo, source=src, kind=kind, text="[图片]", batch=batch_id,
             dedupe_key=dedupe, decision="sent", at=stamp)
        return True


def stats(plugin, umo, now=None):
    """只读统计：最近 60s/300s 条数、按 source 计数、丢弃原因 Top。"""
    stamp = _now_ts(now)
    data = rows(plugin, umo)
    sent = [item for item in data if str(item.get("decision") or "") == "sent"]
    by_source: dict[str, int] = {}
    for item in sent:
        name = str(item.get("source") or "")
        by_source[name] = by_source.get(name, 0) + 1
    reasons: dict[str, int] = {}
    for item in data:
        if str(item.get("decision") or "") == "sent":
            continue
        reason = str(item.get("reason") or "?")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "total": len(sent),
        "last_60s": sum(1 for item in sent if stamp - float(item.get("at") or 0) <= 60),
        "last_300s": sum(1 for item in sent if stamp - float(item.get("at") or 0) <= 300),
        "by_source": by_source,
        "drop_reasons": reasons,
    }


def take_deferred(plugin=None, umo=""):
    """测试用：取走 deferred 队列（不等待 30s 冷却）。"""
    return _DEFERRED.pop(str(umo or ""), [])


__all__ = [
    "SOURCES", "FAIL_CLOSED_SOURCE", "ENGAGE_SOURCES",
    "normalize_source", "intent_of", "begin_chat_batch", "end_chat_batch",
    "note_chat", "chat_active", "send", "send_media", "rows", "stats",
    "take_deferred",
]
