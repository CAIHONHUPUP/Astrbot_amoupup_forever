# -*- coding: utf-8 -*-
"""MetricsAPI v1 契约测试（task-27）：消费口径四插件只有一份定义。

跨仓数值比对：
- forever/services/metrics.py（生产者，唯一计算函数）；
- watch/services/metrics.py（消费端，旧版能陪时的降级重算）。
同一批流水必须算出同一个“本月消费”，否则守护首页/生活卡/能陪面板又会各说各话。

（watch 的 ui.report.life_block 与 dashboard_text 同源由 watch 仓
test_metrics_consistency.py 覆盖；本文件只比对口径本身。）
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import sys
import unittest

from services import metrics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.dirname(ROOT)
WATCH_METRICS = os.path.join(PLUGINS_DIR, "Astrbot_amoupup_watch", "services", "metrics.py")


def _load_watch_metrics():
    if not os.path.isfile(WATCH_METRICS):
        return None
    spec = importlib.util.spec_from_file_location("watch_metrics_under_test", WATCH_METRICS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(idem, delta, reason, category="其他", ts=None, account="cash"):
    stamp = ts if ts is not None else dt.datetime(2026, 11, 20, 12, 0).timestamp()
    return {"idem": idem, "account": account, "delta_cents": delta, "reason": reason,
            "category": category, "ts": stamp}


def _november_rows():
    """2026-11 生产形态流水（删敏）：覆盖消费/账单/资产/退款/收入/其他月份。"""
    return [
        _row("meal:1", -1500, "面条", "餐饮"),
        _row("shop:2026-11-14:item", -134997, "买衣服：一条绿色碎花连衣裙", "生活"),
        _row("rent:2026-11", -185303, "房租", "其他"),                    # 旧库 category 被回填“其他”
        _row("water:2026-11", -1929, "水费", "其他"),
        _row("bank:dep:2026-11-16", -1000000, "存款：定期3个月", "存款"),
        _row("decision:fundbuy", -50000, "buy_fund", "投资"),
        _row("decision:buy", -126201, "买入 600968", "其他"),
        _row("social_event:prepaid", -20000, "下班和柳如烟吃火锅（预扣，已退回）", "社交"),
        _row("cleanup:refund:1", 20000, "退款：预扣已退回", "退款"),
        _row("salary:2026-11", 680000, "工资", "工资"),
        _row("meal:oct", -300, "早餐", "餐饮",
             dt.datetime(2026, 10, 31, 9, 0).timestamp()),
        _row("init:1", 2090104, "可取余额初始化", "其他", account="withdrawable"),
    ]


EXPECTED_CONSUMPTION = 1500 + 134997  # 面条 + 连衣裙（房租/存款/基金/买入/退款都不是消费）


class MetricsConsumptionContractTests(unittest.TestCase):
    def test_forever_consumption_is_canonical(self):
        rows = _november_rows()
        self.assertEqual(metrics.consumption_cents(rows, "2026-11"), EXPECTED_CONSUMPTION)
        self.assertEqual(metrics.bill_cents(rows, "2026-11"), 185303 + 1929)
        self.assertEqual(metrics.assets_change_cents(rows, "2026-11"),
                         1000000 + 50000 + 126201)

    def test_watch_fallback_matches_forever(self):
        watch_metrics = _load_watch_metrics()
        if watch_metrics is None:
            self.skipTest("本机没有守护插件目录，跨仓比对跳过")
        rows = _november_rows()
        self.assertEqual(watch_metrics.consumption_cents(rows, "2026-11"),
                         metrics.consumption_cents(rows, "2026-11"))
        self.assertEqual(watch_metrics.consumption_cents(rows, "2026-11"),
                         EXPECTED_CONSUMPTION)

    def test_row_classification_agrees(self):
        watch_metrics = _load_watch_metrics()
        if watch_metrics is None:
            self.skipTest("本机没有守护插件目录，跨仓比对跳过")
        for row in _november_rows():
            with self.subTest(row=row["idem"]):
                self.assertEqual(watch_metrics.classify_row(row),
                                 metrics.classify_row(row))

    def test_contract_descriptors(self):
        forever = metrics.contract_descriptor()
        self.assertEqual(forever["contract"], "amo-metrics")
        self.assertEqual(forever["version"], 1)
        self.assertIn("expense_month_consumption", forever["metrics"])
        watch_metrics = _load_watch_metrics()
        if watch_metrics is not None:
            watch = watch_metrics.contract_descriptor()
            self.assertEqual(watch["contract"], forever["contract"])
            self.assertEqual(watch["version"], forever["version"])
            self.assertEqual(watch["role"], "consumer")


class _Wallet:
    def __init__(self, rows):
        self.rows = rows

    def balance(self, account="cash"):
        return 394725

    def rows_between(self, start, end):
        return list(self.rows)

    def ledger(self, limit=200):
        return list(self.rows)


class _Plugin:
    def __init__(self, rows):
        self.wallet = _Wallet(rows)

    def __getattr__(self, name):
        raise AttributeError(name)


class BuildMetricsTests(unittest.TestCase):
    def test_build_metrics_wallet_block(self):
        # 固定“现在”为 2026-11，避免跨月跑测试时月界变化
        result = metrics.build_metrics(_Plugin(_november_rows()), dt.datetime(2026, 11, 22))
        self.assertEqual(result["contract"], "amo-metrics")
        self.assertEqual(result["version"], 1)
        wallet = result["wallet"]
        self.assertEqual(wallet["month"], "2026-11")
        self.assertEqual(wallet["month_consumption_cents"], EXPECTED_CONSUMPTION)
        self.assertEqual(wallet["balance_cents"], 394725)
        self.assertEqual(result["photos"]["source"], "media_ledger")

    def test_month_bounds(self):
        month, start, end = metrics.month_bounds("2026-11")
        self.assertEqual(month, "2026-11")
        self.assertEqual(dt.datetime.fromtimestamp(start).date(), dt.date(2026, 11, 1))
        self.assertEqual(dt.datetime.fromtimestamp(end).date(), dt.date(2026, 12, 1))


if __name__ == "__main__":
    unittest.main()
