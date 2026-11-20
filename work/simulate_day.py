# -*- coding: utf-8 -*-
"""离线一致性仿真：假时钟跑一天，逐条打印 PASS/FAIL。

用法：
    python work/simulate_day.py [--date 2026-11-16] [--city 东莞] [--trip]

不连插件真库、不调真模型：数据库用内存字典/内存 SQLite，LLM 用固定坏文本，
钱包用真实 Wallet 但库在内存里。退出码非 0 表示有不变量失败。
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import datetime as dt
import os
import sqlite3
import sys
import threading

_WORK = os.path.dirname(os.path.abspath(__file__))
if _WORK not in sys.path:
    sys.path.insert(0, _WORK)

import sim_lib  # noqa: E402

ROOT = sim_lib.ROOT
TESTS = sim_lib.TESTS
sim_lib.install_stub()

import amo_human_state  # noqa: E402
import amo_location_rules as loc  # noqa: E402
import amo_shared  # noqa: E402
import amo_transition as tr  # noqa: E402
import amo_trip as trip  # noqa: E402
from amo_schedule import ScheduleMixin  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402
from sim_lib import (  # noqa: E402
    DEFAULT_DAY, FakeDB, FakePlugin, WRONG_TEXT, WrongLLM, at, make_state,
    minute_of, place_category, restore_clock, set_clock, span,
)

def build_day(plugin, state, day, day_type="workday", health=None, meta=None,
              sick_action=""):
    ctx = state.engine.build_context(
        day, dict(meta or {}), {"temp": 22, "text": "晴"},
        {"occupation": "普通上班族", "workplace": "%s·写字楼" % plugin.config["schedule_city"]},
        dict(state.mood), {}, dict(health or {"sick": False}), persona="",
    )
    ctx["event"] = {"kind": "none"}
    ctx["scenic"] = None
    ctx["swim"] = None
    ctx["holiday_plan"] = None
    ctx["day_type"] = {"id": day_type, "label": day_type}
    if sick_action:
        ctx["sick_action"] = str(sick_action)
    items = state.engine.build_skeleton(ctx)
    state.commit_schedule_change(day, items, old_items=[], source="ensure",
                                 reason="audit")
    rows = list(state.schedule.get(day) or plugin.db.get_schedule(day) or [])
    return ctx, rows


def timeline_violations(rows):
    bad = []
    ordered = sorted([row for row in rows if isinstance(row, dict)],
                     key=lambda row: (span(row) or (24 * 60, 24 * 60))[0])
    for row in ordered:
        current = span(row)
        if current is None:
            bad.append("bad_span %r" % (row,))
            continue
        begin, finish = current
        length = finish - begin
        if begin < 0 or finish > 24 * 60:
            bad.append("out_of_day %s-%s" % (row.get("start"), row.get("end")))
        if length <= 1:
            bad.append("fragment %s-%s" % (row.get("start"), row.get("end")))
        if str(row.get("source") or "") == "transit_block" and not (10 <= length <= 40):
            bad.append("transit_duration %s-%s=%d" % (row.get("start"), row.get("end"), length))
    for index, row in enumerate(ordered):
        current = span(row)
        if current is None:
            continue
        for other in ordered[index + 1:]:
            other_span = span(other)
            if other_span is None or other_span[0] >= current[1]:
                break
            if str(row.get("place") or "") != str(other.get("place") or ""):
                bad.append("same_minute_two_places %s/%s" % (row.get("place"), other.get("place")))
            bad.append("overlap %s-%s / %s-%s" % (
                row.get("start"), row.get("end"), other.get("start"), other.get("end")))
    return bad


def place_violations(rows):
    bad = []
    ordered = sorted([row for row in rows if isinstance(row, dict)],
                     key=lambda row: (span(row) or (24 * 60, 24 * 60))[0])
    for first, second in zip(ordered, ordered[1:]):
        first_span, second_span = span(first), span(second)
        if not first_span or not second_span or first_span[1] != second_span[0]:
            continue
        prev_place = str(first.get("place") or "").strip()
        next_place = str(second.get("place") or "").strip()
        if not prev_place or not next_place or prev_place == next_place:
            continue
        if not (tr._is_known_place(prev_place) and tr._is_known_place(next_place)):
            continue
        if tr._is_home_place(prev_place) and tr._is_home_place(next_place):
            continue
        if str(first.get("zone") or "") == "road" or str(second.get("zone") or "") == "road":
            continue
        bad.append("switch_without_road %s-%s %s->%s" % (
            first.get("start"), first.get("end"), prev_place, next_place))
    return bad


def check_repair(day):
    bad = []
    now = at(day, 23, 59)
    normal = [
        {"start": "13:41", "end": "15:41", "place": "医院", "zone": "hospital",
         "activity": "看诊"},
        {"start": "15:41", "end": "16:30", "place": "家", "zone": "home",
         "activity": "瘫着"},
    ]
    repaired, inserted = tr.repair_cross_place_blocks(None, day, normal, now=now)
    if len(inserted) != 1 or inserted[0].get("transit_kind") != "return":
        bad.append("repair_normal_inserted=%d" % len(inserted))
    bad.extend(timeline_violations(repaired))
    bad.extend(place_violations(repaired))
    again, inserted_again = tr.repair_cross_place_blocks(None, day, repaired, now=now)
    if inserted_again:
        bad.append("repair_not_idempotent=%d" % len(inserted_again))
    late = [
        {"start": "21:00", "end": "23:30", "place": "医院", "zone": "hospital",
         "activity": "看诊"},
        {"start": "23:30", "end": "24:00", "place": "家", "zone": "home",
         "activity": "瘫着"},
    ]
    late_rows, late_inserted = tr.repair_cross_place_blocks(None, day, late, now=now)
    if len(late_inserted) > 1:
        bad.append("repair_late_duplicates=%d" % len(late_inserted))
    bad.extend(timeline_violations(late_rows))
    bad.extend(place_violations(late_rows))
    late_again, late_again_inserted = tr.repair_cross_place_blocks(
        None, day, late_rows, now=now)
    if late_again_inserted:
        bad.append("repair_late_not_idempotent=%d" % len(late_again_inserted))
    tight = [
        {"start": "23:50", "end": "23:57", "place": "公司", "zone": "office",
         "activity": "加班"},
        {"start": "23:57", "end": "24:00", "place": "家", "zone": "home",
         "activity": "瘫着"},
    ]
    tight_rows, _tight_inserted = tr.insert_transit_blocks(day, tight, old_items=[], now=now)
    bad.extend(timeline_violations(tight_rows))
    return bad


def check_chat(plugin, state, rows, day):
    bad = []
    for clock_minute in range(9 * 60, 23 * 60 + 1, 10):
        fixed = at(day, clock_minute // 60, clock_minute % 60)
        set_clock(fixed)
        info, reason = state.current_activity_info()
        covering = [row for row in rows
                    if (span(row) or (0, 0))[0] <= clock_minute < (span(row) or (0, 0))[1]]
        if not covering:
            bad.append("no_covering_block %02d:%02d" % (clock_minute // 60, clock_minute % 60))
            continue
        current = covering[0]
        if place_category(info) != place_category(current):
            bad.append("chat_place %02d:%02d %s != %s (%s)" % (
                clock_minute // 60, clock_minute % 60,
                place_category(info), place_category(current), reason))
        if str(current.get("zone") or "") == "road" or str(current.get("place") or "") == "路上":
            guarded = tr.guard_road_home(plugin, "我到家了，瘫在沙发上", fixed)
            if any(word in guarded for word in ("沙发", "到家了")):
                bad.append("road_guard_failed %02d:%02d %s" % (
                    clock_minute // 60, clock_minute % 60, guarded))
    set_clock(at(day, 23, 0))
    return bad


def wrong_text_for(item):
    return WRONG_TEXT.get(place_category(item), WRONG_TEXT["home"])


def check_small_diary(state, ctx, rows):
    bad = []
    base_ctx = dict(ctx)
    base_ctx.update({"fact_pool": [], "chat_lines_hour": [], "worn": "",
                     "assets": "", "outfit": "", "gift_log": ""})
    hourly = state._hourly_schedule(rows)
    for item in hourly:
        if not (7 <= minute_of(item.get("start")) // 60 <= 22):
            continue
        llm = WrongLLM(wrong_text_for(item))
        result = asyncio.run(state.engine.generate_diary(
            "", base_ctx, item, None, None, llm))
        text = str(result.get("diary") or "")
        violations = loc.check(text, item.get("place"), item.get("zone"))
        if violations:
            bad.append("diary_place %s %s %s" % (
                item.get("start"), loc.resolve(str(item.get("place") or ""),
                                               str(item.get("zone") or "")),
                "、".join(violations)))
        if not result.get("fallback") and loc.check(text, item.get("place"), item.get("zone")):
            bad.append("diary_not_guarded %s" % item.get("start"))
    return bad


def check_daily_diary():
    bad = []
    mixin = ScheduleMixin()
    mixin.llm = WrongLLM("今天去了商场试衣间，还在工位上坐了一会儿，回家被窝特别舒服。")
    pool = {"meta": {"places": ["公司", "家"], "location": "公司",
                     "location_rules_enabled": True}}
    text = "今天去了商场试衣间，还在工位上坐了一会儿，回家被窝特别舒服。"
    value, fallback = asyncio.run(mixin._guard_daily_location(
        text, pool, "", "system", "prompt", False))
    if loc.day_check(value, ["公司", "家"], True):
        bad.append("daily_place %s" % "、".join(loc.day_check(value, ["公司", "家"], True)))
    if not fallback:
        bad.append("daily_not_fallback")
    if pool["meta"].get("location_fallback") is not True:
        bad.append("daily_meta_missing")
    return bad


def check_money():
    bad = []
    plugin = FakePlugin()
    plugin.wallet.credit(100000, "初始化", "audit:init")
    info = trip.new_trip("杭州", DEFAULT_DAY, 2)
    before = plugin.wallet.balance()
    first = trip.travel_spend(plugin, info, 100, "晚饭", "audit:food",
                              category="food", balance=None)
    duplicate = trip.travel_spend(plugin, info, 100, "晚饭", "audit:food",
                                  category="food", balance=None)
    hotel = trip.travel_spend(plugin, info, 100, "酒店", "audit:hotel",
                              category="hotel", balance=None)
    if not first.get("ok") or first.get("amount") != 160:
        bad.append("food_premium %r" % (first,))
    if duplicate.get("reason") != "duplicate":
        bad.append("idem_duplicate %r" % (duplicate,))
    if hotel.get("amount") != 100:
        bad.append("hotel_premium %r" % (hotel,))
    if plugin.wallet.balance() != before - 260:
        bad.append("balance_after %d" % plugin.wallet.balance())
    entries = plugin.wallet.ledger(50)
    idems = [entry["idem"] for entry in entries]
    if len(idems) != len(set(idems)):
        bad.append("ledger_duplicate_idem")
    if plugin.wallet.balance() < 0:
        bad.append("negative_balance")
    for category in ("salary", "bill", "mortgage", "utility", "fund"):
        result = trip.travel_spend(plugin, info, 100, "义务", "audit:" + category,
                                   category=category, balance=None)
        if result.get("reason") != "exempt":
            bad.append("exempt_%s %r" % (category, result))
        if trip.price(plugin, category, 100) != 100:
            bad.append("premium_%s" % category)
    for category in ("food", "transport", "daily"):
        if trip.price(plugin, category, 100) != 160:
            bad.append("premium_missing_%s" % category)
    return bad


def check_social(plugin, state, rows, day):
    bad = []
    work_rows = [row for row in rows
                 if str(row.get("category") or "") == "work"
                 and str(row.get("zone") or "") == "office"]
    if len(work_rows) < 3:
        bad.append("workday_no_work %d" % len(work_rows))
    for row in rows:
        begin = minute_of(row.get("start"))
        if 22 * 60 <= begin or begin < 6 * 60:
            category = place_category(row)
            if category in ("mall", "office", "restaurant"):
                bad.append("late_night_place %s %s" % (row.get("start"), category))
    _sick_ctx, sick_rows = build_day(plugin, state, day,
                                     health={"sick": True, "status": "感冒"},
                                     day_type="workday", sick_action="take_sick_leave")
    if any(str(row.get("zone") or "") == "office" for row in sick_rows):
        bad.append("sick_leave_has_office")
    if any(str(row.get("category") or "") == "work" for row in sick_rows):
        bad.append("sick_leave_has_work")
    for hour in (22, 23):
        plan = trip.travel_event_for_hour(plugin, day, hour)
        if str(plan.get("zone") or "") in ("mall", "office", "restaurant"):
            bad.append("trip_late_night_zone %02d %s" % (hour, plan.get("zone")))
    return bad


def check_trip(day):
    bad = []
    plugin, state = make_state(day=day)
    plugin.config["travel_days_max"] = 3
    plugin.wallet.credit(800000, "初始化", "audit:trip:init")
    set_clock(at(day, 9, 0))
    started = trip.trip_manual_start(plugin, city="杭州", days=3, force=True,
                                     now_day=day, now=at(day, 9, 0), balance=8000)
    if not started.get("ok"):
        return ["trip_start %r" % (started,)]
    outbound = [row for row in state.schedule.get(day, [])
                if str(row.get("leg") or "") == "outbound"]
    total = sum((span(row) or (0, 0))[1] - (span(row) or (0, 0))[0] for row in outbound)
    if not (180 <= total <= 300):
        bad.append("outbound_total=%d" % total)
    day2 = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    set_clock(at(day2, 9, 0))
    trip.sync(plugin, now=at(day2, 9, 0))
    if trip.day_kind(plugin, day2) != "traveling":
        bad.append("day_kind=%s" % trip.day_kind(plugin, day2))
    pool = state._trip_day_items(day2, "traveling")
    if not (3 <= len(pool) <= 5):
        bad.append("travel_pool_count=%d" % len(pool))
    for row in pool:
        text = str(row.get("activity") or "")
        if any(word in text for word in ("上班", "工位", "洗衣", "做饭")):
            bad.append("travel_pool_work %s" % text)
    plan, cost, paid = state._jit_plan_for_hour(at(day2, 10, 0), 10, day2)
    if any(word in str(plan.get("action") or "") for word in ("上班", "工位", "洗衣", "做饭")):
        bad.append("travel_jit_work %s" % plan.get("action"))
    plan_night, _cost, _paid = state._jit_plan_for_hour(at(day2, 22, 0), 22, day2)
    if str(plan_night.get("zone") or "") in ("mall", "office", "restaurant"):
        bad.append("travel_jit_late_night %s" % plan_night.get("zone"))
    before = copy.deepcopy(state.schedule.get(day2))
    guard = trip.guard_change(plugin, "改去大理")
    if guard.get("action") != "blocked":
        bad.append("destination_guard=%s" % guard.get("action"))
    result = state.commit_schedule_change(
        day2, [{"start": "10:00", "end": "11:00", "activity": "去大理",
                "place": "大理", "zone": "scenic", "category": "play"}],
        old_items=before, source="chat", reason="改去大理")
    if not result.get("blocked") or state.schedule.get(day2) != before:
        bad.append("destination_change_not_blocked")
    info = trip.new_trip("杭州", day, 3)
    spent = trip.travel_spend(plugin, info, 100, "晚饭", "audit:trip:food",
                              category="food", balance=None)
    if not spent.get("ok") or spent.get("amount") != 160:
        bad.append("trip_spend %r" % (spent,))
    duplicate = trip.travel_spend(plugin, info, 100, "晚饭", "audit:trip:food",
                                  category="food", balance=None)
    if duplicate.get("reason") != "duplicate":
        bad.append("trip_spend_duplicate %r" % (duplicate,))
    stranded = trip.check_stranded(plugin, balance=100, trip=info)
    if not stranded.get("stranded"):
        bad.append("stranded_missing")
    return bad


def check_trip_finish(day):
    bad = []
    plugin, state = make_state(day=day)
    set_clock(at(day, 9, 0))
    started = trip.trip_manual_start(plugin, city="杭州", days=2, force=True,
                                     now_day=day, now=at(day, 9, 0), balance=8000)
    if not started.get("ok"):
        return ["trip_finish_start %r" % (started,)]
    return_day = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    set_clock(at(return_day, 16, 0))
    returned = trip.begin_return(plugin, started["trip"], now=at(return_day, 16, 0))
    if returned.get("phase") != "returning":
        bad.append("begin_return=%s" % returned.get("phase"))
    set_clock(at(return_day, 22, 0))
    finished = trip.sync(plugin, now=at(return_day, 22, 0))
    if finished.get("phase") != "rest":
        bad.append("finish_phase=%s" % finished.get("phase"))
    rest_day = finished.get("rest_day")
    set_clock(at(rest_day, 9, 0))
    rest_rows = state._trip_day_items(rest_day, "rest") or state.schedule.get(rest_day) or []
    rest = [row for row in rest_rows if row.get("activity") == trip.REST_DAY_ACTIVITY]
    if len(rest) != 1:
        bad.append("rest_day_blocks=%d" % len(rest))
    if rest and (rest[0].get("start"), rest[0].get("end")) != ("08:00", "20:00"):
        bad.append("rest_day_span=%s-%s" % (rest[0].get("start"), rest[0].get("end")))
    asyncio.run(trip.finalize_async(plugin, finished))
    asyncio.run(trip.finalize_async(plugin, finished))
    memory = [call for call in plugin.memory_calls if call.get("kind") == "activity"]
    facts = [call for call in plugin.memory_calls if call.get("kind") == "fact"]
    if len(memory) != 1 or len(facts) != 1:
        bad.append("memory_calls=%d/%d" % (len(memory), len(facts)))
    if memory and (memory[0].get("importance") != 0.9 or memory[0].get("tags") != trip.MEMORY_TAG):
        bad.append("memory_params %r" % (memory[0],))
    log = plugin.db.kv.get(trip.TRAVEL_LOG_KEY) or []
    if len(log) != 1:
        bad.append("travel_log=%d" % len(log))
    first_rows = copy.deepcopy(state.schedule.get(day))
    set_clock(at(day, 12, 0))
    trip.load(plugin)
    trip.load(plugin)
    if state.schedule.get(day) != first_rows:
        bad.append("reload_changed_rows")
    return bad


def run(day=DEFAULT_DAY, city="东莞", with_trip=False):
    checks = []
    plugin, state = make_state(city=city, day=day)
    set_clock(at(day, 9, 0))
    ctx, rows = build_day(plugin, state, day)
    checks.append(("timeline.adjacency", timeline_violations(rows)))
    checks.append(("place.physics", place_violations(rows)))
    checks.append(("place.repair", check_repair(day)))
    checks.append(("chat.now", check_chat(plugin, state, rows, day)))
    checks.append(("diary.small", check_small_diary(state, ctx, rows)))
    checks.append(("diary.daily", check_daily_diary()))
    checks.append(("money.day", check_money()))
    checks.append(("social.logic", check_social(plugin, state, rows, day)))
    if with_trip:
        checks.append(("trip.day", check_trip(day)))
        checks.append(("trip.finish", check_trip_finish(day)))
    failed = 0
    for name, violations in checks:
        if violations:
            failed += 1
            print("[FAIL] %s: %d 条" % (name, len(violations)))
            for item in violations[:12]:
                print("       - %s" % item)
        else:
            print("[PASS] %s" % name)
    print("== %d/%d PASS ==" % (len(checks) - failed, len(checks)))
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线一致性仿真（假时钟，不连真库）")
    parser.add_argument("--date", default=DEFAULT_DAY)
    parser.add_argument("--city", default="东莞")
    parser.add_argument("--trip", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(args.date, args.city, args.trip)
    finally:
        restore_clock()


if __name__ == "__main__":
    sys.exit(main())