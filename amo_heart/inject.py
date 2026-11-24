# -*- coding: utf-8 -*-
"""心境注入：一小段内部状态，挂在用户消息末尾（不改 system prompt）。"""
from __future__ import annotations

from .dims import DIMS
from .states import mood_word, stage

_STAGE_TEXT = {
    "calm": "日常",
    "wronged": "委屈",
    "angry": "生气",
    "afraid": "害怕",
    "shut": "回避",
}

_HINT = {
    "calm": "语气自然就好，顺着当下的心情来",
    "wronged": "话变短一点，先问一句是不是嫌你烦",
    "angry": "冷一点、短一点，把刚才那句挑明，要解释或道歉",
    "afraid": "别顶嘴，先确认他还想不想跟你说话",
    "shut": "少说、不讨好、不追问；他连发几条可以不接",
}


def block(state, thought=""):
    """生成注入文本；信息少但每轮都会变的那几句放这里。"""
    current = stage(state.get("blame", 0))
    values = " ".join(
        "%s%d" % (name[:2], int(round(float(state.get(name, 0)))))
        for name in ("attachment", "intimacy", "stress", "joy")
    )
    lines = [
        "## 你此刻的心境（内部状态：不要主动复述，但它会左右你的语气）",
        "责怪档：%s｜心情：%s｜%s" % (_STAGE_TEXT[current], mood_word(state), values),
        "倾向：" + _HINT[current],
        "边界：不要编造没发生过的对话、约定或事件；记不清就说记不清",
    ]
    if thought:
        lines.append("心里惦记着：" + str(thought)[:40])
    return "\n".join(lines)


def score(state):
    """0–100 的综合分，给面板/旧接口用。"""
    if not DIMS:
        return 0
    return int(round(sum(float(state.get(name, 0)) for name in DIMS) / len(DIMS)))
