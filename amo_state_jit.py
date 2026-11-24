# -*- coding: utf-8 -*-
"""JIT 即时决策 mixin（盲盒模式）：从 amo_human_state.py 按域拆出（只搬方法，行为零变化）。"""
from datetime import datetime
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
        _now,
        minutes_now,
        normalize_place,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_common import (
            _now,
            minutes_now,
            normalize_place,
        )

try:  # 时间轴：块读取/查询的唯一入口（第 1 阶段重构）
    from .amo_timeline import Timeline
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_timeline import Timeline

try:  # amo_shared：时间/日程/心情共享底层
    from .amo_shared import (
        day_meta,
        logger,
        now_ts,
        parse_hhmm,
        to_float,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import (
            day_meta,
            logger,
            now_ts,
            parse_hhmm,
            to_float,
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

try:  # 餐饮扣费唯一入口（餐次实例/价目表/日程块都在 amo_food_cost 里收口）
    from . import amo_food_cost as _food_cost
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_food_cost as _food_cost


#: 重启补跑最多回补这么多小时，避免长假/长断线一次铺满日程。
_JIT_REPLAY_MAX_HOURS = 12


def _hour_end_ts(hour, now=None):
    """这一小时结束的时间戳（current_action 的有效期边界）。"""
    now = now or _now()
    try:
        start = datetime(now.year, now.month, now.day, int(hour), 0, 0)
        return start.timestamp() + 3600
    except (TypeError, ValueError):
        return now_ts() + 3600


def _scene_constrain_plan(plugin, plan, day, now=None):
    """按场景账本硬约束钳制 JIT 计划（2026-11-22 task-25，模块级便于无宿主测试）。

    病假/事假 → 家/医院；出差 → 出差地相关 zone。钳完再走一次账本写入审计，
    保证「到点即时决策」不会在请假日记里写出公司/通勤（唯一场景源是
    amo_scene_ledger，这里不再自己猜）。
    """
    plan = dict(plan or {})
    try:
        try:
            from . import amo_scene_ledger as _ledger
        except ImportError:
            import amo_scene_ledger as _ledger
    except Exception as exc:
        _note_swallowed("amo_state_jit.scene_ledger.import", exc)
        return plan
    try:
        activity = {"activity": plan.get("action"), "place": plan.get("place"),
                    "zone": plan.get("zone"), "category": plan.get("category")}
        fixed = _ledger.constrain_activity(plugin, activity, day, now)
        if isinstance(fixed, dict) and any(
                str(fixed.get(key) or "") != str(activity.get(key) or "")
                for key in ("activity", "place", "zone")):
            plan["action"] = fixed.get("activity") or plan.get("action")
            plan["place"] = fixed.get("place") or plan.get("place")
            plan["zone"] = fixed.get("zone") or plan.get("zone")
            plan["category"] = fixed.get("category") or plan.get("category")
            plan["_scene_clamped"] = str(fixed.get("_reason") or "hard_constraint")
            # 场景被钳掉 = 原来那件事没发生：扣费/餐费元数据一起清掉，不能「人在家请假还扣逛街钱」。
            plan["cost_cents"] = 0
            plan.pop("cost", None)
            logger.info("[amo] JIT 场景硬约束：%s -> %s（%s）",
                        activity.get("place"), plan.get("place"),
                        plan["_scene_clamped"])
        written = _ledger.write(
            plugin, {"activity": plan.get("action"), "place": plan.get("place"),
                     "zone": plan.get("zone"), "category": plan.get("category")},
            source="jit", reason="JIT 到点决策", now=now)
        if isinstance(written, dict) and not written.get("ok"):
            # 钳制后仍不合法（脏 zone/未知约束）：按约束类型给兜底场景，绝不写公司/通勤。
            kind = str((_ledger.constraints(plugin, day, now) or {}).get("kind") or "")
            if kind in ("sick_leave", "leave"):
                plan.update({"action": "请假在家休息", "place": "家",
                             "zone": "home", "category": "rest"})
            else:
                plan.update({"action": "在酒店休息", "place": "酒店房间",
                             "zone": "hotel", "category": "rest"})
            plan["_scene_clamped"] = "ledger_reject"
    except Exception as exc:
        _note_swallowed("amo_state_jit.scene_constrain", exc)
    return plan


class JitStateMixin(_MixinBase):
    """JIT 域：到点即时决策、心跳/补洞、提交与持久化。"""
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _fmt_minutes: Any
        _todo_for_hour: Any
        _todo_plan: Any
        current_activity: Any
        decide_sick_action: Any
        dominant_mood: Any
        ensure_schedule: Any
        health_info: Any
        late: Any
        mood: Any
        p: Any
        settle_schedule_costs: Any
        sick_decision: Any

    # ---------- JIT 即时决策（盲盒模式） ----------

    _JIT_BLOCKS = (
        ("00:00", "06:00", "深夜"),
        ("06:00", "12:00", "上午"),
        ("12:00", "18:00", "中午"),
        ("18:00", "24:00", "晚上"),
    )

    def _jit_enabled(self):
        try:
            return bool(self.p.cfg_bool("schedule_jit", True))
        except Exception:
            return True

    def jit_action(self):
        try:
            data = self.db.kv_get("current_action", {}) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def current_action_text(self):
        data = self.jit_action()
        if not data:
            return ""
        try:
            if float(data.get("until") or 0) <= now_ts():
                return ""
        except Exception:
            return ""
        return str(data.get("action") or "")

    def _jit_items(self, day, items=None):
        _trip_kind = self._trip_day_kind(day)
        if _trip_kind:
            return self._trip_day_items(day, _trip_kind)
        if items is None:
            try:
                items = Timeline(self).blocks(day)
            except Exception as _amo_exc:
                _note_swallowed("amo_state_jit._jit_items.timeline", _amo_exc)
                items = []
        now = minutes_now()
        try:
            live = _ds.active(self) is not None
        except Exception:
            live = False
        kept = []
        for item in items:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "")
            row = dict(item)
            # 约会已经结束（date_now 清空）：那条「和老公…」不能继续显示成进行中
            if not live and category == "social" and "老公" in str(row.get("activity") or ""):
                begin = parse_hhmm(row.get("start"))
                end = parse_hhmm(row.get("end"))
                if begin < now < end:
                    row["end"] = self._fmt_minutes(min(end, now))
            if (category in ("appointment", "date") or row.get("appointment")
                    or row.get("happened") or parse_hhmm(row.get("start")) <= now):
                kept.append(row)
        return kept

    def _jit_home(self):
        profile = getattr(self, "schedule_profile", {}) or {}
        home = self.p.cfg_str("schedule_home", "") or profile.get("home") or "家"
        return normalize_place(home, "home")

    @staticmethod
    def _gap_place(items, begin, finish):
        """补洞用：按前后最近的块推她当时在哪，推不出才交给调用方回家。"""
        if finish - begin > 90 or finish >= 21 * 60:
            return None, ""  # 长空档/晚上：不猜地点，交给调用方按家处理
        prev = nxt = None
        for item in items or []:
            if not isinstance(item, dict):
                continue
            try:
                start = parse_hhmm(item.get("start"))
                end_text = str(item.get("end") or "").strip()
                end = 24 * 60 if end_text in ("24:00", "0:00", "00:00") else parse_hhmm(end_text)
            except Exception:
                continue
            if end <= begin:
                prev = item
            elif start >= finish and nxt is None:
                nxt = item
        prev_place = str((prev or {}).get("place") or "").strip()
        prev_zone = str((prev or {}).get("zone") or "").strip()
        next_place = str((nxt or {}).get("place") or "").strip()
        next_zone = str((nxt or {}).get("zone") or "").strip()
        if prev_place and next_place:
            if prev_zone and prev_zone == next_zone:
                return prev_place, prev_zone
            return None, ""  # 前后不一致（比如 21:00 餐厅 → 23:00 睡觉）：按未知处理
        if next_place:
            return next_place, next_zone or "home"
        if prev_place:
            return prev_place, prev_zone or "home"
        return None, ""

    def _jit_place_of(self, plan):
        zone = str(plan.get("zone") or "")
        place = str(plan.get("place") or "")
        profile = getattr(self, "schedule_profile", {}) or {}
        if zone == "office" or place == "公司":
            return normalize_place(
                self.p.cfg_str("schedule_workplace", "") or profile.get("workplace") or "公司", "office")
        if zone == "home" or not place:
            return self._jit_home()
        return normalize_place(place, zone)

    def _trip_day_kind(self, day):
        """旅游日：traveling / rest / ""（其他日子完全不变）。"""
        try:
            try:
                from . import amo_trip as _trip
            except ImportError:
                import amo_trip as _trip
            plugin = getattr(self, "p", None) or self
            return str(_trip.day_kind(plugin, day) or "")
        except Exception as exc:
            _note_swallowed("amo_state_jit._trip_day_kind", exc)
            return ""

    def _trip_day_items(self, day, kind):
        """旅游日池：只生成一次（kv 标记）；重载后读回同一份，不重复写。"""
        try:
            try:
                from . import amo_trip as _trip
            except ImportError:
                import amo_trip as _trip
        except Exception as exc:
            _note_swallowed("amo_state_jit._trip_day_items.import", exc)
            return []
        plugin = getattr(self, "p", None) or self
        day = str(day or "")
        try:
            existing = Timeline(self).blocks(day)
        except Exception:
            existing = []
        flag = "trip_day:%s:%s" % (kind or "", day)
        try:
            if self.db.kv_get(flag, False):
                return existing
        except Exception as exc:
            _note_swallowed("amo_state_jit._trip_day_items.flag", exc)
        # 10B2：出发/返程当天已经写了自己的长途块，不再用旅游日池覆盖。
        # 10B3：跨午夜到达日只占 00:00-到达点，02:00 到家的那天白天仍要铺旅行日池，
        # 凌晨那几条长途腿跟着保留（不重不漏）；白天出发/返程当天继续不覆盖。
        if kind == "traveling":
            legs, late = _trip.travel_day_legs(existing)
            if late:
                return existing
            rows = legs + _trip.travel_day_blocks(plugin, day)
        else:
            rows, _changed = _trip.rest_day_rows(plugin, day, existing)
        if not rows:
            return existing
        try:
            Timeline(self).save(day, rows, old_items=existing,
                                source="trip_pool", reason="旅游日池")
        except Exception as exc:
            _note_swallowed("amo_state_jit._trip_day_items.commit", exc)
            return existing
        try:
            self.db.kv_set(flag, True)
        except Exception as exc:
            _note_swallowed("amo_state_jit._trip_day_items.mark", exc)
        try:
            return Timeline(self).blocks(day) or rows
        except Exception:
            return rows

    def _trip_plan_for_hour(self, now, hour, day, kind):
        """旅游日的到点决策：只用旅游事件池，绝不出上班/洗衣/做饭。"""
        try:
            try:
                from . import amo_trip as _trip
            except ImportError:
                import amo_trip as _trip
        except Exception as exc:
            _note_swallowed("amo_state_jit._trip_plan_for_hour.import", exc)
            return ({"action": "休息", "place": "家", "zone": "home",
                     "category": "rest", "cost_cents": 0, "diary": ""}, 0, False)
        plugin = getattr(self, "p", None) or self
        if kind == "rest":
            if 8 <= int(hour) < 20:
                action, place, zone, category = _trip.REST_DAY_ACTIVITY, "家", "home", "rest"
            elif int(hour) < 7 or int(hour) >= 23:
                action, place, zone, category = "睡觉", "家", "home", "sleep"
            else:
                action, place, zone, category = "在家躺着", "家", "home", "rest"
        else:
            plan = _trip.travel_event_for_hour(plugin, day, hour)
            action = str(plan.get("action") or "休息")
            place = str(plan.get("place") or "家")
            zone = str(plan.get("zone") or "")
            category = str(plan.get("category") or "rest")
        return ({"action": action, "place": place, "zone": zone,
                 "category": category, "cost_cents": 0, "diary": ""}, 0, False)

    def _hour_has_plan(self, day, hour):
        """这一小时是不是已经有真实安排（预约/聊天改日程/补记），有就不再插一条。"""
        start_min = max(0, min(23, int(hour))) * 60
        try:
            return bool(Timeline(self).between(day, start_min, start_min + 60))
        except Exception as _amo_exc:
            _note_swallowed("amo_state_jit._hour_has_plan.timeline", _amo_exc)
            return False

    @staticmethod
    def _minute_text(value):
        value = max(0, min(24 * 60, int(value)))
        if value >= 24 * 60:
            return "24:00"
        return "%02d:%02d" % (value // 60, value % 60)

    def recover_missed_ticks(self, now=None):
        """启动/断网恢复：上次心跳到现在有空洞就补成中立时间块，保证 UI 不断档。"""
        now = now or _now()
        try:
            if self._trip_day_kind(now.date().isoformat()):
                return []  # 旅行日由旅行池排，补洞不插手
        except Exception as _amo_exc:
            _note_swallowed("amo_state_jit.recover_missed_ticks.trip", _amo_exc)
        try:
            last = to_float(self.db.kv_get("jit:heartbeat", 0), 0)
        except Exception:
            return []
        current = now_ts()
        if last > 0 and current - last < 60:
            return []
        try:
            self.db.kv_set("jit:heartbeat", current)
        except Exception:
            return []
        if last <= 0:
            return []
        gap = current - last
        if gap < 20 * 60:
            return []
        try:
            last_dt = datetime.fromtimestamp(last)
        except Exception:
            return []
        if last_dt.date() != now.date():
            return []
        try:
            if self.db.kv_get("sleep_open", {}):
                return []
        except Exception as exc:
            _note_swallowed("amo_human_state.py.1996", exc)
        # 只补真实 JIT 计划；旧的“一大段琐事”兜底不再自动跑，避免长断线补出巨块。
        return self._jit_replay_hours(now, last_dt)

    def _jit_replay_hours(self, now, last_dt):
        """重启补跑：从上次心跳所在小时到当前小时，逐小时走一遍 JIT 计划。

        只补时间轴，不补扣款；当前小时也补，补完 jit_tick 会因已有计划而跳过。
        """
        day = now.date().isoformat()
        end_hour = int(now.hour)
        start_hour = max(7, int(last_dt.hour), end_hour - _JIT_REPLAY_MAX_HOURS + 1)
        rows = []
        for hour in range(start_hour, end_hour + 1):
            flag = "jit:%s:%02d" % (day, hour)
            try:
                if self.db.kv_get(flag, False):
                    continue
            except Exception as exc:
                _note_swallowed("amo_state_jit.replay.flag", exc)
            try:
                if self._hour_has_plan(day, hour):
                    self.db.kv_set(flag, True)
                    continue
            except Exception as exc:
                _note_swallowed("amo_state_jit.replay.has_plan", exc)
            hour_now = now.replace(hour=hour, minute=30, second=0, microsecond=0)
            try:
                plan, _cost, _paid = self._jit_plan_for_hour(hour_now, hour, day, charge=False)
            except Exception as exc:
                _note_swallowed("amo_state_jit.replay.plan", exc)
                continue
            item = {"start": "%02d:00" % hour,
                    "end": "24:00" if hour == 23 else "%02d:00" % (hour + 1),
                    "activity": plan.get("action"), "place": plan.get("place"),
                    "zone": plan.get("zone"), "category": plan.get("category"),
                    "source": "jit_recover", "happened": True}
            if str(plan.get("category") or "") == "sleep" and hour == 23:
                item["activity"] = "入睡"
                item["open_sleep"] = True
                try:
                    self.db.kv_set("sleep_open", {"day": day, "start": "23:00",
                                                  "at": now_ts()})
                except Exception as exc:
                    _note_swallowed("amo_state_jit.replay.sleep_open", exc)
            committed, plan = self._jit_commit(day, hour, item, plan, 0, False)
            if committed:
                rows.append(item)
            try:
                self._jit_persist(day, hour, plan, flag, committed)
            except Exception as exc:
                _note_swallowed("amo_state_jit.replay.persist", exc)
        return rows

    def _jit_heartbeat(self, now):
        """心跳：顺手按天打一次 kv_health INFO 摘要（只读，不删键）。"""
        try:
            from .amo_state_store import for_plugin as _state_store_for
            from .amo_state_store import should_log_health as _should_log_health
            if _should_log_health(now.timestamp()):
                logger.info("[amo] %s", _state_store_for(self).health_line(now=now.timestamp()))
        except Exception as exc:
            logger.debug("[amo] kv_health 心跳摘要失败: %s" % exc)
        return self.recover_missed_ticks(now)

    def _jit_backfill_gap(self, now, last_dt):
        """legacy：生产路径已改逐小时 replay，只有旧测试直接调。"""
        """把上次心跳到现在的空档补成一条“琐事”，补到本小时末防断层。"""
        day = now.date().isoformat()
        now_min = int(now.hour) * 60 + int(now.minute)
        start_min = max(0, int(last_dt.hour) * 60)
        if start_min >= now_min:
            return []
        end_min = min(24 * 60, (int(now.hour) + 1) * 60)
        timeline = Timeline(self)
        try:
            items = timeline.blocks(day)
        except Exception:
            items = []
        covered = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                begin = parse_hhmm(item.get("start"))
                end_text = str(item.get("end") or "").strip()
                finish = 24 * 60 if end_text in ("24:00", "0:00", "00:00") else parse_hhmm(end_text)
            except Exception:
                continue
            if finish <= begin:
                finish = begin + 60
            begin = max(begin, start_min)
            finish = min(finish, end_min)
            if finish > begin:
                covered.append((begin, finish))
        covered.sort()
        gaps = []
        cursor = start_min
        for begin, finish in covered:
            if begin > cursor:
                gaps.append((cursor, min(begin, end_min)))
            cursor = max(cursor, finish)
        if cursor < end_min:
            gaps.append((cursor, end_min))
        rows = []
        for begin, finish in gaps:
            if finish - begin < 5:
                continue
            place, zone = self._gap_place(items, begin, finish)
            rows.append({
                "start": self._minute_text(begin),
                "end": self._minute_text(finish),
                "activity": self._jit_gap_label(),
                "place": place or self._jit_home(),
                "zone": zone or "home",
                "category": "rest",
                "source": "jit_recover",
                "happened": True,
            })
        if rows:
            try:
                timeline.save(day, items + rows, old_items=items,
                             source="jit", reason="重启后补全空档")
            except Exception:
                return []
        return rows

    def _jit_gap_label(self):
        """空档补位的人话标题（按身体状态/地点），不写“未记录”。"""
        try:
            health = self.health_info() or {}
        except Exception:
            health = {}
        if health.get("sick"):
            return "躺在床上休息"
        try:
            place = str((self.current_activity() or {}).get("place") or "")
        except Exception:
            place = ""
        if place and place not in ("家", "卧室", "客厅", "出租屋"):
            return "在原地发着呆"
        return "在家休息（日常发呆）"

    def _finish_open_sleep(self, now):
        """早上醒来后，把昨晚 23:00 开始的睡眠合并成 23:00-07:00。"""
        try:
            opened = self.db.kv_get("sleep_open", {}) or {}
        except Exception:
            return None
        if not isinstance(opened, dict) or not opened:
            return None
        sleep_day = str(opened.get("day") or "")
        start = str(opened.get("start") or "23:00")
        today = now.date().isoformat()
        if not sleep_day or sleep_day >= today or int(now.hour) < 7:
            return None
        wake = "%02d:00" % min(23, int(now.hour))
        sleep_timeline = Timeline(self)
        try:
            previous = sleep_timeline.blocks(sleep_day)
        except Exception:
            previous = []
        updated = []
        changed = False
        for row in previous:
            row = dict(row)
            if str(row.get("start") or "") == start and row.get("open_sleep"):
                row["activity"] = "睡觉"
                row["category"] = "sleep"
                row.pop("open_sleep", None)
                row["sleep_until"] = wake
                changed = True
            updated.append(row)
        if changed:
            try:
                sleep_timeline.save(sleep_day, updated, old_items=previous,
                                    source="jit", reason="醒来后合并睡眠")
            except Exception as exc:
                _note_swallowed("amo_human_state._finish_open_sleep.commit_sleep", exc)
        today_timeline = Timeline(self)
        try:
            today_items = today_timeline.blocks(today)
        except Exception:
            today_items = []
        has_sleep = any(str(item.get("category") or "") == "sleep"
                        and str(item.get("start") or "") == "00:00" for item in today_items)
        if not has_sleep:
            old = list(today_items)
            today_items = old + [{
                "start": "00:00", "end": wake, "activity": "睡觉", "category": "sleep",
                "sleep_from": start, "source": "jit", "happened": True,
            }]
            try:
                today_timeline.save(today, today_items, old_items=old,
                                    source="jit", reason="醒来后合并睡眠")
            except Exception as exc:
                _note_swallowed("amo_human_state._finish_open_sleep.commit_today", exc)
        try:
            self.db.kv_set("sleep_last", {"day": sleep_day, "start": start,
                                          "wake": wake, "merged_at": now_ts()})
            self.db.kv_set("sleep_open", {})
        except Exception as exc:
            _note_swallowed("amo_human_state._finish_open_sleep.kv", exc)
        return {"day": sleep_day, "start": start, "wake": wake}

    async def jit_tick_async(self, now=None):
        """先让生病状态自主决策，再走同步 JIT；请假时把当天骨架也切到病假。"""
        now = now or _now()
        try:
            decision = await self.decide_sick_action(
                getattr(self.p, "_last_umo", "") or "", now)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.jit_tick_async.decide", _amo_exc)
            decision = None
        if decision and decision.get("action") == "take_sick_leave":
            day = now.date().isoformat()
            flag = "sick_rebuilt:%s" % day
            try:
                if not self.db.kv_get(flag, False):
                    await self.ensure_schedule(getattr(self.p, "_last_umo", "") or "", force=True)
                    self.db.kv_set(flag, True)
            except Exception as _amo_exc:
                _note_swallowed("amo_human_state.jit_tick_async.rebuild", _amo_exc)
        return self.jit_tick(now)

    def _sync_action_from_schedule(self, now):
        """日程当前块是 transit/chat/manual 时，把 current_action 拉回它，防两套状态打架。"""
        try:
            day = now.date().isoformat()
            block = Timeline(self).current(day, now)
            if not isinstance(block, dict):
                return None
            if str(block.get("source") or "") not in ("transit", "chat", "command", "manual"):
                return None
            current = self.db.kv_get("current_action", {}) or {}
            action = str(block.get("activity") or "在忙")
            place = str(block.get("place") or "家")
            zone = str(block.get("zone") or "home")
            if (str(current.get("action") or "") == action
                    and str(current.get("place") or "") == place
                    and str(current.get("zone") or "") == zone):
                return None
            end = parse_hhmm(block.get("end"))
            until = now.timestamp() + max(60, (end - (now.hour * 60 + now.minute)) * 60)
            setter = getattr(self.p, "set_current_action", None)
            if callable(setter):
                setter(action, until_ts=until, place=place, zone=zone,
                       category=str(block.get("category") or "life"))
            return block
        except Exception as _amo_exc:
            _note_swallowed("amo_state_jit.sync_action_from_schedule", _amo_exc)
            return None

    def jit_tick(self, now=None):
        """到点推演一次：只决定此刻做什么，不排以后。"""
        now = now or _now()
        try:
            self._sync_action_from_schedule(now)
        except Exception as _amo_exc:
            _note_swallowed("amo_state_jit.jit_tick.sync_action", _amo_exc)
        if not self._jit_enabled():
            return None
        try:
            self._jit_heartbeat(now)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.jit_tick.heartbeat", _amo_exc)
        try:
            self._finish_open_sleep(now)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.jit_tick.sleep", _amo_exc)
        try:
            self.settle_schedule_costs(now)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.jit_tick.settle_cost", _amo_exc)
        try:
            if _ds.active(self, now_ts()):
                return None  # 约会进行中：不让 JIT 覆盖“和老公在一起”
        except Exception as exc:
            _note_swallowed("amo_human_state.py.2139", exc)
        hour = int(now.hour)
        day = now.date().isoformat()
        if hour < 7:
            return None
        if self._hour_has_plan(day, hour):
            try:
                self.db.kv_set("jit:%s:%02d" % (day, hour), True)
            except Exception as exc:
                _note_swallowed("amo_human_state.jit_tick.flag", exc)
            return None
        flag = "jit:%s:%02d" % (day, hour)
        try:
            if self.db.kv_get(flag, False):
                return None
        except Exception as exc:
            _note_swallowed("amo_human_state.py.2155", exc)
        plan, cost, paid = self._jit_plan_for_hour(now, hour, day)
        item = {"start": "%02d:00" % hour,
                "end": "24:00" if hour == 23 else "%02d:00" % (hour + 1),
                "activity": plan.get("action"), "place": plan.get("place"),
                "zone": plan.get("zone"), "category": plan.get("category"),
                "source": "jit", "happened": True}
        # 餐费已按餐次实例扣过：这次落库的块必须沿用同一个 event_id，
        # 账本/日程/日记才指向同一顿饭，微型事件再来也只会复用这个块。
        _paid_instance = str(plan.get("_paid_instance") or "")
        if _paid_instance:
            item["event_id"] = _paid_instance
        if str(plan.get("category") or "") == "sleep" and hour == 23:
            item["activity"] = "入睡"
            item["open_sleep"] = True
            self.db.kv_set("sleep_open", {"day": day, "start": "%02d:00" % hour,
                                          "at": now_ts()})
        committed, plan = self._jit_commit(day, hour, item, plan, cost, paid)
        self._jit_persist(day, hour, plan, flag, committed)
        if committed and _paid_instance:
            try:
                _food_cost.coalesce_meal_blocks(
                    self.p, day, slot=str(plan.get("_paid_slot") or ""))
            except Exception as exc:
                _note_swallowed("amo_human_state.jit_tick.coalesce_meal", exc)
        return item

    def _jit_plan_for_hour(self, now, hour, day, charge=True):
        # 锚点优先：这天已经定下来的待办（例如“和柳如烟逛街”）先落实成真实日程，
        # 其余空余时间继续按盲盒随机推演。
        anchor = self._todo_for_hour(day, hour)
        if anchor:
            return self._todo_plan(anchor)
        _trip_kind = self._trip_day_kind(day)
        if _trip_kind:
            return self._trip_plan_for_hour(now, hour, day, _trip_kind)
        # 饥饿/余额/健康 → jit_plan；熬夜补觉覆盖；再按计划扣钱（扣不到就改成忍着）
        hunger = 100.0
        try:
            hunger = float(self.p.get_hunger())
        except Exception as exc:
            _note_swallowed("amo_human_state.py.2160", exc)
        try:
            cash = int(self.p.wallet.balance())
        except Exception:
            cash = 0
        health = self.health_info() or {}
        on_leave = False
        try:
            _leave = getattr(self, "on_sick_leave", None)
            on_leave = bool(_leave(day)) if callable(_leave) else False
        except Exception:
            on_leave = False
        try:
            metadata = day_meta(day)
        except Exception:
            metadata = {}
        rest_day = bool(metadata.get("rest")) if metadata else None
        try:
            _pick_type = getattr(self, "_today_day_type", None)
            day_type = (_pick_type(day) or {}) if callable(_pick_type) else {}
        except Exception:
            day_type = {}
        weather = getattr(self, "weather", {}) or {}
        try:
            _period_info = getattr(self, "period_info", None)
            period = (_period_info() or {}) if callable(_period_info) else {}
        except Exception:
            period = {}
        try:
            from .schedule_engine import jit_plan
        except ImportError:
            from schedule_engine import jit_plan
        sick_action = None
        if health.get("sick") or on_leave:
            try:
                sick_action = str((self.sick_decision(day) or {}).get("action") or "") or None
            except Exception:
                sick_action = None
        # 心情词：self.mood 只有九维数值（没有 word 键），以前这里永远传空字符串，
        # jit_plan 里 15:00「郁闷/低落 → 窝着发呆」那支成了死代码。
        mood_word = str(self.mood.get("word") or "")
        if not mood_word:
            try:
                mood_word = str(self.dominant_mood() or "")
            except Exception:
                mood_word = ""
        try:
            try:
                from .amo_shared import normalize_mood as _normalize_mood
            except ImportError:
                from amo_shared import normalize_mood as _normalize_mood
            mood_value = float(_normalize_mood(self.mood).get(mood_word) or 0)
        except Exception:
            mood_value = 0.0
        plan = jit_plan(hour, hunger, cash, bool(health.get("sick")),
                        mood_word, weekday=now.weekday(),
                        sick_action=sick_action, mood_value=mood_value,
                        leave_day=on_leave, rest_day=rest_day,
                        weather_text=str(weather.get("text") or ""),
                        weather_temp=weather.get("temp"),
                        period_phase=str(period.get("phase") or ""),
                        day_type_id=str(day_type.get("id") or ""))
        plan = dict(plan)
        try:
            if self.late is not None and hour in (12, 15) and now.weekday() >= 5 and self.late.late_yesterday(now):
                plan = {"action": "在家补觉，睡到自然醒", "place": "家", "zone": "home",
                        "category": "rest", "cost_cents": 0,
                        "diary": "昨晚熬夜，今天白天补觉。"}
        except Exception as exc:
            _note_swallowed("amo_human_state.py.2179", exc)
        plan["place"] = self._jit_place_of(plan)
        # 2026-11-22 task-25：病假/事假/出差硬约束在落库前统一钳制（唯一场景源）。
        plan = _scene_constrain_plan(self, plan, day, now)
        cost = int(plan.get("cost_cents") or 0)
        paid = False
        paid_idem = ""
        paid_instance = ""
        paid_slot = ""
        if cost and charge:
            # 本函数只做扣费，不写场景/日程块（场景由 Timeline/commit 写）。
            # 餐饮类必须走 pay_food：JIT 自己 wallet.debit 会造出 jit:<day>:<hour>
            # 这种和餐次实例无关的幂等键，导致同一顿被 JIT/微型事件各扣一次。
            try:
                _plan_text = str(plan.get("action") or "")
                _is_meal_cost = (str(plan.get("category") or "") == "meal"
                                 or any(word in _plan_text for word in (
                                     "吃", "饭", "粥", "面条", "外卖", "夜宵", "宵夜",
                                     "烧烤", "泡面", "蛋糕")))
                if _is_meal_cost and _food_cost.looks_like_food({"text": _plan_text}):
                    result = _food_cost.pay_food(
                        self.p, str(plan.get("action") or "吃饭"),
                        str(plan.get("action") or "吃饭"), source="jit",
                        now=now, amount=cost, ensure_block=False)
                    paid = bool(result.get("charged"))
                    paid_idem = str(result.get("idem") or "")
                    paid_instance = str(result.get("instance_id") or "")
                    paid_slot = str(result.get("slot") or "")
                    if not paid and str(result.get("reason") or "") in (
                            "duplicate", "duplicate_slot"):
                        # 这一顿已经被别的入口扣过：保留「吃过了」的事实，不重复扣，
                        # 也别改成“没舍得花钱”，更不能再补一个 meal 块。
                        plan = {"action": "这顿已经吃过了，不重复吃了",
                                "place": str(plan.get("place") or "家"),
                                "zone": str(plan.get("zone") or "home"),
                                "category": "life", "cost_cents": 0, "diary": ""}
                        cost = 0
                else:
                    paid = bool(self.p.wallet.debit(
                        cost, str(plan.get("action") or "花销"),
                        "jit:%s:%02d" % (day, hour)))
                    paid_idem = "jit:%s:%02d" % (day, hour)
            except Exception:
                paid = False
            if cost and not paid:
                plan = {"action": "没舍得花钱，先忍一忍", "place": "家", "zone": "home",
                        "category": "rest", "cost_cents": 0, "diary": ""}
                paid_idem = ""
                paid_instance = ""
            plan["_paid_idem"] = paid_idem
            plan["_paid_instance"] = paid_instance
            plan["_paid_slot"] = paid_slot
        if not charge:
            # 重启补跑只补时间轴，不补扣款：过去的小时不能现在再花一次钱。
            plan["cost_cents"] = 0
            plan.pop("_paid_idem", None)
            plan.pop("_paid_instance", None)
            plan.pop("_paid_slot", None)
            return plan, 0, False
        return plan, cost, paid

    def _jit_commit(self, day, hour, item, plan, cost, paid):
        # 落库；失败且已扣钱就原路退款（ref 指向原扣款 idem）
        jit_idem = str(plan.get("_paid_idem") or ("jit:%s:%02d" % (day, hour)))
        committed = True
        try:
            timeline = Timeline(self)
            items = timeline.blocks(day)
            result = timeline.save(day, items + [item], old_items=items,
                                   source="jit", reason="到点即时决策")
            if isinstance(result, dict) and result.get("saved") is False:
                committed = False
        except Exception as exc:
            committed = False
            _note_swallowed("amo_human_state.jit_tick.commit", exc)
        todo_id = int(plan.get("_todo_id") or 0)
        if committed and todo_id:
            try:
                self.db.close_todo(todo_id)
            except Exception as exc:
                _note_swallowed("amo_human_state.jit_tick.todo_close", exc)
        if cost and paid and not committed:
            # 钱已经扣了、这一小时的日程却没落库：原路退回，别留「钱没了但时间轴没记录」的穿帮。
            # 退款 idem 按「天+小时」固定，重复触发也不会退两次（wallet 里 INSERT OR IGNORE）；
            # ref 指向原扣款 idem，amo_fact_binder.reversed_idems() 会把这笔支出当成已退回。
            refunded = False
            # 退款理由写人话：面板/流水里不许出现 JIT/回滚/系统误记 这类调试词。
            _action_text = str(plan.get("action") or "")
            _refund_reason = ("外卖退款" if "外卖" in _action_text else
                              ("打车退款" if "打车" in _action_text else "退款"))
            try:
                refunded = bool(self.p.wallet.credit(
                    cost, _refund_reason,
                    "jit:rollback:%s:%02d" % (day, hour), ref=jit_idem))
            except Exception as exc:
                _note_swallowed("amo_human_state.jit_tick.refund", exc)
            if refunded:
                logger.warning("[amo] 日程落库失败，已触发原路退款：%.2f 元（ref=%s）",
                               cost / 100.0, jit_idem)
            else:
                logger.error("[amo] 日程落库失败，退款也没成功，需人工核对：%.2f 元（ref=%s）",
                             cost / 100.0, jit_idem)
            plan = {"action": "没舍得花钱，先忍一忍", "place": "家", "zone": "home",
                    "category": "rest", "cost_cents": 0, "diary": ""}
        return committed, plan

    def _jit_persist(self, day, hour, plan, flag, committed=True):
        # 没真正落库（让行/失败）就别写 flag/current_action，否则这一小时永远不会重试
        # current_action 反映「她此刻在干嘛」：即使没落库，也要写退款后的兜底动作
        self.db.kv_set("current_action", {"action": plan.get("action"),
                                          "place": plan.get("place"),
                                          "zone": plan.get("zone"),
                                          "category": plan.get("category"),
                                          "at": now_ts(),
                                          "until": _hour_end_ts(hour),
                                          "hour": hour,
                                          "source": "jit"})
        if not committed:
            return
        self.db.kv_set(flag, True)
        diary = str(plan.get("diary") or "")
        if diary:
            # 记忆写者加场景守卫：日记只写「此刻允许的地方」，病假/出差绝不留公司/通勤。
            try:
                try:
                    from .amo_scene_ledger import guard_memory_text as _guard_memory
                except ImportError:
                    from amo_scene_ledger import guard_memory_text as _guard_memory
                diary = str(_guard_memory(self, diary) or diary)
            except Exception as _amo_exc:
                _note_swallowed("amo_state_jit.persist.scene_guard", _amo_exc)
        if diary:
            try:
                self.db.kv_set("jit_diary:%s:%02d" % (day, hour), diary)
                write = getattr(self.p, "_data_call", None)
                if callable(write):
                    import asyncio
                    asyncio.get_running_loop().create_task(write(
                        "remember", self.p._data_bot_id(), "amo", diary,
                        kind="activity", importance=0.35))
            except Exception as exc:
                _note_swallowed("amo_human_state.py.2225", exc)
