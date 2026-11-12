# -*- coding: utf-8 -*-
"""ProactiveMixin：从 main.py 拆出的 mixin，由 HumanCorePlugin 组合使用。"""
from __future__ import annotations

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
from datetime import timedelta
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_shared import (
        _greeting_targets, _now, _within_minutes, in_minute_range, logger, minutes_now, now_ts, parse_hhmm, quiet_now, to_float, to_int, today_str,
    )
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_shared import (
            _greeting_targets, _now, _within_minutes, in_minute_range, logger, minutes_now, now_ts, parse_hhmm, quiet_now, to_float, to_int, today_str,
        )
    except ImportError:  # 直接按文件加载时
        from amo_shared import (
            _greeting_targets, _now, _within_minutes, in_minute_range, logger, minutes_now, now_ts, parse_hhmm, quiet_now, to_float, to_int, today_str,
        )


if TYPE_CHECKING:
    from .amo_types import PluginLike as _MixinBase
else:  # 运行时用 object：协议里的声明不能进 MRO，否则会挡掉别的 mixin 的真实实现
    _MixinBase = object


class ProactiveMixin(_MixinBase):
    def _migrate_proactive_timing(self):
        try:
            if self.db.kv_get("proactive_timing_migrated", False):
                return
            changes = {}
            if to_int(self.cfg("proactive_followup_minutes", 0), 0) == 30:
                changes["proactive_followup_minutes"] = 5
            if to_int(self.cfg("proactive_no_reply_minutes", 0), 0) == 120:
                changes["proactive_no_reply_minutes"] = 30
            for key, value in changes.items():
                self.config[key] = value
            if changes and hasattr(self.config, "save_config"):
                self.config.save_config()
            self.db.kv_set("proactive_timing_migrated", True)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._migrate_proactive_timing", _amo_exc)

    def _is_proactive_friend(self, profile):
        return str(profile.get("relation") or "普通") in ("熟识", "相恋")

    def _set_proactive_pending(self, uid, kind, text, umo=""):
        self.db.kv_set(self._pending_key(uid), {
            "sent_at": now_ts(), "kind": kind, "text": str(text or ""),
            # 记下真正发出去的会话：追问要回同一个平台，否则会「飞书问完、QQ 追」。
            "umo": str(umo or ""),
            "followup": False, "mood": False,
        })

    def _clear_proactive_pending(self, uid):
        pending = self.db.kv_get(self._pending_key(uid), None)
        if pending:
            self.db.kv_set(self._pending_key(uid), {})
            self.db.kv_set("proactive_ignored_" + str(uid), 0)

    def _try_soothe(self, uid, text):
        if not self._depressed_active(uid):
            return
        words = ("抱抱", "亲亲", "哄", "对不起", "别难过", "喜欢你", "爱你", "怎么啦", "我在", "在呢", "乖")
        if any(word in str(text or "") for word in words):
            self.db.kv_set("proactive_depressed_until_" + str(uid), 0)
            self.db.kv_set("proactive_ignored_" + str(uid), 0)
            self.state.nudge(委屈=-8, 郁闷=-8, 开心=5, 依恋=3)
            logger.debug("[amo] 抑郁状态被哄好: %s" % uid)

    @staticmethod
    def _followup_topics(text):
        """追问别再问同一件事：按话题打标，避开上一句已经问过的内容。"""
        text = str(text or "")
        groups = (
            ("food", ("吃", "饭", "饿", "外卖", "餐")),
            ("comfort", ("疼", "难受", "舒服", "药", "撑着")),
            ("work", ("下班", "加班", "上班", "项目")),
        )
        return {name for name, words in groups if any(word in text for word in words)}

    def _followup_text(self, uid, kind, previous_text=""):
        """追问换着说法：同一句不连着出现，也别把刚问的问题再问一遍。"""
        templates = [t for t in (self.FOLLOWUP_TEMPLATES.get(str(kind)) or self.FOLLOWUP_TEMPLATES["greet"]) if t]
        if not templates:
            return "？怎么不理我了？"
        key = "proactive_followup_last_%s" % uid
        try:
            last = str(self.db.kv_get(key, "") or "")
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._followup_text", _amo_exc)
            last = ""
        pool = [t for t in templates if t != last]
        topics = self._followup_topics(previous_text)
        if topics:
            fresh = [t for t in pool if not (self._followup_topics(t) & topics)]
            pool = fresh
        if not pool:
            # 同一类话题的问句都问过了，就换成「怎么不理我」的催回，别再问一遍。
            pool = [t for t in self.FOLLOWUP_TEMPLATES["greet"] if t != last] or list(self.FOLLOWUP_TEMPLATES["greet"])
        text = random.choice(pool)
        try:
            self.db.kv_set(key, text)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._followup_text", _amo_exc)
        return text

    async def _check_proactive_pending(self):
        try:
            profiles = self.db.proactive_profiles(limit=100)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._check_proactive_pending", _amo_exc)
            return
        now = now_ts()
        for profile in profiles:
            uid = str(profile.get("uid") or "")
            if not uid:
                continue
            pending = self.db.kv_get(self._pending_key(uid), None)
            if not isinstance(pending, dict) or not pending.get("sent_at"):
                continue
            # 追问要回真正发出这条主动消息的平台（飞书发的就在飞书追）；
            # 老数据没有 umo 时退回主页会话。
            umo = str(pending.get("umo") or "") or str(profile.get("private_umo") or "")
            if not umo:
                continue
            age = now - to_float(pending.get("sent_at"), 0)
            if age > 6 * 3600:
                self.db.kv_set(self._pending_key(uid), {})
                continue
            followup_minutes = self.cfg_int("proactive_followup_minutes", 30)
            # 0 / 负数 = 关掉追问，只保留「心情变差」这一档
            followup_after = 10 ** 9 if followup_minutes <= 0 else max(5, followup_minutes) * 60
            # 0 / 负数 = 关掉「没回复就心情变差」；默认跟 schema 一致（30 分钟）
            mood_minutes = max(0, self.cfg_int("proactive_no_reply_minutes", 30))
            mood_after = mood_minutes * 60 if mood_minutes else 10 ** 9
            if not pending.get("followup") and age >= followup_after:
                if not quiet_now(self.cfg_str("quiet_hours", "23:30-07:30")):
                    kind = str(pending.get("kind") or "greet")
                    followup = self._followup_text(uid, kind, pending.get("text"))
                    if await self._send_to_umo(umo, followup):
                        pending["followup"] = True
                        pending["followup_at"] = now
                        self.db.kv_set(self._pending_key(uid), pending)
                        # 追问本身也是一句她说过的话，不记下来会引出下一轮「你怎么不理我」
                        self._remember_bot_message(umo, followup)
                        self._mark_bot_initiated(umo)
                        logger.debug("[amo] 主动私聊未回复，已追问: %s" % uid)
            if not pending.get("mood") and age >= mood_after:
                pending["mood"] = True
                self.db.kv_set(self._pending_key(uid), pending)
                ignored = to_int(self.db.kv_get("proactive_ignored_" + uid, 0), 0) + 1
                self.db.kv_set("proactive_ignored_" + uid, ignored)
                self.state.nudge(委屈=5, 郁闷=5, 生气=2, 依恋=2)
                if ignored >= max(1, self.cfg_int("proactive_depressed_count", 3)):
                    self._set_depressed(uid)
                logger.debug("[amo] 主动私聊仍无回复，心情变差: %s (%d)" % (uid, ignored))

    def _ritual_in_window(self, spec):
        try:
            start, end = str(spec).split("-", 1)
            return in_minute_range(minutes_now(), parse_hhmm(start), parse_hhmm(end))
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._ritual_in_window", _amo_exc)
            return False

    def _ritual_umos(self, profile):
        """她能给你发问候的会话：QQ、飞书…按最近说过话排序。"""
        uid = str(profile.get("uid") or "")
        items = []
        try:
            data = self.db.kv_get("channels:%s" % uid, {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._ritual_umos", _amo_exc)
            data = {}
        if isinstance(data, dict):
            entries = [value for value in data.values() if isinstance(value, dict) and value.get("umo")]
            entries.sort(key=lambda value: -to_float(value.get("ts"), 0))
            for value in entries:
                umo = str(value.get("umo") or "")
                if umo and umo not in items:
                    items.append(umo)
        private_umo = str(profile.get("private_umo") or "")
        if private_umo and private_umo not in items:
            items.append(private_umo)
        return items

    def _recent_greeting(self, uid, minutes):
        """最近一次每日问候发在哪个平台、隔了多久（QQ/飞书算同一个人）。"""
        if not uid:
            return False
        try:
            data = self.db.kv_get("greeting_last:%s" % uid, {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._recent_greeting", _amo_exc)
            data = {}
        if not isinstance(data, dict):
            return False
        return _within_minutes(data.get("ts"), now_ts(), minutes)

    async def _user_spoke_today(self, uid, kind):
        """他今天已经主动说过话了吗（决定要不要发「醒了没 / 吃了吗 / 早点睡」）。

        早上问「醒了没」最容易露馅：他 08:04 说「我九点上班」，09:29 再问就成失忆了。
        所以早上只要他今天开过口，就当他已经醒了，不再发问候（正常回复里自然会带早安）。

        数据从记忆终端（想记）读：本地 `messages` 表只在开了 `local_chat_cache` 时才写，
        默认是空的，拿它判断会永远「今天没说话」。
        """
        uid = str(uid or "")
        if not uid:
            return False
        try:
            start = _now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        except Exception:
            start = now_ts() - 86400
        rows = []
        try:
            rows = await self._data_call(
                "messages_since", self._data_bot_id(), uid, int(start),
            ) or []
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._user_spoke_today.data", _amo_exc)
            rows = []
        if not rows:
            try:
                rows = self.db.messages_by_uid(uid, limit=60)
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive.ProactiveMixin._user_spoke_today.local", _amo_exc)
                rows = []
        for row in rows or []:
            if str((row or {}).get("role") or "") != "user":
                continue
            try:
                ts = float((row or {}).get("created_at") or 0)
            except (TypeError, ValueError):
                continue
            if ts < start:
                continue
            text = str((row or {}).get("content") or "")
            if kind == "morning":
                return True
            if kind == "lunch" and any(word in text for word in
                                       ("吃", "饭", "饿", "外卖", "食堂", "早餐", "午饭", "汤", "面", "粉",
                 "粥", "菜", "火锅", "汉堡", "披萨", "便当", "寿司", "烧烤", "米线",
                 "饺子", "包子", "零食", "喝了")):
                return True
            if kind == "goodnight" and any(word in text for word in ("睡", "晚安", "困", "熬夜")):
                return True
        return False

    def _mark_greeting(self, uid, kind, umo):
        try:
            self.db.kv_set("greeting_last:%s" % str(uid), {
                "kind": str(kind),
                "umo": str(umo or ""),
                "ts": now_ts(),
            })
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._mark_greeting", _amo_exc)

    def _ritual_pending(self, profile):
        """现在是否正处在「还没发」的问候时段。

        主动消息要让路：早晨 8 点主动搭话和早安问候撞在一起，就是
        「两个地方各响一次」的来源。非恋人的好友、刚问候过的、没有会话可发的，
        都不算待发，否则会白让路一整个时段。
        """
        if not self.cfg_bool("ritual_enabled", True):
            return False
        if str(profile.get("relation") or "") != "相恋":
            return False
        uid = str(profile.get("uid") or "")
        umos = self._ritual_umos(profile)
        if not uid or not umos:
            return False
        if self._recent_greeting(uid, self.cfg_int("ritual_min_gap_minutes", 30)):
            return False
        for kind, enabled_key, window_key, default_window in self.RITUAL_SPECS:
            if not self.cfg_bool(enabled_key, True):
                continue
            if not self._ritual_in_window(self.cfg_str(window_key, default_window)):
                continue
            if self.db.kv_get("ritual_" + kind + "_" + uid + "_" + today_str(), False):
                continue
            if not self._ritual_should_send(umos, kind):
                continue
            return True
        return False

    def _ritual_should_send(self, umos, kind):
        """今天这个问候还该不该发；QQ/飞书的近聊一起算，避免两边各问候一遍。"""
        umos = list(umos) if isinstance(umos, (list, tuple, set)) else [umos]
        rows = []
        for item in umos:
            try:
                rows.extend(self._recent(item, 10))
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive.ProactiveMixin._ritual_should_send", _amo_exc)
        rows.sort(key=lambda row: to_float(row.get("created_at"), 0))
        now = now_ts()
        recent_gap = max(5, self.cfg_int("ritual_skip_recent_minutes", 45)) * 60
        if rows:
            last_at = to_float(rows[-1].get("created_at"), 0)
            if last_at and now - last_at < recent_gap:
                return False
        if not self.cfg_bool("ritual_skip_if_greeted", True):
            return True
        # 只认「他」问候过：她自己那句「吃了没 / 晚安」多半是主动消息或追问，
        # 算进来会把当天的问候整段吃掉（午饭问候时有时无就是这么来的）。
        texts = [str(row.get("content") or "") for row in rows if str(row.get("role") or "") == "user"]
        joined = "\n".join(texts[-10:])
        if kind == "morning":
            return not bool(re.search(r"(?:^|\s)早(?:\s|$|[!！。~～])|早安|早上好|早上好呀|我醒了|起床了|还没起来吗|醒了没", joined))
        if kind == "goodnight":
            return not bool(re.search(r"晚安|睡了|睡觉|先睡|去睡|早点休息", joined))
        if kind == "lunch":
            return not bool(re.search(r"午饭|中午吃|吃啥|吃什么|吃饭|吃了没|吃了吗", joined))
        return True

    async def _maybe_rituals(self):
        if not self.cfg_bool("ritual_enabled", True):
            return
        now = now_ts()
        if now - to_float(getattr(self, "_last_ritual", 0), 0) < 60:
            return
        self._last_ritual = now
        try:
            profiles = self.db.proactive_profiles(limit=100)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._maybe_rituals", _amo_exc)
            return
        snap = self.state.snapshot()
        for profile in profiles:
            if str(profile.get("relation") or "") != "相恋":
                continue
            uid = str(profile.get("uid") or "")
            umos = self._ritual_umos(profile)
            if not umos or not uid:
                continue
            home_umo = str(profile.get("private_umo") or "")
            name = str(profile.get("alias") or profile.get("name") or "宝").strip() or "宝"
            try:
                birthday = str(profile.get("birthday") or "").strip()[-5:]
                today_md = _now().strftime("%m-%d")
            except Exception:
                birthday, today_md = "", ""
            if birthday and birthday == today_md and self.cfg_bool("birthday_greet_enabled", True):
                year_key = "birthday_greet:" + uid + ":" + str(_now().year)
                if not self.db.kv_get(year_key, False):
                    targets = _greeting_targets(
                        umos, home_umo,
                        randomize=self.cfg_bool("ritual_random_platform", True),
                    )
                    text = ""
                    if targets:
                        try:
                            text = await self.llm.gen_proactive(targets[0], profile, snap, "birthday")
                        except Exception as exc:
                            logger.debug("[amo] 生日祝福生成失败: %s" % exc)
                    if not text:
                        text = name + "，生日快乐！"
                    sent_umo = ""
                    for candidate in targets:
                        if await self._send_to_umo(candidate, text):
                            sent_umo = candidate
                            break
                    if sent_umo:
                        self._mark_bot_initiated(sent_umo)
                        self.db.kv_set(year_key, True)
                        self.db.log_proactive(uid, "birthday", text)
                        logger.info("[amo] 生日祝福 -> %s: %s" % (name, text[:120]))
                        continue
            for kind, enabled_key, window_key, default_window in self.RITUAL_SPECS:
                if not self.cfg_bool(enabled_key, True):
                    continue
                if not self._ritual_in_window(self.cfg_str(window_key, default_window)):
                    continue
                # 晚安跨零点：00:00-02:00 仍算前一晚，否则 22:00 发过、0 点后 key 变了再发一次
                ritual_day = today_str()
                if kind == "goodnight" and minutes_now() < 120:
                    ritual_day = (_now() - timedelta(days=1)).date().isoformat()
                daily_key = "ritual_" + kind + "_" + uid + "_" + ritual_day
                if self.db.kv_get(daily_key, False):
                    continue
                if kind == "morning" and (snap.get("activity") or {}).get("category") == "sleep":
                    continue
                if self._recent_greeting(uid, self.cfg_int("ritual_min_gap_minutes", 30)):
                    continue
                # 他今天已经主动说过话，就别再问「醒了没 / 吃了吗 / 早点睡」——
                # 2026-11-12 线上：08:04 他说「我九点上班」，09:29 还在发「老公早安，醒了没呀」
                if await self._user_spoke_today(uid, kind):
                    continue
                if not self._ritual_should_send(umos, kind):
                    continue
                targets = _greeting_targets(
                    umos,
                    home_umo,
                    randomize=self.cfg_bool("ritual_random_platform", True),
                )
                if not targets:
                    continue
                text = ""
                try:
                    text = await self.llm.gen_proactive(targets[0], profile, snap, kind)
                except Exception as exc:
                    logger.debug("[amo] 仪式生成失败: %s" % exc)
                if not text:
                    # 兜底文案也要跟着方言开关：关掉方言/普通话时不能冒出「吃啥子哦」
                    lunch_text = name + "，今天中午吃什么呢？"
                    try:
                        if self._dialect_active() and "贵州" in self._dialect_style():
                            lunch_text = name + "，今天中午吃啥子哦？"
                    except Exception as _amo_exc:
                        _note_swallowed("amo_proactive.ProactiveMixin._maybe_rituals", _amo_exc)
                    text = {
                        "goodnight": name + "，太晚了，我先去睡觉啦，你也早点睡。",
                        "morning": name + "，早上好呀，还没起来吗？",
                        "lunch": lunch_text,
                    }.get(kind, "")
                if not text:
                    continue
                sent_umo = ""
                for candidate in targets:
                    if await self._send_to_umo(candidate, text):
                        sent_umo = candidate
                        break
                if not sent_umo:
                    continue
                # 只标记真正收到的那一处；另一头靠 greeting_last 跨平台去重，
                # 不能把「她刚跟我说过话」的副作用（火气/打扰冷却）打到没收到消息的平台
                self._mark_bot_initiated(sent_umo)
                self._mark_greeting(uid, kind, sent_umo)
                self.db.kv_set(daily_key, True)
                self.db.log_proactive(uid, kind, text)
                logger.info(
                    "[amo] 每日问候(%s) -> %s [%s]: %s"
                    % (kind, name, str(sent_umo).split(":", 1)[0], text[:200])
                )
                break

    def _choose_proactive_kind(self, profile, snap, category=""):
        relation = profile.get("relation", "普通")
        mood = snap["mood"]
        painful = (
            snap["health"]["sick"]
            or snap["period"]["pain"] >= 6
            or mood["委屈"] >= 60
            or mood["郁闷"] >= 65
        )
        health = snap.get("health") or {}
        if (relation == "相恋" and health.get("sick")
                and self.cfg_bool("proactive_comfort", True)
                and not self.db.kv_get("unwell_announced:" + today_str(), False)):
            return "unwell"
        if relation == "相恋" and painful and self.cfg_bool("proactive_comfort", True):
            return "comfort"
        activity = str((snap.get("activity") or {}).get("activity") or "")
        if category == "play" and ("游戏" in activity or "打游戏" in activity):
            return "game"
        if category == "meal":
            return "meal"
        if category in ("rest", "play"):
            return "slack"
        if category == "commute":
            return "offwork" if _now().hour >= 16 else "commute"
        if relation in ("熟识", "相恋") and random.random() < self.cfg_float("proactive_share_prob", 0.35):
            return "share"
        return "greet"

    def _proactive_user_just_talked(self, profile, now):
        """对方刚说过话就别主动插话。0 表示关闭这项检查。"""
        minutes = max(0, self.cfg_int("proactive_user_idle_minutes", 10))
        if not minutes:
            return False
        last_user_at = to_float(profile.get("last_seen"), 0)
        return bool(last_user_at) and (now - last_user_at) < minutes * 60

    def _proactive_forced_allowed(self, profile, now):
        """「日常关心」也要跟上次主动拉开间隔，避免重启后立刻又发。"""
        gap = max(1, self.cfg_int("proactive_forced_gap_minutes", 30)) * 60
        return (now - to_float(profile.get("last_proactive", 0))) >= gap

    def _proactive_candidates(self, profile, primary):
        """主动消息的候选平台：选中的优先，其次是其它通道，最后 private_umo。

        最近刚被主动联系过的平台会被跳过，避免兜底时连着骚扰同一处。
        """
        items = []

        def push(umo, force=False):
            umo = str(umo or "")
            if not umo or umo in items:
                return
            if not force and self._recent_bot_initiated(umo, 3600):
                return
            items.append(umo)

        push(primary, force=True)
        if self.cfg_bool("proactive_rotate_enabled", True):
            try:
                data = self.db.kv_get("channels:%s" % (profile.get("uid") or ""), {}) or {}
            except Exception as _amo_exc:
                _note_swallowed("amo_proactive.ProactiveMixin._proactive_candidates", _amo_exc)
                data = {}
            if isinstance(data, dict):
                entries = [v for v in data.values() if isinstance(v, dict) and v.get("umo")]
                entries.sort(key=lambda v: -float(v.get("ts") or 0))
                for value in entries:
                    push(value.get("umo"))
        push(profile.get("private_umo"))
        return items

    def _pick_proactive_umo(self, profile):
        """选这轮主动消息先发哪个平台；关掉开关就用 private_umo。"""
        fallback = str(profile.get("private_umo") or "")
        if not self.cfg_bool("proactive_rotate_enabled", True):
            return fallback
        uid = str(profile.get("uid") or "")
        try:
            data = self.db.kv_get("channels:%s" % uid, {}) or {}
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._pick_proactive_umo", _amo_exc)
            return fallback
        if not isinstance(data, dict):
            return fallback
        items = []
        for value in data.values():
            if isinstance(value, dict) and value.get("umo"):
                items.append((float(value.get("ts") or 0), str(value.get("umo"))))
        if not items:
            return fallback
        items.sort(key=lambda pair: -pair[0])
        if len(items) == 1:
            return items[0][1]
        window = max(1, self.cfg_int("proactive_recent_minutes", 30)) * 60
        if now_ts() - items[0][0] <= window:
            return items[0][1]
        last = str(self.db.kv_get("last_channel:%s" % uid, "") or "")
        for _, umo in items:
            if umo != last:
                return umo
        return items[0][1]

    async def _maybe_proactive(self):
        quiet = quiet_now(self.cfg_str("quiet_hours", "23:30-07:30"))
        try:
            profiles = self.db.proactive_profiles(limit=100)
        except Exception as _amo_exc:
            _note_swallowed("amo_proactive.ProactiveMixin._maybe_proactive", _amo_exc)
            return
        if self.cfg_bool("proactive_require_friend", True):
            profiles = [profile for profile in profiles if self._is_proactive_friend(profile)]
        if not profiles:
            return
        random.shuffle(profiles)
        now = now_ts()
        interval = max(10, self.cfg_int("proactive_interval_min", 90)) * 60
        daily_max = max(0, self.cfg_int("proactive_daily_max", 3))
        for profile in profiles:
            uid = str(profile.get("uid") or "")
            if self._ritual_pending(profile):
                continue
            if self._recent_greeting(uid, self.cfg_int("proactive_after_greeting_minutes", 60)):
                continue
            target_umo = self._pick_proactive_umo(profile)
            if not target_umo:
                continue
            if self._recent_bot_initiated(target_umo, 3600):
                continue
            if self._proactive_user_just_talked(profile, now):
                continue
            count = to_int(profile.get("proactive_count"))
            if profile.get("proactive_day") != today_str():
                count = 0
            if count >= daily_max:
                continue
            relation = str(profile.get("relation") or "普通")
            guaranteed = (
                relation == "相恋"
                and count == 0
                and self.cfg_bool("proactive_daily_guarantee", True)
                and _now().hour >= 8
                and _now().hour < 23
                and not quiet
            )
            if quiet and not guaranteed:
                continue
            pending = self.db.kv_get(self._pending_key(profile.get("uid")), None)
            if isinstance(pending, dict) and pending.get("sent_at"):
                if now - to_float(pending.get("sent_at"), 0) < 6 * 3600:
                    continue
                self.db.kv_set(self._pending_key(profile.get("uid")), {})
            snap = self.state.snapshot()
            category = (snap.get("activity") or {}).get("category", "")
            if category in ("sleep", "work", "study") and not guaranteed:
                continue
            forced = (
                relation == "相恋"
                and category in ("meal", "rest", "play", "commute")
                and self._proactive_forced_allowed(profile, now)
                and not self._forced_sent_this_hour(profile.get("uid"))
            )
            if not guaranteed and not forced and now - to_float(profile.get("last_proactive", 0)) < interval:
                continue
            if not guaranteed and not forced and random.random() > self.cfg_float("proactive_prob", 0.25):
                continue
            if not guaranteed and self.cfg_bool("proactive_schedule_only", True) and category not in ("meal", "rest", "play", "commute"):
                continue
            kind = self._choose_proactive_kind(profile, snap, category)
            # 他今天已经说过吃了什么（哪怕是在另一个平台），就别再发「到饭点了，别随便对付」。
            if kind == "meal" and await self._user_spoke_today(uid, "lunch"):
                continue
            text = await self.llm.gen_proactive(
                target_umo, profile, snap, kind
            )
            if not text:
                continue
            sent_umo = ""
            for candidate in self._proactive_candidates(profile, target_umo):
                if await self._send_to_umo(candidate, text):
                    sent_umo = candidate
                    break
            ok = bool(sent_umo)
            if ok:
                target_umo = sent_umo
                if kind == "unwell":
                    self.db.kv_set("unwell_announced:" + today_str(), True)
                self._mark_bot_initiated(target_umo)
                self.db.kv_set("last_channel:%s" % profile.get("uid"), target_umo)
                self.db.bump_proactive(profile["uid"])
                self.db.log_proactive(profile["uid"], kind, text)
                self._set_proactive_pending(profile["uid"], kind, text, umo=target_umo)
                self._mark_forced_hour(profile.get("uid"))

                logger.debug("[amo] 主动私聊 -> %s (%s)" % (profile.get("name", ""), kind))
