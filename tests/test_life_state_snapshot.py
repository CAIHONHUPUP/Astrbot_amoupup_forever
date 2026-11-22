# -*- coding: utf-8 -*-
"""LifeStateSnapshot v1 契约测试（task-27）。

只读断言：字段/版本/优先级/跨字段一致性/降级不崩；
用假插件跑，不连生产库、不启动 AstrBot。
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest

from services import life_state

NOW = dt.datetime(2026, 11, 22, 10, 32, 0)


class _DB:
    def __init__(self, kv=None, profiles=None):
        self.kv = dict(kv or {})
        self.profiles = list(profiles or [])
        self.writes = []

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.writes.append(key)
        self.kv[key] = value

    def proactive_profiles(self, limit=10):
        return self.profiles


class _Schedule:
    def __init__(self, rows):
        self.rows = dict(rows or {})

    def get(self, day):
        return list(self.rows.get(day) or [])


class _State:
    def __init__(self):
        self.schedule = _Schedule({"2026-11-22": [
            {"start": "09:00", "end": "11:00", "activity": "在家歇着", "place": "家", "zone": "home"},
            {"start": "11:30", "end": "13:00", "activity": "做午饭", "place": "家", "zone": "home"},
        ]})

    def current_activity_info(self):
        return ({"start": "09:00", "end": "11:00", "activity": "在家歇着",
                 "place": "家", "zone": "home", "category": "life", "event_id": "ev_home"},
                "covering")

    def current_activity(self):
        return self.current_activity_info()[0]

    def mood_score(self):
        return 62

    def mood_words(self):
        return ["平和", "有点想他"]


class _Wallet:
    def __init__(self, balance_cents=394725, rows=None):
        self._balance = int(balance_cents)
        self.rows = list(rows or [])

    def balance(self, account="cash"):
        return self._balance

    def rows_between(self, start, end):
        return list(self.rows)

    def ledger(self, limit=200):
        return list(self.rows)


class _Wardrobe:
    def daytime_text(self, day):
        return "卡其色风衣、卡其色半身裙、米色乐福鞋"


class _Plugin:
    def __init__(self, kv=None, wallet_rows=None, balance=394725):
        self.db = _DB(kv=kv, profiles=[{"uid": "u1",
                                        "private_umo": "lark:FriendMessage:ou_cec66"}])
        self.state = _State()
        self.wallet = _Wallet(balance_cents=balance, rows=wallet_rows or [])
        self.wardrobe = _Wardrobe()
        self._owner_umos = lambda: ["lark:FriendMessage:ou_cec66"]


def _nov_rows():
    return [
        {"idem": "meal:1", "account": "cash", "delta_cents": -1500, "reason": "面条",
         "category": "餐饮", "ts": dt.datetime(2026, 11, 20, 12, 0).timestamp()},
        {"idem": "get", "account": "cash", "delta_cents": -1000000, "reason": "存款：定期3个月",
         "category": "存款", "ts": dt.datetime(2026, 11, 16, 9, 0).timestamp()},
        {"idem": "rent:2026-11", "account": "cash", "delta_cents": -185303, "reason": "房租",
         "category": "其他", "ts": dt.datetime(2026, 11, 1, 9, 0).timestamp()},
    ]


class LifeStateSnapshotTests(unittest.TestCase):
    def test_contract_descriptor_matches_snapshot_fields(self):
        descriptor = life_state.contract_descriptor()
        self.assertEqual(descriptor["contract"], life_state.CONTRACT_NAME)
        self.assertEqual(descriptor["version"], life_state.CONTRACT_VERSION)
        self.assertEqual(tuple(descriptor["fields"]), life_state.REQUIRED_FIELDS)
        snapshot = life_state.build_snapshot(_Plugin(), NOW)
        self.assertEqual(set(snapshot), set(life_state.REQUIRED_FIELDS))

    def test_snapshot_header_and_principal(self):
        snapshot = life_state.build_snapshot(_Plugin(), NOW)
        self.assertEqual(snapshot["contract"], "amo-life-state")
        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(snapshot["source"], "forever")
        self.assertEqual(snapshot["day"], "2026-11-22")
        self.assertEqual(snapshot["generated_at"], NOW.timestamp())
        self.assertEqual(snapshot["principal"]["umo"], "lark:FriendMessage:ou_cec66")
        self.assertEqual(snapshot["principal"]["uid"], "ou_cec66")
        self.assertEqual(snapshot["principal"]["kind"], "owner")

    def test_location_and_action_come_from_single_scene(self):
        snapshot = life_state.build_snapshot(_Plugin(), NOW)
        self.assertEqual(snapshot["location"]["place"], "家")
        self.assertEqual(snapshot["location"]["zone"], "home")
        self.assertTrue(snapshot["location"]["at_home"])
        self.assertFalse(snapshot["location"]["in_shop"])
        self.assertEqual(snapshot["current_action"]["place"], "家")
        self.assertEqual(snapshot["schedule"]["current"]["activity"], "在家歇着")
        self.assertEqual(snapshot["schedule"]["next"]["activity"], "做午饭")

    def test_month_consumption_uses_canonical_metric(self):
        snapshot = life_state.build_snapshot(_Plugin(wallet_rows=_nov_rows()), NOW)
        # 只有面条 15 元算消费：存款/房租排除（单列资产变动/账单）
        self.assertEqual(snapshot["wallet"]["month"], "2026-11")
        self.assertEqual(snapshot["wallet"]["month_consumption_cents"], 1500)
        self.assertEqual(snapshot["wallet"]["balance_cents"], 394725)

    def test_shopping_line_stale_vs_live_wallet(self):
        kv = {"shopping_prompt": {
            "line": "你正在商场试米白色针织开衫，价格 985 元，卡里余额 4072 元，等老公回复",
            "until": NOW.timestamp() + 600,
        }}
        snapshot = life_state.build_snapshot(_Plugin(kv=kv), NOW)
        shopping = snapshot["shopping"]
        self.assertTrue(shopping["active"])
        self.assertEqual(shopping["mentioned_balance_cents"], 407200)
        self.assertEqual(shopping["mentioned_price_cents"], 98500)
        self.assertTrue(shopping["line_stale"])
        self.assertFalse(snapshot["consistency"]["ok"])
        self.assertIn("shopping_balance_stale",
                      [issue["code"] for issue in snapshot["consistency"]["issues"]])

    def test_shopping_line_fresh_has_no_issue(self):
        kv = {"shopping_prompt": {
            "line": "你正在商场试米白色针织开衫，价格 985 元，卡里余额 3946 元，等老公回复",
            "until": NOW.timestamp() + 600,
        }}
        snapshot = life_state.build_snapshot(_Plugin(kv=kv), NOW)
        self.assertFalse(snapshot["shopping"]["line_stale"])
        self.assertTrue(snapshot["consistency"]["ok"])

    def test_date_session_reports_canonical_missing(self):
        snapshot = life_state.build_snapshot(_Plugin(), NOW)
        # 生产事实：canonical kv[date_session] 不存在，只有旧投影
        self.assertFalse(snapshot["date_session"]["canonical_present"])
        self.assertEqual(snapshot["date_session"]["source"], "legacy_projection")

    def test_snapshot_is_readonly(self):
        plugin = _Plugin(kv={"shopping_prompt": {"line": "试衣服", "until": NOW.timestamp() + 60}})
        before = dict(plugin.db.kv)
        life_state.build_snapshot(plugin, NOW)
        self.assertEqual(plugin.db.writes, [])
        self.assertEqual(plugin.db.kv, before)

    def test_degraded_plugin_does_not_raise(self):
        class _Broken:
            def __getattr__(self, name):
                raise RuntimeError("no %s" % name)

        snapshot = life_state.build_snapshot(_Broken(), NOW)
        self.assertEqual(snapshot["contract"], "amo-life-state")
        self.assertTrue(snapshot["degraded"])
        fields = {item["field"] for item in snapshot["degraded"]}
        self.assertIn("wallet", fields)

    def test_compact_keeps_contract(self):
        snapshot = life_state.build_snapshot(_Plugin(), NOW)
        compact = life_state.compact(snapshot, ("wallet",))
        self.assertEqual(compact["contract"], "amo-life-state")
        self.assertEqual(compact["version"], 1)
        self.assertIn("wallet", compact)
        self.assertNotIn("schedule", compact)


if __name__ == "__main__":
    unittest.main()
