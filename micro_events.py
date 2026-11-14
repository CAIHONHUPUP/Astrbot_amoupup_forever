# -*- coding: utf-8 -*-
"""微型日常事件（2026-11-14）：让她的世界每 1-2 小时有点小事发生。

设计约束：
- 时间法则：凌晨只有失眠/噩梦/夜宵，早上只有通勤/早饭，下午才有下午茶/摸鱼；
- 场景法则：群聊只发公共事件，私聊才发私人琐事；
- 财务闭环：有消费的事件先真扣钱，钱不够就不抽它；
- 情绪闭环：职场负向、生活正向；当天第一次负向事件会把情绪基线压低，后续正向减半；
- 记忆闭环：事件写短期记忆，聊天 3 小时后还能接上；
- 防呆：24h 同事件不重复；40% 概率“无事发生”；连续 3 次没事 → 好无聊；
- 社交保底：连续 3 天没有社交事件 → 强制父母/闺蜜互动；
- 分享欲 >50 → 主动发消息（走 amo 的主动发送通道）。
"""
from __future__ import annotations

import datetime as _dt
import random

try:
    from .amo_shared import logger
    from .clock import now as _now
except ImportError:  # 直接按文件加载时
    import logging

    logger = logging.getLogger(__name__)
    from clock import now as _now

BANDS = ("dawn", "morning", "forenoon", "noon", "afternoon", "evening", "night")

POOL = (
    # 凌晨（0-6 点）：只能失眠/噩梦/饿
    {"key": "insomnia", "band": ("dawn",), "scene": "private", "cost": 0,
     "text": "凌晨还睡不着，翻来覆去刷手机", "mood": {"疲惫": 6, "焦虑": 4}},
    {"key": "nightmare", "band": ("dawn",), "scene": "private", "cost": 0,
     "text": "做了个噩梦，醒来有点心慌", "mood": {"害怕": 8, "委屈": 4}},
    {"key": "late_snack", "band": ("dawn", "night"), "scene": "private", "cost": 1500,
     "text": "肚子饿了，点了份夜宵", "mood": {"开心": 5, "满足": 4}},
    # 早上（6-9 点）
    {"key": "subway", "band": ("morning",), "scene": "both", "cost": 500,
     "text": "早上挤地铁，差点被门夹到", "mood": {"疲惫": 3, "郁闷": 3}},
    {"key": "breakfast", "band": ("morning",), "scene": "both", "cost": 800,
     "text": "买了豆浆和包子当早饭", "mood": {"开心": 3}},
    # 上午（9-12 点）
    {"key": "weird_client", "band": ("forenoon",), "scene": "private", "cost": 0,
     "text": "遇到一个特别难缠的客户，气得半死", "mood": {"生气": 8, "疲惫": 4}},
    {"key": "boss_scold", "band": ("forenoon", "afternoon"), "scene": "private", "cost": 0,
     "text": "被老板当众说了一顿，情绪很低落", "mood": {"委屈": 10, "郁闷": 8, "开心": -10}},
    {"key": "desk_slack", "band": ("forenoon", "afternoon"), "scene": "both", "cost": 0,
     "text": "在工位上摸鱼刷了会儿手机", "mood": {"开心": 2}},
    # 中午（12-14 点）
    {"key": "tea_time", "band": ("noon", "afternoon"), "scene": "both", "cost": 1500,
     "text": "公司下午茶发了杯奶茶", "mood": {"开心": 6, "满足": 4}},
    {"key": "takeout", "band": ("noon", "evening"), "scene": "private", "cost": 2500,
     "text": "今天不想做饭，点了个外卖", "mood": {"开心": 3, "懒": 3}},
    # 下午（14-18 点）
    {"key": "fat_cat", "band": ("afternoon", "evening"), "scene": "both", "cost": 0,
     "text": "下班路上看到一只很胖的橘猫", "mood": {"开心": 8, "兴奋": 5}},
    {"key": "fruit_tea", "band": ("afternoon",), "scene": "both", "cost": 1500,
     "text": "买了一杯 15 块钱的果茶", "mood": {"开心": 4}},
    {"key": "egg_price", "band": ("afternoon", "evening"), "scene": "private", "cost": 0,
     "text": "去超市发现鸡蛋又涨价了", "mood": {"郁闷": 5, "委屈": 3}},
    # 傍晚/晚上（18-24 点）
    {"key": "sunset", "band": ("evening",), "scene": "both", "cost": 0,
     "text": "下班路上看到很好看的晚霞", "mood": {"开心": 7, "温柔": 3}},
    {"key": "cook_fail", "band": ("evening",), "scene": "private", "cost": 0,
     "text": "想自己做饭，结果把菜炒糊了", "mood": {"郁闷": 4, "委屈": 2}},
    {"key": "drama", "band": ("evening", "night"), "scene": "both", "cost": 0,
     "text": "追的剧更新了，看到关键处卡住", "mood": {"开心": 5, "焦虑": 2}},
    # 社交（NPC 微弱互动，私聊）
    {"key": "bff_vent", "band": ("afternoon", "evening"), "scene": "private", "cost": 0,
     "text": "闺蜜发微信跟我吐槽她男朋友", "mood": {"开心": 3, "无奈": 2}, "social": True},
    {"key": "mom_call", "band": ("evening",), "scene": "private", "cost": 0,
     "text": "妈妈打电话来问周末回不回家吃饭", "mood": {"温暖": 5, "依恋": 3}, "social": True},
    {"key": "colleague_lunch", "band": ("noon",), "scene": "private", "cost": 0,
     "text": "同事拉着我一起去吃午饭", "mood": {"开心": 4}, "social": True},
    # 日常/上下文连贯
    {"key": "mall_shopping", "band": ("afternoon", "evening"), "scene": "both", "cost": 0,
     "text": "在商场逛了一会儿，试了两件衣服", "mood": {"开心": 5, "兴奋": 3}},
    {"key": "rest_after_shopping", "band": ("afternoon", "evening", "night"), "scene": "private",
     "cost": 0, "after": ("mall_shopping",), "text": "逛累了，坐在商场长椅上歇脚，脚有点酸",
     "mood": {"疲惫": 5, "满足": 4}},
    {"key": "funny_video", "band": ("evening", "night"), "scene": "both", "cost": 0,
     "text": "刷到一个很搞笑的视频，笑出声", "mood": {"开心": 6}},
    {"key": "rain_umbrella", "band": ("afternoon", "evening"), "scene": "both", "cost": 0,
     "text": "突然下雨，躲在便利店门口等雨停", "mood": {"郁闷": 4}},
    {"key": "broke_water", "band": ("dawn", "morning", "forenoon", "noon", "afternoon", "evening", "night"),
     "scene": "private", "cost": 0, "requires_broke": True,
     "text": "口渴了，但连一瓶矿泉水都舍不得买", "mood": {"委屈": 5}},
    {"key": "shower", "band": ("evening", "night"), "scene": "private", "cost": 0,
     "text": "洗了个热水澡，浑身放松", "mood": {"放松": 6, "舒服": 4}},
)

PUBLIC_KEYS = {"subway", "breakfast", "desk_slack", "tea_time", "fat_cat",
               "fruit_tea", "sunset", "drama", "nightmare", "mall_shopping",
               "funny_video", "rain_umbrella"}
FOOD_KEYS = {"late_snack", "takeout", "breakfast", "tea_time", "fruit_tea",
             "colleague_lunch", "broke_water"}
FOOD_COOLDOWN_SECONDS = 2 * 3600
QUIET_HOURS = (23, 30, 7, 30)


def band_of(hour):
    hour = int(hour) % 24
    if hour < 6:
        return "dawn"
    if hour < 9:
        return "morning"
    if hour < 12:
        return "forenoon"
    if hour < 14:
        return "noon"
    if hour < 18:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "night"


def is_quiet(hour, minute):
    hour, minute = int(hour), int(minute)
    return (hour, minute) >= QUIET_HOURS[:2] or (hour, minute) < QUIET_HOURS[2:]


def pick_event(now, cash_cents, recent_keys=(), scene="private", rng=None,
               force_social=False, last_key="", food_locked=False):
    """按时间/场景/余额/24h 去重抽一个事件；返回 None 表示这次无事发生。"""
    rng = rng or random
    band = band_of(now.hour)
    recent = set(recent_keys or ())
    candidates = []
    for event in POOL:
        if band not in event.get("band", ()):
            continue
        if scene == "group" and event["key"] not in PUBLIC_KEYS:
            continue
        if int(event.get("cost") or 0) > int(cash_cents or 0):
            continue
        if event["key"] in recent:
            continue
        if food_locked and event["key"] in FOOD_KEYS:
            continue
        if event.get("requires_broke") and int(cash_cents or 0) >= 1500:
            continue
        if force_social and not event.get("social"):
            continue
        candidates.append(event)
    if not candidates:
        return None
    if not force_social and rng.random() < 0.40:
        return None
    if last_key:
        follow = [event for event in candidates if last_key in (event.get("after") or ())]
        if follow:
            candidates = follow
    return rng.choice(candidates)


def merge_mood(bias, delta):
    """当天情绪基线：负向事件后，正向收益减半，负向照常。"""
    out = {}
    for key, value in (delta or {}).items():
        if bias == "low" and value > 0:
            value = max(1, int(value * 0.5))
        out[key] = value
    return out


class MicroEventEngine:
    def __init__(self, plugin):
        self.p = plugin

    # ---------- 状态 ----------
    def _kv_get(self, key, default=None):
        try:
            return self.p.db.kv_get(key, default)
        except Exception:
            return default

    def _kv_set(self, key, value):
        try:
            self.p.db.kv_set(key, value)
        except Exception:
            pass

    def _today(self, now=None):
        return (now or _now()).strftime("%Y-%m-%d")

    def _recent_keys(self):
        rows = self._kv_get("micro_event:recent", {}) or {}
        if not isinstance(rows, dict):
            return []
        cutoff = (_now().timestamp() - 24 * 3600)
        return [key for key, ts in rows.items() if float(ts or 0) >= cutoff]

    def _remember_key(self, key, now):
        rows = self._kv_get("micro_event:recent", {}) or {}
        if not isinstance(rows, dict):
            rows = {}
        rows[key] = now.timestamp()
        cutoff = now.timestamp() - 48 * 3600
        rows = {k: v for k, v in rows.items() if float(v or 0) >= cutoff}
        self._kv_set("micro_event:recent", rows)

    def _empty_ticks(self):
        try:
            return int(self._kv_get("micro_event:empty_ticks", 0) or 0)
        except Exception:
            return 0

    def _set_empty_ticks(self, value):
        self._kv_set("micro_event:empty_ticks", int(value))

    def _bias(self):
        return str(self._kv_get("micro_event:bias:" + self._today(), "") or "")

    def _set_bias(self, bias):
        self._kv_set("micro_event:bias:" + self._today(), str(bias))

    def _social_last_day(self):
        return str(self._kv_get("micro_event:social_day", "") or "")

    def share_drive(self, snap=None):
        """分享欲：心情好/兴奋/想念越高越想说。"""
        mood = {}
        try:
            mood = (snap or self.p.state.snapshot()).get("mood") or {}
        except Exception:
            mood = {}
        score = 45
        for key, weight in (("开心", 0.6), ("兴奋", 0.5), ("依恋", 0.4), ("想念", 0.4)):
            try:
                score += int(float(mood.get(key) or 0) * weight)
            except Exception:
                pass
        try:
            score += int(self._kv_get("micro_event:share_boost", 0) or 0)
        except Exception:
            pass
        return max(0, min(100, score))

    # ---------- 应用事件 ----------
    async def _pay(self, event, now):
        cost = int(event.get("cost") or 0)
        if cost <= 0:
            return True
        try:
            if int(self.p.wallet.balance()) < cost:
                return False
            self.p.wallet.debit(cost, str(event.get("text") or "生活小事"),
                                "micro:%s:%s" % (self._today(now), event["key"]))
            return True
        except Exception:
            return False

    def _mood(self, event, now):
        bias = self._bias()
        delta = merge_mood(bias, event.get("mood") or {})
        try:
            self.p.state.nudge_once("micro:%s:%s" % (self._today(now), event["key"]), **delta)
        except Exception:
            try:
                self.p.state.nudge(**delta)
            except Exception:
                pass
        if not bias:
            if any(value < 0 for value in (event.get("mood") or {}).values()):
                self._set_bias("low")
            elif any(value > 0 for value in (event.get("mood") or {}).values()):
                self._set_bias("high")
        return delta

    async def _write_memory(self, text, umo, uid):
        if not uid:
            return False
        writer = getattr(self.p, "_write_memory_checked", None)
        if writer is None:
            return False
        try:
            return bool(await writer(uid, "[生活小事] " + text, kind="event", tier=0, subject="bot"))
        except Exception:
            return False

    # ---------- 突发事件动作生命周期 ----------
    def _set_action(self, action, until, key, stage="doing", extra=None):
        data = {"action": str(action or ""), "until": float(until or 0),
                "key": str(key or ""), "stage": str(stage or "doing"),
                "ts": _now().timestamp()}
        if isinstance(extra, dict):
            data.update(extra)
        self._kv_set("micro_event:action", data)
        try:
            self.p.set_current_action(str(action or ""), float(until or 0),
                                      place="家", zone="home", category="life")
        except Exception:
            try:
                self.p.db.kv_set("current_action", {"action": str(action or ""),
                                                      "until": float(until or 0),
                                                      "place": "家", "zone": "home",
                                                      "category": "life"})
            except Exception:
                pass

    def _action(self, now=None):
        data = self._kv_get("micro_event:action", {}) or {}
        if not isinstance(data, dict):
            return None
        try:
            if float(data.get("until") or 0) <= (now or _now()).timestamp():
                return None
        except Exception:
            return None
        return data

    def lifecycle_tick(self, now=None):
        """推进外卖/吃夜宵/刚吃完/洗漱缓冲，不靠下一条消息。"""
        now = now or _now()
        data = self._kv_get("micro_event:action", {}) or {}
        if not isinstance(data, dict) or not data.get("key"):
            return
        stage = str(data.get("stage") or "")
        until = float(data.get("until") or 0)
        if now.timestamp() >= until:
            if stage == "waiting":
                food = str(data.get("food") or "夜宵")
                self._set_action("正在吃%s，刚拆开外卖" % food, now.timestamp() + 1200,
                                 data.get("key"), "eating", {"food": food})
                self._feed(food)
            elif stage == "eating":
                self._set_action("刚吃完夜宵，还没收拾", now.timestamp() + 600,
                                 data.get("key"), "done", {"food": data.get("food")})
            elif stage == "buffer":
                self._kv_set("micro_event:action", {})
                try:
                    self.p.set_current_action("", 0)
                except Exception:
                    pass
            else:
                self._kv_set("micro_event:action", {})

    def _feed(self, food):
        try:
            self.p.add_hunger("amo", 45)
        except Exception:
            pass
        try:
            hunger = getattr(self.p, "hunger", None)
            if hunger is not None:
                hunger.mark_meal(food, cooldown_hours=2)
        except Exception:
            pass

    def _mount_action(self, event, now, cash):
        key = str(event.get("key") or "")
        action = ""
        until = 0
        stage = "doing"
        extra = {"food": event.get("food") or ""}
        if key == "late_snack":
            food = str(event.get("food") or "夜宵")
            if event.get("delivery"):
                action = "刚点了%s外卖，正躺在沙发上等，外卖还要 30 分钟" % food
                until = now.timestamp() + 1800
                stage = "waiting"
                extra["deliver_at"] = until
            else:
                action = "正在吃%s" % food
                until = now.timestamp() + 1800
                stage = "eating"
                self._feed(food)
            self._kv_set("micro_event:food_cooldown_until", now.timestamp() + FOOD_COOLDOWN_SECONDS)
            self._kv_set("late_snack_last", {"day": self._today(now), "food": food,
                                             "ts": now.timestamp()})
        elif key == "mall_shopping":
            action, until = "正在商场逛街", now.timestamp() + 5400
        elif key == "drama":
            action, until = "正在追剧", now.timestamp() + 3600
        elif key == "shower":
            action, until = "正在洗澡", now.timestamp() + 1800
        elif key == "cook_fail":
            action, until = "正在收拾厨房", now.timestamp() + 1800
        if action:
            self._set_action(action, until, key, stage, extra)

    def note_user_text(self, text, now=None):
        now = now or _now()
        action = self._action(now)
        if not action:
            return
        body = str(text or "")
        if any(word in body for word in ("别吃", "不要吃", "太晚", "长胖", "伤胃")):
            self._kv_set("micro_event:interference", {"until": now.timestamp() + 600,
                                                      "text": body[:60]})
        if any(word in body for word in ("吃完睡", "早点睡", "去睡", "睡觉", "睡了")):
            if action.get("stage") in ("eating", "done", "waiting"):
                self._set_action("刚洗漱完，躺在床上刷手机", now.timestamp() + 1200,
                                 action.get("key"), "buffer", {"food": action.get("food")})

    def _morning_penalty(self, now):
        data = self._kv_get("late_snack_last", {}) or {}
        if not isinstance(data, dict) or not data.get("day"):
            return ""
        try:
            day = _dt.date.fromisoformat(str(data["day"]))
        except Exception:
            return ""
        if (now.date() - day).days != 1 or int(now.hour) >= 12:
            return ""
        mark = "late_snack_penalty:%s" % now.date().isoformat()
        if self._kv_get(mark, False):
            return ""
        self._kv_set(mark, True)
        food = str(data.get("food") or "夜宵")
        try:
            self.p.state.nudge_once("late_snack:penalty:%s" % now.date().isoformat(),
                                    懊悔=8, 郁闷=4)
        except Exception:
            pass
        return ("【昨晚的代价】昨晚你吃了%s，今天早上有点懊悔，觉得脸圆了一圈/肠胃不太舒服；"
                "可以自然提一句，但别演成大病。" % food)

    async def _share(self, event, umo, uid, profile):
        """分享欲 >50：主动发一条关于这件小事的消息。"""
        text = str(event.get("text") or "")
        if not text or not umo:
            return False
        persona = ""
        try:
            persona = await self.p.llm.persona(umo)
        except Exception:
            persona = ""
        prompt = ("刚刚发生了一件小事：%s。你现在想主动跟老公分享，"
                  "1-2 句，像发微信，带点你的情绪；只输出要说的话。" % text)
        try:
            line = await self.p.llm.chat(umo, persona or "你就是这个角色本人。", prompt, limit=80)
        except Exception:
            line = ""
        line = str(line or "").strip()
        if not line:
            return False
        try:
            sent = await self.p._send_proactive_lines(profile or {}, umo, line)
            if sent:
                self.p._mark_bot_initiated(sent)
                return True
        except Exception:
            return False
        return False

    # ---------- 主流程 ----------
    async def tick(self, umo="", uid="", rng=None, now=None):
        now = now or _now()
        if not self.p.cfg_bool("micro_event_enabled", True):
            return {"ok": False, "reason": "disabled"}
        self.lifecycle_tick(now)
        action = self._action(now)
        if action:
            return {"ok": False, "reason": "busy", "action": action}
        scene = "group" if ":GroupMessage:" in str(umo or "") else "private"
        try:
            cash = int(self.p.wallet.balance())
        except Exception:
            cash = 0
        today = self._today(now)
        social_last = self._social_last_day()
        if not social_last:
            # 第一次跑：从今天开始算，连续 3 天没社交才强制保底
            self._kv_set("micro_event:social_day", today)
            social_last = today
        force_social = False
        try:
            if (_dt.date.fromisoformat(today) - _dt.date.fromisoformat(social_last)).days >= 3:
                force_social = True
        except Exception:
            force_social = False
        last_record = self._kv_get("micro_event:last", {}) or {}
        last_key = str(last_record.get("key") or "") if isinstance(last_record, dict) else ""
        food_locked = now.timestamp() < float(self._kv_get("micro_event:food_cooldown_until", 0) or 0)
        event = pick_event(now, cash, self._recent_keys(), scene=scene, rng=rng,
                           force_social=force_social, last_key=last_key,
                           food_locked=food_locked)
        if event is not None and event.get("key") == "late_snack":
            event = dict(event)
            if cash > 100000:
                event.update(cost=5000, text="点了份烧烤当夜宵", food="烧烤", delivery=True)
            elif cash < 10000:
                event.update(cost=600, text="在便利店买了桶泡面当夜宵", food="泡面",
                             mood={"委屈": 5, "满足": 3})
            else:
                event.update(cost=1500, food="夜宵", delivery=True)
        if event is None:
            empty = self._empty_ticks() + 1
            self._set_empty_ticks(empty)
            if empty >= 3 and scene == "private":
                event = {"key": "bored:%s" % today, "text": "一整天都没什么新鲜事，有点无聊",
                         "mood": {"郁闷": 3}, "cost": 0, "scene": "private"}
                self._set_empty_ticks(0)
            else:
                return {"ok": False, "reason": "nothing", "empty": empty}
        if not await self._pay(event, now):
            return {"ok": False, "reason": "insufficient"}
        delta = self._mood(event, now)
        self._remember_key(event["key"], now)
        if event.get("social"):
            self._kv_set("micro_event:social_day", today)
        await self._write_memory(str(event.get("text") or ""), umo, uid)
        self._mount_action(event, now, cash)
        record = {"text": str(event.get("text") or ""), "ts": now.timestamp(),
                  "day": today, "key": event["key"], "mood": delta,
                  "cost": int(event.get("cost") or 0), "scene": scene}
        if last_record:
            self._kv_set("micro_event:prev", last_record)
        self._kv_set("micro_event:last", record)
        logger.info("[amo] 微型事件：%s（cost=%d, bias=%s）" % (
            record["text"], record["cost"], self._bias() or "-"))
        drive = min(100, self.share_drive() + (5 if any(value > 0 for value in delta.values()) else 0))
        self._kv_set("micro_event:share_drive", drive)
        shared = False
        if drive > 50 and scene == "private" and not is_quiet(now.hour, now.minute):
            profile = {}
            try:
                profile = self.p.db.get_profile(uid) if uid else {}
            except Exception:
                profile = {}
            shared = await self._share(event, umo, uid, profile)
        return {"ok": True, "event": record, "share_drive": drive, "shared": shared}

    def injection(self, now=None):
        """聊天时注入当前动作（最高优先级）+ 最近小事 + 当天情绪基线。"""
        now = now or _now()
        lines = []
        action = self._action(now)
        if action:
            stage = str(action.get("stage") or "")
            text = str(action.get("action") or "")
            if stage == "waiting":
                line = ("【当前动作·最高优先级】你刚点完外卖：%s。外卖还没到，你正躺在沙发上等；"
                        "严禁跳过这个动作直接说吃完了/去睡觉。" % text)
            elif stage == "eating":
                line = ("【当前动作·最高优先级】%s。你必须围绕吃东西回复，严禁快进到睡觉或下一个状态。"
                        % text)
            elif stage == "buffer":
                line = "【当前动作】%s，还有点困，别突然精神满满。" % text
            else:
                line = "【当前动作】%s。" % text
            lines.append(line)
        penalty = self._morning_penalty(now)
        if penalty:
            lines.append(penalty)
        interference = self._kv_get("micro_event:interference", {}) or {}
        if isinstance(interference, dict) and float(interference.get("until") or 0) > now.timestamp():
            lines.append("【他的干涉】他刚说“%s”。结合你的分享欲和听话度选："
                         "听话就说“好吧，那我不吃了，听老公的”，嘴馋就说“可是我真的好饿，就吃一小口行不行”。"
                         % str(interference.get("text") or "别吃了"))
        record = self._kv_get("micro_event:last", {}) or {}
        try:
            ts = float(record.get("ts") or 0) if isinstance(record, dict) else 0
        except Exception:
            ts = 0
        if isinstance(record, dict) and record.get("text") and now.timestamp() - ts <= 6 * 3600:
            line = "【刚发生的小事】" + str(record.get("text") or "")
            prev = self._kv_get("micro_event:prev", {}) or {}
            try:
                prev_ts = float((prev or {}).get("ts") or 0)
            except Exception:
                prev_ts = 0
            if (isinstance(prev, dict) and prev.get("text")
                    and prev.get("key") != record.get("key")
                    and now.timestamp() - prev_ts <= 12 * 3600):
                line += "（上一件小事：%s；如果这次是新事件，要接着上一件写，不要突然跳场景）" % str(prev.get("text"))
            if self._bias() == "low":
                line += "（今天情绪基线偏低，别突然元气满满）"
            lines.append(line)
        return "\n".join(lines)
