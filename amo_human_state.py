
import time
from typing import Any, TYPE_CHECKING

# -*- coding: utf-8 -*-
"""人类状态机：心情/生理期/健康/天气/世界观/日程/旅行。"""

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

# 稳定事件：日程写完后发布；当前订阅者为空，行为与直接调用完全兼容。
try:
    from .amo_events import ScheduleChanged
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_events import ScheduleChanged

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .day_types import normalize_day_types, pick_day_type
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .day_types import normalize_day_types, pick_day_type
    except ImportError:  # 直接按文件加载时
        try:
            from day_types import normalize_day_types, pick_day_type
        except ImportError:
            normalize_day_types = None  # type: ignore[assignment]
            pick_day_type = None  # type: ignore[assignment]


from datetime import date
from datetime import datetime
from datetime import timedelta
import asyncio
import json
import math
import random
import re
import threading

try:  # 日程主语校验（纯函数）：生成的活动不能凭空带上老公
    from .schedule_subject import validate_activity as _validate_activity
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from schedule_subject import validate_activity as _validate_activity
        except Exception:  # pragma: no cover - 缺模块时退回放行，不让日程写不进去
            def _validate_activity(activity, approved=False):
                return {"ok": True, "activity": activity, "changed": False, "reason": "unavailable"}

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        MOOD_BASE,
        MOOD_KEYS,
        SCHEDULE_VERSION,
        SICK_STATUSES,
        ScheduleEngine,
        _CITY_DEFAULT,
        _city_pick_spots,
        _city_travel_events,
        _city_travel_to_events,
        _now,
        clamp,
        day_meta,
        in_minute_range,
        logger,
        md5_int,
        minutes_now,
        now_ts,
        parse_hhmm,
        parse_weather_daily,
        schedule_category_mood,
        city_or_empty,
        sentiment,
        to_float,
        to_int,
        today_str,
        weekday_cn,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            MOOD_BASE,
            MOOD_KEYS,
            SCHEDULE_VERSION,
        SICK_STATUSES,
            ScheduleEngine,
            _CITY_DEFAULT,
            _city_pick_spots,
            _city_travel_events,
            _city_travel_to_events,
            _now,
            clamp,
            day_meta,
            in_minute_range,
            logger,
            md5_int,
            minutes_now,
            now_ts,
            parse_hhmm,
            parse_weather_daily,
            schedule_category_mood,
            city_or_empty,
        sentiment,
            to_float,
            to_int,
            today_str,
            weekday_cn,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                MOOD_BASE,
                MOOD_KEYS,
                SCHEDULE_VERSION,
            SICK_STATUSES,
                ScheduleEngine,
                _CITY_DEFAULT,
                _city_pick_spots,
                _city_travel_events,
                _city_travel_to_events,
                _now,
                clamp,
                day_meta,
                in_minute_range,
                logger,
                md5_int,
                minutes_now,
                now_ts,
                parse_hhmm,
                parse_weather_daily,
                schedule_category_mood,
                city_or_empty,
            sentiment,
                to_float,
                to_int,
                today_str,
                weekday_cn,
            )

try:  # 纯规则模块：日程 → 地点/场景优先级（照片严格跟随日程用）
    from .photo_rules import CATEGORY_PRIORITY, zone_for_place
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_rules import CATEGORY_PRIORITY, zone_for_place

# ---- 拆分后的公共层与 mixin：helper/常量走 amo_state_common，方法按域搬进四个 mixin ----
try:
    from .amo_state_common import (
        DIARY_REWRITE_LIMIT,
        PLACE_WHITELIST,
        _PLACE_ALIASES,
        _PLACE_ZONE_KEEP,
        _jit_activity,
        _pick_covering,
        _pick_stale,
        _pick_yesterday_hour,
        _today_items,
        normalize_place,
        slot_has_ended,
        slot_is_future,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_common import (
            DIARY_REWRITE_LIMIT,
            PLACE_WHITELIST,
            _PLACE_ALIASES,
            _PLACE_ZONE_KEEP,
            _jit_activity,
            _pick_covering,
            _pick_stale,
            _pick_yesterday_hour,
            _today_items,
            normalize_place,
            slot_has_ended,
            slot_is_future,
        )

try:
    from .amo_state_schedule import ScheduleStateMixin
    from .amo_state_todo import ScheduleTodoMixin
    from .amo_state_jit import JitStateMixin
    from .amo_state_diary import DiaryStateMixin
    from .amo_state_date import DateStateMixin
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_schedule import ScheduleStateMixin
        from amo_state_todo import ScheduleTodoMixin
        from amo_state_jit import JitStateMixin
        from amo_state_diary import DiaryStateMixin
        from amo_state_date import DateStateMixin


#: 一天只允许三次真实天气刷新（硬锁定，2026-11-19 用户要求）：
#: 早上 07:00、中午 12:00、晚上 19:45（20:00 预报预警前）。窗口内只刷一次，其余时间读缓存。
WEATHER_REFRESH_WINDOWS = (
    ("morning", (7 * 60, 9 * 60)),
    ("noon", (12 * 60, 14 * 60)),
    ("evening", (19 * 60 + 40, 20 * 60)),
)
WEATHER_REFRESH_MAX = 3
#: 失败缓存/退避：2 小时（2026-11-19 用户要求，接口坏了就别反复撞）
WEATHER_FAIL_TTL = 2 * 3600

#: 天气请求每日硬上限（2026-11-20）：3 个刷新槽 × 2 个端点 + 预警 + 手动查询的余量。
#: 不管上层怎么 force、签名怎么抖，一天都不可能超过这个数字——4 万次那种事故直接绝迹。
WEATHER_DAILY_MAX_CALLS = 12

class HumanState(ScheduleStateMixin, ScheduleTodoMixin, JitStateMixin, DiaryStateMixin, DateStateMixin):
    def __init__(self, plugin):
        self.p = plugin
        self.db = plugin.db
        # 日程读改写统一串行化：对话改日程、后台 tick、面板写日程不会同时落库。
        self._schedule_commit_lock = threading.RLock()
        self._schedule_gen_locks = {}
        saved_mood = self.db.kv_get("mood", {}) or {}
        self.mood = {key: clamp(saved_mood.get(key, MOOD_BASE[key])) for key in MOOD_KEYS}
        self.mood_ts = to_float(self.db.kv_get("mood_ts", now_ts()), now_ts())
        config_start = self.p.cfg_str("period_start", "").strip()
        seen = self.db.kv_get("period_config_seen", "")
        stored = self.db.kv_get("period_start", "")
        if config_start and config_start != seen:
            try:
                date.fromisoformat(config_start)
                stored = config_start
                self.db.kv_set("period_config_seen", config_start)
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.__init__", _amo_exc)
        self.period_start = stored or (_now().date() - timedelta(days=10)).isoformat()
        self.health = self.db.kv_get("health", {}) or {
            "status": "健康", "since": now_ts(), "until": 0, "note": ""
        }
        self.weather = self.db.kv_get("weather", {}) or {}
        self.worldview = self.db.kv_get("worldview", {}) or {}
        self.schedule = self._load_schedule_cache()
        self.schedule_ctx = self.db.kv_get("schedule_ctx", {}) or {}
        if not isinstance(self.schedule_ctx, dict):
            self.schedule_ctx = {}
        self.schedule_profile = self.db.kv_get("schedule_profile", {}) or {}
        if not isinstance(self.schedule_profile, dict):
            self.schedule_profile = {}
        self.engine = None
        if ScheduleEngine is not None:
            try:
                self.engine = ScheduleEngine(self)
            except Exception as exc:
                logger.warning("[amo] 日程引擎初始化失败: %s" % exc)
        self.last_daily = self.db.kv_get("last_daily", "")
        self.last_health_roll = self.db.kv_get("last_health_roll", "")
        try:
            try:
                from .amo_late_night import LateNightEngine
            except ImportError:
                from amo_late_night import LateNightEngine
            self.late = LateNightEngine(self)
        except Exception:
            self.late = None

    def _load_schedule_cache(self):
        """读侧单一来源：schedule 表优先；表为空才回落老 kv schedule（迁移兼容）。

        2026-11-19 起 kv["schedule"] 只是 save_day 写出的只读镜像，不再权威：
        这里先读表，表里有数据就完全忽略 kv，直接改 kv 不会影响读出来的块。
        """
        table: dict[str, Any] = {}
        loader = getattr(self.db, "all_schedules", None)
        if callable(loader):
            try:
                table = loader() or {}
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.schedule_from_table", _amo_exc)
                table = {}
        if isinstance(table, dict) and table:
            return table
        legacy = self.db.kv_get("schedule", {}) or {}
        return legacy if isinstance(legacy, dict) else {}

    # ---------- 心情 ----------
    def save_mood(self):
        self.db.kv_set("mood", {key: round(value, 2) for key, value in self.mood.items()})
        self.db.kv_set("mood_ts", self.mood_ts)

    def save(self):
        self.save_mood()
        self.db.kv_set("period_start", self.period_start)
        self.db.kv_set("health", self.health)
        self.db.kv_set("last_daily", self.last_daily)
        self.db.kv_set("last_health_roll", self.last_health_roll)

    def nudge(self, **deltas):
        for key, value in deltas.items():
            if key in self.mood:
                self.mood[key] = clamp(self.mood[key] + value)
        self.mood_ts = now_ts()
        self.save_mood()

    def nudge_once(self, idem, **deltas):
        """带幂等的心情调整：同一 idem 只生效一次（金融事件用）。"""
        if not idem:
            self.nudge(**deltas)
            return True
        key = "mood_applied:" + str(idem)
        try:
            if bool(self.db.kv_get(key, False)):
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.nudge_once", _amo_exc)
        self.nudge(**deltas)
        try:
            self.db.kv_set(key, True)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.nudge_once", _amo_exc)
        return True

    def decay_mood(self):
        now = now_ts()
        hours = max(0.0, (now - self.mood_ts) / 3600.0)
        if hours <= 0:
            return
        # 连续衰减：约 45 分钟向基础值回落一半，不再要求隔 9 分钟才衰减一次。
        rate = min(0.85, 1.0 - math.exp(-hours / 0.75))
        for key in MOOD_KEYS:
            self.mood[key] = self.mood[key] + (MOOD_BASE[key] - self.mood[key]) * rate
        self.mood_ts = now
        self.save_mood()

    def dominant_mood(self):
        return max(MOOD_KEYS, key=lambda key: self.mood[key])

    def mood_score(self):
        m = self.mood
        positive = (m["开心"] + m["兴奋"] + m["平静"] + m["依恋"]) / 4.0
        negative = (m["郁闷"] + m["疲惫"] + m["生气"] + m["暴躁"] + m["委屈"] + m["焦虑"]) / 6.0
        return clamp(50 + (positive - negative) * 0.8)

    def mood_text(self):
        """调试/面板用：带数值。对外回复、日记、提示词一律用 mood_words()。"""
        parts = [key + str(int(round(value))) for key, value in self.mood.items()]
        return " ".join(parts) + "（整体偏" + self.dominant_mood() + "）"

    def mood_words(self):
        """对外只给自然语言情绪词，不暴露数值（金融联动也会用到）。"""
        score = self.mood_score()
        if score >= 85:
            return "狂喜、超级开心，开心到转圈"
        if score >= 70:
            return "很开心、心情不错"
        if score >= 58:
            return "还算轻快"
        if score >= 45:
            return "平静、没什么波动"
        if score >= 35:
            return "有点低落、不太开心"
        if score >= 20:
            return "难过、委屈"
        return "崩溃、伤心，呜呜呜"

    def observe(self, text):
        senti = sentiment(text)
        if re.search(r"(对不起|抱歉|我错了|别生气|不生气|哄你|哄哄|原谅我)", text or ""):
            # 恋人或普通人的道歉都要能真正降温，不能“道歉也没用”。
            self.nudge(生气=-18, 暴躁=-10, 委屈=-6, 郁闷=-3, 开心=4, 依恋=2)
        elif senti["insult"] or senti["angry"] >= 2:
            self.nudge(生气=9, 暴躁=5, 开心=-8, 委屈=5)
        elif senti["sad"]:
            self.nudge(郁闷=8, 委屈=7, 开心=-4)
        elif senti["aff"]:
            self.nudge(开心=10, 依恋=12, 生气=-5, 暴躁=-3)
        elif senti["pos"]:
            self.nudge(开心=6, 平静=2, 生气=-2)
        elif senti["neg"]:
            self.nudge(郁闷=4, 疲惫=2)
        else:
            self.nudge(平静=1)
        hour = _now().hour
        if 0 <= hour < 6:
            self.nudge(疲惫=3, 平静=2)
        elif 6 <= hour < 9:
            self.nudge(疲惫=-1, 开心=1)
        elif 22 <= hour < 24:
            self.nudge(疲惫=2, 依恋=2)
        # 聊天里约好的共同活动（一起看电视/打游戏…）：识别 → 写当前小时日程
        try:
            hook = getattr(self.p, "shared_activity_on_message", None)
            if callable(hook):
                hook(text)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.observe.shared", _amo_exc)

    # ---------- 生理期 ----------
    def period_info(self):
        if not self.p.cfg_bool("enable_period_simulate", True):
            return {
                "day": 0, "phase": "非生理期", "tone": "", "pain": 0,
                "reply_mult": 1.0, "days_to_next": 0,
                "cycle_days": self.p.cfg_int("period_cycle_days", 28),
                "bleed_days": 0, "next_start": "",
            }
        try:
            start = date.fromisoformat(str(self.period_start))
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.period_info", _amo_exc)
            start = _now().date() - timedelta(days=10)
            self.period_start = start.isoformat()
        cycle_days = max(10, self.p.cfg_int("period_cycle_days", 28))
        bleed_days = max(1, min(self.p.cfg_int("period_days", 5), cycle_days - 6))
        day = (_now().date() - start).days % cycle_days + 1
        ovulation_day = max(bleed_days + 1, min(cycle_days - 1, self.p.cfg_int("period_ovulation_day", 14)))
        ovulation_window = max(1, min(9, self.p.cfg_int("period_ovulation_window", 3)))
        ovulation_start = max(bleed_days + 1, ovulation_day - (ovulation_window - 1) // 2)
        ovulation_end = min(cycle_days - 1, ovulation_start + ovulation_window - 1)
        if day <= bleed_days:
            phase = "月经期"
            tone = self.p.cfg_str("period_menstrual_tone", "身体沉沉的，容易累，也更容易委屈")
            pain = min(9, 5 + max(0, bleed_days + 1 - day))
            reply_mult = 0.8
        elif day < ovulation_start:
            phase = "卵泡期"
            tone = self.p.cfg_str("period_follicular_tone", "身体轻快，心情慢慢变好")
            pain = 1
            reply_mult = 1.05
        elif day <= ovulation_end:
            phase = "排卵期"
            tone = self.p.cfg_str("period_ovulatory_tone", "精力最好，也更想和人说话")
            pain = 0
            reply_mult = 1.12
        else:
            phase = "黄体期"
            tone = self.p.cfg_str("period_luteal_tone", "容易烦躁、敏感，想要被哄")
            pain = 3
            reply_mult = 0.88
        pain = max(0, min(10, pain + (md5_int(str(start) + str(day)) % 3) - 1))
        if phase == "月经期":
            phase_day = day
        elif phase == "卵泡期":
            phase_day = day - bleed_days
        elif phase == "排卵期":
            phase_day = day - ovulation_start + 1
        else:
            phase_day = day - ovulation_end
        return {
            "day": day, "phase": phase, "phase_day": max(1, phase_day),
            "tone": tone, "pain": pain,
            "reply_mult": reply_mult, "days_to_next": cycle_days - day + 1,
            "cycle_days": cycle_days, "bleed_days": bleed_days,
            "ovulation_day": ovulation_day, "ovulation_window": ovulation_window,
            "next_start": (_now().date() + timedelta(days=cycle_days - day + 1)).isoformat(),
        }

    def set_period_start(self, value):
        try:
            parsed = date.fromisoformat(str(value))
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.set_period_start", _amo_exc)
            return False
        self.period_start = parsed.isoformat()
        self.db.kv_set("period_start", self.period_start)
        self.db.kv_set("period_config_seen", self.p.cfg_str("period_start", ""))
        return True

    # ---------- 健康 ----------
    @staticmethod
    def _severity_of(status):
        return "serious" if str(status) in ("发烧", "重感冒") else "minor"

    def health_info(self):
        status = self.health.get("status", "健康")
        # 老数据里如果留着肺炎/重症之类，读出来时也降级，绝不带进提示词
        fatal_map = {"肺炎": "重感冒", "重症肺炎": "重感冒", "癌症": "不舒服",
                     "肿瘤": "不舒服", "心梗": "不舒服", "脑梗": "不舒服",
                     "白血病": "不舒服"}
        if status in fatal_map:
            status = fatal_map[status]
            self.health["status"] = status
            self.db.kv_set("health", self.health)
        until = to_float(self.health.get("until", 0))
        if status != "健康" and until <= 0:
            # 老数据/手工写入可能留下 until=0（旧版「永不过期」）→ 以前永远病着。
            # 按 since + 48h 兜底写回一个到期时间，再走正常康复判断。
            since = to_float(self.health.get("since", 0)) or now_ts()
            until = since + 48 * 3600
            self.health["until"] = until
            try:
                self.db.kv_set("health", self.health)
            except Exception as exc:
                _note_swallowed("amo_human_state.health_info.until_fallback", exc)
        if status != "健康" and now_ts() >= until:
            self.health = {"status": "健康", "since": now_ts(), "until": 0,
                           "note": "", "kind": "", "treatment": "",
                           "severity": "", "base_hours": 0, "doctor": False}
            self.db.kv_set("health", self.health)
            status = "健康"
        return {
            "status": status,
            "kind": self.health.get("kind", ""),
            "severity": self.health.get("severity", ""),
            "treatment": self.health.get("treatment", ""),
            "doctor": bool(self.health.get("doctor", False)),
            "base_hours": to_float(self.health.get("base_hours", 0)),
            "since": to_float(self.health.get("since", 0)),
            "until": to_float(self.health.get("until", 0)),
            "note": self.health.get("note", ""),
            # 「疲惫」只是累，不是病：以前 status != 健康 全算 sick，于是休息日
            # 排成「吃药、量体温、捂汗」的病号一天。
            "sick": status in SICK_STATUSES,
        }

    def set_health(self, status, hours=None, note="", treatment="", kind="", severity=""):
        """生病/康复。

        小病看医生 1-2 天、大病 3-4 天；不看医生恢复时间翻倍。
        ``hours`` 是「看医生时的基准小时数」，不传则按病种/严重程度随机。
        """
        status = str(status or "").strip()
        # 只保留日常小病/普通发烧；不做任何危及生命的病症
        fatal_map = {
            "肺炎": "重感冒", "重症肺炎": "重感冒", "癌症": "不舒服", "肿瘤": "不舒服",
            "心梗": "不舒服", "脑梗": "不舒服", "白血病": "不舒服",
        }
        status = fatal_map.get(status, status)
        if status not in set(SICK_STATUSES) | {"疲惫", "健康"}:
            status = "不舒服"
        if status == "健康":
            self.health = {"status": "健康", "since": now_ts(), "until": 0,
                           "note": "", "kind": "", "treatment": "",
                           "severity": "", "base_hours": 0, "doctor": False}
        else:
            try:
                from .services.health_engine import resolve_hours, total_hours
            except ImportError:  # 顶层/单文件加载
                from services.health_engine import resolve_hours, total_hours
            severity = severity or self._severity_of(status)
            doctor = bool(str(treatment or "").strip())
            base = resolve_hours(hours, severity == "serious", random)
            total = total_hours(base, doctor)
            # 正在生病时又被同一套触发器 set 一次（连续熬夜/日程），不能把起点重置成今天：
            # 保留最早的 since，只把 until 往后延，避免「14 号生病、18 号又重新算 3 天」。
            now = now_ts()
            current = self.health or {}
            ongoing = (str(current.get("status") or "") in SICK_STATUSES
                       and to_float(current.get("until", 0), 0) > now)
            since = to_float(current.get("since", 0), 0) if ongoing else now
            until = max(to_float(current.get("until", 0), 0), since + total * 3600)
            self.health = {
                "status": status, "kind": kind or str(status), "since": since,
                "until": until, "note": note,
                "treatment": str(treatment or ""), "severity": severity,
                "base_hours": base, "doctor": doctor,
            }
        self.db.kv_set("health", self.health)

    def sick_decision(self, day=None):
        day = str(day or today_str())
        try:
            data = self.db.kv_get("sick_decision:%s" % day, {}) or {}
        except Exception:
            data = {}
        return dict(data) if isinstance(data, dict) else {}

    def on_sick_leave(self, day=None):
        """今天是否请了病假：请了就是全天在家，病好也不回公司（2026-11-18）。"""
        day = str(day or today_str())
        try:
            return str((self.sick_decision(day) or {}).get("action") or "") == "take_sick_leave"
        except Exception:
            return False

    async def decide_sick_action(self, umo="", now=None):
        """工作日早晨发烧：让 LLM 在请假/带病上班之间自主选一次，结果落库。"""
        now = now or _now()
        day = now.date().isoformat()
        health = self.health_info() or {}
        if not health.get("sick") or now.weekday() >= 5 or not (6 <= int(now.hour) <= 10):
            return self.sick_decision(day) or None
        cached = self.sick_decision(day)
        if cached.get("action") in ("take_sick_leave", "go_to_work_sick"):
            return cached
        try:
            from .amo_sick_decision import (ATTENDANCE_BONUS_CENTS, DAILY_WAGE_CENTS,
                                            SICK_SYSTEM, build_prompt, parse_decision)
        except ImportError:
            from amo_sick_decision import (ATTENDANCE_BONUS_CENTS, DAILY_WAGE_CENTS,
                                           SICK_SYSTEM, build_prompt, parse_decision)

        reason = str(health.get("note") or health.get("status") or "发烧")
        try:
            cash = int(self.p.wallet.balance())
        except Exception:
            cash = 0
        decision = None
        try:
            llm = getattr(self.p, "llm", None)
            if llm is not None and hasattr(llm, "chat_raw"):
                label = ("星期一", "星期二", "星期三", "星期四", "星期五",
                         "星期六", "星期日")[now.weekday()]
                raw = await llm.chat_raw(
                    umo or getattr(self.p, "_last_umo", "") or "",
                    SICK_SYSTEM, build_prompt(health.get("status"), cash, label, reason),
                )
                decision = parse_decision(raw)
        except Exception as exc:
            logger.warning("[amo] 生病自主决策失败，按带病上班兜底: %s" % exc)
        if not decision:
            decision = {"action": "go_to_work_sick",
                        "reason": "没想好，还是先去上班", "fallback": True}
        decision.update({"day": day, "at": now_ts(), "status": health.get("status")})

        if decision["action"] == "take_sick_leave":
            # 2026-11-18 用户明确：病假扣的是「下个月工资」，不是现金，不能扣钱包余额。
            # 这里只留一条扣款凭据（watch 工资单读取并计入下月），不再 wallet.debit。
            try:
                deductions = self.db.kv_get("salary_deductions", []) or []
                if not isinstance(deductions, list):
                    deductions = []
                deductions.append({
                    "day": day,
                    "wage_cents": int(DAILY_WAGE_CENTS),
                    "attendance_cents": int(ATTENDANCE_BONUS_CENTS),
                    "at": now_ts(),
                    "reason": "sick_leave",
                })
                self.db.kv_set("salary_deductions", deductions[-60:])
            except Exception as exc:
                _note_swallowed("amo_human_state.sick_leave.deduction_record", exc)
            for key, delta in (("平静", 6), ("疲惫", 4), ("委屈", 2)):
                if key in self.mood:
                    self.mood[key] = max(0.0, min(100.0, float(self.mood[key]) + delta))
            decision["mood_word"] = "虚弱但安心"
        else:
            for key, delta in (("委屈", 8), ("疲惫", 6), ("焦虑", 3)):
                if key in self.mood:
                    self.mood[key] = max(0.0, min(100.0, float(self.mood[key]) + delta))
            try:
                self.health["until"] = max(float(self.health.get("until") or 0),
                                           now_ts() + 4 * 3600)
                self.health["note"] = (str(self.health.get("note") or "") + "；带病上班").strip("；")
                self.db.kv_set("health", self.health)
            except Exception as exc:
                _note_swallowed("amo_human_state.sick_work.health", exc)
            decision["mood_word"] = "委屈又疲惫"
        try:
            self.db.kv_set("mood", self.mood)
            self.db.kv_set("sick_decision:%s" % day, decision)
        except Exception as exc:
            _note_swallowed("amo_human_state.sick_decision.save", exc)
        logger.info("[amo] 生病自主决策 day=%s action=%s reason=%s fallback=%s",
                    day, decision.get("action"), decision.get("reason"), decision.get("fallback"))
        return decision

    def set_treatment(self, treatment):
        """她决定去看医生 / 不去：不重置生病起点，只按新方案重算恢复时间。"""
        if self.health.get("status") == "健康":
            return
        treatment = str(treatment or "").strip()
        doctor = bool(treatment)
        base = max(1.0, to_float(self.health.get("base_hours", 0), 0) or 0)
        severity = str(self.health.get("severity") or self._severity_of(self.health.get("status")))
        if not base:
            base = random.uniform(72, 96) if severity == "serious" else random.uniform(24, 48)
        since = to_float(self.health.get("since", 0), 0) or now_ts()
        total = base * (1 if doctor else 2)
        until = max(now_ts() + 3600, since + total * 3600)
        self.health.update({
            "treatment": treatment, "doctor": doctor,
            "base_hours": base, "until": until,
        })
        self.db.kv_set("health", self.health)

    def roll_health(self):
        if not self.p.cfg_bool("enable_sick_random", True):
            return
        now = now_ts()
        if self.health.get("status") != "健康" and to_float(self.health.get("until", 0)) <= now:
            self.health = {"status": "健康", "since": now, "until": 0, "note": ""}
            self.db.kv_set("health", self.health)
            # 病好之后 3 天内不再被随机/熬夜触发，避免「刚好又感冒」
            try:
                keep = to_float(self.db.kv_get("sick_cooldown_until", 0), 0)
            except Exception:
                keep = 0.0
            self.db.kv_set("sick_cooldown_until", max(keep, now + 3 * 86400))
        if self.last_health_roll == today_str():
            return
        self.last_health_roll = today_str()
        self.db.kv_set("last_health_roll", self.last_health_roll)
        if self.health.get("status") != "健康":
            return
        try:
            if now < to_float(self.db.kv_get("sick_cooldown_until", 0), 0):
                return
        except Exception as exc:
            _note_swallowed("amo_human_state.roll_health.cooldown", exc)
        try:
            from .services.health_engine import pick_illness, sick_probability
        except ImportError:  # 顶层/单文件加载
            from services.health_engine import pick_illness, sick_probability
        weather = self.weather or {}
        prob = sick_probability(
            self.p.cfg_float("sick_prob", 0.02),
            weather.get("tag"),
            to_float(weather.get("temp", 20), 20),
            self.period_info()["phase"] == "月经期",
            self.mood.get("疲惫", 0),
        )
        try:
            if self.late is not None and self.late.streak() >= 2:
                prob = min(1.0, prob * 2)
        except Exception as exc:
            _note_swallowed("amo_human_state.py.565", exc)
        if random.random() >= prob:
            return
        status = pick_illness(random)
        if status == "疲惫":
            self.health = {"status": "疲惫", "kind": "疲惫", "since": now,
                           "until": now + 10 * 3600, "note": "没由来的累，只想躺着",
                           "treatment": "", "severity": "minor",
                           "base_hours": 10, "doctor": False}
            self.db.kv_set("health", self.health)
            return
        note = {
            "轻微不适": "有点不舒服，说不上哪里难受",
            "感冒": "鼻子堵堵的，头有点沉",
            "低烧": "身上发烫，没什么力气",
            "发烧": "烧得头昏昏的，浑身没劲",
            "肠胃炎": "肚子一阵阵疼，还犯恶心",
        }[status]
        severity = self._severity_of(status)
        # 她自己去不去医院：小病多半先扛着，大病更可能去看
        doctor = random.random() < (0.65 if severity == "serious" else 0.35)
        treatment = "看医生" if doctor else ""
        self.set_health(status, note=note, treatment=treatment, severity=severity)

    # ---------- 天气 ----------
    @staticmethod
    def _weather_base_url(value, default=""):
        """和风 Host 兼容：允许只填域名（自动补 https://），也允许带路径。"""
        text = str(value or default or "").strip().rstrip("/")
        if not text:
            return ""
        if not text.startswith(("http://", "https://")):
            text = "https://" + text
        return text

    @staticmethod
    def _weather_tag(text):
        if any(word in text for word in ("雷", "暴雨")):
            return "storm"
        if "雪" in text:
            return "snow"
        if "雨" in text:
            return "rain"
        if "晴" in text:
            return "clear"
        if "阴" in text:
            return "overcast"
        if "雾" in text or "霾" in text:
            return "fog"
        return "cloud"

    def _simulate_weather(self):
        month = _now().date().month
        rng = random.Random(md5_int(today_str() + str(_now().hour)))
        if month in (12, 1, 2):
            choices = [("晴", "clear"), ("多云", "cloud"), ("阴", "overcast"),
                       ("小雪", "snow"), ("小雨", "rain")]
            base_temp = 7
        elif month in (6, 7, 8):
            choices = [("晴", "clear"), ("多云", "cloud"), ("雷阵雨", "storm"),
                       ("阵雨", "rain"), ("阴", "overcast")]
            base_temp = 29
        else:
            choices = [("晴", "clear"), ("多云", "cloud"), ("小雨", "rain"),
                       ("阴", "overcast")]
            base_temp = 19
        text, tag = rng.choice(choices)
        temp = base_temp + rng.randint(-4, 4)
        city = ((self.p.cfg_str("weather_city_name", "") or "").strip()
                or city_or_empty(self.p))
        return {
            # 存显示名，refresh_weather 的缓存签名才比得上（weather_city 可能是数字 ID）
            "city": city,
            "text": text, "tag": tag, "temp": temp,
            "humidity": rng.randint(40, 90),
            "wind": random.choice(["东风", "南风", "西北风", "微风"]) + str(rng.randint(1, 4)) + "级",
            "ts": now_ts(), "source": "simulate", "ok": True, "error": "",
            "key_set": bool(self.p.cfg_str("hefeng_key", "")),
            "host": self.p.cfg_str("weather_host", ""),
        }

    async def _qweather_payload(self, response, label):
        try:
            text = await response.text()
        except Exception as exc:
            logger.warning("[amo] 和风天气 %s 读取响应失败: %s" % (label, exc))
            return {}
        if not str(text or "").strip():
            logger.warning("[amo] 和风天气 %s 返回空响应 HTTP %s" % (label, getattr(response, "status", "?")))
            return {}
        try:
            payload = json.loads(text)
        except Exception:
            logger.warning(
                "[amo] 和风天气 %s 返回非 JSON HTTP %s: %s"
                % (label, getattr(response, "status", "?"), str(text)[:160])
            )
            return {}
        if not isinstance(payload, dict):
            logger.warning("[amo] 和风天气 %s 返回格式异常: %s" % (label, type(payload).__name__))
            return {}
        code = str(payload.get("code") or "")
        status = to_int(getattr(response, "status", 0), 0)
        if status >= 400 or (code and code != "200"):
            logger.warning(
                "[amo] 和风天气 %s HTTP %s code=%s: %s"
                % (label, status, code or "?", str(payload)[:200])
            )
        return payload

    async def _qweather_get(self, url, params):
        """和风请求：用系统网络栈（线程里 urllib），避免 aiohttp 在部分环境超时。

        所有天气 HTTP 都从这里出去，所以每日配额也在这里扣（2026-11-20）：
        不管上层怎么 force、签名怎么抖，一天最多 WEATHER_DAILY_MAX_CALLS 次。
        """
        if not self._weather_budget_take():
            return {}
        import urllib.parse
        import urllib.request

        key = self.p.cfg_str("hefeng_key", "")
        query = dict(params or {})
        query["key"] = key
        full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)

        def _do():
            req = urllib.request.Request(
                full, headers={"X-QW-Api-Key": key, "Accept-Encoding": "identity"}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                import gzip
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))

        return await asyncio.to_thread(_do)

    def _location_of(self, city):
        """中文城市名换成 LocationID；数字/坐标/空值原样返回。

        注意：这两个取天气的方法在 HumanState 上，不能调插件的 `_city_lookup`，
        所以直接用离线表模块的纯函数，模块不在就原样交给上层。
        """
        try:
            try:
                from .amo_city_index import city_location
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState._location_of", _amo_exc)
                from amo_city_index import city_location
            return city_location(city)
        except Exception as exc:
            logger.debug("[amo] 城市名解析失败: %s" % exc)
            return str(city or "").strip()

    async def _fetch_qweather(self, key, city):
        host = self._weather_base_url(self.p.cfg_str("weather_host", ""))
        if not host:
            return None
        legacy_hosts = {"https://devapi.qweather.com", "https://api.qweather.com"}
        if host in legacy_hosts:
            logger.warning("[amo] 和风天气公共 API 域名已停服，请在配置里填写控制台的 API Host")
        geo_raw = self._weather_base_url(self.p.cfg_str("weather_geo_host", ""))
        if geo_raw:
            geo_base = geo_raw
            if geo_base.endswith("/city/lookup"):
                geo_base = geo_base[: -len("/city/lookup")]
            if not geo_base.endswith(("/geo/v2", "/v2")):
                geo_base += "/geo/v2"
        else:
            geo_base = host + "/geo/v2"
        location = self._location_of(city)
        try:
            # 坐标（经度,纬度）和风直接吃，别再去 GeoAPI 换 ID（那会 403）
            if not location.isdigit() and "," not in location:
                geo = await self._qweather_get(
                    geo_base + "/city/lookup", {"location": location, "number": 1}
                )
                if str(geo.get("code") or "") != "200" or not isinstance(geo.get("location"), list):
                    return None
                first = geo["location"][0] if geo["location"] else None
                if not isinstance(first, dict) or not first.get("id"):
                    return None
                location = str(first["id"])
            data = await self._qweather_get(host + "/v7/weather/now", {"location": location})
            if not isinstance(data, dict) or str(data.get("code") or "") != "200":
                logger.warning("[amo] 和风天气 now 返回异常: %s" % str(data)[:160])
                return None
            now = data.get("now")
            if not isinstance(now, dict):
                logger.warning("[amo] 和风天气 now 字段缺失或格式异常")
                return None
            text = str(now.get("text") or "多云")
            city_name = (self.p.cfg_str("weather_city_name", "") or "").strip() or city
            return {
                "city": city_name, "text": text, "tag": self._weather_tag(text),
                "temp": to_int(now.get("temp")), "humidity": to_int(now.get("humidity")),
                "wind": str(now.get("windDir", "")) + str(now.get("windScale", "")) + "级",
                "ts": now_ts(), "source": "qweather", "ok": True, "error": "",
                "key_set": True,
                "host": host,
            }
        except Exception as exc:
            logger.warning("[amo] 和风天气接口失败: %s %r" % (type(exc).__name__, exc))
            return None

    def _cache_daily_empty(self, city, days, ttl=WEATHER_FAIL_TTL):
        """预报失败也写一份空缓存：401/断网时别每次问天气都重新打接口。"""
        try:
            from .amo_state_store import for_plugin as _state_store_for
        except ImportError:
            from amo_state_store import for_plugin as _state_store_for
        try:
            store = _state_store_for(self.p)
            store.set_ttl("weather_daily", {"ts": now_ts(), "city": city,
                                           "days": int(days), "items": []}, ttl)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state._cache_daily_empty", _amo_exc)

    async def _fetch_qweather_daily(self, key, city, days=3, fresh=False):
        """明天/后天的预报；缓存 3 小时，避免后台循环反复打接口。

        fresh=True 跳过缓存：晚上发预警前要拿最新的一份，早上判断的那份不算数。
        """
        try:
            from .amo_state_store import for_plugin as _state_store_for
        except ImportError:
            from amo_state_store import for_plugin as _state_store_for
        store = _state_store_for(self.p)
        cache = store.get_fresh("weather_daily") or {}
        city = str(city or "").strip()
        if (
            not fresh
            and isinstance(cache, dict)
            and cache.get("ts")
            and str(cache.get("city") or "") == city
            and to_int(cache.get("days")) == int(days)
        ):
            items = cache.get("items")
            return items if isinstance(items, list) else []
        host = self._weather_base_url(self.p.cfg_str("weather_host", ""))
        if not host:
            self._cache_daily_empty(city, days)
            return []
        geo_raw = self._weather_base_url(self.p.cfg_str("weather_geo_host", ""))
        if geo_raw:
            geo_base = geo_raw
            if geo_base.endswith("/city/lookup"):
                geo_base = geo_base[: -len("/city/lookup")]
            if not geo_base.endswith(("/geo/v2", "/v2")):
                geo_base += "/geo/v2"
        else:
            geo_base = host + "/geo/v2"
        location = self._location_of(city)
        try:
            # 坐标（经度,纬度）和风直接吃，别再去 GeoAPI 换 ID（那会 403）
            if not location.isdigit() and "," not in location:
                geo = await self._qweather_get(
                    geo_base + "/city/lookup", {"location": location, "number": 1}
                )
                if str(geo.get("code") or "") != "200" or not isinstance(geo.get("location"), list):
                    self._cache_daily_empty(city, days)
                    return []
                first = geo["location"][0] if geo["location"] else None
                if not isinstance(first, dict) or not first.get("id"):
                    self._cache_daily_empty(city, days)
                    return []
                location = str(first["id"])
            data = await self._qweather_get(host + "/v7/weather/%dd" % int(days), {"location": location})
            if not isinstance(data, dict) or str(data.get("code") or "") != "200":
                logger.warning("[amo] 和风天气预报返回异常: %s" % str(data)[:160])
                self._cache_daily_empty(city, days)
                return []
            items = parse_weather_daily(data)
        except Exception as exc:
            logger.warning("[amo] 和风天气预报接口失败: %s %r" % (type(exc).__name__, exc))
            self._cache_daily_empty(city, days)
            return []
        store.set_ttl("weather_daily", {"ts": now_ts(), "city": city,
                                       "days": int(days), "items": items}, 3 * 3600)
        return items

    async def _weather_slot_due(self, now=None):
        """一天只允许三次真实刷新：早上 07:00、中午 12:00、晚上 19:45（20:00 预报前）。

        2026-11-19：用户要求硬锁定，一天最多 3 次，其余时间只读缓存/模拟。
        返回 (slot_key, state)；slot_key 为空表示现在不该打接口。
        """
        now = now or _now()
        day = today_str()
        state = self.p.db.kv_get("weather_slot_state", {}) or {}
        if not isinstance(state, dict) or state.get("date") != day:
            state = {"date": day, "done": {}}
        done = state.get("done")
        if not isinstance(done, dict):
            done = {}
        minute = now.hour * 60 + now.minute
        for slot, (start, end) in WEATHER_REFRESH_WINDOWS:
            if slot in done:
                continue
            if start <= minute <= end:
                return slot, state
        return "", state

    def _weather_budget_take(self):
        """每日天气请求配额：所有 HTTP 入口共用，返回 False 表示今天不能再请求。"""
        try:
            day = today_str()
            key = "weather_calls:" + day
            used = int(self.p.db.kv_get(key, 0) or 0)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.weather_budget.read", _amo_exc)
            return True
        if used >= WEATHER_DAILY_MAX_CALLS:
            logger.warning("[amo] 今日天气请求已达上限 %d 次，跳过本次请求", WEATHER_DAILY_MAX_CALLS)
            return False
        try:
            self.p.db.kv_set(key, used + 1)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.weather_budget.write", _amo_exc)
        return True

    def _weather_city(self):
        """天气用哪个城市：天气配置优先（ID/显示名），没有才退回日程城市。

        2026-11-19：以前这里用 city_or_empty（先日程城市），缓存里却存 weather_city_name，
        两边一旦不同（示例：日程东莞、天气清远）→ 签名永远不相等 → 每次 tick 都 force 刷新，
        30 分钟缓存被绕过，和风一天被打 4 万多次。"""
        raw = (self.p.cfg_str("weather_city", "") or "").strip()
        name = (self.p.cfg_str("weather_city_name", "") or "").strip()
        return (name or raw or city_or_empty(self.p)).strip()

    async def refresh_weather(self, force=False, city_switch=False):
        try:
            self.p._reload_config_if_changed()
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.refresh_weather", _amo_exc)
        try:
            self.p._sync_weather_city_config()
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.refresh_weather", _amo_exc)
        current = self.weather or {}
        key = self.p.cfg_str("hefeng_key", "")
        city = self._weather_city()
        host = self.p.cfg_str("weather_host", "")
        # 配置里的城市/Key/Host 变了就必须重取，不能继续用旧缓存。
        # 比较用显示名：weather_city 可能是数字 LocationID，缓存里存的却是「东莞」这种名字，
        # 拿数字去比永远不相等 → 每次轮询都强制重取天气（30 秒一次，免费额度会被打光）。
        city_label = city
        current_signature = (current.get("city"), bool(current.get("key_set")), current.get("host"))
        desired_signature = (city_label, bool(key), host)
        if current_signature != desired_signature:
            force = True
        # 刚刷过就别再打接口（双击「设置」、面板刷新和后台 tick 撞车）
        if force and current.get("ts") and now_ts() - to_float(current["ts"], 0) < 3:
            return current
        slot_key, slot_state = await self._weather_slot_due()
        done_map = slot_state.get("done") or {}
        city_used = bool(done_map.get("city_switch"))
        used = len([key for key in done_map if key != "city_switch"])
        city_ok = bool(city_switch) and not city_used
        need_network = bool(force) or bool(slot_key) or city_ok
        if not need_network or (used >= WEATHER_REFRESH_MAX and not city_ok):
            # 没到点 / 今天 3 次额度已用完：只读缓存，没缓存就给模拟数据
            if current:
                return current
            data = self._simulate_weather()
            if city:
                data["city"] = city
            self.weather = data
            self.db.kv_set("weather", data)
            return data
        old_tag = current.get("tag")
        # 拉取失败先退避 5 分钟：401/断网时别每个 tick 都去撞接口（401 也计费）
        fail_ts = to_float(getattr(self, "_weather_fail_ts", 0), 0)
        fail_sig = getattr(self, "_weather_fail_sig", ())
        # 2026-11-20：失败退避原来只在 not force 时生效，而签名抖动/面板刷新都会带上
        # force，结果 401 时一分钟能撞十几下。配置（key/city/host）没变就照样退避，
        # 只有他真改了配置（签名不同）才立刻重试。
        if fail_ts and now_ts() - fail_ts < WEATHER_FAIL_TTL and (fail_sig == desired_signature
                                                                 or not force):
            return current or self._simulate_weather()
        data = None
        if key and city:
            data = await self._fetch_qweather(key, city)
        if not data:
            self._weather_fail_ts = now_ts()
            self._weather_fail_sig: tuple = desired_signature
            data = self._simulate_weather()
            # 失败兜底也写「想取的城市」：城市名对不上会让缓存永远失效、每个 tick 重试
            if city:
                data["city"] = city
        else:
            self._weather_fail_ts = 0
            self._weather_fail_sig = ()
        slot_state = dict(slot_state)
        done = dict(slot_state.get("done") or {})
        done["city_switch" if (city_switch and not city_used) else (slot_key or ("manual:%d" % used))] = now_ts()
        slot_state["done"] = done
        slot_state["date"] = today_str()
        self.db.kv_set("weather_slot_state", slot_state)
        self.weather = data
        self.db.kv_set("weather", data)
        if data.get("tag") != old_tag:
            if data.get("tag") in ("rain", "storm", "snow"):
                self.nudge(郁闷=2, 平静=2)
            elif data.get("tag") == "clear":
                self.nudge(开心=2)
        return data

    def weather_text(self):
        weather = self.weather or {}
        if not weather:
            return "还不知道天气"
        city = str(weather.get("city") or "").strip()
        prefix = (city + " ") if city else ""
        return "%s%s %s℃ 湿度%s%% %s" % (
            prefix, weather.get("text", ""),
            weather.get("temp", ""), weather.get("humidity", ""), weather.get("wind", ""),
        )

    def weather_brief(self):
        """面板用的一行天气摘要（只读缓存，不请求和风）。"""
        weather = self.weather or {}
        if not weather:
            return {}
        return {
            "city": str(weather.get("city") or ""),
            "text": str(weather.get("text") or ""),
            "temp": weather.get("temp"),
            "humidity": weather.get("humidity"),
            "wind": str(weather.get("wind") or ""),
            "tag": str(weather.get("tag") or ""),
            "source": str(weather.get("source") or ""),
            "ts": to_float(weather.get("ts"), 0),
        }

    # ---------- 世界观 ----------
    def _fallback_world(self):
        return {
            "name": self.p.persona_name or self.p.cfg_str("bot_name", "") or "陪伴",
            "age": 22,
            "city": city_or_empty(self.p),
            "occupation": "普通上班族",
            "workplace": "公司",
            "personality": "温柔、敏感，偶尔有点小脾气",
            "hobbies": ["听歌", "散步", "喝奶茶"],
            "daily_life": "一个人在城市里上班、生活，会为小事开心，也会有力气用光的时候。",
            "world_rules": "这是一个普通的现代都市，没有超能力；角色要上班、吃饭、睡觉，也会生病和情绪低落。",
            "summary": "一个在城市里认真生活的普通女孩。",
            "_ts": now_ts(),
        }

    async def ensure_worldview(self, umo, force=False):
        if not force and self.worldview and now_ts() - to_float(self.worldview.get("_ts")) < 7 * 86400:
            return self.worldview
        persona = await self.p.llm.persona(umo)
        data = await self.p.llm.gen_worldview(umo, persona)
        if not data:
            data = self._fallback_world()
        name = await self.p._persona_name_for(umo)
        if name:
            data["name"] = name
        data["_ts"] = now_ts()
        self.worldview = data
        self.db.kv_set("worldview", data)
        if name:
            self.p.persona_name = name
            self.p.bot_name = name
        elif data.get("name") and self.p.cfg_str("bot_name", ""):
            self.p.bot_name = data["name"]
        return data

    def worldview_text(self):
        world = self.worldview or {}
        if not world:
            return "还没有生成世界观。"
        hobbies = world.get("hobbies") or []
        if isinstance(hobbies, list):
            hobbies = "、".join([str(item) for item in hobbies])
        return "\n".join([
            "名字：%s" % world.get("name", ""),
            "年龄：%s" % world.get("age", ""),
            "城市：%s" % world.get("city", ""),
            "职业：%s" % world.get("occupation", ""),
            "任职：%s" % world.get("workplace", ""),
            "性格：%s" % world.get("personality", ""),
            "爱好：%s" % hobbies,
            "日常：%s" % world.get("daily_life", ""),
            "规则：%s" % world.get("world_rules", ""),
        ])

    # ---------- 汇总 ----------
    def snapshot(self):
        period = self.period_info()
        health = self.health_info()
        activity = self.current_activity()
        # 2026-11-15 全局状态锁：此刻动作统一走 amo_current_action.get_current_action()
        # （日程 / JIT / 微型事件同一个入口，聊天、生图、主动消息都读这里）
        try:
            try:
                from .amo_current_action import get_current_action as _get_action
            except ImportError:
                from amo_current_action import get_current_action as _get_action
            unified = _get_action(self.p)
            if unified.get("action"):
                merged = dict(unified.get("raw") or {})
                merged["activity"] = unified["action"]
                merged["place"] = unified.get("place") or merged.get("place") or "家"
                merged["zone"] = unified.get("zone") or merged.get("zone") or ""
                merged["category"] = unified.get("category") or merged.get("category") or "life"
                activity = merged
        except Exception as exc:
            _note_swallowed("amo_human_state.py.3115", exc)
        weather = self.weather or {}
        return {
            "date": today_str(),
            "weekday": weekday_cn(),
            "time": _now().strftime("%H:%M"),
            "datetime_text": "%s 星期%s %s" % (today_str(), weekday_cn(), _now().strftime("%H:%M")),
            "mood": {key: round(value, 1) for key, value in self.mood.items()},
            "dominant": self.dominant_mood(),
            "mood_score": self.mood_score(),
            "period": period,
            "health": health,
            "weather": weather,
            "weather_text": self.weather_text(),
            "activity": activity,
            "busy": self.busy_level(activity),
            "schedule": self.schedule.get(today_str()) or [],
        }

    def prompt_text(self, snap=None, with_activity=False):
        snap = snap or self.snapshot()
        activity = snap["activity"]
        lines = [
            "现在是 %s。" % snap["datetime_text"],
            "心情：%s" % self.mood_words(),
            "生理期：%s第%d天，%s。" % (
                snap["period"]["phase"], snap["period"]["day"], snap["period"]["tone"]
            ),
            "身体：%s%s%s。" % (
                snap["health"]["status"],
                ("（" + snap["health"]["note"] + "）") if snap["health"].get("note") else "",
                ("，正在" + str(snap["health"].get("treatment")))
                if snap["health"].get("treatment") else "",
            ),
            "天气：%s。" % snap["weather_text"],
        ]
        if with_activity:
            # 只有被问到「你在干嘛/在哪」时才给活动，避免她张口就报行程
            lines.append(
                "（内在状态，仅供自洽，不是话题）你此刻在%s（%s）。"
                % (activity.get("activity", "休息"), activity.get("place", "家"))
            )
        return "\n".join(lines)

    def tone_text(self, snap=None, relation="普通"):
        snap = snap or self.snapshot()
        mood = snap["mood"]
        if relation == "相恋" and (mood["暴躁"] >= 60 or mood["生气"] >= 62):
            tone = (
                "你在生对方的气，但你是他的恋人：会抱怨、闹别扭、说反话、要哄，"
                "语气带一点委屈，不会像对普通人那样冷怼、赶人或人身攻击；可以嘴硬，但不要真的伤人。"
            )
        elif mood["暴躁"] >= 60:
            tone = "你现在很暴躁，说话冲、短、带刺，一点就着，但不要真正辱骂。"
        elif mood["生气"] >= 62:
            tone = "你现在很烦躁，说话冲、短、带刺，容易不耐烦，但不要辱骂。"
        elif mood["委屈"] >= 60:
            tone = "你现在很委屈，语气软软的，会撒娇，想被哄。"
        elif mood["开心"] >= 65 or mood["兴奋"] >= 65:
            tone = "你现在心情很好，语气轻快，会用语气词和感叹号。"
        elif mood["郁闷"] >= 60:
            tone = "你现在有点低落，说话安静、句子短。"
        elif mood["疲惫"] >= 65:
            tone = "你很累，句子短一点、不展开；但别拿累当理由赶人、结束话题。"
        elif mood["焦虑"] >= 60:
            tone = "你有点焦虑，句子急，容易追问。"
        elif mood["依恋"] >= 65:
            tone = "你现在很黏人，想和对方多聊几句。"
        else:
            tone = "你语气自然、轻快，像跟他发微信：会带一点语气词和小情绪，不是客服。"
        if snap["health"]["sick"]:
            tone += " 你身体不舒服，语气会比平时弱一点。"
        if snap["period"]["phase"] in ("月经期", "黄体期"):
            tone += " 生理期让你更容易累和敏感。"
        if relation == "相恋":
            tone += " 你和对方是恋人，可以亲昵、撒娇、用昵称。"
        elif relation == "仇恨":
            tone += " 你很讨厌对方，语气冷淡、疏远。"
        elif relation == "抵触":
            tone += " 你对对方有点防备，保持距离。"
        return tone
