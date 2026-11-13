# -*- coding: utf-8 -*-
"""穿搭/姿势面板 API：从 amo_api.py 拆出的 handler mixin。"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .amo_shared import logger
else:
    try:
        from .amo_shared import logger
    except ImportError:
        from amo_shared import logger

try:
    from .pose_library import batch_status as pose_batch_status
    from .pose_library import ensure_batch as pose_ensure_batch
    from .pose_library import pending_poses as pose_pending
except ImportError:
    try:
        from pose_library import batch_status as pose_batch_status
        from pose_library import ensure_batch as pose_ensure_batch
        from pose_library import pending_poses as pose_pending
    except Exception:
        def pose_batch_status():
            return {}
        async def pose_ensure_batch(plugin, poses, force=False):
            return {"ok": False}
        def pose_pending(library):
            return []


class WardrobeApiMixin:
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
