# -*- coding: utf-8 -*-
"""饥饿值：0-100（100 刚吃饱，0 快饿死）。惰性衰减，读的时候补算。

暴露给守护插件：get_hunger(target) / add_hunger(target, amount) / mark_meal()。
"""
from __future__ import annotations

import datetime as _dt

try:
    from .amo_shared import now_ts, to_float
except ImportError:
    from amo_shared import now_ts, to_float

SICK_STATUSES = ("感冒", "发烧", "低烧", "轻微不适", "肠胃炎", "疲惫")


class HungerEngine:
    BASE_RATE = 3.0        # 每小时自然掉 3 点
    COOLDOWN_HOURS = 4.0   # 吃完 4 小时内不再自动觅食

    def __init__(self, plugin):
        self.p = plugin
        self.db = plugin.db
        data = self.db.kv_get("hunger", {}) or {}
        self.value = max(0.0, min(100.0, to_float(data.get("value"), 100.0)))
        self.ts = to_float(data.get("ts"), now_ts()) or now_ts()
        self.last_food = str(data.get("last_food") or "")
        self.last_food_at = to_float(data.get("last_food_at"), 0.0)
        self.cooldown_until = to_float(data.get("cooldown_until"), 0.0)
        self.help_pending = bool(data.get("help_pending"))
        self.last_food_mood = str(data.get("last_food_mood") or "满足")
        self.help_message = str(data.get("help_message") or "")

    # ---------- 持久化 ----------
    def _save(self):
        self.db.kv_set("hunger", {
            "value": round(self.value, 2), "ts": self.ts,
            "last_food": self.last_food, "last_food_at": self.last_food_at,
            "cooldown_until": self.cooldown_until, "help_pending": self.help_pending,
            "last_food_mood": self.last_food_mood, "help_message": self.help_message,
        })

    # ---------- 衰减 ----------
    def _extra_rate(self):
        """生病 / 连续熬夜疲惫 / 大姨妈：每小时多掉 1 点（不叠加）。"""
        try:
            health = getattr(self.p.state, "health", {}) or {}
            status = str(health.get("status") or "")
            if status in SICK_STATUSES or health.get("sick"):
                return 1.0
            mood = getattr(self.p.state, "mood", {}) or {}
            if to_float(mood.get("疲惫"), 0) >= 65:
                return 1.0
            start = str(getattr(self.p.state, "period_start", "") or "")
            if start:
                days = (_dt.date.today() - _dt.date.fromisoformat(start)).days
                if days >= 0 and days % 28 < 5:
                    return 1.0
        except Exception:
            pass
        return 0.0

    def decay(self):
        now = now_ts()
        hours = max(0.0, (now - self.ts) / 3600.0)
        if hours > 0:
            rate = self.BASE_RATE + self._extra_rate()
            try:
                if getattr(self.p.state, "late", None) is not None and self.p.state.late.is_staying_up():
                    rate *= 2.0
            except Exception:
                pass
            self.value = max(0.0, self.value - rate * hours)
            self.ts = now
            self._save()
        return self.value

    # ---------- 对外接口 ----------
    def get(self):
        return round(self.decay(), 1)

    def add(self, amount):
        self.decay()
        self.value = max(0.0, min(100.0, self.value + to_float(amount, 0.0)))
        self.ts = now_ts()
        self.help_pending = False
        self._save()
        return round(self.value, 1)

    def mark_meal(self, food, cooldown_hours=None, mood="满足"):
        self.last_food = str(food or "")
        self.last_food_mood = str(mood or "满足")
        self.last_food_at = now_ts()
        hours = self.COOLDOWN_HOURS if cooldown_hours is None else to_float(cooldown_hours, self.COOLDOWN_HOURS)
        self.cooldown_until = now_ts() + hours * 3600.0
        self.help_pending = False
        self._save()

    def mark_cooldown(self, minutes):
        self.cooldown_until = now_ts() + max(0.0, to_float(minutes, 0.0)) * 60.0
        self._save()

    def request_help(self, message=""):
        self.help_pending = True
        self.help_message = str(message or "")
        self.cooldown_until = now_ts() + 1800.0   # 半小时内别重复喊
        self._save()

    # ---------- 状态 / prompt ----------
    def label(self, value=None):
        val = self.value if value is None else value
        if val > 80:
            return "饱腹"
        if val >= 40:
            return "正常"
        if val >= 10:
            return "饥饿"
        return "极度饥饿"

    def state(self):
        val = self.decay()
        try:
            sick = bool((getattr(self.p.state, "health", {}) or {}).get("sick"))
        except Exception:
            sick = False
        return {
            "value": round(val, 1), "label": self.label(val), "sick": sick,
            "last_food": self.last_food, "last_food_at": self.last_food_at,
            "last_food_mood": self.last_food_mood,
            "cooldown_until": self.cooldown_until, "help_pending": self.help_pending,
        }

    def prompt_line(self):
        val = self.decay()
        now = now_ts()
        if self.last_food and now - self.last_food_at < 1800:
            return "你刚刚吃了%s，感觉%s。" % (self.last_food, self.last_food_mood or "满足")
        if val < 10:
            line = ("你现在极度饥饿，饿得头晕眼花，脾气变得很差，说话有气无力，"
                    "极度渴望老公带你去吃顿好的。")
        elif val < 40:
            line = "你现在有点饿了，肚子在咕咕叫，如果老公在聊天，你可以向他撒娇说想吃饭。"
        else:
            line = ""
        if self.help_pending:
            line += "你饿得实在受不了，已经向老公求助过了。"
        return line
