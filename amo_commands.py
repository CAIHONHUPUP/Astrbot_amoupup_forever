# -*- coding: utf-8 -*-
"""CommandMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

import re

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import snapshot as _swallowed_snapshot
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import snapshot as _swallowed_snapshot
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import snapshot as _swallowed_snapshot

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .clock import from_timestamp as _clock_from_ts
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .clock import from_timestamp as _clock_from_ts
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from clock import from_timestamp as _clock_from_ts

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_scope import event_scope as _event_scope
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_scope import event_scope as _event_scope
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_scope import event_scope as _event_scope

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        AstrMessageEvent, Comp, MessageChain, PLUGIN_VERSION, RELATIONS, SCHEDULE_VERSION, city_or_empty, clamp, day_meta, filter, logger, now_ts, to_float, to_int, today_str,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            AstrMessageEvent, Comp, MessageChain, PLUGIN_VERSION, RELATIONS, SCHEDULE_VERSION, city_or_empty, clamp, day_meta, filter, logger, now_ts, to_float, to_int, today_str,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                AstrMessageEvent, Comp, MessageChain, PLUGIN_VERSION, RELATIONS, SCHEDULE_VERSION, city_or_empty, clamp, day_meta, filter, logger, now_ts, to_float, to_int, today_str,
            )


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .schedule_change import apply_at as apply_sched_at, infer_place_category
else:
    try:
        from .schedule_change import apply_at as apply_sched_at, infer_place_category
    except ImportError:  # 直接按文件加载时
        try:
            from schedule_change import apply_at as apply_sched_at, infer_place_category
        except ImportError:
            apply_sched_at = None  # type: ignore[assignment]
            infer_place_category = None  # type: ignore[assignment]


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from . import amo_trip as trip
else:  # 运行时：旅游引擎（一期手动入口）
    try:
        from . import amo_trip as trip
    except ImportError:
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            import amo_trip as trip


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


#: 记忆后端（Astrbot_amoupup_data）连没连上，指令里要说人话。
#: 没装它的时候聊天照常、记忆却是空的，用户只会以为「记忆坏了」。
MEMORY_BACKEND_MISSING = (
    "🧠 记忆后端：未连接 —— 缺「我能永远记住你」(Astrbot_amoupup_data)，"
    "记忆写不进去；装好并启用它，几十秒内会自动接上，不用重启"
)
MEMORY_BACKEND_OK = "🧠 记忆后端：已连接 Astrbot_amoupup_data"


def memory_backend_line(connected):
    """`/amo status`、`/amo memories` 共用的一句话：记忆后端到底连上没有。"""
    return MEMORY_BACKEND_OK if connected else MEMORY_BACKEND_MISSING


class CommandMixin(_MixinBase):
    _status_cache: dict | None  # 与 ScheduleApiMixin 同一类型（/status 两秒缓存）
    #: 由 HumanCorePlugin 注入 / 其它 mixin 提供（仅类型声明，不改运行时）
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _jobs_report: Any
        _obs_health: Any
        protocol_info: Any
    housing: Any
    _ensure_home_images: Any

    def _args(self, event):
        raw = (event.message_str or "").strip()
        raw = re.sub(r"^[/／]\s*", "", raw)
        parts = raw.split()
        if parts and parts[0].lower() in ("amo", "陪伴", "amo_forever", "astrbot_amo_forever"):
            parts = parts[1:]
        return parts

    def _resolve_target(self, event, args, index):
        if Comp is not None:
            for comp in event.get_messages():
                if isinstance(comp, Comp.At):
                    target = str(getattr(comp, "qq", ""))
                    if target and target != "all" and target != str(event.get_self_id()):
                        return target
        if index < len(args):
            token = str(args[index]).strip().lstrip("@")
            if token in ("我", "me", "self"):
                return self._canonical_uid(event)
            if token:
                return token
        return self._canonical_uid(event)

    async def _mood_story(self, snap, umo):
        """情绪面板旁白/归因：10 分钟缓存，失败退回模板。"""
        import asyncio as _asyncio
        import datetime as _dt
        mood = dict(snap.get("mood") or {})
        top = sorted(mood.items(), key=lambda kv: float(kv[1] or 0), reverse=True)[:3]
        names = [k for k, _ in top]
        recent = ""
        try:
            rows = self._recent(umo, 6) if callable(getattr(self, "_recent", None)) else []
            recent = "；".join(str(r.get("content") or "")[:40] for r in rows[-6:])
        except Exception as _amo_exc:
            _note_swallowed("amo_commands.py.mood_recent", _amo_exc)
            recent = ""
        shared_line = ""
        try:
            act = self.db.kv_get("shared_activity", {}) or {}
            if act.get("status") == "active":
                shared_line = "正在进行：和老公一起%s（%s 开始）" % (
                    act.get("label") or "看电视",
                    _dt.datetime.fromtimestamp(float(act.get("started_at") or 0)).strftime("%H:%M"))
            else:
                last = self.db.kv_get("shared_activity_last", {}) or {}
                if last and now_ts() - float(last.get("at") or 0) < 7200:
                    shared_line = "刚发生：%s（%s）" % (
                        str(last.get("text") or ""),
                        _dt.datetime.fromtimestamp(float(last.get("at") or 0)).strftime("%H:%M"))
        except Exception as _amo_exc:
            _note_swallowed("amo_commands.py.mood_shared", _amo_exc)
            shared_line = ""
        sig = "|".join("%s:%d" % (k, int(float(v or 0)) // 10) for k, v in top)
        sig += "|" + str(abs(hash(recent + "||" + shared_line)) % 100000)
        now = _clock_from_ts(now_ts())
        try:
            cache = self.db.kv_get("mood_story_cache", {}) or {}
        except Exception:
            cache = {}
        if isinstance(cache, dict) and cache.get("sig") == sig:
            try:
                if (now - _dt.datetime.fromisoformat(cache["at"])).total_seconds() < 600:
                    return str(cache.get("narration") or ""), str(cache.get("attribution") or "")
            except Exception as exc:
                _note_swallowed("amo_commands.py.130", exc)
        activity = snap.get("activity") or {}
        place = str(activity.get("place") or "家")
        act = str(activity.get("activity") or "待着")
        health = snap.get("health") or {}
        period = snap.get("period") or {}
        fallback_narr = "今天%s，心里%s，%s。" % (
            "、".join(names[:2]) or "说不上来", "还算安稳" if float(snap.get("mood_score") or 50) >= 50 else "有点沉",
            "在%s%s" % (place, act) if place else "待着")
        fallback_attr = "主要是最近%s，所以情绪%s。" % (
            "、".join(names[:2]) or "没什么特别的事", "比较平稳" if float(snap.get("mood_score") or 50) >= 50 else "有点低")
        narration, attribution = fallback_narr, fallback_attr
        llm = getattr(self, "llm", None)
        if llm is not None and callable(getattr(llm, "chat_raw", None)) and umo:
            prompt = (
                "你在给情绪面板写一句第一人称内心独白和一句归因。当前最高情绪：%s；"
                "地点/动作：%s·%s；健康：%s；生理期：%s；"
                "最近聊天（从旧到新）：%s；%s。"
                "只输出两行：\n独白：...\n归因：...\n"
                "独白 30-50 字，自然口语，不要数字；归因 20-40 字，解释为什么，不要编造未发生的事。"
                "归因必须按最近发生的事写（优先最近聊天和上面的共同活动）；超过 2 小时的事不要再提，"
                "也不要把同一件事一直黏在归因里。"
                % ("、".join("%s%d" % (k, int(float(v or 0))) for k, v in top),
                   place, act, health.get("status") or "健康",
                   period.get("phase") or "非生理期", recent or "没有新聊天",
                   shared_line or "没有正在进行的共同活动")
            )
            try:
                text = await _asyncio.wait_for(
                    llm.chat_raw(umo, "你是她的内心独白，不是助手。", prompt), timeout=3.5)
                for line in str(text or "").splitlines():
                    line = line.strip()
                    if line.startswith("独白："):
                        narration = line[3:].strip() or narration
                    elif line.startswith("归因："):
                        attribution = line[3:].strip() or attribution
            except Exception as exc:
                _note_swallowed("amo_commands.py.169", exc)
        try:
            self.db.kv_set("mood_story_cache", {
                "sig": sig, "at": now.isoformat(timespec="seconds"),
                "narration": narration, "attribution": attribution,
            })
        except Exception as exc:
            _note_swallowed("amo_commands.py.176", exc)
        return narration, attribution

    def _mood_history(self, snap):
        import datetime as _dt
        history = []
        today = _clock_from_ts(now_ts()).date()
        for offset in range(6, -1, -1):
            day = today - _dt.timedelta(days=offset)
            score = None
            if offset == 0:
                score = float(snap.get("mood_score") or 50)
            else:
                try:
                    raw = self.db.kv_get("diary_mood:" + day.isoformat(), None)
                    if raw is not None:
                        score = float(raw)
                except Exception:
                    score = None
            if score is not None:
                history.append({"date": day.isoformat(), "score": round(score, 1)})
        return history

    async def _status_data(self, fresh=False):
        # P2：面板同一次刷新会先调 api_status 再调 api_get_status，旧实现把整份
        # 状态（天气/生理期/健康/计数聚合）算两遍。2 秒内复用同一份只读聚合；
        # 写状态/手动刷新会清缓存，fresh=True 强制重算。
        now = now_ts()
        cached = getattr(self, "_status_cache", None)
        if (not fresh and isinstance(cached, dict)
                and now - float(cached.get("at") or 0) <= 2.0):
            return cached.get("data")
        self._reload_config_if_changed()
        umo = self._last_umo or self._first_private_umo()
        if not self.state.weather:
            self.state.weather = self.state._simulate_weather()
        # /status 每 60 秒被面板轮询一次，必须是只读：以前城市名对不上就 force 刷天气
        # （烧和风额度），日程缺失就现生成（走模型）。都丢给后台。
        self._spawn(self.state.refresh_weather())
        if not self.state.worldview:
            self.state.worldview = self.state._fallback_world()
            self._spawn(self.state.ensure_worldview(umo))
        if (
            not self.state.schedule.get(today_str())
            or self.db.kv_get("schedule_version", "") != SCHEDULE_VERSION
            or not self.state.schedule_context(today_str())
        ):
            self._spawn(self.state.ensure_schedule(umo))
        snap = self.state.snapshot()
        activity = 0.0
        threshold = {"value": 0.0, "base": 0.0, "absolute": 0.0}
        if self.heartflow is not None:
            try:
                hf_threshold, hf_activity, hf_absolute = self.heartflow._get_dynamic_threshold(umo)
                threshold = {
                    "value": round(float(hf_threshold) * 100, 1),
                    "base": round(float(self.heartflow.reply_threshold) * 100, 1),
                    "absolute": round(float(hf_absolute), 1),
                }
                activity = float(hf_activity) * 100
            except Exception as exc:
                logger.debug("[amo] 心流阈值读取失败: %s" % exc)
        counts = self.db.counts()
        await _status_data_part(self, counts)
        result = {
            "time": snap["datetime_text"],
            "day": day_meta(),
            "mood": snap["mood"],
            "dominant": snap["dominant"],
            "mood_score": round(snap["mood_score"], 1),
            "period": snap["period"],
            "health": snap["health"],
            "weather": snap["weather"],
            "weather_text": snap["weather_text"],
            "activity": snap["activity"],
            "schedule": snap["schedule"],
            "schedule_tree": self.state.schedule_tree(),
            "worldview": self.state.worldview,
            "threshold": threshold,
            "activity_rate": round(activity, 2),
            "counts": counts,
            "jobs": self._jobs_report(),
            # Phase 17：结构化健康快照（plugin/version/uptime/db/jobs/counts）。
            # 老字段 health 是「人物健康」，语义不同，所以这里用 obs_health，不覆盖。
            "obs_health": self._obs_health(),
            # Phase 19：跨插件 feature API 协议摘要（只新增字段）
            "protocol": self.protocol_info(),
            "mood_narration": "",
            "mood_attribution": "",
            "mood_history": [],
            "config": {
                "enable_group_reply": self.cfg_bool("enable_group_reply", True),
                "enable_private_reply": self.cfg_bool("enable_private_reply", True),
                "auto_active_msg": self.cfg_bool("auto_active_msg", True),
                "enable_polish": self.cfg_bool("enable_polish", True),
                "heartflow": self.heartflow is not None,
                "segment_engine": self.segment_engine is not None,
                "weather_key_set": bool(self.cfg_str("hefeng_key", "")),
                "weather_host": self.cfg_str("weather_host", "https://devapi.qweather.com"),
                "version": PLUGIN_VERSION,
                "dialect_style": self._dialect_style(),
                # 面板靠这个判断「方言到底开没开」：dialect_style 可能还留着上次的方言名
                "dialect_enabled": self._dialect_active(),
                "dialect_count": len(self._dialect_rows()) if self._dialect_active() else 0,
                "companion_memory_available": self._data_connected(),
            },
            "data_plugin": {
                "name": "Astrbot_amoupup_data",
                "connected": self._data_connected(),
                "synced": bool(self._data_synced),
            },
        }
        try:
            result["mood_narration"], result["mood_attribution"] = await self._mood_story(snap, umo)
        except Exception as _amo_exc:
            _note_swallowed("amo_commands.HumanCorePlugin._status_data.mood_story", _amo_exc)
        try:
            result["mood_history"] = self._mood_history(snap)
        except Exception as _amo_exc:
            _note_swallowed("amo_commands.HumanCorePlugin._status_data.mood_history", _amo_exc)
        try:
            try:
                from .amo_trip_cost import settle_report as _settle_report
            except ImportError:
                from amo_trip_cost import settle_report as _settle_report
            result["trip_settle"] = _settle_report(self) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_commands.HumanCorePlugin._status_data.trip_settle", _amo_exc)
            result["trip_settle"] = {}
        self._status_cache = {"at": now_ts(), "data": result}
        return result

    def _format_status(self, data):
        mood = data["mood"]
        mood_line = " ".join([key + str(int(value)) for key, value in mood.items()])
        threshold = data["threshold"]
        lines = [
            "🌙 陪伴 · 当前状态",
            "🕒 " + str(data["time"]),
            "💛 心情：" + str(data["dominant"]) + "（" + mood_line + "）",
            "🩸 生理期：" + str(data["period"]["phase"]) + " 第" + str(data["period"]["day"]) + "天（" + str(data["period"]["tone"]) + "）",
            "🩺 身体：" + str(data["health"]["status"]),
            "🌦 天气：" + str(data["weather_text"]),
            "📅 正在：" + str(data["activity"].get("activity", "休息")) + "（" + str(data["activity"].get("place", "")) + "）",
            "🎚 心流阈值：" + str(threshold["value"]) + "（基础 " + str(threshold["base"]) + "，活跃度 " + str(data["activity_rate"]) + "%）",
            "📚 档案 " + str(data["counts"]["profiles"]) + " · 记忆 " + str(data["counts"]["memories"]) + " · 消息 " + str(data["counts"]["messages"]),
        ]
        # 记忆后端连没连上要写在状态里：没装能记的人只会看到「记忆 0」，然后以为功能坏了。
        plugin_state = data.get("data_plugin") or {}
        lines.append(memory_backend_line(bool(plugin_state.get("connected"))))
        settle = data.get("trip_settle") or {}
        if settle.get("line"):
            lines.append("🧳 旅游结算：" + str(settle.get("line")))
        if not city_or_empty(self):
            lines.append("📍 城市：未设置城市（不影响聊天，日程会写得笼统些）")
        return "\n".join(lines)

    async def _handle_command(self, event):
        args = self._args(event)
        sub = args[0].lower() if args else "help"
        uid = self._canonical_uid(event)
        if sub in ("help", "帮助", ""):
            return await self._cmd_help(event, args, uid)
        if sub in ("segment", "分段"):
            return await self._cmd_segment(event, args, uid)
        if sub in ("draw", "画图"):
            return await self._cmd_draw(event, args, uid)
        if sub in ("photo", "照片"):
            return await self._cmd_photo(event, args, uid)
        if sub == "status" or sub == "状态":
            return await self._cmd_status(event, args, uid)
        if sub == "mood" or sub == "心情":
            return await self._cmd_mood(event, args, uid)
        if sub == "profile" or sub == "档案":
            return await self._cmd_profile(event, args, uid)
        if sub == "memories" or sub == "记忆":
            return await self._cmd_memories(event, args, uid)
        if sub in ("rel", "关系"):
            return await self._cmd_rel(event, args, uid)
        if sub == "set" or sub == "设置":
            return await self._cmd_set(event, args, uid)
        if sub == "memo" or sub == "记住":
            return await self._cmd_memo(event, args, uid)
        if sub == "forget" or sub == "忘记":
            return await self._cmd_forget(event, args, uid)
        if sub == "period" or sub == "生理期":
            return await self._cmd_period(event, args, uid)
        if sub == "health" or sub == "健康":
            return await self._cmd_health(event, args, uid)
        if sub == "schedule" or sub == "日程":
            return await self._cmd_schedule(event, args, uid)
        if sub == "weather" or sub == "天气":
            return await self._cmd_weather(event, args, uid)
        if sub == "worldview" or sub == "世界观":
            return await self._cmd_worldview(event, args, uid)
        if sub == "threshold" or sub == "阈值":
            return await self._cmd_threshold(event, args, uid)
        if sub == "say" or sub == "说":
            return await self._cmd_say(event, args, uid)
        if sub == "clean" or sub == "清理":
            return await self._cmd_clean(event, args, uid)
        if sub in ("trip", "旅游"):
            return await self._cmd_trip(event, args, uid)
        if sub in ("audit", "审计", "日记审计"):
            return await self._cmd_audit(event, args, uid)
        return "未知指令。发送 /amo help 查看帮助。"
    async def _cmd_help(self, event, args, uid):
        """原 _handle_command 的「help」分支，逐行搬来。"""
        return (
            "🌙 陪伴 指令\n"
            "/amo status 状态｜/amo mood 九维心情\n"
            "/amo profile [@人] 档案｜/amo memories [@人] 记忆\n"
            "/amo period 生理期｜/amo health 健康｜/amo schedule 日程\n"
            "/amo weather 天气｜/amo worldview 世界观｜/amo threshold 阈值\n"
            "管理员：/amo rel @人 关系｜/amo set @人 字段 值｜/amo memo @人 内容 [0/1/2]（写入记忆插件）\n"
            "/amo period set YYYY-MM-DD｜/amo health set 状态\n"
            "/amo draw 描述（测试画图）｜/amo photo（照片概率/状态）\n"
            "/amo trip [start 城市 天数|status|cancel] 旅游\n"
            "/amo audit [YYYY-MM-DD] 日记质检（主人）\n"
            "/amo schedule regen｜/amo worldview regen｜/amo say 内容｜/amo clean"
        )

    async def _cmd_segment(self, event, args, uid):
        """原 _handle_command 的「segment」分支，逐行搬来。"""
        text = " ".join(args[1:]).strip()
        delay_type = self.cfg_str("seg_delay_type", "smart")
        if not text:
            return (
                "分段测试：/amo segment 一段文字\n"
                "使用完整本地分段引擎：按 seg_split_symbols 的符号列表断句\n"
                "保留标点：" + ("是" if self.cfg_bool("seg_keep_symbol", True) else "否") +
                "｜成对符号保护：" + ("开" if self.cfg_bool("seg_protect_paired_symbols", True) else "关") +
                "｜延迟：" + delay_type +
                "\n配置项：seg_split_symbols / seg_min_length / seg_max_length / seg_delay_type"
            )
        segments = self._segment(text)
        lines = ["分段结果（" + delay_type + "），共 " + str(len(segments)) + " 段"]
        for index, segment in enumerate(segments, 1):
            lines.append(str(index) + ". " + segment)
        if not segments:
            lines.append("（没有可发送的片段）")
        return "\n".join(lines)

    async def _cmd_draw(self, event, args, uid):
        """原 _handle_command 的「draw」分支，逐行搬来。"""
        engine = getattr(self, "draw", None)
        if engine is None:
            return "画图引擎未加载"
        text = " ".join(args[1:]).strip()
        return engine.manual(event, text)

    async def _cmd_photo(self, event, args, uid):
        """原 _handle_command 的「photo」分支，逐行搬来。"""
        engine = getattr(self, "photo", None)
        if engine is None:
            return "照片引擎未加载"
        tail = " ".join(args[1:]).strip().lower()
        if tail in ("test", "测试", "share"):
            return "照片测试：" + await engine.share_now("daily")
        if tail in ("travel", "shopping", "weekend", "daily", "outfit"):
            return "照片测试：" + await engine.share_now(tail)
        return engine.status_text()

    async def _cmd_status(self, event, args, uid):
        """原 _handle_command 的「status」分支，逐行搬来。"""
        return self._format_status(await self._status_data())

    async def _cmd_mood(self, event, args, uid):
        """原 _handle_command 的「mood」分支，逐行搬来。"""
        snap = self.state.snapshot()
        lines = ["🎨 陪伴的十维心情"]
        for key, value in snap["mood"].items():
            bar = "█" * int(round(value / 10)) + "░" * (10 - int(round(value / 10)))
            lines.append("%s %s %d" % (key, bar, int(value)))
        lines.append("整体偏「%s」，语气：%s" % (
            snap["dominant"], self.state.tone_text(snap, "普通")
        ))
        return "\n".join(lines)

    async def _cmd_profile(self, event, args, uid):
        """原 _handle_command 的「profile」分支，逐行搬来。"""
        target = self._resolve_target(event, args, 1)
        profile = self.db.get_profile(target)
        if not profile:
            return "还没有这个人的档案。多聊几句就会自动建立。"
        return "\n".join([
            "📁 档案 · " + (profile.get("name") or profile.get("uid")),
            "ID：" + str(profile.get("uid")),
            "称呼：" + str(profile.get("alias") or "未设置"),
            "生日：" + str(profile.get("birthday") or "未知"),
            "关系：" + str(profile.get("relation") or "普通"),
            "好感：" + str(profile.get("affection") or 0),
            "消息数：" + str(profile.get("msg_count") or 0),
            "记忆数：" + str(len(await self._all_memories(target, "", 200))),
            "备注：" + str(profile.get("notes") or "无"),
        ])

    async def _cmd_memories(self, event, args, uid):
        """原 _handle_command 的「memories」分支，逐行搬来。"""
        target = self._resolve_target(event, args, 1)
        if target != uid and not self._is_admin(event):
            return "只有管理员才能查看别人的记忆。"
        items = await self._all_memories(target, "", 20)
        if not items:
            # 空列表有两种原因：真的没记过，或者记忆后端压根没连上。
            # 不分清楚，用户会一直以为「她记不住我」是插件坏了。
            if self._data_connected():
                return "暂时没有记忆。"
            return "暂时没有记忆。\n" + memory_backend_line(False)
        lines = ["📖 记忆 · " + target]
        for item in items:
            tier = to_int(item.get("tier"), 0)
            label = "珍贵" if tier >= 2 else ("重要" if tier == 1 else "普通")
            expires = item.get("expires_at")
            if not expires:
                left = "永久"
            else:
                left = "%d天" % max(0, int((to_float(expires) - now_ts()) / 86400))
            lines.append("#%s [%s|%s] %s" % (item["id"], label, left, item["content"]))
        return "\n".join(lines)

    async def _cmd_rel(self, event, args, uid):
        """原 _handle_command 的「rel」分支，逐行搬来。"""
        if not self._is_admin(event):
            return "只有管理员才能调整关系。"
        if len(args) < 3:
            return "用法：/amo rel @人 仇恨|抵触|普通|熟识|相恋"
        target = self._resolve_target(event, args, 1)
        relation = args[2]
        if relation not in RELATIONS:
            return "关系只能是：" + "、".join(RELATIONS)
        self.db.update_profile(target, relation=relation)
        if relation == "相恋":
            self._register_lover(target)
        self._external_memory_write(target, "与角色的关系是" + relation, "relation", 2)
        return "✅ 已将 " + target + " 的关系设为「" + relation + "」"

    async def _cmd_set(self, event, args, uid):
        """原 _handle_command 的「set」分支，逐行搬来。"""
        if not self._is_admin(event):
            return "只有管理员才能修改档案。"
        if len(args) < 4:
            return "用法：/amo set @人 name|alias|birthday|notes|affection 值"
        target = self._resolve_target(event, args, 1)
        field = args[2]
        value: Any = " ".join(args[3:])
        if field == "relation":
            return "请用 /amo rel 设置关系。"
        if field == "birthday":
            match = re.match(r"^(\d{4}-)?(\d{1,2})-(\d{1,2})$", value)
            if not match:
                return "生日格式：MM-DD 或 YYYY-MM-DD"
            value = "%02d-%02d" % (to_int(match.group(2)), to_int(match.group(3)))
        if field == "affection":
            value = int(clamp(to_int(value), 0, 100))
        if field not in ("name", "alias", "birthday", "notes", "affection"):
            return "可修改字段：name、alias、birthday、notes、affection"
        self.db.update_profile(target, **{field: value})
        return "✅ 已更新 " + target + " 的 " + field

    async def _cmd_memo(self, event, args, uid):
        """原 _handle_command 的「memo」分支，逐行搬来。"""
        if not self._is_admin(event):
            return "只有管理员才能手动写入记忆。"
        if len(args) < 3:
            return "用法：/amo memo @人 内容 [0普通/1重要/2珍贵]"
        target = self._resolve_target(event, args, 1)
        content_parts = args[2:]
        tier = 1
        if content_parts and content_parts[-1] in ("0", "1", "2"):
            tier = to_int(content_parts[-1])
            content_parts = content_parts[:-1]
        content = " ".join(content_parts).strip()
        if not content:
            return "内容不能为空。"
        # 在群里写的记忆就留在群里：私聊里不该冒出群里的内容
        scope, group_id = _event_scope(event)
        if self._external_memory_write(target, content, "manual", tier, scope, group_id):
            return "✅ 已记住：" + content
        return "⚠️ 没能写进记忆插件：请先在设置里选好记忆来源插件。"

    async def _cmd_forget(self, event, args, uid):
        """原 _handle_command 的「forget」分支，逐行搬来。"""
        if not self._is_admin(event):
            return "只有管理员才能删除记忆。"
        if len(args) < 2:
            return "用法：/amo forget 记忆ID"
        return "记忆由外挂插件保存，请到该插件里删除；本插件不再单独存记忆。"

    async def _cmd_period(self, event, args, uid):
        """原 _handle_command 的「period」分支，逐行搬来。"""
        if len(args) >= 3 and args[1].lower() == "set":
            if not self._is_admin(event):
                return "只有管理员才能调整生理期。"
            if self.state.set_period_start(args[2]):
                return "✅ 生理期起始日已设为 " + args[2]
            return "日期格式应为 YYYY-MM-DD"
        period = self.state.period_info()
        return (
            "🩸 生理期\n阶段：" + period["phase"] + " 第" + str(period["day"]) + "天\n"
            "状态：" + period["tone"] + "\n"
            "疼痛：" + str(period["pain"]) + "/10\n"
            "周期：" + str(period.get("cycle_days", 28)) + "天｜经期：" + str(period.get("bleed_days", 5)) + "天\n"
            "距离下次：" + str(period["days_to_next"]) + "天（预计 " + str(period.get("next_start", "")) + "）\n"
            "起始日：" + self.state.period_start + "（/amo period set YYYY-MM-DD，或网页配置 period_start）"
        )

    async def _cmd_health(self, event, args, uid):
        """原 _handle_command 的「health」分支，逐行搬来。"""
        if len(args) >= 3 and args[1].lower() == "set":
            if not self._is_admin(event):
                return "只有管理员才能调整健康状态。"
            status = args[2]
            if status not in ("健康", "轻微不适", "感冒", "低烧", "疲惫"):
                return "状态：健康 / 轻微不适 / 感冒 / 低烧 / 疲惫"
            self.state.set_health(status, 24, "管理员设置")
            return "✅ 健康状态已设为 " + status
        health = self.state.health_info()
        return (
            "🩺 身体状态：" + health["status"] + "\n"
            + ("备注：" + health["note"] + "\n" if health.get("note") else "")
            + "恢复时间：" + (
                _clock_from_ts(health["until"]).strftime("%m-%d %H:%M")
                if health.get("until") else "—"
            )
        )

    async def _cmd_schedule(self, event, args, uid):
        """原 _handle_command 的「schedule」分支，逐行搬来。"""
        if len(args) >= 2 and args[1].lower() == "regen":
            if not self._is_admin(event):
                return "只有管理员才能重生成日程。"
            items = await self.state.ensure_schedule(self._last_umo, force=True)
            return "✅ 日程已重新生成，共 " + str(len(items)) + " 条。"
        items = self.state.schedule.get(today_str()) or []
        if not items:
            items = await self.state.ensure_schedule(self._last_umo)
        activity = self.state.current_activity()
        lines = ["📅 今日日程"]
        for item in items:
            lines.append("%s-%s %s（%s）" % (
                item.get("start", ""), item.get("end", ""),
                item.get("activity", ""), item.get("place", ""),
            ))
        lines.append("现在应该在：" + str(activity.get("activity", "")))
        return "\n".join(lines)

    async def _cmd_audit(self, event, args, uid):
        """/amo audit [YYYY-MM-DD]：日记质检摘要（主人）。"""
        if not self._is_admin(event):
            return "只有管理员才能看日记质检。"
        day = ""
        if len(args) >= 2:
            try:
                from .amo_shared import _diary_parse_day
            except ImportError:
                from amo_shared import _diary_parse_day
            parsed = _diary_parse_day(args[1])
            if parsed is None:
                return "日期格式应为 YYYY-MM-DD。"
            day = parsed.isoformat()
        try:
            try:
                from . import amo_diary_audit as audit
            except ImportError:
                import amo_diary_audit as audit
        except Exception as exc:
            _note_swallowed("amo_commands.CommandMixin._cmd_audit.import", exc)
            return "日记质检模块未加载。"
        return audit.report_text(self, day)

    async def _cmd_weather(self, event, args, uid):
        """原 _handle_command 的「weather」分支，逐行搬来。"""
        if len(args) >= 2 and args[1].lower() in ("refresh", "刷新"):
            await self.state.refresh_weather(force=True)
        weather = await self.state.refresh_weather()
        return (
            "🌦 " + str(weather.get("city", "")) + " " + str(weather.get("text", ""))
            + " " + str(weather.get("temp", "")) + "℃\n"
            + "湿度：" + str(weather.get("humidity", "")) + "%\n"
            + "风：" + str(weather.get("wind", "")) + "\n"
            + "来源：" + ("和风天气" if weather.get("source") == "qweather" else "本地模拟")
        )

    async def _cmd_worldview(self, event, args, uid):
        """原 _handle_command 的「worldview」分支，逐行搬来。"""
        if len(args) >= 2 and args[1].lower() == "regen":
            if not self._is_admin(event):
                return "只有管理员才能重生成世界观。"
            await self.state.ensure_worldview(self._last_umo, force=True)
            return "✅ 世界观已重新生成。"
        await self.state.ensure_worldview(self._last_umo)
        return "🌍 世界观\n" + self.state.worldview_text()

    async def _cmd_threshold(self, event, args, uid):
        """原 _handle_command 的「threshold」分支，逐行搬来。"""
        data = await self._status_data()
        threshold = data["threshold"]
        return (
            "🎚 心流回复阈值\n"
            "当前动态阈值：" + str(round(threshold["value"] / 100, 3)) + "\n"
            "基础阈值：" + str(round(threshold["base"] / 100, 3)) + "\n"
            "绝对分数阈值：" + str(threshold["absolute"]) + "/10\n"
            "当前群活跃度：" + str(data["activity_rate"]) + "%\n"
            "说明：阈值越高越不想说话；详细状态用 /heartflow。"
        )

    async def _cmd_say(self, event, args, uid):
        """原 _handle_command 的「say」分支，逐行搬来。"""
        if not self._is_admin(event):
            return "只有管理员才能测试发言。"
        content = " ".join(args[1:]).strip()
        if not content:
            return "用法：/amo say 内容"
        await event.send(MessageChain([Comp.Plain(content)]))
        return ""

    async def _cmd_clean(self, event, args, uid):
        """原 _handle_command 的「clean」分支，逐行搬来。"""
        if not self._is_admin(event):
            return "只有管理员才能清理。"
        self.db.purge_messages(self.cfg_int("chat_log_days", 14))
        return "✅ 已清理过期聊天记录。"

    async def _cmd_trip(self, event: AstrMessageEvent, args: list[str], uid: str) -> str:
        """旅游手动入口：/amo trip [start 城市 天数 | status | cancel]。

        引擎（amo_trip）已经负责预算/冷却/城市池/幂等；这里只做主人校验和
        把结果翻译成人话。主人限制沿用同文件 _is_admin 的写法。
        """
        if not self._is_admin(event):
            return "这个只给主人用哦。"
        tail = [str(item).strip() for item in args[1:] if str(item).strip()]
        action = tail[0].lower() if tail else "status"
        if action in ("status", "状态"):
            info = trip.status(self)
            phase = str(info.get("phase") or "")
            if phase in ("", "done"):
                return "现在没在旅游。"
            day = max(1, to_int(info.get("day_no"), 0) + 1)
            return ("她在%s，第 %d 天 / 共 %d 天，已经花了 %d 元。"
                    % (str(info.get("city") or "外地"), day,
                       to_int(info.get("days"), 0), to_int(info.get("spent"), 0)))
        if action in ("start", "出发"):
            rest = [token for token in tail[1:]
                    if token.lower() not in ("force", "强制")]
            force = len(rest) != len(tail[1:])
            city = rest[0] if rest else ""
            days = 0
            if len(rest) > 2:
                return "用法：/amo trip start 城市 [天数] [force]"
            if len(rest) == 2:
                if not rest[1].isdigit():
                    return "天数要写数字，比如 /amo trip start 大理 2"
                days = to_int(rest[1], 0)
                if days < 1:
                    return "天数至少 1 天。"
            result = trip.trip_manual_start(self, city=city, days=days or None, force=force)
            if not result.get("ok"):
                return self._trip_error_text(result)
            info = trip.status(self)
            return ("出发去%s啦，计划 %d 天，路上小心。"
                    % (info.get("city") or city or "外地", to_int(info.get("days"), 0)))
        if action in ("cancel", "取消"):
            result = trip.cancel(self)
            if not result.get("ok"):
                if not str(result.get("phase") or ""):
                    return "现在没有在计划的旅行，不用取消。"
                return "已经出发了，取消不了；想回来就跟她说「回来吧」。"
            return "旅游取消啦，还没出发，随时可以再计划。"
        return "用法：/amo trip [start 城市 天数 | status | cancel]"

    def _trip_error_text(self, result: dict[str, Any]) -> str:
        """trip_manual_start 的 reason → 人话（和面板接口同一口径）。"""
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


    @filter.command("日程")
    async def cmd_schedule(self, event: AstrMessageEvent):
        """查看日程：/日程 [今天|明天|昨天]"""
        await self._ensure_ready()
        try:
            import datetime as _dt

            # 去掉 @她 之类的 mention；前缀写法可能是 / 或 ／
            args = [
                item for item in str(getattr(event, "message_str", "") or "").replace("／", "/").split()
                if not item.startswith("@")
            ]
            which = args[1] if len(args) > 1 else "今天"
            offset = {
                "今天": 0, "today": 0,
                "明天": 1, "tomorrow": 1,
                "昨天": -1, "yesterday": -1,
            }.get(which)
            if offset is None:
                yield event.plain_result("用法：/日程 [今天|明天|昨天]")
                return
            target = _dt.date.fromisoformat(today_str()) + _dt.timedelta(days=offset)
            yield event.plain_result(self.state.schedule_text_of(target.isoformat()))
        except Exception as _amo_exc:
            _note_swallowed("amo_commands.cmd_schedule", _amo_exc)
            yield event.plain_result("日程读不出来…")

    @filter.command("改日程")
    async def cmd_schedule_edit(self, event: AstrMessageEvent):
        """改今天某一段：/改日程 15:00 提前回家"""
        await self._ensure_ready()
        try:
            raw = str(getattr(event, "message_str", "") or "").replace("／", "/").strip()
            parts = [item for item in raw.split() if not item.startswith("@")]
            if len(parts) < 3:
                yield event.plain_result("用法：/改日程 15:00 提前回家")
                return
            match = re.match(r"^(\d{1,2})[:：](\d{2})$", parts[1].strip())
            if not match:
                yield event.plain_result("时间要写成 HH:MM，比如 /改日程 15:00 提前回家")
                return
            hour, minute = int(match.group(1)), int(match.group(2))
            if hour > 23 or minute > 59:
                yield event.plain_result("时间不对，要 00:00–23:59。")
                return
            clock = "%02d:%02d" % (hour, minute)
            activity = " ".join(parts[2:]).strip()[:60]
            if not activity:
                yield event.plain_result("还没说要改成什么。")
                return
            key = today_str()
            old_items = self.state.schedule.get(key) or self.db.get_schedule(key) or []
            items = [dict(item) if isinstance(item, dict) else item for item in old_items]
            hit = None
            for item in items:
                if str(item.get("start") or "") == clock:
                    hit = item
                    break
            if hit is None:
                for item in items:
                    start = str(item.get("start") or "")
                    end = str(item.get("end") or "")
                    if start and end and start <= clock < end:
                        hit = item
                        break
            if hit is None:
                yield event.plain_result("那个时间点找不到对应的安排。")
                return
            place = str(hit.get("place") or "")
            category = str(hit.get("category") or "")
            if callable(infer_place_category):
                place, category = infer_place_category(activity, place, category)
            change = {"activity": activity, "place": place, "category": category}
            if callable(apply_sched_at):
                new_items = apply_sched_at(items, str(hit.get("start") or clock), change)
            else:
                hit["activity"] = activity
                hit["place"] = place
                hit["category"] = category
                new_items = items
            if new_items == items:
                yield event.plain_result("和原来一样，没有改。")
                return
            reason_text = "/改日程 %s %s" % (clock, activity)
            hit_start = str(hit.get("start") or clock)
            apply = getattr(self.state, "apply_schedule", None)
            if callable(apply):
                def _mutate(rows):
                    if callable(apply_sched_at):
                        return apply_sched_at(rows, hit_start, change)
                    patched = [dict(row) if isinstance(row, dict) else row for row in rows]
                    for row in patched:
                        if isinstance(row, dict) and str(row.get("start") or "") == hit_start:
                            row.update(change)
                            break
                    return patched
                self.state.apply_schedule(key, _mutate, source="command",
                                          reason=reason_text)
            else:
                self.state.commit_schedule_change(
                    key, new_items, old_items=old_items,
                    source="command", reason=reason_text,
                )
            yield event.plain_result("改好了，%s 那段现在是「%s」。" % (hit.get("start"), activity[:30]))
        except Exception as _amo_exc:
            _note_swallowed("amo_commands.cmd_schedule_edit", _amo_exc)
            yield event.plain_result("没改成…")

    @filter.command("heartflow")
    async def cmd_heartflow(self, event: AstrMessageEvent):
        await self._ensure_ready()
        if self.heartflow is None:
            yield event.plain_result("心流引擎未加载")
            return
        async for item in self.heartflow.heartflow_status(event):
            yield item

    @filter.command("heartflow_debug")
    async def cmd_heartflow_debug(self, event: AstrMessageEvent):
        await self._ensure_ready()
        if self.heartflow is None:
            yield event.plain_result("心流引擎未加载")
            return
        async for item in self.heartflow.heartflow_debug(event):
            yield item

    @filter.command("heartflow_clear")
    async def cmd_heartflow_clear(self, event: AstrMessageEvent):
        await self._ensure_ready()
        if self.heartflow is None:
            yield event.plain_result("心流引擎未加载")
            return
        async for item in self.heartflow.heartflow_clear_buffer(event):
            yield item

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("heartflow_log")
    async def cmd_heartflow_log(self, event: AstrMessageEvent):
        await self._ensure_ready()
        if self.heartflow is None:
            yield event.plain_result("心流引擎未加载")
            return
        async for item in self.heartflow.heartflow_toggle_log(event):
            yield item

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("heartflow_reset")
    async def cmd_heartflow_reset(self, event: AstrMessageEvent):
        await self._ensure_ready()
        if self.heartflow is None:
            yield event.plain_result("心流引擎未加载")
            return
        async for item in self.heartflow.heartflow_reset(event):
            yield item

    @filter.command("heartflow_help")
    async def cmd_heartflow_help(self, event: AstrMessageEvent):
        await self._ensure_ready()
        if self.heartflow is None:
            yield event.plain_result("心流引擎未加载")
            return
        async for item in self.heartflow.heartflow_help(event):
            yield item

    @filter.command("amo")
    async def cmd_amo(self, event: AstrMessageEvent):
        await self._ensure_ready()
        try:
            message = await self._handle_command(event)
        except Exception as exc:
            logger.error("[amo] 指令异常: %s" % exc)
            message = "处理失败：" + str(exc)
        if message:
            yield event.plain_result(message)

    @filter.command("修正日记")
    async def cmd_fix_diary(self, event: AstrMessageEvent):
        await self._ensure_ready()
        if not self._is_admin(event):
            yield event.plain_result("只有管理员才能修正日记。")
            return
        raw = str(event.message_str or "").strip()
        parts = raw.split(maxsplit=2)
        if len(parts) < 3:
            yield event.plain_result("用法：/修正日记 2026-11-15 正文内容")
            return
        try:
            from .amo_shared import _diary_parse_day, _diary_write
        except ImportError:
            if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
                from amo_shared import _diary_parse_day, _diary_write
        day = _diary_parse_day(parts[1])
        if day is None:
            yield event.plain_result("日期格式应为 YYYY-MM-DD")
            return
        body = parts[2].strip()
        try:
            path = _diary_write(self._diary_dir(), day, body)
            self.db.kv_set("diary_written:" + day.isoformat(), path)
            self.db.kv_set("diary_version:" + day.isoformat(), self.db.schedule_version(day.isoformat()))
            self.db.kv_set("diary_stale:" + day.isoformat(), False)
        except Exception as exc:
            yield event.plain_result("修正失败：%s" % exc)
            return
        yield event.plain_result("已修正 %s 的日记。" % day.isoformat())

    @filter.command("c_status")
    async def cmd_status(self, event: AstrMessageEvent):
        await self._ensure_ready()
        yield event.plain_result(self._format_status(await self._status_data()))

    @filter.command("c_profile")
    async def cmd_profile(self, event: AstrMessageEvent):
        await self._ensure_ready()
        uid = self._canonical_uid(event)
        profile = self.db.get_profile(uid)
        if not profile:
            yield event.plain_result("还没有你的档案。")
            return
        yield event.plain_result(
            "🤍 关系：" + str(profile.get("relation", "普通"))
            + "\n📖 记忆条数：" + str(len(await self._all_memories(uid, "", 200)))
        )

    @filter.command("c_setrel")
    async def cmd_setrel(self, event: AstrMessageEvent):
        await self._ensure_ready()
        if not self._is_admin(event):
            yield event.plain_result("只有管理员才能调整关系。")
            return
        parts = [item for item in (event.message_str or "").replace("/", " ").split() if item]
        relation = parts[-1] if parts else ""
        if relation not in RELATIONS:
            yield event.plain_result("用法：/c_setrel 仇恨|抵触|普通|熟识|相恋")
            return
        uid = self._canonical_uid(event)
        self.db.update_profile(uid, relation=relation)
        yield event.plain_result("✅ 关系已设置为：" + relation)

    @filter.command("learn_and_gate")
    async def cmd_learn(self, event: AstrMessageEvent):
        await self._ensure_ready()
        text = (event.message_str or "").replace("/learn_and_gate", "").strip()
        if not text:
            yield event.plain_result("请输入要学习的内容")
            return
        uid = self._canonical_uid(event)
        if self._external_memory_write(uid, text, "manual", 1):
            yield event.plain_result("✅ 已经记下来啦。")
        else:
            yield event.plain_result("⚠️ 没能写进记忆插件：请先在设置里选好记忆来源插件。")

    @filter.command("礼物")
    async def cmd_gift_list(self, event: AstrMessageEvent):
        await self._ensure_ready()
        if self.gift is None or not self.gift.enabled:
            yield event.plain_result("礼物功能还没开。")
            return
        if not self.gift.is_owner(event):
            yield event.plain_result("这个只给老公看哦。")
            return
        yield event.plain_result(self.gift.command_text())

    @filter.command("送礼")
    async def cmd_gift_send(self, event: AstrMessageEvent, item: str = ""):
        await self._ensure_ready()
        if self.gift is None or not self.gift.enabled:
            yield event.plain_result("礼物功能还没开。")
            return
        if not self.gift.is_owner(event):
            yield event.plain_result("这个只给老公用哦。")
            return
        # AstrBot 的 command handler 不保证把命令后面的参数注入 item；
        # 这里以 event.message_str 为准兜底，避免 /送礼 一条围巾 永远提示“用法”。
        if not str(item or "").strip():
            raw = str(getattr(event, "message_str", "") or "").replace("／", "/").strip()
            parts = [part for part in raw.split() if part and not part.startswith("@")]
            if parts and parts[0].lstrip("/").startswith("送礼"):
                item = " ".join(parts[1:]).strip()
        yield event.plain_result(self.gift.manual_gift(event, item))

    # ---------- 房屋 ----------
    @filter.command("我的家")
    async def cmd_my_home(self, event: AstrMessageEvent):
        """查看现在的家：/我的家"""
        await self._ensure_ready()
        try:
            yield event.plain_result(self.housing.card_text())
        except Exception:
            yield event.plain_result("家有点乱，等会儿再看…")

    @filter.command("房屋")
    async def cmd_house(self, event: AstrMessageEvent):
        """房屋分页：/房屋 房产 | /房屋 租房"""
        await self._ensure_ready()
        try:
            raw = str(getattr(event, "message_str", "") or "").replace("／", "/").strip()
            parts = [part for part in raw.split() if part and not part.startswith("@")]
            if len(parts) > 2 and parts[1] in ("出租", "出售"):
                name = parts[2]
                if parts[1] == "出租":
                    rent_yuan = 0.0
                    if len(parts) > 3:
                        try:
                            rent_yuan = float(parts[3])
                        except Exception:
                            rent_yuan = 0.0
                    _ok, msg = self.housing.rent_out(name, int(rent_yuan * 100))
                else:
                    _ok, msg = self.housing.sell_home(name)
                yield event.plain_result(msg)
                return
            page = parts[1] if len(parts) > 1 else ""
            yield event.plain_result(self.housing.card_text(page))
        except Exception:
            yield event.plain_result("房屋信息读不出来…")

    @filter.command("搬家")
    async def cmd_move_house(self, event: AstrMessageEvent):
        """搬到名下另一套房：/搬家 原木风两居室"""
        await self._ensure_ready()
        try:
            raw = str(getattr(event, "message_str", "") or "").replace("／", "/").strip()
            parts = [part for part in raw.split() if part and not part.startswith("@")]
            ref = " ".join(parts[1:]).strip()
            if not ref:
                yield event.plain_result("用法：/搬家 <房产名>")
                return
            ok, msg, _home = self.housing.move_in(ref)
            if ok:
                try:
                    self._spawn(self._ensure_home_images(force=True))
                except Exception as exc:
                    _note_swallowed("amo_commands.py.925", exc)
            yield event.plain_result(msg)
        except Exception:
            yield event.plain_result("搬家没成功…")



async def _status_data_part(self, counts):
    counts["swallowed"] = _swallowed_snapshot(10)
    # 记忆条数改成外挂插件的：本插件不再存记忆，面板显示外部模块里的条数
    primary_uid = ""
    try:
        primary = self.db.list_profiles(limit=1)
        primary_uid = str(primary[0].get("uid") or "") if primary else ""
    except Exception as _amo_exc:
        _note_swallowed("amo_commands.HumanCorePlugin._status_data", _amo_exc)
        primary_uid = ""
    try:
        # 状态页会被轮询，外部记忆条数缓存 30 秒，别每次都去读别人的库
        stamp = to_float(self.db.kv_get("ext_mem_count_at_" + primary_uid, 0), 0.0)
        cached = to_int(self.db.kv_get("ext_mem_count_" + primary_uid, 0), 0)
        if primary_uid and now_ts() - stamp < 30:
            counts["memories"] = cached
        elif primary_uid:
            counted = len(await self._all_memories(primary_uid, "", 200))
            counts["memories"] = counted
            self.db.kv_set("ext_mem_count_at_" + primary_uid, now_ts())
            self.db.kv_set("ext_mem_count_" + primary_uid, counted)
        else:
            counts["memories"] = 0
    except Exception as _amo_exc:
        _note_swallowed("amo_commands.HumanCorePlugin._status_data", _amo_exc)
        counts["memories"] = 0

