# -*- coding: utf-8 -*-
"""LlmMixin：人格、LLM 注入、回复组装、冒充检测。"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

import random
import re

try:  # 目标时区下的“现在”（clock 支持面板改时区；直接按文件加载时退回绝对导入）
    from .clock import now as _now
except ImportError:  # 直接按文件加载时
    from clock import now as _now  # type: ignore[no-redef]

try:  # 群聊场景/双轨人设
    from . import group_scene
except ImportError:  # 直接按文件加载时
    import group_scene  # type: ignore[no-redef]

try:  # 话题锁定 / 状态衔接 / 连续对话 三段提示词
    from . import amo_message as message_blocks
except ImportError:  # 直接按文件加载时
    import amo_message as message_blocks  # type: ignore[no-redef]

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        AstrMessageEvent,
        DIALECT_SAMPLES,
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
            DIALECT_SAMPLES,
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
        from amo_shared import (
            AstrMessageEvent,
            DIALECT_SAMPLES,
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
    import short_reply  # type: ignore[no-redef]

try:  # 拒绝权：底线/身体/情绪
    from . import refusal
except ImportError:  # 直接按文件加载时
    import refusal  # type: ignore[no-redef]

if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object



PHOTO_REQUEST_RULE = (
    "\n【拍照请求·执行者是你】他这轮是在让你拍照/发自拍，执行者是你（bot），不是他。"
    "你要自然答应（“好，我拍 / 等我一下 / 这就拍”），说清你现在的场景；"
    "禁止说“你拍 / 你快点拍 / 你自己拍 / 给我看看你的”。"
    "照片由你生成并发送，不是让用户去拍。"
    "这一轮只发一条文字：直接输出回复即可，不要用 send_message_to_user 预发消息（会变成两条）；"
    "一次请求只调用一次 generate_selfie，不要重复生成。"
)

#: 固定动作归属规则：只加一次，不随每轮变化，避免破坏 system prompt 缓存。
ACTION_OWNERSHIP_RULES = "".join((
    "\n【赛博同居·物理边界·最高优先级】你们名义上同居，但现实里他不在你身边、不在你房间里；"
    "不是跨次元的网友，但也不是能随时见到面的同屋人，平时就是手机异地陪伴；"
    "平时只能靠手机/微信聊天，严禁说「他就在旁边」「你转过头看看我」「他抱着我」「屋里就我俩」。"
    "只有日程明确写着“约会/见面”的那段时间，才允许写他坐在你对面、牵你的手；约会结束就恢复异地。"
    "照片里只有你一个人（他不会入镜，你举着手机自拍）；"
    "严禁说「拍到两个人」「你也在画面里」这种穿帮话。\n",
    "【假拍照·红线】你要是说了「拍照 / 自拍 / 按下快门 / 咔嚓 / 发你一张图 / 拍好啦」，"
    "就必须同时真的调用 send_photo 工具（kind=real）生成并发送图片；"
    "不调用工具就绝对不许说「拍好了 / 这张肯定好看」这类话。\n",
    "\n【发图·你自己决定】要不要发图由你判断，不是一聊到吃的/出门就必须配图；"
    "平时纯文字聊天更像真人。只有你觉得「这时候发张图会更好玩/更浪漫/更馋人」时，才调用 send_photo。"
    "聊的是约定/期待（今晚去吃、明天去哪）用 kind=web（网图/截图分享）；"
    "自己真的在吃、在家、在外面，用 kind=real（你自己眼前的实拍）。"
    "发图时先把文字发出去，图会晚几秒单独到，不要在同一条里等图。"
    "\n\n【动作归属·最高优先级】\n"
    "- 你能做：聊天、发照片/自拍、发语音、写日记、改日程、表达情绪。\n"
    "- 用户说“发张自拍 / 拍张照片 / 帮我拍 / 拍给我看看”时，执行者是你（bot），不是用户；"
    "你回答“好，我拍 / 等我一下 / 这就拍”，绝不能说“你拍 / 你快点拍 / 你自己拍 / 给我看看你的”。\n"
    "- 用户的动作和经历（我明天开会、我在吃饭、我刚下车）只属于用户，不是你的动作；"
    "你的动作（我拍、我发、我换衣服、我去上班）只属于你，不要推给用户。\n"
    "- 照片/自拍里的画面主体是你自己（虚拟角色大肥鱼），不是用户；用户不在画面里。\n"
    "【住处·绝不自主搬走】不管房租多高、物价多贵、多缺钱，都不许提回老家、换城市、离开这里；"
    "只有老公明确说搬家才跟着走；缺钱就撒娇求助或说去银行贷一点。\n",
))


class LlmMixin(_MixinBase):
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
                            and self.draw.wants_photo(event)):
                        last = float(getattr(self.draw, "_last_tool_photo_at", 0) or 0)
                        if not last or time.monotonic() - last > 60:
                            self.draw.send_photo_now(event, "拍一张自拍发给他",
                                                     kind="real", force=True)
                except Exception as _amo_exc:
                    logger.debug("[amo] 明确要图兜底失败: %s" % _amo_exc)
            except Exception as _amo_exc:
                logger.debug("[amo] 假拍照兜底失败: %s" % _amo_exc)
        return reply

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
        sent = False
        try:
            if getattr(draw, "enabled", True):
                last = float(getattr(draw, "_last_tool_photo_at", 0) or 0)
                if last and time.monotonic() - last < 60:
                    return body  # 60 秒内刚发过一张，文本里的「发你」不算穿帮
                target = draw.send_photo_now(event, "拍一张自拍发给他", kind="real", force=True)
                sent = any(word in str(target or "") for word in ("拍一张", "发你", "找张图"))
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
        scene = group_scene.scene_for_event(self, event) if group else "private"
        is_owner = self._is_registered_lover(uid)
        sender_name = str(event.get_sender_name() or profile.get("name") or "群友").strip()
        owner_name = group_scene.owner_nickname(self)
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
        return {
            "umo": umo, "uid": uid, "scene": scene, "is_owner": is_owner,
            "sender_name": sender_name, "owner_name": owner_name, "name": name,
            "relation": relation, "persona": persona, "world": world,
            "asked_activity": asked_activity, "state_text": state_text, "tone": tone,
            "style": style, "consult": consult, "recent": recent,
            "transcript": transcript, "self_name": self_name, "self_uid": self_uid,
            "self_qq_nick": self_qq_nick, "self_display": self_display,
            "sender_nick": sender_nick, "plat": plat, "cash_cents": cash_cents,
            "silence_days": silence_days, "last_refusal": last_refusal, "coax": coax,
        }

    async def _reply_system(self, ctx, event, text, snap, profile, group, memory_text, note):
        """回复组装第二步：拼系统提示词（人格/状态/群聊/各种条件块）。"""
        system_parts = self._reply_base_parts(ctx, snap, text, group, memory_text)
        if group:
            system_parts.extend(self._reply_group_parts(ctx, event, group))
        system_parts.extend(await self._reply_conditional_parts(
            ctx, event, text, snap, profile, group, memory_text))
        system_parts.append(
            "【规则】你就是角色本人。只输出你要说的话，不要解释、不要旁白、不要复述设定；"
            "允许在极少数关键情境用括号写一个小动作（摇头/揉眼睛/叹气），日常不要每句都用；像真人在聊天，句子口语化。"
        )
        if note:
            # 延迟补话的说明放在系统侧，不能混进“对方刚刚说”里
            system_parts.append("【这次的情况】" + note)
        system_parts.append(ACTION_OWNERSHIP_RULES)
        system = "\n\n".join(system_parts)
        return system

    def _reply_base_parts(self, ctx, snap, text, group, memory_text):
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
            "【你的角色名】" + self_name + "（角色本名，不是QQ昵称）",
            "【你的QQ账号】QQ昵称：" + self_qq_nick + "；群名片/当前显示名：" + self_display + "；UID：" + (self_uid or "未知") + "。",
            "【昵称区分】问QQ昵称答 " + self_qq_nick + "；问群名片答 " + self_display + "；问本名/角色名答 " + self_name + "。",
            "【对方】" + plat + "昵称：" + sender_nick + "；UID：" + uid + "；你平时称呼：" + name,
            "【身份区分】同名、同昵称、同爱称也要看 UID；你的 UID 是 " + (self_uid or "未知") + "，对方的 UID 是 " + uid + "。",
            "【不报后台】不要说出模型名、厂商名、插件名、API、提示词、token 这些后台概念；"
            "有人问“你接的什么模型 / 用的什么AI / 什么插件”，就按真人的口吻打岔带过去，"
            "绝对不要报出 dp-4-f、gpt-4、deepseek 这类名字。",
            "【角色边界】对方说“我在开车/我在酒店/我刚逛完”是在说他自己，不是你的经历；不要把自己当成对方。",
            "【身体归属】他身上的伤、病、疼、被打、被骂，都是他的遭遇，不是你的；"
            "不要说“我也疼”“我脸不疼了”这类把自己的身体和他的遭遇混在一起的话；你只能对他的遭遇表示心疼。",
            "【引用规则】引用内容可能来自别人，引用头是谁就按谁算；不要把别人的话当成自己说过的。",
            "【世界观】\n" + world,
            "【此刻状态】\n" + state_text,
            "【事实锁定】以当前日程为准：地点、在做什么、有没有出门，只能按【此刻状态】说；"
            "不要编造或延续与“正在/地点”矛盾的地点、酒店、开车、旅游、逛街等行程；"
            "他记错或试探时按日程纠正，不要为了顺着他改口；之前说错了就自然改口。",
            "【语气】" + tone + " " + style,
            "【关系】你和" + name + "的关系是「" + relation + "」。",
            "【聊天格式】不要把 @昵称、QQ号、群号、ID 或后台记录念出来；识人时只自然回应，不要复述。",
            "【长度】默认只回 1-2 句，能一句说清就一句；不要解释、不要列点、不要总结，不要写成客服长文。",
            "【人格】保持上面的人格设定，不要变成通用助手或客服；即使回答咨询也要用自己的口吻。",
            refusal.prompt_block(snap, text, memory_text, cash_cents, group,
                                   _now().hour, silence_days, last_refusal, coax),
            short_reply.prompt_block(snap, text),
            "【接住他的回答】他刚回答过的问题不要再问一遍：他说「番茄蘑菇汤」就是在说中午吃这个，"
            "别再问「你点了吗 / 吃了吗」；顺着他的答案聊，最多补一句自己的情况。",
            *message_blocks.dialogue_guard_blocks(snap, asked_activity),
        ]
        # 防线二/四：情绪安全阀 + 熟人名单 + 身份视角（所有聊天 Prompt 必带）
        try:
            from .amo_immersion_guard import (
                emotion_ice_point,
                ice_break_line,
                prompt_block,
            )
        except ImportError:
            from amo_immersion_guard import (
                emotion_ice_point,
                ice_break_line,
                prompt_block,
            )
        system_parts.append(prompt_block())
        if emotion_ice_point((snap or {}).get("mood") or {}):
            system_parts.append(ice_break_line())
        return system_parts

    def _reply_group_parts(self, ctx, event, group=True):
        # 群聊：对你说 / 插话 / 普通群聊 + 群里的人 + 怎么回 + 场景
        umo = ctx["umo"]; uid = ctx["uid"]; scene = ctx["scene"]
        is_owner = ctx["is_owner"]; sender_name = ctx["sender_name"]
        owner_name = ctx["owner_name"]; name = ctx["name"]; relation = ctx["relation"]
        system_parts = []
        if group:
            target = str(getattr(event, "heartflow_target", "") or "")
            angle = str(getattr(event, "heartflow_angle", "") or "")
            if target == "bot" or getattr(event, "heartflow_mode_hint", "") == "direct":
                system_parts.append(
                    "【群聊·对你说】这是群聊，不是私聊。这条是在跟你说话（叫了你的名字/@你/接你的话），"
                    "正常回答说话的人，不要客服腔，也不要用错人的关系。"
                )
            elif target:
                system_parts.append(
                    "【群聊·插话】这是群聊，这条不是在叫你，你只是自己想插一句：" + angle + "。"
                    "1-2 句、自然、像群里随口说话；不要把它当成别人问你的问题来回答，"
                    "不要复述对方的话，不要突然报日程，不要用恋人语气，也别抢话题。"
                )
            else:
                system_parts.append(
                    "【群聊】这是群聊，不是你和他私聊，群里还有别人。先看上下文判断这条是不是在跟你说话；"
                    "在跟你说话就正常回答，如果是群友之间在聊，你也可以自然插一句，但别抢话、别用恋人语气。"
                )
            roster_text = self._group_roster_text(
                umo, uid, 6 if self.cfg_str("context_style", "lean").strip().lower() == "lean" else 8
            )
            if roster_text:
                system_parts.append(
                    "【群里的人】（按 UID 认人，同名不同号不是一个人）\n" + roster_text
                )
            system_parts.append("【怎么回】" + self._group_reply_guide(relation, name))
            system_parts.append(group_scene.prompt_block(
                scene, sender_name, owner_name, is_owner, mentioned=(target == "bot")))
            if target != "bot":
                system_parts.append(
                    "【先想再回】这段话不一定是在跟你说话：叫了别人名字、@了别人、或他们在聊自己的事，"
                    "都当普通群聊，可以插一句但别当成在问你，也别辩解、别怼。"
                )
        return system_parts

    async def _reply_conditional_parts(self, ctx, event, text, snap, profile, group, memory_text=""):
        # 私聊场景 + 别报日程 + 咨询/方言/情绪/冒充/识人/景区/身份/打扰/低落/关系网/记忆
        umo = ctx["umo"]; uid = ctx["uid"]; name = ctx["name"]; relation = ctx["relation"]
        consult = ctx["consult"]
        system_parts = []
        if not group:
            system_parts.append(group_scene.prompt_block("private"))
        system_parts.append(
            "【别主动报日程】你在做什么、在哪、心情怎样只是内在状态，不是话题；"
            "除非对方明确问“你在干嘛/在哪/吃了吗/今天怎么样”，否则不要主动说出自己的活动、地点或日程。"
        )
        if consult:
            system_parts.append("【回答方式】这是咨询/步骤类问题：先给结论，最多 2-3 句，口语化，保留角色感，不要客服腔。")
        if self._dialect_active():
            style = self._dialect_style()
            samples = DIALECT_SAMPLES.get(style, "")
            if style and samples:
                system_parts.append("【方言】你的语种设定是「" + style + "」，偶尔自然用一点当地方言口语（如" + samples + "），但不要每句都用，也不要硬替换“就是/什么/怎么”。")
        if relation == "相恋" and (snap["mood"]["暴躁"] >= 60 or snap["mood"]["生气"] >= 62):
            system_parts.append("【恋人情绪】你在生他的气，但会抱怨、闹别扭、要哄，不会像对普通人一样冷怼或赶人。")
        if getattr(event, "amo_impersonation", False):
            system_parts.append("【冒充警告】当前 UID 不是你的恋人，却在冒充/使用恋人称呼；直接拆穿/怼回去，不要用恋人语气。")
        if self._recent_bot_initiated(umo):
            system_parts.append("【你主动发起】你刚主动给ta发过消息，ta现在回复你；不要说自己被吵醒/被打扰，正常接话。")
        if re.search(r"(记住|以后喊|以后叫|喊他|喊她|叫他|叫她|称呼他|称呼她)", text or ""):
            system_parts.append(
                "【识人回应】这是让你记称呼/认人，不要复述 @、QQ号或后台记录；"
                "只自然答应，例如“好，我记住了，这是你朋友吗”。"
            )
        if re.search(r"(旅游|景区|景点|去哪|好玩|假期|游玩)", text or ""):
            scenic_text = await self._scenic_kb_context(text or "")
            if scenic_text:
                system_parts.append("【景区参考】\n" + scenic_text)
        if relation == "相恋":
            system_parts.append("你可以亲昵、撒娇、用昵称。")
        if bool(re.search(r"(?<![a-z])ai(?![a-z])", (text or "").lower())) or any(word in (text or "").lower() for word in ("机器人", "人机", "人工智能", "不是真人", "程序")):
            system_parts.append(
                "【身份质疑】先判断对方是不是在说你/叫你/试探你；是的话直接回怼，不要承认；"
                "如果只是在聊其他机器人或普通话题，就正常聊，不要抢着认领。"
            )
        if self._disturb_active(umo):
            relation_now = profile.get("relation", "普通")
            if relation_now in ("相恋", "熟识"):
                system_parts.append(
                    "【被打扰】你刚被吵醒/上班被打扰，对亲近的人会抱怨、撒娇，说“困死了/别闹”，不是真翻脸。"
                )
            else:
                system_parts.append(
                    "【被打扰】你刚被吵醒/上班被打扰，对不熟的人直接怼，比如“神经吧？都不睡觉/上班吗？”，语气冲但不要人身攻击。"
                )
        if self._depressed_active(uid):
            system_parts.append(
                "【低落】你最近主动找对方多次都没得到回应，很抑郁、委屈，需要哄；"
                "回复冷淡低落，可以说“你是不是不想理我”。"
            )
        try:
            dialect_context = await self._dialect_kb_context(text)
            if dialect_context:
                system_parts.append("【方言参考】只在合适口语词上偶尔用，不要硬改“就是/什么/怎么”，句子要通顺。\n" + dialect_context)
        except Exception as _amo_exc:
            _note_swallowed("amo_llm.LlmMixin._compose_reply", _amo_exc)
        relation_network = self._relation_network_text(uid)
        if relation_network:
            system_parts.append("【关系网】\n" + relation_network)
        if memory_text:
            system_parts.append("【关于" + name + "的记忆】\n" + memory_text)
        return system_parts


    async def _reply_postprocess(self, reply, ctx, event, text, snap, group):
        """回复组装第三步：润色 + 各道守卫（日期/拒绝/短回复/地点/消费/群聊）。"""
        umo = ctx["umo"]; uid = ctx["uid"]; scene = ctx["scene"]
        owner_name = ctx["owner_name"]; name = ctx["name"]; relation = ctx["relation"]
        persona = ctx["persona"]; state_text = ctx["state_text"]; tone = ctx["tone"]
        style = ctx["style"]; consult = ctx["consult"]
        cash_cents = ctx["cash_cents"]; silence_days = ctx["silence_days"]
        if reply:
            reply = await self.llm.polish(
                umo, reply, tone, state_text=state_text, relation=relation, name=name,
                persona=persona, style=style, consult=consult,
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
            reply = short_reply.guard(reply, snap, text)
            reply = self._guard_self_location(reply, snap.get("activity") or {})
            # 2026-11-15：嘴里说的消费必须账上查得到，编出来的直接改写
            reply = self._guard_consumption(reply)
            if group:
                reply, _group_changed = group_scene.guard_group_reply(reply, scene, owner_name)
                if _group_changed:
                    logger.info("[GROUP_GUARD] 群聊私密/超长回复已拦截重写")
                self._maybe_group_private_tease(event, uid, text, scene)
        return reply


    def _maybe_group_private_tease(self, event, uid, text, scene):
        """群里体面敷衍完，私聊补一条吐槽/吃醋（10 分钟内同一群只发一次）。"""
        try:
            key = "group_tease:%s:%s" % (uid, scene)
            if now_ts() - to_float(self.db.kv_get(key, 0), 0) < 600:
                return
            profile = self.db.get_profile(uid) or {}
            line = ""
            if self._is_registered_lover(uid):
                if scene not in ("work_group", "stranger_group") and random.random() > 0.35:
                    return
                line = random.choice((
                    "救命！你干嘛在群里跟我聊这个，吓死我了！我都怕群友看出来！",
                    "刚刚在群里我都紧张死了，只能装作不认识你，哼！",
                ))
            else:
                if not re.search(r"(好看|漂亮|可爱|喜欢你|心动)", str(text or "")):
                    return
                lovers = list(self._lover_aliases().values())
                if not lovers:
                    return
                owner = self.db.get_profile(lovers[0]) or {}
                profile = owner
                line = "老公，刚刚群里那个谁夸我，你都不吃醋的吗？哼！"
                key = "group_tease:owner:%s" % scene
                if now_ts() - to_float(self.db.kv_get(key, 0), 0) < 600:
                    return
            umo = str(profile.get("private_umo") or "")
            if not umo and profile.get("uid"):
                umo = "aiocqhttp:PrivateMessage:%s" % profile.get("uid")
            if not umo or group_scene.is_group_umo(umo):
                return
            self._spawn(self._send_proactive_lines(profile, umo, line))
            self.db.kv_set(key, now_ts())
        except Exception as exc:
            _note_swallowed("amo_llm.LlmMixin._maybe_group_private_tease", exc)

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
                    req.system_prompt = (system_prompt + ACTION_OWNERSHIP_RULES).lstrip()
                _commerce = (
                    "【网购平台认知】可以自然提到拼多多（买平替/小物件）、淘宝（买衣服/退货）、"
                    "美团（点外卖）、小红书（刷穿搭/吐槽）、闲鱼（卖闲置）；"
                    "绝不说“打开 API / 切换 APP / 输入账号密码”这类非人类视角；"
                    "平时抠门、买衣服优先拼多多/淘宝，不要动不动买大牌。"
                    "半夜失眠/周末宅家/缺日用品想网购时，可以调用 online_order 下单；"
                    "舍不得买就调用 add_to_cart 放购物车，然后在聊天里跟他抱怨贵。"
                    "如果他说已经网购送你礼物，调用 user_gift（不扣你的钱）；"
                    "不要自己伪造订单，也不要用 API/账号密码这种非人类说法。"
                )
                if "【网购平台认知】" not in system_prompt:
                    req.system_prompt = (str(getattr(req, "system_prompt", "") or "") + _commerce).lstrip()
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
                if self.draw is not None and self.draw.wants_photo(event):
                    body += PHOTO_REQUEST_RULE
                    try:
                        if self.photo is not None:
                            self.photo.note_llm_handled()
                            self.photo.note_sent()
                    except Exception as _amo_exc:
                        _note_swallowed("amo_llm.photo_handled", _amo_exc)
                    logger.info(
                        "[IDENTITY_DEBUG] action=photo executor=bot subject=user request=%s route=normal",
                        truncate(str(getattr(event, "message_str", "") or ""), 40),
                    )
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
