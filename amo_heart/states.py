# -*- coding: utf-8 -*-
"""五档：日常 / 委屈 / 生气 / 害怕 / 回避，以及心结。"""
from __future__ import annotations

from .dims import clamp

WRONGED, ANGRY, AFRAID, SHUT = 25.0, 55.0, 70.0, 80.0
#: 重责一次最多推到生气上沿，不跳档。
HEAVY_CAP = 69.0
#: 没人哄时责怪每 6 小时褪 8 点，止于 40（心结）。
DECAY_PER_6H = 8.0
FLOOR = 40.0


def stage(blame):
    value = float(blame or 0)
    if value >= SHUT:
        return "shut"
    if value >= AFRAID:
        return "afraid"
    if value >= ANGRY:
        return "angry"
    if value >= WRONGED:
        return "wronged"
    return "calm"


def apply_blame(state, pressure, heavy=False):
    """加责怪压力；从日常/委屈被重责时最多停在 69。"""
    before = float(state.get("blame", 0))
    value = clamp(before + float(pressure), 0, 100)
    if heavy and before < ANGRY < value:
        value = HEAVY_CAP
    state["blame"] = value
    return state


def soothe(state, rounds=1, apology=False):
    """哄到位才降：生气档只认道歉/说清楚。"""
    if stage(state.get("blame", 0)) in ("angry", "afraid", "shut") and not apology:
        return state
    state["blame"] = clamp(float(state.get("blame", 0)) - 18.0 * int(rounds), 0, 100)
    return state


def decay_blame(state, minutes):
    """自然褪：每 6 小时 8 点，止于 40。"""
    hours = max(0.0, float(minutes)) / 60.0
    drop = DECAY_PER_6H * (hours / 6.0)
    state["blame"] = clamp(max(FLOOR, float(state.get("blame", 0)) - drop), 0, 100)
    return state


def mood_word(state):
    """给面板/注入用的一枚心情词（日常里再细分）。"""
    blame = float(state.get("blame", 0))
    if blame >= SHUT:
        return "回避"
    if blame >= AFRAID:
        return "害怕"
    if blame >= ANGRY:
        return "生气"
    if blame >= WRONGED:
        return "委屈"
    if float(state.get("fatigue", 0)) >= 70:
        return "累了"
    if float(state.get("attachment", 0)) >= 66 and float(state.get("intimacy", 0)) >= 66:
        return "黏人"
    if float(state.get("stress", 0)) >= 50:
        return "紧绷"
    if float(state.get("joy", 0)) >= 66:
        return "开心"
    if float(state.get("curiosity", 0)) >= 66:
        return "好奇"
    return "平常"


_PHRASE = {
    "回避": "不想说话，想一个人待着",
    "害怕": "有点怕，怕他真的不理你",
    "生气": "在生气，语气冷一点",
    "委屈": "委屈，想被哄",
    "累了": "累了，没什么力气",
    "黏人": "很黏他，想靠近",
    "紧绷": "心里绷着，放松不下来",
    "开心": "开心，心情不错",
    "好奇": "好奇，想听他说",
    "平常": "平静，没什么波动",
}
_LEGACY = {"累了": "疲惫", "黏人": "依恋", "紧绷": "焦虑", "好奇": "兴奋", "平常": "平静"}


def mood_phrase(state):
    """给提示词/面板的一句自然语言心情。"""
    return _PHRASE.get(mood_word(state), _PHRASE["平常"])


def legacy_word(state):
    """旧十维中文词：日程 JIT / 日记短情绪词仍按这套词表匹配。"""
    return _LEGACY.get(mood_word(state), mood_word(state))
