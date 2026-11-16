# -*- coding: utf-8 -*-
"""日程总闸：zone 与 activity 明显冲突时纠正文案，保留物理字段。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402
astrbot_stub.install()

from amo_human_state import HumanState  # noqa: E402


def test_road_item_with_office_wording_is_repaired():
    items = [{"start": "08:00", "end": "09:00", "zone": "road", "category": "commute",
              "activity": "在工位上发呆", "detail": "键盘边放着药"}]
    HumanState._enforce_zone_activity(items, "2026-11-16", "chat")
    assert items[0]["activity"] == "在路上赶路"
    assert items[0]["detail"] == ""


def test_home_item_with_commute_wording_is_repaired():
    items = [{"start": "08:00", "end": "09:00", "zone": "home", "category": "life",
              "activity": "挤地铁去公司"}]
    HumanState._enforce_zone_activity(items, "2026-11-16", "jit")
    assert items[0]["activity"] == "在家待着，做点自己的事"


def test_office_item_with_home_wording_is_repaired():
    items = [{"start": "10:00", "end": "11:00", "zone": "office", "category": "work",
              "activity": "窝在沙发上刷手机"}]
    HumanState._enforce_zone_activity(items, "2026-11-16", "jit")
    assert items[0]["activity"] == "在工位上忙工作"


def test_physical_partner_presence_is_removed_outside_dates():
    items = [
        {"start": "05:00", "end": "06:00", "zone": "home", "category": "sleep",
         "activity": "黏着老公的那边睡得最安稳"},
        {"start": "20:00", "end": "21:00", "zone": "home", "category": "life",
         "activity": "他还在屋里，我回头喊他"},
        {"start": "18:00", "end": "19:00", "zone": "home", "category": "social",
         "activity": "和老公一起看电视"},
    ]
    HumanState._enforce_zone_activity(items, "2026-11-16", "jit")
    assert items[0]["activity"] == "一个人裹着被子睡着"
    assert "他还在屋里" not in items[1]["activity"]
    assert items[2]["activity"] == "和老公一起看电视"


def test_sleep_category_cannot_write_morning_routine():
    items = [{"start": "06:00", "end": "07:00", "zone": "home", "category": "sleep",
              "activity": "急急忙忙洗漱，带了药才出门"}]
    HumanState._enforce_zone_activity(items, "2026-11-16", "jit")
    assert items[0]["activity"] == "一个人裹着被子睡着"


def test_commute_category_cannot_write_office_discussion():
    items = [{"start": "08:00", "end": "09:00", "zone": "road", "category": "commute",
              "activity": "和同事讨论事情，脑子有点转不动"}]
    HumanState._enforce_zone_activity(items, "2026-11-16", "jit")
    assert items[0]["activity"] == "在路上赶路"


def test_valid_commute_and_office_wording_untouched():
    items = [
        {"start": "08:00", "end": "09:00", "zone": "road", "category": "commute",
         "activity": "下班回家"},
        {"start": "10:00", "end": "11:00", "zone": "office", "category": "work",
         "activity": "在工位上忙工作"},
    ]
    HumanState._enforce_zone_activity(items, "2026-11-16", "jit")
    assert items[0]["activity"] == "下班回家"
    assert items[1]["activity"] == "在工位上忙工作"
