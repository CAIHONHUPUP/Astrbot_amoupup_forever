# -*- coding: utf-8 -*-
"""MessageMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

from typing import TYPE_CHECKING

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
        from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        Comp, MessageChain, clean_reply, logger, looks_like_model_leak, now_ts, to_float, typing_delay_ms,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            Comp, MessageChain, clean_reply, logger, looks_like_model_leak, now_ts, to_float, typing_delay_ms,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            Comp, MessageChain, clean_reply, logger, looks_like_model_leak, now_ts, to_float, typing_delay_ms,
        )


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object

try:  # 地点跟日程对齐：她嘴里说的地方和日程对不上时改回来
    from .place_guard import rewrite_reply as _rewrite_reply
except ImportError:  # 直接按文件加载时
    try:
        from place_guard import rewrite_reply as _rewrite_reply
    except Exception:  # pragma: no cover
        def _rewrite_reply(text, activity):
            return str(text or "")


class MessageMixin(_MixinBase):
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
            return rows[-size:]
        try:
            local = self.db.recent_messages(umo, size)
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._recent", _amo_exc)
            local = []
        if not rows:
            return local
        # 缓冲只有一小段时，用本地补前面
        known = {(row.get("role"), row.get("content")) for row in rows}
        merged = [row for row in local if (row.get("role"), row.get("content")) not in known]
        merged.extend(rows)
        return merged[-size:]

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
                return sorted(rows, key=lambda item: to_float(item.get("created_at"), 0))[-size:]
        if callable(lister):
            try:
                rows = await lister(self._data_bot_id(), "", size, uid=uid) or []
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._recent_by_uid", _amo_exc)
                rows = []
            if rows:
                return sorted(rows, key=lambda item: to_float(item.get("created_at"), 0))[-size:]
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
            return sorted(merged, key=lambda item: to_float(item.get("created_at"), 0))[-size:]
        try:
            return self.db.messages_by_uid(uid, size)
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
        rows = []
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
        self._ctx_hydrated = getattr(self, "_ctx_hydrated", set())
        self._ctx_hydrated.add(str(umo))

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
            return
        text = (event.message_str or "").strip()
        if not text:
            return
        if self._looks_like_command(text):
            return

        umo = event.unified_msg_origin
        private = event.is_private_chat()
        if private:
            self._clear_proactive_pending(uid)
            self._try_soothe(uid, text)
        if private and not self.cfg_bool("enable_private_reply", True):
            return
        if not private and not self.cfg_bool("enable_group_reply", True):
            return
        if private and self.cfg_bool("private_debounce_enabled", True):
            # 同一个人连着发的几条先合起来，只让最后一条走完整回复流程
            ok_debounce = await self._debounce_private(event, text)
            if not ok_debounce:
                return
            text = (event.message_str or "").strip()
            if not text:
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
            return
        snap = self.state.snapshot()
        if self._should_disturb(snap, umo, uid, text, group=True):
            self._apply_disturbance(umo)
            setattr(event, "amo_force_reply", True)
        try:
            await self.heartflow.on_group_message(event)
        except Exception as exc:
            logger.error("[amo] 心流处理失败: %s" % exc)

    async def _handle_private(self, event, profile):
        snap = self.state.snapshot()
        umo = event.unified_msg_origin
        uid = self._canonical_uid(event)
        if self._recent_bot_initiated(umo):
            self._disturb_until[str(umo)] = 0
            logger.info("[amo] 主动消息后的回复，按正常聊天处理")
            return
        if self._should_disturb(snap, umo, uid, event.message_str or "", group=False):
            self._apply_disturbance(umo)
            # 不 stop_event，让 AstrBot 正常生成；<amo_state> 会带上被打扰的语气。
            return
        probability = self.gate.private_probability(snap, profile)
        roll = random.random()
        logger.info(
            "[amo] 私聊判定 | 概率=%.2f | roll=%.2f | %s"
            % (probability, roll, "回复" if roll <= probability else "不回")
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
        snap = self.state.snapshot()
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
            if saved and saved[0] == text and time.time() - saved[1] < 180:
                saved_user = str(saved[2] if len(saved) > 2 else "")
                if saved_user == last_user:
                    return
            self._ctx_remember(umo, "bot", self.bot_name, "bot", text)
            if self.cfg_bool("local_chat_cache", False):
                self.db.add_message(umo, "bot", self.bot_name, "bot", text)
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
            self._last_bot_saved[umo] = (text, time.time(), last_user)
        except Exception as exc:
            logger.debug("[amo] 记录自己的回复失败: %s" % exc)

    async def _send_now(self, event, text, quote_id=None):
        """跳过分段引擎直接发一条（seg 关闭时，用来绕开 AstrBot 核心的引用/@）。"""
        if Comp is None or MessageChain is None:
            return False
        chain = []
        if quote_id is not None:
            try:
                chain.append(Comp.Reply(id=quote_id))
            except Exception as _amo_exc:
                _note_swallowed("amo_message.HumanCorePlugin._send_now", _amo_exc)
        chain.append(Comp.Plain(text))
        try:
            await event.send(MessageChain(chain))
        except Exception as exc:
            logger.error("[amo] 直接发送失败: %s" % exc)
            return False
        self._remember_sent_reply(event, text)
        try:
            self._last_reply_at[event.unified_msg_origin] = now_ts()
        except Exception as _amo_exc:
            _note_swallowed("amo_message.HumanCorePlugin._send_now", _amo_exc)
        logger.info("[amo] 直接发送 -> %s: %s" % (event.unified_msg_origin, text[:300]))
        return True

    async def _send_to_umo(self, umo, text, quote_id=None, group=False):
        text = clean_reply(text, self.cfg_int("max_reply_chars", 220))
        if looks_like_model_leak(text):
            text = "秘密，不告诉你。"
        if not text or not umo:
            return False
        segments = self._segment(text)
        if not segments:
            return False
        last_sent_at = 0.0
        for index, segment in enumerate(segments):
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
            try:
                await self.context.send_message(umo, MessageChain(chain))
            except Exception as exc:
                logger.warning("[amo] 主动发送失败: %s" % exc)
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
        self._remember_bot_message(umo, text)
        return True

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
