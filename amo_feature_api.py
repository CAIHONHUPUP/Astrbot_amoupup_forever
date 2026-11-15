# -*- coding: utf-8 -*-
"""陪伴插件对外状态/事件 API（守护插件读取陪伴状态的唯一入口）。

对外签名固化：get_hunger / add_hunger / hunger_state / ... / on_forced_overtime。
内部实现可以变，方法名、参数、返回值不要改。
"""
from __future__ import annotations

try:
    from .amo_shared import now_ts
except ImportError:
    from amo_shared import now_ts


class FeatureApiMixin:
        def note_expense_summary(self, line):
            self.db.kv_set("expense_summary", {"line": str(line or ""), "until": now_ts() + 2 * 86400})

        def get_hunger(self, target="amo"):

            """给守护/面板读：当前饥饿值 0-100。"""

            return self.hunger.get()



        def add_hunger(self, target="amo", amount=0):

            """吃东西后加饱腹度；target 目前只有她本人（amo）。"""

            return self.hunger.add(amount)



        def hunger_state(self):

            return self.hunger.state()



        def mark_meal(self, food, cooldown_hours=4.0, mood="满足"):

            self.hunger.mark_meal(food, cooldown_hours, mood)



        def request_hunger_help(self):

            self.hunger.request_help()



        def note_transfer_event(self, amount_cents):

            """记录一笔他转来的钱，供“转账推拉”Prompt 用。"""

            import time as _t

            now = _t.time()

            self.db.kv_set("transfer:last", {"amount": int(amount_cents or 0), "ts": now})

            self.db.kv_set("positive:last", now)

        def note_purchase(self, name, price_cents):

            """记录她刚买的贵东西，供“花钱心虚”Prompt 用。"""

            import time as _t

            self.db.kv_set("last_purchase", {"name": str(name or "东西"),

                                             "price": int(price_cents or 0), "ts": _t.time()})

        def _owner_umos(self):

            """她能主动找到他的会话：qq / 飞书私聊优先。"""

            umos = []

            try:

                rows = self.db.proactive_profiles(limit=10) or []

            except Exception:

                rows = []

            for row in rows:

                for key in ("private_umo", "last_group"):

                    umo = str((row or {}).get(key) or "").strip()

                    if umo and umo not in umos:

                        umos.append(umo)

                try:

                    data = self.db.kv_get("channels:%s" % (row or {}).get("uid"), {}) or {}

                    for value in data.values():

                        umo = str((value or {}).get("umo") or "").strip()

                        if umo and umo not in umos:

                            umos.append(umo)

                except Exception:

                    pass

                if umos:

                    break

            return umos



        async def push_to_owner(self, text):

            """主动消息：给管理员/老公发一条（逛街试穿、回家炫耀等）。"""

            text = str(text or "").strip()

            if not text:

                return False

            for umo in self._owner_umos():

                try:

                    await self._send_to_umo(umo, text)

                    return True

                except Exception:

                    continue

            return False



        async def hunger_help(self, message):

            """饿到没钱：写进状态，并真的给他发一条求助。"""

            self.hunger.request_help(message)

            for umo in self._owner_umos():

                try:

                    await self._send_to_umo(umo, message)

                    return True

                except Exception:

                    continue

            return False



        def set_date_mood(self, line, until_ts=0):

            """守护插件写进来的约会情绪提示（期待/失落/冷淡），到点自动失效。"""

            self.db.kv_set("date_mood_line", {"line": str(line or ""), "until": float(until_ts or 0)})

        def set_date_separation(self, title, ended_ts=0):
            import time as _time
            base = float(ended_ts or _time.time())
            self.db.kv_set("date_separation", {"title": str(title or "约会"),
                                               "ended_ts": base,
                                               "until": base + 6 * 3600})

        def set_date_home(self, title, at=0):
            """约会收尾：你们一起回到了共同的家（同居），不是分别/送别。"""
            at = float(at or 0)
            self.db.kv_set("date_home", {"title": str(title or "约会"), "at": at,
                                         "until": at + 3 * 3600})

        def set_date_now(self, line, until_ts=0, since=None):

            """约会进行中：最高事实 + 锁定当前行为（守护插件 /赴约 调用）。"""

            self.db.kv_set("date_now", {"line": str(line or ""), "until": float(until_ts or 0),
                                        "since": float(since or 0)})
            # 已经碰面：清掉「出门前准备」状态，别让她再补一句「我出门了」
            try:
                self.db.kv_set("date_prep", {})
            except Exception:  # cleanup: 清出门准备状态，失败不影响约会
                pass

        def set_date_meet(self, title, place="", ttl=900):
            """碰面：只记事实（事件/地点），开场白由大模型按事实自己生成。"""
            import time as _t
            self.db.kv_set("date_meet", {"title": str(title or ""),
                                         "place": str(place or ""),
                                         "until": float(_t.time()) + float(ttl or 900)})

        def set_date_ending(self, fact, reason="", ttl=600):
            """约会收尾：只写客观事实，道别话由大模型现场生成（禁止模板）。"""
            import time as _t
            self.db.kv_set("date_ending", {
                "fact": str(fact or ""), "reason": str(reason or ""),
                "until": float(_t.time()) + float(ttl or 600), "sent": False,
            })

        def clear_date_ending(self):
            self.db.kv_set("date_ending", {})

        def set_date_fact(self, line, until_ts=0):
            import time as _t
            stamp = float(until_ts or 0) or _t.time()
            day = _t.strftime("%Y-%m-%d", _t.localtime(stamp))

            """约会已发生：当天内最高事实，防“一个人吃饭”幻觉。"""

            self.db.kv_set("date_fact", {"line": str(line or ""), "until": float(until_ts or 0),
                                         "day": day})

        def set_last_date_event(self, activity, ended_ts=0, hours=6):

            """约会结束锁：6 小时内她必须记得“刚和老公共度”，防失忆。"""

            import time as _t

            end = float(ended_ts or _t.time())

            self.db.kv_set("last_date_event", {

                "activity": str(activity or "约会"), "ended_ts": end,

                "until_ts": end + float(hours or 6) * 3600,

                "day": _t.strftime("%Y-%m-%d", _t.localtime(end)),

            })

        def set_date_prep(self, activity, start_ts=0):

            """约会前准备：化妆/挑衣服，别让她秒回“我到了”。"""

            self.db.kv_set("date_prep", {"activity": str(activity or "约会"),

                                          "start_ts": float(start_ts or 0)})

        def set_date_incident(self, text, until_ts=0):

            """约会中的随机小插曲（下雨/排队/试衣服），让聊天有话题。"""

            self.db.kv_set("date_incident", {"text": str(text or ""),

                                             "until_ts": float(until_ts or 0)})

        def set_current_action(self, action, until_ts=0, place="外面", zone="restaurant", category="social"):

            """守护插件写当前行为：压过 JIT 到点决策，到点自动失效。

            2026-11-15：写入口收敛到 amo_current_action.set_action（全局状态锁），
            日程 Tick / 微型事件 / 守护插件共用同一条写路径。
            """

            try:

                try:
                    from .amo_current_action import set_action as _set_action
                except ImportError:
                    from amo_current_action import set_action as _set_action

                if _set_action(self, action, until_ts, place=place, zone=zone,
                               category=category, source="writer"):
                    return
            except Exception:
                pass

            self.db.kv_set("current_action", {

                "action": str(action or ""), "place": str(place or ""),

                "zone": str(zone or ""), "category": str(category or ""),

                "at": now_ts(), "until": float(until_ts or 0),

            })



        def mark_hunger_cooldown(self, minutes):

            self.hunger.mark_cooldown(minutes)



        def late_night_state(self):

            if getattr(self.state, "late", None) is None:

                return {}

            return self.state.late.state()



        def note_late_snack(self, amount_cents, food=""):

            if getattr(self.state, "late", None) is not None:

                self.state.late.note_snack(amount_cents, food)



        def note_late_fine(self, amount_cents):

            if getattr(self.state, "late", None) is not None:

                self.state.late.note_fine(amount_cents)



        def soothe_late_night(self, reason=""):

            if getattr(self.state, "late", None) is not None:

                return self.state.late.soothe(reason)

            return None



        def shopping_trigger(self, now=None):

            """周末 70%、发工资、情绪低落时想逛街；返回原因或空串。"""

            import datetime as _dt

            import hashlib

            now = now or _dt.datetime.now()

            day = now.date().isoformat()

            if now.weekday() >= 5:

                roll = int(hashlib.md5(day.encode()).hexdigest()[:8], 16) % 100

                if roll < 70:

                    return "周末逛街"

            if now.day == 15:

                return "发工资犒劳自己"

            try:

                mood = self.state.mood

                if float(mood.get("郁闷", 0)) + float(mood.get("焦虑", 0)) >= 130:

                    return "心情不好花钱发泄"

            except Exception:

                pass

            return ""



        def add_closet_item(self, item_data):

            """购物同步衣橱：name/color/category/time_slot/price。"""

            if getattr(self, "wardrobe", None) is None:

                return {"ok": False, "msg": "衣橱未就绪"}

            data = dict(item_data or {})

            name = str(data.get("name") or "").strip()

            if not name:

                return {"ok": False, "msg": "衣服名字为空"}

            cat = str(data.get("category") or "top")

            cat = {"上衣": "top", "裤子": "bottom", "裙子": "skirt", "外套": "coat",

                   "鞋子": "shoes", "睡衣": "top"}.get(cat, cat)

            slot = str(data.get("time_slot") or "day")

            sleep = slot == "night"

            res = self.wardrobe.add_item(cat, name, source="shopping", sleep=sleep)

            if res.get("ok") and res.get("item"):

                try:

                    db = self.wardrobe._load()

                    for row in db.get("items") or []:

                        if row.get("id") == res["item"].get("id"):

                            row["color"] = str(data.get("color") or "")

                            row["time_slot"] = slot

                            row["price"] = int(data.get("price") or 0)

                            break

                    self.wardrobe._save(db)

                except Exception:

                    pass

                try:

                    import asyncio

                    from .amo_closet_image import ensure_after_add

                    asyncio.get_running_loop().create_task(ensure_after_add(self, res["item"]))

                except Exception:

                    pass

            return res



        def note_shopping_pending(self, line, until_ts=0):

            self.db.kv_set("shopping_prompt", {"line": str(line or ""), "until": float(until_ts or 0)})



        def note_gift_event(self, line, until_ts=0):

            self.db.kv_set("gift_line", {"line": str(line or ""), "until": float(until_ts or 0)})



        def on_big_stock_loss(self):

            if getattr(self.state, "late", None) is not None:

                self.state.late.on_big_stock_loss()



        def on_forced_overtime(self):

            if getattr(self.state, "late", None) is not None:

                self.state.late.on_forced_overtime()
