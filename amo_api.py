# -*- coding: utf-8 -*-
"""ApiMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

try:
    from .amo_route_table import build_routes
    from .amo_api_diary import DiaryApiMixin
    from .amo_api_schedule import ScheduleApiMixin
    from .amo_api_wardrobe import WardrobeApiMixin
    from .amo_api_photo import PhotoApiMixin
except ImportError:  # 直接按文件加载时
    from amo_route_table import build_routes
    from amo_api_diary import DiaryApiMixin
    from amo_api_schedule import ScheduleApiMixin
    from amo_api_wardrobe import WardrobeApiMixin
    from amo_api_photo import PhotoApiMixin

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
        DIALECT_REGIONS, NETWORK_RELATIONS, PLUGIN_NAME, RELATIONS, clamp, logger, now_ts, summarize_token_usage, to_float, to_int, uniq, usage_cost, web_request,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            DIALECT_REGIONS, NETWORK_RELATIONS, PLUGIN_NAME, RELATIONS, clamp, logger, now_ts, summarize_token_usage, to_float, to_int, uniq, usage_cost, web_request,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            DIALECT_REGIONS, NETWORK_RELATIONS, PLUGIN_NAME, RELATIONS, clamp, logger, now_ts, summarize_token_usage, to_float, to_int, uniq, usage_cost, web_request,
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


class ApiMixin(DiaryApiMixin, ScheduleApiMixin, WardrobeApiMixin, PhotoApiMixin, _MixinBase):
    def _register_web_apis(self):
        prefixes = uniq([
            getattr(self, "name", None),
            PLUGIN_NAME,
            os.path.basename(self.plugin_dir),
            "astrbot_plugin_Astrbot_amoupup_forever",
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
        routes = build_routes(self)
        failures = []
        for prefix in prefixes:
            clean = str(prefix).strip("/")
            if not clean:
                continue
            for path, handler, methods in routes:
                full = "/" + clean + path
                if not self._safe_register(full, handler, methods, path):
                    failures.append((full, handler, methods, path))
        self._web_route_failures = failures
        if failures:
            logger.warning("[WEB_REG] failed=%d/%d pending_retry=1",
                           len(failures), len(failures) + (len(prefixes) * len(routes)))

    def _retry_web_api_routes(self):
        """只重试失败的路由；成功注册过的路由不重复注册。"""
        failures = list(getattr(self, "_web_route_failures", None) or [])
        if not failures:
            return 0
        remaining = []
        fixed = 0
        for full, handler, methods, desc in failures:
            if self._safe_register(full, handler, methods, desc):
                fixed += 1
            else:
                remaining.append((full, handler, methods, desc))
        self._web_route_failures = remaining
        logger.warning("[WEB_REG] retry fixed=%d remaining=%d", fixed, len(remaining))
        return fixed

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
                logger.warning("[WEB_GUARD] check_failed=1 fail_open=1 error=%s", exc)
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
        """记忆来源只有「想记」(Astrbot_amoupup_data)；这里只报告它在不在。"""
        await self._ensure_ready()
        plugin = self._data_plugin()
        name = "Astrbot_amoupup_data"
        return {
            "code": 0,
            "data": {
                "items": ([{"name": name, "title": "想记（Astrbot_amoupup_data）", "activated": True}]
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
