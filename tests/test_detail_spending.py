# -*- coding: utf-8 -*-
"""「本时段消费」不能把可取额度的联动下调再算一次（2026-11-23 线上：20 元面显示 40）。"""
from __future__ import annotations

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_api_schedule as api  # noqa: E402


class _FakeWallet:
    def __init__(self, rows):
        self._rows = list(rows)

    def rows_between(self, start_ts, end_ts):
        return [dict(row) for row in self._rows]

    def _expense_excluded(self, row):
        return str((row or {}).get('category') or '') in ('账户调整', '房租', '水电')


class DetailSpendingTests(unittest.TestCase):
    def _plugin(self, rows):
        return types.SimpleNamespace(wallet=_FakeWallet(rows))

    def test_credit_adjustment_is_not_counted_twice(self):
        rows = [
            {'account': 'cash', 'delta_cents': -2000, 'item_name': '一碗热汤面',
             'category': '吃的', 'ts': 1795000000.0},
            {'account': 'withdrawable', 'delta_cents': -2000,
             'item_name': '可取额度随现金下调', 'category': '账户调整', 'ts': 1795000001.0},
        ]
        data = api._detail_spending(self._plugin(rows), '2026-11-23', '12:00', '13:00')
        self.assertEqual(20.0, round(data['spending_total_yuan'], 2))
        self.assertEqual(['一碗热汤面'], [item['reason'] for item in data['spending']])

    def test_refund_and_non_cash_rows_are_ignored(self):
        rows = [
            {'account': 'cash', 'delta_cents': -1500, 'item_name': '牛肉粉',
             'category': '吃的', 'ts': 1795000000.0},
            {'account': 'cash', 'delta_cents': 1500, 'item_name': '退款',
             'category': '退款', 'ts': 1795000002.0},
            {'account': 'savings', 'delta_cents': -8000, 'item_name': '转账',
             'category': '转账', 'ts': 1795000003.0},
        ]
        data = api._detail_spending(self._plugin(rows), '2026-11-23', '12:00', '13:00')
        self.assertEqual(15.0, round(data['spending_total_yuan'], 2))
        self.assertEqual(['牛肉粉'], [item['reason'] for item in data['spending']])


if __name__ == '__main__':
    unittest.main()
