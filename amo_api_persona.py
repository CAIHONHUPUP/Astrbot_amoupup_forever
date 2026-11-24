# -*- coding: utf-8 -*-
"""人设面板 Web API（2026-11-21；2026-11-24 A6 增加自动进化可见/开关/撤销）。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

try:
    from . import amo_persona_store as _store
    from . import amo_persona_pack as _pack
    from . import amo_persona_evolve as _evolve
except ImportError:
    if not TYPE_CHECKING:
        import amo_persona_store as _store
        import amo_persona_pack as _pack
        import amo_persona_evolve as _evolve


class PersonaApiMixin:
    if TYPE_CHECKING:
        _body: Any
        config: Any

    def _persona_status(self):
        active = _store.active_id(self)
        data = _store.load_pack(self, active)
        state = _store.load_state(self, active)
        candidates = [row.get("text") for row in (state.get("candidates") or [])
                      if isinstance(row, dict)]
        candidate_rows = [{
            "text": str(row.get("text") or ""),
            "source": str(row.get("source") or "manual"),
            "scene": str(row.get("scene") or ""),
            "mood": str(row.get("mood") or ""),
        } for row in (state.get("candidates") or []) if isinstance(row, dict)]
        out = {
            "active": active,
            "profiles": _store.profiles(self),
            "name": str(data.get("name") or ""),
            "core": str((data.get("card") or {}).get("core") or ""),
            "corpus_count": len(data.get("corpus") or []),
            # 旧字段保持不变（面板老版本仍能用）：纯文本候选列表。
            "candidates": candidates,
            # 新字段只增不改：来源标签 / 自动进化状态 / 最近自动入池。
            "candidate_rows": candidate_rows,
        }
        try:
            out.update(_evolve.status(self))
        except Exception:
            out.update({"auto_evolve": False, "auto_approve": False, "today_added": 0,
                        "pool_count": 0, "archive_count": 0, "pending_review": None,
                        "recent_auto": []})
        return out

    async def api_persona_status(self):
        return {"code": 0, "data": self._persona_status()}

    async def api_persona_switch(self):
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        out = _store.switch(self, str(data.get("id") or ""))
        if not out.get("ok"):
            return {"code": 1, "msg": "没有这个人设", "data": self._persona_status()}
        return {"code": 0, "data": self._persona_status()}

    async def api_persona_import(self):
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        pack_data = data.get("pack")
        if not isinstance(pack_data, dict):
            return {"code": 1, "msg": "缺少 pack 对象"}
        out = _store.import_pack(self, pack_data)
        if not out.get("ok"):
            msg = "人设包不合法" if out.get("reason") == "invalid" else "这个 id 已存在"
            return {"code": 1, "msg": msg, "error": out.get("error", ""),
                    "data": self._persona_status()}
        return {"code": 0, "msg": "已导入 %s" % out.get("id"),
                "data": self._persona_status()}

    async def api_persona_approve(self):
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        try:
            index = int(data.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        out = _store.approve_candidate(self, index)
        if not out.get("ok"):
            return {"code": 1, "msg": "没有这条候选", "data": self._persona_status()}
        return {"code": 0, "msg": "已通过", "data": self._persona_status()}

    async def api_persona_export(self):
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        pid = str(data.get("id") or _store.active_id(self))
        if pid not in _store.profiles(self):
            return {"code": 1, "msg": "没有这个人设"}
        return {"code": 0, "data": {"pack": _store.load_pack(self, pid),
                                    "state": _store.load_state(self, pid)}}

    async def api_persona_preview(self):
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        scene = str(data.get("scene") or "")
        mood = str(data.get("mood") or "")
        pack_data = _store.active_pack(self)
        try:
            state = _store.load_state(self)
            rows = _pack.retrieve_with_sources(pack_data, scene=scene, mood=mood, limit=3,
                                               last_used=state.get("last_used") or {})
            block = _pack.prompt_block(pack_data, scene=scene, mood=mood,
                                       last_used=state.get("last_used") or {})
            learned = _evolve.learned_style_samples(self, "%s %s" % (scene, mood), mark=False)
        except Exception as exc:
            return {"code": 1, "msg": "预览失败: %s" % exc}
        return {"code": 0, "data": {
            "prompt": block,
            "samples": [row["text"] for row in rows],
            # A6：标注哪几条来自通用/手动语料，哪 2 条来自她自己的池。
            "sample_sources": ["通用/进化" if row.get("generic") else "场景语料" for row in rows],
            "learned": learned,
        }}

    async def api_persona_auto(self):
        """面板开关：A 阶段只开/关采集总开关；自动通过默认关，代码只提供开关。"""
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        kind = str(data.get("key") or "auto_evolve")
        key = "persona_auto_approve" if kind == "auto_approve" else "persona_auto_evolve"
        enabled = bool(data.get("enabled"))
        try:
            self.config[key] = enabled
            if hasattr(self.config, "save_config"):
                self.config.save_config()
        except Exception as exc:
            return {"code": 1, "msg": "保存开关失败：%s" % exc,
                    "data": self._persona_status()}
        return {"code": 0, "msg": ("已开启 " if enabled else "已暂停 ") + key,
                "data": self._persona_status()}

    async def api_persona_undo(self):
        """撤销最近一条自动入池（A6/B3）。"""
        data = await self._body()
        data = data if isinstance(data, dict) else {}
        try:
            index = int(data.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        out = _evolve.undo_last_auto(self, index)
        if not out.get("ok"):
            return {"code": 1, "msg": "没有可撤销的自动进化", "data": self._persona_status()}
        return {"code": 0, "msg": "已撤销：%s" % out.get("text", ""),
                "data": self._persona_status()}
