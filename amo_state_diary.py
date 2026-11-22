# -*- coding: utf-8 -*-
"""小日记 mixin：整点小日记生成/缓存/失效重算、当前活动汇总。从 amo_human_state.py 按域拆出。"""
import hashlib
import time
from typing import Any, TYPE_CHECKING

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
        DIARY_REWRITE_LIMIT,
        _jit_activity,
        _now,
        _pick_covering,
        _pick_stale,
        _pick_yesterday_hour,
        _today_items,
        block_diary_enabled,
        block_id,
        block_places,
        minutes_now,
        slot_has_ended,
        today_str,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_common import (
            DIARY_REWRITE_LIMIT,
            _jit_activity,
            _now,
            _pick_covering,
            _pick_stale,
            _pick_yesterday_hour,
            _today_items,
            block_diary_enabled,
            block_id,
            block_places,
            minutes_now,
            slot_has_ended,
            today_str,
        )

try:  # 纯规则模块：日程 → 地点/场景优先级（照片严格跟随日程用）
    from .photo_rules import zone_for_place
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_rules import zone_for_place

try:  # 时间轴：块读取/查询的唯一入口（第 1 阶段重构）
    from .amo_timeline import Timeline
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_timeline import Timeline

try:  # amo_shared：时间/日程/心情共享底层
    from .amo_shared import (
        city_or_empty,
        day_meta,
        logger,
        md5_int,
        now_ts,
        parse_hhmm,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import (
            city_or_empty,
            day_meta,
            logger,
            md5_int,
            now_ts,
            parse_hhmm,
        )

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object


def _reconcile_minute(text):
    """HH:MM → 分钟；解析不了返回 -1。只给手动改块对账用。"""
    try:
        hour, _, minute = str(text or "").strip().partition(":")
        return max(0, min(24, int(hour))) * 60 + max(0, min(59, int(minute or 0)))
    except (TypeError, ValueError):
        return -1


def _block_signature(row):
    """活动块的身份：活动/地点/区域/类别；状态对账只认这四项。"""
    row = row if isinstance(row, dict) else {}
    return tuple(str(row.get(key) or "") for key in ("activity", "place", "zone", "category"))


def _match_block(items, item):
    """在当前原始日程里找同一个块（start 必须一致，给了 end 就一起比）。"""
    row = item if isinstance(item, dict) else {}
    start = str(row.get("start") or "")
    end = str(row.get("end") or "")
    for entry in items or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("start") or "") != start:
            continue
        if end and str(entry.get("end") or "") != end:
            continue
        return entry
    return None


def _covering_block(items, minute):
    """当前分钟盖住的那条日程块；没有返回 None。"""
    for row in items or []:
        if not isinstance(row, dict):
            continue
        start = _reconcile_minute(row.get("start"))
        end = _reconcile_minute(row.get("end"))
        if start < 0:
            continue
        if end <= start:
            end = start + 60
        if start <= minute < end:
            return row
    return None


def _is_sleep_block(item, start):
    """睡眠块：category=sleep 且起点落在 23 点或 00-07 点。"""
    return (str(item.get("category") or "") == "sleep"
            and start[:2] in ("23", "00", "01", "02", "03", "04", "05", "06", "07"))


class DiaryStateMixin(_MixinBase):
    """小日记域：整点小日记的上下文/生成/失效重算，以及当前活动与兜底活动。"""
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _hourly_schedule: Any
        engine: Any
        health_info: Any
        mood: Any
        nudge: Any
        p: Any
        period_info: Any
        save: Any
        schedule: Any
        schedule_ctx: Any
        weather: Any
        worldview: Any


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
        """[deprecated 回滚路径] 按整点生成小日记；正常路径已改 expand_block。

        只有 block_diary_enabled=false 时 expand_block 才会回退到这里，
        新代码不得直接调用；它的读写都走旧 schedule_diary 表。
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
        activity = str(item.get("activity") or "")
        if str(item.get("category") or "") == "sleep" and start[:2] in (
                "23", "00", "01", "02", "03", "04", "05", "06", "07"):
            logger.info("[DIARY_DEBUG] hour=%s timer_cancel=1 reason=sleep", start)
            return {"diary": "（呼呼大睡中，翻了个身又睡过去了）", "mood": item.get("mood") or "平静",
                    "place": item.get("place") or "", "activity": activity,
                    "sleep": True, "placeholder": True, "version": version}
        try:
            from .amo_fact_pool import build_fact_pool
            _pool = await build_fact_pool(
                self.p, day, hour_slot=int(start[:2] or 0),
                slot_start=start, slot_end=item.get("end"))
            facts = list(_pool.get("facts") or [])
        except Exception:
            facts = []
        get_record = getattr(self.db, "get_diary_record", None)
        record = None
        if callable(get_record):
            try:
                record = get_record(day, start)
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.expand_hour", _amo_exc)
        # 归档态：过去日期的小日记只读，不再让模型改写
        if day < today_str():
            if record and str(record.get("text") or "").strip():
                return {"diary": str(record.get("text") or ""),
                        "mood": item.get("mood") or "平静",
                        "place": item.get("place") or "", "activity": activity,
                        "cached": True, "archived": True, "version": version}
            return {"diary": "", "mood": item.get("mood") or "平静",
                    "place": item.get("place") or "", "activity": activity,
                    "archived": True, "version": version,
                    "empty_message": "这一天的日记已经归档了，不再重写。"}
        # 重写预算：同一时段最多 2 次，防止日程/日记互相触发死循环
        if (force or (record and str(record.get("status") or "valid") != "valid")) \
                and self._diary_rewrites(day, start) >= DIARY_REWRITE_LIMIT:
            logger.warning("[DIARY_DEBUG] hour=%s rewrite_budget=freeze", start)
            # 冻结不是清空：最后一次成功生成的正文必须保住，并标成 frozen 不再被日程变更踩掉
            text = str((record or {}).get("text") or "")
            if not text:
                try:
                    text = self.db.get_diary(day, start) or ""
                except Exception:
                    text = ""
            if record and text:
                try:
                    self.db.save_diary(day, str(start), text, version=version, status="frozen")
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.expand_hour.freeze_save", _amo_exc)
            payload = {"diary": text,
                       "mood": item.get("mood") or "平静",
                       "place": item.get("place") or "", "activity": activity,
                       "frozen": True, "locked": True, "version": version}
            if not text:
                payload["empty_message"] = "这一小时变动太多，日记已经锁定啦，先保留这一版。"
            return payload
        # 2026-11-20：日程版本变了会把日记标 stale，但这一小时内容没变时不该再调一次模型
        if not force and record and str(record.get("status") or "") == "stale" \
                and str(record.get("text") or "").strip():
            sig = self._diary_sig(item)
            if sig and self._diary_sig_get(day, start) == sig:
                try:
                    self.db.save_diary(day, start, str(record.get("text") or ""),
                                       version=version, status="valid", source="block")
                except TypeError:
                    self.db.save_diary(day, start, str(record.get("text") or ""),
                                       version=version, status="valid")
                logger.info("[DIARY_DEBUG] hour=%s reused=same_content version=%s",
                            start, version)
                return {"diary": str(record.get("text") or ""),
                        "mood": item.get("mood") or "平静",
                        "place": item.get("place") or "", "activity": activity,
                        "cached": True, "reused": True, "version": version}
        cached = self._diary_cached_reply(day, start, item, version, force, facts, record)
        if cached:
            return cached
        stale_text = ""
        if record and str(record.get("status") or "") == "stale":
            stale_text = str(record.get("text") or "")
        bundle = self._diary_context(day, items, start, facts)
        context = bundle["context"]
        try:
            context["chat_lines_hour"] = self._diary_hour_chat(
                day, start, str(item.get("end") or ""))
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.expand_hour.chat", _amo_exc)
        prev_item = bundle["prev_item"]
        next_item = bundle["next_item"]
        prev_diary = bundle["prev_diary"]
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
        return self._diary_finalize(day, start, item, result, context, prev_item,
                                    record, stale_text, force, version)

    # ---------- 块级小日记（block_diary）：块身份 = block_id ----------
    async def expand_block(self, day, item, items=None, force=False):
        """按日程块生成这一块的第一人称小日记（块读取统一走 Timeline）。

        块身份由 block_id(day, item) 决定：两个相接的块（16:07-16:56 / 16:56-18:26）
        天然是两条记录，不会共用一段日记；块没结束一律 pending，不提前出文本。
        开关 block_diary_enabled=false 时整条回退旧的整点逻辑（expand_hour/旧表，
        [deprecated] 回滚路径，只为兼容保留）。
        """
        if not block_diary_enabled(getattr(self, "p", None)):
            return await self.expand_hour(day, item, items, force)
        day = str(day or today_str())
        item = dict(item or {})
        bid = block_id(day, item)
        get_lock = getattr(self.p, "_diary_lock", None)
        if callable(get_lock) and bid:
            lock = get_lock(day, bid)
            if lock is not None:
                async with lock:
                    return await self._expand_block_impl(day, item, items, force)
        return await self._expand_block_impl(day, item, items, force)

    async def _expand_block_impl(self, day, item, items=None, force=False):
        """已结束的块才生成：未结束返回 pending，不写库、不调模型。"""
        day = str(day or today_str())
        item = dict(item or {})
        start = str(item.get("start") or "")
        if not start:
            return {"diary": "", "mood": "平静", "place": "", "activity": ""}
        # 以当前日程为准刷新这一块：面板请求可能在改日程之前就取到了旧 item。
        try:
            latest_items = Timeline(self).blocks(day)
            latest_item = _match_block(latest_items, item)
            if latest_item is not None:
                item = dict(latest_item)
                items = latest_items
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.expand_block.refresh", _amo_exc)
        if not isinstance(items, list) or not items:
            items = self.schedule.get(day) or self.db.get_schedule(day) or []
        bid = block_id(day, item)
        start = str(item.get("start") or "")
        end = str(item.get("end") or "")
        places = block_places(items, item)
        version = self._diary_version(day)
        # 小日记只写已经过完的块：块还没走完就返回 pending，不能提前生成。
        if not slot_has_ended(day, start, end):
            logger.info(
                "[DIARY_DEBUG] block=%s start=%s timer_cancel=none next_run=%s "
                "reason=pending version=%s",
                bid, start, end or "-", version,
            )
            return {"diary": "", "mood": item.get("mood") or "平静",
                    "place": item.get("place") or "", "activity": item.get("activity") or "",
                    "pending": True, "version": version, "block_id": bid}
        activity = str(item.get("activity") or "")
        if _is_sleep_block(item, start):
            return self._diary_sleep_reply(day, item, start, end, version, bid, activity)
        facts = await self._diary_facts(day, start, end)
        record = self._diary_block_record(day, bid)
        # 归档态：过去日期的小日记只读，不再让模型改写
        archived = self._diary_archived_reply(day, record, item, activity, version, bid)
        if archived is not None:
            return archived
        # 重写预算：同一个块最多 DIARY_REWRITE_LIMIT 次，防止日程/日记互相触发死循环
        if (force or (record and str(record.get("status") or "valid") != "valid")) \
                and self._diary_rewrites(day, bid) >= DIARY_REWRITE_LIMIT:
            return self._diary_freeze_reply(day, item, record, start, end, version,
                                            bid, activity)
        cached = self._diary_block_cached_reply(day, bid, item, version, force, facts,
                                                record, places)
        if cached:
            return cached
        stale_text = ""
        if record and str(record.get("status") or "") == "stale":
            stale_text = str(record.get("text") or "")
        prev_diary = self._diary_prev_text(day, items, start)
        bundle = self._diary_context(day, items, start, facts, prev_diary=prev_diary)
        context = bundle["context"]
        try:
            context["chat_lines_hour"] = self._diary_hour_chat(day, start, end)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.expand_block.chat", _amo_exc)
        prev_item = bundle["prev_item"]
        next_item = bundle["next_item"]
        result = {"diary": "", "mood": item.get("mood") or "平静",
                  "place": item.get("place") or "", "activity": item.get("activity") or "",
                  "version": version, "block_id": bid}
        if self.engine is not None:
            try:
                result.update(await self.engine.generate_diary(
                    self.p._last_umo, context, item, prev_item, next_item, self.p.llm,
                    prev_diary=bundle["prev_diary"],
                ))
            except Exception as exc:
                logger.warning("[amo] 动态日记生成失败: %s" % exc)
        return self._block_diary_finalize(day, bid, item, result, context, prev_item,
                                          record, stale_text, force, version, places)

    def _diary_version(self, day):
        """日程版本号；老库没有 schedule_version 时按 0。"""
        get_version = getattr(self.db, "schedule_version", None)
        try:
            return int(get_version(day)) if callable(get_version) else 0
        except Exception:
            return 0

    async def _diary_facts(self, day, start, end):
        """事实池窗口 = 块窗口：跨小时块也只喂这一块的素材；取不到就空列表。

        2026-11-22 P0：块日记不再作为事实回流。以前这里会叠一层 day_view_facts
        （把当天已经生成的块日记正文当素材），上一块/孤儿块的错误就会被下一块
        继承——「日程在家、日记写逛街」正是这么滚出来的。文风/情绪衔接只保留
        上一块正文（prev_diary，作为 context 不是 fact）。
        """
        try:
            try:
                from .amo_fact_pool import build_fact_pool
            except ImportError:  # 直接按文件加载时
                from amo_fact_pool import build_fact_pool
            pool = await build_fact_pool(self.p, day, slot_start=start, slot_end=end)
            return list(pool.get("facts") or [])
        except Exception:
            return []

    def _diary_block_record(self, day, bid):
        """块日记的原始行（status/version 判定用），取不到就 None。"""
        get_record = getattr(self.db, "get_block_diary_record", None)
        record = None
        if callable(get_record):
            try:
                record = get_record(day, bid)
            except Exception as _amo_exc:
                _note_swallowed("amo_state_diary.expand_block.record", _amo_exc)
        return record

    def _diary_prev_text(self, day, items, start):
        """上一块的正文，用来给这一块当连续性锚点。"""
        prev_diary = ""
        try:
            prev_item = None
            for position, entry in enumerate(items):
                if isinstance(entry, dict) and str(entry.get("start") or "") == start:
                    prev_item = items[position - 1] if position > 0 else None
                    break
            if isinstance(prev_item, dict):
                prev_diary = str(self.db.get_block_diary(day, block_id(day, prev_item)) or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.expand_block.prev", _amo_exc)
        return prev_diary

    def _diary_archived_reply(self, day, record, item, activity, version, bid):
        """归档态：过去日期只读；不是过去日期返回 None。"""
        if day >= today_str():
            return None
        if record and str(record.get("text") or "").strip():
            return {"diary": str(record.get("text") or ""),
                    "mood": item.get("mood") or "平静",
                    "place": item.get("place") or "", "activity": activity,
                    "cached": True, "archived": True, "version": version,
                    "block_id": bid}
        return {"diary": "", "mood": item.get("mood") or "平静",
                "place": item.get("place") or "", "activity": activity,
                "archived": True, "version": version, "block_id": bid,
                "empty_message": "这一天的日记已经归档了，不再重写。"}

    def _diary_freeze_reply(self, day, item, record, start, end, version, bid, activity):
        """重写预算用完：保住最后一版正文并标 frozen。"""
        logger.warning("[DIARY_DEBUG] block=%s rewrite_budget=freeze", bid)
        # 冻结不是清空：最后一次成功生成的正文必须保住，并标成 frozen 不再被日程变更踩掉
        text = str((record or {}).get("text") or "")
        if not text:
            try:
                text = self.db.get_block_diary(day, bid) or ""
            except Exception:
                text = ""
        if record and text:
            try:
                self.db.save_block_diary(day, bid, text, start=start, end=end,
                                         place=str(item.get("place") or ""),
                                         version=version, status="frozen")
            except Exception as _amo_exc:
                _note_swallowed("amo_state_diary.expand_block.freeze_save", _amo_exc)
        payload = {"diary": text, "mood": item.get("mood") or "平静",
                   "place": item.get("place") or "", "activity": activity,
                   "frozen": True, "locked": True, "version": version, "block_id": bid}
        if not text:
            payload["empty_message"] = "这一块变动太多，日记已经锁定啦，先保留这一版。"
        return payload

    def _diary_sleep_reply(self, day, item, start, end, version, bid, activity):
        """睡眠块的占位正文；占位也要落库，否则调度每 tick 都重试同一块。"""
        logger.info("[DIARY_DEBUG] block=%s timer_cancel=1 reason=sleep", bid)
        text = "（呼呼大睡中，翻了个身又睡过去了）"
        try:
            self.db.save_block_diary(day, bid, text, start=start, end=end,
                                     place=str(item.get("place") or ""),
                                     version=version, status="valid")
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.expand_block.sleep_save", _amo_exc)
        return {"diary": text, "mood": item.get("mood") or "平静",
                "place": item.get("place") or "", "activity": activity,
                "sleep": True, "placeholder": True, "version": version, "block_id": bid}

    def _diary_block_cached_reply(self, day, bid, item, version, force, facts, record, places):
        """有效缓存直接回放（顺手过一遍事实池清洗 + 地点闸），没有则返回 None。"""
        if force or not record or str(record.get("status") or "valid") != "valid" \
                or not record.get("text"):
            return None
        # 旧日程版本写下的正文不能直接回放：即使状态还是 valid，也要按当前版本重写。
        try:
            rec_ver = int(record.get("version") or 0)
            cur_ver = int(version or 0)
        except (TypeError, ValueError):
            rec_ver, cur_ver = 0, 0
        if rec_ver and cur_ver and rec_ver < cur_ver:
            return None
        text = str(record.get("text") or "")
        if facts and self.engine is not None and hasattr(self.engine, "_sanitize_hour_diary"):
            cleaned = self.engine._sanitize_hour_diary(text, facts)
            if cleaned != text:
                text = cleaned
                try:
                    self.db.save_block_diary(
                        day, bid, text, start=str(item.get("start") or ""),
                        end=str(item.get("end") or ""), place=str(item.get("place") or ""))
                except Exception as _amo_exc:
                    _note_swallowed("amo_state_diary.expand_block.clean_cached", _amo_exc)
        text = self._guard_block_location(text, item, day, bid, places)
        return {"diary": text, "mood": item.get("mood") or "平静",
                "place": item.get("place") or "", "activity": item.get("activity") or "",
                "cached": True, "version": version, "block_id": bid}

    def _guard_block_location(self, text, item, day, bid, places=None, save=True):
        """块级地点闸：单地点块用单点规则，跨地点块用块内地点集合，避免误杀。

        旧版按整点回放，一个整点可能混了多个地点；块化之后地点判断必须看块窗口，
        否则「医院→家」这种块里合法的“家”就会被医院规则删掉。
        """
        value = str(text or "")
        if not value:
            return value
        try:
            from . import amo_location_rules as loc
        except ImportError:
            import amo_location_rules as loc
        if not loc.enabled(self.p):
            return value
        spots = [str(x).strip() for x in (places or []) if str(x).strip()]
        place = str((item or {}).get("place") or "")
        zone = str((item or {}).get("zone") or "")
        multi = len(spots) > 1
        if multi:
            violations = loc.day_check(value, spots, True)
            filtered = loc.strip_day_violations(value, spots, True)
            fallback = loc.day_fallback_line()
        else:
            single = spots[0] if spots else place
            violations = loc.check(value, single, zone)
            filtered = loc.strip_violations(value, single, zone)
            fallback = loc.fallback_line(single, zone)
        if not violations:
            return value
        if not filtered or len(filtered) < 6:
            filtered = fallback or value
        logger.info("[DIARY_GUARD] block=%s place=%s violations=%s retry=0 source=%s",
                    bid, place or zone or "、".join(spots), "、".join(violations),
                    "cache" if save else "block")
        if save:
            try:
                self.db.save_block_diary(
                    day, bid, filtered, start=str((item or {}).get("start") or ""),
                    end=str((item or {}).get("end") or ""), place=place)
            except Exception as _amo_exc:
                _note_swallowed("amo_state_diary.expand_block.guard_save", _amo_exc)
        return filtered

    def _block_diary_finalize(self, day, bid, item, result, context, prev_item,
                              record, stale_text, force, version, places):
        """块级收尾：stale 兜底、块身份变了就丢弃、按 block_id 落库、标记每日总结重写。"""
        version = result.get("version", version)
        result["block_id"] = bid
        start = str(item.get("start") or "")
        end = str(item.get("end") or "")
        place = str(item.get("place") or "")
        try:
            if (force or (record and str(record.get("status") or "valid") != "valid")) \
                    and str(result.get("diary") or "").strip():
                self._bump_diary_rewrite(day, bid)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.expand_block.rewrite_budget", _amo_exc)
        get_version = getattr(self.db, "schedule_version", None)
        get_record = getattr(self.db, "get_block_diary_record", None)
        if not result.get("diary") and stale_text:
            # 重算失败就保留旧文，不把这一块清空；下次读取会再试。
            result["diary"] = stale_text
            result["stale"] = True
            return result
        if not result.get("diary") and self.engine is not None:
            result["diary"] = self.engine.fallback_diary(context, item, prev_item)
        try:
            current_version = int(get_version(day)) if callable(get_version) else version
        except Exception:
            current_version = version
        if current_version != version:
            # 块身份 = 时间 + 活动 + 地点；生成期间块被改/被删 → id 变了，这一版作废。
            changed = True
            try:
                current_items = self.schedule.get(day) or self.db.get_schedule(day) or []
                current_item = _match_block(current_items, item)
                changed = (not current_item) or block_id(day, current_item) != bid
            except Exception as _amo_exc:
                _note_swallowed("amo_state_diary.expand_block.version_check", _amo_exc)
            record_now = None
            if callable(get_record):
                try:
                    record_now = get_record(day, bid)
                except Exception as _amo_exc:
                    _note_swallowed("amo_state_diary.expand_block.version_record", _amo_exc)
            marked_stale = bool(record_now and str(record_now.get("status") or "") == "stale")
            if changed or marked_stale:
                logger.info(
                    "[DIARY_DEBUG] block=%s reason=block_changed_discard version=%s->%s",
                    bid, version, current_version,
                )
                if stale_text:
                    result["diary"] = stale_text
                result["stale"] = True
                result["version"] = current_version
                return result
            version = current_version
            result["version"] = version
        if len(places) > 1:
            # 跨地点块：用块内地点集合过闸，别把块里真实去过的地点删掉。
            try:
                guarded = self._guard_block_location(
                    result.get("diary") or "", item, day, bid, places, save=False)
                if guarded:
                    result["diary"] = guarded
            except Exception as _amo_exc:
                _note_swallowed("amo_state_diary.expand_block.guard", _amo_exc)
        if not result.get("diary") and stale_text:
            result["diary"] = stale_text
            result["stale"] = True
            return result
        try:
            try:
                from . import amo_person_ref as _pr
            except ImportError:
                import amo_person_ref as _pr
            result["diary"] = _pr.normalize_for(self.p, result.get("diary") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.person_ref", _amo_exc)
        try:
            self.db.save_block_diary(day, bid, result["diary"], start=start, end=end,
                                     place=place, version=version, status="valid")
        except TypeError:
            # 兼容旧测试里的简化 DB
            self.db.save_block_diary(day, bid, result["diary"])
        # 小日记内容变了（手动补块/历史修正）：标 stale，等下一个 08:00 窗口再重写大日记。
        if force or stale_text or record is None:
            daily_mark = getattr(self.p, "_mark_daily_diary_stale", None)
            if callable(daily_mark):
                try:
                    daily_mark(day, version)
                except Exception as _amo_exc:
                    _note_swallowed("amo_state_diary.expand_block.daily_mark", _amo_exc)
        logger.info("[DIARY_DEBUG] block=%s reason=%s version=%s",
                    bid, "force_regen" if force else "generated", version)
        return result

    def _diary_hour_chat(self, day, start, end, limit=40):
        """这一小时的真实聊天（amo 自己 messages 表），给小日记当事实依据。"""
        try:
            begin = parse_hhmm(str(start or "")[:5])
            finish = parse_hhmm(str(end or "")[:5]) if str(end or "").strip() else begin + 60
            if finish <= begin:
                finish = begin + 60
            base = time.mktime(time.strptime(str(day)[:10], "%Y-%m-%d"))
            rows = self.db.conn.execute(
                "SELECT role, content FROM messages WHERE created_at>=? AND created_at<? "
                "ORDER BY created_at ASC",
                (base + begin * 60, base + finish * 60),
            ).fetchall()
            lines = []
            for role, content in rows[-limit:]:
                text = str(content or "").strip().replace("\n", " ")
                if not text:
                    continue
                # 系统指令/媒体占位/转账通知都不算“聊天互动”
                if text.startswith("/") or text.startswith("["):
                    continue
                if any(word in text for word in ("转账", "红包", "已收款", "收款码")):
                    continue
                # 2026-11-20：用户统一标「老公：」，不再喂裸「他：」，
                # 否则小日记会把素材标签直接抄成没有先行词的「他」。
                lines.append(("我：" if str(role) == "bot" else "老公：") + text[:200])
            return lines
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state._diary_hour_chat", _amo_exc)
            return []

    @staticmethod
    def _diary_sig(item):
        """这一小时的内容签名：活动/地点/细节/同席人。内容没变就不重生成。"""
        item = item or {}
        parts = [str(item.get("activity") or ""), str(item.get("place") or ""),
                 str(item.get("detail") or ""),
                 "/".join(str(x) for x in (item.get("people") or item.get("companions") or []))]
        raw = "|".join(parts)
        if not raw.strip("|"):
            return ""
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _diary_sig_get(self, day, hour):
        try:
            data = self.db.kv_get("diary_sig", {}) or {}
        except Exception:
            data = {}
        return str((data or {}).get("%s|%s" % (day, hour)) or "")

    def _diary_sig_set(self, day, hour, sig):
        if not sig:
            return
        try:
            data = dict(self.db.kv_get("diary_sig", {}) or {})
            data["%s|%s" % (day, hour)] = str(sig)
            if len(data) > 500:
                data = dict(list(data.items())[-300:])
            self.db.kv_set("diary_sig", data)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.diary_sig", _amo_exc)

    def _diary_rewrites(self, day, hour):
        try:
            data = self.db.kv_get("diary_rewrites", {}) or {}
        except Exception:
            data = {}
        return int((data or {}).get("%s|%s" % (day, hour)) or 0)

    def _bump_diary_rewrite(self, day, hour):
        try:
            data = dict(self.db.kv_get("diary_rewrites", {}) or {})
        except Exception:
            data = {}
        key = "%s|%s" % (day, hour)
        data[key] = int(data.get(key) or 0) + 1
        try:
            self.db.kv_set("diary_rewrites", data)
        except Exception:
            pass  # cleanup：重写预算记不上就放行
        return data[key]

    def _diary_cached_reply(self, day, start, item, version, force, facts, record):
        """有效缓存直接回放（顺手用事实池清洗一遍旧文），没有则返回 None。"""
        if not force and record and str(record.get("status") or "valid") == "valid" \
                and record.get("text"):
            # 旧日程版本写下的正文不能直接回放：即使状态还是 valid，也要按当前版本重写。
            # version=0 是早期数据/测试里的旧记录，保留原兼容行为。
            try:
                rec_ver = int(record.get("version") or 0)
                cur_ver = int(version or 0)
            except (TypeError, ValueError):
                rec_ver, cur_ver = 0, 0
            if rec_ver and cur_ver and rec_ver < cur_ver:
                return None
            text = str(record.get("text") or "")
            if facts and self.engine is not None and hasattr(self.engine, "_sanitize_hour_diary"):
                cleaned = self.engine._sanitize_hour_diary(text, facts)
                if cleaned != text:
                    text = cleaned
                    try:
                        self.db.save_diary(day, start, text)
                    except Exception as _amo_exc:
                        _note_swallowed("amo_human_state.HumanState.expand_hour.clean_cached", _amo_exc)
            text = self._guard_cached_location(text, item, day, start)
            return {
                "diary": text,
                "mood": item.get("mood") or "平静",
                "place": item.get("place") or "",
                "activity": item.get("activity") or "",
                "cached": True, "version": version,
            }
        return None

    def _guard_cached_location(self, text, item, day, start):
        """缓存回放也要过地点闸：旧版本写下的“被窝/茶几”不能继续展示。"""
        value = str(text or "")
        if not value:
            return value
        try:
            from .amo_location_rules import check, enabled, fallback_line, strip_violations
        except ImportError:
            from amo_location_rules import check, enabled, fallback_line, strip_violations
        place = str((item or {}).get("place") or "")
        zone = str((item or {}).get("zone") or "")
        if not enabled(self.p):
            return value
        violations = check(value, place, zone)
        if not violations:
            return value
        filtered = strip_violations(value, place, zone)
        if not filtered or len(filtered) < 6:
            filtered = fallback_line(place, zone)
        logger.info("[DIARY_GUARD] place=%s violations=%s retry=0 source=cache",
                    place or zone, "、".join(violations))
        try:
            self.db.save_diary(day, start, filtered)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState._guard_cached_location", _amo_exc)
        return filtered

    def _diary_context(self, day, items, start, facts, prev_diary=None):
        """拼这一段的小日记上下文：日程上下文 + 心情/健康/周期/天气/穿搭 + 前后段。

        prev_diary 传了就用它（块模式按上一块的日记衔接），没传才按整点键回查旧表。
        """
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
                # 2026-11-20：以前不传 hour/activity，按「现在」取穿搭——19:09 的火锅
                # 日记在 20:00 之后生成，就写成「穿着白睡衣就下来了」。改成按块的
                # 时间与地点取，历史块永远是它那一刻穿的那套。
                try:
                    block_hour = int(str(start or "")[:2])
                except (TypeError, ValueError):
                    block_hour = None
                block_act = None
                for entry in items or []:
                    if str((entry or {}).get("start") or "") == str(start or ""):
                        block_act = {
                            "place": str(entry.get("place") or ""),
                            "zone": str(entry.get("zone") or ""),
                            "activity": str(entry.get("activity") or ""),
                        }
                        break
                worn = wardrobe.current(day, hour=block_hour, activity=block_act) or {}
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
        prev_diary = "" if prev_diary is None else str(prev_diary)
        if not prev_diary and prev_item is not None:
            try:
                prev_diary = self.db.get_diary(day, str(prev_item.get("start") or ""))
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.expand_hour", _amo_exc)
                prev_diary = ""
        context["fact_pool"] = facts
        return {
            "context": context, "prev_item": prev_item,
            "next_item": next_item, "prev_diary": prev_diary,
        }

    def _diary_finalize(self, day, start, item, result, context, prev_item,
                        record, stale_text, force, version):
        """生成之后的收尾：stale 兜底、版本冲突丢弃、落库、标记每日总结重写。"""
        version = result.get("version", version)
        try:
            if (force or (record and str(record.get("status") or "valid") != "valid")) \
                    and str(result.get("diary") or "").strip():
                self._bump_diary_rewrite(day, start)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.expand_hour.rewrite_budget", _amo_exc)
        get_version = getattr(self.db, "schedule_version", None)
        get_record = getattr(self.db, "get_diary_record", None)
        if not result.get("diary") and stale_text:
            # 重算失败就保留旧文，不把这一小时清空；下次读取会再试。
            result["diary"] = stale_text
            result["stale"] = True
            return result
        if not result.get("diary") and self.engine is not None:
            result["diary"] = self.engine.fallback_diary(context, item, prev_item)
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
            try:
                from . import amo_person_ref as _pr
            except ImportError:
                import amo_person_ref as _pr
            result["diary"] = _pr.normalize_for(self.p, result.get("diary") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.person_ref", _amo_exc)
        try:
            self.db.save_diary(day, start, result["diary"], version=version, status="valid",
                               source="block")
        except TypeError:
            # 兼容旧测试里的简化 DB
            self.db.save_diary(day, start, result["diary"])
        self._diary_sig_set(day, start, self._diary_sig(item))
        # 小日记内容变了（手动补块/历史修正）：标 stale，等下一个 08:00 窗口再重写大日记。
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
        # 先认「此刻正在发生的那条日程」：20:00-20:18 在路上时，
        # 不能被 JIT 的 20:18「在家歇着」或过渡目的地提前顶掉。
        items = _today_items(self, today_str())
        current = minutes_now()
        chosen, reason = _pick_covering(self, items, current)
        if chosen:
            return chosen, reason

        # A1：库里只存事件，跨地点空档的“在路上”由 schedule_engine_v2 现算。
        # 过渡状态机优先之前先看派生过渡，保证聊天不会在通勤空档里说已在目的地。
        try:
            try:
                from . import schedule_observer as _obs_transit
            except ImportError:
                import schedule_observer as _obs_transit
            _derived = _obs_transit.transit_at(items, today_str(), current)
            if _derived:
                return _derived, "derived_transit"
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.current_activity.derived_transit", _amo_exc)
        # 第四轮防穿帮：过渡状态优先于日程——还没到就不许出现目的地（医院/公司…）
        try:
            try:
                from .amo_transit import transit_activity as _transit_activity
            except ImportError:
                from amo_transit import transit_activity as _transit_activity
            _transit = _transit_activity(self.p)
            if _transit:
                # 生病在家时，昨天的通勤过渡不能继续压着「在地铁上」。
                try:
                    _status = str((getattr(self, "health", {}) or {}).get("status") or "健康")
                    _place = str((_transit.get("place") if isinstance(_transit, dict) else "") or "")
                    if _status not in ("健康", "疲惫") and _place not in ("医院", "家"):
                        _transit = None
                except Exception as _amo_exc:
                    _note_swallowed("amo_state_diary.current_activity.transit_health", _amo_exc)
            if _transit:
                return _transit, "transit"
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.current_activity.transit", _amo_exc)
        today = today_str()
        jit_activity = _jit_activity(self)
        if jit_activity:
            return jit_activity, "jit"
        items = _today_items(self, today)
        current = minutes_now()
        chosen, reason = _pick_covering(self, items, current)
        if chosen:
            return chosen, reason
        chosen = _pick_stale(self, items, current)
        if chosen:
            return chosen, "stale_slot"
        if not items:  # 今天已有日程但这一小时空档：宁可沿用今天最近块，也不拿昨天通勤冒充
            chosen = _pick_yesterday_hour(self, current)
            if chosen:
                return chosen, "yesterday_same_hour"

        data = self._hour_fallback_activity()
        data["_source"] = "no_schedule"
        data["_reason"] = "今天和昨天都没有日程，按小时兜底"
        self._photo_info("[PHOTO_DEBUG] fallback=no_schedule reason=no_today_no_yesterday")
        return data, "no_schedule"

    def current_activity(self):
        return self.current_activity_info()[0]

    def _reconcile_manual_schedule_edit(self, day, old_items, new_items, source="manual_edit"):
        """手动改/删活动块后对账 activity_now / current_action / transit。

        只保证「此刻」状态和日程一致，**不写记忆**（手动编辑不是她真实经历的事）。
        面板本身没有删/改块入口，所以这套对账挂在「重生成」和「归一」两条写路径上：
        块被删/换地点后，旧的「活动块/此刻动作/过渡」不能继续压着已经不存在的场景。
        """
        day = str(day or "")
        none = {"activity_now": False, "current_action": False, "transit": False}
        if day != today_str():
            return none
        old_rows = [row for row in (old_items or []) if isinstance(row, dict)]
        new_rows = [row for row in (new_items or []) if isinstance(row, dict)]
        if [_block_signature(row) for row in old_rows] == [_block_signature(row) for row in new_rows]:
            return none
        minute = minutes_now()
        old_cur = _covering_block(old_rows, minute)
        new_cur = _covering_block(new_rows, minute)
        block_changed = _block_signature(old_cur) != _block_signature(new_cur)
        new_sigs = {_block_signature(row) for row in new_rows}
        new_places = {str(row.get("place") or "") for row in new_rows}
        new_actions = {str(row.get("activity") or "") for row in new_rows}
        cleared = dict(none)
        # 1) 活动块状态：块没了/换地点，旧的 arrival 状态必须清掉。
        try:
            activity_now = self.db.kv_get("activity_now", {}) or {}
            if (isinstance(activity_now, dict)
                    and str(activity_now.get("activity") or "").strip()
                    and (block_changed or _block_signature(activity_now) not in new_sigs)):
                self.db.kv_set("activity_now", {})
                cleared["activity_now"] = True
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.reconcile.activity_now", _amo_exc)
        # 2) JIT/微型事件写下的 current_action：和当前块不再一致就清，聊天不再说旧地点。
        try:
            action = self.db.kv_get("current_action", {}) or {}
            if isinstance(action, dict) and (action.get("action") or action.get("place")) and block_changed:
                same_block = bool(new_cur) and (
                    str(action.get("action") or "") == str(new_cur.get("activity") or "")
                    and str(action.get("place") or "") == str(new_cur.get("place") or ""))
                if not same_block:
                    self.db.kv_set("current_action", {})
                    cleared["current_action"] = True
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.reconcile.current_action", _amo_exc)
        # 3) 进行中的过渡：目的地已从日程消失就清状态（不跑 finish，避免顺手写记忆）。
        try:
            transit = self.db.kv_get("transition", {}) or {}
            to_place = str((transit or {}).get("to") or "") if isinstance(transit, dict) else ""
            if (isinstance(transit, dict) and transit.get("steps") and to_place
                    and block_changed and to_place not in new_places
                    and to_place not in new_actions):
                self.db.kv_set("transition", {})
                cleared["transit"] = True
        except Exception as _amo_exc:
            _note_swallowed("amo_state_diary.reconcile.transit", _amo_exc)
        if any(cleared.values()):
            logger.info(
                "[activity] manual_edit_reconcile day=%s source=%s "
                "activity_now=%s current_action=%s transit=%s",
                day, source, int(cleared["activity_now"]), int(cleared["current_action"]),
                int(cleared["transit"]),
            )
        return cleared

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


# ---------------- 当天连载小记（diary-live） ----------------
# 存储：kv["diary_live:<day>"] 一条 JSON 列表，每条 {ts, event_id, text, mood}；
# 只追加（读旧写新，绝不整篇覆盖），event_id 天然幂等，重复触发不会写两遍。
# 触发：块过完后先进 kv["diary_live_pending:<day>"] 记 due（debounce 5-10 分钟），
# 到点由后台 tick 追加；每次最多补 DIARY_LIVE_BATCH_MAX 条，首次启用当天已过完的块
# 按时间顺序分批补齐（不一次刷一屏）；进程内单飞，重启后 pending 还在。
# 事实门：追加前跑 amo_diary_guard.narrative_check(scope="diary")，有矛盾就不写并记跳过。
# 收束：次日大日记写成功时 diary_live_finalize 合并/去重后归档，连载本身删除；
# 连载和归档都只留 2 天（diary_live_purge）。

DIARY_LIVE_PREFIX = "diary_live:"
DIARY_LIVE_ARCHIVE_PREFIX = "diary_live_archive:"
DIARY_LIVE_PENDING_PREFIX = "diary_live_pending:"
DIARY_LIVE_SKIP_PREFIX = "diary_live_skip:"
DIARY_LIVE_DAYS_KEY = "diary_live_days"
DIARY_LIVE_ARCHIVE_DAYS_KEY = "diary_live_archive_days"
DIARY_LIVE_DEBOUNCE_DEFAULT = 7
DIARY_LIVE_DEBOUNCE_MIN = 5
DIARY_LIVE_DEBOUNCE_MAX = 10
DIARY_LIVE_KEEP_DAYS = 2
DIARY_LIVE_MAX_ENTRIES = 240
DIARY_LIVE_BATCH_MAX = 5


def diary_live_key(day):
    return DIARY_LIVE_PREFIX + str(day or "")[:10]


def _live_norm(text):
    """去重用的归一：空白和标点都不算差异。"""
    value = "".join(str(text or "").split())
    for mark in "，。！？…、；：,.!?;:\"'“”‘’":
        value = value.replace(mark, "")
    return value


def _live_day_list(value):
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        item = str(item or "")[:10]
        if len(item) == 10 and item not in out:
            out.append(item)
    return out


def _live_days(plugin, key):
    try:
        return _live_day_list(plugin.db.kv_get(key, []) or [])
    except Exception as exc:
        logger.debug("[LIVE] 天索引读取失败: %r", exc)
        return []


def _live_touch_day(plugin, key, day):
    """把 day 记进天索引（保留最近 8 天，清理时按索引删键）。"""
    day = str(day or "")[:10]
    if not day:
        return
    days = [value for value in _live_days(plugin, key) if value != day]
    days.append(day)
    days.sort()
    try:
        plugin.db.kv_set(key, days[-8:])
    except Exception as exc:
        logger.debug("[LIVE] 天索引写入失败: %r", exc)


def diary_live_entries(plugin, day):
    """当天连载（只读）：坏值/坏行按空处理，不抛。"""
    try:
        raw = plugin.db.kv_get(diary_live_key(day), []) or []
    except Exception as exc:
        logger.debug("[LIVE] 连载读取失败: %r", exc)
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        try:
            stamp = float(row.get("ts") or 0)
        except (TypeError, ValueError):
            stamp = 0.0
        out.append({"ts": stamp, "event_id": str(row.get("event_id") or ""),
                    "text": text, "mood": str(row.get("mood") or "")})
    return out


def diary_live_append(plugin, day, text, event_id="", mood="", now=None):
    """追加一条连载：只追加不覆盖；同 event_id 或同文重复写返回 duplicate。"""
    body = str(text or "").strip()
    if not body:
        return {}
    moment = now or _now()
    day = str(day or moment.date().isoformat())[:10]
    rows = diary_live_entries(plugin, day)
    ident = str(event_id or "").strip()
    if ident:
        for row in rows:
            if row.get("event_id") == ident:
                return {"appended": 0, "duplicate": True, "entry": row}
    norm = _live_norm(body)
    if norm:
        for row in rows:
            if _live_norm(row.get("text")) == norm:
                return {"appended": 0, "duplicate": True, "entry": row}
    try:
        stamp = float(moment.timestamp())
    except (AttributeError, TypeError, ValueError, OSError):
        stamp = now_ts()
    entry = {"ts": stamp, "event_id": ident, "text": body, "mood": str(mood or "")}
    rows.append(entry)
    rows = rows[-DIARY_LIVE_MAX_ENTRIES:]
    try:
        plugin.db.kv_set(diary_live_key(day), rows)
    except Exception as exc:
        logger.debug("[LIVE] 连载写入失败: %r", exc)
        return {}
    _live_touch_day(plugin, DIARY_LIVE_DAYS_KEY, day)
    return {"appended": 1, "entry": entry, "count": len(rows)}


def diary_live_mood(plugin, event=None):
    """这一条的短情绪词：块上有就用户块的，没有就看当前主导心情。"""
    value = str((event or {}).get("mood") or "").strip()
    if value:
        return value
    state = getattr(plugin, "state", None)
    dominant = getattr(state, "dominant_mood", None)
    if callable(dominant):
        try:
            value = str(dominant() or "").strip()
            if value:
                return value
        except Exception:  # cleanup: 心情读不到不算错
            pass
    return "平静"


def diary_live_time_hint(event, now=None):
    minute = event.get("end") if isinstance(event.get("end"), int) else None
    if minute is None:
        minute = event.get("start") if isinstance(event.get("start"), int) else None
    if minute is None:
        moment = now or _now()
        minute = moment.hour * 60 + moment.minute
    hour = int(minute) // 60
    if hour < 6:
        return "天还没亮"
    if hour < 11:
        return "早上"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 22:
        return "晚上"
    return "深夜"


def diary_live_compose(plugin, event, previous=None, now=None):
    """块 → 一段第一人称小记：优先块日记原文，没有才用事实拼一句。"""
    event = dict(event or {})
    body = str(event.get("diary") or "").strip()
    if not body:
        activity = str(event.get("activity") or "").strip()
        place = str(event.get("place") or "").strip()
        band = diary_live_time_hint(event, now)
        if activity:
            if activity.startswith(("在", "去", "回", "和", "跟", "陪")):
                clause = "我" + activity
            elif place:
                clause = "我在%s，%s" % (place, activity)
            else:
                clause = "我" + activity
        elif place:
            clause = "我在%s" % place
        else:
            clause = "我还在忙自己的事"
        body = "%s，%s。" % (band, clause)
    if not body.endswith(("。", "！", "？", "…", "~", "～")):
        body += "。"
    return body


def _live_day_view(plugin, day, now=None):
    try:
        try:
            from .amo_fact_timeline import build_day_view
        except ImportError:
            from amo_fact_timeline import build_day_view
        return build_day_view(plugin, day, now)
    except Exception as exc:
        logger.debug("[LIVE] 事实视图读取失败: %r", exc)
        return {"day": str(day or ""), "now": now, "events": [], "sources": {}, "count": 0}


def diary_live_gate(plugin, day, text, now=None):
    """事实门：返回 (定稿文本, 矛盾列表)；守卫异常一律放行，不拦叙事。"""
    value = str(text or "")
    try:
        try:
            from .amo_diary_guard import narrative_check
        except ImportError:
            from amo_diary_guard import narrative_check
        fixed, contradictions = narrative_check(
            value, _live_day_view(plugin, day, now), "diary", plugin=plugin, now=now)
        return str(fixed or value), list(contradictions or [])
    except Exception as exc:
        logger.debug("[LIVE] 事实门失败: %r", exc)
        return value, []


def diary_live_pending(plugin, day):
    try:
        raw = plugin.db.kv_get(DIARY_LIVE_PENDING_PREFIX + str(day or "")[:10], {}) or {}
    except Exception as exc:
        logger.debug("[LIVE] pending 读取失败: %r", exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _live_set_pending(plugin, day, data):
    try:
        plugin.db.kv_set(DIARY_LIVE_PENDING_PREFIX + str(day or "")[:10], dict(data or {}))
    except Exception as exc:
        logger.debug("[LIVE] pending 写入失败: %r", exc)


def _live_clear_pending(plugin, day):
    try:
        plugin.db.kv_delete(DIARY_LIVE_PENDING_PREFIX + str(day or "")[:10])
    except Exception:
        try:
            plugin.db.kv_set(DIARY_LIVE_PENDING_PREFIX + str(day or "")[:10], {})
        except Exception as exc:
            logger.debug("[LIVE] pending 清理失败: %r", exc)


def diary_live_skip_ids(plugin, day):
    try:
        raw = plugin.db.kv_get(DIARY_LIVE_SKIP_PREFIX + str(day or "")[:10], []) or []
    except Exception:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item or "") for item in raw if str(item or "")]


def _live_add_skip(plugin, day, event_id):
    ident = str(event_id or "").strip()
    if not ident:
        return []
    ids = diary_live_skip_ids(plugin, day)
    if ident in ids:
        return ids
    ids.append(ident)
    try:
        plugin.db.kv_set(DIARY_LIVE_SKIP_PREFIX + str(day or "")[:10], ids[-50:])
    except Exception as exc:
        logger.debug("[LIVE] 跳过记录失败: %r", exc)
    return ids


def diary_live_enabled(plugin):
    try:
        if not plugin.cfg_bool("diary_enabled", True):
            return False
    except Exception as exc:
        logger.debug("[LIVE] 日记总开关读取失败: %r", exc)
    try:
        return bool(plugin.cfg_bool("diary_live_enabled", True))
    except Exception:
        return True


def diary_live_debounce_minutes(plugin):
    """debounce：默认 7 分钟，配置值夹在 5-10 分钟之间（用户要求 5-10 分钟）。"""
    try:
        raw = plugin.cfg_int("diary_live_debounce_minutes", DIARY_LIVE_DEBOUNCE_DEFAULT)
    except Exception:
        raw = DIARY_LIVE_DEBOUNCE_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DIARY_LIVE_DEBOUNCE_DEFAULT
    return max(DIARY_LIVE_DEBOUNCE_MIN, min(DIARY_LIVE_DEBOUNCE_MAX, value))


def diary_live_candidates(plugin, day, now=None):
    """已经过完、但还没写进连载的日程块（按结束时间升序）；只认块，不认零散账本/身体条目。"""
    moment = now or _now()
    minute = moment.hour * 60 + moment.minute
    view = _live_day_view(plugin, day, moment)
    done = set()
    for row in diary_live_entries(plugin, day):
        if row.get("event_id"):
            done.add(row["event_id"])
    done.update(diary_live_skip_ids(plugin, day))
    out = []
    for event in view.get("events") or ():
        if not isinstance(event, dict):
            continue
        ident = str(event.get("event_id") or "").strip()
        if not ident or ident in done:
            continue
        if str(event.get("type") or "") != "日程":
            continue
        start = event.get("start")
        if not isinstance(start, int):
            continue
        finish = event.get("end") if isinstance(event.get("end"), int) else start
        if finish > minute:
            continue
        if not (str(event.get("activity") or "").strip() or str(event.get("place") or "").strip()):
            continue
        out.append(event)
    out.sort(key=lambda row: (int(row.get("start") or 0), int(row.get("end") or 0)))
    return out


def _live_ready(event, moment, debounce_minutes):
    """块结束满 debounce 才写：刚结束的先留着，等块日记落库，也避免抢跑。"""
    finish = event.get("end")
    if not isinstance(finish, int):
        start = event.get("start")
        finish = start if isinstance(start, int) else None
    if finish is None:
        return True
    return (moment.hour * 60 + moment.minute) >= finish + max(0, int(debounce_minutes))


def diary_live_tick(plugin, now=None):
    """后台 tick：debounce 到点后按时间顺序补写，每次最多 DIARY_LIVE_BATCH_MAX 条。

    首次启用/重启时当天可能已经过完好几个块：不再一条一条隔 7 分钟补，
    而是一批补满 N 条，剩下的后续 tick 继续（仍过事实门与去重，不刷屏、不丢）。
    单飞；任何异常都不外抛。
    """
    if not diary_live_enabled(plugin):
        return {"skipped": "disabled"}
    if getattr(plugin, "_diary_live_running", False):
        return {"skipped": "running"}
    plugin._diary_live_running = True
    try:
        moment = now or _now()
        day = moment.date().isoformat()
        debounce = diary_live_debounce_minutes(plugin)
        pending = diary_live_pending(plugin, day)
        candidates = diary_live_candidates(plugin, day, moment)
        if not candidates:
            if pending:
                _live_clear_pending(plugin, day)
            return {"day": day, "pending": 0, "appended": 0}
        try:
            stamp = float(moment.timestamp())
        except (AttributeError, TypeError, ValueError, OSError):
            stamp = now_ts()
        if not pending:
            # 第一次发现新块：不立刻写，先记 due 等 debounce（块还在继续/还会改就并进下一次）
            due = stamp + debounce * 60
            _live_set_pending(plugin, day, {
                "due": due, "event_ids": [row.get("event_id") for row in candidates[-6:]]})
            return {"day": day, "scheduled": len(candidates), "due": due, "appended": 0}
        try:
            due = float(pending.get("due") or 0)
        except (TypeError, ValueError):
            due = 0.0
        if due and stamp < due:
            return {"day": day, "waiting": True, "due": due, "appended": 0}
        ready = [row for row in candidates if _live_ready(row, moment, debounce)]
        if not ready:
            # 块刚结束、还没过 debounce：保持待写状态，后续 tick 再看，别重排 7 分钟
            _live_set_pending(plugin, day, {
                "due": stamp, "event_ids": [row.get("event_id") for row in candidates[-6:]]})
            return {"day": day, "waiting": True, "due": stamp, "appended": 0}
        batch = ready[:DIARY_LIVE_BATCH_MAX]
        previous = diary_live_entries(plugin, day)
        appended = 0
        blocked = 0
        for event in batch:
            ident = str(event.get("event_id") or "")
            text = diary_live_compose(plugin, event, previous[-1] if previous else None, moment)
            text, contradictions = diary_live_gate(plugin, day, text, moment)
            if contradictions:
                _live_add_skip(plugin, day, event.get("event_id"))
                blocked += 1
                logger.info("[LIVE] 跳过矛盾段落 event=%s", ident)
                continue
            mood = diary_live_mood(plugin, event)
            result = diary_live_append(plugin, day, text,
                                       event_id=event.get("event_id"), mood=mood, now=moment)
            if int(result.get("appended") or 0):
                appended += 1
                entry = result.get("entry")
                if entry:
                    previous = list(previous) + [entry]
            else:
                # 同文重复（两个块写了同一句话）：记跳过，别每个 tick 再试一遍
                _live_add_skip(plugin, day, event.get("event_id"))
        # 本批写的/跳过的都已经落库；剩下的（含刚结束还没到 debounce 的）留给后续 tick
        done = {str(row.get("event_id") or "") for row in batch}
        rest = [row for row in candidates
                if str(row.get("event_id") or "") not in done]
        if rest:
            _live_set_pending(plugin, day, {
                "due": stamp, "event_ids": [row.get("event_id") for row in rest[-6:]]})
        else:
            _live_clear_pending(plugin, day)
        out = {"day": day, "appended": appended}
        if blocked:
            out["blocked"] = blocked
        if rest:
            out["scheduled"] = len(rest)
        if batch:
            out["batch"] = len(batch)
        return out
    except Exception as exc:
        logger.debug("[LIVE] 连载 tick 失败: %r", exc)
        return {"error": str(exc)}
    finally:
        plugin._diary_live_running = False


def diary_live_merge(rows):
    """合并/去重（次日收束用）：同文只留一次，段与段之间空一行。"""
    texts = []
    seen = set()
    for row in rows or ():
        text = str((row or {}).get("text") or "").strip()
        if not text:
            continue
        key = _live_norm(text)
        if key:
            if key in seen:
                continue
            seen.add(key)
        texts.append(text)
    return "\n\n".join(texts)


def diary_live_snapshot(plugin, day):
    """面板「今天 · 还在写」只读取这一份。"""
    day_text = str(day or today_str())[:10]
    rows = diary_live_entries(plugin, day_text)
    texts = []
    seen = set()
    for row in rows:
        text = str(row.get("text") or "").strip()
        key = _live_norm(text)
        if text and key not in seen:
            seen.add(key)
            texts.append(text)
    return {"day": day_text, "count": len(rows), "entries": rows,
            "text": "\n\n".join(texts), "pending": bool(diary_live_pending(plugin, day_text))}


def diary_live_finalize(plugin, day, now=None):
    """次日大日记写成功：连载合并去重 → 归档 → 删连载（收束）。"""
    day_text = str(day or "")[:10]
    if not day_text:
        return {}
    rows = diary_live_entries(plugin, day_text)
    if not rows:
        _live_clear_pending(plugin, day_text)
        return {"day": day_text, "count": 0}
    moment = now or _now()
    merged = diary_live_merge(rows)
    try:
        stamp = float(moment.timestamp())
    except (AttributeError, TypeError, ValueError, OSError):
        stamp = now_ts()
    archive = {"day": day_text, "text": merged, "entries": rows, "archived_at": stamp}
    try:
        plugin.db.kv_set(DIARY_LIVE_ARCHIVE_PREFIX + day_text, archive)
        _live_touch_day(plugin, DIARY_LIVE_ARCHIVE_DAYS_KEY, day_text)
        plugin.db.kv_delete(diary_live_key(day_text))
        plugin.db.kv_delete(DIARY_LIVE_PENDING_PREFIX + day_text)
        plugin.db.kv_delete(DIARY_LIVE_SKIP_PREFIX + day_text)
    except Exception as exc:
        logger.debug("[LIVE] 归档失败: %r", exc)
        return {"day": day_text, "count": len(rows), "error": str(exc)}
    days = [value for value in _live_days(plugin, DIARY_LIVE_DAYS_KEY) if value != day_text]
    try:
        plugin.db.kv_set(DIARY_LIVE_DAYS_KEY, days)
    except Exception as exc:
        logger.debug("[LIVE] 天索引清理失败: %r", exc)
    diary_live_purge(plugin, moment)
    logger.info("[LIVE] 连载已收束 day=%s count=%d", day_text, len(rows))
    return {"day": day_text, "count": len(rows), "text": merged}


def diary_live_purge(plugin, now=None):
    """连载和归档都只留 2 天：更早的键直接删掉，天索引同步。"""
    from datetime import timedelta
    moment = now or _now()
    cutoff = (moment.date() - timedelta(days=DIARY_LIVE_KEEP_DAYS)).isoformat()
    removed = 0
    for key in (DIARY_LIVE_DAYS_KEY, DIARY_LIVE_ARCHIVE_DAYS_KEY):
        days = _live_days(plugin, key)
        keep = []
        for day in days:
            if day >= cutoff:
                keep.append(day)
                continue
            if key == DIARY_LIVE_ARCHIVE_DAYS_KEY:
                targets: tuple[str, ...] = (DIARY_LIVE_ARCHIVE_PREFIX + day,)
            else:
                targets = (diary_live_key(day), DIARY_LIVE_PENDING_PREFIX + day,
                           DIARY_LIVE_SKIP_PREFIX + day)
            for target in targets:
                try:
                    plugin.db.kv_delete(target)
                    removed += 1
                except Exception as exc:
                    logger.debug("[LIVE] 清理失败 %s: %r", target, exc)
        try:
            plugin.db.kv_set(key, keep)
        except Exception as exc:
            logger.debug("[LIVE] 天索引回写失败: %r", exc)
    return removed
