# -*- coding: utf-8 -*-
"""收件人 / 角色反转唯一守卫（task-32，2026-11-22 11:31 飞书真实 P0）。

现场（messages #2288，日志 amo_message:986 「主动 ->」）：
    妈，我地铁上呢，中午回家吃，随便做点就行，我马上到。
收件人是老公，但首句称呼是「妈」，内容是老公对他妈报备的第一人称视角。
来源链路（已核实，不是 proactive_log、不是延迟队列、也不是旧发送函数）：
    watch 插件 social_tick
      -> services/social_ai.py respond 生成 now（回 NPC 的那句）
      -> social_engine._apply 把 result["line"] 放进 out["push"]
      -> core/jobs.py 调 amo.request_push(text, "social")
      -> amo_feature_api.request_push -> push_to_owner -> amo_message._send_to_umo
      -> [amo] 主动 -> ...（source=push）
    kv 证据：external_push_ts:social=1795318312.505433、mood_applied:social_event:妈妈...
    proactive_log 不记录外部 request_push，所以查不到；延迟回复是 4-18 分钟，也对不上。

本模块只干三件事，是出站最后一层兜底（生成层另有收件人锁 prompt）：
1. 首句称呼白名单：只允许老公 / 你 / 名字（profile 里的名字/昵称）；
   妈/爸/婆婆/阿姨/朋友/同事…… 以及名单外 NPC 名字都不许当收件人；
2. 命中禁称呼且句子里是「用户第一人称自述」（我马上到 / 我回家吃 / 我在地铁上……）
   => 判定角色反转，整条丢弃；
3. 不是角色反转时：私聊只把首句称呼改回老公/名字后继续发；群聊（umo 含
   ":GroupMessage:"，群里这句话不是对老公说的）只删掉首句称呼连同逗号，
   保留剩余句子，删空才丢弃，绝不改写成「老公」。

一条规则只在这里实现：persona_guard / outbound / message / state 都只调用本模块，
不许再各自写局部正则。所有判定不碰网络、不碰数据库，异常一律 fail-open（宁可少拦，
也不能把正常聊天拦死）。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("amo.addressee_guard")

#: 允许的首句称呼：老公 / 你 / profile 里的名字（别名、昵称）。
ALLOWED_ADDRESSES = frozenset({
    "老公", "你", "亲爱的", "宝贝", "宝宝", "对象", "男朋友", "喂",
})

#: 明确禁止当收件人的称呼：家人、亲戚、朋友、同事、领导等。
BANNED_ADDRESSES = frozenset({
    "妈", "妈妈", "老妈", "母亲",
    "爸", "爸爸", "老爸", "父亲",
    "婆婆", "公公", "岳父", "岳母", "丈母娘", "老丈人",
    "阿姨", "叔叔", "舅舅", "舅妈", "姑姑", "姑父", "爷爷", "奶奶",
    "外婆", "外公", "姥姥", "姥爷",
    "哥", "哥哥", "姐", "姐姐", "弟", "弟弟", "妹", "妹妹",
    "表哥", "表姐", "表妹", "表弟", "堂哥", "堂姐", "堂妹", "堂弟",
    "朋友", "朋友们", "同事", "同事们", "闺蜜", "死党", "同学", "校友",
    "老师", "领导", "老板", "客户", "甲方", "室友", "邻居", "兄弟", "姐妹",
})

# 首句称呼：行首（允许先有动作旁白）1-6 个字 + 中文/英文逗号。
_VOCATIVE = re.compile(r"^\s*([\u4e00-\u9fffA-Za-z]{1,6})\s*[，,]\s*")
_LEAD_PAREN = re.compile(r"^\s*(?:[（(][^（()）\n]{0,60}[)）]\s*)+")

#: 用户第一人称自述标记：这些是「老公对他妈/朋友说的话」，不是她该对老公说的。
_USER_VIEW = re.compile(
    r"我(?:在)?(?:地铁|公交|车)上"
    r"|我(?:马上|这就|就|已经|现在|刚|快)(?:到|到了|到啦|回家|回去|下班|出门)"
    r"|我在路上了|我快到了|我到了"
    r"|我(?:中午|晚上|今天|明天|等下|晚点)?回(?:家|妈|爸)(?:吃|去|一趟)?"
    r"|我(?:下班|出门|去上班|在上班|在公司|加班|洗漱|起床|睡|做饭|吃饭)"
    r"|随便做(?:点|个)|别等我|不用等我|给我留(?:点|着)?"
)

_REWRITE_DEFAULT = "老公"

#: 第二人称敬称。出现「您/您们」说明这句话仍然是在对原来的收件人（长辈/婆婆）说；
#: 只把首句称呼改成「老公」会拼出「老公，我周末过去看您」这种称谓/敬语不匹配的句子
#: （task-41 holdout A9）。这类不是「只错称呼」，按角色反转整条丢弃。
_HONORIFIC = re.compile(r"您")

#: 群聊 umo 标记（如 aiocqhttp:GroupMessage:123）：群里的首句称呼不是对老公说的。
_GROUP_UMO_MARK = ":GroupMessage:"
#: 群聊 @ 点名：@柳如烟，/ @柳如烟<空格>。只在群聊分支启用，私聊行为不变。
_VOCATIVE_AT = re.compile(r"^\s*@\s*([\u4e00-\u9fffA-Za-z]{1,6})(?:\s*[，,]\s*|\s+)")


def is_group_chat(umo):
    """umo 是否群聊（含 ":GroupMessage:"）；空/异常一律当私聊。"""
    try:
        return _GROUP_UMO_MARK in str(umo or "")
    except Exception:
        return False


def _vocative_patterns(allow_at=False):
    return (_VOCATIVE_AT, _VOCATIVE) if allow_at else (_VOCATIVE,)


def _first_vocative(text, allow_at=False):
    """返回首句称呼 match（跳过行首动作旁白）；没有称呼返回 None。"""
    body = str(text or "")
    patterns = _vocative_patterns(allow_at)
    for pattern in patterns:
        match = pattern.match(body)
        if match:
            return match
    stripped = _LEAD_PAREN.sub("", body, count=1)
    if stripped != body:
        for pattern in patterns:
            match = pattern.match(stripped)
            if match:
                return match
    return None


def _vocative_lead(body, allow_at=False):
    """首句称呼前可保留的动作旁白长度（直接命中返回 0）。"""
    for pattern in _vocative_patterns(allow_at):
        if pattern.match(body):
            return 0
    stripped = _LEAD_PAREN.sub("", body, count=1)
    if stripped != body:
        for pattern in _vocative_patterns(allow_at):
            if pattern.match(stripped):
                return len(body) - len(stripped)
    return 0


def strip_vocative(text, allow_at=False):
    """删掉首句称呼连同其后的逗号，保留剩余句子与行首动作旁白。"""
    body = str(text or "")
    match = _first_vocative(body, allow_at=allow_at)
    if match is None:
        return body
    lead = _vocative_lead(body, allow_at=allow_at)
    return body[:lead] + body[lead + match.end():]


def _split_owner_names(value):
    """把 profile 的 name/alias/remark 拆成称呼候选（只留 1-4 个字的短词）。"""
    out = set()
    for part in re.split(r"[\s；;、，,/|]+", str(value or "")):
        part = part.strip("。.!！?？")
        if 1 <= len(part) <= 4 and not part.isdigit():
            out.add(part)
    return out


def owner_names_from_profile(profile):
    """从 profiles 行取老公的称呼（name / alias）。"""
    names: set[str] = set()
    if not isinstance(profile, dict):
        return names
    names.update(_split_owner_names(profile.get("alias") or ""))
    name = str(profile.get("name") or "").strip()
    if name and name not in ("family", "owner", "admin"):
        names.update(_split_owner_names(name))
    names.discard("")
    return names


def owner_names(plugin=None, umo="", profile=None):
    """取当前私聊对象的称呼；拿不到就空集合（仍允许老公/你）。"""
    names: set[str] = set()
    if isinstance(profile, dict):
        names |= owner_names_from_profile(profile)
    if not names and plugin is not None:
        try:
            rows = list(plugin.db.proactive_profiles(limit=20) or [])
        except Exception:
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            private_umo = str(row.get("private_umo") or "")
            if umo and private_umo and private_umo != str(umo):
                continue
            names |= owner_names_from_profile(row)
            if names:
                break
    return names


def other_names():
    """名单里的人物（柳如烟/白凝冰/…）：他们只能是话题，不能当收件人。"""
    try:
        try:
            from .amo_names import NAME_SET
        except ImportError:
            from amo_names import NAME_SET
        return set(NAME_SET)
    except Exception:
        return set()


def is_banned_address(word, owner=(), others=()):
    """这个首句称呼是否不允许（不是老公/你/名字）。"""
    word = str(word or "").strip()
    if not word:
        return False
    if word in ALLOWED_ADDRESSES or word in set(owner or ()):
        return False
    if word in BANNED_ADDRESSES or word in set(others or ()):
        return True
    return False


def is_user_perspective(text):
    """命中「妈，我……」这类用户第一人称自述（他对他妈/朋友报备）。"""
    return bool(_USER_VIEW.search(str(text or "")))


def rewrite_vocative(text, addr="老公"):
    """只改首句称呼，其余原样（非角色反转时用）。"""
    body = str(text or "")
    match = _first_vocative(body)
    if match is None:
        return body
    start, end = match.span(1)
    # 动作旁白保留：只替换称呼那一段。
    body_match = _VOCATIVE.match(body)
    if body_match is None:
        # 称呼在动作旁白后面：定位到动作旁白之后。
        stripped = _LEAD_PAREN.sub("", body, count=1)
        lead = body[: len(body) - len(stripped)]
        return lead + stripped[: start] + str(addr) + stripped[end:]
    return body[: start] + str(addr) + body[end:]


def apply(text, owner=(), others=(), rewrite_addr="", group=False):
    """纯函数守卫：返回 (text, reason)。

    reason 为空 = 放行；返回空文本 + reason = 丢弃；返回新文本 + reason = 已改写。
    reason 里带称呼词，便于日志/outbound_log 记录来源与原因。

    group=False 是私聊旧行为：禁称呼改写为老公；group=True 是群聊：只删首句称呼，
    绝不改写成「老公」，删完为空才丢弃。角色反转/敬语两条判定在两种模式下都先执行。
    """
    body = str(text or "").strip()
    if not body:
        return body, ""
    try:
        match = _first_vocative(body, allow_at=bool(group))
        if match is None:
            return body, ""
        word = str(match.group(1) or "").strip()
        if not is_banned_address(word, owner, others):
            return body, ""
        if is_user_perspective(body):
            return "", "addressee_role_reversal:%s" % word
        if _HONORIFIC.search(body):
            # 改写必须连敬语一起自洽；「您/您们」说明收件人本来就是长辈，改称呼不成立。
            # 这里选择整条丢弃（task-41：改写要连「您」一起改或直接拒）。
            return "", "addressee_honorific:%s" % word
        if group:
            # 群聊里「朋友们，晚安」是对全群说的，不能串成「老公，晚安」；删掉称呼即可。
            fixed = strip_vocative(body, allow_at=True).strip()
            reason = "addressee_group_vocative:%s" % word
            if not fixed:
                return "", reason
            return fixed, reason
        target = str(rewrite_addr or "").strip()
        if not target:
            owners = set(owner or ())
            if "老公" in owners:
                target = "老公"
            else:
                target = next(iter(sorted(owners)), "") or _REWRITE_DEFAULT
        fixed = rewrite_vocative(body, target)
        if not fixed or fixed == body:
            return "", "addressee_banned:%s" % word
        return fixed, "addressee_rewrite:%s->%s" % (word, target)
    except Exception:
        return body, ""


def enforce(plugin, umo, text, source="", profile=None, rewrite_addr=""):
    """带日志/来源的出站守卫（fail-open）：返回 (text, reason)。"""
    body, reason = apply(
        text,
        owner=owner_names(plugin, umo, profile),
        others=other_names(),
        rewrite_addr=rewrite_addr,
        group=is_group_chat(umo),
    )
    if reason:
        try:
            action = "drop" if not body else "rewrite"
            logger.info(
                "[ADDRESSEE] action=%s source=%s umo=%s reason=%s text=%s",
                action, str(source or "-"), str(umo or "-"), reason, str(text or "")[:120],
            )
        except Exception:  # cleanup: 兜底/降级，保持原行为
            pass
    return body, reason


__all__ = [
    "ALLOWED_ADDRESSES", "BANNED_ADDRESSES", "owner_names_from_profile",
    "owner_names", "other_names", "is_banned_address", "is_user_perspective",
    "is_group_chat", "strip_vocative", "rewrite_vocative", "apply", "enforce",
]
