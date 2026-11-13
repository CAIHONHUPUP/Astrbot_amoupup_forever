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


class ScheduleApiMixin:
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
            items = self.state._hourly_schedule(self.db.get_schedule(day) or [])
        return {"code": 0, "data": {
            "day": day, "meta": day_meta(day), "items": items,
            "tree": self.state.schedule_tree(day),
            "current": self.state.current_activity(),
            "context": self.state.schedule_context(day),
        }}

    async def api_schedule_detail(self):
        await self._ensure_ready()
        data = await self._body()
        day = str(data.get("date") or "").strip() or today_str()
        start_raw = str(data.get("start") or "").strip()
        matched = re.match(r"^(\d{1,2})(?::(\d{2}))?$", start_raw)
        if not matched:
            return {"code": 1, "msg": "start 需要 HH:MM，例如 10:00"}
        hour = max(0, min(23, int(matched.group(1))))
        start = "%02d:00" % hour
        if day == today_str():
            items = await self.state.ensure_schedule(self._last_umo)
        else:
            items = self.state._hourly_schedule(self.db.get_schedule(day) or [])
        if not items:
            return {"code": 1, "msg": "这天没有日程，不生成小日记"}
        item = None
        for entry in items:
            if str(entry.get("start") or "") == start:
                item = entry
                break
        if item is None:
            return {"code": 1, "msg": "没有找到这一小时的日程：" + start}
        force_raw = data.get("force", "")
        force = str(force_raw).lower() in ("1", "true", "yes", "on")
        result = await self.state.expand_hour(day, item, items, force=force)
        payload = {"day": day, "start": item.get("start", start), "end": item.get("end", "")}
        payload.update(result)
        return {"code": 0, "data": payload}

    async def api_schedule_regen(self):
        await self._ensure_ready()
        items = await self.state.ensure_schedule(self._last_umo, force=True)
        return {"code": 0, "data": items}
