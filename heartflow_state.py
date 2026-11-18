from __future__ import annotations

import asyncio
import json
import random
import re
import time

from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Plain, Reply
from astrbot.api.provider import LLMResponse

try:
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_observability import note as _note_swallowed

try:
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

try:
    from .clock import now as _hf_now, from_timestamp as _hf_ts
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from clock import now as _hf_now, from_timestamp as _hf_ts


try:
    from .amo_group_guard import (
    MemberBook,
    addressing_verdict,
    is_bot_sender,
    parse_ids,
    parse_keywords,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_group_guard import (
            MemberBook,
            addressing_verdict,
            is_bot_sender,
            parse_ids,
            parse_keywords,
        )

try:
    from .heartflow_model import (
    ChatState,
    RawMessage,
    SCORE_DEFAULT,
    SCORE_MAX,
    SCORE_MIN,
    clamp_score,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from heartflow_model import (
            ChatState,
            RawMessage,
            SCORE_DEFAULT,
            SCORE_MAX,
            SCORE_MIN,
            clamp_score,
        )

if TYPE_CHECKING:
    from .heartflow_typing import HeartflowHost
else:  # 运行时基类仍是 object，行为不变
    HeartflowHost = object


class HeartflowStateMixin(HeartflowHost):
    """状态：群聊状态、暂停/恢复、沉浸模式、原始消息缓冲与活跃度。"""

    def _log_detail(self, msg: str) -> None:
        """输出详细日志（仅当开启时）"""
        if self.enable_detailed_log:
            logger.debug(msg)

    def _get_chat_state(self, chat_id: str) -> ChatState:
        """获取或创建群聊状态"""
        with self._lock:
            return self.chat_states.setdefault(chat_id, ChatState())

    def _cancel_pause_restore_task(self, chat_id: str) -> None:
        """取消暂停恢复任务"""
        task = self._pause_restore_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    def _schedule_pause_restore_task(self, chat_id: str) -> None:
        """安排暂停恢复任务"""
        self._cancel_pause_restore_task(chat_id)
        try:
            loop = asyncio.get_running_loop()
            self._pause_restore_tasks[chat_id] = loop.create_task(
                self._delayed_restore_judgment(chat_id)
            )
        except RuntimeError:
            pass  # cleanup

    async def _delayed_restore_judgment(self, chat_id: str) -> None:
        """延迟恢复判断（超时保护）"""
        try:
            await asyncio.sleep(self.judgment_paused_timeout)
            state = self._get_chat_state(chat_id)
            if state.judgment_paused and state.pending_heartflow_reply:
                logger.warning(f"[心流] ⚠️ 判断暂停后台超时，自动恢复: {chat_id}")
                self._set_judgment_paused(chat_id, paused=False)
        except asyncio.CancelledError:
            pass  # cleanup
        except Exception as e:
            logger.error(f"[心流] 暂停恢复异常: {e}")

    def _is_judgment_paused(self, chat_id: str) -> bool:
        """检查判断是否暂停"""
        state = self._get_chat_state(chat_id)
        if not state.judgment_paused:
            return False
        if state.judgment_paused_at > 0 and (_amo_clock.now_ts() - state.judgment_paused_at) > self.judgment_paused_timeout:
            self._set_judgment_paused(chat_id, paused=False)
            return False
        return True

    def _set_judgment_paused(self, chat_id: str, paused: bool = True) -> None:
        """设置判断暂停状态"""
        state = self._get_chat_state(chat_id)
        if paused:
            state.judgment_paused = True
            state.judgment_paused_at = _amo_clock.now_ts()
            self._schedule_pause_restore_task(chat_id)
            self._log_detail(f"[心流] ⏸️ 暂停小模型判断: {chat_id}")
        else:
            state.judgment_paused = False
            state.judgment_paused_at = 0.0
            state.pending_heartflow_reply = False
            state.pending_heartflow_mode = ""
            self._cancel_pause_restore_task(chat_id)
            self._log_detail(f"[心流] ▶️ 恢复小模型判断: {chat_id}")

    def _get_minutes_since_last_reply(self, chat_id: str) -> int:
        """获取距离上次回复的分钟数"""
        t = self._get_chat_state(chat_id).last_reply_time
        return 999 if t == 0 else max(0, int((_amo_clock.now_ts() - t) / 60))

    def _is_whitelisted(self, unified_msg_origin: str) -> bool:
        """白名单匹配：支持完整 unified_msg_origin 或直接填 QQ 群号/用户号"""
        if unified_msg_origin in self._whitelist_set:
            return True
        parts = unified_msg_origin.split(":")
        return len(parts) >= 3 and parts[-1] in self._whitelist_set

    def _record_raw_message(self, event: AstrMessageEvent, is_bot: bool = False, is_other_bot: bool = False) -> bool:
        """记录原始消息到缓冲区。

        is_bot=True 是她自己的发言；is_other_bot=True 是群里别的机器人，
        只当背景，不参与“在跟谁说话”的判定。
        """
        umo = event.unified_msg_origin
        with self._lock:
            buf = self._raw_msg_buffer.setdefault(umo, deque(maxlen=self._raw_msg_buffer_size))
            raw_content = str(event.message_str or "")
            normalized = self._normalize_for_dedup(raw_content)
            if not normalized:
                return False
            sender_id = "bot" if is_bot else str(event.get_sender_id())
            if is_other_bot:
                sender_id = "bot:" + sender_id
            if buf and buf[-1].sender_id == sender_id and buf[-1].normalized_content == normalized and (_amo_clock.now_ts() - buf[-1].timestamp) < 5:
                return False
            meta = self._message_meta(getattr(event, "message_obj", None))
            self_id = ""
            try:
                self_id = str(event.get_self_id())
            except Exception as _amo_exc:
                _note_swallowed("heartflow_engine.HeartflowEngine._record_raw_message", _amo_exc)
                self_id = ""
            at_ids = tuple(meta["at_ids"])
            buf.append(RawMessage(
                sender_name="bot" if is_bot else event.get_sender_name(),
                sender_id=sender_id, content=raw_content,
                normalized_content=normalized, timestamp=_amo_clock.now_ts(), is_bot=is_bot,
                msg_id=meta["msg_id"],
                self_id=self_id,
                at_ids=at_ids,
                at_self=bool(self_id and self_id in at_ids),
                reply_to_id=meta["reply_to_id"],
                reply_to_uid=meta["reply_to_uid"],
                reply_to_name=meta["reply_to_name"],
                reply_to_self=bool(self_id and meta["reply_to_uid"] == self_id),
                is_other_bot=is_other_bot,
            ))
            if not is_bot:
                try:
                    self.members.observe(
                        umo, str(event.get_sender_id()), event.get_sender_name(), is_bot=is_other_bot,
                    )
                except Exception as _amo_exc:
                    _note_swallowed("heartflow_engine.HeartflowEngine._record_raw_message", _amo_exc)
            self._get_chat_state(umo).last_recorded_message = normalized
            return True

    def _record_bot_reply_manual(self, umo: str, content: str) -> bool:
        """手动记录机器人回复"""
        if not content or not content.strip():
            return False
        normalized = self._normalize_for_dedup(content)
        if not normalized:
            return False
        with self._lock:
            buf = self._raw_msg_buffer.setdefault(umo, deque(maxlen=self._raw_msg_buffer_size))
            if buf and buf[-1].is_bot and buf[-1].normalized_content == normalized and (_amo_clock.now_ts() - buf[-1].timestamp) < 5:
                return False
            buf.append(RawMessage(
                sender_name="bot", sender_id="bot", content=content,
                normalized_content=normalized, timestamp=_amo_clock.now_ts(), is_bot=True
            ))
            self._get_chat_state(umo).last_recorded_message = normalized
            return True

    def _get_raw_buffer(self, umo: str) -> List[RawMessage]:
        """获取原始消息缓冲区的副本"""
        with self._lock:
            return list(self._raw_msg_buffer.get(umo, []))

    def _cleanup_old_messages(self, umo: str) -> None:
        """清理过期消息"""
        now = _amo_clock.now_ts()
        state = self._get_chat_state(umo)
        if now - state.last_cleanup_time < self._buffer_cleanup_interval or umo not in self._raw_msg_buffer:
            return
        state.last_cleanup_time = now
        self._raw_msg_buffer[umo] = deque(
            (m for m in self._raw_msg_buffer[umo] if m.timestamp >= now - 3600),
            maxlen=self._raw_msg_buffer_size
        )

    def _calculate_activity_level(self, chat_id: str) -> float:
        """计算群聊活跃度（0.0-1.0）"""
        state = self._get_chat_state(chat_id)
        now = _amo_clock.now_ts()
        if (now - state.activity_cache_time) < 5.0 and state.activity_cache_time > 0:
            return state.cached_activity_level
        
        msgs = self._get_raw_buffer(chat_id)
        recent_msgs = [
            m for m in msgs
            if m.timestamp >= now - self.dynamic_activity_window and not m.is_bot and not m.is_other_bot
        ]
        if not recent_msgs:
            state.cached_activity_level = 0.0
            state.activity_cache_time = now
            return 0.0

        w_freq, w_interval, w_diversity = self.dynamic_activity_weights
        freq_score = min(1.0, (len(recent_msgs) / (self.dynamic_activity_window / 60.0)) / 10.0)
        
        interval_score = 0.0
        if len(recent_msgs) >= 2:
            intervals = [recent_msgs[i].timestamp - recent_msgs[i - 1].timestamp for i in range(1, len(recent_msgs))]
            avg_interval = sum(intervals) / len(intervals)
            interval_score = 1.0 if avg_interval <= 5 else (0.0 if avg_interval >= 120 else 1.0 - (avg_interval - 5) / 115.0)
            
        diversity_score = min(1.0, len(set(m.sender_id for m in recent_msgs)) / 5.0)
        activity = round(max(0.0, min(1.0, freq_score * w_freq + interval_score * w_interval + diversity_score * w_diversity)), 3)
        
        state.cached_activity_level = activity
        state.activity_cache_time = now
        return activity

    def _get_activity_description(self, activity: float) -> str:
        """获取活跃度描述"""
        if activity >= 0.7: return "🔥非常活跃"
        if activity >= 0.4: return "💬一般活跃"
        if activity >= 0.15: return "😴较冷清"
        return "🥶几乎没人说话"

    def _get_group_config_info(self, chat_id: str) -> str:
        """获取群聊配置来源"""
        if chat_id in self.group_threshold_configs:
            return "🎯 独立配置"
        parts = chat_id.split(":")
        if len(parts) >= 3 and parts[-1] in self.group_threshold_configs:
            return f"🎯 独立配置 (群号 {parts[-1]})"
        return "🌐 全局配置"

    def _get_dynamic_threshold(self, chat_id: str) -> Tuple[float, float, float]:
        """获取动态阈值（返回：当前阈值, 活跃度, 绝对分数阈值）"""
        group_cfg = self.group_threshold_configs.get(chat_id)
        if not group_cfg:
            parts = chat_id.split(":")
            if len(parts) >= 3:
                group_cfg = self.group_threshold_configs.get(parts[-1])
        
        if group_cfg:
            base, t_max, t_min = group_cfg["reply_threshold"], group_cfg["dynamic_threshold_max"], group_cfg["dynamic_threshold_min"]
        else:
            base, t_max, t_min = self.reply_threshold, self.dynamic_threshold_max, self.dynamic_threshold_min

        if not self.enable_dynamic_threshold:
            return base, 0.0, self.score_min + (self.score_max - self.score_min) * base

        activity = self._calculate_activity_level(chat_id)
        offset_range = t_max - t_min
        normalized_activity = (activity - 0.5) * 2
        smoothed = max(-1.0, min(1.0, normalized_activity * (1 + 0.3 * abs(normalized_activity))))
        dynamic_threshold = round(max(t_min, min(t_max, base + smoothed * (offset_range / 2))), 3)
        absolute_threshold = self.score_min + (self.score_max - self.score_min) * dynamic_threshold
        
        return dynamic_threshold, activity, absolute_threshold

    # ============================================================
    # 沉浸模式管理
    # ============================================================
    def _is_immersive_mode_active(self, chat_id: str) -> bool:
        """检查沉浸模式是否激活"""
        if self.immersive_mode_duration <= 0:
            return False
        state = self._get_chat_state(chat_id)
        if state.immersive_mode_end_time == 0:
            return False
        if _amo_clock.now_ts() > state.immersive_mode_end_time:
            state.immersive_mode_end_time = 0
            return False
        return True

    def _get_immersive_mode_remaining(self, chat_id: str) -> int:
        """获取沉浸模式剩余时间（秒）"""
        state = self._get_chat_state(chat_id)
        if state.immersive_mode_end_time == 0:
            return 0
        return max(0, int(state.immersive_mode_end_time - _amo_clock.now_ts()))

    def _activate_immersive_mode(self, chat_id: str) -> None:
        """激活沉浸模式"""
        if self.immersive_mode_duration <= 0:
            return
        state = self._get_chat_state(chat_id)
        state.immersive_mode_end_time = _amo_clock.now_ts() + self.immersive_mode_duration
        logger.debug(f"[心流] 🌊 沉浸模式已激活: {chat_id}, 持续 {self.immersive_mode_duration}秒")

    def _refresh_immersive_mode(self, chat_id: str) -> None:
        """刷新沉浸模式倒计时"""
        if self.immersive_mode_duration <= 0:
            return
        state = self._get_chat_state(chat_id)
        if state.immersive_mode_end_time > 0:
            state.immersive_mode_end_time = _amo_clock.now_ts() + self.immersive_mode_duration
            self._log_detail(f"[心流] 🔄 沉浸模式倒计时已刷新: {chat_id}")
