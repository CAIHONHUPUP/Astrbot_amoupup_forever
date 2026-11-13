# -*- coding: utf-8 -*-
"""amo ↔「我能永远守护你」(Astrbot_amoupup_watch) 握手桥。

amo 只做一件事：启动时发现 watch → 调一次 `describe()` → 日志留痕。
watch 没装 / 停用 / 加载失败 / 版本不匹配 → 一律当没装：不抛异常、不阻塞、不重试风暴。

社会逻辑、数据、定时器全在 watch 自己的插件里；这里不 import 它的任何模块。
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from astrbot.api import logger
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from astrbot.api import logger
    except Exception:  # pragma: no cover - 纯单测环境
        import logging

        logger = logging.getLogger("amo_social_bridge")

try:
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    try:
        from amo_observability import note as _note_swallowed
    except ImportError:  # pragma: no cover

        def _note_swallowed(scope, exc=None):
            return None


WATCH_PLUGIN_NAME = "Astrbot_amoupup_watch"
WATCH_API_MAJOR = "1"
CACHE_SECONDS = 60.0
HANDSHAKE_TIMEOUT = 0.2
RETRY_TIMES = 8
RETRY_INTERVAL = 2.0


def get_watch(plugin, force: bool = False):
    """拿 watch 插件实例；没装/停用/坏的返回 None。结果缓存 60 秒。"""
    now = time.monotonic()
    if not force and now - getattr(plugin, "_watch_checked", 0.0) < CACHE_SECONDS:
        return getattr(plugin, "_watch_inst", None)
    plugin._watch_checked = now
    plugin._watch_inst = None
    try:
        context = getattr(plugin, "context", None)
        getter = getattr(context, "get_registered_star", None) if context else None
        if not callable(getter):
            return None
        meta = getter(WATCH_PLUGIN_NAME)
        if meta is None:
            return None
        # 停用 ≠ 卸载：AstrBot 停用只把 activated 置 False，实例还挂在内存里
        if getattr(meta, "activated", True) is False:
            return None
        instance = getattr(meta, "star_cls", None)
        if instance is not None and callable(getattr(instance, "describe", None)):
            plugin._watch_inst = instance
    except Exception as exc:
        _note_swallowed("amo_social_bridge.get_watch", exc)
    return plugin._watch_inst


async def handshake(plugin):
    """启动后握一次手；watch 比 amo 晚加载就等一会儿（最多约 16 秒）。"""
    instance = None
    for attempt in range(RETRY_TIMES):
        instance = get_watch(plugin, force=True)
        if instance is not None:
            break
        await asyncio.sleep(RETRY_INTERVAL)
    if instance is None:
        logger.info("[amo] watch 未安装/未启用，社会模拟不接入（聊天不受影响）")
        return None
    try:
        info = await asyncio.wait_for(asyncio.to_thread(instance.describe), HANDSHAKE_TIMEOUT)
    except Exception as exc:
        logger.warning("[amo] watch 握手失败: %r", exc)
        return None
    if not isinstance(info, dict):
        logger.warning("[amo] watch 握手返回格式不对: %r", type(info))
        return None
    api = str(info.get("api_version", "") or "")
    if api.split(".")[0] != WATCH_API_MAJOR:
        logger.warning("[amo] watch api 版本不匹配: %s（amo 认 %s.x）", api or "?", WATCH_API_MAJOR)
        return None
    logger.info("[amo] watch handshake ok version=%s api=%s", info.get("version", "?"), api)
    return info
