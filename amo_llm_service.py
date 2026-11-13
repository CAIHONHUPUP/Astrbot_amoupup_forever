
from typing import TYPE_CHECKING
# -*- coding: utf-8 -*-
"""模型调用层：人格读取、对话、主动消息、回复组装。"""

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        _pg_analyze_rows,
        _pg_compose_task,
        _pg_near_duplicate,
        clean_reply,
        json_extract,
        json_extract_list,
        logger,
        looks_like_ai_admission,
        now_ts,
        other_platform_bot_lines,
        to_int,
        today_str,
        city_or_empty,
        truncate,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            _pg_analyze_rows,
            _pg_compose_task,
            _pg_near_duplicate,
            clean_reply,
            json_extract,
            json_extract_list,
            logger,
            looks_like_ai_admission,
            now_ts,
            other_platform_bot_lines,
            to_int,
            today_str,
            city_or_empty,
        truncate,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            _pg_analyze_rows,
            _pg_compose_task,
            _pg_near_duplicate,
            clean_reply,
            json_extract,
            json_extract_list,
            logger,
            looks_like_ai_admission,
            now_ts,
            other_platform_bot_lines,
            to_int,
            today_str,
            city_or_empty,
        truncate,
        )

try:  # 地点跟日程对齐：主动消息里说错地方也改回来
    from .place_guard import rewrite_reply as _rewrite_place
except ImportError:  # 直接按文件加载时
    try:
        from place_guard import rewrite_reply as _rewrite_place
    except Exception:  # pragma: no cover
        def _rewrite_place(text, activity):
            return str(text or "")


def _align_place(text, snap):
    """主动消息里说的地方和日程对不上时，改回日程地点（兜底，正常不该触发）。"""
    try:
        return _rewrite_place(text, (snap or {}).get("activity") or {})
    except Exception:  # pragma: no cover - 纯文本兜底，不该把主流程带崩
        return str(text or "")


class LLMService:
    def __init__(self, plugin):
        self.p = plugin
        self.context = plugin.context
        self._persona_cache = {}

    async def persona(self, umo=""):
        key = umo or "default"
        cached = self._persona_cache.get(key)
        if cached and now_ts() - cached[0] < 300:
            return cached[1]
        prompt = ""
        manager = getattr(self.context, "persona_manager", None)
        if manager is not None:
            try:
                persona = await manager.get_default_persona_v3(umo or None)
                if isinstance(persona, dict):
                    prompt = persona.get("prompt") or ""
                else:
                    prompt = getattr(persona, "prompt", "") or ""
            except Exception as exc:
                logger.debug("[amo] 读取 AstrBot 人格失败: %s" % exc)
        if not prompt:
            try:
                cfg = self.context.get_config(umo or None)
                personas = cfg.get("persona", []) if cfg else []
                if isinstance(personas, list) and personas:
                    prompt = personas[0].get("prompt", "") or ""
            except Exception as _amo_exc:
                _note_swallowed("amo_llm_service.LLMService.persona", _amo_exc)
        if not prompt:
            prompt = "你是一个生活在中国城市的普通女孩，有工作、有情绪、有自己的生活，说话自然口语化。"
        extra = self.p.cfg_str("persona_extra", "")
        if extra:
            prompt += "\n" + extra
        try:
            name = await self.p._persona_name_for(umo)
            if name and name not in prompt[:240]:
                prompt = "你的名字是" + name + "。\n" + prompt
        except Exception as _amo_exc:
            _note_swallowed("amo_llm_service.LLMService.persona", _amo_exc)
        self._persona_cache[key] = (now_ts(), prompt)
        return prompt

    async def provider(self, umo=""):
        try:
            if hasattr(self.context, "get_using_provider_async"):
                return await self.context.get_using_provider_async(umo or None)
            return self.context.get_using_provider(umo or None)
        except Exception as exc:
            logger.warning("[amo] 获取对话模型失败: %s" % exc)
            return None

    async def chat_raw(self, umo, system, prompt, contexts=None):
        provider = await self.provider(umo)
        if provider is None:
            return ""
        try:
            response = await provider.text_chat(
                prompt=prompt,
                system_prompt=system,
                contexts=contexts or [],
            )
        except Exception as exc:
            logger.error("[amo] LLM 调用失败: %s" % exc)
            return ""
        return str(getattr(response, "completion_text", "") or "").strip()

    async def chat(self, umo, system, prompt, contexts=None, limit=300):
        return clean_reply(await self.chat_raw(umo, system, prompt, contexts), limit)

    async def polish(self, umo, text, tone, state_text="", relation="", name="", persona="", style="", consult=False):
        text = (text or "").strip()
        if not text or not self.p.cfg_bool("enable_polish", True):
            return text
        system = (
            "你就是这个角色本人，不是AI助手，也不是在润色一段AI台词。"
            "保持第一人称和原意，只输出角色会说的话。"
        )
        prompt = (
            "角色人格：\n" + (persona or "（未提供）")[:1000] + "\n"
            "角色当前语气：" + tone + "\n"
            "风格：" + (style or "自然") + "\n"
            "角色此刻不可矛盾的状态：\n" + (state_text or "（无）") + "\n"
            "对话对象：" + (name or "对方") + "；关系：" + (relation or "普通") + "\n"
            "原句：" + text + "\n"
            "请把这句话改成真人在聊天：口语化、有一点情绪，长度接近原句，通常不要超过原句的 1.3 倍。\n"
            "必须遵守：\n"
            "0. 保持角色人格：就是人格设定里的那个人，不是客服/通用助手；\n"
            "0. 只改语气和口语表达，不要新增原句没有的地点、行程、动作；如果原句与状态矛盾，以状态为准，不要继续编；\n"
            "1. 绝对不要承认自己是AI、人工智能、机器人、人机、程序或模型；\n"
            "2. 如果原句在承认或顺着“人机/机器人”说，要改成真人的反驳、无语或有点生气；\n"
            "3. 不要提润色、提示词、模型、系统、插件、API；被问“你接的什么模型/用的什么AI”时，\n"
            "   回一句像真人的打岔（“什么模型呀”“我就是我啊”），绝对不要报出任何模型名或厂商；\n"
            "4. 保持原句情绪强度和态度。如果原句在生气/烦躁/怼人，绝对不要改成甜蜜、撒娇、讨好，最多缓和一点；\n"
            "5. 不要一秒变脸，情绪要有惯性；\n"
            "6. 默认 1-2 句，能一句说清就一句；咨询/步骤最多 3 句，先给结论，不要列点、不要总结；\n"
            "7. 不要扩写，不要新增原句没有的信息。"
            + ("\n8. 这是咨询/步骤类：用角色口吻简短回答，不要客服腔。" if consult else "")
        )
        result = await self.chat(umo, system, prompt, limit=self.p.cfg_int("max_reply_chars", 220))
        if not result:
            return text
        if looks_like_ai_admission(result):
            retry_prompt = (
                "角色当前语气：" + tone + "\n"
                "原句：" + text + "\n"
                "原句/改写里出现了承认自己是人机或机器人的说法，这是不允许的。\n"
                "请改成真人被质疑时的自然反应：反驳、无语、有点生气或开个玩笑，"
                "绝对不要出现“我是AI/机器人/人机/程序/模型”“人机也有用”这类自曝。\n"
                "只输出一句角色会说的话。"
            )
            retry = await self.chat(umo, system, retry_prompt, limit=self.p.cfg_int("max_reply_chars", 220))
            if retry and not looks_like_ai_admission(retry):
                result = retry
            else:
                result = "你才人机呢，你见过这么会聊天的机器人吗？"
        if len(result) > max(len(text) + 30, int(len(text) * 1.5)):
            return text
        return result

    async def decide(self, umo, question, state):
        service = getattr(self.context, "decision", None)
        if service is not None:
            try:
                return bool(await service.yes_no(
                    question=question, state=state, threshold=0.5, default=None
                ))
            except Exception as exc:
                logger.debug("[amo] Decision 判断不可用: %s" % exc)
        answer = await self.chat(
            umo, "你是一个判断器，只输出 yes 或 no。",
            question + "\n只输出 yes 或 no。", limit=8,
        )
        if not answer:
            return None
        answer = answer.strip().lower()
        return answer.startswith("y") or answer.startswith("是") or answer.startswith("对")

    async def gen_worldview(self, umo, persona):
        system = "你是一个世界观设定师。只输出 JSON，不要输出其他文字。"
        prompt = (
            "根据下面的人格设定，为这个角色生成一个真实可信的现代生活世界观。\n"
            "人格设定：\n" + truncate(persona, 1500) + "\n\n"
            "输出 JSON 对象，字段：name(角色名), age(整数), city(城市), occupation(职业), "
            "workplace(工作或学习地点), personality(性格一句话), hobbies(字符串数组), "
            "daily_life(日常生活一段话), world_rules(这个世界和人设的规则一段话), "
            "summary(50字内总结)。"
        )
        raw = await self.chat_raw(umo, system, prompt)
        data = json_extract(raw)
        if not isinstance(data, dict):
            return None
        hobbies = data.get("hobbies")
        if not isinstance(hobbies, list):
            hobbies = [str(hobbies)] if hobbies else []
        return {
            "name": str(data.get("name") or self.p.bot_name),
            "age": to_int(data.get("age"), 22),
            "city": str(data.get("city") or city_or_empty(self.p)),
            "occupation": str(data.get("occupation") or "普通上班族"),
            "workplace": str(data.get("workplace") or "公司"),
            "personality": str(data.get("personality") or "温柔敏感"),
            "hobbies": [str(item)[:20] for item in hobbies[:5]],
            "daily_life": str(data.get("daily_life") or ""),
            "world_rules": str(data.get("world_rules") or ""),
            "summary": str(data.get("summary") or ""),
        }

    async def gen_proactive(self, umo, profile, snap, kind):
        name = profile.get("alias") or profile.get("name") or "你"
        relation = profile.get("relation", "普通")
        system = (
            await self.persona(umo) + "\n\n"
            + self.p.state.prompt_text(snap, with_activity=True) + "\n"
            + self.p.state.tone_text(snap, relation) + "\n"
            "规则：你就是角色本人，只输出要发出去的消息，不要解释、不要旁白。\n"
            "一致性：不要编造自己的经历；不能和你刚才说过的话矛盾"
            "（刚才说吃过了，现在就不能说去买吃的）。\n"
            "此刻在做什么以上面「你此刻在…」为准：和最近聊天记录里说的不一样，就按此刻说，"
            "自然地改口（「会开完了」「我不在便利店了」），不要沿用旧说法，"
            "也不要编出没发生过的会议/加班/行程。"
        )
        try:
            rows = self.p._recent(umo, 12)
        except Exception as _amo_exc:
            _note_swallowed("amo_llm_service.LLMService.gen_proactive", _amo_exc)
            rows = []
        analysis = _pg_analyze_rows(rows, now=now_ts()) if _pg_analyze_rows is not None else {
            "context_text": "",
            "unanswered_text": "",
            "unanswered_seconds": 0.0,
            "unanswered_at": 0.0,
            "recent_bot_texts": [],
        }
        # 她刚在另一个平台（QQ / 飞书）说过的话也带上：只看本会话的话，
        # 另一边早就改口了，这边还在接上一场开会。
        other_bot = []
        uid = str(profile.get("uid") or "")
        if uid:
            try:
                other_bot = other_platform_bot_lines(
                    await self.p._recent_by_uid(uid, 12, ""), umo,
                    platform=str(umo).split(":", 1)[0],
                )
            except Exception as exc:
                logger.debug("[amo] 取跨平台近聊失败: %s" % exc)
                other_bot = []
        if other_bot:
            analysis["recent_bot_texts"] = list(analysis.get("recent_bot_texts") or []) + list(other_bot)
            system = system + (
                "\n【你刚在别的平台说过】\n"
                + "\n".join("· " + str(item)[:120] for item in other_bot)
                + "\n（这些是你在另一个平台刚说的话，不是他说的，别复述、别重复；"
                "和此刻状态对不上就以此刻为准。）"
            )
        window = max(0, self.p.cfg_int("proactive_unanswered_window_minutes", 30)) * 60
        limit = self.p.cfg_int("max_reply_chars", 220)
        # 记忆只来自外挂记忆插件；主动消息也带上，免得她说得像个陌生人
        memory_text = ""
        try:
            uid = str(profile.get("uid") or "")
            if uid:
                _, memory_text = await self.p._recall(uid, "", None)
        except Exception as _amo_exc:
            _note_swallowed("amo_llm_service.LLMService.gen_proactive", _amo_exc)
            memory_text = ""
        if memory_text:
            system = system + "\n【关于他的记忆】\n" + memory_text
        try:
            relation_network = self.p._relation_network_text(str(profile.get("uid") or ""))
        except Exception as _amo_exc:
            _note_swallowed("amo_llm_service.LLMService.gen_proactive", _amo_exc)
            relation_network = ""
        if relation_network:
            system = system + "\n【关系网】\n" + relation_network
        try:
            trip = self.p.db.kv_get("wander_trip", {}) or {}
            if (
                isinstance(trip, dict) and trip.get("city") and not trip.get("announced")
                and str(trip.get("day") or "") <= today_str() <= str(trip.get("return_day") or "")
            ):
                spots = trip.get("spots") or []
                spot = str(spots[0]) if spots else ""
                system = system + (
                    "\n【旅游中】你现在一个人在 " + str(trip["city"]) + " 旅游"
                    + ("，今天去了" + spot if spot else "")
                    + "。主动跟他分享今天看到的、吃到的、天气；别报公司/上班，也别编景点。"
                )
        except Exception as _amo_exc:
            _note_swallowed("amo_llm_service.LLMService.gen_proactive", _amo_exc)

        def build_task(duplicate_hint=""):
            if kind == "unwell":
                health = snap.get("health") or {}
                detail = str(health.get("note") or health.get("status") or "不太舒服")
                return ("你现在确实生病了（" + detail + "），主动跟" + name
                        + "说你不太舒服，可以撒个娇、让他关心你；不要装没事，也别夸张成重病。")
            if kind == "birthday":
                return ("今天是他生日，用你平时对他的称呼自然地祝他生日快乐，"
                        "语气符合你的人设，不要提 AI/模型/日程。")
            if _pg_compose_task is None:
                return "你突然想起" + name + "，想主动发一条消息。"
            event_text = ""
            if kind == "share":
                try:
                    event_text = self.p.state.interesting_event()
                except Exception as _amo_exc:
                    _note_swallowed("amo_llm_service.LLMService.gen_proactive.build_task", _amo_exc)
                    event_text = ""
            try:
                dialect_enabled = bool(self.p._dialect_active())
            except Exception as _amo_exc:
                _note_swallowed("amo_llm_service.LLMService.gen_proactive.build_task", _amo_exc)
                dialect_enabled = False
            return _pg_compose_task(
                kind=kind,
                name=name,
                unanswered_text=analysis["unanswered_text"],
                unanswered_seconds=analysis["unanswered_seconds"],
                window_seconds=window,
                recent_bot_texts=analysis["recent_bot_texts"],
                interesting_event=event_text,
                duplicate_hint=duplicate_hint,
                dialect_style=self.p.cfg_str("dialect_style", ""),
                dialect_enabled=dialect_enabled,
            )

        def build_prompt(task):
            prompt = "这是私聊。请以角色身份主动发一条消息，" + task + " 1 到 2 句，口语化。"
            if analysis["context_text"]:
                prompt = "最近私聊记录：\n" + analysis["context_text"] + "\n\n" + prompt
            if memory_text:
                prompt = "你记得关于他的事：\n" + memory_text + "\n\n" + prompt
            return prompt

        text = await self.chat(umo, system, build_prompt(build_task()), limit=limit)
        text = _align_place(text, snap)
        if _pg_near_duplicate is None or not self.p.cfg_bool("proactive_dedupe_enabled", True):
            return text
        threshold = self.p.cfg_float("proactive_dedupe_threshold", 0.62)
        hit = _pg_near_duplicate(text, analysis["recent_bot_texts"], threshold)
        if not hit:
            return text
        retry = await self.chat(
            umo, system, build_prompt(build_task(duplicate_hint=hit[0])), limit=limit,
        )
        retry = _align_place(retry, snap)
        if retry and not _pg_near_duplicate(retry, analysis["recent_bot_texts"], threshold):
            return retry
        logger.info("[amo] 主动消息和最近的发言重复，跳过本次：%s" % str(text or retry or "")[:60])
        return ""

    async def extract_facts(self, umo, name, lines):
        system = "你是记忆提取器。只输出 JSON 数组，不要输出其他文字。"
        prompt = (
            "请从下面的聊天中提取关于用户“" + name + "”的长期事实。\n"
            "聊天里每一行都带说话人标记：[他] 是用户本人说的，[我] 是角色自己说的。\n"
            "只从 [他] 开头的行里提取；[我] 开头的内容是角色自己的事，不能算成用户的事实。\n"
            "只提取值得长期记住的内容：名字、生日、喜好、厌恶、居住地、工作或学校、"
            "宠物、年龄、重要计划、重要经历。\n"
            "位置类事实统一写成「他现在在X」；只有他明确说了搬/住/定居，才写「他住在X」。\n"
            "content 一律用第三人称「他」开头描述用户：写「他被扇了耳光，脸疼」而不是「我被扇了耳光」；"
            "绝不能用「我」或「你」指代用户，也不要省略主语。\n"
            "输出 JSON 数组，每项包含 content(一句话事实)、kind(name/birthday/preference/place/work/pet/age/event)、"
            "tier(0 普通 / 1 重要 / 2 珍贵)。没有可提取的就输出 []。\n\n"
            "聊天记录：\n" + "\n".join(lines[-20:])
        )
        raw = await self.chat_raw(umo, system, prompt)
        data = json_extract_list(raw)
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if len(content) < 2:
                continue
            # 兜底：模型偶尔仍会写成「我脸疼」/「被扇了耳光」这种没有主语或第一人称的句子，
            # 这种记忆注入后会被当成角色自己的经历（就是"幻视"）。这里统一补成第三人称。
            if content.startswith(("我", "你")):
                content = "他" + content[1:]
            elif not content.startswith(("他", "她", "用户", name or "\u0000")):
                content = "他" + content
            result.append({
                "content": content[:80],
                "kind": str(item.get("kind", "fact"))[:20],
                "tier": to_int(item.get("tier"), 1),
            })
        return result[:8]
