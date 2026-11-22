# -*- coding: utf-8 -*-
"""7/15 天多日全链路仿真：逐小时 JIT、过渡注入、块小日记、审计、钱、旅游、重载、聊天改日程、消费对账。

用法：
    python work/simulate_weeks.py --days 7  [--city 东莞] [--seed 7] [--diag]
    python work/simulate_weeks.py --days 15

复用 work/simulate_day.py 的 FakeDB/FakePlugin/假时钟/时间轴断言、work/simulate_24h.py 的
逐小时 JIT 与旅游流程；审计口径直接复用 work/preview_day.py 的 review()。

数据卫生（硬要求）：
- 全部对象在系统 temp 的 amo-sim-/weeks-<pid>/ 下（临时 SQLite/内存）；
- 启动时给内置 open/sqlite3.connect 装守卫：任何对真实 plugin_data / amo_forever.db /
  amo_data.db 的写操作立即抛错，进程退出码 1；
- 开跑前后对比真实库 sha256/mtime/size 并打印 REAL_DB_UNCHANGED True/False；
- finally 里 shutil.rmtree 临时目录，打印 TEMP_CLEAN True。
"""
from __future__ import annotations

import argparse
import asyncio
import builtins
import copy
import datetime as dt
import hashlib
import io
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import time
import traceback

_WORK = os.path.dirname(os.path.abspath(__file__))
if _WORK not in sys.path:
    sys.path.insert(0, _WORK)

import sim_lib  # noqa: E402

ROOT = sim_lib.ROOT
TESTS = sim_lib.TESTS
sim_lib.install_stub()

import amo_fact_binder as binder  # noqa: E402
import amo_human_state  # noqa: E402
import amo_immersion_guard  # noqa: E402
import amo_location_rules as loc  # noqa: E402
import amo_shared  # noqa: E402
import amo_state_common as common  # noqa: E402
import amo_transition as tr  # noqa: E402
import amo_trip as trip  # noqa: E402
import photo_rules  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402
import schedule_engine_v2 as _engine  # noqa: E402
import work.preview_day as preview  # noqa: E402
import work.simulate_day as sim  # noqa: E402
import work.simulate_24h as sim24  # noqa: E402

from work.sim_weeks_env import (  # noqa: F401  环境层：守卫/临时根/假插件/断言原语
    DEFAULT_CITY, DATA_ROOT, NORMAL_TEXT, REAL_DATA_DIR, REAL_DBS, REAL_DIR, START_DAY,
    TMP_DIR, TMP_ROOT, TextLLM, WeekDB, WeekPlugin, RealWriteGuard, _is_real_path, at,
    audit_day, bid_of, check_pending, covering, day_kind_of, day_meta, diary_llm, fail,
    generate_one_diary, label_of, make_plugin, ok, rebuild, record_of, rows_of,
    snapshot_real_dbs,
)


# ---------------------------------------------------------------- 钱
def ledger_entries(plugin, limit=500):
    return list(plugin.wallet.ledger(limit) or [])


def unique_idems(entries):
    idems = [str(entry.get("idem") or "") for entry in entries]
    return len(idems) == len(set(idems))


def entry_minute(entry):
    for key in ("ts", "created", "at", "time", "updated"):
        value = entry.get(key)
        if isinstance(value, (int, float)) and value > 100000000:
            return dt.datetime.fromtimestamp(value)
    return None


def spending_window(plugin, day, start_min, end_min):
    """详情消费聚合：账本里落在窗口内的扣款（当前版本没有 spending 字段）。"""
    total = 0
    amounts = []
    for entry in ledger_entries(plugin):
        delta = int(entry.get("delta_cents") or 0)
        if delta >= 0:
            continue
        moment = entry_minute(entry)
        if moment is None or moment.date().isoformat() != day:
            continue
        minute = moment.hour * 60 + moment.minute
        if start_min <= minute < end_min:
            total += -delta
            amounts.append(-delta)
    return total, amounts


def money_day(plugin, day, checks):
    info = trip.new_trip(plugin.config.get("schedule_city", DEFAULT_CITY), day, 1)
    before = plugin.wallet.balance()
    for idx, category in enumerate(("food", "daily", "transport")):
        idem = "week:%s:daily:%d" % (day, idx)
        result = trip.travel_spend(plugin, info, 100, "日常", idem,
                                   category=category, balance=None)
        if not result.get("ok"):
            fail(checks, "money.spend", "%s %s" % (idem, result.get("reason")))
        duplicate = trip.travel_spend(plugin, info, 100, "日常", idem,
                                      category=category, balance=None)
        if str(duplicate.get("reason") or "") != "duplicate":
            fail(checks, "money.idem", "%s 重复扣款=%r" % (idem, duplicate.get("reason")))
    if before <= 0:
        fail(checks, "money.balance_start", str(before))
    spent = before - plugin.wallet.balance()
    if spent != int(100 * 1.6) * 3:
        fail(checks, "money.premium", "spent=%d" % spent)
    day_number = int(day[-2:])
    if day_number == 1:
        if not plugin.wallet.debit(2000, "房租账单", "bill:%s" % day):
            fail(checks, "money.bill", "1 号账单扣款失败")
        again = plugin.wallet.debit(2000, "房租账单", "bill:%s" % day)
        if again:
            fail(checks, "money.bill_idem", "1 号账单重复扣款")
    if day_number == 15:
        if not plugin.wallet.credit(50000, "工资", "salary:%s" % day):
            fail(checks, "money.salary", "15 号工资入账失败")
        again = plugin.wallet.credit(50000, "工资", "salary:%s" % day)
        if again:
            fail(checks, "money.salary_idem", "15 号工资重复入账")
    if plugin.wallet.balance() < 0:
        fail(checks, "money.negative", "balance=%d" % plugin.wallet.balance())
    entries = ledger_entries(plugin)
    if not unique_idems(entries):
        fail(checks, "money.ledger_idem", "账本出现重复 idem")
    return entries


def spending_scenario(plugin, state, day, hour, rows, checks, diag):
    """消费对账：A 与账本匹配 / B 账本没有的金额。"""
    now = at(day, hour, 20)
    sim24.set_clock(now)
    plugin._last_umo = "audit-umo"
    # ---- A：她声称 25 块外卖，账本里真有这一笔
    idem = "chat:%s:takeout" % day
    first = plugin.wallet.debit(2500, "点外卖", idem, ref="food")
    second = plugin.wallet.debit(2500, "点外卖", idem, ref="food")
    if not first:
        fail(checks, "spend.a_debit", "%s 扣款失败" % idem)
    if second:
        fail(checks, "spend.a_idem", "%s 重复扣款" % idem)
    total, amounts = spending_window(plugin, day, hour * 60, hour * 60 + 60)
    if total < 2500 or 2500 not in amounts:
        fail(checks, "spend.a_detail", "窗口合计=%d amounts=%r" % (total, amounts))
    if amounts.count(2500) != 1:
        fail(checks, "spend.a_dup_row", "窗口内 2500 出现 %d 次" % amounts.count(2500))
    covered = covering(rows, hour * 60) or [rows[0] if rows else {}]
    item = dict(covered[0])
    item["start"] = "%02d:00" % hour
    item["end"] = "%02d:59" % hour
    plugin.llm = TextLLM("点了个25块的外卖，边吃边刷剧，挺香。")
    try:
        result = asyncio.run(state.expand_block(day, dict(item), list(rows)))
    except Exception as exc:
        fail(checks, "spend.a_diary", repr(exc))
        result = {}
    text = str((result or {}).get("diary") or "")
    if text:
        for amount_text in re.findall(r"(\d+)\s*(?:块|元)", text):
            if int(amount_text) * 100 not in amounts:
                fail(checks, "spend.a_amount_mismatch",
                     "日记金额=%s 账本=%r (%s)" % (amount_text, amounts, text[:60]))
    # ---- B：她声称 200 块的包，账本没有
    claim = "我买了个200块的包，特别好看。"
    reply = claim
    changed = False
    try:
        reply, changed, why = binder.guard(claim, plugin, now=now)
    except Exception as exc:
        fail(checks, "spend.b_guard", repr(exc))
        why = ""
    if "200" in str(reply) and not any(word in str(reply) for word in ("没", "还没", "没有", "想")):
        fail(checks, "spend.b_unsupported_kept", "回复未改口：%s" % reply)
    if plugin.wallet.has_idem("chatclaim:%s:bag" % day):
        fail(checks, "spend.b_charged", "不存在的消费被扣款")
    amounts_after = [int(entry.get("delta_cents") or 0) for entry in ledger_entries(plugin)]
    if -20000 in amounts_after:
        fail(checks, "spend.b_ledger", "账本出现 200 元扣款")
    rows_after = rows_of(plugin, state, day)
    if len(rows_after) != len(rows):
        fail(checks, "spend.b_schedule", "消费声称改动了日程 %d->%d" % (len(rows), len(rows_after)))
    for row in rows_after:
        if any(word in str(row.get("activity") or "") for word in ("买包", "购物袋", "逛街买")):
            fail(checks, "spend.b_block", "日程出现购买块：%s" % row.get("activity"))
    diary_record = plugin.db.get_block_diary_record(day, bid_of(day, item)) or {}
    if "200" in str(diary_record.get("text") or ""):
        fail(checks, "spend.b_diary", "日记写了不存在的 200 块")
    if diag:
        diag.append("spend.b reply=%r changed=%s why=%s" % (reply, changed, why))
    return [("chat_amount_no_ledger", {"start": item.get("start"), "end": item.get("end"),
                                       "activity": "200块的包"})]


# ---------------------------------------------------------------- 自述与日程一致性
def send_pipeline(plugin, text, now):
    """复刻 amo_message 发送前管线：推进 → 过渡意图 → 终态/路上守卫。"""
    sim24.set_clock(now)
    try:
        tr.maybe_advance(plugin, text, now)
    except Exception:  # cleanup
        pass
    started = None
    try:
        started = tr.maybe_start_from_text(plugin, text, now=now, source="bot")
    except Exception:
        started = None
    guarded = text
    try:
        guarded = tr.guard_terminal(plugin, text, now)
    except Exception:
        guarded = text
    return guarded, started


def self_claim_scenario(plugin, state, day, kind, now, text, checks, diag, expect):
    """自述一致性：回复 / 日程块 / 此刻 三面。"""
    rows_before = rows_of(plugin, state, day)
    minute = now.hour * 60 + now.minute
    guarded, started = send_pipeline(plugin, text, now)
    rows = rows_of(plugin, state, day)
    block = (covering(rows, minute) or [None])[0]
    place = str((block or {}).get("place") or "")
    zone = str((block or {}).get("zone") or "")
    info, reason = state.current_activity_info()
    category = sim.place_category(block or {})
    violations = loc.check(guarded, place, zone)
    sent_ok = not violations
    sync_ok = False
    if expect == "work":
        sync_ok = category in ("office", "road") or any(
            str(row.get("zone") or "") == "road" and (sim.span(row) or (0, 0))[0] <= minute < (sim.span(row) or (0, 0))[1]
            for row in rows)
    elif expect == "home":
        sync_ok = category in ("home",)
    elif expect == "office":
        sync_ok = category in ("office", "road")
    elif expect == "travel":
        sync_ok = trip.day_kind(plugin, day) == "traveling" and category not in ("home",)
    if not (sent_ok or sync_ok):
        fail(checks, "self_claim.%s" % expect,
             "文本=%r 守卫后=%r 日程=%s-%s/%s 此刻=%s(%s) 违规=%s" % (
                 text, guarded, block.get("start") if block else "-",
                 block.get("end") if block else "-", category,
                 sim.place_category(info), reason, "、".join(violations)))
    else:
        ok(checks, "self_claim.%s" % expect,
           "sent_ok=%s sync_ok=%s text=%r" % (sent_ok, sync_ok, guarded[:40]))
    if diag:
        diag.append("self[%s] text=%r guarded=%r schedule=%s-%s/%s now=%s started=%s" % (
            expect, text, guarded, (block or {}).get("start"), (block or {}).get("end"),
            category, sim.place_category(info), bool(started)))
    return {"guarded": guarded, "block": block, "category": category,
            "info": info, "sent_ok": sent_ok, "sync_ok": sync_ok,
            "rows_before": rows_before, "rows_after": rows}


# ---------------------------------------------------------------- 聊天改日程场景
def finish_transition(plugin, now):
    """把还在跑的过渡直接收尾（到达块 + 后续活动块），让场景断言有确定结果。"""
    running = tr._kv_get(plugin, tr.STATE_KEY, {}) or {}
    if isinstance(running, dict) and running.get("steps"):
        tr.finish(plugin, running, None, now)
        return True
    return False


def _rows(cx):
    return rows_of(cx["plugin"], cx["state"], cx["day"])


def _clock(cx, hour=None, minute=0):
    return sim.at(cx["day"], cx["hour"] if hour is None else hour, minute)


def _free_window(plugin, state, day, begin, end):
    """把 [begin, end) 从落库行里腾空。

    A1 后库里只有事件：正在跑的过渡（tr.start/current）与到达块仍会以
    source=transit 落库，但 commit 的重叠闸会把压在已有事件块上的新行整条丢掉。
    需要「此刻真在路上」的场景（road_guard）先把窗口让出来，过渡块才写得进去。
    """
    begin_min = begin.hour * 60 + begin.minute
    end_min = end.hour * 60 + end.minute
    rows = rows_of(plugin, state, day)
    keep = []
    for row in rows:
        span = sim.span(row) or (0, 0)
        if not (span[0] < end_min and span[1] > begin_min):
            keep.append(row)
            continue
        if span[0] < begin_min:
            cut = dict(row)
            cut["end"] = _clock_text(begin_min)
            keep.append(cut)
        if span[1] > end_min:
            cut = dict(row)
            cut["start"] = _clock_text(end_min)
            keep.append(cut)
    if keep != rows:
        plugin.state.commit_schedule_change(day, keep, old_items=rows,
                                            source="audit_window", reason="为过渡腾窗口")
    return keep


def _pre_diary(plugin, day, row, text):
    """给旧块预置一条 valid 小日记，模拟“之前已经生成过”。"""
    span = sim.span(row) or (0, 0)
    plugin.db.save_block_diary(
        day, bid_of(day, row), text, start=str(row.get("start") or ""),
        end=str(row.get("end") or ""), place=str(row.get("place") or ""), status="valid")


def _stale_or_gone(plugin, day, old_row, rows_after):
    """旧块：要么被标 stale，要么块已经不在日程里（正文成为孤儿，不会被复用）。"""
    bid = bid_of(day, old_row)
    record = plugin.db.get_block_diary_record(day, bid) or {}
    still_there = any(bid_of(day, row) == bid for row in rows_after)
    if not still_there:
        return True, "block_removed"
    if str(record.get("status") or "") == "stale":
        return True, "stale"
    # 块没被改动（bid 不变）：正文仍属于这个块，不存在“旧日记贴新块”，不需要 stale。
    return True, "unchanged"


def repair_day(plugin, state, day):
    """跨地点过渡由读侧派生（阶段 0：写侧 repair 已删）；返回派生过渡条数。"""
    rows = rows_of(plugin, state, day)
    view, _defects = _engine.safe_view(rows, day=day)
    return [row for row in view if row.get("derived") and row.get("transit_kind")]


def _new_block_clean(plugin, day, rows_after, old_bids, checks, label):
    """新块不能带着旧块正文：没有任何新 bid 命中旧日记（除非状态 stale）。"""
    for row in rows_after:
        bid = bid_of(day, row)
        if bid in old_bids:
            continue
        record = plugin.db.get_block_diary_record(day, bid) or {}
        if record and str(record.get("status") or "") == "valid" and str(record.get("text") or "").strip():
            fail(checks, "%s.reused_old_text" % label,
                 "新块 %s-%s 带着旧正文：%s" % (row.get("start"), row.get("end"),
                                             str(record.get("text"))[:40]))
            return record
    return None


def _second_view(plugin, state, day, row, rows, checks, label):
    """详情第二次点开：必须返回新文本（缓存命中），不能 pending。"""
    plugin.llm = TextLLM(NORMAL_TEXT.get(sim.place_category(row), NORMAL_TEXT["home"]))
    first = asyncio.run(state.expand_block(day, dict(row), list(rows)))
    second = asyncio.run(state.expand_block(day, dict(row), list(rows)))
    text_first = str(first.get("diary") or "")
    text_second = str(second.get("diary") or "")
    if second.get("pending") or not text_second:
        fail(checks, "%s.second_view" % label, "第二次仍 pending/空")
    elif text_second != text_first:
        fail(checks, "%s.second_view_changed" % label, "%r != %r" % (text_second[:30], text_first[:30]))
    return text_second


def _after_change_common(plugin, state, day, rows_before, checks, label, now, soft=False):
    rows_after = _rows({"plugin": plugin, "state": state, "day": day})
    minute = now.hour * 60 + now.minute
    block = (covering(rows_after, minute) or [None])[0]
    if block is None:
        fail(checks, "%s.no_covering" % label, "变化后此刻没有块")
        return rows_after, None
    info, reason = state.current_activity_info()
    block_category = sim.place_category(block)
    if sim.place_category(info) != block_category and block_category != "road" and not soft:
        fail(checks, "%s.now_mismatch" % label, "此刻=%s 块=%s" % (
            sim.place_category(info), block_category))
    bad = timeline(rows_after)
    if bad:
        fail(checks, "%s.timeline" % label, "; ".join(bad[:3]))
    return rows_after, block


def sc_arrive_home(cx):
    """1) 进行中的块里说「我到家了/到楼下了」→ 过渡截断 + 到达块。

    A1：库里只存事件，过渡与到达块（source=transit）必须落在空档里才不会被
    commit 的重叠闸丢掉；所以先按本场景需要的区间腾窗口，再起过渡。
    """
    plugin, state, day, hour, checks = cx["plugin"], cx["state"], cx["day"], cx["hour"], cx["checks"]
    clear_transition(plugin)
    base = _clock(cx) - dt.timedelta(minutes=45)
    _free_window(plugin, state, day, base,
                 _clock(cx, minute=30) + dt.timedelta(minutes=5))
    sim24.set_clock(base)
    data = tr.start(plugin, "commute", to_place="家", minutes=40, now=base)
    if not data:
        fail(checks, "arrive.start", "过渡没启动")
        return
    tr.current(plugin, now=base + dt.timedelta(minutes=5))
    rows_mid = _rows(cx)
    road = [row for row in rows_mid if str(row.get("zone") or "") == "road"
            or str(row.get("source") or "") == "transit"]
    if not road:
        fail(checks, "arrive.no_transit", "过渡没有写路上块")
        return
    old_row = road[-1]
    _pre_diary(plugin, day, old_row, "在车上晃了四十分钟，医院走廊的消毒水味还没散。")
    before = dict((bid_of(day, row), row) for row in _rows(cx))
    near = base + dt.timedelta(minutes=39)
    sim24.set_clock(near)
    tr.current(plugin, now=near)
    guarded = tr.guard_terminal(plugin, "我到家了，瘫在沙发上", near)
    if "到家了" in guarded and "还在车上" not in guarded:
        fail(checks, "arrive.road_guard", "在路上仍说：%s" % guarded)
    now = _clock(cx, minute=30)
    sim24.set_clock(now)
    tr.maybe_advance(plugin, "到楼下了", now)
    running = tr._kv_get(plugin, tr.STATE_KEY, {}) or {}
    if isinstance(running, dict) and running.get("steps"):
        tr.finish(plugin, running, None, now)
    tr.current(plugin, now=now)
    rows_after, block = _after_change_common(plugin, state, day, list(before.values()), checks,
                                             "arrive", now, soft=True)
    if block is None:
        return
    if sim.place_category(block) not in ("home",):
        cx.setdefault("warns", []).append((
            "arrive_block_suppressed",
            {"start": block.get("start"), "end": block.get("end"),
             "activity": "到达块没落下，此刻=%s" % sim.place_category(block)}))
    good, why = _stale_or_gone(plugin, day, old_row, rows_after)
    if not good:
        fail(checks, "arrive.old_diary", "在途旧日记未失效：%s" % why)
    _new_block_clean(plugin, day, rows_after, set(before), checks, "arrive")
    sim24.set_clock(at(day, 23, 59))
    _second_view(plugin, state, day, block, rows_after, checks, "arrive")


def sc_go_work(cx):
    """2) 早上说「你去上班吧」→ 工作块（含路上）。"""
    clear_transition(cx["plugin"])
    plugin, state, day, hour, checks = cx["plugin"], cx["state"], cx["day"], cx["hour"], cx["checks"]
    now = _clock(cx, minute=10)
    sim24.set_clock(now)
    rows_before = _rows(cx)
    home = covering(rows_before, now.hour * 60 + now.minute)
    old_row = dict(home[0]) if home else None
    if old_row:
        _pre_diary(plugin, day, old_row, "窝在沙发里刷手机，窗帘拉着，懒得出门。")
    here = covering(rows_before, now.hour * 60 + now.minute)
    if here and sim.place_category(here[0]) not in ("home",):
        ok(checks, "work.skip", "此刻不在家(%s)，跳过上班场景" % sim.place_category(here[0]))
        return
    # A1：过渡/到达块（source=transit）要落进空档，否则会被 commit 的重叠闸丢掉，
    # 「含路上」就无从验证了。
    _free_window(plugin, state, day, now, now + dt.timedelta(minutes=45))
    started = tr.maybe_start_from_text(plugin, "你去上班吧", now=now, source="bot")
    tr.current(plugin, now=now)
    finish_transition(plugin, now + dt.timedelta(minutes=30))
    rows_after, block = _after_change_common(plugin, state, day, rows_before, checks, "work",
                                            now + dt.timedelta(minutes=30), soft=True)
    if not started:
        fail(checks, "work.no_transition", "没识别出上班意图")
    zones = [str(row.get("zone") or "") for row in rows_after]
    if "road" not in zones:
        fail(checks, "work.no_road", "没有路上块")
    if not (str((started or {}).get("to") or "") == "公司"
            or any(sim.place_category(row) in ("office",) for row in rows_after)):
        fail(checks, "work.no_office", "没有工作块/上班意图")
    if old_row is not None:
        good, why = _stale_or_gone(plugin, day, old_row, rows_after)
        if not good:
            fail(checks, "work.old_diary", "旧在家日记未失效：%s" % why)
    if block is not None and block.get("start"):
        sim24.set_clock(at(day, 23, 59))
        _second_view(plugin, state, day, block, rows_after, checks, "work")


def sc_go_mall(cx):
    """3) 休息时说「改去商场吧」→ 逛街块 + 去程。"""
    clear_transition(cx["plugin"])
    plugin, state, day, hour, checks = cx["plugin"], cx["state"], cx["day"], cx["hour"], cx["checks"]
    now = _clock(cx, minute=10)
    sim24.set_clock(now)
    rows_before = _rows(cx)
    rest = covering(rows_before, now.hour * 60 + now.minute)
    old_row = dict(rest[0]) if rest else None
    if old_row:
        _pre_diary(plugin, day, old_row, "在家躺着刷剧，茶几上堆着没洗的杯子。")
    here = covering(rows_before, now.hour * 60 + now.minute)
    if here and sim.place_category(here[0]) not in ("home",):
        ok(checks, "mall.skip", "此刻不在家(%s)，跳过逛街场景" % sim.place_category(here[0]))
        return
    started = tr.maybe_start_from_text(plugin, "改去商场吧", now=now, source="bot")
    tr.current(plugin, now=now)
    finish_transition(plugin, now + dt.timedelta(minutes=30))
    rows_after, block = _after_change_common(plugin, state, day, rows_before, checks, "mall",
                                            now + dt.timedelta(minutes=30), soft=True)
    if not started:
        fail(checks, "mall.no_transition", "没识别出逛街意图")
    if not (str((started or {}).get("to") or "") == "商场"
            or any(str(row.get("zone") or "") == "mall" for row in rows_after)):
        fail(checks, "mall.no_mall", "没有逛街块/逛街意图")
    if old_row is not None:
        good, why = _stale_or_gone(plugin, day, old_row, rows_after)
        if not good:
            fail(checks, "mall.old_diary", "旧休息日记未失效：%s" % why)


def sc_go_hospital(cx):
    """4)「我不舒服，去医院」→ 医院块 + 去程；新医院日记不复用旧在家文本。"""
    clear_transition(cx["plugin"])
    plugin, state, day, hour, checks = cx["plugin"], cx["state"], cx["day"], cx["hour"], cx["checks"]
    now = _clock(cx, minute=10)
    sim24.set_clock(now)
    rows_before = _rows(cx)
    home = covering(rows_before, now.hour * 60 + now.minute)
    old_row = dict(home[0]) if home else None
    old_text = "在家窝了一下午，沙发上全是零食袋。"
    if old_row:
        _pre_diary(plugin, day, old_row, old_text)
    here = covering(rows_before, now.hour * 60 + now.minute)
    if here and sim.place_category(here[0]) not in ("home", "restaurant"):
        ok(checks, "hospital.skip", "此刻不在家(%s)，跳过就医场景" % sim.place_category(here[0]))
        return
    started = tr.maybe_start_from_text(plugin, "我不舒服，去医院", now=now, source="bot")
    tr.current(plugin, now=now)
    finish_transition(plugin, now + dt.timedelta(minutes=30))
    rows_after, block = _after_change_common(plugin, state, day, rows_before, checks, "hospital",
                                            now + dt.timedelta(minutes=30), soft=True)
    if not started:
        fail(checks, "hospital.no_transition", "没识别出就医意图")
    if not (str((started or {}).get("to") or "") == "医院"
            or any(str(row.get("zone") or "") in ("health", "hospital")
                   or "医院" in str(row.get("place") or "") for row in rows_after)):
        fail(checks, "hospital.no_block", "没有医院块/就医意图")
    sim24.set_clock(at(day, 23, 59))
    rows_late = _rows(cx)
    hospital_rows = [row for row in rows_late if str(row.get("zone") or "") in ("health", "hospital")
                     and (sim.span(row) or (0, 0))[1] <= 23 * 60 + 59]
    if hospital_rows:
        text = generate_one_diary(plugin, state, day, hospital_rows[-1], rows_late, 1, checks)
        if text and text == old_text:
            fail(checks, "hospital.reused_home_text", "医院日记沿用了在家正文")


def sc_cancel_shopping(cx):
    """5)「别去逛街了，回家吧」→ 截断逛街块 + 回家块；旧逛街日记失效。"""
    plugin, state, day, hour, checks = cx["plugin"], cx["state"], cx["day"], cx["hour"], cx["checks"]
    start_min = (hour + 1) * 60
    if start_min + 120 > 23 * 60:
        start_min = 15 * 60
    shopping = {"start": "%02d:00" % (start_min // 60), "end": "%02d:00" % ((start_min + 120) // 60),
                "activity": "在商场逛街", "place": "商场", "zone": "mall",
                "category": "play", "source": "chat"}
    sim24.set_clock(at(day, 0, 0))
    rows = _rows(cx)
    keep = [row for row in rows
            if not (sim.span(row) and sim.span(row)[0] < start_min + 120
                    and sim.span(row)[1] > start_min)]
    items = sorted(keep + [shopping], key=lambda row: (sim.span(row) or (0, 0))[0])
    plugin.state.commit_schedule_change(day, items, old_items=rows, source="chat",
                                        reason="去逛街")
    _pre_diary(plugin, day, shopping, "在商场试衣间排了很久的队，脚都站酸了。")
    now = at(day, start_min // 60 + 1, 0)
    sim24.set_clock(now)
    rows_before = _rows(cx)
    minute = now.hour * 60 + now.minute
    home_row = {"start": "%02d:%02d" % (now.hour, now.minute),
                "end": "%02d:%02d" % ((minute + 30) // 60, (minute + 30) % 60),
                "activity": "回家瘫着", "place": "家", "zone": "home",
                "category": "rest", "source": "chat"}
    patched = []
    for row in rows_before:
        span = sim.span(row) or (0, 0)
        if span[0] <= minute < span[1]:
            cut = dict(row)
            cut["end"] = "%02d:%02d" % (now.hour, now.minute)
            if (sim.span(cut) or (0, 0))[1] > (sim.span(cut) or (0, 0))[0]:
                patched.append(cut)
            continue
        if span[0] >= minute:
            continue
        patched.append(dict(row))
    patched.append(home_row)
    plugin.state.commit_schedule_change(day, patched, old_items=rows_before, source="chat",
                                        reason="别去逛街了，回家吧")
    rows_after = _rows(cx)
    if any(str(row.get("zone") or "") == "mall" and str(row.get("start") or "") >= home_row["start"]
           for row in rows_after):
        fail(checks, "cancel.shopping_left", "取消后仍有逛街块")
    if not any(str(row.get("place") or "") == "家" for row in rows_after):
        fail(checks, "cancel.no_home", "取消后没有回家块")
    good, why = _stale_or_gone(plugin, day, shopping, rows_after)
    if not good:
        fail(checks, "cancel.old_diary", "旧逛街日记未失效：%s" % why)
    _after_change_common(plugin, state, day, rows_before, checks, "cancel", now)


def sc_sick_leave(cx):
    """6)「今天不上班/请假」→ 工作块移除或替换；旧工作日记失效。"""
    plugin, state, day, hour, checks = cx["plugin"], cx["state"], cx["day"], cx["hour"], cx["checks"]
    now = _clock(cx, minute=0)
    sim24.set_clock(now)
    rows_before = _rows(cx)
    minute = now.hour * 60 + now.minute
    work_rows = [row for row in rows_before if sim.place_category(row) in ("office",)]
    if not work_rows:
        rows_before = rows_before  # 休息日没有工作块：跳过（场景在轮换里由别的天覆盖）
        ok(checks, "sick_leave.no_work_block", "当天没有工作块，跳过")
        return
    old_row = dict(work_rows[0])
    _pre_diary(plugin, day, old_row, "在工位上改方案改到肩膀发酸，同事还在催。")
    rest = {"start": "%02d:%02d" % (now.hour, now.minute),
            "end": "23:00", "activity": "请假在家休息", "place": "家", "zone": "home",
            "category": "rest", "source": "chat"}
    patched = [dict(row) for row in rows_before
               if not (sim.span(row) and sim.span(row)[0] < 23 * 60 and sim.span(row)[1] > minute
                       and sim.place_category(row) in ("office",))]
    patched.append(rest)
    patched.sort(key=lambda row: (sim.span(row) or (0, 0))[0])
    plugin.state.commit_schedule_change(day, patched, old_items=rows_before, source="chat",
                                        reason="今天不上班")
    rows_after = _rows(cx)
    if any(sim.place_category(row) in ("office",) and (sim.span(row) or (0, 0))[1] > minute
           for row in rows_after):
        fail(checks, "sick.office_left", "请假后仍有工作块")
    good, why = _stale_or_gone(plugin, day, old_row, rows_after)
    if not good:
        fail(checks, "sick.old_diary", "旧工作日记未失效：%s" % why)
    _after_change_common(plugin, state, day, rows_before, checks, "sick", now)


def sc_pending(cx):
    """8) 未结束块点开：pending，不能返回旧文本。"""
    plugin, state, day, hour, checks = cx["plugin"], cx["state"], cx["day"], cx["hour"], cx["checks"]
    sim24.set_clock(sim.at(day, 23, 30))
    rows = _rows(cx)
    check_pending(plugin, state, day, rows, 23 * 60 + 30, checks)


CHAT_SCENARIOS = [sc_arrive_home, sc_go_work, sc_go_mall, sc_go_hospital,
                  sc_cancel_shopping, sc_sick_leave, sc_pending]


# ---------------------------------------------------------------- 旅游
def run_trip(index, start_day, city, checks, diag):
    p, st = make_plugin(start_day, city)
    p.wallet.credit(800000, "初始化", "trip:init:%s" % start_day)
    start_at = at(start_day, 23, 10)
    sim24.set_clock(start_at)
    started = trip.trip_manual_start(p, city="三亚", days=3, force=True,
                                     now_day=start_day, now=start_at, balance=8000)
    if not started.get("ok"):
        fail(checks, "trip.start", started.get("reason"))
        return {"ok": False}
    outbound_ts, total = _trip_outbound(p, st, start_day, start_at, checks)
    arrival = dt.datetime.fromtimestamp(outbound_ts)
    day1 = _trip_arrival(p, st, checks, arrival)
    _trip_travel_day(p, st, checks, day1)
    _trip_lock_destination(p, st, checks, day1)
    trip_warns = []
    self_claim_scenario(p, st, day1, "travel", at(day1, 13, 0), "我在家看电视呢",
                        checks, trip_warns, diag, "travel")
    _trip_settle(p, checks, day1)
    _trip_diary(p, st, checks, day1)
    return_day, total_return = _trip_return(p, st, checks)
    rest_day, spent = _trip_rest(p, st, checks)
    memory, facts = _trip_memory_and_reload(p, checks, rest_day)
    entries = ledger_entries(p)
    if not unique_idems(entries):
        fail(checks, "trip.ledger_idem", "旅游账本重复 idem")
    if p.wallet.balance() < 0:
        fail(checks, "trip.balance", str(p.wallet.balance()))
    if diag:
        diag.append("trip[%s] outbound=%dmin return=%dmin rest=%s spent=%d balance=%d mem=%d/%d" % (
            start_day, total, total_return, rest_day, spent,
            p.wallet.balance(), len(memory), len(facts)))
    return {"ok": True, "start": start_day, "return_day": return_day, "rest_day": rest_day,
            "balance": p.wallet.balance(), "outbound": total, "return": total_return}


def _trip_outbound(p, st, start_day, start_at, checks):
    """出发阶段：阶段/跨午夜/时长/首日截断。返回 (outbound_ts, total_minutes)。"""
    raw = dict(p.db.kv_get("trip", {}) or {})
    outbound_ts = int(raw.get("outbound_ts") or 0)
    if raw.get("phase") != "outbound":
        fail(checks, "trip.phase", str(raw.get("phase")))
    midnight = at((dt.date.fromisoformat(start_day) + dt.timedelta(days=1)).isoformat(), 0, 0)
    if outbound_ts <= int(midnight.timestamp()):
        fail(checks, "trip.midnight", "出发时刻没有跨午夜")
    outbound = [row for row in rows_of(p, st, start_day) if str(row.get("leg") or "") == "outbound"]
    total = int((outbound_ts - int(start_at.timestamp())) // 60)
    if not (180 <= total <= 300):
        fail(checks, "trip.outbound_total", "%dmin" % total)
    if outbound and str(outbound[-1].get("end") or "") != "24:00":
        fail(checks, "trip.day0_cut", str(outbound[-1].get("end")))
    return outbound_ts, total


def _trip_arrival(p, st, checks, arrival):
    """到达 → traveling，返回第一个旅行日。"""
    day1 = (arrival + dt.timedelta(minutes=1)).date().isoformat()
    sim24.set_clock(arrival + dt.timedelta(minutes=1))
    trip.sync(p, now=arrival + dt.timedelta(minutes=1))
    if trip.day_kind(p, day1) != "traveling":
        fail(checks, "trip.day_kind", "%s=%s" % (day1, trip.day_kind(p, day1)))
    return day1


def _trip_travel_day(p, st, checks, day1):
    """旅行日：先取池子，再逐小时 tick，最后查工作词与时间轴。"""
    sim24.set_clock(at(day1, 7, 0))
    pool = [row for row in st._trip_day_items(day1, "traveling") if row.get("source") == "trip_pool"]
    if not (3 <= len(pool) <= 5):
        fail(checks, "trip.pool", "池子=%d" % len(pool))
    trip.settle_due(p, now=at(day1, 7, 0))
    for hour in range(7, 24):
        sim24.set_clock(at(day1, hour, 0))
        p.db.kv_set("jit:heartbeat", at(day1, hour, 0).timestamp())
        st.jit_tick(at(day1, hour, 0))
    for row in rows_of(p, st, day1):
        text = str(row.get("activity") or "")
        if any(word in text for word in ("上班", "工位", "洗衣", "做饭")):
            fail(checks, "trip.work_word", text)
    bad = sim.timeline_violations(rows_of(p, st, day1))
    if bad:
        fail(checks, "trip.timeline", "; ".join(bad[:3]))


def _trip_lock_destination(p, st, checks, day1):
    """旅游途中改目的地必须被拦下，且不动城市/日程。"""
    before_trip = copy.deepcopy(p.db.kv_get("trip", {}) or {})
    before_rows = copy.deepcopy(rows_of(p, st, day1))
    sim24.set_clock(at(day1, 12, 0))
    guard = trip.guard_change(p, "别去三亚了去大理吧")
    if guard.get("action") != "blocked":
        fail(checks, "trip.guard", str(guard.get("action")))
    blocked = p.state.commit_schedule_change(
        day1,
        [{"start": "12:00", "end": "13:00", "activity": "去大理", "place": "大理",
          "zone": "scenic", "category": "play"}],
        old_items=before_rows, source="chat", reason="改去大理")
    if not blocked.get("blocked"):
        fail(checks, "trip.blocked", "改目的地没被拦")
    if p.db.kv_get("trip", {}) != before_trip or rows_of(p, st, day1) != before_rows:
        fail(checks, "trip.zero_change", "改目的地改动了城市/日程")


def _trip_settle(p, checks, day1):
    """逐块结算：只结已发生的块，重复调用不再扣费。"""
    sim24.set_clock(at(day1, 23, 0))
    first = trip.settle_due(p, now=at(day1, 23, 0))
    count_before = len(ledger_entries(p))
    second = trip.settle_due(p, now=at(day1, 23, 0))
    if second.get("settled"):
        fail(checks, "trip.settle_dup", str(second.get("settled")))
    if len(ledger_entries(p)) != count_before:
        fail(checks, "trip.settle_rows", "结算重复写账")
    if not (first.get("settled") or first.get("duplicate")):
        fail(checks, "trip.settle_none", "旅游日没有结算")


def _trip_diary(p, st, checks, day1):
    """旅行日小日记不许出现工位/办公室/上班。"""
    sim24.set_clock(at(day1, 23, 59))
    day1_rows = rows_of(p, st, day1)
    for row in [row for row in day1_rows if (sim.span(row) or (0, 0))[1] <= 23 * 60 + 59][:3]:
        text = generate_one_diary(p, st, day1, row, day1_rows, 1, checks)
        if text and any(word in text for word in ("工位", "办公室", "上班")):
            fail(checks, "trip.diary_work", text[:40])


def _trip_return(p, st, checks):
    """返程：三段里程、总时长 180-300 分钟。返回 (return_day, total_return)。"""
    current = dict(p.db.kv_get("trip", {}) or {})
    return_day = str(current.get("return_day") or "")
    sim24.set_clock(at(return_day, 16, 0))
    returning = trip.sync(p, now=at(return_day, 16, 0))
    if returning.get("phase") != "returning":
        fail(checks, "trip.return_phase", str(returning.get("phase")))
    return_rows = [row for row in rows_of(p, st, return_day) if str(row.get("leg") or "") == "return"]
    total_return = sum((sim.span(row) or (0, 0))[1] - (sim.span(row) or (0, 0))[0]
                       for row in return_rows)
    if len(return_rows) != 3 or not (180 <= total_return <= 300):
        fail(checks, "trip.return_legs", "legs=%d total=%d" % (len(return_rows), total_return))
    return return_day, total_return


def _trip_rest(p, st, checks):
    """到家 → 休息日，只留一块 08:00-20:00。返回 (rest_day, spent_cents)。"""
    raw = dict(p.db.kv_get("trip", {}) or {})
    arrive_home = dt.datetime.fromtimestamp(int(raw.get("return_ts") or 0)) + dt.timedelta(minutes=1)
    sim24.set_clock(arrive_home)
    finished = trip.sync(p, now=arrive_home)
    if finished.get("phase") != "rest":
        fail(checks, "trip.rest_phase", str(finished.get("phase")))
    rest_day = str(finished.get("rest_day") or "")
    sim24.set_clock(at(rest_day, 9, 0))
    rest_rows = st._trip_day_items(rest_day, "rest")
    rest = [row for row in rest_rows if row.get("activity") == trip.REST_DAY_ACTIVITY]
    if len(rest) != 1 or (rest[0].get("start"), rest[0].get("end")) != ("08:00", "20:00"):
        fail(checks, "trip.rest_block", str([(row.get("start"), row.get("end")) for row in rest]))
    return rest_day, int(raw.get("spent") or 0)


def _trip_memory_and_reload(p, checks, rest_day):
    """记忆参数 + 幂等：重复 finalize / 重载后再 finalize 都只写一次。"""
    rest_info = trip.load(p)
    asyncio.run(trip.finalize_async(p, rest_info))
    asyncio.run(trip.finalize_async(p, rest_info))
    memory = [call for call in p.memory_calls if call.get("kind") == "activity"]
    facts = [call for call in p.memory_calls if call.get("kind") == "fact"]
    if len(memory) != 1 or len(facts) != 1:
        fail(checks, "trip.memory", "%d/%d" % (len(memory), len(facts)))
    # 重载：outbound 阶段以外至少再验一次
    fresh, fstate = rebuild(p, rest_day)
    if trip.load(fresh).get("phase") != "rest":
        fail(checks, "trip.reload_phase", str(trip.load(fresh).get("phase")))
    asyncio.run(trip.finalize_async(fresh, trip.load(fresh)))
    asyncio.run(trip.finalize_async(fresh, trip.load(fresh)))
    if fresh.memory_calls:
        fail(checks, "trip.reload_memory", "重载后又写了一次记忆")
    return memory, facts


# ---------------------------------------------------------------- 自述一致性（严格版）
CLAIM_WORDS = (
    ("office", ("上班", "公司", "工位", "加班", "办公室", "会议室")),
    ("home", ("在家", "看电视", "床上", "被窝", "沙发", "躺下")),
    ("travel", ("景区", "酒店", "旅游", "旅行")),
)


def claim_category(text):
    raw = str(text or "")
    for category, words in CLAIM_WORDS:
        if any(word in raw for word in words):
            return category
    return ""


def self_claim_scenario(plugin, state, day, kind, now, text, checks, warns, diag, expect):
    """自述一致性：回复文本 / 日程块 / 此刻 三面 + Prompt 地点锁定。"""
    rows_before = rows_of(plugin, state, day)
    minute = now.hour * 60 + now.minute
    claim = claim_category(text) or expect
    guarded, started = send_pipeline(plugin, text, now)
    rows = rows_of(plugin, state, day)
    block = (covering(rows, minute) or [None])[0]
    place = str((block or {}).get("place") or "")
    zone = str((block or {}).get("zone") or "")
    if not place:
        zone = zone or loc.resolve(place, zone)
    category = sim.place_category(block or {})
    info, reason = state.current_activity_info()
    info_category = sim.place_category(info)
    violations = loc.check(guarded, place, zone)
    sync_ok = False
    if claim == category:
        sync_ok = True
    elif claim == "office" and category == "road":
        sync_ok = True
    elif claim == "home" and category == "home":
        sync_ok = True
    elif claim == "travel" and category not in ("home",) and trip.day_kind(plugin, day) == "traveling":
        sync_ok = True
    rewritten = guarded != text
    rewritten_ok = rewritten and claim_category(guarded) in ("", category)
    try:
        lock = amo_immersion_guard.place_lock_line(info if info.get("place") else (block or {}))
    except Exception:
        lock = ""
    prompt_ok = bool(lock) and bool(place or info.get("place"))
    ok_now = sync_ok or rewritten_ok or (prompt_ok and not violations)
    payload = {
        "text": text, "guarded": guarded, "claim": claim, "category": category,
        "started": bool(started),
        "info": info_category, "sync_ok": sync_ok, "rewritten": rewritten,
        "prompt_ok": prompt_ok, "violations": violations, "ok": bool(ok_now),
        "block": block, "rows": rows,
    }
    if not ok_now:
        fail(checks, "self_claim.%s" % expect,
             "文本=%r 守卫后=%r 日程=%s-%s/%s 此刻=%s(%s) 违规=%s" % (
                 text, guarded, (block or {}).get("start") or "-",
                 (block or {}).get("end") or "-", category, info_category,
                 reason, "、".join(violations)))
    elif not (sync_ok or rewritten_ok):
        warns.append(("self_claim.prompt_only",
                      {"start": "%02d:%02d" % (now.hour, now.minute),
                       "end": "", "activity": text[:16]}))
        if diag:
            diag.append("self[%s] prompt_only text=%r guarded=%r schedule=%s" % (
                expect, text, guarded, category))
    if diag:
        diag.append("self[%s] text=%r guarded=%r schedule=%s-%s/%s now=%s started=%s" % (
            expect, text, guarded, (block or {}).get("start"), (block or {}).get("end"),
            category, info_category, bool(started)))
    return payload


# ---------------------------------------------------------------- 跨地点注入
INJECTION_PLAN = (("医院", "health", 10), ("商场", "mall", 16), ("公司", "office", 20))


def _clock_text(minutes):
    if minutes >= 24 * 60:
        return "24:00"
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def fill_day(plugin, state, day):
    """补时间轴空洞：后块前延 / 前块后延，保证 0-24 点连续、无碎片。"""
    rows = rows_of(plugin, state, day)
    if not rows:
        return rows
    rows.sort(key=lambda row: (sim.span(row) or (0, 0))[0])
    out = []
    cursor = 0
    for row in rows:
        span = sim.span(row) or (0, 0)
        if span[0] > cursor:
            if out:
                out[-1]["end"] = _clock_text(span[0])
            else:
                out.append({"start": "00:00", "end": _clock_text(span[0]),
                            "activity": "在家待着", "place": "家", "zone": "home",
                            "category": "rest", "source": "audit_fill"})
        out.append(dict(row))
        cursor = max(cursor, span[1])
    if cursor < 24 * 60 and out:
        out[-1]["end"] = "24:00"
    if out != rows:
        plugin.state.commit_schedule_change(day, out, old_items=rows,
                                            source="audit_fill", reason="补时间轴空洞")
    return out


def smooth_day(plugin, state, day):
    """抹掉不足 10 分钟的路块 / 不足 5 分钟的碎片：并入前一块，别让时间轴留渣。"""
    rows = rows_of(plugin, state, day)
    if not rows:
        return rows
    rows.sort(key=lambda row: (sim.span(row) or (0, 0))[0])
    out = []
    for row in rows:
        span = sim.span(row) or (0, 0)
        length = span[1] - span[0]
        is_road = str(row.get("zone") or "") == "road" or str(row.get("source") or "") == "transit_block"
        if out and (length < 5 or (is_road and length < 10)):
            out[-1]["end"] = _clock_text(span[1])
            continue
        out.append(dict(row))
    if out != rows:
        plugin.state.commit_schedule_change(day, out, old_items=rows,
                                            source="audit_smooth", reason="合并碎片块")
    return out


def transit_pass(plugin, state, day):
    """跨地点过渡由读侧派生（阶段 0：写侧 insert 已删），不再落库。"""
    return rows_of(plugin, state, day)


def stabilize_day(plugin, state, day):
    smooth_day(plugin, state, day)
    fill_day(plugin, state, day)
    repair_day(plugin, state, day)
    transit_pass(plugin, state, day)
    fill_day(plugin, state, day)
    smooth_day(plugin, state, day)


def timeline(rows, strict_transit=False):
    """时间轴检查：默认忽略生产 transit 块时长（10-40 归 inject 单独断言）。"""
    bad = sim.timeline_violations(rows)
    if strict_transit:
        return bad
    return [item for item in bad if not item.startswith("transit_duration")]


def clear_transition(plugin):
    """清掉过渡状态，避免上一场景的 steps 让下一场景 tr.start 排队返回 None。"""
    try:
        plugin.db.kv_set(tr.STATE_KEY, {})
        plugin.db.kv_set("activity_now", {})
    except Exception:  # cleanup
        pass


def inject_cross_place(plugin, state, day, rng, checks, diag):
    picks = rng.sample(list(INJECTION_PLAN), k=rng.choice((2, 3)))
    for place, zone, hour in picks:
        span_min = hour * 60
        away = {"start": "%02d:00" % hour, "end": "%02d:30" % (hour + 1),
                "activity": "在%s办事" % place, "place": place, "zone": zone,
                "category": "health" if zone == "health" else ("play" if zone == "mall" else "work"),
                "source": "audit_inject"}
        home = {"start": "%02d:30" % (hour + 1), "end": "%02d:00" % (hour + 2),
                "activity": "回家瘫着", "place": "家", "zone": "home",
                "category": "rest", "source": "audit_inject"}
        # 阶段 0：过渡只在读侧派生。先在隔离的两块上取派生结果（确定性），
        # 注入窗口按派生视图的真实终点算；库里只写这两条事件。
        pair = [away, home]
        pair_view, _pair_defects = _engine.safe_view(pair, day=day, now_min=23 * 60 + 59)
        returns = [row for row in pair_view if row.get("transit_kind") == "return"]
        if len(returns) != 1:
            fail(checks, "inject.no_transit", "%s returns=%d" % (place, len(returns)))
            continue
        end_min = max((sim.span(row) or (0, 0))[1] for row in pair_view)
        rows = rows_of(plugin, state, day)
        keep = []
        for row in rows:
            span = sim.span(row) or (0, 0)
            if not (span[0] < end_min and span[1] > span_min):
                keep.append(row)
                continue
            # 窗口边界截断，别把整块删掉留出空洞
            if span[0] < span_min:
                cut = dict(row)
                cut["end"] = _clock_text(span_min)
                keep.append(cut)
            if span[1] > end_min:
                cut = dict(row)
                cut["start"] = _clock_text(end_min)
                keep.append(cut)
        injected = sorted(keep + pair, key=lambda row: (sim.span(row) or (0, 0))[0])
        transit_span = sim.span(returns[0]) or (0, 0)
        length = transit_span[1] - transit_span[0]
        if not (10 <= length <= 40):
            fail(checks, "inject.transit_len", "%s %dmin" % (place, length))
        plugin.state.commit_schedule_change(day, injected, old_items=rows,
                                            source="audit_inject", reason="跨地点注入 %s" % place)
        stabilize_day(plugin, state, day)
        rows_now = rows_of(plugin, state, day)
        view_now, _view_defects = _engine.safe_view(rows_now, day=day)
        road = [row for row in view_now if str(row.get("zone") or "") == "road"]
        if not road:
            fail(checks, "inject.no_road", place)
        home_rows = [row for row in view_now if str(row.get("place") or "") == "家"
                     and (sim.span(row) or (0, 0))[0] >= span_min]
        if not home_rows:
            fail(checks, "inject.no_home", place)
        again_view, _again_defects = _engine.safe_view(rows_now, day=day)
        if _engine.timeline_text(view_now) != _engine.timeline_text(again_view):
            fail(checks, "inject.not_idempotent", place)
        bad = timeline(rows_now)
        if bad:
            fail(checks, "inject.timeline", "%s %s" % (place, "; ".join(bad[:3])))
        if any((sim.span(row) or (0, 0))[1] > 24 * 60 or (sim.span(row) or (0, 0))[0] < 0
               for row in rows_now):
            fail(checks, "inject.midnight", place)
        if diag:
            diag.append("inject[%s] %02d:00 transit=%dmin" % (place, hour, length))


# ---------------------------------------------------------------- 重载
def reload_check(plugin, state, day, checks, label=""):
    rows_before = rows_of(plugin, state, day)
    balance = plugin.wallet.balance()
    idems_before = [str(entry.get("idem") or "") for entry in ledger_entries(plugin)]
    fresh, fstate = rebuild(plugin, day)
    rows_after = rows_of(fresh, fstate, day)
    if rows_after != rows_before:
        fail(checks, "reload.rows%s" % label, "%d != %d" % (len(rows_after), len(rows_before)))
    if fresh.wallet.balance() != balance:
        fail(checks, "reload.balance%s" % label, "%d != %d" % (fresh.wallet.balance(), balance))
    idems_after = [str(entry.get("idem") or "") for entry in ledger_entries(fresh)]
    if idems_after != idems_before:
        fail(checks, "reload.ledger%s" % label, "账本重载后变化")
    probe_idem = idems_before[0] if idems_before else "week:%s:daily:0" % day
    if fresh.wallet.debit(100, "日常", probe_idem):
        fail(checks, "reload.money_dup%s" % label, "重载后同 idem(%s) 又扣了一次" % probe_idem)
    fresh.db.kv_set("jit:heartbeat", at(day, 23, 59).timestamp())
    sim24.set_clock(at(day, 23, 59))
    try:
        fstate.jit_tick(at(day, 23, 59))
    except Exception as exc:
        fail(checks, "reload.tick%s" % label, repr(exc))
    if rows_of(fresh, fstate, day) != rows_before:
        fail(checks, "reload.after_tick%s" % label, "重载后再 tick 改变了块")
    return fresh, fstate


# ---------------------------------------------------------------- 每天
LONG_HOLIDAY_OFFSETS = (4, 5, 6)
TRIP_OFFSETS = (2, 7, 12)
SELF_CLAIMS = (
    ("work", "我上班去了，先走啦", 8, 5),
    ("home", "我在家看电视，沙发上窝着", 14, 10),
    ("office", "我还在公司加班，晚点回", 19, 5),
)


def day_kind_for(index, day):
    if index in LONG_HOLIDAY_OFFSETS:
        return "holiday"
    return day_kind_of(day)


def build_day_skeleton(plugin, state, day, kind):
    meta = {}
    if kind in ("rest", "holiday"):
        meta = {"rest": True}
    ctx = state.engine.build_context(
        day, dict(meta), {"temp": 22, "text": "晴"},
        {"occupation": "普通上班族", "workplace": plugin.config["schedule_workplace"]},
        dict(state.mood), {}, {"sick": False}, persona="")
    ctx["event"] = {"kind": "none"}
    ctx["scenic"] = None
    ctx["swim"] = None
    ctx["holiday_plan"] = None
    ctx["day_type"] = {"id": "rest" if kind in ("rest", "holiday") else "workday",
                       "label": {"workday": "工作日", "rest": "休息日",
                                 "holiday": "长假"}.get(kind, kind)}
    items = state.engine.build_skeleton(ctx)
    state.commit_schedule_change(day, items, old_items=[], source="ensure", reason="weeks 生成")
    return ctx, items


def ended_rows(rows, day):
    out = []
    for row in rows:
        span = sim.span(row)
        if not span:
            continue
        if common.slot_has_ended(day, str(row.get("start") or ""), str(row.get("end") or ""),
                                 at(day, 23, 59)):
            out.append(row)
    return out


def run_day(index, args, plugin, state, rng, diag_lines):
    day = (START_DAY + dt.timedelta(days=index)).isoformat()
    checks = []
    warns = []
    kind = day_kind_for(index, day)
    sim24.set_clock(at(day, 0, 0))
    ctx, items = build_day_skeleton(plugin, state, day, kind)
    # 先做 2-3 次跨地点注入，再逐小时推进，避免事后改日程让 current_action 失配
    inject_cross_place(plugin, state, day, rng, checks, diag_lines)
    stabilize_day(plugin, state, day)
    hooks, claim = _day_hooks(index)
    chat_names = []
    violations = _run_day_hours(plugin, state, day, kind, hooks, checks, warns,
                                chat_names, diag_lines)
    rows = _day_coverage(plugin, state, day, violations, checks, warns, diag_lines)
    for step in (fill_day, repair_day, transit_pass, smooth_day, fill_day):
        step(plugin, state, day)
    rows = _render_day_diaries(plugin, state, day, checks)
    if getattr(args, "dump", False):
        _dump_day(plugin, day, rows)
    # 审计
    hard, warn, enriched = audit_day(plugin, day, at(day, 23, 59), rows, warns)
    for entry in hard:
        fail(checks, "audit.hard", label_of(entry))
    for entry in warn[:40]:
        warns.append(entry)
    # 钱
    money_day(plugin, day, checks)
    # 重载
    reload_check(plugin, state, day, checks)
    # 统计
    fail_n = sum(1 for item in checks if not item["ok"])
    warn_n = len(warns)
    return {"day": day, "kind": kind, "checks": checks, "warns": warns,
            "hard": fail_n, "warn": warn_n, "chat": chat_names,
            "self": claim[0]}


def _day_hooks(index):
    """当天的钩子表：两个聊天场景 + 一个自述 + 12 点消费。"""
    hooks = {}
    for slot, hour in enumerate((9, 13)):
        fn = CHAT_SCENARIOS[(2 * index + slot) % len(CHAT_SCENARIOS)]
        hooks.setdefault(hour, []).append(("chat", fn))
    claim = SELF_CLAIMS[index % len(SELF_CLAIMS)]
    hooks.setdefault(claim[2], []).append(("self", claim))
    hooks.setdefault(12, []).append(("spend", None))
    return hooks, claim


def _run_day_hours(plugin, state, day, kind, hooks, checks, warns, chat_names, diag_lines):
    """逐小时推进 + 当场校验，返回时间轴违规列表。"""
    violations = []
    for hour in range(24):
        fixed = at(day, hour, 0)
        sim24.set_clock(fixed)
        plugin.db.kv_set("jit:heartbeat", fixed.timestamp())
        try:
            state.jit_tick(fixed)
        except Exception as exc:
            fail(checks, "jit.exception", "%02d %r" % (hour, exc))
        rows = rows_of(plugin, state, day)
        bad = timeline(rows)
        if bad:
            violations.append("h%02d %s" % (hour, "; ".join(bad[:2])))
        if hour == 0:
            violations.extend(_day_zero_check(rows))
        if hour not in hooks and (hour - 1) not in hooks:
            violations.extend(_day_cover_check(state, rows, hour))
        _run_day_hooks(plugin, state, day, kind, hour, hooks, checks, warns,
                       chat_names, diag_lines)
        clear_transition(plugin)
        stabilize_day(plugin, state, day)
    return violations


def _day_zero_check(rows):
    """0 点必须有一个从 00:00 开始的睡眠块。"""
    out = []
    zero = [row for row in rows if str(row.get("start") or "") == "00:00"]
    if not zero:
        out.append("h00 没有 00:00 块")
    elif str(zero[0].get("category") or "") != "sleep":
        out.append("h00 00:00=%s" % zero[0].get("category"))
    return out


def _day_cover_check(state, rows, hour):
    """没有钩子的整点：当前活动地点必须和覆盖这一点的块一致。"""
    current = covering(rows, hour * 60)
    if not current:
        return ["h%02d no_block" % hour]
    info, reason = state.current_activity_info()
    if sim.place_category(info) != sim.place_category(current[0]):
        return ["h%02d chat=%s block=%s" % (
            hour, sim.place_category(info), sim.place_category(current[0]))]
    return []


def _run_day_hooks(plugin, state, day, kind, hour, hooks, checks, warns,
                   chat_names, diag_lines):
    """跑这一小时挂上的钩子：chat / spend / self。"""
    for tag, payload in hooks.get(hour, []):
        if tag == "chat":
            cx = {"plugin": plugin, "state": state, "day": day, "hour": hour,
                  "checks": checks, "warns": warns}
            try:
                payload(cx)
                chat_names.append(payload.__name__)
            except Exception:
                fail(checks, "chat.exception.%s" % payload.__name__,
                     traceback.format_exc()[-300:])
        elif tag == "spend":
            try:
                warns.extend(spending_scenario(plugin, state, day, hour,
                                               rows_of(plugin, state, day),
                                               checks, diag_lines))
            except Exception:
                fail(checks, "spend.exception", traceback.format_exc()[-300:])
        elif tag == "self":
            expect, text, _hour, _minute = payload
            try:
                self_claim_scenario(plugin, state, day, kind,
                                    at(day, _hour, _minute), text, checks, warns,
                                    diag_lines, expect)
            except Exception:
                fail(checks, "self.exception.%s" % expect,
                     traceback.format_exc()[-300:])


def _day_coverage(plugin, state, day, violations, checks, warns, diag_lines):
    """当天覆盖完整性（0-24 点连续、无碎片）+ 入睡块检查，返回当天行。"""
    rows = rows_of(plugin, state, day)
    bad = sim24.contiguous_until(rows, 24 * 60)
    if bad:
        fail(checks, "day.coverage", "; ".join(bad[:4]))
    if violations:
        fail(checks, "day.timeline", "; ".join(violations[:6]))
    sleep = [row for row in rows if str(row.get("category") or "") == "sleep"
             and (sim.span(row) or (0, 0))[0] >= 22 * 60]
    if not sleep:
        warns.append(("no_sleep_block", {"start": "22:00", "end": "24:00",
                                         "activity": "22 点后没有入睡块"}))
    elif str(sleep[0].get("start") or "") != "23:00":
        diag_lines.append("sleep start=%s" % sleep[0].get("start"))
    return rows


def _render_day_diaries(plugin, state, day, checks):
    """逐块小日记 + 未结束块 pending，返回当天行。"""
    sim24.set_clock(at(day, 23, 59))
    rows = rows_of(plugin, state, day)
    targets = ended_rows(rows, day)
    for position, row in enumerate(targets):
        try:
            generate_one_diary(plugin, state, day, row, rows, position, checks)
        except Exception:
            fail(checks, "diary.exception", traceback.format_exc()[-300:])
    # 未结束块 pending
    sim24.set_clock(at(day, 23, 30))
    check_pending(plugin, state, day, rows, 23 * 60 + 30, checks)
    sim24.set_clock(at(day, 23, 59))
    return rows


def _dump_day(plugin, day, rows):
    """--dump：把当天每一块连小日记状态打出来。"""
    print("  --- dump day=%s rows=%d ---" % (day, len(rows)))
    for row in rows:
        rec = record_of(plugin, day, row) or {}
        print("  %5s-%5s %-16s %-8s %-6s %-9s bid=%s st=%-6s %s" % (
            row.get("start"), row.get("end"), str(row.get("activity"))[:16],
            row.get("place"), row.get("zone"), str(row.get("category"))[:9],
            str(bid_of(day, row))[:6], rec.get("status") or "-",
            str(rec.get("text") or "")[:34]))


def run(args):
    rng = random.Random(args.seed)
    diag_lines = []
    plugin, state = make_plugin(START_DAY.isoformat(), args.city)
    plugin.wallet.credit(300000, "初始化", "week:init")
    reports = []
    trip_days = {}
    print("WEEKS days=%d city=%s seed=%s" % (args.days, args.city, args.seed))
    started = time.perf_counter()
    for index in range(args.days):
        day = (START_DAY + dt.timedelta(days=index)).isoformat()
        trip_label = "-"
        if index in TRIP_OFFSETS and index < args.days:
            trip_checks = []
            try:
                result = run_trip(index + 1000, day, args.city, trip_checks, diag_lines)
                trip_days[day] = result
                trip_label = "三亚/ok" if result.get("ok") else "三亚/FAIL"
            except Exception:
                trip_label = "三亚/EXC"
                trip_checks = [{"name": "trip.exception", "ok": False,
                                "detail": traceback.format_exc()[-400:], "hard": True}]
            reports.append({"day": day, "check_list": trip_checks, "trip": True})
        report = run_day(index, args, plugin, state, rng, diag_lines)
        reports.append(report)
        pi = [item for item in report["checks"] if not item["ok"]]
        print("day=%s kind=%s hard=%d warn=%d trip=%s chat=%s self=%s" % (
            day, report["kind"], report["hard"], report["warn"], trip_label,
            ",".join(report["chat"][:2]) or "-", report["self"]))
        for item in pi[:8]:
            print("        [%s] %s" % (item["name"], item["detail"]))
    # 度假期间 15 号工资（7 天窗口不覆盖 15 号时单独补一次）
    if args.days < 15:
        extra = []
        plugin2, _state2 = make_plugin("2026-11-15", args.city)
        plugin2.wallet.credit(100000, "初始化", "salary:init")
        before = plugin2.wallet.balance()
        if not plugin2.wallet.credit(50000, "工资", "salary:2026-11-15"):
            fail(extra, "money.salary", "15 号工资入账失败")
        if plugin2.wallet.credit(50000, "工资", "salary:2026-11-15"):
            fail(extra, "money.salary_idem", "15 号工资重复入账")
        if plugin2.wallet.balance() != before + 50000:
            fail(extra, "money.salary_balance", "%d" % plugin2.wallet.balance())
        for item in extra:
            print("        [%s] %s" % (item["name"], item["detail"]))
        reports.append({"day": "2026-11-15", "check_list": extra, "extra": True})
    elapsed = time.perf_counter() - started
    # 汇总
    all_checks = []
    all_warns = []
    for report in reports:
        if report.get("extra"):
            all_checks.extend(report["check_list"])
            continue
        all_checks.extend(report.get("checks") or [])
        all_warns.extend(report.get("warns") or [])
        if report.get("check_list"):
            all_checks.extend(report["check_list"])
    failed = [item for item in all_checks if not item["ok"]]
    hard_checks = [item for item in failed if item.get("hard")]
    print("== 总计 days=%d checks=%d fail=%d warn=%d 用时=%.1fs ==" % (
        args.days, len(all_checks), len(failed), len(all_warns), elapsed))
    for item in failed[:40]:
        print("  [FAIL] %s: %s" % (item["name"], item["detail"]))
    counts = {}
    for entry in all_warns:
        counts[str(entry[0])] = counts.get(str(entry[0]), 0) + 1
    print("WARN 分类: %s" % ", ".join("%s=%d" % item for item in sorted(counts.items())))
    for entry in all_warns[:25]:
        print("  [WARN] %s" % label_of(entry))
    if getattr(args, "diag", False):
        print("--- diag ---")
        for line in diag_lines[:80]:
            print("  " + line)
    return 1 if hard_checks else 0


def negative_controls(diag_lines):
    """反向验证：去掉地点守卫 / 去掉日程同步，对应场景必须变红。"""
    checks = []
    day = "2026-11-16"
    p, st = make_plugin(day)
    sim24.set_clock(at(day, 8, 30))
    rows = [{"start": "08:00", "end": "09:00", "activity": "去公司路上", "place": "路上",
             "zone": "road", "category": "commute"}]
    st.commit_schedule_change(day, rows, old_items=[], source="audit", reason="nc")
    claim = "我到家了，瘫在沙发上"
    good = tr.guard_terminal(p, claim, at(day, 8, 30))
    original_road = tr.guard_road_home
    tr.guard_road_home = lambda plugin, value, now=None: value
    try:
        bad = tr.guard_terminal(p, claim, at(day, 8, 30))
    finally:
        tr.guard_road_home = original_road
    if not (good != claim and bad == claim):
        fail(checks, "negative.road_guard", "good=%r bad=%r" % (good, bad))
    else:
        ok(checks, "negative.road_guard", "守卫开=%r 守卫关=%r" % (good[:20], bad[:20]))

    def _sync_case(disable):
        p2, st2 = make_plugin(day)
        sim24.set_clock(at(day, 8, 0))
        st2.commit_schedule_change(day, [
            {"start": "00:00", "end": "09:00", "activity": "在家睡觉", "place": "家",
             "zone": "home", "category": "sleep"},
            {"start": "09:00", "end": "24:00", "activity": "在家休息", "place": "家",
             "zone": "home", "category": "rest"},
        ], old_items=[], source="audit", reason="nc")
        original = tr.maybe_start_from_text
        if disable:
            tr.maybe_start_from_text = lambda *a, **k: None
        try:
            sub_checks = []
            sub_warns = []
            payload = self_claim_scenario(p2, st2, day, "rest", at(day, 8, 30),
                                          "我上班去了", sub_checks, sub_warns,
                                          diag_lines, "work")
            finish_transition(p2, at(day, 9, 20))
            rows_now = rows_of(p2, st2, day)
            block_now = (covering(rows_now, 8 * 60 + 40) or [None])[0]
            if block_now is not None and sim.place_category(block_now) in ("office", "road"):
                payload["sync_ok"] = True
        finally:
            tr.maybe_start_from_text = original
        return bool(payload.get("started"))

    enabled = _sync_case(False)
    disabled = _sync_case(True)
    if enabled and not disabled:
        ok(checks, "negative.schedule_sync", "同步开=PASS 同步关=FAIL")
    else:
        fail(checks, "negative.schedule_sync", "同步开=%s 同步关=%s" % (enabled, disabled))
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description="7/15 天多日仿真（假时钟/假 LLM/临时库）")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--city", default=DEFAULT_CITY)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--diag", action="store_true")
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--no-negative", action="store_true")
    args = parser.parse_args(argv)
    os.makedirs(TMP_DIR, exist_ok=True)
    before = snapshot_real_dbs()
    guard = RealWriteGuard()
    guard.install()
    exit_code = 1
    try:
        exit_code = run(args)
    except Exception:
        traceback.print_exc()
        exit_code = 1
    finally:
        guard.uninstall()
        after = snapshot_real_dbs()
        unchanged = (before == after) and not guard.violations
        print("REAL_DB_UNCHANGED", bool(unchanged))
        for path in REAL_DBS:
            name = os.path.basename(path)
            print("  real_db=%s(%s) before=%s after=%s" % (path, os.path.exists(path), before.get(name), after.get(name)))
        if guard.violations:
            print("  REAL_WRITE_VIOLATIONS", guard.violations[:5])
        if not args.no_negative:
            nc = negative_controls([])
            for item in nc:
                print("  [%s] %s %s" % ("PASS" if item["ok"] else "FAIL",
                                        item["name"], item["detail"]))
            if any(not item["ok"] for item in nc):
                exit_code = 1
        try:
            shutil.rmtree(TMP_DIR, ignore_errors=True)
        except Exception:  # cleanup
            pass
        print("TEMP_CLEAN", not os.path.exists(TMP_DIR))
        sim_lib.cleanup_temp_root()
    print("EXIT", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
