# -*- coding: utf-8 -*-
"""旅游面板 API（一期 10A）：状态、手动出发、取消。

只做入口，不在这里碰日程/JIT/钱包/记忆；那些接线留给 10B。
写入路径（start/cancel）已经在 amo_routes.WRITE_ROUTES 里只收 POST 并过 CSRF 守卫。
Web 请求没有 event，所以主人限制沿用守卫，来源写进日志便于事后追溯。
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

if TYPE_CHECKING:
    from .amo_shared import logger, to_int, web_request
else:
    try:
        from .amo_shared import logger, to_int, web_request
    except ImportError:
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import logger, to_int, web_request

if TYPE_CHECKING:
    from . import amo_trip as trip
else:
    try:
        from . import amo_trip as trip
    except ImportError:
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            import amo_trip as trip

if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO
    _MixinBase = object


_TRUTHY = ("1", "true", "yes", "on", "是")


def _truthy(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in _TRUTHY


def _start_error_text(result: dict[str, Any]) -> str:
    """把引擎的 reason 翻成一句人话；认不出的 reason 也给可继续操作的话。"""
    reason = str(result.get("reason") or "")
    if reason == "disabled":
        return "旅游功能没开（travel_enabled=false），先去设置里打开吧。"
    if reason == "trip_active":
        return "已经有一次旅行在计划里或路上了，先别急着再出发。"
    if reason == "cooldown":
        return "刚旅游回来，还在冷却期（travel_cooldown_days）里，过几天再说吧。"
    if reason == "city_not_in_pool":
        pool = [str(city) for city in (result.get("candidates") or []) if str(city)]
        if pool:
            return "她不太熟这个城市，从这几个里挑一个吧：" + "、".join(pool)
        return "她不太熟这个城市，换一个景区城市吧。"
    if reason == "no_candidate":
        return "景区名单里还没有可选的城市，先重建一下景区知识库吧。"
    if reason == "budget":
        return "钱不够：这趟至少要 %d 元，现在只有 %d 元。" % (
            to_int(result.get("need"), 0), to_int(result.get("balance"), 0))
    return "这次没出发成，再试一次看看。"


def _cancel_error_text(result: dict[str, Any]) -> str:
    if not str(result.get("phase") or ""):
        return "现在没有在计划的旅行，不用取消。"
    return "已经出发了，取消不了；想回来就跟她说「回来吧」。"


class TripApiMixin(_MixinBase):
    def _trip_source(self) -> str:
        """写请求的来源摘要：Web 没有 event，拦不住就至少留痕。"""
        parts: list[str] = []
        try:
            if web_request is not None:
                method = str(getattr(web_request, "method", "") or "")
                if method:
                    parts.append("method=" + method)
                headers = getattr(web_request, "headers", None)
                if headers is not None:
                    for name in ("origin", "referer", "x-forwarded-for", "x-real-ip"):
                        value = str(headers.get(name) or "").strip()
                        if value:
                            parts.append(name + "=" + value)
        except Exception as _amo_exc:
            _note_swallowed("amo_api_trip.TripApiMixin._trip_source", _amo_exc)
        return " ".join(parts) or "unknown"

    async def api_trip_status(self) -> dict[str, Any]:
        await self._ensure_ready()
        await self._body()
        return {"code": 0, "data": trip.status(self)}

    async def api_trip_start(self) -> dict[str, Any]:
        await self._ensure_ready()
        data = await self._body()
        city = str(data.get("city") or self._q("city", "") or "").strip()
        raw_days = data.get("days")
        if raw_days is None:
            raw_days = self._q("days", 0)
        days = to_int(raw_days, 0)
        force = _truthy(data.get("force")) or _truthy(self._q("force", ""))
        logger.info("[trip] web 手动出发 来源=%s city=%s days=%s force=%s",
                    self._trip_source(), city, days, force)
        result = trip.trip_manual_start(self, city=city, days=days or None, force=force)
        status = trip.status(self)
        if not result.get("ok"):
            logger.info("[trip] web 手动出发被拒 reason=%s", result.get("reason"))
            return {"code": 1, "msg": _start_error_text(result), "data": status}
        return {"code": 0, "msg": "出发去%s啦。" % (status.get("city") or city),
                "data": status}

    async def api_trip_cancel(self) -> dict[str, Any]:
        await self._ensure_ready()
        await self._body()
        logger.info("[trip] web 取消旅游 来源=%s", self._trip_source())
        result = trip.cancel(self)
        status = trip.status(self)
        if not result.get("ok"):
            return {"code": 1, "msg": _cancel_error_text(result), "data": status}
        return {"code": 0, "msg": "旅游取消啦，还没出发，随时可以再计划。",
                "data": status}
