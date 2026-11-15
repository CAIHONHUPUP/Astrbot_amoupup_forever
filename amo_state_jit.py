# -*- coding: utf-8 -*-
"""JIT 即时决策 mixin（盲盒模式）：从 amo_human_state.py 按域拆出（只搬方法，行为零变化）。"""
from datetime import datetime
from typing import TYPE_CHECKING

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
        minutes_now,
        normalize_place,
    )
except ImportError:  # 直接按文件加载时
    from amo_state_common import (
        _now,
        minutes_now,
        normalize_place,
    )

try:  # amo_shared：时间/日程/心情共享底层
    from .amo_shared import (
        logger,
        now_ts,
        parse_hhmm,
        to_float,
    )
except ImportError:  # 直接按文件加载时
    from amo_shared import (
        logger,
        now_ts,
        parse_hhmm,
        to_float,
    )

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object


class JitStateMixin(_MixinBase):
    """JIT 域：到点即时决策、心跳/补洞、提交与持久化。"""

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

    def _jit_items(self, day):
        items = []
        try:
            items = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
        except Exception:
            items = []
        now = minutes_now()
        try:
            live = bool((self.db.kv_get("date_now", {}) or {}).get("line"))
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

    def _hour_has_plan(self, day, hour):
        """这一小时是不是已经有真实安排（预约/聊天改日程/补记），有就不再插一条。"""
        try:
            items = self.schedule.get(day) or self.db.get_schedule(day) or []
        except Exception:
            return False
        start_min = max(0, min(23, int(hour))) * 60
        for item in items or []:
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
            if begin <= start_min < finish:
                return True
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
        if gap < 20 * 60 or gap > 6 * 3600:
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
        return self._jit_backfill_gap(now, last_dt)

    def _jit_heartbeat(self, now):
        return self.recover_missed_ticks(now)

    def _jit_backfill_gap(self, now, last_dt):
        """把上次心跳到现在的空档补成一条“琐事”，补到本小时末防断层。"""
        day = now.date().isoformat()
        now_min = int(now.hour) * 60 + int(now.minute)
        start_min = max(0, int(last_dt.hour) * 60)
        if start_min >= now_min:
            return []
        end_min = min(24 * 60, (int(now.hour) + 1) * 60)
        try:
            items = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
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
            rows.append({
                "start": self._minute_text(begin),
                "end": self._minute_text(finish),
                "activity": "未记录",
                "place": self._jit_home(),
                "zone": "home",
                "category": "rest",
                "source": "jit_recover",
                "happened": True,
            })
        if rows:
            try:
                self.commit_schedule_change(day, items + rows, old_items=items,
                                            source="jit", reason="重启后补全空档")
            except Exception:
                return []
        return rows

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
        try:
            previous = list(self.schedule.get(sleep_day) or self.db.get_schedule(sleep_day) or [])
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
                self.commit_schedule_change(sleep_day, updated, old_items=previous,
                                            source="jit", reason="醒来后合并睡眠")
            except Exception as exc:
                _note_swallowed("amo_human_state._finish_open_sleep.commit_sleep", exc)
        try:
            today_items = list(self.schedule.get(today) or self.db.get_schedule(today) or [])
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
                self.commit_schedule_change(today, today_items, old_items=old,
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

    def jit_tick(self, now=None):
        """到点推演一次：只决定此刻做什么，不排以后。"""
        if not self._jit_enabled():
            return None
        now = now or _now()
        try:
            self._jit_heartbeat(now)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.jit_tick.heartbeat", _amo_exc)
        try:
            self._finish_open_sleep(now)
        except Exception as _amo_exc:
            _note_swallowed("amo_human_state.HumanState.jit_tick.sleep", _amo_exc)
        try:
            _date_now = self.db.kv_get("date_now", {}) or {}
            if isinstance(_date_now, dict) and float(_date_now.get("until") or 0) > now_ts():
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
        if str(plan.get("category") or "") == "sleep" and hour == 23:
            item["activity"] = "入睡"
            item["open_sleep"] = True
            self.db.kv_set("sleep_open", {"day": day, "start": "%02d:00" % hour,
                                          "at": now_ts()})
        committed, plan = self._jit_commit(day, hour, item, plan, cost, paid)
        self._jit_persist(day, hour, plan, flag)
        return item

    def _jit_plan_for_hour(self, now, hour, day):
        # 锚点优先：这天已经定下来的待办（例如“和柳如烟逛街”）先落实成真实日程，
        # 其余空余时间继续按盲盒随机推演。
        anchor = self._todo_for_hour(day, hour)
        if anchor:
            return self._todo_plan(anchor)
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
        try:
            from .schedule_engine import jit_plan
        except ImportError:
            from schedule_engine import jit_plan
        plan = jit_plan(hour, hunger, cash, bool(health.get("sick")),
                        str(self.mood.get("word") or ""), weekday=now.weekday())
        plan = dict(plan)
        try:
            if self.late is not None and hour in (12, 15) and now.weekday() >= 5 and self.late.late_yesterday(now):
                plan = {"action": "在家补觉，睡到自然醒", "place": "家", "zone": "home",
                        "category": "rest", "cost_cents": 0,
                        "diary": "昨晚熬夜，今天白天补觉。"}
        except Exception as exc:
            _note_swallowed("amo_human_state.py.2179", exc)
        plan["place"] = self._jit_place_of(plan)
        cost = int(plan.get("cost_cents") or 0)
        paid = False
        if cost:
            try:
                paid = bool(self.p.wallet.debit(cost, str(plan.get("action") or "花销"),
                                                "jit:%s:%02d" % (day, hour)))
            except Exception:
                paid = False
            if not paid:
                plan = {"action": "没舍得花钱，先忍一忍", "place": "家", "zone": "home",
                        "category": "rest", "cost_cents": 0, "diary": ""}
        return plan, cost, paid

    def _jit_commit(self, day, hour, item, plan, cost, paid):
        # 落库；失败且已扣钱就原路退款（ref 指向原扣款 idem）
        jit_idem = "jit:%s:%02d" % (day, hour)
        committed = True
        try:
            items = list(self.schedule.get(day) or self.db.get_schedule(day) or [])
            result = self.commit_schedule_change(day, items + [item], old_items=items,
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

    def _jit_persist(self, day, hour, plan, flag):
        # 写 current_action / 当日标记 / 小日记并顺手写一条活动记忆
        self.db.kv_set("current_action", {"action": plan.get("action"),
                                          "place": plan.get("place"),
                                          "at": now_ts(),
                                          "until": now_ts() + 3600,
                                          "hour": hour})
        self.db.kv_set(flag, True)
        diary = str(plan.get("diary") or "")
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
