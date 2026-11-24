# -*- coding: utf-8 -*-
"""注入用的上下文块：按时间回看、跨平台近聊、最终 _recall。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .clock import from_timestamp as _clock_from_ts
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .clock import from_timestamp as _clock_from_ts
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from clock import from_timestamp as _clock_from_ts

import re

try:  # 包模式
    from .amo_memory_rerank_mixin import MemoryRerankMixin
except ImportError:  # 直接按文件加载时
    from amo_memory_rerank_mixin import MemoryRerankMixin

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
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
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


_MEDIA_WARNED = {"done": False}


def _media_helpers():
    """媒体占位记录的读写口径（唯一定义在 amo_message，避免两套正则）。"""
    try:
        try:
            from .amo_message import is_media_record as _is_record
            from .amo_message import strip_media_markers as _strip
        except ImportError:
            from amo_message import is_media_record as _is_record
            from amo_message import strip_media_markers as _strip
        return _is_record, _strip
    except Exception as exc:
        # 兜底返回「不是媒体」等于整块功能静默失效（照片占位行会进上下文被复读）；
        # 只提醒一次，别每条消息刷屏。
        if not _MEDIA_WARNED["done"]:
            _MEDIA_WARNED["done"] = True
            logger.warning("[amo] 媒体占位清洗不可用（照片/语音占位行会进上下文）: %r", exc)
        return (lambda text: False), (lambda text: str(text or ""))


def _ledger_helpers():
    """记忆事实账本的口径（唯一定义在 amo_memory_ledger，避免两套过滤）。"""
    try:
        try:
            from .amo_memory_ledger import filter_recalled, format_memory_text
        except ImportError:
            from amo_memory_ledger import filter_recalled, format_memory_text
        return filter_recalled, format_memory_text
    except Exception:
        return (lambda plugin, uid, rows: list(rows or [])), (lambda text: str(text or ""))


def _clean_context_message(row):
    """记忆注入前清洗一行：媒体占位记录整行不要，正文里的标记剥掉。"""
    if not isinstance(row, dict):
        return row
    is_media_record, strip_media_markers = _media_helpers()
    content = str(row.get("content") or "")
    if is_media_record(content):
        return None
    cleaned = strip_media_markers(content)
    if cleaned != content:
        row = dict(row)
        row["content"] = cleaned
    return row


def memory_subject(item):
    """记忆主体：优先 subject 字段；旧记忆按 bot_ 前缀 kind 兜底，默认 user。"""
    row = item if isinstance(item, dict) else {}
    subject = str(row.get("subject") or "").strip().lower()
    if subject in ("user", "bot"):
        return subject
    kind = str(row.get("kind") or row.get("memory_type") or "")
    content = str(row.get("content") or "")
    if kind.startswith("bot_") or content.startswith(("[我发的照片]", "[我发的语音]")):
        return "bot"
    return "user"


class RecallContextMixin(MemoryRerankMixin, _MixinBase):
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
            # 配 0 / 负数时以前会「请求 4 条、只渲染 1 条」，两边都按同一档位收敛。
            # 注意这个数一处两用：取回时按 limit×2 拉候选，渲染时按 limit 行截断。
            # 想「多取少显」就改下面的 ×2，别直接调大 limit（会把块也撑长）。
            limit = max(1, min(40, self.cfg_int("time_recall_limit", 6)))
            span = None
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
                gathered: list[Any] = []
                label = ""
                for one_umo in umos:
                    result = await self._data_call(
                        "recall_messages", self._data_bot_id(), query=text,
                        umo=one_umo, limit=min(limit * 2, 40),
                    )
                    if not isinstance(result, dict):
                        continue
                    label = label or str(result.get("label") or "")
                    if span is None:
                        span = result.get("span") or None
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
                if isinstance(result, dict):
                    span = result.get("span") or None
        except Exception as exc:
            logger.debug("[amo] 时间回看失败: %s" % exc)
            return ""
        if not isinstance(result, dict):
            return ""
        messages = result.get("messages") or []
        label = str(result.get("label") or "")
        if not label:
            return ""
        # 媒体占位记录不是聊天原话：整行剔除；她自己的旧场景断言降级成过去时。
        # 以前这里只做媒体清洗，和【最近对话】走的是两套口径——同一句「我在小馆吃」
        # 进最近对话会被降级、进时间回看不会，取尾部后最容易把刚说过的话当成"现在"。
        try:
            try:
                from .amo_message import sanitize_context_rows as _sanitize_rows
            except ImportError:
                from amo_message import sanitize_context_rows as _sanitize_rows
            messages = _sanitize_rows(self, messages)
        except Exception as exc:  # cleanup: 清洗不可用就退回只去媒体，聊天不能因此断开
            _note_swallowed("amo_memory.HumanCorePlugin._time_recall_context", exc)
            messages = [
                clean for clean in (_clean_context_message(row) for row in messages)
                if clean is not None
            ]
        # 取尾还是取头：窗口在过去的（昨晚/前天/上周三）取末尾，问「最后一句」才有答案；
        # 窗口已经盖到此刻的（今天/今晚）取开头，尾部那几句【最近对话】已经注入过，
        # 再取一遍等于同一段话喂两次。解析不出时间（退化成「最近 N 条」）同样取开头。
        try:
            span_end = float((span or {}).get("end") or 0)
        except (TypeError, ValueError, AttributeError):
            span_end = 0.0
        try:
            now_epoch = float(_now().timestamp())
        except Exception:
            now_epoch = 0.0
        from_tail = bool(span_end and now_epoch and span_end < now_epoch - 1800)
        return _render_time_block(label, messages, max_lines=max(1, int(limit)),
                                  from_tail=from_tail)
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
        lines: list[Any] = []
        if others:
            labels = []
            for item in others:
                labels.append("%s（%s）" % (item.get("label") or item.get("platform"), item.get("uid")))
            lines.append(
                "【跨平台】这个人在 " + "、".join(labels) + " 也是同一个人；"
                "不管他在哪个平台说话，都是同一个人在跟你聊。"
                "【跨平台·别重复问】下面他在另一个平台已经回答过的事（吃了什么/在哪/在干嘛）"
                "就是已经说过，不要再问一遍；要接住，别问「你那边呢 / 中午吃了吗 / 又随便对付了」。"
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
            raw_text = str(item.get("content") or "").strip().replace("\n", " ")
            if not raw_text:
                continue
            _is_media_record, _strip_media = _media_helpers()
            if _is_media_record(raw_text):
                continue
            text = _strip_media(raw_text)
            if not text:
                continue
            label = item.get("label") or item.get("platform") or "别的平台"
            group_name = str(item.get("group_name") or "").strip()
            if group_name:
                label = "%s · %s" % (label, group_name)
            # 必须标出「他 / 她」：只写「· [飞书] 番茄蘑菇汤」模型分不清是谁说的，
            # 就会当成闲聊而不是「他已经回答过」。
            who = "她" if str(item.get("role") or "") == "bot" else "他"
            line = "· [%s] %s：%s" % (label, who, text[:120])
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
                    hub_platform = ""
                    try:
                        if event is not None:
                            hub_platform = self._platform_of(event)
                    except Exception as _amo_exc:
                        _note_swallowed("amo_memory.HumanCorePlugin._recall", _amo_exc)
                        hub_platform = ""
                    try:
                        memories = await hub.recall(uid, text, limit, scope, group_id, hub_platform)
                    except TypeError:
                        # 旧版 hub 的 recall 没有 platform 参数
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
        _is_media_record, _strip_media = _media_helpers()
        for item in memories:
            content = str(item.get("content", "")).strip()
            if not content or _is_media_record(content):
                continue
            content = _strip_media(content)
            key = re.sub(r"[\s，。！？!?、；;：:]+", "", content).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
        # task-28：系统/运维词零进提示词；同一件商品只保留一个 active 状态（新 supersede 旧）
        try:
            filter_recalled, _format_memory_text = _ledger_helpers()
            unique = filter_recalled(self, uid, unique)
        except Exception as _amo_exc:
            _note_swallowed("amo_memory.HumanCorePlugin._recall.ledger", _amo_exc)
        # 每轮只重排一次：先在候选池里挑，再按 limit 截断（候选 ≤ limit 时不调模型）
        cap = max(1, int(limit or self.cfg_int("memory_recall_limit", 6)))
        pool = unique[:max(cap, self.cfg_int("memory_rerank_candidates", 10))]
        reranker = getattr(self, "_rerank_memories", None)
        if callable(reranker):
            memories = await reranker(pool, text, cap, event)
        else:  # 单测 FakePlugin 没有重排方法：按原顺序截断
            memories = pool[:cap]
        if not memories:
            return [], ""
        lines: list[Any] = []
        _recall_format(self, item, lines, memories)
        text = "\n".join(lines)
        budget = self.cfg_int("memory_inject_max_chars", 1200)
        if budget > 0 and len(text) > budget:
            text = text[:budget]
            if "\n" in text:
                text = text.rsplit("\n", 1)[0]
        return memories, text



def _recall_format(self, item, lines, memories):
    _is_media_record, _strip_media = _media_helpers()
    for item in memories:
        source = str(item.get("source") or "")
        subject = memory_subject(item)
        if _is_media_record(item.get("content")):
            continue
        if source == "amo_data":
            label = "记忆"
        elif source == "companion":
            label = "陪伴记忆"
        else:
            tier = to_int(item.get("tier"))
            label = "珍贵" if tier >= 2 else ("重要" if tier == 1 else "普通")
        who = str(item.get("name") or "").strip()
        prefix = (who + "：") if (who and source in ("hub", "amo_data")) else ""
        if subject == "bot":
            prefix = "[bot] "
        # 老记忆里常有"今天/昨晚/刚才"这种相对说法：注入时补一个记录日期，
        # 免得她把 11-04 的事当成"昨晚"（用户反馈过一次）。
        body = _strip_media(item.get("content", ""))
        # task-28：精确余额/价格/等待分钟只以大概数注入（「卡里还有4072」→「卡里还有四千多」）
        try:
            _filter_recalled, format_memory_text = _ledger_helpers()
            body = format_memory_text(body)
        except Exception as _amo_exc:
            _note_swallowed("amo_memory.HumanCorePlugin._recall_format", _amo_exc)
        # 单条记忆先截断：一条 4000 字摘要会吃光 1200 字注入预算，
        # 后面的记忆一条都进不去（线上实测过，3 条长摘要召回次数全库最高）。
        item_cap = self.cfg_int("memory_item_max_chars", 240)
        item_cap = 0 if item_cap <= 0 else max(60, min(1000, item_cap))
        if item_cap and len(body) > item_cap:
            body = body[:item_cap].rstrip() + "…"
        rel_words = ("今天", "今晚", "昨天", "昨晚", "刚才", "刚刚", "前天", "这会儿", "这两天")
        created = to_int(item.get("created_at"))
        if created:
            try:
                # 防线一：统一用 [今日]/[昨日]/[前天]/[日期]，不再只分今天/昨天
                try:
                    from .amo_immersion_guard import time_label as _time_label
                except ImportError:
                    from amo_immersion_guard import time_label as _time_label
                label = _time_label(created, _now())
                if label:
                    if label == "[今日]":
                        label = "[今日 %s]" % _clock_from_ts(created).strftime("%H:%M")
                    body = label + " " + body
                elif any(w in body for w in rel_words):
                    body = "（记于 %s）%s" % (_clock_from_ts(created).strftime("%m-%d"), body)
            except Exception as _amo_exc:
                _note_swallowed("amo_memory.HumanCorePlugin._recall", _amo_exc)
        # 用户记忆默认补「他：」；bot 自己的记忆用 [bot]，不能标成用户。
        if subject == "user" and not prefix and not body.startswith(("老公", "他", "她", "用户")):
            prefix = "老公："
        # 记忆带来源平台时标出来：别把飞书说过的话当成 QQ 刚说的
        src = str(item.get("platform") or "").strip()
        src_label = ""
        if src:
            try:
                src_label = str(self._platform_label(src) or src)
            except Exception as _amo_exc:
                _note_swallowed("amo_memory.HumanCorePlugin._recall", _amo_exc)
                src_label = src
        lines.append("· " + prefix + body + "（" + label
                     + (" · 来源:" + src_label if src_label else "") + "）")
    if lines:
        lines.append("（每条记忆前的 [今日]/[昨日]/[前天]/[日期] 是记录时间。写职场、出门的事时，"
                     "昨天或更早的必须说成「昨天」「以前」，不能说成刚刚。"
                     "旧的约定/约会按记录日期说，别当成今天或今晚的安排；"
                     "记忆里她自己的念头（好想吃、想买）不是他的承诺；"
                     "只有他说定时间（周六、周日、今晚）才算约定，「下次带你去/改天」不算；"
                     "他没在这次对话里说过的话，不要说成「你刚说/你说过」。）")

