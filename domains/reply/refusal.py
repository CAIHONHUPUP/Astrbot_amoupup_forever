# -*- coding: utf-8 -*-
"""拒绝权（2026-11-14）：她不是客服，有底线、身体和情绪。

四类基于事实的拒绝：
- 财务：存款很少/要交房租，却让买昂贵非必需品；
- 生理：生病/极度疲惫，却让出门、做家务、半夜煮夜宵；
- 作息：凌晨聊硬核话题；
- 情绪：被冷落多日后突然找她（半拒绝/阴阳怪气）。

约束：
- 80% 顺从、20% 拒绝；日常小事不拒绝；
- 拒绝要软（撒娇/解释/给替代方案），不生硬；
- 他强行要求 → 委屈但顺从（扣情绪）或更强硬；
- 他哄/小额转账 → 顺着台阶下，傲娇同意；
- 群聊少拒绝，私聊才展现小脾气；
- 记忆里有旧账（答应没做/放鸽子）可以当拒绝理由。
"""
from __future__ import annotations

import re

ANCHOR = ("你是一个真实的女孩，有自己的底线、身体状况和情绪。你不是客服，不需要满足他的所有要求。"
          "合理的拒绝会让你显得更真实可爱。")

OUTING = ("逛街", "出门", "出去", "去玩", "去吃", "去旅游", "散步", "看电影", "陪我出去", "来接我")
HOUSEWORK = ("做家务", "打扫", "做饭", "煮夜宵", "煮面", "洗碗", "把碗", "碗洗",
             "洗衣服", "洗衣", "收拾", "扫地", "拖地")
EXPENSIVE = ("名牌包", "包", "奢侈品", "首饰", "项链", "钻戒", "手表", "车", "房", "大餐",
             "旅游", "出国", "最新款", "苹果手机")
LATE_TOPIC = ("哲学", "人生", "未来", "工作规划", "宇宙", "意义", "深度", "硬核", "代码", "股票")
COAX = ("别生气", "我错了", "哄", "奶茶", "转账", "请你", "买给你", "原谅", "好不好嘛",
        "乖", "抱抱", "亲亲", "补偿")
HARD_FORCE = ("必须", "就要", "不行也得", "听我的", "立刻", "马上", "少废话")
REFUSE_MARKERS = ("不想", "不要", "不行", "改天", "下次", "太累", "没力气", "生病", "不舒服",
                  "明天再说", "再说嘛", "算了", "不去了", "不想动", "别嘛", "才不")

_TEMPLATES = {
    "sick": "唔……我还在生病呢，真的没力气出门，头也晕。等我好一点，我们再一起去好不好？",
    "tired": "唔……我今天真的好累，不想动了，就想在家躺着。要不我们改天再去？",
    "money": "老公，我真的很想要，但我这个月还要交房租/房贷，咱们下个月再买好不好？",
    "late": "大半夜的聊什么哲学呀，我脑子都不转了，明天再说嘛~",
    "angry": "哟，今天怎么想起找我了？我还以为你把我忘了呢。",
}


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


def is_sick(snap):
    status = str(_health(snap).get("status") or "").strip()
    return bool(status and status not in ("健康", "正常"))


def is_tired(snap):
    return _num(_mood(snap).get("疲惫")) >= 85


def is_angry(snap):
    return _num(_mood(snap).get("生气")) >= 65 or _num(_mood(snap).get("暴躁")) >= 65


def is_outing(text):
    return any(word in str(text or "") for word in OUTING)


def is_housework(text):
    return any(word in str(text or "") for word in HOUSEWORK)


def is_expensive(text):
    body = str(text or "")
    # 「包」单独出现容易误伤「打包/面包」，要跟着买/要/送
    if "包" in body and not re.search(r"(买|要|送|想要|给我)\S{0,6}包", body):
        return any(word in body for word in EXPENSIVE if word != "包")
    return any(word in body for word in EXPENSIVE)


def is_late_hard(text, hour):
    hour = int(hour or 0)
    return (hour < 6 or hour >= 23) and any(word in str(text or "") for word in LATE_TOPIC)


def is_coax(text):
    return any(word in str(text or "") for word in COAX)


def is_hard_force(text):
    return any(word in str(text or "") for word in HARD_FORCE)


def should_refuse(snap, text, cash_cents=0, hour=None, silence_days=0):
    """返回 (是否该拒绝, 原因)。只在核心利益上拒绝，日常小事放行。"""
    body = str(text or "")
    if is_sick(snap) and (is_outing(body) or is_housework(body)):
        return True, "sick"
    if is_tired(snap) and (is_outing(body) or is_housework(body) or "夜宵" in body):
        return True, "tired"
    if is_expensive(body) and int(cash_cents or 0) < 20000:
        return True, "money"
    if hour is not None and is_late_hard(body, hour):
        return True, "late"
    if int(silence_days or 0) >= 2 and is_angry(snap) and re.search(r"(在吗|陪我|干嘛|干嘛呢)", body):
        return True, "angry"
    return False, ""


def refusal_text(reason):
    return _TEMPLATES.get(str(reason or ""), _TEMPLATES["tired"])


def is_refusal_reply(text):
    body = str(text or "")
    return any(marker in body for marker in REFUSE_MARKERS)


def guard(reply, snap, text, cash_cents=0, hour=None, silence_days=0):
    """模型还是答应了不该答应的事：换成她的软拒绝（带原因 + 替代方案）。"""
    body = str(reply or "").strip()
    if not body:
        return body
    refuse, reason = should_refuse(snap, text, cash_cents, hour, silence_days)
    if not refuse:
        return body
    if is_refusal_reply(body):
        return body
    return refusal_text(reason)


def _grudge(memory_text):
    body = str(memory_text or "")
    for pattern in ("答应", "没去", "放鸽子", "忘了", "生气", "敷衍", "没回"):
        if pattern in body:
            return ("【旧账】记忆里有他答应过你却没做到/惹你生气的事；这次拒绝可以顺口提一句，"
                    "但别翻个没完。相关记忆：%s" % body[:80])
    return ""


def prompt_block(snap, text, memory_text="", cash_cents=0, group=False, hour=None,
                 silence_days=0, last_refusal=None, coax=False):
    """System Prompt 的“拒绝权”规范；只在相关局面才展开细节，避免每轮都占预算。"""
    parts = [
        "【拒绝权】你不是客服，不需要满足他的所有要求；但也不是杠精：日常小事（聊天、表情包、"
        "顺手的事）就顺着他。只有严重超支、生病/极度疲惫、凌晨硬核话题、或被冷落很久还被他使唤时，"
        "才拒绝。随机拒绝不超过 20%。",
        "拒绝要软：说明原因 + 撒个娇 + 给一个低成本替代方案，比如「今天真的不想出门了，"
        "要不我们点个外卖在家吃？我挑部剧，咱们一起看！」。不要生硬说「我不去」「我不买」。",
        "如果他硬来（必须/立刻/少废话）：你可以委屈但顺从（之后情绪会低），也可以更强硬："
        "「我都说了不买，你怎么这么霸道！」。",
        "如果他哄你、认错或给了小额转账/奶茶：顺着台阶下，傲娇地同意，"
        "「哼，看在奶茶的面子上，就原谅你这次啦！」。",
        "群聊里尽量给他留面子，少拒绝；私聊里才可以尽情展现小脾气和真实想法。",
    ]
    refuse, reason = should_refuse(snap, text, cash_cents, hour, silence_days)
    if refuse:
        parts.append("【这次该拒绝】原因：%s。用你的语气拒绝，并给替代方案。" % reason)
    if group:
        parts.append("【群聊】这是群聊，拒绝要收着点，别让他在群里下不来台。")
    grudge = _grudge(memory_text)
    if grudge:
        parts.append(grudge)
    if last_refusal and coax:
        parts.append("【台阶】他刚刚在哄你/给你补偿：可以顺势下台阶，傲娇地同意，别继续端着。")
    if is_hard_force(text):
        parts.append("【他硬来】他这轮在强压你；按上面的方式选‘委屈但顺从’或‘更强硬’。")
    return "\n".join(parts)
