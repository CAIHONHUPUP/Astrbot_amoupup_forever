# -*- coding: utf-8 -*-
"""日程面板 API：从 amo_api.py 拆出的 handler mixin。"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import re
import time

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

try:
    from .amo_shared import (MOOD_KEYS, clean_ui_value, clamp, day_meta, logger,
    today_str, web_request)
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import (MOOD_KEYS, clean_ui_value, clamp, day_meta, logger,
        today_str, web_request)

try:  # 块级小日记：块身份 / 开关
    from .amo_state_common import block_diary_enabled, block_id
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_common import block_diary_enabled, block_id

try:  # 时间轴：块读取的唯一入口（第 1 阶段重构）
    from .amo_timeline import Timeline
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_timeline import Timeline


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


def _detail_block_ended(day, start, end, now=None):
    """详情页：块还没走完就不展示整点日记（否则“进行中”的块会提前出日记）。"""
    try:
        from .amo_state_common import slot_has_ended as _slot_ended
    except ImportError:
        from amo_state_common import slot_has_ended as _slot_ended
    try:
        return bool(_slot_ended(str(day or ""), str(start or ""), str(end or ""), now=now))
    except Exception:
        return True


#: 可信的块日记状态（stale 是时间口径歪掉的旧行，不能当缓存回放）
_TRUSTED_DIARY_STATUS = ("valid", "frozen", "archived")


def _block_diary_text(plugin, day, item, start="", end="", raw_item=None, blocks=None):
    """按请求块读 block_diary：块身份 = block_id，没有就交给块重新生成。

    blocks（当天真实日程块）给了就读侧兜底：记录窗口对不上任何现存块的行是孤儿
    （2026-11-17 实锤：06:12-06:22 块被删，block_diary 还挂着 valid），不展示。

    第 1 阶段重构：删除旧整点回退链（legacy:<start> / schedule_diary 的 item.start）。
    旧表只读、只留给 22:00 的日报与 block_diary_enabled=false 的回滚路径，
    详情/调度/日记不再按小时猜日记，也就不会出现「两套钥匙各写各的」。
    """
    if not block_diary_enabled(plugin):
        return ""
    row = dict(item or {})
    row["start"] = str(row.get("start") or start or "")
    row["end"] = str(row.get("end") or end or "")

    existing = None
    if blocks is not None:
        existing = {(str(b.get("start") or "")[:5], str(b.get("end") or "")[:5])
                    for b in blocks if isinstance(b, dict)}

    def _trusted(record):
        if not isinstance(record, dict):
            return ""
        if str(record.get("status") or "valid") not in _TRUSTED_DIARY_STATUS:
            return ""
        if existing is not None:
            rec_start = str(record.get("start") or "")
            rec_end = str(record.get("end") or "")
            # 没有窗口的老记录（兼容测试/早期数据）不判定；有窗口就必须对得上现存块。
            if rec_start and rec_end and (rec_start[:5], rec_end[:5]) not in existing:
                return ""
        return str(record.get("text") or "").strip()

    record_fn = getattr(plugin.db, "get_block_diary_record", None)
    getter = getattr(plugin.db, "get_block_diary", None)
    # 请求区间可能被面板夹过（24:00 → 23:00）；原始日程块的身份也要试一次，
    # 否则调度按原始块写的行、详情按夹过的区间读，又会回到“永远 generating”。
    keys = [block_id(day, row)]
    if isinstance(raw_item, dict):
        raw_key = block_id(day, raw_item)
        if raw_key not in keys:
            keys.append(raw_key)
    for block_key in keys:
        if callable(record_fn):
            try:
                current = record_fn(day, block_key)
            except Exception as _amo_exc:
                _note_swallowed("amo_api_schedule._block_diary_text", _amo_exc)
                current = None
            text = _trusted(current)
            if text:
                return text
        elif callable(getter):
            try:
                text = str(getter(day, block_key) or "").strip()
            except Exception as _amo_exc:
                _note_swallowed("amo_api_schedule._block_diary_text.block", _amo_exc)
                text = ""
            if text:
                return text
    return ""


def _minutes_of(text):
    """HH:MM → 分钟；解析不了返回 None。"""
    body = str(text or "").strip()[:5]
    hour_text, _, minute_text = body.partition(":")
    try:
        hour = int(hour_text)
        minute = int(minute_text or 0)
    except (TypeError, ValueError):
        return None
    if hour < 0 or hour > 24 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def _diary_spawn_items(plugin, items):
    """块模式把 Timeline 的块交给 expand_block；旧整点只属于 [deprecated] 回滚路径。"""
    try:
        if block_diary_enabled(plugin):
            return list(items or [])
        return plugin.state._hourly_schedule(items)
    except Exception as _amo_exc:
        _note_swallowed("amo_api_schedule._diary_spawn_items", _amo_exc)
        return list(items or [])


def _detail_clock(text):
    """面板时间 → HH:MM；24:00 是合法的“当天结束”，不能被夹成 23:00。"""
    matched = re.match(r"^(\d{1,2}):(\d{2})$", str(text or "").strip()[:5])
    if not matched:
        return ""
    hour = int(matched.group(1))
    minute = int(matched.group(2))
    if hour == 24 and minute == 0:
        return "24:00"
    return "%02d:%02d" % (max(0, min(23, hour)), max(0, min(59, minute)))


def _detail_blocks(plugin, day, start, end, item_id):
    """详情第一步：取块（统一 Timeline：排序/去重/空 end 防御），返回 (items, item, raw_item, 拦截)。"""
    items = Timeline(plugin).blocks(day)
    if day == today_str() and not items:
        return [], None, None, {"code": 0, "data": {
            "day": day, "start": start, "end": end, "status": "generating",
            "message": "她正在写日记呢，过几秒再刷新看看~"}}
    item = plugin.state.resolve_schedule_item(day, start, end, item_id)
    raw_item = dict(item) if item is not None else None
    if item is not None and end:
        # 面板点的可能是分钟级块（例如 13:00-13:41）：以请求的精确区间为准，
        # 不能被 _hourly_schedule 归一成 13:00-14:00，否则 13:41 已结束还会被当成“还没过完”。
        item = dict(item, start=start, end=end)
    return items, item, raw_item, None


def _detail_pending(plugin, day, start, end, item):
    """详情第二步：还没过完的块只给 pending，不读缓存、不入队。"""
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
                "empty_message": "这段时间还没过完呢，等她做完再来看吧~"}}
        return None
    if day == today_str() and not _detail_block_ended(day, start, item.get("end") or end):
        return {"code": 0, "data": {
            "day": day, "start": item.get("start", start), "end": item.get("end") or end,
            "diary": "", "pending": True,
            "empty_message": "这一小时还没过完呢，等她忙完再看吧~"}}
    return None


def _detail_read_cache(plugin, day, item, start, end, raw_item, blocks=None):
    """详情第三步：只读块身份对应的 block_diary；旧整点表不再作为回退。

    blocks 用来挡孤儿行：记录的 day+start+end 对不上现存日程块就不展示。
    """
    try:
        return _block_diary_text(plugin, day, item, start, end, raw_item, blocks)
    except Exception as _amo_exc:
        _note_swallowed("amo_api_schedule.api_schedule_detail.cache", _amo_exc)
        return ""


def _detail_spending(plugin, day, start, end):
    """本时段消费（只读）：块窗口内 wallet 支出；金额分→元。"""
    try:
        begin = _minutes_of(start)
        finish = _minutes_of(end)
        if begin is None:
            return {"spending": [], "spending_total_yuan": 0.0}
        if finish is None or finish <= begin:
            finish = begin + 60
        base = time.mktime(time.strptime(str(day or "")[:10], "%Y-%m-%d"))
    except (TypeError, ValueError, OverflowError):
        return {"spending": [], "spending_total_yuan": 0.0}
    wallet = getattr(plugin, "wallet", None) or getattr(plugin.state, "wallet", None)
    rows_between = getattr(wallet, "rows_between", None)
    if not callable(rows_between):
        return {"spending": [], "spending_total_yuan": 0.0}
    try:
        rows = rows_between(base + begin * 60, base + finish * 60) or []
    except Exception as _amo_exc:
        _note_swallowed("amo_api_schedule._detail_spending", _amo_exc)
        return {"spending": [], "spending_total_yuan": 0.0}
    items = []
    total = 0.0
    # 只有现金账户的支出才算“消费”：withdrawable 的「可取额度随现金下调」
    # 是同一笔花钱的额度联动（account=withdrawable / category=账户调整），
    # 再算一次会把一碗 20 的面显示成 40（2026-11-23 线上）。
    excluded = getattr(wallet, "_expense_excluded", None)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("account") or "cash") != "cash":
            continue
        if callable(excluded):
            try:
                if excluded(row):
                    continue
            except Exception as _amo_exc:  # cleanup: 判据不可用就只按账户过滤
                _note_swallowed("amo_api_schedule._detail_spending.excluded", _amo_exc)
        cents = int(row.get("delta_cents") or 0)
        if cents >= 0:
            continue  # 只展示支出；工资/退款不算“消费”
        yuan = round(abs(cents) / 100.0, 2)
        total += yuan
        try:
            clock = time.strftime("%H:%M", time.localtime(float(row.get("ts") or 0)))
        except (TypeError, ValueError, OverflowError):
            clock = ""
        items.append({
            "time": clock,
            "reason": str(row.get("item_name") or row.get("reason") or ""),
            "amount_yuan": yuan,
            "category": str(row.get("category") or ""),
        })
    return {"spending": items, "spending_total_yuan": round(total, 2)}


def _detail_fallback_reply(day, start, end, item):
    return {"code": 0, "data": {
        "day": day, "start": start, "end": end or item.get("end", ""),
        "diary": "", "fallback": True,
        "empty_message": "这一小时的小日记没写下来，先空着啦，等下次刷新再补。"}}


def _detail_enqueue(plugin, day, item, items, start, end, reason="panel_get"):
    """详情第四步：入队后台生成；过去日期已归档，入队只会永远 generating，直接给兜底文案。"""
    if day < today_str():
        return {"code": 0, "data": {
            "day": day, "start": item.get("start", start), "end": end or item.get("end", ""),
            "diary": plugin._ui_gap_line(item), "archived": True, "fallback": True,
            "empty_message": "这一天的日记已经归档了，没有留下这一块的小记。"}}
    try:
        spawn = (getattr(plugin, "_spawn_diary_task", None)
                 or getattr(plugin.state, "_spawn_diary_task", None))
        if callable(spawn):
            spawn(day, item, _diary_spawn_items(plugin, items),
                  plugin.db.schedule_version(day), reason=reason)
    except Exception as _amo_exc:
        _note_swallowed("amo_api_schedule.api_schedule_detail.enqueue", _amo_exc)
    return {"code": 0, "data": {
        "day": day, "start": start, "end": end or item.get("end", ""),
        "status": "generating",
        "message": "她正在写日记呢，过几秒再刷新看看~"}}


def _detail_gap_reply(plugin, day, start, end):
    """合并出来的空档（如「20:27-22:00 窝在家里的晚上」）：把区间里已有的小日记带出来。"""
    try:
        try:
            from .amo_human_state import parse_hhmm as _hhmm
        except ImportError:
            from amo_human_state import parse_hhmm as _hhmm
        begin_h = min(23, int(_hhmm(start) // 60))
        stop_h = 24 if not end else max(begin_h + 1, min(24, int(_hhmm(end) // 60)))
        found = ""
        for hour in range(begin_h, stop_h):
            text = plugin.state.db.get_diary(day, "%02d:00" % hour) or ""
            if str(text).strip():
                found = text
        if found:
            return {"code": 0, "data": {
                "day": day, "start": start, "end": end or start, "diary": found,
                "synthetic": True}}
    except Exception as _amo_exc:
        _note_swallowed("amo_api_schedule.gap_diary", _amo_exc)
    return {"code": 0, "data": {
        "day": day, "start": start, "end": end or start, "diary": "",
        "empty_message": "这一段是空档补出来的，没有单独的小记。",
        "synthetic": True}}


def _detail_payload(plugin, day, item_id, start, end, item, diary):
    """命中缓存后的最终回包：清洗正文 + 补 activity/空档兜底。"""
    payload = {"day": day, "start": item.get("start", start), "end": item.get("end", ""),
               "id": item_id, "diary": diary, "activity": str(item.get("activity") or ""),
               "pending": False}
    if str(payload.get("activity") or "").strip() in ("", "未记录"):
        payload["activity"] = plugin._gap_label(item)
    try:
        from .schedule_engine import ScheduleEngine as _SE
    except ImportError:
        from schedule_engine import ScheduleEngine as _SE
    _diary_text = _SE._clean_diary(payload.get("diary") or "")
    if any(word in _diary_text for word in ("到家", "沙发", "睡衣", "卧室", "被窝", "出租屋")):
        _diary_text = _SE._fix_home_diary(_diary_text)
    payload["diary"] = _diary_text
    if not str(payload.get("diary") or "").strip():
        payload["diary"] = plugin._ui_gap_line(item)
        payload["fallback"] = True
    if str(payload.get("activity") or "").strip() in ("", "未记录"):
        payload["activity"] = plugin._gap_label(item)
    payload.update(_detail_spending(plugin, day, str(item.get("start") or start),
                                    str(item.get("end") or end)))
    return {"code": 0, "data": payload}


# ---------- B3：面板日程块删除/修改（写入口的校验/审计口径）----------
SCHEDULE_EDIT_MIN_MINUTES = 10
SCHEDULE_AUDIT_KEY = "schedule_edit_audit"
SCHEDULE_AUDIT_LIMIT = 100


def _schedule_clock(text):
    """'H:MM' / 'HH:MM' → 当天分钟数；非法返回 None。"""
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(text or "").strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour < 0 or hour > 24 or minute < 0 or minute > 59:
        return None
    total = hour * 60 + minute
    return total if 0 <= total <= 24 * 60 else None


def _schedule_hm(minutes):
    """分钟数 → 'HH:MM'；1440 记作 '24:00'（当天的结束边界）。"""
    total = max(0, min(24 * 60, int(minutes)))
    return "%02d:%02d" % divmod(total, 60)


def _schedule_truthy(value):
    """请求体里的布尔：面板可能发 true / "true" / 1 / "1"。"""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def _schedule_row_ids(item):
    item = item if isinstance(item, dict) else {}
    return [str(item.get(key) or "") for key in ("id", "event_id", "block_id")]


def _schedule_row_label(item):
    item = item if isinstance(item, dict) else {}
    return "%s-%s %s" % (str(item.get("start") or ""), str(item.get("end") or ""),
                         str(item.get("activity") or ""))


#: 审计要能完整回滚一块：这些字段按现有块 schema 原样存进 before/after。
#: 只放标量字段；effects 这类整块 dict 不抄进 kv，恢复时留在当前块上。
SCHEDULE_AUDIT_BLOCK_FIELDS = (
    "id", "event_id", "block_id", "start", "end", "activity", "place",
    "category", "zone", "detail", "source", "transit", "vehicle",
    "spot", "event", "mood", "mood_valence", "subject",
    "step", "transit_kind", "open_sleep", "sleep_from", "happened", "soothe",
)
#: 摘要之外的字段：判「已经是这一版」时也要一起比，否则只改 category 的块会被当成已恢复。
SCHEDULE_AUDIT_EXTRA_FIELDS = tuple(
    key for key in SCHEDULE_AUDIT_BLOCK_FIELDS
    if key not in ("id", "event_id", "block_id", "start", "end", "activity", "place")
)


def _schedule_audit_row(item):
    """审计里存整块字段（老数据只有摘要）：恢复要原样还原，缺字段不算坏值。"""
    item = item if isinstance(item, dict) else {}
    return {key: item.get(key) for key in SCHEDULE_AUDIT_BLOCK_FIELDS
            if item.get(key) not in (None, "")}


def _schedule_audit_visible(plugin, day="", limit=20):
    """只读审计：老 fake/旧版本没有 _schedule_audit_rows 时安静返回空表。"""
    fn = getattr(plugin, "_schedule_audit_rows", None)
    if not callable(fn):
        return []
    try:
        return fn(day, limit=limit)
    except Exception as _amo_exc:
        _note_swallowed("amo_api_schedule.schedule_audit_visible", _amo_exc)
        return []


class ScheduleApiMixin(_MixinBase):
    _status_cache: dict | None  # 与 CommandMixin 同一类型（/status 两秒缓存）
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
        self._status_cache = None  # 心情变了，下一次 /status 必须重算
        try:  # task-30：mood_update 返回的快照也走场景账本（只改 activity，形状不变）
            from .amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        except ImportError:
            from amo_state_snapshot import snapshot_with_scene as _snap_with_scene
        return {"code": 0, "data": _snap_with_scene(self)}

    def _kick_schedule_titles(self, day):
        """后台补 4 字小标题：同一日期 5 分钟最多一次，GET 不等 LLM。"""
        try:
            stamps = getattr(self, "_schedule_title_kick", None)
            if not isinstance(stamps, dict):
                stamps = {}
                self._schedule_title_kick = stamps
            now = time.monotonic()
            if float(stamps.get(day) or 0) > now - 300:
                return False
            stamps[day] = now
            spawn = getattr(self, "_spawn", None)
            if callable(spawn):
                spawn(self._bg_enrich_schedule(day))
                return True
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.kick_schedule_titles", _amo_exc)
        return False

    async def _bg_enrich_schedule(self, day):
        try:
            try:
                from .amo_timeline import Timeline
            except ImportError:
                from amo_timeline import Timeline
            items = Timeline(self.state).blocks(day)
            builder = getattr(self.state, "schedule_tree_from_items", None)
            tree = builder(day, items) if callable(builder) else self.state.schedule_tree(day)
            await self.state.enrich_schedule_tree(day, tree, getattr(self, "_last_umo", "") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.bg_enrich_schedule", _amo_exc)


    async def api_schedule(self):
        await self._ensure_ready()
        data = await self._body()
        day = str(data.get("date") or self._q("date", "") or today_str())
        # P2-1：一次 GET 只读一份日程。读作用域让 items 与 current_activity 共用
        # Timeline.raw 的同一次读取；作用域只覆盖同步读，函数返回/异常必清。
        try:
            self.state._schedule_read_scope = {"day": day}
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.api_schedule.read_scope", _amo_exc)
        try:
            if day == today_str():
                # 第八轮 D：GET 纯只读——不调 ensure_schedule、不跑 LLM；缺数据返回 generating，
                # 真正的生成交给后台 tick（主干 ensure 在 _diary_scheduler_tick 里补）。
                try:
                    items = Timeline(self.state).blocks(day)
                except Exception as _amo_exc:
                    _note_swallowed("amo_api_schedule.api_schedule.read", _amo_exc)
                    items = []
                current = clean_ui_value(self.state.current_activity())
                if not items:
                    return {"code": 0, "data": {
                        "day": day, "status": "generating",
                        "message": "她正在写日记呢，过几秒再刷新看看~",
                        "meta": clean_ui_value(day_meta(day)), "items": [], "tree": [],
                        "current": current,
                        "context": {}, "blind": "",
                        "audit": clean_ui_value(_schedule_audit_visible(self, day)),
                    }}
            else:
                # 未来日期只回「已经定下来的行程」，不铺 24 小时假休息
                items = self.state._booked_items(day)
                current = clean_ui_value(self.state.current_activity())
        finally:
            try:
                self.state._schedule_read_scope = None
            except Exception as _amo_exc:
                _note_swallowed("amo_api_schedule.api_schedule.read_scope_clear", _amo_exc)
        # 历史数据可能残留互相重叠的块（早期 JIT 与过渡并存）：读时统一去重叠并落库，
        # 保证面板时间轴是一条直线（新块互斥之外的第二道保险）。
        try:
            try:
                from .amo_transition import normalize_day as _normalize_day
            except ImportError:
                from amo_transition import normalize_day as _normalize_day
            # 只做展示层归一：读接口绝不改写历史数据（写回曾在 14:1x 误删过当天日程）。
            original = list(items or [])
            fixed, changed = _normalize_day(items or [])
            if changed:
                items = fixed
                # 归一删/改掉了与此刻重叠的块：状态也要跟着对账，不能块没了人还在原地。
                reconcile = getattr(self.state, "_reconcile_manual_schedule_edit", None)
                if callable(reconcile):
                    reconcile(day, original, fixed, source="normalize")
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.normalize_overlaps", _amo_exc)
        # P2：items 已经读过一次；真实 state 用同一份数据建树（GET 不再二次全日程读）。
        # 极简 fake（只有 schedule_tree(day)）仍走原接口。
        builder = getattr(self.state, "schedule_tree_from_items", None)
        if callable(builder):
            tree = builder(day, items)
        else:
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
        # 组标题：GET 只贴缓存；缺标题的组丢到后台让 LLM 起 4 字小标题（下次刷新可见）。
        try:
            apply = getattr(self.state, "apply_schedule_summaries", None)
            if callable(apply):
                tree = apply(day, tree) or tree
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.api_schedule.summaries", _amo_exc)
        try:
            if any(isinstance(block, dict) and isinstance(block.get("facts"), dict)
                   and block["facts"].get("events") and not block.get("summary")
                   for block in (tree or [])):
                self._kick_schedule_titles(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.api_schedule.kick_titles", _amo_exc)
        # 第八轮 D：组标题的 LLM 补写也移到后台，GET 只读已缓存的标题。
        return {"code": 0, "data": {
            "day": day, "meta": clean_ui_value(day_meta(day)),
            "items": clean_ui_value(_rows_displayed(self, items)),
            "tree": clean_ui_value(_tree_displayed(self, tree)),
            "current": current,
            "context": clean_ui_value(self.state.schedule_context(day)),
            "blind": clean_ui_value(self.state.schedule_blind_hint(day)),
            # B3b/B3c：审计只读入口复用既有 GET，恢复走 /schedule/block/restore。
            "audit": clean_ui_value(_schedule_audit_visible(self, day)),
        }}

    # ---------- B3：面板日程块删除/修改 ----------
    def _schedule_edit_items(self, day):
        """读当天真实日程行（表优先、kv 兜底）：编辑只针对落库的块，不碰展示层补段。"""
        try:
            rows = self.db.get_schedule(day)
            if rows:
                return [dict(row) for row in rows if isinstance(row, dict)]
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.schedule_edit.read_db", _amo_exc)
        try:
            rows = (self.state.schedule or {}).get(day)
            return [dict(row) for row in (rows or []) if isinstance(row, dict)]
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.schedule_edit.read_kv", _amo_exc)
            return []

    @staticmethod
    def _schedule_find_block(items, block_id="", start=""):
        block_id = str(block_id or "").strip()
        start = str(start or "").strip()[:5]
        for index, row in enumerate(items or []):
            if block_id and block_id in _schedule_row_ids(row):
                return index, row
            if block_id and block_id == "%s-%s" % (str(row.get("start") or ""),
                                                   str(row.get("end") or "")):
                return index, row
        if start:
            for index, row in enumerate(items or []):
                if str(row.get("start") or "")[:5] == start:
                    return index, row
        return -1, None

    @staticmethod
    def _schedule_find_overlap(items, begin, finish, skip_index=-1):
        for index, row in enumerate(items or []):
            if index == skip_index:
                continue
            other_begin = _schedule_clock(row.get("start"))
            other_finish = _schedule_clock(row.get("end"))
            if other_begin is None or other_finish is None:
                continue
            if other_finish <= other_begin:
                other_finish = min(24 * 60, other_begin + 60)
            if min(finish, other_finish) > max(begin, other_begin):
                return row
        return None

    def _schedule_operator(self, data=None):
        """操作人：面板请求体 > 反代用户名头 > 「面板」。没有登录态时至少留来源。"""
        data = data if isinstance(data, dict) else {}
        for key in ("operator", "username", "user"):
            value = str(data.get(key) or "").strip()
            if value:
                return value[:40]
        try:
            headers = getattr(web_request, "headers", None) if web_request is not None else None
            if headers is not None:
                for key in ("x-amo-operator", "x-username", "x-user-name"):
                    value = str(headers.get(key) or "").strip()
                    if value:
                        return value[:40]
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.schedule_operator.header", _amo_exc)
        return "面板"

    def _schedule_audit(self, action, day, before=None, after=None, reason="", operator=""):
        """日程手动编辑审计：logger 一行 + kv 环形日志（面板/排障可回查）。"""
        ts = _amo_clock.now_ts()
        entry = {"id": "%s-%s" % (ts, str(action or "")),
                 "ts": ts, "action": str(action or ""), "day": str(day or ""),
                 "operator": str(operator or "面板")[:40],
                 "before": _schedule_audit_row(before), "after": _schedule_audit_row(after),
                 "reason": str(reason or ""), "source": "panel"}
        logger.info("[amo] schedule_edit action=%s day=%s operator=%s before=%s after=%s reason=%s",
                    entry["action"], entry["day"], entry["operator"], entry["before"],
                    entry["after"], entry["reason"])
        rows = []
        try:
            rows = list(self.db.kv_get(SCHEDULE_AUDIT_KEY, []) or [])
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.schedule_audit.read", _amo_exc)
        rows = rows[-(SCHEDULE_AUDIT_LIMIT - 1):]
        rows.append(entry)
        try:
            self.db.kv_set(SCHEDULE_AUDIT_KEY, rows)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.schedule_audit.write", _amo_exc)
        return entry

    def _schedule_audit_rows(self, day="", limit=20):
        """只读审计：最新的在前；时间转成人话，before/after 保留整块字段（老数据只有摘要）。"""
        try:
            rows = list(self.db.kv_get(SCHEDULE_AUDIT_KEY, []) or [])
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.schedule_audit_rows.read", _amo_exc)
            rows = []
        limit = max(1, int(limit or 20))
        output = []
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            if day and str(row.get("day") or "") != str(day):
                continue
            try:
                ts = int(row.get("ts") or 0)
            except Exception:
                ts = 0
            time_text = ""
            if ts:
                try:
                    time_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
                except Exception:
                    time_text = ""
            output.append({
                "id": str(row.get("id") or ts),
                "ts": ts, "time": time_text,
                "day": str(row.get("day") or ""),
                "action": str(row.get("action") or ""),
                "operator": str(row.get("operator") or "面板"),
                "before": _schedule_audit_row(row.get("before")),
                "after": _schedule_audit_row(row.get("after")),
                "reason": str(row.get("reason") or ""),
            })
            if len(output) >= limit:
                break
        return output

    def _schedule_commit_edit(self, day, new_items, old_items, action, before, after,
                              reason="", operator=""):
        """手动改/删/恢复的唯一落库：网关读改写 → 对账 → 审计。

        面板调用方已经按旧快照算好了 new_items；真实 state 有 apply_schedule 时，
        在网关锁内以「当前块」为基座重放同一动作（块在面板读完后被别处改掉也能
        找到它；确实找不到就按没改成回滚），再走唯一提交入口 commit_schedule_change。
        极简假件（只有 commit_schedule_change）仍按老签名兜底。
        """
        written = {"items": list(new_items or [])}

        def _mutate(current):
            before_row = before if isinstance(before, dict) else {}
            after_row = after if isinstance(after, dict) else None
            key_id = str(before_row.get("id") or "").strip()
            key_start = str(before_row.get("start") or "").strip()[:5]
            index = None
            for pos, row in enumerate(current or []):
                if not isinstance(row, dict):
                    continue
                if key_id and key_id in _schedule_row_ids(row):
                    index = pos
                    break
                if key_start and str(row.get("start") or "").strip()[:5] == key_start:
                    index = pos
                    break
            if action == "delete":
                if index is None:
                    return None
                merged = [row for pos, row in enumerate(current) if pos != index]
            elif after_row is not None:
                merged = list(current or [])
                if index is None:
                    if action != "restore":
                        return None
                    merged.append(dict(after_row))
                else:
                    merged[index] = dict(after_row)
                merged.sort(key=lambda row: _schedule_clock((row or {}).get("start")) or 0)
            else:
                merged = list(new_items or [])
            written["items"] = merged
            return merged

        apply = getattr(self.state, "apply_schedule", None)
        try:
            if callable(apply):
                result = apply(day, _mutate, source="manual_edit_api",
                               reason=str(action or ""))
            else:
                result = self.state.commit_schedule_change(
                    day, new_items, old_items=old_items,
                    source="manual_edit_api", reason=str(action or ""))
        except Exception as exc:
            logger.warning("[amo] 日程手动%s落库失败: %s", action, exc)
            return {"code": 1, "msg": "落库失败：%s" % exc}
        if not isinstance(result, dict) or not result.get("changed"):
            return {"code": 1, "msg": "没改成（可能和其它块重叠或被保护），刷新看看"}
        reconcile = getattr(self.state, "_reconcile_manual_schedule_edit", None)
        if callable(reconcile):
            try:
                reconcile(day, old_items, written["items"], source="panel_" + str(action or ""))
            except Exception as _amo_exc:
                _note_swallowed("amo_api_schedule.schedule_commit.reconcile", _amo_exc)
        entry = self._schedule_audit(action, day, before, after, reason=reason,
                                      operator=operator)
        return {"code": 0, "data": {"day": day, "action": str(action or ""), "changed": True,
                                    "version": result.get("version"), "items": written["items"],
                                    "audit": {"ts": entry["ts"], "action": entry["action"]}}}

    @staticmethod
    def _schedule_block_key(row):
        row = row if isinstance(row, dict) else {}
        return (str(row.get("start") or ""), str(row.get("end") or ""),
                str(row.get("activity") or ""))

    @classmethod
    def _schedule_audit_entries(cls, rows, limit=200):
        """把 kv schedule_audit:<day> 的原始行归一成面板可直接渲染的结构（最新在前）。"""
        limit = max(1, min(200, int(limit or 200)))
        output = []
        for row in reversed(list(rows or [])):
            if not isinstance(row, dict):
                continue
            try:
                ts = int(row.get("ts") or 0)
            except (TypeError, ValueError):
                ts = 0
            time_text = ""
            if ts:
                try:
                    time_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
                except Exception:
                    time_text = ""
            try:
                version = int(row.get("version") or 0)
            except (TypeError, ValueError):
                version = 0
            rejected = row.get("rejected") or []
            hours = []
            for hour in row.get("changed_hours") or []:
                try:
                    hours.append(int(hour))
                except (TypeError, ValueError):
                    continue
            output.append({
                "ts": ts, "time": time_text,
                "source": str(row.get("source") or ""),
                "reason": str(row.get("reason") or ""),
                "version": version,
                "changed_hours": hours,
                "rejected": [str(item) for item in rejected][:20]
                if isinstance(rejected, list) else [],
                "before": [dict(item) for item in (row.get("before") or [])
                           if isinstance(item, dict)],
                "after": [dict(item) for item in (row.get("after") or [])
                          if isinstance(item, dict)],
            })
            if len(output) >= limit:
                break
        return output

    @classmethod
    def _schedule_audit_replay(cls, entries):
        """按时间顺序用 before/after 串成「谁在几点改了什么」，直接给面板渲染。"""
        replay = []
        ordered = sorted(entries or [], key=lambda row: int(row.get("ts") or 0))
        for entry in ordered:
            before = entry.get("before") or []
            after = entry.get("after") or []
            before_keys = {cls._schedule_block_key(row) for row in before}
            after_keys = {cls._schedule_block_key(row) for row in after}
            added = [row for row in after if cls._schedule_block_key(row) not in before_keys]
            removed = [row for row in before if cls._schedule_block_key(row) not in after_keys]
            rejected = entry.get("rejected") or []
            parts = []
            for row in removed:
                parts.append("删掉 %s-%s「%s」" % (row.get("start") or "", row.get("end") or "",
                                                  row.get("activity") or ""))
            for row in added:
                parts.append("加上 %s-%s「%s」" % (row.get("start") or "", row.get("end") or "",
                                                  row.get("activity") or ""))
            if rejected:
                parts.append("体检拒绝：" + "；".join(str(item) for item in rejected[:3]))
            replay.append({
                "ts": entry.get("ts") or 0,
                "time": entry.get("time") or "",
                "source": entry.get("source") or "",
                "reason": entry.get("reason") or "",
                "version": entry.get("version") or 0,
                "rejected": list(rejected),
                "added": added,
                "removed": removed,
                "summary": "；".join(parts) or (entry.get("reason") or "（无块变化）"),
            })
        return replay

    def _schedule_change_audit(self, day, limit=200):
        """读 kv schedule_audit:<day>：entries 最新在前，replay 按时间正序。"""
        try:
            rows = list(self.db.kv_get("schedule_audit:%s" % str(day), []) or [])
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.schedule_audit.read", _amo_exc)
            rows = []
        entries = self._schedule_audit_entries(rows[-200:], limit)
        return entries, self._schedule_audit_replay(list(reversed(entries)))

    async def api_schedule_audit(self):
        """GET/POST /schedule/audit：日程变更 + 体检拒绝审计（day 缺省 = 今天）。"""
        await self._ensure_ready()
        data = await self._body()
        data = dict(data) if isinstance(data, dict) else {}
        day = str(data.get("day") or data.get("date") or self._q("day", "") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            day = today_str()
        try:
            limit = int(data.get("limit") or self._q("limit", "") or 200)
        except (TypeError, ValueError):
            limit = 200
        entries, replay = self._schedule_change_audit(day, limit)
        return {"code": 0, "data": {"day": day, "entries": entries, "replay": replay}}

    async def api_schedule_block_update(self):
        """面板：改一块日程（时间/活动/地点）。POST-only + 时间/重叠校验 + 对账 + 审计。"""
        await self._ensure_ready()
        data = await self._body()
        data = dict(data) if isinstance(data, dict) else {}
        day = str(data.get("date") or data.get("day") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            return {"code": 1, "msg": "日期要写成 YYYY-MM-DD"}
        if day != today_str():
            return {"code": 1, "msg": "面板只改今天的日程（历史/未来只读）"}
        items = self._schedule_edit_items(day)
        if not items:
            return {"code": 1, "msg": "这天还没有日程，先刷新看看吧"}
        index, target = self._schedule_find_block(
            items, data.get("id"), data.get("start_old") or data.get("start"))
        if target is None:
            return {"code": 1, "msg": "没找到这一块（可能刚刷新过），刷新后再试"}
        begin = _schedule_clock(data.get("start"))
        finish = _schedule_clock(data.get("end"))
        if begin is None or finish is None:
            return {"code": 1, "msg": "开始/结束时间要写成 HH:MM"}
        if finish <= begin:
            return {"code": 1, "msg": "结束时间要晚于开始时间"}
        if finish - begin < SCHEDULE_EDIT_MIN_MINUTES:
            return {"code": 1, "msg": "一块至少 %d 分钟" % SCHEDULE_EDIT_MIN_MINUTES}
        clash = self._schedule_find_overlap(items, begin, finish, skip_index=index)
        if clash is not None:
            return {"code": 1, "msg": "和 %s 重叠了" % _schedule_row_label(clash)}
        updated = dict(target)
        updated["start"] = _schedule_hm(begin)
        updated["end"] = _schedule_hm(finish)
        for key in ("activity", "place", "category", "detail"):
            if key in data and str(data.get(key) or "").strip():
                updated[key] = str(data.get(key)).strip()
        if not str(updated.get("activity") or "").strip():
            return {"code": 1, "msg": "活动不能为空"}
        new_items = list(items)
        new_items[index] = updated
        return self._schedule_commit_edit(day, new_items, items, "update", target, updated,
                                          reason=str(data.get("reason") or ""),
                                          operator=self._schedule_operator(data))

    async def api_schedule_block_delete(self):
        """面板：删一块日程。POST-only + 存在性/历史校验 + 对账 + 审计。"""
        await self._ensure_ready()
        data = await self._body()
        data = dict(data) if isinstance(data, dict) else {}
        day = str(data.get("date") or data.get("day") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            return {"code": 1, "msg": "日期要写成 YYYY-MM-DD"}
        if day != today_str():
            return {"code": 1, "msg": "面板只改今天的日程（历史/未来只读）"}
        items = self._schedule_edit_items(day)
        if not items:
            return {"code": 1, "msg": "这天还没有日程，先刷新看看吧"}
        index, target = self._schedule_find_block(items, data.get("id"), data.get("start"))
        if target is None:
            return {"code": 1, "msg": "没找到这一块（可能刚刷新过），刷新后再试"}
        new_items = [row for pos, row in enumerate(items) if pos != index]
        return self._schedule_commit_edit(day, new_items, items, "delete", target, None,
                                          reason=str(data.get("reason") or ""),
                                          operator=self._schedule_operator(data))

    def _schedule_restore_entry(self, day, audit_id):
        """按 audit_id（老数据用 ts）找当天那条改/删审计；最新的优先。"""
        try:
            rows = list(self.db.kv_get(SCHEDULE_AUDIT_KEY, []) or [])
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.schedule_restore.read", _amo_exc)
            rows = []
        wanted = str(audit_id or "").strip()
        if not wanted:
            return None
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            if str(row.get("day") or "") != str(day or ""):
                continue
            if str(row.get("action") or "") not in ("delete", "update"):
                continue
            if wanted != str(row.get("id") or "") and wanted != str(row.get("ts") or ""):
                continue
            return row
        return None

    @staticmethod
    def _schedule_same_block(left, right):
        """同一版块：起止+活动+地点一样；审计带了扩展字段时也要一致。"""
        left = left if isinstance(left, dict) else {}
        right = right if isinstance(right, dict) else {}
        if not (str(left.get("start") or "")[:5] == str(right.get("start") or "")[:5]
                and str(left.get("end") or "")[:5] == str(right.get("end") or "")[:5]
                and str(left.get("activity") or "").strip()
                == str(right.get("activity") or "").strip()
                and str(left.get("place") or "").strip()
                == str(right.get("place") or "").strip()):
            return False
        for key in SCHEDULE_AUDIT_EXTRA_FIELDS:
            if key not in right:
                continue
            if str(left.get(key) or "").strip() != str(right.get(key) or "").strip():
                return False
        return True

    @staticmethod
    def _schedule_restored_block(before, target):
        """恢复一整块：审计字段覆盖当前块，当前块的非审计字段保留，老审计缺字段就沿用当前块/默认。"""
        restored = dict(target or {})
        restored.update(_schedule_audit_row(before))
        return restored

    async def api_schedule_block_restore(self):
        """面板：按审计 before 恢复一块（仅今天；块不存在重建，存在覆盖需确认）。"""
        await self._ensure_ready()
        data = await self._body()
        data = dict(data) if isinstance(data, dict) else {}
        day = str(data.get("date") or data.get("day") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            return {"code": 1, "msg": "日期要写成 YYYY-MM-DD"}
        if day != today_str():
            return {"code": 1, "msg": "面板只改今天的日程（历史/未来只读）"}
        entry = self._schedule_restore_entry(day, data.get("audit_id") or data.get("audit"))
        if entry is None:
            return {"code": 1, "msg": "没找到这条记录（可能太久了），刷新看看"}
        before = entry.get("before") if isinstance(entry.get("before"), dict) else {}
        after = entry.get("after") if isinstance(entry.get("after"), dict) else {}
        if not before:
            return {"code": 1, "msg": "这条记录里没有可恢复的块"}
        begin = _schedule_clock(before.get("start"))
        finish = _schedule_clock(before.get("end"))
        if begin is None or finish is None or finish <= begin:
            return {"code": 1, "msg": "这条记录里的时间坏了，没法恢复"}
        if finish - begin < SCHEDULE_EDIT_MIN_MINUTES:
            return {"code": 1, "msg": "一块至少 %d 分钟" % SCHEDULE_EDIT_MIN_MINUTES}
        items = self._schedule_edit_items(day)
        action = str(entry.get("action") or "")
        # 改：当前块是 after；删：当前块还是 before（不存在就该重建）。
        key = after if action == "update" else before
        index, target = self._schedule_find_block(items, key.get("id"), key.get("start"))
        if action == "update" and target is None:
            check_index, check_row = self._schedule_find_block(
                items, before.get("id"), before.get("start"))
            if check_row is not None and self._schedule_same_block(check_row, before):
                return {"code": 1, "msg": "这一块已经恢复了，不用再撤销"}
        if target is not None and self._schedule_same_block(target, before):
            return {"code": 1, "msg": "这一块已经恢复了，不用再撤销"}
        if target is not None and not _schedule_truthy(data.get("confirm")):
            return {"code": 1, "msg": "这一块现在还在，撤销会用旧版本覆盖它，确认后再点一次",
                    "data": {"confirm_required": True, "audit_id": str(entry.get("id") or ""),
                             "action": action}}
        clash = self._schedule_find_overlap(items, begin, finish, skip_index=index)
        if clash is not None:
            return {"code": 1, "msg": "和 %s 重叠了" % _schedule_row_label(clash)}
        restored = self._schedule_restored_block(before, target)
        restored["start"] = _schedule_hm(begin)
        restored["end"] = _schedule_hm(finish)
        if not str(restored.get("activity") or "").strip():
            return {"code": 1, "msg": "这条记录里的活动是空的，没法恢复"}
        new_items = list(items)
        if index >= 0:
            new_items[index] = restored
        else:
            new_items.append(restored)
        new_items.sort(key=lambda row: _schedule_clock(row.get("start")) or 0)
        return self._schedule_commit_edit(day, new_items, items, "restore", target or {}, restored,
                                          reason="undo",
                                          operator=self._schedule_operator(data))

    async def api_facts(self):
        """当天唯一事实视图（只读）：日程 + 账本 + 块日记 + 身体按 event_id 合并。

        F1：面板/调试只读这一份；GET 纯只读，不生成、不改库。
        """
        await self._ensure_ready()
        data = await self._body()
        day = str(data.get("day") or data.get("date") or self._q("day", "") or "").strip()
        if not day:
            day = today_str()
        try:
            try:
                from .amo_fact_timeline import build_day_view
            except ImportError:  # 直接按文件加载时
                from amo_fact_timeline import build_day_view
            view = build_day_view(self, day)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.ScheduleApiMixin.api_facts", _amo_exc)
            return {"code": 1, "message": "事实视图读取失败", "data": {
                "day": day, "events": [], "count": 0, "sources": {}}}
        return {"code": 0, "data": {
            "day": view.get("day") or day,
            "events": clean_ui_value(list(view.get("events") or [])),
            "count": int(view.get("count") or 0),
            "sources": clean_ui_value(view.get("sources") or {}),
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

    def _diary_task_failed(self, day, item, start=""):
        """后台小日记连本地兜底都写不进去时，详情别再无限 generating。"""
        key_start = str((item or {}).get("start") or start or "")
        try:
            mark = float(self.db.kv_get(
                "diary_task_failed:%s:%s" % (day, key_start), 0) or 0)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.diary_task_failed", _amo_exc)
            return False
        # 5 分钟后允许再试：失败标记只挡「立刻重复点开」，不能把这一小时永久钉死。
        return bool(mark) and _amo_clock.now_ts() - mark < 300

    async def api_schedule_detail(self):
        await self._ensure_ready()
        data = await self._body()
        day = str(data.get("date") or self._q("date", "") or "").strip() or today_str()
        item_id = str(data.get("id") or self._q("id", "") or "").strip()
        start = _detail_clock(data.get("start") or data.get("time") or self._q("start", "") or "")
        end = _detail_clock(data.get("end") or self._q("end", "") or "")
        if not start:
            return {"code": 0, "data": {
                "day": day, "start": "", "end": end, "diary": "",
                "empty_message": "她迷糊了，没留下这段小记。",
            }}
        items, item, raw_item, blocked = _detail_blocks(self, day, start, end, item_id)
        if blocked is not None:
            return blocked
        pending = _detail_pending(self, day, start, end, item)
        if pending is not None:
            return pending
        if item is None:
            return _detail_gap_reply(self, day, start, end)
        diary = _detail_read_cache(self, day, item, start, end, raw_item, items)
        if not diary:
            failed = getattr(self, "_diary_task_failed", None)
            if callable(failed) and failed(day, item, start):
                return _detail_fallback_reply(day, start, end, item)
            return _detail_enqueue(self, day, item, items, start, end, reason="panel_get")
        return _detail_payload(self, day, item_id, start, end, item, diary)

    async def api_schedule_regen(self):
        await self._ensure_ready()
        day = today_str()
        try:
            old_items = list(self.state.schedule.get(day) or self.db.get_schedule(day) or [])
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.regen.snapshot", _amo_exc)
            old_items = []
        items = await self.state.ensure_schedule(self._last_umo, force=True)
        # 重生成会整份替换当天日程：被删/改掉的活动块，其 activity_now / current_action /
        # transit 状态也必须对账（手动操作不写记忆，只保证此刻状态别穿帮）。
        try:
            reconcile = getattr(self.state, "_reconcile_manual_schedule_edit", None)
            if callable(reconcile):
                reconcile(day, old_items, list(items or []), source="regen")
        except Exception as _amo_exc:
            _note_swallowed("amo_api_schedule.regen.reconcile", _amo_exc)
        return {"code": 0, "data": items}
