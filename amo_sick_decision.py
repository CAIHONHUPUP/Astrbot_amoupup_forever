# -*- coding: utf-8 -*-
"""生病工作日早上的自主决策：LLM 二选一，结果落库后统一喂给 JIT 和骨架。"""
from __future__ import annotations

import json
import re

DAILY_WAGE_CENTS = 30000       # 约 300 元/天
ATTENDANCE_BONUS_CENTS = 20000  # 全勤奖约 200 元

SICK_SYSTEM = (
    "你是角色的决策器。只输出一个 JSON 对象，不要解释，不要 markdown。\n"
    "可用工具：\n"
    "1) take_sick_leave(reason)：今天请假。扣当天工资约 300 元，并失去全勤奖约 200 元；"
    "可以在家喝白粥、吃药休息。\n"
    "2) go_to_work_sick(reason)：带病上班。今天照常挤地铁去公司，工位上会很难受。\n"
    "必须二选一，输出格式：{\"tool\":\"take_sick_leave\",\"reason\":\"...\"} "
    "或 {\"tool\":\"go_to_work_sick\",\"reason\":\"...\"}。"
)

ACTIONS = ("take_sick_leave", "go_to_work_sick")


def _safety_line():
    """防线二：生病决策也是「生成人设内容」，必须带情绪安全句。"""
    try:
        from .amo_immersion_guard import SAFETY_LINE
    except ImportError:
        from amo_immersion_guard import SAFETY_LINE
    return SAFETY_LINE


def build_prompt(status, balance_cents, weekday_label="星期一", reason="发烧"):
    balance = int(balance_cents or 0) / 100.0
    return (
        "【绝对事实】\n"
        "今天是%s，工作日。你现在%s，很难受。\n"
        "如果请假：扣今天工资约 300 元，并失去全勤奖约 200 元；"
        "你当前总余额是 %.2f 元；不上班可以在家休息。\n"
        "如果去上班：要挤地铁，会很累，到了公司也只能昏昏沉沉撑着。\n"
        "请按你自己的性格和经济压力选一个，只输出 JSON。\n"
        "（病情备注：%s）\n"
        "%s"
    ) % (weekday_label, status or "生病", balance, reason or "不舒服", _safety_line())


def _extract_json(raw):
    text = str(raw or "").strip()
    text = re.sub(r"^\x60\x60\x60(?:json)?\s*", "", text)
    text = re.sub(r"\s*\x60\x60\x60$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def parse_decision(raw):
    data = _extract_json(raw)
    if not isinstance(data, dict):
        return None
    tool = str(data.get("tool") or data.get("action") or "").strip()
    if tool not in ACTIONS:
        return None
    return {"action": tool, "reason": str(data.get("reason") or "").strip()[:120]}
