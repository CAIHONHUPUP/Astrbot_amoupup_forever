# -*- coding: utf-8 -*-
"""私聊亲密尺度 / 夸和撩 / 先贬后夸 / 露骨内容识别（纯函数，可单测）。

2026-11-23 用户要求：
- 「睡衣丑但是穿在你身上好看」是先贬后夸，不许当成换睡衣请求；
- 私聊要接住夸和撩，不能拿「换衣服/催睡觉」打发；
- intimacy_level 0 含蓄 / 1 调情 / 2 露骨，2 档单独开关、不自动升；
- 露骨对话不进日记和里程碑。
"""
from __future__ import annotations

import re

#: 贬低/挑毛病
CRITICISM_WORDS = (
    "不好看", "难看", "丑", "土气", "老气", "俗气", "显胖", "不搭", "不合适",
)
#: 夸奖
PRAISE_WORDS = (
    "好看", "漂亮", "可爱", "美", "显白", "适合你", "衬你", "性感", "迷人",
    "有气质", "优雅", "心动", "喜欢", "爱你", "想你",
)
#: 先贬后夸的连接词/句式
CONTRAST_WORDS = (
    "但是", "但", "可是", "不过", "就是", "却", "然而", "倒",
    "穿在你身上", "你穿", "你穿着",
)
#: 撩/想念（不要求一定带好看）
FLIRT_WORDS = (
    "没吃到你", "睡不着", "失眠", "想你", "爱你", "喜欢你", "馋你",
    "亲你", "抱你", "贴贴", "撩", "勾人", "要命", "想见你", "想抱",
    "想亲", "心跳", "心动",
)
#: 明确的换装指令：即使先贬后夸，只要他明说换，就照换
EXPLICIT_CHANGE_WORDS = (
    "换一套", "换套", "换装", "换衣服", "换件", "换一下", "重新换", "再换",
)
#: 露骨内容（只拦强词，避免把「胸口闷/插队」误伤）
_EXPLICIT_RE = re.compile(
    r"(做爱|性爱|性交|上床|睡你|操你|干你|插进去|射了|高潮|裸体|脱光|"
    r"内裤|情趣内衣|吻遍|摸遍|舔你|舔我|硬了|湿了|要了我|身体给你)"
)


def _first_index(text, words):
    indexes = [text.find(word) for word in words if word and word in text]
    return min(indexes) if indexes else -1


def _mask(text, words):
    masked = str(text or "")
    for word in words:
        if word:
            masked = masked.replace(word, "□" * len(word))
    return masked


def is_contrast_praise(text):
    """先贬后夸：先挑毛病，再用「但/不过/穿在你身上」夸回来。"""
    body = str(text or "")
    if not body:
        return False
    crit = _first_index(body, CRITICISM_WORDS)
    if crit < 0:
        return False
    masked = _mask(body, CRITICISM_WORDS)
    praise = _first_index(masked, PRAISE_WORDS)
    if praise < 0 or praise <= crit:
        return False
    between = body[crit:praise]
    return any(word in between for word in CONTRAST_WORDS)


def has_explicit_change_request(text):
    body = str(text or "")
    return any(word in body for word in EXPLICIT_CHANGE_WORDS)


def is_compliment_or_flirt(text):
    """夸她/撩她：好看/漂亮，或「没吃到你/睡不着/想你」这类。"""
    body = str(text or "")
    if not body:
        return False
    if is_contrast_praise(body):
        return True
    masked = _mask(body, CRITICISM_WORDS)
    if any(word in masked for word in PRAISE_WORDS):
        return True
    return any(word in body for word in FLIRT_WORDS)


def is_explicit(text):
    """露骨成人向内容：只用于日记/里程碑过滤，不影响聊天本身。"""
    return bool(_EXPLICIT_RE.search(str(text or "")))


def resolve_intimacy_level(configured, explicit_enabled):
    """0/1/2 三档；2 档必须单独开关，不满足时回落 1，绝不自动升级。"""
    try:
        level = int(configured)
    except (TypeError, ValueError):
        level = 1
    if level not in (0, 1, 2):
        level = 1
    if level == 2 and not bool(explicit_enabled):
        return 1
    return level


_MEAL_WORDS = ("吃", "饭", "餐", "外卖", "宵夜", "夜宵")


def meal_missing_labels(items, now_min, facts=None):
    """只读判断今天哪顿饭已经过点但没有任何 meal 事实（不写日程）。

    facts 是食物送达写入的事实层条目（slot=lunch/dinner/snack）；
    日程 meal 块和 facts 任一存在，就算这顿有记录。
    """
    fact_slots = {str(item.get("slot") or "") for item in (facts or [])
                  if isinstance(item, dict)}
    labels = []
    for start, end, label, slot in ((12 * 60, 13 * 60, "午饭", "lunch"),
                                    (19 * 60, 20 * 60, "晚饭", "dinner")):
        if int(now_min) < end:
            continue
        covered = False
        for item in items or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("activity") or "")
            if str(item.get("category") or "") != "meal" and not any(
                    word in text for word in _MEAL_WORDS):
                continue
            try:
                begin = int(str(item.get("start") or "0:0").split(":")[0]) * 60 + int(
                    str(item.get("start") or "0:0").split(":")[1])
                finish = int(str(item.get("end") or "0:0").split(":")[0]) * 60 + int(
                    str(item.get("end") or "0:0").split(":")[1])
            except (TypeError, ValueError, IndexError):
                continue
            if begin < end and finish > start:
                covered = True
                break
        if not covered and slot not in fact_slots:
            labels.append(label)
    return labels


def intimacy_prompt(level):
    """私聊上下文里的亲密尺度提示；group 不调用这个函数。"""
    level = int(level)
    if level <= 0:
        return (
            "【亲密尺度·0 含蓄】可以亲近、撒娇、说想他，但不主动开黄腔、不写露骨身体细节；"
            "亲密场景最多 1-2 句，点到为止。"
        )
    if level == 2:
        return (
            "【亲密尺度·2 露骨】他明确要更亲密时可以更直接地回应，但仍要像她本人、不要变成色情模板；"
            "亲密场景可以写到 2-4 句，不要一句就打发。"
            "露骨内容只留在当下私聊，不许写进日记、里程碑、每日总结或长期记忆。"
        )
    return (
        "【亲密尺度·1 调情】可以暧昧、撩他、接住他的荤话，但不用露骨器官描写；"
        "亲密场景可以写到 2-3 句，别一句就打发，也别突然转成换衣服/催睡觉。"
    )
