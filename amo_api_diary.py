# -*- coding: utf-8 -*-
"""日记面板 API：从 amo_api.py 拆出的 handler mixin。"""
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:
        from amo_observability import note as _note_swallowed

try:
    from .amo_shared import (
        _diary_import_folder,
        _diary_parse_day,
        _diary_read,
        _diary_tree,
        _diary_write,
        _now,
        logger,
    )
except ImportError:
    from amo_shared import (
        _diary_import_folder,
        _diary_parse_day,
        _diary_read,
        _diary_tree,
        _diary_write,
        _now,
        logger,
    )

try:
    from .amo_schedule import diary_manual_target
except ImportError:
    try:
        from amo_schedule import diary_manual_target
    except Exception:  # pragma: no cover
        def diary_manual_target(now, requested):
            return requested, ""


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object


class DiaryApiMixin(_MixinBase):
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
        day_text = day.isoformat()
        try:
            stale = bool(self.db.kv_get("diary_stale:" + day_text, False))
        except Exception:
            stale = False
        return {"code": 0, "data": {"date": day_text, "text": text, "stale": stale}}

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
            _note_swallowed("amo_api_diary.DiaryApiMixin.api_diary_write", exc)
            return {"code": 1, "msg": "写入失败: %s" % exc}
        try:
            day_text = day.isoformat()
            self.db.kv_set("diary_written:" + day_text, path)
            self.db.kv_set("diary_version:" + day_text, self.db.schedule_version(day_text))
            self.db.kv_set("diary_stale:" + day_text, False)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_diary.DiaryApiMixin.api_diary_write", _amo_exc)
        logger.info("[amo] 日记已写（手动）: %s" % path)
        return {"code": 0, "data": {"date": day.isoformat(), "path": path, "text": text}}

    async def api_diary_open(self):
        await self._ensure_ready()
        root = self._diary_dir()
        try:
            os.makedirs(root, exist_ok=True)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_diary.DiaryApiMixin.api_diary_open", _amo_exc)
        opened = False
        try:
            if os.name == "nt" and os.path.isdir(root):
                os.startfile(root)
                opened = True
        except Exception as exc:
            logger.debug("[amo] 打开日记文件夹失败: %s" % exc)
        return {"code": 0, "data": {"root": root, "opened": opened}}
