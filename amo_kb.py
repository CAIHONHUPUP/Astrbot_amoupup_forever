# -*- coding: utf-8 -*-
"""KbMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

from typing import Any

from typing import TYPE_CHECKING

import hashlib
import json
import os
import inspect

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        logger, now_ts, to_float,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            logger, now_ts, to_float,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                logger, now_ts, to_float,
            )


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class KbMixin(_MixinBase):
    _scenic_cache: Any



    def scenic_spots(self):
        if getattr(self, "_scenic_cache", None):
            return self._scenic_cache
        try:
            path = os.path.join(self.plugin_dir, "scenic_spots.json")
            with open(path, encoding="utf-8") as stream:
                data = json.load(stream)
            self._scenic_cache = data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("[amo] 读取景区数据失败: %s" % exc)
            self._scenic_cache = []
        return self._scenic_cache

    def _scenic_kb_name(self):
        name = self.cfg_str("scenic_kb_name", "旅游知识库-风景区").strip()
        if not name or name in ("中国景区知识库", "景区知识库"):
            return "旅游知识库-风景区"
        return name

    def _scenic_text_lines(self):
        lines = []
        for spot in self.scenic_spots():
            if not isinstance(spot, dict):
                continue
            projects = spot.get("projects")
            if not isinstance(projects, list):
                projects = []
            lines.append(
                "景区：%s\n城市：%s·%s\n等级：%s\n游玩项目：%s\n建议时长：%s\n区域：%s" % (
                    spot.get("name", ""), spot.get("province", ""), spot.get("city", ""),
                    spot.get("level", ""), "、".join(str(item) for item in projects),
                    spot.get("duration", ""), spot.get("area", ""),
                )
            )
        return lines

    @staticmethod
    def _scenic_fingerprint(name, lines):
        """景区库内容指纹：内容没变就不重传，省掉每次启动的 470 条切分 + embedding。"""
        body = str(name) + "|" + "\n".join(str(item) for item in lines or [])
        return hashlib.sha1(body.encode("utf-8")).hexdigest()

    async def _ensure_scenic_kb(self):
        if not self.cfg_bool("create_scenic_kb", True):
            return None
        try:
            manager = getattr(self.context, "kb_manager", None)
            if manager is None:
                logger.warning("[amo] 当前 AstrBot 没有 kb_manager，景区知识库未创建")
                return None
            if callable(manager) and not hasattr(manager, "create_kb"):
                manager = manager()
            name = self._scenic_kb_name()
            kb = await manager.get_kb_by_name(name)
            created_now = False
            if kb is None:
                kb = await _create_scenic_kb(self, manager, name)
                if kb is None:
                    return None
                created_now = True
            await _sync_scenic_kb(self, name, kb, created_now)
            return kb
        except Exception as exc:
            text = str(exc)
            # 2026-11-20：这两类是“已经在跑”的正常状态，不该每次启动都刷 WARN
            if "已存在" in text:
                logger.debug("[amo] 景区知识库跳过: %s", exc)
            else:
                logger.warning("[amo] 景区知识库创建失败: %s" % exc)
            return None

    async def _scenic_kb_context(self, query):
        try:
            manager = getattr(self.context, "kb_manager", None)
            if manager is None or not str(query or "").strip():
                return ""
            name = self._scenic_kb_name()
            kb = await manager.get_kb_by_name(name)
            if kb is None:
                return ""
            result = await manager.retrieve(str(query)[:400], [name], top_m_final=4)
            return str(result.get("context_text") or "") if isinstance(result, dict) else ""
        except Exception as exc:
            logger.debug("[amo] 景区知识库检索失败: %s" % exc)
            return ""




async def _create_scenic_kb(self, manager, name):
    """没有库就建一个：先找 Embedding 供应商，拿不到有效 id 就返回 None。"""
    providers = list(self.context.get_all_embedding_providers() or [])
    if not providers:
        logger.warning("[amo] 没有 Embedding 供应商，景区知识库未创建")
        return None
    provider_id = str(getattr(providers[0], "provider_config", {}).get("id", "") or "")
    if not provider_id:
        try:
            provider_id = str(providers[0].meta().id or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_kb.HumanCorePlugin._ensure_scenic_kb", _amo_exc)
            provider_id = ""
    if not provider_id:
        logger.warning("[amo] Embedding 供应商没有有效 id，景区知识库未创建")
        return None
    created = manager.create_kb(
        name,
        "中国 3A 级以上景区、所在城市与具体游玩项目",
        emoji="🏞️",
        embedding_provider_id=provider_id,
    )
    return await created if inspect.isawaitable(created) else created

async def _sync_scenic_kb(self, name, kb, created_now) -> None:
    """按内容指纹同步景区文档：分批上传，只成功一部分就 6 小时内不重试。"""
    lines = self._scenic_text_lines()
    fingerprint = self._scenic_fingerprint(name, lines)
    key = "scenic_kb_synced:" + name
    retry_key = "scenic_kb_retry_after:" + name
    synced = ""
    retry_after = 0.0
    try:
        synced = str(self.db.kv_get(key, "") or "")
        retry_after = to_float(self.db.kv_get(retry_key, 0), 0)
    except Exception as _amo_exc:
        _note_swallowed("amo_kb.HumanCorePlugin._ensure_scenic_kb", _amo_exc)
    if lines and synced != fingerprint and (created_now or now_ts() >= retry_after):
        doc_ids = []
        try:
            try:
                docs = await kb.list_documents()
            except TypeError:
                docs = await kb.list_documents(search="amo_scenic")
            for doc in docs or []:
                label = str(getattr(doc, "name", "") or getattr(doc, "doc_name", "") or "")
                doc_id = getattr(doc, "doc_id", "") or getattr(doc, "id", "")
                if doc_id and (not label or "amo_scenic" in label):
                    doc_ids.append(doc_id)
        except Exception as _amo_exc:
            _note_swallowed("amo_kb.HumanCorePlugin._ensure_scenic_kb", _amo_exc)
        # 升级到这一版之前没有指纹：库里已经有文档就先认下来，不重传。
        adopted = bool(doc_ids) and not created_now and not synced
        uploaded = False
        if adopted:
            logger.info(
                "[amo] 景区知识库 %s 已存在（%d 个景区），本次跳过重传" % (name, len(lines))
            )
        else:
            for doc_id in doc_ids:
                try:
                    await kb.delete_document(doc_id)
                except Exception as _amo_exc:
                    _note_swallowed("amo_kb.HumanCorePlugin._ensure_scenic_kb", _amo_exc)
            # 一次传 470 个景区会失败：AstrBot 那边把全部文本块塞进一次
            # insert_batch，几十个块就写不进索引（实测 150 个景区/11 块可以，
            # 400 个景区就报「写入知识库索引时出错」）。这里按批切成小文档。
            batch = max(20, int(getattr(self, "SCENIC_KB_BATCH_LINES", 120)))
            parts = [lines[index:index + batch] for index in range(0, len(lines), batch)]
            ok_parts = 0
            for index, part in enumerate(parts, 1):
                try:
                    await kb.upload_document(
                        "amo_scenic_%02d.txt" % index,
                        "\n\n".join(part).encode("utf-8"),
                        "txt",
                    )
                    ok_parts += 1
                except Exception as exc:
                    logger.warning(
                        "[amo] 景区知识库第 %d/%d 批上传失败（%s）" % (index, len(parts), exc)
                    )
            uploaded = bool(parts) and ok_parts == len(parts)
            if uploaded:
                logger.info(
                    "[amo] 景区知识库 %s 已同步，共 %d 个景区（分 %d 批）"
                    % (name, len(lines), len(parts))
                )
            else:
                logger.warning(
                    "[amo] 景区知识库只成功 %d/%d 批：先沿用已有内容，6 小时内不再重试，"
                    "不会每次启动都刷这条" % (ok_parts, len(parts))
                )
                try:
                    self.db.kv_set(retry_key, now_ts() + 6 * 3600)
                except Exception as _amo_exc:
                    _note_swallowed("amo_kb.HumanCorePlugin._ensure_scenic_kb", _amo_exc)
        if adopted or uploaded:
            try:
                self.db.kv_set(key, fingerprint)
                self.db.kv_set(retry_key, 0)
            except Exception as _amo_exc:
                _note_swallowed("amo_kb.HumanCorePlugin._ensure_scenic_kb", _amo_exc)
    elif lines and synced == fingerprint:
        logger.debug("[amo] 景区知识库 %s 内容没变，跳过重传" % name)
    elif lines:
        logger.debug("[amo] 景区知识库 %s 上次上传失败，6 小时内不再重试" % name)



