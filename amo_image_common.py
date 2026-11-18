# -*- coding: utf-8 -*-
"""照片引擎与画图引擎共用的那一层。

``photo_engine``（按日程主动分享）和 ``draw_engine``（他说「画一张」才画）是两条
不同的业务链路，但有五件事以前各写了一遍，逐字相同或只差一行：

- 读插件配置（cfg_bool / cfg_int / cfg_float / cfg_str，带默认值与吞异常留痕）；
- 找生图插件实例（新名优先、旧名兜底；按名字问 context，失败缓存 60 秒，避免每 30 秒的重试风暴）；
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
from typing import TYPE_CHECKING

try:  # 兼容层：旧插件名 / 旧数据目录 / 旧配置名的唯一来源
    from . import amo_compat as _compat
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        import amo_compat as _compat

import asyncio
import os
import time

try:  # Phase 11：地点纯归一唯一实现在 amo_place
    from .amo_place import clean_place
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_place import clean_place

from astrbot.api import logger

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
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

#: 生图插件「我能永远定格你」的插件名（context.get_registered_star 按它查）
OMNI_PLUGIN_NAME = _compat.IMAGE_PLUGIN_NAME  # 旧名见 amo_compat
#: 生图插件在 plugin_data 下的目录名：新名优先，旧名只读回退（改名后老路径还能读）
OMNI_DATA_DIR_NAME = _compat.IMAGE_DATA_DIR_NAME
OMNI_DATA_DIR_NAME_LEGACY = _compat.IMAGE_PLUGIN_LEGACY_NAME
#: 三视图模板在生图插件数据目录下的子目录
TEMPLATE_SUBDIR = "amo_templates"
#: 三视图文件名后缀（正面/侧面/全身）
TEMPLATE_VIEW_SUFFIXES = {"front": ("_front", "_正面"), "side": ("_side", "_侧面"),
                          "full": ("_full", "_全身")}
#: 图片扩展名（三视图只认这些）
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
#: 旧插件名（legacy fallback）：老版本 / 未改名时仍能握手（只做回退，不重复加载；不是当前名字）
OMNI_PLUGIN_NAME_LEGACY = _compat.IMAGE_PLUGIN_LEGACY_NAME
#: 查找顺序：新名优先，旧名兜底
OMNI_PLUGIN_NAMES = _compat.IMAGE_PLUGIN_NAMES
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
        except Exception as exc:
            _note_swallowed("amo_image_common.py.104", exc)
        return None


def registered_omni_meta(getter, scope="amo_image_common.registered_omni_meta"):
    """按 OMNI_PLUGIN_NAMES 顺序找已注册的生图插件元数据。

    返回 (meta, blocked)：meta 是第一个已启用的元数据（新名优先、旧名兜底），
    blocked 表示别名都注册了但全被停用——停用 ≠ 卸载，实例还挂在内存里，
    调用方应当按「没装」处理，不能拿缓存里的旧实例继续用。
    同一时刻最多返回一个 meta，两个别名不会各拿一次。
    """
    if not callable(getter):
        return None, False
    blocked = False
    for name in OMNI_PLUGIN_NAMES:
        try:
            meta = getter(name)
        except Exception as exc:
            _note_swallowed(scope, exc)
            continue
        if meta is None:
            continue
        if getattr(meta, "activated", True) is False:
            blocked = True
            continue
        return meta, False
    return None, blocked


def find_omni(plugin, holder, scope="amo_image_common.find_omni", ttl=OMNI_CACHE_SECONDS,
              force=False):
    """拿生图插件实例（新名优先、旧名兜底；没装/没启用返回 None），结果缓存在 holder 上。

    holder 需要 ``_omni`` / ``_omni_checked`` 两个属性——就是两个引擎自己的字段，
    这样缓存跟着引擎走，热重载后不会留着一个指向旧实例的引用。
    """
    now = time.monotonic()
    # 停用 ≠ 卸载：AstrBot 停用插件只把 activated 置 False，实例还挂在内存里。
    # 不看这个标记，停用生图插件后照样能出图（用户以为已经关了）。
    try:
        context = getattr(plugin, "context", None)
        getter = getattr(context, "get_registered_star", None) if context else None
        meta, blocked = registered_omni_meta(getter, scope)
    except Exception as exc:
        _note_swallowed(scope, exc)
        meta = None
        blocked = False
        getter = None
    if blocked:
        # 新名/旧名都注册了但被停用：按「没装」处理，别拿缓存里的旧实例继续出图
        holder._omni = None
        holder._omni_checked = now
        return None
    checked = getattr(holder, "_omni_checked", 0.0)
    cached = getattr(holder, "_omni", None)
    if not force and now - checked < ttl:
        # 命中缓存；没找到时只缓存 10 秒，后装/后启用我能永远定格你能很快被发现
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


# clean_place 已迁到 amo_place（默认 limit 同样是 PLACE_MAX_CHARS=16）。


def split_keywords(text):
    """中英文逗号都能分隔的关键词串 → 列表（去空白、丢空项）。"""
    return [item.strip() for item in str(text or "").replace("，", ",").split(",") if item.strip()]


def persona_config_payload(omni, refs):
    """把参考图写进我能永远定格你「自己那一份」raw_config，返回新 dict（不改原对象）。

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
    # 顶层同样写单数键：我能永远定格你只认这个键（复数键它从来不读）
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
            # 我能永远定格你归一化时单数键 persona_ref_image 优先于复数键，
            # 只写复数会被面板以前留下的 [] 盖掉（2026-11-11 实测：三视图一张都进不去）。
            item["persona_ref_image"] = list(refs)
            personas.append(item)
        persona["profiles"] = personas
    payload["persona_config"] = persona
    return payload


def apply_persona_config(omni, refs, scope="amo_image_common.apply_persona_config"):
    """通过我能永远定格你自己的入口更新参考图；它有校验和落盘，我们不直接写它的文件。

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
            logger.warning("[amo] 我能永远定格你配置落盘失败: %s" % exc)
    sync = getattr(omni, "_safe_update_context_config", None)
    if callable(sync):
        try:
            sync()
        except Exception as exc:
            logger.debug("[amo] 我能永远定格你同步 AstrBot 配置失败: %s" % exc)
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


def plugin_data_root(plugin):
    """plugin_data 根目录（amo 自己库路径的上一级）；拿不到返回空串。"""
    path = str(getattr(getattr(plugin, "db", None), "path", "") or "")
    if not path:
        return ""
    return os.path.dirname(os.path.dirname(os.path.abspath(path)))


def template_dirs(plugin=None, omni=None):
    """三视图可用的目录列表：新插件 data_dir 在前，旧 omnidraw 只读兜底。"""
    data_dir = str(getattr(omni, "data_dir", "") or "")
    root = os.path.dirname(data_dir) if data_dir else plugin_data_root(plugin)
    if not root:
        return []
    return [os.path.join(root, name, TEMPLATE_SUBDIR)
            for name in (OMNI_DATA_DIR_NAME, OMNI_DATA_DIR_NAME_LEGACY)]


def canonical_template_dir(plugin=None, omni=None):
    """新插件的 amo_templates（迁移目标）；拿不到 plugin_data 时返回空串。"""
    dirs = template_dirs(plugin, omni)
    return dirs[0] if dirs else ""


def _inside(path, root):
    """path 是否在 root 目录里（大小写敏感按 Windows 归一）。"""
    try:
        path = os.path.abspath(path)
        root = os.path.abspath(root)
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(root)]) == os.path.normcase(root)
    except (OSError, ValueError):
        return False


def _match_view_file(name, view):
    lower = name.lower()
    if not lower.endswith(IMAGE_EXTS):
        return False
    for suffix in TEMPLATE_VIEW_SUFFIXES.get(view, ()):
        if suffix in lower:
            return True
    return view == "front" and lower.startswith("amo_persona.")


def find_template_file(view, root):
    """在 root 里按视角找一张模板；没有返回空串。"""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return ""
    for name in names:
        if _match_view_file(name, view):
            path = os.path.join(root, name)
            if os.path.isfile(path):
                return path
    return ""


def resolve_template_path(view, current="", plugin=None, omni=None):
    """某视角实际该用的模板路径：新目录优先，旧目录/当前值只读兜底。"""
    dirs = template_dirs(plugin, omni)
    canonical = dirs[0] if dirs else ""
    current = str(current or "")
    if canonical and current and _inside(current, canonical) and os.path.isfile(current):
        return current
    for root in dirs:
        found = find_template_file(view, root)
        if found:
            return found
    if current and os.path.isfile(current):
        return current
    return ""


def persona_refs_of(omni):
    """生图插件当前生效的人设参考图（先用它归一化后的配置，再退回 raw_config）。"""
    refs = []
    conf = getattr(omni, "plugin_config", None)
    if conf is not None:
        value = getattr(conf, "persona_ref_images", None)
        if value is None:
            value = getattr(conf, "persona_ref_image", None)
        if isinstance(value, str):
            value = [value] if value.strip() else []
        refs = [str(item) for item in (value or []) if str(item).strip()]
    if refs:
        return refs
    raw = getattr(omni, "raw_config", None)
    if isinstance(raw, dict):
        persona = raw.get("persona_config") if isinstance(raw.get("persona_config"), dict) else {}
        profiles = persona.get("profiles") if isinstance(persona.get("profiles"), list) else []
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            value = profile.get("persona_ref_image", profile.get("persona_ref_images"))
            if isinstance(value, str):
                value = [value] if value.strip() else []
            found = [str(item) for item in (value or []) if str(item).strip()]
            if found:
                return found
        value = persona.get("persona_ref_image") or persona.get("persona_ref_images")
        if isinstance(value, str):
            value = [value] if value.strip() else []
        refs = [str(item) for item in (value or []) if str(item).strip()]
    return refs


def self_use_hint(plugin):
    """提醒：生图插件要装「我能永远定格你」自用版，上游版一次自拍可能发两张。"""
    try:
        ctx = getattr(plugin, "context", None)
        getter = getattr(ctx, "get_registered_star", None) if ctx else None
        meta, _blocked = registered_omni_meta(getter, "amo.main.omnidraw_hint")
        if meta is None:
            return
        version = str(getattr(meta, "version", "") or "")
        name = str(getattr(meta, "display_name", "") or "")
        # 「我能永远定格你」= 自用版；旧版自用版 display 里带「自用」也认
        if "定格" in name or "自用" in name or version.startswith(("3.4.3", "3.4.4")):
            return
        logger.warning(
            "[amo] 提醒：当前生图插件不是「我能永远定格你」自用版（%s）。上游版一次自拍可能发两张；"
            "请安装「我能永远定格你」（Astrbot_amoupup_photo）。",
            name or version or "未知版本")
    except Exception as _amo_exc:
        _note_swallowed("amo.main.omnidraw_hint", _amo_exc)
