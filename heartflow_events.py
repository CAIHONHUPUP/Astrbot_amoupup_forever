from __future__ import annotations

import asyncio
import json
import random
import re
import time
import traceback

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
    INTERJECT_ANGLES,
    JudgeResult,
    RawMessage,
    RE_URL,
    RE_XML_TAG,
    clamp_score,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from heartflow_model import (
            ChatState,
            INTERJECT_ANGLES,
            JudgeResult,
            RawMessage,
            RE_URL,
            RE_XML_TAG,
            clamp_score,
        )

if TYPE_CHECKING:
    from .heartflow_typing import HeartflowHost
else:  # 运行时基类仍是 object，行为不变
    HeartflowHost = object


class HeartflowEventsMixin(HeartflowHost):
    """事件：收消息、身份识别、定向、防抖与回复准备。"""

    def _addresses_bot(self, text: str) -> str:
        normalized = re.sub(r"[\s，。！？!?、；;：:,.~～…\-]+", "", str(text or "")).lower()
        if not normalized:
            return ""
        for candidate in [self.bot_name] + self.bot_aliases:
            value = re.sub(r"[\s，。！？!?、；;：:,.~～…\-]+", "", str(candidate or "")).lower()
            if len(value) >= 2 and value in normalized:
                return candidate
        return ""

    def note_other_bot(self, event: AstrMessageEvent) -> None:
        """记下别的机器人的发言：只当背景，不触发判定。"""
        try:
            self._record_raw_message(event, is_other_bot=True)
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine.note_other_bot", _amo_exc)

    def is_other_bot_sender(self, event: AstrMessageEvent) -> bool:
        """发言人是不是别的机器人：显式账号名单 / 名册里学到的 / 昵称关键词。"""
        try:
            sender_id = str(event.get_sender_id() or "")
            sender_name = str(event.get_sender_name() or "")
            umo = str(event.unified_msg_origin or "")
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine.is_other_bot_sender", _amo_exc)
            return False
        if sender_id and sender_id in self.bot_ids:
            return True
        if self.members.is_known_bot(umo, sender_id):
            return True
        return is_bot_sender(sender_name, sender_id, self.bot_keywords, ())

    @staticmethod
    def _is_side_question(text: str) -> bool:
        """这条是不是在问某个人（而不是随口感慨）。"""
        return bool(re.search(r"[?？]|你|吗|呢|怎么|为什么|哪|谁|多少|几点", str(text or "")))

    @classmethod
    def _should_veto_side_question(cls, target: str, hard: bool, text: str) -> bool:
        """上下文明确显示在对别人说，而且这条是在问别人 → 不接。"""
        return bool(hard and target == "other" and cls._is_side_question(text))

    @staticmethod
    def _explicit_target(msg) -> str:
        """只看一条消息自带的 @ / 引用，判断它在对谁说。

        平台没返回机器人 ID 时（self_id 为空），@ 的目标根本没法可靠比较：
        以前会把所有 @ 都当成「@别人」，真正 @ 机器人的提问反而被硬证据
        🔇 掉。这里改成「不知道就不给硬证据」，交回模型按上下文判断。
        """
        if msg is None:
            return ""
        if msg.at_ids:
            if msg.at_self:
                return "bot"
            return "other" if msg.self_id else ""
        if msg.reply_to_id:
            if msg.reply_to_self:
                return "bot"
            if msg.self_id and (msg.reply_to_uid or msg.reply_to_name):
                return "other"
        return ""

    def _retrieve_addressing(self, event: AstrMessageEvent):
        """从最近上下文里检索证据：这条消息到底在对谁说。

        返回 (target, hard)：
          target = bot / other / all / unknown；取不到证据返回空字符串。
          hard   = 是否硬证据。硬证据会覆盖模型判定，并在 on_group_message 里
                   决定“这条是在问别人，不接”。

        判定顺序（从强到弱）：
          1. 当前消息 @ 了谁 / 引用了谁；
          2. 当前消息里有没有机器人名字/别名；
          3. 上一条**明确 @ 了别人 / 回复了别人**的消息 → 继承给这条（5 分钟内）；
          4. 机器人 60 秒内说过话 + 这条是个短问句 → 软证据；
          5. 名册里点名了别的群友（不是她）→ 对别人说（软证据）；
          6. 最近几条里的群友昵称出现在消息里 → 对别人说（软证据）。
        单独的“你”不算证据：群聊里“你吃了吗”太常见，把它认成机器人是上次
        “你pr好了吗”被误答的直接原因。
        """
        text = event.message_str or ""
        try:
            msgs = self._get_raw_buffer(event.unified_msg_origin)
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine._retrieve_addressing", _amo_exc)
            msgs = []
        current = None
        history = msgs
        if msgs and not msgs[-1].is_bot and msgs[-1].sender_id == str(event.get_sender_id()) and msgs[-1].content == text:
            current = msgs[-1]
            history = msgs[:-1]

        # 1. 当前这条自带的 @ / 引用最可靠（先于名字判断：引用的旧文本里可能带机器人名字）
        who = self._explicit_target(current)
        if who:
            return who, True
        # 2. 明确叫了机器人名字/别名
        if self._addresses_bot(text):
            return "bot", True
        # 3. 上一条明确 @ 了别人 / 回复了别人：这条没有新的 @/引用，就是在继续对他说
        last_who = ""
        for msg in reversed(history[-8:]):
            if msg.is_bot or msg.is_other_bot:
                continue
            if _amo_clock.now_ts() - msg.timestamp > 300:
                break
            who = self._explicit_target(msg)
            if who:
                last_who = who
                break
        if last_who == "other":
            return "other", True
        if last_who == "bot":
            return "bot", False
        # 4. 机器人刚说过话，这条又是个短问句：软证据
        last = history[-1] if history else None
        plain = re.sub(r"\s+", "", text)
        if last is not None and last.is_bot and (_amo_clock.now_ts() - last.timestamp) <= 60:
            if len(plain) <= 24 and re.search(r"[?？]", text):
                return "bot", False
        # 5. 名册里点名了某个群友（把她自己和发送者排除）：这条在对别人说
        try:
            member = self.members.mentioned_member(
                event.unified_msg_origin,
                text,
                exclude=[event.get_sender_name() or ""],
                exclude_uids=[str(event.get_sender_id() or "")],
            )
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine._retrieve_addressing", _amo_exc)
            member = None
        if member and not self._addresses_bot(str(member.get("name") or "")):
            return "other", False
        # 6. 最近几条里的群友昵称出现在消息里：同样算在对别人说（昵称至少两个字）
        for msg in reversed(history[-4:]):
            if msg.is_bot or msg.is_other_bot:
                continue
            name = re.sub(r"\s+", "", str(msg.sender_name or ""))
            if len(name) >= 2 and name in plain and name != re.sub(r"\s+", "", text):
                if not self._addresses_bot(name):
                    return "other", False
        return "", False

    def _should_process_message(self, event: AstrMessageEvent) -> bool:
        """检查消息是否需要处理"""
        if not self.config.get("enable_heartflow", False):
            return False
        if self.whitelist_enabled and not self._is_whitelisted(event.unified_msg_origin):
            return False
        if event.get_sender_id() == event.get_self_id():
            return False
        if self.ignore_bots and self.is_other_bot_sender(event):
            # 别的机器人：只记账当背景，不判定、更不接话
            self.note_other_bot(event)
            self._log_detail("[心流] 🤖 忽略机器人发言：%s" % (event.get_sender_name() or event.get_sender_id()))
            return False
        if not (event.message_str and event.message_str.strip()):
            return False
        try:
            if self._is_judgment_paused(event.unified_msg_origin):
                # 上一轮已经判定「该回」、正在生成或等冷却：这条先只记账，别再触发一次
                self._record_raw_message(event, is_bot=False)
                return False
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine._should_process_message", _amo_exc)

        if event.is_at_or_wake_command:
            # @ 机器人 / 唤醒词不走进判定，但必须留在上下文里：
            # 下一次“你……”要知道上一条是在叫她。
            self._record_raw_message(event, is_bot=False)
            umo = event.unified_msg_origin
            debounce_state = self._debounce_states.get(umo)
            if debounce_state and debounce_state.get("active"):
                debounce_state["cancelled"] = True
            return False

        try:
            self_id = str(event.get_self_id() or "")
            for component in event.message_obj.message:
                # 平台没返回 self_id 时不要瞎判：所有 @ 都当成「@别人」会把
                # 真正 @ 机器人的提问也挡掉。宁可交回模型按上下文判断。
                if self_id and isinstance(component, At) and str(component.qq) != self_id:
                    # @ 别人的消息同理：不判定，但它是“上一条在对谁说”的证据。
                    self._record_raw_message(event, is_bot=False)
                    return False
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine._should_process_message", _amo_exc)

        if event.message_str.strip().startswith(("/", "#", "!", "！")):
            return False
        return True

    def _clean_message_content(self, text: str, max_len: int = 200) -> str:
        """清洗消息内容：去除XML标签、替换链接、截断"""
        if not text:
            return ""
        text = RE_XML_TAG.sub("", text)
        text = RE_URL.sub("[链接]", text).strip()
        return (text[:max_len] + "...") if len(text) > max_len else text

    def _normalize_for_dedup(self, text: str) -> str:
        """消息归一化（用于去重）"""
        if not text:
            return ""
        return RE_URL.sub("[链接]", RE_XML_TAG.sub("", text)).strip().lower()

    @staticmethod
    def _message_meta(msg_obj) -> dict:
        """从消息对象里抠出 @ 对象和引用对象。

        ``event.message_str`` 在 AstrBot 里已经被拍平成纯文本，@ 的目标和
        “回复的是哪条消息”都会丢失；只靠文本判断，群聊里很容易把“你……”
        误判成在对机器人说。
        """
        meta: dict[str, Any] = {
            "msg_id": str(getattr(msg_obj, "message_id", "") or ""),
            "at_ids": [],
            "reply_to_id": "",
            "reply_to_uid": "",
            "reply_to_name": "",
        }
        try:
            components = getattr(msg_obj, "message", None) or []
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine._message_meta", _amo_exc)
            components = []
        for component in components:
            try:
                if isinstance(component, At):
                    target = str(getattr(component, "qq", "") or "")
                    if target:
                        meta["at_ids"].append(target)
                elif isinstance(component, Reply):
                    meta["reply_to_id"] = str(getattr(component, "id", "") or "")
                    meta["reply_to_uid"] = str(getattr(component, "sender_id", "") or "")
                    meta["reply_to_name"] = str(getattr(component, "sender_nickname", "") or "")
            except Exception as _amo_exc:
                _note_swallowed("heartflow_engine.HeartflowEngine._message_meta", _amo_exc)
                continue
        return meta

    async def on_group_message(self, event: AstrMessageEvent):
        """群消息监听入口"""
        if not self.config.get("enable_heartflow", False):
            return
        umo = event.unified_msg_origin
        state = self._get_chat_state(umo)

        # 检查是否处于防抖状态（针对特定用户）
        if self._absorb_debounced_message(event, umo, state):
            return

        if not self._should_process_message(event):
            return

        self._record_raw_message(event, is_bot=False)
        self._cleanup_old_messages(umo)

        try:
            is_immersive = self._is_immersive_mode_active(umo)
            res = await self.judge_immersive_mode(event) if is_immersive else await self.judge_with_tiny_model(event)

            if res.should_reply:
                # 判定通过 → 定向/插话 + 防抖 + 打触发标记（total_messages 在里面按分支结算）
                await self._prepare_heartflow_reply(event, umo, state, res, is_immersive)
                return

            state.total_messages += 1
        except Exception as e:
            logger.error(f"[心流] 处理异常: {e}\n{traceback.format_exc()}")

    def _absorb_debounced_message(self, event: AstrMessageEvent, umo: str, state: ChatState) -> bool:
        """防抖进行中：只收触发者的消息，其他人忽略；True＝这条已处理。"""
        debounce_key = f"{umo}:{event.get_sender_id()}"
        debounce_state = self._debounce_states.get(debounce_key)
        if not (debounce_state and debounce_state.get("active")):
            return False
        # 防抖期间：只处理触发者的消息，其他人忽略
        if debounce_state.get("trigger_sender_id") == str(event.get_sender_id()):
            # 触发者发的消息：追加文本（非文本用占位符），不重置计时
            text_content = event.message_str.strip() if event.message_str else "[非文本消息]"
            # 防抖期间的可能无限追加：留最近 30 条就够合并用了
            if len(debounce_state["pending_msgs"]) < 30:
                debounce_state["pending_msgs"].append(text_content)
            self._log_detail(f"[心流] ⏳ 防抖中收到触发者消息，已追加: {umo}")
        else:
            # 其他人发的消息：完全忽略
            self._log_detail(f"[心流] ⏳ 防抖中收到他人消息，已忽略: {umo}")

        # 无论谁发的，都记录原始消息（保持小模型上下文完整）
        self._record_raw_message(event, is_bot=False)
        self._cleanup_old_messages(umo)
        state.total_messages += 1
        return True

    def _decide_reply_target(self, event: AstrMessageEvent, res: JudgeResult):
        """判定这条是不是冲她说的：返回 (target, hard, style, mode_hint)；None＝不接。"""
        # 心流判定通过后，再检索一次上下文：这条到底是不是在跟机器人说。
        # 开了“只在被叫到时回复”：不是冲她说的就不开口（下面的闸会拦住）；
        # 关掉时才回到“随机挑个角度插进他们的话题里”。
        # 优先级：模型的语义判定 > 上下文检索的软证据；只有硬证据（叫名字 / @）才覆盖模型。
        target = res.target or "unknown"
        hinted, hard = self._retrieve_addressing(event)
        if hard:
            target = hinted
        elif target in ("", "unknown") and hinted:
            target = hinted
        style = res.reply_style or ""
        if hard and target == "other":
            # 模型把“你……”认成机器人，但上下文明确显示上一条 @ 的是别人：
            # 先撤掉“点名”标记，再决定是插话还是不接。
            res.is_mentioning_bot = False
            if res.context_type == "mention_bot":
                res.context_type = "other"
            if style == "answer":
                style = "comment"
            _text = event.message_str or ""
            if self._should_veto_side_question(target, hard, _text):
                # 两个人在一问一答，她插进去只会像“没问你，去玩吧你”
                logger.info("[心流] 🔇 这条明确在对别人说（上一条 @ 了别人），不接：%s" % _text[:40])
                return None
        verdict, verdict_reason = addressing_verdict(
            target=target,
            mentioning_bot=bool(res.is_mentioning_bot),
            hard=bool(hard),
            soft=bool(not hard and hinted == "bot"),
            only_when_addressed=self.only_when_addressed,
            forced=bool(getattr(event, "amo_force_reply", False)),
        )
        if not verdict:
            logger.info(
                "[心流] 🔇 没点名她，不搭话（%s）| target=%s | %s",
                verdict_reason, target or "?", (event.message_str or "")[:40],
            )
            return None
        if style == "none":
            style = "comment" if target != "bot" else "answer"

        # 回复方式：以模型的 context_type / reply_style 为主，target 兜底。
        # 「AI好强」这类感慨 → knowledge_comment → 插话；
        # 「你觉得AI强吗」→ 对机器人说 / question → 直接回答。
        if getattr(event, "amo_force_reply", False) or target == "bot" or res.is_mentioning_bot:
            mode_hint = "direct"
        elif res.context_type == "knowledge_comment":
            mode_hint = "interject"
        elif style == "answer":
            mode_hint = "direct"
        elif res.context_type == "question" and target in ("all", "unknown"):
            # 群里公开提问，机器人正面答一句是自然的
            mode_hint = "direct"
        else:
            mode_hint = "interject"
        return target, hard, style, mode_hint

    async def _prepare_heartflow_reply(self, event: AstrMessageEvent, umo: str, state: ChatState, res: JudgeResult, is_immersive: bool) -> None:
        """判定通过：写请求头 → 防抖合并 → 打触发标记。"""
        mode_name = "immersive" if is_immersive else "score"
        decision = self._decide_reply_target(event, res)
        if decision is None:
            state.total_messages += 1
            return
        target, hard, style, mode_hint = decision

        setattr(event, "heartflow_target", target)
        setattr(event, "heartflow_thought", res.reasoning or "")
        setattr(event, "heartflow_context_type", res.context_type or "")
        setattr(event, "heartflow_topic", res.topic or "")
        setattr(event, "heartflow_reply_style", style)
        if style == "quote":
            # 人多、指代不清时，她判断“引用着说更清楚”
            setattr(event, "amo_quote_hint", True)

        if mode_hint == "direct":
            setattr(event, "heartflow_mode_hint", "direct")
            logger.info(
                "[心流] 🎯 对象:%s(%s) 类型:%s → 按这条消息直接回（%s）"
                % (target, "硬证据" if hard else "模型判定", res.context_type or "?", (res.reasoning or "")[:40])
            )
        else:
            angle = random.choice(INTERJECT_ANGLES)
            setattr(event, "heartflow_mode_hint", "interject")
            setattr(event, "heartflow_angle", angle)
            logger.info(
                "[心流] 💬 对象:%s 类型:%s 话题:%s → 插话角度「%s」"
                % (target, res.context_type or "?", res.topic or "?", angle)
            )
        state.pending_heartflow_reply = True
        state.pending_heartflow_mode = mode_name
        self._set_judgment_paused(umo, paused=True)

        if self.enable_debounce:
            # 启动针对当前触发者的防抖；等待期间被取消就不回复，也不计这条消息
            if not await self._await_debounce(event, umo):
                self._set_judgment_paused(umo, paused=False)
                return

        event.is_at_or_wake_command = True
        setattr(event, "heartflow_triggered", True)
        setattr(event, "heartflow_mode", mode_name)
        self._activate_immersive_mode(umo)
        logger.info(f"[心流] 🎯 心流触发: {event.message_str[:50]}... | 模式: {'🌊沉浸' if is_immersive else '📊评分'}")
        state.total_messages += 1

    async def _await_debounce(self, event: AstrMessageEvent, umo: str) -> bool:
        """等防抖窗口结束并合并触发者的消息；False＝等待期间被取消。"""
        debounce_key = f"{umo}:{event.get_sender_id()}"
        with self._lock:
            self._debounce_states[debounce_key] = {
                "active": True,
                "start_time": _amo_clock.now_ts(),  # 固定起始时间，不重置
                "pending_msgs": [event.message_str.strip() or "[非文本消息]"],
                "cancelled": False,
                "trigger_sender_id": str(event.get_sender_id()),
            }
        logger.info(f"[心流] ⏳ 触发防抖等待 ({self.debounce_delay}s) | 触发者: {event.get_sender_name()}")

        # 防抖等待 + 清理放 try/finally：等待期间被取消/抛异常也不会留下 active=True 的死状态
        try:
            while True:
                with self._lock:
                    debounce_state = self._debounce_states.get(debounce_key)
                if not debounce_state:
                    break
                elapsed = _amo_clock.now_ts() - debounce_state["start_time"]
                # 达到基础延迟立即触发，或达到最大等待时间强制触发
                if elapsed >= self.debounce_delay or elapsed >= self.max_debounce_wait or debounce_state.get("cancelled"):
                    break
                await asyncio.sleep(min(self.debounce_delay - elapsed, 0.5))

            # 检查是否被取消
            with self._lock:
                debounce_state = self._debounce_states.get(debounce_key)
                cancelled = bool(debounce_state and debounce_state.get("cancelled"))
            if cancelled:
                logger.debug(f"[心流] 🚫 防抖被取消: {umo}")
                return False

            # 合并消息（只有触发者的消息）
            final_msgs = debounce_state["pending_msgs"] if debounce_state else [event.message_str]
            event.message_str = "\n".join(final_msgs)
            logger.debug(f"[心流] ✅ 防抖结束，准备回复（合并{len(final_msgs)}条）")
            return True
        finally:
            with self._lock:
                self._debounce_states.pop(debounce_key, None)

    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """记录大模型回复到本地缓冲（供小模型下次判断参考）"""
        if not getattr(event, "heartflow_triggered", False):
            return
        text = ""
        try:
            for attr in ("completion_text", "text", "content"):
                val = getattr(resp, attr, None)
                if isinstance(val, str) and val.strip():
                    text = val.strip()
                    break
            if not text:
                result = getattr(resp, "result", None)
                if result and getattr(result, "chain", None):
                    texts = [c.text.strip() for c in result.chain if isinstance(c, Plain) and c.text and c.text.strip()]
                    if texts:
                        text = "\n".join(texts)
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine.on_llm_response", _amo_exc)
        if text:
            self._record_bot_reply_manual(event.unified_msg_origin, text)

    async def on_after_message_sent(self, event: AstrMessageEvent):
        """消息发送后的状态更新"""
        if not self.config.get("enable_heartflow"):
            return
        umo = event.unified_msg_origin
        state = self._get_chat_state(umo)
        bot_reply_content = None
        has_reply = False

        try:
            res = event.get_result()
            if res and getattr(res, "chain", None):
                texts = []
                non_plain_reply = False
                for component in res.chain:
                    if isinstance(component, Plain):
                        if component.text and component.text.strip():
                            texts.append(component.text.strip())
                    else:
                        non_plain_reply = True
                if texts:
                    bot_reply_content = "\n".join(texts)
                    has_reply = True
                elif non_plain_reply:
                    bot_reply_content = "[非文本回复]"
                    has_reply = True
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine.on_after_message_sent", _amo_exc)

        msg_str = (event.message_str or "").strip()
        is_command = msg_str.startswith(("/", "#", "!", "！"))
        if has_reply and bot_reply_content and not is_command:
            self._record_bot_reply_manual(umo, bot_reply_content)

        if getattr(event, "heartflow_triggered", False) and state.pending_heartflow_reply:
            if has_reply:
                state.last_reply_time = _amo_clock.now_ts()
                state.total_replies += 1
            if self._is_immersive_mode_active(umo):
                self._refresh_immersive_mode(umo)
            else:
                self._activate_immersive_mode(umo)
            self._set_judgment_paused(umo, paused=False)
        elif event.is_at_or_wake_command and not is_command:
            if has_reply:
                state.last_reply_time = _amo_clock.now_ts()
            if not self._is_immersive_mode_active(umo):
                self._activate_immersive_mode(umo)
            else:
                self._refresh_immersive_mode(umo)
