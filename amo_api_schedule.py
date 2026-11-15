# -*- coding: utf-8 -*-
"""日程面板 API：从 amo_api.py 拆出的 handler mixin。"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:
        from amo_observability import note as _note_swallowed

try:
    from .amo_shared import MOOD_KEYS, clamp, day_meta, today_str
except ImportError:
    from amo_shared import MOOD_KEYS, clamp, day_meta, today_str


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object


class ScheduleApiMixin(_MixinBase):
    async def api_mood_update(self):
        await self._ensure_ready()
        data = await self._body()
        incoming = data.get("mood") if isinstance(data.get("mood"), dict) else data
        for key in MOOD_KEYS:
            if key in incoming:
                try:
                    self.state.mood[key] = clamp(float(incoming[key]), 0.0, 100.0)
                except Exception as _amo_exc:
                    _note_swallowed("amo_api_schedule.ScheduleApiMixin.api_mood_update", _amo_exc)
                    continue
        self.state.save()
        return {"code": 0, "data": self.state.snapshot()}

    async def api_schedule(self):
        await self._ensure_ready()
        data = await self._body()
        day = str(data.get("date") or self._q("date", "") or today_str())
        if day == today_str():
            items = await self.state.ensure_schedule(self._last_umo)
        else:
            # 未来日期只回「已经定下来的行程」，不铺 24 小时假休息
            items = self.state._booked_items(day)
        tree = self.state.schedule_tree(day)
        try:
            tree = await self.state.enrich_schedule_tree(day, tree, self._last_umo)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.api_schedule.summary", _amo_exc)
        return {"code": 0, "data": {
            "day": day, "meta": day_meta(day), "items": items,
            "tree": tree,
            "current": self.state.current_activity(),
            "context": self.state.schedule_context(day),
            "blind": self.state.schedule_blind_hint(day),
        }}

    async def api_todo_list(self):
        """明日待办：未来事件只读待办表，不剧透精确日程。"""
        await self._ensure_ready()
        data = await self._body()
        day = str(data.get("date") or self._q("date", "") or "").strip()
        if not day:
            try:
                import datetime as _dt
                day = (_dt.date.fromisoformat(today_str()) + _dt.timedelta(days=1)).isoformat()
            except Exception:
                day = ""
        items = []
        try:
            items = list(self.state.todos(day) or [])
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.ScheduleApiMixin.api_todo_list", _amo_exc)
            items = []
        compact = []
        for item in items:
            compact.append({
                "id": item.get("id"),
                "start": str(item.get("start") or ""),
                "end": str(item.get("end") or ""),
                "activity": str(item.get("activity") or ""),
                "place": str(item.get("place") or ""),
                "category": str(item.get("category") or ""),
            })
        return {"code": 0, "data": {
            "day": day,
            "items": compact,
            "empty_text": "明天暂时没有特别的安排，又是充满未知的一天哦~",
        }}

    async def api_schedule_detail(self):
        await self._ensure_ready()
        data = await self._body()
        day = str(data.get("date") or self._q("date", "") or "").strip() or today_str()
        item_id = str(data.get("id") or self._q("id", "") or "").strip()

        def _norm_clock(value):
            matched = re.match(r"^(\d{1,2}):(\d{2})$", str(value or "").strip()[:5])
            if not matched:
                return ""
            hour = max(0, min(23, int(matched.group(1))))
            minute = max(0, min(59, int(matched.group(2))))
            return "%02d:%02d" % (hour, minute)

        start = _norm_clock(data.get("start") or data.get("time") or self._q("start", "") or "")
        end = _norm_clock(data.get("end") or self._q("end", "") or "")
        if not start:
            return {"code": 0, "data": {
                "day": day, "start": "", "end": end, "diary": "",
                "empty_message": "她迷糊了，没留下这段小记。",
            }}
        if day == today_str():
            items = await self.state.ensure_schedule(self._last_umo)
        else:
            items = self.state._hourly_schedule(self.db.get_schedule(day) or [])
        item = self.state.resolve_schedule_item(day, start, end, item_id)
        if item is None:
            try:
                from .amo_human_state import slot_has_ended as _slot_has_ended
            except ImportError:
                from amo_human_state import slot_has_ended as _slot_has_ended
            _ended = (day < today_str()) or (day == today_str() and _slot_has_ended(day, start, end))
            if day >= today_str() and not _ended:
                return {"code": 0, "data": {
                    "day": day, "start": start, "end": end or start, "diary": "",
                    "pending": True,
                    "empty_message": "她还在忙呢，这小时还没过完，晚点再看吧。",
                }}
            # 合并出来的空档（如「20:27-22:00 窝在家里的晚上」）：把区间里已有的小日记带出来
            try:
                try:
                    from .amo_human_state import parse_hhmm as _hhmm
                except ImportError:
                    from amo_human_state import parse_hhmm as _hhmm
                begin_h = min(23, int(_hhmm(start) // 60))
                stop_h = 24 if not end else max(begin_h + 1, min(24, int(_hhmm(end) // 60)))
                found = ""
                for hour in range(begin_h, stop_h):
                    text = self.state.db.get_diary(day, "%02d:00" % hour) or ""
                    if str(text).strip():
                        found = text
                if found:
                    return {"code": 0, "data": {
                        "day": day, "start": start, "end": end or start, "diary": found,
                        "synthetic": True,
                    }}
            except Exception as _amo_exc:
                _note_swallowed("amo_api_schedule.gap_diary", _amo_exc)
            return {"code": 0, "data": {
                "day": day, "start": start, "end": end or start, "diary": "",
                "empty_message": "这一段是空档补出来的，没有单独的小记。",
                "synthetic": True,
            }}
        force_raw = data.get("force", "")
        force = str(force_raw).lower() in ("1", "true", "yes", "on")
        try:
            result = await self.state.expand_hour(day, item, items, force=force)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.api_schedule_detail", _amo_exc)
            return {"code": 0, "data": {
                "day": day, "start": start, "end": end or item.get("end", ""), "diary": "",
                "empty_message": "她迷糊了，这段小记没接上，再点一次试试。",
            }}
        payload = {"day": day, "start": item.get("start", start),
                   "end": item.get("end", ""), "id": item_id}
        payload.update(result)
        try:
            from .schedule_engine import ScheduleEngine as _SE
        except ImportError:
            from schedule_engine import ScheduleEngine as _SE
        _diary_text = _SE._clean_diary(payload.get("diary") or "")
        if any(word in _diary_text for word in ("到家", "沙发", "睡衣", "卧室", "被窝", "出租屋")):
            _diary_text = _SE._fix_home_diary(_diary_text)
        payload["diary"] = _diary_text
        if payload.get("pending"):
            payload.setdefault("empty_message", "她还在忙呢，这小时还没过完，晚点再看吧。")
        elif not str(payload.get("diary") or "").strip():
            payload.setdefault("empty_message", "她迷糊了，没留下这段小记。")
        return {"code": 0, "data": payload}

    async def api_schedule_regen(self):
        await self._ensure_ready()
        items = await self.state.ensure_schedule(self._last_umo, force=True)
        return {"code": 0, "data": items}
