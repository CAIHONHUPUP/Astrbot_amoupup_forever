# -*- coding: utf-8 -*-
"""运行时·回复 mixin：发送后处理/装饰改写/语音。从 amo_runtime.py 按域拆出。"""
from __future__ import annotations

from typing import TYPE_CHECKING
import asyncio
import re
import inspect

try:  # 三个 mixin 的共同类型底座（运行期=object，沿用拆分前的 MRO 语义）
    from .amo_runtime_ports import RuntimeMixinBase
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_runtime_ports import RuntimeMixinBase


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        AstrMessageEvent, Comp, DIALECT_SAMPLES, SCHEDULE_VERSION, STYLE_HINTS, _DIARY_SHOW, _DIARY_WRITTEN, _city_block, _city_pick_spots, _city_travel_block, _diary_pick_hints, _ms_build_content, _ms_build_prompt, _ms_idle_due, _ms_should_archive, _ms_transcript, _now, attribution_leak, filter, first_person_experience, is_weather_query, leak_tokens, logger, looks_like_ai_admission, looks_like_defensive_reply, looks_like_model_leak, now_ts, render_recent_block, strip_brushoff, strip_chat_format, to_float, to_int, today_str, truncate,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            AstrMessageEvent, Comp, DIALECT_SAMPLES, SCHEDULE_VERSION, STYLE_HINTS, _DIARY_SHOW, _DIARY_WRITTEN, _city_block, _city_pick_spots, _city_travel_block, _diary_pick_hints, _ms_build_content, _ms_build_prompt, _ms_idle_due, _ms_should_archive, _ms_transcript, _now, attribution_leak, filter, first_person_experience, is_weather_query, leak_tokens, logger, looks_like_ai_admission, looks_like_defensive_reply, looks_like_model_leak, now_ts, render_recent_block, strip_brushoff, strip_chat_format, to_float, to_int, today_str, truncate,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                AstrMessageEvent, Comp, DIALECT_SAMPLES, SCHEDULE_VERSION, STYLE_HINTS, _DIARY_SHOW, _DIARY_WRITTEN, _city_block, _city_pick_spots, _city_travel_block, _diary_pick_hints, _ms_build_content, _ms_build_prompt, _ms_idle_due, _ms_should_archive, _ms_transcript, _now, attribution_leak, filter, first_person_experience, is_weather_query, leak_tokens, logger, looks_like_ai_admission, looks_like_defensive_reply, looks_like_model_leak, now_ts, render_recent_block, strip_brushoff, strip_chat_format, to_float, to_int, today_str, truncate,
            )


try:  # 回复事实守卫（「不矛盾」档）：装饰后、发送前兜底
    from . import amo_reply_pipeline as _reply_pipeline
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_reply_pipeline as _reply_pipeline


def _narrative_place(plugin):
    """此刻地点：只读唯一事实视图（F1）；读不到给空串，让调用方退回旧快照。"""
    try:
        try:
            from .amo_diary_guard import narrative_place as _place
        except ImportError:
            from amo_diary_guard import narrative_place as _place
        return str(_place(plugin) or "")
    except Exception as exc:  # cleanup: 视图读不到就不给地点
        logger.debug("[amo] 事实视图地点读取失败: %s", exc)
        return ""


try:  # 「发语音 / 用语音说」按需触发
    from .amo_voice import clean_voice_text, voice_requested
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            from amo_voice import clean_voice_text, voice_requested
        except Exception:  # pragma: no cover
            def voice_requested(text):  # type: ignore[misc]
                return False

            def clean_voice_text(text, limit=600):  # type: ignore[misc]
                return str(text or "")


class RuntimeReplyMixin(RuntimeMixinBase):
    _last_relation_decay: float
    _last_relation_sediment: float
    _last_purge: float
    _last_auto_learn: float
    _last_pending_check: float
    _last_schedule_save: str
    _proactive_last: float

    @filter.after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent):
        try:
            self._last_reply_at[event.unified_msg_origin] = now_ts()
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.on_after_message_sent", _amo_exc)
        if self.heartflow is None:
            return
        try:
            await self.heartflow.on_after_message_sent(event)
        except Exception as exc:
            logger.debug("[amo] 心流发送后状态更新失败: %s" % exc)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        try:
            await self._ensure_ready()
            result = event.get_result()
            if not result or not result.chain:
                return
            try:
                if not result.is_llm_result():
                    return
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result", _amo_exc)
            text = strip_chat_format("".join(
                comp.text for comp in result.chain if isinstance(comp, Comp.Plain)
            ))
            text = self._deco_soft_guards(event, text)
            text = self._deco_rewrite_reply(event, result, text)
            record_cls = getattr(Comp, "Record", None)
            if record_cls is not None and any(
                isinstance(comp, record_cls) for comp in result.chain
            ):
                # Record.text 是 TTS 念的那句原文（别的语音插件也这么读），有就记内容
                voice_text = " ".join(
                    str(getattr(comp, "text", "") or "").strip()
                    for comp in result.chain
                    if isinstance(comp, record_cls)
                ).strip()
                marker = "[我发的语音] " + (voice_text or text.strip() or "一段语音")
                remember = getattr(self, "_remember_bot_media", None)
                if callable(remember):
                    self._spawn(remember(event.unified_msg_origin, marker))
                return
            if not text.strip():
                return
            group = not event.is_private_chat()
            if not group:
                # 私聊永远不引用（QQ / 飞书 / 任何平台）：连上游核心或别的插件
                # 塞进 chain 的 Reply 也在这里摘掉，而不是整条消息交回去不管。
                result.chain = [
                    comp for comp in result.chain if not isinstance(comp, Comp.Reply)
                ]
            if any(not isinstance(comp, (Comp.Plain, Comp.At)) for comp in result.chain):
                return
            quote_enabled = bool(
                self._quote_allowed(event.unified_msg_origin, group)
                or (group and getattr(event, "amo_quote_hint", False))
            )
            quote_id = getattr(event.message_obj, "message_id", None) if quote_enabled else None
            core_decor = self._core_decor_flags(event.unified_msg_origin)
            takeover = bool(core_decor.get("quote") or core_decor.get("mention"))
            if takeover and not getattr(self, "_core_decor_logged", False):
                logger.info(
                    "[amo] 检测到 AstrBot 全局引用/mention 开启：本插件接管发送，"
                    "避免核心覆盖「私聊不引用 / 群聊按概率引用」"
                )
                self._core_decor_logged = True
            text = await self._deco_polish_native(event, result, text)
            text = await self._deco_text_cleanup(event, text)
            result.chain = [Comp.Plain(text)]
            if await self._maybe_send_voice_reply(event, text, result):
                return

            # 群聊/私聊的最终发送只交给完整本地分段引擎。
            if self.segment_engine is None or not self.cfg_bool("seg_enable", True):
                if takeover and await self._send_now(
                    event, text, quote_id if quote_enabled else None
                ):
                    result.chain = []
                    return
                self._remember_sent_reply(event, text)
                return
            backup = list(result.chain)
            try:
                handled = await self.segment_engine.handle_segment_reply(
                    event,
                    quote_id=quote_id if quote_enabled else None,
                    group=group,
                    force=takeover,
                )
            except Exception as exc:
                logger.error("[amo] 完整分段引擎异常: %s" % exc)
                handled = False
            if not handled:
                result.chain = backup
                self._remember_sent_reply(event, text)
            return
        except Exception as exc:
            logger.debug("[amo] 结果装饰失败: %s" % exc)

    def _deco_soft_guards(self, event, text):
        """发出去之前的软护栏：私聊下班同步、地点纠正、归属串了只告警不拦。"""
        try:
            if event.is_private_chat():
                self._maybe_own_offwork_sync(text)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result", _amo_exc)
        try:
            # 地点事实只读唯一视图（F1）；视图读不到才退回日程快照
            place = _narrative_place(self) or self.state.current_activity().get("place")
            text = self._guard_self_location(text, place)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result", _amo_exc)
        # 老地点守卫先跑；这里用新守卫兜底（日程/健康/约会/吃饭/旅游对不上时改口）
        try:
            text = _reply_pipeline.apply_fact_guard(text, self)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result", _amo_exc)
        # 跨轮/跨段状态复读 + 他问吃啥先答（只加这一层，不改事实守卫）
        try:
            text = _reply_pipeline.apply_narrative_repetition(
                text, self, getattr(event, "message_str", "") or "",
                str(getattr(event, "unified_msg_origin", "") or ""))
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result", _amo_exc)
        # 口头禅去逐字复读：只动句首/独立短语，紧跟在事实守卫之后
        try:
            text = _reply_pipeline.apply_tone_guard(
                text, self, event.unified_msg_origin)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result", _amo_exc)
        try:
            if attribution_leak is not None and first_person_experience is not None:
                body_now = event.message_str or ""
                if first_person_experience(body_now) and attribution_leak(body_now, text):
                    hits = leak_tokens(body_now, text) if leak_tokens is not None else []
                    logger.warning(
                        "[amo] 归属串了（%s）：他说=%s / 她说=%s"
                        % ("、".join(hits) or "?", truncate(body_now, 40), truncate(text, 40))
                    )
                    alerts = getattr(self, "_attribution_alert", None)
                    if alerts is None:
                        alerts = {}
                        self._attribution_alert = alerts
                    alerts[str(event.unified_msg_origin)] = now_ts()
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result", _amo_exc)
        return text

    def _deco_rewrite_reply(self, event, result, text):
        """礼物心愿/画图钩子 + 防御话术、AI 自曝、后台泄漏三类硬改写。"""
        if self.gift is not None:
            try:
                self.gift.observe_reply(event, text)
            except Exception as exc:
                logger.debug("[amo] 礼物心愿捕获失败: %s" % exc)
        # 用上下文判断“这句是不是在跟她说”，而不是靠关键词猜
        addressed_to_me = self._is_talking_to_me(event)
        if not addressed_to_me and (
            looks_like_defensive_reply(text) or looks_like_ai_admission(text)
        ):
            # 他们在聊自己的事，她冒出一句“你才人机”很突兀：不接这茬
            logger.info("[amo] 非指名对话里拦下防御话术: %s" % text[:60])
            text = "你们聊你们的，我不抢话。"
            result.chain = [Comp.Plain(text=text)]
        elif looks_like_ai_admission(text):
            text = "你才人机呢，你见过这么会聊天的机器人吗？"
            result.chain = [Comp.Plain(text=text)]
        if looks_like_model_leak(text):
            # 模型名/厂商是后台信息，不能报；按角色打岔带过去
            logger.info("[amo] 拦截后台信息泄漏: %s" % text[:60])
            text = "秘密，不告诉你。"
            result.chain = [Comp.Plain(text=text)]
        # 先判断要不要补图：表情包/图片回复会被后面的非 Plain 检查提前 return，
        # 所以放在这里，保证「拍给我看看」即使她只回表情也能触发
        if self.draw is not None:
            try:
                self.draw.maybe_after_reply(event, text)
            except Exception as exc:
                logger.warning("[amo] 画图判断失败: %s" % exc)
        return text

    async def _deco_polish_native(self, event, result, text):
        """polish_native 打开时用原生润色再过一遍（失败保留原文）。"""
        # B 方案桥：同步守卫已走 A；这里在 async 阶段让 LLM 只在候选里选序号。
        if _reply_pipeline.tone_llm_pick_enabled(self):
            try:
                picked_text = await _reply_pipeline.apply_pending_tone_pick_async(text, self)
                if picked_text and picked_text != text:
                    text = picked_text
                    result.chain = [Comp.Plain(text)]
            except Exception as _amo_exc:  # cleanup: B 失败保留 A
                _note_swallowed("amo_runtime_reply._deco_polish_native.tone_pick", _amo_exc)
        if self.cfg_bool("polish_native", False):
            snap = self.state.snapshot()
            native_profile = self.db.get_profile(self._canonical_uid(event)) or {}
            native_relation = str(native_profile.get("relation") or "普通")
            native_name = str(native_profile.get("alias") or native_profile.get("name") or "对方")
            native_tone = self._tone_with_continuity(
                event.unified_msg_origin, self.state.tone_text(snap, native_relation)
            )
            native_persona = await self.llm.persona(event.unified_msg_origin)
            native_style = STYLE_HINTS.get(self.cfg_str("reply_style", "soft"), "")
            native_consult = bool(re.search(r"(怎么|如何|为什么|步骤|怎么办|该怎么做|教程|方法|建议|教我|能不能|是什么|该不该|可不可以)", event.message_str or ""))
            polished = await self.llm.polish(
                event.unified_msg_origin, text, native_tone,
                state_text=self.state.prompt_text(snap),
                relation=native_relation, name=native_name,
                persona=native_persona, style=native_style, consult=native_consult,
            )
            if polished and polished != text:
                text = polished
                result.chain = [Comp.Plain(text)]
        return text

    def _align_footwear(self, text):
        try:
            fixed = align_footwear(text, getattr(self, "wardrobe", None))
            if fixed != text:
                logger.info("[amo] 鞋子对齐衣柜: %s -> %s", text[:40], fixed[:40])
            return fixed
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.on_decorating_result.footwear", _amo_exc)
            return text

    def _align_arrival(self, text):
        """还在路上时，不许说「到了/刚进门/鞋都换了」；顺手修「车上上」这种叠字。"""
        import re as _re
        body = str(text or "")
        try:
            act = self.state.current_activity() or {}
        except Exception:
            return body
        place = str(act.get("place") or "")
        zone = str(act.get("zone") or "")
        on_road = zone in ("road", "outside") or any(w in place for w in ("路上", "地铁", "公交", "车", "街", "外面"))
        if on_road and _re.search(r"(到了|到家了|刚进门|进家门|鞋都换了|换好鞋|换上拖鞋)", body):
            body = _re.sub(r"[^。！？\n]*(?:到了|到家了|刚进门|进家门|鞋都换了|换好鞋|换上拖鞋)[^。！？\n]*",
                           "我还在路上呢，快到了", body)
        body = _re.sub(r"(车|路|家|床|桌|椅|站)上上", r"\1上", body)
        return body

    def _align_transit_word(self, text):
        """坐地铁时不许说「车上/在车上」，改成地铁上。"""
        body = str(text or "")
        try:
            act = self.state.current_activity() or {}
        except Exception:
            return body
        blob = str(act.get("activity") or "") + str(act.get("transit") or "") + str(act.get("vehicle") or "")
        if "地铁" in blob or "subway" in blob.lower():
            body = body.replace("在车上", "在地铁上").replace("车上", "地铁上")
        return body

    def _align_swim_where(self, text, user_text):
        """在外面被要求换泳装/比基尼：不许答应「行/就一张」，统一改成回家再拍。"""
        body = str(text or "")
        asked = str(user_text or "")
        if not any(w in asked for w in ("比基尼", "泳衣", "泳装", "泳裤", "三点式")):
            return body
        try:
            act = self.state.current_activity() or {}
        except Exception:
            return body
        place = str(act.get("place") or "")
        home = any(w in place for w in ("家", "客厅", "卧室", "沙发", "床", "阳台", "洗手间", "浴室"))
        if home:
            return body
        if any(w in body for w in ("行", "好", "可以", "就一张", "换", "拍")):
            return "在外面呢，等我到家再拍给你看，先别催。"
        return body

    async def _deco_text_cleanup(self, event, text):
        """最终清洗：去赶人话、去重复饭问、地点黑名单、日期守卫、裁长度。"""
        if strip_brushoff is not None:
            try:
                text, brush = strip_brushoff(text, event.message_str or "")
                if brush:
                    logger.debug("[amo] 去掉赶人的话: %s" % brush)
            except Exception as _amo_exc:
                _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result", _amo_exc)
        try:
            recent = self._recent(event.unified_msg_origin, 8)
            bot_line = ""
            for row in reversed(recent or []):
                if str((row or {}).get("role") or "") == "bot":
                    bot_line = str((row or {}).get("content") or "")
                    break
            text = self._strip_repeated_food_question(
                text, event.message_str or "", bot_line)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result", _amo_exc)
        try:  # 催办降温：他已经答应/要去做了，就别再催（确定性兜底）
            urged = self._strip_repeated_urge(text, event.message_str or "", bot_line)
            if urged != text:
                logger.info("[amo] 催办降温: %s -> %s", text[:40], urged[:40])
                text = urged
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.on_decorating_result.urge", _amo_exc)
        try:  # 短句先接话题：他只回「慢慢看/去吧」时，别反问「啥呀」（确定性兜底）
            fixed = self._strip_confused_short_reply(text, event.message_str or "", bot_line)
            if fixed != text:
                logger.info("[amo] 短句接话题: %s -> %s", text[:40], fixed[:40])
                text = fixed
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.on_decorating_result.short_reply", _amo_exc)
        try:  # 鞋子对齐衣柜：今天穿高跟鞋时别顺口说成拖鞋
            text = self._align_footwear(text)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.on_decorating_result.footwear", _amo_exc)
        try:  # 还在路上时别说到家了；顺手修「车上上」
            text = self._align_arrival(text)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.on_decorating_result.arrival", _amo_exc)
        try:  # 坐地铁时别把地铁说成「车上」
            text = self._align_transit_word(text)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.on_decorating_result.transit_word", _amo_exc)
        try:  # 在外面不能换泳装：不许答应「行/就一张」
            text = self._align_swim_where(text, event.message_str or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.on_decorating_result.swim_where", _amo_exc)
        # 第十轮 B1：通勤块覆盖当前时间时，不许说「到家了 / 瘫在沙发上」这类终态。
        try:
            try:
                from .amo_transit import rewrite_sent_text as _transit_rewrite
            except ImportError:
                from amo_transit import rewrite_sent_text as _transit_rewrite
            text = _transit_rewrite(self, text)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.on_decorating_result.transit", _amo_exc)
        try:
            guarded = await self._guard_zone_reply(
                text, event.unified_msg_origin, user_text=event.message_str or "")
            if guarded and guarded != text:
                logger.info("[amo] 地点黑名单重写: %s -> %s", text[:40], guarded[:40])
                text = guarded
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result.zone", _amo_exc)
        try:
            from .amo_date_context import guard_reply as _guard_date_reply
            text = _guard_date_reply(text, self)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.HumanCorePlugin.on_decorating_result.date", _amo_exc)
        try:
            from .amo_date_context import mark_reply_date_end as _mark_date_end
            _mark_date_end(self, text)
        except Exception as _amo_exc:
            _note_swallowed("amo_runtime.on_decorating_result.date_end_cue", _amo_exc)
        text = self._trim_reply(text)
        return text

    async def _maybe_send_voice_reply(self, event, text, result):
        """用户这一轮明确要语音时，把最终人设文字交给 TTS 发出去。

        普通回复仍走 AstrBot 的随机 TTS；只有这里命中「发语音 / 用语音说 /
        语音回我 / 念给我听」才强制转换一次。失败只回退文字，不吞消息。
        """
        if not self.cfg_bool("voice_on_demand_enable", True):
            return False
        if not voice_requested(getattr(event, "message_str", "")):
            return False
        get_extra = getattr(event, "get_extra", None)
        if callable(get_extra) and get_extra("_amo_voice_done"):
            return False
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if not umo or not text.strip():
            return False
        getter = getattr(self.context, "get_using_tts_provider_async", None)
        if not callable(getter):
            logger.info("[amo] 按需语音：当前上下文不支持 TTS，回退文字")
            return False
        try:
            provider = getter(umo)
            if inspect.isawaitable(provider):
                provider = await provider
        except Exception as exc:
            logger.warning("[amo] 按需语音：获取 TTS 供应商失败，回退文字: %s" % exc)
            return False
        if provider is None:
            logger.info("[amo] 按需语音：这个会话没有 TTS 供应商，回退文字")
            return False
        max_chars = 600
        timeout = 45
        try:
            cfg_int = getattr(self, "cfg_int", None)
            if callable(cfg_int):
                max_chars = max(60, min(2000, int(cfg_int("voice_max_chars", 600))))
                timeout = max(5, min(120, int(cfg_int("voice_tts_timeout_seconds", 45))))
        except Exception as exc:
            _note_swallowed("amo_runtime.py.1280", exc)
        voice_text = clean_voice_text(text, limit=max_chars)
        if not voice_text:
            logger.info("[amo] 按需语音：清理后没有可念的文字，回退文字")
            return False
        try:
            audio_call = provider.get_audio(voice_text)
            if inspect.isawaitable(audio_call):
                audio_path = await asyncio.wait_for(audio_call, timeout=timeout)
            else:
                audio_path = audio_call
        except asyncio.TimeoutError:
            logger.warning("[amo] 按需语音：TTS 超过 %s 秒，回退文字" % timeout)
            return False
        except Exception as exc:
            logger.warning("[amo] 按需语音：TTS 合成失败，回退文字: %s" % exc)
            return False
        if not audio_path:
            logger.warning("[amo] 按需语音：TTS 没有返回音频，回退文字")
            return False
        record_cls = getattr(Comp, "Record", None)
        if record_cls is None:
            logger.warning("[amo] 按需语音：当前消息组件没有 Record，回退文字")
            return False
        try:
            record = record_cls(file=audio_path, url=audio_path, text=voice_text)
        except TypeError:
            record = record_cls(file=audio_path)
        result.chain = [record]
        set_extra = getattr(event, "set_extra", None)
        if callable(set_extra):
            set_extra("_amo_voice_done", True)
        remember = getattr(self, "_remember_bot_media", None)
        if callable(remember):
            try:
                self._spawn(remember(umo, "[我发的语音] " + voice_text))
            except Exception as exc:
                logger.debug("[amo] 按需语音记忆写入失败: %s" % exc)
        logger.info("[amo] 按需语音已发送: %s" % text[:40])
        return True



def align_footwear(text, wardrobe):
    """衣柜里不是拖鞋时，不许把鞋顺口说成拖鞋/凉拖。"""
    body = str(text or "")
    if not body or wardrobe is None:
        return body
    try:
        cur = wardrobe.current() or {}
    except Exception:
        return body
    shoes = str(cur.get("shoes") or "")
    if not shoes or "拖鞋" in shoes or cur.get("sleep") or cur.get("hotel"):
        return body
    for word in ("凉拖", "拖鞋"):
        body = body.replace(word, shoes)
    return body
