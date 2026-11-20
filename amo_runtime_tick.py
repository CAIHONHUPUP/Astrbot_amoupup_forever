# -*- coding: utf-8 -*-
"""运行时·心跳 mixin：后台循环/整点维护/里程碑/每日总结/预热。从 amo_runtime.py 按域拆出。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING
import asyncio
import re
from datetime import timedelta

try:  # 三个 mixin 的共同类型底座（运行期=object，沿用拆分前的 MRO 语义）
    from .amo_runtime_ports import RuntimeMixinBase
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_runtime_ports import RuntimeMixinBase


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .observability import log_event
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .observability import log_event
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from observability import log_event


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        AstrMessageEvent, Comp, DIALECT_SAMPLES, SCHEDULE_VERSION, STYLE_HINTS, _DIARY_SHOW, _DIARY_WRITTEN, _city_block, _city_pick_spots, _city_travel_block, _diary_pick_hints, _ms_build_content, _ms_build_prompt, _ms_idle_due, _ms_should_archive, _ms_transcript, _now, attribution_leak, filter, first_person_experience, is_weather_query, leak_tokens, logger, looks_like_ai_admission, looks_like_defensive_reply, looks_like_model_leak, now_ts, render_recent_block, strip_brushoff, strip_chat_format, to_float, to_int, today_str, truncate,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            AstrMessageEvent, Comp, DIALECT_SAMPLES, SCHEDULE_VERSION, STYLE_HINTS, _DIARY_SHOW, _DIARY_WRITTEN, _city_block, _city_pick_spots, _city_travel_block, _diary_pick_hints, _ms_build_content, _ms_build_prompt, _ms_idle_due, _ms_should_archive, _ms_transcript, _now, attribution_leak, filter, first_person_experience, is_weather_query, leak_tokens, logger, looks_like_ai_admission, looks_like_defensive_reply, looks_like_model_leak, now_ts, render_recent_block, strip_brushoff, strip_chat_format, to_float, to_int, today_str, truncate,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                AstrMessageEvent, Comp, DIALECT_SAMPLES, SCHEDULE_VERSION, STYLE_HINTS, _DIARY_SHOW, _DIARY_WRITTEN, _city_block, _city_pick_spots, _city_travel_block, _diary_pick_hints, _ms_build_content, _ms_build_prompt, _ms_idle_due, _ms_should_archive, _ms_transcript, _now, attribution_leak, filter, first_person_experience, is_weather_query, leak_tokens, logger, looks_like_ai_admission, looks_like_defensive_reply, looks_like_model_leak, now_ts, render_recent_block, strip_brushoff, strip_chat_format, to_float, to_int, today_str, truncate,
            )


class RuntimeTickMixin(RuntimeMixinBase):
    _last_relation_decay: float
    _last_relation_sediment: float
    _last_purge: float
    _last_auto_learn: float
    _last_pending_check: float
    _last_schedule_save: str
    _proactive_last: float

    async def _background_loop(self):
        """后台 tick：注册进 JobManager（首跑 4s、之后 cfg 间隔，默认 30s）。

        4s 首跑由 JobManager(startup_delay=4.0) 统一实现；单飞/超时/退避/取消
        也由它负责，这里只留一个等待 stop() 的生命周期任务（测试仍可 patch 掉）。
        """
        try:
            interval = self.cfg_int("loop_interval_seconds", 30)
            self.jobs.register("tick", self._tick, interval, immediate=True,
                               timeout=180)
            self.jobs.start()
            await self.jobs.wait()
        except asyncio.CancelledError:
            return

    async def _ensure_home_images(self, force=False):
        """生成/复用客厅、卧室场景图；失败只记日志，不影响聊天。"""
        if getattr(self, "housing", None) is None:
            return {}
        try:
            try:
                from .home_refs import ensure_room_images
            except ImportError:
                from home_refs import ensure_room_images
            out = await ensure_room_images(self, self.housing, force=force)
            if out:
                logger.info("[amo] 家的场景图就绪：%s" % "、".join(sorted(out)))
            return out
        except Exception as exc:
            logger.debug("[amo] 家的场景图生成失败: %s" % exc)
            return {}

    async def _tick(self):
        if not await self._tick_housekeeping():
            return
        umo = self._last_umo or self._first_private_umo()
        if umo:
            try:
                await self._hydrate_recent(umo, 30)
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin._tick", _amo_exc)
        # 逐阶段隔离：某一阶段抛异常不能把同一轮的后续任务（主动消息 / JIT 盲盒 / 夜间事件）
        # 一起跳过。以前这里直接顺序 await，一个阶段失败就整轮停摆，JIT 和夜间事件永远轮不到。
        _stages = (
            ("social", self._tick_social, (umo,)),
            ("schedule_media", self._tick_schedule_media, (umo,)),
            ("loop_tasks", self._tick_loop_tasks, ()),
        )
        for _stage, _step, _args in _stages:
            try:
                await _step(*_args)
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin._tick." + _stage, _amo_exc)

    async def _tick_housekeeping(self):
        # 保活：照片任务、数据插件探测、心情/健康/天气维护；未就绪返回 False
        if not self._ready or self.state is None:
            logger.debug("[GATE] background skip reason=not_ready")
            return False
        if self.photo is not None:
            try:
                await self.photo.tick()
            except Exception as exc:
                logger.debug("[amo] 照片任务失败: %s" % exc)
        # 数据插件可能在陪伴之后才装：每 5 分钟重新探测一次，装上就自动接管/迁移
        if (self._data_mem() is None and self._data_store() is None
                and now_ts() - to_float(getattr(self, "_last_data_probe", 0), 0) > 300):
            self._last_data_probe = now_ts()
            self._spawn(self._sync_with_data_plugin())
        elif now_ts() - to_float(getattr(self, "_last_digest_check", 0), 0) > 300:
            self._last_digest_check = now_ts()
            self._spawn(self._maybe_daily_digest())
        # 数据插件晚于 amo 加载：它一出现就补做第一次同步（60 秒最多试一次，成功后就停）
        if (not self._data_synced and self._data_connected()
                and now_ts() - to_float(getattr(self, "_last_data_sync_try", 0), 0) > 60):
            self._last_data_sync_try = now_ts()
            self._spawn(self._sync_with_data_plugin())
        if now_ts() - to_float(getattr(self, "_last_diary_check", 0), 0) > 300:
            self._last_diary_check = now_ts()
            self._spawn(self._maybe_daily_diary())
        # 日记质检：10 分钟最多一次，一次只补审一天（最旧优先），后台跑，不阻塞 tick。
        if now_ts() - to_float(getattr(self, "_last_diary_audit", 0), 0) > 600:
            self._last_diary_audit = now_ts()
            self._spawn(self._diary_audit_tick())
        # 世界观是「触发生成」类副作用：面板 GET 只读缓存，这里在后台补一次（空才生成，7 天缓存）。
        if (not getattr(self.state, "worldview", None)
                and now_ts() - to_float(getattr(self, "_last_worldview_check", 0), 0) > 300):
            self._last_worldview_check = now_ts()
            self._spawn(self.state.ensure_worldview(self._last_umo))
        if now_ts() - to_float(getattr(self, "_last_solo_return", 0), 0) > 120:
            self._last_solo_return = now_ts()
            self._spawn(self._maybe_solo_return())
        if now_ts() - to_float(getattr(self, "_last_date_wrap", 0), 0) > 60:
            self._last_date_wrap = now_ts()
            self._spawn(self._maybe_date_wrap())
        if now_ts() - to_float(getattr(self, "_last_milestone_check", 0), 0) > 300:
            self._last_milestone_check = now_ts()
            self._spawn(self._maybe_milestone_archive())
        elif now_ts() - to_float(getattr(self, "_last_profile_sync", 0), 0) > 600:
            # 画像每 10 分钟对一次账，保证面板和本地不脱节
            self._last_profile_sync = now_ts()
            self._spawn(self._sync_profiles_to_data())
            self._spawn(self._sync_relations_from_data())
        self.state.decay_mood()
        if self.state.last_daily != today_str():
            self.state.daily_tick()
        self.state.roll_health()
        try:
            self.state.reconcile_health_schedule()
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._tick.health_schedule", _amo_exc)
        await self.state.refresh_weather()
        await self._trip_sync()
        try:
            await self._maybe_weather_alert()
        except Exception as exc:
            logger.debug("[amo] 天气提醒失败: %s" % exc)
        try:
            self.shared_activity_tick()
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime._tick.shared_activity", _amo_exc)
        return True

    async def _diary_audit_tick(self):
        """每日日记质检：审一天、写报告；HARD 已标 stale，交给小日记调度重写。"""
        try:
            try:
                from . import amo_diary_audit as audit
            except ImportError:
                import amo_diary_audit as audit
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._diary_audit_tick.import", _amo_exc)
            return {}
        try:
            return await audit.tick(self)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._diary_audit_tick", _amo_exc)
            return {}

    async def _tick_social(self, umo):
        # 社交/微型事件/记忆审计这类纯副作用钩子
        if now_ts() - to_float(getattr(self, "_last_social_check", 0), 0) > 60:
            self._last_social_check = now_ts()
            if getattr(self, "social_learn", None) is not None:
                self._spawn(self.social_learn.tick(
                    umo=umo or "",
                    uid=str(getattr(self, "_last_uid", "") or "")))
        try:
            if getattr(self, "micro_events", None) is not None:
                self.micro_events.lifecycle_tick()
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._tick.micro_lifecycle", _amo_exc)
        _micro_last = max(to_float(getattr(self, "_last_micro_event", 0), 0),
                          to_float(self.db.kv_get("micro_event:last_tick", 0), 0))
        if now_ts() - _micro_last > max(30, self.cfg_int("micro_event_interval_minutes", 90)) * 60:
            self._last_micro_event = now_ts()
            self.db.kv_set("micro_event:last_tick", self._last_micro_event)
            if getattr(self, "micro_events", None) is not None:
                self._spawn(self.micro_events.tick(
                    umo=umo or "", uid=str(getattr(self, "_last_uid", "") or "")))
        try:
            import time as _time_mod
            if 4 <= _time_mod.localtime(now_ts()).tm_hour < 5:
                _audit_key = "memory_audit:" + today_str()
                if not self.db.kv_get(_audit_key, False):
                    self.db.kv_set(_audit_key, True)
                    self._spawn(self._audit_memories())
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._tick.memory_audit", _amo_exc)

    async def _tick_schedule_media(self, umo):
        try:
            try:
                from .laundry_engine import maybe_auto_wash
            except ImportError:
                from laundry_engine import maybe_auto_wash
            washed = maybe_auto_wash(self)
            if washed.get("ok"):
                logger.info("[amo] 自动洗衣（%s）：%d 件" % (
                    washed.get("wash_mode"), washed.get("count")))
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.auto_laundry", _amo_exc)
        # 日程补齐、web 路由重试、穿搭 tick、小日记调度
        if now_ts() - to_float(getattr(self, "_last_home_image_check", 0), 0) > 600:
            self._last_home_image_check = now_ts()
            if (getattr(self, "housing", None) is not None
                    and (self.housing.current().get("images_needed")
                         or self.housing.current().get("image_status") == "pending")
                    and self.housing.can_redraw()):
                self._spawn(self._ensure_home_images())
        if (
            not self.state.schedule.get(today_str())
            or self.db.kv_get("schedule_version", "") != SCHEDULE_VERSION
            or not self.state.schedule_context(today_str())
        ):
            await self.state.ensure_schedule(umo)
        # P2-6：读接口不再做修复；瞬移修复/约会收尾改成后台显式维护（每轮 tick 幂等）。
        try:
            maintain = getattr(self.state, "maintain_schedule_day", None)
            if callable(maintain):
                maintain(today_str())
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._tick.schedule_maintenance", _amo_exc)
        try:
            self.state.apply_schedule_events()
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._tick", _amo_exc)
        # Web API 注册失败时每 5 分钟只重试失败的路由，不重复注册成功的。
        if (getattr(self, "_web_route_failures", None)
                and now_ts() - to_float(getattr(self, "_last_web_retry", 0), 0) > 300):
            self._last_web_retry = now_ts()
            try:
                self._retry_web_api_routes()
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin._tick.web_retry", _amo_exc)
        # 穿搭后台 tick：8:00 生成白天、20:00 在家生成晚上；不依赖面板打开。
        if getattr(self, "wardrobe", None) is not None:
            try:
                self.wardrobe.tick()
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin._tick.outfit", _amo_exc)
        # 小日记统一调度：每 30 秒最多扫一次，补已经过完但还没生成的小时。
        if now_ts() - to_float(getattr(self, "_last_diary_sched", 0), 0) >= 30:
            self._last_diary_sched = now_ts()
            self._spawn(self._diary_scheduler_tick())

    def _apply_retention(self, now=None):
        """保留策略（Phase 7）：表驱动入口。

        删除口径与原来的 fade_schedules(7) + purge_schedules(14) + purge_diaries(30)
        + purge_wallet_ledger(90) 一致；messages 也按 chat_log_days 兜一次（小时循环里
        已经清过，这里是同一个 14 天，不会多删）。
        """
        try:  # 包模式优先
            from .amo_retention import apply as _retention_apply
        except ImportError:  # 直接按文件加载时
            from amo_retention import apply as _retention_apply
        try:
            report = _retention_apply(self.db, now=now, cfg_getter=self.cfg_int)
        except Exception as exc:
            logger.debug("[amo] 保留策略执行失败: %s" % exc)
            return None
        if report.get("errors"):
            logger.warning("[amo] 保留策略部分失败: %s" % "；".join(report["errors"]))
        return report

    async def _tick_loop_tasks(self):
        # 慢循环：关系衰减/沉淀、清理、自动学习、主动私聊、仪式、JIT、夜间事件
        now = now_ts()
        if now - self._last_relation_decay > 3600:
            self._last_relation_decay = now
            try:
                self._decay_hate_relations()
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin.relation_decay", _amo_exc)
        if (self.cfg_bool("auto_relation", True)
                and now - self._last_relation_sediment > 1800):
            self._last_relation_sediment = now
            try:
                await self._sediment_relations()
            except Exception as exc:
                logger.debug("[amo] 关系沉淀失败: %s" % exc)
        if now - self._last_purge > 3600:
            self._last_purge = now
            try:
                self.db.purge_messages(self.cfg_int("chat_log_days", 14))
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin.purge_messages", _amo_exc)

        if self.cfg_bool("enable_auto_learn", True) and now - self._last_auto_learn > 1800:
            self._last_auto_learn = now
            try:
                await self._auto_learn()
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin.auto_learn", _amo_exc)

        if now - self._last_pending_check >= 60:
            self._last_pending_check = now
            try:
                await self._check_proactive_pending()
            except Exception as exc:
                logger.debug("[amo] 检查主动私聊回复失败: %s" % exc)

        current_dt = _now()
        if current_dt.hour == 23 and current_dt.minute >= 59:
            save_key = today_str()
            if self._last_schedule_save != save_key:
                self._last_schedule_save = save_key
                try:
                    items = self.state.schedule.get(save_key) or []
                    # 第八轮 C：快照前先归一，内存里万一有重叠也别原样落库（读接口只读，这里兜底）
                    try:
                        try:
                            from .amo_transition import normalize_day as _normalize_day
                        except ImportError:
                            from amo_transition import normalize_day as _normalize_day
                        items, _fixed = _normalize_day(items)
                        if _fixed:
                            logger.info("[amo] 23:59 快照前归一了重叠块")
                    except Exception as exc:
                        logger.debug("[amo] 快照归一失败: %s", exc)
                    if items:
                        # 走唯一提交入口（体检 + 审计 + 表/kv 同写），
                        # 不再直接 save_day 绕过体检与审计（2026-11-19 收口）。
                        self.state.commit_schedule_change(
                            save_key, items,
                            old_items=list(self.state.schedule.get(save_key) or []),
                            source="snapshot", reason="23:59 快照兜底")
                    self.db.fade_schedules(7)
                    # 清理走保留策略统一入口（Phase 7），口径与原来的 4 个 purge 调用一致
                    self._apply_retention(now)
                except Exception as exc:
                    logger.debug("[amo] 保存/清理日程失败: %s" % exc)

        if now - self._proactive_last >= self.cfg_int("proactive_check_seconds", 300):
            self._proactive_last = now
            try:
                await self._maybe_proactive()
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin.proactive", _amo_exc)
        if now - self._last_ritual >= 60:
            try:
                await self._maybe_rituals()
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin.rituals", _amo_exc)
        try:
            tick_async = getattr(self.state, "jit_tick_async", None)
            if callable(tick_async):
                await tick_async(_now())
            else:
                self.state.jit_tick(_now())
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.jit_tick", _amo_exc)
        try:
            if getattr(self.state, "late", None) is not None:
                self.state.late.tick(_now())
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.late_tick", _amo_exc)


    async def _maybe_milestone_archive(self):
        """每 N 轮私聊压成一条「里程碑」记忆；原始聊天不删，只多一条归纳。"""
        if getattr(self, "_milestone_running", False):
            return
        self._milestone_running = True
        try:
            await self._milestone_archive_once()
        finally:
            self._milestone_running = False

    async def _milestone_archive_once(self):
        """真正的归档逻辑（外面包了并发锁）。"""
        if not self.cfg_bool("milestone_enabled", True):
            return
        mem = self._data_mem()
        if mem is None or not callable(getattr(mem, "messages_since", None)):
            return
        every = max(2, self.cfg_int("milestone_every_turns", 20))
        min_messages = max(4, self.cfg_int("milestone_min_messages", 12))
        try:
            profiles = self.db.list_profiles(limit=20) or []
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._milestone_archive_once", _amo_exc)
            profiles = []
        for profile in profiles:
            uid = str(profile.get("uid") or "")
            home_umo = str(profile.get("private_umo") or "")
            if not uid or not home_umo:
                continue
            if str(profile.get("relation") or "") != "相恋":
                continue
            key = "milestone_ts:" + uid
            try:
                since = to_int(self.db.kv_get(key, 0))
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin._milestone_archive_once", _amo_exc)
                since = 0
            # 按这个人的会话取（umo），会话里双方的话都在；不能按 uid（她的行是 uid='bot'），
            # 也不能不带范围（会把所有人的聊天拉进来）。
            rows: list[Any] = []
            for one_umo in self._ritual_umos(profile)[:3]:
                try:
                    part = await self._data_call(
                        "messages_since", self._data_bot_id(), "", since, 200, umo=one_umo
                    )
                except Exception as exc:
                    logger.debug("[amo] 里程碑取聊天失败(%s): %s" % (one_umo, exc))
                    continue
                rows.extend(part or [])
            rows.sort(key=lambda r: to_int(r.get("created_at"), 0))
            # P2：这里原来连着判了两遍
            if not rows:
                continue
            if not since:
                # 第一次只从"现在"开始记，不把全部历史一次性压成一条。
                # 注意不能用 rows[-1]（那是本批 400 条里最旧的一条），否则会从历史开始滚。
                try:
                    self.db.kv_set(key, now_ts())
                except Exception as _amo_exc:
                    _note_swallowed("amo_runtime.HumanCorePlugin._milestone_archive_once", _amo_exc)
                log_event("info", "milestone.start", uid=uid)
                continue
            if not _ms_should_archive(rows, every, min_messages):
                # 每 20 轮才归档的话，话少的那天一条摘要都不会有（11-08 之后
                # conversation_summary 断了一周）。这里给一个时间兜底：
                # 距上次归档 >= 2 小时、又攒了 >= 6 条，也压一条。
                idle_minutes = max(30, self.cfg_int("milestone_idle_minutes", 120))
                idle_msgs = max(4, self.cfg_int("milestone_idle_min_messages", 6))
                if not _ms_idle_due(len(rows), since, int(now_ts()), idle_minutes, idle_msgs):
                    continue
            body = _ms_transcript(rows)
            label = _now().strftime("%Y-%m-%d")
            system = "你是长期记忆整理器。只输出一段中文总结，不要标题、不要分点、不要时间戳。"
            prompt = _ms_build_prompt(label, body)
            try:
                summary = await self.llm.chat_raw(home_umo, system, prompt)
            except Exception as exc:
                logger.debug("[amo] 里程碑总结失败: %s" % exc)
                summary = ""
            summary = re.sub(r"\s+", " ", str(summary or "")).strip()
            if summary.upper().startswith("[SKIP]") or len(summary) < 8:
                log_event("debug", "milestone.skip", uid=uid, reason="nothing_important")
                continue
            content = _ms_build_content(label, summary)
            # 里程碑是按人归纳的私聊产物，明确写进私聊域
            written = await self._write_memory_checked(
                uid, content, "milestone", 2, scope="private"
            )
            logger.info("[amo] 里程碑归档%s: %s" % ("已写入" if written else "写入失败", content[:80]))
            if written:
                try:
                    last_ts = int(rows[-1].get("created_at") or now_ts())
                    self.db.kv_set(key, max(last_ts + 1, since))
                except Exception as _amo_exc:
                    _note_swallowed("amo_runtime.HumanCorePlugin._milestone_archive_once", _amo_exc)

    async def _maybe_daily_digest(self):
        """每天上午把"昨天"的聊天压成一条长期记忆（阶段记忆）。"""
        if not self.cfg_bool("daily_digest_enabled", True):
            return
        now = _now()
        if now.hour < max(0, min(23, self.cfg_int("daily_digest_hour", 6))):
            return
        today = now.strftime("%Y-%m-%d")
        guard = "daily_digest:" + today
        try:
            if self.db.kv_get(guard, ""):
                return
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._maybe_daily_digest", _amo_exc)
        mem = self._data_mem()
        if mem is None or not callable(getattr(mem, "recall_messages", None)):
            return
        try:
            profiles = self.db.list_profiles(limit=20) or []
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._maybe_daily_digest", _amo_exc)
            profiles = []
        digest_written = 0
        digest_failed = 0
        digest_attempted = 0
        for profile in profiles:
            lines = await self._digest_profile_lines(profile)
            if not lines:
                continue
            digest_attempted += 1
            written = await self._digest_profile_write(
                str(profile.get("uid") or ""), str(profile.get("private_umo") or ""), lines, now,
            )
            if written:
                digest_written += 1
            else:
                digest_failed += 1
        # 只有"确实生成过"或"根本没有足够的聊天"才落 guard；
        # 生成失败会重试（最多 3 次/天），避免一天都没有阶段记忆。
        if digest_written or (digest_failed == 0 and digest_attempted == 0):
            try:
                self.db.kv_set(guard, today)
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin._maybe_daily_digest", _amo_exc)
        elif digest_failed:
            try:
                tries = to_int(self.db.kv_get("daily_digest_tries:" + today, 0), 0) + 1
                self.db.kv_set("daily_digest_tries:" + today, tries)
                if tries >= 3:
                    self.db.kv_set(guard, today)
                    logger.warning("[amo] 每日总结连续失败 %d 次，今天不再重试" % tries)
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin._maybe_daily_digest", _amo_exc)

    async def _digest_profile_lines(self, profile) -> list:
        """拉这个人昨天各会话的聊天 → 去重 → [我]/[他] 行；不够 6 条返回空。"""
        uid = str(profile.get("uid") or "")
        home_umo = str(profile.get("private_umo") or "")
        if not uid or not home_umo:
            return []
        if str(profile.get("relation") or "") != "相恋":
            return []
        # 他上午可能在飞书、晚上在 QQ，所以按**这个人自己的会话**逐个拉，
        # 每个会话里双方的话都要（机器人行 uid='bot'，按 uid 过滤会把她的话丢光）。
        # 注意不能传 umo=""：那会把这个 bot 下所有人的聊天都拉进来。
        umos = self._ritual_umos(profile)[:3]
        messages: list[Any] = []
        for one_umo in umos:
            try:
                result = await self._data_call(
                    "recall_messages", self._data_bot_id(), query="昨天",
                    umo=one_umo, limit=150,
                )
            except Exception as exc:
                logger.debug("[amo] 每日总结取聊天失败(%s): %s" % (one_umo, exc))
                continue
            messages.extend((result or {}).get("messages") or [])
        if not messages:
            return []
        # 两个平台各拉一遍可能重复（同一会话不会被拉两次，但保守起见按内容去重）
        seen_lines = set()
        deduped = []
        for row in messages:
            key = (to_int(row.get("created_at"), 0), str(row.get("content") or "")[:80])
            if key in seen_lines:
                continue
            seen_lines.add(key)
            deduped.append(row)
        messages = sorted(deduped, key=lambda r: to_int(r.get("created_at"), 0))
        lines = []
        for row in messages:
            text = str(row.get("content") or "").strip().replace("\n", " ")
            if not text or str(row.get("role") or "") == "system":
                continue
            who = "我" if str(row.get("role") or "") == "bot" else "他"
            lines.append("[" + who + "] " + text[:120])
        if len(lines) < 6:
            return []
        return lines

    async def _digest_profile_write(self, uid: str, home_umo: str, lines, now) -> bool:
        """把昨天聊天压成一条长期记忆写进去；True＝写入成功。"""
        system = "你是长期记忆整理器。只输出一段中文总结，不要标题、不要分点、不要时间戳。"
        try:
            from .amo_diary_guard import FACT_RULES, sanitize_diary
        except ImportError:
            if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
                from amo_diary_guard import FACT_RULES, sanitize_diary
        prompt = (
            FACT_RULES + "\n"
            "下面是昨天（" + (now - timedelta(days=1)).strftime("%Y-%m-%d") + "）两人的聊天记录。\n"
            "请把它压成 2-4 句长期记忆：写清聊到的事、约定、他的状态变化（工作/身体/情绪/计划）。\n"
            "每句必须带主体：写用户的用「他……」，写你自己的用「我……」；不要出现无主语的句子。\n"
            "只写事实，不写「今天/昨天」这类相对时间；不要复述原话；不确定的不要编。\n\n"
            + "\n".join(lines[-120:])
        )
        try:
            summary = await self.llm.chat_raw(home_umo, system, prompt)
        except Exception as exc:
            logger.debug("[amo] 每日总结生成失败: %s" % exc)
            summary = ""
        summary = re.sub(r"\s+", " ", str(summary or "")).strip()
        # 摘要也会被当记忆注入：无主语句子会被读成角色自己的经历，统一补「他」
        fixed = []
        for piece in re.split(r"(?<=[。！？!?；;])", summary):
            piece = piece.strip()
            if not piece:
                continue
            if not piece.startswith(("他", "我", "她", "用户")):
                piece = "他" + piece
            fixed.append(piece)
        summary = "".join(fixed)
        if len(summary) < 8:
            # 模型拒答/限流会一直返回空：不计数的话 every-5-min tick 会无限重跑
            return False
        summary = sanitize_diary(summary)
        content = "【" + (now - timedelta(days=1)).strftime("%Y-%m-%d") + "】" + summary[:400]
        written = await self._write_memory_checked(
            uid, content, "daily_digest", 2, scope="private"
        )
        logger.info("[amo] 每日总结%s: %s" % ("已写入" if written else "写入失败", content[:80]))
        return bool(written)


    def _first_private_umo(self):
        try:
            for profile in self.db.list_profiles(limit=50):
                if profile.get("private_umo"):
                    return profile["private_umo"]
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin._first_private_umo", _amo_exc)
        return ""


async def warm_up(plugin):
    """启动后 3 秒预热：名字 / 方言库 / 场景库 / 世界观 / 日程（原 main._warm_up）。"""
    try:
        await asyncio.sleep(3)
        umo = plugin._last_umo or plugin._first_private_umo()
        await plugin._sync_persona_name(umo or "")
        await plugin._ensure_dialect_kb()
        await plugin._ensure_scenic_kb()
        await plugin.state.ensure_worldview(umo)
        await plugin.state.ensure_schedule(umo)
    except Exception as exc:
        logger.debug("[amo] 预热失败: %s" % exc)

