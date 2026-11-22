# -*- coding: utf-8 -*-
"""隔离日程仿真器：跑 3/5/7 天多日生成，检查地点/事件漂移、过渡块、三餐与宵夜。

只读约束（本文件自己执行，不依赖外部约定）
----------------------------------------
1. 不连生产库：sqlite3.connect / builtins.open / io.open 在运行期被守卫包住，
   任何指向 plugin_data 下真实库的写/建连都会抛错并计入 violations；
   AmoDB.__init__ 直接拦死；
2. 时钟：只用生产自带 clock.freeze() 测试钩子，把「现在」钉在每天的 00:00/22:00/23:00；
3. 状态：work/sim_lib 的 FakeDB（:memory:）/FakePlugin + 内存 Wallet，全部在临时目录，
   结束 shutil.rmtree + sim_lib.cleanup_temp_root()，打印 TEMP_CLEAN；
4. 生成/落库走生产真实入口（ScheduleEngine.build_context/build_skeleton ->
   HumanState.commit_schedule_change），过渡由 schedule_engine_v2.safe_view 读侧派生，
   不复制生成规则；
5. 跑前/跑后对生产库做 sha256+size+mtime 指纹；主库/数据主库必须一致（WAL 由在跑的
   AstrBot 进程持续写，单独记录并注明外部写入，不是本进程所为）。

产出：work/sim/schedule_report.json / schedule_report.md（每个 run 每天时间线 + 失败分类
+ 前 10 个脱敏样例 + 疑似根因计数）。
"""
from __future__ import annotations

import argparse
import asyncio
import builtins
import contextlib
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
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _path in (ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "work")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import sim_lib  # noqa: E402

sim_lib.install_stub()

import amo_food_audit  # noqa: E402
import amo_food_cost  # noqa: E402
import amo_food_event  # noqa: E402
import amo_shared  # noqa: E402
import clock  # noqa: E402
import micro_events  # noqa: E402
import schedule_checks as chk  # noqa: E402
from amo_shared import MOOD_BASE, MOOD_KEYS  # noqa: E402

ASTRBOT_DATA = os.path.dirname(os.path.dirname(ROOT))
REAL_DIR = os.path.join(ASTRBOT_DATA, "plugin_data", "Astrbot_amoupup_forever")
REAL_DATA_DIR = os.path.join(ASTRBOT_DATA, "plugin_data", "Astrbot_amoupup_data")
REAL_MAIN_DBS = (os.path.join(REAL_DIR, "amo_forever.db"),
                 os.path.join(REAL_DATA_DIR, "amo_data.db"))
REAL_SIDE_FILES = (os.path.join(REAL_DIR, "amo_forever.db-wal"),
                   os.path.join(REAL_DIR, "amo_forever.db-shm"))
OUT_JSON = os.path.join(HERE, "schedule_report.json")
OUT_MD = os.path.join(HERE, "schedule_report.md")

CITY = "东莞"
HORIZONS = (3, 5, 7)
SEEDS = (7, 11, 42, 123, 2026)
PROFILES = {
    "default": {"event_prob": 0.28, "occupation": "普通上班族", "label": "默认（事件概率 0.28）"},
    "event_rich": {"event_prob": 0.9, "occupation": "普通上班族", "label": "事件密集（0.9）"},
    "sales": {"event_prob": 0.7, "occupation": "项目经理", "label": "外勤职业（0.7）"},
}
SAMPLE_LIMIT = 10


# ---------------------------------------------------------------- 隔离守卫
class RealWriteGuard:
    """真实库只许读：写文件 / 建 sqlite 连接 / 实例化 AmoDB 直接失败。"""

    def __init__(self):
        self.violations = []
        self.blocked_attempts = {"open": 0, "sqlite": 0, "amo_db": 0}
        self.touched = []
        self._open = None
        self._io_open = None
        self._connect = None
        self._amo_init = None

    @staticmethod
    def is_real(path):
        try:
            text = str(path)
            if not text or text == ":memory:":
                return False
            absolute = os.path.abspath(text)
        except Exception:  # cleanup: 仿真容错
            return False
        if absolute == REAL_DIR or absolute.startswith(REAL_DIR + os.sep):
            return True
        return os.path.basename(absolute) in ("amo_forever.db", "amo_data.db")

    def install(self):
        guard = self
        self._open = builtins.open
        self._io_open = io.open
        self._connect = sqlite3.connect

        def guard_open(file, mode="r", *args, **kwargs):
            text = str(mode or "r")
            if guard.is_real(file):
                guard.touched.append("open:%s:%s" % (file, mode))
            if any(ch in text for ch in "wax+") and guard.is_real(file):
                guard.blocked_attempts["open"] += 1
                guard.violations.append("OPEN %s mode=%s" % (file, mode))
                raise RuntimeError("SIM_BLOCKED: 禁止写生产路径 %s" % file)
            return guard._open(file, mode, *args, **kwargs)

        def guard_io_open(file, mode="r", *args, **kwargs):
            return guard_open(file, mode, *args, **kwargs)

        def guard_connect(database, *args, **kwargs):
            if guard.is_real(database):
                guard.touched.append("sqlite:%s" % database)
                guard.blocked_attempts["sqlite"] += 1
                guard.violations.append("SQLITE %s" % database)
                raise RuntimeError("SIM_BLOCKED: 禁止连接生产库 %s" % database)
            return guard._connect(database, *args, **kwargs)

        builtins.open = guard_open
        io.open = guard_io_open
        sqlite3.connect = guard_connect
        try:
            import amo_db
            original = amo_db.AmoDB.__init__

            def blocked_init(self_obj, *args, **kwargs):
                guard.blocked_attempts["amo_db"] += 1
                raise RuntimeError("SIM_BLOCKED: 隔离仿真器禁止实例化生产 AmoDB")

            amo_db.AmoDB.__init__ = blocked_init
            self._amo_init = (amo_db.AmoDB, original)
        except Exception:  # cleanup: 仿真容错
            self._amo_init = None

    def uninstall(self):
        if self._open is not None:
            builtins.open = self._open
            io.open = self._io_open
            sqlite3.connect = self._connect
            self._open = None
        if self._amo_init is not None:
            cls, original = self._amo_init
            cls.__init__ = original
            self._amo_init = None


def fingerprint(path):
    try:
        stat = os.stat(path)
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return {"path": path, "exists": True, "size": stat.st_size,
                "mtime": round(stat.st_mtime, 3), "sha256": digest.hexdigest()}
    except FileNotFoundError:
        return {"path": path, "exists": False, "size": 0, "mtime": 0, "sha256": ""}
    except Exception as exc:
        return {"path": path, "exists": None, "error": repr(exc)[:160]}


def fingerprint_side(path):
    try:
        stat = os.stat(path)
        return {"path": path, "exists": True, "size": stat.st_size,
                "mtime": round(stat.st_mtime, 3)}
    except FileNotFoundError:
        return {"path": path, "exists": False, "size": 0, "mtime": 0}
    except Exception as exc:
        return {"path": path, "exists": None, "error": repr(exc)[:160]}


def snapshot_real():
    return {"main": [fingerprint(path) for path in REAL_MAIN_DBS],
            "side": [fingerprint_side(path) for path in REAL_SIDE_FILES]}


CODE_FILES = (
    "schedule_engine.py", "schedule_skeleton.py", "schedule_plan.py", "schedule_slots.py",
    "amo_transition.py", "amo_state_schedule.py", "amo_food_cost.py", "amo_food_audit.py",
    "amo_food_event.py", "micro_events.py", "clock.py",
    "domains/schedule/place.py", "domains/schedule/rest_segments.py",
    "domains/schedule/social_segments.py", "domains/schedule/day_types.py",
)


def code_fingerprint():
    rows = {}
    for name in CODE_FILES:
        path = os.path.join(ROOT, name)
        try:
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()[:16]
            rows[name] = {"sha256_16": digest, "mtime": int(os.stat(path).st_mtime)}
        except Exception as exc:
            rows[name] = {"error": repr(exc)[:80]}
    return rows


# ---------------------------------------------------------------- 假插件
def make_plugin(seed, profile):
    plugin, state = sim_lib.make_state(city=CITY, day="2026-10-01")
    conf = PROFILES[profile]
    plugin.config.update({
        "schedule_event_prob": float(conf["event_prob"]),
        "schedule_swim_min_temp": 22.0,
        "schedule_block_mode": True,
    })
    plugin.state = state
    # 宵夜领域入口需要的宿主机接口（真实插件由 main/守护插件提供）
    plugin.late_night_state = lambda: {"active": True}
    plugin.get_hunger = lambda: 88.0
    plugin.add_hunger = lambda *args, **kwargs: None
    plugin.note_late_snack = lambda *args, **kwargs: None
    try:
        state.mood_score = lambda: 62.0
    except Exception:  # cleanup: 仿真容错
        pass
    plugin.wallet.credit(3000000, "仿真入金", "sim:credit")
    return plugin, state


def initial_mood(seed):
    rng = random.Random("mood|%s" % seed)
    return {key: rng.randint(30, 78) for key in MOOD_KEYS}


def evolve_mood(mood, rows, seed, day):
    rng = random.Random("mood-adv|%s|%s" % (seed, day))
    delta = {}
    for row in rows or []:
        effects = row.get("effects") if isinstance(row, dict) else None
        for key, value in (effects or {}).items():
            if key in MOOD_KEYS:
                try:
                    delta[key] = delta.get(key, 0.0) + float(value)
                except (TypeError, ValueError):
                    continue
    out = {}
    for key in MOOD_KEYS:
        base = float(mood.get(key, MOOD_BASE.get(key, 50)))
        value = base + delta.get(key, 0.0) * 0.4 + rng.uniform(-2.0, 2.0)
        out[key] = int(max(0, min(100, round(value))))
    return out


def weather_for(seed, day):
    rng = random.Random("weather|%s|%s" % (seed, day))
    bad = rng.random() < 0.2
    text = rng.choice(("小雨", "阴", "多云")) if bad else rng.choice(("晴", "多云", "晴转多云"))
    return {"temp": float(rng.choice((12, 16, 19, 22, 24, 26, 28, 30))), "text": text}


def world_for(profile):
    return {"occupation": PROFILES[profile]["occupation"],
            "workplace": "东莞·写字楼"}


def rows_of(state, day):
    return [dict(row) for row in (state.schedule.get(day) or state.db.get_schedule(day) or [])
            if isinstance(row, dict)]


@contextlib.contextmanager
def frozen(day, hour=0, minute=0):
    moment = dt.datetime.combine(dt.date.fromisoformat(str(day)), dt.time(int(hour), int(minute)))
    clock.freeze(moment.timestamp())
    try:
        yield moment
    finally:
        clock.unfreeze()


def day_type_of(plugin, state, day):
    try:
        picked = state._today_day_type(day) if hasattr(state, "_today_day_type") else {}
        if isinstance(picked, dict) and picked.get("id"):
            return dict(picked)
    except Exception:  # cleanup: 仿真容错
        pass
    return {"id": "workday", "name": "上班日"}


# ---------------------------------------------------------------- 生成一天
def generate_day(plugin, state, day, seed, profile, mood, force_day_type=None,
                 force_event=None):
    with frozen(day, 0, 0):
        meta = dict(amo_shared.day_meta(day) or {})
        weather = weather_for(seed, day)
        world = world_for(profile)
        ctx = state.engine.build_context(day, meta, weather, world, dict(mood),
                                         {"pain": 0}, {"sick": False}, persona="")
        if force_event is not None:
            ctx["event"] = dict(force_event)
        ctx["day_type"] = force_day_type or day_type_of(plugin, state, day)
        ctx["sick_action"] = ""
        raw = [dict(row) for row in state.engine.build_skeleton(ctx)]
        if not raw:
            ctx["event"] = {"kind": "none"}
            raw = [dict(row) for row in state.engine.build_skeleton(ctx)]
        before = rows_of(state, day)
        result = state.commit_schedule_change(day, [dict(row) for row in raw],
                                              old_items=before, source="ensure",
                                              reason="schedule_sim")
        # 阶段 0：写侧 _repair_transit_blocks 已整块删除，过渡只在读侧派生（safe_view）；
        # 这里恒为 0，保留字段只为报告结构不变。
        repair_inserted = 0
        rows = rows_of(state, day)
        stored = [dict(row) for row in (state.db.get_schedule(day) or [])]
    return {"ctx": ctx, "meta": meta, "weather": weather, "raw": raw, "rows": rows,
            "stored": stored, "commit": result, "repair_inserted": repair_inserted}


# ---------------------------------------------------------------- 宵夜仿真
async def snack_probe(plugin, state, day, index):
    attempts = []
    with frozen(day, 22, 0):
        forced = index % 2 == 0
        first = await amo_food_event.late_snack_tick(plugin, roll=0 if forced else None)
        attempts.append({"hour": "22:00", "forced": forced,
                         "eat": bool(first.get("eat")), "skipped": str(first.get("skipped") or ""),
                         "charged": int(first.get("charged") or 0),
                         "block": first.get("block") or None})
        if first.get("eat"):
            again = await amo_food_event.late_snack_tick(plugin, roll=0)
            attempts.append({"hour": "22:00#again", "forced": True,
                             "eat": bool(again.get("eat")),
                             "skipped": str(again.get("skipped") or ""),
                             "charged": int(again.get("charged") or 0)})
    with frozen(day, 23, 0):
        third = await amo_food_event.late_snack_tick(plugin, roll=0)
        attempts.append({"hour": "23:00", "forced": True,
                         "eat": bool(third.get("eat")), "skipped": str(third.get("skipped") or ""),
                         "charged": int(third.get("charged") or 0),
                         "block": third.get("block") or None})
    ledger = [dict(row) for row in plugin.wallet.ledger(1000)]
    snack_rows = []
    for row in ledger:
        idem = str(row.get("idem") or "")
        reason = str(row.get("reason") or "")
        if not (idem.startswith("late_snack:") or "夜宵" in reason):
            continue
        try:
            when = dt.datetime.fromtimestamp(float(row.get("ts") or 0))
        except (TypeError, ValueError, OSError):
            continue
        if when.strftime("%Y-%m-%d") != str(day):
            continue
        snack_rows.append(row)
    eat = any(item.get("eat") for item in attempts)
    charged = sum(int(item.get("charged") or 0) > 0 for item in attempts)
    return {"attempts": attempts, "eat": eat, "charged_count": charged,
            "idems": [str(row.get("idem") or "") for row in snack_rows],
            "ledger": ledger}


# ---------------------------------------------------------------- 三餐守卫
def meal_guard_probe(plugin, state, day, rows):
    results = []
    for slot, minute in (("breakfast", 8 * 60), ("lunch", 12 * 60 + 30), ("dinner", 19 * 60)):
        moment = dt.datetime.combine(dt.date.fromisoformat(str(day)),
                                     dt.time(minute // 60, minute % 60))
        with frozen(day, minute // 60, minute % 60):
            claim = ""
            spent = False
            done = False
            leaked = []
            negative = []
            error = ""
            try:
                claim = amo_food_cost.meal_claim(plugin, day, slot)
                spent = bool(amo_food_cost.meal_spent(plugin, moment))
                engine = micro_events.MicroEventEngine(plugin)
                done = bool(engine._meal_done(moment))
                keys = set()
                for seed in range(60):
                    event = micro_events.pick_event(moment, 200000, scene="private",
                                                    rng=random.Random(seed), meal_done=True)
                    if event:
                        keys.add(event["key"])
                leaked = sorted(keys & micro_events.MEAL_KEYS)
                if not claim:
                    neg_keys = set()
                    for seed in range(60):
                        event = micro_events.pick_event(moment, 200000, scene="private",
                                                        rng=random.Random(seed), meal_done=False)
                        if event:
                            neg_keys.add(event["key"])
                    negative = sorted(neg_keys & micro_events.MEAL_KEYS)
            except Exception as exc:
                error = repr(exc)[:200]
            results.append({"slot": slot, "time": "%02d:%02d" % (minute // 60, minute % 60),
                            "claim": str(claim or ""), "meal_spent": spent,
                            "meal_done": done, "leaked_meal_keys": leaked,
                            "negative_control": negative,
                            "negative_ok": bool(negative) if not claim else None,
                            "error": error})
    return results


def _missing_rows(memory_rows, stored_rows):
    def key(row):
        return (str(row.get("start") or ""), str(row.get("end") or ""),
                str(row.get("activity") or ""))
    stored = {key(row) for row in stored_rows}
    return ["%s-%s %s" % item for item in
            sorted(key(row) for row in memory_rows if key(row) not in stored)][:4]


# ---------------------------------------------------------------- 单天检查
def run_day_checks(day_index, day, ctx, meta, raw, rows, stored, prev_record,
                   ledger, snack, guard_probes):
    issues = []
    try:
        issues.extend(chk.check_timeline(day, rows))
    except Exception as exc:
        issues.append(chk.make_issue(day, "gap", "check_error", "timeline 检查异常: %r" % exc))
    if stored and [[str(row.get(k) or "") for k in ("start", "end", "activity")]
                   for row in stored] != [[str(row.get(k) or "") for k in ("start", "end", "activity")]
                                           for row in rows]:
        issues.append(chk.make_issue(day, "drift", "storage_mismatch",
                                     "内存日程与落库表不一致（表 %d 条 / 内存 %d 条），"
                                     "表里少了：%s"
                                     % (len(stored), len(rows),
                                        _missing_rows(rows, stored)), "fail",
                                     root_cause="R15_snack_memory_overwrite"))
    try:
        issues.extend(chk.check_place_jumps(day, rows, raw))
    except Exception as exc:
        issues.append(chk.make_issue(day, "drift", "check_error", "地点检查异常: %r" % exc, "warn"))
    try:
        issues.extend(chk.check_place_collapse(day, rows, raw))
    except Exception as exc:
        issues.append(chk.make_issue(day, "drift", "check_error",
                                     "地点归一化检查异常: %r" % exc, "warn"))
    try:
        issues.extend(chk.check_rewrites(day, rows, raw))
    except Exception as exc:
        issues.append(chk.make_issue(day, "drift", "check_error",
                                     "改写检查异常: %r" % exc, "warn"))
    try:
        issues.extend(chk.check_snack_blocks(day, rows))
    except Exception as exc:
        issues.append(chk.make_issue(day, "drift", "check_error",
                                     "宵夜块检查异常: %r" % exc, "warn"))
    try:
        issues.extend(chk.check_transitions(day, rows, raw, ctx))
    except Exception:  # cleanup: 仿真容错
        pass
    try:
        issues.extend(chk.check_required_transitions(day, rows, ctx, raw))
    except Exception:  # cleanup: 仿真容错
        pass
    try:
        issues.extend(chk.check_events(day, rows, ctx, raw))
    except Exception:  # cleanup: 仿真容错
        pass
    try:
        meal_issues, meal_info = chk.check_meals(day, rows, ledger, snack)
        issues.extend(meal_issues)
    except Exception as exc:
        meal_info = {"error": repr(exc)[:200]}
    if prev_record is not None:
        try:
            issues.extend(chk.check_cross_day(day, rows, ctx, prev_record, raw))
        except Exception:  # cleanup: 仿真容错
            pass
    if snack and snack.get("eat") and snack.get("charged_count"):
        has_block = any("宵夜" in str(row.get("activity") or "") or "夜宵" in str(row.get("activity") or "")
                        for row in rows)
        if not has_block:
            issues.append(chk.make_issue(day, "gap", "snack_block_missing",
                                         "宵夜扣款了但日程里没有宵夜块", "warn",
                                         root_cause="R11_snack_block_no_guard"))
    for probe in guard_probes or []:
        if probe.get("error"):
            issues.append(chk.make_issue(day, "meal_duplicate", "guard_error",
                                         "饭点守卫异常: %s" % probe.get("error"), "warn"))
            continue
        if not probe.get("claim"):
            continue
        if not probe.get("meal_spent") or not probe.get("meal_done") or probe.get("leaked_meal_keys"):
            issues.append(chk.make_issue(day, "meal_duplicate", "guard_would_double",
                                         "%s 已有饭点证据「%s」，但 meal_spent=%s meal_done=%s "
                                         "leaked=%s -> 可能同餐多扣一顿"
                                         % (probe.get("slot"), probe.get("claim"),
                                            probe.get("meal_spent"), probe.get("meal_done"),
                                            probe.get("leaked_meal_keys")),
                                         "fail", at=probe.get("time") or ""))
    return issues, meal_info


# ---------------------------------------------------------------- 一个 run
def run_sequence(plugin, state, seed, profile, start_day, horizon, seed_label="",
                 target_days=None, target_events=None):
    target_days = target_days or {}
    target_events = target_events or {}
    mood = initial_mood(seed)
    days = []
    prev_rows = None
    prev_ctx = None
    prev_day = None
    prev_key = None
    for index in range(horizon):
        day = (dt.date.fromisoformat(start_day) + dt.timedelta(days=index)).isoformat()
        generated = generate_day(plugin, state, day, seed, profile, mood,
                                 force_day_type=target_days.get(day),
                                 force_event=target_events.get(day))
        ctx = generated["ctx"]
        meta = generated["meta"]
        raw = generated["raw"]
        rows = generated["rows"]
        stored = generated["stored"]
        snack = None
        try:
            snack = asyncio.run(snack_probe(plugin, state, day, index))
        except Exception as exc:
            snack = {"attempts": [], "eat": False, "charged_count": 0,
                     "error": repr(exc)[:200], "ledger": []}
        rows = rows_of(state, day)
        stored = [dict(row) for row in (state.db.get_schedule(day) or [])]
        ledger = [dict(row) for row in plugin.wallet.ledger(1000)]
        try:
            guards = meal_guard_probe(plugin, state, day, rows)
        except Exception as exc:
            guards = [{"error": repr(exc)[:200]}]
        prev_ctx_record = None
        if prev_rows is not None:
            prev_ctx_record = {"day": prev_day, "rows": prev_rows,
                               "ctx": prev_ctx or {}, "timeline_key": prev_key}
        issues, meal_info = run_day_checks(index, day, ctx, meta, raw, rows, stored,
                                           prev_ctx_record, ledger, snack, guards)
        for issue in issues:
            issue["run_kind"] = seed_label or "random"
            issue["seed"] = seed
            issue["day_index"] = index
        day_record = {
            "day": day, "index": index,
            "weekday": (meta.get("weekday") or ""), "rest": bool(meta.get("rest")),
            "holiday": str(meta.get("holiday") or ""),
            "day_type": str((ctx.get("day_type") or {}).get("id") or ""),
            "event": str((ctx.get("event") or {}).get("kind") or "none"),
            "weather": generated["weather"],
            "mood": {key: mood.get(key) for key in MOOD_KEYS},
            "raw_count": len(raw), "rows_count": len(rows),
            "transit_rows": sum(1 for row in rows if str(row.get("source") or "") == "transit_block"),
            "repair_inserted": generated["repair_inserted"],
            "stored_count": len(stored),
            "blocks": [{key: row.get(key) for key in
                        ("start", "end", "activity", "place", "zone", "category", "source")}
                       for row in rows],
            "issues": issues,
            "meal": meal_info,
            "snack": ({key: snack.get(key) for key in
                       ("eat", "charged_count", "idems", "attempts", "error")}
                      if isinstance(snack, dict) else None),
            "meal_guard": guards,
            "timeline_key": chk.timeline_key(rows),
        }
        days.append(day_record)
        prev_rows = rows
        prev_ctx = ctx
        prev_day = day
        prev_key = day_record["timeline_key"]
        mood = evolve_mood(mood, rows, seed, day)
    return days


def summarize_run(run):
    issues = [issue for day in run["days"] for issue in day["issues"]]
    fails = [issue for issue in issues if issue.get("severity") != "warn"]
    by_category = {category: 0 for category in chk.CATEGORIES}
    for issue in fails:
        by_category[issue["category"]] = by_category.get(issue["category"], 0) + 1
    return {"days": len(run["days"]), "issues": len(issues), "fails": len(fails),
            "warns": len(issues) - len(fails), "fails_by_category": by_category,
            "failed_days": sum(1 for day in run["days"]
                               if any(issue.get("severity") != "warn" for issue in day["issues"])),
            "meals_total": sum(int((day.get("meal") or {}).get("total") or 0)
                               for day in run["days"]),
            "snack_charged": sum(int((day.get("snack") or {}).get("charged_count") or 0)
                                 for day in run["days"])}


# ---------------------------------------------------------------- 目标场景（覆盖事件）
def scan_event_days(seed, kind, limit=2, profile="event_rich", occupation=None):
    plugin, state = make_plugin(seed, profile)
    found = []
    base = dt.date(2026, 10, 3)
    for offset in range(0, 45):
        day = (base + dt.timedelta(days=offset)).isoformat()
        with frozen(day, 0, 0):
            meta = dict(amo_shared.day_meta(day) or {})
            if kind in ("friend_dinner", "overtime_rest") and not meta.get("rest"):
                continue
            if kind == "business_trip" and meta.get("rest"):
                continue
            weather = weather_for(seed, day)
            world = {"occupation": occupation or PROFILES[profile]["occupation"],
                     "workplace": "东莞·写字楼"}
            try:
                ctx = state.engine.build_context(day, meta, weather, world,
                                                 initial_mood(seed), {"pain": 0},
                                                 {"sick": False}, persona="")
            except Exception:  # cleanup: 仿真容错
                continue
            event = ctx.get("event") or {}
            if kind == "friend_dinner" and event.get("kind") == "friend_dinner":
                found.append({"day": day, "event": dict(event), "meta": meta})
            elif kind == "business_trip" and event.get("kind") == "business_trip":
                found.append({"day": day, "event": dict(event), "meta": meta})
            elif kind == "overtime_rest" and event.get("kind") == "overtime" and meta.get("rest"):
                found.append({"day": day, "event": dict(event), "meta": meta})
        if len(found) >= limit:
            break
    return found


def build_targeted_runs():
    runs = []
    used = set()
    for seed in (42, 2026, 11):
        for item in scan_event_days(seed, "friend_dinner", limit=4, profile="event_rich"):
            day = item["day"]
            if day in used:
                continue
            used.add(day)
            runs.append({"kind": "targeted_friend_dinner", "seed": seed,
                         "profile": "event_rich", "start_day": day, "horizon": 1,
                         "target_days": {day: {"id": "restday", "name": "休息日", "guide": ""}},
                         "target_events": {}})
            if len([run for run in runs if run["kind"] == "targeted_friend_dinner"]) >= 3:
                break
    for seed in (42, 2026):
        for item in scan_event_days(seed, "business_trip", limit=3, profile="sales",
                                    occupation="项目经理"):
            day = item["day"]
            if day in used:
                continue
            used.add(day)
            runs.append({"kind": "targeted_business_trip", "seed": seed,
                         "profile": "sales", "start_day": day, "horizon": 2,
                         "target_days": {},
                         "target_events": {day: dict(item["event"])}})
            if len([run for run in runs if run["kind"] == "targeted_business_trip"]) >= 2:
                break
    return runs


def start_day_for(seed):
    base = dt.date(2026, 9, 28)
    offset = (int(seed) * 7 + 3) % 45
    return (base + dt.timedelta(days=offset)).isoformat()


# ---------------------------------------------------------------- 自检（阳性对照）
def detector_self_test():
    results = []
    base_rows = [
        {"start": "00:00", "end": "07:00", "activity": "睡觉", "place": "家", "zone": "home", "category": "sleep"},
        {"start": "07:00", "end": "08:00", "activity": "洗漱", "place": "家", "zone": "home", "category": "life"},
        {"start": "08:00", "end": "08:30", "activity": "挤地铁去公司", "place": "路上", "zone": "road", "category": "commute"},
        {"start": "08:30", "end": "18:00", "activity": "上班", "place": "公司", "zone": "office", "category": "work"},
        {"start": "18:00", "end": "18:30", "activity": "下班回家", "place": "路上", "zone": "road", "category": "commute"},
        {"start": "18:30", "end": "24:00", "activity": "在家休息", "place": "家", "zone": "home", "category": "rest"},
    ]
    clean = chk.check_timeline("2026-01-01", base_rows)
    results.append({"name": "timeline_clean_no_issue", "ok": not clean, "detail": clean[:2]})
    overlap_rows = [dict(row) for row in base_rows]
    overlap_rows[3] = dict(overlap_rows[3], start="07:30")
    overlap = chk.check_timeline("2026-01-01", overlap_rows)
    results.append({"name": "timeline_detects_overlap",
                    "ok": any(issue["category"] == "overlap" for issue in overlap),
                    "detail": [issue["subtype"] for issue in overlap]})
    gap_rows = [dict(row) for row in base_rows]
    gap_rows[3] = dict(gap_rows[3], start="09:00")
    gap = chk.check_timeline("2026-01-01", gap_rows)
    results.append({"name": "timeline_detects_gap",
                    "ok": any(issue["category"] == "gap" for issue in gap),
                    "detail": [issue["subtype"] for issue in gap]})
    teleport = [dict(row) for row in base_rows]
    teleport[3] = dict(teleport[3], end="17:00")
    teleport.insert(4, {"start": "17:00", "end": "18:00", "activity": "在家休息",
                        "place": "家", "zone": "home", "category": "rest"})
    teleport[5] = dict(teleport[5], start="18:00", end="18:30")
    teleport[6] = dict(teleport[6], start="18:30", end="24:00")
    jumps = chk.check_place_jumps("2026-01-01", teleport, teleport)
    results.append({"name": "place_jump_detected",
                    "ok": any(issue["category"] == "transition_missing" for issue in jumps),
                    "detail": [issue["subtype"] for issue in jumps]})
    meal_rows = [dict(row) for row in base_rows]
    meal_rows[4] = {"start": "18:00", "end": "19:00", "activity": "和朋友吃饭",
                    "place": "楼下小馆", "zone": "restaurant", "category": "social"}
    meal_rows.insert(5, {"start": "19:45", "end": "20:30", "activity": "点外卖",
                         "place": "家", "zone": "home", "category": "meal"})
    meal_issues, meal_info = chk.check_meals("2026-01-01", meal_rows, [], {"eat": False})
    results.append({"name": "meal_duplicate_detected",
                    "ok": any(issue["category"] == "meal_duplicate" for issue in meal_issues),
                    "detail": meal_info})
    return results


# ---------------------------------------------------------------- 报告
def desensitize(text, extra_terms=()):
    value = str(text or "")
    for city in sorted(chk.cities(), key=len, reverse=True):
        if city and city in value:
            value = value.replace(city, "某市")
    for term in ("老公", "大肥鱼", "302578963"):
        if term in value:
            value = value.replace(term, "【partner】" if term != "302578963" else "【id】")
    for term in extra_terms:
        if term:
            value = value.replace(str(term), "【workplace】")
    value = re.sub(r"[0-9]{6,}", "【num】", value)
    return value[:120]


def collect_summary(runs, detector, isolation, guard):
    all_issues = [issue for run in runs for day in run["days"] for issue in day["issues"]]
    fails = [issue for issue in all_issues if issue.get("severity") != "warn"]
    warns = [issue for issue in all_issues if issue.get("severity") == "warn"]
    by_category = {category: 0 for category in chk.CATEGORIES}
    for issue in fails:
        by_category[issue["category"]] = by_category.get(issue["category"], 0) + 1
    by_subtype = {}
    for issue in all_issues:
        key = "%s/%s" % (issue["category"], issue["subtype"])
        by_subtype[key] = by_subtype.get(key, 0) + 1
    root_counts = {}
    seen_root = set()
    for run in runs:
        for day in run["days"]:
            for issue in day["issues"]:
                if issue.get("severity") == "warn":
                    continue
                root = str(issue.get("root_cause") or "")
                if not root:
                    continue
                token = (run["run_id"], day["day"], root)
                if token in seen_root:
                    continue
                seen_root.add(token)
                root_counts[root] = root_counts.get(root, 0) + 1
    root_notes = {}
    for root, count in sorted(root_counts.items(), key=lambda item: -item[1]):
        root_notes[root] = {"count": count, "note": chk.ROOT_CAUSES.get(root, "")}
    for root, note in chk.ROOT_CAUSES.items():
        root_notes.setdefault(root, {"count": 0, "note": note})
    by_horizon = {}
    for run in runs:
        bucket = by_horizon.setdefault(str(run["horizon"]), {"runs": 0, "days": 0,
                                                            "fails": 0, "warns": 0})
        bucket["runs"] += 1
        bucket["days"] += len(run["days"])
        for day in run["days"]:
            for issue in day["issues"]:
                if issue.get("severity") == "warn":
                    bucket["warns"] += 1
                else:
                    bucket["fails"] += 1
    meal_totals = []
    for run in runs:
        for day in run["days"]:
            meal = day.get("meal") or {}
            if isinstance(meal, dict) and "total" in meal:
                meal_totals.append({"day": day["day"], "total": int(meal.get("total") or 0),
                                    "buckets": meal.get("buckets") or {}})
    guard_rows = []
    for run in runs:
        for day in run["days"]:
            for probe in day.get("meal_guard") or []:
                guard_rows.append(probe)
    guard_fail = [row for row in guard_rows
                  if row.get("claim") and (not row.get("meal_spent")
                                           or not row.get("meal_done")
                                           or row.get("leaked_meal_keys")
                                           or row.get("error"))]
    friend_days = []
    for run in runs:
        for day in run["days"]:
            if day.get("event") != "friend_dinner":
                continue
            dinner_claim = ""
            dinner_guard = {}
            for probe in day.get("meal_guard") or []:
                if probe.get("slot") == "dinner":
                    dinner_claim = str(probe.get("claim") or "")
                    dinner_guard = {"meal_spent": probe.get("meal_spent"),
                                    "meal_done": probe.get("meal_done"),
                                    "leaked": probe.get("leaked_meal_keys")}
            friend_days.append({"day": day["day"], "day_type": day.get("day_type"),
                                "dinner_guard": dinner_guard,
                                "social_block": any(str(block.get("category") or "") in ("social", "date")
                                                    for block in day.get("blocks") or []),
                                "dinner_claim": dinner_claim,
                                "snack": bool((day.get("snack") or {}).get("eat"))})
    return {"runs": len(runs),
            "days": sum(len(run["days"]) for run in runs),
            "friend_dinner_days": friend_days,
            "issues": len(all_issues), "fails": len(fails), "warns": len(warns),
            "failed_days": sum(1 for run in runs for day in run["days"]
                               if any(issue.get("severity") != "warn" for issue in day["issues"])),
            "fails_by_category": by_category,
            "by_subtype": dict(sorted(by_subtype.items(), key=lambda item: -item[1])),
            "root_causes": root_notes,
            "by_horizon": by_horizon,
            "meals": {"days_checked": len(meal_totals),
                      "max_per_day": max((item["total"] for item in meal_totals), default=0),
                      "over_four": [item for item in meal_totals if item["total"] > 4]},
            "meal_guard": {"probes": len(guard_rows), "failed": len(guard_fail),
                           "fail_samples": guard_fail[:5]},
            "detector_self_test": detector,
            "isolation": isolation,
            "guard_blocked": dict(guard.blocked_attempts),
            "guard_violations": list(guard.violations)}


def _sample_of(run, day, issue):
    block = issue.get("block") or {}
    return {"run": run["run_id"], "seed": run["seed"],
            "horizon": run["horizon"], "kind": run["kind"],
            "day": day["day"], "event": day["event"],
            "day_type": day["day_type"],
            "category": issue["category"],
            "subtype": issue["subtype"],
            "at": issue.get("at") or "",
            "root_cause": issue.get("root_cause") or "",
            "detail": desensitize(issue.get("detail") or "",
                                  extra_terms=(CITY + "·写字楼",)),
            "block": {"start": block.get("start"), "end": block.get("end"),
                      "activity": desensitize(block.get("activity") or ""),
                      "place": desensitize(block.get("place") or "")}}


def pick_samples(runs, limit=SAMPLE_LIMIT):
    """按分类轮转各取 1 个，再补齐到 10 个：保证六类失败都有代表样例。"""
    buckets = {category: [] for category in chk.CATEGORIES}
    seen = set()
    for run in runs:
        for day in run["days"]:
            for issue in day["issues"]:
                if issue.get("severity") == "warn":
                    continue
                category = issue["category"]
                key = (category, issue["subtype"], run["run_id"], day["day"], issue.get("at"))
                if key in seen or category not in buckets:
                    continue
                seen.add(key)
                buckets[category].append(_sample_of(run, day, issue))
    samples = []
    cursor = {category: 0 for category in buckets}
    progress = True
    while len(samples) < limit and progress:
        progress = False
        for category in chk.CATEGORIES:
            items = buckets.get(category) or []
            index = cursor.get(category, 0)
            if index >= len(items):
                continue
            samples.append(items[index])
            cursor[category] = index + 1
            progress = True
            if len(samples) >= limit:
                break
    return samples


def write_markdown(report):
    summary = report["summary"]
    lines = []
    lines.append("# 日程隔离仿真报告（3/5/7 天 × 随机种子）")
    lines.append("")
    lines.append("- 生成时间：%s" % report["meta"]["generated_at"])
    lines.append("- 插件：Astrbot_amoupup_forever（只读测试；未改生产代码、未写生产库）")
    lines.append("- 生成入口：ScheduleEngine.build_context/build_skeleton -> "
                 "HumanState.commit_schedule_change(source=ensure)；过渡由 "
                 "schedule_engine_v2.safe_view 读侧派生")
    lines.append("- 时钟：clock.freeze()（每天 00:00/22:00/23:00）；库：sim_lib FakeDB(:memory:)；"
                 "生成过程不调 LLM")
    lines.append("")
    lines.append("## 1. 隔离与生产库校验")
    lines.append("")
    iso = report["isolation"]
    touched = iso.get("our_process_touched_real_paths") or []
    write_touched = [item for item in touched if not str(item).endswith(":r")]
    if not touched:
        verdict = "本进程 0 次访问生产路径"
    elif not write_touched:
        names = []
        for item in touched:
            body = str(item).split(":", 1)[1] if ":" in str(item) else str(item)
            names.append(os.path.basename(body.rsplit(":", 1)[0] if body.endswith(":r") else body))
        verdict = "本进程仅只读访问生产 data 目录（%s），无写/建连" % "、".join(names)
    else:
        verdict = "本进程对生产路径有非只读访问（见下）"
    lines.append("- 生产主库前后 sha256 一致：**%s**（%s）" % (iso["main_db_unchanged"], verdict))
    if iso.get("external_writer"):
        lines.append("  - 主库在窗口内被**外部进程**（在跑的 AstrBot）写入/checkpoint："
                     "mtime 变化 %s 秒；本进程对真实路径的 open/connect 次数 = %d，"
                     "被守卫拦截的写 = %s"
                     % (iso.get("main_db_mtime_delta"), len(iso.get("our_process_touched_real_paths") or []),
                        iso.get("our_process_blocked_writes")))
    for item in iso["before"]["main"]:
        lines.append("  - %s size=%s mtime=%s sha256=%s"
                     % (os.path.basename(item["path"]), item.get("size"), item.get("mtime"),
                        str(item.get("sha256") or "")[:16]))
    lines.append("- WAL/SHM（在跑的 AstrBot 进程持续写，本进程未打开）：%s"
                 % iso["side_files_note"])
    lines.append("- 写守卫拦截次数：%s；违规：%s"
                 % (summary["guard_blocked"], summary["guard_violations"] or "无"))
    lines.append("- 临时目录已删除：**%s**（%s）" % (iso["temp_clean"], iso["temp_root_deleted"]))
    lines.append("")
    lines.append("## 2. 总览")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("| --- | --- |")
    lines.append("| run 数 | %d |" % summary["runs"])
    lines.append("| 生成天数 | %d |" % summary["days"])
    lines.append("| 失败问题 | %d |" % summary["fails"])
    lines.append("| 警告问题 | %d |" % summary["warns"])
    lines.append("| 有失败的天 | %d / %d |" % (summary["failed_days"], summary["days"]))
    for category in chk.CATEGORIES:
        lines.append("| %s | %d |" % (category, summary["fails_by_category"].get(category, 0)))
    lines.append("")
    lines.append("按 horizon：")
    for horizon, bucket in summary["by_horizon"].items():
        lines.append("- %s 天：%d 个 run / %d 天 / fail=%d / warn=%d"
                     % (horizon, bucket["runs"], bucket["days"], bucket["fails"], bucket["warns"]))
    lines.append("")
    lines.append("## 3. 失败分类（fail）")
    lines.append("")
    if summary["by_subtype"]:
        lines.append("| 分类/子类 | 次数 |")
        lines.append("| --- | --- |")
        for key, count in summary["by_subtype"].items():
            lines.append("| %s | %d |" % (key, count))
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 4. 疑似根因（按命中次数）")
    lines.append("")
    lines.append("| 根因 | 命中（run×天） | 代码位置与说明 |")
    lines.append("| --- | --- | --- |")
    zero_roots = []
    for root, item in summary["root_causes"].items():
        if not item["count"]:
            zero_roots.append(root)
            continue
        lines.append("| %s | %d | %s |" % (root, item["count"], item["note"]))
    if zero_roots:
        lines.append("")
        lines.append("本轮未命中的已知隐患（代码路径存在，但这批种子/场景没触发）：%s"
                     % "、".join(zero_roots))
    lines.append("")
    lines.append("## 5. 前 10 个脱敏样例")
    lines.append("")
    if report["samples"]:
        for index, sample in enumerate(report["samples"], 1):
            lines.append("%d. %s %s（%s日/事件=%s）**%s/%s**：%s"
                         % (index, sample["day"], sample["at"], sample["day_type"],
                            sample["event"], sample["category"], sample["subtype"],
                            sample["detail"]))
            if sample.get("root_cause"):
                lines.append("   - 根因：%s" % sample["root_cause"])
    else:
        lines.append("（无失败样例）")
    lines.append("")
    lines.append("## 6. 三餐 / 宵夜")
    lines.append("")
    meals = summary["meals"]
    lines.append("- 检查天数：%d；单日最多顿数：%d；超过 4 顿的天：%s"
                 % (meals["days_checked"], meals["max_per_day"],
                    meals["over_four"] or "无"))
    guard = summary["meal_guard"]
    lines.append("- 饭点守卫探针：%d 次，失败 %d 次" % (guard["probes"], guard["failed"]))
    if guard["fail_samples"]:
        for row in guard["fail_samples"]:
            lines.append("  - %s" % desensitize(row))
    lines.append("- 宵夜仿真：隔天 22:00 强制吃一次，随后 22:00 重入/23:00 再决策应被"
                 "「already_eaten / already_decided」拦下，同晚最多 1 笔扣款。")
    friend = summary.get("friend_dinner_days") or []
    if friend:
        social = sum(1 for item in friend if item.get("social_block"))
        claimed = sum(1 for item in friend if str(item.get("dinner_claim") or ""))
        lines.append("- 朋友聚餐日：%d 天；有 social 块 %d 天；dinner 饭点守卫认领 %d 天"
                     % (len(friend), social, claimed))
        for item in friend[:8]:
            guard = item.get("dinner_guard") or {}
            lines.append("  - %s（%s日）social=%s dinner_claim=%r meal_spent=%s meal_done=%s leaked=%s"
                         % (item["day"], item.get("day_type"), item.get("social_block"),
                            item.get("dinner_claim") or "",
                            guard.get("meal_spent"), guard.get("meal_done"),
                            guard.get("leaked") or []))
    lines.append("")
    lines.append("## 7. 检测器自检（阳性对照）")
    lines.append("")
    for item in summary["detector_self_test"]:
        lines.append("- %s：%s" % ("PASS" if item["ok"] else "FAIL", item["name"]))
    lines.append("")
    lines.append("## 8. 每天时间线（JSON 里有完整块字段）")
    lines.append("")
    for run in report["runs"]:
        lines.append("### %s（horizon=%s seed=%s %s）" % (run["run_id"], run["horizon"],
                                                         run["seed"], run["kind"]))
        for day in run["days"]:
            timeline = "；".join("%s-%s %s（%s）" % (block.get("start"), block.get("end"),
                                                   desensitize(block.get("activity")),
                                                   desensitize(block.get("place")))
                                 for block in day["blocks"])
            flag = "" if not any(issue.get("severity") != "warn" for issue in day["issues"]) else " [FAIL]"
            lines.append("- **%s** %s%s event=%s：%s"
                         % (day["day"], day["day_type"], flag, day["event"], timeline))
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 主流程
def run_all(args):
    guard = RealWriteGuard()
    before = snapshot_real()
    tmp_root = tempfile.mkdtemp(prefix="amo-schedsim-")
    isolation = {}
    runs = []
    failures = []
    guard.install()
    try:
        plan = []
        for horizon in args.horizons:
            for seed in args.seeds:
                profile = "default" if seed % 2 else "event_rich"
                plan.append({"kind": "random", "seed": seed, "profile": profile,
                             "horizon": horizon, "start_day": start_day_for(seed),
                             "target_days": {}, "target_events": {}})
        if not args.no_targeted:
            plan.extend(build_targeted_runs())
        for item in plan:
            run_id = "%s-h%d-s%d-%s" % (item["kind"], item["horizon"], item["seed"],
                                        item["start_day"].replace("-", "")[4:])
            try:
                plugin, state = make_plugin(item["seed"], item["profile"])
                days = run_sequence(plugin, state, item["seed"], item["profile"],
                                    item["start_day"], item["horizon"],
                                    seed_label=item["kind"],
                                    target_days=item.get("target_days"),
                                    target_events=item.get("target_events"))
            except Exception as exc:
                failures.append({"run": run_id, "error": repr(exc)[:300],
                                 "trace": traceback.format_exc()[-1200:]})
                print("[run] %-28s ERROR %r" % (run_id, exc), flush=True)
                continue
            run = {"run_id": run_id, "kind": item["kind"], "seed": item["seed"],
                   "profile": item["profile"], "horizon": item["horizon"],
                   "start_day": item["start_day"], "days": days}
            run["summary"] = summarize_run(run)
            runs.append(run)
            print("[run] %-28s days=%d fail=%d warn=%d" % (
                run_id, run["summary"]["days"], run["summary"]["fails"],
                run["summary"]["warns"]), flush=True)
    finally:
        guard.uninstall()
        after = snapshot_real()
        sim_lib.cleanup_temp_root()
        try:
            shutil.rmtree(tmp_root, ignore_errors=True)
        except Exception:  # cleanup: 仿真容错
            pass
        temp_clean = not os.path.isdir(tmp_root)
        detector = detector_self_test()
        isolation = {
            "before": before, "after": after,
            "main_db_unchanged": all(
                before["main"][index].get("sha256") == after["main"][index].get("sha256")
                and before["main"][index].get("size") == after["main"][index].get("size")
                for index in range(len(before["main"]))),
            "main_db_mtime_delta": {
                os.path.basename(before["main"][index]["path"]):
                    round(float(after["main"][index].get("mtime") or 0)
                          - float(before["main"][index].get("mtime") or 0), 3)
                for index in range(len(before["main"]))},
            "our_process_touched_real_paths": list(guard.touched),
            "our_process_blocked_writes": dict(guard.blocked_attempts),
            "external_writer": bool(not all(
                before["main"][index].get("sha256") == after["main"][index].get("sha256")
                for index in range(len(before["main"])))) and not guard.touched,
            "side_files_note": "；".join(
                "%s size %s→%s mtime %s→%s" % (os.path.basename(b.get("path", "")),
                                               b.get("size"), a.get("size"),
                                               b.get("mtime"), a.get("mtime"))
                for b, a in zip(before["side"], after["side"])),
            "temp_clean": temp_clean,
            "temp_root_deleted": tmp_root,
            "guard_blocked": dict(guard.blocked_attempts),
            "guard_violations": list(guard.violations),
            "run_errors": failures,
        }
        report = {"meta": {"generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "python": sys.version.split()[0],
                           "root": ROOT, "city": CITY,
                           "horizons": list(args.horizons),
                           "seeds": list(args.seeds),
                           "run_errors": len(failures),
                           "vcs": "未记录 commit：本机 PATH 无 git（用插件文件 sha256 追踪代码版本）",
                           "code": code_fingerprint(),
                           "outputs": [OUT_JSON, OUT_MD]},
                  "isolation": isolation,
                  "summary": collect_summary(runs, detector, isolation, guard),
                  "samples": pick_samples(runs),
                  "runs": runs}
        with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        with open(OUT_MD, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(write_markdown(report))
    summary = report["summary"]
    print("=" * 70)
    print("REAL_DB_UNCHANGED", isolation["main_db_unchanged"])
    print("GUARD_BLOCKED", isolation["guard_blocked"], "VIOLATIONS", len(isolation["guard_violations"]))
    print("TEMP_CLEAN", isolation["temp_clean"])
    print("RUNS", summary["runs"], "DAYS", summary["days"],
          "FAILS", summary["fails"], "WARNS", summary["warns"])
    print("FAILS_BY_CATEGORY", json.dumps(summary["fails_by_category"], ensure_ascii=False))
    print("RUN_ERRORS", len(isolation["run_errors"]))
    print("REPORT", OUT_JSON)
    print("REPORT", OUT_MD)
    return 0 if not isolation["run_errors"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="隔离日程仿真器（只读）")
    parser.add_argument("--horizons", nargs="*", type=int, default=list(HORIZONS))
    parser.add_argument("--seeds", nargs="*", type=int, default=list(SEEDS))
    parser.add_argument("--no-targeted", action="store_true")
    args = parser.parse_args(argv)
    return run_all(args)


if __name__ == "__main__":
    raise SystemExit(main())
