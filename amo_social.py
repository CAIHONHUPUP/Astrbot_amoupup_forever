# -*- coding: utf-8 -*-
"""SocialMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

from typing import TYPE_CHECKING

import json
import os
import random
import re
import time

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        AstrMessageEvent, Comp, _rank_edges, clamp, lark_quote_kwargs, logger, merge_uid_alias_data, now_ts, parse_relations, plan_relation_edges, sentiment, to_float, to_int, truncate,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            AstrMessageEvent, Comp, _rank_edges, clamp, lark_quote_kwargs, logger, merge_uid_alias_data, now_ts, parse_relations, plan_relation_edges, sentiment, to_float, to_int, truncate,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            AstrMessageEvent, Comp, _rank_edges, clamp, lark_quote_kwargs, logger, merge_uid_alias_data, now_ts, parse_relations, plan_relation_edges, sentiment, to_float, to_int, truncate,
        )


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class SocialMixin(_MixinBase):
    def _uid_alias_mapping(self):
        """读映射表（配置优先，其次数据目录 uid_map.json）；按内容+mtime 缓存。"""
        cache_key = None
        try:
            text = str(self.cfg_str("uid_alias_map", "") or "").strip().lstrip("﻿")
            map_path = os.path.join(os.path.dirname(str(self.db.path)), "uid_map.json")
            stamp = os.path.getmtime(map_path) if os.path.exists(map_path) else 0
            cache_key = (text, stamp)
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._uid_alias_mapping", _amo_exc)
            cache_key = None
        if getattr(self, "_uid_alias_cache_key", None) != cache_key:
            raw = {}
            try:
                text = str(self.cfg_str("uid_alias_map", "") or "").strip().lstrip("﻿")
                parsed = json.loads(text) if text else {}
                if isinstance(parsed, dict):
                    raw.update(parsed)
            except Exception as exc:
                logger.warning("[amo] uid_alias_map 配置解析失败: %s" % exc)
            try:
                map_path = os.path.join(os.path.dirname(str(self.db.path)), "uid_map.json")
                if os.path.exists(map_path):
                    # utf-8-sig：Windows 记事本另存的 JSON 常带 BOM，用 utf-8 会直接解析失败
                    with open(map_path, "r", encoding="utf-8-sig") as fh:
                        parsed = json.load(fh)
                    if isinstance(parsed, dict):
                        for key, value in parsed.items():
                            raw.setdefault(key, value)
            except Exception as exc:
                logger.warning("[amo] uid_map 读取失败: %s" % exc)
            raw = {str(k): str(v) for k, v in raw.items() if k and v}
            # 解析链式映射（A→B、B→C 归到 A→C），并丢掉自映射
            resolved = {}
            for key in raw:
                current = key
                seen = {key}
                while current in raw:
                    nxt = raw[current]
                    if nxt in seen:
                        break
                    seen.add(nxt)
                    current = nxt
                if current != key:
                    resolved[key] = current
            self._uid_alias_map = resolved
            self._uid_alias_cache_key = cache_key
        return getattr(self, "_uid_alias_map", {}) or {}

    def _canonical_uid(self, event):
        """把平台账号换算成同一个人的主 uid；关掉开关就返回原 uid。"""
        try:
            uid = str(event.get_sender_id())
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._canonical_uid", _amo_exc)
            return ""
        if not self.cfg_bool("uid_merge_enabled", True):
            return uid
        return self._uid_alias_mapping().get(uid, uid)

    def _merge_alias_profiles(self):
        """一次性把旧版本按平台 ID 建好的档案并进主 uid（映射变了会再跑一次）。"""
        if not self.cfg_bool("uid_merge_enabled", True):
            return
        mapping = self._uid_alias_mapping()
        if not mapping:
            return
        signature = json.dumps(mapping, sort_keys=True, ensure_ascii=False)
        try:
            if self.db.kv_get("uid_alias_merged_signature", "") == signature:
                return
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._merge_alias_profiles", _amo_exc)
        try:
            report = merge_uid_alias_data(self.db.conn, mapping)
            self.db.kv_set("uid_alias_merged_signature", signature)
            logger.info("[amo] 跨平台账号合并完成: %s" % report)
        except Exception as exc:
            logger.warning("[amo] 跨平台账号合并失败: %s" % exc)

    def _is_admin(self, event):
        uid = self._canonical_uid(event)
        if uid in self.admin_uids:
            return True
        try:
            umo = str(event.unified_msg_origin or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._is_admin", _amo_exc)
            umo = ""
        # 每条消息都会走这里，get_config 没必要每次读：按会话缓存 60 秒
        cache = getattr(self, "_admin_cache", None)
        if cache is None:
            cache = {}
            self._admin_cache = cache
        now = time.time()
        hit = cache.get(umo)
        if hit and now - hit[0] < 60:
            return uid in hit[1]
        try:
            cfg = self.context.get_config(umo)
            admins = cfg.get("admins_id", []) if cfg else []
            if isinstance(admins, str):
                admins = [item.strip() for item in admins.split(",") if item.strip()]
            names = {str(item) for item in (admins or [])}
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._is_admin", _amo_exc)
            names = set()
        cache[umo] = (now, names)
        return uid in names

    async def _learn(self, event, text):
        uid = self._canonical_uid(event)
        name = event.get_sender_name() or uid
        umo = event.unified_msg_origin
        group_id = event.get_group_id()
        private = event.is_private_chat()
        profile = self.db.touch_profile(
            uid, name, event.get_platform_name(), umo, group_id, private
        )
        if private and umo:
            self._record_channel(uid, umo, event.get_platform_name())
        if profile and not profile.get("is_admin") and uid in self.admin_uids:
            self.db.update_profile(uid, is_admin=1)
            profile["is_admin"] = 1

        self.state.observe(text)
        if self.cfg_bool("auto_relation", True):
            try:
                await self._learn_relations(event, text, uid)
            except Exception as exc:
                logger.debug("[amo] 关系自动识别失败: %s" % exc)
        # 上下文先进内存；本地缓存默认关掉，原始聊天交给记忆终端
        self._ctx_remember(umo, uid, name, "user", text)
        if self.cfg_bool("local_chat_cache", False):
            self.db.add_message(umo, uid, name, "user", text)
        try:
            await self._data_call(
                "add_message", self._data_bot_id(), umo, uid, name, "user", text,
                platform=self._platform_of(event), message_id=self._event_message_id(event),
            )
        except Exception as exc:
            logger.warning("[amo] 聊天记录同步失败: %s" % exc)

        senti = sentiment(text)
        affection = to_int(profile.get("affection"), 10)
        if senti["aff"] or senti["pos"]:
            affection += 2
        if senti["neg"] or senti["insult"]:
            affection -= 2
        affection = int(clamp(affection, 0, 100))

        # 记忆只在外挂记忆插件里；这里只把生日这种档案字段记进画像。
        match = re.search(r"生日(?:是|在)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text or "")
        if match:
            self.db.update_profile(uid, birthday="%02d-%02d" % (to_int(match.group(1)), to_int(match.group(2))))

        fields = {"affection": affection}
        relation = profile.get("relation", "普通")
        if relation == "普通" and to_int(profile.get("msg_count"), 0) >= self.cfg_int("familiar_msg_count", 30):
            fields["relation"] = "熟识"
        elif relation == "熟识" and senti["insult"]:
            fields["relation"] = "抵触"
        self.db.update_profile(uid, **fields)
        try:
            self._learn_alias(event, text, uid, profile)
        except Exception as exc:
            logger.debug("[amo] 学习称呼失败: %s" % exc)

        if group_id:
            times = self._group_times.setdefault(group_id, [])
            times.append(now_ts())
            if len(times) > 200:
                self._group_times[group_id] = times[-200:]
            self._last_group = group_id
        self._last_umo = umo

    def _register_lover(self, uid):
        uid = str(uid or "").strip()
        if uid:
            self.db.kv_set("lover_uid_" + uid, True)

    def _is_registered_lover(self, uid):
        uid = str(uid or "").strip()
        if not uid:
            return False
        if self.db.kv_get("lover_uid_" + uid, False):
            return True
        profile = self.db.get_profile(uid) or {}
        if str(profile.get("relation") or "") == "相恋":
            self._register_lover(uid)
            return True
        return False

    def _restore_lover_relation(self, uid):
        uid = str(uid or "").strip()
        if not self._is_registered_lover(uid):
            return
        profile = self.db.get_profile(uid) or {}
        if str(profile.get("relation") or "") in ("抵触", "仇恨"):
            self.db.update_profile(uid, relation="相恋")
            logger.info("[amo] 恋人 UID 恢复为相恋: %s" % uid)

    def _lover_aliases(self):
        aliases = {}
        try:
            profiles = self.db.list_profiles("", 500)
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._lover_aliases", _amo_exc)
            profiles = []
        for profile in profiles:
            uid = str(profile.get("uid") or "")
            if str(profile.get("relation") or "") == "相恋":
                self._register_lover(uid)
            elif not self._is_registered_lover(uid):
                continue
            values = [profile.get("name") or ""]
            values.extend(re.split(r"[；;、,，/\s]+", str(profile.get("alias") or "")))
            for value in values:
                name = str(value or "").strip()
                if len(name) >= 1:
                    aliases[name] = uid
        return aliases

    def _learn_alias(self, event, text, uid, profile):
        """从“以后叫我XX / 叫他XX”里学习称呼，并在后续沿用。"""
        blocked = {"干嘛", "什么", "名字", "滚", "闭嘴", "一声", "一下", "出来"}
        alias = ""
        for pattern in (
            r"(?:以后|今后|你可以|可以|请)?叫我\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})",
            r"叫我\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})\s*(?:就行|就好|吧)",
        ):
            match = re.search(pattern, str(text or ""))
            if match:
                candidate = match.group(1).strip()
                if candidate and candidate not in blocked:
                    alias = candidate
                    break
        if alias:
            current = str(profile.get("alias") or "")
            if alias not in current:
                new_alias = (current + "；" + alias).strip("；")[:80]
                self.db.update_profile(uid, alias=new_alias)
                logger.debug("[amo] 记住称呼: %s -> %s" % (uid, alias))

        try:
            messages = event.message_obj.message
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._learn_alias", _amo_exc)
            messages = []
        at_targets = [
            str(getattr(comp, "qq", ""))
            for comp in (messages or [])
            if Comp is not None and isinstance(comp, Comp.At)
        ]
        for comp in messages:
            if Comp is None or not isinstance(comp, Comp.At):
                continue
            target = str(getattr(comp, "qq", ""))
            if not target or target in ("all", str(event.get_self_id())):
                continue
            # “以后喊他XX”通常跟在最后一个 @ 后面；不要误记到前一个被 @ 的人。
            if at_targets and target != at_targets[-1]:
                continue
            for pattern in (
                r"(?:以后|就)?(?:喊|叫|称呼)(?:他|她|ta|TA|做)\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})",
            ):
                match = re.search(pattern, str(text or ""))
                if not match:
                    continue
                candidate = match.group(1).strip()
                if not candidate or candidate in blocked:
                    continue
                target_profile = self.db.get_profile(target)
                if not target_profile:
                    self.db.touch_profile(target, candidate, event.get_platform_name(), "", "", False)
                    target_profile = self.db.get_profile(target)
                current = str(target_profile.get("alias") or "")
                if candidate not in current:
                    self.db.update_profile(target, alias=(current + "；" + candidate).strip("；")[:80])
                break
            # 群里反复 @ 同一个人并叫同一个短称呼，累计三次后记住。
            plain = re.sub(r"@\S+", "", str(text or "")).strip()
            common = {
                "在吗", "你好", "早安", "晚安", "吃了吗", "干嘛", "出来", "看这个",
                "哈哈", "笑死", "救命", "谢谢", "收到", "好的", "可以", "不行",
                "快看", "快来", "过来", "上线", "打游戏", "起床", "睡觉", "上班", "下班",
            }
            if 1 <= len(plain) <= 8 and plain not in common and not re.search(r"[，。！？!?、；;：:]", plain):
                seen_key = "alias_seen_" + target + "_" + plain
                seen = to_int(self.db.kv_get(seen_key, 0), 0) + 1
                self.db.kv_set(seen_key, seen)
                if seen >= 3:
                    alias_profile = self.db.get_profile(target) or {}
                    current = str(alias_profile.get("alias") or "")
                    if plain not in current:
                        self.db.update_profile(target, alias=(current + "；" + plain).strip("；")[:80])
                    self.db.kv_set(seen_key, 0)

    def _ctx_remember(self, umo, uid, name, role, content):
        """把一句话放进内存缓冲；不落库也能有上下文。"""
        umo = str(umo or "")
        text = str(content or "").strip()
        if not umo or not text:
            return
        hub = self._memory_hub()
        if hub is not None:
            try:
                hub.buffer.add(umo, uid, name, role, text)
            except Exception as _amo_exc:
                _note_swallowed("amo_social.HumanCorePlugin._ctx_remember", _amo_exc)
        try:
            if hub is not None and str(role) != "bot":
                hub.next_turn()
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._ctx_remember", _amo_exc)
        buf = getattr(self, "_ctx_buffer", None)
        if buf is None:
            buf = {}
            self._ctx_buffer = buf
        rows = buf.get(umo)
        if rows is None:
            rows = []
            buf[umo] = rows
        rows.append({
            "umo": umo,
            "uid": str(uid or ""),
            "name": str(name or ""),
            "role": "bot" if str(role) == "bot" else "user",
            "content": text,
            "created_at": now_ts(),
        })
        if len(rows) > self.CONTEXT_BUFFER_LIMIT:
            del rows[:len(rows) - self.CONTEXT_BUFFER_LIMIT]

    def _group_roster(self, umo, current_uid="", limit=8):
        """群里最近说过话的人：uid → 称呼 / 关系 / 聊过多少句。

        让她在群里先认人：谁在说话、和她什么关系、该用什么语气，而不是把所有人都当同一个人。
        """
        roster = []
        seen = set()
        try:
            rows = self._recent(umo, 40)
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._group_roster", _amo_exc)
            return roster
        for row in rows:
            if str(row.get("role") or "") != "user":
                continue
            uid = str(row.get("uid") or "")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            profile = {}
            try:
                profile = self.db.get_profile(uid) or {}
            except Exception as _amo_exc:
                _note_swallowed("amo_social.HumanCorePlugin._group_roster", _amo_exc)
                profile = {}
            name = (
                str(profile.get("alias") or "").strip()
                or str(profile.get("name") or "").strip()
                or str(row.get("name") or "").strip()
                or uid
            )
            relation = str(profile.get("relation") or "普通")
            msg_count = to_int(profile.get("msg_count"), 0)
            roster.append({
                "uid": uid,
                "name": name,
                "relation": relation,
                "msg_count": msg_count,
                "current": bool(current_uid) and uid == str(current_uid),
            })
            if len(roster) >= max(1, int(limit)):
                break
        roster.sort(key=lambda item: (not item["current"], -item["msg_count"]))
        return roster

    def _group_roster_text(self, umo, current_uid="", limit=8):
        roster = self._group_roster(umo, current_uid, limit)
        if not roster:
            return ""
        lines = []
        for item in roster:
            tag = "（现在这句就是ta说的）" if item["current"] else ""
            if item["msg_count"] >= 200:
                familiar = "很熟"
            elif item["msg_count"] >= 30:
                familiar = "熟识"
            elif item["msg_count"] >= 1:
                familiar = "刚认识"
            else:
                familiar = "陌生"
            lines.append(
                "· %s（UID %s）：和你关系「%s」，%s%s"
                % (item["name"], item["uid"], item["relation"], familiar, tag)
            )
        return "\n".join(lines)

    def _group_speaker_count(self, umo, limit=20, window=600):
        """最近一段时间内群聊里的不同发言人数。"""
        try:
            rows = self._recent(umo, limit)
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._group_speaker_count", _amo_exc)
            return 0
        cutoff = now_ts() - window
        speakers = {
            str(row.get("uid") or "")
            for row in rows
            if row.get("role") == "user"
            and to_float(row.get("created_at"), 0) >= cutoff
            and str(row.get("uid") or "")
        }
        return len(speakers)

    def _should_quote_group(self, umo):
        """群聊要不要引用原消息：人数过阈值后按概率引用。

        人数没到阈值（人少）就不引用；到了阈值也不该句句引用——
        真人只在需要指代时才引用，所以再加一层概率。
        `quote_group_probability`：1.0 = 旧行为（一直引用），0 = 从不引用。
        """
        try:
            threshold = self.cfg_int("quote_group_speaker_threshold", 3)
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._should_quote_group", _amo_exc)
            threshold = 3
        if threshold <= 0:
            return False
        try:
            if self._group_speaker_count(umo) < threshold:
                return False
            prob = self.cfg_float("quote_group_probability", 0.4)
            if prob <= 0:
                return False
            if prob >= 1:
                return True
            return random.random() < prob
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._should_quote_group", _amo_exc)
            return False

    def _quote_allowed(self, umo, group=None):
        """能不能引用原消息：私聊（任何平台）永远不引用，群聊按人数 + 概率。

        `group` 不传就看 umo 里的消息类型。私聊不引用是硬规则：无论 QQ、飞书
        还是别的平台，都不该在私聊里把他的话再引用一遍；群聊才看
        `_should_quote_group()`（人数阈值 + `quote_group_probability`）。
        """
        umo = str(umo or "")
        if group is None:
            group = "GroupMessage" in umo
        if not group:
            return False
        if "GroupMessage" not in umo:
            return False
        try:
            return bool(self._should_quote_group(umo))
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._quote_allowed", _amo_exc)
            return False

    def _install_qq_quote_guard(self):
        """QQ 私聊也不许引用。

        AstrBot 核心的 `reply_with_quote` 是在插件 `on_decorating_result` **之后**
        才往消息链里插 Reply 的，插件钩子里摘不干净；所以在发送前再兜一层。
        群聊保持原样（插件自己按人数 + 概率加 Reply）。
        """
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._install_qq_quote_guard", _amo_exc)
            return
        original = getattr(AiocqhttpMessageEvent, "_amo_original_send", None)
        if original is None:
            original = AiocqhttpMessageEvent.send
            AiocqhttpMessageEvent._amo_original_send = original

        async def send(ev, message):
            try:
                if not ev.get_group_id():
                    message.chain = [
                        comp for comp in message.chain if not isinstance(comp, Comp.Reply)
                    ]
            except Exception as _amo_exc:
                _note_swallowed("amo_social.HumanCorePlugin._install_qq_quote_guard.send", _amo_exc)
            await original(ev, message)

        AiocqhttpMessageEvent.send = send
        AiocqhttpMessageEvent._amo_quote_guard = True
        logger.info("[amo] QQ 引用守卫已安装：私聊不引用，群聊按概率")

    def _install_lark_quote_guard(self):
        """飞书私聊不要引用：适配器把每条消息都发成「回复」。

        只装在飞书环境；装一次就够（类上留标记，热重载不会套娃）。
        群聊仍然按 `_should_quote_group()` 的概率决定引用，私聊一律普通发送。
        """
        try:
            from astrbot.core.platform.sources.lark.lark_event import LarkMessageEvent
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._install_lark_quote_guard", _amo_exc)
            return
        async def send(ev, message):
            try:
                is_private = bool(ev.is_private_chat())
            except Exception as _amo_exc:
                _note_swallowed("amo_social.HumanCorePlugin._install_lark_quote_guard.send", _amo_exc)
                is_private = False
            # 只认消息链里已经有的 Reply（插件按人数+概率加的那一条）；
            # 这里不能重新掷一次概率，否则「插件决定不引用」会被守卫再翻成引用。
            try:
                has_reply = any(isinstance(comp, Comp.Reply) for comp in message.chain)
            except Exception as _amo_exc:
                _note_swallowed("amo_social.HumanCorePlugin._install_lark_quote_guard.send", _amo_exc)
                has_reply = False
            quote = bool(has_reply and not is_private)
            if not quote:
                try:
                    message.chain = [comp for comp in message.chain if not isinstance(comp, Comp.Reply)]
                except Exception as _amo_exc:
                    _note_swallowed("amo_social.HumanCorePlugin._install_lark_quote_guard.send", _amo_exc)
            kwargs = lark_quote_kwargs(
                is_private,
                getattr(ev.message_obj, "message_id", ""),
                ev.get_sender_id(),
                ev.get_group_id(),
                quote,
            )
            message_type = getattr(getattr(ev.message_obj, "type", None), "value", "")
            try:
                await LarkMessageEvent.send_message_chain(
                    message, ev.bot, message_type=message_type, **kwargs,
                )
            except Exception as exc:
                if "reply_message_id" in kwargs:
                    raise
                # 普通发送失败（比如 open_id 被拒）就退回原来的引用发送，别把话吞了
                logger.warning("[amo] 飞书普通发送失败，退回引用发送: %s" % exc)
                await LarkMessageEvent.send_message_chain(
                    message, ev.bot,
                    reply_message_id=getattr(ev.message_obj, "message_id", ""),
                    message_type=message_type,
                )
            finally:
                # 父类只做 metrics 记录，不能少
                await AstrMessageEvent.send(ev, message)

        LarkMessageEvent.send = send
        LarkMessageEvent._amo_quote_guard = True
        logger.info("[amo] 飞书引用守卫已安装：私聊不引用，群聊按概率")

    async def _learn_relations(self, event, text, uid):
        """从对话里认关系：他说「小明是我同事」就记一条边。

        只认明确陈述 + 能在画像里唯一定位到人；管理员手动设过的不覆盖。
        """
        if parse_relations is None or not text or not uid:
            return
        try:
            pairs = parse_relations(text)
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._learn_relations", _amo_exc)
            return
        if not pairs:
            return
        name_map = {}
        try:
            for item in self.db.list_profiles(limit=500):
                for key in ("name", "alias"):
                    raw = str(item.get(key) or "").strip()
                    if not raw:
                        continue
                    for piece in re.split(r"[;；,，、/|]", raw):
                        name = piece.strip()
                        if len(name) >= 2:
                            name_map.setdefault(name, set()).add(str(item.get("uid") or ""))
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._learn_relations", _amo_exc)
            return
        try:
            existing = {}
            for edge in self.db.list_relations():
                pair = tuple(sorted([str(edge.get("a_uid") or ""), str(edge.get("b_uid") or "")]))
                existing[pair] = str(edge.get("note") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._learn_relations", _amo_exc)
            existing = {}
        for who, relation, evidence in pairs:
            targets = [item for item in name_map.get(who, set()) if item and item != str(uid)]
            if len(targets) != 1:
                continue
            target = targets[0]
            pair = tuple(sorted([str(uid), target]))
            old_note = existing.get(pair, "")
            if old_note and not old_note.startswith("对话："):
                # 管理员手动设过的关系，不拿聊天内容覆盖
                continue
            note = "对话：" + truncate(evidence, 24)
            try:
                if not self.db.upsert_relation(uid, target, relation, note):
                    continue
                logger.debug("[amo] 关系网自动记录: %s --%s--> %s" % (uid, relation, target))
                await self._push_relation_to_data(uid, target, relation, note)
            except Exception as exc:
                logger.debug("[amo] 自动关系写入失败: %s" % exc)

    def _relation_network_text(self, uid):
        """给当前用户生成两跳内的关系网摘要；只读。

        「陌生人」只在面板图谱里出现，不进提示词——不然每句都带一串「小明和小满是陌生人」。
        """
        try:
            edges = [edge for edge in self.db.list_relations()
                     if str(edge.get("relation") or "") != "陌生人"]
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._relation_network_text", _amo_exc)
            return ""
        uid = str(uid or "")
        if not edges:
            return ""
        names = {}
        try:
            for item in self.db.list_profiles(limit=500):
                names[str(item.get("uid"))] = item.get("alias") or item.get("name") or str(item.get("uid"))
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._relation_network_text", _amo_exc)
        names["bot"] = str(getattr(self, "bot_name", "") or "你")
        picked = []
        if _rank_edges is not None:
            try:
                # 图小的时候放宽到三跳，图大了只走两跳，避免噪声和 token 膨胀
                hops = 3 if len(edges) <= 40 else 2
                picked = _rank_edges(edges, uid, 12, hops)
            except Exception as _amo_exc:
                _note_swallowed("amo_social.HumanCorePlugin._relation_network_text", _amo_exc)
                picked = []
        if not picked:
            direct = []
            neighbors = set()
            for edge in edges:
                a, b = str(edge.get("a_uid")), str(edge.get("b_uid"))
                if uid in (a, b):
                    direct.append(edge)
                    neighbors.add(b if a == uid else a)
            second = []
            for edge in edges:
                if edge in direct:
                    continue
                a, b = str(edge.get("a_uid")), str(edge.get("b_uid"))
                if a in neighbors or b in neighbors:
                    second.append(edge)
            picked = (direct + second)[:12]
        lines = []
        for edge in picked[:12]:
            a, b = str(edge.get("a_uid")), str(edge.get("b_uid"))
            na = names.get(a, a)
            nb = names.get(b, b)
            lines.append("%s和%s是%s" % (na, nb, edge.get("relation", "朋友")))
        return "\n".join(lines)

    def _disturb_category(self, snap):
        category = (snap.get("activity") or {}).get("category", "")
        return category in ("sleep", "work", "study")

    def _disturb_active(self, umo):
        return to_float(getattr(self, "_disturb_until", {}).get(str(umo), 0), 0) > now_ts()

    def _tone_with_continuity(self, umo, tone):
        """给语气加情绪惯性，避免上一秒发火、下一秒突然甜蜜。"""
        tone = str(tone or "")
        if self._disturb_active(umo):
            if "不要一秒变甜蜜" not in tone:
                tone += " 你刚被吵醒/打扰，还在烦躁，不要一秒变甜蜜。"
        last = getattr(self, "_last_affect", {}).get(str(umo))
        if last and now_ts() - to_float(last[0], 0) < 1200:
            previous = str(last[1] or "")
            if any(word in previous for word in ("烦躁", "暴躁", "生气", "被打扰")) and "慢慢缓和" not in tone:
                tone += " 上一轮你还在气头上，态度要慢慢缓和，不要突然撒娇甜蜜。"
        self._last_affect[str(umo)] = (now_ts(), tone)
        return tone

    def _apply_disturbance(self, umo):
        now = now_ts()
        self._disturb_until[str(umo)] = now + max(30, self.cfg_int("disturb_tone_seconds", 900))
        self._disturb_cooldown[str(umo)] = now + 120
        self._disturb_reset_at[str(umo)] = now
        last = to_float(getattr(self, "_disturb_last", {}).get(str(umo), 0), 0)
        if now - last >= 60:
            penalty = max(1, self.cfg_int("disturb_mood_penalty", 6))
            self.state.nudge(
                生气=penalty,
                郁闷=max(1, penalty // 2),
                疲惫=max(1, penalty // 2),
                开心=-max(1, penalty // 3),
            )
            self._disturb_last[str(umo)] = now
        logger.debug("[amo] 被打扰：消息过多，强制回复并降低心情（%s）" % umo)

    def _disturb_pressure(self, umo, uid, text="", group=False):
        """最近窗口内，同一个人连续发消息数 / 群里叫机器人的消息数。"""
        try:
            rows = self._recent(umo, 24)
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._disturb_pressure", _amo_exc)
            return 0
        window = max(1, self.cfg_int("disturb_window_minutes", 10)) * 60
        cutoff = max(
            now_ts() - window,
            to_float(getattr(self, "_disturb_reset_at", {}).get(str(umo), 0), 0),
        )
        same_sender = 0
        mentions = 0
        names = [self.bot_name] + list(self.bot_aliases)
        for row in reversed(rows):
            if to_float(row.get("created_at"), 0) < cutoff:
                continue
            if row.get("role") == "bot":
                break
            if row.get("role") != "user":
                continue
            if group:
                if str(row.get("uid")) == str(uid):
                    same_sender += 1
                content = str(row.get("content") or "")
                if any(name and len(str(name)) >= 2 and str(name) in content for name in names):
                    mentions += 1
            else:
                same_sender += 1
        return max(same_sender, mentions) if group else same_sender

    def _mark_bot_initiated(self, umo):
        key = str(umo)
        self._bot_initiated[key] = now_ts()
        # 她主动发的消息代表当前是她来找你，不该延续上一轮的火气。
        self._disturb_until[key] = 0
        self._last_affect[key] = (now_ts(), "你主动发起聊天，语气自然亲昵")

    def _recent_bot_initiated(self, umo, seconds=3600):
        return now_ts() - to_float(getattr(self, "_bot_initiated", {}).get(str(umo), 0), 0) < seconds

    def _should_disturb(self, snap, umo, uid, text="", group=False):
        if not self.cfg_bool("disturb_reply_enabled", True):
            return False
        if self._recent_bot_initiated(umo):
            return False
        if not self._disturb_category(snap):
            return False
        if to_float(getattr(self, "_disturb_cooldown", {}).get(str(umo), 0), 0) > now_ts():
            return False
        threshold = max(1, self.cfg_int("disturb_reply_threshold", 3))
        return self._disturb_pressure(umo, uid, text, group=group) >= threshold

    async def _sediment_relations(self):
        """按真实互动量沉淀关系网：聊得多就是熟人/朋友，并让 bot 通过你认识你的人。

        只补两类 note（算法见 relation_sediment.py）：
        1. bot ↔ 用户（全部历史累计）：聊过一句 / 只在人物列表里 → 陌生人；
           ≥15 条且跨 ≥2 天 → 熟人；≥60 条且跨 ≥7 天 → 朋友；
        2. 两跳推断：bot↔用户已是熟/朋友，而用户↔A 有非仇人边 → bot↔A 记「朋友」，
           note「推断：经X认识」，每人最多 5 条。
        管理员手设的关系（note 不带「自动：」「推断：」）永远不覆盖；
        家人/恋人/仇人不改；自动边只升不降。
        """
        if plan_relation_edges is None:
            return
        # 0 = 统计全部历史：老用户、只聊过一两句的人也要进关系网
        days = max(0, int(self.cfg_int("relation_sediment_days", 0)))
        try:
            stats = self.db.interaction_stats(days)
        except Exception as exc:
            logger.debug("[amo] 互动统计失败: %s" % exc)
            return
        if self.cfg_bool("relation_stranger_edges", True):
            seen = {str(row.get("uid") or "") for row in stats}
            try:
                # 只出现在人物列表、从没聊过天的人，也记成「陌生人」进图；
                # 觉得面板边太多就把 relation_stranger_edges 关掉，只记聊过天的人
                for item in self.db.list_profiles(limit=1000):
                    uid = str(item.get("uid") or "")
                    if uid and uid not in seen:
                        stats.append({"uid": uid, "c": 0, "f": 0, "l": 0})
                        seen.add(uid)
            except Exception as exc:
                logger.debug("[amo] 读取人物列表失败: %s" % exc)
        if not stats:
            return

        names = {}
        try:
            for item in self.db.list_profiles(limit=500):
                names[str(item.get("uid"))] = str(item.get("alias") or item.get("name") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._sediment_relations", _amo_exc)
            names = {}

        existing = {}
        try:
            for edge in self.db.list_relations():
                pair = tuple(sorted([str(edge.get("a_uid") or ""), str(edge.get("b_uid") or "")]))
                existing[pair] = (str(edge.get("relation") or ""), str(edge.get("note") or ""))
        except Exception as exc:
            logger.debug("[amo] 读取关系网失败: %s" % exc)
            return

        infer_limit = max(1, min(10, int(self.cfg_int("relation_infer_per_user", 5))))
        try:
            plan = plan_relation_edges(stats, existing, names, infer_limit)
        except Exception as exc:
            logger.debug("[amo] 关系沉淀计算失败: %s" % exc)
            return
        # 一次最多写 300 条，人物很多时分成几轮补齐，别把 tick 卡太久
        for a_uid, b_uid, relation, note in plan[:300]:
            try:
                if not self.db.upsert_relation(a_uid, b_uid, relation, note):
                    continue
                await self._push_relation_to_data(a_uid, b_uid, relation, note)
            except Exception as exc:
                logger.debug("[amo] 关系沉淀写入失败: %s" % exc)

    def _decay_hate_relations(self):
        try:
            cutoff = now_ts() - 7 * 86400
            rows = self.db.conn.execute(
                "SELECT uid FROM profiles WHERE relation='仇恨' AND relation_at>0 AND relation_at<=?",
                (cutoff,),
            ).fetchall()
            for row in rows:
                self.db.update_profile(str(row["uid"]), relation="抵触")
                logger.info("[amo] 仇恨超过7天，降为抵触: %s" % row["uid"])
        except Exception as exc:
            logger.debug("[amo] 仇恨关系衰减失败: %s" % exc)

    async def _auto_learn(self):
        """定时提炼长期事实，写进外挂记忆插件；本插件不再存记忆。

        候选用户以前取本地 messages 表；可聊天记录从 5.9.4 起就不落本地库了，
        那张表从 2026-11-09 起一直冻着，于是自动学习一个用户都挑不出来，
        用户说什么都不会变成记忆（只剩里程碑摘要在写）。
        现在按「画像里有私聊会话 + 数据插件里最近一小时确实说过话」挑人。
        """
        cutoff = now_ts() - 3600
        handled = 0
        try:
            profiles = self.db.list_profiles("", 20)
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._auto_learn", _amo_exc)
            return
        for profile_row in profiles:
            if handled >= 2:
                break
            uid = str(profile_row.get("uid") or "").strip()
            if not uid:
                continue
            # QQ、飞书都算同一个人：只认 private_umo 会漏掉“他最近在另一个平台说话”
            umos = [str(item) for item in (self._ritual_umos(profile_row) or []) if item][:3]
            if not umos:
                # 还没私聊过：不要退到全局会话，否则群聊的话会被当成他说的
                continue
            key = "auto_learn_" + uid
            last = to_float(self.db.kv_get(key, 0))
            interval = max(5, self.cfg_int("auto_learn_minutes", 15)) * 60
            if now_ts() - last < interval:
                continue
            rows = []
            for one_umo in umos:
                try:
                    rows.extend(await self._recent_by_uid(uid, 20, umo=one_umo) or [])
                except Exception as exc:
                    logger.debug("[amo] 自动学习取近聊失败(%s): %s" % (one_umo, exc))
            rows.sort(key=lambda item: to_float(item.get("created_at"), 0))
            rows = rows[-20:]
            newest = 0.0
            for row in rows:
                newest = max(newest, to_float(row.get("created_at"), 0))
            if newest < cutoff:
                continue
            handled += 1
            try:
                lines = []
                for row in rows:
                    row_umo = str(row.get("umo") or "").strip()
                    if row_umo and row_umo not in umos:
                        continue
                    role = str(row.get("role") or "")
                    if role == "summary":
                        continue
                    who = "我" if role == "bot" else "他"
                    lines.append("[" + who + "] " + str(row.get("content") or ""))
                if not lines:
                    continue
                facts = await self.llm.extract_facts(
                    umos[0], str(profile_row.get("name") or "") or uid, lines
                )
                ok_any = False
                for fact in facts:
                    written = await self._write_memory_checked(
                        uid, fact["content"], fact["kind"], fact["tier"]
                    )
                    ok_any = ok_any or written
                if facts and not ok_any:
                    logger.warning("[amo] 记忆写入失败，本轮不标记已学: %s" % uid)
                    continue
                self.db.kv_set(key, now_ts())
            except Exception as exc:
                logger.debug("[amo] 自动学习失败: %s" % exc)

    def _purge_profile_kv(self, uid):
        """删掉这个 uid 专属的 kv（渠道/节流/主动消息/纪念日…），返回删除条数。"""
        prefixes = (
            "channels:", "last_channel:", "lover_uid_", "auto_learn_",
            "angel_profile_sync_", "companion_profile_sync_", "memcomp_profile_sync_",
            "proactive_ignored_", "proactive_depressed_until_",
            "proactive_followup_last_", "proactive_forced_hour_", "proactive_pending_",
            "ext_mem_count_", "ext_mem_count_at_",
        )
        try:
            rows = self.db.conn.execute("SELECT key FROM kv").fetchall()
        except Exception as _amo_exc:
            _note_swallowed("amo_social.HumanCorePlugin._purge_profile_kv", _amo_exc)
            return 0
        keys = []
        for row in rows:
            try:
                key = str(row["key"] if hasattr(row, "keys") else row[0])
            except Exception as _amo_exc:
                _note_swallowed("amo_social.HumanCorePlugin._purge_profile_kv", _amo_exc)
                continue
            if any(key == prefix + uid for prefix in prefixes):
                keys.append(key)
            elif key.startswith("ritual_") and ("_" + uid + "_") in key:
                keys.append(key)
        if not keys:
            return 0
        try:
            for key in keys:
                self.db.conn.execute("DELETE FROM kv WHERE key=?", (key,))
            self.db.conn.commit()
        except Exception as exc:
            logger.debug("[amo] 清理 kv 失败: %s" % exc)
            return 0
        return len(keys)
