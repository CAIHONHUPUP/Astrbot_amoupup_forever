# -*- coding: utf-8 -*-
"""熬夜：23:30 后仍清醒 → is_staying_up；疲劳加速、次日惩罚、哄睡/转账清空。"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import logging
logger = logging.getLogger("amo")

import datetime as _dt
import random

try:
    from .amo_shared import now_ts
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import now_ts

START_HOUR = 23
START_MIN = 30


class LateNightEngine:
    def __init__(self, state):
        self.human = state
        self.db = state.db

    def _data(self):
        try:
            data = self.db.kv_get("late_night", {}) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self, data):
        try:
            self.db.kv_set("late_night", data or {})
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)

    @staticmethod
    def _day_of(now):
        # 凌晨 0-6 点算前一天熬夜
        day = now.date()
        if now.hour < 6:
            day -= _dt.timedelta(days=1)
        return day.isoformat()

    def _roll_night(self, data, now):
        """进入新的一晚（18:00 后）清掉上一晚的哄睡/惩罚/生病标记。

        以前 cleared/penalty_done/sick_done 写进 kv 后从不按天复位：
        只要被哄睡一次，熬夜提示、次日疲惫、连续两晚感冒就永久失效。
        """
        night = self._day_of(now)
        if now.hour < 18 or not data.get("date") or data.get("date") == night:
            return data
        data["date"] = night
        for key in ("cleared", "penalty_done", "sick_done", "active", "late2", "staying"):
            data.pop(key, None)
        self._save(data)
        return data

    def flags(self):
        try:
            data = self.db.kv_get("late_flags", {}) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _set_flag(self, key):
        data = self.flags()
        data["date"] = _amo_clock.today().isoformat()
        data[key] = True
        try:
            self.db.kv_set("late_flags", data)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)

    def on_big_stock_loss(self):
        self._set_flag("big_loss")

    def on_forced_overtime(self):
        self._set_flag("overtime")

    def is_staying_up(self, now=None):
        now = now or _amo_clock.now()
        data = self._roll_night(self._data(), now)
        if data.get("cleared"):
            return False
        if data.get("decided_date") == self._day_of(now) and data.get("staying") is False:
            return False
        late = now.hour > START_HOUR or (now.hour == START_HOUR and now.minute >= START_MIN) or now.hour < 6
        return bool(late)

    def tick(self, now=None):
        now = now or _amo_clock.now()
        data = self._roll_night(self._data(), now)
        if data.get("cleared"):
            return None
        hour_key = now.strftime("%Y%m%d%H")
        if now.hour >= START_HOUR and now.minute >= START_MIN or now.hour < 6:
            day_key = self._day_of(now)
            if data.get("decided_date") != day_key:
                flags = self.flags()
                if flags.get("date") == _amo_clock.today().isoformat() or flags.get("date") == day_key:
                    if flags.get("overtime"):
                        stay = True
                    elif flags.get("big_loss"):
                        stay = random.random() < 0.7
                    else:
                        stay = True
                else:
                    stay = True
                data["decided_date"] = day_key
                data["staying"] = stay
                if not stay:
                    data["active"] = False
                    self._save(data)
                    return data
            data["active"] = True
            data["date"] = self._day_of(now)
            hist = list(data.get("history") or [])
            if data["date"] not in hist:
                hist.append(data["date"])
                data["history"] = hist[-14:]
            # late2 = 真的熬过了凌晨两点。以前写成「now.hour >= 2 or now.hour < 6」，
            # 23 点刚进窗口也成立，于是每天 23:30 就把「熬到很晚」标记上，次日白扣心情。
            if 2 <= now.hour < 6:
                data["late2"] = True
            if data.get("fatigue_hour") != hour_key:
                try:
                    self.human.nudge(疲惫=2)
                except Exception as exc:
                    logger.debug("[amo] optional step failed: %r", exc)
                data["fatigue_hour"] = hour_key
            self._save(data)
        # 连续两晚熬夜：第二天有概率感冒/发烧（不再是 100%，且病好后 3 天免疫）
        try:
            already_sick = str((getattr(self.human, "health", {}) or {}).get("status") or "") in (
                "轻微不适", "感冒", "低烧", "发烧", "肠胃炎")
            streak = self.streak()
            chance = 0.25 if streak == 2 else (0.45 if streak >= 3 else 0.0)
            try:
                cooldown = float(self.human.db.kv_get("sick_cooldown_until", 0) or 0)
            except Exception:
                cooldown = 0.0
            if (8 <= now.hour < 20 and not data.get("cleared") and not data.get("sick_done")
                    and streak >= 2 and not already_sick
                    and _amo_clock.now_ts() >= cooldown
                    and random.random() < chance):
                status = random.choice(("感冒", "发烧"))
                self.human.set_health(status, note="连续熬夜，免疫力垮了", severity="minor")
                data["history"] = []
                data["sick_done"] = True
                self._save(data)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)
        # 第二天惩罚：疲惫 +30、皮肤差情绪 -10（只一次）
        if data.get("date") and not data.get("penalty_done") and 8 <= now.hour < 20:
            yesterday = (_amo_clock.today() - _dt.timedelta(days=1)).isoformat()
            if data.get("date") == yesterday and not data.get("cleared"):
                try:
                    self.human.nudge(疲惫=30)
                    if data.get("late2"):
                        self.human.nudge(开心=-10)
                except Exception as exc:
                    logger.debug("[amo] optional step failed: %r", exc)
                data["penalty_done"] = True
                self._save(data)
        return data

    def soothe(self, reason=""):
        data = self._data()
        data.update({"active": False, "cleared": True, "penalty_done": True,
                     "soothed_by": str(reason or ""), "soothed_at": now_ts(),
                     "cleared_date": self._day_of(_amo_clock.now())})
        try:
            self.db.kv_set("late_flags", {})
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)
        self._save(data)
        try:
            # 被哄睡是「心动」时刻，必须给数值反馈：原来写的是「感動」，既不是简体也不是
            # MOOD_KEYS 里的词，被 nudge 静默丢掉，等于她的感动没有任何情绪记账。
            self.human.nudge(开心=12, 依恋=5, 疲惫=-10)
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)
        return data

    def note_snack(self, amount_cents, food=""):
        data = self._data()
        data["snack_cents"] = int(data.get("snack_cents") or 0) + int(amount_cents or 0)
        data["snack_food"] = str(food or data.get("snack_food") or "")
        self._save(data)

    def note_fine(self, amount_cents):
        data = self._data()
        data["fine_cents"] = int(data.get("fine_cents") or 0) + int(amount_cents or 0)
        data["fined"] = True
        self._save(data)

    def late_yesterday(self, now=None):
        now = now or _amo_clock.now()
        data = self._data()
        if data.get("cleared"):
            return False
        y = (now.date() - _dt.timedelta(days=1)).isoformat()
        return str(data.get("date") or "") == y

    def streak(self):
        hist = list(self._data().get("history") or [])
        if not hist:
            return 0
        days = set(hist)
        count = 0
        cursor = _amo_clock.today() - _dt.timedelta(days=1)
        while cursor.isoformat() in days:
            count += 1
            cursor -= _dt.timedelta(days=1)
        return count

    def state(self, now=None):
        now = now or _amo_clock.now()
        data = self._data()
        return {
            "active": self.is_staying_up(now),
            "date": data.get("date"),
            "late2": bool(data.get("late2")),
            "cleared": bool(data.get("cleared")),
            "late_yesterday": self.late_yesterday(now),
            "streak": self.streak(),
            "snack_cents": int(data.get("snack_cents") or 0),
            "snack_food": data.get("snack_food") or "",
            "fine_cents": int(data.get("fine_cents") or 0),
        }

    def prompt_line(self, now=None):
        now = now or _amo_clock.now()
        data = self._data()
        if self.is_staying_up(now):
            text = "现在是凌晨%d点，你非常困但睡不着；聊天时语气带点疲惫、打哈欠，可以撒娇让老公哄你睡。" % now.hour
            if data.get("snack_food"):
                text += "你刚没忍住点了%s，有点心疼钱。" % data.get("snack_food")
            return text
        if self.late_yesterday(now):
            text = "你昨晚熬夜了，今天头重脚轻、没什么精神，只想赶紧下班回家睡觉。"
            if data.get("snack_food"):
                text += "昨晚还点了%s，花了不少钱，有点心疼。" % data.get("snack_food")
            return text
        return ""
