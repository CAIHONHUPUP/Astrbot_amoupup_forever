# -*- coding: utf-8 -*-
"""能力层（MemoryHub）与「能记」写入：amo 自己的记忆入口只走这一条。

不存任何本地记忆，也不再有"退回外挂记忆插件"的分支——记忆只有两个去处：
能力层（MemoryHub，内部就是 Astrbot_amoupup_data）和它的 mem 门面。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

import re

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        MemoryHub,
        logger,
        to_int,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            MemoryHub,
            logger,
            to_int,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                MemoryHub,
                logger,
                to_int,
            )

if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class BridgeMixin(_MixinBase):
    def _memory_hub(self):
        hub = getattr(self, "_mem_hub", None)
        if hub is not None:
            return hub
        if MemoryHub is None:
            return None
        try:
            hub = MemoryHub(
                self._memory_runtime(),
                buffer_size=max(8, self.cfg_int("ctx_window", 48)),
            )
        except Exception as exc:
            logger.warning("[amo] 记忆能力层初始化失败: %s" % exc)
            return None
        self._mem_hub = hub
        return hub

    def _memory_runtime(self):
        star = self

        class _Runtime:
            logger = logger

            @staticmethod
            def cfg(key, default=None):
                try:
                    data = star.config
                    if isinstance(data, dict):
                        return data.get(key, default)
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory_bridge.BridgeMixin._memory_runtime._Runtime.cfg", _amo_exc)
                return default

            @staticmethod
            def bot_id():
                return star._data_bot_id()

            @staticmethod
            def get_plugin(names):
                return star._plugin_by_names(names)

            @staticmethod
            def normalize_memories(rows):
                return star._normalize_memories(rows)

        return _Runtime()

    async def generate_memory_text(self, event, state_context=None):
        """统一记忆生成管道：硬校验 → 状态前缀 → （可选）LLM 口语化；冲突直接拦掉。"""
        try:
            from .amo_state_context import (StateConflict, build_state_context,
                                            format_event_memory, memory_prefix)
            from .amo_state_snapshot import blacklist_hit
        except ImportError:
            from amo_state_context import (StateConflict, build_state_context,
                                           format_event_memory, memory_prefix)
            from amo_state_snapshot import blacklist_hit
        ctx = state_context or build_state_context(self)
        try:
            fact = format_event_memory(event, ctx)
        except StateConflict as exc:
            logger.warning("[amo] 记忆写入拦截（状态冲突）：%s" % exc)
            raise
        text = str((event or {}).get("text") or "")
        llm = getattr(self, "llm", None)
        if llm is None:
            return fact
        where = {"home": "在家", "office": "在公司", "road": "在路上"}.get(
            str(ctx.get("zone") or ""), "在家")
        health = "生病休息" if ctx.get("is_sick") else "状态正常"
        prompt = (
            "你目前" + where + "（" + health + "）。以下是后台触发的随机小事件：[" + text + "]。\n"
            "请立刻审查：如果该事件与你当前的物理位置或健康状态冲突，只回复 DROP；\n"
            "如果不冲突，请转换成符合你当前状态的口语化表达（30 字以内，第一人称），"
            "并以 " + memory_prefix(ctx) + " 开头，只输出这一句。"
        )
        try:
            line = await llm.chat("", "你就是这个角色本人，只输出要写进记忆的一句话。", prompt, limit=80)
        except Exception:
            return fact
        line = str(line or "").strip()
        if not line or "DROP" in line.upper():
            logger.warning("[amo] 记忆写入拦截（模型判定冲突）：%s" % text[:60])
            return ""
        if blacklist_hit(line, str(ctx.get("zone") or "")):
            return fact
        return line if line.startswith("[") else (memory_prefix(ctx) + " " + line)

    async def _audit_memories(self):
        """凌晨审查：把当天和当前物理状态矛盾的记忆物理删除。"""
        try:
            from .amo_state_context import build_state_context
            from .amo_state_snapshot import blacklist_hit
        except ImportError:
            from amo_state_context import build_state_context
            from amo_state_snapshot import blacklist_hit
        uid = str(getattr(self, "_last_uid", "") or "")
        lister = getattr(self, "_data_mem_list", None)
        if not uid or not callable(lister):
            return 0
        ctx = build_state_context(self)
        zone = str(ctx.get("zone") or "")
        if zone != "home":
            return 0
        try:
            rows = await lister(uid, "", 80)
        except Exception as exc:
            logger.debug("[amo] 记忆审查读取失败: %s" % exc)
            return 0
        deleted = 0
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            content = str(row.get("content") or "")
            mid = int(row.get("id") or 0)
            if not content or not mid:
                continue
            # 只审事件流水；日记摘要/里程碑是"她说过的话"，不能按地点词误删。
            kind = str(row.get("kind") or "")
            if kind not in ("event",) and not content.startswith(
                    ("[生活小事]", "[在家", "[生病在家", "[在公司]", "[在路上]")):
                continue
            if not blacklist_hit(content, zone):
                continue
            try:
                if await self._data_call("delete_memory", self._data_bot_id(), mid):
                    deleted += 1
                    logger.warning("[amo] 记忆审查删除冲突记录 #%d：%s" % (mid, content[:60]))
            except Exception:
                continue
        return deleted

    async def _write_memory_checked(self, uid, content, kind="fact", tier=1,
                                    scope="private", group_id="", subject="user", platform=""):
        """写一条记忆并返回是否真的成功；subject=user/bot 区分主体。"""
        try:
            from .amo_intimacy import is_explicit as _is_explicit
        except ImportError:
            from amo_intimacy import is_explicit as _is_explicit
        if str(kind or "") in ("daily_digest", "milestone", "summary") and _is_explicit(content):
            logger.info("[amo] 露骨内容不进日记/里程碑/总结记忆")
            return False
        # 群聊隔离：群友闲聊不进长期记忆，只有老公在群里说的话才允许记。
        _is_owner = getattr(self, "_is_registered_lover", None)
        if (scope == "group" or group_id) and not (_is_owner(uid) if callable(_is_owner) else False):
            logger.debug("[amo] 群聊记忆已隔离（非主人）: %s" % str(content)[:40])
            return False
        # 2026-11-22 task-25：摘要/里程碑/记忆写者统一过场景守卫。只改「现在/此刻」的
        # 地点断言（昨天/以前/说他的句子原样保留），避免「病假在家」被写成「现在在公司」
        # 再回灌进长期记忆；全句都违规时宁可不写这一条。
        try:
            try:
                from .amo_scene_ledger import guard_memory_text as _guard_memory
            except ImportError:
                from amo_scene_ledger import guard_memory_text as _guard_memory
            _guarded = str(_guard_memory(self, content) or "")
            if _guarded != str(content or ""):
                logger.info("[amo] 记忆场景守卫改写：%r -> %r",
                            str(content or "")[:60], _guarded[:60])
                if not _guarded.strip():
                    logger.warning("[amo] 记忆整条与此刻场景冲突，本次不写: %s",
                                   str(content or "")[:60])
                    return False
                content = _guarded
        except Exception as _amo_exc:
            _note_swallowed("amo_memory_bridge.BridgeMixin._write_memory_checked.scene_guard",
                            _amo_exc)
        try:
            try:
                from .amo_memory_ledger import guard_memory_write, record_memory_facts
            except ImportError:
                from amo_memory_ledger import guard_memory_write, record_memory_facts
            content, _guard = guard_memory_write(
                self, uid, content, kind=kind, subject=subject, source="memory")
            if not content:
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_memory_bridge.BridgeMixin._write_memory_checked.guard", _amo_exc)
        hub = self._memory_hub()
        level = to_int(tier, 1)
        importance = 0.75 if level >= 2 else (0.55 if level == 1 else 0.35)
        if hub is not None and hub.writable():
            try:
                result = await hub.remember(
                    str(uid), content, str(kind or "fact"), importance,
                    scope=str(scope or "private"), group_id=str(group_id or ""),
                    subject=str(subject or "user"),
                )
            except Exception as exc:
                logger.warning("[amo] 记忆写入异常: %s" % exc)
                result = {"ok": False}
            if isinstance(result, dict) and result.get("ok"):
                try:
                    record_memory_facts(
                        self, uid, content,
                        memory_id=int((result or {}).get("memory_id") or 0),
                        source="memory",
                    )
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory_bridge.BridgeMixin._write_memory_checked.facts", _amo_exc)
                return True
            logger.warning(
                "[amo] 记忆没写进去（%s）: %s" % ((result or {}).get("reason", "?"), str(content)[:40])
            )
            return False
        if self._data_mem() is not None:
            try:
                result = await self._data_memory_write(
                    uid, content, kind, tier, scope, group_id, subject=subject, platform=platform
                )
            except Exception as _amo_exc:
                _note_swallowed("amo_memory_bridge.BridgeMixin._write_memory_checked", _amo_exc)
                result = False
            if result:
                try:
                    record_memory_facts(self, uid, content, source="memory")
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory_bridge.BridgeMixin._write_memory_checked.facts", _amo_exc)
                return True
        return False

    def _external_memory_write(self, uid, content, kind="fact", tier=1,
                               scope="private", group_id="", subject="user", platform=""):
        """把一条记忆交给「能记」；subject=user/bot 区分主体。

        陪伴自己不存记忆，只负责把要记的东西丢过去；写不进去就返回 False，
        调用方（命令/面板）会如实告诉用户。
        """
        content = re.sub(r"\s+", " ", str(content or "")).strip()
        if not content:
            return False
        try:
            try:
                from .amo_memory_ledger import guard_memory_write, record_memory_facts
            except ImportError:
                from amo_memory_ledger import guard_memory_write, record_memory_facts
            content, _guard = guard_memory_write(
                self, uid, content, kind=kind, subject=subject, source="memory")
            if not content:
                return False
        except Exception as _amo_exc:
            _note_swallowed("amo_memory_bridge.BridgeMixin._external_memory_write.guard", _amo_exc)
        hub = self._memory_hub()
        if hub is not None and hub.writable():
            level = to_int(tier, 1)
            importance = 0.75 if level >= 2 else (0.55 if level == 1 else 0.35)
            try:
                self._spawn(hub.remember(
                    uid, content, str(kind or "fact"), importance,
                    scope=str(scope or "private"), group_id=str(group_id or ""),
                    subject=str(subject or "user"),
                ))
                try:
                    record_memory_facts(self, uid, content, source="memory")
                except Exception as _amo_exc:
                    _note_swallowed("amo_memory_bridge.BridgeMixin._external_memory_write.facts", _amo_exc)
                logger.debug("[amo] 记忆已交给能力层: %s" % content[:40])
                return True
            except Exception as exc:
                logger.debug("[amo] 能力层写入失败，改走 mem 门面: %s" % exc)
        mem = self._data_mem()
        if mem is not None and callable(getattr(mem, "remember", None)):
            coro = None
            try:
                coro = self._data_memory_write(
                    uid, content, kind, tier, scope, group_id, subject=subject, platform=platform,
                )
                if self._spawn(coro) is not None:
                    logger.debug("[amo] 记忆已交给记忆终端: %s" % content[:40])
                    return True
            except Exception as exc:
                if coro is not None:
                    coro.close()
                logger.debug("[amo] 记忆终端写入失败: %s" % exc)
        return False
