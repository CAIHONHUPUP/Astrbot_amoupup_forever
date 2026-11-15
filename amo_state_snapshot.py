# -*- coding: utf-8 -*-
"""全局状态快照 + 地点黑名单（2026-11-15）。

一次组装「时间 / 地点 / 健康 / 日程」，聊天、主动消息、分享、记忆注入
都以这份快照为最高事实；地点和此刻对不上的说法在发送前拦下来：
先让模型按快照重生成，退一步再按快照做确定性改写。
"""
from __future__ import annotations

import re

try:
    from .clock import now as _now
    from .amo_shared import day_meta, today_str
except ImportError:  # 直接按文件加载
    from clock import now as _now
    from amo_shared import day_meta, today_str

try:
    from .photo_scene import place_of, zone_of
except ImportError:
    from photo_scene import place_of, zone_of

SNAPSHOT_MARK = "【当前状态·最高事实】"

#: 健康分：>=70 算能正常活动，<60 只适合躺着/喝水/点外卖
HEALTH_SCORE = {"健康": 100, "疲惫": 70, "不舒服": 40, "感冒": 45, "发烧": 20, "重感冒": 20}

PERIODS = ((0, 5, "凌晨"), (5, 9, "早上"), (9, 11, "上午"), (11, 14, "中午"),
           (14, 18, "下午"), (18, 23, "晚上"), (23, 24, "深夜"))

ZONE_TEXT = {
    "home": "家", "office": "公司", "mall": "商场", "scenic": "景区",
    "restaurant": "餐厅", "road": "路上", "hotel": "酒店", "station": "车站",
    "hospital": "医院", "street": "街边", "gym": "健身房", "unknown": "家",
}

#: 每个 zone 不能出现的地点/身份词（句子里带“昨天/以前”等过去标记时放行）
ZONE_BLACKLIST = {
    "home": ("工位", "公司", "办公室", "写字楼", "会议室", "单位", "地铁", "公交",
             "车站", "机场", "客户", "上班", "打卡"),
    "office": ("被窝", "床上", "逛街", "商场", "景区", "旅游", "酒店"),
    "mall": ("工位", "办公室", "被窝", "床上"),
    "road": ("工位", "办公室", "被窝", "床上"),
    "restaurant": ("工位", "办公室", "被窝", "床上"),
    "hotel": ("工位", "办公室"),
    "hospital": ("工位", "办公室"),
    "scenic": ("工位", "办公室", "被窝"),
    "station": ("工位", "办公室", "被窝", "床上"),
    "street": ("工位", "办公室", "被窝", "床上"),
    "gym": ("工位", "被窝", "床上"),
}

#: 句子里有这些词，说明说的是以前的事，允许出现职场/外出词
PAST_MARKERS = ("昨天", "昨晚", "前天", "以前", "之前", "上周", "上个", "那次",
                "那时候", "当年", "大学", "去年", "前阵子", "前两天")

#: 最后兜底改写用的安全替换
SAFE_REPLACEMENTS = {
    "工位": "书桌", "办公室": "屋里", "写字楼": "家", "公司": "家",
    "会议室": "客厅", "单位": "家", "地铁": "楼下", "公交": "楼下",
    "车站": "楼下", "机场": "楼下", "客户": "朋友", "上班": "忙活", "打卡": "签到",
}


def _clauses(text):
    return [part for part in re.split(r"[，,。.！!？?；;\s]+", str(text or "")) if part]


def period_of(hour):
    hour = int(hour) % 24
    for start, end, name in PERIODS:
        if start <= hour < end:
            return name
    return "晚上"


def health_score(health):
    status = str((health or {}).get("status") or "健康")
    return int(HEALTH_SCORE.get(status, 60))


def build_snapshot(plugin, now=None):
    """时间 / 地点 / 健康 / 日程 一次组装；拿不到就退化成空快照，不抛异常。"""
    now = now or _now()
    snap = {}
    try:
        state = getattr(plugin, "state", None)
        if state is not None and callable(getattr(state, "snapshot", None)):
            snap = state.snapshot() or {}
    except Exception:
        snap = {}
    activity = snap.get("activity") or {}
    try:
        zone = zone_of(activity) or "unknown"
    except Exception:
        zone = "unknown"
    try:
        place = place_of(activity) or str(activity.get("place") or "")
    except Exception:
        place = str(activity.get("place") or "")
    health = {}
    try:
        health = snap.get("health") or {}
    except Exception:
        health = {}
    if not health:
        try:
            health = plugin.state.health_info() or {}
        except Exception:
            health = {}
    day = today_str()
    try:
        meta = day_meta(day)
    except Exception:
        meta = {}
    score = health_score(health)
    return {
        "ts": now.timestamp(),
        "date": day,
        "weekday": str(meta.get("weekday") or ""),
        "time": now.strftime("%H:%M"),
        "period": period_of(now.hour),
        "zone": zone,
        "place": place or ZONE_TEXT.get(zone, ""),
        "activity": str(activity.get("activity") or ""),
        "category": str(activity.get("category") or ""),
        "health": {
            "status": str(health.get("status") or "健康"),
            "note": str(health.get("note") or ""),
            "sick": bool(health.get("sick")),
            "score": score,
        },
        "rest": bool(meta.get("rest")),
        "label": str(meta.get("label") or ""),
        "mood": str(snap.get("dominant") or ""),
    }


def zone_blacklist(zone):
    return tuple(ZONE_BLACKLIST.get(str(zone or ""), ()))


def blacklist_hit(text, zone):
    """回复里有没有「此刻不可能出现」的地点词（带昨天/以前标记的句子放行）。"""
    words = zone_blacklist(zone)
    if not words:
        return []
    hits = []
    for clause in _clauses(text):
        if any(marker in clause for marker in PAST_MARKERS):
            continue
        for word in words:
            if word in clause and word not in hits:
                hits.append(word)
    return hits


def force_rewrite(text, zone):
    """确定性兜底：把冲突地点词换成安全说法；仍冲突就丢掉冲突分句。"""
    words = zone_blacklist(zone)
    value = str(text or "")
    for word in words:
        if word in value:
            value = value.replace(word, SAFE_REPLACEMENTS.get(word, "家"))
    if not blacklist_hit(value, zone):
        return value
    kept = [clause for clause in _clauses(value) if not blacklist_hit(clause, zone)]
    if kept:
        return "，".join(kept)
    return "我在家呢，刚在忙别的事。"


def snapshot_text(snapshot):
    """给 LLM 的顶层状态块；快照为空时返回空串。"""
    if not snapshot:
        return ""
    zone = str(snapshot.get("zone") or "")
    if zone in ("", "unknown"):
        return ""
    place = str(snapshot.get("place") or ZONE_TEXT.get(zone, "家"))
    weekday = str(snapshot.get("weekday") or "")
    lines = [
        "%s 现在 %s 星期%s %s（%s）。你在「%s」（zone=%s）。"
        % (SNAPSHOT_MARK, snapshot.get("date") or "", weekday,
           snapshot.get("time") or "", snapshot.get("period") or "", place, zone),
    ]
    activity = str(snapshot.get("activity") or "").strip()
    if activity:
        lines.append("日程：%s。" % activity)
    if snapshot.get("rest"):
        label = str(snapshot.get("label") or "").strip()
        lines.append("今天是休息日%s。" % (("（" + label + "）") if label else ""))
    health = snapshot.get("health") or {}
    if health.get("sick") or int(health.get("score") or 100) < 70:
        lines.append(
            "【身体】%s（健康分 %d/100）：只能做轻微的事——倒水吃药、裹被子、点外卖；"
            "不许做大扫除、瑜伽、做一桌菜这种体力活。"
            % (health.get("status") or "不舒服", int(health.get("score") or 0))
        )
    words = "、".join(zone_blacklist(zone)[:6])
    if words:
        lines.append(
            "【防穿帮】此刻不许说自己在%s；这些地方只能以「昨天」「以前」出现，"
            "绝不能说成刚刚或今天。" % words
        )
    if zone == "home":
        lines.append(
            "【记忆口径】记忆里昨天/以前的职场、出门经历，分享时必须说明是「昨天」「以前」，"
            "不能说成刚刚发生。"
        )
    return "\n".join(lines)
