# -*- coding: utf-8 -*-
"""01:00 diary hygiene: 07:00 cached text must not claim he is physically in the room."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402
astrbot_stub.install()

from amo_diary_guard import sanitize_diary  # noqa: E402


def test_he_still_in_room_is_rewritten():
    raw = "洗漱的时候手忙脚乱，他还在屋里，我回头喊了句我先走了，也没等应声。"
    out = sanitize_diary(raw, together=False)
    assert "他还在屋里" not in out
    assert "我先走了" not in out
    assert "一个人" in out or "微信" in out
