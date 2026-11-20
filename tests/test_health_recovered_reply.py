# -*- coding: utf-8 -*-
"""病好了以后不许再说「刚退烧/又该烧」（2026-11-18 18:48 比基尼事故）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reply_fact_guard import guard  # noqa: E402

HEALTHY = {"status": "健康", "sick": False}


def test_recovered_but_says_just_broke_fever_is_rewritten():
    text = "（裹着被子翻了个身）不行，就是有点累刚退，一出汗又该烧了。"
    fixed, violations = guard(text, {"health": HEALTHY})
    assert "刚退" not in fixed and "该烧" not in fixed
    assert "好了" in fixed or "有点累" in fixed
    assert violations and all(item["rule"] == "health" for item in violations)
