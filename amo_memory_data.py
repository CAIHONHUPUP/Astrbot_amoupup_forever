# -*- coding: utf-8 -*-
"""「我能永远记住你」(Astrbot_amoupup_data) 的桥：读写记忆、同步画像/关系、缓存。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

import asyncio
import re

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        INTERNAL_RELATION_NODES,
        logger,
        to_float,
        to_int,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            INTERNAL_RELATION_NODES,
            logger,
            to_float,
            to_int,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            INTERNAL_RELATION_NODES,
            logger,
            to_float,
            to_int,
        )

if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class DataSyncMixin(_MixinBase):
    _data_found_logged: bool

    @staticmethod
    def _star_instance(meta):
        """AstrBot 不同版本把实例放在 star_cls / instance / star 上，挨个试。"""
        if meta is None:
            return None
        for attr in ("star_cls", "instance", "star", "plugin"):
            value = getattr(meta, attr, None)
            if value is not None and hasattr(value, "mem"):
                return value
        return None
    def _data_plugin(self):
        """找「我能永远记住你」插件实例；没装**或已被停用**就返回 None。

        停用插件时 AstrBot 只把 `activated` 置 False，实例还留在内存里，
        所以光看实例会误判「还连着」：`/amo status` 写「已连接」，记忆却写不进去。
        """
        try:
            for name in self.DATA_PLUGIN_NAMES:
                meta = self.context.get_registered_star(name)
                if getattr(meta, "activated", True) is False:
                    continue
                star = self._star_instance(meta)
                if star is not None:
                    return star
        except Exception as _amo_exc:
            _note_swallowed("amo_memory.HumanCorePlugin._data_plugin", _amo_exc)
        try:
            for meta in self.context.get_all_stars():
                if getattr(meta, "activated", True) is False:
                    continue
                marker = (getattr(meta, "name", "") or "") + " " + (getattr(meta, "module_path", "") or "")
                star = self._star_instance(meta)
                if star is not None and "amo_data" in marker.lower():
                    return star
        except Exception as _amo_exc:
            _note_swallowed("amo_memory.HumanCorePlugin._data_plugin", _amo_exc)
        return None
    def _data_mem(self):
        plugin = self._data_plugin()
        return getattr(plugin, "mem", None) if plugin is not None else None
    def _normalize_memories(self, rows):
        """把各家插件返回的记忆统一成 {content, tier, name, kind, ...}。"""
        items = []
        for row in rows or []:
            if isinstance(row, dict):
                content = str(row.get("content") or row.get("memory") or "").strip()
                importance = to_float(row.get("importance"), 0.5)
                name = str(row.get("name") or row.get("who") or "").strip()
                kind = str(row.get("kind") or row.get("memory_type") or "fact")
                subject = str(row.get("subject") or "")
                created_at = row.get("created_at", 0)
            else:
                memory = getattr(row, "memory", None) or row
                content = str(getattr(memory, "content", "") or "").strip()
                importance = to_float(getattr(memory, "importance", 0.5), 0.5)
                name = str(getattr(memory, "name", "") or "").strip()
                kind = str(getattr(memory, "memory_type", "") or "fact")
                subject = str(getattr(memory, "subject", "") or "")
                created_at = getattr(memory, "created_at", 0)
            if len(content) < 2:
                continue
            items.append({
                "content": content[:500],
                "tier": 2 if importance >= 0.72 else (1 if importance >= 0.42 else 0),
                "importance": importance,
                "kind": kind,
                "subject": ("bot" if (subject == "bot" or kind.startswith("bot_")) else "user"),
                "name": name,
                "source": "hub",
                "created_at": created_at,
                "hits": 0,
                "expires_at": None,
            })
        return items
    def _data_store(self):
        """兜底：直接拿数据插件的 DataStore（mem 门面缺失/旧版时用）。"""
        plugin = self._data_plugin()
        return getattr(plugin, "store", None) if plugin is not None else None
    def _data_connected(self):
        """数据插件此刻在不在：只看内存里的插件表，不读库、不联网。"""
        plugin = self._data_plugin()
        if plugin is None:
            return False
        return getattr(plugin, "mem", None) is not None or getattr(plugin, "store", None) is not None
    def _data_bot_id(self):
        return str(self.cfg_str("data_bot_id", "") or self.bot_name or "default")
    async def _data_call(self, name, *args, **kwargs):
        # 写操作失败必须看得见：以前一律 debug，聊天记录/记忆写不进去也没人知道
        _loud = name in ("add_message", "remember", "save_message")
        _log_fail = logger.warning if _loud else logger.debug
        mem = self._data_mem()
        func = getattr(mem, name, None) if mem is not None else None
        if not callable(func):
            # 旧版/半装的数据插件可能只暴露 store 门面（mem 上没有同名方法）。
            # 以前这里直接 return None，聊天记录会一句都不进终端，表现就是「她失忆」。
            store = self._data_store()
            func = getattr(store, name, None) if store is not None else None
            if not callable(func):
                return None
        try:
            return await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=max(1.0, self.cfg_float("data_call_timeout_seconds", 15.0)),
            )
        except asyncio.TimeoutError:
            logger.warning("[amo] 数据插件 %s 调用超时，先跳过（不影响这轮回复）" % name)
            return None
        except TypeError:
            # 数据插件是旧版（还没有 platform / message_id 参数）时退一步，别把聊天记录整个丢掉
            trimmed = {k: v for k, v in kwargs.items() if k not in ("platform", "message_id")}
            if trimmed != kwargs:
                try:
                    return await func(*args, **trimmed)
                except Exception as exc:
                    _log_fail("[amo] 数据插件 %s 调用失败: %s" % (name, exc))
                    return None
            _log_fail("[amo] 数据插件 %s 参数不匹配" % name)
            return None
        except Exception as exc:
            _log_fail("[amo] 数据插件 %s 调用失败: %s" % (name, exc))
            return None
    async def _push_profile_to_data(self, uid, profile=None, platform=""):
        """把本地画像推给数据插件（名字/称呼/关系/备注），顺带记下平台身份。

        返回“是否真的写进去了”：数据插件没装、旧版没有 platform 参数、或者内部报错，
        都会返回 False，调用方好统计失败条数，不再假装成功。
        """
        if not uid:
            return False
        profile = profile or self.db.get_profile(uid) or {}
        name = str(profile.get("name") or "")
        # 本地画像自己记着这个人是哪个平台的，补推时别把飞书用户错标成 QQ
        platform = str(platform or profile.get("platform") or "").strip().lower()
        fields = {
            "name": name,
            "alias": str(profile.get("alias") or ""),
            "relation": str(profile.get("relation") or ""),
            "remark": str(profile.get("notes") or ""),
        }
        mem = self._data_mem()
        save = getattr(mem, "save_profile", None) if mem is not None else None
        result = None
        if callable(save):
            # 直接调一次，异常不再被 _data_call 吞掉，好定位到底哪一步失败
            try:
                result = await save(self._data_bot_id(), str(uid), platform=platform, **fields)
            except TypeError:
                # 旧版数据插件没有 platform 参数
                try:
                    result = await save(self._data_bot_id(), str(uid), **fields)
                except Exception as exc:
                    _note_swallowed("amo_memory.HumanCorePlugin._push_profile_to_data", exc)
                    self._log_push_failure(uid, exc)
                    result = None
            except Exception as exc:
                _note_swallowed("amo_memory.HumanCorePlugin._push_profile_to_data", exc)
                self._log_push_failure(uid, exc)
                result = None
        if isinstance(result, dict) and result.get("uid"):
            return True
        if result:
            return True
        # 兜底：mem 门面不可用时，直接写数据插件的 DataStore（老版本只有 store）
        store = self._data_store()
        saver = getattr(store, "save_profile", None)
        if not callable(saver):
            return False
        try:
            raw = await asyncio.to_thread(
                lambda: saver(self._data_bot_id(), str(uid), **fields)
            )
        except Exception as exc:
            _note_swallowed("amo_memory.HumanCorePlugin._push_profile_to_data", exc)
            self._log_push_failure(uid, exc)
            return False
        return bool(raw and (not isinstance(raw, dict) or raw.get("uid")))
    def _log_push_failure(self, uid, exc):
        """同一次同步里最多打 3 条 warning，避免刷屏。"""
        count = to_int(getattr(self, "_push_fail_count", 0))
        self._push_fail_count = count + 1
        if count < 3:
            logger.warning(
                "[amo] 推送画像 %s 失败: %s: %s" % (uid, type(exc).__name__, exc)
            )
    async def _sync_relations_from_data(self):
        """把记忆终端里的关系网拉回本地（面板改过的也能生效，不用重启）。"""
        try:
            rows = await self._data_call("list_relations", self._data_bot_id())
        except Exception as _amo_exc:
            _note_swallowed("amo_memory.HumanCorePlugin._sync_relations_from_data", _amo_exc)
            return 0
        if rows is None:
            # 终端不可用时不要动本地，避免把关系网清空
            return 0
        remote = set()
        count = 0
        for edge in rows:
            a_uid = str(edge.get("a_uid") or "")
            b_uid = str(edge.get("b_uid") or "")
            if not a_uid or not b_uid:
                continue
            if a_uid in INTERNAL_RELATION_NODES or b_uid in INTERNAL_RELATION_NODES:
                # 老版本可能推过 bot 边：不参与比较，也不动本地
                continue
            remote.add(tuple(sorted([a_uid, b_uid])))
            try:
                self.db.upsert_relation(a_uid, b_uid, edge.get("relation") or "朋友", edge.get("note") or "")
                count += 1
            except Exception as _amo_exc:
                _note_swallowed("amo_memory.HumanCorePlugin._sync_relations_from_data", _amo_exc)
                continue
        # 面板上删掉的关系，本地也同步删掉（终端为准）；
        # 终端返回空、或远端的边比本地还少时都不删——那多半是换库/重装/恢复残缺备份，
        # 照删一次就能把本地关系网清空（之后还得靠聊天慢慢沉淀）。
        try:
            local_rows = self.db.list_relations()
        except Exception as _amo_exc:
            _note_swallowed("amo_memory.HumanCorePlugin._sync_relations_from_data", _amo_exc)
            local_rows = []
        # bot/self 边推不到远端，比较前先排掉：不然远端永远"比本地少"，
        # 这条 warning 会每轮都响，而且下面"终端删了本地也删"永远不会执行。
        local_rows = [
            item for item in local_rows
            if str(item.get("a_uid") or "") not in INTERNAL_RELATION_NODES
            and str(item.get("b_uid") or "") not in INTERNAL_RELATION_NODES
        ]
        if not remote or len(remote) < len(local_rows):
            if remote:
                logger.warning(
                    "[amo] 关系网不同步：远端 %d 条 < 本地 %d 条，先不删本地"
                    % (len(remote), len(local_rows))
                )
            return count
        try:
            for item in local_rows:
                pair = tuple(sorted([str(item.get("a_uid") or ""), str(item.get("b_uid") or "")]))
                if pair not in remote:
                    self.db.delete_relation(pair[0], pair[1])
        except Exception as _amo_exc:
            _note_swallowed("amo_memory.HumanCorePlugin._sync_relations_from_data", _amo_exc)
        return count
    async def _sync_profiles_to_data(self):
        """把本地画像补齐到数据插件：缺的补、改过的重推。不依赖一次性迁移标记。

        之前迁移是一次性的，标记写早了就永远是 0；这里改成每次都对一遍，
        而且推完再查一次条数，日志里能直接看到到底写进去没有。
        """
        mem = self._data_mem()
        store = self._data_store()
        if mem is None and store is None:
            return 0
        bot_id = self._data_bot_id()
        try:
            local = self.db.list_profiles(limit=500) or []
        except Exception as exc:
            logger.warning("[amo] 读本地画像失败: %s" % exc)
            return 0
        try:
            remote_rows = await self._data_call("list_profiles", bot_id, "", 500) or []
        except Exception as _amo_exc:
            _note_swallowed("amo_memory.HumanCorePlugin._sync_profiles_to_data", _amo_exc)
            remote_rows = []
        if not remote_rows:
            # mem 门面不可用的旧版：直接读 DataStore
            lister: Any = getattr(self._data_store(), "list_profiles", None)
            if callable(lister):
                try:
                    remote_rows = await asyncio.to_thread(lambda: lister(bot_id, "", 500)) or []
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory.HumanCorePlugin._sync_profiles_to_data", _amo_exc)
                    remote_rows = []
        self._push_fail_count = 0
        remote = {}
        for row in remote_rows:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("uid") or "")
            if uid:
                remote[uid] = row
        pushed = 0
        failed = 0
        pulled = 0
        for profile in local:
            if not isinstance(profile, dict):
                continue
            uid = str(profile.get("uid") or "")
            if not uid:
                continue
            name = str(profile.get("name") or "")
            alias = str(profile.get("alias") or "")
            relation = str(profile.get("relation") or "")
            remark = str(profile.get("notes") or "")
            old = remote.get(uid)
            if old is not None:
                if (
                    str(old.get("name") or "") == name
                    and str(old.get("alias") or "") == alias
                    and str(old.get("relation") or "") == relation
                    and str(old.get("remark") or "") == remark
                ):
                    continue
                # 数据插件里更新过（面板手动改了名字/关系）就以远端为准，拉回本地。
                # 比较基准是 profile_updated_at（本地手动改画像的时间），不是 last_seen：
                # last_seen 每收一条消息都会刷新，拿它比会把面板改动永远判成「更旧」。
                remote_at = to_float(old.get("updated_at"), 0)
                local_at = to_float(profile.get("profile_updated_at"), 0)
                if remote_at and remote_at > local_at:
                    if self._apply_remote_profile(uid, old, profile):
                        pulled += 1
                    continue
            try:
                ok = await self._push_profile_to_data(uid, profile)
            except Exception as exc:
                logger.warning("[amo] 推送画像 %s 异常: %s: %s" % (uid, type(exc).__name__, exc))
                ok = False
            if ok:
                pushed += 1
            else:
                failed += 1
        # 推完再查一次，拿到“真的写进去了多少条”
        after: list[Any] = []
        try:
            after = await self._data_call("list_profiles", bot_id, "", 500) or []
        except Exception as _amo_exc:
            _note_swallowed("amo_memory.HumanCorePlugin._sync_profiles_to_data", _amo_exc)
            after = []
        if not after:
            lister = getattr(self._data_store(), "list_profiles", None)
            if callable(lister):
                try:
                    after = await asyncio.to_thread(lambda: lister(bot_id, "", 500)) or []
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory.HumanCorePlugin._sync_profiles_to_data", _amo_exc)
                    after = []
        total = max(len(after), len(remote))
        message = (
            "[amo] 画像同步：本地 %d 条 / 数据插件 %d 条，本次补推 %d 条，拉回 %d 条，失败 %d 条"
            % (len(local), total, pushed, pulled, failed)
        )
        if failed:
            logger.warning(message)
            if failed >= len(local) and local:
                logger.warning(
                    "[amo] 画像一条都没写进去：先确认 Astrbot_amoupup_data 已启用且版本 >= 2.0.4"
                )
        elif pushed or not getattr(self, "_profile_sync_logged", False):
            self._profile_sync_logged = True
            logger.info(message)
        else:
            logger.debug(message)
        return pushed
    def _apply_remote_profile(self, uid, remote_row, local_profile):
        """把数据插件面板改过的画像字段拉回本地（不标记成本地手动修改）。"""
        fields = {}
        for src, dst in (("name", "name"), ("alias", "alias"), ("relation", "relation"), ("remark", "notes")):
            value = str(remote_row.get(src) or "").strip()
            if value and value != str(local_profile.get(dst) or "").strip():
                fields[dst] = value
        if not fields:
            return False
        try:
            self.db.update_profile(uid, mark_local=False, **fields)
        except Exception as exc:
            logger.warning("[amo] 拉回画像 %s 失败: %s" % (uid, exc))
            return False
        logger.info("[amo] 画像从数据插件拉回：%s %s" % (uid, ",".join(fields.keys())))
        return True
    async def _push_relation_to_data(self, a_uid, b_uid, relation, note=""):
        # "bot"/"self" 是插件内部标识，不是真实人物：写进记忆终端会让关系图多出一个
        # 叫 bot 的节点，所以只推真实的人与人之间的关系。
        if str(a_uid) in INTERNAL_RELATION_NODES or str(b_uid) in INTERNAL_RELATION_NODES:
            return
        await self._data_call(
            "save_relation", self._data_bot_id(), str(a_uid), str(b_uid),
            str(relation or "朋友"), str(note or ""),
        )
    async def _data_mem_list(self, uid, keyword="", limit=50):
        """面板记忆页：直接列出记忆终端里的条目。"""
        mem = self._data_mem()
        if mem is None or not uid:
            return []
        size = max(1, min(200, to_int(limit, 50)))
        try:
            if keyword:
                recall = getattr(mem, "recall", None)
                rows = await recall(self._data_bot_id(), str(uid), str(keyword), size, "", "") if callable(recall) else []
            else:
                lister = getattr(mem, "list_memories", None)
                rows = await lister(self._data_bot_id(), str(uid), size) if callable(lister) else []
        except Exception as exc:
            logger.debug("[amo] 读取记忆终端失败: %s" % exc)
            return []
        return [dict(row) for row in (rows or []) if isinstance(row, dict)]

    async def _all_memories(self, uid, keyword="", limit=50):
        """面板/命令统一的记忆读取入口：只读「想记」(Astr_Amo_data)。

        以前各走各的：记忆页读想记，画像页和命令只读旧适配器 → 只装想记时会显示 0 条。
        """
        return await self._data_mem_list(uid, keyword, limit)

    async def _push_cache_to_data(self, key, value):
        await self._data_call("cache_set", self._data_bot_id(), str(key), value)
    async def _sync_with_data_plugin(self):
        """启动时双向同步：首次把本地画像/关系/日程缓存推过去，之后以插件为准补齐本地。

        数据插件（Astr_Amo_data）可能比 amo 后加载，所以允许重试：
        - 本实例只成功同步一次（_data_synced），之后所有调用直接返回，不重复推送；
        - 启动、on_astrbot_loaded、后台 tick 三处可能同时来，用锁串行，避免重复迁移。
        """
        if self._data_synced:
            return
        async with self._data_sync_lock:
            if self._data_synced:
                return
            await self._sync_with_data_plugin_once()
    async def _sync_with_data_plugin_once(self):
        """真正干活的同步；没找到数据插件就只记一笔、安排 60 秒后的一次提醒。"""
        mem = self._data_mem()
        if mem is None and self._data_store() is None:
            self._note_data_missing()
            return
        if not self._data_found_logged:
            self._data_found_logged = True
            logger.info("[amo] 已连接数据插件 Astrbot_amoupup_data，开始同步")
        bot_id = self._data_bot_id()
        marker = "data_plugin_migrated_" + bot_id
        migrated = bool(self.db.kv_get(marker, False))
        if not migrated:
            pushed = {"relations": 0, "cache": 0}
            try:
                for edge in self.db.list_relations():
                    await self._push_relation_to_data(
                        edge.get("a_uid"), edge.get("b_uid"),
                        edge.get("relation"), edge.get("note"),
                    )
                    pushed["relations"] += 1
            except Exception as exc:
                logger.debug("[amo] 关系网迁移失败: %s" % exc)
            for key in ("schedule", "schedule_ctx", "schedule_profile", "worldview"):
                try:
                    value = self.db.kv_get(key, None)
                    if value:
                        await self._push_cache_to_data(key, value)
                        pushed["cache"] += 1
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory.HumanCorePlugin._sync_with_data_plugin_once", _amo_exc)
            self.db.kv_set(marker, True)
            logger.info("[amo] 已把关系网/日程缓存迁移到「我能永远记住你」：%s" % pushed)
        # 画像每次都对齐（不看一次性标记）：缺的补、改过的重推
        try:
            await self._sync_profiles_to_data()
        except Exception as exc:
            logger.warning("[amo] 画像同步失败: %s" % exc)
        # 插件里有、本地没有的，补回本地
        try:
            rows = await self._data_call("list_relations", bot_id) or []
            for edge in rows:
                try:
                    self.db.upsert_relation(
                        edge.get("a_uid"), edge.get("b_uid"),
                        edge.get("relation") or "朋友", edge.get("note") or "",
                    )
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory.HumanCorePlugin._sync_with_data_plugin_once", _amo_exc)
                    continue
        except Exception as exc:
            logger.debug("[amo] 拉取关系网失败: %s" % exc)
        try:
            rows = await self._data_call("list_profiles", bot_id, "", 500) or []
            for row in rows:
                uid = str(row.get("uid") or "")
                if not uid:
                    continue
                local = self.db.get_profile(uid) or {}
                if not local:
                    self.db.touch_profile(uid, str(row.get("name") or ""), "", "", "", True)
                    local = self.db.get_profile(uid) or {}
                # 只有数据插件里的这条更新（面板改过）才覆盖本地，否则以本地为准，
                # 免得上一条自动改名被旧值顶回去、来回打架。
                try:
                    remote_at = to_float(row.get("updated_at"), 0)
                    local_at = to_float(local.get("profile_updated_at"), 0)
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory.HumanCorePlugin._sync_with_data_plugin_once", _amo_exc)
                    remote_at = local_at = 0
                if remote_at and remote_at <= local_at:
                    continue
                self._apply_remote_profile(uid, row, local)
        except Exception as exc:
            logger.debug("[amo] 拉取画像失败: %s" % exc)
        # 日程/世界观缓存：本地空的才用插件的
        for key, attr in (("schedule", "schedule"), ("schedule_ctx", "schedule_ctx"),
                          ("schedule_profile", "schedule_profile"), ("worldview", "worldview")):
            try:
                local = self.db.kv_get(key, None)
                if local:
                    continue
                value = await self._data_call("cache_get", bot_id, key)
                if value:
                    self.db.kv_set(key, value)
                    if self.state is not None:
                        setattr(self.state, attr, value)
            except Exception as _amo_exc:
                _note_swallowed("amo_memory.HumanCorePlugin._sync_with_data_plugin_once", _amo_exc)
                continue
        # 到这里才算这次启动真的同步完了：以后不再探测、不再重复推
        self._data_synced = True
        self._data_missing_logged = False
    def finance_memory_uid(self):
        """金融事件的记忆挂到谁身上：优先管理员 uid，其次最近会话的 uid。"""
        try:
            for item in sorted(getattr(self, "admin_uids", set()) or []):
                if str(item).strip():
                    return str(item).strip()
        except Exception as _amo_exc:
            _note_swallowed("amo_memory_data.finance_memory_uid", _amo_exc)
        umo = str(getattr(self, "_last_umo", "") or "")
        return umo.split(":")[-1] if umo else ""

    async def finance_remember(self, content, kind="finance", tier=2, idem_key=""):
        """金融记忆写入（长期、subject=bot）；返回 bool。"""
        uid = self.finance_memory_uid()
        if not uid:
            return False
        try:
            return bool(await self._data_memory_write(
                uid, content, kind=str(kind or "finance"), tier=int(tier),
                subject="bot", idem_key=str(idem_key or ""),
            ))
        except Exception as exc:
            logger.debug("[amo] 金融记忆写入失败: %s" % exc)
            return False

    async def finance_recall(self, query, limit=6):
        """金融记忆检索（返回 list[dict]）；失败返回空列表。"""
        uid = self.finance_memory_uid()
        if not uid:
            return []
        try:
            return await self._data_memory_recall(uid, str(query or ""), limit=int(limit))
        except Exception as exc:
            logger.debug("[amo] 金融记忆检索失败: %s" % exc)
            return []

    async def _data_memory_recall(self, uid, text, limit=6, event=None):
        """从「记忆终端」（Astr_Amo_data）取回这个人的记忆。

        陪伴自己不存记忆：装了就只问它要，没装才退回外挂记忆插件。
        """
        mem = self._data_mem()
        if mem is None or not uid:
            return []
        recall = getattr(mem, "recall", None)
        if not callable(recall):
            return []
        scope = "private"
        group_id = ""
        try:
            if event is not None and not event.is_private_chat():
                scope = "group"
                group_id = str(event.get_group_id() or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_llm.LlmMixin._data_memory_recall", _amo_exc)
        platform = ""
        try:
            if event is not None:
                platform = self._platform_of(event)
        except Exception as _amo_exc:
            _note_swallowed("amo_llm.LlmMixin._data_memory_recall", _amo_exc)
            platform = ""
        try:
            rows = await recall(
                self._data_bot_id(), str(uid), str(text or ""),
                max(1, to_int(limit, 6)), scope, group_id, platform=platform,
            )
        except TypeError:
            # 旧版没有 scope / group_id / platform 参数
            try:
                rows = await recall(
                    self._data_bot_id(), str(uid), str(text or ""), max(1, to_int(limit, 6))
                )
            except Exception as exc:
                logger.debug("[amo] 记忆终端召回失败: %s" % exc)
                return []
        except Exception as exc:
            logger.debug("[amo] 记忆终端召回失败: %s" % exc)
            return []
        items = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            content = re.sub(r"\s+", " ", str(row.get("content") or "")).strip()
            if len(content) < 2:
                continue
            importance = to_float(row.get("importance"), 0.5)
            kind = str(row.get("kind") or "fact")
            subject = str(row.get("subject") or "")
            items.append({
                "content": content[:500],
                "tier": 2 if importance >= 0.72 else (1 if importance >= 0.42 else 0),
                "kind": kind,
                "subject": ("bot" if (subject == "bot" or kind.startswith("bot_")) else "user"),
                "source": "amo_data",
                "memory_type": str(row.get("kind") or ""),
                "created_at": row.get("created_at", 0),
                "platform": str(row.get("platform") or ""),
                "pid": int(row.get("pid") or 0),
                "hits": 0,
                "expires_at": None,
            })
        return items
    async def _data_memory_write(self, uid, content, kind="fact", tier=1,
                                 scope="private", group_id="", subject="user", platform="",
                                 idem_key=""):
        """把一条记忆交给「记忆终端」写入；subject=user/bot 区分主体。"""
        mem = self._data_mem()
        if mem is None or not uid:
            return False
        content = re.sub(r"\s+", " ", str(content or "")).strip()
        if not content:
            return False
        remember = getattr(mem, "remember", None)
        if not callable(remember):
            return False
        level = to_int(tier, 1)
        importance = 0.75 if level >= 2 else (0.55 if level == 1 else 0.35)
        # 群聊写 group + group_id，私聊写 private（记忆终端按这个分桶）
        scope = "group" if str(scope or "") == "group" else "private"
        group_id = str(group_id or "") if scope == "group" else ""
        # 数据插件版本不同认的参数也不同：从最全的写法往后退，别把记忆整个丢掉
        subject = "bot" if str(subject or "").strip().lower() == "bot" else "user"
        logger.info(
            "[IDENTITY_DEBUG] action=memory_write subject=%s kind=%s",
            subject, str(kind or "fact"),
        )
        platform = str(platform or "").strip()
        variants = []
        if platform:
            # 新版记忆终端认 platform：写进去才知道这条是飞书还是 QQ 说的
            variants.append({"kind": str(kind or "fact"), "importance": importance,
                             "scope": scope, "group_id": group_id, "subject": subject,
                             "platform": platform, "idem_key": str(idem_key or "")})
        variants.extend([
            {"kind": str(kind or "fact"), "importance": importance,
             "scope": scope, "group_id": group_id, "subject": subject,
             "idem_key": str(idem_key or "")},
            {"kind": str(kind or "fact"), "importance": importance,
             "scope": scope, "group_id": group_id},
            {"kind": str(kind or "fact"), "importance": importance},
            {},
        ])
        result = False
        last_error = None
        for kwargs in variants:
            try:
                result = await remember(self._data_bot_id(), str(uid), content, **kwargs)
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:
                logger.debug("[amo] 记忆终端写入失败: %s" % exc)
                return False
            break
        else:
            if last_error is not None:
                logger.debug("[amo] 记忆终端写入失败: %s" % last_error)
            return False
        if isinstance(result, dict):
            return bool(result.get("ok", True))
        return bool(result)
