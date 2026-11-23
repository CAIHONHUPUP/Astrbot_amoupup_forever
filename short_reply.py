# -*- coding: utf-8 -*-
"""短回复人设（2026-11-14）：几个字也要像她，不像客服。

两件事：
- prompt_block()：把禁用词、语气规范、动作限制、标点潜台词、状态绑定、敷衍反击和 few-shot 拼进 System Prompt；
- state_binding()：按当前健康/心情给一条“短回复状态”约束；

纯函数，方便单测；不碰模型、不碰数据库。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import re

try:  # 提示词注册表（Phase 4）
    from . import amo_prompt_registry as _prompts
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_prompt_registry as _prompts

#: 正文仍由注册表唯一维护；这里保留旧名字给老调用方/测试。
RULES = _prompts.render("short.rules")

_CURT = {"嗯", "哦", "行", "好", "好的", "随便", "嗯嗯", "哦哦", "知道了", "行吧", "。。"}


def _mood(snap):
    try:
        return (snap or {}).get("mood") or {}
    except Exception:
        return {}


def _health(snap):
    try:
        return (snap or {}).get("health") or {}
    except Exception:
        return {}


def _num(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def state_binding(snap):
    """按健康/心情给短回复一条状态约束；没有明显状态返回空串。"""
    health = _health(snap)
    status = str(health.get("status") or "").strip()
    if status and status not in ("健康", "正常", ""):
        return _prompts.render("short.state.sick")
    mood = _mood(snap)
    if _num(mood.get("疲惫")) >= 60:
        return _prompts.render("short.state.tired")
    if _num(mood.get("生气")) >= 60 or _num(mood.get("暴躁")) >= 60:
        return _prompts.render("short.state.angry")
    if _num(mood.get("开心")) >= 60:
        return _prompts.render("short.state.happy")
    return ""


def is_curt(text):
    """他是不是只回了一个敷衍的短词（嗯/哦/行/随便/……）。"""
    raw = str(text or "")
    body = re.sub(r"[\s。.!！?？…~]+", "", raw)
    if body in _CURT:
        return True
    return bool(raw.strip()) and body == "" and any(ch in raw for ch in "。.!！?？…")


def prompt_block(snap=None, user_text=""):
    """System Prompt 的“聊天行为规范”短回复段。"""
    parts = [RULES]
    binding = state_binding(snap)
    if binding:
        parts.append(binding)
    if is_curt(user_text):
        parts.append(_prompts.render("short.curt"))
    return "\n".join(parts)


