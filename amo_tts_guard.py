# -*- coding: utf-8 -*-
"""TTS 统一守卫（2026-11-21）：合成前剥掉动作/旁白，任何 TTS 路径都不念动作。

为什么包 Provider 而不是改消息链：
- AstrBot 的「随机 TTS」在 result_decorate 阶段把每个 Plain 组件交给 tts_provider.get_audio，
  消息链由内核掌控，插件改不了它的转换顺序；
- 但 get_audio 是 Provider 实例方法，包一层就能保证"送进合成器的只有台词"，
  文字消息本身（含动作）不受影响。
- 与 tts_sanitizer 叠加也安全：两边都是幂等包装，先剥后剥结果一致。
"""
from __future__ import annotations

import logging
from typing import Any

try:
    from .amo_voice import clean_voice_text
except ImportError:  # 直接按文件加载时
    from amo_voice import clean_voice_text

logger = logging.getLogger("amo")

_WRAPPED_FLAG = "_amo_tts_guard"


def _wrap_provider(provider: Any) -> bool:
    """包装单个 TTS Provider 的 get_audio；返回是否新包装。"""
    if getattr(provider, _WRAPPED_FLAG, False):
        return False
    original = getattr(provider, "get_audio", None)
    if not callable(original):
        return False

    async def wrapped(text, *args, **kwargs):
        try:
            cleaned = clean_voice_text(text, limit=0)
        except Exception as exc:  # 清理失败就按原文合成，绝不吞掉语音
            logger.debug("[amo] TTS 清理失败，按原文合成: %r", exc)
            cleaned = str(text or "")
        if not cleaned.strip():
            return ""
        return await original(cleaned, *args, **kwargs)

    try:
        provider.get_audio = wrapped
        setattr(provider, _WRAPPED_FLAG, True)
    except Exception as exc:
        logger.debug("[amo] TTS Provider 包装失败: %r", exc)
        return False
    return True


def wrap_tts_providers(context, log=None) -> int:
    """包装当前所有 TTS Provider；返回新包装数量。"""
    target = log or logger
    getter = getattr(context, "get_all_tts_providers", None)
    if not callable(getter):
        return 0
    try:
        providers = list(getter() or [])
    except Exception as exc:
        target.debug("[amo] 取 TTS Provider 失败: %r", exc)
        return 0
    wrapped = 0
    for provider in providers:
        if _wrap_provider(provider):
            wrapped += 1
    if wrapped:
        target.info("[amo] TTS 动作过滤已接管 %d 个 Provider（语音只念台词）", wrapped)
    return wrapped
