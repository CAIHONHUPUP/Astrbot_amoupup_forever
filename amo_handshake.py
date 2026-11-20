# -*- coding: utf-8 -*-
"""HandshakeMixin（2026-11-17 从 main.py 拆出）：配套插件发现 + 数据插件缺失提醒 + 平台标签。

这三件事都不碰业务逻辑，只回答「配套插件在不在」「这条消息来自哪个平台」，
放在 main.py 里让装配文件越来越长，所以单独成 mixin（方法体逐字搬运，未改文本）。

注意：_plugin_by_names 是跨插件握手的唯一入口（停用的插件必须当它不存在），
改这里等于改「记忆往哪写」，测试见 tests/test_data_plugin_disabled.py。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

try:  # 兼容层：旧插件名 / 旧数据目录唯一来源
    from . import amo_compat as _compat
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        import amo_compat as _compat

try:
    from .amo_observability import note as _note_swallowed
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_observability import note as _note_swallowed

try:
    from .amo_shared import logger
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from amo_shared import logger


class HandshakeMixin:
    """配套插件握手与平台标签（宿主由 HumanCorePlugin 提供）。"""

    # 平台名 -> 显示名。Amo 在 QQ 和飞书上都要认得出同一个人。
    PLATFORM_LABELS = {
        "aiocqhttp": "QQ", "qq": "QQ", "qq_official": "QQ", "onebot": "QQ", "napcat": "QQ",
        "lark": "飞书", "feishu": "飞书",
        "wecom": "企业微信", "wework": "企业微信", "dingtalk": "钉钉",
        "telegram": "Telegram", "discord": "Discord", "slack": "Slack",
        "webchat": "网页", "wechat": "微信", "wx": "微信",
    }

    if TYPE_CHECKING:  # 只给类型检查看：宿主 HumanCorePlugin 提供的成员
        context: Any
        _data_missing_logged: bool
        _data_warn_scheduled: bool
        _running: bool
        _data_synced: bool
        _seen_platforms: set[str]

        def _star_instance(self, meta: Any) -> Any:
            ...

        def _spawn(self, coro: Any) -> Any:
            ...

        def _data_connected(self) -> bool:
            ...

    def _plugin_by_names(self, names):
        """按插件名找实例；不缓存，reload 之后拿到的是新实例。

        停用的插件还挂在内存里（activated=False），这里必须当它不存在：
        否则停用「能记」之后，记忆仍会往它身上写，用户却看不到任何数据。
        """
        for name in names:
            try:
                meta = self.context.get_registered_star(name)
            except Exception as _amo_exc:
                _note_swallowed("amo_handshake.HandshakeMixin._plugin_by_names", _amo_exc)
                meta = None
            if getattr(meta, "activated", True) is False:
                continue
            star = self._star_instance(meta)
            if star is not None:
                return star
        try:
            wanted = [str(item).lower() for item in names]
            for meta in self.context.get_all_stars():
                if getattr(meta, "activated", True) is False:
                    continue
                module = str(getattr(meta, "module_path", "") or "").lower()
                label = str(getattr(meta, "name", "") or "").lower()
                if any(word in module or word in label for word in wanted):
                    star = self._star_instance(meta)
                    if star is not None:
                        return star
        except Exception as _amo_exc:
            _note_swallowed("amo_handshake.HandshakeMixin._plugin_by_names", _amo_exc)
        return None

    def _note_data_missing(self):
        """第一次没找到数据插件：记一次日志，并在 60 秒后再确认一次（只提醒一次）。"""
        if not self._data_missing_logged:
            self._data_missing_logged = True
            logger.info(
                "[amo] 未检测到数据插件 Astrbot_amoupup_data（没装或已停用）："
                "这期间聊天正常，但记忆不会写入，面板记忆页是空的；装好并启用后一分钟内会自动接上"
            )
        if not self._data_warn_scheduled:
            self._data_warn_scheduled = True
            self._spawn(self._warn_data_missing_later())

    async def _warn_data_missing_later(self, delay=60.0):
        try:
            await asyncio.sleep(max(1.0, float(delay)))
        except asyncio.CancelledError:
            return
        if not self._running or self._data_synced or self._data_connected():
            return
        logger.warning(
            "[amo] 数据插件未连接：去「插件」页看 Astrbot_amoupup_data 是不是被停用/加载报错；"
            "装好并启用后一分钟内会自动接上，不用重启"
        )

    def _platform_of(self, event=None):
        """当前消息来自哪个平台（aiocqhttp / lark …），失败就退回 umo 前缀。"""
        name = ""
        try:
            name = str(event.get_platform_name() or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_handshake.HandshakeMixin._platform_of", _amo_exc)
            name = ""
        if not name:
            try:
                name = str(event.unified_msg_origin or "").split(":", 1)[0]
            except Exception as _amo_exc:
                _note_swallowed("amo_handshake.HandshakeMixin._platform_of", _amo_exc)
                name = ""
        name = name.strip().lower()
        if name:
            seen = getattr(self, "_seen_platforms", None)
            if seen is None:
                seen = set()
                self._seen_platforms = seen
            seen.add(name)
        return name

    def _platform_label(self, platform=""):
        key = str(platform or "").strip().lower()
        if not key:
            return "这里"
        return self.PLATFORM_LABELS.get(key, str(platform).strip())

    @staticmethod
    def _event_message_id(event):
        """取平台消息 id（QQ 的 message_id / 飞书的 event id），用于聊天记录去重。"""
        for holder in (getattr(event, "message_obj", None), event):
            for attr in ("message_id", "msg_id", "id"):
                try:
                    value = getattr(holder, attr, None)
                except Exception as _amo_exc:
                    _note_swallowed("amo_handshake.HandshakeMixin._event_message_id", _amo_exc)
                    value = None
                if value not in (None, "", 0):
                    return str(value)[:128]
        return ""
