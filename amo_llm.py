# -*- coding: utf-8 -*-
"""LlmMixin：人格、LLM 注入、回复组装、冒充检测。"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

import re

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

if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object



class LlmMixin(_MixinBase):
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

    async def _compose_reply(self, event, text, snap, profile, memories, memory_text, group, note=""):
        umo = event.unified_msg_origin
        uid = self._canonical_uid(event)
        name = profile.get("alias") or profile.get("name") or event.get_sender_name() or "对方"
        relation = profile.get("relation", "普通")
        persona = await self.llm.persona(umo)
        world = self.state.worldview_text()
        asked_activity = bool(re.search(
            r"(你在干嘛|在干嘛|干嘛呢|干什么呢|你在哪|在哪呢|吃了吗|吃没吃|吃了没|忙吗|忙不忙"
            r"|在忙什么|上班吗|在上班|下班了吗|今天干嘛|做什么呢|干啥呢)",
            text or "",
        ))
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
        transcript = "\n".join(lines[-8:])
        self_name, self_uid, self_qq_nick, self_display = await self._self_identity(event)
        sender_nick = str(event.get_sender_name() or profile.get("name") or uid).strip()
        plat = self._platform_label(self._platform_of(event))
        system_parts = [
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
            "【短回复也要像你】哪怕只回一两个字，也要带你的称呼/语气词/小情绪（比如「嗯呐」「好呀」「知道啦」），"
            "不要只回「好」「嗯」「收到」这种通用答复；短不代表冷淡。",
        ]
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
            if target != "bot":
                system_parts.append(
                    "【先想再回】这段话不一定是在跟你说话：叫了别人名字、@了别人、或他们在聊自己的事，"
                    "都当普通群聊，可以插一句但别当成在问你，也别辩解、别怼。"
                )
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
        system_parts.append(
            "【规则】你就是角色本人。只输出你要说的话，不要解释、不要旁白、不要复述设定、"
            "不要用括号描述动作；像真人在聊天，句子口语化。"
        )
        if note:
            # 延迟补话的说明放在系统侧，不能混进“对方刚刚说”里
            system_parts.append("【这次的情况】" + note)
        system = "\n\n".join(system_parts)
        if group:
            prompt = (
                "以下是群聊最近的对话：\n" + transcript + "\n\n"
                + name + "刚刚说：" + text + "\n"
                "群里正在聊天，你决定插一句。只回 1-2 句，不要@任何人，不要重复别人说过的话。"
            )
        else:
            prompt = (
                "以下是你们最近的私聊：\n" + transcript + "\n\n"
                + name + "刚刚说：" + text + "\n"
                "请以角色身份自然回复。默认 1-2 句，能短就短；咨询类最多 3 句，不要客服腔。"
            )
        reply = await self.llm.chat(
            umo, system, prompt, limit=self.cfg_int("max_reply_chars", 220)
        )
        if reply:
            reply = await self.llm.polish(
                umo, reply, tone, state_text=state_text, relation=relation, name=name,
                persona=persona, style=style, consult=consult,
            )
            reply = self._trim_reply(reply)
            reply = self._guard_self_location(reply, snap.get("activity") or {})
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
            # 只走官方推荐的 extra_user_content_parts（临时 part）注入。
            # 绝不能改 req.system_prompt：那里放每轮都变的内容会打散系统提示词前缀，
            # 破坏模型侧缓存（成本涨 7-20 倍），上架审核也会因此被拒。
            body = "<amo_state>\n" + context_text + "\n</amo_state>"
            try:
                if self.draw is not None and self.draw.wants_photo(event):
                    body += (
                        "\n【拍照请求】他这轮明确想要一张照片：自然答应（可以害羞、可以说等一下），"
                        "说清你现在在哪、在做什么；不要直接拒绝（不要说“不拍 / 拍什么拍”）。"
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
            logger.debug("[amo] 注入状态失败: %s" % exc)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        if self.heartflow is None:
            return
        try:
            await self.heartflow.on_llm_response(event, resp)
        except Exception as exc:
            logger.debug("[amo] 心流回复记录失败: %s" % exc)
