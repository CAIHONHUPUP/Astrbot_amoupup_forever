# -*- coding: utf-8 -*-
"""照片引擎与画图引擎共用的那一层。

``photo_engine``（按日程主动分享）和 ``draw_engine``（他说「画一张」才画）是两条
不同的业务链路，但有五件事以前各写了一遍，逐字相同或只差一行：

- 读插件配置（cfg_bool / cfg_int / cfg_float / cfg_str，带默认值与吞异常留痕）；
- 找万象画卷实例（按名字问 context，失败缓存 60 秒，避免每 30 秒的重试风暴）；
- 起后台任务（优先走插件统一的 ``_spawn``，退回 ``asyncio.create_task``）；
- 把生图结果（data_url / file_path / url）转成消息组件；
- 把日程里那些不适合当拍照地点的词（「他桌面上」）洗成「家里」。

两份实现已经漂移过（``_clean_place`` 一边截断一边不截），所以收到这里。
业务判断（概率、限额、缓存键、提示词）仍然留在各自引擎里，这里只放无状态的公共部分。

约定：
- 不吞异常：读不到就返回默认值并记一次 ``note()``，让 ``/status`` 能看见；
- 不 import 两个引擎（避免环形依赖），引擎把自己（holder）传进来。
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

try:
    from astrbot.api.message_components import Image
except ImportError:  # 老版本/兼容路径
    try:
        from astrbot.api.event.components import Image
    except Exception:  # pragma: no cover
        Image = None

try:
    from astrbot.core.message.message_event_result import MessageChain
except Exception:  # pragma: no cover - 单测会注入桩
    MessageChain = None

#: 万象画卷的插件名（context.get_registered_star 按它查）
OMNI_PLUGIN_NAME = "astrbot_plugin_omnidraw"
#: 找到/找不到都缓存这么久，别每轮都去问一遍插件注册表
OMNI_CACHE_SECONDS = 60
#: 地点词最长保留多少字（日程里的 place 有时是整句话）
PLACE_MAX_CHARS = 16

_TRUE_WORDS = ("1", "true", "yes", "on", "是")


def cfg(plugin, key, default, kind=str, scope=""):
    """按类型读插件配置。

    ``kind`` 决定调插件的哪个读取器（cfg_bool / cfg_int / cfg_float / cfg_str），
    读取器缺失或抛异常时返回 ``default``，并记一次吞异常（scope 用来聚合统计）。
    """
    reader = {bool: "cfg_bool", int: "cfg_int", float: "cfg_float", str: "cfg_str"}[kind]
    getter = getattr(plugin, reader, None)
    if not callable(getter):
        return default
    try:
        value = getter(key, default)
    except Exception as exc:
        _note_swallowed(scope or ("amo_image_common.cfg:%s" % key), exc)
        return default
    if kind is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in _TRUE_WORDS
        return bool(value)
    try:
        return kind(value)
    except (TypeError, ValueError):
        return default


def spawn(plugin, coro, scope="amo_image_common.spawn"):
    """把协程丢到后台；插件没有 ``_spawn`` 时退回 ``asyncio.create_task``。

    返回 None 表示这次没起来（调用方据此恢复自己的 busy/inflight 标记）。
    插件有 ``_spawn`` 就直接采信它的结果——它返回 None 时协程可能已经被关掉了，
    再交给 ``create_task`` 会碰到「协程已关闭」的 RuntimeError。
    """
    starter = getattr(plugin, "_spawn", None)
    if callable(starter):
        try:
            return starter(coro)
        except Exception as exc:
            _note_swallowed(scope, exc)
    try:
        return asyncio.create_task(coro)
    except RuntimeError:
        try:
            coro.close()
        except Exception:
            pass
        return None


def find_omni(plugin, holder, scope="amo_image_common.find_omni", ttl=OMNI_CACHE_SECONDS,
              force=False):
    """拿万象画卷插件实例（没装/没启用返回 None），结果缓存在 holder 上。

    holder 需要 ``_omni`` / ``_omni_checked`` 两个属性——就是两个引擎自己的字段，
    这样缓存跟着引擎走，热重载后不会留着一个指向旧实例的引用。
    """
    now = time.monotonic()
    # 停用 ≠ 卸载：AstrBot 停用插件只把 activated 置 False，实例还挂在内存里。
    # 不看这个标记，停用万象画卷后照样能出图（用户以为已经关了）。
    try:
        context = getattr(plugin, "context", None)
        getter = getattr(context, "get_registered_star", None) if context else None
        meta = getter(OMNI_PLUGIN_NAME) if getter else None
    except Exception as exc:
        _note_swallowed(scope, exc)
        meta = None
        getter = None
    if meta is not None and getattr(meta, "activated", True) is False:
        holder._omni = None
        holder._omni_checked = now
        return None
    checked = getattr(holder, "_omni_checked", 0.0)
    cached = getattr(holder, "_omni", None)
    if not force and now - checked < ttl:
        # 命中缓存；没找到时只缓存 10 秒，后装/后启用万象画卷能很快被发现
        if cached is not None or now - checked < 10:
            return cached
    holder._omni_checked = now
    holder._omni = None
    if getter is None:
        return None
    try:
        instance = getattr(meta, "star_cls", None) if meta else None
        if instance is not None and hasattr(instance, "generate_images_for_plugin"):
            holder._omni = instance
    except Exception as exc:
        _note_swallowed(scope, exc)
    return holder._omni


def image_component(item):
    """生图结果 → ``Image`` 组件；三种字段按 data_url / file_path / url 依次尝试。"""
    if Image is None or not isinstance(item, dict):
        return None
    data_url = str(item.get("data_url") or "")
    if data_url:
        payload = data_url.split(",", 1)[1] if "," in data_url else data_url
        try:
            return Image.fromBase64(payload)
        except Exception as exc:
            _note_swallowed("amo_image_common.image_component.base64", exc)
    path = str(item.get("file_path") or "")
    if path:
        try:
            return Image.fromFileSystem(path)
        except Exception as exc:
            _note_swallowed("amo_image_common.image_component.file", exc)
    url = str(item.get("url") or item.get("image_url") or "")
    if url.startswith("http"):
        try:
            return Image.fromURL(url)
        except Exception as exc:
            _note_swallowed("amo_image_common.image_component.url", exc)
    return None


def clean_place(place, limit=PLACE_MAX_CHARS):
    """日程里的 place 有时是「他桌面上」这种动作描述，不适合当拍照地点。"""
    place = str(place or "").strip()
    if not place:
        return ""
    if "桌" in place or place.startswith("他") or place.startswith("她"):
        return "家里"
    return place[:limit]


def split_keywords(text):
    """中英文逗号都能分隔的关键词串 → 列表（去空白、丢空项）。"""
    return [item.strip() for item in str(text or "").replace("，", ",").split(",") if item.strip()]


def persona_config_payload(omni, refs):
    """把参考图写进万象画卷「自己那一份」raw_config，返回新 dict（不改原对象）。

    只动它的公开配置结构（``persona_config.persona_ref_images``），交给
    ``apply_persona_config()`` 去校验 + 落盘；这里不自己拼 JSON 写文件。
    """
    raw = getattr(omni, "raw_config", None)
    if not isinstance(raw, dict):
        return None
    refs = [str(item) for item in (refs or []) if item]
    payload = dict(raw)
    persona = dict(payload.get("persona_config") or {})
    persona["persona_ref_images"] = list(refs)
    # 顶层同样写单数键：万象画卷只认这个键（复数键它从来不读）
    persona["persona_ref_image"] = list(refs)
    profiles = persona.get("profiles")
    if isinstance(profiles, list):
        personas = []
        for profile in profiles:
            if not isinstance(profile, dict):
                personas.append(profile)
                continue
            item = dict(profile)
            item["persona_ref_images"] = list(refs)
            # 万象画卷归一化时单数键 persona_ref_image 优先于复数键，
            # 只写复数会被面板以前留下的 [] 盖掉（2026-11-11 实测：三视图一张都进不去）。
            item["persona_ref_image"] = list(refs)
            personas.append(item)
        persona["profiles"] = personas
    payload["persona_config"] = persona
    return payload


def apply_persona_config(omni, refs, scope="amo_image_common.apply_persona_config"):
    """通过万象画卷自己的入口更新参考图；它有校验和落盘，我们不直接写它的文件。

    返回 True 表示至少更新了内存里的配置（落盘失败只 warning，不抛）。
    """
    if omni is None:
        return False
    refs = [str(item) for item in (refs or []) if item]
    payload = persona_config_payload(omni, refs)
    apply_fn = getattr(omni, "_apply_runtime_config", None)
    if payload is None or not callable(apply_fn):
        # 没有配置结构（老版本/桩对象）：退化成只改内存属性，至少本次生图能用到
        return _set_in_memory(omni, refs, scope)
    try:
        apply_fn(payload)
    except Exception as exc:
        _note_swallowed(scope, exc)
        return _set_in_memory(omni, refs, scope)
    persist = getattr(omni, "_persist_config", None)
    if callable(persist):
        try:
            persist()
        except Exception as exc:  # 落盘失败不影响本次运行
            logger.warning("[amo] 万象画卷配置落盘失败: %s" % exc)
    sync = getattr(omni, "_safe_update_context_config", None)
    if callable(sync):
        try:
            sync()
        except Exception as exc:
            logger.debug("[amo] 万象画卷同步 AstrBot 配置失败: %s" % exc)
    return True


def _set_in_memory(omni, refs, scope):
    """只更新内存里的 plugin_config（不落盘）：接口缺失时的降级路径。"""
    cfg_obj = getattr(omni, "plugin_config", None)
    if cfg_obj is None:
        return False
    changed = False
    for attr, value in (("persona_ref_image", refs[0] if refs else ""),
                        ("persona_ref_images", list(refs))):
        try:
            setattr(cfg_obj, attr, value)
            changed = True
        except Exception as exc:
            _note_swallowed(scope, exc)
    for profile in getattr(cfg_obj, "personas", None) or []:
        try:
            profile.ref_images = list(refs)
            changed = True
        except Exception as exc:
            _note_swallowed(scope, exc)
    return changed
