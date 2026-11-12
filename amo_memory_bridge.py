# -*- coding: utf-8 -*-
"""能力层（MemoryHub）与「想记」写入：amo 自己的记忆入口只走这一条。

不存任何本地记忆，也不再有"退回外挂记忆插件"的分支——记忆只有两个去处：
能力层（MemoryHub，内部就是 Astr_Amo_data）和它的 mem 门面。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

import re

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        MemoryHub,
        logger,
        to_int,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            MemoryHub,
            logger,
            to_int,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            MemoryHub,
            logger,
            to_int,
        )

if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class BridgeMixin(_MixinBase):
    def _memory_hub(self):
        hub = getattr(self, "_mem_hub", None)
        if hub is not None:
            return hub
        if MemoryHub is None:
            return None
        try:
            hub = MemoryHub(
                self._memory_runtime(),
                buffer_size=max(8, self.cfg_int("ctx_window", 48)),
            )
        except Exception as exc:
            logger.warning("[amo] 记忆能力层初始化失败: %s" % exc)
            return None
        self._mem_hub = hub
        return hub

    def _memory_runtime(self):
        star = self

        class _Runtime:
            logger = logger

            @staticmethod
            def cfg(key, default=None):
                try:
                    data = star.config
                    if isinstance(data, dict):
                        return data.get(key, default)
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory_bridge.BridgeMixin._memory_runtime._Runtime.cfg", _amo_exc)
                return default

            @staticmethod
            def bot_id():
                return star._data_bot_id()

            @staticmethod
            def get_plugin(names):
                return star._plugin_by_names(names)

            @staticmethod
            def normalize_memories(rows):
                return star._normalize_memories(rows)

        return _Runtime()

    async def _write_memory_checked(self, uid, content, kind="fact", tier=1,
                                    scope="private", group_id=""):
        """写一条记忆并返回是否真的成功；失败打 warning，调用方据此决定要不要标记。"""
        hub = self._memory_hub()
        level = to_int(tier, 1)
        importance = 0.75 if level >= 2 else (0.55 if level == 1 else 0.35)
        if hub is not None and hub.writable():
            try:
                result = await hub.remember(
                    str(uid), content, str(kind or "fact"), importance,
                    scope=str(scope or "private"), group_id=str(group_id or ""),
                )
            except Exception as exc:
                logger.warning("[amo] 记忆写入异常: %s" % exc)
                result = {"ok": False}
            if isinstance(result, dict) and result.get("ok"):
                return True
            logger.warning(
                "[amo] 记忆没写进去（%s）: %s" % ((result or {}).get("reason", "?"), str(content)[:40])
            )
            return False
        if self._data_mem() is not None:
            try:
                result = await self._data_memory_write(
                    uid, content, kind, tier, scope, group_id
                )
            except Exception as _amo_exc:
                _note_swallowed("amo_memory_bridge.BridgeMixin._write_memory_checked", _amo_exc)
                result = False
            if result:
                return True
        return False

    def _external_memory_write(self, uid, content, kind="fact", tier=1,
                               scope="private", group_id=""):
        """把一条记忆交给「想记」；能力层不可用时直接写它的 mem 门面。

        陪伴自己不存记忆，只负责把要记的东西丢过去；写不进去就返回 False，
        调用方（命令/面板）会如实告诉用户。
        """
        content = re.sub(r"\s+", " ", str(content or "")).strip()
        if not content:
            return False
        hub = self._memory_hub()
        if hub is not None and hub.writable():
            level = to_int(tier, 1)
            importance = 0.75 if level >= 2 else (0.55 if level == 1 else 0.35)
            try:
                self._spawn(hub.remember(
                    uid, content, str(kind or "fact"), importance,
                    scope=str(scope or "private"), group_id=str(group_id or ""),
                ))
                logger.debug("[amo] 记忆已交给能力层: %s" % content[:40])
                return True
            except Exception as exc:
                logger.debug("[amo] 能力层写入失败，改走 mem 门面: %s" % exc)
        mem = self._data_mem()
        if mem is not None and callable(getattr(mem, "remember", None)):
            coro = None
            try:
                coro = self._data_memory_write(uid, content, kind, tier, scope, group_id)
                if self._spawn(coro) is not None:
                    logger.debug("[amo] 记忆已交给记忆终端: %s" % content[:40])
                    return True
            except Exception as exc:
                if coro is not None:
                    coro.close()
                logger.debug("[amo] 记忆终端写入失败: %s" % exc)
        return False
