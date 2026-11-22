# -*- coding: utf-8 -*-
"""场景账本：唯一的「她此刻在哪/在做什么」读写入口（2026-11-22 task-17）。

为什么需要它：线上至少五套“此刻”事实（get_current_action / state.current_activity /
build_snapshot / amo_presence.current_scene / 约会 Scene），空档还会用 stale_slot
无限沿用旧块，于是出现「14:00 在商场 → 21:00 还在试衣服」「病假在家 → 23:00 在公司」。

本模块边界：
- read：scene_at(plugin, now) 返回规范化 SceneRecord（含 quality/constraints/version）；
  病假/事假/出差/约会在这里做硬约束钳制，任何读到的“她”都不会落在请假/出差不允许的地点。
- write：write(plugin, record) 是唯一写入口；违反硬约束直接拒绝并写审计。
- guard：scene_guard(text, scene) / guard_text(plugin, text) 给所有出口统一用：
  和当前 zone 冲突的地点词按 force_rewrite 改写；quality=unknown 时禁止具体地点词；
  约会 together 时禁止“一个人/独自”这类话。
- 照片：verify_photo_prompt / verify_photo_scene 给照片链路做发送前复核。

迁移进度（2026-11-22 task-30 更新）：
- presence / build_snapshot 已改读 scene_at；
- JIT（_scene_constrain_plan）、过渡（start/_ensure_blocks/finish）、日程唯一落库点
  （commit 锁内的 _constrain_scene_items）已改走 constrain/write 硬约束，
  schedule 与块日记不再拿到请假日的公司/通勤块；
- JIT 日记、过渡到达事实、里程碑/每日总结/记忆写入（guard_memory_text）加了场景守卫；
- photo_share 发送前调用 verify_photo_scene；
- task-30：老读者统一改读 amo_state_snapshot.snapshot_with_scene / scene_at
  （amo_message/amo_proactive/amo_schedule/amo_runtime_context/amo_runtime_reply/
  amo_commands/amo_api_schedule/social_learn_engine），state.snapshot() 只保留给
  心情/健康/生理期等非场景字段；
- task-30：current_action 写入口收敛——amo_current_action.set_action 先过 write
  再落库；微型事件/守护插件（amo_feature_api.set_current_action）与兜底分支都走账本；
- task-30：shopping_prompt / date_*（last_date_event/date_prep/date_incident）消费方
  按 scene_id 校验，旧场景实例的逛街/约会事实不再注入当前 prompt；
- 剩余已知旁路：amo_state_jit._jit_persist 直接 kv_set current_action（写入值已由
  _scene_constrain_plan 钳制）；micro_events.share_drive 仍读 state.snapshot() 的
  mood（非场景字段）；老库 current_action 没有 scene_id/quality（只做硬约束校验）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        import clock as _amo_clock

try:
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_observability import note as _note_swallowed

try:
    from .amo_state_snapshot import (
        NEGATION_MARKERS,
        ZONE_BLACKLIST,
        all_place_words,
        blacklist_hit,
        force_rewrite,
        strip_negated,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_state_snapshot import (
            NEGATION_MARKERS,
            ZONE_BLACKLIST,
            all_place_words,
            blacklist_hit,
            force_rewrite,
            strip_negated,
        )

AUDIT_KEY = "scene_ledger:audit"
AUDIT_LIMIT = 200
AUDIT_THROTTLE_SECONDS = 60.0

#: 场景质量，越靠前越可信
QUALITY_COVERING = "covering"          # 覆盖当前分钟的日程块
QUALITY_TRANSITION = "transition"      # 正在进行的过渡/通勤
QUALITY_JIT = "jit"                    # 到点即时决策（带 until）
QUALITY_CONTINUATION = "continuation"  # 距上一块结束 <=30 分钟的延续
QUALITY_WEAK = "weak"                  # 沿用昨天同小时/按小时兜底
QUALITY_UNKNOWN = "unknown"            # 空档超过延续上限：不许说具体地点

KNOWN_QUALITIES = (QUALITY_COVERING, QUALITY_TRANSITION, QUALITY_JIT,
                   QUALITY_CONTINUATION, QUALITY_WEAK, QUALITY_UNKNOWN)

#: 病假/事假允许出现的地点（硬约束）
LEAVE_ALLOWED_PLACES = ("家", "医院")
#: 出差允许的 zone（不出现“家”）
TRIP_ALLOWED_ZONES = ("hotel", "road", "station", "office", "street", "scenic",
                      "restaurant", "mall", "hospital", "unknown")

#: 场景词表：用于“单 zone 校验”和 unknown 时禁地点断言。
#: 只收场景名词，不收衣服/动作词（“居家裤”“睡衣”是衣服，不是地点）。
ZONE_WORDS = {
    "home": ("家里", "在家中", "客厅", "卧室", "厨房", "阳台", "卫生间", "洗手台",
             "沙发", "茶几", "抱枕", "被窝", "出租屋", "屋里", "餐桌旁", "床头"),
    "road": ("地铁", "车厢", "公交", "站台", "吊环", "扶手", "车窗", "路面",
             "安全带", "驾驶位", "出租车", "网约车", "车上", "隧道", "站牌"),
    "office": ("工位", "办公室", "办公桌", "会议室", "写字楼", "文件", "键盘",
               "显示器", "办公椅", "茶水间"),
    "mall": ("商场", "店铺", "橱窗", "扶梯", "专柜", "试衣间", "购物袋", "柜台"),
    "restaurant": ("餐厅", "小馆", "火锅", "食堂", "菜单", "碗筷", "餐盘", "餐桌"),
    "hospital": ("医院", "候诊", "诊室", "挂号", "消毒水", "叫号屏", "走廊"),
    "scenic": ("景区", "景点", "山顶", "海边", "沙滩", "公园", "湖边", "游乐园"),
    "hotel": ("酒店", "宾馆", "民宿", "客房", "行李", "房价"),
    "station": ("车站", "候车", "站台", "时刻表", "检票", "行李箱"),
}

_ZONE_ALIASES = {
    "home_bed": "home", "home_sofa": "home", "home_living": "home", "home_kitchen": "home",
    "home_bath": "home", "work": "office", "office": "office", "road": "road",
    "mall": "mall", "restaurant": "restaurant", "hospital": "hospital",
    "scenic": "scenic", "hotel": "hotel", "station": "station", "street": "street",
    "gym": "gym", "unknown": "unknown",
}

#: 同一场景的兼容 zone（照片 prompt 里同时出现不算多场景）
ZONE_COMPAT = {
    "road": {"station"},
    "station": {"road"},
    "street": {"road", "station", "mall"},
    "scenic": {"street"},
    "gym": set(),
}

TOGETHER_FORBIDDEN = ("一个人", "独自", "自己一个人", "孤零零", "单身一人")

_UNKNOWN_ACTIVITY = {
    "activity": "", "place": "", "zone": "unknown", "category": "rest",
    "_source": "gap_unknown", "_reason": "日程空档超过延续上限，地点未知",
}


# ---------------------------------------------------------------- 基础工具

def _now(now=None):
    return now or _amo_clock.now()


def _day_of(now):
    try:
        return now.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _state_of(plugin):
    """HumanState 或 plugin 都能传：HumanState 自己就是 state。"""
    state = getattr(plugin, "state", None)
    if state is not None:
        return state
    return plugin


def _db_of(plugin):
    db = getattr(plugin, "db", None)
    if db is not None:
        return db
    p = getattr(plugin, "p", None)
    return getattr(p, "db", None)


def _kv_get(plugin, key, default=None):
    db = _db_of(plugin)
    getter = getattr(db, "kv_get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:
        return default


def _kv_set(plugin, key, value):
    db = _db_of(plugin)
    setter = getattr(db, "kv_set", None)
    if not callable(setter):
        return False
    try:
        setter(key, value)
        return True
    except Exception:
        return False


def normalize_zone(zone, place=""):
    text = str(zone or "").strip()
    if text in _ZONE_ALIASES:
        return _ZONE_ALIASES[text]
    if text.startswith("home"):
        return "home"
    # 规则表里的复合 key（road_subway/home_bed/work_desk…）按第一段归组
    base = text.split("_")[0]
    if base in _ZONE_ALIASES:
        return _ZONE_ALIASES[base]
    if text:
        return text
    try:
        from .photo_scene import zone_for_place as _zone_for_place
    except ImportError:
        try:
            from photo_scene import zone_for_place as _zone_for_place
        except Exception:
            _zone_for_place = None
    if _zone_for_place is not None and place:
        try:
            value = str(_zone_for_place(place) or "")
            return _ZONE_ALIASES.get(value, value)
        except Exception:
            return ""
    return ""


# ---------------------------------------------------------------- 硬约束

def _call_state(plugin, name, *args, **kwargs):
    state = _state_of(plugin)
    fn = getattr(state, name, None)
    if not callable(fn):
        return None
    try:
        return fn(*args, **kwargs)
    except TypeError:
        try:
            return fn()
        except Exception:
            return None
    except Exception:
        return None


def _leave_marker(plugin, day):
    """事假事实：watch 请假链路只留下 mood_applied:leave:<day>:... 一类键。"""
    db = _db_of(plugin)
    keys = getattr(db, "kv_keys", None)
    prefix = "mood_applied:leave:%s" % str(day or "")
    if callable(keys):
        try:
            return bool(list(keys(prefix=prefix, limit=5) or []))
        except Exception:  # cleanup: 兜底/降级，保持原行为
            pass
    for key in ("leave:%s" % day, "leave_day:%s" % day, "day_leave:%s" % day):
        if _kv_get(plugin, key):
            return True
    return False


def _trip_kind(plugin, day):
    value = _call_state(plugin, "_trip_day_kind", day)
    return str(value or "")


def is_leave_day(plugin, day=None):
    """病假或事假：全天只允许家/医院。"""
    day = str(day or _day_of(_now()))
    return bool(is_sick_leave(plugin, day) or _leave_marker(plugin, day))


def is_sick_leave(plugin, day=None):
    day = str(day or _day_of(_now()))
    on_leave = _call_state(plugin, "on_sick_leave", day)
    if on_leave is None:
        decision = _call_state(plugin, "sick_decision", day)
        if isinstance(decision, dict):
            on_leave = str(decision.get("action") or "") == "take_sick_leave"
        else:
            on_leave = False
    try:
        return bool(on_leave)
    except Exception:
        return False


def _together(plugin):
    try:
        try:
            from .amo_date_session import together as _together_fn
        except ImportError:
            from amo_date_session import together as _together_fn
        return bool(_together_fn(plugin))
    except Exception:
        return False


def constraints(plugin, day=None, now=None):
    """一天之内的硬约束：请假→家/医院；出差→限定 zone；约会→together。"""
    now = _now(now)
    day = str(day or _day_of(now))
    out = {"day": day, "kind": "", "reason": "",
           "allowed_places": None, "allowed_zones": None,
           "together": False, "trip_kind": ""}
    if is_sick_leave(plugin, day):
        out.update({"kind": "sick_leave", "reason": "病假：只能在家或医院",
                    "allowed_places": set(LEAVE_ALLOWED_PLACES),
                    "allowed_zones": {"home", "hospital"}})
    elif _leave_marker(plugin, day):
        out.update({"kind": "leave", "reason": "事假：只能在家或医院",
                    "allowed_places": set(LEAVE_ALLOWED_PLACES),
                    "allowed_zones": {"home", "hospital"}})
    else:
        trip = _trip_kind(plugin, day)
        # "rest" 是旅游回来累瘫在家的那天：人已经到家，不能再按出差锁地点。
        if trip and trip != "rest":
            out.update({"kind": "trip", "trip_kind": trip,
                        "reason": "出差：地点必须在出差地相关 zone",
                        "allowed_zones": set(TRIP_ALLOWED_ZONES)})
    out["together"] = _together(plugin)
    if out["together"]:
        out["reason"] = (out["reason"] + "；约会中").strip("；")
    return out


def _violations(scene, cons):
    """一条场景是否违反硬约束；返回违反项（空表示合法）。"""
    bad = []
    if not isinstance(scene, dict) or not cons:
        return bad
    place = str(scene.get("place") or "").strip()
    zone = normalize_zone(scene.get("zone"), place)
    allowed_places = cons.get("allowed_places")
    if allowed_places is not None and place and place not in allowed_places:
        bad.append("place:%s" % place)
    allowed_zones = cons.get("allowed_zones")
    if allowed_zones is not None and zone and zone not in allowed_zones:
        bad.append("zone:%s" % zone)
    if str(cons.get("kind") or "") == "trip" and zone == "home":
        bad.append("zone:home")
    return bad


def _clamp_scene(plugin, scene, cons, now=None):
    """把违反硬约束的场景钳到允许集合；返回 (scene, clamped_from)。"""
    bad = _violations(scene, cons)
    if not bad:
        return scene, ""
    kind = str(cons.get("kind") or "")
    if kind in ("sick_leave", "leave"):
        clamped = {
            "activity": "请假在家休息" if kind == "sick_leave" else "请假在家",
            "place": "家", "zone": "home", "category": "rest",
            "event_id": str(scene.get("event_id") or ""),
            "start": str(scene.get("start") or ""), "end": str(scene.get("end") or ""),
            "source": "leave_constraint", "quality": QUALITY_COVERING,
            "constraints": [kind], "clamped_from": dict(scene),
        }
        _audit(plugin, {"ts": _now(now).timestamp(), "action": "read_clamp",
                        "kind": kind, "from": {k: scene.get(k) for k in ("place", "zone", "activity")},
                        "to": {"place": "家", "zone": "home"}, "violations": bad}, throttle=True)
        return clamped, ",".join(bad)
    if kind == "trip":
        clamped = dict(scene)
        clamped.update({"place": "酒店", "zone": "hotel", "source": "trip_constraint",
                        "quality": QUALITY_WEAK, "constraints": ["trip"],
                        "clamped_from": dict(scene)})
        _audit(plugin, {"ts": _now(now).timestamp(), "action": "read_clamp",
                        "kind": kind, "from": {k: scene.get(k) for k in ("place", "zone", "activity")},
                        "to": {"place": "酒店", "zone": "hotel"}, "violations": bad}, throttle=True)
        return clamped, ",".join(bad)
    return scene, ""


# ---------------------------------------------------------------- 审计

def _audit(plugin, entry, throttle=False):
    """写审计：write() 全量；read 钳制按 60 秒节流，避免热路径写库。"""
    entry = dict(entry or {})
    entry.setdefault("ts", _now().timestamp())
    if throttle:
        last = getattr(plugin, "_scene_ledger_audit_at", None)
        try:
            current = float(entry["ts"])
        except Exception:
            current = 0.0
        if last is not None and current - float(last or 0) < AUDIT_THROTTLE_SECONDS:
            return False
        try:
            plugin._scene_ledger_audit_at = current
        except Exception:  # cleanup: 兜底/降级，保持原行为
            pass
    rows = _kv_get(plugin, AUDIT_KEY, [])
    if not isinstance(rows, list):
        rows = []
    rows.append(entry)
    return _kv_set(plugin, AUDIT_KEY, rows[-AUDIT_LIMIT:])


def audit_entries(plugin, limit=20):
    rows = _kv_get(plugin, AUDIT_KEY, [])
    if not isinstance(rows, list):
        return []
    try:
        limit = max(1, int(limit))
    except Exception:
        limit = 20
    return rows[-limit:]


# ---------------------------------------------------------------- read

def _quality_of(reason, activity=None):
    reason = str(reason or "")
    if reason in ("schedule", "schedule_conflict"):
        return QUALITY_COVERING
    if reason in ("transit", "transition"):
        return QUALITY_TRANSITION
    if reason == "jit":
        return QUALITY_JIT
    if reason == "stale_slot":
        return QUALITY_CONTINUATION
    if reason == "gap_unknown":
        return QUALITY_UNKNOWN
    if reason in ("yesterday_same_hour", "no_schedule"):
        return QUALITY_WEAK
    activity = activity if isinstance(activity, dict) else {}
    if activity.get("start") and activity.get("end"):
        return QUALITY_COVERING
    return QUALITY_WEAK


def unknown_scene(now=None, reason="日程空档，地点未知"):
    now = _now(now)
    data = dict(_UNKNOWN_ACTIVITY)
    data.update({"ts": now.timestamp(), "day": _day_of(now),
                 "quality": QUALITY_UNKNOWN, "reason": reason, "constraints": []})
    return data


def scene_from_activity(activity, *, quality=QUALITY_COVERING, source="", version=0,
                        constraints_list=None):
    """把日程块/过渡块规范成 SceneRecord（纯函数，不读库）。"""
    activity = dict(activity) if isinstance(activity, dict) else {}
    place = str(activity.get("place") or "").strip()
    zone = normalize_zone(activity.get("zone"), place)
    return {
        "activity": str(activity.get("activity") or "").strip(),
        "place": place,
        "zone": zone or "unknown",
        "category": str(activity.get("category") or "life"),
        "event_id": str(activity.get("event_id") or activity.get("id") or ""),
        "start": str(activity.get("start") or ""),
        "end": str(activity.get("end") or ""),
        "source": str(source or activity.get("_source") or "schedule"),
        "reason": str(activity.get("_reason") or ""),
        "quality": str(quality or QUALITY_COVERING),
        "version": int(version or 0),
        "constraints": list(constraints_list or []),
        "together": False,
    }


def _schedule_version(plugin, day):
    value = _kv_get(plugin, "schedule_ver:%s" % day, 0)
    try:
        return int(value or 0)
    except Exception:
        return 0


def _read_activity(plugin):
    """state.current_activity_info() 优先；老对象退回 current_activity()。"""
    state = _state_of(plugin)
    fn = getattr(state, "current_activity_info", None)
    if callable(fn):
        try:
            result = fn()
            if isinstance(result, tuple) and len(result) >= 2:
                return dict(result[0] or {}), str(result[1] or "")
            if isinstance(result, dict):
                return dict(result), str(result.get("_source") or "")
        except Exception as exc:
            _note_swallowed("amo_scene_ledger.read_info", exc)
    fn = getattr(state, "current_activity", None)
    if callable(fn):
        try:
            data = fn()
            if isinstance(data, dict):
                return dict(data), str(data.get("_source") or "")
        except Exception as exc:
            _note_swallowed("amo_scene_ledger.read_activity", exc)
    return {}, "gap_unknown"


def scene_at(plugin, now=None):
    """唯一读入口：返回规范化 SceneRecord（含 quality/constraints/version）。"""
    now = _now(now)
    day = _day_of(now)
    activity, reason = _read_activity(plugin)
    cons = constraints(plugin, day, now)
    quality = _quality_of(reason, activity)
    if quality == QUALITY_UNKNOWN or not activity or not (activity.get("place") or activity.get("zone")):
        scene = unknown_scene(now, "日程空档，地点未知")
    else:
        scene = scene_from_activity(activity, quality=quality, source=reason,
                                    version=_schedule_version(plugin, day))
    scene["ts"] = now.timestamp()
    scene["day"] = day
    scene["constraints"] = [cons.get("kind")] if cons.get("kind") else []
    scene["together"] = bool(cons.get("together"))
    scene, clamped_from = _clamp_scene(plugin, scene, cons, now)
    if clamped_from:
        scene.setdefault("clamped_from", {})["reason"] = clamped_from
    return scene


# ---------------------------------------------------------------- write

def write(plugin, record, *, source="", reason="", now=None):
    """唯一写入口：硬约束校验 + 审计；返回 {ok, record, violations}。"""
    now = _now(now)
    record = record if isinstance(record, dict) else {}
    day = str(record.get("day") or _day_of(now))
    cons = constraints(plugin, day, now)
    scene = scene_from_activity(record, quality=record.get("quality") or QUALITY_COVERING,
                                source=source or record.get("source") or "manual",
                                version=record.get("version") or _schedule_version(plugin, day))
    scene["day"] = day
    scene["ts"] = now.timestamp()
    scene["constraints"] = [cons.get("kind")] if cons.get("kind") else []
    scene["together"] = bool(cons.get("together"))
    bad = _violations(scene, cons)
    entry = {"ts": now.timestamp(), "action": "write", "ok": not bad,
             "source": scene["source"], "reason": str(reason or ""),
             "scene": {k: scene.get(k) for k in ("activity", "place", "zone", "quality")},
             "violations": bad, "constraint": cons.get("kind") or ""}
    if bad:
        scene["quality"] = QUALITY_UNKNOWN
        scene["place"] = ""
        scene["zone"] = "unknown"
    _audit(plugin, entry)
    return {"ok": not bad, "record": scene, "violations": bad}


# ---------------------------------------------------------------- 文本守卫

def _unknown_place_words():
    words = set()
    try:
        words.update(all_place_words())
    except Exception:
        for group in ZONE_BLACKLIST.values():
            words.update(group)
    for group in ZONE_WORDS.values():
        words.update(group)
    return sorted(words)


def _drop_conflicting_clauses(text, banned):
    """按分句删掉含冲突词（非否定用法）的分句；返回 (新文本, 命中词)。"""
    import re
    clauses = re.split(r"(?<=[，,。！!？?；;\n])", str(text or ""))
    kept = []
    hits = []
    for clause in clauses:
        body = strip_negated(clause, banned)
        found = [word for word in banned if word in body]
        if not found:
            kept.append(clause)
            continue
        for word in found:
            if word not in hits:
                hits.append(word)
    return "".join(kept).strip(), hits


def scene_guard(text, scene, plugin=None):
    """统一出口守卫：地点冲突改写 / unknown 禁地点 / 约会 together 禁“一个人”。"""
    value = str(text or "")
    if not value:
        return {"text": value, "hits": [], "changed": False, "reason": ""}
    scene = scene if isinstance(scene, dict) else {}
    quality = str(scene.get("quality") or QUALITY_WEAK)
    zone = normalize_zone(scene.get("zone"), scene.get("place"))
    hits = []
    reason = ""
    if quality == QUALITY_UNKNOWN or zone in ("", "unknown"):
        words = _unknown_place_words()
        fixed, hits = _drop_conflicting_clauses(value, words)
        if hits:
            value = fixed or "我这会儿没细想在哪，先忙别的。"
            reason = "unknown_place"
    else:
        hits = list(blacklist_hit(value, zone))
        if hits:
            value = force_rewrite(value, zone)
            reason = "zone_conflict"
    if scene.get("together"):
        together_hits = [word for word in TOGETHER_FORBIDDEN if word in value]
        if together_hits:
            fixed, _ = _drop_conflicting_clauses(value, together_hits)
            value = fixed or "我在呢。"
            hits.extend(together_hits)
            reason = reason or "together_conflict"
    return {"text": value, "hits": hits, "changed": value != str(text or ""), "reason": reason}


def guard_text(plugin, text, now=None):
    return scene_guard(text, scene_at(plugin, now), plugin)["text"]


def detect_zones(text):
    """文本里出现的场景组（否定用法不算）；用于照片 prompt 单 zone 校验。"""
    body = strip_negated(str(text or ""), _unknown_place_words())
    found = set()
    for zone, words in ZONE_WORDS.items():
        if any(word in body for word in words):
            found.add(zone)
    return found


def sanitize_prompt_zone(prompt, primary_zone):
    """照片 prompt 只保留一个 zone：非主场景且含具体地点词的分句删掉。

    - primary_zone 为 home 时，删“地铁车厢/商场/工位”等外景分句；
    - primary_zone 为 road/office/mall/restaurant 时，删“客厅/沙发/被窝/床”等居家分句；
    - 否定句（“不要地铁/不是沙发”）不算命中，保留。
    """
    primary = normalize_zone(primary_zone)
    if not primary or primary == "unknown":
        return prompt
    compatible = {primary} | set(ZONE_COMPAT.get(primary, set()))
    other = [zone for zone in ZONE_WORDS if zone not in compatible]
    banned = []
    for zone in other:
        banned.extend(word for word in ZONE_WORDS[zone] if word not in ZONE_WORDS.get(primary, ()))
    if not banned:
        return prompt
    fixed, _hits = _drop_conflicting_clauses(prompt, banned)
    return fixed or prompt


def verify_photo_prompt(prompt, scene):
    """照片发送前复核：prompt 里只能出现当前场景 zone 的地点词。"""
    scene = scene if isinstance(scene, dict) else {}
    primary = normalize_zone(scene.get("zone"), scene.get("place"))
    zones = detect_zones(prompt)
    expected = {primary} if primary not in ("", "unknown") else set()
    expected |= set(ZONE_COMPAT.get(primary, set())) if expected else set()
    extra = sorted(zone for zone in zones if zone not in expected)
    return {"ok": not extra, "primary": primary, "zones": sorted(zones), "extra_zones": extra}


def verify_photo_scene(plugin, expected_zone="", expected_event_id="", now=None):
    """发送前复核（给 photo_share 接线用）：场景在生图期间是否变了。"""
    scene = scene_at(plugin, now)
    current_zone = normalize_zone(scene.get("zone"), scene.get("place"))
    expected = normalize_zone(expected_zone)
    reasons = []
    if expected and current_zone != expected:
        reasons.append("zone_changed:%s->%s" % (expected, current_zone))
    current_event = str(scene.get("event_id") or "")
    if expected_event_id and current_event and expected_event_id != current_event:
        reasons.append("event_changed")
    return {"ok": not reasons, "scene": scene, "reasons": reasons}


def constrain_activity(plugin, activity, day=None, now=None):
    """给日程/过渡/JIT 读侧用的钳制：请假/出差期间非法地点改成允许场景。"""
    activity = dict(activity) if isinstance(activity, dict) else {}
    if not activity:
        return activity
    now = _now(now)
    day = str(day or _day_of(now))
    cons = constraints(plugin, day, now)
    scene = scene_from_activity(activity, quality=_quality_of(activity.get("_source"), activity))
    scene["day"] = day
    clamped, _ = _clamp_scene(plugin, scene, cons, now)
    if not clamped.get("clamped_from"):
        return activity
    out = dict(activity)
    out.update({"activity": clamped.get("activity") or activity.get("activity"),
                "place": clamped.get("place") or activity.get("place"),
                "zone": clamped.get("zone") or activity.get("zone"),
                "category": clamped.get("category") or activity.get("category") or "rest",
                "_source": "leave_constraint" if cons.get("kind") in ("sick_leave", "leave") else "trip_constraint",
                "_reason": cons.get("reason") or "硬约束钳制"})
    return out


# ---------------------------------------------------------------- 记忆/摘要写者守卫

#: 只有带这些「现在/此刻」断言的句子才按当前场景改写。过去事实（昨天/以前/他…）
#: 不能被当前场景改写，否则历史记忆会被改错（里程碑/每日总结就是写过去的）。
PRESENT_CLAIM_MARKERS = ("现在", "此刻", "这会儿", "目前", "刚刚", "刚才",
                         "正在", "眼下")


def _zone_banned(zone):
    """当前 zone 不能出现的地点词（记忆守卫用；只收黑名单，不收动作词）。"""
    return [word for word in ZONE_BLACKLIST.get(str(zone or ""), ()) if word]


def guard_current_claims(plugin, text, now=None):
    """记忆/摘要/里程碑写者用：只改写「现在/此刻」分句，过去事实原样保留。

    - 带现在断言的句子命中当前 zone 黑名单 → 按当前场景确定性改写；
    - 改写后仍冲突（或说不清）就整句丢掉——宁可不写，也不把错地点写进长期记忆；
    - 句子里提到「他/用户」的是在说对方，不是她此刻的位置，不动。
    返回 {text, hits, changed, dropped}；text 可能为空串（调用方决定是否放弃这条）。
    """
    value = str(text or "")
    result = {"text": value, "hits": [], "changed": False, "dropped": 0}
    if not value:
        return result
    scene = scene_at(plugin, now)
    zone = normalize_zone(scene.get("zone"), scene.get("place"))
    if zone in ("", "unknown"):
        return result
    banned = _zone_banned(zone)
    if not banned:
        return result
    import re
    clauses = re.split(r"(?<=[，,。！!？?；;\n])", value)
    kept = []
    hits = []
    dropped = 0
    for clause in clauses:
        if "他" in clause or "用户" in clause:
            kept.append(clause)  # 说他的事，不是她此刻在哪
            continue
        body = strip_negated(clause, banned)
        found = [word for word in banned if word in body]
        if found and any(marker in clause for marker in PRESENT_CLAIM_MARKERS):
            fixed = force_rewrite(clause, zone)
            if blacklist_hit(fixed, zone) or not str(fixed).strip():
                dropped += 1
            else:
                kept.append(fixed)
            for word in found:
                if word not in hits:
                    hits.append(word)
            continue
        kept.append(clause)
    out = "".join(kept).strip()
    result.update({"text": out, "hits": hits, "changed": out != value, "dropped": dropped})
    return result


def guard_memory_text(plugin, text, now=None):
    """记忆/摘要/里程碑写者统一调用：返回可落库文本（空串=这条不要写）。"""
    result = guard_current_claims(plugin, text, now)
    if result.get("changed"):
        _audit(plugin, {"ts": _now(now).timestamp(), "action": "memory_guard",
                        "from": str(text or "")[:120], "to": str(result.get("text") or "")[:120],
                        "hits": result.get("hits") or [],
                        "dropped": result.get("dropped") or 0})
    return str(result.get("text") or "")


__all__ = [
    "QUALITY_COVERING", "QUALITY_TRANSITION", "QUALITY_JIT", "QUALITY_CONTINUATION",
    "QUALITY_WEAK", "QUALITY_UNKNOWN", "LEAVE_ALLOWED_PLACES", "TRIP_ALLOWED_ZONES",
    "ZONE_WORDS", "AUDIT_KEY",
    "normalize_zone", "constraints", "is_leave_day", "is_sick_leave",
    "scene_from_activity", "scene_at", "unknown_scene", "write",
    "scene_guard", "guard_text", "strip_negated", "detect_zones",
    "sanitize_prompt_zone", "verify_photo_prompt", "verify_photo_scene",
    "constrain_activity", "audit_entries",
    "PRESENT_CLAIM_MARKERS", "guard_current_claims", "guard_memory_text",
]
