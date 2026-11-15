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
        _diary_append_note,
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
        _diary_append_note,
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

    async def api_diary_note(self):
        await self._ensure_ready()
        payload = await self._body()
        day = _diary_parse_day((payload or {}).get("date"))
        note = str((payload or {}).get("note") or "").strip()
        if not day or not note:
            return {"code": 1, "msg": "需要日期和补记内容"}
        if not callable(_diary_append_note):
            return {"code": 1, "msg": "日记模块未加载"}
        try:
            path = _diary_append_note(self._diary_dir(), day, note)
        except Exception as exc:
            _note_swallowed("amo_api_diary.DiaryApiMixin.api_diary_note", exc)
            return {"code": 1, "msg": "追加失败: %s" % exc}
        if not path:
            return {"code": 1, "msg": "这天的日记还没定稿，不能补记"}
        return {"code": 0, "data": {"date": day.isoformat(), "path": path}}

    async def api_diary_tree(self):
        await self._ensure_ready()
        if not callable(_diary_tree):
            return {"code": 1, "msg": "日记模块未加载"}
        return {"code": 0, "data": await asyncio.to_thread(_diary_tree, self._diary_dir())}

    async def api_diary_read(self):
        await self._ensure_ready()
        payload = await self._body()
        day = _diary_parse_day((payload or {}).get("date") or self._q("date", ""))
        if not day:
            return {"code": 1, "msg": "日期格式应为 YYYY-MM-DD"}
        text = _diary_read(self._diary_dir(), day) if callable(_diary_read) else ""
        cleaner = getattr(self, "_clean_diary_text", None)
        if callable(cleaner):
            text = cleaner(text)
        day_text = day.isoformat()
        try:
            stale = bool(self.db.kv_get("diary_stale:" + day_text, False))
        except Exception:
            stale = False
        locked = False
        try:
            locked = bool(self.db.kv_get("diary_locked:" + day_text, False))
        except Exception:
            locked = False
        return {"code": 0, "data": {"date": day_text, "text": text, "stale": stale,
                                    "locked": locked}}

    def write_diary_text(self, day, text, force=False):
        """把给定文本写进长日记（金融事件用；不调模型）。

        返回 {"code":0,"data":{"date","path"}} 或 {"code":1,"msg"}。
        """
        body = str(text or "").strip()
        cleaner = getattr(self, "_clean_diary_text", None)
        if callable(cleaner):
            body = cleaner(body)
        try:
            from .amo_diary_guard import sanitize_diary
        except ImportError:
            from amo_diary_guard import sanitize_diary
        body = sanitize_diary(body)
        if not body:
            return {"code": 1, "msg": "空的日记"}
        try:
            target = _diary_parse_day(day if isinstance(day, str) else getattr(day, "isoformat", lambda: "")())
        except Exception:
            target = None
        if target is None:
            return {"code": 1, "msg": "日期不合法"}
        if not force and self.db.kv_get("diary_locked:" + target.isoformat(), False):
            return {"code": 1, "msg": "今天的大日记已经定稿了，不再重写"}
        if target < _now().date() and self.db.kv_get("diary_written:" + target.isoformat()):
            return {"code": 1, "msg": "历史日记已定稿，只读"}
        try:
            path = _diary_write(self._diary_dir(), target, body)
        except Exception as exc:
            _note_swallowed("amo_api_diary.DiaryApiMixin.write_diary_text", exc)
            return {"code": 1, "msg": "写入失败: %s" % exc}
        try:
            day_text = target.isoformat()
            self.db.kv_set("diary_written:" + day_text, path)
            self.db.kv_set("diary_version:" + day_text, self.db.schedule_version(day_text))
            self.db.kv_set("diary_stale:" + day_text, False)
            try:
                self.db.kv_set("diary_mood:" + day_text, self.state.mood_score())
            except Exception as exc:
                _note_swallowed("amo_api_diary.py.149", exc)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_diary.DiaryApiMixin.write_diary_text", _amo_exc)
        logger.info("[amo] 日记已写（外部文本）: %s" % path)
        return {"code": 0, "data": {"date": target.isoformat(), "path": path}}

    async def api_diary_write(self):
        await self._ensure_ready()
        payload = await self._body()
        force = bool((payload or {}).get("force"))
        day = _diary_parse_day((payload or {}).get("date"))
        today = _now().date()
        if day is not None and day == today:
            # 22:30 前点开日记卡：不许临时调模型现编；force=True 是明确要求重写今天
            gate = getattr(self, "_diary_gate_open", None)
            if callable(gate) and not gate(_now()) and not force:
                return {"code": 1, "msg": "她今天还在过呢，大日记还没开始写哦。"}
            if self.db.kv_get("diary_locked:" + day.isoformat(), False) and not force:
                text = _diary_read(self._diary_dir(), day) if callable(_diary_read) else ""
                return {"code": 0, "data": {"date": day.isoformat(), "text": text,
                                            "locked": True, "msg": "今天的大日记已经定稿了"}}
            if force:
                # 明确重写：先解开定稿锁，避免生成完又被当成“已定稿”挡住
                for key in ("diary_locked:", "diary_written:"):
                    try:
                        self.db.kv_set(key + day.isoformat(), False)
                    except Exception as exc:
                        _note_swallowed("amo_api_diary.force_unlock", exc)
                try:
                    self.db.kv_set("diary_stale:" + day.isoformat(), False)
                except Exception as exc:
                    _note_swallowed("amo_api_diary.force_unlock", exc)
            day, error = day, ""
        else:
            day, error = diary_manual_target(_now(), day)
        if error:
            return {"code": 1, "msg": error}
        if day < _now().date() and self.db.kv_get("diary_written:" + day.isoformat()):
            return {"code": 1, "msg": "历史日记已定稿，只读"}
        text = await self._generate_daily_diary(day)
        cleaner = getattr(self, "_clean_diary_text", None)
        if callable(cleaner):
            text = cleaner(text)
        try:
            from .amo_diary_guard import rewrite_together_chat, sanitize_diary
        except ImportError:
            from amo_diary_guard import rewrite_together_chat, sanitize_diary
        try:
            _together = bool(self.db.kv_get("diary_together:" + day.isoformat(), False))
        except Exception:
            _together = False
        text = sanitize_diary(text, together=_together)
        if _together:
            text = rewrite_together_chat(text)
        if not text:
            return {"code": 1, "msg": "日记生成失败（没有素材或模型没返回）"}
        try:
            path = _diary_write(self._diary_dir(), day, text)
        except Exception as exc:
            _note_swallowed("amo_api_diary.DiaryApiMixin.api_diary_write", exc)
            return {"code": 1, "msg": "写入失败: %s" % exc}
        try:
            day_text = day.isoformat()
            self.db.kv_set("diary_locked:" + day_text, True)
            self.db.kv_set("diary_written:" + day_text, path)
            self.db.kv_set("diary_version:" + day_text, self.db.schedule_version(day_text))
            self.db.kv_set("diary_stale:" + day_text, False)
            try:
                self.db.kv_set("diary_mood:" + day_text, self.state.mood_score())
            except Exception as exc:
                _note_swallowed("amo_api_diary.py.188", exc)
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
                getattr(os, "startfile")(root)  # 只有 Windows 有，Linux 类型桩里没有
                opened = True
        except Exception as exc:
            logger.debug("[amo] 打开日记文件夹失败: %s" % exc)
        return {"code": 0, "data": {"root": root, "opened": opened}}
