# -*- coding: utf-8 -*-
"""ApiMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

import asyncio
import os
import re
import uuid
from datetime import timedelta

# 画像关系（RELATIONS）和关系网（NETWORK_RELATIONS）是两套词表
PROFILE_TO_NETWORK = {
    "仇恨": "仇人",
    "抵触": "陌生人",
    "普通": "朋友",
    "熟识": "熟人",
    "相恋": "恋人",
}
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        DIALECT_REGIONS, MOOD_KEYS, NETWORK_RELATIONS, PLUGIN_NAME, RELATIONS, _diary_import_folder, _diary_parse_day, _diary_read, _diary_tree, _diary_write, _now, clamp, day_meta, logger, now_ts, summarize_token_usage, to_float, to_int, today_str, uniq, usage_cost, web_request,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            DIALECT_REGIONS, MOOD_KEYS, NETWORK_RELATIONS, PLUGIN_NAME, RELATIONS, _diary_import_folder, _diary_parse_day, _diary_read, _diary_tree, _diary_write, _now, clamp, day_meta, logger, now_ts, summarize_token_usage, to_float, to_int, today_str, uniq, usage_cost, web_request,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            DIALECT_REGIONS, MOOD_KEYS, NETWORK_RELATIONS, PLUGIN_NAME, RELATIONS, _diary_import_folder, _diary_parse_day, _diary_read, _diary_tree, _diary_write, _now, clamp, day_meta, logger, now_ts, summarize_token_usage, to_float, to_int, today_str, uniq, usage_cost, web_request,
        )


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object

try:  # 面板手动写日记只能补昨天及以前（当天由 22:00 自动写）
    from .amo_schedule import diary_manual_target
except ImportError:  # 直接按文件加载时
    try:
        from amo_schedule import diary_manual_target
    except Exception:  # pragma: no cover
        def diary_manual_target(now, requested):
            return requested, ""

try:  # 姿势库的参考图任务（双模式导入：按文件加载时相对 import 会失败）
    from .pose_library import batch_status as pose_batch_status
    from .pose_library import ensure_batch as pose_ensure_batch
    from .pose_library import pending_poses as pose_pending
except ImportError:  # 直接按文件加载时
    try:
        from pose_library import batch_status as pose_batch_status
        from pose_library import ensure_batch as pose_ensure_batch
        from pose_library import pending_poses as pose_pending
    except Exception:  # pragma: no cover
        def pose_batch_status():
            return {}

        async def pose_ensure_batch(plugin, poses, force=False):
            return {"ok": False}

        def pose_pending(library):
            return []


class ApiMixin(_MixinBase):
    def _register_web_apis(self):
        prefixes = uniq([
            getattr(self, "name", None),
            PLUGIN_NAME,
            os.path.basename(self.plugin_dir),
            "astrbot_plugin_Astrbot_Amo_forever",
            "astrbot_plugin_amo_forever",
        ])
        # 清除上一实例/旧版本残留的 Web API 路由。
        # AstrBot 重载插件时不会自动清理 registered_web_apis，旧路由会继续
        # 指向已经 terminate 的旧实例，表现为 "Cannot operate on a closed database"。
        try:
            own_prefixes = tuple(
                "/" + str(prefix).strip("/") + "/"
                for prefix in prefixes
                if str(prefix).strip("/")
            )
            holders = (type(self.context), self.context)
            seen_holders = set()
            for holder in holders:
                if id(holder) in seen_holders:
                    continue
                seen_holders.add(id(holder))
                registry = getattr(holder, "registered_web_apis", None)
                if not isinstance(registry, list):
                    continue
                registry[:] = [
                    item
                    for item in registry
                    if not (
                        isinstance(item, tuple)
                        and item
                        and (
                            str(item[0]).startswith(own_prefixes)
                            or str(item[0]) in ("get_status", "get_profile", "refresh_status")
                        )
                    )
                ]
        except Exception as exc:
            logger.warning("[amo] 清理旧 Web API 路由失败（插件重载后如遇 closed database，请完整重启）: %s" % exc)
        routes = [
            ("/status", self.api_status, ["GET"]),
            ("/get_status", self.api_get_status, ["GET"]),
            ("/diary/tree", self.api_diary_tree, ["GET", "POST"]),
            ("/diary/read", self.api_diary_read, ["GET", "POST"]),
            ("/diary/write", self.api_diary_write, ["POST"]),
            ("/diary/open", self.api_diary_open, ["GET", "POST"]),
            ("/diary/import", self.api_diary_import, ["POST"]),
            ("/profiles", self.api_profiles, ["GET", "POST"]),
            ("/get_profile", self.api_get_profile, ["GET", "POST"]),
            ("/profiles/sync", self.api_profiles_sync, ["GET", "POST"]),
            ("/relations", self.api_relations, ["GET", "POST"]),
            ("/relation/save", self.api_relation_save, ["GET", "POST"]),
            ("/relation_save", self.api_relation_save, ["GET", "POST"]),
            ("/relation/delete", self.api_relation_delete, ["GET", "POST"]),
            ("/relation_delete", self.api_relation_delete, ["GET", "POST"]),
            ("/memory/candidates", self.api_memory_candidates, ["GET"]),
            ("/memory_candidates", self.api_memory_candidates, ["GET"]),
            ("/profiles_sync", self.api_profiles_sync, ["GET", "POST"]),
            ("/config", self.api_config, ["GET"]),
            ("/weather/brief", self.api_weather_brief, ["GET", "POST"]),
            ("/city/search", self.api_city_search, ["GET", "POST"]),
            ("/city/set", self.api_city_set, ["GET", "POST"]),
            ("/usage", self.api_usage, ["GET"]),
            ("/providers", self.api_providers, ["GET"]),
            ("/providers_list", self.api_providers, ["GET"]),
            ("/balance", self.api_balance, ["GET"]),
            ("/config/save", self.api_config_save, ["GET", "POST"]),
            ("/config_save", self.api_config_save, ["GET", "POST"]),
            ("/profile/save", self.api_profile_save, ["GET", "POST"]),
            ("/profile/links", self.api_profile_links, ["GET", "POST"]),
            ("/profile_save", self.api_profile_save, ["GET", "POST"]),
            ("/profile/delete", self.api_profile_delete, ["GET", "POST"]),
            ("/profile_delete", self.api_profile_delete, ["GET", "POST"]),
            ("/memories", self.api_memories, ["GET", "POST"]),
            ("/chat/history", self.api_chat_history, ["GET", "POST"]),
            ("/chat_history", self.api_chat_history, ["GET", "POST"]),
            ("/memory/add", self.api_memory_add, ["GET", "POST"]),
            ("/memory_add", self.api_memory_add, ["GET", "POST"]),
            ("/memory/delete", self.api_memory_delete, ["GET", "POST"]),
            ("/memory_delete", self.api_memory_delete, ["GET", "POST"]),
            ("/schedule", self.api_schedule, ["GET", "POST"]),
            ("/schedule/detail", self.api_schedule_detail, ["GET", "POST"]),
            ("/schedule_detail", self.api_schedule_detail, ["GET", "POST"]),
            ("/mood/update", self.api_mood_update, ["GET", "POST"]),
            ("/mood_update", self.api_mood_update, ["GET", "POST"]),
            ("/schedule/regen", self.api_schedule_regen, ["GET", "POST"]),
            ("/kb/regen", self.api_kb_regen, ["GET", "POST"]),
            ("/scenic_kb/regen", self.api_scenic_kb_regen, ["GET", "POST"]),
            ("/scenic_kb_regen", self.api_scenic_kb_regen, ["GET", "POST"]),
            ("/kb_regen", self.api_kb_regen, ["GET", "POST"]),
            ("/schedule_regen", self.api_schedule_regen, ["GET", "POST"]),
            ("/weather", self.api_weather, ["GET"]),
            ("/gift", self.api_gift, ["GET"]),
            ("/gift/list", self.api_gift, ["GET"]),
            ("/wardrobe", self.api_wardrobe, ["GET", "POST"]),
            ("/wardrobe/add", self.api_wardrobe_add, ["GET", "POST"]),
            ("/wardrobe/delete", self.api_wardrobe_delete, ["GET", "POST"]),
            ("/wardrobe/reroll", self.api_wardrobe_reroll, ["GET", "POST"]),
            ("/wardrobe/today", self.api_wardrobe_today, ["GET", "POST"]),
            ("/wardrobe/night", self.api_wardrobe_night, ["GET", "POST"]),
            ("/wardrobe/refs", self.api_wardrobe_refs, ["GET", "POST"]),
            ("/wardrobe/refs/status", self.api_wardrobe_refs_status, ["GET", "POST"]),
            ("/poses", self.api_poses, ["GET", "POST"]),
            ("/poses/save", self.api_poses_save, ["GET", "POST"]),
            ("/poses/refs", self.api_poses_refs, ["GET", "POST"]),
            ("/poses/refs/status", self.api_poses_refs_status, ["GET", "POST"]),
            ("/photo/test", self.api_photo_test, ["GET", "POST"]),
            ("/photo/prompt", self.api_photo_prompt, ["GET", "POST"]),
            ("/photo/prompt_saved", self.api_photo_prompt_saved, ["GET", "POST"]),
            ("/photo/sheet", self.api_photo_sheet, ["GET", "POST"]),
            ("/photo/job", self.api_photo_job, ["GET", "POST"]),
            ("/photo/persona", self.api_photo_persona, ["GET", "POST"]),
            ("/photo/persona/delete", self.api_photo_persona_delete, ["GET", "POST"]),
            ("/photo/persona/toggle", self.api_photo_persona_toggle, ["GET", "POST"]),
            ("/photo/template_dir", self.api_photo_template_dir, ["GET", "POST"]),
            ("/photo/template_open", self.api_photo_template_open, ["GET", "POST"]),
            ("/draw/status", self.api_draw_status, ["GET"]),
            ("/draw/decide", self.api_draw_decide, ["GET", "POST"]),
            ("/draw/test", self.api_draw_test, ["GET", "POST"]),
            ("/worldview", self.api_worldview, ["GET"]),
            ("/worldview/regen", self.api_worldview_regen, ["GET", "POST"]),
            ("/worldview_regen", self.api_worldview_regen, ["GET", "POST"]),
            ("/say", self.api_say, ["GET", "POST"]),
            ("/refresh_status", self.api_refresh_status, ["GET"]),
        ]
        for prefix in prefixes:
            clean = str(prefix).strip("/")
            if not clean:
                continue
            for path, handler, methods in routes:
                self._safe_register("/" + clean + path, handler, methods, path)

    def _web_guard(self, handler, is_write=False):
        """写操作的前置检查：只收 POST + 拒绝跨站来源。

        这不是完整鉴权（AstrBot 侧有它自己的登录态），而是挡住
        <img src>/聊天预览/外链/跨站表单这类最容易触发的 CSRF 面。

        - 写路由在 `_safe_register` 里已经只注册 POST，这里再兜一层（防止别的注册路径漏掉）；
        - `Sec-Fetch-Site: cross-site` 直接 403（浏览器自己标的跨站，最可靠）；
        - Origin/Referer 与 Host 不一致：带 `same-origin/same-site` 的放行（反向代理/内网穿透下
          Host 常被改写），没有 Sec-Fetch-Site 的按跨站处理。
        """
        async def wrapper(*args, **kwargs):
            try:
                method = ""
                origin = ""
                host = ""
                site = ""
                headers = None
                if web_request is not None:
                    method = str(getattr(web_request, "method", "") or "").upper()
                    headers = getattr(web_request, "headers", None)
                    if headers is not None:
                        origin = str(headers.get("origin") or headers.get("referer") or "")
                        host = str(headers.get("host") or "")
                        site = str(headers.get("sec-fetch-site") or "").strip().lower()
                if is_write and method and method != "POST":
                    logger.warning("[amo] 拒绝非 POST 的 Web 写操作（%s）" % method)
                    return {"code": 403, "msg": "写操作只接受 POST（面板请刷新到最新版）"}
                if is_write and site == "cross-site":
                    logger.warning("[amo] 拒绝跨站 Web 写操作（origin=%s）" % origin)
                    return {"code": 403, "msg": "拒绝跨站请求"}
                if is_write and origin and host:
                    from urllib.parse import urlparse
                    origin_host = (urlparse(origin).hostname or "").lower()
                    allowed_hosts = {(host.split(":")[0] or "").strip().lower()}
                    if headers is not None:
                        for key in ("x-forwarded-host", "x-real-host"):
                            value = str(headers.get(key) or "").split(",")[0].strip()
                            if value:
                                allowed_hosts.add(value.split(":")[0].strip().lower())
                    allowed_hosts.discard("")
                    if origin_host and allowed_hosts and origin_host not in allowed_hosts:
                        if site in ("same-origin", "same-site"):
                            logger.debug("[amo] Web 写操作 Host 与 Origin 不一致（同源请求，放行）: %s -> %s"
                                         % (origin, host))
                        else:
                            logger.warning("[amo] 拒绝来源不一致的 Web 写操作: %s -> %s" % (origin, host))
                            return {"code": 403, "msg": "拒绝来源不一致的请求"}
            except Exception as exc:
                logger.debug("[amo] Web 守卫检查失败（放行）: %s" % exc)
            return await handler(*args, **kwargs)
        return wrapper

    async def api_weather_brief(self):
        """面板用：当前天气一行摘要 + 配置里的城市（只读缓存）。"""
        await self._ensure_ready()
        if not self.state.weather:
            try:
                await self.state.refresh_weather()
            except Exception as exc:
                logger.debug("[amo] 面板取天气失败: %s" % exc)
        return {
            "code": 0,
            "data": {
                "weather": self.state.weather_brief(),
                "city": self.cfg_str("weather_city", ""),
                "city_name": self.cfg_str("weather_city_name", ""),
            },
        }

    async def api_city_search(self):
        """面板用：中文城市名候选。"""
        await self._ensure_ready()
        query = str(self._q("q", "") or "").strip()
        if not query:
            data = await self._body()
            if isinstance(data, dict):
                query = str(data.get("q") or "").strip()
        if not query:
            return {"code": 0, "data": {"items": [], "query": ""}}
        module = self._city_module()
        if module is None:
            return {"code": 1, "msg": "城市表缺失：amo_city_index.py 没装好"}
        try:
            items = module.search_city(query, 20)
        except Exception as exc:
            _note_swallowed("amo_api.ApiMixin.api_city_search", exc)
            return {"code": 1, "msg": "城市表读取失败：%s" % exc}
        return {"code": 0, "data": {"items": items, "query": query}}

    async def api_city_set(self):
        """面板用：中文城市名 → 写入 weather_city(LocationID) + 城市显示名。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        text = str(data.get("city") or data.get("q") or "").strip()
        if not text:
            return {"code": 1, "msg": "请填写城市名"}
        hit = self._city_lookup(text)
        if not hit:
            return {
                "code": 1,
                "msg": "没找到「%s」；重名或冷门地方请写「省+市」，例如「广东东莞」「北京朝阳」" % text,
            }
        save = False
        if str(self.config.get("weather_city") or "") != str(hit["id"]):
            self.config["weather_city"] = str(hit["id"])
            save = True
        if not data.get("keep_name"):
            if self.cfg_str("weather_city_name", "").strip() != hit["name"]:
                self.config["weather_city_name"] = hit["name"]
                save = True
        if save:
            try:
                if hasattr(self.config, "save_config"):
                    self.config.save_config()
            except Exception as exc:
                _note_swallowed("amo_api.ApiMixin.api_city_set", exc)
                return {"code": 1, "msg": "保存配置失败：%s" % exc}
        weather = None
        if not save and self.state.weather:
            # 城市没变，别为一次「设置」白打接口
            weather = self.state.weather
        else:
            try:
                weather = await self.state.refresh_weather(force=bool(save))
            except Exception as exc:
                logger.debug("[amo] 切换城市后刷新天气失败: %s" % exc)
        return {
            "code": 0,
            "data": {
                "id": hit["id"], "name": hit["name"],
                "label": hit["label"], "weather": weather or {},
                "ambiguous": hit.get("ambiguous", 0),
                "candidates": hit.get("candidates", []),
                "note": hit.get("note", ""),
            },
        }

    async def api_config(self):
        await self._ensure_ready()
        return {"code": 0, "data": {"items": self._config_schema_items()}}

    async def api_providers(self):
        """面板下拉用：列出已配置的模型供应商（脱敏），并标注心流判定/主对话模型。"""
        await self._ensure_ready()
        judge_id = self._judge_provider_id()
        using_id = self._using_provider_id()
        items = self._provider_inventory()
        for item in items:
            item["judge"] = bool(judge_id) and item["id"] == judge_id
            item["using"] = bool(using_id) and item["id"] == using_id
        return {"code": 0, "data": {"items": items}}

    async def api_usage(self):
        await self._ensure_ready()
        if not self.cfg_bool("usage_stats_enabled", True):
            return {"code": 0, "data": {"enabled": False}}
        db_path = self._usage_db_path()
        prices = self._usage_prices()
        day_start = self._usage_start_of_today()

        def build():
            result = {"enabled": True, "currency": "元", "prices": prices}
            for label, start in (("today", day_start), ("week", day_start - 6 * 86400), ("total", None)):
                usage = summarize_token_usage(db_path, start)
                usage["cost"] = usage_cost(usage, prices)
                result[label] = usage
            return result

        data = await asyncio.to_thread(build)
        if self.cfg_bool("deepseek_balance_enabled", True):
            data["balance"] = await self._balance_cached()
        else:
            data["balance"] = {"ok": False, "error": "余额查询已关闭"}
        # 多模型用量：已配置供应商 + 逐个余额探测 + 按模型匹配今日 token
        providers = self._provider_inventory()
        judge_id = self._judge_provider_id()
        using_id = self._using_provider_id()
        today_by_model = {}
        for entry in (data.get("today", {}) or {}).get("by_model", []) or []:
            today_by_model[str(entry.get("model") or "")] = entry
        for item in providers:
            item["judge"] = bool(judge_id) and item["id"] == judge_id
            item["using"] = bool(using_id) and item["id"] == using_id
            stats = today_by_model.get(item["model"]) or today_by_model.get(item["id"]) or {}
            tokens = int(stats.get("input_miss") or 0) + int(stats.get("input_cached") or 0) + int(stats.get("output") or 0)
            item["today_tokens"] = tokens
            item["today_calls"] = int(stats.get("calls") or 0)
            item["today_cost"] = float(stats.get("cost") or 0) if isinstance(stats.get("cost"), (int, float)) else 0.0
        if self.cfg_bool("usage_balance_enabled", True) and self.cfg_bool("deepseek_balance_enabled", True):
            balances = await self._provider_balances_cached(providers)
            by_id = {str(item.get("id") or ""): item for item in balances}
            for item in providers:
                item["balance"] = by_id.get(item["id"]) or {"ok": False, "error": "未配置密钥或地址"}
        else:
            for item in providers:
                item["balance"] = {"ok": False, "error": "余额查询已关闭"}
        data["providers"] = providers
        return {"code": 0, "data": data}

    async def api_balance(self):
        await self._ensure_ready()
        data = await self._balance_cached()
        return {"code": 0, "data": data}

    async def api_config_save(self):
        await self._ensure_ready()
        data = await self._body()
        changes = data.get("changes") if isinstance(data.get("changes"), dict) else data
        schema = self._schema_map()
        saved = {}
        for key, value in (changes or {}).items():
            meta = schema.get(key)
            if not isinstance(meta, dict):
                # 操作页的「语种」下拉不在 schema 里，单独放行（普通话/各地方言）
                if key == "dialect_style":
                    meta = {"type": "string", "default": ""}
                else:
                    continue
            try:
                coerced = self._coerce_config_value(meta.get("type", "string"), value)
            except Exception as exc:
                logger.warning("[amo] 配置 %s 值无效: %s" % (key, exc))
                continue
            if key in self.SENSITIVE_KEYS:
                current = str(self.cfg(key, "") or "")
                incoming = str(coerced or "")
                # 面板回传的是打码串、或者干脆留空 → 当作"不修改"，别把真 key 覆盖掉
                if not incoming or incoming == self._mask_secret(current):
                    continue
            self.config[key] = coerced
            saved[key] = coerced
        if saved:
            try:
                if hasattr(self.config, "save_config"):
                    self.config.save_config()
            except Exception as exc:
                logger.error("[amo] 保存插件配置失败: %s" % exc)
                return {"code": 1, "msg": "保存配置失败：" + str(exc)}
            try:
                await self._apply_runtime_config()
            except Exception as exc:
                logger.error("[amo] 应用配置失败: %s" % exc)
        return {"code": 0, "data": {"saved": saved}}

    async def api_profiles_sync(self):
        # 4.3.2 起用户画像只来自本插件聊天/手动建档；
        # 不再把记忆插件的用户导入画像，记忆由 LLM hook 读取。
        return {
            "code": 0,
            "data": {"synced": 0, "profiles": self.db.list_profiles(limit=500)},
            "msg": "原版模式：画像只从聊天建立，不再从记忆插件同步用户",
        }

    async def api_memory_candidates(self):
        """记忆来源只有「想记」(Astr_Amo_data)；这里只报告它在不在。"""
        await self._ensure_ready()
        plugin = self._data_plugin()
        name = "Astr_Amo_data"
        return {
            "code": 0,
            "data": {
                "items": ([{"name": name, "title": "想记（Astr_Amo_data）", "activated": True}]
                          if plugin is not None else []),
                "selected": name if plugin is not None else "",
                "detected": name if plugin is not None else "",
            },
        }

    async def api_relations(self):
        await self._ensure_ready()
        profiles = self.db.list_profiles(limit=500)
        names = {str(item.get("uid")): (item.get("alias") or item.get("name") or item.get("uid")) for item in profiles}
        names["bot"] = str(getattr(self, "bot_name", "") or "你")
        profile_edges = {}
        for item in profiles:
            uid = str(item.get("uid") or "")
            mapped = PROFILE_TO_NETWORK.get(str(item.get("relation") or ""))
            if uid and mapped:
                profile_edges[uid] = mapped
        edges = []
        covered = set()
        for edge in self.db.list_relations():
            row = dict(edge)
            a_uid, b_uid = str(row.get("a_uid")), str(row.get("b_uid"))
            other = b_uid if a_uid == "bot" else a_uid if b_uid == "bot" else ""
            if other in profile_edges:
                row["relation"] = profile_edges[other]
                covered.add(other)
            row["a_name"] = names.get(a_uid, a_uid)
            row["b_name"] = names.get(b_uid, b_uid)
            edges.append(row)
        for uid, relation in profile_edges.items():
            if uid in covered:
                continue
            edges.append({
                "a_uid": "bot", "b_uid": uid, "relation": relation,
                "note": "画像：关系字段", "updated_at": now_ts(),
                "a_name": names["bot"], "b_name": names.get(uid, uid),
            })
        return {"code": 0, "data": {"edges": edges, "profiles": profiles, "relations": list(NETWORK_RELATIONS)}}

    async def api_profile_links(self):
        """面板用：这个 UID 关联了哪些平台身份（QQ / 飞书等）。"""
        data = await self._body()
        uid = str(data.get("uid") or "").strip()
        platform = str(data.get("platform") or "").strip() or "aiocqhttp"
        if not uid:
            return {"code": 0, "data": {"links": []}}
        mem = self._data_mem()
        if mem is None or not callable(getattr(mem, "person_context", None)):
            return {"code": 0, "data": {"links": []}}
        try:
            detail = await self._data_call("person_context", self._data_bot_id(), platform, uid, 1)
        except Exception as exc:
            logger.debug("[amo] 读取关联身份失败: %s" % exc)
            detail = {}
        links = []
        for item in (detail or {}).get("links") or []:
            links.append({
                "platform": str(item.get("platform") or ""),
                "label": str(item.get("label") or item.get("platform") or ""),
                "uid": str(item.get("uid") or ""),
                "name": str(item.get("name") or ""),
            })
        return {"code": 0, "data": {"links": links}}

    async def api_profile_delete(self):
        """面板用：删掉一个人的档案，并把本插件跟他有关的引用清干净。

        清掉：profiles / messages / proactive_log / relation_network、
              他专属的 kv（channels:、last_channel:、节流、主动消息、纪念日…）、
              礼物心愿与礼物墙、内存里的临时缓存。
        不清（属于别处）：AstrBot 核心会话库、牢记/livingmemory 等记忆插件的库。
        """
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        uid = str(data.get("uid") or "").strip()
        confirm = str(data.get("confirm") or "").strip().lower()
        if not uid:
            return {"code": 1, "msg": "缺少 uid"}
        if confirm not in ("1", "true", "yes", "on"):
            return {"code": 1, "msg": "删除档案需要确认：再带一次 confirm=1"}
        logger.info("[amo] 删除档案请求: uid=%s" % uid)
        profile = {}
        try:
            profile = self.db.get_profile(uid) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_api.ApiMixin.api_profile_delete", _amo_exc)
            profile = {}
        umos = set()
        for value in (profile.get("private_umo"), profile.get("last_group")):
            if value:
                umos.add(str(value))
        try:
            channels = self.db.kv_get("channels:%s" % uid, {}) or {}
            if isinstance(channels, dict):
                for entry in channels.values():
                    if isinstance(entry, dict) and entry.get("umo"):
                        umos.add(str(entry["umo"]))
        except Exception as _amo_exc:
            _note_swallowed("amo_api.ApiMixin.api_profile_delete", _amo_exc)
        counts = {}
        try:
            for table in ("profiles", "messages", "proactive_log"):
                try:
                    cur = self.db.conn.execute("DELETE FROM %s WHERE uid=?" % table, (uid,))
                    counts[table] = max(0, cur.rowcount or 0)
                except Exception as exc:
                    logger.debug("[amo] 删除档案时清理 %s 失败: %s" % (table, exc))
            try:
                cur = self.db.conn.execute(
                    "DELETE FROM relation_network WHERE a_uid=? OR b_uid=?", (uid, uid)
                )
                counts["relation_network"] = max(0, cur.rowcount or 0)
            except Exception as exc:
                logger.debug("[amo] 删除档案时清理关系网失败: %s" % exc)
            counts["kv"] = self._purge_profile_kv(uid)
            try:
                self.db.conn.commit()
            except Exception as _amo_exc:
                _note_swallowed("amo_api.ApiMixin.api_profile_delete", _amo_exc)
        except Exception as exc:
            _note_swallowed("amo_api.ApiMixin.api_profile_delete", exc)
            return {"code": 1, "msg": "删除失败：%s" % exc}
        gift = getattr(self, "gift", None)
        if gift is not None:
            try:
                removed = gift.purge_user(uid, umos)
                if removed:
                    counts["gifts"] = removed
            except Exception as exc:
                logger.debug("[amo] 删除档案时清理礼物失败: %s" % exc)
        try:
            if str(getattr(self, "_last_umo", "") or "") in umos:
                self._last_umo = ""
            cache = getattr(self, "_last_reply_at", None)
            if isinstance(cache, dict):
                for umo in umos:
                    cache.pop(umo, None)
        except Exception as _amo_exc:
            _note_swallowed("amo_api.ApiMixin.api_profile_delete", _amo_exc)
        # 面板的画像列表读的是数据插件（想记），只删本地等于没删：
        # 数据侧还在，刷新就「删不掉」。这里把数据侧也删掉。
        try:
            data_counts = await self._data_call("delete_profile", self._data_bot_id(), uid)
            if isinstance(data_counts, dict):
                total = sum(int(v or 0) for v in data_counts.values() if isinstance(v, (int, float)))
                if total:
                    counts["data"] = total
        except Exception as exc:
            logger.warning("[amo] 删除画像时同步数据插件失败: %s" % exc)
        logger.info("[amo] 已删除档案 %s（清理 %s）" % (uid, counts))
        return {"code": 0, "msg": "已删除", "data": {"uid": uid, "counts": counts}}

    async def api_relation_save(self):
        await self._ensure_ready()
        data = await self._body()
        a_uid = str(data.get("a_uid") or "").strip()
        b_uid = str(data.get("b_uid") or "").strip()
        relation = str(data.get("relation") or "朋友").strip()
        note = str(data.get("note") or "").strip()
        if not a_uid or not b_uid or a_uid == b_uid:
            return {"code": 1, "msg": "请选择两个不同的用户"}
        if relation not in NETWORK_RELATIONS:
            return {"code": 1, "msg": "关系类型无效"}
        self.db.upsert_relation(a_uid, b_uid, relation, note)
        try:
            await self._push_relation_to_data(a_uid, b_uid, relation, note)
        except Exception as exc:
            logger.debug("[amo] 关系同步失败: %s" % exc)
        logger.debug("[amo] 关系网更新: %s <-> %s = %s" % (a_uid, b_uid, relation))
        return await self.api_relations()

    async def api_relation_delete(self):
        await self._ensure_ready()
        data = await self._body()
        a_uid = str(data.get("a_uid") or "").strip()
        b_uid = str(data.get("b_uid") or "").strip()
        if not a_uid or not b_uid:
            return {"code": 1, "msg": "缺少用户"}
        self.db.delete_relation(a_uid, b_uid)
        await self._data_call("delete_relation", self._data_bot_id(), a_uid, b_uid)
        return await self.api_relations()

    async def api_diary_import(self):
        await self._ensure_ready()
        payload = await self._body()
        path = str((payload or {}).get("path") or "").strip()
        if not path:
            return {"code": 1, "msg": "缺少文件夹路径"}
        if len(path.rstrip("\\/")) <= 2:
            return {"code": 1, "msg": "别整盘导入，请选具体文件夹"}
        if not callable(_diary_import_folder):
            return {"code": 1, "msg": "日记模块未加载"}
        # 递归遍历 + 复制是同步阻塞的，放线程里，别把 AstrBot 事件循环卡住
        result = await asyncio.to_thread(_diary_import_folder, path, self._diary_dir())
        if not result.get("ok"):
            return {"code": 1, "msg": result.get("error") or "导入失败"}
        return {"code": 0, "data": result}

    async def api_diary_tree(self):
        await self._ensure_ready()
        if not callable(_diary_tree):
            return {"code": 1, "msg": "日记模块未加载"}
        return {"code": 0, "data": await asyncio.to_thread(_diary_tree, self._diary_dir())}

    async def api_diary_read(self):
        await self._ensure_ready()
        payload = await self._body()
        day = _diary_parse_day((payload or {}).get("date"))
        if not day:
            return {"code": 1, "msg": "日期格式应为 YYYY-MM-DD"}
        text = _diary_read(self._diary_dir(), day) if callable(_diary_read) else ""
        return {"code": 0, "data": {"date": day.isoformat(), "text": text}}

    async def api_diary_write(self):
        await self._ensure_ready()
        payload = await self._body()
        day = _diary_parse_day((payload or {}).get("date"))
        day, error = diary_manual_target(_now(), day)
        if error:
            return {"code": 1, "msg": error}
        text = await self._generate_daily_diary(day)
        if not text:
            return {"code": 1, "msg": "日记生成失败（没有素材或模型没返回）"}
        try:
            path = _diary_write(self._diary_dir(), day, text)
        except Exception as exc:
            _note_swallowed("amo_api.ApiMixin.api_diary_write", exc)
            return {"code": 1, "msg": "写入失败: %s" % exc}
        try:
            self.db.kv_set("diary_written:" + day.isoformat(), path)
        except Exception as _amo_exc:
            _note_swallowed("amo_api.ApiMixin.api_diary_write", _amo_exc)
        logger.info("[amo] 日记已写（手动）: %s" % path)
        return {"code": 0, "data": {"date": day.isoformat(), "path": path, "text": text}}

    async def api_diary_open(self):
        await self._ensure_ready()
        root = self._diary_dir()
        try:
            os.makedirs(root, exist_ok=True)
        except Exception as _amo_exc:
            _note_swallowed("amo_api.ApiMixin.api_diary_open", _amo_exc)
        opened = False
        try:
            if os.name == "nt" and os.path.isdir(root):
                os.startfile(root)
                opened = True
        except Exception as exc:
            logger.debug("[amo] 打开日记文件夹失败: %s" % exc)
        return {"code": 0, "data": {"root": root, "opened": opened}}

    async def api_dispatch(self, action: str = ""):
        await self._ensure_ready()
        name = (action or "").replace("/", "_").strip("_").lower()
        try:
            if name in ("", "index", "status", "get_status"):
                return await self.api_get_status()
            if name in ("profiles", "profile_list"):
                return await self.api_profiles()
            if name in ("profile_delete", "delete_profile"):
                return await self.api_profile_delete()
            if name in ("profile_save", "save_profile"):
                return await self.api_profile_save()
            if name in ("memories", "memory_list"):
                return await self.api_memories()
            if name in ("memory_add", "add_memory"):
                return await self.api_memory_add()
            if name in ("memory_delete", "delete_memory"):
                return await self.api_memory_delete()
            if name in ("mood_update", "update_mood"):
                return await self.api_mood_update()
            if name == "schedule":
                return await self.api_schedule()
            if name in ("scenic_kb_regen", "regen_scenic_kb"):
                return await self.api_scenic_kb_regen()
            if name in ("kb_regen", "regen_kb", "dialect_kb_regen"):
                return await self.api_kb_regen()
            if name in ("schedule_regen", "regen_schedule"):
                return await self.api_schedule_regen()
            if name in ("weather", "weather_refresh", "refresh_weather"):
                return await self.api_weather()
            if name == "worldview":
                return await self.api_worldview()
            if name in ("worldview_regen", "regen_worldview"):
                return await self.api_worldview_regen()
            if name in ("say", "send"):
                return await self.api_say()
            if name in ("refresh_status", "refresh"):
                return await self.api_refresh_status()
            if name in ("get_profile", "profile"):
                return await self.api_get_profile()
            return {"code": 1, "msg": "未知接口：" + name}
        except Exception as exc:
            logger.error("[amo] Web API %s 异常: %s" % (name, exc))
            return {"code": 1, "msg": str(exc)}

    async def api_status(self):
        await self._ensure_ready()
        data = await self._status_data()
        return {"code": 0, "data": data}

    async def api_get_status(self):
        await self._ensure_ready()
        data = await self._status_data()
        return {
            "code": 0,
            "data": {
                "mood": data["dominant"] + "（" + data["mood"].__str__() + "）",
                "schedule": data["activity"].get("activity", ""),
                "health": data["health"]["status"],
                "period": data["period"]["phase"] + " 第" + str(data["period"]["day"]) + "天",
                "weather": data["weather_text"],
                "switch_status": data["config"],
                "threshold": data["threshold"],
                "counts": data["counts"],
            },
        }

    async def api_get_profile(self):
        await self._ensure_ready()
        data = await self._body()
        uid = str(data.get("uid") or self._q("uid", "") or "")
        if uid:
            profile = self.db.get_profile(uid)
        else:
            profiles = self.db.list_profiles(limit=1)
            profile = profiles[0] if profiles else {}
        return {
            "code": 0,
            "data": {
                "relation": profile.get("relation", "普通"),
                "mem_count": len(await self._all_memories(profile.get("uid", uid), "", 200)),
                "profile": profile,
            },
        }

    async def api_refresh_status(self):
        await self._ensure_ready()
        # 限频：这个接口会强制刷新天气（和风额度），被 <img>/外链或连点触发时不能每次都打出去
        now = now_ts()
        last = to_float(getattr(self, "_refresh_status_at", 0), 0)
        if last and now - last < 5:
            return {"code": 0, "msg": "刚刚刷新过，请稍候", "data": {"throttled": True}}
        self._refresh_status_at = now
        try:
            await self.state.refresh_weather(force=True)
        except Exception as exc:
            logger.warning("[amo] 手动刷新天气失败: %s" % exc)
            return {"code": 1, "msg": "刷新失败：%s" % exc}
        return {"code": 0, "msg": "刷新成功"}

    async def api_profiles(self):
        await self._ensure_ready()
        data = await self._body()
        keyword = str(data.get("q") or self._q("q", "") or "")
        limit = to_int(data.get("limit") or self._q("limit", 100), 100)
        return {"code": 0, "data": self.db.list_profiles(keyword, limit)}

    async def api_profile_save(self):
        await self._ensure_ready()
        data = await self._body()
        uid = str(data.get("uid") or "").strip()
        if not uid:
            return {"code": 1, "msg": "uid 不能为空"}
        fields = {}
        for key in ("name", "alias", "birthday", "relation", "notes"):
            value = data.get(key, None)
            if value is not None:
                fields[key] = str(value)[:100]
        if "relation" in fields and fields["relation"] not in RELATIONS:
            return {"code": 1, "msg": "关系只能是：" + "、".join(RELATIONS)}
        if "birthday" in fields and fields["birthday"]:
            match = re.match(r"^(\d{4}-)?(\d{1,2})-(\d{1,2})$", fields["birthday"])
            if not match:
                return {"code": 1, "msg": "生日格式应为 MM-DD 或 YYYY-MM-DD"}
            fields["birthday"] = "%02d-%02d" % (to_int(match.group(2)), to_int(match.group(3)))
        affection = data.get("affection", None)
        if affection is not None:
            fields["affection"] = int(clamp(to_int(affection, 10), 0, 100))
        if not self.db.get_profile(uid):
            self.db.touch_profile(uid, fields.get("name", ""), "web", "", "", True)
        self.db.update_profile(uid, **fields)
        if fields.get("relation") == "相恋":
            self._register_lover(uid)
        if fields.get("relation"):
            mapped = PROFILE_TO_NETWORK.get(str(fields["relation"]))
            if mapped:
                try:
                    self.db.upsert_relation("bot", uid, mapped, "画像：关系字段")
                except Exception as exc:
                    logger.debug("[amo] 画像关系同步到关系网失败: %s" % exc)
        try:
            await self._push_profile_to_data(uid, self.db.get_profile(uid))
        except Exception as exc:
            logger.debug("[amo] 画像同步失败: %s" % exc)
        return {"code": 0, "data": self.db.get_profile(uid)}

    async def api_chat_history(self):
        await self._ensure_ready()
        data = await self._body()
        uid = str(data.get("uid") or self._q("uid", "") or "")
        if not uid:
            return {"code": 1, "msg": "uid 不能为空"}
        limit = to_int(data.get("limit") or self._q("limit", 200), 200)
        memories = await self._all_memories(uid, "", limit)
        return {
            "code": 0,
            "data": {
                "chat": self.db.chat_history(uid, limit),
                "memories": memories,
            },
        }

    async def api_memories(self):
        """面板记忆页：只读外挂记忆插件的记忆（本插件不再存记忆）。"""
        await self._ensure_ready()
        data = await self._body()
        uid = str(data.get("uid") or self._q("uid", "") or "")
        if not uid:
            return {"code": 1, "msg": "uid 不能为空"}
        keyword = str(data.get("q") or self._q("q", "") or "")
        limit = self._q_int("limit", 50)
        items = await self._all_memories(uid, keyword, limit)
        return {"code": 0, "data": items[:max(1, min(100, limit))]}

    async def api_memory_add(self):
        """面板“添加记忆”改为写进外挂记忆插件。"""
        await self._ensure_ready()
        data = await self._body()
        uid = str(data.get("uid") or "").strip()
        content = str(data.get("content") or "").strip()
        if not uid or not content:
            return {"code": 1, "msg": "uid 和 content 不能为空"}
        tier = to_int(data.get("tier", 1), 1)
        if self._external_memory_write(uid, content, str(data.get("kind", "manual")), tier):
            return {"code": 0, "msg": "已写入记忆终端"}
        return {"code": 1, "msg": "没有可写入的记忆终端，请先安装并启用「我能永远记住你」"}

    async def api_memory_delete(self):
        """有记忆终端就删终端里的那条；没有就还按老规矩让对方插件自己管。"""
        await self._ensure_ready()
        data = await self._body()
        memory_id = to_int(data.get("id") or 0)
        if memory_id and self._data_mem() is not None:
            ok = await self._data_call("delete_memory", self._data_bot_id(), memory_id)
            if ok:
                return {"code": 0, "msg": "已删除"}
        return {
            "code": 1,
            "msg": "记忆由记忆终端保存，请到「我能永远记住你」面板里删除",
        }

    async def api_mood_update(self):
        await self._ensure_ready()
        data = await self._body()
        incoming = data.get("mood") if isinstance(data.get("mood"), dict) else data
        for key in MOOD_KEYS:
            if key in incoming:
                try:
                    self.state.mood[key] = clamp(float(incoming[key]), 0.0, 100.0)
                except Exception as _amo_exc:
                    _note_swallowed("amo_api.ApiMixin.api_mood_update", _amo_exc)
                    continue
        self.state.save()
        return {"code": 0, "data": self.state.snapshot()}

    async def api_schedule(self):
        await self._ensure_ready()
        data = await self._body()
        day = str(data.get("date") or self._q("date", "") or today_str())
        if day == today_str():
            items = await self.state.ensure_schedule(self._last_umo)
        else:
            items = self.state._hourly_schedule(self.db.get_schedule(day) or [])
        return {"code": 0, "data": {"day": day, "meta": day_meta(day), "items": items, "tree": self.state.schedule_tree(day), "current": self.state.current_activity(), "context": self.state.schedule_context(day)}}

    async def api_schedule_detail(self):
        await self._ensure_ready()
        data = await self._body()
        day = str(data.get("date") or "").strip() or today_str()
        start_raw = str(data.get("start") or "").strip()
        matched = re.match(r"^(\d{1,2})(?::(\d{2}))?$", start_raw)
        if not matched:
            return {"code": 1, "msg": "start 需要 HH:MM，例如 10:00"}
        hour = max(0, min(23, int(matched.group(1))))
        start = "%02d:00" % hour
        if day == today_str():
            items = await self.state.ensure_schedule(self._last_umo)
        else:
            items = self.state._hourly_schedule(self.db.get_schedule(day) or [])
        item = None
        for entry in items:
            if str(entry.get("start") or "") == start:
                item = entry
                break
        if item is None:
            return {"code": 1, "msg": "没有找到这一小时的日程：" + start}
        force_raw = data.get("force", "")
        force = str(force_raw).lower() in ("1", "true", "yes", "on")
        result = await self.state.expand_hour(day, item, items, force=force)
        payload = {"day": day, "start": item.get("start", start), "end": item.get("end", "")}
        payload.update(result)
        return {"code": 0, "data": payload}

    async def api_scenic_kb_regen(self):
        await self._ensure_ready()
        kb = await self._ensure_scenic_kb()
        if kb is None:
            return {
                "code": 1,
                "msg": "景区知识库创建失败：请检查 Embedding 供应商，并查看 AstrBot 日志",
            }
        return {"code": 0, "msg": "景区知识库已就绪：" + self._scenic_kb_name()}

    async def api_kb_regen(self):
        await self._ensure_ready()
        try:
            data = await self._body()
        except Exception as _amo_exc:
            _note_swallowed("amo_api.ApiMixin.api_kb_regen", _amo_exc)
            data = {}
        style = str(data.get("dialect_style") or "").strip()
        if style in DIALECT_REGIONS:
            self.config["dialect_style"] = style
            try:
                if hasattr(self.config, "save_config"):
                    self.config.save_config()
            except Exception as _amo_exc:
                _note_swallowed("amo_api.ApiMixin.api_kb_regen", _amo_exc)
        style_now = self._dialect_style().strip()
        logger.info("[amo] 重建语种: style=%s region=%s rows=%d kb=%s" % (
            style_now, self._dialect_region(), len(self._dialect_rows()), self._dialect_kb_name()
        ))
        kb = await self._ensure_dialect_kb()
        if kb is None:
            return {
                "code": 1,
                "msg": "知识库创建失败：请检查是否配置了 Embedding 供应商，并查看 AstrBot 日志",
            }
        return {"code": 0, "msg": "知识库已就绪：" + self._dialect_kb_name()}

    async def api_gift(self):
        await self._ensure_ready()
        if self.gift is None:
            return {"code": 0, "data": {"enabled": False, "wishes": [], "gifts": []}}
        try:
            return {"code": 0, "data": self.gift.panel_data()}
        except Exception as exc:
            logger.debug("[amo] 礼物面板数据失败: %s" % exc)
            return {"code": 0, "data": {"enabled": False, "wishes": [], "gifts": []}}

    async def api_wardrobe(self):
        """面板「穿搭」页：三分类 + 今日锁定的一套。"""
        await self._ensure_ready()
        wardrobe = getattr(self, "wardrobe", None)
        empty = {"enabled": False, "categories": [], "today": {}, "outfit_count": 0}
        if wardrobe is None:
            return {"code": 0, "data": empty}
        try:
            data = wardrobe.snapshot()
        except Exception as exc:
            logger.debug("[amo] 穿搭面板数据失败: %s" % exc)
            return {"code": 0, "data": empty}
        data["enabled"] = True
        return {"code": 0, "data": data}

    async def api_wardrobe_add(self):
        """面板手动加一件：category=top/bottom/shoes，name=衣服名。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        wardrobe = getattr(self, "wardrobe", None)
        if wardrobe is None:
            return {"code": 1, "msg": "穿搭衣柜没加载（看日志有没有 wardrobe 初始化失败）"}
        result = wardrobe.add_item(
            data.get("category", ""),
            data.get("name", ""),
            sleep=bool(data.get("sleep")),
            hotel=bool(data.get("hotel")),
            swim=bool(data.get("swim")),
        )
        if not result.get("ok"):
            return {"code": 1, "msg": result.get("msg") or "添加失败"}
        name = str(data.get("name") or "").strip()
        if name and not result.get("dup"):
            try:
                from .outfit_refs import ensure_ref
                asyncio.create_task(ensure_ref(self, name))
            except Exception as exc:
                logger.debug("[amo] 服装参考图任务创建失败: %s" % exc)
        return {
            "code": 0,
            "msg": "已添加" + ("（已有的同款）" if result.get("dup") else ""),
            "data": {"item": result.get("item"), "combos": result.get("combos", 0)},
        }

    async def api_wardrobe_refs(self):
        """逐件生成参考图：已有跳过，只补没有的；生成一张确认后再下一张。"""
        await self._ensure_ready()
        wardrobe = getattr(self, "wardrobe", None)
        if wardrobe is None:
            return {"code": 1, "msg": "穿搭衣柜没加载"}
        from .outfit_refs import batch_items, ensure_batch, pending_items, batch_status
        status = batch_status()
        if status.get("running"):
            return {"code": 0,
                    "msg": "正在生成 %d/%d：%s" % (status.get("done", 0),
                                                status.get("total", 0),
                                                status.get("current", "")),
                    "data": status}
        items = batch_items(wardrobe)
        if not items:
            return {"code": 1, "msg": "衣柜里还没有衣服"}
        pending = pending_items(self, items)
        if not pending:
            return {"code": 0,
                    "msg": "全部衣服都有参考图了（%d 件），没有要补的" % len(items),
                    "data": {"running": False, "total": len(items), "done": len(items)}}
        asyncio.create_task(ensure_batch(self, pending))
        return {"code": 0,
                "msg": "已生成 %d 件，开始补剩下的 %d 件" % (len(items) - len(pending), len(pending)),
                "data": {"running": True, "total": len(pending), "done": 0}}

    async def api_wardrobe_refs_status(self):
        await self._ensure_ready()
        from .outfit_refs import batch_status
        status = batch_status()
        return {"code": 0, "data": status,
                "msg": ("生成中 %d/%d：%s" % (status.get("done", 0), status.get("total", 0),
                                            status.get("current", ""))
                        if status.get("running") else "空闲")}

    async def api_wardrobe_delete(self):
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        wardrobe = getattr(self, "wardrobe", None)
        if wardrobe is None:
            return {"code": 1, "msg": "穿搭衣柜没加载"}
        result = wardrobe.delete_item(data.get("id", ""))
        if not result.get("ok"):
            return {"code": 1, "msg": result.get("msg") or "删除失败"}
        return {"code": 0, "msg": "已删除", "data": result}

    async def api_wardrobe_reroll(self):
        """换一套：重掷今天的锁定穿搭。"""
        await self._ensure_ready()
        wardrobe = getattr(self, "wardrobe", None)
        if wardrobe is None:
            return {"code": 1, "msg": "穿搭衣柜没加载"}
        try:
            wardrobe.reroll()
            return {"code": 0, "msg": "换好了", "data": wardrobe.snapshot()}
        except Exception as exc:
            logger.debug("[amo] 换穿搭失败: %s" % exc)
            return {"code": 1, "msg": "换穿搭失败：" + str(exc)}

    async def api_wardrobe_today(self):
        """保存今日穿搭：面板选好上衣/裤子/鞋子后点保存。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        wardrobe = getattr(self, "wardrobe", None)
        if wardrobe is None:
            return {"code": 1, "msg": "穿搭衣柜没加载"}
        if bool(data.get("swim")):
            # 泳衣模式：只保存上衣（泳衣）+ 裤子（泳裤），没有鞋
            result = wardrobe.set_swim(
                top=str(data.get("top") or ""),
                bottom=str(data.get("bottom") or ""),
            )
            if not result.get("ok"):
                return {"code": 1, "msg": result.get("msg") or "保存失败"}
            return {"code": 0, "msg": "泳装已保存", "data": wardrobe.snapshot()}
        try:
            result = wardrobe.set_today(
                coat=data.get("coat", ""),
                top=data.get("top", ""),
                bottom=data.get("bottom", ""),
                skirt=data.get("skirt", ""),
                shoes=data.get("shoes", ""),
            )
        except Exception as exc:
            logger.debug("[amo] 保存今日穿搭失败: %s" % exc)
            return {"code": 1, "msg": "保存失败：" + str(exc)}
        if not result.get("ok"):
            return {"code": 1, "msg": result.get("msg") or "保存失败"}
        return {"code": 0, "msg": "今日穿搭已保存", "data": wardrobe.snapshot()}

    async def api_wardrobe_night(self):
        """晚上穿搭（睡衣）：换一套 / 锁定今晚这套。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        wardrobe = getattr(self, "wardrobe", None)
        if wardrobe is None:
            return {"code": 1, "msg": "穿搭衣柜没加载"}
        reroll = bool(data.get("reroll"))
        try:
            wardrobe.set_night(
                outfit_id=str(data.get("outfit") or ""),
                reroll=reroll,
                top=str(data.get("top") or ""),
                bottom=str(data.get("bottom") or ""),
                shoes=str(data.get("shoes") or ""),
            )
            return {
                "code": 0,
                "msg": "换好睡衣了" if reroll else "今晚睡衣已锁定",
                "data": wardrobe.snapshot(),
            }
        except Exception as exc:
            logger.debug("[amo] 设置睡衣失败: %s" % exc)
            return {"code": 1, "msg": "设置睡衣失败：" + str(exc)}

    # ---------- 自拍姿势库 ----------

    async def api_poses(self):
        """面板「姿势库」：列出姿势 + 有没有参考图。"""
        await self._ensure_ready()
        library = getattr(self, "pose", None)
        if library is None:
            return {"code": 1, "msg": "姿势库没加载（看日志）"}
        return {"code": 0, "data": library.snapshot()}

    async def api_poses_save(self):
        """保存姿势库：整份替换（面板点「保存」时调用）。"""
        await self._ensure_ready()
        library = getattr(self, "pose", None)
        if library is None:
            return {"code": 1, "msg": "姿势库没加载（看日志）"}
        payload = await self._body()
        items = payload.get("poses") if isinstance(payload, dict) else None
        if items is None:
            items = payload if isinstance(payload, list) else []
        result = library.save_all(items)
        if not result.get("ok"):
            return {"code": 1, "msg": result.get("msg") or "保存失败"}
        return {"code": 0, "msg": result.get("msg") or "已保存", "data": library.snapshot()}

    async def api_poses_refs(self):
        """逐张生成姿势参考图：已有跳过，只补没有的。"""
        await self._ensure_ready()
        library = getattr(self, "pose", None)
        if library is None:
            return {"code": 1, "msg": "姿势库没加载（看日志）"}
        status = pose_batch_status()
        if status.get("running"):
            return {"code": 0,
                    "msg": "正在生成 %d/%d：%s" % (status.get("done", 0), status.get("total", 0),
                                                status.get("current", "")),
                    "data": status}
        poses = library.poses()
        if not poses:
            return {"code": 1, "msg": "姿势库里还没有姿势"}
        pending = pose_pending(library)
        if not pending:
            return {"code": 0,
                    "msg": "全部姿势都有参考图了（%d 个），没有要补的" % len(poses),
                    "data": {"running": False, "total": len(poses), "done": len(poses)}}
        asyncio.create_task(pose_ensure_batch(self, pending))
        return {"code": 0,
                "msg": "开始补 %d 个姿势的参考图" % len(pending),
                "data": {"running": True, "total": len(pending), "done": 0}}

    async def api_poses_refs_status(self):
        await self._ensure_ready()
        status = pose_batch_status()
        return {"code": 0, "data": status,
                "msg": ("生成中 %d/%d：%s" % (status.get("done", 0), status.get("total", 0),
                                            status.get("current", ""))
                        if status.get("running") else "空闲")}

    def _photo_job_start(self, kind, coro):
        """后台跑生图任务，接口立刻返回 job id，避免桥接请求超时。"""
        jobs = getattr(self, "_photo_jobs", None)
        if jobs is None:
            jobs = {}
            self._photo_jobs = jobs
        cutoff = now_ts() - 900
        for key in [key for key, value in jobs.items() if float(value.get("ts") or 0) < cutoff]:
            jobs.pop(key, None)
        job_id = uuid.uuid4().hex[:12]
        jobs[job_id] = {"status": "running", "kind": kind, "ts": now_ts(), "data": None, "msg": ""}

        async def runner():
            try:
                result = await coro
                jobs[job_id].update(status="done", data=result)
            except Exception as exc:
                logger.warning("[amo] 照片任务失败(%s): %s" % (kind, exc))
                jobs[job_id].update(status="error", msg=str(exc))

        try:
            asyncio.create_task(runner())
        except RuntimeError:
            try:
                coro.close()
            except Exception:
                pass
            jobs[job_id].update(status="error", msg="事件循环不可用")
        return job_id

    async def api_photo_job(self):
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        job_id = str(data.get("id") or "")
        job = (getattr(self, "_photo_jobs", {}) or {}).get(job_id)
        if not job:
            return {"code": 1, "msg": "任务不存在（插件可能重载过，重新生成一次）"}
        return {"code": 0, "data": {
            "id": job_id,
            "status": job.get("status"),
            "kind": job.get("kind"),
            "msg": job.get("msg") or "",
            "data": job.get("data"),
        }}

    async def api_photo_prompt(self):
        """面板「按提示词生成」：用角色模板画一张，发到最近会话。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        prompt = str(data.get("prompt") or "").strip()
        send = bool(data.get("send", False))
        if not prompt:
            return {"code": 1, "msg": "提示词不能为空"}
        engine = getattr(self, "photo", None)
        if engine is None:
            return {"code": 1, "msg": "照片引擎未加载"}

        async def build():
            item = await engine.generate_prompt(prompt, send=send)
            if not item:
                raise RuntimeError("没出图，看日志（[amo] 自定义生图）")
            url = (item.get("url") or item.get("image_url") or item.get("data_url")
                   or item.get("file_path") or "")
            return {"url": url, "preview": engine.preview_data_url(item),
                    "msg": "已生成并发到聊天" if send else "已生成"}

        return {"code": 0, "data": {"job": self._photo_job_start("prompt", build())}}

    async def api_photo_prompt_saved(self):
        """保存/读取「按提示词生成」输入框里上一次写的提示词。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        if "text" in data:
            try:
                self.db.kv_set("photo_prompt_saved", str(data.get("text") or ""))
            except Exception as exc:
                return {"code": 1, "msg": "保存失败：%s" % exc}
        try:
            text = str(self.db.kv_get("photo_prompt_saved", "") or "")
        except Exception:
            text = ""
        return {"code": 0, "data": {"text": text}}

    async def api_photo_sheet(self):
        """角色三视图：front / side / full。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        view = str(data.get("view") or "front").strip().lower()
        engine = getattr(self, "photo", None)
        if engine is None:
            return {"code": 1, "msg": "照片引擎未加载"}

        async def build():
            item = await engine.persona_sheet(view)
            if not item:
                raise RuntimeError("没出图，看日志")
            engine.set_candidate(view, item)
            url = (item.get("url") or item.get("image_url") or item.get("data_url")
                   or item.get("file_path") or "")
            return {"url": url, "preview": engine.preview_data_url(item),
                    "view": view, "msg": "已生成 " + view}

        return {"code": 0, "data": {"job": self._photo_job_start("sheet", build())}}

    async def api_photo_persona(self):
        """把选中的三视图设为角色模板。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        url = str(data.get("url") or "").strip()
        view = str(data.get("view") or "front").strip().lower()
        engine = getattr(self, "photo", None)
        if engine is None:
            return {"code": 1, "msg": "照片引擎未加载"}
        # 优先用本地候选文件，避免远端 URL 过期后保存失败
        candidate = engine.candidate(view)
        local = str(candidate.get("file") or "")
        if local and os.path.exists(local):
            url = local
        if not url:
            return {"code": 1, "msg": "缺少图片地址"}
        message = await engine.adopt_persona(url, view)
        if message.startswith("✅"):
            engine.clear_candidate(view)
        return {"code": 0 if message.startswith("✅") else 1, "msg": message}

    async def api_photo_persona_delete(self):
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        view = str(data.get("view") or "").strip().lower()
        engine = getattr(self, "photo", None)
        if engine is None:
            return {"code": 1, "msg": "照片引擎未加载"}
        try:
            message = await engine.delete_persona(view)
        except Exception as exc:
            return {"code": 1, "msg": "删除失败：%s" % exc}
        return {"code": 0 if message.startswith("✅") else 1, "msg": message}

    async def api_photo_persona_toggle(self):
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        view = str(data.get("view") or "").strip().lower()
        enabled = bool(data.get("enabled", True))
        engine = getattr(self, "photo", None)
        if engine is None:
            return {"code": 1, "msg": "照片引擎未加载"}
        message = engine.set_template_enabled(view, enabled)
        return {"code": 0 if message.startswith("✅") else 1, "msg": message}

    async def api_photo_template_dir(self):
        """角色模板保存位置：GET 读取，POST 修改。"""
        await self._ensure_ready()
        engine = getattr(self, "photo", None)
        if engine is None:
            return {"code": 1, "msg": "照片引擎未加载"}
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        if "dir" in data:
            folder = str(data.get("dir") or "").strip()
            try:
                self.config["photo_template_dir"] = folder
                if hasattr(self.config, "save_config"):
                    self.config.save_config()
            except Exception as exc:
                return {"code": 1, "msg": "保存失败：%s" % exc}
        info = engine.template_info()
        info["configured"] = self.cfg_str("photo_template_dir", "")
        info["msg"] = "已保存" if "dir" in data else ""
        return {"code": 0, "data": info}

    async def api_photo_template_open(self):
        await self._ensure_ready()
        engine = getattr(self, "photo", None)
        if engine is None:
            return {"code": 1, "msg": "照片引擎未加载"}
        root = engine.template_info().get("root", "")
        opened = False
        try:
            if os.name == "nt" and os.path.isdir(root):
                os.startfile(root)
                opened = True
        except Exception as exc:
            logger.debug("[amo] 打开模板文件夹失败: %s" % exc)
        return {"code": 0, "data": {"root": root, "opened": opened}}

    async def api_photo_test(self):
        """面板「现在发一张」：无视概率，按类别立刻分享一张照片。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        kind = str(data.get("category") or "daily").strip().lower() or "daily"
        target_umo = str(data.get("umo") or "").strip()
        engine = getattr(self, "photo", None)
        if engine is None:
            return {"code": 1, "msg": "照片引擎未加载"}

        async def build():
            message = await engine.share_now(kind, umo=target_umo)
            return {"msg": message}

        return {"code": 0, "data": {"job": self._photo_job_start("test", build())}}

    async def api_draw_status(self):
        await self._ensure_ready()
        engine = getattr(self, "draw", None)
        if engine is None:
            return {"code": 1, "msg": "画图引擎未加载"}
        try:
            return {"code": 0, "msg": engine.diagnostics()}
        except Exception as exc:
            return {"code": 1, "msg": "画图自检失败：%s" % exc}

    async def api_draw_decide(self):
        """调试：给一段用户消息和她的回复，看会不会触发、提示词是什么。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        text = str(data.get("text") or "")
        reply = str(data.get("reply") or "")
        uid = str(data.get("uid") or "302578963")
        engine = getattr(self, "draw", None)
        if engine is None:
            return {"code": 1, "msg": "画图引擎未加载"}

        class _FakeEvent:
            def __init__(self, message, sender):
                self.message_str = message
                self.unified_msg_origin = "debug:private:%s" % sender
                self._sender = sender

            def is_private_chat(self):
                return True

            def get_sender_id(self):
                return self._sender

            def is_admin(self):
                return False

        try:
            plan = engine.decide(_FakeEvent(text, uid), reply)
        except Exception as exc:
            return {"code": 1, "msg": "decide 异常：%s" % exc}
        safe = None
        if isinstance(plan, dict):
            safe = {k: v for k, v in plan.items() if k != "cached"}
            safe["cached"] = bool(plan.get("cached"))
        return {"code": 0, "data": {"plan": safe}}

    async def api_draw_test(self):
        """调试：按当前场景真画一张并发到最近会话（会花钱，仅测试用）。"""
        await self._ensure_ready()
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        text = str(data.get("text") or "拍照给我看看")
        reply = str(data.get("reply") or "在呀")
        umo = str(data.get("umo") or self._last_umo or "")
        if not umo:
            try:
                for profile in self.db.proactive_profiles(limit=5) or []:
                    candidate = self._pick_proactive_umo(profile)
                    if candidate:
                        umo = str(candidate)
                        break
            except Exception as exc:
                logger.debug("[amo] 画图测试取会话失败: %s" % exc)
        engine = getattr(self, "draw", None)
        if engine is None:
            return {"code": 1, "msg": "画图引擎未加载"}
        if not umo:
            return {"code": 1, "msg": "没有可发送的会话"}
        plugin = self

        class _FakeEvent:
            def __init__(self):
                self.message_str = text
                self.unified_msg_origin = umo

            def is_private_chat(self):
                return True

            def get_sender_id(self):
                return "302578963"

            def is_admin(self):
                return False

            async def send(self, chain):
                await plugin.context.send_message(umo, chain)

        plan = engine.decide(_FakeEvent(), reply)
        if not plan:
            return {"code": 0, "msg": "没有触发（看 draw_debug 日志）", "data": {"plan": None}}
        engine._schedule(_FakeEvent(), plan["prompt"], plan.get("reason", "test"),
                         plan.get("cached"), plan.get("cache_key", ""), plan.get("selfie", False))
        return {"code": 0, "msg": "已开始画：%s" % plan["prompt"], "data": {"umo": umo}}

    async def api_schedule_regen(self):
        await self._ensure_ready()
        items = await self.state.ensure_schedule(self._last_umo, force=True)
        return {"code": 0, "data": items}

    async def api_weather(self):
        await self._ensure_ready()
        weather = await self.state.refresh_weather(force=True)
        return {"code": 0, "data": weather}

    async def api_worldview(self):
        await self._ensure_ready()
        await self.state.ensure_worldview(self._last_umo)
        return {"code": 0, "data": self.state.worldview}

    async def api_worldview_regen(self):
        await self._ensure_ready()
        worldview = await self.state.ensure_worldview(self._last_umo, force=True)
        return {"code": 0, "data": worldview}

    async def api_say(self):
        """面板「代她说一句」。只允许发到已知会话，参数只认 body。"""
        await self._ensure_ready()
        data = await self._body()
        text = str(data.get("text") or "").strip()
        umo = str(data.get("umo") or "").strip() or str(self._last_umo or "")
        if not text:
            return {"code": 1, "msg": "text 不能为空"}
        if not umo:
            return {"code": 1, "msg": "没有可发送的会话"}
        known = set()
        try:
            for profile in self.db.list_profiles(limit=500):
                for key in ("private_umo", "last_group"):
                    value = str(profile.get(key) or "").strip()
                    if value:
                        known.add(value)
        except Exception as exc:
            logger.debug("[amo] 读取已知会话失败: %s" % exc)
        if umo not in known:
            logger.warning("[amo] 拒绝向未知会话发送: %s" % umo)
            return {"code": 1, "msg": "umo 不在已知会话内"}
        ok = await self._send_to_umo(umo, text)
        return {"code": 0 if ok else 1, "msg": "已发送" if ok else "发送失败"}
