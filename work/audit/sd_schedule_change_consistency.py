# -*- coding: utf-8 -*-
"""只读证据脚本（sd_*）：改日程对话后「场景块 / 日记条目 / 提醒」一致性。

三条改动场景（全部走生产写入口，内存 FakeDB + clock.freeze，不碰生产库）：
  S1 chat「不去了」：下午商场外出 -> 在家休息
  S2 chat「我们出去吃吧」：晚饭在家 -> 出去吃（生产规则 place=外面）
  S3 chat 加一段加班：17:00-19:00 把下班通勤改成在公司加班（apply_schedule 网关）

每个场景记录 before/after 的块、场景账本、提醒事实、日记提示词/正文与旧日记失效状态，
并给出三者是否互相矛盾的判定。产出 work/audit/sd_schedule_change_scenarios.json。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _path in (ROOT, os.path.join(ROOT, "work"), os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import sim_lib  # noqa: E402

sim_lib.install_stub()

import amo_location_rules as loc  # noqa: E402
import amo_scene_ledger as ledger  # noqa: E402
from amo_proactive import (  # noqa: E402
    schedule_fact_current,
    schedule_reminder_fact,
)
from amo_timeline import block_id  # noqa: E402
import schedule_change as SC  # noqa: E402
from sim_lib import at, minute_of, restore_clock, set_clock, span  # noqa: E402
import work.simulate_weeks as weeks  # noqa: E402

OUT_JSON = os.path.join(HERE, "sd_schedule_change_scenarios.json")


class PromptEchoLLM:
    """确定性 LLM：只把提示词里「这一小时」的地点/动作写回日记。

    这样日记正文是否跟着新块走，取决于生产提示词是否已刷新，而不是模型自由发挥。
    """

    def __init__(self):
        self.prompts = []
        self.texts = []

    async def chat_raw(self, umo, system, prompt):
        self.prompts.append(prompt)
        match = re.search(
            r"这一小时：(\d{1,2}:\d{2})-(\d{1,2}:\d{2})，(.+?)，在(.+?)（(.+?)），心情",
            prompt)
        if match:
            activity, place = match.group(3), match.group(4)
        else:
            activity, place = "待着", "家"
        text = ("这一会儿我在%s，%s，没什么特别的，就安安静静待着，"
                "听着外面的动静，慢慢把这一阵过完。") % (place, activity)
        self.texts.append(text)
        return json.dumps({"diary": text, "mood": "平静"}, ensure_ascii=False)


def install_db_stubs(plugin):
    """给 FakeDB 补生产块日记接口（内存实现），用来验证旧日记失效/新日记写入。"""
    store = {}

    def save_block_diary(day, bid, text, start="", end="", place="",
                         version=0, status="valid", **kwargs):
        store[(str(day), str(bid))] = {
            "text": str(text or ""), "status": str(status or "valid"),
            "version": int(version or 0), "start": str(start or ""),
            "end": str(end or ""), "place": str(place or ""),
        }

    def get_block_diary_record(day, bid):
        record = store.get((str(day), str(bid)))
        return dict(record) if record else None

    def sync_block_diaries(day, current_items, stale_ids=(), version=None):
        current = {block_id(day, row) for row in (current_items or [])
                   if isinstance(row, dict)}
        exact = {str(x) for x in (stale_ids or ())}
        plugin.db.sync_calls.append({"day": str(day), "stale_ids": sorted(exact),
                                     "current": sorted(current)})
        marked, orphans = [], []
        for (store_day, bid), record in list(store.items()):
            if store_day != str(day):
                continue
            orphan = bid not in current
            if orphan:
                orphans.append(bid)
            if (orphan or bid in exact) and record.get("status") not in (
                    "stale", "frozen", "archived"):
                record["status"] = "stale"
                marked.append(bid)
        return {"stale": len(marked), "orphans": sorted(orphans),
                "invalidated": sorted(exact)}

    def mark_diaries_stale(day, start_hour, version=0):
        return 0

    plugin.db.sync_calls = []
    plugin.db.save_block_diary = save_block_diary
    plugin.db.get_block_diary_record = get_block_diary_record
    plugin.db.sync_block_diaries = sync_block_diaries
    plugin.db.mark_diaries_stale = mark_diaries_stale
    return store


def block_at(rows, clock_text):
    minute = minute_of(clock_text)
    for row in rows:
        current = span(row)
        if current and current[0] <= minute < current[1]:
            return row
    return None


def find_block(rows, predicate):
    for row in rows:
        if predicate(row):
            return row
    return None


def brief(row):
    if not row:
        return {}
    return {key: row.get(key) for key in
            ("start", "end", "activity", "place", "zone", "category", "event_id")}


def scene_brief(scene):
    return {key: (scene or {}).get(key) for key in
            ("activity", "place", "zone", "category", "quality", "source", "version")}


def reminder_brief(fact):
    if not fact:
        return {}
    return {key: fact.get(key) for key in ("event", "place", "time", "zone", "day", "key")}


def current_hour_line(prompt):
    match = re.search(r"这一小时：[^\n]+", str(prompt or ""))
    return match.group(0) if match else ""


def scenario_setup(day, kind):
    """复用 work/simulate_weeks 的确定性入口（golden 同款）：生成 -> 落库。"""
    index = (dt.date.fromisoformat(day) - weeks.START_DAY).days
    plugin, state = weeks.make_plugin(day, weeks.DEFAULT_CITY)
    weeks.sim24.set_clock(weeks.at(day, 0, 0))
    _ctx, items = weeks.build_day_skeleton(plugin, state, day, kind)
    state.commit_schedule_change(day, items, old_items=[], source="ensure",
                                 reason="sd setup")
    rows = list(state.schedule.get(day) or plugin.db.get_schedule(day) or [])
    return plugin, state, rows


def run_scenario(spec):
    day = spec["day"]
    plugin, state, rows = scenario_setup(day, spec["kind"])
    store = install_db_stubs(plugin)
    llm = PromptEchoLLM()
    plugin.llm = llm
    out = {"id": spec["id"], "name": spec["name"], "day": day,
           "entry": spec["entry"], "before": {}, "after": {}, "checks": {}}

    old_block = spec["pick_old"](rows)
    if old_block is None:
        out["error"] = "before_block_missing"
        out["consistent"] = False
        return out, False
    old_bid = block_id(day, old_block)
    old_start = minute_of(old_block.get("start"))
    old_end = span(old_block)[1]
    old_mid = old_start + max(5, (old_end - old_start) // 2)
    out["before"]["block"] = brief(old_block)
    out["before"]["block_id"] = old_bid

    # 旧日记：先按旧块身份写一条 valid，改完必须失效
    store[(day, old_bid)] = {
        "text": "我在%s，%s。" % (old_block.get("place"), old_block.get("activity")),
        "status": "valid", "version": 0,
        "start": old_block.get("start"), "end": old_block.get("end"),
        "place": old_block.get("place"),
    }
    # 旧的 current_action 故意留着旧地点，验证改完场景不读旧动作
    plugin.db.kv_set("current_action", {
        "action": old_block.get("activity"), "place": old_block.get("place"),
        "zone": old_block.get("zone"), "category": old_block.get("category"),
        "at": at(day, old_start // 60, old_start % 60).timestamp(),
        "until": at(day, old_end // 60, old_end % 60).timestamp(),
        "hour": old_start // 60, "source": "before_change",
    })

    set_clock(at(day, old_mid // 60, old_mid % 60))
    out["before"]["scene"] = scene_brief(ledger.scene_at(plugin))

    before_minute = max(0, old_start - 15)
    set_clock(at(day, before_minute // 60, before_minute % 60))
    before_fact = schedule_reminder_fact(
        plugin.db, now=at(day, before_minute // 60, before_minute % 60))
    out["before"]["reminder"] = reminder_brief(before_fact)

    # ---------------- 改日程（生产写入口） ----------------
    if spec["apply"] == "chat":
        change = SC.detect_schedule_change(spec["text"])
        if not change:
            out["error"] = "detect_schedule_change_none"
            out["consistent"] = False
            return out, False
        out["entry"]["detected_change"] = change
        now = at(day, spec["now"][0], spec["now"][1])
        set_clock(now)
        result = state.apply_schedule(
            day, lambda current: SC.apply_change(list(current), change, now),
            source="chat", reason=str(spec["text"]))
    elif spec["apply"] == "at":
        change = dict(spec["change"])
        out["entry"]["change_dict"] = change
        start_text = str(spec.get("start") or old_block.get("start"))
        set_clock(at(day, spec["now"][0], spec["now"][1]))
        result = state.apply_schedule(
            day, lambda current: SC.apply_at(list(current), start_text, change),
            source="command", reason=str(spec.get("reason") or "改日程"))
    else:
        start_text, end_text = spec["window"]
        item = {
            "start": start_text, "end": end_text,
            "activity": spec["activity"], "place": spec["place"],
            "zone": spec["zone"], "category": spec["category"],
        }

        def _mutate(current):
            kept = []
            for row in current:
                if not isinstance(row, dict):
                    kept.append(row)
                    continue
                bounds = span(row)
                if bounds and bounds[0] >= minute_of(start_text) and bounds[1] <= minute_of(end_text):
                    continue
                if bounds and bounds[1] <= minute_of(start_text):
                    kept.append(row)
                elif bounds and bounds[0] >= minute_of(end_text):
                    kept.append(row)
                else:
                    # 与窗口部分重叠：截断到窗口外
                    piece = dict(row)
                    if bounds[0] < minute_of(start_text):
                        piece["end"] = start_text
                        kept.append(piece)
                    if bounds[1] > minute_of(end_text):
                        piece["start"] = end_text
                        piece["activity"] = row.get("activity")
                        kept.append(piece)
            kept.append(dict(item))
            kept.sort(key=lambda row: minute_of(row.get("start")))
            return kept

        set_clock(at(day, spec["now"][0], spec["now"][1]))
        result = state.apply_schedule(day, _mutate, source="chat",
                                      reason=spec["reason"])

    out["change_result"] = {key: result.get(key) for key in
                            ("changed", "version", "changed_hours", "saved")}
    new_rows = list(state.schedule.get(day) or plugin.db.get_schedule(day) or [])
    out["after"]["blocks"] = [brief(row) for row in new_rows]
    new_block = spec["pick_new"](new_rows)
    if new_block is None:
        out["error"] = "after_block_missing"
        out["consistent"] = False
        return out, False
    new_bid = block_id(day, new_block)
    new_start, new_end = span(new_block)
    new_mid = new_start + max(5, (new_end - new_start) // 2)
    out["after"]["block"] = brief(new_block)
    out["after"]["block_id"] = new_bid
    out["debug"] = {
        "sync_calls": plugin.db.sync_calls[-2:],
        "store_keys": sorted("%s|%s" % key for key in store),
        "old_bid": old_bid,
        "changed_ids": sorted(state._changed_block_ids(day, rows, new_rows)),
    }
    if spec["apply"] == "chat":
        # 聊天改完生产入口会清 current_action（amo_schedule._apply_user_schedule_change）
        plugin.db.kv_set("current_action", {})

    # ---------------- 改后：场景 ----------------
    set_clock(at(day, new_mid // 60, new_mid % 60))
    scene = ledger.scene_at(plugin)
    out["after"]["scene"] = scene_brief(scene)

    # ---------------- 改后：提醒 ----------------
    reminder_minute = max(0, new_start - 15)
    new_fact = schedule_reminder_fact(
        plugin.db, now=at(day, reminder_minute // 60, reminder_minute % 60))
    out["after"]["reminder"] = reminder_brief(new_fact)
    old_still_valid = schedule_fact_current(
        plugin.db, before_fact,
        now=at(day, reminder_minute // 60, reminder_minute % 60)) if before_fact else None
    out["after"]["old_reminder_still_valid"] = old_still_valid

    # ---------------- 改后：小日记 ----------------
    diary_minute = min(24 * 60 - 1, new_end + 10)
    set_clock(at(day, diary_minute // 60, diary_minute % 60))
    diary_result = asyncio.run(state.expand_block(day, dict(new_block), new_rows, force=True))
    # 第一次调用是生成本文；后续可能是事实审计/重写，不能拿最后一条当生成提示词
    prompt = next((item for item in llm.prompts if "这一小时：" in item), "")
    text = str(diary_result.get("diary") or "")
    sync_stale_ids = sorted({bid for call in plugin.db.sync_calls
                             for bid in call.get("stale_ids") or []})
    out["after"]["diary"] = {
        "text": text,
        "block_id": diary_result.get("block_id"),
        "version": diary_result.get("version"),
        "pending": bool(diary_result.get("pending")),
        "prompt_current_hour": current_hour_line(prompt),
        "prompt_place_action": (re.search(r"地点=([^、]+)、动作=([^。\n]+)", prompt).groups()
                                if re.search(r"地点=([^、]+)、动作=([^。\n]+)", prompt) else []),
        "guard_violations": loc.check(text, new_block.get("place"), new_block.get("zone")),
        "new_record": store.get((day, new_bid)),
        "old_record_status": (store.get((day, old_bid)) or {}).get("status"),
        "old_record": store.get((day, old_bid)),
        "old_text_seed": "我在%s，%s。" % (old_block.get("place"), old_block.get("activity")),
        "sync_stale_ids": sync_stale_ids,
        "llm_calls": len(llm.prompts),
        "prompt_len": len(prompt),
    }

    # ---------------- 判定 ----------------
    def _norm(value):
        return str(value or "").strip()

    scene_ok = (_norm(scene.get("activity")) == _norm(new_block.get("activity"))
                and _norm(scene.get("place")) == _norm(new_block.get("place")))
    reminder_ok = bool(new_fact) and (
        _norm(new_fact.get("event")) == _norm(new_block.get("activity"))
        and _norm(new_fact.get("place")) == _norm(new_block.get("place"))
        and _norm(new_fact.get("time")) == _norm(new_block.get("start")))
    diary_prompt_ok = (
        _norm(new_block.get("activity")) in out["after"]["diary"]["prompt_current_hour"]
        and _norm(new_block.get("place")) in out["after"]["diary"]["prompt_current_hour"])
    # 7.6.0 人物指代：日记里没有先行词的「他」会补成「老公」（他→老公），
    # 判据要认这个既定改写，不能把正确的个性化日记判成不一致。
    _diary_activity = _norm(new_block.get("activity"))
    _activity_in_text = (_diary_activity in text
                         or _diary_activity.replace("和他", "和老公") in text)
    diary_text_ok = (_norm(new_block.get("place")) in text and _activity_in_text)
    old_place_in_current = (_norm(old_block.get("place"))
                            and _norm(old_block.get("place")) in out["after"]["diary"]["prompt_current_hour"]
                            and _norm(old_block.get("place")) != _norm(new_block.get("place")))
    old_diary_invalid = old_bid in sync_stale_ids
    old_reminder_invalid = (old_still_valid is False)
    new_record_ok = bool(out["after"]["diary"]["new_record"]) and (
        out["after"]["diary"]["new_record"].get("status") == "valid")
    _activity = _norm(new_block.get("activity"))
    _place = _norm(new_block.get("place"))
    block_self_ok = not (
        ("出去" in _activity or "餐厅" in _activity)
        and _place in ("家", "家里", "卧室", "客厅"))
    checks = {
        "block_activity_place_consistent": bool(block_self_ok),
        "scene_matches_block": bool(scene_ok),
        "reminder_matches_block": bool(reminder_ok),
        "diary_prompt_matches_block": bool(diary_prompt_ok),
        "diary_text_matches_block": bool(diary_text_ok),
        "diary_guard_clean": not out["after"]["diary"]["guard_violations"],
        "diary_not_pending": not out["after"]["diary"]["pending"],
        "old_place_absent_from_current_hour": not bool(old_place_in_current),
        "old_block_diary_invalidated": bool(old_diary_invalid),
        "old_reminder_invalidated": bool(old_reminder_invalid),
        "new_diary_record_valid": bool(new_record_ok),
    }
    out["checks"] = checks
    out["consistent"] = all(checks.values())
    return out, out["consistent"]


def _pick_afternoon(rows):
    for row in rows:
        bounds = span(row)
        if (bounds and bounds[0] <= 15 * 60 < bounds[1]
                and str(row.get("category") or "") == "play"
                and str(row.get("zone") or "") == "mall"):
            return row
    return None


def _pick_dinner(rows):
    for row in rows:
        if (str(row.get("category") or "") == "meal"
                and minute_of(row.get("start")) >= 18 * 60):
            return row
    return None


def _pick_work(rows):
    for row in rows:
        if (str(row.get("category") or "") == "work"
                and str(row.get("zone") or "") == "office"
                and minute_of(row.get("start")) >= 17 * 60):
            return row
    return None


def _new_by_activity(activity):
    return lambda rows: find_block(rows, lambda row: str(row.get("activity") or "") == activity)


SPECS = [
    {
        "id": "S1",
        "name": "下午外出改成在家休息",
        "day": "2026-11-01",
        "kind": "rest",
        "entry": {"kind": "chat", "text": "不去了", "now": [15, 0]},
        "text": "不去了",
        "now": (15, 0),
        "apply": "chat",
        "pick_old": _pick_afternoon,
        "pick_new": _new_by_activity("不去了，改回家待着"),
    },
    {
        "id": "S2",
        "name": "晚饭改到餐厅（/改日程 结构化 change：activities/place/category）",
        "day": "2026-11-01",
        "kind": "rest",
        "entry": {"kind": "command_apply_at", "start": "19:00",
                  "change": {"activity": "去餐厅吃晚饭", "place": "餐厅",
                             "category": "meal"},
                  "now": [17, 30]},
        "change": {"activity": "去餐厅吃晚饭", "place": "餐厅", "category": "meal"},
        "reason": "/改日程 19:00 去餐厅吃晚饭",
        "now": (17, 30),
        "apply": "at",
        "pick_old": _pick_dinner,
        "pick_new": _new_by_activity("去餐厅吃晚饭"),
    },
    {
        "id": "S2b",
        "name": "附加探针：同一场景改用对话「我们出去吃吧」",
        "day": "2026-11-01",
        "kind": "rest",
        "entry": {"kind": "chat", "text": "我们出去吃吧", "now": [17, 30]},
        "text": "我们出去吃吧",
        "now": (17, 30),
        "apply": "chat",
        "extra": True,
        "pick_old": _pick_dinner,
        "pick_new": _new_by_activity("和他一起出去吃饭"),
    },
    {
        "id": "S3",
        "name": "加一段加班（17:00-19:00 留在公司）",
        "day": "2026-11-02",
        "kind": "workday",
        "entry": {"kind": "chat_apply_schedule", "window": ["17:00", "19:00"],
                  "activity": "加班", "place": "公司", "zone": "office",
                  "category": "work", "now": [16, 0]},
        "reason": "老公说今晚加个班",
        "now": (16, 0),
        "apply": "mutate",
        "window": ("17:00", "19:00"),
        "activity": "加班",
        "place": "公司",
        "zone": "office",
        "category": "work",
        "pick_old": _pick_work,
        "pick_new": _new_by_activity("加班"),
    },
]


def main(argv=None):
    parser = argparse.ArgumentParser(description="sd 改日程三者一致性（只读）")
    parser.parse_args(argv)
    report = {
        "schema": "sd_schedule_change_scenarios_v1",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "mode": "FakeDB(:memory:) + clock.freeze；未连生产库",
        "scenarios": [],
    }
    ok = True
    extras_ok = True
    try:
        for spec in SPECS:
            row, passed = run_scenario(spec)
            row["extra"] = bool(spec.get("extra"))
            report["scenarios"].append(row)
            if spec.get("extra"):
                extras_ok = extras_ok and passed
            else:
                ok = ok and passed
            print("[%s]%s %s consistent=%s" % (
                spec["id"], " (附加)" if spec.get("extra") else "",
                spec["name"], passed))
            print("  before:", json.dumps(row.get("before", {}), ensure_ascii=False))
            print("  after :", json.dumps(row.get("after", {}).get("block", {}), ensure_ascii=False))
            print("  scene :", json.dumps(row.get("after", {}).get("scene", {}), ensure_ascii=False))
            print("  remind:", json.dumps(row.get("after", {}).get("reminder", {}), ensure_ascii=False))
            print("  checks:", json.dumps(row.get("checks", {}), ensure_ascii=False))
    finally:
        restore_clock()
        report["required_consistent"] = bool(ok)
        report["extra_consistent"] = bool(extras_ok)
        with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
    print("REQUIRED_CONSISTENT", ok, "EXTRA_CONSISTENT", extras_ok)
    print("REPORT", OUT_JSON)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
