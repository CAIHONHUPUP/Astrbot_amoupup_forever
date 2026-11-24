# -*- coding: utf-8 -*-
"""LlmMixin：人格、LLM 注入、回复组装、冒充检测。"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

import random
import re

try:  # 提示词注册表（Phase 4）
    from . import amo_prompt_registry as _prompts
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_prompt_registry as _prompts

try:
    from . import amo_understanding as _understanding
except ImportError:
    import amo_understanding as _understanding

try:  # 目标时区下的“现在”（clock 支持面板改时区；直接按文件加载时退回绝对导入）
    from .clock import now as _now
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from clock import now as _now  # type: ignore[no-redef]


def _narration_on(plugin) -> bool:
    """动作描写开关（persona_action_narration，默认关）：提示词与出站共用同一个口径。"""
    try:
        try:
            from . import amo_narration_policy as _policy
        except ImportError:
            import amo_narration_policy as _policy
        return bool(_policy.enabled(plugin))
    except Exception as exc:  # cleanup: 读不到按关处理
        logger.debug("[amo] 动作开关读取失败: %s", exc)
        return False

try:  # 话题锁定 / 状态衔接 / 连续对话 三段提示词
    from . import amo_message as message_blocks
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_message as message_blocks  # type: ignore[no-redef]

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        AstrMessageEvent,
        STYLE_HINTS,
        TextPart,
        extract_persona_name,
        filter,
        logger,
        now_ts,
        to_float,
        truncate,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            AstrMessageEvent,
            STYLE_HINTS,
            TextPart,
            extract_persona_name,
            filter,
            logger,
            now_ts,
            to_float,
            truncate,
        )
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_shared import (
                AstrMessageEvent,
                STYLE_HINTS,
                TextPart,
                extract_persona_name,
                filter,
                logger,
                now_ts,
                to_float,
                truncate,
            )

try:  # 短回复人设：prompt 段 + 代码兜底
    from . import short_reply
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import short_reply  # type: ignore[no-redef]

try:  # 拒绝权：底线/身体/情绪
    from . import refusal
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import refusal  # type: ignore[no-redef]

if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object



#: 常量名保留给老调用方/测试；正文由注册表唯一维护（Phase 4）。
SWIM_PHOTO_WORDS = ("比基尼", "泳衣", "泳装", "泳裤", "三点式")


def restore_swim_outfit(plugin):
    """泳装只是临时换：拍完/下一张照片前换回当天白天或晚上那套。"""
    try:
        wardrobe = getattr(plugin, "wardrobe", None)
        if wardrobe is not None:
            wardrobe.current()
    except Exception as exc:
        _note_swallowed("amo_llm.swim_photo.restore", exc)
    plugin._swim_photo_until = 0


async def restore_swim_later(plugin, delay=200):
    import asyncio
    await asyncio.sleep(delay)
    if now_ts() < to_float(getattr(plugin, "_swim_photo_until", 0), 0):
        return
    restore_swim_outfit(plugin)


def swim_photo_policy(text, zone, sick):
    """他点名要泳装/比基尼时的口径：在家且没病才换；病着可拒绝；在外面换不了。"""
    lowered = str(text or "").lower()
    if not any(word in lowered for word in SWIM_PHOTO_WORDS):
        return ""
    if sick:
        return ("【他要看泳装】你正病着/不舒服，可以拒绝，但要用你自己的语气：撒娇、病恹恹地推掉，"
                "例如「等我好了嘛…现在头还晕着呢」，1-2 句就够；别客服腔、别讲道理、别写小作文；"
                "也别假装已经拍了。")
    if str(zone or "") != "home":
        return ("【他要看泳装】你现在不在家，没法换泳装；用你的语气说清楚「我这在外面呢，怎么换嘛，回家再说」，"
                "可以带点无奈/好笑；别答应现在拍，也别编造已经换了；不要像在复述规则。")
    return ("【他要看泳装】你在家也没生病：这次别拒绝、别推三阻四，答应换泳装拍；"
            "先像你本人那样逗他一句或直接答应（例如「你就惦记这个…行吧，就一张啊」），别往回缩，也别只回“好/可以”；"
            "照片就穿衣柜里那套泳装，场景说在家/卧室或泳池边都行；"
            "不要换成长袖/病号服，也不要说“下次”。")


def swim_photo_rule(plugin, text):
    """计算泳装照片口径；在家且健康时顺手把生图锁切到泳装。"""
    lowered = str(text or "").lower()
    if not any(word in lowered for word in SWIM_PHOTO_WORDS):
        if to_float(getattr(plugin, "_swim_photo_until", 0), 0):
            restore_swim_outfit(plugin)
        return ""
    try:
        from .amo_state_snapshot import build_snapshot
    except ImportError:
        from amo_state_snapshot import build_snapshot
    try:
        snap = build_snapshot(plugin) or {}
    except Exception as _amo_exc:
        _note_swallowed("amo_llm.swim_photo.snapshot", _amo_exc)
        snap = {}
    health = snap.get("health") or {}
    status = str(health.get("status") or "健康")
    sick = bool(health.get("sick")) or status not in ("", "健康")
    zone = str(snap.get("zone") or "unknown")
    policy = swim_photo_policy(text, zone, sick)
    if policy and not sick and zone == "home":
        try:
            wardrobe = getattr(plugin, "wardrobe", None)
            swim = wardrobe.swim_outfit() if wardrobe is not None else {}
            # 他点名「蓝色/黑色…」时优先挑同色那套：否则按日期随机那套可能不是他要的颜色。
            try:
                color = next((item for item in ("蓝", "黑", "白", "粉", "绿", "紫", "黄", "红")
                              if item in lowered), "")
                if color and wardrobe is not None:
                    match = None
                    for entry in wardrobe.swim_sets():
                        if color in str(entry.get("top") or "") or color in str(entry.get("set") or ""):
                            match = entry
                            break
                    if match and match.get("top_id") and match.get("bottom_id"):
                        wardrobe.set_swim(top=match["top_id"], bottom=match["bottom_id"])
                        swim = wardrobe.swim_outfit()
            except Exception as _amo_exc:
                _note_swallowed("amo_llm.swim_photo.color", _amo_exc)
            if (wardrobe is not None and isinstance(swim, dict)
                    and swim.get("text") and not swim.get("pending")):
                wardrobe._sync_sidecar(swim)
                plugin._swim_photo_until = now_ts() + 180
                policy += "这次穿的是：%s。拍完就换回今天这套，别一直穿着。" % str(swim.get("text"))
                try:
                    import asyncio
                    plugin._swim_restore_task = asyncio.get_running_loop().create_task(
                        restore_swim_later(plugin))
                except Exception as _amo_exc:
                    _note_swallowed("amo_llm.swim_photo.schedule", _amo_exc)
        except Exception as _amo_exc:
            _note_swallowed("amo_llm.swim_photo.sidecar", _amo_exc)
    return policy


PHOTO_REQUEST_RULE = _prompts.render("llm.photo_request")

#: 固定动作归属规则：只加一次，不随每轮变化，避免破坏 system prompt 缓存。
ACTION_OWNERSHIP_RULES = _prompts.render("llm.action_ownership")

#: 分居档居住边界（household 读不到时的兜底；正常走 amo_household.wording("boundary")）。
APART_RESIDENCE_RULE = _prompts.render("llm.residence_apart")


class LlmMixin(_MixinBase):
    if TYPE_CHECKING:  # 宿主成员（插件主类/其它 mixin 提供）：严格口径用声明而不是 ignore
        _send_proactive_lines: Any
        wallet: Any
    persona_name: str

    async def _persona_name_for(self, umo=""):
        """读取当前会话的人格名；读不到时返回空字符串。"""
        if not self.cfg_bool("use_persona_name", True):
            return ""
        key = str(umo or "default")
        cached = self._persona_name_cache.get(key)
        if cached and now_ts() - cached[0] < 300:
            return cached[1]
        manager = getattr(self.context, "persona_manager", None)
        persona = None
        if manager is not None:
            try:
                conversation_persona_id = None
                cm = getattr(self.context, "conversation_manager", None)
                if cm is not None:
                    cid = await cm.get_curr_conversation_id(key)
                    if cid:
                        conv = await cm.get_conversation(key, cid)
                        conversation_persona_id = getattr(conv, "persona_id", None)
                platform_name = key.split(":", 1)[0] if ":" in key else ""
                _, persona, _, _ = await manager.resolve_selected_persona(
                    umo=key,
                    conversation_persona_id=conversation_persona_id,
                    platform_name=platform_name,
                    provider_settings={},
                )
            except Exception as exc:
                logger.debug("[amo] 解析当前人格失败: %s" % exc)
            if persona is None:
                try:
                    persona = await manager.get_default_persona_v3(key or None)
                except Exception as exc:
                    logger.debug("[amo] 读取默认人格失败: %s" % exc)
        name = extract_persona_name(persona)
        self._persona_name_cache[key] = (now_ts(), name)
        return name

    async def _sync_persona_name(self, event_or_umo):
        umo = getattr(event_or_umo, "unified_msg_origin", event_or_umo)
        try:
            name = await self._persona_name_for(umo)
        except Exception as exc:
            logger.debug("[amo] 同步人格名失败: %s" % exc)
            return
        if not name:
            return
        changed = name != self.persona_name
        self.persona_name = name
        self.bot_name = name
        if self.heartflow is not None:
            self.heartflow.set_identity(self.bot_name, self.bot_aliases)
        # worldview 挂在 HumanState 上（不是插件本身）：热重载后第一条消息可能比
        # _ensure_ready 里的 worldview 初始化更早，直接 self.worldview 会 AttributeError，
        # 整条消息被吞掉（2026-11-12 线上实测）。这里按 state 取，取不到就跳过。
        worldview = getattr(getattr(self, "state", None), "worldview", None)
        if changed and isinstance(worldview, dict) and worldview.get("name") != name:
            worldview["name"] = name
            try:
                self.db.kv_set("worldview", worldview)
            except Exception as _amo_exc:
                _note_swallowed("amo_llm.LlmMixin._sync_persona_name", _amo_exc)



    def _detect_impersonation(self, event):
        """群里非恋人 UID 使用/冒充恋人的名字或爱称时，5 分钟内累计降级。"""
        group_id = str(event.get_group_id() or "")
        if not group_id:
            return False
        uid = self._canonical_uid(event)
        lovers = self._lover_aliases()
        if not lovers or uid in set(lovers.values()) or self._is_registered_lover(uid):
            return False
        sender_name = str(event.get_sender_name() or "").strip()
        text = str(event.message_str or "").strip()
        hit = ""
        if sender_name in lovers:
            hit = sender_name
        elif text in lovers:
            hit = text
        else:
            for alias in lovers:
                if re.search(r"(?:我才是|我是|叫我|叫我做|以后叫我|我才是那个)\s*" + re.escape(alias), text):
                    hit = alias
                    break
        if not hit:
            return False
        setattr(event, "amo_impersonation", True)
        key = "impersonate_%s_%s" % (group_id, uid)
        now = now_ts()
        stamps = self.db.kv_get(key, [])
        if not isinstance(stamps, list):
            stamps = []
        stamps = [to_float(item, 0) for item in stamps if now - to_float(item, 0) <= 300]
        stamps.append(now)
        self.db.kv_set(key, stamps)
        count = len(stamps)
        profile = self.db.get_profile(uid) or {}
        current = str(profile.get("relation") or "普通")
        relation = current
        if count >= 3 and current != "仇恨":
            relation = "仇恨"
        elif count == 1 and current != "仇恨":
            relation = "抵触"
        if relation != current:
            self.db.update_profile(uid, relation=relation)
            logger.info(
                "[amo] 识别到冒充恋人称呼: %s -> %s（%s，5分钟内第%d次）"
                % (uid, relation, hit, count)
            )
        return True

    def _group_reply_guide(self, relation, name):
        """按关系给一句“该怎么回”的指引。"""
        relation = str(relation or "普通")
        if relation == "相恋":
            return "对" + name + "可以亲近、撒娇、用昵称，但别在群里太露骨。"
        if relation == "熟识":
            return "对" + name + "像老朋友一样随便聊，可以开玩笑，但不用恋人语气。"
        if relation == "抵触":
            return "对" + name + "有点距离感，句子短，不主动热络。"
        if relation == "仇恨":
            return "对" + name + "很冷淡，不接他的茬，也不解释。"
        return "对" + name + "客气一点、像普通群友，不要叫老公/宝贝，也别装得很熟。"
















    def _trim_reply(self, text):
        value = self._strip_place_marks(str(text or "").strip())
        limit = max(1, self.cfg_int("reply_max_sentences", 3))
        parts = [item for item in re.split(r"(?<=[。！？!?~～])", value) if item.strip()]
        if len(parts) <= limit:
            return value
        return "".join(parts[:limit]).strip()

    async def _claim_retry(self, reply, umo, system, prompt):
        """消费口嗨：先让模型重写（最多 N 次），仍不改再交给确定性改写（2026-11-15）。

        线上事故：日程说在家做饭，回复却说「点了个外卖」。这里在 polish 之前先拦一道，
        命中就把"系统事实里没有的消费"原样告诉模型并重试，默认最多 2 次。
        """
        value = str(reply or "").strip()
        if not value:
            return value
        try:
            try:
                from .amo_fact_binder import ban_words as _ban_words
            except ImportError:
                from amo_fact_binder import ban_words as _ban_words
            try:
                from .amo_fact_binder import guard as _claim_guard
            except ImportError:
                from amo_fact_binder import guard as _claim_guard
        except Exception:
            return value
        try:
            times = int(self.cfg_int("claim_retry_times", 2))
        except Exception:
            times = 2
        times = max(0, min(3, times))
        try:
            limit = self.cfg_int("max_reply_chars", 220)
        except Exception:
            limit = 220
        for attempt in range(times):
            try:
                _fixed, changed, why = _claim_guard(value, self)
            except Exception:
                return value
            if not changed:
                return value
            try:
                bans = "、".join(_ban_words(self))
            except Exception:
                bans = "外卖、骑手"
            retry_prompt = (
                prompt + "\n【重写要求】上一条回复里有系统事实中不存在的消费或行踪（" + why
                + "），这是编的。只重写这一条：不许出现“" + bans
                + "”，也不许说自己点了/买了/刚吃完/在等。只输出要说的话，1-2 句。"
            )
            try:
                candidate = await self.llm.chat(umo, system, retry_prompt, limit=limit)
            except Exception:
                break
            candidate = str(candidate or "").strip()
            if not candidate:
                break
            value = candidate
            logger.info("[CLAIM_GUARD] 重试 #%d：%s" % (attempt + 1, why))
        try:
            return _claim_guard(value, self)[0]
        except Exception:
            return value

    async def _compose_reply(self, event, text, snap, profile, memories, memory_text, group, note=""):
        """回复组装：准备 → 系统提示词 → 提问 → 润色与守卫（三段各自可读）。"""
        if not group:
            # A2：他这一轮开口了，先判上一轮她那条回复的反应（正向才入池）。
            try:
                try:
                    from . import amo_persona_evolve as _evolve
                except ImportError:
                    import amo_persona_evolve as _evolve
                _evolve.review_pending(self, text, group=False)
            except Exception as _amo_exc:  # cleanup: 学习失败不影响回复
                logger.debug("[amo] 人设采集判定失败: %s", _amo_exc)
        ctx = await self._reply_prepare(event, text, snap, profile, group)
        system = await self._reply_system(ctx, event, text, snap, profile, group,
                                          memory_text, note)
        if group:
            prompt = (
                "以下是群聊最近的对话：\n" + ctx["transcript"] + "\n\n"
                + ctx["name"] + "刚刚说：" + text + "\n"
                "群里正在聊天，你决定插一句。只回 1-2 句，不要@任何人，不要重复别人说过的话。"
            )
        else:
            prompt = (
                "以下是你们最近的私聊：\n" + ctx["transcript"] + "\n\n"
                + ctx["name"] + "刚刚说：" + text + "\n"
                "请以角色身份自然回复。默认 1-2 句，能短就短；咨询类最多 3 句，不要客服腔。"
            )
        reply = await self.llm.chat(
            ctx["umo"], system, prompt, limit=self.cfg_int("max_reply_chars", 220)
        )
        reply = await self._claim_retry(reply, ctx["umo"], system, prompt)
        if reply:
            reply = await self._reply_postprocess(reply, ctx, event, text, snap, group)
            try:
                enforcer = getattr(getattr(self, "draw", None), "enforce_photo_claim", None)
                _added_photo = False
                if callable(enforcer):
                    reply, _added_photo = enforcer(event, reply)
                # 防线三：意图词（拍一张/发给你/你看这张…）也硬拦，不只是「拍好了」这种完成态
                if not _added_photo:
                    reply = self._enforce_photo_intent(event, reply)
                # 他明确要照片：不管模型说没说「拍好了」，都补一张真图（一小时内只补一次）
                try:
                    if (not _added_photo and self.draw is not None
                            and self.draw.wants_photo(event)
                            and not self._photo_already_sent(event)):
                        last = float(getattr(self.draw, "_last_tool_photo_at", 0) or 0)
                        if not last or time.monotonic() - last > 60:
                            self.draw.send_photo_now(event, str(getattr(event, "message_str", "") or "拍一张自拍发给他"),
                                                     kind="real", force=True)
                except Exception as _amo_exc:
                    logger.debug("[amo] 明确要图兜底失败: %s" % _amo_exc)
            except Exception as _amo_exc:
                logger.debug("[amo] 假拍照兜底失败: %s" % _amo_exc)
        return reply

    def _photo_already_sent(self, event):
        """这一轮是不是已经有图发出：模型直接调生图插件 / amo 刚发过。

        2026-11-19 09:12 事故：amo 在 on_llm_request 里先发一张脚趾图，主模型这一轮
        又调了生图插件的 generate_selfie，同一轮发两张。现在提前发送改到回复后兜底，
        这里用生图插件记的 umo 证据 + 自己的最近发送时间统一判重。
        """
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        photo = getattr(self, "photo", None)
        guard = getattr(photo, "photo_guard_state", None)
        if callable(guard) and umo:
            try:
                if guard(umo) == "ok":
                    return True
            except Exception as _amo_exc:
                _note_swallowed("amo_llm.photo_guard", _amo_exc)
        draw = getattr(self, "draw", None)
        last = float(getattr(draw, "_last_tool_photo_at", 0) or 0)
        return bool(last and time.monotonic() - last <= 120)

    def _enforce_photo_intent(self, event, reply):
        """防线三：命中拍照意图词却没真发图 → 强制补一张；发不出来就删掉口头承诺。

        以前只拦「拍好了/咔嚓」这种完成态，她说「我拍一张发给你」但没调工具就穿帮了。
        """
        try:
            from .amo_immersion_guard import photo_intent, strip_photo_promise
        except ImportError:
            from amo_immersion_guard import photo_intent, strip_photo_promise
        body = str(reply or "")
        if not body or not photo_intent(body):
            return body
        draw = getattr(self, "draw", None)
        if draw is None:
            return strip_photo_promise(body)
        if LlmMixin._photo_already_sent(self, event):
            return body
        sent = False
        try:
            if getattr(draw, "enabled", True):
                last = float(getattr(draw, "_last_tool_photo_at", 0) or 0)
                if last and time.monotonic() - last < 60:
                    return body  # 60 秒内刚发过一张，文本里的「发你」不算穿帮
                target = draw.send_photo_now(event, str(getattr(event, "message_str", "") or "拍一张自拍发给他"), kind="real", force=True)
                sent = any(word in str(target or "") for word in ("拍一张", "发你", "找张图", "已安排", "已收到"))
        except Exception as _amo_exc:
            logger.debug("[amo] 意图拍照兜底失败: %s" % _amo_exc)
            sent = False
        if sent:
            return body
        logger.info("[amo] 口头拍照已确定性改写（发图失败）: %s" % body[:60])
        return strip_photo_promise(body)

    async def _reply_prepare(self, event, text, snap, profile, group):
        """回复组装第一步：身份/语境/近期对话/余额/沉默天数（原 _compose_reply 前 56 行）。"""
        umo = event.unified_msg_origin
        uid = self._canonical_uid(event)
        scene = "private"
        is_owner = self._is_registered_lover(uid)
        sender_name = str(event.get_sender_name() or profile.get("name") or "群友").strip()
        owner_name = ""
        name = profile.get("alias") or profile.get("name") or event.get_sender_name() or "对方"
        relation = profile.get("relation", "普通")
        persona = await self.llm.persona(umo)
        world = self.state.worldview_text()
        asked_activity = message_blocks.asks_about_activity(text)
        state_text = self.state.prompt_text(snap, with_activity=asked_activity)
        tone = self._tone_with_continuity(umo, self.state.tone_text(snap, relation))
        style = STYLE_HINTS.get(self.cfg_str("reply_style", "soft"), "")
        consult = bool(re.search(r"(怎么|如何|为什么|步骤|怎么办|该怎么做|教程|方法|建议|教我|能不能|是什么|该不该|可不可以)", text or ""))
        recent = self._recent(umo, 10)
        lines = []
        current_uid = self._canonical_uid(event)
        current_place = str((snap.get("activity") or {}).get("place") or "")
        for item in recent:
            content = str(item.get("content") or "")
            role = str(item.get("role") or "")
            item_uid = str(item.get("uid") or "")
            # 当前明明在家/公司时，不把历史里“酒店/开车/逛景区”的旧幻觉再喂回模型。
            if (
                role != "user"
                and current_place in ("家", "公司", "工位")
                and re.search(r"(酒店|咖啡店|咖啡|开着车|在开车|逛完|旅游|景区)", content)
            ):
                continue
            if role == "user":
                if item_uid and item_uid != current_uid:
                    who = (str(item.get("name") or "群友") + "（另一位群友）")
                else:
                    who = name
            else:
                who = "你"
            lines.append(who + "：" + truncate(content, 100))
        transcript = "\n".join(lines[-8:])[:1200]
        self_name, self_uid, self_qq_nick, self_display = await self._self_identity(event)
        sender_nick = str(event.get_sender_name() or profile.get("name") or uid).strip()
        plat = self._platform_label(self._platform_of(event))
        try:
            cash_cents = int(self.wallet.balance())
        except Exception:
            cash_cents = 0
        silence_days = 0
        try:
            last_seen = to_float(profile.get("last_seen"), 0)
            if last_seen > 0:
                silence_days = max(0, int((now_ts() - last_seen) / 86400))
        except Exception:
            silence_days = 0
        last_refusal = self.db.kv_get("refusal:last", {}) or {}
        if not isinstance(last_refusal, dict):
            last_refusal = {}
        coax = refusal.is_coax(text)
        # task-28 P0-5：她最近 3 条自己发的话，作为「不可否认」硬清单进 prompt / critic
        try:
            try:
                from .amo_persona_guard import self_recent_texts as _self_recent_texts
            except ImportError:
                from amo_persona_guard import self_recent_texts as _self_recent_texts
            self_texts = _self_recent_texts(recent, 3)
        except Exception as _amo_exc:
            logger.debug("[amo] 取自我发言清单失败: %s" % _amo_exc)
            self_texts = []
        return {
            "umo": umo, "uid": uid, "scene": scene, "is_owner": is_owner,
            "sender_name": sender_name, "owner_name": owner_name, "name": name,
            "relation": relation, "persona": persona, "world": world,
            "asked_activity": asked_activity, "state_text": state_text, "tone": tone,
            "style": style, "consult": consult, "recent": recent,
            "self_texts": self_texts,
            "transcript": transcript, "self_name": self_name, "self_uid": self_uid,
            "self_qq_nick": self_qq_nick, "self_display": self_display,
            "sender_nick": sender_nick, "plat": plat, "cash_cents": cash_cents,
            "silence_days": silence_days, "last_refusal": last_refusal, "coax": coax,
            "understanding": await _understanding.build_smart(self, text, umo, recent),
        }

    async def _reply_system(self, ctx, event, text, snap, profile, group, memory_text, note):
        """回复组装第二步：拼系统提示词（人格/状态/群聊/各种条件块）。"""
        system_parts = self._reply_base_parts(ctx, snap, text, group, memory_text)
        if group:
            system_parts.extend(self._reply_group_parts(ctx, event, group))
        system_parts.extend(await self._reply_conditional_parts(
            ctx, event, text, snap, profile, group, memory_text))
        _allow_action = _narration_on(self)
        system_parts.append(_prompts.render("llm.rule.core", allow_action=_allow_action))
        if note:
            # 延迟补话的说明放在系统侧，不能混进“对方刚刚说”里
            system_parts.append(_prompts.render("llm.note", note=note))
        # 居住边界（分居/半同居/同居各一份）+ 假拍照/发图/动作归属：都是固定块，
        # 只加一次、不随每轮变化，不破坏 system prompt 前缀缓存。
        # 2026-11-24：居住边界迁到 amo_household.wording("boundary")（apart 也有文案了），
        # 这里不再拿 ACTION_OWNERSHIP_RULES 当回落。
        _boundary = ""
        try:
            try:
                from . import amo_household as _hh
            except ImportError:
                import amo_household as _hh
            _boundary = _hh.wording("boundary", plugin=self)
        except Exception:
            _boundary = ""
        system_parts.append(_boundary or APART_RESIDENCE_RULE)
        system_parts.append(ACTION_OWNERSHIP_RULES)
        system_parts.append(_prompts.URGE_DISCIPLINE_RULES)
        if not group:
            # 撒娇版放最后：越靠后的指令权重越高；示例只学语气。
            system_parts.append(_prompts.render("llm.rule.style", allow_action=_allow_action))
            # A4：优先她自己的池（每意图最高 2 条）；空池回落固定 33 条，冷启动不空窗。
            _style_text = ""
            try:
                try:
                    from .amo_persona_evolve import learned_style_block as _learned_style
                except ImportError:
                    from amo_persona_evolve import learned_style_block as _learned_style
                _style_text = _learned_style(self, str(text or ""))
            except Exception as _amo_exc:  # cleanup: 示例池缺失不影响回复
                logger.debug("[amo] learned style skipped: %s", _amo_exc)
                _style_text = ""
            if not _style_text:
                try:
                    from .amo_persona_pack import style_block as _style_block
                except ImportError:
                    from amo_persona_pack import style_block as _style_block
                try:
                    _style_text = _style_block(str(text or ""))
                except Exception as _amo_exc:  # cleanup: 固定池也缺失就不注入
                    logger.debug("[amo] style examples skipped: %s", _amo_exc)
                    _style_text = ""
            if _style_text:
                system_parts.append(_style_text)
        system = "\n\n".join(system_parts)
        return system

    def _persona_pack_blocks(self, ctx, snap):
        """人设包：内核/关系语气/语言规则/禁语/少量语料。"""
        try:
            try:
                from .amo_persona_store import active_pack, load_state, mark_used
                from .amo_persona_pack import prompt_block
            except ImportError:
                from amo_persona_store import active_pack, load_state, mark_used
                from amo_persona_pack import prompt_block
            mood = ""
            try:
                mood = str((snap or {}).get("dominant") or "")
            except Exception:
                mood = ""
            scene = ""
            try:
                scene = str(((snap or {}).get("activity") or {}).get("zone") or "")
            except Exception:
                scene = ""
            try:
                _state = load_state(self) or {}
                last_used = dict(_state.get("last_used") or {})
                # 基础包语料是只读的，last_used 只在有进化/池内句子时才需要落盘；
                # 否则每条私聊都写一次 state.json（P2：避免无意义磁盘写）。
                mutable_texts = {
                    str(row.get("text") or "")
                    for row in list(_state.get("corpus_evolved") or [])
                    + list(_state.get("style_pool") or [])
                    if isinstance(row, dict)
                }
            except Exception:
                last_used = {}
                mutable_texts = set()
            used: list[str] = []
            block = prompt_block(active_pack(self), scene=scene, mood=mood,
                                 register="partner_safe", last_used=last_used,
                                 used_out=used, allow_action=_narration_on(self))
            if used and any(text in mutable_texts for text in used):
                try:
                    mark_used(self, used)
                except Exception as _amo_exc:  # cleanup: 记录 last_used 失败不影响注入
                    logger.debug("[amo] 记录 last_used 失败: %s", _amo_exc)
            return ["【人格包】\n" + block] if block else []
        except Exception as exc:
            logger.debug("[amo] 人设包注入失败: %s", exc)
            return []

    def _reply_base_parts(self, ctx, snap, text, group, memory_text):
        _understanding_block = ""
        try:
            try:
                from .amo_understanding import prompt_block as _understanding_prompt
            except ImportError:
                from amo_understanding import prompt_block as _understanding_prompt
            _understanding_block = _understanding_prompt(ctx.get("understanding") or {})
        except Exception:
            _understanding_block = ""
        # 人格/身份/世界观/此刻状态/事实锁定 + 拒绝/短回复/对话护栏
        uid = ctx["uid"]; name = ctx["name"]; relation = ctx["relation"]
        persona = ctx["persona"]; world = ctx["world"]; asked_activity = ctx["asked_activity"]
        state_text = ctx["state_text"]; tone = ctx["tone"]; style = ctx["style"]
        self_name = ctx["self_name"]; self_uid = ctx["self_uid"]
        self_qq_nick = ctx["self_qq_nick"]; self_display = ctx["self_display"]
        sender_nick = ctx["sender_nick"]; plat = ctx["plat"]
        cash_cents = ctx["cash_cents"]; silence_days = ctx["silence_days"]
        last_refusal = ctx["last_refusal"]; coax = ctx["coax"]
        system_parts = [
            refusal.ANCHOR,
            persona,
            _prompts.render("llm.self_role", self_name=self_name),
            _prompts.render("llm.self_account", self_qq_nick=self_qq_nick,
                            self_display=self_display, self_uid=self_uid),
            _prompts.render("llm.nickname_distinction", self_qq_nick=self_qq_nick,
                            self_display=self_display, self_name=self_name),
            _prompts.render("llm.partner", plat=plat, sender_nick=sender_nick,
                            uid=uid, name=name),
            _prompts.render("llm.identity_distinction", self_uid=self_uid, uid=uid),
            _prompts.render("llm.no_backend"),
            _prompts.render("llm.self_recent", self_texts=ctx.get("self_texts") or []),
            _prompts.render("llm.role_boundary"),
            _prompts.render("llm.body_ownership"),
            _prompts.render("llm.quote_rule"),
            _prompts.render("llm.worldview", world=world),
            _prompts.render("llm.state", state_text=state_text),
            _prompts.render("llm.fact_lock"),
            *([_understanding_block] if _understanding_block else []),
            _prompts.render("llm.tone", tone=tone, style=style),
            _prompts.render("llm.relation", name=name, relation=relation),
            _prompts.render("llm.chat_format"),
            _prompts.render("llm.length"),
            _prompts.render("llm.persona"),
            *self._persona_pack_blocks(ctx, snap),
            refusal.prompt_block(snap, text, memory_text, cash_cents, group,
                                   _now().hour, silence_days, last_refusal, coax),
            short_reply.prompt_block(snap, text, allow_action=_narration_on(self)),
            _prompts.render("llm.catch_answer"),
            *message_blocks.dialogue_guard_blocks(snap, asked_activity),
        ]
        # 口头禅规则（提示词侧）：文案在 tone_guard，这里只负责注入
        try:
            try:
                from . import amo_reply_pipeline as _tone_pipeline
            except ImportError:
                import amo_reply_pipeline as _tone_pipeline
            _tone_rule = str(_tone_pipeline.tone_guard_prompt_rule() or "")
            if _tone_rule:
                system_parts.append(_tone_rule)
        except Exception as _amo_exc:
            logger.debug("[amo] 口头禅规则注入失败: %s" % _amo_exc)
        # 防线二/四：情绪安全阀 + 熟人名单 + 身份视角（所有聊天 Prompt 必带）
        try:
            from .amo_immersion_guard import emotion_ice_point, ice_break_line
        except ImportError:
            from amo_immersion_guard import emotion_ice_point, ice_break_line
        system_parts.append(_prompts.render("guard.immersion"))
        if emotion_ice_point((snap or {}).get("mood") or {}):
            system_parts.append(ice_break_line())
        # 第四轮防穿帮：过渡中（起床/出门/在路上）不许直接说已经到目的地
        try:
            _transit_text = _prompts.render("guard.transit", plugin=self)
            if _transit_text:
                system_parts.append(_transit_text)
        except Exception as _amo_exc:
            logger.debug("[amo] 过渡状态注入失败: %s" % _amo_exc)
        return system_parts

    def _reply_group_parts(self, ctx, event, group=True):
        # 群聊已整体删除：只保留私聊，这里不再拼任何群场景。
        return []

    async def _reply_conditional_parts(self, ctx, event, text, snap, profile, group, memory_text=""):
        # 私聊场景 + 别报日程 + 咨询/方言/情绪/冒充/识人/景区/身份/打扰/低落/关系网/记忆
        umo = ctx["umo"]; uid = ctx["uid"]; name = ctx["name"]; relation = ctx["relation"]
        consult = ctx["consult"]
        system_parts = []
        if not group:
            system_parts.append(_prompts.render("group.scene.private"))
        system_parts.append(_prompts.render("llm.no_schedule_dump"))
        if consult:
            system_parts.append(_prompts.render("llm.consult"))
        if relation == "相恋" and float((snap.get("mood") or {}).get("压力") or 0) >= 60:
            system_parts.append(_prompts.render("llm.love_anger"))
        if getattr(event, "amo_impersonation", False):
            system_parts.append(_prompts.render("llm.impersonation"))
        if self._recent_bot_initiated(umo):
            quote = ""
            getter = getattr(self, "_bot_initiated_quote", None)
            if callable(getter):
                quote = getter(umo)
            system_parts.append(_prompts.render("llm.bot_initiated", quote=quote))
        if re.search(r"(记住|以后喊|以后叫|喊他|喊她|叫他|叫她|称呼他|称呼她)", text or ""):
            system_parts.append(_prompts.render("llm.remember_name"))
        if re.search(r"(旅游|景区|景点|去哪|好玩|假期|游玩)", text or ""):
            scenic_text = await self._scenic_kb_context(text or "")
            if scenic_text:
                system_parts.append(_prompts.render("llm.scenic", scenic_text=scenic_text))
        if relation == "相恋":
            system_parts.append(_prompts.render("llm.affection"))
        if bool(re.search(r"(?<![a-z])ai(?![a-z])", (text or "").lower())) or any(word in (text or "").lower() for word in ("机器人", "人机", "人工智能", "不是真人", "程序")):
            system_parts.append(_prompts.render("llm.identity_challenge"))
        if self._disturb_active(umo):
            relation_now = profile.get("relation", "普通")
            if relation_now in ("相恋", "熟识"):
                system_parts.append(_prompts.render("llm.disturbed_close"))
            else:
                system_parts.append(_prompts.render("llm.disturbed_stranger"))
        if self._depressed_active(uid):
            system_parts.append(_prompts.render("llm.depressed"))
        if memory_text:
            system_parts.append(_prompts.render("llm.memory", name=name, memory_text=memory_text))
        return system_parts


    async def _reply_postprocess(self, reply, ctx, event, text, snap, group):
        """回复组装第三步：润色 + 各道守卫（日期/拒绝/地点/消费/群聊）。"""
        umo = ctx["umo"]; name = ctx["name"]; relation = ctx["relation"]
        persona = ctx["persona"]; state_text = ctx["state_text"]; tone = ctx["tone"]
        style = ctx["style"]; consult = ctx["consult"]
        cash_cents = ctx["cash_cents"]; silence_days = ctx["silence_days"]
        if reply:
            reply = await self.llm.polish(
                umo, reply, tone, state_text=state_text, relation=relation, name=name,
                persona=persona, style=style, consult=consult, public=bool(group),
                user_text=str(text or ""), self_texts=ctx.get("self_texts") or [],
            )
            reply = self._trim_reply(reply)
            try:
                from .date_state import guard as _date_guard
            except ImportError:
                from date_state import guard as _date_guard
            _before = reply
            reply = _date_guard(reply, self)
            if reply != _before:
                logger.info("[DATE_GUARD] 约会后失忆回复已拦截")
            _refuse, _reason = refusal.should_refuse(
                snap, text, cash_cents, _now().hour, silence_days)
            if _refuse:
                reply = refusal.guard(reply, snap, text, cash_cents, _now().hour, silence_days)
                if refusal.is_refusal_reply(reply):
                    try:
                        self.db.kv_set("refusal:last", {"ts": now_ts(), "reason": _reason})
                    except Exception as exc:
                        _note_swallowed("amo_llm.py.553", exc)
            # 2026-11-15：嘴里说的消费必须账上查得到，编出来的直接改写
            reply = self._guard_consumption(reply)
        if reply:
            try:
                from .amo_message import looks_like_photo_resend_request
            except ImportError:
                from amo_message import looks_like_photo_resend_request
            if looks_like_photo_resend_request(text):
                photo = getattr(getattr(self, "p", None), "photo", None)
                resend = getattr(photo, "resend_last", None)
                if callable(resend):
                    try:
                        if await resend(umo):
                            reply = "喏，刚才那张，看吧。"
                            logger.info("[PHOTO] 他要重发，已发上一张，不重新生图")
                    except Exception as exc:
                        logger.warning("[amo] 重发照片失败: %s", exc)
            draw = getattr(getattr(self, "p", None), "draw", None)
            retry_guard = getattr(draw, "enforce_photo_retry", None)
            if callable(retry_guard):
                try:
                    reply = retry_guard(event, text, reply)
                except Exception as exc:
                    logger.warning("[amo] 补拍兜底失败: %s", exc)
        return reply


    def _remember_sent_reply(self, event, text):
        """分段引擎没接手时（关掉分段 / 发送异常）的兜底记录。"""
        msg_obj = getattr(event, "message_obj", None)
        if msg_obj is not None and not getattr(msg_obj, "message_id", None):
            return  # 别的插件伪造的主动消息事件，不是这一轮的正常回复
        self._remember_bot_message(event.unified_msg_origin, text)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        try:
            await self._ensure_ready()
            if not self._ready or self.state is None:
                return
            if self._looks_like_command(event.message_str or ""):
                return
            context_text = await self._dynamic_context(event)
            # 固定动作归属块只加一次，不随每轮变化；不破坏 system prompt 前缀缓存。
            try:
                system_prompt = str(getattr(req, "system_prompt", "") or "")
                if "【动作归属·最高优先级】" not in system_prompt:
                    _boundary = ""
                    try:
                        try:
                            from . import amo_household as _hh
                        except ImportError:
                            import amo_household as _hh
                        _boundary = _hh.wording("boundary", plugin=self)
                    except Exception:
                        _boundary = ""
                    req.system_prompt = (system_prompt + (_boundary or APART_RESIDENCE_RULE)
                                         + ACTION_OWNERSHIP_RULES).lstrip()
            except Exception as _amo_exc:
                _note_swallowed("amo_llm.LlmMixin.on_llm_request.system", _amo_exc)
            # 只走官方推荐的 extra_user_content_parts（临时 part）注入。
            # 绝不能放每轮都变的内容到 req.system_prompt；这里只加固定规则。
            try:
                from .amo_state_snapshot import build_snapshot as _build_snapshot
                from .amo_state_snapshot import snapshot_text as _snapshot_text
                _state_block = _snapshot_text(_build_snapshot(self))
            except Exception:
                _state_block = ""
            try:
                from .amo_date_context import prompt_block as _date_prompt_block
                _date_block = _date_prompt_block(self)
            except Exception:
                _date_block = ""
            body = (
                "<amo_state>\n"
                "[系统内部状态，不是用户发言；本块里的“你”=bot，“他”=用户]\n"
                + (("【最高事实】\n" + _state_block + "\n") if _state_block else "")
                + (("【约会状态】\n" + _date_block + "\n") if _date_block else "")
                + "【动作归属】用户说“发张自拍/拍张照片/帮我拍”是让你拍，执行者是你（bot），"
                "不是用户；禁止说“你拍/你快点拍/你自己拍”。\n"
                + context_text + "\n</amo_state>"
            )
            try:
                if self.draw is not None:
                    _text = str(getattr(event, "message_str", "") or "")
                    if self.draw.wants_photo(event):
                        body += PHOTO_REQUEST_RULE
                        try:
                            if self.photo is not None:
                                self.photo.note_llm_handled()
                        except Exception as _amo_exc:
                            _note_swallowed("amo_llm.photo_handled", _amo_exc)
                        logger.info(
                            "[IDENTITY_DEBUG] action=photo executor=bot subject=user request=%s route=normal",
                            truncate(_text, 40),
                        )
                    # 2026-11-19：泳装请求不一定带「拍照」字样（「老婆，蓝色比基尼呢」）。
                    # 这轮只要提到泳装，就把泳装口径 + sidecar 锁上；否则动作写「穿比基尼」，
                    # 生图锁还是日常穿搭，出图就是大T恤。非泳装消息交给 200 秒定时换回。
                    if any(word in _text for word in SWIM_PHOTO_WORDS):
                        try:
                            body += swim_photo_rule(self, _text)
                        except Exception as _amo_exc:
                            _note_swallowed("amo_llm.swim_photo", _amo_exc)
            except Exception as _amo_exc:
                _note_swallowed("amo_llm.LlmMixin.on_llm_request", _amo_exc)
            parts = getattr(req, "extra_user_content_parts", None)
            if parts is None:
                return
            part = None
            if TextPart is not None:
                try:
                    part = TextPart(text=body).mark_as_temp()
                except Exception as _amo_exc:
                    _note_swallowed("amo_llm.LlmMixin.on_llm_request", _amo_exc)
                    part = None
            if part is None:
                # 老版本 AstrBot 拿不到 TextPart：用 dict 形式，同样只在本轮生效
                part = {"type": "text", "text": body, "_no_save": True}
            parts.append(part)
        except Exception as exc:
            logger.warning("[CONTEXT] inject=0 reason=%s", exc)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        if self.heartflow is None:
            return
        try:
            await self.heartflow.on_llm_response(event, resp)
        except Exception as exc:
            logger.debug("[amo] 心流回复记录失败: %s" % exc)
