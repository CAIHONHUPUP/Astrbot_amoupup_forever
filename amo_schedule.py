# -*- coding: utf-8 -*-
"""ScheduleMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .schedule_change import (
        TRAVEL_WORDS,
        apply_change as apply_sched_change,
        detect_schedule_change,
        should_switch_to_pajamas,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .schedule_change import (
        TRAVEL_WORDS,
        apply_change as apply_sched_change,
        detect_schedule_change,
        should_switch_to_pajamas,
    )
    except ImportError:  # 直接按文件加载时
        try:
            from schedule_change import (
                TRAVEL_WORDS,
                apply_change as apply_sched_change,
                detect_schedule_change,
                should_switch_to_pajamas,
            )
        except ImportError:
            detect_schedule_change = None  # type: ignore[assignment]
            should_switch_to_pajamas = None  # type: ignore[assignment]
            TRAVEL_WORDS = ()  # type: ignore[assignment]
            apply_sched_change = None  # type: ignore[assignment]

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_scope import private_rows as _private_rows
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_scope import private_rows as _private_rows
    except ImportError:  # 直接按文件加载时
        from amo_scope import private_rows as _private_rows


import asyncio
import os
import random
import re
import time
from datetime import date, datetime, timedelta
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        PLUGIN_NAME, _DIARY_DEFAULT_DIR, _diary_build_prompt, _diary_resolve_dir, _diary_write, _now, evaluate_weather_alert, forecast_brief, logger, next_check_slot, now_ts, parse_clock, slot_key, to_float, today_str,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            PLUGIN_NAME, _DIARY_DEFAULT_DIR, _diary_build_prompt, _diary_resolve_dir, _diary_write, _now, evaluate_weather_alert, forecast_brief, logger, next_check_slot, now_ts, parse_clock, slot_key, to_float, today_str,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            PLUGIN_NAME, _DIARY_DEFAULT_DIR, _diary_build_prompt, _diary_resolve_dir, _diary_write, _now, evaluate_weather_alert, forecast_brief, logger, next_check_slot, now_ts, parse_clock, slot_key, to_float, today_str,
        )


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


def diary_end_day(now, hh, mm):
    """按 diary_time 算这一轮该收尾到哪一天；没到点返回 None。

    傍晚以后（``hh >= 18``）写当天：晚上 22:00 就能看到今天的日记；
    早上（``hh < 18``）写昨天：保留「次日早上收尾」的旧用法。
    """
    if (now.hour, now.minute) < (int(hh), int(mm)):
        return None
    if int(hh) >= 18:
        return now.date()
    return (now - timedelta(days=1)).date()


def diary_manual_target(now, requested):
    """面板手动写日记能写哪一天：只允许昨天及以前，当天和未来交给 22:00 自动写。

    返回 ``(day, error)``：能写返回 ``(date, "")``，不能写返回 ``(None, 原因)``。
    """
    yesterday = (now - timedelta(days=1)).date()
    if requested is None:
        return yesterday, ""
    if requested > yesterday:
        return None, "今天的日记 22:00 会自动写，手动只能补昨天及以前的"
    return requested, ""


class ScheduleMixin(_MixinBase):
    def _schedule_change_allowed(self, event) -> bool:
        """改日程是会落库的副作用：只认主人的私聊，群聊/其他人不算。"""
        try:
            if not self.cfg_bool("schedule_change_enabled", True):
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
        try:
            if not event.is_private_chat():
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
            return False
        owner_ids = {
            item.strip()
            for item in re.split(r"[,，\s]+", self.cfg_str("schedule_change_owner_ids", "") or "")
            if item.strip()
        }
        if owner_ids:
            uid = ""
            try:
                uid = str(self._canonical_uid(event) or "")
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
            sender = ""
            try:
                sender = str(event.get_sender_id() or "")
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
            return uid in owner_ids or sender in owner_ids
        # 没单独配名单时，沿用礼物引擎的主人判定（gift_owner_ids / 恋人关系 / 管理员）
        gift = getattr(self, "gift", None)
        checker = getattr(gift, "is_owner", None) if gift is not None else None
        if callable(checker):
            try:
                return bool(checker(event))
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
        # 既没配名单、也没礼物引擎时，退回「只认她的主页私聊」
        home_umo = ""
        try:
            getter = getattr(self, "_first_private_umo", None)
            if callable(getter):
                home_umo = str(getter() or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
        if home_umo:
            try:
                return str(getattr(event, "unified_msg_origin", "") or "") == home_umo
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._schedule_change_allowed", _amo_exc)
                return False
        return True

    async def _apply_user_schedule_change(self, event, user_text: str) -> None:
        """用户明确安排了行程（来接我 / 一起睡 / 出去吃），把那一段日程改掉。"""
        if detect_schedule_change is None or apply_sched_change is None:
            return
        try:
            # 先做便宜的关键词识别，命中再查主人（is_owner 会读一次画像库，
            # 放前面等于每条私聊都多一次 DB 查询）。
            change = detect_schedule_change(user_text)
            travel = any(word in str(user_text or "") for word in TRAVEL_WORDS)
            if not change:
                if travel:
                    # 直接出发去旅游/出差：把之前可能设置的「回家换睡衣」清掉
                    self.db.kv_set("home_early:" + today_str(), False)
                return
            if not self._schedule_change_allowed(event):
                return
            key = today_str()
            # 当天第一句话还没生成日程时，先把骨架补出来，否则「我回家了」会被直接忽略
            try:
                await self.state.ensure_schedule(str(getattr(event, "unified_msg_origin", "") or ""))
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._apply_user_schedule_change", _amo_exc)
            # schedule 挂在 HumanState 上（self.state.schedule），插件本身没有这个属性
            items = self.state.schedule.get(key) or self.db.get_schedule(key) or []
            if not items:
                return
            new_items = apply_sched_change(items, change, _now())
            if new_items == items:
                return
            self.state.schedule[key] = new_items
            self.db.kv_set("schedule", self.state.schedule)
            self.db.save_schedule(key, new_items)
            # 生病/请假长期呆家才换睡衣；旅游/出差直接出发不换
            if str(change.get("category") or "") == "health":
                status = "发烧" if "发烧" in str(user_text) else ("感冒" if "感冒" in str(user_text) else "生病")
                try:
                    self.state.set_health(status, hours=24, note="按主人的话生病请假")
                except Exception as _amo_exc:
                    _note_swallowed("amo_schedule._apply_user_schedule_change", _amo_exc)
            if should_switch_to_pajamas is not None and should_switch_to_pajamas(change, user_text):
                self.db.kv_set("home_early:" + key, True)
            elif travel:
                self.db.kv_set("home_early:" + key, False)
            # 日程突然变了：今天当前小时及以后的小日记全部作废，重新生成当前小时
            try:
                hour = _now().strftime("%H")
                self.db.clear_diaries_from(key, hour)
                current = self.state.current_activity()
                if current:
                    self._spawn(self.state.expand_hour(key, current, force=True))
            except Exception as _amo_exc:
                _note_swallowed("amo_schedule._apply_user_schedule_change", _amo_exc)
            logger.info("[amo] 按用户的话改了日程: %s" % change.get("activity"))
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule._apply_user_schedule_change", _amo_exc)

    def _migrate_schedule_defaults(self):
        """5.1.1：位置/资产改为“人格优先、缺失随机”，清掉 5.1.0 自动写入的固定默认值。"""
        try:
            if self.db.kv_get("schedule_defaults_511", False):
                return
            auto_filled = bool(self.db.kv_get("schedule_defaults_510", False))
            changed = False
            cleared_city = False
            old_defaults = {
                "schedule_city": "贵阳",
                "schedule_home": "观山湖区·小窝",
                "schedule_workplace": "公司（贵阳·观山湖区）",
                "schedule_car": "黑色奔驰C级",
            }
            for key, value in old_defaults.items():
                if self.cfg_str(key, "").strip() == value:
                    self.config[key] = ""
                    changed = True
                    if key == "schedule_city":
                        cleared_city = True
            weather_city = self.cfg_str("weather_city", "").strip()
            if weather_city == "杭州" or ((cleared_city or auto_filled) and weather_city == "贵阳"):
                self.config["weather_city"] = ""
                changed = True
            if changed and hasattr(self.config, "save_config"):
                self.config.save_config()
            self.db.kv_set("schedule_defaults_511", True)
        except Exception as exc:
            logger.debug("[amo] 日程默认配置迁移失败: %s" % exc)

    def _migrate_diary_continuity(self):
        """5.2.1：旧版动态日记是逐小时独立生成的，可能互相矛盾；升级时清一次缓存。"""
        try:
            if self.db.kv_get("diary_continuity_521", False):
                return
            self.db.conn.execute("DELETE FROM schedule_diary")
            self.db.conn.commit()
            self.db.kv_set("diary_continuity_521", True)
            logger.info("[amo] 已清理旧版日程日记缓存（5.2.1 连续性修复）")
        except Exception as exc:
            logger.debug("[amo] 清理日记缓存失败: %s" % exc)

    def _schedule_delayed_reply(self, event, profile):
        umo = event.unified_msg_origin
        delay = random.randint(
            self.cfg_int("delay_min_minutes", 4),
            self.cfg_int("delay_max_minutes", 18),
        ) * 60
        text = event.message_str or ""
        started = now_ts()

        async def runner():
            try:
                await asyncio.sleep(delay)
                if not self._running or not self._ready:
                    return
                # 这 4-18 分钟里他已经又说话了、或者她已经回过 → 这条补话已经过期，
                # 再用当初那句 event 走一遍会答非所问（真机上出现过重复回答旧问题）。
                if to_float(self._last_reply_at.get(umo, 0), 0) > started:
                    logger.debug("[amo] 延迟回复已过期（期间已回复）: %s" % umo)
                    return
                try:
                    latest = self._recent(umo, 1) or []
                    if latest and to_float(latest[-1].get("created_at"), 0) > started:
                        logger.debug("[amo] 延迟回复已过期（期间有新消息）: %s" % umo)
                        return
                except Exception as _amo_exc:
                    _note_swallowed("amo_schedule.ScheduleMixin._schedule_delayed_reply.runner", _amo_exc)
                snap = self.state.snapshot()
                note = "你刚刚忙完，主动补一句；不要提等待时间，语气自然。"
                reply = await self._compose_reply(
                    event, text, snap, profile, [], "", False, note=note
                )
                if reply:
                    await self._send_to_umo(umo, reply)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("[amo] 延迟回复失败: %s" % exc)

        self._spawn(runner())

    def _diary_data_root(self):
        """插件数据目录 data/plugin_data/<plugin_name>（上架规范要求写在这里）。"""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            return os.path.join(str(get_astrbot_data_path()), "plugin_data", PLUGIN_NAME)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._diary_data_root", _amo_exc)
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    def _diary_dir(self):
        """日记目录：默认 <插件数据目录>/diary，配置只能落在数据目录内。"""
        setting = str(self.cfg_str("diary_dir", "") or "").strip()
        root = self._diary_data_root()
        if callable(_diary_resolve_dir):
            return _diary_resolve_dir(setting, root)
        return os.path.join(root, "diary") if root else (_DIARY_DEFAULT_DIR or "")

    def _diary_clock(self):
        """diary_time 默认 22:00（傍晚以后写「当天」，白天写「昨天」）。"""
        raw = str(self.cfg_str("diary_time", "22:00") or "22:00").strip()
        try:
            hour, _, minute = raw.partition(":")
            return max(0, min(23, int(hour))), max(0, min(59, int(minute or 0)))
        except (TypeError, ValueError):
            return 22, 0

    async def _maybe_daily_diary(self):
        """到 diary_time 写一篇日记。

        - ``diary_time >= 18:00``（默认 22:00）：写**当天**的日记，当天内容到点为止；
        - 早上（``< 18:00``）：写**昨天**的，保留旧的「次日早上收尾」用法。
        """
        if not self.cfg_bool("diary_enabled", True) or not callable(_diary_write):
            return
        now = _now()
        hh, mm = self._diary_clock()
        end_day = diary_end_day(now, hh, mm)
        if end_day is None:
            return
        if getattr(self, "_diary_writing", False):
            return
        try:
            last_written = str(self.db.kv_get("diary_last_day", "") or "")
            start_day = (date.fromisoformat(last_written) + timedelta(days=1)) if last_written else end_day
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary", _amo_exc)
            start_day = end_day
        if start_day > end_day:
            return
        if (end_day - start_day).days > 6:
            start_day = end_day - timedelta(days=6)
        days = []
        cursor = start_day
        while cursor <= end_day:
            days.append(cursor)
            cursor += timedelta(days=1)
        self._diary_writing = True
        try:
            for day in days:
                guard = "diary_written:" + day.isoformat()
                attempt_key = "diary_attempt:" + day.isoformat()
                try:
                    if self.db.kv_get(guard, ""):
                        continue
                    last = to_float(self.db.kv_get(attempt_key, 0), 0)
                    if last and now_ts() - last < 1800:
                        continue
                except Exception as _amo_exc:
                    _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary", _amo_exc)
                try:
                    text = await self._generate_daily_diary(day)
                    if not text:
                        try:
                            self.db.kv_set(attempt_key, now_ts())
                        except Exception as _amo_exc:
                            _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary", _amo_exc)
                        continue
                    path = _diary_write(self._diary_dir(), day, text)
                    try:
                        self.db.kv_set(guard, path)
                        self.db.kv_set("diary_last_day", day.isoformat())
                    except Exception as _amo_exc:
                        _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary", _amo_exc)
                    logger.info("[amo] 日记已写: %s" % path)
                except Exception as exc:
                    logger.warning("[amo] 写日记失败: %s" % exc)
                    try:
                        self.db.kv_set(attempt_key, now_ts())
                    except Exception as _amo_exc:
                        _note_swallowed("amo_schedule.ScheduleMixin._maybe_daily_diary", _amo_exc)
        finally:
            self._diary_writing = False

    async def _diary_chat_lines(self, day):
        """当天聊天 → 「谁：内容」行；取不到就返回空列表。"""
        day_start = int(datetime.combine(day, datetime.min.time()).timestamp())
        mem = self._data_mem()
        if mem is None or not callable(getattr(mem, "messages_between", None)):
            return []
        try:
            rows = await self._data_call(
                "messages_between", self._data_bot_id(), day_start, day_start + 86400, "", 500,
            ) or []
        except Exception as exc:
            logger.debug("[amo] 日记取聊天失败: %s" % exc)
            return []
        # 日记是她的私人视角，之后还会进记忆：群里的对话、别人的对话都不该写进来
        rows = _private_rows(rows)
        lines = []
        for row in rows:
            text = str(row.get("content") or "").strip().replace("\n", " ")
            if not text or str(row.get("role") or "") == "system":
                continue
            who = "我" if str(row.get("role") or "") == "bot" else (str(row.get("name") or "") or "他")
            lines.append(who + "：" + text[:200])
        return lines

    def _diary_hourly_lines(self, day):
        """当天每小时小日记 → 「HH:MM 内容」行。"""
        try:
            cursor = self.db.conn.execute(
                "SELECT hour, text FROM schedule_diary WHERE day=? ORDER BY hour ASC",
                (day.isoformat(),),
            )
            rows = cursor.fetchall()
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._diary_hourly_lines", _amo_exc)
            return []
        lines = []
        for hour, text in rows:
            text = str(text or "").strip()
            if text:
                lines.append(str(hour)[:5] + " " + text[:160])
        try:
            log = self.db.kv_get("travel_log", []) or []
            for entry in log[-10:]:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("day") or "") != day.isoformat():
                    continue
                city = str(entry.get("city") or "").strip()
                hour = str(entry.get("hour") or "").strip()
                if city and hour:
                    if entry.get("journey"):
                        lines.append(hour + " 她坐车去" + city + "陪他，路上约 2 小时")
                    elif entry.get("solo"):
                        if entry.get("back"):
                            lines.append(hour + " 她自己从" + city + "旅游回来了")
                        else:
                            lines.append(hour + " 她自己出门去" + city + "旅游，晚上住酒店")
                    elif entry.get("back"):
                        lines.append(hour + " 跟他从" + city + "回来了")
                    else:
                        lines.append(hour + " 跟他出门到了" + city + "，晚上住酒店")
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._diary_hourly_lines", _amo_exc)
        try:
            rejects = self.db.kv_get("follow_reject_log", []) or []
            for entry in rejects[-5:]:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("day") or "") != day.isoformat():
                    continue
                city = str(entry.get("city") or "").strip()
                hour = str(entry.get("hour") or "").strip()
                if city:
                    lines.append(hour + " 她说想陪他去" + city + "，他说让她好好上班，她闷闷不乐")
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._diary_hourly_lines", _amo_exc)
        return lines

    async def _generate_daily_diary(self, day):
        """用当天的聊天 + 每小时日程小日记，写一篇大肥鱼口吻的日记。"""
        if not callable(_diary_write):
            return ""
        chat_lines = await self._diary_chat_lines(day)
        hourly_lines = self._diary_hourly_lines(day)
        if not chat_lines and not hourly_lines:
            return ""
        umo = self._last_umo or self._first_private_umo()
        try:
            persona = await self.llm.persona(umo)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary", _amo_exc)
            persona = ""
        system = (
            (persona or "你是一个生活在中国城市的普通女孩，说话自然口语化。") +
            "\n\n你现在要保持这个人格，用自己的口吻写日记：第一人称、像真人写给自己的，"
            "不是助手汇报，也不要提 AI、模型、插件、日程表这些词。"
        )
        prompt = _diary_build_prompt(day, chat_lines, hourly_lines)
        try:
            wardrobe = getattr(self, "wardrobe", None)
            if wardrobe is not None:
                worn = wardrobe.current(day.isoformat()) or {}
                outfit_text = str(worn.get("text") or "").strip()
                if outfit_text:
                    prompt += ("\n\n今天实际穿的是：%s。日记里如果写到衣服，"
                               "必须写这一套，不能另编。" % outfit_text)
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._generate_daily_diary", _amo_exc)
        try:
            text = await self.llm.chat_raw(umo, system, prompt)
        except Exception as exc:
            logger.warning("[amo] 日记生成失败: %s" % exc)
            return ""
        text = str(text or "").strip()
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
        text = re.sub(r"^我的日记[^\n]*\n+", "", text)
        return text if len(text) >= 10 else ""

    def _weather_alert_umos(self):
        """天气提醒的候选会话，按最近活跃排序。

        只用她**真的收到过消息**的会话（channels:<uid> + private_umo），
        没配飞书的人候选里就只有 QQ，不会随机到一个不存在的平台。
        """
        pairs = []
        try:
            for profile in self.db.proactive_profiles(limit=20):
                uid = str(profile.get("uid") or "")
                data = self.db.kv_get("channels:%s" % uid, {}) or {}
                if isinstance(data, dict):
                    for value in data.values():
                        if isinstance(value, dict) and value.get("umo"):
                            pairs.append((float(value.get("ts") or 0), str(value["umo"])))
                item = str(profile.get("private_umo") or "")
                if item:
                    pairs.append((0.0, item))
        except Exception as exc:
            logger.debug("[amo] 天气提醒选平台失败: %s" % exc)
        pairs.sort(key=lambda pair: -pair[0])
        seen = []
        for _, umo in pairs:
            if umo and umo not in seen:
                seen.append(umo)
        if not seen:
            fallback = self._last_umo or self._first_private_umo()
            if fallback:
                seen.append(fallback)
        return seen

    def _pick_weather_alert_umo(self):
        """兼容旧调用：返回最该优先尝试的那个会话。"""
        items = self._weather_alert_umos()
        return items[0] if items else ""

    @staticmethod
    def _clean_weather_alert(text):
        """把模型偶尔蹦出的客服腔/播报腔挡掉，宁可退回模板。"""
        value = str(text or "").strip().strip('"').strip("“”").strip()
        value = re.sub(r"^(?:天气提醒|提醒你|温馨提示)[：:，,\s]*", "", value)
        for bad in ("请注意", "建议您", "提醒您", "天气预报", "温馨提示", "作为AI", "作为 AI", "助手"):
            if bad in value:
                return ""
        if len(value) > 80:
            return ""
        return value

    async def _weather_alert_text(self, umo, tomorrow, fallback):
        """用她自己的语气说这句提醒；模型失败就退回模板。"""
        facts = []
        day_text = (str(tomorrow.get("textDay") or "") + " " + str(tomorrow.get("textNight") or "")).strip()
        if day_text:
            facts.append("明天天气：" + day_text)
        if tomorrow.get("tempMax"):
            facts.append("明天最高 " + str(tomorrow.get("tempMax")) + "℃")
        recent_lines = []
        try:
            for row in self._recent(umo, 8):
                content = str(row.get("content") or "").strip()
                if not content:
                    continue
                who = "他" if str(row.get("role") or "") == "user" else "你"
                recent_lines.append(who + "：" + content[:80])
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._weather_alert_text", _amo_exc)
            recent_lines = []
        try:
            state_text = self.state.prompt_text(self.state.snapshot())
        except Exception as _amo_exc:
            _note_swallowed("amo_schedule.ScheduleMixin._weather_alert_text", _amo_exc)
            state_text = ""
        system = (
            await self.llm.persona(umo) + "\n\n"
            "规则：你就是角色本人，只输出要发出去的消息，不要解释、不要旁白、不要复述数据。"
        )
        prompt = (
            (state_text + "\n" if state_text else "")
            + "事实：\n" + "\n".join(facts) + "\n"
            + ("最近聊天：\n" + "\n".join(recent_lines) + "\n" if recent_lines else "")
            + "参考说法（不要照抄）：" + fallback + "\n"
            + "请用你平时跟他说话的语气，主动提一句明天的天气。\n"
            "要求：只发一句，20-30 字以内；像真人随口说的，不要像天气播报、客服或助手；\n"
            "不要用“提醒您/请注意/建议您/记得带伞哦/注意保暖”这种模板，不要罗列温度风力；\n"
            "可以带一点情绪（关心、吐槽、嫌麻烦都行），但别煽情；不要问“要不要带伞”，不要重复你刚说过的话。"
        )
        try:
            text = await self.llm.chat(umo, system, prompt, limit=60)
        except Exception as exc:
            logger.debug("[amo] 天气提醒文案生成失败: %s" % exc)
            text = ""
        text = self._clean_weather_alert(text)
        return text or fallback

    async def _weather_query_context(self, text=""):
        """他问天气时，用缓存里的「现在天气 + 今天 + 明天」拼一段给他看。

        只有缓存过期才会真的请求和风（现在天气 30 分钟一份、预报 3 小时一份），
        所以连着问十遍通常一次都不多请。
        """
        key = self.cfg_str("hefeng_key", "")
        city = (
            self.cfg_str("weather_city", "").strip()
            or str((getattr(self.state, "schedule_profile", {}) or {}).get("city") or "").strip()
            or "贵阳"
        )
        now_data = {}
        try:
            now_data = await self.state.refresh_weather()
        except Exception as exc:
            logger.debug("[amo] 天气查询取现在天气失败: %s" % exc)
        if not now_data:
            now_data = getattr(self.state, "weather", {}) or {}
        items = []
        if key:
            try:
                items = await self.state._fetch_qweather_daily(key, city, 3)
            except Exception as exc:
                logger.debug("[amo] 天气查询取预报失败: %s" % exc)
        today = items[0] if items else {}
        tomorrow = items[1] if len(items) > 1 else {}
        brief = forecast_brief(now_data, today, tomorrow)
        if not brief:
            logger.info("[amo] 天气查询：没有可用数据")
            return "【天气查询】这会儿取不到天气数据，别编温度，直接说看不太准。"
        logger.info("[amo] 天气查询: %s" % brief)
        return "【天气查询】" + brief

    async def _maybe_weather_alert(self):
        """明天天气变坏时，到点提醒一次（每天最多一条）。

        判断一天只做两次：
        - 早上 `weather_alert_morning_time`（默认 08:00）：只判断、不发送；
        - 晚上 `weather_alert_time`（默认 20:00）：到点才真的发预报，
          且**跳过预报缓存**拿最新一份，保证当天晚上这条的准确性。

        以前每 30 秒的后台循环都会重算一遍，日志刷屏、还每次都要请一次和风接口。
        用 `weather_alert_checked`（形如 `2026-11-08|evening`）记住今天哪个槽位
        已经判断过，没到点 / 判断过的直接返回。
        """
        if not self.cfg_bool("weather_alert_enabled", False):
            return
        key = self.cfg_str("hefeng_key", "")
        if not key:
            return
        target = self.cfg_str("weather_alert_time", "20:00")
        evening_min = parse_clock(target, 20, 0)
        morning_raw = str(self.cfg_str("weather_alert_morning_time", "08:00") or "").strip()
        morning_min = parse_clock(morning_raw, 8, 0) if morning_raw else None
        now = _now()
        today = today_str()
        if str(self.db.kv_get("weather_alert_sent", "") or "") == today:
            return
        checked = str(self.db.kv_get("weather_alert_checked", "") or "")
        slot = next_check_slot(
            now.hour * 60 + now.minute, morning_min, evening_min, checked, today
        )
        if not slot:
            return
        label = "早上" if slot == "morning" else "晚上"
        city = (
            self.cfg_str("weather_city", "").strip()
            or str((getattr(self.state, "schedule_profile", {}) or {}).get("city") or "").strip()
            or "贵阳"
        )
        # 晚上要发预警，必须用最新预报：跳过 3 小时缓存。
        items = await self.state._fetch_qweather_daily(
            key, city, 3, fresh=(slot == "evening")
        )
        if len(items) < 2:
            # 拿不到预报就别每 30 秒重试，今天不提醒。
            self.db.kv_set("weather_alert_checked", slot_key(today, slot))
            return
        text = evaluate_weather_alert(
            items[0], items[1], self.cfg_int("weather_alert_temp_drop", 8)
        )
        self.db.kv_set("weather_alert_checked", slot_key(today, slot))
        if not text:
            logger.info("[amo] 天气提醒（%s）：明天天气正常，不提醒" % label)
            return
        if slot == "morning":
            # 早上只判断，真正的预报晚上再发，一天一条不追发。
            logger.info("[amo] 天气提醒（早上）：明天要变天，晚上 %s 再发给他" % target)
            return
        umos = self._weather_alert_umos()
        if not umos:
            logger.info("[amo] 天气提醒：没有可发送的会话")
            return
        text = await self._weather_alert_text(umos[0], items[1], text)
        if self.cfg_bool("weather_alert_dry_run", False):
            logger.info("[amo] 天气提醒（试跑，不发送）: %s -> %s" % (text, umos[0]))
            self.db.kv_set("weather_alert_sent", today)
            return
        # 一个一个平台试：某个平台的会话失效时换下一个，而不是整天发不出去
        sent_umo = ""
        for umo in umos:
            if await self._send_to_umo(umo, text):
                sent_umo = umo
                break
            logger.warning("[amo] 天气提醒这个平台发送失败，换下一个: %s" % umo)
        if sent_umo:
            self.db.kv_set("weather_alert_sent", today)
            self.db.kv_set("weather_alert_open", today)
            logger.info("[amo] 天气提醒已发送: %s -> %s" % (text, sent_umo))
        else:
            # 所有平台都失败：清掉 checked 让下一轮重试（原来也是每轮重试）。
            self.db.kv_set("weather_alert_checked", "")
            logger.warning("[amo] 天气提醒所有平台都失败: %s" % umos)

    async def _switch_weather_city(self, hit):
        """按城市跟随的结果改 weather_city / 显示名，并立刻刷一次天气。"""
        city_id = str(hit.get("id") or "").strip()
        name = str(hit.get("name") or "").strip()
        if not city_id or not name:
            return
        changed = False
        if str(self.config.get("weather_city") or "") != city_id:
            self.config["weather_city"] = city_id
            changed = True
        if self.cfg_str("weather_city_name", "").strip() != name:
            self.config["weather_city_name"] = name
            changed = True
        if not changed:
            return
        try:
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
        except Exception as exc:
            logger.warning("[amo] 城市跟随：保存天气城市失败: %s" % exc)
        await self.state.refresh_weather(force=True)

    def _sync_weather_city_config(self):
        """配置里填了中文城市名时，自动换算并回填 LocationID（幂等）。"""
        raw = self.cfg_str("weather_city", "").strip()
        if not raw or raw.isdigit() or "," in raw:
            return False
        hit = self._city_lookup(raw)
        if not hit:
            # 同一个错城市名每小时只提醒一次，避免后台 30 秒一轮刷日志
            cache = getattr(self, "_city_warn_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._city_warn_cache = cache
            now = time.time()
            if now - to_float(cache.get(raw), 0) > 3600:
                cache[raw] = now
                logger.warning(
                    "[amo] 城市名「%s」不在离线城市表里，天气会退回模拟数据；"
                    "可以写「省+市」或直接填 LocationID" % raw
                )
            return False
        if hit.get("ambiguous"):
            cache = getattr(self, "_city_warn_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._city_warn_cache = cache
            now = time.time()
            key = "amb:" + raw
            if now - to_float(cache.get(key), 0) > 3600:
                cache[key] = now
                logger.warning(
                    "[amo] 天气城市%s" % (hit.get("note") or "有多个同名城市，已默认选一个")
                )
        changed = False
        if str(hit["id"]) != raw:
            self.config["weather_city"] = str(hit["id"])
            changed = True
        if not self.cfg_str("weather_city_name", "").strip():
            self.config["weather_city_name"] = hit["name"]
            changed = True
        if changed:
            try:
                if hasattr(self.config, "save_config"):
                    self.config.save_config()
                logger.info("[amo] 城市「%s」→ LocationID %s（已回填）" % (raw, hit["id"]))
            except Exception as exc:
                logger.error("[amo] 回填城市配置失败: %s" % exc)
        return changed
