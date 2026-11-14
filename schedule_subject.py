# -*- coding: utf-8 -*-
"""日程主语校验：她的日程只能写她自己的活动（2026-11-14）。

背景：去禁令化之后，模型会自己编「今天和老公去逛街」「老公陪我看电影」，
即使他这轮并没有答应约会。日程会反过来变成【正在】/【今日安排】，
她就真的"凭空"和老公出门了。

规则（纯函数，无数据库、无模型调用）：

- 活动主语是「我」「我和闺蜜」「我和同事」「我自己」→ 允许；
- 出现「和老公 / 跟老公 / 和他 / 老公陪我 / 他带我去」→ 只有真约过会才允许；
- 主语是「老公/他」（他在加班、他去医院）→ 这是他的日程，不是她的，同样要改写。

没有约会授权时把"老公/他"换成"闺蜜"（或调用方给的 fallback），保留原来的动作：
「和老公去逛街」会变成「和闺蜜去逛街」，而不是整段丢掉。

单测：tests/test_schedule_subject.py。
"""
from __future__ import annotations

import re

SELF = "self"
WITH_OTHER = "with_other"
OTHER = "other"
UNKNOWN = "unknown"

#: 把对方写进活动：和老公 / 跟老公 / 与男朋友 / 和他 / 老公陪我 / 他带我去 / 让老公开车…
_COMPANION = re.compile(
    r"(?:和|跟|与|同)\s*(?:我|我的)?\s*(?:老公|他|男朋友|男友|对象)"
    r"|(?:老公|他)\s*(?:开车(?:来|去)?|陪|带|送|接|载)(?:着|上)?(?:我)?"
    r"|(?:让|叫)\s*(?:老公|他)\s*(?:开车|陪|带|送|接|载)"
)

#: 活动主语直接是他：他在加班 / 他去医院 / 老公出差 / 他今天很忙
_OTHER_SUBJECT = re.compile(
    r"^\s*(?:老公|他)\s*(?:在|去|要|会|今天|明天|刚|正在|加班|出差|上班|下班|忙|睡|吃|看|买|逛|回|来)"
)

DEFAULT_COMPANION = "闺蜜"
NEUTRAL_FALLBACK = "自己待着"


def _clean(text) -> str:
    return str(text or "").strip()


def subject_of(activity) -> str:
    """活动的主语是谁：self / with_other / other / unknown。"""
    body = _clean(activity)
    if not body:
        return UNKNOWN
    if _COMPANION.search(body):
        return WITH_OTHER
    if _OTHER_SUBJECT.search(body):
        return OTHER
    return SELF


def needs_date(activity) -> bool:
    """这句话是不是必须真有约会授权才能写进她的日程。"""
    return subject_of(activity) in (WITH_OTHER, OTHER)


def rewrite_activity(activity, approved=False, companion=DEFAULT_COMPANION) -> str:
    """把不允许的"和老公"改写掉；approved=True 或本来就合规时原样返回。"""
    body = _clean(activity)
    if not body or approved or not needs_date(body):
        return body
    if subject_of(body) == OTHER:
        # 主语是他：这句本来就不是她的日程，退回一个中性活动
        return NEUTRAL_FALLBACK
    out = _COMPANION.sub("和" + companion, body)
    # 「老公陪我逛街」→ 替换后可能多出「陪我」
    out = re.sub(r"和" + companion + r"\s*(?:陪|带|送|接|载)(?:着|上)?(?:我)?", "和" + companion, out)
    return out.strip()


def validate_activity(activity, approved=False, companion=DEFAULT_COMPANION) -> dict:
    """返回 {ok, subject, activity, reason, changed}；ok=False 时 activity 已改写。"""
    body = _clean(activity)
    subject = subject_of(body)
    if not body:
        return {"ok": True, "subject": UNKNOWN, "activity": "", "reason": "empty", "changed": False}
    if approved or subject == SELF:
        return {
            "ok": True,
            "subject": subject,
            "activity": body,
            "reason": "date_approved" if approved else "ok",
            "changed": False,
        }
    new = rewrite_activity(body, approved=approved, companion=companion)
    return {
        "ok": False,
        "subject": subject,
        "activity": new,
        "reason": "date_required",
        "changed": new != body,
    }
