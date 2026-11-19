# -*- coding: utf-8 -*-
"""约会/出行 mixin：跟随、回家、出行覆盖层与约会收尾。从 amo_human_state.py 按域拆出。"""
import time
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
        _city_pick_spots,
        _city_travel_events,
        _city_travel_to_events,
        _now,
        minutes_now,
        today_str,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_common import (
            _city_pick_spots,
            _city_travel_events,
            _city_travel_to_events,
            _now,
            minutes_now,
            today_str,
        )

try:  # amo_shared：时间/日程/心情共享底层
    from .amo_shared import (
        _CITY_DEFAULT,
        logger,
        now_ts,
        parse_hhmm,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import (
            _CITY_DEFAULT,
            logger,
            now_ts,
            parse_hhmm,
        )

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object

try:  # 约会会话单一源（Phase 2）
    from . import amo_date_session as _ds
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_date_session as _ds


class DateStateMixin(_MixinBase):
    """约会/出行域：开始跟随、回家覆盖、出行覆盖层、约会收尾截断与出行日志。"""
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _fmt_minutes: Any
        _log_follow_day: Any
        _log_solo_day: Any
        commit_schedule_change: Any
        p: Any
        schedule: Any
        schedule_ctx: Any
        schedule_profile: Any


    def _date_approved(self) -> bool:
        """今天是否真的约过会：/赴约、propose_date 会写 date_now/date_fact。"""
        try:
            if _ds.active(self, now_ts()):
                return True
            row = self.db.kv_get("date_fact", {}) or {}
            try:
                from .amo_state_store import ttl_fresh as _ttl_fresh
            except ImportError:
                if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
                    from amo_state_store import ttl_fresh as _ttl_fresh
            if _ttl_fresh(row, now_ts(), zero_is_expired=True) is not None:
                return True
        except Exception:
            return False
        return False

    def settle_date_end(self, day):
        """约会已经结束、日程还挂着「和老公…」：真截断，并让小日记失效重算。"""
        day = str(day or today_str())
        if day != today_str():
            return []
        try:
            live = _ds.active(self) is not None
        except Exception:
            live = False
        if live:
            return []
        try:
            items = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
        except Exception:
            return []
        now_min = minutes_now()
        try:
            closed = float((_ds.load(self).get("closed_at") or self.db.kv_get("date_closed_at", 0) or 0))
            if closed:
                stamp = time.localtime(closed)
                if time.strftime("%Y-%m-%d", stamp) == day:
                    now_min = min(now_min, int(stamp.tm_hour) * 60 + int(stamp.tm_min))
        except Exception:
            pass  # cleanup：读不到 date_closed_at 就按当前时间收尾
        patched, hours = [], []
        for item in items:
            row = dict(item) if isinstance(item, dict) else item
            if isinstance(row, dict) and str(row.get("category")) == "social" \
                    and "老公" in str(row.get("activity") or ""):
                begin = parse_hhmm(row.get("start"))
                end = parse_hhmm(row.get("end"))
                if begin < now_min < end:
                    row["end"] = self._fmt_minutes(now_min)
                    hours.append(begin // 60)
            patched.append(row)
        if hours:
            def _truncate(rows):
                out = []
                for item in rows:
                    row = dict(item) if isinstance(item, dict) else item
                    if isinstance(row, dict) and str(row.get("category")) == "social" \
                            and "老公" in str(row.get("activity") or ""):
                        begin = parse_hhmm(row.get("start"))
                        end = parse_hhmm(row.get("end"))
                        if begin < now_min < end:
                            row["end"] = self._fmt_minutes(now_min)
                    out.append(row)
                return out

            try:
                apply = getattr(self, "apply_schedule", None)
                if callable(apply):
                    apply(day, _truncate, source="date", reason="约会收尾截断",
                          invalidate_from=min(hours))
                else:
                    self.commit_schedule_change(day, patched, old_items=items,
                                                source="date", reason="约会收尾截断",
                                                invalidate_from=min(hours))
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.settle_date_end", _amo_exc)
        return hours

    async def _schedule_apply_travel(self, today):
        """出行覆盖：他出差/她旅游的出发、游玩、返程各阶段套到日程和天气上。"""
        try:
            travel = self.db.kv_get("travel", {}) or {}
            if isinstance(travel, dict) and str(travel.get("city") or "").strip():
                city = str(travel["city"])
                start_day = str(travel.get("start_day") or "")
                today = today_str()
                if start_day and today < start_day:
                    pass
                elif today == start_day and travel.get("phase") == "journey":
                    try:
                        start_hour = int(travel.get("start_hour") or 9)
                    except (TypeError, ValueError):
                        start_hour = 9
                    rows = [x for x in (travel.get("spots") or []) if isinstance(x, dict)]
                    self.apply_follow_start(city, start_hour, rows, keep_past=False)
                    self.db.kv_set("his_city", city)
                    if travel.get("id"):
                        try:
                            await self.p._switch_weather_city({
                                "id": str(travel["id"]), "name": city,
                            })
                        except Exception as exc:
                            logger.debug("[amo] 跟随天气切换失败: %s" % exc)
                    travel["phase"] = "touring"
                    self.db.kv_set("travel", travel)
                else:
                    self.apply_travel_overlay(city)
                    self._log_follow_day(city, today)
        except Exception as exc:
            logger.debug("[amo] 出行日程覆盖失败: %s" % exc)
        try:
            trip = self.db.kv_get("wander_trip", {}) or {}
            if isinstance(trip, dict) and trip.get("city"):
                city = str(trip["city"])
                start_day = str(trip.get("day") or "")
                ret_day = str(trip.get("return_day") or start_day)
                today = today_str()
                if start_day and today < start_day:
                    pass
                elif today == start_day:
                    self.apply_travel_overlay(city)
                    trip["phase"] = "applied"
                    self.db.kv_set("wander_trip", trip)
                    self._log_solo_day(city, today)
                    if trip.get("id"):
                        try:
                            await self.p._switch_weather_city({
                                "id": str(trip["id"]), "name": city,
                            })
                        except Exception as exc:
                            logger.debug("[amo] 旅游天气切换失败: %s" % exc)
                elif start_day < today < ret_day:
                    self.apply_travel_overlay(city)
                    self._log_solo_day(city, today)
                elif today == ret_day:
                    until = int(trip.get("return_hour") or 12)
                    self.apply_return_overlay(city, until)
                    self._log_solo_day(city, today)
                    if _now().hour >= until:
                        his = str(self.db.kv_get("his_city", "") or "").strip() or _CITY_DEFAULT
                        hit = self.p._city_lookup(his)
                        if hit:
                            try:
                                await self.p._switch_weather_city(hit)
                            except Exception as _amo_exc:
                                _note_swallowed("amo_human_state.HumanState.ensure_schedule", _amo_exc)
                elif today > ret_day:
                    self.db.kv_set("wander_trip", {})
                    his = str(self.db.kv_get("his_city", "") or "").strip() or _CITY_DEFAULT
                    hit = self.p._city_lookup(his)
                    if hit:
                        try:
                            await self.p._switch_weather_city(hit)
                        except Exception as _amo_exc:
                            _note_swallowed("amo_human_state.HumanState.ensure_schedule", _amo_exc)
                    self.apply_travel_overlay(his)
                    try:
                        rows = self.db.list_profiles(limit=1) or []
                        uid = str((rows[0] or {}).get("uid") or "") if rows else ""
                        if uid:
                            self.p._external_memory_write(
                                uid, "她 %s 从 %s 旅游回来了" % (today, city), "event", 1,
                            )
                    except Exception as exc:
                        logger.debug("[amo] 回来记忆失败: %s" % exc)
        except Exception as exc:
            logger.debug("[amo] 旅游行程推进失败: %s" % exc)

    def apply_follow_start(self, city, start_hour=9, spots=None, keep_past=True):
        """跟去他那边的出发日：起床 → 出门坐车约 2 小时 → 到达 → 旅游。"""
        name = str(city or "").strip()
        if not name:
            return False
        if not self.p.cfg_bool("city_travel_schedule", True):
            return False
        today = today_str()
        try:
            start = max(0, min(21, int(start_hour or 9)))
        except (TypeError, ValueError):
            start = 9
        existing = self.schedule.get(today) or self.db.get_schedule(today) or []
        keep = []
        if keep_past:
            for item in existing:
                try:
                    hour = int(str(item.get("start") or "0")[:2])
                except Exception as _amo_exc:
                    _note_swallowed("amo_human_state.HumanState.apply_follow_start", _amo_exc)
                    hour = 0
                if hour < start:
                    keep.append(item)
        items = _city_travel_to_events(
            name, start, spots=spots, weekday=_now().date().weekday() < 5, seed=today,
        )
        if keep_past:
            items = [x for x in items if int(str(x.get("start") or "0")[:2]) >= start]
        merged = keep + items
        if not merged:
            return False
        try:
            ctx = dict(self.schedule_ctx.get(today) or {})
            if ctx:
                ctx["city"] = name
                ctx["location"] = name
                ctx["home"] = "家"
                ctx["workplace"] = "（在去" + name + "的路上）"
                self.schedule_ctx[today] = ctx
                self.db.kv_set("schedule_ctx", self.schedule_ctx)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.apply_follow_start", _amo_exc)
        self.commit_schedule_change(
            today, merged, old_items=existing,
            source="travel_follow_start", reason="follow_start",
            invalidate_from=start if keep_past else 0,
        )
        logger.info("[amo] 出发日日程：%s 点坐车去 %s" % (start, name))
        return True

    def apply_return_overlay(self, city, until_hour=12):
        """旅游最后一天：0 点到 until_hour 在旅游城市，之后回家。"""
        name = str(city or "").strip()
        if not name:
            return False
        try:
            until = max(0, min(23, int(until_hour or 12)))
        except (TypeError, ValueError):
            until = 12
        today = today_str()
        spots = []
        try:
            spots = _city_pick_spots(self.p.scenic_spots(), name, today)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.apply_return_overlay", _amo_exc)
            spots = []
        his = str(self.db.kv_get("his_city", "") or "").strip() or _CITY_DEFAULT
        existing = self.schedule.get(today) or self.db.get_schedule(today) or []
        items = _city_travel_events(name, 0, spots=spots, until_hour=until)
        items += _city_travel_events(his, until, home=True)
        if not items:
            return False
        self.commit_schedule_change(
            today, items, old_items=existing,
            source="travel_return", reason="return_overlay", invalidate_from=0,
        )
        logger.info("[amo] 旅游最后一天：%s 点前在 %s，之后回家" % (until, name))
        return True

    def apply_travel_overlay(self, city=None):
        """出行覆盖层：把今天剩下的日程换成「跟他一起在 X」（已过去的时段不动）。

        只在换城时调用；写库 + 清掉今天剩余时段的小日记，让它们按新日程重算。
        """
        travel = self.db.kv_get("travel", {}) or {}
        name = str(city or (travel.get("city") if isinstance(travel, dict) else "") or "").strip()
        if not name:
            return False
        if not self.p.cfg_bool("city_travel_schedule", True):
            return False
        today = today_str()
        from_hour = _now().hour
        home = name == _CITY_DEFAULT
        existing = self.schedule.get(today) or self.db.get_schedule(today) or []
        if not existing:
            from_hour = 0
        if home and from_hour >= 22:
            logger.info("[amo] 超过 22 点才回去，今晚住酒店，今天日程不再改")
            return False
        keep = []
        for item in existing:
            try:
                hour = int(str(item.get("start") or "0")[:2])
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.HumanState.apply_travel_overlay", _amo_exc)
                hour = 0
            if hour < from_hour:
                keep.append(item)
        spots = []
        if not home:
            try:
                spots = _city_pick_spots(self.p.scenic_spots(), name, today)
            except Exception as exc:
                logger.debug("[amo] 出行景点挑选失败: %s" % exc)
        merged = keep + _city_travel_events(
            name, from_hour, home=home, weekday=_now().date().weekday() < 5, spots=spots,
        )
        if not merged:
            return False
        try:
            ctx = dict(self.schedule_ctx.get(today) or {})
            if ctx:
                ctx["city"] = name
                ctx["location"] = name
                if home:
                    ctx["home"] = "家"
                    ctx["workplace"] = str((self.schedule_profile or {}).get("workplace") or "")
                    ctx.pop("scenic", None)
                else:
                    ctx["home"] = name + "·酒店房间"
                    ctx["workplace"] = "（旅游中，不上班）"
                    if spots:
                        ctx["scenic"] = spots[0]
                self.schedule_ctx[today] = ctx
                self.db.kv_set("schedule_ctx", self.schedule_ctx)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.apply_travel_overlay", _amo_exc)
        self.commit_schedule_change(
            today, merged, old_items=existing,
            source="travel_overlay", reason="travel_overlay",
            invalidate_from=from_hour,
        )
        logger.info("[amo] 出行日程已写入：%s 起在 %s%s" % (
            "%02d:00" % from_hour, name, "（回家）" if home else "",
        ))
        return True
