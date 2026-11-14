# -*- coding: utf-8 -*-
"""Web 面板写路由表 + handler 模块路径归一（原 main.py）。"""
from __future__ import annotations

try:  # 包模式优先，按文件直接加载时退回绝对导入
    from .amo_observability import note as _note_swallowed
    from .amo_shared import logger, to_int, web_request
except ImportError:
    from amo_observability import note as _note_swallowed
    from amo_shared import logger, to_int, web_request


WRITE_ROUTES = frozenset({
    "/config/save", "/config_save",
    "/profile/save", "/profile_save",
    "/profile/delete", "/profile_delete",
    "/relation/save", "/relation_save",
    "/relation/delete", "/relation_delete",
    "/memory/add", "/memory_add",
    "/memory/delete", "/memory_delete",
    "/mood/update", "/mood_update",
    "/schedule/regen", "/schedule_regen",
    "/kb/regen", "/kb_regen",
    "/scenic_kb/regen", "/scenic_kb_regen",
    "/worldview/regen", "/worldview_regen",
    "/say",
    "/city/set",
    "/photo/test",
    "/photo/prompt",
    "/photo/prompt_saved",
    "/photo/sheet",
    "/photo/persona",
    "/photo/persona/delete",
    "/photo/persona/toggle",
    "/photo/template_dir",
    "/photo/template_open",
    "/draw/test",
    "/draw/decide",
    "/wardrobe/add",
    "/wardrobe/delete",
    "/wardrobe/reroll",
    "/wardrobe/today",
    "/wardrobe/night",
    "/poses/save",
    "/poses/refs",
    "/diary/write", "/diary/import", "/diary/open",
    "/refresh_status",
})




def align_handler_module_paths(main_module):
    """把 amo_*.py 子模块里注册的 handler 归到插件主模块。

    AstrBot 的 StarHandlerRegistry 只认 `handler_module_path == 插件主模块路径` 的 handler：
    加载时按这个路径取 handler 做实例绑定（`get_handlers_by_module_name`），派发时又用
    `star_map[handler_module_path].activated` 判断插件是否启用。模块化重构后 handler 都定义在
    amo_commands / amo_runtime / amo_llm 里，路径对不上 → 20 个 handler 被静默丢掉：
    `on_message` 不跑、`/amo` 命令无效、`<amo_state>` 不注入，只有核心 LLM 还在回复。
    """
    try:
        from astrbot.core.star.star_handler import star_handlers_registry
    except Exception as exc:  # 单测/非 AstrBot 环境
        logger.debug("[amo] 跳过 handler 路径归一: %s" % exc)
        return 0
    prefix = str(main_module).rsplit(".", 1)[0] + "."
    fixed = 0
    try:
        for md in list(star_handlers_registry):
            path = str(getattr(md, "handler_module_path", "") or "")
            if path == main_module or not path.startswith(prefix):
                continue
            old_full = str(getattr(md, "handler_full_name", "") or "")
            md.handler_module_path = main_module
            new_full = str(main_module) + "_" + str(getattr(md, "handler_name", "") or "")
            md.handler_full_name = new_full
            mapping = getattr(star_handlers_registry, "star_handlers_map", None)
            if isinstance(mapping, dict) and old_full in mapping:
                mapping[new_full] = mapping.pop(old_full)
            fixed += 1
    except Exception as exc:
        logger.warning("[amo] handler 路径归一失败: %s" % exc)
        return 0
    if fixed:
        logger.info("[amo] handler 路径归一完成：%d 个（%s → 主模块）" % (fixed, prefix[:-1]))
    return fixed




class WebRouteMixin:
    """面板注册 / 请求参数工具（原 main.py，行为不变）。"""

    def _safe_register(self, route, handler, methods, desc):
        # 写接口只收 POST：面板的 WRITE_ACTIONS 已经强制带 body 走 POST，
        # 老版本面板桥接没有 apiPost 时会收到 405/403，然后自动退回 fetch POST（前端有兜底）。
        # 读接口不套守卫，避免给状态轮询加额外开销。
        # 注意：route 带插件名前缀（/Astrbot_amoupup_forever/...），WRITE_ROUTES 里存的是不带前缀的
        # 路径，所以要拿 desc（注册时传的 path）来判定，否则所有写路由都会被漏掉。
        is_write = str(desc or "") in WRITE_ROUTES
        if is_write:
            methods = ["POST"]
        if is_write:
            handler = self._web_guard(handler, is_write=True)
        try:
            self.context.register_web_api(route, handler, methods, desc)
            return True
        except TypeError:
            pass
        except Exception as exc:
            logger.warning("[WEB_REG] route=%s ok=0 error=%s", route, exc)
            return False
        try:
            self.context.register_web_api(route, methods, desc, handler)
            return True
        except Exception as exc:
            logger.warning("[WEB_REG] route=%s ok=0 legacy_error=%s", route, exc)
            return False

    def _q(self, key, default=None):
        try:
            if web_request is not None:
                return web_request.query.get(key, default)
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._q", _amo_exc)
        return default

    def _q_int(self, key, default=0):
        return to_int(self._q(key, default), default)

    async def _body(self):
        try:
            if web_request is not None and hasattr(web_request, "json"):
                data = await web_request.json(default={})
                return data if isinstance(data, dict) else {}
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._body", _amo_exc)
        try:
            if web_request is not None:
                data = await web_request.get_json(silent=True)
                return data or {}
        except Exception as _amo_exc:
            _note_swallowed("main.HumanCorePlugin._body", _amo_exc)
        return {}


class _RequestProxy:
    """让测试/旧代码 patch main.web_request 仍然生效（请求对象由 main 暴露）。"""

    def __init__(self, fallback):
        self._fallback = fallback

    def _target(self):
        import sys

        for name in ("main", "data.plugins.Astrbot_amoupup_forever.main"):
            module = sys.modules.get(name)
            if module is not None and "web_request" in vars(module):
                return vars(module)["web_request"]
        return self._fallback

    def __getattr__(self, name):
        target = self._target()
        if target is None:
            raise AttributeError(name)
        return getattr(target, name)

    def __bool__(self):
        return self._target() is not None


web_request = _RequestProxy(web_request)
