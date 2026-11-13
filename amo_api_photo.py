# -*- coding: utf-8 -*-
"""照片/画图面板 API：从 amo_api.py 拆出的 handler mixin。"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .amo_shared import logger, now_ts
else:
    try:
        from .amo_shared import logger, now_ts
    except ImportError:
        from amo_shared import logger, now_ts


class PhotoApiMixin:
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
