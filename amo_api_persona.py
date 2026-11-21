# -*- coding: utf-8 -*-
"""人设面板 Web API（2026-11-21）。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

try:
    from . import amo_persona_store as _store
    from . import amo_persona_pack as _pack
except ImportError:
    if not TYPE_CHECKING:
        import amo_persona_store as _store
        import amo_persona_pack as _pack


class PersonaApiMixin:
    if TYPE_CHECKING:
        _body: Any

    def _persona_status(self):
        active = _store.active_id(self)
        data = _store.load_pack(self, active)
        state = _store.load_state(self, active)
        return {
            "active": active,
            "profiles": _store.profiles(self),
            "name": str(data.get("name") or ""),
            "core": str((data.get("card") or {}).get("core") or ""),
            "corpus_count": len(data.get("corpus") or []),
            "candidates": [row.get("text") for row in (state.get("candidates") or [])
                           if isinstance(row, dict)],
            "evolved": len(state.get("corpus_evolved") or []),
        }

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
            block = _pack.prompt_block(pack_data, scene=scene, mood=mood)
        except Exception as exc:
            return {"code": 1, "msg": "预览失败: %s" % exc}
        return {"code": 0, "data": {"prompt": block,
                                    "samples": _pack.retrieve(pack_data, scene=scene,
                                                              mood=mood, limit=3)}}
