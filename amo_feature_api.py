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
