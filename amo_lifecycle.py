# -*- coding: utf-8 -*-
"""生命周期（2026-11-17 从 main.py 拆出）：initialize / terminate / stop。

启动只做两件事：LLM 工具注册（买家电/网购/发照片/日程）+ kv 只读巡检。
停止按「任务 → 分段引擎 → state」顺序收，不关数据库
（AstrBot 重载插件时旧实例的 Web API 还可能被调用，关连接会报
Cannot operate on a closed database）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from .amo_shared import logger
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_shared import logger

try:
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_observability import note as _note_swallowed


class LifecycleMixin:
    """启动/停止（宿主由 HumanCorePlugin 提供）。"""

    if TYPE_CHECKING:  # 只给类型检查看：宿主提供的成员
        context: Any
        _running: bool
        jobs: Any
        segment_engine: Any
        state: Any
        _tasks: set[Any]

        async def _ensure_ready(self) -> Any: ...
    async def initialize(self):
        await self._ensure_ready()
        try:
            manager = getattr(getattr(self.context, "provider_manager", None), "llm_tools", None)
            from .draw_engine import register_send_photo
            ok3 = register_send_photo(self, manager)
            logger.info("[amo] LLM tool send_photo: %s", "registered" if ok3 else "skipped")
            from .amo_schedule_tools import register_schedule_tools
            ok4 = register_schedule_tools(self, manager)
            logger.info("[amo] LLM tools schedule: %s", "registered" if ok4 else "skipped")
        except Exception as exc:
            logger.warning("[amo] 注册 LLM 工具失败: %s", exc)
        try:  # Phase 5：启动时只读巡检 kv 一次（INFO 摘要，不删任何键）
            from .amo_state_store import attach as _attach_state_store
            from .amo_state_store import should_log_health as _should_log_health
            state_store = _attach_state_store(self)
            if _should_log_health():
                logger.info("[amo] %s", state_store.health_line())
        except Exception as exc:
            logger.debug("[amo] kv_health 摘要失败: %s" % exc)





















    async def terminate(self):
        self._running = False
        jobs = getattr(self, "jobs", None)
        if jobs is not None:
            try:
                await jobs.stop()
            except Exception as _amo_exc:
                _note_swallowed("amo_lifecycle.LifecycleMixin.terminate.jobs", _amo_exc)
        if self.segment_engine is not None:
            for task in list(getattr(self.segment_engine, "_tasks", set())):
                try:
                    task.cancel()
                except Exception as _amo_exc:
                    _note_swallowed("amo_lifecycle.LifecycleMixin.terminate", _amo_exc)
        for task in list(self._tasks):
            try:
                task.cancel()
            except Exception as _amo_exc:
                _note_swallowed("amo_lifecycle.LifecycleMixin.terminate", _amo_exc)
        if self.state is not None:
            try:
                self.state.save()
            except Exception as _amo_exc:
                _note_swallowed("amo_lifecycle.LifecycleMixin.terminate", _amo_exc)
        # 不在这里关闭数据库：AstrBot 重载插件时旧实例注册的 Web API
        # 可能仍被调用，关闭连接会导致 "Cannot operate on a closed database"。
        # 连接由进程退出时回收；AmoDB.conn 也会在需要时自动重连。

    async def stop(self):
        await self.terminate()
