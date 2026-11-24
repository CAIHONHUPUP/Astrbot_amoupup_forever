# -*- coding: utf-8 -*-
"""居住关系与在场场景（2026-11-20）。

单一真相：``scene_at(plugin, day, start, end, place)`` 返回一个 Scene，
回答「这一块时间、这个地点，他能不能在场、为什么」。

三档行为（展示层早期叫 L0-L3，现在统一按三档命名）：

- ``apart``     同城分居：默认档，行为与 7.5.9 完全一致。
- ``part_time`` 半同居：周五 20:00 ~ 周日 22:00 他在这边；工作日各住各的。
- ``together``  正式同居：在家且他不在上班时在场；在外面仍按约会算。

设计约束：

- 纯函数优先：``scene_at`` 不写库、不调模型；只有 load/save/transition 碰 kv。
- 按块时间求值：日程生成写的是「明天 10:00」，不能用「现在是 23:00」判断。
- 文案 override：apart 档一律返回空串，调用方回落到 7.5.9 的原文，
  保证黄金快照零漂移。
"""
from __future__ import annotations

try:  # 吞异常留痕（2026-11-24）
    from .amo_observability import note as _note_swallowed
except ImportError:
    from amo_observability import note as _note_swallowed

import re
from dataclasses import dataclass
from datetime import date as _date
from typing import Any, TYPE_CHECKING

STAGE_APART = "apart"
STAGE_PART_TIME = "part_time"
STAGE_TOGETHER = "together"
STAGES = (STAGE_APART, STAGE_PART_TIME, STAGE_TOGETHER)
DEFAULT_STAGE = STAGE_APART

PRESENCE_ALONE = "alone"
PRESENCE_DATE = "date"
PRESENCE_HOME = "home"

STAGE_LABELS = {
    STAGE_APART: "同城分居",
    STAGE_PART_TIME: "半同居",
    STAGE_TOGETHER: "同居",
}

#: 半同居窗口：周五 20:00 ~ 周日 22:00
PART_TIME_FRI_HOUR = 20
PART_TIME_SUN_HOUR = 22

#: 他上班的默认时段（工作日）；同居时他在上班就不在家。
PARTNER_WORK_START = 9 * 60
PARTNER_WORK_END = 18 * 60 + 30

#: L1 -> L2 默认过夜天数
DEFAULT_TO_TOGETHER_NIGHTS = 30

HOME_ZONE = "home"


@dataclass(frozen=True)
class Scene:
    """一块时间/地点的在场判定结果。"""

    stage: str
    day: str
    start: int
    end: int
    place: str
    zone: str
    presence: str
    partner: bool
    partner_place: str
    reason: str

    @property
    def home(self) -> bool:
        return self.zone == HOME_ZONE


# ---------------------------------------------------------------- 配置/开关


def _cfg_bool(plugin, key: str, default: bool) -> bool:
    try:
        fn = getattr(plugin, "cfg_bool", None)
        if callable(fn):
            return bool(fn(key, default))
    except Exception:  # cleanup: 读不到就用默认
        # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
        pass
    return default


def _cfg_str(plugin, key: str, default: str) -> str:
    try:
        fn = getattr(plugin, "cfg_str", None)
        if callable(fn):
            return str(fn(key, default))
    except Exception:  # cleanup: 读不到就用默认
        # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
        pass
    return default


def _cfg_int(plugin, key: str, default: int) -> int:
    try:
        fn = getattr(plugin, "cfg_int", None)
        if callable(fn):
            return int(fn(key, default))
    except Exception:  # cleanup: 读不到就用默认
        # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
        pass
    try:
        return int(_cfg_str(plugin, key, str(default)) or default)
    except Exception:
        return default


def enabled(plugin) -> bool:
    return _cfg_bool(plugin, "household_enabled", True)


def to_together_nights(plugin) -> int:
    try:
        return max(1, _cfg_int(plugin, "household_to_together_nights", DEFAULT_TO_TOGETHER_NIGHTS))
    except Exception:
        return DEFAULT_TO_TOGETHER_NIGHTS


# ---------------------------------------------------------------- 状态读写


def blank() -> dict:
    return {
        "v": 2,
        "stage": DEFAULT_STAGE,
        "since": 0.0,
        "night_count": 0,
        "last_night_day": "",
        "ready": False,
        "history": [],
    }


def normalize(data) -> dict:
    out = blank()
    if not isinstance(data, dict):
        return out
    stage = str(data.get("stage") or "")
    out["stage"] = stage if stage in STAGES else DEFAULT_STAGE
    try:
        out["since"] = float(data.get("since") or 0.0)
    except Exception:
        out["since"] = 0.0
    try:
        out["night_count"] = max(0, int(data.get("night_count") or 0))
    except Exception:
        out["night_count"] = 0
    out["last_night_day"] = str(data.get("last_night_day") or "")
    out["ready"] = bool(data.get("ready"))
    hist = data.get("history")
    if isinstance(hist, list):
        out["history"] = [dict(h) for h in hist if isinstance(h, dict)][-30:]
    return out


def load(plugin) -> dict:
    """读居住状态；总开关关闭时强制 apart（数据保留）。"""
    if not enabled(plugin):
        return blank()
    try:
        raw = plugin.db.kv_get("household", {}) or {}
    except Exception:
        raw = {}
    data = normalize(raw)
    # kv 为空时允许用配置项设定初始档位（便于首次上线/回滚）。
    if not raw:
        cfg_stage = _cfg_str(plugin, "household_stage", DEFAULT_STAGE)
        if cfg_stage in STAGES:
            data["stage"] = cfg_stage
    return data


def save(plugin, data) -> dict:
    data = normalize(data)
    try:
        plugin.db.kv_set("household", data)
    except Exception as exc:  # cleanup: 读不到就用默认
        _note_swallowed("amo_household.save", exc)
    return data


def stage_of(plugin) -> str:
    return load(plugin)["stage"]


# ---------------------------------------------------------------- 出差/不在家


def away_state(plugin) -> dict:
    """他是不是出差/不在（同居/半同居下覆盖"在家"判定）；支持到期自动回家。"""
    try:
        raw = plugin.db.kv_get("partner_away", {}) or {}
    except Exception:  # cleanup: 读不到就是不在出差
        raw = {}
    if not isinstance(raw, dict):
        return {"away": False, "reason": "", "since": 0.0, "until": 0.0}
    away = bool(raw.get("away"))
    until = float(raw.get("until") or 0.0)
    if away and until:
        try:
            from .amo_shared import now_ts as _now_ts
        except ImportError:
            from amo_shared import now_ts as _now_ts
        if float(_now_ts()) >= until:
            away = False   # 到期自动回家
    return {"away": away, "reason": str(raw.get("reason") or ""),
            "since": float(raw.get("since") or 0.0), "until": until}


def set_away(plugin, away: bool, reason: str = "", days: int = 0) -> dict:
    """手动标记他出差/回家：/居住 出差 [天数]、/居住 回家。"""
    try:
        from .amo_shared import now_ts as _now_ts
    except ImportError:
        from amo_shared import now_ts as _now_ts
    now = float(_now_ts())
    until = 0.0
    if away and int(days or 0) > 0:
        until = now + int(days) * 86400
    data = {"away": bool(away), "reason": str(reason or ""), "since": now, "until": until}
    try:
        plugin.db.kv_set("partner_away", data)
    except Exception as exc:  # cleanup: 写不进去就返回内存结果
        _note_swallowed("amo_household.set_away", exc)
    return data


# ---------------------------------------------------------------- 时间/地点


def _minutes_of(text, default: int = 0) -> int:
    """'HH:MM' / int / 空 -> 分钟。"""
    if isinstance(text, int):
        return text
    raw = str(text or "").strip()
    if not raw:
        return default
    try:
        hour, minute = raw.split(":")[:2]
        return int(hour) * 60 + int(minute)
    except Exception:
        return default


def _day_of(day) -> _date | None:
    try:
        return _date.fromisoformat(str(day or "")[:10])
    except Exception:
        return None


def _weekday(day) -> int:
    d = _day_of(day)
    return d.weekday() if d is not None else 0


def zone_of(place, zone="") -> str:
    """地点 -> zone；优先复用 photo_rules 的唯一实现。"""
    raw = str(zone or "").strip()
    text = str(place or "").strip()
    if raw:
        if raw == "road" and text:
            try:
                try:
                    from .photo_rules import resolve_zone
                except ImportError:
                    from photo_rules import resolve_zone
                fixed = str(resolve_zone(text, "", raw) or "")
                if fixed and fixed != "road":
                    return fixed
            except Exception:
                return raw
        return raw
    if not text:
        return HOME_ZONE
    try:
        try:
            from .photo_rules import resolve_zone
        except ImportError:
            from photo_rules import resolve_zone
        return str(resolve_zone(text, "", HOME_ZONE) or HOME_ZONE)
    except Exception:
        return HOME_ZONE if any(w in text for w in ("家", "卧室", "客厅", "床", "厨房", "出租屋")) else ""


def is_home(place, zone="") -> bool:
    return zone_of(place, zone) == HOME_ZONE


def _partner_place(place, activity="") -> str:
    text = str(place or "") + " " + str(activity or "")
    if any(w in text for w in ("卧室", "床", "被窝", "睡")):
        return "卧室"
    if any(w in text for w in ("厨房", "做饭", "煮")):
        return "厨房"
    return "客厅"


def is_holiday(day) -> bool:
    """法定节假日（含可编辑日历）；读不到就当普通工作日。"""
    try:
        try:
            from .amo_shared_time import _holiday_name
        except ImportError:
            from amo_shared_time import _holiday_name
        return bool(_holiday_name(str(day or "")[:10]))
    except Exception:  # cleanup: 日历读不到按非假日
        return False


def partner_at_work(weekday: int, start: int, end: int, *, holiday: bool = False) -> bool:
    """同居时他是否在上班：工作日 09:00-18:30 的块；法定假日不算上班。"""
    if weekday > 4 or holiday:
        return False
    # 与上班时段有交集就算在上班；整块在上班时段内更典型。
    return start < PARTNER_WORK_END and end > PARTNER_WORK_START


def _part_time_window(weekday: int, start: int, end: int) -> bool:
    """半同居窗口：周五 20:00 后、周六全天、周日 22:00 前。"""
    if weekday == 4:
        return end > PART_TIME_FRI_HOUR * 60
    if weekday == 5:
        return True
    if weekday == 6:
        return start < PART_TIME_SUN_HOUR * 60
    return False


def scene_at(plugin, day, start, end, place, *, zone="", activity="",
             together_hint=None) -> Scene:
    """一块时间/地点的唯一在场判定。"""
    data = load(plugin)
    stage = data["stage"]
    start_min = _minutes_of(start)
    end_min = _minutes_of(end, start_min + 60)
    if end_min <= start_min:
        end_min = start_min + 60
    zone_text = zone_of(place, zone)
    home = zone_text == HOME_ZONE
    partner_place = _partner_place(place, activity)

    if together_hint is True:
        return Scene(stage, str(day), start_min, end_min, str(place or ""), zone_text,
                     PRESENCE_DATE, True, partner_place, "约会窗口")

    if stage == STAGE_APART or not home:
        reason = "分居档" if stage == STAGE_APART else "不在家"
        return Scene(stage, str(day), start_min, end_min, str(place or ""), zone_text,
                     PRESENCE_ALONE, False, partner_place, reason)

    _away = away_state(plugin)
    if _away["away"]:
        # 出差/回自己那边：即使同居，家里也只有她一个人（2026-11-21）
        return Scene(stage, str(day), start_min, end_min, str(place or ""), zone_text,
                     PRESENCE_ALONE, False, partner_place,
                     "他出差" if "出差" in _away["reason"] else "他不在家")

    weekday = _weekday(day)

    if stage == STAGE_PART_TIME:
        if _part_time_window(weekday, start_min, end_min):
            return Scene(stage, str(day), start_min, end_min, str(place or ""), zone_text,
                         PRESENCE_HOME, True, partner_place, "半同居周末窗口")
        return Scene(stage, str(day), start_min, end_min, str(place or ""), zone_text,
                     PRESENCE_ALONE, False, partner_place, "半同居·工作日")

    if stage == STAGE_TOGETHER:
        if partner_at_work(weekday, start_min, end_min, holiday=is_holiday(day)):
            return Scene(stage, str(day), start_min, end_min, str(place or ""), zone_text,
                         PRESENCE_ALONE, False, partner_place, "同居·他上班")
        return Scene(stage, str(day), start_min, end_min, str(place or ""), zone_text,
                     PRESENCE_HOME, True, partner_place, "同居·在家")

    return Scene(stage, str(day), start_min, end_min, str(place or ""), zone_text,
                 PRESENCE_ALONE, False, partner_place, "未知档位")


def _ledger_now(plugin, now=None) -> dict:
    """此刻的场景账本记录（唯一 owner）；读不到或空档返回 {}。"""
    try:
        try:
            from .amo_scene_ledger import QUALITY_UNKNOWN as _unknown
            from .amo_scene_ledger import scene_at as _ledger_at
        except ImportError:
            from amo_scene_ledger import QUALITY_UNKNOWN as _unknown
            from amo_scene_ledger import scene_at as _ledger_at
        record = _ledger_at(plugin, now)
    except Exception as exc:  # cleanup: 账本读不到就回落旧链路
        _note_swallowed("amo_household.scene_now.ledger", exc)
        return {}
    if not isinstance(record, dict):
        return {}
    if not (str(record.get("place") or "") or str(record.get("zone") or "")):
        return {}
    if str(record.get("quality") or "") == _unknown:
        return {}
    return record


def scene_now(plugin, now=None) -> Scene:
    """此刻的在场判定；聊天/主动消息用。

    2026-11-24 收口：场景账本 amo_scene_ledger.scene_at() 是唯一 owner
    （带病假/事假/出差/约会硬约束，地点会被钳到允许集合）；账本读不到或空档时
    才回落 amo_current_action.get_current_action()。
    """
    day = ""
    start = "00:00"
    end = "23:59"
    try:
        if now is not None:
            day = now.date().isoformat()
            start = "%02d:%02d" % (now.hour, now.minute)
            end = "%02d:%02d" % ((now.hour + 1) % 24, now.minute)
    except Exception as exc:  # cleanup: 读不到就用默认
        _note_swallowed("amo_household.scene_now", exc)
    if not day:
        try:
            from .amo_shared import today_str
        except ImportError:
            from amo_shared import today_str
        day = today_str()
    record = _ledger_now(plugin, now)
    if record:
        place = str(record.get("place") or "")
        zone = str(record.get("zone") or "")
        activity = str(record.get("activity") or "")
        together = bool(record.get("together"))
    else:
        try:
            try:
                from .amo_current_action import get_current_action
            except ImportError:
                from amo_current_action import get_current_action
            current = get_current_action(plugin, now) or {}
        except Exception:
            current = {}
        place = str(current.get("place") or "家")
        zone = str(current.get("zone") or "")
        activity = str(current.get("action") or "")
        together = False
        try:
            try:
                from . import amo_date_session as _ds
            except ImportError:
                import amo_date_session as _ds
            together = bool(_ds.together(plugin, now))
        except Exception:
            together = False
    return scene_at(plugin, day, start, end, place or "家",
                    zone=zone, activity=activity, together_hint=together)


# ---------------------------------------------------------------- 过夜计数


def count_night(plugin, day, scene: Scene) -> dict:
    """半同居每过一个「有他在」的夜晚 +1，达标置 ready。幂等：按天去重。"""
    data = load(plugin)
    if data["stage"] != STAGE_PART_TIME or not scene.partner:
        return data
    if scene.presence != PRESENCE_HOME:
        return data
    if not (scene.start >= 23 * 60 or scene.start < 8 * 60):
        return data  # 只数过夜（23:00 后或凌晨），22 点的追剧块不算
    key = str(day or "")
    if not key or data.get("last_night_day") == key:
        return data
    data["last_night_day"] = key
    data["night_count"] = int(data.get("night_count") or 0) + 1
    if data["night_count"] >= to_together_nights(plugin):
        data["ready"] = True
    return save(plugin, data)


# ---------------------------------------------------------------- 迁移


def can_transition(plugin, to_stage: str, *, force=False):
    if to_stage not in STAGES:
        return False, "未知档位 %s" % to_stage
    current = stage_of(plugin)
    if to_stage == current:
        return False, "已经是当前档位"
    if force:
        return True, "强制迁移"
    order = {STAGE_APART: 0, STAGE_PART_TIME: 1, STAGE_TOGETHER: 2}
    if order[to_stage] < order[current]:
        return False, "不支持降级（管理员可用 /搬回 强制）"
    if current == STAGE_APART and to_stage == STAGE_TOGETHER:
        return False, "请先 /同居 确认，或先用 /同居 半同居"
    if current == STAGE_PART_TIME and to_stage == STAGE_TOGETHER:
        data = load(plugin)
        if data.get("ready"):
            return True, "过夜天数已达标"
        need = to_together_nights(plugin) - int(data.get("night_count") or 0)
        return False, "还需过夜 %d 天" % max(0, need)
    return True, "可以迁移"


def transition(plugin, to_stage: str, *, force=False, reason="", source="command"):
    ok, msg = can_transition(plugin, to_stage, force=force)
    if not ok:
        return False, msg
    data = load(plugin)
    old = data["stage"]
    try:
        from .amo_shared import now_ts as _now_ts
    except ImportError:
        from amo_shared import now_ts as _now_ts
    data["stage"] = to_stage
    data["since"] = float(_now_ts())
    data["ready"] = False
    if to_stage != STAGE_PART_TIME:
        data["night_count"] = 0
    data["history"] = list(data.get("history") or []) + [{
        "at": data["since"], "from": old, "to": to_stage,
        "reason": str(reason or ""), "source": str(source or ""),
    }]
    save(plugin, data)
    # 世界观缓存失效：内存 + kv 一起清，否则 ensure_worldview 会拿旧内存继续用。
    try:
        plugin.db.kv_delete("worldview")
    except Exception as exc:  # cleanup: 读不到就用默认
        _note_swallowed("amo_household.transition", exc)
    try:
        if getattr(plugin, "state", None) is not None:
            plugin.state.worldview = {}
    except Exception as exc:  # cleanup: 清不掉就等 7 天自然过期
        _note_swallowed("amo_household.transition", exc)
    return True, "已切换为%s" % STAGE_LABELS.get(to_stage, to_stage)


async def transition_async(plugin, to_stage: str, *, force=False, reason="",
                           source="command"):
    old = stage_of(plugin)
    ok, msg = transition(plugin, to_stage, force=force, reason=reason, source=source)
    if not ok:
        return ok, msg
    text = {
        (STAGE_APART, STAGE_PART_TIME): "我们开始半同居了，周末他会过来住。",
        (STAGE_PART_TIME, STAGE_TOGETHER): "我们正式同居了，现在住在一起。",
        (STAGE_APART, STAGE_TOGETHER): "我们同居了，现在住在一起。",
    }.get((old, to_stage), "居住关系变了：" + msg)
    try:
        uid = str(getattr(plugin, "_last_uid", "") or "")
        if uid:
            await plugin._write_memory_checked(uid, "【居住】" + text, kind="fact", tier=1)
    except Exception as exc:  # cleanup: 读不到就用默认
        _note_swallowed("amo_household.transition_async", exc)
    return True, msg


# ---------------------------------------------------------------- 文案


def _apart_wording(key: str) -> str:
    """分居档文案：2026-11-24 起居住边界也在这里（其余键仍回落到调用方旧文案）。"""
    if key != "boundary":
        return ""
    try:
        try:
            from .amo_prompt_registry import RESIDENCE_APART_RULE
        except ImportError:
            from amo_prompt_registry import RESIDENCE_APART_RULE
        return RESIDENCE_APART_RULE
    except Exception as exc:  # cleanup: 取不到就回落旧文案
        _note_swallowed("amo_household.wording.apart", exc)
        return ""


def wording(key: str, scene: Scene | None = None, plugin=None) -> str:
    """按档位给 override；apart 的居住边界也由这里给，其余键回落到旧文案。

    只认档位：给了 scene 就用它的档位，否则用 stage_of(plugin)。system prompt 固定块
    不能每轮再算一遍 scene_now（缓存约束见 amo_llm.py 里 ACTION_OWNERSHIP_RULES 的注释）。
    """
    if scene is not None:
        stage = scene.stage
    elif plugin is not None:
        stage = stage_of(plugin)
    else:
        return ""
    if stage == STAGE_APART:
        return _apart_wording(key)

    _title = "老公"
    if plugin is not None:
        try:
            try:
                from . import amo_person_ref as _pr
            except ImportError:
                import amo_person_ref as _pr
            _title = _pr.title(plugin)
        except Exception:
            _title = "老公"
    _who = ("【他是谁·最高优先级】这里的「他」只能是你老公，也就是用户本人（称呼：%s）；"
            "不许出现第二个男性，不许写「有个男人」「神秘的他」。" % _title)
    table = {
        "boundary": {
            STAGE_PART_TIME: (
                "【动作归属·最高优先级】【半同居·物理边界】你们同城，她住出租屋，他周末会过来住"
                "（周五晚上到周日下午）。周五 20:00 后、周六、周日 22:00 前，他在她屋里，"
                "可以写「他在旁边翻身」「他抱着我」；工作日他回自己那边，只能手机联系。"
                "只有约会或这个周末窗口，才允许写面对面。" + _who
            ),
            STAGE_TOGETHER: (
                "【动作归属·最高优先级】【同居·物理边界】你们住在一起。在家时他就在屋里"
                "（客厅/厨房/卧室）；他上班/出差时不在，只能手机联系。"
                "在家可以写「他在客厅看电视」「他抱着我」；在外面仍按约会算，"
                "只有约会时段他才在你旁边。" + _who
            ),
        },
        "worldview": {
            STAGE_PART_TIME: (
                "设定：她住出租屋，他周末会过来住（周五晚到周日下午），"
                "工作日各住各的，平时靠 QQ/飞书/微信联系。"
            ),
            STAGE_TOGETHER: (
                "设定：他们住在一起，在家时他就在屋里；他上班/出差时不在，"
                "靠 QQ/飞书/微信联系。"
            ),
        },
        "schedule_boundary": {
            STAGE_PART_TIME: (
                "住法：周末住一起（周五晚到周日下午）；工作日各住各的，"
                "非周末、非约会不写他就在她身边。"
            ),
            STAGE_TOGETHER: (
                "住法：你们住在一起，在家时他就在屋里；他上班/出差时不在。"
            ),
        },
        "fact_pool": {
            STAGE_PART_TIME: "【物理事实】周末他在家；工作日你一个人，写他就写手机联系。",
            STAGE_TOGETHER: "【物理事实】你们住在一起；在家时他就在屋里，但只写素材里的具体事件。",
        },
        "date_ending": {
            STAGE_TOGETHER: "约会结束，你们一起回家。",
            STAGE_PART_TIME: "约会结束，他送你到楼下；周末他会留下。",
        },
        "morning": {
            STAGE_TOGETHER: "醒来时他就在旁边，或者已经起床在客厅。",
            STAGE_PART_TIME: "周末醒来时他就在旁边；工作日醒来只有你自己。",
        },
    }
    return table.get(key, {}).get(stage, "")


#: 世界观里和同居冲突的句子（LLM 按旧人格生成后可能残留）
_WORLD_CONFLICT = ("不同住", "各住各", "隔几条街", "约会才见", "平时靠 QQ", "平时靠QQ",
                   "不能凭空出现", "只有约会见面", "各有住处")


def apply_worldview(plugin, data) -> dict:
    """按当前居住档位修正世界观文本（简介/日常/世界三块）。

    世界观是 LLM 按人格生成、缓存 7 天的；同居后旧文案里「同城不同住、约会才见面」
    会明显出戏。这里只在非分居档清理冲突句，并把「住一起」的设定补进世界规则。
    """
    if not isinstance(data, dict):
        return data
    try:
        stage = stage_of(plugin)
        if stage == STAGE_APART:
            return data
        setting = wording("worldview", plugin=plugin)
    except Exception:  # cleanup: 读不到就保持原样
        return data
    out = dict(data)
    for key in ("summary", "daily_life", "world_rules"):
        text = str(out.get(key) or "")
        if not text:
            continue
        parts = re.split(r"(?<=[。！？；])", text)
        kept = [part for part in parts if part and not any(w in part for w in _WORLD_CONFLICT)]
        out[key] = "".join(kept).strip()
    if setting and setting not in str(out.get("world_rules") or ""):
        base = str(out.get("world_rules") or "").strip()
        out["world_rules"] = (base + setting).strip()
    return out


#: 同居后日记里不许再出现的「分居感」说法（2026-11-21 线上：同城另一头/隔几条街）
_APART_WORDS = (
    ("在同城另一头", "就在家里"),
    ("同城另一头", "家里"),
    ("隔着几条街", "就在家里"),
    ("隔几条街", "就在家里"),
    ("异地恋", "在一起"),
    ("异地", "在家"),
    ("分居", "住一起"),
    ("各住各的", "住一起"),
)


def fix_apart_text(plugin, text):
    """同居/半同居时把日记里的「同城另一头/隔几条街/异地」改掉，避免和设定打架。"""
    value = str(text or "")
    try:
        if stage_of(plugin) == STAGE_APART:
            return value
    except Exception:
        return value
    for bad, good in _APART_WORDS:
        value = value.replace(bad, good)
    return value


def presence_line(plugin, now=None) -> str:
    """聊天上下文里的【居住】一行；apart 也返回（让模型有稳定口径）。"""
    scene = scene_now(plugin, now)
    if scene.stage == STAGE_APART:
        return ("【居住】你们同城不同住：她住出租屋，他住他自己那边。"
                "平时他不在她身边，只能手机联系；只有日程写着约会/见面时他才能在对面。")
    if scene.stage == STAGE_PART_TIME:
        return ("【居住】你们半同居：周末（周五晚到周日下午）他在这边，"
                "工作日各住各的。现在他%s。" % ("在屋里" if scene.partner else "不在，只能手机联系"))
    if not scene.partner and scene.reason in ("他出差", "他不在家"):
        return "【居住】你们住在一起，但他这会儿%s，只能手机联系。" % scene.reason
    if scene.partner:
        return "【居住】你们住在一起，他这会儿在家（%s）。" % scene.partner_place
    reason = "他上班/出差" if scene.reason == "同居·他上班" else "他不在家"
    return "【居住】你们住在一起，但这会儿%s，只能手机联系。" % reason
