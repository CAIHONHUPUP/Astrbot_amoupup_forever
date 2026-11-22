# -*- coding: utf-8 -*-
"""TTS 统一守卫（2026-11-21 初版；2026-11-22 task-24 接管修复 + 语音落库）。

为什么包 Provider 而不是改消息链：
- AstrBot 的「随机 TTS」在 result_decorate 阶段把每个 Plain 组件交给 tts_provider.get_audio，
  消息链由内核掌控，插件改不了它的转换顺序；但 get_audio 是 Provider 实例方法，包一层就能
  保证"送进合成器的只有台词"，文字消息本身（含动作）不受影响。
- 与 tts_sanitizer 叠加也安全：两边都是幂等包装，先剥后剥结果一致。

2026-11-22 task-24 修的两个真问题：
1. **0 次接管**：main.py 的 on_astrbot_loaded 以前把 `filter` 解析成 Python 内建函数，
   hook 恒为 None，wrap_tts_providers 从未被调用（生产日志 0 次「接管」）。现在 main.py
   取 amo_shared 导出的真 filter；本模块保证"被调用就一定有日志"，0 Provider 也打 INFO，
   监控上能区分「没调用」和「调用了但 0 provider」。
2. **语音记录缺失**：核心随机 TTS 的 Record 由内核在插件 on_decorating_result 之后拼接，
   插件拿不到最终 chain，于是 amo_data.db 只有一条纯文本、没有 [我发的语音] 记录。
   现在在 get_audio 里记录**实际送进合成器的文本**（剥完动作/旁白后的台词）+ 原文里被
   剥掉的动作 + provider，并写一条 [我发的语音] 媒体标记（和按需语音同一格式，重复写由
   _remember_bot_message 的 180 秒去重兜住）。

失败统一出口：get_audio 失败只回 ""，让核心把该段按文字发出；不抛异常、不把
provider 的技术原文送进聊天，也不额外补一句"服务器/接口"式的话。

记录位置：
- kv "voice_ledger"（滚动 100 条，结构化：status/provider/umo/tts_text/actions/at/day）；
- messages/长期记忆里的 "[我发的语音] <实际台词>（动作：<被剥掉的动作>）" 标记；
- 无 plugin/无 db 时退回进程内 _RECORDS，测试和早期加载也能查（recent_records()）。
"""
from __future__ import annotations

import logging
import time
from typing import Any

try:
    from .amo_voice import clean_voice_text, split_actions
except ImportError:  # 直接按文件加载时
    from amo_voice import clean_voice_text, split_actions

try:  # 统一时钟：源码守护禁止生产代码裸调 time.time()
    from . import clock as _clock
except ImportError:  # 直接按文件加载时
    import clock as _clock

logger = logging.getLogger("amo")

_WRAPPED_FLAG = "_amo_tts_guard"
#: 结构化语音记录（实际 TTS 文本 + 动作 + provider）；滚动保存，审计/回放用。
VOICE_LEDGER_KEY = "voice_ledger"
VOICE_LEDGER_LIMIT = 100
#: 无 plugin/db 时的进程内兜底（测试、早期加载）。
_RECORDS: list = []
#: 能找插件实例的注册名（AstrBot 按插件目录名注册）。
_PLUGIN_NAMES = ("Astrbot_amoupup_forever", "Astr_Amo_forever")


# ---------------------------------------------------------------- 基础工具

def _now_ts() -> float:
    try:
        return float(_clock.now_ts())
    except Exception:
        return 0.0


def _day_of(stamp: float) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(stamp or 0)))
    except Exception:
        return ""


def _provider_name(provider: Any) -> str:
    """Provider 的可读名字：优先配置里的 type/id，退化成类名。"""
    try:
        cfg = getattr(provider, "provider_config", None) or {}
        if isinstance(cfg, dict):
            for key in ("type", "id", "name"):
                value = str(cfg.get(key) or "").strip()
                if value:
                    return value
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass
    try:
        return type(provider).__name__
    except Exception:
        return "tts"


def _resolve_plugin(context: Any) -> Any:
    """从 context 找陪伴插件实例（写 voice_ledger / [我发的语音] 标记用）。"""
    getter = getattr(context, "get_registered_star", None)
    if not callable(getter):
        return None
    for name in _PLUGIN_NAMES:
        try:
            meta = getter(name)
        except Exception:
            meta = None
        if meta is None or getattr(meta, "activated", True) is False:
            continue
        star = getattr(meta, "star_cls", None) or getattr(meta, "star", None)
        if star is not None:
            return star
    return None


def _owner_umo(plugin: Any) -> str:
    """她能主动找到他的会话（私聊优先）：媒体标记写到这个 umo。"""
    getter = getattr(plugin, "_owner_umos", None)
    if not callable(getter):
        return ""
    try:
        for umo in list(getter() or []):
            value = str(umo or "").strip()
            if value:
                return value
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass
    return ""


def _read_ledger(plugin: Any) -> list:
    db = getattr(plugin, "db", None) if plugin is not None else None
    getter = getattr(db, "kv_get", None)
    if not callable(getter):
        return list(_RECORDS)
    try:
        raw = getter(VOICE_LEDGER_KEY, []) or []
    except Exception:
        return list(_RECORDS)
    if isinstance(raw, str):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _write_ledger(plugin: Any, data: list) -> bool:
    db = getattr(plugin, "db", None) if plugin is not None else None
    setter = getattr(db, "kv_set", None)
    if not callable(setter):
        return False
    try:
        setter(VOICE_LEDGER_KEY, list(data[-VOICE_LEDGER_LIMIT:]))
        return True
    except Exception:
        return False


def reset_records() -> None:
    """清空进程内兜底记录（测试用；不动 kv 账本）。"""
    _RECORDS.clear()


def recent_records(plugin: Any = None, limit: int = 20) -> list:
    """最近的语音记录（按发生顺序，旧的在前）；给测试/审计/面板用。"""
    rows = _read_ledger(plugin)
    try:
        count = max(0, int(limit))
    except (TypeError, ValueError):
        count = 20
    return rows[-count:] if count else list(rows)


def record_voice(plugin: Any, *, provider: Any = None, umo: str = "",
                 raw_text: str = "", spoken: str = "", actions: str = "",
                 status: str = "sent", reason: str = "") -> dict:
    """记一条语音事实：实际 TTS 文本、被剥掉的动作、provider、态。

    status ∈ sent / failed / skipped（skipped=整段都是动作/旁白，没合成语音）。
    """
    stamp = _now_ts()
    entry = {
        "kind": "voice",
        "source": "tts_guard",
        "status": str(status or "sent"),
        "reason": str(reason or "")[:80],
        "raw": str(raw_text or "")[:200],
        "provider": _provider_name(provider) if provider is not None else "",
        "umo": str(umo or ""),
        "tts_text": str(spoken or ""),
        "actions": str(actions or "")[:200],
        "has_actions": bool(str(actions or "").strip()),
        "at": stamp,
        "day": _day_of(stamp),
    }
    _RECORDS.append(dict(entry))
    if len(_RECORDS) > VOICE_LEDGER_LIMIT:
        del _RECORDS[: len(_RECORDS) - VOICE_LEDGER_LIMIT]
    if plugin is not None:
        rows = _read_ledger(plugin)
        rows.append(dict(entry))
        _write_ledger(plugin, rows)
    return entry


def _voice_marker(spoken: str, actions: str) -> str:
    """[我发的语音] 标记：记实际台词；有被剥掉的动作时一并留下（动作不进语音）。"""
    marker = "[我发的语音] " + str(spoken or "").strip()
    action_text = str(actions or "").strip()
    if action_text:
        marker += "（动作：%s）" % action_text
    return marker


# ---------------------------------------------------------------- 包装

def _clean(raw: str) -> str:
    """真正送进合成器的台词：去 Markdown、剥动作/旁白（幂等）。"""
    try:
        return clean_voice_text(raw, limit=0)
    except Exception as exc:
        logger.debug("[amo] TTS 清理失败，按原文合成: %r", exc)
        return str(raw or "")


async def _record_play(plugin: Any, provider: Any, raw: str, spoken: str) -> dict:
    """记录一次成功合成的语音：结构化账本 + [我发的语音] 媒体标记。"""
    try:
        _spoken_text, actions = split_actions(raw)
    except Exception:
        actions = ""
    umo = _owner_umo(plugin)
    entry = record_voice(plugin, provider=provider, umo=umo, raw_text=raw,
                         spoken=spoken, actions=actions, status="sent")
    remember = getattr(plugin, "_remember_bot_media", None) if plugin is not None else None
    if callable(remember) and umo:
        marker = _voice_marker(spoken, actions)
        # 生产里走受管后台任务：媒体标记落库不能拖慢 TTS 合成/播放。
        spawn = getattr(plugin, "_spawn", None)
        if callable(spawn):
            pending = remember(umo, marker)
            try:
                spawn(pending)
                return entry
            except Exception as exc:
                logger.debug("[amo] 语音媒体标记后台写入失败，改同步写: %r", exc)
                try:
                    pending.close()
                except Exception:  # cleanup: 兜底/降级，保持原行为
                    pass
        try:
            await remember(umo, marker)
        except Exception as exc:
            # 记录失败不能影响语音本身，也不能让技术原文进聊天
            logger.debug("[amo] 语音媒体标记写入失败: %r", exc)
    return entry


def _wrap_provider(provider: Any, plugin: Any = None) -> bool:
    """包装单个 TTS Provider 的 get_audio；返回是否新包装。"""
    if getattr(provider, _WRAPPED_FLAG, False):
        return False
    original = getattr(provider, "get_audio", None)
    if not callable(original):
        return False

    async def wrapped(text, *args, **kwargs):
        raw = str(text or "")
        spoken = _clean(raw)
        if not spoken.strip():
            # 整段都是动作/旁白：不合成，让核心把原文当文字发出去（动作永不进语音）。
            try:
                _spoken_text, actions = split_actions(raw)
            except Exception:
                actions = ""
            record_voice(plugin, provider=provider, umo=_owner_umo(plugin),
                         raw_text=raw, actions=actions, status="skipped",
                         reason="action_only")
            logger.info("[amo] 本段只有动作/旁白，跳过 TTS、按文字发送: %s", raw[:40])
            return ""
        try:
            audio = await original(spoken, *args, **kwargs)
        except Exception as exc:
            # 统一失败出口：不抛给内核、不补技术话术；本轮文字照常发。
            record_voice(plugin, provider=provider, umo=_owner_umo(plugin),
                         raw_text=raw, spoken=spoken, status="failed",
                         reason="tts_error")
            logger.warning("[amo] 语音合成失败，本轮按文字发出（不念动作、不报技术错误）: %r",
                           exc)
            return ""
        if not audio:
            record_voice(plugin, provider=provider, umo=_owner_umo(plugin),
                         raw_text=raw, spoken=spoken, status="failed",
                         reason="no_audio")
            logger.warning("[amo] 语音合成没有返回音频，本轮按文字发出: %s", spoken[:40])
            return ""
        try:
            await _record_play(plugin, provider, raw, spoken)
        except Exception as exc:  # 记录失败不能影响语音本身
            logger.debug("[amo] 语音记录写入失败: %r", exc)
        return audio

    try:
        provider.get_audio = wrapped
        setattr(provider, _WRAPPED_FLAG, True)
    except Exception as exc:
        logger.debug("[amo] TTS Provider 包装失败: %r", exc)
        return False
    return True


def wrap_tts_providers(context, log=None, plugin=None) -> int:
    """包装当前所有 TTS Provider；返回新包装数量。

    重复调用安全（已包装的 provider 直接跳过）。0 Provider 也会打 INFO，便于监控区分
    「hook 没跑」和「跑了但还没有 provider」——2026-11-21 的 0 次接管就是因为 hook 没跑，
    只靠 debug 日志根本发现不了。
    """
    target = log or logger
    getter = getattr(context, "get_all_tts_providers", None)
    if not callable(getter):
        target.debug("[amo] 上下文没有 get_all_tts_providers，跳过 TTS 动作过滤")
        return 0
    try:
        providers = list(getter() or [])
    except Exception as exc:
        target.debug("[amo] 取 TTS Provider 失败: %r", exc)
        return 0
    owner = plugin or _resolve_plugin(context)
    wrapped = 0
    for provider in providers:
        if _wrap_provider(provider, plugin=owner):
            wrapped += 1
    if wrapped:
        target.info(
            "[amo] TTS 动作过滤已接管 %d 个 Provider（语音只念台词；实际 TTS 文本+动作写 voice_ledger）",
            wrapped,
        )
    else:
        target.info("[amo] TTS 动作过滤：当前没有可接管的 TTS Provider（0 个）")
    return wrapped
