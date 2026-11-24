# -*- coding: utf-8 -*-
"""分享台账：她刚分享过什么，后续要接着聊（2026-11-21）。

只存最近一条，2 小时冷却；不写记忆库。
- 对象：猫/狗/视频/图片/其他
- 待回应：她分享后等用户反应；用户回应后清掉
"""
from __future__ import annotations

import logging

logger = logging.getLogger("amo.amo_share_ledger")

import re

try:
    from . import clock as _amo_clock
except ImportError:
    import clock as _amo_clock

_TTL = 2 * 3600
_ANIMALS = ("猫", "狗", "兔", "鸟", "仓鼠")

# 分享的「开场/续聊元信息」和场景括号都不是信息点本身：
#   「老公我刚刷到一个猫的视频，一只橘猫卡住了」->「一只橘猫卡住了」
#   「刚才那个兔还有后续，它听见零食袋的声音」->「它听见零食袋的声音」
#   「（在面馆等面）」这种场景括号直接摘掉。
_PAREN = re.compile(r"[（(][^）)]*[）)]")
_META = re.compile(
    r"^(?:老公[，,]?\s*)?(?:我)?(?:"
    r"刚才那个[^，。！？\n]{1,12}?(?:还有后续|的事|的视频|的图)[，,]?"
    r"|(?:刚刷到|刚刚刷到|刚看到|刷到)(?:一个|一件事|一件|个)?"
    r"[^，。！？\n]{0,16}?(?:视频|图片|照片|图|事)[，,]?"
    r"|你快看这个(?:视频|图片|图)[，,]?"
    r"|快看这个(?:视频|图片|图)[，,]?"
    r"|给你看(?:这个|一个|个)?[^，。！？\n]{0,10}?(?:视频|图片|照片|图|事)[，,]?"
    r"|刚看了个[^，。！？\n]{1,12}?[，,]"
    r"|刚听说了个[^，。！？\n]{1,12}?[，,]"
    r")")
#: 回复里不能出现的「让她叫他自己看」类短语，接话时从信息点里剔掉，避免二次违规。
_ROLE_WORDS = ("你发的", "你那个视频", "你那个图", "你再发", "发我看看", "你翻上去",
               "你自己看", "你再看一遍", "给你看看", "给你看", "快看", "刚刷到")


def last_info(text):
    """分享的最后一个信息点：她真正在说的那句描述（不含开场/续聊/场景括号）。

    台账入账时记一份，聊天守卫接话时也用它，保证「分享的最后一个信息点」
    在两个环节里是同一个定义，不按样例各写一套。
    """
    body = _PAREN.sub("", str(text or "")).strip()
    # 「老公，楼下奶茶店排队…」这类事情分享没有视频/图等开场，先摘掉称呼。
    body = re.sub(r"^(?:老公|亲爱的)[，,]?\s*", "", body)
    body = _META.sub("", body).strip()
    body = re.sub(r"^[，,；;、\s]+", "", body)
    parts = [part for part in re.split(r"(?<=[。！？!?\n])", body) if part.strip()]
    if parts:
        body = parts[-1].strip() if len(parts) > 1 else body.strip()
    # 取最后一个信息点，但别让结尾的重复开场/角色反转把信息点带歪。
    clauses = [clause for clause in re.split(r"(?<=[，,；;])", body) if clause.strip()]
    clauses = [clause for clause in clauses
               if not any(word in clause for word in _ROLE_WORDS)]
    if clauses:
        body = "".join(clauses).strip()
    body = re.sub(r"[，,；;]\s*$", "", body).strip()
    return body[-48:] if len(body) > 48 else body


def _kv(plugin):
    try:
        return plugin.db
    except Exception:  # cleanup: 没有 db 时按无台账处理
        return None


def _topic(text):
    body = str(text or "")
    for animal in _ANIMALS:
        if animal in body:
            return animal
    if any(word in body for word in ("视频", "刷到", "快看", "给你看")):
        return "视频"
    if "图片" in body or "照片" in body:
        return "图片"
    return "事"


def note(plugin, text, media=False, now=None):
    db = _kv(plugin)
    if db is None:
        return {}
    body = str(text or "").strip()
    if not body:
        return {}
    entry = {"topic": _topic(body), "text": body[:60], "info": last_info(body),
             "at": float(now or _amo_clock.now_ts()),
             "media": bool(media), "pending": True}
    try:
        db.kv_set("share_ledger", entry)
    except Exception:  # cleanup: 台账写失败不影响回复
        return {}
    return entry


def last(plugin, now=None):
    db = _kv(plugin)
    if db is None:
        return {}
    try:
        entry = db.kv_get("share_ledger", {}) or {}
    except Exception:  # cleanup: 台账读失败按无台账处理
        return {}
    if not isinstance(entry, dict):
        return {}
    if float(entry.get("at") or 0) + _TTL < float(now or _amo_clock.now_ts()):
        return {}
    return entry


def recent_info(plugin, now=None):
    """最近一条分享的最后一个信息点。

    聊天守卫用它接住「然后呢/什么视频/在哪」这类话：注意这里只看 2 小时 TTL，
    不看 pending——pending 在「他刚回应」那一刻就被 mark_reacted 清掉了，
    但同一件事往往还要再聊一两句，信息点仍然要保留。
    """
    entry = last(plugin, now)
    if not entry:
        return ""
    info = str(entry.get("info") or "").strip()
    if info:
        return info
    return last_info(entry.get("text"))


def mark_reacted(plugin):
    db = _kv(plugin)
    if db is None:
        return
    try:
        entry = db.kv_get("share_ledger", {}) or {}
        if isinstance(entry, dict) and entry:
            entry["pending"] = False
            db.kv_set("share_ledger", entry)
    except Exception:  # cleanup: 标记已回应失败不影响回复
        return


def prompt_line(plugin, now=None):
    entry = last(plugin, now)
    if not entry or not entry.get("pending"):
        return ""
    line = ("【你刚分享过】%s（%s）；他刚回应你。先接住他这句，再接着聊同一件事，"
            "不要让他去看你已经发过的东西，不要换对象；同一件事 2 小时内不要再说“刚刷到/快看”。"
            % (entry.get("text") or entry.get("topic"), "视频" if entry.get("media") else "内容"))
    try:
        act = plugin.state.current_activity() or {}
        doing = str(act.get("activity") or "")
        place = str(act.get("place") or "")
        if any(word in (doing + place) for word in ("吃面", "吃饭")):
            line += "你现在正在吃面/吃饭：如果这条视频你已经看过，可以说“吃面差点喷出来”；否则不要提食物。"
        else:
            line += "你现在没有在吃东西：不要提吃面/吃饭，不要编食物。"
    except Exception as exc:  # cleanup: 读不到状态时不加吃面提示
        logger.debug("[amo_share_ledger.prompt_line] optional step failed: %s", exc)
    return line
