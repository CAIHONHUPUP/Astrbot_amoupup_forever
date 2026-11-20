# -*- coding: utf-8 -*-
"""口头禅去逐字复读守卫（2026-11-17）：保守档。

人设里的口头禅是「语感」，不是「台词」。模型常把口头禅原句背出来，连着两条
一个开头，很出戏。本模块只做两件确定性的事：

1. 提示词侧：PROMPT_RULE 是一段固定的【口头禅】规则（不点具体口头禅、不读配置
   键），由 amo_llm 在拼系统提示词时注入，告诉模型「口头禅 5 轮最多 1 次、
   连续两条别同开头」。
2. 发送侧：记住每个会话最近 5 条回复的句首短语（3-8 字），本轮回复如果和最近
   两条同一个开头，或同一个 3-8 字片段在最近五条里出现过两次以上，就把这个开头
   片段去掉；只去掉片段本身，后面的句子一字不动。

保守边界（默认档）：
- 只动句首/独立短语：从不改句子中间的字，也不重写事实句、情绪句；
- 句首片段里一旦带数字/时间/地点/价格或情绪词，就当作「不能删的内容」，宁可
  不动也不去改口；
- 记忆按会话隔离，进程内保存 5 条；可选 kv 持久化（use_kv=True 才读写），
  默认不落库，进程重启后自然清空；
- 任何异常都不许拦住发送：调用方包 try/except，本模块自身也尽量返回原文。

对外：
    guard(text, plugin=None, umo="", record=True, use_kv=False) -> (text, actions)
    remember(text, plugin=None, umo="", use_kv=False)         -> None
    recent_openings(umo, plugin=None, use_kv=False)            -> list
    reset(umo=None, plugin=None, use_kv=False)                 -> None
    prompt_rule() -> str
"""
from __future__ import annotations

import logging
import re

try:  # 包模式优先，直接按文件加载时退回绝对导入
    from .amo_shared import logger
except Exception:  # 单测/独立加载环境
    try:
        from amo_shared import logger
    except Exception:  # pragma: no cover - 兜底
        logger = logging.getLogger("amo")

__all__ = ["PROMPT_RULE", "guard", "remember", "recent_openings", "reset", "prompt_rule"]

#: 提示词侧规则：只说「语感不是台词」，不点名任何具体口头禅，不读配置键
PROMPT_RULE = (
    "【口头禅】口头禅是语感不是台词：同一句口头禅/固定开场白，5 轮里最多出现 1 次；"
    "连续两条回复不要用同一个开头；想表达同一种语气就换个说法，不要把口头禅原句背出来。"
)

#: 最近多少条回复算「最近五轮」
HISTORY_SIZE = 5
#: 连续同开头比较最近几条
CONSECUTIVE_WINDOW = 2
#: 可去片段字数范围
MIN_PHRASE = 3
MAX_PHRASE = 8
#: 可选 kv 的前缀（只有 use_kv=True 才读写）
KV_PREFIX = "tone_guard:"

_SENTENCE_SPLIT = re.compile(r"[。！？!?…~\n]+")
_SENTENCE_KEEP = re.compile(r"(?<=[。！？!?…~])")
_CLAUSE_SPLIT = re.compile(r"[，,、；;：:]")
_PUNCT_CHARS = "，,、。！？!?…~；;：:\"'“”‘’()（）【】[] \t\r\n"

#: 句首片段里出现这些词，说明是事实信息，不许当口头禅删掉
FACT_TOKENS = (
    "今天", "明天", "昨天", "前天", "刚才", "现在", "早上", "上午", "中午",
    "下午", "晚上", "夜里", "点", "号", "月", "日", "星期", "周", "块", "元",
    "钱", "买", "吃", "上班", "下班", "公司", "学校", "医院", "路上", "到家",
    "睡", "岁", "分钟", "小时", "公里",
)
#: 句首片段里出现这些词，说明是情绪句，宁可不动
EMOTION_TOKENS = (
    "开心", "高兴", "快乐", "难过", "难受", "委屈", "生气", "害怕", "担心",
    "心疼", "感动", "崩溃", "舒服", "累", "烦", "哭", "笑", "爱", "想", "气",
    "怕", "慌", "孤独", "幸福", "踏实", "安心", "舍不得", "惊喜", "失望",
    "无语", "郁闷", "紧张", "兴奋", "放心", "哈", "呵", "嘿",
)
_PROTECTED = FACT_TOKENS + EMOTION_TOKENS

#: 进程内记忆：umo -> 最近 HISTORY_SIZE 条 [{"open","first","text"}]
_MEMORY: dict = {}


def _short(text, limit=40):
    body = str(text or "")
    return body if len(body) <= limit else body[:limit] + "…"


def _common_prefix(left, right):
    size = 0
    for one, two in zip(str(left or ""), str(right or "")):
        if one != two:
            break
        size += 1
    return str(left or "")[:size]


def _is_strippable(phrase):
    """这个片段能不能当口头禅去掉：3-8 字、无标点数字、不带事实/情绪词。"""
    text = str(phrase or "").strip()
    if not (MIN_PHRASE <= len(text) <= MAX_PHRASE):
        return False
    if any(char in _PUNCT_CHARS for char in text):
        return False
    if re.search(r"\d", text):
        return False
    if not re.search(r"[\u4e00-\u9fff]", text):
        return False
    for token in _PROTECTED:
        if token in text:
            return False
    return True


def _opening_of(text):
    """回复的句首：第一句的第一个逗号前短语；取不到就用第一句前 8 字。"""
    body = str(text or "").lstrip()
    if not body:
        return "", ""
    match = _SENTENCE_SPLIT.search(body)
    first = body[:match.start()].strip() if match else body.strip()
    clause = _CLAUSE_SPLIT.split(first, 1)[0].strip()
    opening = clause or first[:MAX_PHRASE]
    return opening, first


def _count_in(history, candidate):
    """这个片段在最近五条里出现过几次：句首算，句子里重复用过的也算。"""
    total = 0
    for row in history or ():
        opening = str(row.get("open") or "")
        body = str(row.get("text") or "")
        if opening.startswith(candidate) or candidate in body:
            total += 1
    return total


def _history(umo, plugin=None, use_kv=False):
    rows = _MEMORY.get(umo)
    if rows is None:
        rows = _kv_load(umo, plugin) if use_kv else []
        _MEMORY[umo] = rows
    return rows


def _kv_load(umo, plugin=None):
    getter = getattr(getattr(plugin, "db", None), "kv_get", None)
    if not callable(getter):
        return []
    try:
        data = getter(KV_PREFIX + str(umo or ""), None)
    except Exception as exc:
        logger.debug("[tone_guard] 读取 kv 失败: %s", exc)
        return []
    if not isinstance(data, list):
        return []
    rows = [dict(item) for item in data if isinstance(item, dict) and item.get("open")]
    return rows[-HISTORY_SIZE:]


def _kv_save(umo, plugin=None, rows=None):
    setter = getattr(getattr(plugin, "db", None), "kv_set", None)
    if not callable(setter):
        return
    try:
        setter(KV_PREFIX + str(umo or ""), [dict(item) for item in (rows or [])])
    except Exception as exc:
        logger.debug("[tone_guard] 写入 kv 失败: %s", exc)


def _remember(umo, text, plugin=None, use_kv=False):
    body = str(text or "").strip()
    if not body:
        return
    opening, first = _opening_of(body)
    entry = {"open": opening[:MAX_PHRASE], "first": first[:60], "text": _short(body, 80)}
    rows = _MEMORY.setdefault(umo, [])
    rows.append(entry)
    if len(rows) > HISTORY_SIZE:
        del rows[:-HISTORY_SIZE]
    if use_kv:
        _kv_save(umo, plugin, rows)


def remember(text, plugin=None, umo="", use_kv=False):
    """记一条已经发出去的回复（只记句首，不落原文全文）。"""
    _remember(str(umo or ""), text, plugin, use_kv)


def _strip_opening(text, phrase):
    """去掉句首片段（连带紧跟的一个逗号/空格）；去掉后没内容就返回空串。"""
    body = str(text or "").lstrip()
    if not body.startswith(phrase):
        return ""
    rest = body[len(phrase):].lstrip()
    rest = rest.lstrip("，,、；;：:。！？!?…~ ")
    if not rest or not re.search(r"[\w\u4e00-\u9fff]", rest):
        return ""
    return rest


def _drop_standalone(text, phrase):
    """整句就是一个口头禅（独立短语）且后面还有句子：删掉这一句。"""
    parts = [item for item in _SENTENCE_KEEP.split(str(text or "").strip()) if item.strip()]
    if len(parts) < 2:
        return ""
    head = parts[0].strip().rstrip("。！？!?…~")
    if head != phrase:
        return ""
    rest = "".join(parts[1:]).strip()
    return rest


def guard(text, plugin=None, umo="", record=True, use_kv=False):
    """发送前确定性去重：返回 (改写后文本, actions)；actions 为空表示没动。"""
    value = str(text or "")
    if not value.strip():
        return value, []
    key = str(umo or "")
    history = _history(key, plugin, use_kv)
    opening, _first = _opening_of(value)
    phrase, reason = "", ""
    if len(opening) >= MIN_PHRASE:
        for row in reversed(history[-CONSECUTIVE_WINDOW:]):
            shared = _common_prefix(opening, str(row.get("open") or ""))
            if MIN_PHRASE <= len(shared) <= MAX_PHRASE and _is_strippable(shared):
                phrase, reason = shared, "consecutive"
                break
        if not phrase:
            for size in range(min(MAX_PHRASE, len(opening)), MIN_PHRASE - 1, -1):
                candidate = opening[:size]
                if not _is_strippable(candidate):
                    continue
                if _count_in(history, candidate) >= 2:
                    phrase, reason = candidate, "repeat"
                    break
    fixed = value
    actions = []
    if phrase:
        stripped = ""
        action = "strip"
        if str(_first).strip() == phrase:
            stripped = _drop_standalone(value, phrase)
            action = "drop"
        if not stripped:
            stripped = _strip_opening(value, phrase)
            action = "strip"
        if stripped and stripped != value:
            fixed = stripped
            actions.append({"action": action, "phrase": phrase, "reason": reason,
                            "before": _short(value), "after": _short(stripped)})
            logger.debug("[tone_guard] 去口头禅 action=%s phrase=%s reason=%s: %s -> %s",
                         action, phrase, reason, _short(value), _short(stripped))
        else:
            logger.debug("[tone_guard] 句首片段 %s 无法安全去掉，保持原样", phrase)
    if record:
        _remember(key, fixed, plugin, use_kv)
    return fixed, actions


def recent_openings(umo="", plugin=None, use_kv=False):
    """诊断用：这个会话最近五条的句首短语（旧 -> 新）。"""
    rows = _history(str(umo or ""), plugin, use_kv)
    return [str(row.get("open") or "") for row in rows]


def reset(umo=None, plugin=None, use_kv=False):
    """清空记忆；umo 为空时清全部。测试与「重启」语义都靠它。"""
    if umo is None:
        _MEMORY.clear()
        return
    key = str(umo)
    _MEMORY.pop(key, None)
    if use_kv:
        _kv_save(key, plugin, [])


def prompt_rule():
    """提示词侧的【口头禅】规则（固定文案，不读任何配置键）。"""
    return PROMPT_RULE
