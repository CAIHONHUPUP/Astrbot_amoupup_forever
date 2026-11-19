# -*- coding: utf-8 -*-
"""日程状态机 mixin：从 amo_human_state.py 按域拆出（只搬方法，行为零变化）。"""
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

try:  # 公共层：helper/常量 + 会被测试改写的模块级函数代理
    from .amo_state_common import (
        minutes_now,
        today_str,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_common import (
            minutes_now,
            today_str,
        )


try:  # 日类型（纯函数）：工作日/周末/节假日
    from .day_types import normalize_day_types, pick_day_type
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from day_types import normalize_day_types, pick_day_type
        except ImportError:
            normalize_day_types = None  # type: ignore[assignment]
            pick_day_type = None  # type: ignore[assignment]

try:  # amo_shared：时间/日程/心情共享底层
    from .amo_shared import (
        logger,
        parse_hhmm,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import (
            logger,
            parse_hhmm,
        )

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object



class ScheduleTodoMixin(_MixinBase):
    """待办、已定行程、时间轴树。"""
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _BOOKED_CATEGORIES: Any
        _JIT_BLOCKS: Any
        _fmt_minutes: Any
        _jit_items: Any
        _jit_visible_slots: Any
        _merge_quiet_slots: Any
        _row_provenance: Any
        _schedule_provenance: Any
        p: Any
        schedule: Any

    def todos(self, day=None, include_done=False):
        """某天已经定下来的事（未来事件只存待办，不提前铺日程）。"""
        day = str(day or today_str())
        try:
            return list(self.db.todos(day, include_done=include_done) or [])
        except Exception:
            return []

    def add_todo(self, day, item, source=""):
        try:
            return int(self.db.add_todo(day, item, source=source) or 0)
        except Exception as exc:
            _note_swallowed("amo_human_state.add_todo", exc)
            return 0

    def _booked_of(self, items):
        booked, seen = [], set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "")
            category = str(item.get("category") or "")
            if source in ("chat", "command", "manual", "social", "date", "watch") \
                    or category in self._BOOKED_CATEGORIES:
                key = (str(item.get("start")), str(item.get("end")),
                       str(item.get("activity")), str(item.get("place")))
                if key in seen:
                    continue
                seen.add(key)
                booked.append(item)
        return booked

    def _commit_todo_day(self, day, new_items, source=""):
        """未来日期：只保留“已经定下来的事”当待办，绝不铺 24 小时日程。"""
        day = str(day or "")
        keep = self._booked_of(new_items)
        try:
            self.db.clear_todos(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state._commit_todo_day.clear", _amo_exc)
        for item in keep:
            self.add_todo(day, item, source=str(item.get("source") or source or ""))
        try:
            self.db.delete_schedule(day)
            self.schedule.pop(day, None)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state._commit_todo_day.schedule", _amo_exc)
        try:
            version = self.db.bump_schedule_version(day)
        except Exception:
            version = 0
        return {"changed": bool(keep), "version": version, "changed_hours": [],
                "cancelled": [], "todo": True}

    def migrate_future_todos(self, today=None):
        """启动时把旧库里已经铺好的未来日程搬进待办表（并且清掉那些日程块）。"""
        today = str(today or today_str())
        moved = 0
        try:
            rows = self.db.conn.execute(
                "SELECT day, items FROM schedule WHERE day > ?", (today,)
            ).fetchall()
        except Exception as exc:
            _note_swallowed("amo_human_state.migrate_future_todos", exc)
            return 0
        for row in rows:
            day = str(row["day"] or "")
            try:
                items = json.loads(row["items"] or "[]")
            except Exception:
                items = []
            for item in self._booked_of(items if isinstance(items, list) else []):
                self.add_todo(day, item, source="migrate")
                moved += 1
            try:
                self.db.delete_schedule(day)
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.migrate_future_todos.delete", _amo_exc)
            self.schedule.pop(day, None)
        if moved:
            logger.info("[amo] 未来日程已搬进待办：%d 条（不再提前铺满明天）", moved)
        return moved

    def _todo_for_hour(self, day, hour):
        """这一小时有没有锚点待办（例如“和柳如烟逛街”）。"""
        try:
            begin = int(hour) * 60
            for item in self.todos(day):
                start = str(item.get("start") or "")
                try:
                    hh, mm = start.split(":")[:2]
                    minute = int(hh) * 60 + int(mm)
                except Exception:
                    continue
                if begin <= minute < begin + 60:
                    return item
        except Exception as exc:
            _note_swallowed("amo_human_state._todo_for_hour", exc)
        return None

    def _todo_plan(self, anchor):
        """把待办落实成这一小时的真实日程（含扣款），其余时间仍走盲盒。"""
        action = str(anchor.get("activity") or "按约出门")
        place = str(anchor.get("place") or "家")
        try:
            cost = int(anchor.get("cost_cents") or 0)
        except Exception:
            cost = 0
        paid = False
        if cost:
            try:
                paid = bool(self.p.wallet.debit(
                    cost, action, "todo:%s:%s" % (anchor.get("day"), anchor.get("start"))))
            except Exception:
                paid = False
            if not paid:
                cost = 0
        plan = {"action": action, "place": place,
                "zone": str(anchor.get("zone") or ""),
                "category": str(anchor.get("category") or "social"),
                "cost_cents": cost, "diary": str(anchor.get("detail") or ""),
                "_todo_id": int(anchor.get("id") or 0)}
        return plan, cost, paid

    def _booked_items(self, day):
        """已经定下来的事：优先读待办表（未来事件），再兼容旧日程里的固定行程。"""
        todo = self.todos(day)
        if todo:
            return [dict(item) for item in todo]
        raw = self.schedule.get(day) or self.db.get_schedule(day) or []
        return self._booked_of(raw)

    def _booked_tree(self, day, items=None):
        """未来日期：只展示已经定下来的行程（约会/随礼/请假/看病…）。"""
        items = items if isinstance(items, list) else self._booked_items(day)
        if not items:
            return []
        prov = self._schedule_provenance(day)
        slots: list[dict[str, Any]] = []
        for item in items:
            try:
                begin = parse_hhmm(item.get("start"))
            except Exception:
                continue
            end_text = str(item.get("end") or "").strip()
            try:
                finish = 24 * 60 if end_text in ("24:00", "0:00", "00:00") else parse_hhmm(end_text)
            except Exception:
                finish = begin + 60
            if finish <= begin:
                finish = min(24 * 60, begin + 60)
            slots.append({
                "start_min": begin, "end_min": finish,
                "activity": str(item.get("activity") or ""),
                "place": str(item.get("place") or ""),
                "category": str(item.get("category") or "social"),
                "zone": str(item.get("zone") or ""),
                "detail": str(item.get("detail") or ""),
            })
        tree = []
        for block_start, block_end, title in self._JIT_BLOCKS:
            bstart = parse_hhmm(block_start)
            bend = 24 * 60 if block_end == "24:00" else parse_hhmm(block_end)
            children = []
            for slot in slots:
                if slot["start_min"] < bstart or slot["start_min"] >= bend:
                    continue
                child = {
                    "id": "%s|%s-%s|%s" % (day, slot["start_min"], slot["end_min"], slot["category"]),
                    "start": self._fmt_minutes(slot["start_min"]),
                    "end": self._fmt_minutes(slot["end_min"]),
                    "activity": slot["activity"],
                    "place": slot["place"],
                    "category": slot["category"],
                    "zone": slot.get("zone", ""),
                    "detail": slot.get("detail", ""),
                    "current": False,
                    "doing": False,
                }
                child.update(self._row_provenance(day, {"start": child["start"],
                                                        "end": child["end"],
                                                        "activity": slot["activity"]}, prov))
                children.append(child)
            if not children:
                continue
            block = {"start": block_start, "end": block_end, "title": title,
                     "date": day, "children": children, "current": False}
            block["activity"] = ""
            block["place"] = ""
            block["facts"] = {
                "part": title, "start": block_start, "end": block_end,
                "events": [{"time": "%s-%s" % (c["start"], c["end"]),
                            "activity": c["activity"], "place": c["place"],
                            "category": c["category"]} for c in children],
            }
            tree.append(block)
        return tree

    def _jit_schedule_tree(self, day, items=None):
        now_min = minutes_now()
        prov = self._schedule_provenance(day)
        slots = self._merge_quiet_slots(
            self._jit_visible_slots(self._jit_items(day, items=items), now_min)
        )
        tree = []
        for block_start, block_end, title in self._JIT_BLOCKS:
            bstart = parse_hhmm(block_start)
            bend = 24 * 60 if block_end == "24:00" else parse_hhmm(block_end)
            children = []
            for slot in slots:
                if slot["start_min"] < bstart or slot["start_min"] >= bend:
                    continue
                child = {
                    "id": "%s|%s-%s|%s" % (day, slot["start_min"], slot["end_min"], slot["category"]),
                    "start": slot["sleep_from"] or self._fmt_minutes(slot["start_min"]),
                    "end": "入睡" if slot["open_sleep"] else self._fmt_minutes(slot["end_min"]),
                    "activity": slot["activity"],
                    "place": slot["place"],
                    "category": slot["category"],
                    "zone": slot.get("zone", ""),
                    "transit": slot.get("transit", ""),
                    "vehicle": slot.get("vehicle", ""),
                    "detail": slot.get("detail", ""),
                    "event": slot.get("event", ""),
                    "mood": slot.get("mood", ""),
                    "current": bool(slot.get("current")),
                    "doing": bool(slot.get("doing")),
                    "open_sleep": bool(slot.get("open_sleep")),
                    "sleep_from": slot.get("sleep_from", ""),
                }
                child.update(self._row_provenance(day, {
                    "start": self._fmt_minutes(slot["start_min"]),
                    "end": self._fmt_minutes(slot["end_min"]),
                    "activity": slot["activity"]}, prov))
                children.append(child)
            if not children:
                continue
            block_title = title
            if (block_start == "00:00" and block_end == "06:00"
                    and all(str(c.get("category") or "") == "sleep" for c in children)):
                block_title = "深度睡眠"
            block = {
                "start": block_start, "end": block_end, "title": block_title,
                "date": day, "children": children,
                "current": any(child["current"] for child in children),
            }
            block["activity"] = ""
            block["place"] = ""
            block["facts"] = {
                "part": block_title,
                "start": block_start,
                "end": block_end,
                "events": [
                    {
                        "time": (child.get("start", "") + "-" + child.get("end", "")),
                        "activity": child.get("activity", ""),
                        "place": child.get("place", ""),
                        "category": child.get("category", ""),
                    }
                    for child in children
                ],
            }
            tree.append(block)
        return tree
