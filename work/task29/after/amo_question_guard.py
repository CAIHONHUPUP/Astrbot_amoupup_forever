# -*- coding: utf-8 -*-
"""答到点上：问地点/动作/同伴/几点回/吃了吗时，回复不相关就用当前状态兜底（2026-11-22）。

不是替模型写话，而是在它答非所问时用唯一状态视图给一个事实短句。
"""
from __future__ import annotations

import re

try:  # 统一兜底注册表（task-18）：问句兜底模板唯一来源
    from .domains.reply import repetition_guard as _fallbacks
except ImportError:  # 直接按文件加载时
    from domains.reply import repetition_guard as _fallbacks

_WHERE = re.compile(r"在哪|哪里|什么地方|哪个地方")
_DOING = re.compile(r"在干嘛|干嘛呢|做什么|干啥|忙什么")
_WHO = re.compile(r"跟谁|和谁|一个人吗|谁在一起")
_WHEN = re.compile(r"几点回|什么时候回|多久回|几点到家|什么时候到家|你到家了吗|你什么时候回来")
_EATEN = re.compile(r"吃了吗|吃了没|吃饭了吗|吃过了吗")

_PLACES = ("家", "公司", "商场", "路上", "地铁", "医院", "餐厅", "学校", "机场",
           "抖音", "B站", "b站", "微博", "小红书", "网上", "店里")


def _act(plugin):
    try:
        return plugin.state.current_activity() or {}
    except Exception:
        return {}


def _category(text):
    body = str(text or "")
    for name, pattern in (("where", _WHERE), ("doing", _DOING), ("who", _WHO),
                          ("when", _WHEN), ("eaten", _EATEN)):
        if pattern.search(body):
            # 「我妈问我们什么时候回去」是转述，不是他现在问
            if name == "when" and "问" in body and not re.search(r"你|您", body):
                continue
            return name
    return ""


def _answered(category, reply):
    body = str(reply or "")
    if category == "where":
        return any(word in body for word in _PLACES + ("外面", "这边", "那儿"))
    if category == "doing":
        return any(word in body for word in ("在", "忙", "做", "弄", "写", "看", "吃", "睡"))
    if category == "who":
        return any(word in body for word in ("一个人", "自己", "和", "跟", "他", "她", "同事", "朋友"))
    if category == "when":
        return bool(re.search(r"\d{1,2}[:：]\d{2}|到家|路上|马上|快了|晚点|这就", body))
    if category == "eaten":
        return any(word in body for word in ("吃", "饭", "面", "饿"))
    return False


def answer(text, plugin, reply, candidate=""):
    """答非所问时用当前状态兜底；本轮候选已经答到这类问题，就不许整条覆盖它。

    candidate = 进入破坏性改写前的原始候选（task-29 后置语义复检入口之一）：
    上游守卫可能把候选删坏或换成通用句，状态模板只能在候选也答不到时才顶上去。
    """
    category = _category(text)
    if not category or _answered(category, reply):
        return reply
    candidate = str(candidate or "").strip()
    if candidate and _answered(category, candidate):
        return candidate
    act = _act(plugin)
    place = str(act.get("place") or "")
    zone = str(act.get("zone") or "")
    doing = str(act.get("activity") or "")
    if category == "where":
        return _fallbacks.sentence("question.where", place=place or "外面")
    if category == "doing":
        doing = doing or "忙点事"
        if doing.startswith("在"):
            # 状态里的活动本身带「在」（「在酒店休息」），模板再套一层会成
            # 「我在在酒店休息。」（审计 3.2.3 真实模型层样例）。
            return "我在%s。" % doing[1:]
        return _fallbacks.sentence("question.doing", doing=doing)
    if category == "who":
        if act.get("together") or act.get("companion"):
            who = str(act.get("companion") or "朋友")
            return _fallbacks.sentence("question.who", who=who)
        return _fallbacks.sentence("question.alone")
    if category == "when":
        if zone.startswith("home") or "家" in place:
            return _fallbacks.sentence("question.when_home")
        end = str(act.get("end") or "").strip()
        if place:
            return _fallbacks.sentence("question.when_route", place=place,
                                       end=(end + " ") if end else "晚点")
        return _fallbacks.sentence("question.when_late")
    if category == "eaten":
        if str(act.get("category") or "") == "meal" or "吃" in doing:
            return _fallbacks.sentence("question.eaten")
        return _fallbacks.sentence("question.not_eaten")
    return reply
