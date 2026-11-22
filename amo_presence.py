# -*- coding: utf-8 -*-
"""唯一在场/打扰策略（2026-11-22 架构收口）。

所有主动入口（主动消息、催问、逛街、自拍、未回复追问、照片、推送）只问这里：
- 她现在在哪（current_scene，唯一读 state.current_activity）；
- 用户沉默多久（silence_hours / is_silent）；
- 这个类型能不能发（can_proactive / can_shop / can_selfie / can_photo / can_push）；
- 照片能不能绑定到当前事件、同一 event_id 是否已经发过图（photo_binding / claim_photo_event）。

硬规则（对应 2026-11-22 真实飞书记录）：
- 最后一条用户消息超过 6 小时：禁止催问/逛街/自拍/未回复追问，只允许早晚问候和
  明确时间点提醒（安全照顾类除外，它们不是打扰）；
- 逛街只在当前 zone 为 mall/street/scenic，或当前活动明确写着逛街/试衣时才触发，
  在家一律不触发；
- 照片必须绑定当前事件：逛街 pending 但人不在商场 → 不发图；在商场 → 用商场/试衣间
  场景；同一 event_id 最多一张图。

禁止各入口再自己写地点/沉默判断；需要新规则就加在这个文件。
"""
from __future__ import annotations

#: 用户最后一条消息超过这么久没回：禁止催问/逛街/自拍/未回复追问。
SILENCE_BLOCK_HOURS = 6.0
#: 沉默超时后仍允许的主动类型：早晚问候 + 明确时间点提醒。
SILENCE_ALLOWED_KINDS = frozenset({"morning", "goodnight", "schedule_reminder"})
#: 安全/照顾类不算打扰：生病、情绪崩、生日、红线急照常发。
SILENCE_CARE_KINDS = frozenset({"birthday", "unwell", "comfort", "forced"})
#: 主动参与类：沉默 6 小时一律禁发（催问/逛街/自拍/未回复追问 + 闲聊分享）。
ENGAGEMENT_KINDS = frozenset({
    "nudge", "shopping", "tryon", "selfie", "photo", "followup",
    "greet", "share", "slack", "commute", "game", "meal", "offwork",
})
SHOP_ZONES = ("mall", "street", "scenic")
SHOP_WORDS = ("商场", "逛街", "试衣", "试穿", "步行街", "专柜", "买衣服", "挑衣服", "试开衫")
HOME_WORDS = ("家", "家里", "家中", "客厅", "卧室", "沙发", "厨房", "阳台",
              "卫生间", "洗手台", "床上", "被窝")
#: watch 写进陪伴库的「正在逛街试衣」事实（note_shopping_pending）。
SHOPPING_PENDING_KEY = "shopping_prompt"
#: 照片事件去重：同一 event_id 最多一张图。
PHOTO_EVENT_KEY = "photo_event:"
#: 提醒类推送：沉默 6 小时也允许（明确时间点提醒）。
PUSH_ALLOWED_KINDS = SILENCE_ALLOWED_KINDS


def _now_ts(now=None):
    """生产统一时钟（clock.now_ts，测试里可冻结）；坏值退回 0，不裸调 time.time。"""
    if now is not None:
        try:
            return float(now)
        except (TypeError, ValueError):  # cleanup: 兜底/降级，保持原行为
            pass
    try:
        try:
            from . import clock as _clock
        except ImportError:
            import clock as _clock
        return float(_clock.now_ts())
    except Exception:
        return 0.0


def _num(value, default=0.0):
    """把任意值压成 float；坏值回退 default（0 表示未知，不误杀）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def silence_hours(last_seen, now=None):
    """用户沉默小时数；没有 last_seen 视为未知，返回 0（不误杀）。"""
    last = _num(last_seen, 0.0)
    if last <= 0:
        return 0.0
    current = _num(now if now is not None else _now_ts(now), _now_ts(now))
    return max(0.0, (current - last) / 3600.0)


def profile_last_seen(plugin, profile=None):
    """取用户最后一次说话的时间：优先用传入画像，没有再查最近活跃画像。"""
    if isinstance(profile, dict):
        value = _num(profile.get("last_seen"), 0.0)
        if value > 0:
            return value
    try:
        rows = list(plugin.db.proactive_profiles(limit=1) or [])
    except Exception:
        rows = []
    for row in rows:
        if isinstance(row, dict):
            value = _num(row.get("last_seen"), 0.0)
            if value > 0:
                return value
    return 0.0


def is_silent(plugin, profile=None, now=None):
    """用户最后一条消息是否已经超过 6 小时（没记录按「没有沉默」处理）。"""
    return silence_hours(profile_last_seen(plugin, profile), now) >= SILENCE_BLOCK_HOURS


def current_scene(plugin):
    """唯一场景事实：读 scene_ledger（2026-11-22 task-17）。

    scene_ledger 会做硬约束钳制（病假/事假→家或医院、出差→限定 zone）并给出
    quality；拿不到账本时退回旧的 state.current_activity() 读法，保证不炸。
    """
    try:
        try:
            from .amo_scene_ledger import scene_at as _scene_at
        except ImportError:
            from amo_scene_ledger import scene_at as _scene_at
        led = _scene_at(plugin) or {}
        if isinstance(led, dict) and (led.get("place") or led.get("zone")):
            return {
                "place": str(led.get("place") or ""),
                "zone": str(led.get("zone") or ""),
                "activity": str(led.get("activity") or ""),
                "category": str(led.get("category") or ""),
                "start": str(led.get("start") or ""),
                "end": str(led.get("end") or ""),
                "event_id": str(led.get("event_id") or ""),
                "quality": str(led.get("quality") or ""),
                "together": bool(led.get("together")),
            }
    except Exception:  # cleanup: 账本不可用时退回旧读法
        pass
    try:
        scene = dict(plugin.state.current_activity() or {})
    except Exception:
        scene = {}
    place = str(scene.get("place") or "")
    zone = str(scene.get("zone") or "").strip()
    if not zone and place:
        try:
            try:
                from .photo_rules import zone_for_place
            except ImportError:
                from photo_rules import zone_for_place
            zone = str(zone_for_place(place) or "")
        except Exception:
            zone = ""
    event_id = str(scene.get("event_id") or scene.get("id") or "").strip()
    return {
        "place": place,
        "zone": zone,
        "activity": str(scene.get("activity") or ""),
        "category": str(scene.get("category") or ""),
        "start": str(scene.get("start") or ""),
        "end": str(scene.get("end") or ""),
        "event_id": event_id,
        "quality": "",
        "together": False,
    }


def _scene_text(scene):
    scene = scene if isinstance(scene, dict) else {}
    return str(scene.get("place") or "") + str(scene.get("activity") or "")


def at_home(scene):
    """人在不在家：zone=home 直接算；zone 缺失时看地点/活动词。"""
    scene = scene if isinstance(scene, dict) else {}
    zone = str(scene.get("zone") or "").strip()
    if zone == "home":
        return True
    if zone and zone not in ("unknown",):
        return False
    text = _scene_text(scene)
    return any(word in text for word in HOME_WORDS)


def _in_shop(scene):
    if at_home(scene):
        return False
    scene = scene if isinstance(scene, dict) else {}
    if str(scene.get("zone") or "") in SHOP_ZONES:
        return True
    return any(word in _scene_text(scene) for word in SHOP_WORDS)


def can_shop(plugin):
    """只有人真的在商场/街上/景区（或活动明确写着逛街/试衣）才允许逛街。"""
    return _in_shop(current_scene(plugin))


def shopping_scene_stale(plugin, record, now=None):
    """逛街事实的 scene_id 还是不是当前这个场景实例（task-30 消费方校验）。

    老记录没有 scene_id（兼容旧库）→ 不算过期，只看时间窗口；有 scene_id 时：
    - 账本能给出版本 → 必须相等，否则 14:00 的「在商场试衣」不能算到 21:00；
    - 账本给不出版本（unknown/weak）→ 退回 zone 对照，能对上就不误杀。
    """
    record = record if isinstance(record, dict) else {}
    saved = str(record.get("scene_id") or "")
    if not saved:
        return False
    try:
        scene = current_scene(plugin)
    except Exception:
        return False
    current = str(scene.get("event_id") or "")
    if current:
        return current != saved
    saved_zone = str(record.get("scene_zone") or "")
    current_zone = str(scene.get("zone") or "")
    if saved_zone and current_zone and current_zone != "unknown":
        return current_zone != saved_zone
    return False


def shopping_pending(plugin, now=None):
    """正在逛街试衣、等回复的事实（watch note_shopping_pending 写入，过期/换场景不算）。"""
    try:
        raw = plugin.db.kv_get(SHOPPING_PENDING_KEY, {})
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        return {}
    if not str(raw.get("line") or "").strip():
        return {}
    until = _num(raw.get("until"), 0.0)
    current = _num(now if now is not None else _now_ts(now), _now_ts(now))
    if until and until <= current:
        return {}
    # task-30：消费方校验 scene_id——场景实例已经换过，旧逛街事实不能再当成「现在」。
    if shopping_scene_stale(plugin, raw, current):
        return {}
    return dict(raw)


def can_selfie(plugin, profile=None, now=None):
    """沉默 6 小时或逛街 pending 但不在商场 → 不允许自拍。"""
    if is_silent(plugin, profile, now):
        return False
    if shopping_pending(plugin, now) and not can_shop(plugin):
        return False
    return True


def can_photo(plugin, profile=None, kind="", now=None):
    """自动照片闸门：沉默 6 小时 / 逛街 pending 但人不在商场 → 不发图。"""
    kind = str(kind or "")
    if is_silent(plugin, profile, now):
        return False
    if shopping_pending(plugin, now):
        return can_shop(plugin)
    if kind in ("shopping", "tryon"):
        return can_shop(plugin)
    return True


def can_proactive(plugin, profile=None, kind="", now=None, forced=False):
    """统一主动闸门：沉默 6 小时后禁催问/逛街/自拍/追问，只留问候与时间点提醒。"""
    kind = str(kind or "")
    if forced or kind in SILENCE_ALLOWED_KINDS or kind in SILENCE_CARE_KINDS:
        return True
    if is_silent(plugin, profile, now):
        return False
    if kind in ("shopping", "shop", "tryon"):
        return can_shop(plugin)
    if kind in ("selfie", "photo"):
        return can_selfie(plugin, profile, now)
    return True


def can_push(plugin, profile=None, kind="", now=None):
    """主动推送闸门（watch 逛街/守护推送走 push_to_owner）：沉默 6 小时不许推。"""
    kind = str(kind or "")
    if kind in PUSH_ALLOWED_KINDS:
        return True
    return not is_silent(plugin, profile, now)


def _fitting_scene(scene):
    """逛街 pending 且人在商场：场景必须是商场/试衣间，不能是家里客厅。"""
    try:
        try:
            from .photo_scene import fitting_room_scene
        except ImportError:
            from photo_scene import fitting_room_scene
        return str(fitting_room_scene(scene) or "")
    except Exception:
        return "商场的试衣间里，试衣镜、帘子和挂钩上的衣服，暖色灯光"


def photo_binding(plugin, profile=None, kind="", now=None, manual=False):
    """照片绑定当前事件：返回 {ok, reason, scene, event_id, shopping}。

    - 自动照片：沉默 6 小时 / 逛街 pending 但人不在商场 → ok=False；
    - 逛街 pending 且在商场 → scene 强制成商场/试衣间，避免家里自拍配商场文案；
    - event_id 供 claim_photo_event 做「同一事件最多一张图」。
    """
    scene = current_scene(plugin)
    result = {"ok": True, "reason": "", "scene": "", "event_id": scene.get("event_id") or "",
              "shopping": False}
    kind = str(kind or "")
    if manual:
        return result
    if is_silent(plugin, profile, now):
        result.update({"ok": False, "reason": "silence_6h"})
        return result
    pending = shopping_pending(plugin, now)
    if pending:
        result["shopping"] = True
        if not can_shop(plugin):
            result.update({"ok": False, "reason": "shopping_pending_offsite"})
            return result
        result["scene"] = _fitting_scene(scene)
        return result
    if kind in ("shopping", "tryon") and not can_shop(plugin):
        result.update({"ok": False, "reason": "shopping_kind_offsite"})
        return result
    return result


def claim_photo_event(plugin, event_id, now=None):
    """同一 event_id 最多一张图：占位成功返回 True，已经发过返回 False。

    没有 event_id（拿不到日程）时不做去重，避免把后续所有照片都误拦。
    """
    key = str(event_id or "").strip()
    if not key:
        return True
    cache_key = PHOTO_EVENT_KEY + key
    try:
        if plugin.db.kv_get(cache_key, False):
            return False
    except Exception:
        return True
    try:
        plugin.db.kv_set(cache_key, _num(now if now is not None else _now_ts(now), _now_ts(now)))
    except Exception:
        return True
    return True


__all__ = [
    "SILENCE_BLOCK_HOURS",
    "SILENCE_ALLOWED_KINDS",
    "SILENCE_CARE_KINDS",
    "ENGAGEMENT_KINDS",
    "SHOP_ZONES",
    "SHOP_WORDS",
    "SHOPPING_PENDING_KEY",
    "PHOTO_EVENT_KEY",
    "silence_hours",
    "profile_last_seen",
    "is_silent",
    "current_scene",
    "at_home",
    "can_shop",
    "shopping_pending",
    "shopping_scene_stale",
    "can_selfie",
    "can_photo",
    "can_proactive",
    "can_push",
    "photo_binding",
    "claim_photo_event",
]
