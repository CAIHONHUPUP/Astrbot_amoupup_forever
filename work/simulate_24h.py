# -*- coding: utf-8 -*-
"""24H 全流程仿真：逐小时 JIT/ensure、聊天此刻、小日记、照片、钱、3 天旅游、重载。

用法：
    python work/simulate_24h.py

复用 work/simulate_day.py 的 FakeDB/FakePlugin/假时钟/断言工具；不连真库、不调真模型。
每条检查打印 PASS/FAIL + 计数；任一失败退出码 1。
"""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import os
import sys
import time

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
import photo_rules  # noqa: E402
import photo_scene  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402
import work.simulate_day as sim  # noqa: E402

DAY0 = "2026-11-16"
DAY1 = "2026-11-17"
DAY2 = "2026-11-18"
DAY3 = "2026-11-19"
DAY4 = "2026-11-20"
#: 旅游单独用 11-19 出发：day1/day2 池子固定含 hotel/meal/ticket/daily，
#: 不改变 24h 仿真日 11-16。
TRIP_START = "2026-11-19"
TRIP_DAY1 = "2026-11-20"
TRIP_DAY2 = "2026-11-21"
_TIME_ORIG = None


def set_clock(fixed):
    global _TIME_ORIG
    if _TIME_ORIG is None:
        _TIME_ORIG = time.time
    sim_lib.set_clock(fixed)
    time.time = lambda: fixed.timestamp()


def restore_clock():
    global _TIME_ORIG
    if _TIME_ORIG is not None:
        time.time = _TIME_ORIG
        _TIME_ORIG = None
    sim_lib.restore_clock()


def at(day, hour, minute=0):
    return sim_lib.at(day, hour, minute)


def make_plugin(day=DAY0):
    return sim_lib.make_plugin(day)


def rebuild(plugin, day):
    """保存 kv/DB 后重建插件：共享同一个 FakeDB，只重建内存对象。"""
    return sim_lib.rebuild(plugin, day)


def rows_of(plugin, state, day):
    return list(state.schedule.get(day) or plugin.db.get_schedule(day) or [])


def covering(rows, minute):
    found = []
    for row in rows:
        current = sim.span(row)
        if current and current[0] <= minute < current[1]:
            found.append(row)
    return found


def contiguous_until(rows, expected_end):
    bad = []
    spans = sorted(current for current in (sim.span(row) for row in rows) if current)
    cursor = 0
    for begin, finish in spans:
        if begin > cursor:
            bad.append("gap %d-%d" % (cursor, begin))
        if begin < cursor:
            bad.append("overlap at %d" % begin)
        cursor = max(cursor, finish)
    if cursor != expected_end:
        bad.append("end %d != %d" % (cursor, expected_end))
    return bad


def diary_context(state, day):
    ctx = state.engine.build_context(
        day, dict(amo_shared.day_meta(day) or {}), {"temp": 22, "text": "晴"},
        {"occupation": "普通上班族", "workplace": "东莞·写字楼"},
        dict(state.mood), {}, {"sick": False}, persona="",
    )
    ctx.update({"event": {"kind": "none"}, "scenic": None, "holiday_plan": None,
                "swim": None, "day_type": {"id": "workday", "label": "工作日"},
                "fact_pool": [], "chat_lines_hour": [], "worn": "", "assets": "",
                "outfit": "", "gift_log": "", "location": "东莞"})
    return ctx


def build_jit_day():
    """00:00 种子睡眠 + 07:00-23:00 逐小时 JIT，返回 (plugin,state,rows,ctx,report)。"""
    plugin, state = make_plugin(DAY0)
    set_clock(at(DAY0, 0, 0))
    state.commit_schedule_change(
        DAY0,
        [{"start": "00:00", "end": "07:00", "activity": "睡觉", "place": "家",
          "zone": "home", "category": "sleep", "source": "audit_seed"}],
        old_items=[], source="audit_seed", reason="24h seed sleep",
    )
    plugin.db.kv_set("current_action", {})
    report = {"hours": [], "jit_calls": 0, "writes": 0, "seed": 1,
              "chat_bad": [], "chat_samples": 0, "chat_road": 0, "chat_rewritten": 0}
    violations = []
    for hour in range(24):
        fixed = at(DAY0, hour, 0)
        set_clock(fixed)
        # 逐小时 tick 没有“漏掉的心跳”，不让 recover 抢先补 jit_recover 块
        plugin.db.kv_set("jit:heartbeat", fixed.timestamp())
        item = state.jit_tick(fixed)
        report["jit_calls"] += 1
        if item:
            report["writes"] += 1
        rows = rows_of(plugin, state, DAY0)
        expected_end = 7 * 60 if hour < 7 else (hour + 1) * 60
        bad = sim.timeline_violations(rows)
        bad += sim.place_violations(rows)
        bad += contiguous_until(rows, expected_end)
        current = covering(rows, hour * 60)
        if not current:
            bad.append("no_block_at_%02d:00" % hour)
        else:
            report["hours"].append((hour, current[0].get("place"),
                                    current[0].get("activity"),
                                    current[0].get("zone")))
            info, reason = state.current_activity_info()
            report["chat_samples"] += 1
            if sim.place_category(info) != sim.place_category(current[0]):
                report["chat_bad"].append("hour %02d chat=%s block=%s (%s)" % (
                    hour, sim.place_category(info), sim.place_category(current[0]), reason))
            block_zone = str(current[0].get("zone") or "")
            chat_zone = str(info.get("zone") or "")
            if block_zone and chat_zone and block_zone != chat_zone:
                report["chat_bad"].append("hour %02d chat_zone=%s block_zone=%s" % (
                    hour, chat_zone, block_zone))
            if str(current[0].get("zone") or "") == "road":
                report["chat_road"] += 1
                guarded = tr.guard_road_home(state.p, "我到家了，瘫在沙发上", fixed)
                if "沙发" in guarded or "到家了" in guarded:
                    report["chat_bad"].append("hour %02d road guard failed: %s" % (hour, guarded))
                else:
                    report["chat_rewritten"] += 1
            elif hour in (19, 20):
                left = tr.guard_road_home(state.p, "我到家了，瘫在沙发上", fixed)
                if left != "我到家了，瘫在沙发上":
                    report["chat_bad"].append("hour %02d home guard still rewrites" % hour)
        if bad:
            violations.append("hour=%02d %s" % (hour, "; ".join(bad[:4])))
        if hour == 23:
            sleep = [row for row in rows if str(row.get("start") or "") == "23:00"]
            if not sleep:
                violations.append("23:00 no sleep block")
            else:
                row = sleep[0]
                if str(row.get("category") or "") != "sleep":
                    violations.append("23:00 category=%s" % row.get("category"))
                if not row.get("open_sleep"):
                    violations.append("23:00 open_sleep missing")
            if not plugin.db.kv_get("sleep_open", {}):
                violations.append("sleep_open kv missing")
    report["violations"] = violations
    return plugin, state, rows_of(plugin, state, DAY0), diary_context(state, DAY0), report


def check_sleep_merge(plugin, state):
    bad = []
    set_clock(at(DAY1, 0, 0))
    state.jit_tick(at(DAY1, 0, 0))
    next_rows = rows_of(plugin, state, DAY1)
    before = [row for row in next_rows if str(row.get("start") or "") == "00:00"]
    if before:
        bad.append("next 00:00 duplicated before wake: %d" % len(before))
    set_clock(at(DAY1, 7, 0))
    state.jit_tick(at(DAY1, 7, 0))
    prev_rows = rows_of(plugin, state, DAY0)
    merged = [row for row in prev_rows if str(row.get("start") or "") == "23:00"]
    zeros = [row for row in rows_of(plugin, state, DAY1)
             if str(row.get("start") or "") == "00:00"]
    if not merged:
        # 允许生产把上一晚 23:00 折进次日 00:00 睡眠（sleep_from=23:00）
        if not zeros or str(zeros[0].get("sleep_from") or "") != "23:00":
            bad.append("23:00 not merged into next sleep")
    else:
        row = merged[0]
        if str(row.get("category") or "") != "sleep":
            bad.append("merged category=%s" % row.get("category"))
        if str(row.get("sleep_until") or "") != "07:00":
            bad.append("sleep_until=%s" % row.get("sleep_until"))
        if row.get("open_sleep"):
            bad.append("open_sleep not cleared")
    next_rows = rows_of(plugin, state, DAY1)
    zeros = [row for row in next_rows if str(row.get("start") or "") == "00:00"]
    if len(zeros) != 1:
        bad.append("next 00:00 count=%d" % len(zeros))
    if zeros and (zeros[0].get("end"), zeros[0].get("category")) != ("07:00", "sleep"):
        bad.append("next sleep=%s-%s/%s" % (zeros[0].get("start"),
                                            zeros[0].get("end"),
                                            zeros[0].get("category")))
    set_clock(at(DAY1, 7, 5))
    state.jit_tick(at(DAY1, 7, 5))
    zeros_again = [row for row in rows_of(plugin, state, DAY1)
                   if str(row.get("start") or "") == "00:00"]
    if len(zeros_again) != 1:
        bad.append("second tick duplicated 00:00: %d" % len(zeros_again))
    return {"violations": bad, "merged": len(zeros or []), "next_blocks": len(next_rows)}


def check_returns():
    bad = []
    checked = 0
    for place in ("医院", "公司", "商场"):
        rows = [
            {"start": "13:41", "end": "15:41", "place": place, "zone": "hospital" if place == "医院" else ("office" if place == "公司" else "mall"),
             "activity": "在%s" % place},
            {"start": "15:41", "end": "16:30", "place": "家", "zone": "home",
             "activity": "回家瘫着"},
        ]
        repaired, inserted = tr.repair_cross_place_blocks(
            None, DAY0, rows, now=at(DAY0, 23, 59))
        checked += 1
        if len(inserted) != 1 or str(inserted[0].get("transit_kind") or "") != "return":
            bad.append("%s return inserted=%d" % (place, len(inserted)))
            continue
        length = (sim.span(inserted[0]) or (0, 0))[1] - (sim.span(inserted[0]) or (0, 0))[0]
        if not (10 <= length <= 40):
            bad.append("%s return minutes=%d" % (place, length))
        home = [row for row in repaired if row.get("place") == "家"]
        if not home or home[0].get("start") != inserted[0].get("end"):
            bad.append("%s home not shifted" % place)
        bad.extend("%s timeline %s" % (place, item) for item in sim.timeline_violations(repaired)[:2])
        bad.extend("%s place %s" % (place, item) for item in sim.place_violations(repaired)[:2])
    return {"violations": bad, "checked": checked}


def check_chat(report):
    return {"violations": report["chat_bad"], "samples": report["chat_samples"],
            "road": report["chat_road"], "rewritten": report["chat_rewritten"]}


class PlainLLM:
    def __init__(self, text):
        self.text = str(text)
        self.calls = 0

    async def chat_raw(self, umo, system, prompt):
        self.calls += 1
        return self.text


def check_diary(state, ctx, rows):
    bad = []
    hourly = state._hourly_schedule(rows)
    fallback = 0
    checked = 0
    for item in hourly:
        llm = sim.WrongLLM(sim.wrong_text_for(item))
        result = asyncio.run(state.engine.generate_diary(
            "", dict(ctx), item, None, None, llm))
        text = str(result.get("diary") or "")
        checked += 1
        if result.get("fallback"):
            fallback += 1
        violations = loc.check(text, item.get("place"), item.get("zone"))
        if violations:
            bad.append("hour %s violations=%s" % (item.get("start"),
                                                  "、".join(violations)))
    synthetic = [
        {"start": "10:00", "end": "11:00", "activity": "在医院排队看诊",
         "place": "医院", "zone": "hospital", "category": "health"},
        {"start": "14:00", "end": "15:00", "activity": "在商场逛街",
         "place": "商场", "zone": "mall", "category": "play"},
    ]
    for item in synthetic:
        llm = sim.WrongLLM(sim.WRONG_TEXT[sim.place_category(item)])
        result = asyncio.run(state.engine.generate_diary(
            "", dict(ctx), item, None, None, llm))
        checked += 1
        if result.get("fallback"):
            fallback += 1
        text = str(result.get("diary") or "")
        violations = loc.check(text, item.get("place"), item.get("zone"))
        if violations:
            bad.append("synthetic %s violations=%s" % (
                sim.place_category(item), "、".join(violations)))
    return {"violations": bad, "checked": checked, "fallback": fallback}


def check_daily_diary():
    bad = []
    wrong = sim.check_daily_diary()
    if wrong:
        bad.extend("wrong-day %s" % item for item in wrong)
    mixin = sim.ScheduleMixin()
    text = "今天去医院排队挂号，回来在家躺了一会儿，医院人真多。"
    mixin.llm = PlainLLM(text)
    pool = {"meta": {"places": ["医院", "家"], "location": "医院",
                     "location_rules_enabled": True}}
    value, fallback = asyncio.run(mixin._guard_daily_location(
        text, pool, "", "system", "prompt", False))
    if "医院" not in value:
        bad.append("hospital removed: %s" % value)
    if fallback:
        bad.append("hospital text was fallback")
    if loc.day_check(value, ["医院", "家"], True):
        bad.append("hospital-in-places still violates")
    if not loc.day_check(text, ["公司", "家"], True):
        bad.append("hospital word not caught when not visited")
    return {"violations": bad, "checked": 2}


def check_photo(state, rows):
    bad = []
    checked = 0
    home_pose = 0
    for item in state._hourly_schedule(rows):
        plan = photo_rules.resolve_plan(item)
        checked += 1
        zone = sim.place_category(item)
        if photo_scene.zone_of(item) != plan.base_zone:
            bad.append("hour %s scene zone=%s plan base=%s" % (
                item.get("start"), photo_scene.zone_of(item), plan.base_zone))
        if zone in ("hospital", "office", "mall"):
            if str(plan.zone or "").startswith("home_"):
                bad.append("hour %s public place got %s" % (item.get("start"), plan.zone))
            if "bedding" not in tuple(plan.banned or ()):
                bad.append("hour %s public plan allows bedding" % item.get("start"))
            if "床" in str(plan.scene) or "被窝" in str(plan.scene):
                bad.append("hour %s public scene has bed: %s" % (item.get("start"), plan.scene))
            if plan.allow_library_pose:
                bad.append("hour %s public plan allows library pose" % item.get("start"))
        if str(plan.zone or "").startswith("home_"):
            home_pose += 1
    synthetic = [
        {"activity": "在医院排队看诊", "place": "医院", "zone": "hospital",
         "category": "health"},
        {"activity": "在商场逛街", "place": "商场", "zone": "mall",
         "category": "play"},
        {"activity": "躺在床上", "place": "家", "zone": "home",
         "category": "sleep"},
    ]
    for item in synthetic:
        plan = photo_rules.resolve_plan(item)
        checked += 1
        zone = sim.place_category(item)
        if zone == "home":
            if plan.zone != "home_bed":
                bad.append("home bed got %s" % plan.zone)
            if not plan.allow_library_pose:
                bad.append("home bed does not allow library pose")
            if "床" not in str(plan.scene):
                bad.append("home bed scene no bed")
        else:
            if plan.zone in ("home_bed", "home_sofa", "home_generic"):
                bad.append("%s got %s" % (zone, plan.zone))
            if "bedding" not in tuple(plan.banned or ()):
                bad.append("%s does not ban bedding" % zone)
            if plan.allow_library_pose:
                bad.append("%s allows library pose" % zone)
    return {"violations": bad, "checked": checked, "home_pose_hours": home_pose}


def check_money_24h():
    bad = []
    plugin, _state = make_plugin(DAY0)
    plugin.wallet.credit(100000, "初始化", "audit:init")
    info = trip.new_trip("杭州", DAY0, 2)
    categories = ("food", "daily", "transport")
    spends = 0
    duplicates = 0
    for hour in range(24):
        set_clock(at(DAY0, hour, 0))
        category = categories[hour % 3]
        result = trip.travel_spend(plugin, info, 100, "日常", "h%02d" % hour,
                                   category=category, balance=None)
        if isinstance(result.get("trip"), dict):
            info = result["trip"]
        spends += 1
        if not result.get("ok"):
            bad.append("hour %02d spend %s" % (hour, result.get("reason")))
        if plugin.wallet.balance() < 0:
            bad.append("hour %02d negative" % hour)
        if hour == 5:
            duplicate = trip.travel_spend(plugin, info, 100, "日常", "h05",
                                          category=category, balance=None)
            spends += 1
            if duplicate.get("reason") == "duplicate":
                duplicates += 1
            else:
                bad.append("hour 05 duplicate=%s" % duplicate.get("reason"))
    set_clock(at("2026-11-15", 9, 0))
    if at("2026-11-15", 9, 0).day != 15:
        bad.append("salary date wrong")
    salary_exempt = trip.travel_spend(plugin, info, 100, "工资", "finance:salary",
                                      category="salary", balance=None)
    if salary_exempt.get("reason") != "exempt":
        bad.append("salary not exempt: %s" % salary_exempt.get("reason"))
    if not plugin.wallet.credit(50000, "工资", "audit:salary"):
        bad.append("salary credit failed")
    set_clock(at("2026-11-01", 9, 0))
    if at("2026-11-01", 9, 0).day != 1:
        bad.append("bill date wrong")
    bill_exempt = trip.travel_spend(plugin, info, 100, "账单", "finance:bill",
                                    category="bill", balance=None)
    if bill_exempt.get("reason") != "exempt":
        bad.append("bill not exempt: %s" % bill_exempt.get("reason"))
    if not plugin.wallet.debit(2000, "账单", "audit:bill"):
        bad.append("bill debit failed")
    if trip.price(plugin, "salary", 100) != 100 or trip.price(plugin, "bill", 100) != 100:
        bad.append("finance got travel premium")
    entries = plugin.wallet.ledger(100)
    idems = [entry["idem"] for entry in entries]
    if len(idems) != len(set(idems)):
        bad.append("duplicate ledger idem")
    if plugin.wallet.balance() < 0:
        bad.append("negative balance at end")
    loaded = plugin.db.kv_get("trip", {}) or {}
    travel_debits = sum(-entry["delta_cents"] for entry in entries
                        if entry.get("fund_source") == "travel"
                        and int(entry.get("delta_cents") or 0) < 0)
    if int(loaded.get("spent") or 0) != travel_debits:
        bad.append("spent %s != ledger %s" % (loaded.get("spent"), travel_debits))
    return {"violations": bad, "spends": spends, "unique_idems": len(set(idems)),
            "duplicates": duplicates, "balance": plugin.wallet.balance(),
            "travel_spent": travel_debits}


def _legs(plugin, state, day, kind):
    return [row for row in rows_of(plugin, state, day)
            if str(row.get("leg") or "") == kind]


def _leg_total(rows):
    return sum((sim.span(row) or (0, 0))[1] - (sim.span(row) or (0, 0))[0]
               for row in rows)


def _trip_common(bad):
    DAY0 = TRIP_START
    DAY1 = TRIP_DAY1
    plugin, state = make_plugin(DAY0)
    plugin.wallet.credit(800000, "初始化", "audit:trip:init")
    set_clock(at(DAY0, 23, 10))
    started = trip.trip_manual_start(plugin, city="三亚", days=3, force=True,
                                     now_day=DAY0, now=at(DAY0, 23, 10), balance=8000)
    if not started.get("ok"):
        bad.append("manual_start %s" % started.get("reason"))
        return None
    raw = dict(plugin.db.kv_get("trip", {}) or {})
    outbound_ts = int(raw.get("outbound_ts") or 0)
    start_ts = int(at(DAY0, 23, 10).timestamp())
    outbound_minutes = (outbound_ts - start_ts) // 60
    outbound_rows = _legs(plugin, state, DAY0, "outbound")
    if raw.get("phase") != "outbound":
        bad.append("start phase=%s" % raw.get("phase"))
    if not outbound_rows:
        bad.append("no outbound blocks")
    midnight = at(DAY1, 0, 0).timestamp()
    if outbound_ts <= int(midnight):
        bad.append("outbound_ts not after midnight: %s" % outbound_ts)
    if not (180 <= outbound_minutes <= 300):
        bad.append("outbound minutes=%d" % outbound_minutes)
    if outbound_rows and str(outbound_rows[-1].get("end") or "") != "24:00":
        bad.append("outbound day0 not cut at midnight: %s" % outbound_rows[-1].get("end"))
    set_clock(at(DAY1, 0, 0))
    pre = trip.load(plugin)
    if pre.get("phase") != "outbound":
        bad.append("day1 00 phase=%s" % pre.get("phase"))
    if sim.timeline_violations(rows_of(plugin, state, DAY0)):
        bad.append("outbound timeline")
    if sim.place_violations(rows_of(plugin, state, DAY0)):
        bad.append("outbound place physics")
    return plugin, state, started["trip"], raw, outbound_ts, outbound_minutes


def check_trip_flow():
    bad = []
    day0, day1, day2, day3 = _trip_days()
    common = _trip_common(bad)
    if common is None:
        return {"violations": bad, "stages": 0}
    plugin, state, info, raw, outbound_ts, outbound_minutes = common
    arrival = dt.datetime.fromtimestamp(outbound_ts)
    _check_outbound_reload(bad, plugin, state, day0)
    _check_arrival(bad, plugin, day1, arrival)
    future_idems = _check_day1_pool(bad, plugin, state, day1)
    _check_traveling_reload(bad, plugin, state, day1)
    _check_mid_trip_change(bad, plugin, state, day1)
    _check_day2_pool(bad, plugin, state, day2)
    second_settle = _check_settle_due(bad, plugin, day1)
    return_day, return_rows, total_return, rest_expected, done_expected = \
        _check_return_split(bad, plugin, state, day1, day2, day3)
    _check_returning_reload(bad, plugin, state, return_day)
    rest_day, rest_rows = _check_finish_rest(bad, plugin, state)
    memory, facts = _check_memory_calls(bad, plugin)
    _check_rest_reload(bad, plugin, rest_day, rest_rows)
    _check_done(bad, plugin, rest_day, rest_expected, done_expected)
    travel = _check_spent(bad, plugin)
    return {"violations": bad, "stages": 4,
            "outbound_ts": dt.datetime.fromtimestamp(outbound_ts).strftime("%m-%d %H:%M"),
            "outbound_minutes": outbound_minutes,
            "return_legs": len(return_rows), "return_total": total_return,
            "rest_day": rest_day, "travel_spent": travel["travel_debits"],
            "balance": plugin.wallet.balance(),
            "hotel": travel["hotel"], "meal": travel["meal"], "ticket": travel["ticket"],
            "daily": travel["daily"], "future": len(future_idems),
            "settle_dup": len(second_settle.get("duplicate") or []),
            "memory": len(memory), "facts": len(facts), "reloads": 4}


def _trip_days():
    """三天的日期常量：出游日 / 第 1 天 / 第 2 天 / 第 3 天。"""
    day0 = TRIP_START
    day1 = TRIP_DAY1
    day2 = TRIP_DAY2
    day3 = (dt.date.fromisoformat(day2) + dt.timedelta(days=1)).isoformat()
    return day0, day1, day2, day3


def _check_outbound_reload(bad, plugin, state, day0):
    """1) 出站阶段重载：行、余额、阶段都不能变。"""
    outbound_rows = rows_of(plugin, state, day0)
    balance0 = plugin.wallet.balance()
    p2 = rebuild(plugin, day0)
    t2 = trip.load(p2)
    if t2.get("phase") != "outbound":
        bad.append("reload outbound phase=%s" % t2.get("phase"))
    if rows_of(p2, p2.state, day0) != outbound_rows:
        bad.append("reload outbound rows changed")
    if p2.wallet.balance() != balance0:
        bad.append("reload outbound balance changed")


def _check_arrival(bad, plugin, day1, arrival):
    """2) 落地后进入 traveling，第二天按「旅行日」算。"""
    set_clock(arrival + dt.timedelta(minutes=1))
    advanced = trip.sync(plugin, now=arrival + dt.timedelta(minutes=1))
    if advanced.get("phase") != "traveling":
        bad.append("arrival phase=%s" % advanced.get("phase"))
    if trip.day_kind(plugin, day1) != "traveling":
        bad.append("day_kind day1=%s" % trip.day_kind(plugin, day1))


def _check_day1_pool(bad, plugin, state, day1):
    """3) 第一天旅行池 + 逐小时 tick；返回尚未扣费的未来 idem 列表。"""
    set_clock(at(day1, 7, 0))
    pool1_rows = state._trip_day_items(day1, "traveling")
    pool1 = [row for row in pool1_rows if row.get("source") == "trip_pool"]
    if not (3 <= len(pool1) <= 5):
        bad.append("day1 pool=%d" % len(pool1))
    for row in rows_of(plugin, state, day1):
        text = str(row.get("activity") or "")
        if any(word in text for word in ("上班", "工位", "洗衣", "做饭")):
            bad.append("day1 work block %s" % text)
    future_summary = trip.settle_due(plugin, now=at(day1, 7, 0))
    future_idems = list(future_summary.get("future") or [])
    known_idems = {entry["idem"] for entry in plugin.wallet.ledger(300)}
    if not future_idems:
        bad.append("no future rows at day1 07")
    for idem in future_idems:
        if idem in known_idems:
            bad.append("future idem already charged: %s" % idem)
    for hour in range(7, 24):
        set_clock(at(day1, hour, 0))
        state.jit_tick(at(day1, hour, 0))
        for row in rows_of(plugin, state, day1):
            if str(row.get("start") or "") == "%02d:00" % hour:
                text = str(row.get("activity") or "")
                if any(word in text for word in ("上班", "工位", "洗衣", "做饭")):
                    bad.append("day1 hour %02d work %s" % (hour, text))
                if hour >= 22 and sim.place_category(row) in ("mall", "office", "restaurant"):
                    bad.append("day1 hour %02d late %s" % (hour, row.get("place")))
    day1_rows = rows_of(plugin, state, day1)
    bad.extend("day1 timeline %s" % item for item in sim.timeline_violations(day1_rows)[:3])
    bad.extend("day1 place %s" % item for item in sim.place_violations(day1_rows)[:3])
    return future_idems


def _check_traveling_reload(bad, plugin, state, day1):
    """4) 旅行日重载：阶段/行/余额/池子都要稳定。"""
    day1_bytes = copy.deepcopy(rows_of(plugin, state, day1))
    p2 = rebuild(plugin, day1)
    t2 = trip.load(p2)
    if t2.get("phase") != "traveling":
        bad.append("reload traveling phase=%s" % t2.get("phase"))
    if rows_of(p2, p2.state, day1) != day1_bytes:
        bad.append("reload traveling rows changed")
    if p2.wallet.balance() != plugin.wallet.balance():
        bad.append("reload traveling balance changed")
    first_pool = p2.state._trip_day_items(day1, "traveling")
    second_pool = p2.state._trip_day_items(day1, "traveling")
    if first_pool != second_pool:
        bad.append("reload repeated pool changed")


def _check_mid_trip_change(bad, plugin, state, day1):
    """5) 旅途中改日程必须被拦下，且不能污染 kv / 行。"""
    before_trip = copy.deepcopy(plugin.db.kv_get("trip", {}))
    before_rows = copy.deepcopy(rows_of(plugin, state, day1))
    set_clock(at(day1, 12, 0))
    guard = trip.guard_change(plugin, "改去大理")
    if guard.get("action") != "blocked":
        bad.append("change guard=%s" % guard.get("action"))
    result = state.commit_schedule_change(
        day1,
        [{"start": "12:00", "end": "13:00", "activity": "去大理", "place": "大理",
          "zone": "scenic", "category": "play"}],
        old_items=before_rows, source="chat", reason="改去大理",
    )
    if not result.get("blocked"):
        bad.append("change not blocked")
    if plugin.db.kv_get("trip", {}) != before_trip:
        bad.append("change mutated trip")
    if rows_of(plugin, state, day1) != before_rows:
        bad.append("change mutated rows")


def _check_day2_pool(bad, plugin, state, day2):
    """6) 第二天旅行池 + 一路 tick 到回程前。"""
    set_clock(at(day2, 7, 0))
    pool2_rows = state._trip_day_items(day2, "traveling")
    pool2 = [row for row in pool2_rows if row.get("source") == "trip_pool"]
    if not (3 <= len(pool2) <= 5):
        bad.append("day2 pool=%d" % len(pool2))
    for hour in range(7, 16):
        set_clock(at(day2, hour, 0))
        state.jit_tick(at(day2, hour, 0))
    day2_rows = rows_of(plugin, state, day2)
    bad.extend("day2 place %s" % item for item in sim.place_violations(day2_rows)[:3])


def _check_settle_due(bad, plugin, day1):
    """7) 逐块结算：只结已经发生的块，重复调用不再扣费。"""
    set_clock(at(day1, 23, 0))
    first_settle = trip.settle_due(plugin, now=at(day1, 23, 0))
    count_before = len(plugin.wallet.ledger(300))
    second_settle = trip.settle_due(plugin, now=at(day1, 23, 0))
    count_after = len(plugin.wallet.ledger(300))
    if second_settle.get("settled"):
        bad.append("second settle charged again: %s" % second_settle.get("settled"))
    if count_after != count_before:
        bad.append("settle_due duplicated ledger rows")
    if not (first_settle.get("settled") or first_settle.get("duplicate")):
        bad.append("day1 settle produced nothing")
    return second_settle


def _check_return_split(bad, plugin, state, day1, day2, day3):
    """8) 回程拆分（start_day 被跨午夜 rebase 后，return_day 以真实 kv 为准）。"""
    current = dict(plugin.db.kv_get("trip", {}) or {})
    return_day = str(current.get("return_day") or day3)
    rest_expected = (dt.date.fromisoformat(return_day) + dt.timedelta(days=1)).isoformat()
    done_expected = (dt.date.fromisoformat(rest_expected) + dt.timedelta(days=1)).isoformat()
    ledger = plugin.wallet.ledger(200)
    if any((":%s:" % rest_expected) in entry["idem"] or
           (":%s:" % done_expected) in entry["idem"] for entry in ledger):
        bad.append("future day charged")
    set_clock(at(return_day, 16, 0))
    returning = trip.sync(plugin, now=at(return_day, 16, 0))
    if returning.get("phase") != "returning":
        bad.append("return phase=%s" % returning.get("phase"))
    return_rows = _legs(plugin, state, return_day, "return")
    if len(return_rows) != 3:
        bad.append("return legs=%d" % len(return_rows))
    total_return = _leg_total(return_rows)
    if not (180 <= total_return <= 300):
        bad.append("return total=%d" % total_return)
    bad.extend("return timeline %s" % item for item in sim.timeline_violations(
        rows_of(plugin, state, day2))[:3])
    return return_day, return_rows, total_return, rest_expected, done_expected


def _check_returning_reload(bad, plugin, state, return_day):
    """9) 回程阶段重载：行、余额、阶段都不能变。"""
    return_bytes = copy.deepcopy(rows_of(plugin, state, return_day))
    balance_return = plugin.wallet.balance()
    p2 = rebuild(plugin, return_day)
    t2 = trip.load(p2)
    if t2.get("phase") != "returning":
        bad.append("reload returning phase=%s" % t2.get("phase"))
    if rows_of(p2, p2.state, return_day) != return_bytes:
        bad.append("reload returning rows changed")
    if p2.wallet.balance() != balance_return:
        bad.append("reload returning balance changed")


def _check_finish_rest(bad, plugin, state):
    """10) 回程结束进入休息日：只留一块 08:00-20:00。"""
    raw = dict(plugin.db.kv_get("trip", {}) or {})
    arrive_home = dt.datetime.fromtimestamp(int(raw.get("return_ts") or 0)) + dt.timedelta(minutes=1)
    set_clock(arrive_home)
    finished = trip.sync(plugin, now=arrive_home)
    if finished.get("phase") != "rest":
        bad.append("finish phase=%s" % finished.get("phase"))
    rest_day = str(finished.get("rest_day") or "")
    set_clock(at(rest_day, 9, 0))
    rest_rows = state._trip_day_items(rest_day, "rest")
    rest = [row for row in rest_rows if row.get("activity") == trip.REST_DAY_ACTIVITY]
    if len(rest) != 1:
        bad.append("rest blocks=%d" % len(rest))
    if rest and (rest[0].get("start"), rest[0].get("end")) != ("08:00", "20:00"):
        bad.append("rest span=%s-%s" % (rest[0].get("start"), rest[0].get("end")))
    return rest_day, rest_rows


def _check_memory_calls(bad, plugin):
    """11) 记忆参数 + 幂等：重复 finalize 只写一次。"""
    rest_info = trip.load(plugin)
    asyncio.run(trip.finalize_async(plugin, rest_info))
    asyncio.run(trip.finalize_async(plugin, rest_info))
    memory = [call for call in plugin.memory_calls if call.get("kind") == "activity"]
    facts = [call for call in plugin.memory_calls if call.get("kind") == "fact"]
    if len(memory) != 1 or len(facts) != 1:
        bad.append("memory calls=%d/%d" % (len(memory), len(facts)))
    if memory:
        mem = memory[0]
        if mem.get("importance") != 0.9 or mem.get("tags") != trip.MEMORY_TAG:
            bad.append("memory params %r" % (mem,))
        if mem.get("idem_key") != "trip:%s:memory" % rest_info.get("id"):
            bad.append("memory idem=%s" % mem.get("idem_key"))
    return memory, facts


def _check_rest_reload(bad, plugin, rest_day, rest_rows):
    """12) 休息日重载：池子稳定、行不变、重复 finalize 不再写记忆。"""
    rest_bytes = copy.deepcopy(rest_rows)
    p2 = rebuild(plugin, rest_day)
    t2 = trip.load(p2)
    if t2.get("phase") != "rest":
        bad.append("reload rest phase=%s" % t2.get("phase"))
    rest_first = p2.state._trip_day_items(rest_day, "rest")
    rest_second = p2.state._trip_day_items(rest_day, "rest")
    if rest_first != rest_second:
        bad.append("rest repeated pool changed")
    if rows_of(p2, p2.state, rest_day) != rest_bytes:
        bad.append("reload rest rows changed")
    asyncio.run(trip.finalize_async(p2, t2))
    asyncio.run(trip.finalize_async(p2, t2))
    if p2.memory_calls:
        bad.append("reload rest wrote memory again")


def _check_done(bad, plugin, rest_day, rest_expected, done_expected):
    """13) 休息日次日进入 done。"""
    if rest_day != rest_expected:
        bad.append("rest_day %s != %s" % (rest_day, rest_expected))
    set_clock(at(done_expected, 9, 0))
    done = trip.sync(plugin, now=at(done_expected, 9, 0))
    if done.get("phase") != "done":
        bad.append("done phase=%s" % done.get("phase"))


def _check_spent(bad, plugin):
    """14) 花费与台账对账；返回各类扣费条数。"""
    entries = plugin.wallet.ledger(300)
    idems = [entry["idem"] for entry in entries]
    for day in ("day1", "day2"):
        if sum(1 for idem in idems if idem.endswith(":%s:hotel" % day)) != 1:
            bad.append("%s hotel count != 1" % day)
        for kind in ("meal", "ticket"):
            if sum(1 for idem in idems if idem.endswith(":%s:%s" % (day, kind))) > 1:
                bad.append("%s %s count > 1" % (day, kind))
    if not any(":daily:" in idem for idem in idems):
        bad.append("no daily settlement")
    if len({entry["idem"] for entry in entries}) != len(entries):
        bad.append("trip duplicate ledger idem")
    travel_debits = sum(-entry["delta_cents"] for entry in entries
                        if entry.get("fund_source") == "travel"
                        and int(entry.get("delta_cents") or 0) < 0)
    loaded = plugin.db.kv_get("trip", {}) or {}
    if int(loaded.get("spent") or 0) != travel_debits:
        bad.append("trip spent %s != ledger %s" % (loaded.get("spent"), travel_debits))
    if plugin.wallet.balance() < 0:
        bad.append("trip negative balance")
    if len({entry["idem"] for entry in entries}) != len(entries):
        bad.append("trip duplicate ledger idem")
    hotel_count = sum(1 for idem in idems if idem.endswith(":day1:hotel")) + \
        sum(1 for idem in idems if idem.endswith(":day2:hotel"))
    meal_count = sum(1 for idem in idems if ":day1:meal" in idem or ":day2:meal" in idem)
    ticket_count = sum(1 for idem in idems if ":day1:ticket" in idem or ":day2:ticket" in idem)
    daily_count = sum(1 for idem in idems if ":daily:" in idem)
    return {"travel_debits": travel_debits, "hotel": hotel_count,
            "meal": meal_count, "ticket": ticket_count, "daily": daily_count}


def run():
    checks = []
    set_clock(at(DAY0, 0, 0))
    try:
        plugin, state, rows, ctx, report = build_jit_day()
        checks.append(("24h.hourly.timeline", report["violations"],
                       "hours=24 jit_calls=%d writes=%d seed=%d" % (
                           report["jit_calls"], report["writes"], report["seed"])))
        merge = check_sleep_merge(plugin, state)
        checks.append(("24h.sleep.merge", merge["violations"],
                       "next_00_count=%d next_blocks=%d" % (
                           merge["merged"], merge["next_blocks"])))
        returns = check_returns()
        checks.append(("24h.place.return", returns["violations"],
                       "checked=%d" % returns["checked"]))
        chat = check_chat(report)
        checks.append(("24h.chat.now", chat["violations"],
                       "samples=%d road=%d rewritten=%d" % (
                           chat["samples"], chat["road"], chat["rewritten"])))
        diary = check_diary(state, ctx, rows)
        checks.append(("24h.diary.hourly", diary["violations"],
                       "checked=%d fallback=%d" % (diary["checked"], diary["fallback"])))
        daily = check_daily_diary()
        checks.append(("24h.diary.daily", daily["violations"],
                       "checked=%d" % daily["checked"]))
        photo = check_photo(state, rows)
        checks.append(("24h.photo.hourly", photo["violations"],
                       "checked=%d home_pose_hours=%d" % (
                           photo["checked"], photo["home_pose_hours"])))
        money = check_money_24h()
        checks.append(("24h.money", money["violations"],
                       "spends=%d unique_idem=%d dup=%d balance=%d spent=%d" % (
                           money["spends"], money["unique_idems"], money["duplicates"],
                           money["balance"], money["travel_spent"])))
        trip_result = check_trip_flow()
        checks.append(("trip.3day.full", trip_result["violations"],
                       "stages=%d outbound=%s/%dmin return=%d/%dmin rest=%s spent=%d balance=%d hotel=%d meal=%d ticket=%d daily=%d future=%d dup_settle=%d mem=%d reloads=%d" % (
                           trip_result.get("stages", 0), trip_result.get("outbound_ts", ""),
                           trip_result.get("outbound_minutes", 0),
                           trip_result.get("return_legs", 0), trip_result.get("return_total", 0),
                           trip_result.get("rest_day", ""), trip_result.get("travel_spent", 0),
                           trip_result.get("balance", 0), trip_result.get("hotel", 0),
                           trip_result.get("meal", 0), trip_result.get("ticket", 0),
                           trip_result.get("daily", 0), trip_result.get("future", 0),
                           trip_result.get("settle_dup", 0), trip_result.get("memory", 0),
                           trip_result.get("reloads", 0))))
    finally:
        restore_clock()
    failed = 0
    for name, violations, detail in checks:
        if violations:
            failed += 1
            print("[FAIL] %s (%s): %d 条" % (name, detail, len(violations)))
            for item in violations[:12]:
                print("       - %s" % item)
        else:
            print("[PASS] %s (%s)" % (name, detail))
    print("== %d/%d PASS ==" % (len(checks) - failed, len(checks)))
    return 1 if failed else 0


def main(argv=None):
    del argv
    return run()


if __name__ == "__main__":
    sys.exit(main())
