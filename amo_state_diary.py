# -*- coding: utf-8 -*-
"""小日记 mixin：整点小日记生成/缓存/失效重算、当前活动汇总。从 amo_human_state.py 按域拆出。"""
import time
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
        DIARY_REWRITE_LIMIT,
        _jit_activity,
        _now,
        _pick_covering,
        _pick_stale,
        _pick_yesterday_hour,
        _today_items,
        minutes_now,
        slot_has_ended,
        today_str,
    )
except ImportError:  # 直接按文件加载时
    from amo_state_common import (
        DIARY_REWRITE_LIMIT,
        _jit_activity,
        _now,
        _pick_covering,
        _pick_stale,
        _pick_yesterday_hour,
        _today_items,
        minutes_now,
        slot_has_ended,
        today_str,
    )

try:  # 纯规则模块：日程 → 地点/场景优先级（照片严格跟随日程用）
    from .photo_rules import zone_for_place
except ImportError:  # 直接按文件加载时
    from photo_rules import zone_for_place

try:  # amo_shared：时间/日程/心情共享底层
    from .amo_shared import (
        city_or_empty,
        day_meta,
        logger,
        md5_int,
        parse_hhmm,
    )
except ImportError:  # 直接按文件加载时
    from amo_shared import (
        city_or_empty,
        day_meta,
        logger,
        md5_int,
        parse_hhmm,
    )

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object


class DiaryStateMixin(_MixinBase):
    """小日记域：整点小日记的上下文/生成/失效重算，以及当前活动与兜底活动。"""


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
        """点击二级卡片/调度器生成这一小时的第一人称小日记。

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
        if any(word in activity for word in ("睡觉", "睡眠", "入睡")) and start[:2] in (
                "23", "00", "01", "02", "03", "04", "05", "06"):
            logger.info("[DIARY_DEBUG] hour=%s timer_cancel=1 reason=sleep", start)
            return {"diary": "", "mood": item.get("mood") or "平静",
                    "place": item.get("place") or "", "activity": activity,
                    "sleep": True, "version": version}
        try:
            from .amo_fact_pool import build_fact_pool
            _pool = await build_fact_pool(self.p, day, hour_slot=int(start[:2] or 0))
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
                lines.append(("我：" if str(role) == "bot" else "他：") + text[:200])
            return lines
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state._diary_hour_chat", _amo_exc)
            return []

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
            text = str(record.get("text") or "")
            if facts and self.engine is not None and hasattr(self.engine, "_sanitize_hour_diary"):
                cleaned = self.engine._sanitize_hour_diary(text, facts)
                if cleaned != text:
                    text = cleaned
                    try:
                        self.db.save_diary(day, start, text)
                    except Exception as _amo_exc:
                        _note_swallowed("amo_human_state.HumanState.expand_hour.clean_cached", _amo_exc)
            return {
                "diary": text,
                "mood": item.get("mood") or "平静",
                "place": item.get("place") or "",
                "activity": item.get("activity") or "",
                "cached": True, "version": version,
            }
        return None

    def _diary_context(self, day, items, start, facts):
        """拼这一小时的小日记上下文：日程上下文 + 心情/健康/周期/天气/穿搭 + 前后段。"""
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
                worn = wardrobe.current(day) or {}
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
        prev_diary = ""
        if prev_item is not None:
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
            self.db.save_diary(day, start, result["diary"], version=version, status="valid")
        except TypeError:
            # 兼容旧测试里的简化 DB
            self.db.save_diary(day, start, result["diary"])
        # 小日记内容变了（首次生成/旧日程重算），已经写好的每日总结也要重写。
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
