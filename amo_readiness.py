# -*- coding: utf-8 -*-
"""就绪 / 握手 / 日程事件 mixin（2026-11-17 从 main.py 搬出）。

搬出的是 `_ensure_ready` / `_log_omni_handshake` / `_watch_handshake` /
`_on_schedule_changed` 四个方法，方法体逐字未改（只把模块级 `_watch_handshake`
函数改名为 `_watch_handshake_probe`，避免和方法名混淆）。
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING, cast

import asyncio

try:
    from .amo_bootstrap import (
        HumanState,
        HungerEngine,
        _note_swallowed,
        logger,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_bootstrap import (
            HumanState,
            HungerEngine,
            _note_swallowed,
            logger,
        )

try:
    from . import amo_compat as _compat
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        import amo_compat as _compat


class ReadinessMixin:
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _background_loop: Any
        _init_lock: Any
        _merge_alias_profiles: Any
        _omnidraw_self_use_hint: Any
        _ready: bool
        _spawn: Any
        _sync_with_data_plugin: Any
        _warm_up: Any
        config_health: Any
        db: Any
        run_config_migrations: Any
        wallet: Any
    async def _ensure_ready(self):
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            try:
                await asyncio.to_thread(self.db.init)
            except Exception as exc:
                logger.error("[amo] 数据库初始化失败: %s" % exc)
            try:
                await asyncio.to_thread(self.wallet.ensure_schema)
            except Exception as exc:
                logger.error("[amo] 钱包初始化失败: %s" % exc)
            self.run_config_migrations()
            self.config_health()
            await asyncio.to_thread(self._merge_alias_profiles)
            self.state = cast(Any, HumanState)(self)
            try:
                # 旧库里提前铺好的未来日程 → 待办表（只搬一次，之后未来事件直接进待办）
                self.state.migrate_future_todos()
            except Exception as _amo_exc:
                _note_swallowed("main.HumanCorePlugin._ensure_ready.todo_migrate", _amo_exc)
            self.hunger = HungerEngine(self)
            try:
                await self._sync_with_data_plugin()
            except Exception as exc:
                logger.debug("[amo] 与数据插件同步失败: %s" % exc)
            self._ready = True
            self._omnidraw_self_use_hint()
            try:
                retry = getattr(self, "_retry_web_api_routes", None)
                if callable(retry):
                    retry()
            except Exception as _amo_exc:
                _note_swallowed("main.HumanCorePlugin._ensure_ready.web_retry", _amo_exc)
            self._spawn(self._log_omni_handshake())
            self._loop_task = self._spawn(self._background_loop())
            self._spawn(self._warm_up())


    async def _log_omni_handshake(self):
        """启动时主动和「我能永远定格你」握手一次；没装也不报错，生图前还会再找。"""
        try:
            from .amo_image_common import find_omni
        except Exception:
            try:
                from amo_image_common import find_omni
            except Exception:
                return
        try:
            engine = getattr(self, "photo", None) or getattr(self, "draw", None)
            if engine is None:
                return
            omni = None
            for attempt in range(8):
                omni = find_omni(self, engine, "main._log_omni_handshake", force=True)
                if omni is not None:
                    break
                await asyncio.sleep(2)
            if omni is None:
                logger.info(
                    "[amo] 没装「我能永远定格你」(" + _compat.IMAGE_PLUGIN_NAME
                    + " / legacy fallback: " + _compat.IMAGE_PLUGIN_LEGACY_NAME
                    + ")：照片/画图暂时用不了,"
                    "用户要图时会回一句说明；装好并启用后会自动接上，不用重启"
                )
                return
            cfg = getattr(omni, "plugin_config", None)
            nodes = list(getattr(cfg, "providers", []) or [])
            model = str(getattr(nodes[0], "model", "") or "") if nodes else ""
            logger.info("[amo] 已握手「我能永远定格你」，可直接生图（生图节点 %d 个%s）" % (
                len(nodes), ("，模型 " + model) if model else ""))
        except Exception as exc:
            logger.debug("[amo] 生图插件握手检查失败: %s" % exc)

    def _on_schedule_changed(self, event):
        """日程事件订阅者：日记失效/取消 + 穿搭失效 + 生图刷新日志。"""
        day = str(getattr(event, "day", "") or "")
        version = int(getattr(event, "version", 0) or 0)
        start_hour = int(getattr(event, "start_hour", -1) or -1)
        cancelled: list[Any] = []
        state = getattr(self, "state", None)
        if state is not None and start_hour >= 0:
            try:
                state.db.mark_diaries_stale(day, start_hour, version)
            except Exception as exc:
                logger.warning("[EVENT] diary_stale failed: %s", exc)
            cancel = getattr(self, "_cancel_diary_tasks", None)
            if callable(cancel):
                try:
                    cancelled = cancel(day, start_hour) or []
                except Exception as exc:
                    logger.warning("[EVENT] diary_cancel failed: %s", exc)
        wardrobe = getattr(self, "wardrobe", None)
        if wardrobe is not None:
            invalidate = getattr(wardrobe, "invalidate", None)
            if callable(invalidate):
                try:
                    invalidate(day, reason="schedule_changed")
                except Exception as exc:
                    logger.warning("[EVENT] wardrobe_invalidate failed: %s", exc)
        photo = getattr(self, "photo", None)
        hook = getattr(photo, "on_schedule_changed", None) if photo is not None else None
        if callable(hook):
            try:
                hook(day, version, "schedule_changed")
            except Exception as exc:
                logger.warning("[EVENT] photo_refresh failed: %s", exc)
        logger.debug("[EVENT] schedule_changed day=%s version=%s start_hour=%s cancelled=%s",
                     day, version, start_hour, len(cancelled))
        return cancelled
