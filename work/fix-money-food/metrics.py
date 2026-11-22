# -*- coding: utf-8 -*-
"""task-21 指标：生产只读对账 + 新规则模拟 + 提醒单飞。

只读生产库（file:...?mode=ro），不写任何生产数据；模拟用内存 DB。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import threading

ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + r"\tests")

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_food_cost as fc  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from plugin_fakes import WalletFakeDB  # noqa: E402

PROD_DB = r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever\amo_forever.db"
DAY = "2026-11-16"


class ReadOnlyDB:
    def __init__(self):
        self.conn = sqlite3.connect("file:" + PROD_DB.replace("\\", "/") + "?mode=ro",
                                    uri=True)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self):
        self.schedule = {DAY: []}

    def commit_schedule_change(self, day, new_items, old_items=None, source="", reason=""):
        self.schedule[day] = [dict(row) for row in new_items]
        return {"changed": True, "saved": True}


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()
        self.wallet = Wallet(WalletFakeDB())
        self.wallet.ensure_schema()
        self.wallet.credit(200000, "测试初始化", "test:init")
        self.state = FakeState()


def production_report():
    db = ReadOnlyDB()
    wallet = Wallet(db)
    wallet._ready = True
    report = wallet.reconcile()
    accounts = report["accounts"]
    anomalies = report["anomalies"]

    rows = [dict(row) for row in db.conn.execute(
        "SELECT idem, account, delta_cents, reason, ref, event_id, ts, category, sub_category "
        "FROM wallet_ledger WHERE account='cash' ORDER BY ts").fetchall()]
    by_day = {}
    for row in rows:
        if int(row["delta_cents"]) >= 0:
            continue
        moment = dt.datetime.fromtimestamp(float(row["ts"]))
        if moment.strftime("%Y-%m-%d") in ("2026-11-16", "2026-11-15"):
            by_day.setdefault(moment.strftime("%Y-%m-%d"), []).append(row)

    def food(row):
        if str(row.get("category") or "") == "餐饮":
            return True
        body = str(row.get("reason") or "") + str(row.get("sub_category") or "")
        return any(word in body for word in ("吃", "饭", "面", "粥", "外卖", "夜宵", "宵夜",
                                             "烧烤", "火锅", "奶茶"))

    meal_stats = {}
    for day, items in by_day.items():
        instances = set()
        stamps = []
        for row in items:
            if not food(row):
                continue
            event = str(row.get("event_id") or "")
            idem = str(row.get("idem") or "")
            instance = event if event.startswith("meal:") else (
                ":".join(idem.split(":")[:3]) if idem.startswith("meal:") else "")
            instances.add(instance or idem)
            stamps.append(float(row["ts"]))
        pairs = 0
        stamps.sort()
        for i in range(len(stamps)):
            for j in range(i + 1, len(stamps)):
                if stamps[j] - stamps[i] > 45 * 60:
                    break
                slot_i = fc.meal_slot_at(dt.datetime.fromtimestamp(stamps[i]))[1]
                slot_j = fc.meal_slot_at(dt.datetime.fromtimestamp(stamps[j]))[1]
                if slot_i and slot_i == slot_j:
                    pairs += 1
        meal_stats[day] = {"餐费笔数": len(stamps),
                           "不同餐次实例": len(instances),
                           "≤45min 同餐次重复对": pairs}

    blocks = {}
    try:
        for row in db.conn.execute("SELECT day, items FROM schedule").fetchall():
            items = row["items"]
            if isinstance(items, str):
                try:
                    items = json.loads(items)
                except Exception:
                    items = []
            meal_blocks = [item for item in (items or [])
                           if isinstance(item, dict) and item.get("category") == "meal"]
            if row["day"] in ("2026-11-15", "2026-11-16"):
                blocks[row["day"]] = len(meal_blocks)
    except Exception as exc:
        blocks = {"error": str(exc)}
    return {"accounts": accounts, "anomalies": anomalies,
            "meal_stats_production": meal_stats, "meal_blocks_production": blocks,
            "ledger_rows": len(rows)}


def simulate_new_rules():
    plugin = FakePlugin()
    attempts = [
        (6, 8, "豆浆和包子", True), (7, 31, "白粥", False), (8, 0, "豆浆和包子", False),
        (11, 31, "面条", True), (12, 0, "家常菜", False), (12, 9, "清淡外卖", False),
        (17, 30, "家常菜", True), (18, 10, "外卖", False),
        (22, 0, "烧烤", True), (22, 30, "泡面", False),
        (23, 30, "小蛋糕", True),
    ]
    charged, blocked = 0, []
    for hour, minute, item, expect in attempts:
        when = dt.datetime(2026, 11, 16, hour, minute)
        clock = fc._amo_clock
        old_now, old_ts = clock.now, clock.now_ts
        clock.now = lambda when=when: when
        clock.now_ts = lambda when=when: when.timestamp()
        try:
            out = fc.pay_food(plugin, item, item, now=when)
        finally:
            clock.now = old_now
            clock.now_ts = old_ts
        if out.get("charged"):
            charged += 1
        else:
            blocked.append({"time": when.strftime("%H:%M"), "item": item,
                            "reason": out.get("reason")})
    rows = [row for row in plugin.wallet.ledger(100)
            if int(row.get("delta_cents") or 0) < 0]
    instances = sorted({str(row.get("event_id") or "") for row in rows})
    meal_blocks = [row for row in plugin.state.schedule[DAY]
                   if str(row.get("category")) == "meal"]
    stamp_pairs = 0
    stamps = sorted(float(row["ts"]) for row in rows)
    for i in range(len(stamps)):
        for j in range(i + 1, len(stamps)):
            if stamps[j] - stamps[i] > 45 * 60:
                break
            slots = {fc.meal_slot_at(dt.datetime.fromtimestamp(stamps[i]))[1],
                     fc.meal_slot_at(dt.datetime.fromtimestamp(stamps[j]))[1]}
            if len(slots) == 1 and "" not in slots:
                stamp_pairs += 1
    return {"尝试扣款": len(attempts), "实际扣款": charged,
            "被拦": blocked, "餐次实例": instances,
            "日程 meal 块": len(meal_blocks),
            "≤45min 同餐次已扣重复对": stamp_pairs,
            "账本与账户对账": plugin.wallet.reconcile()["ok"]}


def reminder_report():
    import amo_schedule as sched

    class Db:
        def __init__(self):
            self.kv = {}

        def kv_get(self, key, default=None):
            return self.kv.get(key, default)

        def kv_set(self, key, value):
            self.kv[key] = value

    host = sched.ScheduleMixin.__new__(sched.ScheduleMixin)
    host.db = Db()
    host.WEATHER_ALERT_CLAIM_TTL = 120.0
    first = host._claim_weather_slot("2026-11-18", "evening", "t1")
    second = host._claim_weather_slot("2026-11-18", "evening", "t2")
    host._release_weather_slot(first)
    third = host._claim_weather_slot("2026-11-18", "evening", "t3")

    import amo_proactive as proactive

    class PDb:
        def __init__(self, rows):
            self.rows = rows
            self.kv = {}

        def get_schedule(self, day):
            return list(self.rows)

        def schedule_version(self, day):
            return 1

        def kv_get(self, key, default=None):
            return self.kv.get(key, default)

        def kv_set(self, key, value):
            self.kv[key] = value

    rows = [{"start": "15:10", "end": "15:50", "activity": "复诊拿药",
             "place": "医院", "zone": "hospital"}]
    db = PDb(rows)
    now = dt.datetime(2026, 11, 18, 15, 0)
    fact1 = proactive.schedule_reminder_fact(db, now, day="2026-11-18", claim=True)
    fact2 = proactive.schedule_reminder_fact(db, now, day="2026-11-18", claim=True)
    late_ok = proactive.schedule_fact_current(db, fact1,
                                              now=dt.datetime(2026, 11, 18, 15, 3))
    late_blocked = proactive.schedule_fact_current(
        db, fact1, now=dt.datetime(2026, 11, 18, 15, 20))
    return {"weather 占位可重入": bool(first) and not second and bool(third),
            "schedule 幂等键": fact1.get("key"),
            "schedule 第二次占位被拦": fact2 == {},
            "schedule 迟到 3 分放行": late_ok,
            "schedule 迟到 20 分拦下": not late_blocked}


if __name__ == "__main__":
    result = {"production_readonly": production_report(),
              "new_rules_simulation": simulate_new_rules(),
              "reminder": reminder_report()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
