# -*- coding: utf-8 -*-
"""消费词表：买衣服/开衫也要被当成消费声称（2026-11-21 线上）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_fact_binder import claim_kinds, supported  # noqa: E402


class ClaimVocabTests(unittest.TestCase):
    def test_cardigan_is_shop_claim(self):
        kinds = claim_kinds("老公你看！我今天买了米白色针织开衫，花了 649 元。")
        self.assertIn("shop", kinds)

    def test_sneakers_is_shop_claim(self):
        self.assertIn("shop", claim_kinds("我买了一双靴子"))

    def test_shop_claim_needs_matching_ledger(self):
        sentence = "今天买了米白色针织开衫，花了 649 元"
        other = [{"reason": "点外卖", "delta_cents": -2500}]
        self.assertFalse(supported(sentence, "shop", other))
        same = [{"reason": "米白色针织开衫", "delta_cents": -64900}]
        self.assertTrue(supported(sentence, "shop", same))


if __name__ == "__main__":
    unittest.main()
