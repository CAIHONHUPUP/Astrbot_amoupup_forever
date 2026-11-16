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
    from .amo_shared import MOOD_KEYS, clean_ui_value, clamp, day_meta, today_str
except ImportError:
    from amo_shared import MOOD_KEYS, clean_ui_value, clamp, day_meta, today_str


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object


def _rows_displayed(plugin, items):
    """假 plugin 只绑了 api_schedule 时没有 helper：拿不到就原样返回。"""
    fn = getattr(plugin, "_display_item", None)
    return [fn(x) for x in (items or [])] if callable(fn) else list(items or [])


def _tree_displayed(plugin, tree):
    fn = getattr(plugin, "_display_tree", None)
    return fn(tree) if callable(fn) else list(tree or [])


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
            # 第八轮 D：GET 纯只读——不调 ensure_schedule、不跑 LLM；缺数据返回 generating，
            # 真正的生成交给后台 tick（主干 ensure 在 _diary_scheduler_tick 里补）。
            try:
                items = list(self.state.schedule.get(day) or self.db.get_schedule(day) or [])
            except Exception as _amo_exc:
                _note_swallowed("amo_api_schedule.api_schedule.read", _amo_exc)
                items = []
            if not items:
                return {"code": 0, "data": {
                    "day": day, "status": "generating",
                    "message": "她正在写日记呢，过几秒再刷新看看~",
                    "meta": clean_ui_value(day_meta(day)), "items": [], "tree": [],
                    "current": clean_ui_value(self.state.current_activity()),
                    "context": {}, "blind": "",
                }}
        else:
            # 未来日期只回「已经定下来的行程」，不铺 24 小时假休息
            items = self.state._booked_items(day)
        # 历史数据可能残留互相重叠的块（早期 JIT 与过渡并存）：读时统一去重叠并落库，
        # 保证面板时间轴是一条直线（新块互斥之外的第二道保险）。
        try:
            try:
                from .amo_transition import normalize_day as _normalize_day
            except ImportError:
                from amo_transition import normalize_day as _normalize_day
            # 只做展示层归一：读接口绝不改写历史数据（写回曾在 14:1x 误删过当天日程）。
            fixed, changed = _normalize_day(items or [])
            if changed:
                items = fixed
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.normalize_overlaps", _amo_exc)
        tree = self.state.schedule_tree(day)
        # 第七轮：过渡块/活动块按分钟标 doing/current（面板「进行中」）
        try:
            try:
                from .amo_transition import mark_doing as _mark_doing
                from .amo_transition import mark_doing_tree as _mark_doing_tree
            except ImportError:
                from amo_transition import mark_doing as _mark_doing
                from amo_transition import mark_doing_tree as _mark_doing_tree
            items = _mark_doing(items)
            tree = _mark_doing_tree(tree)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.api_schedule.doing", _amo_exc)
        # 第八轮 D：组标题的 LLM 补写也移到后台，GET 只读已缓存的标题。
        return {"code": 0, "data": {
            "day": day, "meta": clean_ui_value(day_meta(day)),
            "items": clean_ui_value(_rows_displayed(self, items)),
            "tree": clean_ui_value(_tree_displayed(self, tree)),
            "current": clean_ui_value(self.state.current_activity()),
            "context": clean_ui_value(self.state.schedule_context(day)),
            "blind": clean_ui_value(self.state.schedule_blind_hint(day)),
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

    def _display_rows(self, items):
        """api_schedule 可能被只绑了这一个方法的假 plugin 调用：helper 不在就原样返回。"""
        fn = getattr(self, "_display_item", None)
        return [fn(x) for x in items] if callable(fn) else list(items or [])

    def _display_rows_tree(self, tree):
        fn = getattr(self, "_display_tree", None)
        return fn(tree) if callable(fn) else list(tree or [])

    def _gap_label(self, item=None):
        fn = getattr(self, "_display_gap_label", None)
        if callable(fn):
            try:
                return fn(item)
            except Exception:  # cleanup
                pass
        return "在家休息（日常发呆）"

    def _display_item(self, item):
        """读侧兜底：旧数据里可能存着“未记录”，面板一律显示人话（不迁移库）。"""
        row = dict(item or {})
        if str(row.get("activity") or "").strip() in ("", "未记录"):
            row["activity"] = self._gap_label(row)
        return row

    def _display_gap_label(self, item=None):
        try:
            health = self.state.health_info() or {}
        except Exception:
            health = {}
        if health.get("sick"):
            return "躺在床上休息"
        place = str((item or {}).get("place") or "")
        if place in ("家", "卧室", "客厅", "出租屋") or not place:
            return "在家休息（日常发呆）"
        return "在原地发着呆"

    def _display_tree(self, tree):
        out = []
        for block in tree or []:
            if not isinstance(block, dict):
                continue
            row = dict(block)
            row["children"] = [self._display_item(child)
                               for child in (block.get("children") or [])]
            out.append(row)
        return out

    def _ui_gap_line(self, item=None):
        """空档兜底的人话：按身体状态/地点给一句，不用“未记录”。"""
        try:
            health = self.state.health_info() or {}
        except Exception:
            health = {}
        place = str((item or {}).get("place") or "")
        if health.get("sick"):
            return "（在床上躺了一会儿，浑身没劲，什么也没干）"
        if place in ("家", "卧室", "客厅", "出租屋") or not place:
            return "（窝在家里发了会儿呆，时间就那么过去了）"
        return "（这阵子没什么特别的事，时间悄悄过去了）"

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
            # 第八轮 D：详情也纯只读，缺数据入后台队列 + generating
            try:
                items = list(self.state.schedule.get(day) or self.db.get_schedule(day) or [])
            except Exception as _amo_exc:
                _note_swallowed("amo_api_schedule.api_schedule_detail.read", _amo_exc)
                items = []
            if not items:
                return {"code": 0, "data": {"day": day, "start": start, "end": end,
                                             "status": "generating",
                                             "message": "她正在写日记呢，过几秒再刷新看看~"}}
        else:
            items = self.state._hourly_schedule(self.db.get_schedule(day) or [])
        item = self.state.resolve_schedule_item(day, start, end, item_id)
        if item is not None and end:
            # 面板点的可能是分钟级块（例如 13:00-13:41）：以请求的精确区间为准，
            # 不能被 _hourly_schedule 归一成 13:00-14:00，否则 13:41 已结束还会被当成“还没过完”。
            item = dict(item, start=start, end=end)
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
                    "empty_message": "这段时间还没过完呢，等她做完再来看吧~",
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
        # 第八轮 D：GET 只读——有缓存就直接给；没有就入后台队列并返回 generating
        try:
            from .amo_human_state import parse_hhmm as _hhmm2
        except ImportError:
            from amo_human_state import parse_hhmm as _hhmm2
        _diary = ""
        try:
            _hour_key = "%02d:00" % min(23, int(_hhmm2(str(item.get("start") or start) or start) // 60))
            _diary = str(self.db.get_diary(day, _hour_key) or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.api_schedule_detail.cache", _amo_exc)
            _diary = ""
        if not _diary:
            try:
                _spawn = getattr(self.state, "_spawn_diary_task", None)
                if callable(_spawn):
                    _spawn(day, item, self.state._hourly_schedule(items),
                           self.db.schedule_version(day), reason="panel_get")
            except Exception as _amo_exc:
                _note_swallowed("amo_api_schedule.api_schedule_detail.enqueue", _amo_exc)
            return {"code": 0, "data": {
                "day": day, "start": start, "end": end or item.get("end", ""),
                "status": "generating",
                "message": "她正在写日记呢，过几秒再刷新看看~",
            }}
        result = {"diary": _diary, "activity": str(item.get("activity") or ""),
                  "pending": False}
        payload = {"day": day, "start": item.get("start", start),
                   "end": item.get("end", ""), "id": item_id}
        payload.update(result)
        if str(payload.get("activity") or "").strip() in ("", "未记录"):
            payload["activity"] = self._gap_label(item)
        # 已经过完的块如果还没有小记：先强制补生成一次；仍为空就给一句人话兜底，
        # 绝不再出现“未记录”或“这小时还没过完”这种和真实时间冲突的提示。
        try:
            from .amo_human_state import slot_has_ended as _slot_has_ended
        except ImportError:
            from amo_human_state import slot_has_ended as _slot_has_ended
        _ended = (day < today_str()) or (day == today_str()
                                         and _slot_has_ended(day, start, item.get("end")))
        if _ended and not str(payload.get("diary") or "").strip():
            try:
                _spawn = getattr(self.state, "_spawn_diary_task", None)
                if callable(_spawn):
                    _spawn(day, item, self.state._hourly_schedule(items),
                           self.db.schedule_version(day), reason="panel_get_ended")
            except Exception as _amo_exc:
                _note_swallowed("amo_api_schedule.backfill", _amo_exc)
            return {"code": 0, "data": {
                "day": day, "start": start, "end": end or item.get("end", ""),
                "status": "generating",
                "message": "她正在写日记呢，过几秒再刷新看看~",
            }}
        try:
            from .schedule_engine import ScheduleEngine as _SE
        except ImportError:
            from schedule_engine import ScheduleEngine as _SE
        _diary_text = _SE._clean_diary(payload.get("diary") or "")
        if any(word in _diary_text for word in ("到家", "沙发", "睡衣", "卧室", "被窝", "出租屋")):
            _diary_text = _SE._fix_home_diary(_diary_text)
        payload["diary"] = _diary_text
        if payload.get("pending"):
            payload.setdefault("empty_message", "这段时间还没过完呢，等她做完再来看吧~")
        elif not str(payload.get("diary") or "").strip():
            payload["diary"] = self._ui_gap_line(item)
            payload["fallback"] = True
        # 兜底重生成会用旧 item 覆盖 activity，这里最后再映射一次，面板永远不显示“未记录”
        if str(payload.get("activity") or "").strip() in ("", "未记录"):
            payload["activity"] = self._gap_label(item)
        return {"code": 0, "data": payload}

    async def api_schedule_regen(self):
        await self._ensure_ready()
        items = await self.state.ensure_schedule(self._last_umo, force=True)
        return {"code": 0, "data": items}
