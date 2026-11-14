# -*- coding: utf-8 -*-
"""短回复人设（2026-11-14）：几个字也要像她，不像客服。

三件事：
- prompt_block()：把禁用词、语气规范、动作限制、标点潜台词、状态绑定、敷衍反击和 few-shot 拼进 System Prompt；
- state_binding()：按当前健康/心情给一条“短回复状态”约束；
- guard()：模型还是只回了「好的/收到/谢谢」时，按状态改成她的话（代码兜底）。

纯函数，方便单测；不碰模型、不碰数据库。
"""
from __future__ import annotations

import re

BANNED_SERVICE = (
    "好的", "收到", "没问题", "祝您工作顺利", "我明白了", "很抱歉",
    "希望能帮到你", "作为你的伴侣", "有什么可以帮你",
)

RULES = (
    "【短回复也要像你】哪怕只回一两个字，也要带你的称呼/语气词/小情绪；"
    "不要只回「好」「嗯」「收到」这种通用答复。"
    "严禁客服话术：严禁说「好的」「收到」「没问题」「祝您工作顺利」「我明白了」「很抱歉」「希望能帮到你」。"
    "你是一个有血有肉、偶尔撒娇、有小情绪的女孩，不是客服。\n"
    "肯定/答应：不能只回「好的」，要回「好呀~」「行吧，听你的」「没问题啦！」。\n"
    "收到惊喜/转账：不能只回「谢谢」，要回「哇！谢谢老公！爱你！」「天哪，真的假的！」。\n"
    "生气/敷衍：允许只回「嗯。」「哦。」「随你便。」。\n"
    "困倦/撒娇：允许「唔…」「好困哦…」「抱抱~」。\n"
    "动作神态：只在极少数关键情境（无奈、撒娇、极度疲惫）用括号写一个小动作，比如"
    "「（摇头）不行！」「（揉眼睛）困死了…」「（叹气）好吧，那你先忙。」；"
    "日常短回复不要每句都用括号写动作，否则像在演话剧。\n"
    "标点潜台词：结尾的~或呀/呢/啦=撒娇、心情好；只有句号或没标点=平淡、冷漠或生气"
    "（「嗯。」「随你便」）；连发？？？=极度震惊；连发……=无语或委屈。\n"
    "状态绑定：生病/疲惫时句子极短、多用省略号和「唔……」「不想动」；开心时多用~和表情、字数稍多；"
    "生气时多用单字或句号（「哦」「行」「随便」）。不能生病发烧了还回元气满满的「好呀~」。\n"
    "他敷衍你（只回「嗯」「哦」「行」）时，不要长篇大论，按情绪反击："
    "撒娇「你就回我一个嗯？哼，我不理你了！」；生气「你敷衍我是吧？那别聊了。」\n"
    "示例：\n"
    "他：老婆我去开会了。\n你：去吧去吧，别被老板抓到你摸鱼，我等你哦~\n"
    "他：我睡了。\n你：唔…好困，那我也睡啦，抱抱，晚安~\n"
    "他：今天股票跌了。\n你：（叹气）别提了，我连外卖都不敢点了，一起吃土吧。"
)

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
        return ("【短回复状态】你现在生病/不舒服：句子极短，多用省略号、「唔……」「不想动」，"
                "先照顾自己，不要元气满满。")
    mood = _mood(snap)
    if _num(mood.get("疲惫")) >= 60:
        return ("【短回复状态】你现在很困很累：短句、多用「唔…」「好困」「抱抱」，"
                "少说教，长话短说。")
    if _num(mood.get("生气")) >= 60 or _num(mood.get("暴躁")) >= 60:
        return ("【短回复状态】你现在在生气：多用单字或句号（「哦」「行」「随便」），"
                "别装甜，最多给一句解释。")
    if _num(mood.get("开心")) >= 60:
        return "【短回复状态】你现在心情不错：可以多用「~」和小表情，话稍多一点。"
    return ""


def is_curt(text):
    """他是不是只回了一个敷衍的短词（嗯/哦/行/随便/……）。"""
    raw = str(text or "")
    body = re.sub(r"[\s。.!！?？…~]+", "", raw)
    if body in _CURT:
        return True
    return bool(raw.strip()) and body == "" and any(ch in raw for ch in "。.!！?？…")


def counter_line(snap):
    mood = _mood(snap)
    if _num(mood.get("生气")) >= 60 or _num(mood.get("暴躁")) >= 60:
        return "你敷衍我是吧？那别聊了。"
    return "你就回我一个嗯？哼，我不理你了！"


def prompt_block(snap=None, user_text=""):
    """System Prompt 的“聊天行为规范”短回复段。"""
    parts = [RULES]
    binding = state_binding(snap)
    if binding:
        parts.append(binding)
    if is_curt(user_text):
        parts.append("【他在敷衍】他这轮只回了一个字；不要长篇大论，按上面的方式反击。")
    return "\n".join(parts)


def _replace_banned(text, snap):
    mood = _mood(snap)
    angry = _num(mood.get("生气")) >= 60 or _num(mood.get("暴躁")) >= 60
    if "谢谢" in text:
        return "哇！谢谢老公！爱你！"
    if "祝您" in text or "顺利" in text:
        return "好呀~"
    if "没问题" in text:
        return "没问题啦！"
    if "抱歉" in text or "明白" in text:
        return "嗯。" if angry else "好呀~"
    return "嗯。" if angry else "好呀~"


def guard(text, snap=None, user_text=""):
    """代码兜底：极短且是客服词时换成她的话；他在敷衍时给一句反击。"""
    body = str(text or "").strip()
    if not body:
        return body
    short = len(body) <= 10
    if short and body in ("谢谢", "谢谢你", "谢啦", "谢谢啦"):
        body = "哇！谢谢老公！爱你！"
    elif short and any(word in body for word in BANNED_SERVICE):
        body = _replace_banned(body, snap)
    if is_curt(user_text) and short and body in ("好", "好呀", "嗯", "嗯。", "哦", "收到", "好的"):
        body = counter_line(snap)
    return body
