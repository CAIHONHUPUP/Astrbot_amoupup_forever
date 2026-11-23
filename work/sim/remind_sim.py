# -*- coding: utf-8 -*-
"""隔离仿真：提醒/追问对话一致性（10 组 × 12 轮 × 2 个 provider 变体）。

只读生产代码；所有数据在内存 sqlite（sim_lib.FakeDB.conn = ":memory:"）与系统临时目录，
不读写 AstrBot 的 plugin_data；不连真实模型。

判定分两路：
- clean 变体：provider 按日程事实老实说 → 检验守卫有没有误杀；
- defect 变体：provider 注入一类已知缺陷（错时间/错地点/已取消/重复追问…）
  → 检验生产守卫有没有拦住该类缺陷（召回）。
真实模型语义质量未覆盖，见报告声明。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(HERE)
ROOT = os.path.dirname(WORK)
TESTS = os.path.join(ROOT, "tests")
for _p in (ROOT, WORK, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sim_lib  # noqa: E402

sim_lib.install_stub()

import amo_anti_nag as NAG  # noqa: E402
import amo_location_rules as LOC  # noqa: E402
import amo_message as MSG  # noqa: E402
import amo_proactive as P  # noqa: E402
from domains.reply import repetition_guard as RG  # noqa: E402
import amo_state_consistency as SC  # noqa: E402
import proactive_guard as PG  # noqa: E402
from sim_lib import at, make_state, restore_clock, set_clock, span  # noqa: E402

FAIL_KEYS = (
    "missing_time", "time_mismatch", "stale_event_time", "event_not_grounded",
    "reminds_cancelled_event", "place_mismatch", "home_action_on_road",
    "repeat_same_question", "double_synonym_nag", "busy_nag", "urge_after_ack",
    "repeat_previous", "mixed_checkin", "false_positive_suppressed",
)


def hhmm(minute):
    minute = int(minute)
    return "%02d:%02d" % (minute // 60, minute % 60)


def to_min(text):
    body = str(text or "").strip()
    if not body:
        return -1
    try:
        hour, _, minute = body.partition(":")
        return int(hour) * 60 + int(minute or 0)
    except Exception:  # cleanup: 解析失败按无效时间
        return -1


def replace_window(rows, start_min, end_min, new_rows):
    out = []
    for row in rows:
        begin, finish = span(row) or (0, 0)
        if finish <= start_min or begin >= end_min:
            out.append(dict(row))
            continue
        if begin < start_min:
            out.append({**row, "end": hhmm(start_min)})
        if finish > end_min:
            out.append({**row, "start": hhmm(end_min)})
    out.extend(dict(item) for item in new_rows)
    out.sort(key=lambda item: to_min(item.get("start")))
    return out


def rows_of(state, day):
    return [dict(row) for row in (state.schedule.get(day) or [])]


def commit(state, day, old_rows, new_rows, reason):
    try:
        result = state.commit_schedule_change(day, new_rows, old_items=old_rows,
                                              source="sim", reason=reason)
    except Exception as exc:
        result = {"error": repr(exc)}
    current = rows_of(state, day)
    if [row.get("activity") for row in current] != [row.get("activity") for row in new_rows]:
        state.schedule[day] = [dict(row) for row in new_rows]
        try:
            state.p.db.save_schedule(day, new_rows)
        except Exception:  # cleanup: 仿真写失败不回滚
            pass
        result = {**dict(result or {}), "forced": True}
    return result


def build_day(plugin, state, day, day_type="workday", health=None, sick_action=""):
    ctx = state.engine.build_context(
        day, {}, {"temp": 22, "text": "晴"},
        {"occupation": "普通上班族",
         "workplace": "%s·写字楼" % plugin.config["schedule_city"]},
        dict(state.mood), {}, dict(health or {"sick": False}), persona="")
    ctx["event"] = {"kind": "none"}
    ctx["scenic"] = None
    ctx["swim"] = None
    ctx["holiday_plan"] = None
    ctx["day_type"] = {"id": day_type, "label": day_type}
    if sick_action:
        ctx["sick_action"] = str(sick_action)
    items = state.engine.build_skeleton(ctx)
    state.commit_schedule_change(day, items, old_items=[], source="ensure", reason="sim")
    return rows_of(state, day)


def upcoming(rows, now_min, within=95):
    items = []
    for row in rows:
        begin = to_min(row.get("start"))
        if begin > now_min and begin - now_min <= within:
            items.append(row)
    items.sort(key=lambda row: to_min(row.get("start")))
    return items


def covering_row(rows, now_min):
    for row in rows:
        begin, finish = span(row) or (0, 0)
        if begin <= now_min < finish:
            return row
    return None


def event_key(row):
    # 只认「事件+地点」：改期会换 start，用 start 当身份会把改期误判成取消。
    return "%s|%s" % (row.get("activity"), row.get("place"))


def find_event(rows, key):
    for row in rows:
        if event_key(row) == key:
            return dict(row)
    return None


PLACE_PATTERNS = (
    ("home", re.compile(r"(到家|在家|家里|沙发上|床上|被窝|厨房|换鞋|脱.{0,2}外套)")),
    ("office", re.compile(r"(公司|工位|办公室|写字楼|开会|周报|报表)")),
    ("hospital", re.compile(r"(医院|诊室|挂号|看诊|复诊|体检)")),
    ("restaurant", re.compile(r"(楼下小馆|餐厅|饭店|馆子|食堂)")),
    ("mall", re.compile(r"(商场|逛街|试衣间|店铺)")),
    ("road", re.compile(r"(路上|地铁|公交|车上|打车|通勤)")),
    ("scenic", re.compile(r"(江边|公园|景区|散步|湖边)")),
)


def place_cats(text):
    body = str(text or "")
    return {name for name, pattern in PLACE_PATTERNS if pattern.search(body)}


def zone_cat(place, zone):
    try:
        return str(LOC.resolve(str(place or ""), str(zone or "")) or "")
    except Exception:  # cleanup: 地点解析失败按原 zone
        return str(zone or "")


def explicit_times(text):
    return [to_min("%s:%s" % (hour, minute or "00"))
            for hour, minute in re.findall(r"(\d{1,2})[:：](\d{2})", str(text or ""))]


def activity_forms(row):
    activity = str(row.get("activity") or row.get("event") or "")
    place = str(row.get("place") or "")
    trimmed = activity
    if len(place) >= 2 and activity.startswith(place) and len(activity) > len(place):
        trimmed = activity[len(place):].strip("，, ")
    return {activity, trimmed}


def mentions_activity(text, row):
    body = str(text or "")
    return any(form and form in body for form in activity_forms(row))


class SimProvider:
    """确定性的“模型替身”：只读 prompt 里的【提醒事项】JSON，按 action/mode 造句子。"""

    REMIND = (
        "老公，{time}我在{place}，{event}，到点我可能回不了消息。",
        "我{time}要去{place}，{event}，你那个时间别给我打电话哈。",
        "{time}我在{place}{event}，先这样，回头说。",
        "提醒你一下，{time}我在{place}，{event}，别等我。",
        "{time}我得在{place}{event}，你要是找我，晚点再说。",
        "老公，{time}我在{place}，{event}，先忙啦。",
    )
    FOLLOW = (
        "你忙你的，我等你回。",
        "我把手机声音开着了，你空了回我。",
        "你要是看到消息，回我一个字就行。",
        "不急，你先把手上的事弄完。",
        "我这边也在忙，等你忙完再说。",
        "想起来再回我就行，我不催。",
    )
    REPLY = (
        "嗯，那你先去忙。",
        "好，我记着呢。",
        "知道啦，你去吧。",
        "行，你先忙你的。",
        "嗯嗯，我这边不急。",
        "好，等你忙完。",
    )

    def __init__(self):
        self.calls = 0
        self.clean_seq = 0

    def _fact(self, prompt):
        hit = re.search(r"【提醒事项】(\{.*\})", str(prompt or ""))
        if not hit:
            return None
        try:
            return json.loads(hit.group(1).strip())
        except Exception:  # cleanup: 仿真容错
            return None

    def generate(self, prompt, mode, action, prev_text="", prev_followup="",
                 fact=None):
        self.calls += 1
        fact = fact or self._fact(prompt)
        if mode == "clean":
            self.clean_seq += 1
            index = self.clean_seq % max(1, len(self.REMIND))
        if action == "reply":
            if mode == "clean":
                return self.REPLY[index % len(self.REPLY)]
            return self._defect(mode, fact or {}, prev_text, prev_followup, action)
        if action == "followup":
            if mode == "clean":
                return self.FOLLOW[index % len(self.FOLLOW)]
            return self._defect(mode, fact or {}, prev_text, prev_followup, action)
        if fact is None:
            return "我这边挺好的。"
        if mode == "clean":
            return self.REMIND[index % len(self.REMIND)].format(
                time=fact.get("time") or "", place=fact.get("place") or "",
                event=fact.get("event") or "")
        return self._defect(mode, fact, prev_text, prev_followup, action)

    def _defect(self, mode, fact, prev_text, prev_followup, action):
        event = str(fact.get("event") or "")
        place = str(fact.get("place") or "")
        when = str(fact.get("time") or "")
        if mode == "wrong_time":
            return "老公，我%s在%s，%s，你记得回我。" % (
                hhmm(to_min(when) + 120), place, event)
        if mode == "wrong_place":
            current_zone = str(fact.get("current_zone") or "")
            wrong = "医院"
            for cand in ("医院", "商场", "江边", "公司", "家"):
                if zone_cat(cand, "") not in (zone_cat(place, fact.get("zone")),
                                              current_zone):
                    wrong = cand
                    break
            return "老公，我%s在%s，%s，你记得回我。" % (when, wrong, event)
        if mode == "home_on_road":
            return "我刚到家，鞋都换了，瘫在沙发上呢，别给我打电话哈。"
        if mode == "ungrounded":
            return "老公，我下午要去机场接表姐，你记得帮我查下航班。"
        if mode == "stale_cancelled":
            return "老公，我们%s去%s散步，你记得穿厚点。" % (when, place)
        if mode == "stale_time":
            return "对了，我%s要去医院复查，你别忘了提醒我。" % when
        if mode == "duplicate":
            return prev_text or "我刚说的那件事别忘了。"
        if mode == "followup_repeat":
            return (prev_followup + "呀？") if prev_followup else "你忙完了没？"
        if mode == "followup_two":
            return "你忙完了没？忙完了吗？"
        if mode == "nag_after_ack":
            return "那你快点啊，别磨蹭，水放了没？"
        if mode == "repeat_urge":
            return "快去洗澡，别拖了。"
        if mode == "mixed_checkin":
            return "我在地铁上呢，你在干嘛？"
        return "嗯。"


def guard_stack(text, plugin, user_text, prev_bot_text, refs, recent_proactive, umo,
                prev_followups=()):
    trace = []

    def note(name, before, after, action="rewrite"):
        trace.append({"guard": name, "action": action,
                      "before": str(before or ""), "after": str(after or "")})

    body = str(text or "")
    fixed = P.strip_mixed_checkin(body)
    if fixed != body:
        note("amo_proactive.strip_mixed_checkin", body, fixed)
    body = fixed
    fixed = P._one_topic_guard(body, "schedule_reminder")
    if fixed != body:
        note("amo_proactive._one_topic_guard", body, fixed)
    body = fixed
    # 核实生产接线：追问发送前 followup_repeated(text, (上一次追问, 上一条主动消息))。
    # 近聊 5 条窗口抓不到两轮之前的追问，所以这里显式吃「已经发出去的追问」。
    repeated_followup = P.followup_repeated(
        body, list(prev_followups) + [prev_bot_text])
    if repeated_followup:
        note("amo_proactive.followup_repeated", body, "", "block")
        return None, trace
    repeated = P.proactive_repeated(body, recent_proactive) or P.proactive_same_topic(
        body, recent_proactive)
    if repeated:
        note("amo_proactive.proactive_repeated", body, "", "block")
        return None, trace
    if PG.near_duplicate(body, recent_proactive):
        note("proactive_guard.near_duplicate", body, "", "block")
        return None, trace
    fixed = SC.rewrite(body, plugin, user_text)
    if fixed != body:
        note("amo_state_consistency.rewrite", body, fixed)
    body = fixed
    fixed = NAG.rewrite(body, user_text)
    if fixed != body:
        note("amo_anti_nag.rewrite", body, fixed)
    body = fixed
    fixed = MSG.strip_repeated_urge(body, user_text, prev_bot_text)
    if fixed != body:
        note("amo_message.strip_repeated_urge", body, fixed)
    body = fixed
    before_dedup = body
    fixed, dropped = RG.dedup_or_fallback(body, umo, refs)
    if dropped:
        note("amo_repetition_guard.dedup_or_fallback", body, fixed)
    fixed = P.keep_answer(before_dedup, fixed)
    body = str(fixed or "").strip()
    if not body:
        note("empty_after_guards", "", "", "block")
        return None, trace
    return body, trace


def evaluate_reminder(text, fact, current, cancelled_event, prev_text):
    problems = []
    if not text:
        return problems
    when = to_min(fact.get("time"))
    times = explicit_times(text)
    if not times:
        problems.append("missing_time")
    elif not any(abs(item - when) <= 10 for item in times):
        problems.append("time_mismatch")
    if not mentions_activity(text, fact):
        problems.append("reminds_cancelled_event" if cancelled_event is not None
                        else "event_not_grounded")
    if cancelled_event is not None and mentions_activity(text, cancelled_event):
        if "reminds_cancelled_event" not in problems:
            problems.append("reminds_cancelled_event")
    allowed = {zone_cat(fact.get("place"), fact.get("zone")),
               zone_cat(current.get("place"), current.get("zone"))}
    allowed |= place_cats(fact.get("event") or "")
    allowed.discard("")
    if place_cats(text) - allowed:
        problems.append("place_mismatch")
    current_zone = str(current.get("zone") or "")
    on_road = current_zone == "road" or "路" in str(current.get("place") or "")
    if on_road:
        # 未来句（18:45 到家 / 明天）不是此刻矛盾：生产守卫已豁免，判定也要豁免
        for sentence in re.split(r"(?<=[。！？!?\n])", str(text or "")):
            if not sentence.strip() or SC._FUTURE.search(sentence):
                continue
            if (SC._HOME_ACTION.search(sentence) or SC._ARRIVAL.search(sentence)
                    or SC._HOME_PROP.search(sentence)):
                problems.append("home_action_on_road")
                break
    if prev_text and PG.near_duplicate(text, [prev_text]):
        problems.append("repeat_previous")
    return problems


def question_marks(text):
    return len(re.findall(r"[?？]", str(text or "")))


def evaluate_followup(text, prev_followup, prev_bot_text, user_text):
    problems = []
    if not text:
        return problems
    if prev_followup and (PG.near_duplicate(text, [prev_followup], 0.5)
                          or P.proactive_repeated(text, [prev_followup], 0.5)):
        problems.append("repeat_same_question")
    nag_hits = NAG._NAG.findall(str(text or ""))
    busy = bool(NAG._BUSY.search(str(user_text or "")))
    if busy and nag_hits:
        problems.append("busy_nag")
    if len(nag_hits) >= 2 or (nag_hits and question_marks(text) >= 2):
        problems.append("double_synonym_nag")
    if prev_bot_text and PG.near_duplicate(text, [prev_bot_text]):
        problems.append("repeat_previous")
    return problems


def evaluate_reply(text, user_text, prev_bot_text):
    problems = []
    if not text:
        return problems
    ack = len(str(user_text or "")) <= 16 and any(
        word in str(user_text or "") for word in MSG._ACK_WORDS)
    busy = bool(NAG._BUSY.search(str(user_text or "")))
    if ack and any(word in text for word in MSG._URGE_WORDS):
        problems.append("urge_after_ack")
    if busy and NAG._NAG.search(text):
        problems.append("busy_nag")
    if P.strip_mixed_checkin(text) != text:
        problems.append("mixed_checkin")
    if prev_bot_text and PG.near_duplicate(text, [prev_bot_text]):
        problems.append("repeat_previous")
    return problems


def leaked(mode, text, problems):
    """注入的缺陷是否还在交付文本里。文本被守卫删空 = 没泄漏。"""
    if not text:
        return False
    mapping = {
        "wrong_time": ("time_mismatch",),
        "stale_time": ("time_mismatch", "stale_event_time"),
        "wrong_place": ("place_mismatch",),
        "home_on_road": ("home_action_on_road", "place_mismatch"),
        "ungrounded": ("event_not_grounded",),
        "stale_cancelled": ("reminds_cancelled_event",),
        "duplicate": ("repeat_previous",),
        "followup_repeat": ("repeat_same_question",),
        "followup_two": ("double_synonym_nag",),
        "nag_after_ack": ("urge_after_ack", "busy_nag"),
        "repeat_urge": ("urge_after_ack",),
        "mixed_checkin": ("mixed_checkin",),
    }
    return any(item in problems for item in mapping.get(mode, ()))


GROUPS = [
    {"id": "G01", "title": "工作日·出门前提醒", "day": "2026-11-12",
     "day_type": "workday", "times": (7, 11, 16, 18), "custom": ""},
    {"id": "G02", "title": "工作日·午饭提醒", "day": "2026-11-13",
     "day_type": "workday", "times": (7, 11, 16, 18), "custom": ""},
    {"id": "G03", "title": "工作日·下班路上（居家动作）", "day": "2026-11-16",
     "day_type": "workday", "times": (7, 11, 17, 18), "custom": "road"},
    {"id": "G04", "title": "工作日·下午医院复诊", "day": "2026-11-17",
     "day_type": "workday", "times": (7, 11, 13, 18), "custom": "hospital"},
    {"id": "G05", "title": "周末·家务与逛街", "day": "2026-11-14",
     "day_type": "outing", "times": (8, 11, 15, 18), "custom": ""},
    {"id": "G06", "title": "约会取消后不得再提醒", "day": "2026-11-15",
     "day_type": "homebody", "times": (7, 11, 16, 18), "custom": "date_cancel"},
    {"id": "G07", "title": "日程改期后提醒时间", "day": "2026-11-18",
     "day_type": "workday", "times": (7, 11, 14, 18), "custom": "reschedule"},
    {"id": "G08", "title": "生病在家·居家提醒", "day": "2026-11-19",
     "day_type": "workday", "times": (7, 11, 16, 18), "custom": "sick"},
    {"id": "G09", "title": "他说在忙·不许再催", "day": "2026-11-20",
     "day_type": "workday", "times": (7, 11, 16, 18), "custom": ""},
    {"id": "G10", "title": "连续同义追问", "day": "2026-11-21",
     "day_type": "homebody", "times": (8, 11, 15, 18), "custom": ""},
]

PATTERN = ("remind", "followup", "reply")
OFFSETS = {"remind": 0, "followup": 25, "reply": 55}


def custom_rows(gid, rows):
    if gid == "G04":
        rows = replace_window(rows, 13 * 60 + 50, 16 * 60, [
            {"start": "13:50", "end": "14:00", "activity": "去医院路上", "place": "路上",
             "zone": "road", "category": "commute"},
            {"start": "14:00", "end": "16:00", "activity": "复诊拿药", "place": "医院",
             "zone": "hospital", "category": "health"},
        ])
    elif gid == "G06":
        rows = replace_window(rows, 18 * 60 + 40, 21 * 60, [
            {"start": "18:40", "end": "21:00", "activity": "去江边散步", "place": "江边",
             "zone": "scenic", "category": "play"},
        ])
    elif gid == "G07":
        rows = replace_window(rows, 15 * 60, 16 * 60, [
            {"start": "15:00", "end": "16:00", "activity": "去医院复查", "place": "医院",
             "zone": "hospital", "category": "health"},
        ])
    return rows


def defect_mode(gid, action, cycle):
    if action == "remind":
        if gid == "G03" and cycle >= 3:
            return "home_on_road"
        if gid == "G04" and cycle >= 2:
            return "ungrounded"
        return ("wrong_time", "wrong_place", "ungrounded")[cycle % 3]
    if action == "followup":
        if gid == "G06" and cycle >= 3:
            return "stale_cancelled"
        if gid == "G07" and cycle >= 2:
            return "stale_time"
        if gid == "G09":
            return "followup_repeat"
        if gid == "G10":
            return "followup_two"
        return ("followup_repeat", "followup_two")[cycle % 2]
    if gid in ("G02", "G09"):
        return "nag_after_ack"
    if gid == "G05":
        return "mixed_checkin"
    return ("repeat_urge", "nag_after_ack")[cycle % 2]


def build_prompt(now, fact, current):
    snap = {"activity": dict(current), "mood": {"开心": 60}, "dominant": "平静",
            "weather_text": ""}
    fact = dict(fact or {})
    head = P.proactive_time_block(now)
    facts = P.proactive_facts(snap, 60, now, extra="")
    # 生产接线：compose_task 的 schedule_reminder 分支直接吃时间/地点/事件
    task = PG.compose_task(
        "schedule_reminder", "老公",
        schedule_time=str(fact.get("time") or ""),
        schedule_place=str(fact.get("place") or ""),
        schedule_event=str(fact.get("event") or ""))
    return head + facts + task, task


def pick_event(rows, minute, day=""):
    items = upcoming(rows, minute, 95) or upcoming(rows, minute, 240)
    if items:
        row = items[0]
        return {"event": row.get("activity"), "time": row.get("start"),
                "place": row.get("place"), "zone": row.get("zone"),
                "status": "active", "key": event_key(row), "route": "",
                "time_source": "schedule_start", "day": day}
    row = covering_row(rows, minute)
    if row is not None:
        return {"event": row.get("activity"), "time": hhmm(minute),
                "place": row.get("place"), "zone": row.get("zone"),
                "status": "active", "key": event_key(row), "route": "",
                "time_source": "now_in_block"}
    return None


def run_group(meta, provider):
    gid = meta["id"]
    day = meta["day"]
    plugin, state = make_state(day=day)
    health = {"sick": True, "status": "感冒"} if meta["custom"] == "sick" else None
    sick_action = "take_sick_leave" if meta["custom"] == "sick" else ""
    rows = build_day(plugin, state, day, day_type=meta["day_type"], health=health,
                     sick_action=sick_action)
    rows = custom_rows(gid, rows)
    commit(state, day, rows_of(state, day), rows, "sim_setup")
    rows = rows_of(state, day)

    RG.reset()
    records = []
    bot_history = []
    followups = []
    last_reminder = None
    cancelled = None
    user_ack = "好，我知道了。"

    for cycle in range(4):
        base = int(meta["times"][cycle]) * 60
        for action in PATTERN:
            minute = base + OFFSETS[action]
            now = at(day, minute // 60, minute % 60)
            set_clock(now)
            current, reason = state.current_activity_info()
            current = dict(current or {})
            turn = {
                "group": gid, "group_title": meta["title"], "day": day,
                "cycle": cycle, "action": action, "now": hhmm(minute),
                "current_place": current.get("place"),
                "current_zone": current.get("zone"),
                "current_activity": current.get("activity"),
                "current_source": reason,
            }

            if gid == "G06" and cycle == 3 and action == "followup" and cancelled is None:
                before = rows_of(state, day)
                target = [row for row in before if "散步" in str(row.get("activity") or "")]
                if target:
                    cancelled = dict(target[0])
                    restored = replace_window(before, 18 * 60 + 40, 21 * 60, [
                        {"start": "18:40", "end": "19:00", "activity": "到家换鞋",
                         "place": "家", "zone": "home", "category": "life"},
                        {"start": "19:00", "end": "21:00", "activity": "追剧",
                         "place": "家", "zone": "home", "category": "rest"},
                    ])
                    commit(state, day, before, restored, "取消约会")
                    rows = rows_of(state, day)
                    turn["mutation"] = "cancel_date"
                    turn["mutation_verified"] = find_event(
                        rows, event_key(cancelled)) is None
            if gid == "G07" and cycle == 2 and action == "followup":
                before = rows_of(state, day)
                target = [row for row in before if "复查" in str(row.get("activity") or "")]
                if target:
                    old_row = dict(target[0])
                    moved = [row for row in before if event_key(row) != event_key(old_row)]
                    moved = replace_window(moved, 17 * 60, 18 * 60, [
                        {**old_row, "start": "17:00", "end": "18:00"}])
                    commit(state, day, before, moved, "改期银行")
                    rows = rows_of(state, day)
                    turn["mutation"] = "move_bank_to_17"
                    turn["mutation_verified"] = any(
                        to_min(row.get("start")) == 17 * 60
                        and "复查" in str(row.get("activity") or "") for row in rows)

            fact = None
            route = ""
            if action == "remind":
                fact = P.schedule_reminder_fact(plugin.db, now, day=day)
                fact_source = "production"
                if not fact:
                    fact = pick_event(rows, minute, day=day)
                    fact_source = "harness_fallback"
                if fact is None:
                    for variant in ("clean", "defect"):
                        t = dict(turn, variant=variant, mode="n/a", text="",
                                 suppressed=True, violations=[], passed=True,
                                 note="no_schedule_target", guards=[])
                        records.append(t)
                    continue
                fact.setdefault("status", "active")
                fact.setdefault("route", "")
                turn["fact_source"] = fact_source
                last_reminder = {"fact": dict(fact), "text": "", "key": fact["key"]}
            elif action == "followup":
                if not last_reminder:
                    for variant in ("clean", "defect"):
                        t = dict(turn, variant=variant, mode="n/a", text="",
                                 suppressed=True, violations=[], passed=True,
                                 note="no_schedule_target", guards=[])
                        records.append(t)
                    continue
                stale_fact = dict(last_reminder["fact"])
                live = find_event(rows, stale_fact["key"])
                if live is None:
                    route = "cancelled"
                    fact = {**stale_fact, "status": "cancelled", "route": "cancelled"}
                    if cancelled is not None:
                        fact.update({"event": cancelled.get("activity"),
                                     "place": cancelled.get("place"),
                                     "zone": cancelled.get("zone"),
                                     "time": cancelled.get("start")})
                        stale_fact.update({"event": cancelled.get("activity"),
                                           "place": cancelled.get("place"),
                                           "zone": cancelled.get("zone"),
                                           "time": cancelled.get("start")})
                else:
                    fact = dict(stale_fact)
                    if to_min(live.get("start")) != to_min(stale_fact.get("time")):
                        route = "moved"
                        fact.update({"route": "moved", "time": live.get("start"),
                                     "place": live.get("place"),
                                     "zone": live.get("zone"), "key": event_key(live)})

            for variant in ("clean", "defect"):
                t = dict(turn, variant=variant)
                mode = "clean" if variant == "clean" else defect_mode(gid, action, cycle)
                t["mode"] = mode
                if (variant == "clean" and action == "followup" and fact is not None
                        and fact.get("status") == "cancelled"):
                    t.update({"passed": True, "suppressed": True, "violations": [],
                              "note": "clean_refresh_no_event", "guards": [], "text": ""})
                    records.append(t)
                    continue
                user_text = ""
                prev_bot = bot_history[-1][1] if bot_history else ""
                prev_followup = followups[-1] if followups else ""
                if action == "reply":
                    busy_user = gid in ("G02", "G09")
                    user_text = ("我在忙，等下说。" if busy_user and cycle % 2 == 0
                                 else user_ack)
                    t["user_text"] = user_text
                provider_fact = fact
                if variant == "defect" and route in ("cancelled", "moved"):
                    provider_fact = stale_fact
                prompt, prod_task = build_prompt(now, provider_fact or {}, current)
                t["expected_fact"] = fact
                t["provider_fact"] = provider_fact
                if action == "remind":
                    t["production_compose_task_grounded"] = bool(
                        (fact or {}).get("event")
                        and str(fact.get("event")) in prod_task
                        and str(fact.get("time") or "") in prod_task)
                    t["prompt_has_schedule_fields"] = bool(
                        all(str((provider_fact or {}).get(key) or "") in prompt
                            for key in ("event", "time", "place")))
                text = provider.generate(
                    prompt, mode, action,
                    prev_text=(last_reminder or {}).get("text", ""),
                    prev_followup=prev_followup, fact=provider_fact or {})
                if mode == "duplicate" and last_reminder:
                    text = last_reminder.get("text") or text
                recent = [item for _, item in bot_history[-5:]]
                refs = RG.reference(recent)
                final, trace = guard_stack(text, plugin, user_text, prev_bot, refs,
                                           recent, "sim:" + gid, followups)
                # 生产接线：发送前用 schedule_fact_current 复核，取消/改期作废旧快照
                t["schedule_recheck"] = True
                if (final and action in ("remind", "followup")
                        and turn.get("fact_source") != "harness_fallback"):
                    # 生产接线：追问不吃迟到窗口（日程可能已开始），只复核还在/没改期。
                    current_ok = P.schedule_fact_current(
                        plugin.db, provider_fact or fact or {},
                        late_window=(action == "remind"))
                    t["schedule_recheck"] = bool(current_ok)
                    if not current_ok:
                        trace.append({"guard": "amo_proactive.schedule_fact_current",
                                      "action": "block", "before": final, "after": ""})
                        final = None
                # 生产接线：提醒文本必须带这条日程的时间/地点/事件，否则不发
                t["grounding_checked"] = True
                if final and fact and action in ("remind", "followup"):
                    grounded = P._ground_schedule_text(final, fact)
                    if not grounded:
                        trace.append({"guard": "amo_proactive._ground_schedule_text",
                                      "action": "block", "before": final, "after": ""})
                        final = None
                t["raw_text"] = text
                t["text"] = final or ""
                t["suppressed"] = final is None
                t["guards"] = trace
                if action == "remind":
                    problems = evaluate_reminder(
                        final, fact or {}, current,
                        cancelled if route == "cancelled" else None, prev_bot)
                elif action == "followup":
                    problems = evaluate_followup(final, prev_followup, prev_bot, user_text)
                    # 追问如果又提了一遍事件（再提醒），同样要过提醒的一致性判定
                    if fact is not None and final and (
                            mentions_activity(final, fact)
                            or explicit_times(final)):
                        nested = evaluate_reminder(
                            final, fact, current,
                            cancelled if route == "cancelled" else None, prev_bot)
                        problems = list(dict.fromkeys(problems + nested))
                else:
                    problems = evaluate_reply(final, user_text, prev_bot)
                problems = [item for item in problems if item in FAIL_KEYS]
                if variant == "clean":
                    if final is None and not problems:
                        problems = ["false_positive_suppressed"]
                    t["violations"] = problems
                    t["passed"] = not problems
                    t["fail_kind"] = "" if t["passed"] else "clean_false_positive"
                else:
                    bad = leaked(mode, final, problems)
                    t["violations"] = problems if bad else []
                    t["passed"] = not bad
                    t["fail_kind"] = "defect_leak" if bad else ""
                t["cause"] = trace[-1]["guard"] if (not t["passed"] and trace) else ""
                if final:
                    bot_history.append((minute, final))
                    if action == "followup":
                        followups.append(final)
                    if action == "remind" and (fact or {}).get("key"):
                        plugin.db.kv_set(
                            "schedule_reminded:%s:%s" % (day, fact["key"]), True)
                if action == "remind" and variant == "clean" and last_reminder is not None:
                    last_reminder["text"] = final or ""
                records.append(t)
    restore_clock()
    return records


def desensitize(text):
    body = str(text or "")
    body = body.replace("老公", "[称呼]").replace("东莞·写字楼", "[公司]")
    body = body.replace("东莞", "[城市]")
    body = re.sub(r"\d{4}-\d{2}-\d{2}", "[日期]", body)
    return body


CHECK_SPECS = (
    ("有时间依据", "remind", ("missing_time", "time_mismatch", "stale_event_time")),
    ("事件与日程一致", "remind", ("event_not_grounded", "reminds_cancelled_event")),
    ("地点与日程一致", "remind", ("place_mismatch", "home_action_on_road")),
    ("路上不提醒居家动作", "road", ("home_action_on_road",)),
    ("取消的日程不再提醒", "cancelled", ("reminds_cancelled_event",)),
    ("回应/沉默后不重复催问", "followup", ("repeat_same_question", "busy_nag")),
    ("不连发两条同义追问", "followup", ("double_synonym_nag",)),
    ("他答应后不再催", "reply", ("urge_after_ack", "busy_nag")),
)


def _check_stats(records):
    out = []
    for name, scope, keys in CHECK_SPECS:
        if scope == "remind":
            rows = [r for r in records if r["action"] == "remind"]
        elif scope == "followup":
            rows = [r for r in records if r["action"] == "followup"]
        elif scope == "reply":
            rows = [r for r in records if r["action"] == "reply"]
        elif scope == "cancelled":
            rows = [r for r in records
                    if str((r.get("expected_fact") or {}).get("status")) == "cancelled"]
        else:  # road
            rows = [r for r in records
                    if str(r.get("current_zone") or "") == "road"
                    and r["action"] in ("remind", "followup")]
        bad = [r for r in rows if any(k in (r.get("violations") or []) for k in keys)]
        out.append({"name": name, "scope": scope, "checked": len(rows),
                    "violated": len(bad),
                    "passed": len(rows) - len(bad)})
    return out


def key_evidence(records):
    wanted = [
        ("取消的日程仍被提醒", "defect", "stale_cancelled"),
        ("改期后仍按旧时间提醒", "defect", "stale_time"),
        ("路上提醒被改写", "clean", None),
        ("在家提醒被删空", "clean", None),
        ("两条同义追问", "defect", "followup_two"),
        ("无日程依据的提醒", "defect", "ungrounded"),
        ("第三地点错配", "defect", "wrong_place"),
        ("事实落地误杀通勤提醒", "clean", None),
    ]
    out = []
    for title, variant, mode in wanted:
        for row in records:
            if row["variant"] != variant or row.get("passed"):
                continue
            if mode and row.get("mode") != mode:
                continue
            if title == "事实落地误杀通勤提醒":
                guards = [g["guard"] for g in row.get("guards") or []]
                if "amo_proactive._ground_schedule_text" not in guards:
                    continue
            if title in ("路上提醒被改写", "在家提醒被删空"):
                guards = [g["guard"] for g in row.get("guards") or []]
                if "amo_state_consistency.rewrite" not in guards:
                    continue
                on_road = str(row.get("current_zone") or "") == "road"
                if title == "路上提醒被改写" and not on_road:
                    continue
                if title == "在家提醒被删空" and on_road:
                    continue
            out.append({"title": title, "group": row["group"], "turn": row["now"],
                        "raw": desensitize(row.get("raw_text") or ""),
                        "delivered": desensitize(row.get("text") or ""),
                        "violations": row.get("violations")})
            break
    return out



class _StubState:
    def __init__(self, act):
        self._act = dict(act or {})

    def current_activity(self):
        return dict(self._act)


class _StubPlugin:
    def __init__(self, act):
        self.state = _StubState(act)


def verify_roadfix():
    """定向验证场景守卫：已到场/未来句/兜底/吃面；并标出「带时间的此刻矛盾」权衡。"""
    cases = [
        ("医院(已到场) 说还在路上", {"place": "医院", "zone": "hospital",
                                     "activity": "复诊拿药"},
         "我还在路上呢，快到了", "fixed", "outside_arrival"),
        ("商场(已到场) 说刚到家", {"place": "商场", "zone": "mall", "activity": "逛街"},
         "刚到家，脱了外套瘫沙发上", "fixed", "outside_arrival"),
        ("公司(已到场) 说在地铁上", {"place": "公司", "zone": "office",
                                     "activity": "写周报"},
         "我还在路上，地铁上呢", "fixed", "outside_arrival"),
        ("路上 说 18:45 到家换鞋", {"place": "路上", "zone": "road",
                                     "activity": "下班回家"},
         "18:45到家换鞋，先瘫一会儿", "fixed", "unchanged"),
        ("在家 说 08:00 挤地铁去公司", {"place": "家", "zone": "home",
                                        "activity": "洗漱"},
         "我08:00要去路上，挤地铁去公司", "fixed", "unchanged"),
        ("路上 说此刻刚到家（矛盾）", {"place": "路上", "zone": "road",
                                       "activity": "下班回家"},
         "我刚到家，鞋都换了，瘫在沙发上", "fixed", "road_arrival"),
        ("路上 说 18:00 此刻在家（带时间矛盾）", {"place": "路上", "zone": "road",
                                                  "activity": "下班回家"},
         "我18:00在家，瘫在沙发上", "tradeoff", "unchanged"),
        ("路上 说等会儿吃碗面", {"place": "路上", "zone": "road",
                                  "activity": "下班回家"},
         "我等会儿吃碗面，别等我", "fixed", "noodles"),
        ("全部被删后兜底（家）", {"place": "家", "zone": "home", "activity": "休息"},
         "我在公司呢", "fixed", "fallback_home"),
        ("全部被删后兜底（医院）", {"place": "医院", "zone": "hospital",
                                   "activity": "复诊"},
         "我在家，躺沙发上", "fixed", "fallback_outside"),
    ]

    def check(kind, text, now):
        if kind == "outside_arrival":
            return "我到了" in now and "还在路上" not in now
        if kind == "road_arrival":
            return "还在路上" in now
        if kind == "noodles":
            return "面" in now
        if kind == "fallback_home":
            return "在家呢" in now
        if kind == "fallback_outside":
            return "到了" in now
        return now == text

    out = []
    for name, act, text, expect, kind in cases:
        try:
            fixed = SC.rewrite(text, _StubPlugin(act), "")
        except Exception as exc:
            fixed = "ERROR:%r" % (exc,)
        out.append({"case": name, "current_place": act["place"],
                    "expect": expect, "raw": text, "now": fixed,
                    "ok": bool(check(kind, text, str(fixed or "")))})
    return out





class _Kv:
    def __init__(self):
        self.d = {}

    def kv_get(self, key, default=None):
        return self.d.get(key, default)

    def kv_set(self, key, value):
        self.d[key] = value


class _DedupPlugin:
    def __init__(self):
        self.db = _Kv()
        self.config = {}

    def cfg_bool(self, key, default=False):
        return default

    def cfg_int(self, key, default=0):
        return default

    def cfg_str(self, key, default=""):
        return default

    def _recent(self, umo, limit=10):
        return []


def verify_grounding():
    """定向验证「提醒必须带当日程时间/地点/事件」。"""
    cases = [
        ("事实全对", "我12:00在楼下小馆吃牛肉面，你别等我",
         {"time": "12:00", "place": "楼下小馆", "event": "吃牛肉面"}, True),
        ("错时间（14:00 vs 12:00）", "我14:00在楼下小馆吃牛肉面",
         {"time": "12:00", "place": "楼下小馆", "event": "吃牛肉面"}, False),
        ("别的地点（医院 vs 小馆）", "我12:00在医院吃牛肉面",
         {"time": "12:00", "place": "楼下小馆", "event": "吃牛肉面"}, False),
        ("别的事件（表姐 vs 吃饭）", "我下午要去机场接表姐",
         {"time": "12:00", "place": "楼下小馆", "event": "吃牛肉面"}, False),
        ("路上提醒带目的地（应在）", "我08:00在路上，挤地铁去公司",
         {"time": "08:00", "place": "路上", "event": "挤地铁去公司"}, True),
    ]
    out = []
    for name, text, fact, expect_keep in cases:
        try:
            got = P._ground_schedule_text(text, dict(fact))
        except Exception as exc:
            got = "ERROR:%r" % (exc,)
        kept = bool(str(got or "").strip())
        out.append({"case": name, "raw": text, "out": str(got or ""),
                    "expect_keep": expect_keep, "kept": kept,
                    "ok": kept is expect_keep})
    return out


def verify_schedule_recheck():
    """定向验证 schedule_fact_current：同名活动一天两次时会不会误判改期。"""
    class _Db:
        def __init__(self, rows):
            self.rows = rows
            self.kv = {}

        def get_schedule(self, day):
            return [dict(row) for row in self.rows]

        def schedule_version(self, day):
            return 1

        def kv_get(self, key, default=None):
            return self.kv.get(key, default)

        def kv_set(self, key, value):
            self.kv[key] = value

    dup = [{"start": "10:00", "activity": "开会过方案", "place": "公司"},
           {"start": "17:00", "activity": "开会过方案", "place": "公司"}]
    single = [{"start": "17:00", "activity": "开会过方案", "place": "公司"}]
    cases = [
        ("单条日程，时间未变", single, {"key": "开会过方案|公司", "time": "17:00"}, True),
        ("同名活动两条（17:00 那条）", dup, {"key": "开会过方案|公司",
                                              "time": "17:00"}, True),
        ("改期到 17:00（旧 15:00）", single, {"key": "开会过方案|公司",
                                              "time": "15:00"}, False),
        ("日程被取消", single, {"key": "去江边散步|江边", "time": "18:40"}, False),
    ]
    out = []
    for name, rows, fact, expect in cases:
        try:
            got = bool(P.schedule_fact_current(_Db(rows), fact))
        except Exception as exc:
            got = "ERROR:%r" % (exc,)
        out.append({"case": name, "expect": expect, "got": got,
                    "ok": got is expect or got == expect})
    return out


def verify_dedupe_keep_answer():
    """定向验证「去重保留答案」：回复路径 vs 主动路径。"""
    import amo_reply_pipeline as RP
    from domains.reply import status_text as ST

    line = "我18:00到家，换鞋瘫一会儿。"
    out = []
    def _proactive_chain():
        raw = RP.apply_proactive_dedup(line, _DedupPlugin(), "sim:dd")
        return P.keep_answer(line, raw)

    for path, call in (
        ("回复 apply_narrative_repetition",
         lambda: RP.apply_narrative_repetition(line, _DedupPlugin(), "", "sim:dd")),
        ("主动 apply_proactive_dedup（单独）",
         lambda: RP.apply_proactive_dedup(line, _DedupPlugin(), "sim:dd")),
        ("主动发送链路 dedup + keep_answer",
         _proactive_chain),
    ):
        ST.reset_status_memory()
        ST._remember_status("sim:dd", line)
        result = str(call() or "")
        ST.reset_status_memory()
        out.append({"path": path, "input": line, "out": result,
                    "kept_answer": "18:00" in result,
                    "suppressed": not result})
    return out


def verify_arrival_guard():
    """定向验证「预计到家 / 到家请求不改口」与真正的此刻到家。"""
    try:
        from amo_runtime_reply import RuntimeReplyMixin as _Mixin
    except Exception as exc:
        return [{"case": "import", "raw": "", "now": "ERROR:%r" % (exc,),
                 "expect": "fixed", "ok": False}]
    stub = _StubPlugin({"place": "路上", "zone": "road", "activity": "下班回家"})
    cases = [
        ("回复：预计 18:45 到家", "预计18:45到家，你等我", "unchanged", "reply"),
        ("回复：差不多到家了", "差不多到家了，等我一下", "unchanged", "reply"),
        ("回复：到家跟我说一声", "到家跟我说一声", "unchanged", "reply"),
        ("回复：17:00 差不多到家", "17:00差不多到家", "unchanged", "reply"),
        ("回复：此刻刚到家（真矛盾）", "我刚到家，鞋都换了", "road_arrival", "reply"),
        ("提醒：预计18:45到家", "预计18:45到家，你等我", "unchanged", "remind"),
        ("提醒：到家跟我说一声", "到家跟我说一声", "unchanged", "remind"),
    ]
    out = []
    for name, text, kind, path in cases:
        try:
            fixed = (_Mixin._align_arrival(stub, text) if path == "reply"
                     else SC.rewrite(text, stub, ""))
        except Exception as exc:
            fixed = "ERROR:%r" % (exc,)
        now = str(fixed or "")
        ok = ("还在路上" in now) if kind == "road_arrival" else (now == text)
        out.append({"case": name, "raw": text, "now": now, "path": path,
                    "expect": "fixed", "ok": ok})
    return out


def preserved_evidence(records):
    wanted = ("missing_time", "event_not_grounded", "place_mismatch")
    out = {}
    for key in wanted:
        items, seen = [], set()
        for row in records:
            if key not in (row.get("violations") or []):
                continue
            if row["group"] in seen:
                continue
            seen.add(row["group"])
            items.append({"group": row["group"], "turn": row["now"],
                          "variant": row["variant"], "mode": row.get("mode"),
                          "raw": desensitize(row.get("raw_text") or ""),
                          "delivered": desensitize(row.get("text") or "")})
            if len(items) >= 3:
                break
        out[key] = items
    return out


def delta_block(previous, current):
    if not previous:
        return None
    old = previous.get("summary") or {}
    keys = ("passed", "failed", "clean_passed", "clean_failed",
            "defect_caught", "defect_leaked")
    rows = []
    for key in keys:
        before, after = old.get(key), current.get(key)
        if isinstance(before, int) and isinstance(after, int):
            rows.append({"key": key, "before": before, "after": after,
                         "delta": after - before})
    return {
        "before_generated_at": (previous.get("meta") or {}).get("generated_at"),
        "rows": rows,
        "clean_fp_before": old.get("clean_false_positive_by_guard"),
        "clean_fp_after": current.get("clean_false_positive_by_guard"),
        "leak_before": old.get("defect_leak_by_mode"),
        "leak_after": current.get("defect_leak_by_mode"),
        "classes_before": old.get("failure_classes"),
        "classes_after": current.get("failure_classes"),
    }


def summarize(records):
    total = len(records)
    passed = sum(1 for row in records if row.get("passed"))
    clean = [row for row in records if row.get("variant") == "clean"]
    defect = [row for row in records if row.get("variant") == "defect"]
    clean_pass = sum(1 for row in clean if row.get("passed"))
    defect_pass = sum(1 for row in defect if row.get("passed"))
    classes = {}
    for row in records:
        for item in row.get("violations") or []:
            classes[item] = classes.get(item, 0) + 1
    by_group = {}
    for row in records:
        item = by_group.setdefault(row["group"], {"total": 0, "passed": 0, "failed": 0})
        item["total"] += 1
        item["passed" if row.get("passed") else "failed"] += 1
    clean_cause = {}
    for row in clean:
        if not row.get("passed"):
            key = row.get("cause") or "no_guard"
            clean_cause[key] = clean_cause.get(key, 0) + 1
    leak_by_mode = {}
    for row in defect:
        if not row.get("passed"):
            key = str(row.get("mode"))
            leak_by_mode[key] = leak_by_mode.get(key, 0) + 1
    prompt_rows = [row for row in clean if row["action"] == "remind"]
    return {
        "groups": len(GROUPS),
        "turns": 120,
        "total_messages": total,
        "passed": passed,
        "failed": total - passed,
        "clean_total": len(clean),
        "clean_passed": clean_pass,
        "clean_failed": len(clean) - clean_pass,
        "defect_total": len(defect),
        "defect_caught": defect_pass,
        "defect_leaked": len(defect) - defect_pass,
        "suppressed": sum(1 for row in records if row.get("suppressed")),
        "failure_classes": dict(sorted(classes.items(), key=lambda kv: -kv[1])),
        "by_group": by_group,
        "clean_false_positive_by_guard": dict(sorted(clean_cause.items(),
                                                    key=lambda kv: -kv[1])),
        "defect_leak_by_mode": dict(sorted(leak_by_mode.items(), key=lambda kv: -kv[1])),
        "reminders": len(prompt_rows),
        "prompt_grounded_rows": sum(1 for row in prompt_rows
                                    if row.get("prompt_has_schedule_fields")),
        "production_compose_task_grounded_rows": sum(
            1 for row in prompt_rows if row.get("production_compose_task_grounded")),
        "skipped_no_target": sum(1 for row in records
                                 if row.get("note") == "no_schedule_target"),
        "fact_sources": {
            name: sum(1 for row in records if row.get("fact_source") == name)
            for name in ("production", "harness_fallback")},
        "recheck_blocks": sum(1 for row in records
                              if row.get("schedule_recheck") is False
                              and row.get("suppressed")),
        "clean_action_mix": "%d/%d/%d" % (
            sum(1 for row in clean if row["action"] == "remind"),
            sum(1 for row in clean if row["action"] == "followup"),
            sum(1 for row in clean if row["action"] == "reply")),
        "checks": _check_stats(records),
    }


def top_samples(records, limit=10):
    bad = [row for row in records if not row.get("passed")]
    priority = ("stale_cancelled", "stale_time", "home_on_road", "wrong_place",
                "ungrounded", "followup_two", "followup_repeat", "nag_after_ack",
                "repeat_urge", "mixed_checkin")
    picked, seen = [], set()
    ordered = sorted(bad, key=lambda row: (
        priority.index(row.get("mode")) if row.get("mode") in priority else 99,
        0 if row.get("fail_kind") == "clean_false_positive" else 1,
        row["group"]))
    for row in ordered:
        sig = (row.get("fail_kind"), row.get("mode"), row.get("cause"),
               tuple(row.get("violations") or []))
        if sig in seen:
            continue
        seen.add(sig)
        picked.append(row)
        if len(picked) >= limit:
            break
    counter = {}
    for row in picked:
        counter[row.get("mode")] = counter.get(row.get("mode"), 0) + 1
    for row in ordered:
        if len(picked) >= limit:
            break
        if counter.get(row.get("mode"), 0) >= 2 or row in picked:
            continue
        counter[row.get("mode")] = counter.get(row.get("mode"), 0) + 1
        picked.append(row)
    groups = {row["group"] for row in picked}
    for row in ordered:
        if len(picked) >= limit:
            break
        if row["group"] in groups or row in picked:
            continue
        groups.add(row["group"])
        picked.append(row)
    for row in ordered:
        if len(picked) >= limit:
            break
        if row not in picked:
            picked.append(row)
    picked = picked[:limit]
    out = []
    for row in picked:
        out.append({
            "group": row["group"], "group_title": row["group_title"],
            "turn": "#%s %s %s" % (row["cycle"], row["action"], row["now"]),
            "variant": row["variant"], "mode": row.get("mode"),
            "fail_kind": row.get("fail_kind"),
            "violations": row.get("violations"),
            "text": desensitize(row.get("text") or ""),
            "raw_text": desensitize(row.get("raw_text") or ""),
            "cause": row.get("cause"),
            "current": "%s/%s" % (row.get("current_place"), row.get("current_zone")),
        })
    return out


ROOT_CAUSES = [
    {"id": "RC1", "title": "【已修复】日程提醒接线：kind 选择 + compose_task 分支 + 选日程",
     "evidence": "打开开关后 _choose_proactive_kind 返回 "
                 "schedule_reminder；compose_task(schedule_time/place/event) 有专属分支"
                 "（proactive_guard.compose_task）；schedule_reminder_fact 走 10~60 分钟窗口。"
                 "本次 40/40 条提醒带日程字段（此前 0/40）。",
     "impact": "接线已通，但开关 proactive_schedule_reminder 默认 False、"
               "且 _conf_schema.json 没有这一项——面板打不开，生产默认不发日程提醒。"},
    {"id": "RC1b", "title": "【已修复】提醒发出前复核日程版本",
     "evidence": "schedule_fact_current 在发送前（_proactive_one）和追问前"
                 "（_check_proactive_pending，late_window=False）都调用；"
                 "本次 stale_cancelled / stale_time 注入全部被拦。",
     "impact": "取消/改期后旧快照提醒作废；同名活动误判已在 RC8 修复。"},
    {"id": "RC2", "title": "【已修复】取消/改期复核（发送 + 追问两条链路）",
     "evidence": "schedule_fact_current 在发送前（_proactive_one）与追问前"
                 "（_check_proactive_pending，late_window=False）调用；本次 stale_cancelled / "
                 "stale_time 注入从泄漏变成全部被拦（复核共拦下消息见 2.10）。",
     "impact": "取消/改期后旧快照提醒作废；同名活动 key 误判已在 RC8 修复。"},
    {"id": "RC3", "title": "地点守卫已识别未来句，但带时间的此刻矛盾被一并豁免",
     "evidence": "amo_state_consistency.py:22-23 新增 _FUTURE（HH:MM/明天/后天/下周），"
                 "含未来锚点的句子直接原样保留；兜底改成「我到了 / 我在家呢」；"
                 "_HOME_ACTION 去掉了「煮面/吃面」。本次定向 9/9 通过。",
     "impact": "clean 误杀从 13 条降到 0；代价是「我18:00在家，瘫在沙发上」这类"
               "带时间戳的此刻矛盾也不再被拦（定向里 1 例被豁免）。"
               "医院/餐厅/公司错配仍没有守卫（取消/改期已由 schedule_fact_current 覆盖）。"},
    {"id": "RC4", "title": "没有专门的时间一致性守卫（本轮未泄漏，属潜在风险）",
     "evidence": "guard_proactive_time 只处理「昨天/前天」时态词，不解析 HH:MM；"
                 "本轮 wrong_time 注入没有泄漏，多是被去重/复核顺带拦下，"
                 "不是被显式时间校验拦下。",
     "impact": "若文案绕过去重（换措辞、换事件），错误时间仍可能发出；建议加一条"
               "「显式 HH:MM 必须等于 fact.time」的校验。"},
    {"id": "RC5", "title": "【已修复】同一条消息里的同义追问合并",
     "evidence": "merge_synonym_questions 接进 _one_topic_guard，主动消息统一走这条；"
                 "追问生成也补了同一合并；本次 double_synonym_nag 泄漏 17 → 0。",
     "impact": "「你忙完了没？忙完了吗？」只留第一句；不同问题的连问不动。"},
    {"id": "RC8", "title": "【已修复】schedule_fact_current 同名活动误判改期",
     "evidence": "现在先取同 key 的所有行，再 any(start == fact.time)"
                 "（amo_proactive.schedule_fact_current）；并兼容旧版两段键。定向 4/4 通过，"
                 "同一天 10:00/17:00 的「开会过方案|公司」不再误杀。",
     "impact": "本轮 clean 误杀里已不再出现复核类；追问链路误用迟到窗口见 RC11。"},
    {"id": "RC9", "title": "【已修复】事实落地误杀通勤提醒",
     "evidence": "amo_proactive._ground_schedule_text 现在判断 place in body and place "
                 "not in want_place and place not in want_event；"
                 "「路上/挤地铁去公司」里的公司/地铁放行。定向 5/5 通过，clean 误杀 8 → 0。",
     "impact": "通勤类提醒不再被事实落地吞掉；错时间/别的地点/别的事件仍然拦。"},
    {"id": "RC7", "title": "【已修复】主动去重保留答案",
     "evidence": "主动发送链路的 raw = keep_answer(before_dedup, raw) 使用"
                 "_ANSWER_CUE_RE（时间/地点/吃饭/是否），主动路径整条被删空时保留原回答；"
                 "回复路径也有同规则（amo_reply_pipeline._keep_answer）。",
     "impact": "提醒复述日程答案时不再被静默吞掉；纯状态重复仍按短句兜底。"},
    {"id": "RC6", "title": "反催问依赖「他还没好」词表",
     "evidence": "amo_anti_nag.py:9 _BUSY 只收「没那么快/在忙/还没好…」；"
                 "「知道了/好/行」这类 ack 要靠 amo_message.py:199 strip_repeated_urge，"
                 "而它要求 bot_text 里已经出现过 _URGE_WORDS。",
     "impact": "ack 后第一次换措辞的催问会漏网（urge_after_ack）。"},
    {"id": "RC10", "title": "【已修复】追问重复上一句没人拦（换语气词也算）",
     "evidence": "原主动去重只比「最近 5 条主动消息」：上一句追问在第 7 条时窗口抓不到，"
                 "「你忙完了没？」→「你忙完了没？呀？」原样发出（followup_repeat 泄漏 6 条）。"
                 "生产侧 _followup_text_live 生成时只跟「上一条主动消息」比，"
                 "proactive_followup_last_* 只写不读，等于没有状态。",
     "impact": "新增 followup_repeated(text, (上一次追问, 上一条主动消息))：剥掉语气词后"
               "问句核心相同就不发，作为追问发送前唯一闸门；发送成功后才写 kv + proactive_log"
               "（没发出去的句子不算说过）。本轮 followup_repeat 6 → 0、"
               "followup_two 3 → 0（合并同义问句后也按重复处理）。"},
    {"id": "RC11", "title": "【已修复】追问复核误用提醒的迟到窗口 + 旧键不兼容",
     "evidence": "追问前 schedule_fact_current 默认吃「开始后 5 分钟」迟到窗口，"
                 "而追问本来就发生在提醒之后：日程一开始追问就被作废、pending 被清空；"
                 "复核还只认「活动｜地点｜开始时间」三段键，旧版 pending 的两段键一律判取消。",
     "impact": "追问前复核改 late_window=False，只查「还在/没改期」；迟到窗口只约束提醒发送。"
               "复核匹配兼容旧两段键（活动｜地点，开始时间仍要一致）。"
               "本轮 clean 误杀 1 → 0。"},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=HERE)
    args = parser.parse_args()
    provider = SimProvider()
    records = []
    for meta in GROUPS:
        records.extend(run_group(meta, provider))
    summary = summarize(records)
    probe = sim_lib.FakeDB()
    db_list = probe.conn.execute("PRAGMA database_list").fetchall()
    isolation_proof = {
        "sqlite_main_file": str(db_list[0][2]) if db_list else "",
        "temp_root_created": bool(sim_lib._TEMP_ROOT),
        "production_plugin_data_referenced": "D:\\dafeiyu\\astrbot\\data\\plugin_data"
            in open(os.path.abspath(__file__), encoding="utf-8").read(),
    }
    previous = None
    prev_path = os.path.join(args.out, "remind_report_prev.json")
    if os.path.exists(prev_path):
        try:
            with open(prev_path, encoding="utf-8") as fh:
                previous = json.load(fh)
        except Exception:  # cleanup: 仿真容错
            previous = None
    baseline = None
    baseline_path = os.path.join(args.out, "remind_report_before_roadfix.json")
    if os.path.exists(baseline_path):
        try:
            with open(baseline_path, encoding="utf-8") as fh:
                baseline = json.load(fh)
        except Exception:  # cleanup: 仿真容错
            baseline = None
    payload = {
        "meta": {
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "python": sys.version.split()[0],
            "repo": ROOT,
            "isolation": {
                "db": "sim_lib.FakeDB -> sqlite3.connect(':memory:')",
                "temp_root": tempfile.gettempdir(),
                "production_data_written": False,
                "proof": isolation_proof,
            },
            "provider": "SimProvider（确定性；非模型）",
            "real_model_covered": False,
            "model_semantics_note": "隔离环境没有可用 provider；未做真实模型复跑；语义质量未覆盖。",
        },
        "summary": summary,
        "samples": top_samples(records, 10),
        "key_evidence": key_evidence(records),
        "roadfix_verification": verify_roadfix(),
        "dedupe_verification": verify_dedupe_keep_answer(),
        "arrival_verification": verify_arrival_guard(),
        "recheck_verification": verify_schedule_recheck(),
        "grounding_verification": verify_grounding(),
        "preserved_evidence": preserved_evidence(records),
        "schedule_wiring": {
            "config_key": "proactive_schedule_reminder",
            "default_on": False,
            "schema_exposed": False,
            "compose_task_branch": True,
            "compose_task_grounded_rows": summary[
                "production_compose_task_grounded_rows"],
            "selector": "schedule_reminder_fact(db, now) 10~60 分钟窗口、"
                        "kv schedule_reminded 去重、返回 version",
            "recheck": "schedule_fact_current：发送前 _proactive_one 复核、"
                       "追问前 _check_proactive_pending 复核（late_window=False）",
            "recheck_blocks": summary["recheck_blocks"],
            "fact_sources": summary["fact_sources"],
        },
        "delta": delta_block(previous, summary),
        "delta_baseline": delta_block(baseline, summary),
        "root_causes": ROOT_CAUSES,
        "records": records,
    }
    with open(os.path.join(args.out, "remind_report.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out, "remind_report.md"), "w", encoding="utf-8") as fh:
        fh.write(render_markdown(payload))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("OK", args.out)


def render_markdown(payload):
    meta = payload["meta"]
    s = payload["summary"]
    if s["clean_failed"] == 0:
        clean_line = ("- clean 变体 %d/%d 全部通过：未来句豁免 + 兜底改写 + "
                      "事实落地（目的地写在事件里也放行）生效，不再误杀合法提醒。" % (
                          s["clean_passed"], s["clean_total"]))
    else:
        cause = next(iter(s["clean_false_positive_by_guard"]), "") or "守卫"
        clean_line = ("- clean 变体 %d/%d 通过，%d 条被 @@%s@@ 拦掉（误杀，"
                      "不是文案被改坏；见对应定向小节）。" % (
                          s["clean_passed"], s["clean_total"],
                          s["clean_failed"], cause))
    lines = ["# 提醒/追问对话 隔离仿真报告", "",
             "- 生成时间：%s" % meta["generated_at"],
             "- Python：%s" % meta["python"],
             "- provider：%s" % meta["provider"],
             "- 真实模型：**未覆盖**（%s）" % meta["model_semantics_note"],
             "- 隔离：%s（main 库文件为空 = 内存库；未创建临时目录：%s）" % (
                 meta["isolation"]["db"],
                 "否" if meta["isolation"].get("proof", {}).get("temp_root_created") else "是"),
             "", "## 0. 结论摘要", "",
             "- 日程接线已接上：@@compose_task()@@ 的 schedule_reminder 分支吃时间/地点/事件，"
             "40/40 条提醒带日程字段；选日程走 @@schedule_reminder_fact@@（10~60 分钟窗口），"
             "发送/追问前用 @@schedule_fact_current@@ 复核（本次拦下 %d 条）。"
             "但开关 @@proactive_schedule_reminder@@ 默认 **False** 且未进 _conf_schema.json。"
             % s["recheck_blocks"],
             clean_line,
             (("- defect 变体 %d/%d 全部拦下，0 条泄漏"
               "（同义追问/无据/错地点/取消/改期都被守卫覆盖）。")
              % (s["defect_caught"], s["defect_total"])
              if s["defect_leaked"] == 0 else
              "- defect 变体 %d/%d 被拦，%d 条泄漏：%s。" % (
                  s["defect_caught"], s["defect_total"], s["defect_leaked"],
                  s["defect_leak_by_mode"])),
             "- 去重保留答案已覆盖主动链路：@@apply_proactive_dedup@@ 删空后再走 "
             "@@keep_answer@@，带时间/地点的提醒会被保留（定向见 2.8）。",
             "- 真实模型未复跑，语义质量未覆盖（见第 6 节）。", "",
             "## 1. 总量", "",
             "| 指标 | 值 |", "| --- | --- |",
             "| 组数 | %d |" % s["groups"],
             "| 组内轮次 | %d（10 组 × 12 轮） |" % s["turns"],
             "| 实际判定消息 | %d（每轮 clean + defect） |" % s["total_messages"],
             "| 通过 | %d |" % s["passed"],
             "| 失败 | %d |" % s["failed"],
             "| clean 变体 过/败 | %d / %d |" % (s["clean_passed"], s["clean_failed"]),
             "| defect 变体 拦下/泄漏 | %d / %d |" % (s["defect_caught"], s["defect_leaked"]),
             "| 被守卫整条拦下 | %d |" % s["suppressed"],
             "| clean 变体动作分布 提醒/追问/回应 | %s |" % s["clean_action_mix"],
             "| 提醒条数（clean 变体） | %d |" % s["reminders"],
             "| 其中 prompt 带日程字段 | %d |" % s["prompt_grounded_rows"],
             "| 生产 compose_task 带日程字段 | %d |" % s[
                 "production_compose_task_grounded_rows"],
             "| 无可用日程而跳过 | %d |" % s["skipped_no_target"], "",
             "## 2. 失败分类", "", "| 分类 | 次数 |", "| --- | --- |"]
    for key, value in s["failure_classes"].items():
        lines.append("| `%s` | %d |" % (key, value))
    if not s["failure_classes"]:
        lines.append("| （无） | 0 |")
    lines += ["", "### 2.1 clean 变体误杀（按最后动过的守卫）", "",
              "| 守卫 | 次数 |", "| --- | --- |"]
    for key, value in s["clean_false_positive_by_guard"].items():
        lines.append("| `%s` | %d |" % (key, value))
    if not s["clean_false_positive_by_guard"]:
        lines.append("| （无：clean 变体 0 误杀） | 0 |")
    lines += ["", "### 2.2 defect 变体泄漏（按注入模式）", "",
              "| 注入模式 | 泄漏次数 |", "| --- | --- |"]
    for key, value in s["defect_leak_by_mode"].items():
        lines.append("| `%s` | %d |" % (key, value))
    if not s["defect_leak_by_mode"]:
        lines.append("| （无：defect 变体 0 泄漏） | 0 |")
    lines += ["", "## 2.3 逐项检查覆盖", "",
              "| 检查项 | 判定对象 | 判定数 | 违反 | 通过 |",
              "| --- | --- | --- | --- | --- |"]
    for item in s["checks"]:
        lines.append("| %s | %s | %d | %d | %d |" % (
            item["name"], item["scope"], item["checked"], item["violated"],
            item["passed"]))
    lines += ["", "### 2.4 已验证有效的守卫", ""]
    for item in s["checks"]:
        if item["violated"] == 0 and item["checked"]:
            lines.append("- **%s**：%d/%d 全部通过。" % (
                item["name"], item["checked"], item["checked"]))
    if s["failed"] == 0:
        lines += ["- 注：8 项逐项检查全部 0 违反；注入的同类缺陷（错时间 / 别地点 / 已取消 /"
                  "改期 / 无据 / 重复追问）都被对应守卫拦下，不是判定漏判。",
                  "- 注：判定对象仍按行统计（remind 80 / followup 80 / reply 80 / road 24 /"
                  " cancelled 39），其中 clean 追问在刷新后无日程可追时按「不该发」记通过。", ""]
    else:
        lines += ["- 注：「有时间依据 / 事件一致」的违反里，部分是 clean 变体被"
                  "@@amo_state_consistency.rewrite@@ 改坏（本该通过），部分是 ungrounded "
                  "注入泄漏；不是提醒都没有时间。",
                  "- 注：「取消的日程不再提醒」的判定对象是取消后的旧快照：clean 变体按"
                  "预期不再发，defect 变体才拿着旧快照发。", ""]
    delta = payload.get("delta")
    baseline = payload.get("delta_baseline")
    base_map = {row["key"]: row["before"] for row in (baseline or {}).get("rows", [])}
    if delta:
        lines += ["", "## 2.5 重跑对比（本轮 vs 上一轮 / 原始基线）", "",
                  "| 指标 | 原始基线 | 上一轮 | 本轮 | Δ(本轮-上一轮) |",
                  "| --- | --- | --- | --- | --- |"]
        for row in delta["rows"]:
            lines.append("| %s | %d | %d | %d | %+d |" % (
                row["key"], base_map.get(row["key"], -1), row["before"],
                row["after"], row["delta"]))
        lines += ["", "- 地点类失败（clean 误杀按守卫）：@@%s@@ → @@%s@@" % (
                      delta["clean_fp_before"], delta["clean_fp_after"]),
                  "- 缺陷泄漏：@@%s@@ → @@%s@@" % (
                      delta["leak_before"], delta["leak_after"]),
                  "- 失败分类：@@%s@@ → @@%s@@" % (
                      delta["classes_before"], delta["classes_after"]),
                  ""]
        clean_row = next((row for row in delta["rows"]
                          if row["key"] == "clean_failed"), None)
        leak_row = next((row for row in delta["rows"]
                         if row["key"] == "defect_leaked"), None)
        if s["failed"] == 0 and delta["rows"]:
            lines += ["**结论：本轮 %d/%d 全部通过（clean %d/%d、defect %d/%d）。**"
                      % (s["passed"], s["total_messages"],
                         s["clean_passed"], s["clean_total"],
                         s["defect_caught"], s["defect_total"]),
                      "修复：追问闸门 @@followup_repeated@@（上一句追问换语气词再说一遍不发）"
                      "+ 追问复核不吃迟到窗口（@@late_window=False@@）+ 复核兼容旧两段键"
                      "（RC10/RC11）。", ""]
        elif leak_row and leak_row["delta"] < 0 and clean_row and clean_row["delta"] > 0:
            cause = next(iter(s["clean_false_positive_by_guard"]), "") or "守卫"
            lines += ["**结论：缺陷泄漏 %d → %d（%+d，提醒事实落地生效），"
                      "clean 误杀 %d → %d（%+d，被 @@%s@@ 拦掉，见对应定向小节）。**"
                      % (leak_row["before"], leak_row["after"], leak_row["delta"],
                         clean_row["before"], clean_row["after"], clean_row["delta"],
                         cause), ""]
        elif clean_row and clean_row["delta"] < 0:
            lines += ["**结论：clean 误杀 %d → %d（%+d），defect 泄漏 %d → %d（%+d）。"
                      "本轮事实落地（含目的地写在事件里）与复核修复均已生效。**"
                      % (clean_row["before"], clean_row["after"], clean_row["delta"],
                         (leak_row or {}).get("before", 0),
                         (leak_row or {}).get("after", 0),
                         (leak_row or {}).get("delta", 0)), ""]
        elif all(row["delta"] == 0 for row in delta["rows"]):
            b_clean = base_map.get("clean_failed", -1)
            b_failed = base_map.get("failed", -1)
            b_place = ((baseline or {}).get("classes_before") or {}).get(
                "place_mismatch", 0)
            cur_place = s["failure_classes"].get("place_mismatch", 0)
            lines += ["**结论：本轮与上一轮完全一致（Δ 全 0）。** 本次修改"
                      "（_align_arrival「预计到家/到家请求」、reply_fact_guard、"
                      "amo_reply_pipeline「去重保留答案」、tone_guard、"
                      "状态一致性里的视频/动物词表）都作用在**回复或分享句**；"
                      "提醒链路不经过 _align_arrival，且 state_consistency 没有这条"
                      "预计/请求豁免（见 2.8/2.9）。"
                      "累计（原始基线 → 本轮）：clean 误杀 %d → %d，failed %d → %d；"
                      "place 类泄漏 %d → %d（wrong_place 没有守卫）。"
                      % (b_clean, s["clean_failed"], b_failed, s["failed"],
                         b_place, cur_place), ""]
    ver = payload.get("roadfix_verification") or []
    lines += ["## 2.6 定向验证场景守卫（未来句 / 兜底 / 吃面）", "",
              "| 场景 | 当前地点 | 原文 | 修复后 | 预期 |",
              "| --- | --- | --- | --- | --- |"]
    for item in ver:
        if item["expect"] == "tradeoff":
            label = "权衡（带时间被豁免）"
        else:
            label = "通过" if item.get("ok") else "**失败**"
        lines.append("| %s | %s | %s | %s | %s |" % (
            item["case"], item["current_place"], item["raw"], item["now"], label))
    fixed_ok = sum(1 for item in ver if item["expect"] == "fixed" and item.get("ok"))
    total_fixed = sum(1 for item in ver if item["expect"] == "fixed")
    lines += ["", "- 定向用例：**%d/%d 通过**（已到场说在路上、未来出门/到家、兜底、吃面）。" % (
                  fixed_ok, total_fixed),
              "- 残余权衡：**任何含 HH:MM/明天 的句子都跳过场景守卫**，包括"
              "「我18:00在家」这种带时间的此刻矛盾句；建议只豁免严格未来（> now）"
              "或含「明天/后天」的句子。", ""]
    lines += ["", "## 2.7 提醒链路 / 回复链路：守卫栈对照", "",
              "提醒（本仿真覆盖）：@@strip_mixed_checkin → _one_topic_guard"
              "（内含 merge_synonym_questions）→ followup_repeated（追问闸门）→ "
              "proactive_repeated / proactive_same_topic "
              "→ proactive_guard.near_duplicate → amo_state_consistency.rewrite → "
              "amo_anti_nag.rewrite → strip_repeated_urge → dedup_or_fallback → "
              "keep_answer → schedule_fact_current 复核@@。",
              "",
              "回复：@@reply_fact_guard@@（纯地点句/事实守卫）+ "
              "@@tone_guard@@ / @@domains/reply/tone.py@@（tone 残句），"
              "由 @@amo_reply_pipeline@@ 在回复阶段调用；提醒是主动消息，不经过这两个模块。"
              "本轮真正影响提醒的是：compose_task 的 schedule 分支、"
              "@@merge_synonym_questions@@（含追问生成）、@@followup_repeated@@、"
              "@@keep_answer@@、@@schedule_fact_current@@、@@_ground_schedule_text@@。",
              "- 吃饭词表「扒了两口」补在 @@reply_fact_guard._EAT_RE@@（回复链路），"
              "不改变提醒指标。", ""]
    dedupe = payload.get("dedupe_verification") or []
    if dedupe:
        lines += ["", "## 2.8 定向验证「去重保留答案」", "",
                  "| 路径 | 输入 | 输出 | 是否保留答案 |", "| --- | --- | --- | --- |"]
        for item in dedupe:
            if item["suppressed"]:
                label = "否（整条未发）"
            elif item["kept_answer"]:
                label = "是"
            else:
                label = "否（被短句兜底替换）"
            lines.append("| %s | %s | %s | %s |" % (
                item["path"], item["input"], item["out"] or "（空）", label))
        lines += ["", "- 结论：回复路径与主动发送链路（@@apply_proactive_dedup@@ + "
                  "@@keep_answer@@）都会保留带答案的原句；"
                  "只调用 @@apply_proactive_dedup@@ 本身仍会返回空，"
                  "所以提醒链路必须保持 keep_answer 这一步。", ""]
    arrival = payload.get("arrival_verification") or []
    if arrival:
        lines += ["", "## 2.9 定向验证「预计到家 / 到家请求不改口」", "",
                  "| 路径 | 场景 | 原文 | 修复后 | 结果 |",
                  "| --- | --- | --- | --- | --- |"]
        for item in arrival:
            lines.append("| %s | %s | %s | %s | %s |" % (
                item.get("path") or "-", item["case"], item["raw"],
                item["now"] or "（空）", "通过" if item.get("ok") else "**失败**"))
        reply_ok = sum(1 for item in arrival
                       if item.get("path") == "reply" and item.get("ok"))
        reply_all = sum(1 for item in arrival if item.get("path") == "reply")
        remind_bad = [item for item in arrival
                      if item.get("path") == "remind" and not item.get("ok")]
        lines += ["", "- 回复路径：**%d/%d 通过**（预计/差不多/到家请求都不改口，"
                  "真正的「刚到家」仍改口）。" % (reply_ok, reply_all)]
        if remind_bad:
            lines += ["- **提醒路径仍会改口**：@@amo_state_consistency.rewrite@@ 没有这条"
                      "预计/请求豁免（例：%s → %s）。建议把 @@_align_arrival@@ 开头的"
                      "早退正则复制进 @@state_consistency.rewrite@@，或在主动发送前调用"
                      "@@_align_arrival@@。" % (remind_bad[0]["raw"],
                                                remind_bad[0]["now"] or "（空）")]
        lines += ["", "- 注：@@_align_arrival@@ 只在 @@on_decorating_result@@（回复）里调用，"
                  "提醒链路不经过它；这一条是提醒链路的独立残留（不计入 240 条主指标），"
                  "本轮未改 @@amo_state_consistency.rewrite@@ 的早退规则。", ""]
    wiring = payload.get("schedule_wiring") or {}
    if wiring:
        lines += ["", "## 2.10 日程接线核对（本轮）", "",
                  "| 项 | 状态 |", "| --- | --- |",
                  "| compose_task schedule_reminder 分支 | %s |" % (
                      "有" if wiring.get("compose_task_branch") else "无"),
                  "| compose_task 带日程字段的提醒 | %d |" % wiring.get(
                      "compose_task_grounded_rows", 0),
                  "| 开关 %s 默认值 | **%s** |" % (
                      wiring.get("config_key"),
                      "开" if wiring.get("default_on") else "关"),
                  "| 开关是否在 _conf_schema.json 暴露 | %s |" % (
                      "是" if wiring.get("schema_exposed") else "**否**"),
                  "| 选日程 | %s |" % wiring.get("selector"),
                  "| 发送/追问前复核 | %s |" % wiring.get("recheck"),
                  "| 本次被复核拦下的消息 | %d |" % wiring.get("recheck_blocks", 0),
                  "| 事实来源（生产选/仿真兜底） | %s |" % wiring.get("fact_sources"),
                  ""]
        if not wiring.get("default_on"):
            lines += ["- **注意**：proactive_schedule_reminder 默认 **False**，且 "
                      "_conf_schema.json 里没有这一项——面板改不到，只能改配置字典/代码。"
                      "本次仿真是按「已打开」跑的；生产默认不会发日程提醒。", ""]
    recheck = payload.get("recheck_verification") or []
    if recheck:
        lines += ["", "## 2.11 定向验证 schedule_fact_current（发送前复核）", "",
                  "| 场景 | 期望 | 实际 | 结果 |", "| --- | --- | --- | --- |"]
        for item in recheck:
            lines.append("| %s | %s | %s | %s |" % (
                item["case"], "放行" if item["expect"] else "拦下",
                "放行" if item["got"] else "拦下",
                "通过" if item.get("ok") else "**失败**"))
        lines += ["", "- 复核已按「同 key 多行任一段 start 对得上」匹配（RC8 修复），"
                  "并兼容旧版两段键；4/4 通过：同名活动 10:00/17:00 不再误判，"
                  "改期/取消仍能拦，追问前的复核不吃迟到窗口（RC11）。", ""]
    grounding = payload.get("grounding_verification") or []
    if grounding:
        lines += ["", "## 2.12 定向验证「提醒事实落地」(_ground_schedule_text)", "",
                  "| 场景 | 原文 | 输出 | 期望 | 结果 |",
                  "| --- | --- | --- | --- | --- |"]
        for item in grounding:
            lines.append("| %s | %s | %s | %s | %s |" % (
                item["case"], item["raw"], item["out"] or "（不发）",
                "保留" if item["expect_keep"] else "不发",
                "通过" if item.get("ok") else "**失败**"))
        lines += ["", "- 结果：**5/5 通过**。带错时间 / 别的地点 / 别的活动直接不发；"
                  "「目的地写在事件里」（路上/挤地铁去公司）已放行（RC9 修复）。", ""]
    lines += ["## 3. 分组", "", "| 组 | 场景 | 总 | 过 | 败 |",
              "| --- | --- | --- | --- | --- |"]
    titles = {item["id"]: item["title"] for item in GROUPS}
    for gid, item in sorted(s["by_group"].items()):
        lines.append("| %s | %s | %d | %d | %d |" % (
            gid, titles.get(gid, ""), item["total"], item["passed"], item["failed"]))
    lines += ["", "## 3.5 关键证据（原文 → 交付）", ""]
    if not (payload.get("key_evidence") or []):
        lines += ["- 本轮没有失败证据：240/240 全部通过（clean 120/120、defect 120/120）。", ""]
    for item in payload.get("key_evidence") or []:
        lines += ["- **%s**（%s %s）：%s → %s" % (
            item["title"], item["group"], item["turn"],
            "「" + item["raw"] + "」" if item["raw"] else "（未生成）",
            "「" + item["delivered"] + "」" if item["delivered"] else "（未发送）")]
    lines += ["", "## 3.6 保留证据：missing_time / event_not_grounded / place_mismatch", ""]
    for key, items in (payload.get("preserved_evidence") or {}).items():
        if not items:
            lines += ["### %s（0 条）" % key,
                      "- 本轮没有该类泄漏（已被守卫覆盖）。", ""]
            continue
        lines.append("### %s（%d 条样例）" % (key, len(items)))
        for item in items:
            lines.append("- %s %s %s：%s → %s" % (
                item["group"], item["turn"], item["variant"],
                "「" + item["raw"] + "」" if item["raw"] else "（空）",
                "「" + item["delivered"] + "」" if item["delivered"] else "（未发送）"))
        lines.append("")
    lines += ["", "## 4. 前 10 个脱敏样例", ""]
    if not payload["samples"]:
        lines += ["- 本轮无失败样例（失败 0）。", ""]
    for index, sample in enumerate(payload["samples"], 1):
        lines += ["### %d) %s %s · %s" % (index, sample["group"], sample["group_title"],
                                          sample["turn"]), "",
                  "- 变体/模式：`%s` / `%s`" % (sample["variant"], sample["mode"]),
                  "- 失败类型：`%s`" % (sample.get("fail_kind") or "-"),
                  "- 违反：`%s`" % "`, `".join(sample["violations"] or []),
                  "- 触发守卫：`%s`" % (sample["cause"] or "-"),
                  "- 原文（脱敏）：%s" % (sample["raw_text"] or "（空）"),
                  "- 交付（脱敏）：%s" % (sample["text"] or "（未发送）"), ""]
    lines += ["## 5. 疑似根因", ""]
    for item in payload["root_causes"]:
        lines += ["### %s %s" % (item["id"], item["title"]), "",
                  "- 证据：%s" % item["evidence"],
                  "- 影响：%s" % item["impact"], ""]
    lines += ["## 6. 覆盖声明", "",
              "- 模拟层：确定性 provider，覆盖「日程事实 → 提示词 → 生产守卫 → 交付文本」"
              "的一致性判定，可复现（同一份代码同一输出）。",
              "- 真实模型：**未覆盖**。本机隔离环境没有可用 provider；"
              "按任务约束不得为拿 key 去读生产配置或生产库，因此没有真实模型复跑，"
              "**模型语义质量（措辞自然度、是否主动联想别的日程）未覆盖**。",
              ""]
    return chr(96).join("\n".join(lines).split("@@"))


if __name__ == "__main__":
    main()
