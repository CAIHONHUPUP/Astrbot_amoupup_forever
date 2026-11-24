# -*- coding: utf-8 -*-
"""动作描写（括号动作）的**唯一文案 + 唯一开关**（2026-11-24）。

背景：2026-11-22 的收口是「两套人格真源打架」——
基础人格卡说「不写动作旁白」，PromptRegistry 的后置规则却说「允许括号动作」，
线上 56.5% 的消息带旁白、45.4% 的分段只有动作。收口后所有文案统一成「不写」。

本轮加开关 persona_action_narration（默认关）时，文案不能又变成两套：
所以「写不写动作」这一句**只在这里出现一次**，主链规则 / 说话方式 / 约会块 /
短回复块 / 人格卡都从这里取。任一模式下，提示词里只有一种说法：

- 关：全都是「不写动作旁白、不写括号动作」；
- 开：全都是「可以带一句很短的括号动作」，没有一处还在禁止。

出站侧的硬约束不受开关影响（一行最多一个、16 字以内、整条只有动作不发、
残括号剥掉、语音不念），见 amo_persona_guard。
"""
from __future__ import annotations

from typing import Any

CONFIG_KEY = "persona_action_narration"

#: 主链规则（【规则】块）
RULE_FORBID = "不写动作旁白、不写括号动作（“（歪头）”“（笑了一下）”这类一律不写），也不要只用动作回应"
RULE_ALLOW = ("可以带一句很短的括号动作（十几个字以内，跟台词同一行，例如“（把盒饭推过去）”，"
              "一条消息最多一个），但不能只用动作回应，也不能让括号单独成段")

#: 说话方式块（【说话方式】块的收尾句）
STYLE_FORBID = "不写动作旁白。"
STYLE_ALLOW = "动作最多一句、跟在台词同一行。"

#: 约会块
DATE_FORBID = "不写动作旁白、不用括号动作：把陪伴感写进说出口的话里（撒娇、使唤、炫耀），刚碰面别一上来就太亲密。"
DATE_ALLOW = "动作只当点缀、不单独成段：把陪伴感写进说出口的话里（撒娇、使唤、炫耀），刚碰面别一上来就太亲密。"

#: 短回复块
SHORT_FORBID = "不写动作旁白、不写括号动作"
SHORT_ALLOW = "可以带一句很短的括号动作"

#: 人格卡
CARD_FORBID = "不写动作旁白、不写括号动作；只用说出口的话表达情绪（基础人格唯一真源）。"
CARD_ALLOW = ("可以带一句很短的括号动作（十几字以内、跟在台词后面）；"
              "情绪只用说出口的话表达（基础人格唯一真源）。")


def enabled(plugin: Any) -> bool:
    """读配置；读不到/读坏了都按「关」处理（默认行为零变化）。"""
    cfg = getattr(plugin, "cfg_bool", None)
    if not callable(cfg):
        return False
    try:
        return bool(cfg(CONFIG_KEY, False))
    except Exception:  # cleanup: 配置异常不能把回复打挂
        return False


def pick(forbid: str, allow: str, allow_action: bool) -> str:
    return allow if allow_action else forbid
