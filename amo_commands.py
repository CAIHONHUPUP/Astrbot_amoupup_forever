# -*- coding: utf-8 -*-
"""CommandMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

from typing import TYPE_CHECKING

import re

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import snapshot as _swallowed_snapshot
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import snapshot as _swallowed_snapshot
    except ImportError:  # 直接按文件加载时
        from amo_observability import snapshot as _swallowed_snapshot

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .clock import from_timestamp as _clock_from_ts
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .clock import from_timestamp as _clock_from_ts
    except ImportError:  # 直接按文件加载时
        from clock import from_timestamp as _clock_from_ts

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_scope import event_scope as _event_scope
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_scope import event_scope as _event_scope
    except ImportError:  # 直接按文件加载时
        from amo_scope import event_scope as _event_scope

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        AstrMessageEvent, Comp, MessageChain, PLUGIN_VERSION, RELATIONS, SCHEDULE_VERSION, clamp, day_meta, filter, logger, now_ts, to_float, to_int, today_str,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            AstrMessageEvent, Comp, MessageChain, PLUGIN_VERSION, RELATIONS, SCHEDULE_VERSION, clamp, day_meta, filter, logger, now_ts, to_float, to_int, today_str,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            AstrMessageEvent, Comp, MessageChain, PLUGIN_VERSION, RELATIONS, SCHEDULE_VERSION, clamp, day_meta, filter, logger, now_ts, to_float, to_int, today_str,
        )


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class CommandMixin(_MixinBase):
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

    async def _status_data(self):
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
        return {
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

    def _format_status(self, data):
        mood = data["mood"]
        mood_line = " ".join([key + str(int(value)) for key, value in mood.items()])
        threshold = data["threshold"]
        return "\n".join([
            "🌙 陪伴 · 当前状态",
            "🕒 " + str(data["time"]),
            "💛 心情：" + str(data["dominant"]) + "（" + mood_line + "）",
            "🩸 生理期：" + str(data["period"]["phase"]) + " 第" + str(data["period"]["day"]) + "天（" + str(data["period"]["tone"]) + "）",
            "🩺 身体：" + str(data["health"]["status"]),
            "🌦 天气：" + str(data["weather_text"]),
            "📅 正在：" + str(data["activity"].get("activity", "休息")) + "（" + str(data["activity"].get("place", "")) + "）",
            "🎚 心流阈值：" + str(threshold["value"]) + "（基础 " + str(threshold["base"]) + "，活跃度 " + str(data["activity_rate"]) + "%）",
            "📚 档案 " + str(data["counts"]["profiles"]) + " · 记忆 " + str(data["counts"]["memories"]) + " · 消息 " + str(data["counts"]["messages"]),
        ])

    async def _handle_command(self, event):
        args = self._args(event)
        sub = args[0].lower() if args else "help"
        uid = self._canonical_uid(event)

        if sub in ("help", "帮助", ""):
            return (
                "🌙 陪伴 指令\n"
                "/amo status 状态｜/amo mood 九维心情\n"
                "/amo profile [@人] 档案｜/amo memories [@人] 记忆\n"
                "/amo period 生理期｜/amo health 健康｜/amo schedule 日程\n"
                "/amo weather 天气｜/amo worldview 世界观｜/amo threshold 阈值\n"
                "管理员：/amo rel @人 关系｜/amo set @人 字段 值｜/amo memo @人 内容 [0/1/2]（写入记忆插件）\n"
                "/amo period set YYYY-MM-DD｜/amo health set 状态\n"
                "/amo draw 描述（测试画图）｜/amo photo（照片概率/状态）\n"
                "/amo schedule regen｜/amo worldview regen｜/amo say 内容｜/amo clean"
            )

        if sub in ("segment", "分段"):
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

        if sub in ("draw", "画图"):
            engine = getattr(self, "draw", None)
            if engine is None:
                return "画图引擎未加载"
            text = " ".join(args[1:]).strip()
            return engine.manual(event, text)

        if sub in ("photo", "照片"):
            engine = getattr(self, "photo", None)
            if engine is None:
                return "照片引擎未加载"
            tail = " ".join(args[1:]).strip().lower()
            if tail in ("test", "测试", "share"):
                return "照片测试：" + await engine.share_now("daily")
            if tail in ("travel", "shopping", "weekend", "daily", "outfit"):
                return "照片测试：" + await engine.share_now(tail)
            return engine.status_text()

        if sub == "status" or sub == "状态":
            return self._format_status(await self._status_data())

        if sub == "mood" or sub == "心情":
            snap = self.state.snapshot()
            lines = ["🎨 陪伴的十维心情"]
            for key, value in snap["mood"].items():
                bar = "█" * int(round(value / 10)) + "░" * (10 - int(round(value / 10)))
                lines.append("%s %s %d" % (key, bar, int(value)))
            lines.append("整体偏「%s」，语气：%s" % (
                snap["dominant"], self.state.tone_text(snap, "普通")
            ))
            return "\n".join(lines)

        if sub == "profile" or sub == "档案":
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

        if sub == "memories" or sub == "记忆":
            target = self._resolve_target(event, args, 1)
            if target != uid and not self._is_admin(event):
                return "只有管理员才能查看别人的记忆。"
            items = await self._all_memories(target, "", 20)
            if not items:
                return "暂时没有记忆。"
            lines = ["📖 记忆 · " + target]
            for item in items:
                tier = to_int(item.get("tier"))
                label = "珍贵" if tier >= 2 else ("重要" if tier == 1 else "普通")
                expires = item.get("expires_at")
                if not expires:
                    left = "永久"
                else:
                    left = "%d天" % max(0, int((to_float(expires) - now_ts()) / 86400))
                lines.append("#%s [%s|%s] %s" % (item["id"], label, left, item["content"]))
            return "\n".join(lines)

        if sub in ("rel", "关系"):
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

        if sub == "set" or sub == "设置":
            if not self._is_admin(event):
                return "只有管理员才能修改档案。"
            if len(args) < 4:
                return "用法：/amo set @人 name|alias|birthday|notes|affection 值"
            target = self._resolve_target(event, args, 1)
            field = args[2]
            value = " ".join(args[3:])
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

        if sub == "memo" or sub == "记住":
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

        if sub == "forget" or sub == "忘记":
            if not self._is_admin(event):
                return "只有管理员才能删除记忆。"
            if len(args) < 2:
                return "用法：/amo forget 记忆ID"
            return "记忆由外挂插件保存，请到该插件里删除；本插件不再单独存记忆。"

        if sub == "period" or sub == "生理期":
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

        if sub == "health" or sub == "健康":
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

        if sub == "schedule" or sub == "日程":
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

        if sub == "weather" or sub == "天气":
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

        if sub == "worldview" or sub == "世界观":
            if len(args) >= 2 and args[1].lower() == "regen":
                if not self._is_admin(event):
                    return "只有管理员才能重生成世界观。"
                await self.state.ensure_worldview(self._last_umo, force=True)
                return "✅ 世界观已重新生成。"
            await self.state.ensure_worldview(self._last_umo)
            return "🌍 世界观\n" + self.state.worldview_text()

        if sub == "threshold" or sub == "阈值":
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

        if sub == "say" or sub == "说":
            if not self._is_admin(event):
                return "只有管理员才能测试发言。"
            content = " ".join(args[1:]).strip()
            if not content:
                return "用法：/amo say 内容"
            await event.send(MessageChain([Comp.Plain(content)]))
            return ""

        if sub == "clean" or sub == "清理":
            if not self._is_admin(event):
                return "只有管理员才能清理。"
            self.db.purge_messages(self.cfg_int("chat_log_days", 14))
            return "✅ 已清理过期聊天记录。"

        return "未知指令。发送 /amo help 查看帮助。"

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
            items = self.state.schedule.get(key) or self.db.get_schedule(key) or []
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
            hit["activity"] = activity
            self.state.schedule[key] = items
            self.db.kv_set("schedule", self.state.schedule)
            self.db.save_schedule(key, items)
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
            yield event.plain_result("这个只给主人看哦。")
            return
        yield event.plain_result(self.gift.command_text())

    @filter.command("送礼")
    async def cmd_gift_send(self, event: AstrMessageEvent, item: str = ""):
        await self._ensure_ready()
        if self.gift is None or not self.gift.enabled:
            yield event.plain_result("礼物功能还没开。")
            return
        if not self.gift.is_owner(event):
            yield event.plain_result("这个只给主人用哦。")
            return
        yield event.plain_result(self.gift.manual_gift(event, item))
