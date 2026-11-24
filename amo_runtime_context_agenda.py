# -*- coding: utf-8 -*-
"""运行时上下文：吃饭缺失 + 今日安排（从 amo_runtime_context 拆出）。

只搬方法，行为不变；目标是让 amo_runtime_context 保持在 1400 行以内。
"""
from __future__ import annotations

from typing import Any

try:  # 外卖待送达/吃饭事实
    from .amo_food_delivery import pending_order, today_facts, waiting_hint
except ImportError:  # 直接按文件加载时
    from amo_food_delivery import pending_order, today_facts, waiting_hint

try:  # 饭点缺失标签
    from .amo_intimacy import meal_missing_labels
except ImportError:
    from amo_intimacy import meal_missing_labels


try:  # 吞异常留痕
    from .amo_observability import note as _note_swallowed
except ImportError:
    from amo_observability import note as _note_swallowed


class AgendaContextMixin:
    # 宿主（HumanCorePlugin）提供的成员：只在类型层面声明，运行时不创建属性（不改变任何行为）。
    state: Any
    db: Any
    scenic_spots: Any
    _short_place: Any

    def _ctx_meal_missing(self: Any, c):
        """只读：过了饭点还没有 meal 事实时，允许她说“还没吃/忘了”，不写时间轴。"""
        if c.get("is_group"):
            return []
        try:
            from . import amo_runtime_context as _ctx_mod
        except ImportError:
            import amo_runtime_context as _ctx_mod
        try:
            now = _ctx_mod._now()
            try:
                from .amo_timeline import Timeline
            except ImportError:
                from amo_timeline import Timeline
            day = now.date().isoformat()
            cache_key = "%s|%s|%s" % (c.get("uid"), day, now.strftime("%H%M"))
            cached = getattr(self, "_meal_missing_cache", None)
            if isinstance(cached, dict) and cached.get("key") == cache_key:
                return list(cached.get("parts") or [])

            def _remember(parts):
                self._meal_missing_cache = {"key": cache_key, "parts": list(parts)}
                return parts

            items = Timeline(self).blocks(day) or []
            pending = pending_order(self, now=now.timestamp())
            if pending:
                return _remember([waiting_hint(str(pending.get("food") or "外卖"))])
            facts = today_facts(self, day)
            labels = meal_missing_labels(items, now.hour * 60 + now.minute, facts)
            if not labels:
                return _remember([])
            return _remember([
                "【吃饭】今天%s还没有记录，她可以说还没吃/忘了，不要编吃了什么；"
                "如果现在饿了，可以提议现在去吃点，但别把没吃的饭写成已经吃过。"
                % "、".join(labels)
            ])
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._ctx_meal_missing", _amo_exc)
            return []

    def _ctx_agenda(self: Any, c):
        """今日安排/城市/旅游/他在/跟随/出游。"""
        try:
            from . import amo_runtime_context as _ctx_mod
        except ImportError:
            import amo_runtime_context as _ctx_mod
        follow_text = c["follow_text"]
        wander_text = c["wander_text"]
        parts = []
        _pending = pending_order(self, now=_ctx_mod._now().timestamp())
        try:
            now_text = _ctx_mod._now().strftime("%H:%M")
            agenda: list[Any] = []
            for item in (self.state.schedule.get(_ctx_mod.today_str()) or []):
                end = str(item.get("end") or "")
                if end and end <= now_text:
                    continue
                if len(agenda) >= 6:
                    break
                activity_text = item.get("activity", "休息")
                place_text = item.get("place", "家")
                if _pending and any(
                        word in str(place_text) for word in ("小馆", "餐厅", "饭馆", "餐馆")):
                    activity_text = "在家等外卖，外卖还没到"
                    place_text = "家"
                agenda.append("%s-%s %s（%s）" % (
                    item.get("start", ""), end,
                    activity_text,
                    self._short_place(place_text),
                ))
            if agenda:
                parts.append("【今日安排】" + "；".join(agenda))
                parts.append(
                    "【日程为准】今天的安排以【今日安排】为准；日程里没有的会议、出差、约会、聚餐都不算数；"
                    "之前说错了就自然改口（“我看错时间了/记岔了”）。"
                )
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context", _amo_exc)
        try:
            city_block = _ctx_mod._city_block(self.db.kv_get("his_city", "") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context", _amo_exc)
            city_block = ""
        if city_block:
            parts.append(city_block)
        try:
            travel_row = self.db.kv_get("travel", {}) or {}
            if (
                isinstance(travel_row, dict)
                and str(travel_row.get("city") or "").strip()
                and str(travel_row.get("start_day") or "") <= _ctx_mod.today_str()
            ):
                travel_city = str(travel_row["city"])
                try:
                    travel_spots = _ctx_mod._city_pick_spots(self.scenic_spots(), travel_city, _ctx_mod.today_str())
                except Exception as _amo_exc:
                    _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context", _amo_exc)
                    travel_spots = []
                travel_text = _ctx_mod._city_travel_block(
                    travel_city, weekday=_ctx_mod._now().date().weekday() < 5, spots=travel_spots,
                )
                if travel_text:
                    parts.append(travel_text)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context", _amo_exc)
        try:
            he_city = str(self.db.kv_get("he_city", "") or "").strip()
            my_city = str(self.db.kv_get("his_city", "") or "").strip()
            _tv = self.db.kv_get("travel", {}) or {}
            _tv_active = bool(
                isinstance(_tv, dict) and str(_tv.get("city") or "").strip()
                and str(_tv.get("start_day") or "") <= _ctx_mod.today_str()
            )
            if he_city and he_city != my_city and not _tv_active:
                parts.append(
                    "【他在】他现在在 " + he_city + "，你还在 " + (my_city or "东莞")
                    + "上班；你有点想他，但没过去。"
                )
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._dynamic_context", _amo_exc)
        if follow_text:
            parts.append(follow_text)
        if wander_text:
            parts.append(wander_text)
        return parts
