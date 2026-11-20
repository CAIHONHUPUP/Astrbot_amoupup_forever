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
    BASE_JUDGE_PROMPT,
    JudgeResult,
    RawMessage,
    SCORE_MAX,
    SCORE_MIN,
    _build_rules_section,
    _extract_json,
    _normalize_context_type,
    _normalize_style,
    _normalize_target,
    safe_extract_score,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from heartflow_model import (
            BASE_JUDGE_PROMPT,
            JudgeResult,
            RawMessage,
            SCORE_MAX,
            SCORE_MIN,
            _build_rules_section,
            _extract_json,
            _normalize_context_type,
            _normalize_style,
            _normalize_target,
            safe_extract_score,
        )

def _build_immersive_prompt(self, event, remaining, activity, activity_desc):
    """沉浸模式提示词 + 系统约束 + 上下文；返回 (judge_prompt, system_inst, contexts)。"""
    system_inst = "你是严格的群聊机器人判断系统，严格返回JSON，不要输出其他内容。"
    immersive_reasoning_part = ', "reasoning": "简短原因"' if self.judge_include_reasoning else ""
    contexts = self._build_optimized_contexts(self._get_raw_history_for_context(event))
    
    judge_prompt = f"""
## 机器人身份
机器人名字：{self.bot_name or "机器人"}
机器人别名：{"、".join(self.bot_aliases) or "无"}
如果当前消息包含这些名字或别名，就是在叫机器人，不是叫其他群友。

## ⚠️ 最高优先级：追踪机器人自己的发言
对话历史（contexts）中，role 为 assistant 的消息是【机器人自己】之前说的话，role 为 user 的消息是群友说的话（带 [昵称] 前缀）。
先找到机器人最近说了什么，再判断当前消息与机器人发言的关系。

## 沉浸模式判断规则
先按顺序推理：① 从对话历史还原最近谁在跟谁说话 ② 判断当前这条在对谁说（bot / other / all / unknown）
③ 再决定要不要说话。

你的默认倾向是 **不回复**。只有在以下情况之一时才回复：
1. 消息直接 @ 了机器人或提到机器人名字
2. 消息明确向机器人提问
3. 消息是机器人发起的对话的延续
4. 消息内容与机器人刚聊的话题直接相关
5. 群聊中出现冷场，机器人的回复能自然活跃气氛
6. 群里正聊得热闹、话题轻松，机器人插一句能接上（仅当关闭“只在被叫到时回复”时才成立）

## 当前状态
- 模式: 沉浸模式 | 剩余: {remaining}秒 | 活跃度: {activity_desc} ({activity:.0%})

## 待判断消息
[{event.get_sender_name()}] {event.message_str}

## 严格判断
根据上述规则严格判断。沉默也是好的选择，但不要因为“没叫我”就一律沉默。
严格返回 JSON：
{{"should_reply": true或false, "target": "bot|other|all|unknown", "thought": "一句话说明在对谁说"{immersive_reasoning_part}}}
"""
    return judge_prompt, system_inst, contexts

if TYPE_CHECKING:
    from .heartflow_typing import HeartflowHost
else:  # 运行时基类仍是 object，行为不变
    HeartflowHost = object


class HeartflowTextMixin(HeartflowHost):
    """文案：评分提示词、上下文拼装、结果解析与面板状态文案。"""

    def _get_raw_history_for_context(self, event: AstrMessageEvent) -> List[RawMessage]:
        """获取用于小模型判断的原始历史（滑动窗口）"""
        msgs = self._get_raw_buffer(event.unified_msg_origin)
        # 排除当前消息本身（避免将待判断消息作为历史）
        if msgs and not msgs[-1].is_bot and msgs[-1].sender_id == str(event.get_sender_id()) and msgs[-1].content == event.message_str:
            msgs = msgs[:-1]
        return msgs[-self.judge_context_count:]

    def _build_optimized_contexts(self, raw_msgs: List[RawMessage]) -> List[Dict[str, str]]:
        """将原始消息构建为小模型所需的上下文格式（user/assistant 交替）。

        每条带 `[HH:MM]` 时间；群友消息额外带 `→@xxx` / `→回复xxx`，
        让小模型能看见“上一条 @ 的是谁”，而不是只看到一句光秃秃的“你”。
        """
        contexts: list[dict[str, str]] = []
        for msg in raw_msgs:
            content = self._clean_message_content(msg.content, 200)
            if not content:
                continue
            role = "assistant" if msg.is_bot else "user"
            stamp = _hf_ts(msg.timestamp).strftime("%H:%M")
            marks = []
            if msg.at_ids:
                if msg.at_self:
                    marks.append("→@你(机器人)")
                else:
                    targets = [item for item in msg.at_ids if item]
                    if targets:
                        marks.append("→@" + ",".join(targets))
            if msg.reply_to_id:
                if msg.reply_to_self:
                    who = "你(机器人)"
                else:
                    who = msg.reply_to_name or msg.reply_to_uid or "某条消息"
                marks.append("→回复" + str(who))
            if msg.is_bot:
                text = f"[{stamp}] {content}"
            elif msg.is_other_bot:
                text = f"[{stamp}] [机器人·{msg.sender_name or '机器人'}] {content}"
            else:
                flag = (" " + " ".join(marks)) if marks else ""
                text = f"[{stamp}] [{msg.sender_name}]{flag} {content}"
            # 合并连续同一角色的消息，减少上下文碎片
            if contexts and contexts[-1]["role"] == role:
                contexts[-1]["content"] += "\n" + text
            else:
                contexts.append({"role": role, "content": text})
        # 如果第一条是bot消息，转换为user角色，避免模型混淆
        if contexts and contexts[0]["role"] == "assistant":
            contexts[0]["role"] = "user"
            contexts[0]["content"] = "[Bot自身历史发言] " + contexts[0]["content"]
        return contexts

    def _analyze_conversation_flow(self, event: AstrMessageEvent) -> str:
        """分析对话流（话题提取、提问状态）"""
        msgs = self._get_raw_buffer(event.unified_msg_origin)[-self.context_messages_count:]
        if len(msgs) < 2:
            return "历史较短。"
        flow = []
        last, prev = msgs[-1], msgs[-2]
        if not last.is_bot and not prev.is_bot and any(q in prev.content for q in ("?", "？", "吗")):
            flow.append("用户似乎在回答前一条提问")
        if not flow:
            keywords = {"游戏", "工作", "吃", "学习", "代码", "问题", "群", "机器人"}
            topics = {kw for m in msgs for kw in keywords if kw in m.content}
            flow.append(f"话题: {','.join(topics)}" if topics else "闲聊")
        return " | ".join(flow)

    def _build_chat_context(self, event: AstrMessageEvent) -> str:
        """构建当前群聊状态摘要"""
        s = self._get_chat_state(event.unified_msg_origin)
        rate = s.total_replies / max(1, s.total_messages) * 100
        mode = "沉浸" if self._is_immersive_mode_active(event.unified_msg_origin) else "正常"
        dynamic_threshold, activity, _ = self._get_dynamic_threshold(event.unified_msg_origin)
        activity_desc = self._get_activity_description(activity)
        return (f"活跃度:{activity_desc}({activity:.0%}) | 回复率:{rate:.1f}% | "
                f"模式:{mode} | 阈值:{dynamic_threshold:.2f} | {_hf_now().strftime('%H:%M')}")

    def _group_members_line(self, event: AstrMessageEvent) -> str:
        """群成员一行字，给判定模型认人用；带（机器人）的标记别接话。"""
        try:
            desc = self.members.describe(event.unified_msg_origin, limit=12)
        except Exception as _amo_exc:
            _note_swallowed("heartflow_engine.HeartflowEngine._group_members_line", _amo_exc)
            desc = ""
        return desc or "（还没有记录）"

    def _get_last_bot_reply(self, event: AstrMessageEvent) -> Optional[str]:
        """获取机器人最后一次回复内容"""
        for m in reversed(self._get_raw_buffer(event.unified_msg_origin)):
            if m.is_bot and m.content.strip():
                return m.content
        return None

    async def judge_with_tiny_model(self, event: AstrMessageEvent) -> JudgeResult:
        """使用小模型进行多维度评分判断"""
        if getattr(event, "amo_force_reply", False):
            logger.debug("[心流] 💢 连续打扰强制回复")
            return JudgeResult(
                relevance=float(self.score_max), willingness=float(self.score_max),
                social=float(self.score_max), timing=float(self.score_max),
                continuity=float(self.score_max), reasoning="睡眠/上班期间被连续打扰，强制回复",
                should_reply=True, confidence=1.0, overall_score=1.0,
            )
        addressed = self._addresses_bot(event.message_str or "")
        if addressed:
            logger.info("[心流] 🎯 消息直接呼叫机器人（%s），跳过评分" % addressed)
            return JudgeResult(
                relevance=float(self.score_max), willingness=float(self.score_max),
                social=float(self.score_max), timing=float(self.score_max),
                continuity=float(self.score_max), reasoning="消息直接呼叫机器人名字/别名",
                should_reply=True, confidence=1.0, overall_score=1.0, target="bot",
            )
        provider = await self._resolve_judge_provider(event)
        if not provider:
            return JudgeResult(should_reply=False, reasoning="提供商未配置或不存在")

        dynamic_threshold, activity, _ = self._get_dynamic_threshold(event.unified_msg_origin)
        activity_desc = self._get_activity_description(activity)
        judge_prompt, system_inst, contexts = self._build_judge_prompt(
            event, dynamic_threshold, activity, activity_desc,
        )

        judge_timeout = float(getattr(self, "judge_timeout", 20.0) or 20.0)
        for attempt in range(self.judge_max_retries + 1):
            try:
                try:
                    resp = await asyncio.wait_for(
                        provider.text_chat(
                            prompt=judge_prompt,
                            contexts=contexts,
                            system_prompt=system_inst,
                            image_urls=[]
                        ),
                        timeout=judge_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[心流] 判定模型 %.0fs 无响应（这次不回复）" % judge_timeout)
                    return JudgeResult(should_reply=False, reasoning="判定超时")
                data = _extract_json(resp.completion_text.strip())
                return self._judge_result_from_data(event, data, dynamic_threshold, activity, activity_desc)
            except (json.JSONDecodeError, ValueError):
                if attempt == self.judge_max_retries:
                    return JudgeResult(should_reply=False, reasoning="JSON解析失败")
            except Exception as e:
                logger.error(f"[心流] 评分模式判断异常: {e}")
                return JudgeResult(should_reply=False, reasoning=f"异常: {str(e)}")
        return JudgeResult(should_reply=False, reasoning="未知错误")

    async def _resolve_judge_provider(self, event: AstrMessageEvent):
        """判定用哪个模型：指定 → 自动挑 → 当前会话默认；都没有就 None。"""
        provider = None
        if self.judge_provider_name:
            provider = self.context.get_provider_by_id(self.judge_provider_name)
        if not provider:
            provider = self._auto_pick_judge_provider()
        if not provider:
            try:
                provider = await self.context.get_using_provider_async(event.unified_msg_origin)
            except Exception as _amo_exc:
                _note_swallowed("heartflow_engine.HeartflowEngine.judge_with_tiny_model", _amo_exc)
                provider = None
        return provider

    def _build_judge_prompt(self, event: AstrMessageEvent, dynamic_threshold, activity, activity_desc: str):
        """拼评分提示词、系统约束和上下文；返回 (judge_prompt, system_inst, contexts)。"""
        scoring_rules = _build_rules_section(self.judge_rules)
        prompt_template = BASE_JUDGE_PROMPT
        reasoning_part = ', "reasoning": "简短分析原因"' if self.judge_include_reasoning else ""
        mid_score = (self.score_min + self.score_max) / 2
        fmt_kwargs = dict(
            chat_id=event.unified_msg_origin,
            minutes_since_reply=self._get_minutes_since_last_reply(event.unified_msg_origin),
            chat_context=self._build_chat_context(event),
            chat_flow=self._analyze_conversation_flow(event),
            last_bot_reply=self._get_last_bot_reply(event) or "无",
            sender_name=event.get_sender_name(),
            message=event.message_str,
            bot_name=self.bot_name or "机器人",
            bot_aliases="、".join(self.bot_aliases) or "无",
            group_members=self._group_members_line(event),
            scoring_rules=scoring_rules,
            threshold=dynamic_threshold,
            reasoning_part=reasoning_part,
            score_min=int(self.score_min), score_max=int(self.score_max), mid=int(mid_score),
            activity_level=activity, activity_desc=activity_desc,
        )

        try:
            judge_prompt = prompt_template.format(**fmt_kwargs)
        except KeyError as e:
            logger.warning(f"[心流] 提示词模板变量缺失 {e}，使用基础默认模板重试")
            judge_prompt = BASE_JUDGE_PROMPT.format(**fmt_kwargs)

        system_inst = f"你是群聊机器人决策系统，严格返回JSON，不要输出其他内容。所有分数必须在{int(self.score_min)}到{int(self.score_max)}之间，不能为0。"
        contexts = self._build_optimized_contexts(self._get_raw_history_for_context(event))
        return judge_prompt, system_inst, contexts

    def _judge_result_from_data(self, event: AstrMessageEvent, data: Dict[str, Any], dynamic_threshold, activity, activity_desc: str) -> JudgeResult:
        """模型 JSON → JudgeResult（五维归一 + 阈值 + 定向 + 一条日志）。"""
        scores = {
            "relevance": safe_extract_score(data, "relevance", self.score_min, self.score_max),
            "willingness": safe_extract_score(data, "willingness", self.score_min, self.score_max),
            "social": safe_extract_score(data, "social", self.score_min, self.score_max),
            "timing": safe_extract_score(data, "timing", self.score_min, self.score_max),
            "continuity": safe_extract_score(data, "continuity", self.score_min, self.score_max),
        }
        normalized_scores = {k: (v - self.score_min) / (self.score_max - self.score_min) for k, v in scores.items()}
        overall = sum(normalized_scores[k] * self.weights[k] for k in self.weights)
        should_reply = overall >= dynamic_threshold
        target = _normalize_target(data.get("target"))
        thought = str(data.get("thought") or "").strip()
        mention = bool(data.get("is_mentioning_bot", False))
        if mention:
            target = "bot"
        context_type = _normalize_context_type(data.get("context_type"))
        topic = str(data.get("topic") or "").strip()[:20]
        style = _normalize_style(data.get("reply_style"))
        force = bool(getattr(event, "amo_force_reply", False))
        # 模型说“这次别说话”才压掉；如果是被点名/@，仍按对机器人说处理
        if style == "none" and not force and not mention and target != "bot":
            should_reply = False
        avg_absolute_score = sum(scores.values()) / len(scores)
        dim_order = ["relevance", "willingness", "social", "timing", "continuity"]
        dim_scores = " | ".join([f"{dim}:{scores[dim]:.1f}" for dim in dim_order])
        who = {"bot": "对机器人说", "other": "对某个群友说", "all": "对全群说", "unknown": "看不出对谁说"}.get(target, "未判定对象")
        extra = " | 类型:%s | 话题:%s | 风格:%s" % (context_type or "?", topic or "?", style or "?")

        if should_reply:
            logger.info(f"[心流] 📊 评分模式: {overall:.3f}/{dynamic_threshold:.2f} (均分:{avg_absolute_score:.1f}分) | ✅触发 | {who}{extra} | 活跃度:{activity_desc}({activity:.0%}) | {thought or data.get('reasoning', '')} | 各维度: {dim_scores}")
        else:
            logger.info(f"[心流] 📊 评分模式: {overall:.3f}/{dynamic_threshold:.2f} (均分:{avg_absolute_score:.1f}分) | ❌跳过 | {who}{extra} | 活跃度:{activity_desc}({activity:.0%}) | {thought or data.get('reasoning', '')} | 各维度: {dim_scores}")
        return JudgeResult(
            **scores, reasoning=data.get("reasoning", "") or thought,
            should_reply=should_reply, confidence=overall, overall_score=overall,
            target=target or "unknown", is_mentioning_bot=mention,
            context_type=context_type, topic=topic, reply_style=style,
        )


    async def judge_immersive_mode(self, event: AstrMessageEvent) -> JudgeResult:
        """沉浸模式下的判断（更严格）"""
        if getattr(event, "amo_force_reply", False):
            logger.info("[心流] 💢 连续打扰，沉浸模式也回复")
            return JudgeResult(
                relevance=float(self.score_max), willingness=float(self.score_max),
                social=float(self.score_max), timing=float(self.score_max),
                continuity=float(self.score_max), reasoning="睡眠/上班期间被连续打扰，强制回复",
                should_reply=True, confidence=1.0, overall_score=1.0,
            )
        addressed = self._addresses_bot(event.message_str or "")
        if addressed:
            logger.info("[心流] 🌊 消息直接呼叫机器人（%s），沉浸模式也回复" % addressed)
            return JudgeResult(
                relevance=float(self.score_max), willingness=float(self.score_max),
                social=float(self.score_max), timing=float(self.score_max),
                continuity=float(self.score_max), reasoning="消息直接呼叫机器人名字/别名",
                should_reply=True, confidence=1.0, overall_score=1.0, target="bot",
            )
        provider = await self._resolve_judge_provider(event)

        remaining = self._get_immersive_mode_remaining(event.unified_msg_origin)
        _, activity, _ = self._get_dynamic_threshold(event.unified_msg_origin)
        activity_desc = self._get_activity_description(activity)
        judge_prompt, system_inst, contexts = _build_immersive_prompt(
            self, event, remaining, activity, activity_desc,
        )
        judge_timeout = float(getattr(self, "judge_timeout", 20.0) or 20.0)
        for attempt in range(self.judge_max_retries + 1):
            try:
                try:
                    resp = await asyncio.wait_for(
                        provider.text_chat(
                            prompt=judge_prompt,
                            contexts=contexts,
                            system_prompt=system_inst,
                            image_urls=[]
                        ),
                        timeout=judge_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[心流] 判定模型 %.0fs 无响应（这次不回复）" % judge_timeout)
                    return JudgeResult(should_reply=False, reasoning="判定超时")
                data = _extract_json(resp.completion_text.strip())
                should_reply = bool(data.get("should_reply", False))
                target = _normalize_target(data.get("target")) or "unknown"
                thought = str(data.get("thought") or "").strip()
                if should_reply:
                    logger.info(f"[心流] 🌊 沉浸模式: ✅触发 | 对象:{target} | 剩余{remaining}秒 | 活跃度:{activity_desc}({activity:.0%}) | {thought or data.get('reasoning', '')}")
                else:
                    logger.info(f"[心流] 🌊 沉浸模式: ❌跳过 | 对象:{target} | 剩余{remaining}秒 | 活跃度:{activity_desc}({activity:.0%}) | {thought or data.get('reasoning', '')}")
                return JudgeResult(should_reply=should_reply, reasoning=data.get("reasoning", "") or thought, confidence=1.0 if should_reply else 0.0, overall_score=1.0 if should_reply else 0.0, target=target)
            except (json.JSONDecodeError, ValueError):
                if attempt == self.judge_max_retries:
                    return JudgeResult(should_reply=False, reasoning="JSON解析失败")
            except Exception as e:
                logger.error(f"[心流] 沉浸模式判断异常: {e}")
                return JudgeResult(should_reply=False, reasoning=f"异常: {str(e)}")
        return JudgeResult(should_reply=False, reasoning="未知错误")

    async def heartflow_status(self, event: AstrMessageEvent):
        """查看心流插件运行状态"""
        umo = event.unified_msg_origin
        s = self._get_chat_state(umo)
        rate = (s.total_replies / max(1, s.total_messages) * 100) if s.total_messages > 0 else 0.0
        is_immersive = self._is_immersive_mode_active(umo)
        remaining = self._get_immersive_mode_remaining(umo)
        is_paused = self._is_judgment_paused(umo)
        buffer_size = len(self._raw_msg_buffer.get(umo, []))
        dynamic_threshold, activity, absolute_threshold = self._get_dynamic_threshold(umo)
        activity_desc = self._get_activity_description(activity)
        config_source = self._get_group_config_info(umo)

        if self.enable_dynamic_threshold:
            dynamic_info = "✅启用"
            current_threshold_info = f"当前: {dynamic_threshold:.2f} (绝对值: {absolute_threshold:.1f}分)"
        else:
            dynamic_info = "❌关闭"
            current_threshold_info = f"固定值: {dynamic_threshold:.2f} (绝对值: {absolute_threshold:.1f}分)"

        pending_info = "✅是" if s.pending_heartflow_reply else "❌否"
        log_info = "🔊开启" if self.enable_detailed_log else "🔇关闭"
        debounce_info = f"✅启用 ({self.debounce_delay}s/{self.max_debounce_wait}s)" if self.enable_debounce else "❌关闭"

        yield event.plain_result(f"""
🔮 **心流状态**
━━━━━━━━━━━━━━━━━━━━
📊 **统计信息**
• 上次回复: {self._get_minutes_since_last_reply(umo)}分钟前
• 消息总数: {s.total_messages} | 实际回复: {s.total_replies} ({rate:.1f}%)
• 缓冲区: {buffer_size}条消息

⚙️ **配置信息**
• 评分范围: {int(self.score_min)}-{int(self.score_max)}分
• 基础阈值: {self.reply_threshold} | 沉浸持续: {self.immersive_mode_duration}秒
• 暂停超时: {self.judgment_paused_timeout}秒 | 防抖延迟: {debounce_info}
• 详细日志: {log_info}

🎚️ **阈值配置**
• 来源: {config_source}
• 动态阈值: {dynamic_info}
• {current_threshold_info}

📊 **当前活跃度**
• 状态: {activity_desc} ({activity:.0%})

🌊 **当前模式**
• 沉浸模式: {"✅激活" if is_immersive else "❌未激活"} ({remaining}秒)
• 判断状态: {"⏸️ 已暂停" if is_paused else "▶️ 正常"}
• 等待Bot回复: {pending_info}
━━━━━━━━━━━━━━━━━━━━""")

    async def heartflow_debug(self, event: AstrMessageEvent):
        """查看最近的消息记录（调试用）"""
        umo = event.unified_msg_origin
        msgs = self._get_raw_buffer(umo)
        if not msgs:
            yield event.plain_result("📭 缓冲区为空，暂无消息记录")
            return
        lines = ["📋 **最近消息记录**（最多20条）\n━━━━━━━━━━━━━━"]
        for msg in msgs[-20:]:
            prefix = "🤖" if msg.is_bot else "👤"
            time_str = _hf_ts(msg.timestamp).strftime("%H:%M:%S")
            content = msg.content[:60] + "..." if len(msg.content) > 60 else msg.content
            lines.append(f"{prefix} [{time_str}] {msg.sender_name}: {content or '[空消息]'}")
        yield event.plain_result("\n".join(lines) + f"\n━━━━━━━━━━\n📊 共 {len(msgs)} 条记录")

    async def heartflow_clear_buffer(self, event: AstrMessageEvent):
        """清除当前群的消息缓冲区"""
        umo = event.unified_msg_origin
        with self._lock:
            if umo in self._raw_msg_buffer:
                count = len(self._raw_msg_buffer[umo])
                self._raw_msg_buffer[umo].clear()
                yield event.plain_result(f"✅ 已清除 {count} 条缓冲消息")
            else:
                yield event.plain_result("📭 缓冲区为空，无需清除")

    async def heartflow_toggle_log(self, event: AstrMessageEvent):
        """切换详细日志开关（管理员）"""
        self.enable_detailed_log = not self.enable_detailed_log
        status = "🔊 已开启" if self.enable_detailed_log else "🔇 已关闭"
        logger.debug(f"[心流] 详细日志已切换: {status} (操作者: {event.get_sender_name()})")
        yield event.plain_result(f"{status} 详细日志")

    async def heartflow_reset(self, event: AstrMessageEvent):
        """重置当前群的所有心流状态（管理员）"""
        cid = event.unified_msg_origin
        with self._lock:
            self.chat_states.pop(cid, None)
            self._raw_msg_buffer.pop(cid, None)
            self._cancel_pause_restore_task(cid)
            # 清理该群所有用户的防抖状态
            keys_to_remove = [k for k in self._debounce_states if k.startswith(cid)]
            for k in keys_to_remove:
                self._debounce_states.pop(k, None)
        yield event.plain_result("✅ **心流状态已完全重置**")

    async def heartflow_help(self, event: AstrMessageEvent):
        """显示心流插件所有命令说明"""
        yield event.plain_result("🔮 **心流插件 - 命令帮助**\n• `/heartflow` - 查看运行状态\n• `/heartflow_debug` - 查看消息缓冲记录\n• `/heartflow_clear` - 清空消息缓冲区\n• `/heartflow_log` - 切换详细日志开关 (管理员)\n• `/heartflow_reset` - 重置所有状态 (管理员)\n• `/heartflow_help` - 显示此帮助")

    async def terminate(self):
        """插件卸载时的清理工作"""
        for task in list(self._pause_restore_tasks.values()):
            if task and not task.done():
                task.cancel()
        self._pause_restore_tasks.clear()
        self._debounce_states.clear()
        logger.debug("[心流] 心流插件已卸载，后台任务已清理")
