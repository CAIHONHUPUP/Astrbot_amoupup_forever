# -*- coding: utf-8 -*-
"""schedule_diary：ScheduleEngine 的一部分方法（逐字搬运）。"""

from __future__ import annotations
from typing import Any, TYPE_CHECKING

try:  # 包模式（AstrBot 加载插件）
    from .schedule_plan import (
        _brief_world, _city_of, _diary_material_lines, _mood_text, _note_swallowed, _scenic_text,
        _weather_text, asyncio, diary_presence_rules, json_extract, logger, re, transit_phrase,
    )
except ImportError:  # 直接按文件加载时（测试/模拟器）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from schedule_plan import (
            _brief_world, _city_of, _diary_material_lines, _mood_text, _note_swallowed, _scenic_text,
            _weather_text, asyncio, diary_presence_rules, json_extract, logger, re, transit_phrase,
        )


class ScheduleDiaryMixin:
    # 方法体逐字搬运自拆分前的 schedule_engine.py。

    if TYPE_CHECKING:  # 宿主（插件主类/其它 mixin）提供的成员：严格口径用声明而不是 ignore
        p: Any

    @staticmethod
    def _fix_home_diary(text):
        """物理边界：他不在现场，绝不能把手机聊天改写成“当面”。保留原文。"""
        return str(text or "")

    @staticmethod
    def _interaction_material(lines):
        """把「他：/我：」聊天行整理成自然读的互动素材，空列表返回空串。"""
        try:
            rows = []
            for line in lines or []:
                text = str(line or "").strip()
                if not text or "：" not in text:
                    continue
                who, said = text.split("：", 1)
                said = said.strip().strip('"“”')
                if not said:
                    continue
                rows.append(("他" if who.strip() != "我" else "我", said))
            if not rows:
                return ""
            sentences: list[str] = []
            index = 0
            while index < len(rows) and len(sentences) < 8:
                who, said = rows[index]
                if who == "他":
                    nxt = rows[index + 1] if index + 1 < len(rows) else None
                    if nxt and nxt[0] == "我":
                        sentences.append('老公对你说：“%s”。你回复了：“%s”。' % (said, nxt[1]))
                        index += 2
                        continue
                    sentences.append('老公说：“%s”。' % said)
                else:
                    sentences.append('你说：“%s”。' % said)
                index += 1
            return "在约会期间，" + "".join(sentences[-6:])
        except Exception:
            return ""

    @staticmethod
    def _fix_date_diary(text):
        """约会这一小时：把「隔着手机/一个人」的穿帮措辞改回面对面。"""
        body = str(text or "")
        for old, new in (
            ("在微信上跟老公聊了几句", "跟他面对面聊了几句"),
            ("微信上跟老公聊", "当面跟他聊"),
            ("在微信上", "面对面"),
            ("微信里", "当面"),
            ("隔着屏幕", "面对面"),
            ("我一个人", "我们"),
            ("一个人点了", "我们点了"),
            ("一个人点", "我们点"),
            ("一个人吃", "我们一起吃"),
            ("一个人", "我"),
            ("等他回来", "他就在我对面"),
        ):
            body = body.replace(old, new)
        return body

    async def llm_json(self, llm, umo, system, prompt, fallback=None, timeout=20):
        """大模型返回 JSON：解析失败→严格重试一次→仍失败返回 fallback，绝不抛异常。"""
        raw = ""
        try:
            raw = await asyncio.wait_for(llm.chat_raw(umo, system, prompt), timeout=timeout)
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.llm_json.first", _amo_exc)
        data = json_extract(raw)
        if data is not None:
            return data
        logger.warning("[JSON_PARSE] 首次 JSON 解析失败，触发严格重试")
        try:
            strict = prompt + "\n\n刚才格式错了，请严格只输出 JSON，不要任何其他文字！"
            raw = await asyncio.wait_for(llm.chat_raw(umo, system, strict), timeout=timeout)
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.llm_json.retry", _amo_exc)
            return fallback
        data = json_extract(raw, fallback)
        if data is None:
            logger.warning("[JSON_PARSE] 严格重试仍失败，使用安全兜底")
        return data

    # ------------------------------------------------------------------
    # 8. 动态日记
    # ------------------------------------------------------------------
    @staticmethod
    def _diary_contradicts(prev_diary, diary):
        """粗校验：上一小时已经写他进门/在家，这一小时不能又写他还没回来。"""
        prev = str(prev_diary or "")
        cur = str(diary or "")
        if not prev or not cur:
            return False
        at_home = (
            ("阿墨" in prev or "他" in prev)
            and any(word in prev for word in ("进门", "在家", "回来", "到家", "身边", "陪你", "坐在"))
        )
        alone = any(word in cur for word in ("还没回来", "不在家", "没回来", "一个人", "等他回来"))
        return bool(at_home and alone)

    def _enforce_people(self, item, text):
        body = str(text or "")
        names = [str(n) for n in (item.get("people") or item.get("companions") or []) if str(n)]
        try:
            npc = self.p.db.kv_get("npc_summary", {}) or {}
        except Exception:
            npc = {}
        if not names:
            _text = str(item.get("activity") or "") + str(item.get("detail") or "")
            names = [str(k) for k in npc if str(k) and str(k) in _text]
        if not names:
            return body
        name = names[0]
        rel = str(npc.get(name) or "")
        label = (rel + name) if rel else name
        body = body.replace("一个人", "和" + label + "一起")
        if label not in body:
            body = body.replace(name, label, 1)
        return body

    async def generate_diary(self, umo, ctx, item, prev_item, next_item, llm, prev_diary=""):
        if llm is None:
            return {
                "diary": self.fallback_diary(ctx, item, prev_item),
                "mood": item.get("mood") or "平静",
                "place": item.get("place") or "",
                "activity": item.get("activity") or "",
            }

        def _parse(raw):
            text = ""
            data = json_extract(raw)
            if isinstance(data, dict):
                text = str(data.get("diary") or data.get("text") or "").strip()
            elif isinstance(data, str):
                text = data.strip()
            if not text:
                text = str(raw or "").strip()
            return self._clean_diary(text)

        system = "你就是这个角色本人。你在写只给自己看的第一人称小日记，只输出 JSON。"
        # 约会这一小时只可能是「老公」：模型偶尔会把名单里的别人串进来，先挡掉
        if "老公" in str(item.get("activity") or ""):
            _others = ("柳如烟", "白凝冰", "季博达", "方圆", "沈幼微")
            _orig_parse = _parse

            def _parse(raw):
                text = _orig_parse(raw)
                for other in _others:
                    text = text.replace(other, "老公")
                return text
        _act = str(item.get("activity") or "")
        is_date = (str(item.get("category") or "") == "social"
                   and ("老公" in _act or "约会" in _act))
        prompt = self._diary_prompt(ctx, item, prev_item, next_item, prev_diary,
                                    together=is_date)
        prompt = self._diary_interaction_suffix(prompt, ctx, is_date)
        raw = ""
        try:
            raw = await asyncio.wait_for(llm.chat_raw(umo, system, prompt), timeout=60)
            _text = _parse(raw)
            raw = self._trim_next_hour(raw, _text, str(item.get("activity") or ""),
                                       str((next_item or {}).get("activity") or ""))
        except Exception as _amo_exc:
            _note_swallowed("schedule_engine.ScheduleEngine.generate_diary", _amo_exc)
            raw = ""
        diary = _parse(raw)
        if is_date:
            diary = self._fix_date_diary(diary)
        elif (any(w in str(item.get("place") or "") for w in ("家", "卧室", "客厅", "沙发", "床"))
              or any(w in diary for w in ("到家", "沙发", "睡衣", "卧室", "被窝"))):
            diary = self._fix_home_diary(diary)
        if len(diary) < 20:
            # JSON 崩了/被截断：严格重试一次，再不行走本地兜底，绝不让这一小时空白
            try:
                strict = prompt + "\n\n刚才格式错了，请严格只输出 JSON，不要任何其他文字！"
                raw = await asyncio.wait_for(llm.chat_raw(umo, system, strict), timeout=30)
                diary = _parse(raw)
            except Exception as _amo_exc:
                _note_swallowed("schedule_engine.generate_diary.retry", _amo_exc)
        if len(diary) < 20:
            diary = self.fallback_diary(ctx, item, prev_item)
        facts = ctx.get("fact_pool") or []
        if facts:
            diary = self._sanitize_hour_diary(diary, facts)
            if not await self._audit_hour_diary(diary, facts, llm, umo):
                strict = (prompt + "\n【审计失败】上一版写了事实列表之外的事，必须严格只写白名单里的事件。\n")
                try:
                    raw = await asyncio.wait_for(llm.chat_raw(umo, system, strict), timeout=60)
                    diary = self._sanitize_hour_diary(_parse(raw), facts)
                except Exception as _amo_exc:
                    _note_swallowed("schedule_engine.ScheduleEngine.generate_diary.audit", _amo_exc)
        if prev_diary and self._diary_contradicts(prev_diary, diary):
            strict = (
                prompt
                + "\n【硬性续写】上一小时日记里他已经进门/在家/在陪你。"
                + "这一小时必须承接这个事实，不能写他还没回来、不在家、你一个人。"
            )
            try:
                raw = await asyncio.wait_for(llm.chat_raw(umo, system, strict), timeout=60)
            except Exception as _amo_exc:
                _note_swallowed("schedule_engine.ScheduleEngine.generate_diary", _amo_exc)
                raw = ""
            retried = _parse(raw)
            if retried and len(retried) >= 20 and not self._diary_contradicts(prev_diary, retried):
                diary = retried
            else:
                diary = self.fallback_diary(ctx, item, prev_item)
        diary, location_fallback = await self._guard_diary_location(
            diary, item, llm, umo, system, prompt, _parse, is_date, facts)
        diary = self._enforce_people(item, diary)
        return {
            "diary": diary,
            "mood": item.get("mood") or "平静",
            "place": item.get("place") or "",
            "activity": item.get("activity") or "",
            "fallback": bool(location_fallback),
            "fallback_reason": "location" if location_fallback else "",
        }

    async def _guard_diary_location(self, diary, item, llm, umo, system, prompt,
                                    parse, is_date, facts):
        """小日记生成后的地点闸：命中 forbidden 最多重写 2 次，仍冲突走地点兜底。"""
        try:
            from . import amo_location_rules as _loc
        except ImportError:
            import amo_location_rules as _loc
        place = str(item.get("place") or "").strip()
        zone = str(item.get("zone") or "").strip()
        if llm is None or not (place or zone) or not _loc.enabled(getattr(self, "p", None)):
            return diary, False

        async def _regenerate(hint):
            strict = (prompt + "\n\n【地点校验失败】" + hint
                      + "\n只输出 JSON：{\"diary\":\"...\"}")
            raw = await asyncio.wait_for(llm.chat_raw(umo, system, strict), timeout=60)
            text = parse(raw)
            if len(text) < 20:
                return ""
            if is_date:
                text = self._fix_date_diary(text)
            elif (any(word in str(item.get("place") or "")
                     for word in ("家", "卧室", "客厅", "沙发", "床"))
                  or any(word in text for word in ("到家", "沙发", "睡衣", "卧室", "被窝"))):
                text = self._fix_home_diary(text)
            if facts:
                text = self._sanitize_hour_diary(text, facts)
            return text

        result = await _loc.guard_generation(
            diary, place, zone, regenerate=_regenerate, retries=2,
            enabled_flag=True, tag="small")
        return result["text"], bool(result["fallback"])

    def diary_presence_rules(self, together=False):
        return diary_presence_rules(together)

    def _diary_interaction_suffix(self, prompt, ctx, is_date):
        """提示词尾段：互动素材 + 物理边界/约会约束（文本与旧实现逐字一致）。"""
        _chat_hour = [str(x) for x in (ctx.get("chat_lines_hour") or []) if str(x).strip()]
        _material = self._interaction_material(_chat_hour)
        if _material:
            prompt += ("\n\n【互动素材】" + _material + "\n"
                       "请结合你们的真实聊天记录和物理场景，以第一人称写小日记。"
                       "严禁凭空捏造没说过的话，情感必须基于这些原话；聊天只能当素材，不要逐句复述。\n")
        else:
            prompt += ("\n\n【互动素材】这一小时你们没怎么发消息，可能只是在默默吃饭/待着。"
                       "不要编造对话，只写物理场景和情绪（比如「虽然没怎么说话，但一起吃饭很安心」）。\n")
        if not is_date:
            prompt += ("\n\n【物理边界·最高优先级】他此刻不在你身边、不在这个房间里。"
                       "必须至少写一次“一个人/手机屏幕/微信/语音”这类独处细节，让读者看出你是一个人；"
                       "严禁写他翻身、抱你、坐在旁边、屋里两个人。"
                       "想他可以写想，但不能写成他就在现场。\n")
        if is_date:
            prompt += (
                "\n\n【这一小时你们在一起】你和老公正在约会，他就在你对面/身边。"
                "只能写面对面的细节（给他夹菜、看他吃、一起说话）。"
                "严禁出现「微信/手机/视频/隔着屏幕/一个人/等他回来」这类措辞，"
                "你们不是隔着手机聊天。\n"
            )
        return prompt

    @staticmethod
    def _trim_next_hour(raw, text, current_activity, next_activity):
        """下一小时的关键词漏进这一小时：直接截断，绝不把两段拼成一段。"""
        for _kw in ("火锅", "逛街", "喜酒", "聚餐", "电影", "商场", "医院", "景区", "火锅店"):
            if _kw in next_activity and _kw not in current_activity and _kw in text:
                _idx = text.find(_kw)
                _cut = max(text.rfind("。", 0, _idx), text.rfind("，", 0, _idx))
                if _cut > 20:
                    trimmed = text[:_cut + 1]
                    if trimmed.endswith("，"):
                        trimmed = trimmed[:-1] + "。"
                    return trimmed
                break
        return raw

    @staticmethod
    def _diary_place_switch(prev_item, item, same_place):
        """上一块/这一块地点不同时的硬约束；同一地点返回空串。"""
        if not prev_item or same_place:
            return ""
        _prev_place = str((prev_item or {}).get("place") or "").strip()
        _prev_zone = str((prev_item or {}).get("zone") or "").strip()
        _cur_place = str((item or {}).get("place") or "").strip()
        _cur_zone = str((item or {}).get("zone") or "").strip()
        return ("\n【地点切换·必须遵守】上一小时在【%s】，这一小时在【%s】。"
                "上一小时日记里的床、被窝、外卖、沙发、茶几等细节已经结束，"
                "绝不能搬到【%s】；只承接情绪，不承接场景。\n"
                % (_prev_place or _prev_zone, _cur_place or _cur_zone,
                   _cur_place or _cur_zone))

    @staticmethod
    def _diary_fact_section(facts):
        """事实白名单片段；没有事实返回空串（文本与旧实现逐字一致）。"""
        if not facts:
            return ""
        lines = []
        for idx, fact in enumerate(facts[:40], 1):
            if isinstance(fact, dict):
                type_text = str(fact.get("type") or "事实")
                detail = str(fact.get("detail") or "")
                ledger_like = (any(key in type_text for key in ("收入", "支出", "账单"))
                               or "发工资" in detail)
                if ledger_like:
                    # 账本金额不进日记：只留一句人话，情绪让模型自己写
                    if "工资" in detail:
                        detail = "发工资了，账户里多了一笔钱"
                    elif "支出" in type_text:
                        detail = "花了一笔钱（具体吃什么、买什么看日程）"
                    else:
                        detail = "有一笔进出账"
                    type_text = "账本"
                lines.append("%d. [%s] %s%s" % (
                    idx, type_text,
                    (str(fact.get("time")) + " ") if fact.get("time") and type_text != "账本" else "",
                    detail))
        return ("\n【今天真实发生的事件（白名单）】\n" + "\n".join(lines)
                + "\n你只能扩写上面的内容。上面没写的电话、见面、去公司、聊天，一律不能写。\n")

    def _diary_prompt(self, ctx, item, prev_item, next_item, prev_diary="", together=False):
        event = ctx.get("event") or {}
        scenic = ctx.get("scenic") or {}
        try:
            from . import amo_location_rules as _loc
        except ImportError:
            import amo_location_rules as _loc
        _cur_place = str(item.get("place") or "").strip()
        _cur_zone = str(item.get("zone") or "").strip()
        _prev_place = str((prev_item or {}).get("place") or "").strip()
        _prev_zone = str((prev_item or {}).get("zone") or "").strip()
        _same_place = True
        if prev_item:
            _same_place = _loc.resolve(_prev_place, _prev_zone) == _loc.resolve(_cur_place, _cur_zone)
        _location_line = _loc.prompt_clause(
            _cur_place, _cur_zone, _loc.enabled(getattr(self, "p", None)))
        (prev_text, next_text, prev_diary_text, car_line,
         asset_line, worn_line, gift_line, outfit_line) = _diary_material_lines(
            self, ctx, item, prev_item, next_item, prev_diary, same_place=_same_place)
        try:
            from .amo_immersion_guard import diary_prompt_block as _immersion_block
        except ImportError:
            from amo_immersion_guard import diary_prompt_block as _immersion_block
        _immersion_extra = _immersion_block(item)
        try:
            from .amo_diary_guard import fact_rules
        except ImportError:
            from amo_diary_guard import fact_rules
        _npc_line = ""
        try:
            _npc = self.p.db.kv_get("npc_summary", {}) or {}
            _act = str(item.get("activity") or "") + str(item.get("detail") or "")
            _names = [str(n) for n in (item.get("people") or item.get("companions") or []) if str(n)]
            if not _names:
                _names = [str(k) for k in _npc if str(k) and str(k) in _act]
            _hits = [(n, str(_npc.get(n) or "")) for n in _names]
            
            if _hits:
                _npc_line = ("【同席人·关系】" + "、".join("%s（%s）" % (k, v) for k, v in _hits)
                             + "；这一小时要写和她一起，不能写一个人。" + "\n")
        except Exception:
            _npc_line = ""
        _base = (
            _npc_line + fact_rules(together) + "\n"
            + _immersion_extra + "\n"
            + diary_presence_rules(together)
            + "角色设定：\n" + _brief_world(ctx.get("world") or {}) + "\n"
            "今天：%s，%s；天气：%s；所在地：%s。\n"
            "身体：%s；生理期：%s；昨日：%s。\n"
            "此刻十维情绪：%s。\n"
            "上一小时：%s\n"
            "上一小时的小日记（只作情绪衔接；地点变了就不能照搬场景）：\n%s\n"
            "这一小时：%s-%s，%s，在%s（%s），心情%s。\n"
            "【同席人】这一小时的活动文字里出现的人名（比如「沈幼微」）就是和你在一起的人；"
            "要写「和沈幼微一起」这种，不能写「一个人」。知道关系就按关系写（表姐/同事/朋友），不知道就写名字。" + "\n"
            + "【事实唯一·最高优先级】这一小时只发生这一件事：地点=%s、动作=%s。"
            "如果这条日程刚被约会/新事件覆盖，之前写过、想过的一切作废，"
            "绝不许把旧场景（比如在家做饭、屋里香不香）和现在的地点动作拼在一起；"
            "地点和动作只能有一个，冲突时以后者为准。\n"
            "下一小时：%s\n"
            "【时间边界·必须遵守】这一小时只写到本段结束（%s）为止；之后发生的事、下一小时的日程，"
            "一个字都不能写进来，哪怕只隔几分钟，也不能把两段拼成一段。\n"
            "突发事件：%s\n"
            "景区信息：%s\n"
            "%s\n"
            "%s\n"
            "%s\n"
            "%s\n"
            "%s\n"
            "请写一段 120-260 字的第一人称小日记，要求：\n"
            "1. 只写这一小时；\n"
            "2. 写清交通方式（开车/打车/地铁/走路）、具体干了什么、吃了什么、遇到什么趣事；\n"
            "3. 情绪从上一小时自然延续（郁闷/生气不会无缘无故变成开心/兴奋）；\n"
            "4. 如果是周末开车出去玩，要提到车、堵车、停车位；\n"
            "5. 口语化、生动，像真人发朋友圈，不写旁白，也不提日程/AI/模型；\n"
            "6. 承接上一小时日记：上一小时出现的人、地点、动作保持一致；\n"
            "9. 只写这一小时的新细节，不重复上一小时说过的整段；\n"
            "10. 只写这一小时（今天）：不许写明天、不许把昨天的事当今天；"
            "这一小时只有给出的日程是事实，不能添加电话/见面/新买衣服；\n"
            "11. 聊天只能间接转述大意，不许照抄原话，也不许主客体对调；"
            "提示词里的举例只是格式示意，不是素材；"
            "这一小时素材没有的互动不能写，平台名统一写“微信”；\n"
            "12. 这一小时只写当下：可以写体感/即时情绪，禁止跨时空想念（吃包子突然好想老公）、"
            "禁止虚构不在场的人（想让他送热水但他没来）、禁止总结句（今天真倒霉）；\n"
            "13. 这一小时无事发生就写“发呆、放空、刷剧”或留空，不要没话找话；\n"
            "14. 睡眠时段（23:30-07:00）不写小时日记；\n"
            "15. 允许复合情绪（发工资开心但感冒难受），别把情绪写扁平；\n"
            "16. 不许把账本金额抄进日记：基本/奖金/扣款/实发/余额的具体数字一律不写，"
            "只写“发工资了”“余额多了一点”这种感受；\n"
            "17. 只输出 JSON：{\"diary\":\"...\"}。"
        ) % (
            ctx.get("day"), (ctx.get("meta") or {}).get("label") or "普通一天",
            _weather_text(ctx.get("weather") or {}), ctx.get("location") or _city_of(ctx) or "未设置",
            (ctx.get("health") or {}).get("status", "健康"),
            (ctx.get("period") or {}).get("phase", ""),
            (ctx.get("yesterday") or {}).get("note") or "正常",
            _mood_text(ctx.get("mood") or {}),
            prev_text,
            prev_diary_text,
            item.get("start"), item.get("end"), item.get("activity"),
            item.get("place"), item.get("zone"), item.get("mood") or "平静",
            item.get("place"), item.get("activity"),
            next_text,
            item.get("end"),
            event.get("note") or "无",
            _scenic_text(scenic),
            car_line,
            asset_line,
            worn_line,
            gift_line,
            outfit_line,
        )
        _base += self._diary_place_switch(prev_item, item, _same_place)
        if _location_line:
            _base += "\n" + _location_line + "\n"
        _base += self._diary_fact_section(ctx.get("fact_pool") or [])
        return _base

    @staticmethod
    def _sanitize_hour_diary(text, facts):
        value = str(text or "")
        joined = " ".join(str(f.get("detail") or "") for f in facts if isinstance(f, dict))
        has_phone = any(w in joined for w in ("电话", "通话", "语音"))
        has_meet = any(w in joined for w in ("见面", "约会", "来家里", "一起吃饭"))
        has_chat = any(w in joined for w in ("聊天", "微信", "飞书"))
        location = ""
        for f in facts:
            if isinstance(f, dict) and f.get("type") == "状态" and "现在在" in str(f.get("detail") or ""):
                location = str(f.get("detail") or "")
        home = "家" in location
        sentences = re.split(r"(?<=[。！？!?\n])", value)
        kept = []
        for sentence in sentences:
            s = sentence.strip()
            if not s:
                continue
            if not has_phone and any(w in s for w in ("打电话", "通电话", "听见他声音", "视频通话")):
                continue
            if not has_meet and any(w in s for w in ("他抱我", "他摸我头", "他来找我", "一起吃饭")):
                continue
            if not has_chat and any(w in s for w in ("微信", "飞书")) and any(
                    w in s for w in ("聊", "回", "消息", "说")):
                continue
            if home and any(w in s for w in ("工位", "公司", "地铁")):
                continue
            kept.append(s)
        value = "".join(kept)[:600]
        # 2026-11-17：素材里没有聊天时，模型仍会把“他早上在微信上提醒我”写成事实。
        # 按句做确定性清洗：只删具体互动句，不整段丢情绪。
        try:
            from .amo_diary_guard import strip_fabricated_interaction
        except ImportError:
            from amo_diary_guard import strip_fabricated_interaction
        return strip_fabricated_interaction(value, joined, has_chat)

    async def _audit_hour_diary(self, text, facts, llm, umo):
        if not facts or llm is None:
            return True
        lines = "\n".join(str(f.get("detail") or "") for f in facts if isinstance(f, dict))[:2500]
        prompt = ("事实列表：\n%s\n\n日记：\n%s\n\n日记里有没有事实列表之外的动作或事件？"
                  "只输出 True 或 False。" % (lines, text))
        try:
            answer = await asyncio.wait_for(
                llm.chat_raw(umo, "你是事实审计员，只输出 True/False。", prompt), timeout=25)
            return "false" not in str(answer or "").lower()
        except Exception:
            return True

    @staticmethod
    def _clean_diary(text):
        text = str(text or "").strip()
        if text.startswith("{"):
            _data = json_extract(text)
            if isinstance(_data, dict):
                text = str(_data.get("diary") or _data.get("text") or "").strip()
            if text.startswith("{"):
                _m = re.search(r'"diary"\s*:\s*"(.*)"\s*}\s*$', text, re.S)
                if _m:
                    text = _m.group(1)
            text = text.strip().strip('"').strip("“”").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip().strip('"').strip("“”").strip()
        for prefix in ("小日记：", "日记：", "今天的日记：", "今天："):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        text = re.sub(r"[ \t]+", " ", text)
        text = text.replace("\r", "").strip()
        return text[:600]

    def fallback_diary(self, ctx, item, prev_item=None):
        """模型不可用时的兜底日记，仍然保证交通、地点、心情和趣事。"""
        place = str(item.get("place") or "家")
        activity = str(item.get("activity") or "待着")
        mood = str(item.get("mood") or "平静")
        weather = _weather_text(ctx.get("weather") or {})
        detail = str(item.get("detail") or "").strip()
        zone = str(item.get("zone") or "")
        event = ctx.get("event") or {}
        parts = []
        if zone == "road":
            phrase = transit_phrase(item)
            if phrase:
                parts.append("这小时我%s，%s。" % (phrase, detail or "路上人不少"))
            else:
                parts.append("这小时我在路上，%s。" % (detail or "挤得脚都没地方放"))
        elif zone == "scenic":
            parts.append("这小时我在%s，%s。" % (place, activity))
            if detail:
                parts.append(detail + "。")
        elif zone == "office":
            parts.append("这小时我在%s，%s。" % (place, activity))
        elif zone == "home" and item.get("category") == "sleep":
            parts.append("这小时我睡得迷迷糊糊的，连梦都做得断断续续。")
        else:
            parts.append("这小时我在%s，%s。" % (place, activity))
        if event.get("kind") != "none" and (item.get("event") or item.get("effects")):
            parts.append("今天这事来得太突然了，%s。" % (event.get("note") or ""))
        parts.append("外面%s，我心情%s，%s。" % (
            weather, mood, detail or "只想安安静静待一会儿",
        ))
        return "".join(parts)[:600]
