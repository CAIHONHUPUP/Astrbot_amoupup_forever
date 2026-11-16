# -*- coding: utf-8 -*-
"""日程状态机 mixin：从 amo_human_state.py 按域拆出（只搬方法，行为零变化）。"""
import asyncio
import json
import re
from datetime import date
from datetime import timedelta
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

try:  # 公共层：helper/常量 + 会被测试改写的模块级函数代理
    from .amo_state_common import (
        _now,
        _validate_activity,
        minutes_now,
        normalize_place,
        today_str,
    )
except ImportError:  # 直接按文件加载时
    from amo_state_common import (
        _now,
        _validate_activity,
        minutes_now,
        normalize_place,
        today_str,
    )

# 稳定事件：日程写完后发布；当前订阅者为空，行为与直接调用完全兼容。
try:
    from .amo_events import ScheduleChanged
except ImportError:  # 直接按文件加载时
    from amo_events import ScheduleChanged

try:  # 日类型（纯函数）：工作日/周末/节假日
    from .day_types import normalize_day_types, pick_day_type
except ImportError:  # 直接按文件加载时
    try:
        from day_types import normalize_day_types, pick_day_type
    except ImportError:
        normalize_day_types = None  # type: ignore[assignment]
        pick_day_type = None  # type: ignore[assignment]

try:  # amo_shared：时间/日程/心情共享底层
    from .amo_shared import (
        MOOD_KEYS,
        SCHEDULE_VERSION,
        city_or_empty,
        day_meta,
        logger,
        now_ts,
        parse_hhmm,
        schedule_category_mood,
    )
except ImportError:  # 直接按文件加载时
    from amo_shared import (
        MOOD_KEYS,
        SCHEDULE_VERSION,
        city_or_empty,
        day_meta,
        logger,
        now_ts,
        parse_hhmm,
        schedule_category_mood,
    )

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object


class ScheduleStateMixin(_MixinBase):
    """日程域：主干生成、待办/已定行程、展示优先级、日程读写与持久化。"""

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
        """所有日程写入口的唯一落库点；外层锁保证同一天的读改写串行。

        未来日期不再铺日程（防剧透）：只把“已经定下来的事”写进待办表，
        到了那天由 JIT 按待办锚点落实成真实日程。
        """
        day_text = str(day or "")
        if day_text and day_text > today_str():
            return self._commit_todo_day(day_text, new_items, source=source)
        # 第四轮防穿帮：聊天/命令/工具把地点改成户外目的地时，先开过渡（30 分钟路程），
        # 不再瞬间传送（老 bug：被子里 → 医院走廊）。
        try:
            try:
                from .amo_transit import maybe_start as _maybe_transit
            except ImportError:
                from amo_transit import maybe_start as _maybe_transit
            _maybe_transit(self.p, day_text, new_items, old_items, source)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.commit.transit", _amo_exc)
        # 第七轮：落库前去重叠（旧块优先，新块截断到空档或丢弃）
        try:
            try:
                from .amo_transition import resolve_overlaps as _resolve_overlaps
            except ImportError:
                from amo_transition import resolve_overlaps as _resolve_overlaps
            # 只拿“不在新列表里的旧块”当 blocker：ensure/JIT 重写是整份替换，
            # 旧块和新块其实是同一批，如果不过滤会把所有块都当成冲突截掉（14:1x 的事故）。
            def _ident(row):
                return (str(row.get("start") or ""), str(row.get("end") or ""),
                        str(row.get("activity") or ""))

            _new_ids = {_ident(x) for x in (new_items or []) if isinstance(x, dict)}
            _base = [x for x in (old_items or [])
                     if isinstance(x, dict) and _ident(x) not in _new_ids]
            new_items, _trimmed = _resolve_overlaps(_base, new_items)
            if _trimmed:
                logger.info("[amo] 日程去重叠：截断/丢弃 %d 条新块（旧块优先）", len(_trimmed))
        except Exception as _amo_exc:
            _note_swallowed("amo_state_schedule.commit.overlap", _amo_exc)
        with self._schedule_commit_lock:
            return self._commit_schedule_change_locked(
                day, new_items, old_items=old_items, source=source,
                reason=reason, invalidate_from=invalidate_from,
            )

    def reconcile_health_schedule(self, day=None, now=None):
        """状态一致性巡检：健康已恢复但日程还挂着病假 → 物理删除那几条。"""
        try:
            from .amo_schedule_health import reconcile_stale_health_schedule
        except ImportError:
            from amo_schedule_health import reconcile_stale_health_schedule
        day = str(day or today_str())
        with self._schedule_commit_lock:
            current = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
            if not current:
                return 0
            kept, removed = reconcile_stale_health_schedule(
                current, self.health_info(), now)
            if not removed:
                return 0
            # 第八轮 C：删完病假行也先归一一次，别把重叠写回库（旧代码这里直接 save）
            try:
                try:
                    from .amo_transition import normalize_day as _normalize_day
                except ImportError:
                    from amo_transition import normalize_day as _normalize_day
                kept, _fixed = _normalize_day(kept)
                if _fixed:
                    logger.info("[amo] 健康巡检写入前归一了重叠块")
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule.reconcile_health.normalize", _amo_exc)
            result = self._commit_schedule_change_locked(
                day, kept, old_items=current, source="health_audit",
                reason="健康已恢复，清理过期病假日程",
            )
            if isinstance(result, dict) and result.get("changed"):
                logger.info("[amo] 状态巡检：健康已恢复，清理过期病假日程 %d 条", removed)
                return removed
            self.schedule[day] = kept
            self.db.save_schedule(day, kept)
            self.db.kv_set("schedule", self.schedule)
            logger.info("[amo] 状态巡检：健康已恢复，清理过期病假日程 %d 条", removed)
            return removed

    def add_schedule_item(self, day, item, source="finance", reason=""):
        """在当天日程里追加一条（锁内读改写）；整天覆盖仍走 commit_schedule_change。"""
        day = str(day or "")
        empty = {"changed": False, "version": 0, "changed_hours": [], "cancelled": []}
        if not day or not isinstance(item, dict):
            return empty
        start = str(item.get("start") or "").strip()
        if not re.match(r"^\d{1,2}:\d{2}$", start):
            return empty
        try:
            from .amo_diary_guard import clean_schedule_text
            item = dict(item)
            for key in ("activity", "detail"):
                if item.get(key):
                    item[key] = clean_schedule_text(item[key]) or item[key]
        except Exception as exc:
            _note_swallowed("amo_human_state.py.1096", exc)

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

    #: 只有这些来源生成的活动做主语校验；用户明确改的日程（chat/command/manual）按他说的来。
    ACTIVITY_GUARD_SOURCES = (
        "jit", "ensure", "ensure_regen", "social", "auto", "life", "wander", "generated",
    )

    def _guard_activity_subjects(self, items, source):
        """生成来源的日程：没约过会就把「和老公」改写成「和闺蜜」。"""
        if str(source or "") not in self.ACTIVITY_GUARD_SOURCES:
            return items
        approved = self._date_approved()
        out = []
        for item in items:
            if not isinstance(item, dict):
                out.append(item)
                continue
            row = dict(item)
            result = _validate_activity(row.get("activity"), approved=approved)
            if not result.get("ok"):
                logger.info(
                    "[amo] 日程主语校验：%r -> %r（%s，source=%s）",
                    row.get("activity"), result.get("activity"), result.get("reason"), source,
                )
                row["activity"] = result.get("activity") or row.get("activity")
            out.append(row)
        return out

    def _commit_schedule_change_locked(self, day, new_items, old_items=None, source="",
                                       reason="", invalidate_from=None):
        """commit_schedule_change 的锁内实现。"""
        day = str(day)
        if old_items is None:
            old_items = list(self.schedule.get(day) or [])
        old_items = list(old_items or [])
        new_items = list(new_items or [])
        new_items = self._normalize_schedule_items(new_items, source, day)
        if old_items == new_items and invalidate_from is None:
            return {
                "changed": False,
                "version": self.db.schedule_version(day),
                "changed_hours": [],
                "cancelled": [],
                "saved": True,
            }
        changed = self.changed_hours(old_items, new_items)
        try:
            version = self.db.bump_schedule_version(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.commit_schedule_change", _amo_exc)
            version = 0
        # 第八轮 C 审计结论：这里是今天日程的唯一落库点，写入语义是**整份替换**（不是增量），
        # 因此不需要在这里二次去重叠——去重叠只对「新块 vs 真正的外部旧块」做，
        # 由 commit_schedule_change 公共入口（_resolve_overlaps，先过滤同批旧块）和
        # 健康巡检 / 23:59 快照（各自 normalize 后再写）负责；否则整份替换会自撞（14:1x 事故）。
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
        # 落库成败要能传出去：JIT 到点决策是先扣钱后落库，落库失败要原路退款，
        # 以前这里把异常吞了，调用方永远以为「写成功了」（2026-11-15）。
        saved = True
        try:
            self.db.save_schedule(day, new_items)
            self.db.kv_set("schedule", self.schedule)
        except Exception as _amo_exc:
            saved = False
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
        cancelled = self._publish_schedule_change(
            day, version, source, reason, changed, start_hour)
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
            "saved": saved,
        }

    def _normalize_schedule_items(self, new_items, source, day=""):
        # 清文案 → 归一地点 → 纠偏活动主语（三步各自兜底，坏一步不连累别的）
        try:
            from .amo_diary_guard import clean_schedule_text
            cleaned = []
            for row in new_items:
                if isinstance(row, dict):
                    row = dict(row)
                    for key in ("activity", "detail"):
                        if row.get(key):
                            row[key] = clean_schedule_text(row[key]) or row[key]
                cleaned.append(row)
            new_items = cleaned
        except Exception as exc:
            _note_swallowed("amo_human_state.py.1170", exc)
        try:
            new_items = [
                dict(row, place=normalize_place(row.get("place"), str(row.get("zone") or "")))
                if isinstance(row, dict) else row
                for row in new_items
            ]
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.commit_schedule_change.place", _amo_exc)
        try:
            new_items = self._guard_activity_subjects(new_items, source)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState._commit_schedule_change_locked", _amo_exc)
        try:
            new_items = self._enforce_zone_activity(new_items, day, source)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState._enforce_zone_activity", _amo_exc)
        return new_items

    #: 区域 vs 文案的强冲突词；只拦“明显不可能”的组合，不碰擦边文案。
    _ZONE_CONFLICT = {
        "home": ("挤地铁", "坐地铁", "在公司", "到公司", "在工位", "办公室", "在商场", "逛街", "在路上", "开车去", "打车去"),
        "office": ("在家里", "在家", "窝在", "躺在床上", "被窝", "厨房", "客厅", "卧室"),
        "road": ("工位", "公司", "办公室", "沙发", "床", "电视", "厨房", "被窝", "家"),
    }
    _ZONE_FALLBACK = {
        "home": "在家待着，做点自己的事",
        "office": "在工位上忙工作",
        "road": "在路上赶路",
    }
    _ROAD_EXEMPT = ("回家", "到家", "路上", "地铁", "公交", "开车", "打车", "走", "车厢", "等车")
    #: 非约会日程里绝不能出现的实体陪伴词（用户物理上不在她身边）。
    _CATEGORY_WORDS = {
        "sleep": ("睡", "被窝", "床", "枕头", "眯", "梦", "醒", "赖", "翻", "烧", "冷", "热", "闹钟", "困", "闭眼"),
        "commute": ("地铁", "公交", "车", "路上", "站", "走", "通勤", "堵", "车厢", "赶", "出门", "打车", "骑车", "扶", "下班", "回家", "到家"),
    }
    _CATEGORY_FALLBACK = {"sleep": "一个人裹着被子睡着", "commute": "在路上赶路"}
    _PARTNER_PHYSICAL = ("黏着老公", "抱着老公", "搂着老公", "靠在老公", "躺在老公",
                         "老公怀里", "老公身边", "老公旁边", "和老公一起睡",
                         "他翻了个身", "他还在屋里", "他在屋里", "躺在他怀里")

    @classmethod
    def _enforce_zone_activity(cls, items, day="", source=""):
        """所有写日程路径的总闸：zone 和 activity 明显打架时纠正文案，保留物理骨架。"""
        for row in items or []:
            if not isinstance(row, dict):
                continue
            zone = str(row.get("zone") or "")
            if zone not in cls._ZONE_CONFLICT:
                continue
            act = str(row.get("activity") or "")
            if not act:
                continue
            category = str(row.get("category") or "")
            allowed = cls._CATEGORY_WORDS.get(category)
            if allowed and not any(word in act for word in allowed):
                safe = cls._CATEGORY_FALLBACK[category]
                logger.warning("[amo] 日程类别/文案冲突已纠正: day=%s %s category=%s %r -> %r source=%s",
                               day, row.get("start"), category, act[:40], safe, source)
                row["activity"] = safe
                row["detail"] = ""
                continue
            if category not in ("social", "date") and any(word in act for word in cls._PARTNER_PHYSICAL):
                safe = "一个人裹着被子睡着" if category == "sleep" else cls._ZONE_FALLBACK.get(zone, "一个人待着")
                logger.warning("[amo] 非约会日程出现实体陪伴，已改写: day=%s %s zone=%s %r -> %r source=%s",
                               day, row.get("start"), zone, act[:40], safe, source)
                row["activity"] = safe
                row["detail"] = ""
                continue
            if zone == "road" and any(word in act for word in cls._ROAD_EXEMPT):
                continue
            if not any(word in act for word in cls._ZONE_CONFLICT[zone]):
                continue
            fallback = cls._ZONE_FALLBACK[zone]
            logger.warning("[amo] 日程区域/文案冲突已纠正: day=%s %s-%s zone=%s %r -> %r source=%s",
                           day, row.get("start"), row.get("end"), zone, act[:40], fallback, source)
            row["activity"] = fallback
            row["detail"] = ""
        return items

    def settle_schedule_costs(self, now=None):
        """日程块结束后按 LLM 定价结算一次；同一 event_id 绝不重复扣款。"""
        now = now or _now()
        day = now.date().isoformat()
        now_min = int(now.hour) * 60 + int(now.minute)
        try:
            items = list(self.schedule.get(day) or [])
        except Exception:
            items = []
        for item in items:
            cost = int(item.get("cost_cents") or 0)
            if cost <= 0 or item.get("cost_settled"):
                continue
            end = parse_hhmm(item.get("end"), 24 * 60)
            if now_min < end:
                continue
            event_id = "sch_%s_%s_%s" % (day, item.get("start"), item.get("category") or "life")
            flag = "cost_settled:" + event_id
            try:
                if self.db.kv_get(flag, False):
                    item["cost_settled"] = True
                    continue
            except Exception:  # cleanup: KV read is best-effort
                pass
            payload = {"ok": False, "reason": "余额不足"}
            try:
                reason = "日程消费·%s" % (item.get("activity") or "花销")
                ok = bool(self.p.wallet.debit(cost, reason, event_id,
                                               category="schedule",
                                               item_name=str(item.get("activity") or "")))
                payload = {"ok": ok, "reason": "" if ok else "余额不足",
                           "cost_cents": cost, "event_id": event_id}
                if not ok:
                    logger.warning("[amo] 日程消费失败（余额不足）: %s %s 分", event_id, cost)
            except Exception as exc:
                payload = {"ok": False, "reason": "扣款异常: %s" % exc}
                logger.warning("[amo] 日程消费异常: %s", exc)
            try:
                self.db.kv_set(flag, payload)
            except Exception as _amo_exc:
                _note_swallowed("amo_state_schedule.settle_cost.flag", _amo_exc)
            item["cost_settled"] = True
            item["cost_result"] = payload
        return items

    def _publish_schedule_change(self, day, version, source, reason, changed, start_hour):
        # 有事件总线就发 ScheduleChanged（订阅者取消日记任务）；旧实例/单测走兼容分支
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
        return cancelled

    @staticmethod
    def _fmt_minutes(value):
        value = max(0, min(24 * 60, int(value)))
        if value >= 24 * 60:
            return "24:00"
        return "%02d:%02d" % (value // 60, value % 60)

    _DISPLAY_BOUNDARIES = (6 * 60, 9 * 60, 12 * 60, 14 * 60, 18 * 60, 22 * 60)

    @staticmethod
    def _display_priority(item):
        """展示时谁占这一分钟：具体流水（财务/约会/看病）压过背景日程。"""
        source = str(item.get("source") or "")
        if source in ("chat", "command", "manual"):
            return 90
        category = str(item.get("category") or "")
        if category in ("finance", "appointment", "date", "health"):
            return 70
        if category == "meal":
            return 55
        if category == "commute":
            return 50
        if category == "work":
            return 45
        if category in ("play", "social"):
            return 40
        if category == "life":
            return 30
        if category == "rest":
            return 20
        if category == "sleep":
            return 10
        return 25

    @staticmethod
    def _display_event(value):
        """只保留真正的事件 kind；财务情绪串不能冒充「突发事件」标签。"""
        kind = str(value or "")
        if kind in ("overtime", "friend_dinner", "business_trip", "date", "sick", "hospital"):
            return kind
        return ""

    @staticmethod
    def _finance_activity(activity, detail):
        """把「财务·支出」翻译成人话：买了个小蛋糕（花销 ¥41.95）。"""
        text = str(activity or "")
        if not any(word in text for word in ("财务", "工资", "支出", "收入", "入账")):
            return text
        detail_text = str(detail or "")
        amount = ""
        match = re.search(r"¥\s*([0-9][0-9,]*\.?[0-9]*)", detail_text)
        if not match:
            match = re.search(r"([0-9][0-9,]*\.?[0-9]*)\s*元", detail_text)
        if match:
            amount = match.group(1)
        if "工资" in text:
            return ("工资到账（¥%s）" % amount) if amount else "工资到账"
        reason = ""
        for part in re.split(r"[，,]", detail_text):
            if "：" in part or ":" in part:
                reason = re.split(r"[：:]", part)[-1].strip()
        if "支出" in text or "花" in text:
            if reason and amount:
                return "买了%s（花销 ¥%s）" % (reason, amount)
            if reason:
                return "买了%s" % reason
            if amount:
                return "花销 ¥%s" % amount
        return text

    def _default_slot(self, begin, finish):
        """过去没有事件的空档用默认生活状态填满，不写库，也不算剧透。"""
        home = self._jit_home()
        if finish <= 6 * 60:
            return {"activity": "深度睡眠", "place": home, "category": "sleep", "zone": "home"}
        if begin < 9 * 60:
            return {"activity": "起床后的琐事", "place": home, "category": "life", "zone": "home"}
        if begin < 12 * 60:
            return {"activity": "日常发呆", "place": home, "category": "life", "zone": "home"}
        if begin < 14 * 60:
            return {"activity": "午饭后的放空", "place": home, "category": "rest", "zone": "home"}
        if begin < 18 * 60:
            return {"activity": "日常发呆", "place": home, "category": "life", "zone": "home"}
        if begin < 22 * 60:
            return {"activity": "窝在家里的晚上", "place": home, "category": "rest", "zone": "home"}
        return {"activity": "准备睡觉", "place": home, "category": "life", "zone": "home"}

    def _jit_visible_slots(self, raw, now_min):
        """把真实事件按分钟铺成连续时间轴；只补已经过去的空档。"""
        now_min = max(0, min(24 * 60, int(now_min)))
        now_hour_end = min(24 * 60, (now_min // 60 + 1) * 60)
        events = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            try:
                begin = parse_hhmm(item.get("start"))
            except Exception:
                continue
            if begin >= now_min:
                continue
            end_text = str(item.get("end") or "").strip()
            try:
                finish = 24 * 60 if end_text in ("24:00", "0:00", "00:00") else parse_hhmm(end_text)
            except Exception:
                finish = begin + 60
            if finish <= begin:
                finish = min(24 * 60, begin + 60)
            raw_finish = finish
            finish = min(finish, now_min)
            if finish <= begin:
                continue
            events.append({
                "begin": begin,
                "finish": finish,
                "raw_finish": raw_finish,
                "priority": self._display_priority(item),
                "activity": self._finance_activity(item.get("activity"), item.get("detail")),
                "place": str(item.get("place") or ""),
                "category": str(item.get("category") or "rest"),
                "zone": item.get("zone", ""),
                "transit": item.get("transit", ""),
                "vehicle": item.get("vehicle", ""),
                "detail": item.get("detail", ""),
                "event": self._display_event(item.get("event")),
                "mood": item.get("mood", ""),
                "open_sleep": bool(item.get("open_sleep")),
                "sleep_from": str(item.get("sleep_from") or ""),
            })
        points = {0, now_min}
        for event in events:
            points.add(max(0, min(now_min, event["begin"])))
            points.add(max(0, min(now_min, event["finish"])))
        ordered = sorted(points)
        slots = []
        for start, end in zip(ordered, ordered[1:]):
            if end <= start:
                continue
            covering = [item for item in events if item["begin"] <= start and item["finish"] >= end]
            # 约会/社交进行中：财务流水只留在账本，不打断正在进行的日程块
            if any(item["category"] in ("social", "date") for item in covering):
                social_only = [item for item in covering if item["category"] != "finance"]
                if social_only:
                    covering = social_only
            if covering:
                winner = max(covering, key=lambda item: (item["priority"], item["finish"] - item["begin"]))
                slot = {
                    "start_min": start,
                    "end_min": end,
                    "activity": winner["activity"],
                    "place": winner["place"],
                    "category": winner["category"],
                    "zone": winner.get("zone", ""),
                    "transit": winner.get("transit", ""),
                    "vehicle": winner.get("vehicle", ""),
                    "detail": winner.get("detail", ""),
                    "event": winner.get("event", ""),
                    "mood": winner.get("mood", ""),
                    "open_sleep": winner["open_sleep"],
                    "sleep_from": winner.get("sleep_from", "") if start == winner["begin"] else "",
                    "current": False,
                    "doing": False,
                }
                if end == now_min and winner.get("raw_finish", 0) > now_min:
                    slot["end_min"] = min(winner["raw_finish"], now_hour_end)
                    slot["current"] = True
                    slot["doing"] = True
                slots.append(slot)
                continue
            cuts = [start] + [point for point in self._DISPLAY_BOUNDARIES if start < point < end] + [end]
            for part_start, part_end in zip(cuts, cuts[1:]):
                if part_end <= part_start:
                    continue
                default = self._default_slot(part_start, part_end)
                slots.append({
                    "start_min": part_start,
                    "end_min": part_end,
                    "activity": default["activity"],
                    "place": default["place"],
                    "category": default["category"],
                    "zone": default["zone"],
                    "transit": "",
                    "vehicle": "",
                    "detail": "",
                    "event": "",
                    "mood": "",
                    "open_sleep": False,
                    "sleep_from": "",
                    "current": False,
                    "doing": False,
                })
        return slots

    @staticmethod
    def _merge_quiet_slots(slots):
        """相邻且同一件事的时段合并成自然跨度；不跨 6/12/18 点分组。"""
        def part(value):
            if value < 6 * 60:
                return 0
            if value < 12 * 60:
                return 1
            if value < 18 * 60:
                return 2
            return 3

        merged = []
        for slot in slots:
            if merged:
                last = merged[-1]
                if (last["end_min"] == slot["start_min"]
                        and last["activity"] == slot["activity"]
                        and last["place"] == slot["place"]
                        and last["category"] == slot["category"]
                        and part(last["start_min"]) == part(slot["start_min"])):
                    last["end_min"] = slot["end_min"]
                    last["current"] = last["current"] or slot["current"]
                    last["doing"] = last["doing"] or slot["doing"]
                    last["open_sleep"] = last["open_sleep"] or slot["open_sleep"]
                    continue
            merged.append(dict(slot))
        # 尾巴太碎：晚上最后一段休息/生活不足 1 小时，直接并到睡前（23:00），
        # 不允许出现「21:19-21:29 窝在家里的晚上」这种 10 分钟碎片
        if merged:
            last = merged[-1]
            if (str(last.get("category") or "") in ("rest", "life", "sleep")
                    and int(last.get("start_min") or 0) >= 18 * 60
                    and int(last.get("end_min") or 0) - int(last.get("start_min") or 0) < 60):
                last["end_min"] = max(int(last["end_min"]), 23 * 60)
        return merged

    #: 已经答应/约好的事（未来日期也要展示，不能当盲盒藏起来）
    _BOOKED_CATEGORIES = ("social", "date", "appointment", "health", "leave", "travel")

    async def enrich_schedule_tree(self, day, tree, umo=""):
        """把每段日程的冷事实交给大模型起 4 字小标题；Python 不写现成人话。"""
        try:
            llm = getattr(self.p, "llm", None)
            if llm is None or not hasattr(llm, "chat_raw"):
                return tree
            cache = self.db.kv_get("schedule_block_summaries", {}) or {}
            if not isinstance(cache, dict):
                cache = {}
            changed = False
            for block in tree or []:
                facts = block.get("facts")
                if not isinstance(facts, dict) or not facts.get("events"):
                    continue
                slot_key = "%s|%s" % (day, block.get("start"))
                facts_key = json.dumps(facts, ensure_ascii=False, sort_keys=True)
                cached = cache.get(slot_key)
                if (isinstance(cached, dict) and cached.get("facts") == facts_key
                        and cached.get("summary")):
                    block["summary"] = str(cached["summary"])
                    continue
                system = ("你只负责给日程分组起一个四个字的拟人化小标题。"
                          "根据用户给的事实（时间、活动、地点、类别）概括，只输出四个汉字，"
                          "不要引号、不要解释、不要标点。")
                try:
                    text = await llm.chat_raw(str(umo or ""), system, facts_key)
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.enrich_schedule_tree.chat", _amo_exc)
                    text = ""
                summary = re.sub(r"[^\u4e00-\u9fff]", "", str(text or ""))[:4]
                if len(summary) == 4:
                    block["summary"] = summary
                    cache[slot_key] = {"facts": facts_key, "summary": summary}
                    changed = True
            if changed:
                if len(cache) > 200:
                    for key in list(cache)[:max(0, len(cache) - 200)]:
                        cache.pop(key, None)
                self.db.kv_set("schedule_block_summaries", cache)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.enrich_schedule_tree", _amo_exc)
        return tree

    def resolve_schedule_item(self, day, start="", end="", item_id=""):
        """按精确时间范围/ID 找真实日程；补出来的默认段返回 None，由 API 友好降级。"""
        day = str(day or today_str())
        start = str(start or "").strip()[:5]
        end = str(end or "").strip()[:5]
        try:
            items = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
        except Exception:
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if start and str(item.get("start") or "")[:5] == start:
                if not end or str(item.get("end") or "")[:5] == end:
                    return dict(item)
        try:
            point = parse_hhmm(start)
        except Exception:
            return None
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                begin = parse_hhmm(item.get("start"))
                finish = parse_hhmm(item.get("end"))
            except Exception:
                continue
            if finish <= begin:
                finish = begin + 60
            if begin <= point < finish:
                return dict(item)
        return None

    def schedule_blind_hint(self, day=None):
        """时间轴底部的盲盒提示：未来还没发生，不剧透。"""
        if not self._jit_enabled():
            return ""
        day = str(day or today_str())
        today = today_str()
        if day < today:
            return ""
        if day > today:
            return "—— 那天还没到，她会怎么过？到时候才知道 ——"
        return "—— 今天还没过完呢，她接下来会做什么？——"

    def schedule_tree(self, day=None):
        """一级 4 段、二级每小时；不再生成三级 10 分钟。

        JIT 盲盒模式：今天只展示已经发生的时段和正在进行的这一小时，
        未来不预生成、不占位；未来日期整张表留空，等到了那天再由 Tick 写入。
        """
        day = str(day or today_str())
        try:
            self.settle_date_end(day)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.schedule_tree.settle", _amo_exc)
        if day > today_str():
            return self._booked_tree(day)
        if self._jit_enabled() and day == today_str():
            return self._jit_schedule_tree(day)
        raw = self.schedule.get(day) or self.db.get_schedule(day) or []
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
                    "id": "%s|%s-%s|%s" % (day, start, end, item.get("category", "rest")),
                    "start": self._fmt_minutes(start),
                    "end": self._fmt_minutes(end),
                    "activity": self._finance_activity(item.get("activity", "休息"), item.get("detail")),
                    "place": item.get("place", "家"),
                    "category": item.get("category", "rest"),
                    "zone": item.get("zone", ""),
                    "transit": item.get("transit", ""),
                    "vehicle": item.get("vehicle", ""),
                    "detail": item.get("detail", ""),
                    "event": self._display_event(item.get("event")),
                    "mood": item.get("mood", ""),
                    "current": bool(is_today and start <= now_min < end),
                    "doing": bool(is_today and start <= now_min < end),
                }
                children.append(hour)
            block["date"] = day
            block["children"] = children  # type: ignore[assignment]
            block["current"] = any(child["current"] for child in children)  # type: ignore[assignment]
            if children:
                block["activity"] = ""
                block["place"] = ""
                block["facts"] = {
                    "part": str(block.get("title") or ""),
                    "start": str(block.get("start") or ""),
                    "end": str(block.get("end") or ""),
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
                # 盲盒：未来的时段连预约也不提前展示，到了那小时再由 Tick 写入
                items = [item for item in items
                         if parse_hhmm(item.get("start")) <= now_min]
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

    async def ensure_schedule(self, umo, force=False):
        """每天只生成一次主干；同一日期加锁，避免并发生成两份。"""
        async with self._schedule_gen_lock(today_str()):
            return await self._ensure_schedule_locked(umo, force=force)

    async def _ensure_schedule_locked(self, umo, force=False):
        """ensure_schedule 的锁内实现；点击二级卡片时再动态生成这一小时的日记。"""
        today = today_str()
        meta = day_meta(today)
        try:
            await self.decide_sick_action(umo, _now())
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.ensure_schedule.sick_decision", _amo_exc)
        if not force and self._jit_enabled():
            return self._schedule_jit_purge(today)
        if force:
            self._schedule_reset_forced(today)
        previous = list(self.schedule.get(today) or [])
        existing = None if force else self.schedule.get(today)
        context = self.schedule_ctx.get(today) or {}
        if not force and context.get("manual_override") and self.schedule.get(today):
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
        items, context = await self._schedule_rebuild(umo, today, meta, day_type, force)
        self._schedule_persist(today, items, previous, context, force)
        await self._schedule_apply_travel(today)
        try:
            if (self.sick_decision(today) or {}).get("action") == "take_sick_leave":
                self.db.kv_set("sick_rebuilt:%s" % today, True)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.ensure_schedule.sick_rebuilt", _amo_exc)
        return items

    def _schedule_jit_purge(self, today):
        """JIT 盲盒：只读过滤（预约 + 已发生流水），不写回库。

        以前这里每次 tick 都把过滤结果 commit 回去，既制造写入风暴，又会让
        「整份替换」被去重叠逻辑当成新块截断（2026-11-16 幽灵日程事故）。
        """
        return self._jit_items(today)

    def _schedule_reset_forced(self, today):
        """force 重生成前清掉当天的 day_type / manual_override，否则会读回旧类型。"""
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

    async def _schedule_rebuild(self, umo, today, meta, day_type, force):
        """重建主干：世界观/人设/天气/健康/周期 → engine.build_context/skeleton → 兜底。"""
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
                try:
                    context["sick_action"] = str((self.sick_decision(today) or {}).get("action") or "")
                except Exception:
                    context["sick_action"] = ""
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
        return items, context

    def _schedule_persist(self, today, items, previous, context, force):
        """落库：context/version、commit、清理 14 天前旧日程与 day_type。"""
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
