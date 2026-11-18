# -*- coding: utf-8 -*-
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schedule_change import apply_change  # noqa: E402


def test_chat_split_now_cuts_current_block():
    items = [
        {"start": "20:18", "end": "23:00", "activity": "在家歇着，看剧", "place": "家", "category": "rest"},
    ]
    change = {"activity": "和老公连麦看电视", "place": "家", "category": "rest", "anchor": "now", "split_now": True}
    new = apply_change(items, change, datetime.datetime(2026, 11, 18, 20, 37))
    assert new[0]["end"] == "20:37"
    assert new[1]["start"] == "20:37"
    assert new[1]["activity"] == "和老公连麦看电视"
