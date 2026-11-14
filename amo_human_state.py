
from typing import Any, TYPE_CHECKING
# -*- coding: utf-8 -*-
"""人类状态机：心情/生理期/健康/天气/世界观/日程/旅行。"""

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

# 稳定事件：日程写完后发布；当前订阅者为空，行为与直接调用完全兼容。
try:
    from .amo_events import ScheduleChanged
except ImportError:  # 直接按文件加载时
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
from datetime import timedelta
import asyncio
import json
import math
import random
import re
import threading

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
    from photo_rules import CATEGORY_PRIORITY, zone_for_place


def slot_has_ended(day, start, end, now=None):
    """这一小时是否已经过完（到 end 之后才允许生成小日记）。"""
    try:
        now = now or _now()
        day_text = str(day or "")
        today_text = now.strftime("%Y-%m-%d")
        if day_text < today_text:
            return True
        if day_text > today_text:
            return False
        end_text = str(end or "").strip()[:5]
        if end_text in ("24:00", "0:00"):
            end_min = 24 * 60
        else:
            end_min = parse_hhmm(end_text)
        return now.hour * 60 + now.minute >= end_min
    except Exception:
        return True


def slot_is_future(day, start, now=None):
    """这一小时还没到（包括明天/以后的日程），小日记先不生成。"""
    try:
        now = now or _now()
        day_text = str(day or "")
        start_text = str(start or "")[:5]
        today_text = now.strftime("%Y-%m-%d")
        if day_text > today_text:
            return True
        return day_text == today_text and start_text > now.strftime("%H:%M")
    except Exception:
        return False


class HumanState:
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
        self.schedule = self.db.kv_get("schedule", {}) or {}
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
        if status != "健康" and until and now_ts() >= until:
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
            self.health = {
                "status": status, "kind": kind or str(status), "since": now_ts(),
                "until": now_ts() + total * 3600, "note": note,
                "treatment": str(treatment or ""), "severity": severity,
                "base_hours": base, "doctor": doctor,
            }
        self.db.kv_set("health", self.health)

    def set_treatment(self, treatment):
        """她决定去看医生 / 不去：不重置生病起点，只按新方案重算恢复时间。"""
        if self.health.get("status") == "健康":
            return
        treatment = str(treatment or "").strip()
        doctor = bool(treatment)
        base = max(1.0, to_float(self.health.get("base_hours", 0), 0) or 0)
        if not base:
            severity = str(self.health.get("severity") or self._severity_of(self.health.get("status")))
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
        if self.last_health_roll == today_str():
            return
        self.last_health_roll = today_str()
        self.db.kv_set("last_health_roll", self.last_health_roll)
        if self.health.get("status") != "健康":
            return
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
        except Exception:
            pass
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
        city = city_or_empty(self.p)
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
        """和风请求：用系统网络栈（线程里 urllib），避免 aiohttp 在部分环境超时。"""
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

    async def _fetch_qweather_daily(self, key, city, days=3, fresh=False):
        """明天/后天的预报；缓存 3 小时，避免后台循环反复打接口。

        fresh=True 跳过缓存：晚上发预警前要拿最新的一份，早上判断的那份不算数。
        """
        cache = self.db.kv_get("weather_daily", {}) or {}
        city = str(city or "").strip()
        if (
            not fresh
            and isinstance(cache, dict)
            and cache.get("ts")
            and now_ts() - to_float(cache.get("ts"), 0) < 3 * 3600
            and str(cache.get("city") or "") == city
            and to_int(cache.get("days")) == int(days)
        ):
            items = cache.get("items")
            return items if isinstance(items, list) else []
        host = self._weather_base_url(self.p.cfg_str("weather_host", ""))
        if not host:
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
                    return []
                first = geo["location"][0] if geo["location"] else None
                if not isinstance(first, dict) or not first.get("id"):
                    return []
                location = str(first["id"])
            data = await self._qweather_get(host + "/v7/weather/%dd" % int(days), {"location": location})
            if not isinstance(data, dict) or str(data.get("code") or "") != "200":
                logger.warning("[amo] 和风天气预报返回异常: %s" % str(data)[:160])
                return []
            items = parse_weather_daily(data)
        except Exception as exc:
            logger.warning("[amo] 和风天气预报接口失败: %s %r" % (type(exc).__name__, exc))
            return []
        self.db.kv_set("weather_daily", {"ts": now_ts(), "city": city, "days": int(days), "items": items})
        return items

    async def refresh_weather(self, force=False):
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
        city = city_or_empty(self.p)
        host = self.p.cfg_str("weather_host", "")
        # 配置里的城市/Key/Host 变了就必须重取，不能继续用旧缓存。
        # 比较用显示名：weather_city 可能是数字 LocationID，缓存里存的却是「东莞」这种名字，
        # 拿数字去比永远不相等 → 每次轮询都强制重取天气（30 秒一次，免费额度会被打光）。
        city_label = city
        current_signature = (current.get("city"), bool(current.get("key_set")), current.get("host"))
        desired_signature = (city_label, bool(key), host)
        if current_signature != desired_signature:
            force = True
        if not force and current.get("ts") and now_ts() - current["ts"] < 1800:
            return current
        # 刚刷过就别再打接口（双击「设置」、面板刷新和后台 tick 撞车）
        if force and current.get("ts") and now_ts() - to_float(current["ts"], 0) < 3:
            return current
        old_tag = current.get("tag")
        data = None
        if key and city:
            data = await self._fetch_qweather(key, city)
        if not data:
            data = self._simulate_weather()
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

    # ---------- 日程（主干生成 + 动态展开） ----------
    @staticmethod
    def _hourly_schedule(items):
        """把任意日程整理成 24 条整点日程；最后一条固定 23:00-24:00。"""
        # start 必须是 HH:MM：脏数据（比如「坏数据」）以前会被 parse_hhmm 当成 00:00，
        # 于是午夜凭空多出一段活动，24 小时骨架也不对了。
        def _good_start(item):
            if not isinstance(item, dict):
                return False
            text = str(item.get("start") or "").strip()
            if not re.match(r"^\d{1,2}:\d{2}$", text):
                return False
            return 0 <= parse_hhmm(text) < 24 * 60

        valid = [item for item in (items or []) if _good_start(item)]
        if not valid:
            # 没有日程就是没有：不能凭空造 24 小时「休息/睡觉」，否则会生成假小日记。
            return []
        valid.sort(key=lambda item: parse_hhmm(item.get("start")))
        result = []
        for hour in range(24):
            start_min = hour * 60
            found = None
            found_overlap = 0
            found_begin = -1
            for item in valid:
                begin = parse_hhmm(item.get("start"))
                finish = parse_hhmm(item.get("end")) or (begin + 60)
                # 一小时内被切成两段时，取占时更长的那段；并列取更晚开始的那段
                # （例如 13:30 改日程：13:00-13:30 旧、13:30-14:00 新 -> 13 点日记算新）。
                overlap = min(finish, start_min + 60) - max(begin, start_min)
                if overlap <= 0:
                    continue
                if overlap > found_overlap or (overlap == found_overlap and begin >= found_begin):
                    found = item
                    found_overlap = overlap
                    found_begin = begin
            entry: dict[str, Any] = {
                "start": "%02d:00" % hour,
                "end": "24:00" if hour == 23 else "%02d:00" % (hour + 1),
                "activity": "睡觉" if hour < 7 or hour >= 23 else "休息",
                "place": "家",
                "category": "sleep" if hour < 7 or hour >= 23 else "rest",
                "subject": "bot",
            }
            if found:
                entry.update({
                    "activity": found.get("activity", "休息"),
                    "place": found.get("place", "家"),
                    "category": found.get("category", "rest"),
                })
                for key in ("zone", "transit", "vehicle", "detail", "spot",
                            "event", "mood", "mood_valence", "swim"):
                    if found.get(key) not in (None, "", 0):
                        entry[key] = found.get(key)
                if isinstance(found.get("effects"), dict):
                    entry["effects"] = found.get("effects")
                if found.get("soothe"):
                    entry["soothe"] = True
            result.append(entry)
        return result

    @classmethod
    def changed_hours(cls, old_items, new_items):
        """比较两版日程在 24 个整点槽上的差异，返回变化的小时列表。"""
        try:
            old = cls._hourly_schedule(old_items)
            new = cls._hourly_schedule(new_items)
        except Exception:
            return []
        if not old and not new:
            return []
        changed = []
        fields = ("activity", "place", "category", "zone", "detail", "event")
        for hour in range(24):
            a = old[hour] if hour < len(old) else {}
            b = new[hour] if hour < len(new) else {}
            if any(str(a.get(key) or "") != str(b.get(key) or "") for key in fields):
                changed.append(hour)
        return changed

    @classmethod
    def _brief_schedule(cls, items, hours, limit=3):
        """日志用：只打变化小时的活动/地点/分类，避免整份日程刷屏。"""
        hourly = cls._hourly_schedule(items)
        by_hour = {}
        for item in hourly:
            try:
                by_hour[int(str(item.get("start") or "0")[:2])] = item
            except Exception:
                continue
        picked = []
        for hour in (hours or [])[:limit]:
            item = by_hour.get(int(hour)) or {}
            picked.append("%02d:00|%s|%s|%s" % (
                int(hour), item.get("activity") or "-",
                item.get("place") or "-", item.get("category") or "-",
            ))
        if hours and len(hours) > limit:
            picked.append("...+%d" % (len(hours) - limit))
        return ",".join(picked) or "-"

    def commit_schedule_change(self, day, new_items, old_items=None, source="",
                               reason="", invalidate_from=None):
        """所有日程写入口的唯一落库点；外层锁保证同一天的读改写串行。"""
        with self._schedule_commit_lock:
            return self._commit_schedule_change_locked(
                day, new_items, old_items=old_items, source=source,
                reason=reason, invalidate_from=invalidate_from,
            )

    def add_schedule_item(self, day, item, source="finance", reason=""):
        """在当天日程里追加一条（锁内读改写）；整天覆盖仍走 commit_schedule_change。"""
        day = str(day or "")
        empty = {"changed": False, "version": 0, "changed_hours": [], "cancelled": []}
        if not day or not isinstance(item, dict):
            return empty
        start = str(item.get("start") or "").strip()
        if not re.match(r"^\d{1,2}:\d{2}$", start):
            return empty

        def _minute(text):
            try:
                hh, mm = str(text).split(":", 1)
                return int(hh) * 60 + int(mm)
            except Exception:
                return 24 * 60

        with self._schedule_commit_lock:
            current = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
            merged = current + [dict(item)]
            merged.sort(key=lambda row: _minute(str((row or {}).get("start") or "")))
            return self._commit_schedule_change_locked(
                day, merged, old_items=current, source=source, reason=reason,
            )

    def _commit_schedule_change_locked(self, day, new_items, old_items=None, source="",
                                       reason="", invalidate_from=None):
        """commit_schedule_change 的锁内实现。"""
        day = str(day)
        if old_items is None:
            old_items = list(self.schedule.get(day) or [])
        old_items = list(old_items or [])
        new_items = list(new_items or [])
        if old_items == new_items and invalidate_from is None:
            return {
                "changed": False,
                "version": self.db.schedule_version(day),
                "changed_hours": [],
                "cancelled": [],
            }
        changed = self.changed_hours(old_items, new_items)
        try:
            version = self.db.bump_schedule_version(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
            version = 0
        self.schedule[day] = new_items
        if source in ("chat", "command", "manual"):
            try:
                ctx = dict(self.schedule_ctx.get(day) or {})
                ctx["manual_override"] = True
                ctx["manual_source"] = source
                ctx["manual_reason"] = str(reason or "")
                self.schedule_ctx[day] = ctx
                self.db.kv_set("schedule_ctx", self.schedule_ctx)
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.commit_schedule_change.manual", _amo_exc)
        try:
            self.db.save_schedule(day, new_items)
            self.db.kv_set("schedule", self.schedule)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
        if invalidate_from is not None:
            try:
                start_hour = max(0, min(23, int(invalidate_from)))
            except (TypeError, ValueError):
                start_hour = 0
        elif changed:
            start_hour = min(changed)
        else:
            start_hour = None
        cancelled = []
        events = getattr(self.p, "events", None)
        has_bus = bool(events is not None
                       and callable(getattr(events, "has_subscribers", None))
                       and events.has_subscribers(ScheduleChanged))
        if has_bus and events is not None:
            try:
                results = events.publish_collect(ScheduleChanged(
                    day=str(day), version=int(version or 0),
                    source=str(source or ""), reason=str(reason or ""),
                    manual_override=bool((self.schedule_ctx.get(day) or {}).get("manual_override")),
                    start_hour=start_hour if start_hour is not None else -1,
                    changed_hours=tuple(changed or ()),
                )) or []
                for result in results:
                    if isinstance(result, (list, tuple)):
                        cancelled = list(result)
                        break
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.commit_schedule_change.event", _amo_exc)
        else:
            # 兼容没有事件总线的旧实例/单测；线上由 main 的订阅者接管。
            if start_hour is not None:
                try:
                    self.db.mark_diaries_stale(day, start_hour, version)
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
                cancel = getattr(self.p, "_cancel_diary_tasks", None)
                if callable(cancel):
                    try:
                        cancelled = cancel(day, start_hour) or []
                    except Exception as _amo_exc:
                        _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
            daily_mark = getattr(self.p, "_mark_daily_diary_stale", None)
            if callable(daily_mark):
                try:
                    daily_mark(day, version)
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
            wardrobe = getattr(self.p, "wardrobe", None)
            if wardrobe is not None:
                try:
                    invalidate = getattr(wardrobe, "invalidate", None)
                    if callable(invalidate):
                        invalidate(day, reason="schedule_changed")
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
            photo = getattr(self.p, "photo", None)
            changed_hook = getattr(photo, "on_schedule_changed", None) if photo is not None else None
            if callable(changed_hook):
                try:
                    changed_hook(day, version, "schedule_changed")
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.commit_schedule_change.photo", _amo_exc)
        logger.info(
            "[SCHEDULE_DEBUG] day=%s old=%s new=%s version=%s source=%s subject=bot "
            "target_hours=%s invalidate_from=%s cancel=%s reason=%s manual_override=%s",
            day,
            self._brief_schedule(old_items, changed or ([start_hour] if start_hour is not None else [])),
            self._brief_schedule(new_items, changed or ([start_hour] if start_hour is not None else [])),
            version, source or "-", changed or ([start_hour] if start_hour is not None else []),
            start_hour if start_hour is not None else "-", cancelled, reason or "-",
            bool((self.schedule_ctx.get(day) or {}).get("manual_override")),
        )
        return {
            "changed": True,
            "version": version,
            "changed_hours": changed,
            "cancelled": cancelled,
        }

    @staticmethod
    def _fmt_minutes(value):
        value = max(0, min(24 * 60, int(value)))
        if value >= 24 * 60:
            return "24:00"
        return "%02d:%02d" % (value // 60, value % 60)

    def schedule_tree(self, day=None):
        """一级 4 段、二级每小时；不再生成三级 10 分钟。"""
        day = str(day or today_str())
        raw = self.schedule.get(day) or self.db.get_schedule(day) or []
        if self._jit_enabled():
            now_min = minutes_now()
            raw = [item for item in raw
                   if str(item.get("category") or "") in ("appointment", "date")
                   or item.get("appointment") or item.get("happened")
                   or parse_hhmm(item.get("start")) <= now_min]
        hourly = self._hourly_schedule(raw)
        if not hourly:
            return []
        now_min = minutes_now()
        is_today = day == today_str()
        blocks = [
            {"start": "00:00", "end": "06:00", "title": "深夜"},
            {"start": "06:00", "end": "12:00", "title": "上午"},
            {"start": "12:00", "end": "18:00", "title": "中午"},
            {"start": "18:00", "end": "24:00", "title": "晚上"},
        ]
        for block in blocks:
            bstart = parse_hhmm(block["start"])
            bend = 24 * 60 if block["end"] == "24:00" else parse_hhmm(block["end"])
            children = []
            for item in hourly:
                start = max(bstart, parse_hhmm(item.get("start")))
                end = min(bend, parse_hhmm(item.get("end")))
                if start >= end:
                    continue
                hour = {
                    "start": self._fmt_minutes(start),
                    "end": self._fmt_minutes(end),
                    "activity": item.get("activity", "休息"),
                    "place": item.get("place", "家"),
                    "category": item.get("category", "rest"),
                    "zone": item.get("zone", ""),
                    "transit": item.get("transit", ""),
                    "vehicle": item.get("vehicle", ""),
                    "detail": item.get("detail", ""),
                    "event": item.get("event", ""),
                    "mood": item.get("mood", ""),
                    "current": bool(is_today and start <= now_min < end),
                }
                children.append(hour)
            block["date"] = day
            block["children"] = children  # type: ignore[assignment]
            block["current"] = any(child["current"] for child in children)  # type: ignore[assignment]
            if children:
                if all(item.get("category") == "sleep" for item in children):
                    block["activity"] = "睡觉"
                elif any(item.get("category") in ("play", "social") for item in children):
                    block["activity"] = "出去玩"
                elif any(item.get("category") == "work" for item in children):
                    block["activity"] = "上班"
                elif any(item.get("category") == "commute" for item in children):
                    block["activity"] = "回家"
                elif any(item.get("category") == "meal" for item in children):
                    block["activity"] = "吃饭"
                elif any(item.get("category") == "rest" for item in children):
                    block["activity"] = "休息"
                else:
                    block["activity"] = children[0].get("activity") or "休息"
                block["place"] = children[0].get("place") or ""
        return blocks

    @staticmethod
    def _cover_schedule(items):
        """兼容旧数据：补上 00:00 到第一条安排之间的睡眠段。"""
        if not isinstance(items, list) or not items:
            return items
        valid = [item for item in items if isinstance(item, dict) and item.get("start")]
        if not valid:
            return items
        ordered = sorted(valid, key=lambda item: parse_hhmm(item.get("start")))
        first_start = ordered[0].get("start", "00:00")
        if parse_hhmm(first_start) > 0:
            ordered.insert(0, {
                "start": "00:00", "end": first_start,
                "activity": "睡觉", "place": "家", "category": "sleep", "zone": "home",
            })
        return ordered

    def _emergency_schedule(self):
        """新引擎不可用时的最小兜底，保证 24 小时闭环、不瞬移。"""
        profile = getattr(self, "schedule_profile", {}) or {}
        home = self.p.cfg_str("schedule_home", "") or profile.get("home") or "家"
        office = self.p.cfg_str("schedule_workplace", "") or profile.get("workplace") or "公司"
        items = []
        for hour in range(24):
            if hour < 7 or hour >= 23:
                activity, place, zone, category = "睡觉", home, "home", "sleep"
            elif hour < 8:
                activity, place, zone, category = "起床洗漱", home, "home", "life"
            elif hour < 9:
                activity, place, zone, category = "通勤去公司", "路上", "road", "commute"
            elif hour < 12:
                activity, place, zone, category = "上班", office, "office", "work"
            elif hour < 13:
                activity, place, zone, category = "吃午饭", office + "附近", "restaurant", "meal"
            elif hour < 14:
                activity, place, zone, category = "午休", office, "office", "rest"
            elif hour < 18:
                activity, place, zone, category = "上班", office, "office", "work"
            elif hour < 19:
                activity, place, zone, category = "下班回家", "路上", "road", "commute"
            elif hour < 20:
                activity, place, zone, category = "吃晚饭", home, "home", "meal"
            elif hour < 22:
                activity, place, zone, category = "休息", home, "home", "rest"
            else:
                activity, place, zone, category = "洗澡护肤", home, "home", "life"
            items.append({
                "start": "%02d:00" % hour,
                "end": "24:00" if hour == 23 else "%02d:00" % (hour + 1),
                "activity": activity, "place": place, "zone": zone,
                "category": category,
            })
        return items

    def _day_type_config(self) -> list:
        """读 schedule_day_types：新版走 cfg_list，老配置/测试桩里只有 cfg()。"""
        for getter_name in ("cfg_list", "cfg"):
            getter = getattr(self.p, getter_name, None)
            if not callable(getter):
                continue
            try:
                value = getter("schedule_day_types", [])
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state._day_type_config", _amo_exc)
                continue
            if isinstance(value, list) and value:
                return value
        return []

    def _day_types(self) -> list:
        """读配置里的日子类型；没配就用默认那六种。"""
        if normalize_day_types is None:
            return []
        return normalize_day_types(self._day_type_config() or None)

    def _day_type_store(self) -> dict:
        """当天是什么日子按日期存一份：重启后不变，改权重也不会半路换类型。"""
        try:
            raw = self.db.kv_get("day_types", {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state._day_type_store", _amo_exc)
            return {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _today_day_type(self, day: str) -> dict:
        """今天是什么日子：上班日 / 休息日 / 外出日 / 宅家日 / 加班日 / 摸鱼日。"""
        if pick_day_type is None:
            return {"id": "workday", "name": "上班日", "guide": ""}
        try:
            key = str(day)
            store = self._day_type_store()
            cached = store.get(key)
            if isinstance(cached, dict) and cached.get("id"):
                return cached
            # 法定节假日也按“休息日池”抽；不能只看 weekday>=5，否则节假日
            # 仍可能抽到加班日/上班日。
            try:
                rest = bool(day_meta(key).get("rest"))
            except Exception:
                rest = date.fromisoformat(key).weekday() >= 5
            picked = dict(pick_day_type(self._day_types(), rest, key))
            store[key] = picked
            if len(store) > 30:
                for old in sorted(store)[:-30]:
                    store.pop(old, None)
            self.db.kv_set("day_types", store)
            return picked
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state._today_day_type", _amo_exc)
            return {"id": "workday", "name": "上班日", "guide": ""}

    def schedule_text_of(self, day: str) -> str:
        """把某天的日程拍成一段文本（/日程 用）。"""
        try:
            key = str(day)
            items = self.schedule.get(key) or self.db.get_schedule(key) or []
            if self._jit_enabled() and key == today_str():
                now_min = minutes_now()
                items = [item for item in items
                         if str(item.get("category") or "") in ("appointment", "date")
                         or item.get("appointment") or item.get("happened")
                         or parse_hhmm(item.get("start")) <= now_min]
            if not items:
                return "那天还没有安排。"
            info = self._day_type_store().get(key) or {}
            lines = []
            if isinstance(info, dict) and info.get("name"):
                lines.append("今天是%s。" % info["name"])
            for item in items:
                activity = str(item.get("activity") or "").strip()
                if not activity:
                    continue
                place = str(item.get("place") or "").strip()
                lines.append("%s-%s %s%s" % (
                    item.get("start") or "", item.get("end") or "", activity,
                    ("（%s）" % place) if place else "",
                ))
            return "\n".join(lines[:24]) or "那天还没有安排。"
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.schedule_text_of", _amo_exc)
            return "想不起来…"

    def _schedule_gen_lock(self, day):
        key = str(day or today_str())
        lock = self._schedule_gen_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._schedule_gen_locks[key] = lock
        return lock

    # ---------- JIT 即时决策（盲盒模式） ----------
    def _jit_enabled(self):
        try:
            return bool(self.p.cfg_bool("schedule_jit", True))
        except Exception:
            return True

    def jit_action(self):
        try:
            data = self.db.kv_get("current_action", {}) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def current_action_text(self):
        data = self.jit_action()
        if not data:
            return ""
        try:
            if float(data.get("until") or 0) <= now_ts():
                return ""
        except Exception:
            return ""
        return str(data.get("action") or "")

    def _jit_items(self, day):
        items = []
        try:
            items = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
        except Exception:
            items = []
        now = minutes_now()
        kept = []
        for item in items:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "")
            if (category in ("appointment", "date") or item.get("appointment")
                    or item.get("happened") or parse_hhmm(item.get("start")) <= now):
                kept.append(item)
        return kept

    def jit_tick(self, now=None):
        """到点推演一次：只决定此刻做什么，不排以后。"""
        if not self._jit_enabled():
            return None
        now = now or _now()
        hour = int(now.hour)
        if hour not in (8, 12, 15, 19, 23):
            return None
        day = now.date().isoformat()
        flag = "jit:%s:%02d" % (day, hour)
        try:
            if self.db.kv_get(flag, False):
                return None
        except Exception:
            pass
        hunger = 100.0
        try:
            hunger = float(self.p.get_hunger())
        except Exception:
            pass
        try:
            cash = int(self.p.wallet.balance())
        except Exception:
            cash = 0
        health = self.health_info() or {}
        try:
            from .schedule_engine import jit_plan
        except ImportError:
            from schedule_engine import jit_plan
        plan = jit_plan(hour, hunger, cash, bool(health.get("sick")),
                        str(self.mood.get("word") or ""))
        try:
            if self.late is not None and hour in (12, 15) and now.weekday() >= 5 and self.late.late_yesterday(now):
                plan = {"action": "在家补觉，睡到自然醒", "place": "家", "zone": "home",
                        "category": "rest", "cost_cents": 0,
                        "diary": "昨晚熬夜，今天白天补觉。"}
        except Exception:
            pass
        cost = int(plan.get("cost_cents") or 0)
        paid = False
        if cost:
            try:
                paid = bool(self.p.wallet.debit(cost, "JIT:%s" % plan.get("action"),
                                                "jit:%s:%02d" % (day, hour)))
            except Exception:
                paid = False
            if not paid:
                plan = {"action": "没舍得花钱，先忍一忍", "place": "家", "zone": "home",
                        "category": "rest", "cost_cents": 0, "diary": ""}
        item = {"start": "%02d:00" % hour, "end": "%02d:00" % ((hour + 1) % 24),
                "activity": plan.get("action"), "place": plan.get("place"),
                "zone": plan.get("zone"), "category": plan.get("category"),
                "source": "jit", "happened": True}
        try:
            items = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
            self.commit_schedule_change(day, items + [item], old_items=items,
                                        source="jit", reason="到点即时决策")
        except Exception:
            pass
        self.db.kv_set("current_action", {"action": plan.get("action"),
                                          "place": plan.get("place"),
                                          "at": now_ts(),
                                          "until": now_ts() + 3600,
                                          "hour": hour})
        self.db.kv_set(flag, True)
        diary = str(plan.get("diary") or "")
        if diary:
            try:
                self.db.kv_set("jit_diary:%s:%02d" % (day, hour), diary)
                write = getattr(self.p, "_data_call", None)
                if callable(write):
                    import asyncio
                    asyncio.get_running_loop().create_task(write(
                        "remember", self.p._data_bot_id(), "amo", diary,
                        kind="activity", importance=0.35))
            except Exception:
                pass
        return item

    async def ensure_schedule(self, umo, force=False):
        """每天只生成一次主干；同一日期加锁，避免并发生成两份。"""
        async with self._schedule_gen_lock(today_str()):
            return await self._ensure_schedule_locked(umo, force=force)

    async def _ensure_schedule_locked(self, umo, force=False):
        """ensure_schedule 的锁内实现；点击二级卡片时再动态生成这一小时的日记。"""
        today = today_str()
        meta = day_meta(today)
        if not force and self._jit_enabled():
            # JIT 盲盒模式：只保留预约和已发生的流水，未来随机日常一律隐藏
            items = self._jit_items(today)
            existing = list(self.schedule.get(today) or self.db.get_schedule(today) or [])
            if items != existing:
                try:
                    self.commit_schedule_change(today, items, old_items=existing,
                                                source="jit_purge", reason="隐藏未来随机日程")
                except Exception:
                    pass
            return self._hourly_schedule(items)
        if force:
            # /amo schedule regen、面板重生成：先丢掉当天缓存的 day_type，
            # 否则配置改成 weekend 之后还会读回旧的 homebody。
            try:
                store = self._day_type_store()
                if today in store:
                    store.pop(today, None)
                    self.db.kv_set("day_types", store)
                ctx = dict(self.schedule_ctx.get(today) or {})
                if ctx.pop("manual_override", None):
                    self.schedule_ctx[today] = ctx
                    self.db.kv_set("schedule_ctx", self.schedule_ctx)
                    logger.info("[SCHEDULE_DEBUG] day=%s manual_override=cleared reason=force_regen", today)
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.ensure_schedule.clear_day_type", _amo_exc)
        previous = list(self.schedule.get(today) or [])
        existing = None if force else self.schedule.get(today)
        context = self.schedule_ctx.get(today) or {}
        if not force and context.get("manual_override") and self.schedule.get(today):
            # 用户手动改过就先按手动版走，自动日程不覆盖。
            logger.info("[SCHEDULE_DEBUG] day=%s manual_override=true skip=auto_ensure", today)
            return self._hourly_schedule(self.schedule.get(today) or [])
        version_ok = (
            self.db.kv_get("schedule_version", "") == SCHEDULE_VERSION
            and isinstance(context, dict)
            and context.get("version") == "backbone-v5"
        )
        day_type = self._today_day_type(today)
        if existing and version_ok and not force:
            hourly = self._hourly_schedule(existing)
            if hourly != existing:
                self.commit_schedule_change(
                    today, hourly, old_items=existing,
                    source="ensure_normalize", reason="hourly_normalize",
                )
            return hourly
        world = await self.ensure_worldview(umo)
        persona = ""
        try:
            if self.p.llm is not None:
                persona = await self.p.llm.persona(umo)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.ensure_schedule", _amo_exc)
            persona = ""
        if self.engine is not None:
            try:
                self.schedule_profile = self.engine.resolve_profile(world, persona, seed=today)
            except Exception as exc:
                logger.debug("[amo] 角色位置解析失败: %s" % exc)
        weather = await self.refresh_weather()
        try:
            self.roll_health()
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.ensure_schedule", _amo_exc)
        period = self.period_info()
        health = self.health_info()
        context = None
        items = None
        if self.engine is not None:
            try:
                context = self.engine.build_context(
                    today, meta, weather, world, self.mood, period, health,
                    persona=persona, salt=str(now_ts()) if force else "",
                )
                self.schedule_profile = context.get("profile") or self.schedule_profile
                context["day_type"] = day_type
                items = self.engine.build_skeleton(context)
                try:
                    items = await self.engine.enrich(umo, context, items, self.p.llm)
                except Exception as exc:
                    logger.debug("[amo] 日程主干文案润色失败，保留骨架: %s" % exc)
            except Exception as exc:
                logger.warning("[amo] 日程主干生成失败，使用兜底: %s" % exc)
                items = None
        if not items:
            profile = getattr(self, "schedule_profile", {}) or {}
            fallback_city = city_or_empty(self.p)
            context = context or {
                "version": "backbone-v5",
                "day": today,
                "meta": meta,
                "weather": weather,
                "world": world,
                "city": fallback_city,
                "home": self.p.cfg_str("schedule_home", "") or profile.get("home") or "家",
                "workplace": self.p.cfg_str("schedule_workplace", "") or profile.get("workplace") or "公司",
                "car": profile.get("car") or "",
                "mood": dict(self.mood),
                "period": period,
                "health": health,
                "event": {"kind": "none"},
                "scenic": None,
                "profile": profile,
                "location": fallback_city,
                "good_weather": True,
                "yesterday": self.engine.yesterday_summary(today) if self.engine is not None else {},
            }
            items = self._emergency_schedule()
        items = self._hourly_schedule(items)
        if context:
            context["version"] = "backbone-v5"
            context["day"] = today
            self.schedule_ctx[today] = context
            self.db.kv_set("schedule_ctx", self.schedule_ctx)
        self.db.kv_set("schedule_version", SCHEDULE_VERSION)
        self.commit_schedule_change(
            today, items, old_items=previous,
            source="ensure_regen" if force else "ensure",
            reason="force" if force else "rebuild",
            invalidate_from=0 if force else None,
        )
        cutoff = (_now().date() - timedelta(days=14)).isoformat()
        for day in list(self.schedule.keys()):
            if day < cutoff:
                self.schedule.pop(day, None)
                self.schedule_ctx.pop(day, None)
        self.db.kv_set("schedule_ctx", self.schedule_ctx)
        day_types = self._day_type_store()
        stale_types = [key for key in day_types if key < cutoff]
        if stale_types:
            for key in stale_types:
                day_types.pop(key, None)
            self.db.kv_set("day_types", day_types)
        logger.info("[amo] 生成日程主干 %s：%s" % (
            today, (context or {}).get("event", {}).get("kind", "none"),
        ))
        try:
            travel = self.db.kv_get("travel", {}) or {}
            if isinstance(travel, dict) and str(travel.get("city") or "").strip():
                city = str(travel["city"])
                start_day = str(travel.get("start_day") or "")
                today = today_str()
                if start_day and today < start_day:
                    pass
                elif today == start_day and travel.get("phase") == "journey":
                    try:
                        start_hour = int(travel.get("start_hour") or 9)
                    except (TypeError, ValueError):
                        start_hour = 9
                    rows = [x for x in (travel.get("spots") or []) if isinstance(x, dict)]
                    self.apply_follow_start(city, start_hour, rows, keep_past=False)
                    self.db.kv_set("his_city", city)
                    if travel.get("id"):
                        try:
                            await self.p._switch_weather_city({
                                "id": str(travel["id"]), "name": city,
                            })
                        except Exception as exc:
                            logger.debug("[amo] 跟随天气切换失败: %s" % exc)
                    travel["phase"] = "touring"
                    self.db.kv_set("travel", travel)
                else:
                    self.apply_travel_overlay(city)
                    self._log_follow_day(city, today)
        except Exception as exc:
            logger.debug("[amo] 出行日程覆盖失败: %s" % exc)
        try:
            trip = self.db.kv_get("wander_trip", {}) or {}
            if isinstance(trip, dict) and trip.get("city"):
                city = str(trip["city"])
                start_day = str(trip.get("day") or "")
                ret_day = str(trip.get("return_day") or start_day)
                today = today_str()
                if start_day and today < start_day:
                    pass
                elif today == start_day:
                    self.apply_travel_overlay(city)
                    trip["phase"] = "applied"
                    self.db.kv_set("wander_trip", trip)
                    self._log_solo_day(city, today)
                    if trip.get("id"):
                        try:
                            await self.p._switch_weather_city({
                                "id": str(trip["id"]), "name": city,
                            })
                        except Exception as exc:
                            logger.debug("[amo] 旅游天气切换失败: %s" % exc)
                elif start_day < today < ret_day:
                    self.apply_travel_overlay(city)
                    self._log_solo_day(city, today)
                elif today == ret_day:
                    until = int(trip.get("return_hour") or 12)
                    self.apply_return_overlay(city, until)
                    self._log_solo_day(city, today)
                    if _now().hour >= until:
                        his = str(self.db.kv_get("his_city", "") or "").strip() or _CITY_DEFAULT
                        hit = self.p._city_lookup(his)
                        if hit:
                            try:
                                await self.p._switch_weather_city(hit)
                            except Exception as _amo_exc:
                                _note_swallowed("amo_human_state.HumanState.ensure_schedule", _amo_exc)
                elif today > ret_day:
                    self.db.kv_set("wander_trip", {})
                    his = str(self.db.kv_get("his_city", "") or "").strip() or _CITY_DEFAULT
                    hit = self.p._city_lookup(his)
                    if hit:
                        try:
                            await self.p._switch_weather_city(hit)
                        except Exception as _amo_exc:
                            _note_swallowed("amo_human_state.HumanState.ensure_schedule", _amo_exc)
                    self.apply_travel_overlay(his)
                    try:
                        rows = self.db.list_profiles(limit=1) or []
                        uid = str((rows[0] or {}).get("uid") or "") if rows else ""
                        if uid:
                            self.p._external_memory_write(
                                uid, "她 %s 从 %s 旅游回来了" % (today, city), "event", 1,
                            )
                    except Exception as exc:
                        logger.debug("[amo] 回来记忆失败: %s" % exc)
        except Exception as exc:
            logger.debug("[amo] 旅游行程推进失败: %s" % exc)
        return items

    def _log_solo_day(self, city, day):
        """旅游每天记一条（日记用）；同一天只记一次。"""
        try:
            log = self.db.kv_get("travel_log", []) or []
            if not isinstance(log, list):
                log = []
            if any(
                isinstance(x, dict) and x.get("day") == day and x.get("solo") and not x.get("back")
                for x in log
            ):
                return
            log.append({"day": day, "hour": "09:00", "city": city, "solo": True})
            self.db.kv_set("travel_log", log[-10:])
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState._log_solo_day", _amo_exc)

    def apply_follow_start(self, city, start_hour=9, spots=None, keep_past=True):
        """跟去他那边的出发日：起床 → 出门坐车约 2 小时 → 到达 → 旅游。"""
        name = str(city or "").strip()
        if not name:
            return False
        if not self.p.cfg_bool("city_travel_schedule", True):
            return False
        today = today_str()
        try:
            start = max(0, min(21, int(start_hour or 9)))
        except (TypeError, ValueError):
            start = 9
        existing = self.schedule.get(today) or self.db.get_schedule(today) or []
        keep = []
        if keep_past:
            for item in existing:
                try:
                    hour = int(str(item.get("start") or "0")[:2])
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.apply_follow_start", _amo_exc)
                    hour = 0
                if hour < start:
                    keep.append(item)
        items = _city_travel_to_events(
            name, start, spots=spots, weekday=_now().date().weekday() < 5, seed=today,
        )
        if keep_past:
            items = [x for x in items if int(str(x.get("start") or "0")[:2]) >= start]
        merged = keep + items
        if not merged:
            return False
        try:
            ctx = dict(self.schedule_ctx.get(today) or {})
            if ctx:
                ctx["city"] = name
                ctx["location"] = name
                ctx["home"] = "他桌面上"
                ctx["workplace"] = "（在去" + name + "的路上）"
                self.schedule_ctx[today] = ctx
                self.db.kv_set("schedule_ctx", self.schedule_ctx)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.apply_follow_start", _amo_exc)
        self.commit_schedule_change(
            today, merged, old_items=existing,
            source="travel_follow_start", reason="follow_start",
            invalidate_from=start if keep_past else 0,
        )
        logger.info("[amo] 出发日日程：%s 点坐车去 %s" % (start, name))
        return True

    def _log_follow_day(self, city, day):
        """跟他旅游的每一天记一条（日记用）；同一天只记一次。"""
        try:
            log = self.db.kv_get("travel_log", []) or []
            if not isinstance(log, list):
                log = []
            if any(
                isinstance(x, dict) and x.get("day") == day and x.get("city") == city
                and not x.get("solo") and not x.get("back") and not x.get("journey")
                for x in log
            ):
                return
            log.append({"day": day, "hour": "09:00", "city": city})
            self.db.kv_set("travel_log", log[-10:])
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState._log_follow_day", _amo_exc)

    def apply_return_overlay(self, city, until_hour=12):
        """旅游最后一天：0 点到 until_hour 在旅游城市，之后回家。"""
        name = str(city or "").strip()
        if not name:
            return False
        try:
            until = max(0, min(23, int(until_hour or 12)))
        except (TypeError, ValueError):
            until = 12
        today = today_str()
        spots = []
        try:
            spots = _city_pick_spots(self.p.scenic_spots(), name, today)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.apply_return_overlay", _amo_exc)
            spots = []
        his = str(self.db.kv_get("his_city", "") or "").strip() or _CITY_DEFAULT
        existing = self.schedule.get(today) or self.db.get_schedule(today) or []
        items = _city_travel_events(name, 0, spots=spots, until_hour=until)
        items += _city_travel_events(his, until, home=True)
        if not items:
            return False
        self.commit_schedule_change(
            today, items, old_items=existing,
            source="travel_return", reason="return_overlay", invalidate_from=0,
        )
        logger.info("[amo] 旅游最后一天：%s 点前在 %s，之后回家" % (until, name))
        return True

    def apply_travel_overlay(self, city=None):
        """出行覆盖层：把今天剩下的日程换成「跟他一起在 X」（已过去的时段不动）。

        只在换城时调用；写库 + 清掉今天剩余时段的小日记，让它们按新日程重算。
        """
        travel = self.db.kv_get("travel", {}) or {}
        name = str(city or (travel.get("city") if isinstance(travel, dict) else "") or "").strip()
        if not name:
            return False
        if not self.p.cfg_bool("city_travel_schedule", True):
            return False
        today = today_str()
        from_hour = _now().hour
        home = name == _CITY_DEFAULT
        existing = self.schedule.get(today) or self.db.get_schedule(today) or []
        if not existing:
            from_hour = 0
        if home and from_hour >= 22:
            logger.info("[amo] 超过 22 点才回去，今晚住酒店，今天日程不再改")
            return False
        keep = []
        for item in existing:
            try:
                hour = int(str(item.get("start") or "0")[:2])
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.apply_travel_overlay", _amo_exc)
                hour = 0
            if hour < from_hour:
                keep.append(item)
        spots = []
        if not home:
            try:
                spots = _city_pick_spots(self.p.scenic_spots(), name, today)
            except Exception as exc:
                logger.debug("[amo] 出行景点挑选失败: %s" % exc)
        merged = keep + _city_travel_events(
            name, from_hour, home=home, weekday=_now().date().weekday() < 5, spots=spots,
        )
        if not merged:
            return False
        try:
            ctx = dict(self.schedule_ctx.get(today) or {})
            if ctx:
                ctx["city"] = name
                ctx["location"] = name
                if home:
                    ctx["home"] = "他桌面上"
                    ctx["workplace"] = str((self.schedule_profile or {}).get("workplace") or "")
                    ctx.pop("scenic", None)
                else:
                    ctx["home"] = name + "·酒店房间"
                    ctx["workplace"] = "（旅游中，不上班）"
                    if spots:
                        ctx["scenic"] = spots[0]
                self.schedule_ctx[today] = ctx
                self.db.kv_set("schedule_ctx", self.schedule_ctx)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.apply_travel_overlay", _amo_exc)
        self.commit_schedule_change(
            today, merged, old_items=existing,
            source="travel_overlay", reason="travel_overlay",
            invalidate_from=from_hour,
        )
        logger.info("[amo] 出行日程已写入：%s 起在 %s%s" % (
            "%02d:00" % from_hour, name, "（回家）" if home else "",
        ))
        return True

    def schedule_context(self, day=None):
        day = str(day or today_str())
        context = self.schedule_ctx.get(day) or {}
        if isinstance(context, dict):
            return context
        return {}

    def _minimal_context(self, day):
        try:
            meta = day_meta(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState._minimal_context", _amo_exc)
            meta = day_meta()
        profile = getattr(self, "schedule_profile", {}) or {}
        city = city_or_empty(self.p)
        return {
            "version": "backbone-v5",
            "day": day,
            "meta": meta,
            "weather": self.weather or {},
            "world": self.worldview or {},
            "city": city,
            "home": self.p.cfg_str("schedule_home", "") or profile.get("home") or "家",
            "workplace": self.p.cfg_str("schedule_workplace", "") or profile.get("workplace") or "公司",
            "car": profile.get("car") or "",
            "mood": dict(self.mood),
            "period": self.period_info(),
            "health": self.health_info(),
            "event": {"kind": "none"},
            "scenic": None,
            "profile": profile,
            "location": city,
            "good_weather": True,
            "yesterday": self.engine.yesterday_summary(day) if self.engine is not None else {},
        }

    async def expand_hour(self, day, item, items=None, force=False):
        """点击二级卡片/调度器生成这一小时的第一人称小日记。

        同一 (day, start) 用进程内锁串行，避免双击/调度器重复调用模型。
        """
        day = str(day or today_str())
        item = dict(item or {})
        start = str(item.get("start") or "")
        get_lock = getattr(self.p, "_diary_lock", None)
        if callable(get_lock) and start:
            lock = get_lock(day, start)
            if lock is not None:
                async with lock:
                    return await self._expand_hour_impl(day, item, items, force)
        return await self._expand_hour_impl(day, item, items, force)

    async def _expand_hour_impl(self, day, item, items=None, force=False):
        day = str(day or today_str())
        item = dict(item or {})
        items = items or self._hourly_schedule(self.schedule.get(day) or [])
        start = str(item.get("start") or "")
        if not start:
            return {"diary": "", "mood": "平静", "place": "", "activity": ""}
        # 以当前日程为准刷新这一小时：面板请求可能在改日程之前就取到了旧 item。
        try:
            latest_items = self._hourly_schedule(
                self.schedule.get(day) or self.db.get_schedule(day) or []
            )
            latest_item = next(
                (x for x in latest_items if str(x.get("start") or "") == start), None,
            )
            if latest_item is not None:
                item = dict(latest_item)
                items = latest_items
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.expand_hour", _amo_exc)
        get_version = getattr(self.db, "schedule_version", None)
        try:
            version = int(get_version(day)) if callable(get_version) else 0
        except Exception:
            version = 0
        # 小日记只写已经过完的时间段：这一小时还没结束就返回 pending，不能提前生成。
        if not slot_has_ended(day, start, item.get("end")):
            logger.info(
                "[DIARY_DEBUG] hour=%s timer_cancel=none next_run=%s reason=pending version=%s",
                start, item.get("end") or "-", version,
            )
            return {"diary": "", "mood": item.get("mood") or "平静",
                    "place": item.get("place") or "",
                    "activity": item.get("activity") or "",
                    "pending": True, "version": version}
        get_record = getattr(self.db, "get_diary_record", None)
        record = None
        if callable(get_record):
            try:
                record = get_record(day, start)
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.expand_hour", _amo_exc)
        if not force and record and str(record.get("status") or "valid") == "valid" \
                and record.get("text"):
            return {
                "diary": str(record.get("text") or ""),
                "mood": item.get("mood") or "平静",
                "place": item.get("place") or "",
                "activity": item.get("activity") or "",
                "cached": True, "version": version,
            }
        stale_text = ""
        if record and str(record.get("status") or "") == "stale":
            stale_text = str(record.get("text") or "")
        context = self.schedule_context(day)
        if not context:
            context = self._minimal_context(day)
        else:
            context = dict(context)
            context["mood"] = dict(self.mood)
            context["health"] = self.health_info()
            context["period"] = self.period_info()
            if self.weather:
                context["weather"] = self.weather
        try:
            wardrobe = getattr(self.p, "wardrobe", None)
            if wardrobe is not None:
                worn = wardrobe.current(day) or {}
                context["outfit"] = str(worn.get("text") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.expand_hour", _amo_exc)
        index = 0
        for position, entry in enumerate(items):
            if entry.get("start") == start:
                index = position
                break
        prev_item = items[index - 1] if index > 0 else None
        next_item = items[index + 1] if index + 1 < len(items) else None
        prev_diary = ""
        if prev_item is not None:
            try:
                prev_diary = self.db.get_diary(day, str(prev_item.get("start") or ""))
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.expand_hour", _amo_exc)
                prev_diary = ""
        result = {"diary": "", "mood": item.get("mood") or "平静",
                  "place": item.get("place") or "", "activity": item.get("activity") or "",
                  "version": version}
        if self.engine is not None:
            try:
                result.update(await self.engine.generate_diary(
                    self.p._last_umo, context, item, prev_item, next_item, self.p.llm,
                    prev_diary=prev_diary,
                ))
            except Exception as exc:
                logger.warning("[amo] 动态日记生成失败: %s" % exc)
        if not result.get("diary") and stale_text:
            # 重算失败就保留旧文，不把这一小时清空；下次读取会再试。
            result["diary"] = stale_text
            result["stale"] = True
            return result
        if not result.get("diary") and self.engine is not None:
            result["diary"] = self.engine.fallback_diary(context, item, prev_item)
        if not result.get("diary"):
            result["diary"] = "这小时我在%s，%s，心情%s。" % (
                item.get("place") or "家", item.get("activity") or "待着",
                item.get("mood") or "平静",
            )
        # 生成期间日程又变了：不把旧版本写回去，保留旧文并标记 stale，等下次重算。
        try:
            current_version = int(get_version(day)) if callable(get_version) else version
        except Exception:
            current_version = version
        if current_version != version:
            # 只有“这一小时本身被改 / 被标记 stale”才丢弃；别的时段改日程
            # 导致全局版本变化时，这一小时仍可安全写回新版本。
            slot_changed = False
            try:
                current_items = self._hourly_schedule(self.schedule.get(day) or [])
                current_item: dict[str, Any] = next(
                    (x for x in current_items if str(x.get("start") or "") == start), {},
                )
                slot_changed = any(
                    str(current_item.get(key) or "") != str(item.get(key) or "")
                    for key in ("activity", "place", "category", "zone", "detail")
                )
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.expand_hour", _amo_exc)
            record_now = None
            if callable(get_record):
                try:
                    record_now = get_record(day, start)
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.expand_hour", _amo_exc)
            marked_stale = bool(record_now and str(record_now.get("status") or "") == "stale")
            if slot_changed or marked_stale:
                logger.info(
                    "[DIARY_DEBUG] hour=%s timer_cancel=none next_run=- reason=version_changed_discard version=%s->%s",
                    start, version, current_version,
                )
                if stale_text:
                    result["diary"] = stale_text
                result["stale"] = True
                result["version"] = current_version
                return result
            version = current_version
            result["version"] = version
        if not result.get("diary") and stale_text:
            result["diary"] = stale_text
            result["stale"] = True
            return result
        try:
            self.db.save_diary(day, start, result["diary"], version=version, status="valid")
        except TypeError:
            # 兼容旧测试里的简化 DB
            self.db.save_diary(day, start, result["diary"])
        # 小日记内容变了（首次生成/旧日程重算），已经写好的每日总结也要重写。
        if force or stale_text or record is None:
            daily_mark = getattr(self.p, "_mark_daily_diary_stale", None)
            if callable(daily_mark):
                try:
                    daily_mark(day, version)
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.expand_hour", _amo_exc)
        logger.info(
            "[DIARY_DEBUG] hour=%s timer_cancel=none next_run=- reason=%s version=%s",
            start, "force_regen" if force else "generated", version,
        )
        return result

    def apply_schedule_events(self):
        """当前小时的日程事件只触发一次，避免每轮都扣心情。"""
        item = self.current_activity()
        effects = item.get("effects") if isinstance(item.get("effects"), dict) else {}
        ctx = self.schedule_context(today_str())
        key = "schedule_event_%s_%s_%s" % (
            today_str(), item.get("start", ""), ctx.get("seed", ""),
        )
        if self.db.kv_get(key, False):
            return
        applied = False
        clean = {}
        for mood_key, delta in effects.items():
            if mood_key in MOOD_KEYS:
                try:
                    clean[mood_key] = float(delta)
                    applied = True
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.apply_schedule_events", _amo_exc)
                    continue
        if not applied:
            # 日常活动也要影响真实心情：上班会累、逛街会开心、睡觉回血。
            # CATEGORY_MOOD 是日程内部用的「每小时」增量，真实心情每轮都会向基准
            # 回落，所以乘一个增益（schedule_mood_gain，默认 3）才看得出来。
            try:
                gain = max(0.0, self.p.cfg_float("schedule_mood_gain", 3.0))
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.apply_schedule_events", _amo_exc)
                gain = 3.0
            category = str(item.get("category") or "")
            for mood_key, delta in (schedule_category_mood().get(category) or {}).items():
                if mood_key not in MOOD_KEYS:
                    continue
                try:
                    clean[mood_key] = float(delta) * gain
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.apply_schedule_events", _amo_exc)
                    continue
            applied = bool(clean)
        if clean:
            self.nudge(**clean)
        # 兼容旧版日程里用 detail 标记的事件链。
        detail = str(item.get("detail") or "")
        if not applied and detail:
            legacy = {
                "熬夜": {"疲惫": 7, "暴躁": 3, "开心": -3},
                "精神恍惚": {"疲惫": 5, "焦虑": 5},
                "老板": {"生气": 8, "委屈": 8, "郁闷": 6, "暴躁": 4},
                "扣工资": {"郁闷": 8, "生气": 6},
                "同事闹脾气": {"生气": 5, "委屈": 3},
                "同事聊天": {"开心": 3, "平静": 2},
            }
            for word, deltas in legacy.items():
                if word in detail:
                    self.nudge(**deltas)
                    applied = True
                    break
        self.db.kv_set(key, True)

    def _photo_info(self, message):
        """生图链路的可见日志：只在 debug_prompt / photo_debug 打开时打。"""
        try:
            if not (self.p.cfg_bool("debug_prompt", False) or self.p.cfg_bool("photo_debug", False)):
                return
        except Exception:
            return
        logger.info(str(message))

    def _hour_fallback_activity(self):
        """最后的按小时兜底（只在今天、昨天都没有日程时才用）。"""
        hour = _now().hour
        if hour < 6:
            data = {"start": "00:00", "end": "06:00", "activity": "睡觉", "place": "家", "category": "sleep"}
        elif hour < 9:
            data = {"start": "07:00", "end": "09:00", "activity": "起床收拾", "place": "家", "category": "life"}
        elif hour < 12:
            data = {"start": "09:00", "end": "12:00", "activity": "上班", "place": "公司", "category": "work"}
        elif hour < 14:
            data = {"start": "12:00", "end": "14:00", "activity": "吃午饭", "place": "公司附近", "category": "meal"}
        elif hour < 18:
            data = {"start": "14:00", "end": "18:00", "activity": "上班", "place": "公司", "category": "work"}
        elif hour < 20:
            data = {"start": "18:00", "end": "20:00", "activity": "吃晚饭", "place": "家", "category": "meal"}
        elif hour < 23:
            data = {"start": "20:00", "end": "23:00", "activity": "休息", "place": "家", "category": "rest"}
        else:
            data = {"start": "23:00", "end": "24:00", "activity": "准备睡觉", "place": "家", "category": "sleep"}
        data["zone"] = zone_for_place(data.get("place"))
        return data

    def current_activity_info(self):
        """当前日程 + 取到它的原因（照片/自拍链路用；原因会写进日志）。

        优先级：
        1. 今天日程里覆盖当前分钟的那条（多条重叠取最近开始的，同时间按类别优先级）；
        2. 今天有日程但这小时没覆盖（编辑后留了空档）→ 沿用最近开始的那条；
        3. 今天还没生成日程（0 点后 / 刚启动）→ 沿用昨天同一小时；
        4. 昨天也没有 → 按小时兜底，并明确标记 no_schedule。
        """
        today = today_str()
        jit = self.jit_action()
        try:
            if jit and float(jit.get("until") or 0) > now_ts():
                return {"activity": jit.get("action"), "place": jit.get("place"),
                        "zone": "home", "category": "life", "_source": "jit",
                        "_reason": "到点即时决策"}, "jit"
        except Exception:
            pass
        try:
            items = self.schedule.get(today) or []
        except Exception:
            items = []
        if not isinstance(items, list):
            items = []
        current = minutes_now()
        hits = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                if in_minute_range(current, parse_hhmm(item.get("start")), parse_hhmm(item.get("end"))):
                    hits.append(item)
            except Exception:
                continue
        if len(hits) > 1:
            hits.sort(key=lambda it: (
                parse_hhmm(it.get("start")),
                CATEGORY_PRIORITY.get(str(it.get("category") or ""), 0),
            ))
            chosen = hits[-1]
            self._photo_info(
                "[PHOTO_DEBUG] schedule_conflict=%d chosen=%s-%s zone=%s others=%s"
                % (len(hits), chosen.get("start"), chosen.get("end"), chosen.get("zone"),
                   "|".join("%s-%s" % (it.get("start"), it.get("end")) for it in hits[:-1]))
            )
            return dict(chosen), "schedule_conflict"
        if hits:
            return dict(hits[0]), "schedule"
        if items:
            started = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    if parse_hhmm(item.get("start")) <= current:
                        started.append(item)
                except Exception:
                    continue
            if started:
                chosen = max(started, key=lambda it: parse_hhmm(it.get("start")))
                data = dict(chosen)
                data["_source"] = "stale_slot"
                data["_reason"] = "今天日程这小时没有覆盖，沿用最近开始的一段"
                self._photo_info(
                    "[PHOTO_DEBUG] fallback=stale_slot chosen=%s-%s zone=%s"
                    % (chosen.get("start"), chosen.get("end"), chosen.get("zone"))
                )
                return data, "stale_slot"
        # 今天还没生成：沿用昨天同一小时（生活连续性比凭空换场景更自然）
        try:
            yesterday = (_now().date() - timedelta(days=1)).isoformat()
        except Exception:
            yesterday = ""
        y_items: list[Any] = []
        if yesterday:
            try:
                y_items = self.schedule.get(yesterday) or self.db.get_schedule(yesterday) or []
            except Exception:
                y_items = []
        y_hits = []
        for item in y_items or []:
            if not isinstance(item, dict):
                continue
            try:
                if in_minute_range(current, parse_hhmm(item.get("start")), parse_hhmm(item.get("end"))):
                    y_hits.append(item)
            except Exception:
                continue
        if y_hits:
            data = dict(y_hits[0])
            data["_source"] = "yesterday_same_hour"
            data["_reason"] = "今天日程还没生成，沿用昨天同一小时"
            self._photo_info(
                "[PHOTO_DEBUG] fallback=no_schedule_today reused=yesterday_same_hour %s zone=%s"
                % (yesterday, data.get("zone"))
            )
            return data, "yesterday_same_hour"
        data = self._hour_fallback_activity()
        data["_source"] = "no_schedule"
        data["_reason"] = "今天和昨天都没有日程，按小时兜底"
        self._photo_info("[PHOTO_DEBUG] fallback=no_schedule reason=no_today_no_yesterday")
        return data, "no_schedule"

    def current_activity(self):
        return self.current_activity_info()[0]

    @staticmethod
    def busy_level(activity):
        category = (activity or {}).get("category", "rest")
        if category == "sleep":
            return 1.0
        if category in ("work", "study"):
            return 0.75
        if category == "commute":
            return 0.6
        if category == "meal":
            return 0.3
        return 0.1

    # ---------- 汇总 ----------
    def snapshot(self):
        period = self.period_info()
        health = self.health_info()
        activity = self.current_activity()
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

    def interesting_event(self):
        events = [
            "楼下咖啡店今天买一送一",
            "路上遇到一只很胖的橘猫",
            "同事带了自己烤的小饼干",
            "窗外的云像一只兔子",
            "刷到一个特别好笑的表情包",
            "便利店新出了草莓味的冰淇淋",
        ]
        if (self.weather or {}).get("tag") == "rain":
            events.append("下班的时候突然下雨，躲在便利店屋檐下")
        index = md5_int(today_str() + str(_now().hour)) % len(events)
        return events[index]

    def daily_tick(self):
        self.last_daily = today_str()
        period = self.period_info()
        if period["phase"] == "月经期":
            self.nudge(疲惫=8, 委屈=6, 开心=-4)
        elif period["phase"] == "黄体期":
            self.nudge(郁闷=5, 焦虑=4)
        elif period["phase"] == "排卵期":
            self.nudge(开心=5, 兴奋=4)
        else:
            self.nudge(开心=3)
        self.save()
