# -*- coding: utf-8 -*-
"""注入用的上下文块：按时间回看、跨平台近聊、最终 _recall。"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .clock import from_timestamp as _clock_from_ts
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .clock import from_timestamp as _clock_from_ts
    except ImportError:  # 直接按文件加载时
        from clock import from_timestamp as _clock_from_ts

import re

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        _looks_like_time_query,
        _now,
        _render_time_block,
        logger,
        to_int,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            _looks_like_time_query,
            _now,
            _render_time_block,
            logger,
            to_int,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            _looks_like_time_query,
            _now,
            _render_time_block,
            logger,
            to_int,
        )

if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class RecallContextMixin(_MixinBase):
    async def _time_recall_context(self, event, uid):
        """按时间回看：他问"昨天下午/前天/上周三"时，去记忆终端取那段时间的聊天。"""
        if not self.cfg_bool("time_recall_enabled", True):
            return ""
        text = str(getattr(event, "message_str", "") or "")
        if not _looks_like_time_query(text):
            return ""
        mem = self._data_mem()
        if mem is None or not callable(getattr(mem, "recall_messages", None)):
            return ""
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            # 配 0 / 负数时以前会「请求 4 条、只渲染 1 条」，两边都按同一档位收敛
            limit = max(1, min(40, self.cfg_int("time_recall_limit", 6)))
            # 私聊：不传 uid。recall_messages 按 uid 过滤后只剩「他」说的话，她的回复全被滤掉，
            # 块里就成了「他：是啊 / 自己煮的 / 厉害吧」这种碎片；umo 已经把范围限在这一个
            # 1:1 会话，双方的话都要，不会串到别人。
            # 群聊：**仍然要按 uid**，否则会把群里其他人的话也拉进【按时间回看】。
            try:
                private = bool(event.is_private_chat())
            except Exception as _amo_exc:
                _note_swallowed("amo_memory.HumanCorePlugin._time_recall_context", _amo_exc)
                private = True
            if private:
                # 跨平台：同一个人在 QQ / 飞书两边说过的话都要算进「昨天聊了什么」，
                # 否则飞书说的「今天吃面」在 QQ 上问就等于没说过。
                profile = self.db.get_profile(uid) or {}
                umos = self._ritual_umos(profile)[:3] or [umo]
                gathered = []
                label = ""
                for one_umo in umos:
                    result = await self._data_call(
                        "recall_messages", self._data_bot_id(), query=text,
                        umo=one_umo, limit=min(limit * 2, 40),
                    )
                    if not isinstance(result, dict):
                        continue
                    label = label or str(result.get("label") or "")
                    gathered.extend(result.get("messages") or [])
                seen = set()
                merged = []
                for row in sorted(gathered, key=lambda r: to_int(r.get("created_at"), 0)):
                    key = (to_int(row.get("created_at"), 0), str(row.get("content") or "")[:80])
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(row)
                result = {"label": label, "messages": merged} if merged else {}
            else:
                result = await self._data_call(
                    "recall_messages", self._data_bot_id(), query=text, umo=umo,
                    uid=str(uid), limit=min(limit * 2, 40),
                )
        except Exception as exc:
            logger.debug("[amo] 时间回看失败: %s" % exc)
            return ""
        if not isinstance(result, dict):
            return ""
        messages = result.get("messages") or []
        label = str(result.get("label") or "")
        if not label:
            return ""
        return _render_time_block(label, messages, max_lines=max(1, int(limit)))
    async def _cross_platform_context(self, event, uid):
        """同一个人的其他平台（QQ / 飞书）说过什么，拼成一段给提示词用。"""
        mem = self._data_mem()
        if mem is None or not uid:
            return ""
        platform = self._platform_of(event)
        try:
            # 两个平台共用一个近聊池：只要 6 条的话，他在这头连聊几句就把另一头挤没了。
            detail = await self._data_call("person_context", self._data_bot_id(), platform, str(uid), 24)
        except Exception as _amo_exc:
            _note_swallowed("amo_memory.HumanCorePlugin._cross_platform_context", _amo_exc)
            return ""
        if not detail:
            return ""
        # person_context 是按时间倒序返回的（最新在前），这里统一按时间正序，
        # 下面取尾部才能拿到「最近」几条，而不是最旧的几条。
        recent_all = sorted(
            detail.get("recent") or [],
            key=lambda item: float(item.get("created_at") or 0),
        )
        detail = dict(detail)
        detail["recent"] = recent_all
        others = [
            item for item in (detail.get("others") or [])
            if str(item.get("uid") or "") and str(item.get("uid")) != str(uid)
        ]
        recent = detail.get("recent") or []
        lines = []
        if others:
            labels = []
            for item in others:
                labels.append("%s（%s）" % (item.get("label") or item.get("platform"), item.get("uid")))
            lines.append(
                "【跨平台】这个人在 " + "、".join(labels) + " 也是同一个人；"
                "不管他在哪个平台说话，都是同一个人在跟你聊。"
            )
        other_lines = []
        my_lines = []
        for item in recent:
            # summary/system 是摘要行，不是他当时说的话，别当成跨平台原话
            if str(item.get("role") or "") in ("summary", "system"):
                continue
            item_platform = str(item.get("platform") or "")
            if item_platform and item_platform == str(platform):
                # 本平台的近聊已经在 _recent() 里，不重复
                continue
            if not item_platform and str(item.get("uid") or "") == str(uid):
                continue
            text = str(item.get("content") or "").strip().replace("\n", " ")
            if not text:
                continue
            label = item.get("label") or item.get("platform") or "别的平台"
            line = "· [%s] %s" % (label, text[:120])
            if str(item.get("role") or "") == "bot":
                # 她自己说的：只留最近两句，够判断「另一边已经改口了」
                my_lines.append(line)
                continue
            other_lines.append(line)
        if other_lines:
            # 约定最新在尾部，取尾部而不是开头，免得喂最旧的几句
            lines.append("他在别的平台最近说过：\n" + "\n".join(other_lines[-6:]))
            lines.append(
                "这些事他已经在别的平台说过了，不要当成没说过、也不要再问一遍；"
                "不要把平台名、编号念出来。"
            )
        if my_lines:
            lines.append("你自己在别的平台最近说过：\n" + "\n".join(my_lines[-2:]))
            lines.append(
                "这些是你自己说的，不是他说的；别复述、别重复，"
                "和现在对不上就以【正在】为准，自然改口。"
            )
        return "\n".join(lines)
    async def _recall(self, uid, text, event=None):
        """记忆只来自「记忆终端」（Astrbot_amoupup_data）；没装才退回外挂记忆插件。

        陪伴自己不存记忆：读也只读记忆终端，读不到再退旧的插件库。
        """
        limit = max(1, min(50, self.cfg_int("memory_recall_limit", 6)))
        if self.cfg_str("context_style", "lean").strip().lower() == "lean" and event is not None:
            try:
                if not event.is_private_chat():
                    limit = min(limit, 4)
            except Exception as _amo_exc:
                _note_swallowed("amo_memory.HumanCorePlugin._recall", _amo_exc)
        memories = []
        hub = self._memory_hub()
        if hub is not None:
            try:
                private = True
                try:
                    private = bool(event.is_private_chat()) if event is not None else True
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory.HumanCorePlugin._recall", _amo_exc)
                    private = True
                if not private and len(str(text or "").strip()) <= 3:
                    limit = min(limit, 2)   # 群聊短消息只补 2 条，避免一句“猫”塞满提示词
                if hub.should_recall(text, private):
                    scope = "private"
                    group_id = ""
                    if event is not None and not private:
                        scope = "group"
                        try:
                            group_id = str(event.get_group_id() or "")
                        except Exception as _amo_exc:
                            _note_swallowed("amo_memory.HumanCorePlugin._recall", _amo_exc)
                            group_id = ""
                    memories = await hub.recall(uid, text, limit, scope, group_id)
            except Exception as exc:
                logger.debug("[amo] 能力层召回失败: %s" % exc)
                memories = []
        if not memories:
            try:
                memories = await self._data_memory_recall(uid, text, limit, event)
            except Exception as exc:
                logger.debug("[amo] 记忆终端召回异常: %s" % exc)

        unique = []
        seen = set()
        for item in memories:
            content = str(item.get("content", "")).strip()
            key = re.sub(r"[\s，。！？!?、；;：:]+", "", content).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
        memories = unique[:max(1, self.cfg_int("memory_recall_limit", 6))]
        if not memories:
            return [], ""
        lines = []
        for item in memories:
            source = str(item.get("source") or "")
            if source == "amo_data":
                label = "记忆"
            elif source == "companion":
                label = "陪伴记忆"
            else:
                tier = to_int(item.get("tier"))
                label = "珍贵" if tier >= 2 else ("重要" if tier == 1 else "普通")
            who = str(item.get("name") or "").strip()
            prefix = (who + "：") if (who and source in ("hub", "amo_data")) else ""
            # 老记忆里常有"今天/昨晚/刚才"这种相对说法：注入时补一个记录日期，
            # 免得她把 11-04 的事当成"昨晚"（用户反馈过一次）。
            body = str(item.get("content", ""))
            # 单条记忆先截断：一条 4000 字摘要会吃光 1200 字注入预算，
            # 后面的记忆一条都进不去（线上实测过，3 条长摘要召回次数全库最高）。
            item_cap = self.cfg_int("memory_item_max_chars", 240)
            item_cap = 0 if item_cap <= 0 else max(60, min(1000, item_cap))
            if item_cap and len(body) > item_cap:
                body = body[:item_cap].rstrip() + "…"
            rel_words = ("今天", "今晚", "昨天", "昨晚", "刚才", "刚刚", "前天", "这会儿", "这两天")
            created = to_int(item.get("created_at"))
            if created and any(w in body for w in rel_words):
                try:
                    day = _clock_from_ts(created)
                    if day.strftime("%Y-%m-%d") != _now().strftime("%Y-%m-%d"):
                        body = "（记于 %s）%s" % (day.strftime("%m-%d"), body)
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory.HumanCorePlugin._recall", _amo_exc)
            # 记忆都是“关于他”的：没名字的统一补「他：」，避免模型把「脸疼」当成自己的感受
            if not prefix and not body.startswith(("他", "她", "用户")):
                prefix = "他："
            lines.append("· " + prefix + body + "（" + label + "）")
        if lines:
            lines.append("（记忆里出现的『今天/昨晚/刚才』是当时记下的说法，不是现在；对不上就以他刚说的为准。）")
        text = "\n".join(lines)
        budget = self.cfg_int("memory_inject_max_chars", 1200)
        if budget > 0 and len(text) > budget:
            text = text[:budget]
            if "\n" in text:
                text = text.rsplit("\n", 1)[0]
        return memories, text
