# -*- coding: utf-8 -*-
"""供应商余额查询：DeepSeek / SiliconFlow / OpenAI 兼容中转。

面板上的「账户余额」卡就靠这几个分支，接口字段各家不一样；用假 HTTP 响应
把解析、单位换算、异常兜底都跑一遍（不联网）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_shared import fetch_deepseek_balance, fetch_provider_balance  # noqa: E402


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def json_response(payload):
    return FakeResponse(json.dumps(payload).encode("utf-8"))


class BalanceTests(unittest.TestCase):
    def call(self, payload, kind, base="https://api.example.com"):
        with mock.patch("urllib.request.urlopen", return_value=json_response(payload)):
            return fetch_provider_balance("sk-test", base, kind)

    def test_missing_key_or_base(self) -> None:
        self.assertFalse(fetch_provider_balance("", "https://x", "deepseek")["ok"])
        self.assertFalse(fetch_provider_balance("sk", "", "deepseek")["ok"])

    def test_deepseek_prefers_cny(self) -> None:
        payload = {
            "is_available": True,
            "balance_infos": [
                {"currency": "USD", "total_balance": "1.00"},
                {"currency": "CNY", "total_balance": "36.75",
                 "granted_balance": "0", "topped_up_balance": "36.75"},
            ],
        }
        result = self.call(payload, "deepseek")
        self.assertTrue(result["ok"])
        self.assertTrue(result["available"])
        self.assertEqual(result["currency"], "CNY")
        self.assertEqual(result["amount"], 36.75)
        self.assertEqual(result["topped_up"], 36.75)

    def test_deepseek_falls_back_to_first_info(self) -> None:
        result = self.call({"balance_infos": [{"currency": "USD", "total_balance": "2.5"}]}, "deepseek")
        self.assertEqual(result["amount"], 2.5)

    def test_deepseek_without_info(self) -> None:
        result = self.call({"balance_infos": []}, "deepseek")
        self.assertFalse(result["ok"])
        self.assertIn("余额", result["error"])

    def test_deepseek_bad_number(self) -> None:
        result = self.call({"balance_infos": [{"currency": "CNY", "total_balance": "N/A"}]}, "deepseek")
        self.assertIsNone(result["amount"])

    def test_siliconflow_balance(self) -> None:
        result = self.call({"data": {"balance": "12.345"}}, "siliconflow")
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 12.35)

    def test_siliconflow_total_balance(self) -> None:
        result = self.call({"data": {"totalBalance": "8"}}, "siliconflow")
        self.assertEqual(result["amount"], 8.0)

    def test_siliconflow_missing_field(self) -> None:
        self.assertFalse(self.call({"data": {}}, "siliconflow")["ok"])

    def test_openai_compatible_remaining(self) -> None:
        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if "subscription" in url:
                return json_response({"hard_limit_usd": 10.0})
            return json_response({"total_usage": 250})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = fetch_provider_balance("sk", "https://relay.example.com", "openai")
        self.assertTrue(result["ok"])
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["limit"], 10.0)
        self.assertEqual(result["used"], 2.5)
        self.assertEqual(result["amount"], 7.5)

    def test_openai_compatible_usage_failure(self) -> None:
        def fake_urlopen(request, timeout=0):
            if "subscription" in request.full_url:
                return json_response({"hard_limit_usd": 5.0})
            raise RuntimeError("usage 接口挂了")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = fetch_provider_balance("sk", "https://relay.example.com", "openai")
        self.assertEqual(result["used"], 0.0)
        self.assertEqual(result["amount"], 5.0)

    def test_openai_compatible_missing_limit(self) -> None:
        result = self.call({}, "openai")
        self.assertFalse(result["ok"])

    def test_http_error_is_returned_not_raised(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = fetch_provider_balance("sk", "https://x.example", "deepseek")
        self.assertFalse(result["ok"])
        self.assertIn("connection refused", result["error"])

    def test_deepseek_wrapper(self) -> None:
        payload = {"is_available": True,
                   "balance_infos": [{"currency": "CNY", "total_balance": "12.30"}]}
        with mock.patch("urllib.request.urlopen", return_value=json_response(payload)):
            result = fetch_deepseek_balance("sk", "https://api.deepseek.com")
        self.assertTrue(result["ok"])
        self.assertEqual(result["amount"], 12.3)

    def test_deepseek_wrapper_without_key(self) -> None:
        self.assertFalse(fetch_deepseek_balance("")["ok"])


if __name__ == "__main__":
    unittest.main()
