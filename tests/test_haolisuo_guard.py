# -*- coding: utf-8 -*-
"""病好了以后不许说「等我好利索点」（2026-11-18 20:06）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reply_fact_guard import guard  # noqa: E402

HEALTHY = {"status": "健康", "sick": False}


def test_haolisuo_while_healthy_is_rewritten():
    text = "（窝还在车上，把毯子往上拉了拉）现在？冷不冷啊这天气。等我好利索点再说，别催。"
    fixed, hits = guard(text, {"health": HEALTHY})
    assert "好利索" not in fixed, fixed
    assert hits
