# -*- coding: utf-8 -*-
"""全局状态快照 + 地点黑名单（2026-11-15，2026-11-22 对称化）。

一次组装「时间 / 地点 / 健康 / 日程」，聊天、主动消息、分享、记忆注入
都以这份快照为最高事实；地点和此刻对不上的说法在发送前拦下来：
先让模型按快照重生成，退一步再按快照做确定性改写。

2026-11-22 task-17：黑名单从“只防在家说职场”改成**双向对称**：
- home 也要禁 商场/逛街/试衣/小馆/食堂（否则“在家说在商场试开衫”漏网）；
- road/office/mall 也要禁 沙发/被子/厨房/睡衣（否则“在路上说在沙发上”漏网）；
- 否定用法（“不是沙发”“不要地铁”）不再算命中。
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

import re

try:
    from .clock import now as _now
    from .amo_shared import day_meta, today_str
except ImportError:  # 直接按文件加载
    if not TYPE_CHECKING:
        from clock import now as _now
        from amo_shared import day_meta, today_str

try:
    from .photo_scene import place_of, zone_of
except ImportError:
    if not TYPE_CHECKING:
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

#: 每个 zone 不能出现的地点/身份词（句子里带“昨天/以前/不是…”等标记时放行）。
#: 对称原则：home ↔ 外景、road/office ↔ 居家，两个方向都要有词。
ZONE_BLACKLIST = {
    "home": ("工位", "公司", "办公室", "写字楼", "会议室", "单位", "地铁", "公交",
             "车站", "机场", "客户", "上班", "打卡",
             # 2026-11-22：外景词在家也禁（在商场说在家 / 在家说试衣）
             "商场", "逛街", "试衣", "试穿", "专柜", "小馆", "食堂", "餐厅",
             "公园", "景区", "酒店", "超市"),
    "office": ("被窝", "床上", "逛街", "商场", "景区", "旅游", "酒店",
               # 居家词在公司/工位也禁
               "沙发", "被子", "厨房", "睡衣", "煮面"),
    "mall": ("工位", "办公室", "被窝", "床上", "沙发", "被子", "厨房", "睡衣"),
    "road": ("工位", "办公室", "被窝", "床上", "沙发", "被子", "厨房", "睡衣", "煮面"),
    "restaurant": ("工位", "办公室", "被窝", "床上", "被子", "厨房", "睡衣"),
    "hotel": ("工位", "办公室", "沙发", "厨房"),
    "hospital": ("工位", "办公室", "沙发", "厨房", "商场", "逛街", "试衣"),
    "scenic": ("工位", "办公室", "被窝", "被子", "厨房", "睡衣"),
    "station": ("工位", "办公室", "被窝", "床上", "被子", "厨房", "睡衣"),
    "street": ("工位", "办公室", "被窝", "床上", "被子", "厨房", "睡衣"),
    "gym": ("工位", "被窝", "床上", "被子", "厨房", "睡衣"),
}

#: 否定词：命中词前面（同一分句内 12 字内）出现这些，就按“否定的地点”处理，
#: 不算穿帮（照片提示词里的“不是沙发、不是床”“不要地铁/公交/车厢”）。
NEGATION_MARKERS = ("不是", "没有", "不在", "不出现", "不要", "不许", "别", "非", "无", "没")

#: 句子里有这些词，说明说的是以前的事，允许出现职场/外出词
PAST_MARKERS = ("昨天", "昨晚", "前天", "以前", "之前", "上周", "上个", "那次",
                "那时候", "当年", "大学", "去年", "前阵子", "前两天")

#: 最后兜底改写用的安全替换
SAFE_REPLACEMENTS = {
    "工位": "书桌", "办公室": "屋里", "写字楼": "家", "公司": "家",
    "会议室": "客厅", "单位": "家", "地铁": "楼下", "公交": "楼下",
    "车站": "楼下", "机场": "楼下", "客户": "朋友", "上班": "忙活", "打卡": "签到",
    "商场": "外面", "逛街": "走走", "试衣": "看衣服", "试穿": "看衣服",
    "专柜": "店里", "小馆": "家", "食堂": "家", "餐厅": "家",
    "公园": "外面", "景区": "外面", "酒店": "外面", "超市": "家",
    "沙发": "座椅", "被子": "外套", "厨房": "外面", "睡衣": "便服", "煮面": "吃点东西",
}

#: 通用地点词（unknown 时禁止断言具体地点、照片单 zone 检测共用）
EXTRA_PLACE_WORDS = (
    "家", "家里", "客厅", "卧室", "厨房", "阳台", "卫生间", "洗手台", "沙发", "茶几",
    "抱枕", "被窝", "出租屋", "床上", "公司", "工位", "办公室", "写字楼", "会议室",
    "商场", "店铺", "橱窗", "扶梯", "专柜", "试衣间", "购物袋", "柜台", "逛街", "试衣",
    "试穿", "小馆", "食堂", "餐厅", "火锅", "菜单", "碗筷", "餐盘",
    "地铁", "车厢", "公交", "站台", "吊环", "扶手", "车窗", "路面", "安全带",
    "驾驶位", "出租车", "网约车", "车上", "隧道", "站牌",
    "医院", "候诊", "诊室", "挂号", "消毒水", "叫号屏", "走廊",
    "景区", "景点", "山顶", "海边", "沙滩", "公园", "湖边", "游乐园",
    "酒店", "宾馆", "民宿", "客房", "行李", "房价",
    "车站", "候车", "时刻表", "检票", "行李箱", "图书馆", "健身房", "电影院",
    "教室", "宿舍", "路边", "小区", "外面",
)


def _clauses(text):
    return [part for part in re.split(r"[，,。.！!？?；;\s]+", str(text or "")) if part]


def _word_unnegated(clause, word):
    """word 在 clause 里是否以“非否定”的用法出现。"""
    clause = str(clause or "")
    start = 0
    while True:
        index = clause.find(word, start)
        if index < 0:
            return False
        prefix = clause[max(0, index - 12):index]
        if any(marker in prefix for marker in NEGATION_MARKERS):
            start = index + len(word)
            continue
        return True


def _replace_unnegated(text, word, replacement):
    """只替换非否定用法的 word；“不是沙发”保持原样。"""
    value = str(text or "")
    if not word or word not in value:
        return value
    out = []
    start = 0
    while True:
        index = value.find(word, start)
        if index < 0:
            out.append(value[start:])
            break
        prefix = value[max(0, index - 12):index]
        if any(marker in prefix for marker in NEGATION_MARKERS):
            out.append(value[start:index + len(word)])
        else:
            out.append(value[start:index])
            out.append(replacement)
        start = index + len(word)
    return "".join(out)


def _mask_negated(text, word):
    """把“否定的 word”替换成占位符，肯定用法原样保留。"""
    value = str(text or "")
    if not word or word not in value:
        return value
    out = []
    start = 0
    while True:
        index = value.find(word, start)
        if index < 0:
            out.append(value[start:])
            break
        prefix = value[max(0, index - 12):index]
        if any(marker in prefix for marker in NEGATION_MARKERS):
            out.append(value[start:index])
            out.append("□")
        else:
            out.append(value[start:index + len(word)])
        start = index + len(word)
    return "".join(out)


def strip_negated(text, words=None):
    """把“否定的地点词”遮掉，只留肯定用法（照片单 zone 检测用）。"""
    value = str(text or "")
    for word in (words if words is not None else all_place_words()):
        if word and word in value:
            value = _mask_negated(value, word)
    return value


def all_place_words():
    """所有地点词（黑名单 ∪ 通用地点词），去重保序。"""
    words = []
    for group in ZONE_BLACKLIST.values():
        for word in group:
            if word not in words:
                words.append(word)
    for word in EXTRA_PLACE_WORDS:
        if word not in words:
            words.append(word)
    return tuple(words)


def period_of(hour):
    hour = int(hour) % 24
    for start, end, name in PERIODS:
        if start <= hour < end:
            return name
    return "晚上"


def health_score(health):
    status = str((health or {}).get("status") or "健康")
    return int(HEALTH_SCORE.get(status, 60))


def _ledger_scene(plugin, now):
    """地点/此刻动作的唯一事实源：scene_ledger.scene_at（含病假/事假/出差/约会硬约束）。

    返回 (scene, status)：
    - ("ok") 账本给出规范化场景（已做硬约束钳制）；
    - ("unknown") 账本明确「此刻不知道在哪」→ 快照按未知处理，绝不拿 state.snapshot 猜；
    - ("unavailable") 账本读不到/老宿主没有 db → 调用方退回 state.snapshot 的 activity 兼容。
    """
    try:
        try:
            from .amo_scene_ledger import QUALITY_UNKNOWN, scene_at as _scene_at
        except ImportError:
            from amo_scene_ledger import QUALITY_UNKNOWN, scene_at as _scene_at
        scene = _scene_at(plugin, now) or {}
    except Exception:
        return {}, "unavailable"
    if not isinstance(scene, dict) or not scene:
        return {}, "unavailable"
    # 老宿主/单测桩没有 db：账本读不出日程，退回 state.snapshot 的 activity 兼容。
    has_db = getattr(plugin, "db", None) is not None or getattr(
        getattr(plugin, "state", None), "db", None) is not None
    if not has_db:
        return {}, "unavailable"
    if str(scene.get("quality") or "") == QUALITY_UNKNOWN:
        return {}, "unknown"
    return scene, "ok"


def build_snapshot(plugin, now=None):
    """时间 / 地点 / 健康 / 日程 一次组装；拿不到就退化成空快照，不抛异常。

    2026-11-22 task-25：地点/此刻动作只读场景账本（scene_ledger.scene_at），
    不再读 state.snapshot()["activity"]——那份 activity 可能被 JIT/过渡/旧写者写成
    「病假在家却显示公司/通勤」。健康/心情/天气仍从 state.snapshot() 取。
    """
    now = now or _now()
    snap: dict[str, Any] = {}
    try:
        state = getattr(plugin, "state", None)
        if state is not None and callable(getattr(state, "snapshot", None)):
            snap = state.snapshot() or {}
    except Exception:
        snap: dict[str, Any] = {}
    scene, _scene_status = _ledger_scene(plugin, now)
    if scene:
        zone = str(scene.get("zone") or "unknown")
        place = str(scene.get("place") or "")
        activity = {
            "activity": str(scene.get("activity") or ""),
            "category": str(scene.get("category") or ""),
            "place": place,
            "zone": zone,
            "event_id": str(scene.get("event_id") or ""),
            "quality": str(scene.get("quality") or ""),
        }
    elif _scene_status == "unavailable":
        # 老宿主/单测桩：没有场景账本能力，沿用 state.snapshot 的 activity（向后兼容）。
        activity = snap.get("activity") or {}
        try:
            zone = zone_of(activity) or "unknown"
        except Exception:
            zone = "unknown"
        try:
            place = place_of(activity) or str(activity.get("place") or "")
        except Exception:
            place = str(activity.get("place") or "")
    else:
        # 账本明确「此刻不知道在哪」：不许再用 state.snapshot 里的地点猜（防失真）。
        activity = {}
        zone = "unknown"
        place = ""
    health: dict[str, Any] = {}
    try:
        health = snap.get("health") or {}
    except Exception:
        health: dict[str, Any] = {}
    if not health:
        try:
            health = plugin.state.health_info() or {}
        except Exception:
            health: dict[str, Any] = {}
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


def scene_activity(plugin, now=None):
    """账本场景 → state.snapshot()["activity"] 的老形状（2026-11-22 task-30 兼容读）。

    返回 (activity, status)：
    - ("ok") 账本给出规范化场景；
    - ("unknown") 账本明确「此刻不知道在哪」→ activity 为空（调用方不许拿旧快照猜）；
    - ("unavailable") 老宿主/单测桩没有账本能力 → activity 为空，调用方保留旧快照。
    """
    scene, status = _ledger_scene(plugin, now)
    if not scene:
        return {}, status
    return {
        "activity": str(scene.get("activity") or ""),
        "place": str(scene.get("place") or ""),
        "zone": str(scene.get("zone") or ""),
        "category": str(scene.get("category") or ""),
        "event_id": str(scene.get("event_id") or ""),
        "start": str(scene.get("start") or ""),
        "end": str(scene.get("end") or ""),
        "quality": str(scene.get("quality") or ""),
        "_source": str(scene.get("source") or ""),
        "_reason": str(scene.get("reason") or ""),
    }, status


def snapshot_with_scene(plugin, now=None):
    """state.snapshot() 的兼容读：只把 activity 换成场景账本的规范结果（task-30）。

    为什么不是直接换 build_snapshot()：门控/回复/面板读的是 state.snapshot() 的嵌套形状
    （snap["activity"]["category"]、snap["mood"] 字典、snap["health"]…），build_snapshot
    是给 LLM 的扁平快照，两者不能互换。这里保留 state.snapshot() 的其它字段，
    只让「此刻在哪/在做什么」唯一走 scene_ledger.scene_at（含请假/出差/约会硬约束）。
    """
    snap: dict[str, Any] = {}
    state = getattr(plugin, "state", None)
    getter = getattr(state, "snapshot", None)
    if callable(getter):
        # 和直接调 state.snapshot() 同口径：异常照旧上抛，由调用方的原契约处理
        # （调用方若本来就 try/except，仍会走到它的兜底分支）。
        snap = getter() or {}
    if not isinstance(snap, dict):
        snap = {}
    else:
        snap = dict(snap)
    activity, status = scene_activity(plugin, now)
    if status == "ok":
        snap["activity"] = activity
    elif status == "unknown":
        # 账本明确「此刻不知道在哪」：不许再拿 state.snapshot 里的地点猜（防 21:00 还报 14:00 的商场）。
        snap["activity"] = {}
    # status == "unavailable"：老宿主/单测桩没有 db，保留 state.snapshot() 的 activity（向后兼容）。
    return snap


def zone_blacklist(zone):
    return tuple(ZONE_BLACKLIST.get(str(zone or ""), ()))


def blacklist_hit(text, zone):
    """回复里有没有「此刻不可能出现」的地点词（否定/过去标记的句子放行）。"""
    words = zone_blacklist(zone)
    if not words:
        return []
    hits = []
    for clause in _clauses(text):
        if any(marker in clause for marker in PAST_MARKERS):
            continue
        for word in words:
            if word in clause and word not in hits and _word_unnegated(clause, word):
                hits.append(word)
    return hits


def force_rewrite(text, zone):
    """确定性兜底：把冲突地点词换成安全说法；仍冲突就丢掉冲突分句。"""
    words = zone_blacklist(zone)
    value = str(text or "")
    for word in words:
        if word in value:
            value = _replace_unnegated(value, word, SAFE_REPLACEMENTS.get(word, "家"))
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
    words = "、".join(zone_blacklist(zone)[:8])
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


__all__ = [
    "SNAPSHOT_MARK", "ZONE_TEXT", "ZONE_BLACKLIST", "NEGATION_MARKERS", "PAST_MARKERS",
    "SAFE_REPLACEMENTS", "EXTRA_PLACE_WORDS", "period_of", "health_score",
    "build_snapshot", "scene_activity", "snapshot_with_scene",
    "zone_blacklist", "blacklist_hit", "force_rewrite",
    "strip_negated", "all_place_words", "snapshot_text",
]
