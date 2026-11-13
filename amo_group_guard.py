# -*- coding: utf-8 -*-
"""群聊身份识别：谁是机器人、谁是谁、这句话是不是冲她来的。

参考现有的两套 AstrBot 生态做法：
- ContextAware（muyouzhi6/astrbot_plugin_context_aware）：把「谁在跟谁说话、
  这次是主动搭话还是被动回复」从消息结构里拆出来，别让模型从一句“你……”里猜；
- 私人陪伴（menglimi/astrbot_plugin_private_companion）的 group_addressing：
  at_bot / reply_bot / mention_bot_name / at_other / reply_other 分开判定，
  并用 bot_markers 把别的机器人认出来。

这里只做纯函数 + 一份内存名册，不碰 AstrBot 事件对象，方便单测。
三条原则：
1. 别的机器人发的话只当背景，不判定、不接话；
2. 群成员按 uid 记名字，认人时用名册里的名字当证据；
3. 没有「对她说」的证据（@ / 引用 / 叫名字 / 接着她的话）就不开口。
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# 默认的机器人昵称关键词：英文按独立单词匹配，中文按包含匹配。
# 刻意不收「小爱 / 小冰 / 小微 / 助手」这种像真人昵称的词：昵称叫“小爱”的群友
# 一旦被当成机器人，会连 @ 都不回——误伤真人比漏认机器人严重得多。
DEFAULT_BOT_KEYWORDS: Tuple[str, ...] = (
    "bot", "robot", "assistant", "gpt", "chatgpt", "claude", "llm",
    "机器人", "小助手", "智能助手", "小爱同学", "天猫精灵",
)

_SPLIT_RE = re.compile(r"[,，;；\s]+")


def normalize_name(name: Any) -> str:
    """昵称归一：去掉空白，让“墨辞烟”和“墨 辞 烟”算同一个人。"""
    return re.sub(r"\s+", "", str(name or "")).strip()


def parse_keywords(raw: Any) -> Tuple[str, ...]:
    """配置里的关键词：逗号/空格分隔的字符串，或列表；空值回落默认表。"""
    if isinstance(raw, str):
        items = [item.strip() for item in _SPLIT_RE.split(raw) if item.strip()]
    elif isinstance(raw, (list, tuple, set)):
        items = [str(item).strip() for item in raw if str(item).strip()]
    else:
        items = []
    return tuple(items) or DEFAULT_BOT_KEYWORDS


def parse_ids(raw: Any) -> Set[str]:
    """配置里的机器人账号：逗号分隔的字符串，或列表。"""
    if isinstance(raw, str):
        return {item.strip() for item in _SPLIT_RE.split(raw) if item.strip()}
    if isinstance(raw, (list, tuple, set)):
        return {str(item).strip() for item in raw if str(item).strip()}
    return set()


@lru_cache(maxsize=64)
def _ascii_pattern(keyword: str) -> "re.Pattern[str]":
    """英文关键词的正则只编译一次：每条群消息都要查一遍昵称。"""
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])")


def _ascii_keyword_hit(lowered_name: str, keyword: str) -> bool:
    return bool(_ascii_pattern(keyword).search(lowered_name))


def is_bot_name(name: Any, keywords: Sequence[str] = DEFAULT_BOT_KEYWORDS) -> bool:
    """昵称像不像机器人。

    英文按独立单词匹配：Bottle、abbot 不会因为含 bot 被误伤；
    中文按包含匹配：小助手、群机器人 都能认出来。
    """
    raw = str(name or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    compact = normalize_name(lowered)
    for keyword in keywords:
        word = str(keyword or "").strip().lower()
        if not word:
            continue
        if word.isascii():
            if _ascii_keyword_hit(lowered, word):
                return True
        elif word in compact:
            return True
    return False


def is_bot_sender(
    sender_name: Any,
    sender_id: Any,
    keywords: Sequence[str] = DEFAULT_BOT_KEYWORDS,
    bot_ids: Sequence[str] = (),
) -> bool:
    """这个发言人是不是别的机器人：先看显式账号名单，再看昵称。"""
    uid = str(sender_id or "").strip()
    if uid:
        for item in bot_ids:
            if str(item).strip() == uid:
                return True
    return is_bot_name(sender_name, keywords)


class MemberBook:
    """群成员名册（内存）：谁是谁、谁在说话、谁是机器人。

    只用于群聊语境：让判定模型知道“这条在叫谁”，也让主脑知道群里都有谁。
    每个群各自一份，按最后发言时间淘汰，不落盘。
    """

    def __init__(self, max_members: int = 120, ttl_seconds: int = 7 * 24 * 3600):
        self.max_members = max(8, int(max_members))
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._groups: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def observe(
        self,
        umo: Any,
        uid: Any,
        name: Any,
        is_bot: bool = False,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """记一条发言：同一 uid 更新昵称/计时；认出是机器人就永久标上。"""
        group_key = str(umo or "").strip()
        user_key = str(uid or "").strip()
        if not group_key or not user_key:
            return None
        stamp = time.time() if now is None else float(now)
        group = self._groups.setdefault(group_key, {})
        row = group.get(user_key)
        if row is None:
            row = {
                "uid": user_key,
                "name": "",
                "is_bot": False,
                "first_seen": stamp,
                "last_seen": stamp,
                "count": 0,
            }
            group[user_key] = row
        cleaned = normalize_name(name)
        if cleaned:
            row["name"] = cleaned
        row["is_bot"] = bool(row["is_bot"] or is_bot)
        row["last_seen"] = stamp
        row["count"] = int(row.get("count", 0)) + 1
        self._trim(group, stamp)
        return row

    def get(self, umo: Any, uid: Any) -> Optional[Dict[str, Any]]:
        return self._groups.get(str(umo or ""), {}).get(str(uid or ""))

    def members(self, umo: Any) -> List[Dict[str, Any]]:
        return list(self._groups.get(str(umo or ""), {}).values())

    def is_known_bot(self, umo: Any, uid: Any) -> bool:
        row = self.get(umo, uid)
        return bool(row and row.get("is_bot"))

    def display_name(self, umo: Any, uid: Any, fallback: str = "") -> str:
        row = self.get(umo, uid)
        if row and row.get("name"):
            return str(row["name"])
        return fallback

    def names(self, umo: Any, include_bots: bool = True, limit: int = 0) -> List[str]:
        rows = [row for row in self._sorted_rows(umo) if include_bots or not row.get("is_bot")]
        values = [str(row["name"]) for row in rows if row.get("name")]
        if limit > 0:
            values = values[:limit]
        return values

    def describe(self, umo: Any, limit: int = 12) -> str:
        """名册转成一行字：墨辞烟、小冉（机器人）、阿澈。"""
        rows = [row for row in self._sorted_rows(umo) if row.get("name")][: max(1, int(limit))]
        parts = []
        for row in rows:
            name = str(row["name"])
            parts.append(name + "（机器人）" if row.get("is_bot") else name)
        return "、".join(parts)

    def _sorted_rows(self, umo: Any) -> List[Dict[str, Any]]:
        """按最后发言倒序的成员列表：认人时优先最近说过话的。"""
        return sorted(self.members(umo), key=lambda item: -float(item.get("last_seen") or 0))

    def bots(self, umo: Any) -> Set[str]:
        return {str(row["uid"]) for row in self.members(umo) if row.get("is_bot")}

    def mentioned_member(
        self,
        umo: Any,
        text: Any,
        exclude: Sequence[str] = (),
        exclude_uids: Sequence[str] = (),
    ) -> Optional[Dict[str, Any]]:
        """消息里是否点名了名册里的某个群友（长名字优先）。"""
        compact = normalize_name(text)
        if not compact:
            return None
        skip_names = {normalize_name(item) for item in exclude if normalize_name(item)}
        skip_uids = {str(item).strip() for item in exclude_uids if str(item).strip()}
        rows = [row for row in self.members(umo) if row.get("name")]
        rows.sort(key=lambda item: -len(str(item.get("name") or "")))
        for row in rows:
            name = normalize_name(row.get("name"))
            if len(name) < 2 or name in skip_names or str(row.get("uid")) in skip_uids:
                continue
            if name in compact:
                return row
        return None

    def clear(self, umo: Any = None) -> None:
        if umo is None:
            self._groups.clear()
        else:
            self._groups.pop(str(umo or ""), None)

    def _trim(self, group: Dict[str, Dict[str, Any]], now: float) -> None:
        """先清掉 7 天没说话的，再按人数上限留最近的。"""
        expired = [
            uid for uid, row in group.items()
            if now - float(row.get("last_seen") or 0) > self.ttl_seconds
        ]
        for uid in expired:
            group.pop(uid, None)
        if len(group) <= self.max_members:
            return
        ordered = sorted(group.values(), key=lambda item: float(item.get("last_seen") or 0))
        for row in ordered[: len(group) - self.max_members]:
            group.pop(str(row.get("uid")), None)


def addressing_verdict(
    *,
    target: str,
    mentioning_bot: bool,
    hard: bool,
    soft: bool,
    only_when_addressed: bool,
    forced: bool = False,
) -> Tuple[bool, str]:
    """她这次该不该开口（只判“是不是冲她说的”，不判内容）。

    返回 (allowed, reason)：

    - forced：睡眠/上班被连续打扰，强制回复优先；
    - addressed：模型判成对她说，或明确在叫她；
    - hard_other：硬证据显示在 @/回复别人，不接；
    - soft_continue：她在接她自己的话（她刚说过、这条是短问句）；
    - not_addressed：对全群/看不出对象，不搭话；
    - gate_off：关掉“只在被叫到时回复”，恢复原来的主动插话。
    """
    if forced:
        # 睡眠/上班被连续打扰：该回就回，闸不拦
        return True, "forced"
    if not only_when_addressed:
        return True, "gate_off"
    if mentioning_bot or str(target or "") == "bot":
        return True, "addressed"
    if hard:
        return False, "hard_other"
    if soft and str(target or "") not in ("other", "all"):
        # 她刚说完、这条像在接她的话；但模型已经判成“在对别人/对全群”时，软证据不推翻
        return True, "soft_continue"
    return False, "not_addressed"
