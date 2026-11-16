# -*- coding: utf-8 -*-
"""生病自主决策：JSON 解析、JIT 请假/带病上班、工作日骨架统一。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from amo_sick_decision import parse_decision  # noqa: E402
from schedule_day_segments import workday_segments  # noqa: E402
from schedule_engine import jit_plan  # noqa: E402


def _seg(start, end, activity, place, zone, category, **kwargs):
    row = {"start": start, "end": end, "activity": activity, "place": place,
           "zone": zone, "category": category}
    row.update(kwargs)
    return row


def test_parse_take_sick_leave():
    data = parse_decision('{"tool":"take_sick_leave","reason":"太难受了"}')
    assert data == {"action": "take_sick_leave", "reason": "太难受了"}


def test_parse_go_to_work_with_code_fence():
    fence = chr(96) * 3
    raw = fence + 'json\n{"tool":"go_to_work_sick","reason":"缺钱"}' + fence
    data = parse_decision(raw)
    assert data["action"] == "go_to_work_sick"


def test_parse_rejects_unknown_tool():
    assert parse_decision('{"tool":"sleep_all_day"}') is None


def test_jit_sick_leave_is_home_rest():
    plan = jit_plan(9, 80, 5000, sick=True, weekday=0, sick_action="take_sick_leave")
    assert plan["zone"] == "home"
    assert "请假" in plan["action"] or "休息" in plan["action"]


def test_jit_sick_work_is_masked_commute():
    plan = jit_plan(8, 80, 5000, sick=True, weekday=0, sick_action="go_to_work_sick")
    assert plan["zone"] == "road"
    assert "口罩" in plan["action"]


def test_workday_skeleton_sick_work_masks_and_drowses():
    ctx = {"home": "家", "workplace": "公司", "health": {"sick": True},
           "sick_action": "go_to_work_sick", "yesterday": {}, "has_gift_car": False}
    segments = workday_segments(ctx, _seg)
    commute = [s for s in segments if s["category"] == "commute" and s["start"] == "08:00"][0]
    work = [s for s in segments if s["start"] == "09:00"][0]
    assert "口罩" in commute["activity"]
    assert "昏昏沉沉" in work["activity"]
